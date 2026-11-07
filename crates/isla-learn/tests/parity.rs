use isla_learn::{
    checkpoint,
    network::{Mlp, Normalizer},
    ppo::{advantages, Batch, Ppo, PpoSettings, Rollout},
    Policy, PolicyRunner,
};
use ndarray::{array, Array1, Array2};
use rand::{rngs::StdRng, SeedableRng};
use serde_json::Value;
use std::{collections::BTreeMap, path::PathBuf};

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}
fn fixture(name: &str) -> Value {
    serde_json::from_str(
        &std::fs::read_to_string(
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures")
                .join(name),
        )
        .unwrap(),
    )
    .unwrap()
}
fn matrix(v: &Value) -> Array2<f32> {
    let rows: Vec<Vec<f32>> = serde_json::from_value(v.clone()).unwrap();
    Array2::from_shape_vec(
        (rows.len(), rows[0].len()),
        rows.into_iter().flatten().collect(),
    )
    .unwrap()
}
fn close(actual: f64, expected: f64, tolerance: f64) {
    assert!(
        (actual - expected).abs() <= tolerance,
        "{actual} != {expected} (tolerance {tolerance})"
    );
}

#[test]
fn shipped_policy_and_stateful_trace_match_python() {
    for name in ["luma1-walk", "luma1-leg-design"] {
        let p = Policy::load(root().join(format!("examples/runs/{name}/policy.json"))).unwrap();
        let f = fixture(&format!("{name}.json"));
        let output = p.act(&matrix(&f["observations"])).unwrap();
        let expected = matrix(&f["actions"]);
        for (a, b) in output.iter().zip(expected.iter()) {
            close(*a as f64, *b as f64, 2e-5);
        }
        let roundtrip = Policy::from_document(&p.to_document()).unwrap();
        assert_eq!(p.network.weights, roundtrip.network.weights);
        let mut runner = PolicyRunner::new(p, None).unwrap();
        for row in f["steps"].as_array().unwrap() {
            let c: [f64; 3] = serde_json::from_value(row["command"].clone()).unwrap();
            runner.set_command(c[0], c[1], c[2]).unwrap();
            let targets = runner
                .step(
                    row["dt"].as_f64().unwrap(),
                    serde_json::from_value(row["quaternion"].clone()).unwrap(),
                    serde_json::from_value(row["gyro"].clone()).unwrap(),
                    &serde_json::from_value(row["joints"].clone()).unwrap(),
                    &serde_json::from_value(row["velocities"].clone()).unwrap(),
                )
                .unwrap();
            let expected: BTreeMap<String, f64> =
                serde_json::from_value(row["targets"].clone()).unwrap();
            for (k, v) in targets {
                close(v, expected[&k], 2e-5);
            }
            close(runner.phase, row["phase"].as_f64().unwrap(), 1e-12);
            for (i, v) in runner.speed.unwrap().iter().enumerate() {
                close(*v, row["speed"][i].as_f64().unwrap(), 1e-12);
            }
        }
    }
}
#[test]
fn malformed_policy_is_rejected_without_panics() {
    let p = Policy::load(root().join("examples/runs/luma1-walk/policy.json")).unwrap();
    for field in ["network", "joints", "input"] {
        let mut d = p.to_document();
        d[field] = serde_json::json!([]);
        assert!(Policy::from_document(&d).is_err());
    }
    let mut d = p.to_document();
    d["network"][0]["weight"]["shape"] = serde_json::json!([usize::MAX, 2]);
    assert!(Policy::from_document(&d).is_err());
    let mut d = p.to_document();
    d["rate"] = serde_json::json!(0);
    assert!(Policy::from_document(&d).is_err());
    let mut d = p.to_document();
    d["version"] = serde_json::json!(999);
    assert!(
        Policy::from_document(&d).is_ok(),
        "Reference does not gate version"
    );
    d["gait"] = Value::Null;
    d["gait_period"] = serde_json::json!(0.8);
    assert_eq!(
        Policy::from_document(&d).unwrap().gait.unwrap().period,
        [0.8, 0.8]
    );
}
#[test]
fn ppo_losses_gradients_and_npz_match_python() {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/ppo.npz");
    let s = checkpoint::read(path).unwrap();
    let mut p = Ppo::new(
        4,
        7,
        2,
        PpoSettings {
            hidden: vec![8, 5],
            epochs: 2,
            ..PpoSettings::default()
        },
        23,
    )
    .unwrap();
    p.load_state(&s).unwrap();
    let a2 = |k: &str| {
        s[&format!("batch.{k}")]
            .mapv(|x| x as f32)
            .into_dimensionality()
            .unwrap()
    };
    let a1 = |k: &str| {
        s[&format!("batch.{k}")]
            .mapv(|x| x as f32)
            .into_dimensionality()
            .unwrap()
    };
    let batch = Batch {
        obs: a2("obs"),
        critic_obs: a2("critic_obs"),
        actions: a2("actions"),
        means: a2("means"),
        log_probs: a1("log_probs"),
        values: a1("values"),
        returns: a1("returns"),
        advantages: a1("advantages"),
    };
    let old = s["old_std"]
        .mapv(|x| x as f32)
        .into_dimensionality()
        .unwrap();
    let (parts, grads) = p.losses(&batch, &old).unwrap();
    let expected = fixture("ppo.json");
    for (k, v) in parts {
        close(v, expected[&k].as_f64().unwrap(), 2e-6);
    }
    for (i, g) in grads.iter().enumerate() {
        let expected = &s[&format!("gradient.{i}")];
        assert_eq!(g.shape(), expected.shape());
        for (a, b) in g.iter().zip(expected) {
            close(*a as f64, *b, 2e-6);
        }
    }
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("checkpoint.npz");
    checkpoint::write(&path, &p.state()).unwrap();
    let restored = checkpoint::read(&path).unwrap();
    assert_eq!(p.state(), restored);
}
#[test]
fn existing_checkpoints_load_with_network_and_optimizer_state() {
    for name in ["luma1-walk", "luma1-leg-design"] {
        let state =
            checkpoint::read(root().join(format!("examples/runs/{name}/checkpoint.npz"))).unwrap();
        let policy =
            Policy::load(root().join(format!("examples/runs/{name}/policy.json"))).unwrap();
        let hidden = policy.network.sizes();
        let mut p = Ppo::new(
            hidden[0],
            state["critic.0.weight"].shape()[0],
            *hidden.last().unwrap(),
            PpoSettings {
                hidden: hidden[1..hidden.len() - 1].to_vec(),
                ..PpoSettings::default()
            },
            1,
        )
        .unwrap();
        p.load_state(&state).unwrap();
        assert!(p.updates > 0);
        assert_eq!(p.actor.weights, policy.network.weights);
    }
}
#[test]
fn gae_honors_terminal_boundaries() {
    let a = advantages(
        &array![[1., 2.], [3., 4.]],
        &Array2::zeros((2, 2)),
        &array![[1., 0.], [0., 1.]],
        &array![10., 10.],
        0.9,
        1.,
    )
    .unwrap();
    close(a[[0, 0]] as f64, 1., 1e-6);
    close(a[[0, 1]] as f64, 5.6, 1e-6);
    close(a[[1, 0]] as f64, 12., 1e-6);
}
#[test]
fn minibatches_larger_than_rollout_stay_finite() {
    let mut p = Ppo::new(
        2,
        3,
        1,
        PpoSettings {
            hidden: vec![3],
            minibatches: 20,
            epochs: 1,
            ..PpoSettings::default()
        },
        1,
    )
    .unwrap();
    let mut rollout = Rollout::new(3, 1, 2, 3, 1).unwrap();
    rollout.batch.advantages = Array1::from_vec(vec![1., 2., 3.]);
    rollout.batch.returns = Array1::ones(3);
    let stats = p.update(&rollout).unwrap();
    assert!(stats.values().all(|v| v.is_finite()));
    assert_eq!(p.optimizer.steps, 3);
}
#[test]
fn normalizer_split_batches_equal_one_batch() {
    let x = array![[1., 4.], [2., 5.], [3., 6.]];
    let mut one = Normalizer::new(2);
    one.update(&x).unwrap();
    let mut split = Normalizer::new(2);
    split.update(&array![[1., 4.]]).unwrap();
    split.update(&array![[2., 5.], [3., 6.]]).unwrap();
    assert_eq!(one.mean, split.mean);
    for (a, b) in one.var.iter().zip(&split.var) {
        close(*a, *b, 1e-14);
    }
}
#[test]
fn mlp_backward_matches_finite_difference() {
    let mut rng = StdRng::seed_from_u64(9);
    let mut net = Mlp::new(&[2, 3, 1], 0.4, &mut rng).unwrap();
    let x = array![[0.3, -0.2], [0.1, 0.4]];
    let (_, inputs) = net.forward(&x).unwrap();
    let grads = net.backward(&Array2::ones((2, 1)), &inputs).unwrap();
    let initial = net.params();
    for layer in 0..initial.len() {
        for element in 0..initial[layer].len() {
            let mut plus = initial.clone();
            plus[layer].as_slice_mut().unwrap()[element] += 0.001;
            net.set_params(&plus).unwrap();
            let high = net.predict(&x).unwrap().sum();
            let mut minus = initial.clone();
            minus[layer].as_slice_mut().unwrap()[element] -= 0.001;
            net.set_params(&minus).unwrap();
            let low = net.predict(&x).unwrap().sum();
            close(
                ((high - low) / 0.002) as f64,
                *grads[layer].iter().nth(element).unwrap() as f64,
                1e-4,
            );
        }
    }
}
