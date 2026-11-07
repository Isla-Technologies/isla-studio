use crate::{
    base::MotorStates, HardwareSettings, ImuState, JointLimits, RobotDriver, SafetyFilter,
};
use anyhow::{bail, Context, Result};
use isla_learn::{Policy, PolicyRunner};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    thread,
    time::{Duration, Instant},
};

struct LinkState {
    profile: Value,
    settings: HardwareSettings,
    driver: Option<Box<dyn RobotDriver>>,
    state: String,
    mode: String,
    fault: Option<String>,
    safety: SafetyFilter,
    states: MotorStates,
    imu: Option<ImuState>,
    runner: Option<PolicyRunner>,
    policy_name: String,
    follow: BTreeMap<String, f64>,
    armed_at: Instant,
    rate: f64,
    log: Vec<String>,
}
pub struct HardwareLink {
    inner: Arc<Mutex<LinkState>>,
    stop: Arc<AtomicBool>,
    worker: Option<thread::JoinHandle<()>>,
}
impl HardwareLink {
    pub fn new(profile: Value, settings: HardwareSettings) -> Result<Self> {
        let safety = filter(&profile, &settings)?;
        Ok(Self {
            inner: Arc::new(Mutex::new(LinkState {
                profile,
                settings,
                driver: None,
                state: "disconnected".into(),
                mode: "hold".into(),
                fault: None,
                safety,
                states: BTreeMap::new(),
                imu: None,
                runner: None,
                policy_name: String::new(),
                follow: BTreeMap::new(),
                armed_at: Instant::now(),
                rate: 0.,
                log: Vec::new(),
            })),
            stop: Arc::new(AtomicBool::new(false)),
            worker: None,
        })
    }
    pub fn connect(&mut self, profile: &isla_sim::RobotProfile) -> Result<Vec<String>> {
        self.disconnect()?;
        let mut inner = self.inner.lock().unwrap();
        let mut driver = make_driver(profile, &inner.settings)?;
        driver.connect()?;
        let scanned = match driver.scan() {
            Ok(s) => s,
            Err(e) => {
                let _ = driver.disconnect();
                return Err(e);
            }
        };
        inner.driver = Some(driver);
        inner.state = "connected".into();
        inner.fault = None;
        inner.safety = filter(&inner.profile, &inner.settings)?;
        inner
            .log
            .push(format!("Connected: {} motors answer", scanned.len()));
        drop(inner);
        self.stop.store(false, Ordering::Release);
        let state = self.inner.clone();
        let stop = self.stop.clone();
        self.worker = Some(thread::spawn(move || {
            let mut last = Instant::now();
            let mut window = last;
            let mut ticks = 0;
            while !stop.load(Ordering::Acquire) {
                let start = Instant::now();
                let dt = start.duration_since(last).as_secs_f64();
                last = start;
                if let Ok(mut inner) = state.lock() {
                    if let Err(error) = inner.tick(dt) {
                        inner.estop(&format!("Control loop: {error}"));
                    }
                    ticks += 1;
                    if window.elapsed() >= Duration::from_secs(1) {
                        inner.rate = ticks as f64 / window.elapsed().as_secs_f64();
                        window = Instant::now();
                        ticks = 0;
                    }
                }
                thread::sleep(Duration::from_millis(5).saturating_sub(start.elapsed()));
            }
        }));
        Ok(scanned)
    }
    pub fn disconnect(&mut self) -> Result<()> {
        self.stop.store(true, Ordering::Release);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
        let mut inner = self.inner.lock().unwrap();
        let mut error = None;
        if let Some(mut driver) = inner.driver.take() {
            if let Err(e) = driver.relax() {
                error = Some(e)
            }
            if let Err(e) = driver.disconnect() {
                error = Some(e)
            }
        }
        inner.state = "disconnected".into();
        inner.states.clear();
        inner.imu = None;
        inner.rate = 0.;
        if let Some(error) = error {
            return Err(error);
        }
        Ok(())
    }
    pub fn arm(&mut self) -> Result<()> {
        let mut inner = self.inner.lock().unwrap();
        if inner.state != "connected" {
            bail!("Connect and clear any stop before arming")
        }
        let states = inner.driver.as_mut().context("Connect first")?.read()?;
        if let Some(problem) = inner.safety.check(&states, false) {
            bail!("Cannot arm: {problem}");
        }
        if inner.mode == "policy" {
            let imu = inner
                .driver
                .as_mut()
                .unwrap()
                .imu()?
                .context("This connection has no IMU")?;
            imu.validate(inner.settings.limits.state_timeout)?;
        }
        inner.safety.start(
            &states
                .iter()
                .map(|(j, s)| (j.clone(), s.position))
                .collect(),
        )?;
        inner.follow = inner.safety.targets.clone();
        if let Err(error) = inner.driver.as_mut().unwrap().enable() {
            inner.estop(&error.to_string());
            return Err(error);
        }
        inner.armed_at = Instant::now();
        inner.state = "armed".into();
        inner
            .log
            .push("Armed; stiffness ramps up under safety limits".into());
        Ok(())
    }
    pub fn disarm(&mut self) -> Result<()> {
        let mut inner = self.inner.lock().unwrap();
        if let Some(driver) = inner.driver.as_mut() {
            if let Err(e) = driver.relax() {
                inner.estop(&e.to_string());
                return Err(e);
            }
        }
        if inner.state == "armed" {
            inner.state = "connected".into();
        }
        Ok(())
    }
    pub fn estop(&mut self, reason: &str) -> Result<()> {
        self.inner.lock().unwrap().estop(reason);
        Ok(())
    }
    pub fn clear(&mut self) -> Result<()> {
        let mut inner = self.inner.lock().unwrap();
        if inner.state == "fault" {
            inner.driver.as_mut().context("Connect first")?.relax()?;
            inner.state = "connected".into();
            inner.fault = None;
        }
        Ok(())
    }
    pub fn scan(&mut self) -> Result<Vec<String>> {
        let mut inner = self.inner.lock().unwrap();
        if inner.state == "armed" {
            bail!("Disarm before scanning")
        };
        inner.driver.as_mut().context("Connect first")?.scan()
    }
    pub fn update_settings(&mut self, settings: HardwareSettings) -> Result<()> {
        let mut inner = self.inner.lock().unwrap();
        if inner.state == "armed" {
            bail!("Disarm before changing settings")
        }
        if inner.state != "disconnected"
            && (inner.settings.driver != settings.driver
                || serde_json::to_value(&inner.settings.buses)?
                    != serde_json::to_value(&settings.buses)?
                || serde_json::to_value(&inner.settings.protocols)?
                    != serde_json::to_value(&settings.protocols)?
                || inner.settings.remote != settings.remote)
        {
            bail!("Disconnect before changing the transport or protocol")
        }
        let safety = filter(&inner.profile, &settings)?;
        inner.settings = settings;
        inner.safety = safety;
        Ok(())
    }
    pub fn set_policy(
        &mut self,
        policy: Option<Policy>,
        name: &str,
        design: Option<&BTreeMap<String, f64>>,
    ) -> Result<()> {
        let runner = policy.map(|p| PolicyRunner::new(p, design)).transpose()?;
        let mut inner = self.inner.lock().unwrap();
        inner.runner = runner;
        inner.policy_name = name.into();
        Ok(())
    }
    pub fn set_mode(&mut self, mode: &str) -> Result<()> {
        let mut inner = self.inner.lock().unwrap();
        if !["hold", "follow", "policy"].contains(&mode) {
            bail!("No mode {mode}")
        };
        if mode == "policy" {
            if inner.runner.is_none() {
                bail!("Choose a policy first")
            };
            let imu = inner
                .driver
                .as_mut()
                .context("Connect first")?
                .imu()?
                .context("This connection has no IMU")?;
            imu.validate(inner.settings.limits.state_timeout)?;
        }
        inner.mode = mode.into();
        Ok(())
    }
    pub fn set_follow(&mut self, targets: BTreeMap<String, f64>) -> Result<()> {
        if !targets.values().all(|v| v.is_finite()) {
            bail!("Targets must be finite")
        };
        self.inner.lock().unwrap().follow = targets;
        Ok(())
    }
    pub fn set_command(&mut self, command: [f64; 3]) -> Result<()> {
        if let Some(runner) = &mut self.inner.lock().unwrap().runner {
            runner.set_command(command[0], command[1], command[2])?;
        }
        Ok(())
    }
    pub fn jump(&mut self) -> Result<()> {
        let mut inner = self.inner.lock().unwrap();
        if inner.state != "armed" || inner.mode != "policy" {
            bail!("Arm the robot and run a jump policy first")
        };
        if !inner
            .runner
            .as_mut()
            .context("Choose a jump policy")?
            .jump()
        {
            bail!("Policy cannot jump now")
        };
        Ok(())
    }
    pub fn set_zero(&mut self, joint: &str) -> Result<f64> {
        let mut inner = self.inner.lock().unwrap();
        if inner.state == "armed" {
            bail!("Disarm before setting a zero")
        };
        inner
            .driver
            .as_mut()
            .context("Connect first")?
            .set_zero(joint)
    }
    pub fn inject(&mut self, joint: &str, fault: Option<&str>) -> Result<()> {
        self.inner
            .lock()
            .unwrap()
            .driver
            .as_mut()
            .context("Connect first")?
            .inject(joint, fault)
    }
    pub fn status(&self) -> Value {
        let inner = self.inner.lock().unwrap();
        let joints=inner.profile["motors"].as_array().into_iter().flatten().map(|m|{let joint=m["joint"].as_str().unwrap_or("");let state=inner.states.get(joint);(joint.to_owned(),json!({"online":state.is_some_and(|s|s.age<inner.settings.limits.state_timeout*3.),"q":state.map(|s|s.position),"dq":state.map(|s|s.velocity),"tau":state.map(|s|s.torque),"temp":state.and_then(|s|s.temperature),"fault":state.and_then(|s|s.fault.as_ref()),"target":if inner.state=="armed"{inner.safety.targets.get(joint)}else{None},"bus":m["bus"],"node":m["node"]}))}).collect::<BTreeMap<_,_>>();
        json!({"type":"hardware","state":inner.state,"mode":inner.mode,"fault":inner.fault,"driver":inner.settings.driver,"driver_name":inner.driver.as_ref().map(|d|d.name()).unwrap_or(""),"rate":inner.rate,"armed_for":if inner.state=="armed"{inner.armed_at.elapsed().as_secs_f64()}else{0.},"policy":inner.policy_name,"can_jump":inner.mode=="policy"&&inner.runner.as_ref().is_some_and(|r|r.can_jump()),"joints":joints,"imu":inner.imu.map(|i|json!({"quat":i.quaternion,"gyro":i.gyro})),"limits":inner.settings.limits,"log":inner.log})
    }
}
impl Drop for HardwareLink {
    fn drop(&mut self) {
        let _ = self.disconnect();
    }
}
impl LinkState {
    fn estop(&mut self, reason: &str) {
        if let Some(driver) = &mut self.driver {
            let _ = driver.relax();
        }
        if self.state != "disconnected" {
            self.state = "fault".into();
            self.fault = Some(reason.into());
            self.log.push(format!("STOP: {reason}"));
            if self.log.len() > 200 {
                self.log.remove(0);
            }
        }
    }
    fn tick(&mut self, dt: f64) -> Result<()> {
        let driver = self.driver.as_mut().context("No driver")?;
        self.states = driver.read()?;
        self.imu = driver.imu()?;
        if self.state != "armed" {
            return Ok(());
        }
        if dt > self.settings.limits.state_timeout {
            bail!("Control loop missed its deadline")
        }
        if let Some(problem) = self.safety.check(&self.states, true) {
            bail!(problem)
        }
        let desired = match self.mode.as_str() {
            "follow" => self.follow.clone(),
            "policy" => {
                let imu = self.imu.context("IMU disconnected")?;
                imu.validate(self.settings.limits.state_timeout)?;
                let q = self
                    .states
                    .iter()
                    .map(|(j, s)| (j.clone(), s.position))
                    .collect();
                let v = self
                    .states
                    .iter()
                    .map(|(j, s)| (j.clone(), s.velocity))
                    .collect();
                self.runner.as_mut().context("No policy")?.step(
                    dt,
                    imu.quaternion,
                    imu.gyro,
                    &q,
                    &v,
                )?
            }
            _ => BTreeMap::new(),
        };
        let commands = self
            .safety
            .commands(&desired, dt, self.armed_at.elapsed().as_secs_f64())?;
        driver.command(&commands)
    }
}
fn filter(profile: &Value, settings: &HardwareSettings) -> Result<SafetyFilter> {
    let mut joints = BTreeMap::new();
    for motor in profile["motors"]
        .as_array()
        .context("Robot has no motors")?
    {
        let joint = motor["joint"].as_str().context("Missing joint")?;
        let kind =
            &profile["actuator_types"][motor["type"].as_str().context("Missing motor type")?];
        let range = &profile["operating_ranges"][joint];
        joints.insert(
            joint.into(),
            JointLimits {
                low: range[0].as_f64().unwrap_or(-std::f64::consts::PI),
                high: range[1].as_f64().unwrap_or(std::f64::consts::PI),
                peak_torque: kind["peak_torque"].as_f64().unwrap_or(20.),
                kp: kind["kp"].as_f64().unwrap_or(50.),
                kd: kind["kd"].as_f64().unwrap_or(2.),
            },
        );
    }
    SafetyFilter::new(joints, settings.limits.clone())
}
pub fn make_driver(
    profile: &isla_sim::RobotProfile,
    settings: &HardwareSettings,
) -> Result<Box<dyn RobotDriver>> {
    match settings.driver.as_str() {
        "can" => Ok(Box::new(crate::can::CanDriver::new(
            &profile.data,
            settings.clone(),
        )?)),
        "remote" => Ok(Box::new(crate::remote::RemoteDriver::new(
            &settings.remote,
        )?)),
        "virtual" => Ok(Box::new(crate::virtual_driver::VirtualDriver::new(
            profile.clone(),
            settings.virtual_floor,
        )?)),
        other => bail!("Unsupported driver {other}"),
    }
}
