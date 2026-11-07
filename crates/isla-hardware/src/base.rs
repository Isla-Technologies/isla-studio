use anyhow::{bail, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub type MotorStates = BTreeMap<String, MotorState>;
pub type MotorCommands = BTreeMap<String, MotorCommand>;

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct MotorState {
    pub position: f64,
    pub velocity: f64,
    pub torque: f64,
    pub temperature: Option<f64>,
    pub fault: Option<String>,
    pub age: f64,
}

impl MotorState {
    pub fn finite(&self) -> bool {
        [self.position, self.velocity, self.torque, self.age]
            .iter()
            .all(|v| v.is_finite())
            && self.age >= 0.0
            && self.temperature.is_none_or(f64::is_finite)
    }
}

#[derive(Clone, Copy, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct MotorCommand {
    pub position: f64,
    pub velocity: f64,
    pub kp: f64,
    pub kd: f64,
    pub torque: f64,
    pub torque_limit: f64,
}

impl MotorCommand {
    pub fn validate(&self) -> Result<()> {
        if ![
            self.position,
            self.velocity,
            self.kp,
            self.kd,
            self.torque,
            self.torque_limit,
        ]
        .iter()
        .all(|v| v.is_finite())
        {
            bail!("Motor command contains a non-finite value");
        }
        if self.kp < 0.0 || self.kd < 0.0 || self.torque_limit < 0.0 {
            bail!("Motor gains and torque limit must be nonnegative");
        }
        Ok(())
    }
    pub fn wire(&self) -> [f64; 6] {
        [
            self.position,
            self.velocity,
            self.kp,
            self.kd,
            self.torque,
            self.torque_limit,
        ]
    }
    pub fn from_wire(v: [f64; 6]) -> Result<Self> {
        let out = Self {
            position: v[0],
            velocity: v[1],
            kp: v[2],
            kd: v[3],
            torque: v[4],
            torque_limit: v[5],
        };
        out.validate()?;
        Ok(out)
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct ImuState {
    pub quaternion: [f64; 4],
    pub gyro: [f64; 3],
    pub age: f64,
}
impl Default for ImuState {
    fn default() -> Self {
        Self {
            quaternion: [1., 0., 0., 0.],
            gyro: [0.; 3],
            age: 0.,
        }
    }
}
impl ImuState {
    pub fn validate(&self, timeout: f64) -> Result<()> {
        let norm = self.quaternion.iter().map(|v| v * v).sum::<f64>();
        if !self
            .quaternion
            .iter()
            .chain(self.gyro.iter())
            .chain([&self.age])
            .all(|v| v.is_finite())
            || !(0.5..=1.5).contains(&norm)
            || self.age < 0.0
        {
            bail!("The IMU reports invalid readings");
        }
        if self.age > timeout {
            bail!("The IMU has no fresh data");
        }
        Ok(())
    }
}

/// A driver owns its transport. Dropping a live driver must relax its motors.
pub trait RobotDriver: Send {
    fn name(&self) -> &str;
    fn connect(&mut self) -> Result<()>;
    fn disconnect(&mut self) -> Result<()>;
    fn scan(&mut self) -> Result<Vec<String>>;
    fn read(&mut self) -> Result<MotorStates>;
    fn imu(&mut self) -> Result<Option<ImuState>> {
        Ok(None)
    }
    fn enable(&mut self) -> Result<()>;
    fn command(&mut self, commands: &MotorCommands) -> Result<()>;
    fn relax(&mut self) -> Result<()>;
    fn set_zero(&mut self, joint: &str) -> Result<f64>;
    fn inject(&mut self, _joint: &str, _fault: Option<&str>) -> Result<()> {
        bail!("Fault injection is only available on the practice robot")
    }
    fn reset_pose(&mut self) -> Result<()> {
        bail!("Pose reset is only available on the practice robot")
    }
}
