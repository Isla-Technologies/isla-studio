//! Read and write the existing NumPy `.npz` training state, without Python.
use crate::{network::Mlp, ppo::Ppo};
use anyhow::{ensure, Context, Result};
use ndarray::{arr0, ArrayD, IxDyn};
use ndarray_npy::{NpzReader, NpzWriter};
use std::{collections::BTreeMap, fs::File, path::Path};

pub type State = BTreeMap<String, ArrayD<f64>>;
pub fn read(path: impl AsRef<Path>) -> Result<State> {
    let mut zip = NpzReader::new(File::open(path)?)?;
    let mut state = State::new();
    for name in zip.names()? {
        let v = if let Ok(a) = zip.by_name::<ndarray::OwnedRepr<f64>, IxDyn>(&name) {
            a
        } else if let Ok(a) = zip.by_name::<ndarray::OwnedRepr<f32>, IxDyn>(&name) {
            a.mapv(f64::from)
        } else {
            let a: ArrayD<i64> = zip
                .by_name(&name)
                .with_context(|| format!("Unsupported checkpoint array {name}"))?;
            a.mapv(|v| v as f64)
        };
        ensure!(
            v.iter().all(|x| x.is_finite()),
            "Non-finite checkpoint array {name}"
        );
        state.insert(name.trim_end_matches(".npy").into(), v);
    }
    Ok(state)
}
pub fn write(path: impl AsRef<Path>, state: &State) -> Result<()> {
    let mut zip = NpzWriter::new(File::create(path)?);
    for (k, v) in state {
        if k.starts_with("actor.")
            || k.starts_with("critic.")
            || k.starts_with("adam.") && k != "adam.steps"
            || k == "log_std"
        {
            zip.add_array(k, &v.mapv(|x| x as f32))?;
        } else if matches!(
            k.as_str(),
            "adam.steps" | "updates" | "steps" | "iteration" | "codesign.phase"
        ) {
            zip.add_array(k, &v.mapv(|x| x as i64))?;
        } else {
            zip.add_array(k, v)?;
        }
    }
    zip.finish()?;
    Ok(())
}
fn array<'a>(state: &'a State, key: &str) -> Result<&'a ArrayD<f64>> {
    state
        .get(key)
        .with_context(|| format!("Missing checkpoint array {key}"))
}
pub fn scalar(state: &State, key: &str) -> Result<f64> {
    let a = array(state, key)?;
    ensure!(a.len() == 1, "Checkpoint scalar {key} has wrong shape");
    Ok(*a.iter().next().unwrap())
}
pub fn insert_scalar(state: &mut State, key: &str, value: f64) {
    state.insert(key.into(), arr0(value).into_dyn());
}
fn network_state(state: &mut State, network: &Mlp, prefix: &str) {
    for (i, (w, b)) in network.weights.iter().zip(&network.biases).enumerate() {
        state.insert(format!("{prefix}.{i}.weight"), w.mapv(f64::from).into_dyn());
        state.insert(format!("{prefix}.{i}.bias"), b.mapv(f64::from).into_dyn());
    }
}
fn load_network(state: &State, network: &mut Mlp, prefix: &str) -> Result<()> {
    let mut params = Vec::new();
    for i in 0..network.weights.len() {
        for kind in ["weight", "bias"] {
            params.push(array(state, &format!("{prefix}.{i}.{kind}"))?.mapv(|x| x as f32));
        }
    }
    network.set_params(&params)?;
    network.validate()
}
impl Ppo {
    pub fn state(&self) -> State {
        let mut state = State::new();
        network_state(&mut state, &self.actor, "actor");
        network_state(&mut state, &self.critic, "critic");
        state.insert("log_std".into(), self.log_std.mapv(f64::from).into_dyn());
        for (prefix, n) in [
            ("obs_normalizer", &self.obs_normalizer),
            ("critic_normalizer", &self.critic_normalizer),
        ] {
            state.insert(format!("{prefix}.mean"), n.mean.clone().into_dyn());
            state.insert(format!("{prefix}.var"), n.var.clone().into_dyn());
            insert_scalar(&mut state, &format!("{prefix}.count"), n.count);
        }
        for (i, (m, v)) in self
            .optimizer
            .first
            .iter()
            .zip(&self.optimizer.second)
            .enumerate()
        {
            state.insert(format!("adam.{i}.first"), m.mapv(f64::from));
            state.insert(format!("adam.{i}.second"), v.mapv(f64::from));
        }
        insert_scalar(&mut state, "adam.steps", self.optimizer.steps as f64);
        insert_scalar(&mut state, "learning_rate", self.learning_rate);
        insert_scalar(&mut state, "updates", self.updates as f64);
        state
    }
    pub fn load_state(&mut self, state: &State) -> Result<()> {
        load_network(state, &mut self.actor, "actor")?;
        load_network(state, &mut self.critic, "critic")?;
        let noise = array(state, "log_std")?;
        ensure!(
            noise.shape() == self.log_std.shape(),
            "Saved noise shape differs"
        );
        self.log_std.assign(
            &noise
                .mapv(|x| x as f32)
                .into_dimensionality::<ndarray::Ix1>()?,
        );
        for (prefix, n) in [
            ("obs_normalizer", &mut self.obs_normalizer),
            ("critic_normalizer", &mut self.critic_normalizer),
        ] {
            let mean = array(state, &format!("{prefix}.mean"))?;
            let var = array(state, &format!("{prefix}.var"))?;
            ensure!(
                mean.shape() == n.mean.shape()
                    && var.shape() == n.var.shape()
                    && var.iter().all(|v| *v >= 0.0),
                "Saved normalizer shape or variance invalid"
            );
            n.mean
                .assign(&mean.view().into_dimensionality::<ndarray::Ix1>()?);
            n.var
                .assign(&var.view().into_dimensionality::<ndarray::Ix1>()?);
            n.count = scalar(state, &format!("{prefix}.count"))?;
            ensure!(n.count >= 0.0, "Negative normalizer count");
        }
        for (i, (m, v)) in self
            .optimizer
            .first
            .iter_mut()
            .zip(self.optimizer.second.iter_mut())
            .enumerate()
        {
            let saved_m = array(state, &format!("adam.{i}.first"))?;
            let saved_v = array(state, &format!("adam.{i}.second"))?;
            ensure!(
                saved_m.shape() == m.shape()
                    && saved_v.shape() == v.shape()
                    && saved_v.iter().all(|v| *v >= 0.0),
                "Saved optimizer state shape or variance invalid"
            );
            m.assign(&saved_m.mapv(|x| x as f32));
            v.assign(&saved_v.mapv(|x| x as f32));
        }
        self.optimizer.steps = scalar(state, "adam.steps")? as u64;
        self.learning_rate = scalar(state, "learning_rate")?;
        self.updates = scalar(state, "updates")? as u64;
        ensure!(
            self.learning_rate.is_finite() && self.learning_rate > 0.0,
            "Invalid saved learning rate"
        );
        Ok(())
    }
}
