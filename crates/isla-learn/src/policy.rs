//! The existing `isla-policy` JSON format and stateful policy execution.
use crate::network::Mlp;
use anyhow::{bail, ensure, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use ndarray::{Array1, Array2, ArrayD, IxDyn};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, HashSet},
    f64::consts::TAU,
    path::Path,
};

pub const GYRO_SCALE: f64 = 0.25;
pub const SPEED_SCALE: f64 = 0.05;
pub const COMMAND_SCALE: [f64; 3] = [2.0, 2.0, 0.25];
pub const RAMP: [f64; 3] = [1.0, 0.8, 1.5];

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Commands {
    pub forward: [f64; 2],
    pub sideways: f64,
    pub turning: f64,
}
impl Default for Commands {
    fn default() -> Self {
        Self {
            forward: [-0.3, 0.6],
            sideways: 0.25,
            turning: 0.8,
        }
    }
}
impl Commands {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            self.forward.iter().all(|v| v.is_finite())
                && self.forward[0] <= self.forward[1]
                && self.sideways.is_finite()
                && self.sideways >= 0.0
                && self.turning.is_finite()
                && self.turning >= 0.0,
            "Invalid command ranges"
        );
        Ok(())
    }
    pub fn clamp(&self, c: [f64; 3]) -> [f64; 3] {
        [
            c[0].clamp(self.forward[0], self.forward[1]),
            c[1].clamp(-self.sideways, self.sideways),
            c[2].clamp(-self.turning, self.turning),
        ]
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Gait {
    pub period: [f64; 2],
    pub stance: [f64; 2],
    pub swing_height: [f64; 2],
    pub speeds: [f64; 2],
}
impl Default for Gait {
    fn default() -> Self {
        Self {
            period: [0.7, 0.7],
            stance: [0.55, 0.55],
            swing_height: [0.04, 0.04],
            speeds: [0.5, 1.0],
        }
    }
}
impl Gait {
    pub fn run_share(&self, forward: f64) -> f64 {
        ((forward - self.speeds[0]) / (self.speeds[1] - self.speeds[0]).max(1e-6)).clamp(0.0, 1.0)
    }
    pub fn period_at(&self, share: f64) -> f64 {
        blend(self.period, share)
    }
    pub fn stance_at(&self, share: f64) -> f64 {
        blend(self.stance, share)
    }
    pub fn swing_at(&self, share: f64) -> f64 {
        blend(self.swing_height, share)
    }
    fn validate(&self) -> Result<()> {
        ensure!(
            self.period.iter().all(|v| v.is_finite() && *v > 0.0),
            "Gait periods must be positive"
        );
        ensure!(
            self.stance
                .iter()
                .all(|v| v.is_finite() && (0.0..=1.0).contains(v))
                && self.swing_height.iter().all(|v| v.is_finite() && *v >= 0.0)
                && self.speeds.iter().all(|v| v.is_finite()),
            "Invalid gait values"
        );
        Ok(())
    }
}
fn blend(pair: [f64; 2], share: f64) -> f64 {
    pair[0] + (pair[1] - pair[0]) * share
}

pub fn cross(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}
pub fn frame_matrix(forward: [f64; 3], left: [f64; 3]) -> [[f64; 3]; 3] {
    [forward, left, cross(forward, left)]
}
pub fn quat_rotate_inverse(q: [f64; 4], v: [f64; 3]) -> [f64; 3] {
    let u = [-q[1], -q[2], -q[3]];
    let t = cross(u, v).map(|x| 2.0 * x);
    let c = cross(u, t);
    std::array::from_fn(|i| v[i] + q[0] * t[i] + c[i])
}
pub fn gravity_in_base(q: [f64; 4]) -> [f64; 3] {
    quat_rotate_inverse(q, [0.0, 0.0, -1.0])
}
pub fn rotate(matrix: [[f64; 3]; 3], v: [f64; 3]) -> [f64; 3] {
    std::array::from_fn(|i| (0..3).map(|j| matrix[i][j] * v[j]).sum())
}

pub fn size_of(terms: &[String], joints: usize, design: usize) -> Result<usize> {
    let mut size = 0usize;
    for term in terms {
        size = size
            .checked_add(match term.as_str() {
                "gyro" | "gravity" | "command" | "jump" => 3,
                "joints" | "speeds" | "actions" => joints,
                "phase" => 2,
                "design" => design,
                _ => bail!("A policy cannot sense {term:?}"),
            })
            .context("Observation size overflow")?;
    }
    Ok(size)
}
pub struct Observation<'a> {
    pub quaternion: [f64; 4],
    pub gyro: [f64; 3],
    pub positions: &'a [f64],
    pub velocities: &'a [f64],
    pub default: &'a [f64],
    pub last_actions: &'a [f32],
    pub command: [f64; 3],
    pub phase: f64,
    pub design: &'a [f32],
    pub jump: [f64; 2],
}
pub fn observe(terms: &[String], to_robot: [[f64; 3]; 3], o: &Observation<'_>) -> Result<Vec<f32>> {
    let n = o.default.len();
    ensure!(
        o.positions.len() == n && o.velocities.len() == n && o.last_actions.len() == n,
        "Observation joint dimensions differ"
    );
    let mut parts = Vec::with_capacity(size_of(terms, n, o.design.len())?);
    for term in terms {
        match term.as_str() {
            "gyro" => parts.extend(rotate(to_robot, o.gyro).map(|x| (x * GYRO_SCALE) as f32)),
            "gravity" => {
                parts.extend(rotate(to_robot, gravity_in_base(o.quaternion)).map(|x| x as f32))
            }
            "command" => parts.extend((0..3).map(|i| (o.command[i] * COMMAND_SCALE[i]) as f32)),
            "joints" => parts.extend(
                o.positions
                    .iter()
                    .zip(o.default)
                    .map(|(x, d)| (x - d) as f32),
            ),
            "speeds" => parts.extend(o.velocities.iter().map(|v| (v * SPEED_SCALE) as f32)),
            "actions" => parts.extend_from_slice(o.last_actions),
            "phase" => parts.extend([(TAU * o.phase).sin() as f32, (TAU * o.phase).cos() as f32]),
            "jump" => parts.extend([
                o.jump[0] as f32,
                ((TAU * o.jump[1]).sin() * o.jump[0]) as f32,
                ((TAU * o.jump[1]).cos() * o.jump[0]) as f32,
            ]),
            "design" => parts.extend(o.design.iter().map(|v| v * 2.0 - 1.0)),
            _ => bail!("A policy cannot sense {term:?}"),
        }
    }
    ensure!(
        parts.iter().all(|v| v.is_finite()),
        "Policy observation contains non-finite values"
    );
    Ok(parts)
}

pub fn pack(array: &ArrayD<f32>) -> Value {
    let bytes: Vec<u8> = array.iter().flat_map(|v| v.to_le_bytes()).collect();
    json!({"shape":array.shape(),"data":STANDARD.encode(bytes)})
}
pub fn unpack(value: &Value) -> Result<ArrayD<f32>> {
    let shape: Vec<usize> =
        serde_json::from_value(value.get("shape").context("Array shape missing")?.clone())?;
    ensure!(
        !shape.is_empty() && shape.len() <= 3,
        "Invalid policy array rank"
    );
    let size = shape
        .iter()
        .try_fold(1usize, |n, &v| n.checked_mul(v))
        .context("Policy array shape overflow")?;
    ensure!(size <= 100_000_000, "Policy array exceeds size limit");
    let encoded = value["data"].as_str().context("Array data missing")?;
    ensure!(
        encoded.len() <= size.saturating_mul(4).div_ceil(3) * 4 + 4,
        "Array data exceeds declared shape"
    );
    let bytes = STANDARD.decode(encoded).context("Invalid base64 array")?;
    ensure!(
        bytes.len() == size.checked_mul(4).context("Array size overflow")?,
        "Array byte length differs from shape"
    );
    let data = bytes
        .chunks_exact(4)
        .map(|v| f32::from_le_bytes(v.try_into().unwrap()))
        .collect::<Vec<_>>();
    ensure!(
        data.iter().all(|v| v.is_finite()),
        "Array contains non-finite values"
    );
    Ok(ArrayD::from_shape_vec(IxDyn(&shape), data)?)
}

#[derive(Debug, Clone)]
pub struct Policy {
    pub name: String,
    pub robot: String,
    pub skill: String,
    pub joints: Vec<String>,
    pub default: Vec<f64>,
    pub hold: BTreeMap<String, f64>,
    pub gains: Vec<[f64; 2]>,
    pub terms: Vec<String>,
    pub network: Mlp,
    pub obs_mean: Array1<f32>,
    pub obs_std: Array1<f32>,
    pub to_robot: [[f64; 3]; 3],
    pub action_scale: f64,
    pub action_clip: f64,
    pub rate: f64,
    pub gait: Option<Gait>,
    pub jump_seconds: Option<f64>,
    pub commands: Option<Commands>,
    pub design: BTreeMap<String, f64>,
    pub design_keys: Vec<String>,
    pub design_ranges: Vec<[f64; 2]>,
    pub about: String,
    pub stats: Value,
    pub created: String,
}

#[derive(Deserialize)]
struct Document {
    format: String,
    name: String,
    #[serde(default)]
    robot: String,
    skill: String,
    joints: Vec<String>,
    default: Vec<f64>,
    #[serde(default)]
    hold: BTreeMap<String, f64>,
    gains: Vec<[f64; 2]>,
    terms: Vec<String>,
    to_robot: [[f64; 3]; 3],
    action_scale: f64,
    action_clip: f64,
    rate: f64,
    #[serde(default)]
    gait: Option<Gait>,
    #[serde(default)]
    gait_period: Option<f64>,
    #[serde(default)]
    jump_seconds: Option<f64>,
    #[serde(default)]
    commands: Option<Commands>,
    #[serde(default)]
    design: Option<BTreeMap<String, f64>>,
    #[serde(default)]
    design_keys: Vec<String>,
    #[serde(default)]
    design_ranges: Vec<[f64; 2]>,
    #[serde(default)]
    about: String,
    #[serde(default)]
    stats: Value,
    #[serde(default)]
    created: String,
    input: Value,
    network: Vec<Value>,
}
impl Policy {
    pub fn load(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        let text = std::fs::read_to_string(path)
            .with_context(|| format!("Cannot read policy {}", path.display()))?;
        Self::from_document(&serde_json::from_str(&text)?)
    }
    pub fn from_document(value: &Value) -> Result<Self> {
        let d: Document = serde_json::from_value(value.clone()).context("Not a readable policy")?;
        ensure!(d.format == "isla-policy", "Not an Isla policy");
        let mut network = Mlp {
            weights: Vec::new(),
            biases: Vec::new(),
        };
        for layer in d.network {
            network
                .weights
                .push(unpack(&layer["weight"])?.into_dimensionality()?);
            network
                .biases
                .push(unpack(&layer["bias"])?.into_dimensionality()?);
        }
        let gait = d.gait.or_else(|| {
            d.gait_period.filter(|v| *v != 0.0).map(|p| Gait {
                period: [p, p],
                ..Gait::default()
            })
        });
        let policy = Self {
            name: d.name,
            robot: d.robot,
            skill: d.skill,
            joints: d.joints,
            default: d.default,
            hold: d.hold,
            gains: d.gains,
            terms: d.terms,
            network,
            obs_mean: unpack(&d.input["mean"])?.into_dimensionality()?,
            obs_std: unpack(&d.input["std"])?.into_dimensionality()?,
            to_robot: d.to_robot,
            action_scale: d.action_scale,
            action_clip: d.action_clip,
            rate: d.rate,
            gait,
            jump_seconds: d.jump_seconds,
            commands: d.commands,
            design: d.design.unwrap_or_default(),
            design_keys: d.design_keys,
            design_ranges: d.design_ranges,
            about: d.about,
            stats: d.stats,
            created: d.created,
        };
        policy.validate()?;
        Ok(policy)
    }
    pub fn validate(&self) -> Result<()> {
        let n = self.joints.len();
        ensure!(
            n > 0 && n == self.default.len() && n == self.gains.len(),
            "A policy needs a base pose and gains for each joint"
        );
        ensure!(
            self.joints.iter().all(|v| !v.is_empty())
                && self.joints.iter().collect::<HashSet<_>>().len() == n,
            "Policy joint names must be nonempty and unique"
        );
        self.network.validate()?;
        let size = size_of(&self.terms, n, self.design_keys.len())?;
        ensure!(
            self.network.weights[0].nrows() == size
                && self.network.weights.last().unwrap().ncols() == n,
            "Policy network does not fit what it senses and drives"
        );
        ensure!(
            self.obs_mean.len() == size
                && self.obs_std.len() == size
                && self.obs_std.iter().all(|v| v.is_finite() && *v > 0.0),
            "Invalid policy input scaling"
        );
        ensure!(
            self.design_keys.len() == self.design_ranges.len(),
            "Design keys and ranges differ"
        );
        ensure!(
            self.default
                .iter()
                .chain(self.hold.values())
                .chain(self.design.values())
                .chain(self.to_robot.iter().flatten())
                .chain(self.design_ranges.iter().flatten())
                .all(|v| v.is_finite()),
            "Non-finite policy configuration"
        );
        ensure!(
            self.gains
                .iter()
                .flatten()
                .all(|v| v.is_finite() && *v >= 0.0),
            "Invalid servo gains"
        );
        ensure!(
            self.action_scale.is_finite()
                && self.action_scale > 0.0
                && self.action_clip.is_finite()
                && self.action_clip > 0.0
                && self.rate.is_finite()
                && self.rate > 0.0,
            "Policy scale, clip and rate must be positive"
        );
        if let Some(g) = &self.gait {
            g.validate()?;
        }
        if let Some(c) = &self.commands {
            c.validate()?;
        }
        if let Some(j) = self.jump_seconds {
            ensure!(j.is_finite() && j > 0.0, "Jump duration must be positive");
        }
        Ok(())
    }
    pub fn design_vector(&self, design: Option<&BTreeMap<String, f64>>) -> Vec<f32> {
        self.design_keys
            .iter()
            .zip(&self.design_ranges)
            .map(|(k, [lo, hi])| {
                let v = design
                    .and_then(|d| d.get(k))
                    .or_else(|| self.design.get(k))
                    .copied()
                    .unwrap_or(*lo);
                if hi <= lo {
                    0.5
                } else {
                    ((v - lo) / (hi - lo)).clamp(0.0, 1.0) as f32
                }
            })
            .collect()
    }
    pub fn act(&self, obs: &Array2<f32>) -> Result<Array2<f32>> {
        ensure!(
            obs.ncols() == self.obs_mean.len() && obs.iter().all(|v| v.is_finite()),
            "Invalid policy observations"
        );
        // Saved means/std and network arithmetic remain float32, exactly as files specify.
        let scaled = ((obs - &self.obs_mean) / &self.obs_std).mapv(|v| v.clamp(-10.0, 10.0));
        self.network.predict(&scaled)
    }
    pub fn to_document(&self) -> Value {
        json!({"format":"isla-policy","version":1,"name":self.name,"robot":self.robot,"skill":self.skill,"created":self.created,"about":self.about,"joints":self.joints,"default":self.default,"hold":self.hold,"gains":self.gains,"terms":self.terms,"to_robot":self.to_robot,"action_scale":self.action_scale,"action_clip":self.action_clip,"rate":self.rate,"gait":self.gait,"jump_seconds":self.jump_seconds,"commands":self.commands,"design":self.design,"design_keys":self.design_keys,"design_ranges":self.design_ranges,"stats":self.stats,"input":{"mean":pack(&self.obs_mean.clone().into_dyn()),"std":pack(&self.obs_std.clone().into_dyn())},"network":self.network.weights.iter().zip(&self.network.biases).map(|(w,b)|json!({"weight":pack(&w.clone().into_dyn()),"bias":pack(&b.clone().into_dyn())})).collect::<Vec<_>>()})
    }
    pub fn to_json(&self) -> Result<String> {
        self.validate()?;
        Ok(serde_json::to_string(&self.to_document())? + "\n")
    }
}

#[derive(Debug, Clone)]
pub struct PolicyRunner {
    pub policy: Policy,
    pub command: [f64; 3],
    pub speed: Option<[f64; 3]>,
    pub elapsed: f64,
    pub phase: f64,
    pub jumping: Option<f64>,
    since: f64,
    actions: Vec<f32>,
    targets: BTreeMap<String, f64>,
    design: Vec<f32>,
}
impl PolicyRunner {
    pub fn new(policy: Policy, design: Option<&BTreeMap<String, f64>>) -> Result<Self> {
        policy.validate()?;
        let n = policy.joints.len();
        let targets = policy
            .joints
            .iter()
            .cloned()
            .zip(policy.default.iter().copied())
            .collect();
        let design = policy.design_vector(design);
        Ok(Self {
            policy,
            command: [0.0; 3],
            speed: None,
            elapsed: 0.0,
            phase: 0.0,
            jumping: None,
            since: f64::INFINITY,
            actions: vec![0.0; n],
            targets,
            design,
        })
    }
    pub fn set_command(&mut self, forward: f64, left: f64, turn: f64) -> Result<()> {
        let command = [forward, left, turn];
        ensure!(
            command.iter().all(|v| v.is_finite()),
            "Command must be finite"
        );
        self.command = self
            .policy
            .commands
            .as_ref()
            .map(|c| c.clamp(command))
            .unwrap_or([0.0; 3]);
        Ok(())
    }
    pub fn can_jump(&self) -> bool {
        self.policy.jump_seconds.is_some() && self.policy.terms.iter().any(|v| v == "jump")
    }
    pub fn jump(&mut self) -> bool {
        if !self.can_jump() || self.jumping.is_some() {
            return false;
        }
        self.jumping = Some(0.0);
        true
    }
    pub fn step(
        &mut self,
        dt: f64,
        quaternion: [f64; 4],
        gyro: [f64; 3],
        joints: &BTreeMap<String, f64>,
        velocities: &BTreeMap<String, f64>,
    ) -> Result<BTreeMap<String, f64>> {
        ensure!(
            dt.is_finite() && dt >= 0.0,
            "Policy timestep must be finite and nonnegative"
        );
        ensure!(
            quaternion
                .iter()
                .chain(gyro.iter())
                .chain(joints.values())
                .chain(velocities.values())
                .all(|v| v.is_finite()),
            "Non-finite robot state"
        );
        self.elapsed += dt;
        self.since += dt;
        let speed = self
            .speed
            .map(|s| {
                std::array::from_fn(|i| {
                    s[i] + (self.command[i] - s[i]).clamp(-RAMP[i] * dt, RAMP[i] * dt)
                })
            })
            .unwrap_or(self.command);
        self.speed = Some(speed);
        if let Some(g) = &self.policy.gait {
            self.phase = (self.phase + dt / g.period_at(g.run_share(speed[0]))).rem_euclid(1.0);
        }
        if let Some(j) = self.jumping {
            let value = j + dt / self.policy.jump_seconds.unwrap();
            self.jumping = if value >= 1.0 { None } else { Some(value) };
        }
        if self.since >= 1.0 / self.policy.rate - 1e-9 {
            self.since = 0.0;
            let p = &self.policy;
            let positions = p
                .joints
                .iter()
                .zip(&p.default)
                .map(|(j, d)| joints.get(j).copied().unwrap_or(*d))
                .collect::<Vec<_>>();
            let velocities = p
                .joints
                .iter()
                .map(|j| velocities.get(j).copied().unwrap_or(0.0))
                .collect::<Vec<_>>();
            let obs = observe(
                &p.terms,
                p.to_robot,
                &Observation {
                    quaternion,
                    gyro,
                    positions: &positions,
                    velocities: &velocities,
                    default: &p.default,
                    last_actions: &self.actions,
                    command: speed,
                    phase: self.phase,
                    design: &self.design,
                    jump: self.jumping.map(|v| [1.0, v]).unwrap_or([0.0, 0.0]),
                },
            )?;
            let values = p.act(&Array2::from_shape_vec((1, obs.len()), obs)?)?;
            for i in 0..self.actions.len() {
                let a = values[[0, i]].clamp(-p.action_clip as f32, p.action_clip as f32);
                ensure!(a.is_finite(), "Policy produced non-finite target");
                self.actions[i] = a;
                self.targets.insert(
                    p.joints[i].clone(),
                    p.default[i] + (p.action_scale as f32 * a) as f64,
                );
            }
        }
        let mut result = self.policy.hold.clone();
        result.extend(self.targets.clone());
        Ok(result)
    }
}
