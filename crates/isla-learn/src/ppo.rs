use crate::network::{clip_norm, Adam, Mlp, Normalizer};
use anyhow::{ensure, Result};
use ndarray::{Array1, Array2, ArrayD, Axis};
use rand::{rngs::StdRng, seq::SliceRandom, Rng, SeedableRng};
use rand_distr::StandardNormal;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct PpoSettings {
    pub hidden: Vec<usize>,
    pub learning_rate: f64,
    pub desired_kl: f64,
    pub clip: f64,
    pub value_coef: f64,
    pub entropy_coef: f64,
    pub gamma: f64,
    pub lam: f64,
    pub epochs: usize,
    pub minibatches: usize,
    pub max_grad_norm: f64,
    pub init_std: f64,
    pub min_std: f64,
    pub max_std: f64,
}
impl Default for PpoSettings {
    fn default() -> Self {
        Self {
            hidden: vec![256, 128, 64],
            learning_rate: 1e-3,
            desired_kl: 0.01,
            clip: 0.2,
            value_coef: 1.0,
            entropy_coef: 0.01,
            gamma: 0.99,
            lam: 0.95,
            epochs: 5,
            minibatches: 4,
            max_grad_norm: 1.0,
            init_std: 1.0,
            min_std: 0.02,
            max_std: 1.0,
        }
    }
}
impl PpoSettings {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            !self.hidden.is_empty() && self.hidden.iter().all(|&n| n > 0 && n <= 16384),
            "Hidden layers need 1 to 16384 units"
        );
        ensure!(
            [
                self.learning_rate,
                self.clip,
                self.init_std,
                self.min_std,
                self.max_std
            ]
            .iter()
            .all(|v| v.is_finite() && *v > 0.0),
            "PPO scales must be finite and positive"
        );
        ensure!(
            [
                self.desired_kl,
                self.value_coef,
                self.entropy_coef,
                self.max_grad_norm
            ]
            .iter()
            .all(|v| v.is_finite() && *v >= 0.0),
            "PPO coefficients must be finite and nonnegative"
        );
        ensure!(
            self.max_std >= self.min_std
                && self.gamma > 0.0
                && self.gamma <= 1.0
                && (0.0..=1.0).contains(&self.lam)
                && self.epochs > 0
                && self.minibatches > 0,
            "Invalid PPO settings"
        );
        Ok(())
    }
}

#[derive(Clone, Debug)]
pub struct Batch {
    pub obs: Array2<f32>,
    pub critic_obs: Array2<f32>,
    pub actions: Array2<f32>,
    pub means: Array2<f32>,
    pub log_probs: Array1<f32>,
    pub values: Array1<f32>,
    pub returns: Array1<f32>,
    pub advantages: Array1<f32>,
}
impl Batch {
    pub fn select(&self, rows: &[usize]) -> Self {
        Self {
            obs: self.obs.select(Axis(0), rows),
            critic_obs: self.critic_obs.select(Axis(0), rows),
            actions: self.actions.select(Axis(0), rows),
            means: self.means.select(Axis(0), rows),
            log_probs: self.log_probs.select(Axis(0), rows),
            values: self.values.select(Axis(0), rows),
            returns: self.returns.select(Axis(0), rows),
            advantages: self.advantages.select(Axis(0), rows),
        }
    }
    pub fn validate(&self, obs: usize, critic: usize, actions: usize) -> Result<()> {
        let n = self.obs.nrows();
        ensure!(
            n > 0
                && self.obs.ncols() == obs
                && self.critic_obs.dim() == (n, critic)
                && self.actions.dim() == (n, actions)
                && self.means.dim() == (n, actions)
                && self.log_probs.len() == n
                && self.values.len() == n
                && self.returns.len() == n
                && self.advantages.len() == n,
            "PPO batch shape mismatch"
        );
        ensure!(
            self.obs
                .iter()
                .chain(self.critic_obs.iter())
                .chain(self.actions.iter())
                .chain(self.means.iter())
                .chain(self.log_probs.iter())
                .chain(self.values.iter())
                .chain(self.returns.iter())
                .chain(self.advantages.iter())
                .all(|v| v.is_finite()),
            "Non-finite PPO batch"
        );
        Ok(())
    }
}
pub struct Rollout {
    pub steps: usize,
    pub envs: usize,
    pub batch: Batch,
    pub rewards: Array2<f32>,
    pub dones: Array2<f32>,
}
impl Rollout {
    pub fn new(
        steps: usize,
        envs: usize,
        obs: usize,
        critic: usize,
        actions: usize,
    ) -> Result<Self> {
        ensure!(steps > 0 && envs > 0, "Empty rollout");
        let n = steps
            .checked_mul(envs)
            .ok_or_else(|| anyhow::anyhow!("Rollout too large"))?;
        Ok(Self {
            steps,
            envs,
            batch: Batch {
                obs: Array2::zeros((n, obs)),
                critic_obs: Array2::zeros((n, critic)),
                actions: Array2::zeros((n, actions)),
                means: Array2::zeros((n, actions)),
                log_probs: Array1::zeros(n),
                values: Array1::zeros(n),
                returns: Array1::zeros(n),
                advantages: Array1::zeros(n),
            },
            rewards: Array2::zeros((steps, envs)),
            dones: Array2::zeros((steps, envs)),
        })
    }
}

pub fn advantages(
    rewards: &Array2<f32>,
    values: &Array2<f32>,
    dones: &Array2<f32>,
    last: &Array1<f32>,
    gamma: f64,
    lam: f64,
) -> Result<Array2<f32>> {
    ensure!(
        rewards.dim() == values.dim()
            && dones.dim() == values.dim()
            && last.len() == values.ncols(),
        "GAE shape mismatch"
    );
    let mut out = Array2::zeros(values.dim());
    let mut running = Array1::<f32>::zeros(last.len());
    for t in (0..values.nrows()).rev() {
        for e in 0..last.len() {
            let next = if t + 1 == values.nrows() {
                last[e]
            } else {
                values[[t + 1, e]]
            };
            let alive = 1.0 - dones[[t, e]];
            let delta = rewards[[t, e]] + gamma as f32 * next * alive - values[[t, e]];
            running[e] = delta + (gamma * lam) as f32 * alive * running[e];
            out[[t, e]] = running[e];
        }
    }
    Ok(out)
}

pub struct Ppo {
    pub settings: PpoSettings,
    pub actor: Mlp,
    pub critic: Mlp,
    pub log_std: Array1<f32>,
    pub obs_normalizer: Normalizer,
    pub critic_normalizer: Normalizer,
    pub optimizer: Adam,
    pub learning_rate: f64,
    pub updates: u64,
    pub rng: StdRng,
}
impl Ppo {
    pub fn new(
        obs: usize,
        critic: usize,
        actions: usize,
        settings: PpoSettings,
        seed: u64,
    ) -> Result<Self> {
        settings.validate()?;
        let mut rng = StdRng::seed_from_u64(seed);
        let mut sizes = vec![obs];
        sizes.extend(&settings.hidden);
        sizes.push(actions);
        let actor = Mlp::new(&sizes, 0.01, &mut rng)?;
        sizes[0] = critic;
        *sizes.last_mut().unwrap() = 1;
        let critic_net = Mlp::new(&sizes, 1.0, &mut rng)?;
        let log_std = Array1::from_elem(actions, settings.init_std.ln() as f32);
        let mut params = actor.params();
        params.extend(critic_net.params());
        params.push(log_std.clone().into_dyn());
        let optimizer = Adam::new(&params);
        let learning_rate = settings.learning_rate;
        Ok(Self {
            settings,
            actor,
            critic: critic_net,
            log_std,
            obs_normalizer: Normalizer::new(obs),
            critic_normalizer: Normalizer::new(critic),
            optimizer,
            learning_rate,
            updates: 0,
            rng,
        })
    }
    pub fn std(&self) -> Array1<f32> {
        self.log_std.mapv(f32::exp)
    }
    pub fn normalize(
        &mut self,
        obs: &Array2<f32>,
        critic: &Array2<f32>,
    ) -> Result<(Array2<f32>, Array2<f32>)> {
        let scaled = (
            self.obs_normalizer.normalize(obs)?,
            self.critic_normalizer.normalize(critic)?,
        );
        self.obs_normalizer.update(obs)?;
        self.critic_normalizer.update(critic)?;
        Ok(scaled)
    }
    pub fn act(&mut self, obs: &Array2<f32>) -> Result<(Array2<f32>, Array1<f32>, Array2<f32>)> {
        let mean = self.actor.predict(obs)?;
        let std = self.std();
        let mut actions = mean.clone();
        let mut probs = Array1::zeros(mean.nrows());
        let constant = self.log_std.sum()
            + 0.5 * (2.0 * std::f64::consts::PI).ln() as f32 * mean.ncols() as f32;
        for r in 0..mean.nrows() {
            let mut norm = 0.0;
            for c in 0..mean.ncols() {
                let z: f32 = self.rng.sample(StandardNormal);
                actions[[r, c]] += std[c] * z;
                norm += z * z;
            }
            probs[r] = -0.5 * norm - constant;
        }
        Ok((actions, probs, mean))
    }
    pub fn value(&self, obs: &Array2<f32>) -> Result<Array1<f32>> {
        Ok(self.critic.predict(obs)?.column(0).to_owned())
    }
    pub fn finish(&self, rollout: &mut Rollout, last_obs: &Array2<f32>) -> Result<()> {
        let last = self.value(last_obs)?;
        let values = rollout
            .batch
            .values
            .clone()
            .into_shape_with_order((rollout.steps, rollout.envs))?;
        let a = advantages(
            &rollout.rewards,
            &values,
            &rollout.dones,
            &last,
            self.settings.gamma,
            self.settings.lam,
        )?;
        rollout.batch.advantages = Array1::from_iter(a.iter().copied());
        rollout.batch.returns = &rollout.batch.advantages + &rollout.batch.values;
        Ok(())
    }
    pub fn params(&self) -> Vec<ArrayD<f32>> {
        let mut p = self.actor.params();
        p.extend(self.critic.params());
        p.push(self.log_std.clone().into_dyn());
        p
    }
    pub fn set_params(&mut self, p: &[ArrayD<f32>]) -> Result<()> {
        let actor = self.actor.weights.len() * 2;
        let critic = self.critic.weights.len() * 2;
        ensure!(
            p.len() == actor + critic + 1,
            "PPO parameter count mismatch"
        );
        self.actor.set_params(&p[..actor])?;
        self.critic.set_params(&p[actor..actor + critic])?;
        ensure!(
            p[actor + critic].shape() == self.log_std.shape(),
            "Noise shape mismatch"
        );
        self.log_std.assign(
            &p[actor + critic]
                .view()
                .into_dimensionality::<ndarray::Ix1>()?,
        );
        Ok(())
    }
    pub fn losses(
        &self,
        b: &Batch,
        old_std: &Array1<f32>,
    ) -> Result<(BTreeMap<String, f64>, Vec<ArrayD<f32>>)> {
        b.validate(
            self.actor.weights[0].nrows(),
            self.critic.weights[0].nrows(),
            self.log_std.len(),
        )?;
        ensure!(
            old_std.len() == self.log_std.len()
                && old_std.iter().all(|v| v.is_finite() && *v > 0.0),
            "Invalid old policy spread"
        );
        let (mean, actor_inputs) = self.actor.forward(&b.obs)?;
        let (value, critic_inputs) = self.critic.forward(&b.critic_obs)?;
        let n = b.obs.nrows();
        let size = self.log_std.len();
        let std = self.std();
        let s = &self.settings;
        let mut d_mean = Array2::<f32>::zeros(mean.dim());
        let mut d_std = Array1::<f32>::zeros(size);
        let mut d_value = Array2::<f32>::zeros((n, 1));
        let mut surrogate = 0.0f64;
        let mut value_loss = 0.0f64;
        let mut kl = 0.0f64;
        let log_constant =
            self.log_std.sum() + 0.5 * (2.0 * std::f64::consts::PI).ln() as f32 * size as f32;
        for r in 0..n {
            let mut z = vec![0.0f32; size];
            let mut logp = -log_constant;
            for c in 0..size {
                let spread = std[c];
                z[c] = (b.actions[[r, c]] - mean[[r, c]]) / spread;
                logp -= 0.5 * z[c] * z[c];
                kl += ((spread / old_std[c]).ln()
                    + (old_std[c] * old_std[c] + (b.means[[r, c]] - mean[[r, c]]).powi(2))
                        / (2.0 * spread * spread)
                    - 0.5) as f64;
            }
            let ratio = (logp - b.log_probs[r]).clamp(-20.0, 20.0).exp();
            let adv = b.advantages[r];
            let plain = ratio * adv;
            let clipped = ratio.clamp(1.0 - s.clip as f32, 1.0 + s.clip as f32) * adv;
            surrogate -= plain.min(clipped) as f64;
            let d_log = if plain <= clipped {
                -(ratio * adv) / n as f32
            } else {
                0.0
            };
            for c in 0..size {
                d_mean[[r, c]] = d_log * z[c] / std[c];
                d_std[c] += d_log * (z[c] * z[c] - 1.0);
            }
            let moved = value[[r, 0]] - b.values[r];
            let vc = b.values[r] + moved.clamp(-s.clip as f32, s.clip as f32);
            let error = value[[r, 0]] - b.returns[r];
            let ec = vc - b.returns[r];
            let use_plain = error * error >= ec * ec;
            value_loss += if use_plain { error * error } else { ec * ec } as f64;
            d_value[[r, 0]] = if use_plain {
                2.0 * error
            } else if moved.abs() < s.clip as f32 {
                2.0 * ec
            } else {
                0.0
            } * (s.value_coef / n as f64) as f32;
        }
        d_std -= s.entropy_coef as f32;
        surrogate /= n as f64;
        value_loss /= n as f64;
        kl /= n as f64;
        let entropy = self.log_std.sum() as f64
            + 0.5 * (1.0 + (2.0 * std::f64::consts::PI).ln()) * size as f64;
        let mut grads = self.actor.backward(&d_mean, &actor_inputs)?;
        grads.extend(self.critic.backward(&d_value, &critic_inputs)?);
        grads.push(d_std.into_dyn());
        let parts = BTreeMap::from([
            ("surrogate".into(), surrogate),
            ("value".into(), value_loss),
            ("entropy".into(), entropy),
            (
                "loss".into(),
                surrogate + s.value_coef * value_loss - s.entropy_coef * entropy,
            ),
            ("kl".into(), kl),
        ]);
        Ok((parts, grads))
    }
    pub fn update(&mut self, rollout: &Rollout) -> Result<BTreeMap<String, f64>> {
        let mut flat = rollout.batch.clone();
        let n = flat.obs.nrows();
        ensure!(n > 0, "Empty rollout");
        let mean = flat.advantages.sum() / n as f32;
        let var = flat
            .advantages
            .iter()
            .map(|x| (x - mean).powi(2))
            .sum::<f32>()
            / n as f32;
        flat.advantages
            .mapv_inplace(|x| (x - mean) / (var.sqrt() + 1e-8));
        let old_std = self.std();
        // Divide into at most N nonempty batches and include remainder samples.
        // The old Python loop dropped remainders and produced empty batches when
        // minibatches exceeded the sample count.
        let batches = self.settings.minibatches.min(n);
        let chunk = n.div_ceil(batches);
        let mut totals = BTreeMap::<String, f64>::new();
        let mut count = 0;
        for _ in 0..self.settings.epochs {
            let mut order = (0..n).collect::<Vec<_>>();
            order.shuffle(&mut self.rng);
            for pick in order.chunks(chunk) {
                let batch = flat.select(pick);
                let (parts, mut grads) = self.losses(&batch, &old_std)?;
                let kl = parts["kl"];
                if self.settings.desired_kl > 0.0 {
                    if kl > 2.0 * self.settings.desired_kl {
                        self.learning_rate = (self.learning_rate / 1.5).max(1e-5);
                    } else if kl > 0.0 && kl < self.settings.desired_kl / 2.0 {
                        self.learning_rate = (self.learning_rate * 1.5).min(1e-2);
                    }
                }
                clip_norm(&mut grads, self.settings.max_grad_norm);
                let mut params = self.params();
                self.optimizer
                    .step(&mut params, &grads, self.learning_rate)?;
                self.set_params(&params)?;
                self.log_std.mapv_inplace(|x| {
                    x.clamp(
                        self.settings.min_std.ln() as f32,
                        self.settings.max_std.ln() as f32,
                    )
                });
                for key in ["surrogate", "value", "entropy", "kl"] {
                    *totals.entry(key.into()).or_default() += parts[key];
                }
                count += 1;
            }
        }
        self.updates += 1;
        for v in totals.values_mut() {
            *v /= count as f64;
        }
        totals.insert("learning_rate".into(), self.learning_rate);
        totals.insert(
            "std".into(),
            self.std().sum() as f64 / self.log_std.len() as f64,
        );
        Ok(totals)
    }
}
