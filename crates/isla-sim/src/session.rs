use crate::{
    build::{build, GANTRY, STAND_KEY},
    ffi, geometry as g,
    native::{a3, slice_mut},
    profile::{array, number, string},
    Built, Data, RobotProfile,
};
use anyhow::{bail, Context, Result};
use serde_json::{json, Value};
use std::collections::BTreeMap;

pub trait Controller: Send {
    fn targets(
        &mut self,
        dt: f64,
        quaternion: [f64; 4],
        gyro: [f64; 3],
        positions: &BTreeMap<String, f64>,
        velocities: &BTreeMap<String, f64>,
    ) -> Result<BTreeMap<String, f64>>;
    fn command(&mut self, command: [f64; 3]) -> Result<()>;
    fn jump(&mut self) -> Result<()>;
    fn reset(&mut self) -> Result<()>;
    fn steers(&self) -> bool;
    fn can_jump(&self) -> bool;
    fn jumping(&self) -> bool;
}

/// Motoring falls through zero above no-load speed; reverse torque still brakes.
pub fn torque_envelope(peak: f64, corner: f64, no_load: f64, speed: f64) -> (f64, f64) {
    let motoring = peak * ((no_load - speed.abs()) / (no_load - corner).max(1e-6)).clamp(-1., 1.);
    (
        if speed < 0. { -motoring } else { -peak },
        if speed > 0. { motoring } else { peak },
    )
}

pub struct LiveSession {
    pub profile: RobotProfile,
    pub built: Built,
    pub data: Data,
    pub mode: String,
    pub playing: bool,
    pub rate: f64,
    pub gantry: bool,
    pub stands_alone: bool,
    pub policy_name: String,
    pub controller: Option<Box<dyn Controller>>,
    pub targets: Vec<f64>,
    pub command: [f64; 3],
    base: usize,
    torso: usize,
    free: usize,
    free_dof: usize,
    pub joints: Vec<(String, usize, usize)>,
    pub actuators: Vec<(String, usize, usize)>,
    push: Option<([f64; 3], f64)>,
    pub message: String,
}

pub fn stand_drift(built: &Built) -> Result<f64> {
    let model = built.model.try_clone()?;
    let mut data = model.data()?;
    data.reset_key(&model, model.id(ffi::mjtObj_mjOBJ_KEY as i32, STAND_KEY)?);
    data.forward(&model);
    let base = model.id(ffi::mjtObj_mjOBJ_BODY as i32, string(&built.info, "base"))?;
    let start = unsafe { g::mat(data.raw().xmat, base).column(2).into_owned() };
    for _ in 0..1000 {
        data.step(&model);
        if !data.qpos().iter().all(|v| v.is_finite()) {
            return Ok(180.);
        }
    }
    let end = unsafe { g::mat(data.raw().xmat, base).column(2).into_owned() };
    Ok(start.dot(&end).clamp(-1., 1.).acos().to_degrees())
}

impl LiveSession {
    pub fn new(profile: RobotProfile, design: Option<&Value>) -> Result<Self> {
        let built = build(&profile, design, "display")?;
        let model = &built.model;
        let m = model.raw();
        let base = model.id(ffi::mjtObj_mjOBJ_BODY as i32, string(&built.info, "base"))?;
        let torso = model
            .id(ffi::mjtObj_mjOBJ_BODY as i32, string(&built.info, "torso"))
            .unwrap_or(base);
        let free_joint = (0..m.njnt as usize)
            .find(|&j| unsafe { *m.jnt_type.add(j) == ffi::mjtJoint_mjJNT_FREE as i32 })
            .context("Robot has no floating base")?;
        let free = unsafe { *m.jnt_qposadr.add(free_joint) as usize };
        let free_dof = unsafe { *m.jnt_dofadr.add(free_joint) as usize };
        let joints = (0..m.njnt as usize)
            .filter(|&j| unsafe { *m.jnt_type.add(j) == ffi::mjtJoint_mjJNT_HINGE as i32 })
            .map(|j| unsafe {
                (
                    model.name(3, j),
                    *m.jnt_qposadr.add(j) as usize,
                    *m.jnt_dofadr.add(j) as usize,
                )
            })
            .collect();
        let actuators = (0..m.nu as usize)
            .map(|a| unsafe {
                let j = *m.actuator_trnid.add(a * 2) as usize;
                (
                    model.name(3, j),
                    *m.jnt_qposadr.add(j) as usize,
                    *m.jnt_dofadr.add(j) as usize,
                )
            })
            .collect();
        let targets = serde_json::from_value(built.info["stand_ctrl"].clone())?;
        let data = model.data()?;
        let stands_alone = stand_drift(&built)? < 10.;
        let mut session = Self {
            profile,
            built,
            data,
            mode: "stand".into(),
            playing: true,
            rate: 1.,
            gantry: !stands_alone,
            stands_alone,
            policy_name: String::new(),
            controller: None,
            targets,
            command: [0.; 3],
            base,
            torso,
            free,
            free_dof,
            joints,
            actuators,
            push: None,
            message: String::new(),
        };
        session.reset()?;
        Ok(session)
    }
    pub fn reset(&mut self) -> Result<()> {
        self.data.reset_key(
            &self.built.model,
            self.built
                .model
                .id(ffi::mjtObj_mjOBJ_KEY as i32, STAND_KEY)?,
        );
        self.targets = serde_json::from_value(self.built.info["stand_ctrl"].clone())?;
        if let Some(controller) = &mut self.controller {
            controller.reset()?;
            controller.command(self.command)?;
        }
        self.push = None;
        self.hang(self.gantry)?;
        self.data.forward(&self.built.model);
        Ok(())
    }
    pub fn hang(&mut self, on: bool) -> Result<()> {
        unsafe {
            let model = &self.built.model;
            if let Ok(eq) = model.id(ffi::mjtObj_mjOBJ_EQUALITY as i32, GANTRY) {
                *self.data.raw_mut().eq_active.add(eq) = on;
                if on {
                    let body = model.id(1, GANTRY)?;
                    let hook = *model.raw().body_mocapid.add(body) as usize;
                    let q = self.data.qpos();
                    let pos = [
                        q[self.free],
                        q[self.free + 1],
                        number(
                            &self.built.info["stand_qpos"][self.free + 2],
                            q[self.free + 2],
                        ),
                    ];
                    let quat: Vec<f64> =
                        serde_json::from_value(self.built.info["stand_qpos"].clone())?;
                    slice_mut(self.data.raw_mut().mocap_pos.add(hook * 3), 3).copy_from_slice(&pos);
                    slice_mut(self.data.raw_mut().mocap_quat.add(hook * 4), 4)
                        .copy_from_slice(&quat[self.free + 3..self.free + 7]);
                }
            }
            self.gantry = on;
            Ok(())
        }
    }
    pub fn set_mode(&mut self, mode: &str) -> Result<()> {
        if !["stand", "pose", "limp", "policy", "mirror"].contains(&mode) {
            bail!("No mode {mode}");
        }
        if mode == "policy" {
            if self.controller.is_none() {
                bail!("Choose a policy first");
            }
            self.hang(false)?;
        }
        if mode == "pose" && self.mode != "pose" {
            for (i, (_, q, _)) in self.actuators.iter().enumerate() {
                self.targets[i] = self.data.qpos()[*q];
            }
        }
        self.mode = mode.into();
        Ok(())
    }
    pub fn set_targets(&mut self, targets: &BTreeMap<String, f64>) -> Result<()> {
        if !targets.values().all(|v| v.is_finite()) {
            bail!("Targets must be finite");
        }
        for (i, (name, _, _)) in self.actuators.iter().enumerate() {
            if let Some(value) = targets.get(name) {
                self.targets[i] = *value;
            }
        }
        Ok(())
    }
    pub fn command(&mut self, message: &Value) -> Result<()> {
        match message["type"].as_str().unwrap_or("") {
            "sim.play" => self.playing = message["playing"].as_bool().unwrap_or(true),
            "sim.reset" => self.reset()?,
            "sim.mode" => self.set_mode(message["mode"].as_str().context("Missing mode")?)?,
            "sim.gantry" => self.hang(message["on"].as_bool().unwrap_or(true))?,
            "sim.jump" => self
                .controller
                .as_mut()
                .context("Choose a jump policy first")?
                .jump()?,
            "sim.targets" => {
                self.set_targets(&serde_json::from_value(message["targets"].clone())?)?;
                self.mode = "pose".into();
            }
            "sim.command" => {
                let command: [f64; 3] = serde_json::from_value(message["command"].clone())?;
                if !command.iter().all(|v| v.is_finite()) {
                    bail!("Command must be finite")
                };
                self.command = command;
                if let Some(controller) = &mut self.controller {
                    controller.command(command)?;
                }
            }
            "sim.push" => {
                let force: [f64; 3] = serde_json::from_value(message["force"].clone())?;
                let seconds = number(&message["seconds"], 0.2);
                if !force.iter().all(|v| v.is_finite()) || !(0.01..=10.).contains(&seconds) {
                    bail!("Invalid push")
                };
                self.push = Some((force, self.data.raw().time + seconds));
            }
            "sim.rate" => self.rate = number(&message["rate"], 1.).clamp(0.05, 4.),
            other => bail!("Unknown command {other}"),
        }
        Ok(())
    }
    pub fn step(&mut self) -> Result<()> {
        unsafe {
            if !self.playing || self.mode == "mirror" {
                return Ok(());
            }
            if self.mode == "policy" {
                let positions = self
                    .joints
                    .iter()
                    .map(|(n, q, _)| (n.clone(), self.data.qpos()[*q]))
                    .collect();
                let speeds = self
                    .joints
                    .iter()
                    .map(|(n, _, v)| (n.clone(), self.data.qvel()[*v]))
                    .collect();
                let quaternion = self.data.qpos()[self.free + 3..self.free + 7]
                    .try_into()
                    .unwrap();
                let gyro = self.data.qvel()[self.free_dof + 3..self.free_dof + 6]
                    .try_into()
                    .unwrap();
                let targets = self
                    .controller
                    .as_mut()
                    .context("No policy selected")?
                    .targets(0.002, quaternion, gyro, &positions, &speeds)?;
                self.set_targets(&targets)?;
            }
            let model = &mut self.built.model;
            let m = model.raw_mut();
            let types = &self.profile.data["actuator_types"];
            for (i, (name, _, dof)) in self.actuators.iter().enumerate() {
                let motor = array(&self.built.info["motors"])
                    .iter()
                    .find(|a| a["joint"] == *name);
                let kind = motor
                    .map(|a| &types[string(a, "type")])
                    .unwrap_or(&Value::Null);
                let peak = number(&kind["peak_torque"], 1e6);
                let corner = number(&kind["corner_speed"], 1e6);
                let no_load = number(&kind["no_load_speed"], 2e6).max(corner + 1e-3);
                let velocity = self.data.qvel()[*dof];
                let (low, high) = if self.mode == "limp" {
                    (0., 0.)
                } else {
                    torque_envelope(peak, corner, no_load, velocity)
                };
                *m.actuator_forcerange.add(i * 2) = low;
                *m.actuator_forcerange.add(i * 2 + 1) = high;
                let mut target = if self.mode == "stand" {
                    number(&self.built.info["stand_ctrl"][i], 0.)
                } else {
                    self.targets[i]
                };
                if *m.actuator_ctrllimited.add(i) {
                    target = target.clamp(
                        *m.actuator_ctrlrange.add(i * 2),
                        *m.actuator_ctrlrange.add(i * 2 + 1),
                    );
                }
                self.data.ctrl_mut()[i] = target;
            }
            slice_mut(self.data.raw_mut().xfrc_applied, m.nbody as usize * 6).fill(0.);
            if let Some((force, until)) = self.push {
                if self.data.raw().time < until {
                    slice_mut(self.data.raw_mut().xfrc_applied.add(self.torso * 6), 3)
                        .copy_from_slice(&force);
                } else {
                    self.push = None;
                }
            }
            self.data.step(model);
            if !self.data.qpos().iter().all(|v| v.is_finite()) {
                self.reset()?;
                bail!("The simulation became unstable and was reset");
            }
            Ok(())
        }
    }
    pub fn frame(&self) -> Value {
        unsafe {
            let m = self.built.model.raw();
            let d = self.data.raw();
            let motors = self
                .actuators
                .iter()
                .enumerate()
                .map(|(i, (_, q, v))| {
                    json!([
                        self.data.qpos()[*q],
                        self.data.qvel()[*v],
                        *d.actuator_force.add(i),
                        self.data.ctrl()[i]
                    ])
                })
                .collect::<Vec<_>>();
            let struts = array(&self.built.info["struts"])
                .iter()
                .filter_map(|s| {
                    self.built
                        .model
                        .id(ffi::mjtObj_mjOBJ_TENDON as i32, string(s, "tendon"))
                        .ok()
                })
                .map(|id| {
                    let length = *d.ten_length.add(id);
                    json!([
                        length,
                        *m.tendon_stiffness.add(id) * (*m.tendon_lengthspring.add(id * 2) - length)
                            - *m.tendon_damping.add(id) * *d.ten_velocity.add(id)
                    ])
                })
                .collect::<Vec<_>>();
            let touch = array(&self.built.info["feet"])
                .iter()
                .filter_map(|f| {
                    let id = self
                        .built
                        .model
                        .id(ffi::mjtObj_mjOBJ_SENSOR as i32, string(f, "touch"))
                        .ok()?;
                    Some((
                        string(f, "side").to_owned(),
                        *d.sensordata.add(*m.sensor_adr.add(id) as usize),
                    ))
                })
                .collect::<BTreeMap<_, _>>();
            let tilt = (*d.xmat.add(self.base * 9 + 8))
                .clamp(-1., 1.)
                .acos()
                .to_degrees();
            json!({"type":"frame","source":if self.mode=="mirror"{"mirror"}else{"sim"},"t":d.time,"qpos":self.data.qpos(),"motors":motors,"struts":struts,"touch":touch,"com":a3(d.subtree_com,self.base),"tilt":tilt,"mode":self.mode,"gantry":self.gantry,"stands_alone":self.stands_alone,"playing":self.playing,"policy":self.policy_name,"steers":self.mode=="policy"&&self.controller.as_ref().is_some_and(|c|c.steers()),"can_jump":self.mode=="policy"&&self.controller.as_ref().is_some_and(|c|c.can_jump()),"jumping":self.controller.as_ref().is_some_and(|c|c.jumping()),"command":self.command,"message":self.message})
        }
    }
}
