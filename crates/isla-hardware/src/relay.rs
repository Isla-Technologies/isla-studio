//! Robot-side WebSocket relay. One controller owns the motors at a time.
//! The watchdog runs on its own thread, including when there are no clients.
use crate::{base::MotorCommands, MotorCommand, RobotDriver};
use anyhow::{bail, Context, Result};
use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use std::{
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc,
    },
    thread,
    time::{Duration, Instant},
};
use tokio::sync::{broadcast, oneshot, watch};
use tokio_tungstenite::{accept_async, tungstenite::Message};

enum Request {
    Message {
        value: Value,
        stamp: Instant,
        reply: oneshot::Sender<Value>,
    },
    Disconnect,
}
struct Worker {
    sender: mpsc::SyncSender<Request>,
    states: broadcast::Sender<Value>,
    stop: Arc<AtomicBool>,
    handle: Option<thread::JoinHandle<()>>,
}
impl Worker {
    fn new(mut driver: Box<dyn RobotDriver>, watchdog: Duration) -> Result<Self> {
        if watchdog < Duration::from_millis(10) || watchdog > Duration::from_secs(10) {
            bail!("Watchdog must be between 0.01 and 10 seconds");
        }
        let (sender, receiver) = mpsc::sync_channel(32);
        let (states, _) = broadcast::channel(8);
        let publish = states.clone();
        let stop = Arc::new(AtomicBool::new(false));
        let stopping = stop.clone();
        let handle=thread::Builder::new().name("robot-relay".into()).spawn(move||{
            let mut enabled=false;let mut heard=Instant::now();
            while !stopping.load(Ordering::Acquire) {
                let start=Instant::now();
                if enabled&&heard.elapsed()>watchdog {let _=driver.relax();enabled=false;}
                for _ in 0..32 {
                    let request=match receiver.try_recv(){Ok(v)=>v,Err(_)=>break};
                    match request {
                        Request::Disconnect=>{let _=driver.relax();enabled=false;},
                        Request::Message{value,stamp,reply}=>{
                            let id=value.get("id").cloned().unwrap_or(Value::Null);let mut response=json!({"id":id});
                            let result=(||->Result<()> {
                                if stamp.elapsed()>watchdog {bail!("Command expired before the robot could apply it");}
                                match value["op"].as_str().context("Missing operation")? {
                                    "command"=>{
                                        if !enabled {bail!("Enable the motors before sending commands");}
                                        let mut commands=MotorCommands::new();
                                        for(j,v)in value["commands"].as_object().context("Missing motor commands")? {let wire:[f64;6]=serde_json::from_value(v.clone())?;commands.insert(j.clone(),MotorCommand::from_wire(wire)?);}
                                        if commands.is_empty(){bail!("A command packet must contain motor commands");}
                                        driver.command(&commands)?;heard=Instant::now();
                                    },
                                    "scan"=>{if enabled{bail!("Disarm before scanning the motors");}response["joints"]=json!(driver.scan()?);},
                                    "enable"=>{driver.enable()?;enabled=true;heard=Instant::now();},
                                    "relax"=>{driver.relax()?;enabled=false;},
                                    "set_zero"=>{if enabled{bail!("Disarm before setting a zero");}response["zero"]=json!(driver.set_zero(value["joint"].as_str().context("Missing joint")?)?);},
                                    other=>bail!("Unknown robot operation: {other}"),
                                }
                                Ok(())
                            })();
                            if let Err(error)=result {let _=driver.relax();enabled=false;response["error"]=json!(error.to_string());}
                            let _=reply.send(response);
                        }
                    }
                }
                let readings=(||->Result<Value>{let states=driver.read()?;let imu=driver.imu()?;
                    let motors:serde_json::Map<String,Value>=states.into_iter().map(|(j,s)|(j,json!([s.position,s.velocity,s.torque,s.temperature,s.fault,s.age]))).collect();
                    Ok(json!({"type":"states","motors":motors,"imu":imu.map(|i|json!({"quat":i.quaternion,"gyro":i.gyro,"age":i.age}))}))})();
                match readings {Ok(value)=>{let _=publish.send(value);},Err(_)=>{if enabled{let _=driver.relax();enabled=false;}}}
                if let Some(spare)=Duration::from_millis(5).checked_sub(start.elapsed()){thread::sleep(spare);}
            }
            let _=driver.relax();let _=driver.disconnect();
        })?;
        Ok(Self {
            sender,
            states,
            stop,
            handle: Some(handle),
        })
    }
}
impl Drop for Worker {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(worker) = self.handle.take() {
            let _ = worker.join();
        }
    }
}
struct ControllerLease {
    sender: mpsc::SyncSender<Request>,
    occupied: Arc<AtomicBool>,
}
impl Drop for ControllerLease {
    fn drop(&mut self) {
        let _ = self.sender.send(Request::Disconnect);
        self.occupied.store(false, Ordering::Release);
    }
}

/// Serve an already-connected native driver until `shutdown` becomes true or closes.
pub async fn serve(
    driver: Box<dyn RobotDriver>,
    host: &str,
    port: u16,
    watchdog: Duration,
    mut shutdown: watch::Receiver<bool>,
) -> Result<()> {
    let listener = tokio::net::TcpListener::bind((host, port)).await?;
    serve_listener(driver, listener, watchdog, &mut shutdown).await
}
pub async fn serve_listener(
    driver: Box<dyn RobotDriver>,
    listener: tokio::net::TcpListener,
    watchdog: Duration,
    shutdown: &mut watch::Receiver<bool>,
) -> Result<()> {
    let worker = Worker::new(driver, watchdog)?;
    let occupied = Arc::new(AtomicBool::new(false));
    let mut clients = tokio::task::JoinSet::new();
    loop {
        if *shutdown.borrow() {
            break;
        }
        tokio::select! {
            changed=shutdown.changed()=>{if changed.is_err()||*shutdown.borrow(){break;}},
            incoming=listener.accept()=>{
                let(stream,_)=incoming?;let sender=worker.sender.clone();let mut states=worker.states.subscribe();let occupied=occupied.clone();
                clients.spawn(async move {
                    let Ok(Ok(mut ws))=tokio::time::timeout(Duration::from_secs(3),accept_async(stream)).await else{return};
                    if occupied.compare_exchange(false,true,Ordering::AcqRel,Ordering::Acquire).is_err(){let _=ws.send(Message::Text(json!({"error":"Another controller is connected"}).to_string().into())).await;let _=ws.close(None).await;return;}
                    let _lease=ControllerLease{sender:sender.clone(),occupied};
                    loop {
                        tokio::select! {
                            incoming=ws.next()=>match incoming {
                                Some(Ok(Message::Text(text)))=>{
                                    let value:Value=match serde_json::from_str(&text){Ok(v)=>v,Err(_)=>break};
                                    let requested=value.get("id").is_some();let(reply,answer)=oneshot::channel();
                                    if sender.try_send(Request::Message{value,stamp:Instant::now(),reply}).is_err(){break;}
                                    let response=match tokio::time::timeout(Duration::from_secs(3),answer).await{Ok(Ok(v))=>v,_=>break};
                                    if (requested||response.get("error").is_some())&& !matches!(tokio::time::timeout(Duration::from_millis(200),ws.send(Message::Text(response.to_string().into()))).await,Ok(Ok(()))){break;}
                                },
                                Some(Ok(Message::Ping(data)))=>{if ws.send(Message::Pong(data)).await.is_err(){break;}},
                                Some(Ok(Message::Close(_)))|Some(Err(_))|None=>break,
                                _=>{},
                            },
                            reading=states.recv()=>match reading {
                                Ok(value)=>if !matches!(tokio::time::timeout(Duration::from_millis(100),ws.send(Message::Text(value.to_string().into()))).await,Ok(Ok(()))){break;},
                                Err(broadcast::error::RecvError::Lagged(_))=>continue,
                                Err(_)=>break,
                            }
                        }
                    }
                    let _=tokio::time::timeout(Duration::from_millis(100),ws.close(None)).await;
                });
            },
            _=clients.join_next(),if !clients.is_empty()=>{},
        }
    }
    clients.abort_all();
    while clients.join_next().await.is_some() {}
    drop(worker);
    Ok(())
}
