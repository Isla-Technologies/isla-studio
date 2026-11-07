use crate::{
    policy::{Commands, Gait},
    ppo::PpoSettings,
};
use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Jumping {
    pub seconds: f64,
    pub first: [f64; 2],
    pub every: [f64; 2],
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Skill {
    pub key: String,
    pub title: String,
    pub summary: String,
    pub detail: String,
    pub terms: Vec<String>,
    pub weights: BTreeMap<String, f64>,
    pub steps: u64,
    pub push_speed: f64,
    pub push_every: [f64; 2],
    pub init_std: f64,
    pub gait: Option<Gait>,
    pub episode: f64,
    pub commands: Commands,
    pub curriculum: Option<[f64; 2]>,
    pub run_practice: f64,
    pub impact: [f64; 2],
    pub jumping: Option<Jumping>,
}
pub fn skills() -> BTreeMap<String, Skill> {
    serde_json::from_str(include_str!("../data/skills.json")).expect("Embedded skills are valid")
}
pub fn catalogue() -> Value {
    serde_json::from_str(include_str!("../data/catalogue.json"))
        .expect("Embedded catalogue is valid")
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Robustness {
    pub friction: [f64; 2],
    pub added_mass: [f64; 2],
    pub strength: [f64; 2],
    pub strut_spread: f64,
    pub push_speed: Option<f64>,
    pub push_every: Option<[f64; 2]>,
    pub noise: f64,
    pub delay: f64,
}
impl Default for Robustness {
    fn default() -> Self {
        Self {
            friction: [0.5, 1.5],
            added_mass: [-1.0, 2.5],
            strength: [0.9, 1.1],
            strut_spread: 0.1,
            push_speed: None,
            push_every: None,
            noise: 1.0,
            delay: 0.01,
        }
    }
}
impl Robustness {
    pub fn validate(&self) -> Result<()> {
        for r in [self.friction, self.added_mass, self.strength] {
            ensure!(
                r.iter().all(|x| x.is_finite()) && r[0] <= r[1],
                "Randomization ranges must be finite and ordered"
            );
        }
        ensure!(
            self.friction[0] > 0.0 && self.strength[0] > 0.0,
            "Friction and strength must stay positive"
        );
        ensure!(
            (0.0..=0.5).contains(&self.strut_spread)
                && (0.0..=3.0).contains(&self.noise)
                && (0.0..=0.05).contains(&self.delay),
            "Invalid noise, delay or shock spread"
        );
        if let Some(v) = self.push_speed {
            ensure!((0.0..=3.0).contains(&v), "Push speed is 0 to 3 m/s");
        }
        if let Some(r) = self.push_every {
            ensure!(
                r.iter().all(|v| v.is_finite()) && r[0] > 0.0 && r[0] <= r[1],
                "Push interval must be positive and ordered"
            );
        }
        Ok(())
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Codesign {
    pub enabled: bool,
    pub params: Vec<String>,
    pub pool: usize,
    pub warmup: f64,
    pub finetune: f64,
    pub updates: usize,
    pub elite: f64,
    pub smoothing: f64,
    pub init_std: f64,
    pub min_std: f64,
    pub min_episodes: usize,
    pub target_height: Option<f64>,
    pub height_weight: f64,
}
impl Default for Codesign {
    fn default() -> Self {
        Self {
            enabled: false,
            params: vec![],
            pool: 128,
            warmup: 0.15,
            finetune: 0.15,
            updates: 24,
            elite: 0.25,
            smoothing: 0.35,
            init_std: 0.35,
            min_std: 0.03,
            min_episodes: 4,
            target_height: None,
            height_weight: 4.0,
        }
    }
}
impl Codesign {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            self.pool >= 8 && self.pool <= 16384,
            "Design pool needs 8 to 16384 candidates"
        );
        ensure!(
            (0.0..0.9).contains(&self.warmup)
                && (0.0..0.9).contains(&self.finetune)
                && self.warmup + self.finetune < 0.95,
            "Warmup and fine-tuning must leave time to search"
        );
        ensure!(
            self.elite > 0.0
                && self.elite < 1.0
                && (0.0..=1.0).contains(&self.smoothing)
                && self.init_std.is_finite()
                && self.init_std > 0.0
                && self.min_std.is_finite()
                && self.min_std > 0.0
                && self.updates > 0
                && self.min_episodes > 0,
            "Invalid design search parameters"
        );
        ensure!(
            self.height_weight.is_finite()
                && self.height_weight >= 0.0
                && self.target_height.is_none_or(|v| v.is_finite() && v > 0.0),
            "Invalid design target height"
        );
        Ok(())
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct TrainingSetup {
    pub robot: String,
    pub skill: String,
    pub weights: BTreeMap<String, f64>,
    pub commands: Option<Commands>,
    pub robustness: Robustness,
    pub codesign: Codesign,
    pub design: BTreeMap<String, f64>,
    pub arms: bool,
    pub robots: usize,
    pub steps: Option<u64>,
    pub rollout: usize,
    pub episode: Option<f64>,
    pub seed: u64,
    pub ppo: Option<PpoSettings>,
}
impl Default for TrainingSetup {
    fn default() -> Self {
        Self {
            robot: "luma_1".into(),
            skill: "walk".into(),
            weights: BTreeMap::new(),
            commands: None,
            robustness: Robustness::default(),
            codesign: Codesign::default(),
            design: BTreeMap::new(),
            arms: false,
            robots: 1024,
            steps: None,
            rollout: 24,
            episode: None,
            seed: 1,
            ppo: None,
        }
    }
}
impl TrainingSetup {
    pub fn from_value(value: Value) -> Result<Self> {
        Self::from_document(&value)
    }
    pub fn to_value(&self) -> Value {
        serde_json::to_value(self).expect("Validated setup serializes")
    }
    pub fn from_document(value: &Value) -> Result<Self> {
        let mut value = value.clone();
        for key in ["weights", "robustness", "codesign", "design"] {
            if value[key].is_null() {
                value
                    .as_object_mut()
                    .context("Training setup must be an object")?
                    .remove(key);
            }
        }
        let setup: Self = serde_json::from_value(value).context("Not a readable training setup")?;
        setup.validate()?;
        Ok(setup)
    }
    pub fn from_json(text: &str) -> Result<Self> {
        Self::from_document(&serde_json::from_str(text)?)
    }
    pub fn to_document(&self) -> Result<Value> {
        self.validate()?;
        Ok(serde_json::to_value(self)?)
    }
    pub fn validate(&self) -> Result<()> {
        ensure!(
            skills().contains_key(&self.skill),
            "Unknown skill {}",
            self.skill
        );
        ensure!(!self.robot.is_empty(), "A robot is required");
        ensure!(
            (1..=16384).contains(&self.robots)
                && (1..=1000).contains(&self.rollout)
                && self.steps.is_none_or(|v| v > 0)
                && self.episode.is_none_or(|v| (1.0..=600.0).contains(&v)),
            "Invalid robot count, rollout or training duration"
        );
        let cat = catalogue();
        let rewards = cat["rewards"].as_array().unwrap();
        for (k, v) in &self.weights {
            ensure!(
                rewards.iter().any(|r| r["key"] == *k) && v.is_finite(),
                "Invalid reward {k}"
            );
        }
        ensure!(
            self.design.values().all(|v| v.is_finite()),
            "Design values must be finite"
        );
        if let Some(c) = &self.commands {
            c.validate()?;
        }
        self.robustness.validate()?;
        self.codesign.validate()?;
        if let Some(p) = &self.ppo {
            p.validate()?;
        }
        Ok(())
    }
    pub fn about(&self) -> Skill {
        skills().remove(&self.skill).expect("Validated skill")
    }
    pub fn command_ranges(&self) -> Commands {
        self.commands
            .clone()
            .unwrap_or_else(|| self.about().commands)
    }
    pub fn total_steps(&self) -> u64 {
        self.steps.unwrap_or_else(|| self.about().steps)
    }
    pub fn episode_seconds(&self) -> f64 {
        self.episode.unwrap_or_else(|| self.about().episode)
    }
    pub fn reward_weights(&self) -> BTreeMap<String, f64> {
        let mut weights = self.about().weights;
        weights.extend(self.weights.clone());
        weights
    }
    pub fn settings(&self) -> PpoSettings {
        self.ppo.clone().unwrap_or_else(|| PpoSettings {
            init_std: self.about().init_std,
            ..PpoSettings::default()
        })
    }
    pub fn push_speed(&self) -> f64 {
        self.robustness
            .push_speed
            .unwrap_or_else(|| self.about().push_speed)
    }
    pub fn push_every(&self) -> [f64; 2] {
        self.robustness
            .push_every
            .unwrap_or_else(|| self.about().push_every)
    }
}
