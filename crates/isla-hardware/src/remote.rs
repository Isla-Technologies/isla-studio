use crate::{
    base::{MotorCommands, MotorStates},
    ImuState, MotorState, RobotDriver,
};
use anyhow::{bail, Context, Result};
use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    sync::{mpsc, Arc, Mutex},
    thread,
    time::{Duration, Instant},
};
use tokio_tungstenite::{connect_async, tungstenite::Message};

pub const PORT: u16 = 47210;
#[derive(Default)]
struct Shared {
    states: MotorStates,
    imu: Option<ImuState>,
    stamp: Option<Instant>,
    alive: bool,
    error: Option<String>,
}
enum Outgoing {
    Send(Value, Option<mpsc::SyncSender<Result<Value, String>>>),
    Stop,
}
pub struct RemoteDriver {
    url: String,
    name: String,
    shared: Arc<Mutex<Shared>>,
    outgoing: Option<tokio::sync::mpsc::Sender<Outgoing>>,
    thread: Option<thread::JoinHandle<()>>,
    sequence: u64,
}
impl RemoteDriver {
    pub fn new(address: &str) -> Result<Self> {
        let address = address.trim();
        if address.is_empty() {
            bail!("Enter the robot computer's address");
        }
        let url = if address.starts_with("ws://") || address.starts_with("wss://") {
            address.to_owned()
        } else if address.starts_with('[') {
            if address.contains("]:") {
                format!("ws://{address}")
            } else {
                format!("ws://{address}:{PORT}")
            }
        } else if address.matches(':').count() > 1 {
            format!("ws://[{address}]:{PORT}")
        } else if address.contains(':') {
            format!("ws://{address}")
        } else {
            format!("ws://{address}:{PORT}")
        };
        Ok(Self {
            name: format!("Robot computer ({})", url.trim_start_matches("ws://")),
            url,
            shared: Arc::new(Mutex::new(Shared::default())),
            outgoing: None,
            thread: None,
            sequence: 1,
        })
    }
    fn send(
        &self,
        message: Value,
        reply: Option<mpsc::SyncSender<Result<Value, String>>>,
    ) -> Result<()> {
        let shared = self
            .shared
            .lock()
            .map_err(|_| anyhow::anyhow!("Robot connection state is unavailable"))?;
        if !shared.alive {
            bail!(
                "{}",
                shared
                    .error
                    .as_deref()
                    .unwrap_or("Not connected to the robot")
            );
        }
        drop(shared);
        self.outgoing
            .as_ref()
            .context("Not connected to the robot")?
            .try_send(Outgoing::Send(message, reply))
            .map_err(|e| anyhow::anyhow!("Robot connection cannot accept a command: {e}"))?;
        Ok(())
    }
    fn ask(&mut self, op: &str, fields: Value, timeout: Duration) -> Result<Value> {
        self.sequence = self
            .sequence
            .checked_add(1)
            .context("Robot request ID overflow")?;
        let mut msg = fields;
        msg["op"] = json!(op);
        msg["id"] = json!(self.sequence);
        let (tx, rx) = mpsc::sync_channel(1);
        self.send(msg, Some(tx))?;
        match rx
            .recv_timeout(timeout)
            .with_context(|| format!("The robot did not answer {op}"))?
        {
            Ok(v) => Ok(v),
            Err(e) => bail!("{e}"),
        }
    }
}
fn parse_states(message: &Value) -> Result<(MotorStates, Option<ImuState>)> {
    let mut states = BTreeMap::new();
    for (joint, v) in message["motors"]
        .as_object()
        .context("Missing motor readings")?
    {
        let a = v.as_array().context("Invalid motor reading")?;
        if a.len() != 6 {
            bail!("A motor reading has six fields");
        }
        let state = MotorState {
            position: a[0].as_f64().context("Invalid position")?,
            velocity: a[1].as_f64().context("Invalid velocity")?,
            torque: a[2].as_f64().context("Invalid torque")?,
            temperature: if a[3].is_null() {
                None
            } else {
                Some(a[3].as_f64().context("Invalid temperature")?)
            },
            fault: if a[4].is_null() {
                None
            } else {
                Some(a[4].as_str().context("Invalid fault")?.into())
            },
            age: a[5].as_f64().context("Invalid reading age")?,
        };
        if !state.finite() {
            bail!("Robot reported a non-finite motor reading");
        }
        states.insert(joint.clone(), state);
    }
    let imu = if message["imu"].is_null() {
        None
    } else {
        let v = &message["imu"];
        let imu = ImuState {
            quaternion: serde_json::from_value(v["quat"].clone())?,
            gyro: serde_json::from_value(v["gyro"].clone())?,
            age: v["age"].as_f64().unwrap_or(0.),
        };
        imu.validate(f64::MAX)?;
        Some(imu)
    };
    Ok((states, imu))
}
impl RobotDriver for RemoteDriver {
    fn name(&self) -> &str {
        &self.name
    }
    fn connect(&mut self) -> Result<()> {
        if self.outgoing.is_some() {
            self.disconnect()?;
        }
        let url = self.url.clone();
        let shared = self.shared.clone();
        let (tx, mut rx) = tokio::sync::mpsc::channel(64);
        let (ready_tx, ready_rx) = mpsc::sync_channel(1);
        let worker=thread::Builder::new().name("robot-remote".into()).spawn(move||{
            let rt=match tokio::runtime::Builder::new_current_thread().enable_all().build(){Ok(rt)=>rt,Err(e)=>{let _=ready_tx.send(Err(e.to_string()));return;}};
            rt.block_on(async move {
                let result=tokio::time::timeout(Duration::from_secs(3),connect_async(&url)).await;
                let mut ws=match result {Ok(Ok((ws,_)))=>ws,other=>{let _=ready_tx.send(Err(format!("Could not reach {url}: {other:?}")));return;}};
                if let Ok(mut s)=shared.lock(){s.alive=true;s.stamp=None;s.states.clear();s.imu=None;s.error=None;}
                let _=ready_tx.send(Ok(()));
                let mut pending:BTreeMap<u64,(Instant,mpsc::SyncSender<Result<Value,String>>)>=BTreeMap::new();
                let mut cleanup=tokio::time::interval(Duration::from_millis(100));
                let error=loop {
                    tokio::select! {
                        outgoing=rx.recv()=>match outgoing {
                            Some(Outgoing::Send(value,reply))=>{
                                if let Some(reply)=reply {if let Some(id)=value["id"].as_u64(){pending.insert(id,(Instant::now(),reply));}}
                                match tokio::time::timeout(Duration::from_millis(200),ws.send(Message::Text(value.to_string().into()))).await {
                                    Ok(Ok(()))=>{},_=>break "The robot connection stopped accepting commands".to_owned(),
                                }
                            },
                            _=>{let _=tokio::time::timeout(Duration::from_millis(200),ws.close(None)).await;break "Disconnected from the robot".to_owned();}
                        },
                        incoming=ws.next()=>match incoming {
                            Some(Ok(Message::Text(text)))=>{
                                let value:Value=match serde_json::from_str(&text){Ok(v)=>v,Err(_)=>break "The robot sent invalid JSON".to_owned()};
                                if value["type"]=="states" {
                                    match parse_states(&value) {Ok((states,imu))=>if let Ok(mut s)=shared.lock(){s.states=states;s.imu=imu;s.stamp=Some(Instant::now());},Err(e)=>break e.to_string()}
                                }else if let Some(id)=value["id"].as_u64(){if let Some((_,reply))=pending.remove(&id){let result=match value["error"].as_str(){Some(e)=>Err(e.into()),None=>Ok(value)};let _=reply.try_send(result);}}
                            },
                            Some(Ok(Message::Ping(p)))=>{if ws.send(Message::Pong(p)).await.is_err(){break "The robot connection dropped".into();}},
                            Some(Ok(Message::Close(_)))|None=>break "The robot connection closed".into(),
                            Some(Err(e))=>break format!("The robot connection dropped: {e}"),
                            _=>{},
                        },
                        _=cleanup.tick()=>{
                            pending.retain(|_,(stamp,reply)|{if stamp.elapsed()>Duration::from_secs(3){let _=reply.try_send(Err("The robot request timed out".into()));false}else{true}});
                        }
                    }
                };
                for(_,(_,reply))in pending {let _=reply.try_send(Err(error.clone()));}
                if let Ok(mut s)=shared.lock(){s.alive=false;s.error=Some(error);}
            });
        })?;
        self.thread = Some(worker);
        self.outgoing = Some(tx);
        match ready_rx.recv_timeout(Duration::from_secs(4)) {
            Ok(Ok(())) => Ok(()),
            other => {
                let _ = self.disconnect();
                bail!("Could not connect to robot: {other:?}")
            }
        }
    }
    fn disconnect(&mut self) -> Result<()> {
        if let Some(tx) = self.outgoing.take() {
            let _ = tx.try_send(Outgoing::Stop);
        }
        if let Some(worker) = self.thread.take() {
            let _ = worker.join();
        }
        Ok(())
    }
    fn scan(&mut self) -> Result<Vec<String>> {
        Ok(serde_json::from_value(
            self.ask("scan", json!({}), Duration::from_secs(2))?["joints"].clone(),
        )?)
    }
    fn read(&mut self) -> Result<MotorStates> {
        let s = self
            .shared
            .lock()
            .map_err(|_| anyhow::anyhow!("Robot state unavailable"))?;
        if !s.alive {
            bail!("{}", s.error.as_deref().unwrap_or("Robot disconnected"));
        }
        let age = s.stamp.map(|t| t.elapsed().as_secs_f64()).unwrap_or(9.9);
        Ok(s.states
            .iter()
            .map(|(j, s)| {
                let mut state = s.clone();
                state.age += age;
                (j.clone(), state)
            })
            .collect())
    }
    fn imu(&mut self) -> Result<Option<ImuState>> {
        let s = self
            .shared
            .lock()
            .map_err(|_| anyhow::anyhow!("Robot state unavailable"))?;
        let age = s.stamp.map(|t| t.elapsed().as_secs_f64()).unwrap_or(9.9);
        Ok(s.imu.map(|mut imu| {
            imu.age += age;
            imu
        }))
    }
    fn enable(&mut self) -> Result<()> {
        self.ask("enable", json!({}), Duration::from_secs(2))?;
        Ok(())
    }
    fn command(&mut self, commands: &MotorCommands) -> Result<()> {
        for c in commands.values() {
            c.validate()?;
        }
        let values: BTreeMap<_, _> = commands.iter().map(|(j, c)| (j, c.wire())).collect();
        self.send(json!({"op":"command","commands":values}), None)
    }
    fn relax(&mut self) -> Result<()> {
        if self.shared.lock().map(|s| s.alive).unwrap_or(false) {
            self.ask("relax", json!({}), Duration::from_secs(1))?;
        }
        Ok(())
    }
    fn set_zero(&mut self, joint: &str) -> Result<f64> {
        self.ask("set_zero", json!({"joint":joint}), Duration::from_secs(2))?["zero"]
            .as_f64()
            .context("Robot sent invalid zero")
    }
}
impl Drop for RemoteDriver {
    fn drop(&mut self) {
        let _ = self.relax();
        let _ = self.disconnect();
    }
}
