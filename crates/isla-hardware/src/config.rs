use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct BusConfig {
    pub name: String,
    pub interface: String,
    pub channel: String,
    pub bitrate: u32,
}
impl Default for BusConfig {
    fn default() -> Self {
        Self {
            name: "can0".into(),
            interface: "slcan".into(),
            channel: String::new(),
            bitrate: 1_000_000,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct ProtocolConfig {
    pub protocol: String,
    pub gear_ratio: f64,
    pub p_max: f64,
    pub v_max: f64,
    pub kp_max: f64,
    pub kd_max: f64,
    pub t_max: f64,
}
impl Default for ProtocolConfig {
    fn default() -> Self {
        Self {
            protocol: "mit".into(),
            gear_ratio: 1.,
            p_max: 12.5,
            v_max: 65.,
            kp_max: 500.,
            kd_max: 5.,
            t_max: 60.,
        }
    }
}
impl ProtocolConfig {
    pub fn validate(&self) -> Result<()> {
        if !matches!(self.protocol.as_str(), "mit" | "odrive") {
            bail!("Unsupported motor protocol: {}", self.protocol);
        }
        if ![
            self.gear_ratio,
            self.p_max,
            self.v_max,
            self.kp_max,
            self.kd_max,
            self.t_max,
        ]
        .iter()
        .all(|v| v.is_finite() && *v > 0.)
        {
            bail!("Protocol ranges and gear ratio must be finite and positive");
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct SafetyLimits {
    pub max_speed: f64,
    pub torque_fraction: f64,
    pub stiffness_fraction: f64,
    pub soft_start: f64,
    pub limit_margin: f64,
    pub state_timeout: f64,
    pub max_temperature: f64,
    pub max_tracking_error: f64,
}
impl Default for SafetyLimits {
    fn default() -> Self {
        Self {
            max_speed: 2.,
            torque_fraction: 0.5,
            stiffness_fraction: 0.6,
            soft_start: 2.,
            limit_margin: 0.05,
            state_timeout: 0.1,
            max_temperature: 80.,
            max_tracking_error: 0.7,
        }
    }
}
impl SafetyLimits {
    pub fn validate(&self) -> Result<()> {
        let checks = [
            (
                (0.05..=30.).contains(&self.max_speed),
                "Top speed is 0.05 to 30 rad/s",
            ),
            (
                self.torque_fraction > 0. && self.torque_fraction <= 1.,
                "Torque share is above 0 and at most 1",
            ),
            (
                self.stiffness_fraction > 0. && self.stiffness_fraction <= 1.,
                "Stiffness share is above 0 and at most 1",
            ),
            (
                (0.0..=30.).contains(&self.soft_start),
                "Soft start is 0 to 30 s",
            ),
            (
                (0.0..=0.5).contains(&self.limit_margin),
                "Limit margin is 0 to 0.5 rad",
            ),
            (
                (0.01..=2.).contains(&self.state_timeout),
                "Data timeout is 10 ms to 2 s",
            ),
            (
                (20.0..=150.).contains(&self.max_temperature),
                "Temperature limit is 20 to 150 degC",
            ),
            (
                (0.05..=std::f64::consts::PI).contains(&self.max_tracking_error),
                "Tracking stop is 0.05 to pi rad",
            ),
        ];
        for (ok, message) in checks {
            if !ok {
                bail!("{message}");
            }
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct HardwareSettings {
    pub robot: String,
    pub driver: String,
    pub buses: Vec<BusConfig>,
    pub protocols: BTreeMap<String, ProtocolConfig>,
    pub remote: String,
    pub virtual_floor: bool,
    pub limits: SafetyLimits,
}
impl Default for HardwareSettings {
    fn default() -> Self {
        Self {
            robot: "luma_1".into(),
            driver: "virtual".into(),
            buses: vec![],
            protocols: BTreeMap::new(),
            remote: "192.168.4.1:47210".into(),
            virtual_floor: false,
            limits: SafetyLimits::default(),
        }
    }
}
impl HardwareSettings {
    pub fn validate(&self) -> Result<()> {
        validate_id(&self.robot)?;
        self.limits.validate()?;
        if !matches!(self.driver.as_str(), "virtual" | "can" | "remote") {
            bail!("Unsupported driver: {}", self.driver);
        }
        let mut names = BTreeSet::new();
        for b in &self.buses {
            if b.name.is_empty() || !names.insert(&b.name) {
                bail!("CAN bus names must be nonempty and unique");
            }
            if b.bitrate == 0 || b.bitrate > 1_000_000 {
                bail!("Classic CAN bitrate must be between 1 and 1000000");
            }
        }
        for p in self.protocols.values() {
            p.validate()?;
        }
        Ok(())
    }
    pub fn from_value(value: Value) -> Result<Self> {
        let out: Self = serde_json::from_value(value)?;
        out.validate()?;
        Ok(out)
    }
    pub fn defaults(robot: &str, profile: &Value) -> Result<Self> {
        validate_id(robot)?;
        let mut out = Self {
            robot: robot.into(),
            ..Default::default()
        };
        let buses: BTreeSet<_> = profile["motors"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|m| m["bus"].as_str().unwrap_or("can0"))
            .collect();
        out.buses = buses
            .into_iter()
            .map(|name| BusConfig {
                name: name.into(),
                ..Default::default()
            })
            .collect();
        if let Some(kinds) = profile["actuator_types"].as_object() {
            for (key, kind) in kinds {
                out.protocols.insert(
                    key.clone(),
                    ProtocolConfig {
                        protocol: kind["protocol"].as_str().unwrap_or("mit").into(),
                        gear_ratio: kind["gear_ratio"]
                            .as_f64()
                            .filter(|n| *n > 0.)
                            .unwrap_or(1.),
                        t_max: (kind["peak_torque"].as_f64().unwrap_or(20.) * 1.2).max(1.),
                        ..Default::default()
                    },
                );
            }
        }
        out.validate()?;
        Ok(out)
    }
    pub fn path(&self, settings: &Path) -> Result<PathBuf> {
        validate_id(&self.robot)?;
        Ok(settings.join(format!("{}-hardware.json", self.robot)))
    }
    pub fn save(&self, settings: &Path) -> Result<()> {
        self.validate()?;
        fs::create_dir_all(settings)?;
        let path = self.path(settings)?;
        let mut temp = tempfile::NamedTempFile::new_in(settings)?;
        use std::io::Write;
        writeln!(temp, "{}", serde_json::to_string_pretty(self)?)?;
        temp.as_file().sync_all()?;
        temp.persist(&path)
            .with_context(|| format!("Cannot save {}", path.display()))?;
        Ok(())
    }
    pub fn load(settings: &Path, robot: &str, profile: &Value) -> Result<Self> {
        validate_id(robot)?;
        let path = settings.join(format!("{robot}-hardware.json"));
        if path.exists() {
            let out = Self::from_value(serde_json::from_slice(&fs::read(&path)?)?)?;
            if out.robot != robot {
                bail!("Hardware settings belong to a different robot");
            }
            Ok(out)
        } else {
            Self::defaults(robot, profile)
        }
    }
}
pub fn validate_id(id: &str) -> Result<()> {
    if id.is_empty()
        || id == "."
        || id == ".."
        || !id
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'-' | b'.'))
    {
        bail!("Invalid robot identifier");
    }
    Ok(())
}
