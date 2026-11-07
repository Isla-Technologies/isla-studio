use anyhow::Result;
use isla_sim::{build, LiveSession, Model, RobotProfile};
use std::path::PathBuf;
#[test]
fn torque_envelope_brakes_beyond_no_load_speed() {
    use isla_sim::session::torque_envelope;
    assert_eq!(torque_envelope(10., 5., 10., 0.), (-10., 10.));
    assert_eq!(torque_envelope(10., 5., 10., 10.), (-10., 0.));
    assert_eq!(torque_envelope(10., 5., 10., 15.), (-10., -10.));
    assert_eq!(torque_envelope(10., 5., 10., -15.), (10., 10.));
}
#[test]
fn native_model_ownership_and_physics() -> Result<()> {
    let model=Model::from_xml("<mujoco><worldbody><body pos='0 0 1'><freejoint/><geom type='sphere' size='.1' mass='1'/></body></worldbody></mujoco>")?;
    let mut data = model.data()?;
    let height = data.qpos()[2];
    for _ in 0..100 {
        data.step(&model);
    }
    assert!(data.qpos()[2] < height);
    Ok(())
}
#[test]
fn bundled_robot_builds_streams_and_packages_meshes() -> Result<()> {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../robots");
    let profile = RobotProfile::load(&root, "luma_1")?;
    let built = build(&profile, None, "train")?;
    assert_eq!(built.model.raw().nu, 19);
    assert!(built.model.total_mass() > 1.);
    compare_reference(&built, "train")?;
    let mut session = LiveSession::new(profile, None)?;
    compare_reference(&session.built, "display")?;
    for _ in 0..20 {
        session.step()?;
    }
    let frame = session.frame();
    assert_eq!(frame["type"], "frame");
    assert_eq!(frame["motors"].as_array().unwrap().len(), 19);
    assert!(session.data.qpos().iter().all(|v| v.is_finite()));
    let meta = session.built.meta(&session.profile)?;
    assert_eq!(meta["nu"], 19);
    let (blob, index) = session.built.meshes()?;
    assert!(blob.len() > 12);
    assert!(!index.as_array().unwrap().is_empty());
    Ok(())
}

fn compare_reference(built: &isla_sim::Built, purpose: &str) -> Result<()> {
    let reference: serde_json::Value =
        serde_json::from_str(include_str!("fixtures/luma-reference.json"))?;
    let expected = &reference[purpose];
    for (key, actual) in [
        ("mass", built.model.total_mass()),
        ("height", built.info["height"].as_f64().unwrap()),
        ("hip_height", built.info["hip_height"].as_f64().unwrap()),
        ("pitch", built.info["stand_pitch"].as_f64().unwrap()),
    ] {
        let target = expected[key].as_f64().unwrap();
        assert!(
            (actual - target).abs() < 1e-5,
            "{purpose}/{key}: {actual} != {target}"
        );
    }
    for (key, native) in [("stand", "stand_qpos"), ("ctrl", "stand_ctrl")] {
        for (a, b) in built.info[native]
            .as_array()
            .unwrap()
            .iter()
            .zip(expected[key].as_array().unwrap())
        {
            assert!(
                (a.as_f64().unwrap() - b.as_f64().unwrap()).abs() < 1e-5,
                "{purpose}/{key}: {a} != {b}"
            );
        }
    }
    Ok(())
}
