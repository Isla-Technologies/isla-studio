use crate::{
    base::{MotorCommands, MotorStates},
    ImuState, MotorState, RobotDriver,
};
use anyhow::{bail, Context, Result};
use isla_sim::{
    build,
    build::STAND_KEY,
    ffi,
    native::slice_mut,
    profile::{array, number, string},
    Built, Data, RobotProfile,
};
use std::{collections::BTreeMap, time::Instant};

pub struct VirtualDriver {
    built: Built,
    data: Data,
    joints: Vec<(String, usize, usize, f64, f64, f64)>,
    enabled: bool,
    connected: bool,
    commands: MotorCommands,
    faults: BTreeMap<String, String>,
    temperature: BTreeMap<String, f64>,
    torque: BTreeMap<String, f64>,
    last: Instant,
    debt: f64,
    floor: bool,
    anchor: [f64; 7],
}
impl VirtualDriver {
    pub fn new(profile: RobotProfile, floor: bool) -> Result<Self> {
        let mut built = build(&profile, None, "train")?;
        built.model.raw_mut().opt.timestep = 0.002;
        let model = &mut built.model;
        let m = model.raw_mut();
        unsafe {
            for a in 0..m.nu as usize {
                *m.actuator_gainprm.add(a * 10) = 0.;
                *m.actuator_biasprm.add(a * 10 + 1) = 0.;
                *m.actuator_biasprm.add(a * 10 + 2) = 0.;
            }
        }
        let mut data = model.data()?;
        data.reset_key(model, model.id(ffi::mjtObj_mjOBJ_KEY as i32, STAND_KEY)?);
        if !floor {
            data.qpos_mut()[2] += 0.06;
        }
        data.forward(model);
        let mut joints = Vec::new();
        for motor in array(&profile.data["motors"]) {
            let name = string(motor, "joint");
            let j = model.id(3, name)?;
            let kind = &profile.data["actuator_types"][string(motor, "type")];
            let corner = number(&kind["corner_speed"], 1e6);
            unsafe {
                joints.push((
                    name.to_owned(),
                    *model.raw().jnt_qposadr.add(j) as usize,
                    *model.raw().jnt_dofadr.add(j) as usize,
                    number(&kind["peak_torque"], 20.),
                    corner,
                    number(&kind["no_load_speed"], 2e6).max(corner + 1e-3),
                ));
            }
        }
        let anchor = data.qpos()[..7].try_into()?;
        let temperature = joints.iter().map(|j| (j.0.clone(), 28.)).collect();
        Ok(Self {
            built,
            data,
            joints,
            enabled: false,
            connected: false,
            commands: BTreeMap::new(),
            faults: BTreeMap::new(),
            temperature,
            torque: BTreeMap::new(),
            last: Instant::now(),
            debt: 0.,
            floor,
            anchor,
        })
    }
    fn require(&self) -> Result<()> {
        if !self.connected {
            bail!("The practice robot is not connected")
        };
        Ok(())
    }
    fn advance(&mut self) -> Result<()> {
        self.require()?;
        self.debt = (self.debt + self.last.elapsed().as_secs_f64()).min(0.05);
        self.last = Instant::now();
        while self.debt >= 0.002 {
            unsafe {
                let model = &self.built.model;
                let nv = model.raw().nv as usize;
                slice_mut(self.data.raw_mut().qfrc_applied, nv).fill(0.);
                if !self.floor {
                    for i in 0..3 {
                        *self.data.raw_mut().xfrc_applied.add(6 + i) =
                            (self.anchor[i] - self.data.qpos()[i]) * 4000.
                                - self.data.qvel()[i] * 200.;
                    }
                    let mut difference = [0.; 3];
                    model.api().mju_subQuat(
                        difference.as_mut_ptr(),
                        self.anchor[3..].as_ptr(),
                        self.data.qpos()[3..7].as_ptr(),
                    );
                    for i in 0..3 {
                        *self.data.raw_mut().xfrc_applied.add(9 + i) =
                            difference[i] * 600. - self.data.qvel()[i + 3] * 40.;
                    }
                }
                for (joint, q, v, peak, corner, no_load) in &self.joints {
                    let mut torque = -0.05 * self.data.qvel()[*v];
                    if self.enabled && !self.faults.contains_key(joint) {
                        if let Some(command) = self.commands.get(joint) {
                            let velocity = self.data.qvel()[*v];
                            let (low, high) = isla_sim::session::torque_envelope(
                                *peak, *corner, *no_load, velocity,
                            );
                            let limit = if command.torque_limit > 0. {
                                command.torque_limit
                            } else {
                                *peak
                            };
                            torque = (command.kp * (command.position - self.data.qpos()[*q])
                                + command.kd * (command.velocity - velocity)
                                + command.torque)
                                .max(low.max(-limit))
                                .min(high.min(limit));
                        }
                    }
                    *self.data.raw_mut().qfrc_applied.add(*v) = torque;
                    self.torque.insert(joint.clone(), torque);
                    let temperature = self.temperature.get_mut(joint).unwrap();
                    *temperature += (28. + 0.012 * torque * torque - *temperature) / 90. * 0.002;
                }
                self.data.step(model);
                self.debt -= 0.002;
            }
        }
        Ok(())
    }
}
impl RobotDriver for VirtualDriver {
    fn name(&self) -> &str {
        "Practice robot"
    }
    fn connect(&mut self) -> Result<()> {
        self.connected = true;
        self.last = Instant::now();
        Ok(())
    }
    fn disconnect(&mut self) -> Result<()> {
        self.enabled = false;
        self.connected = false;
        self.commands.clear();
        Ok(())
    }
    fn scan(&mut self) -> Result<Vec<String>> {
        self.require()?;
        Ok(self
            .joints
            .iter()
            .filter(|j| self.faults.get(&j.0).is_none_or(|f| f != "offline"))
            .map(|j| j.0.clone())
            .collect())
    }
    fn read(&mut self) -> Result<MotorStates> {
        self.advance()?;
        Ok(self
            .joints
            .iter()
            .filter(|j| self.faults.get(&j.0).is_none_or(|f| f != "offline"))
            .map(|(j, q, v, _, _, _)| {
                (
                    j.clone(),
                    MotorState {
                        position: self.data.qpos()[*q],
                        velocity: self.data.qvel()[*v],
                        torque: *self.torque.get(j).unwrap_or(&0.),
                        temperature: self.temperature.get(j).copied(),
                        fault: self
                            .faults
                            .get(j)
                            .filter(|f| f.as_str() != "overheat")
                            .cloned(),
                        age: 0.002,
                    },
                )
            })
            .collect())
    }
    fn imu(&mut self) -> Result<Option<ImuState>> {
        self.require()?;
        Ok(Some(ImuState {
            quaternion: self.data.qpos()[3..7].try_into()?,
            gyro: self.data.qvel()[3..6].try_into()?,
            age: 0.002,
        }))
    }
    fn enable(&mut self) -> Result<()> {
        self.require()?;
        self.enabled = true;
        Ok(())
    }
    fn command(&mut self, commands: &MotorCommands) -> Result<()> {
        self.require()?;
        for command in commands.values() {
            command.validate()?;
        }
        self.commands = commands.clone();
        Ok(())
    }
    fn relax(&mut self) -> Result<()> {
        self.enabled = false;
        self.commands.clear();
        Ok(())
    }
    fn set_zero(&mut self, joint: &str) -> Result<f64> {
        self.require()?;
        if self.enabled {
            bail!("Disarm before setting a zero")
        };
        let entry = self
            .joints
            .iter()
            .find(|j| j.0 == joint)
            .context("No such joint")?;
        Ok(self.data.qpos()[entry.1])
    }
    fn inject(&mut self, joint: &str, fault: Option<&str>) -> Result<()> {
        if !self.temperature.contains_key(joint) {
            bail!("No such joint")
        };
        self.faults.remove(joint);
        if let Some(fault) = fault {
            if fault == "overheat" {
                self.temperature.insert(joint.into(), 95.);
            } else if !["offline", "encoder"].contains(&fault) {
                bail!("Unknown practice fault")
            };
            self.faults.insert(joint.into(), fault.into());
        }
        Ok(())
    }
    fn reset_pose(&mut self) -> Result<()> {
        self.require()?;
        self.data.reset_key(
            &self.built.model,
            self.built
                .model
                .id(ffi::mjtObj_mjOBJ_KEY as i32, STAND_KEY)?,
        );
        Ok(())
    }
}
impl Drop for VirtualDriver {
    fn drop(&mut self) {
        let _ = self.disconnect();
    }
}
