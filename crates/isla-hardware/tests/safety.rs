use anyhow::Result;
use isla_hardware::{
    can::{slcan_decode, slcan_encode, CanBus, CanDriver, CanFrame, MemoryBus},
    protocols as p, HardwareSettings, ImuState, JointLimits, MotorState, ProtocolConfig,
    RobotDriver, SafetyFilter, SafetyLimits,
};
use serde_json::json;
use std::{collections::BTreeMap, time::Duration};

#[test]
fn protocol_bytes_match_reference() -> Result<()> {
    let ranges = p::MitRanges {
        t_max: 30.,
        ..Default::default()
    };
    assert_eq!(
        p::mit_command(0.5, 0., 45., 1., 0., ranges)?,
        [133, 30, 128, 1, 113, 51, 56, 0]
    );
    let frame = p::mit_reply_frame(7, 1.234, -3.5, 12., ranges)?;
    assert_eq!(frame, [7, 140, 162, 121, 27, 50]);
    let (node, q, v, t) = p::mit_reply(&frame, ranges)?;
    assert_eq!(node, 7);
    assert!((q - 1.234).abs() < 0.001 && (v + 3.5).abs() < 0.04 && (t - 12.).abs() < 0.02);
    assert_eq!(
        p::odrive_set_input_pos(1.25, -0.5, 0.25)?,
        [0, 0, 160, 63, 12, 254, 250, 0]
    );
    assert!(p::mit_command(f64::NAN, 0., 0., 0., 0., ranges).is_err());
    assert!(p::odrive_set_input_pos(f64::INFINITY, 0., 0.).is_err());
    for n in 0..6 {
        assert!(p::mit_reply(&frame[..n], ranges).is_err());
    }
    Ok(())
}
#[test]
fn slcan_preserves_classic_frames() -> Result<()> {
    let frame = CanFrame::new(0x123, &[0, 0xff, 17], false)?;
    assert_eq!(slcan_encode(&frame)?, "t123300FF11\r");
    assert_eq!(slcan_decode(b"t123300FF11")?, Some(frame));
    assert!(slcan_decode(b"t1238AA").is_err());
    assert!(slcan_decode(b"tFFF0").is_err());
    Ok(())
}
fn filter() -> Result<SafetyFilter> {
    SafetyFilter::new(
        BTreeMap::from([(
            "knee".into(),
            JointLimits {
                low: -1.,
                high: 1.,
                peak_torque: 66.,
                kp: 160.,
                kd: 5.,
            },
        )]),
        SafetyLimits {
            max_speed: 1.,
            soft_start: 2.,
            stiffness_fraction: 0.5,
            ..Default::default()
        },
    )
}
#[test]
fn limits_and_stalls_cannot_produce_large_target_steps() -> Result<()> {
    let mut filter = filter()?;
    filter.start(&BTreeMap::from([("knee".into(), 0.)]))?;
    let target = BTreeMap::from([("knee".into(), 5.)]);
    let first = filter.commands(&target, 10., 0.)?["knee"];
    assert!(first.position <= 0.1);
    assert_eq!(first.kp, 0.);
    for _ in 0..100 {
        filter.commands(&target, 0.1, 5.)?;
    }
    assert!((filter.targets["knee"] - 0.95).abs() < 1e-9);
    assert!(filter
        .commands(&BTreeMap::from([("knee".into(), f64::NAN)]), 0.1, 5.)
        .is_err());
    Ok(())
}
#[test]
fn stale_invalid_hot_and_faulted_states_stop() -> Result<()> {
    let mut filter = filter()?;
    filter.start(&BTreeMap::from([("knee".into(), 0.)]))?;
    for state in [
        MotorState {
            position: f64::NAN,
            ..Default::default()
        },
        MotorState {
            age: 0.5,
            ..Default::default()
        },
        MotorState {
            temperature: Some(95.),
            ..Default::default()
        },
        MotorState {
            fault: Some("encoder".into()),
            ..Default::default()
        },
        MotorState {
            position: 0.9,
            ..Default::default()
        },
    ] {
        assert!(filter
            .check(&BTreeMap::from([("knee".into(), state)]), true)
            .is_some());
    }
    assert!(filter
        .check(
            &BTreeMap::from([("knee".into(), MotorState::default())]),
            true
        )
        .is_none());
    assert!(ImuState {
        age: 1.,
        ..Default::default()
    }
    .validate(0.1)
    .is_err());
    Ok(())
}
#[test]
fn odrive_heartbeats_do_not_refresh_encoder_age() -> Result<()> {
    let profile = json!({"motors":[{"joint":"knee","type":"drive","bus":"can0","node":1,"direction":1,"zero":0.}]});
    let settings = HardwareSettings {
        robot: "test".into(),
        protocols: BTreeMap::from([(
            "drive".into(),
            ProtocolConfig {
                protocol: "odrive".into(),
                ..Default::default()
            },
        )]),
        ..Default::default()
    };
    let (bus, mut peer) = MemoryBus::pair();
    let mut driver = CanDriver::with_buses(
        &profile,
        settings,
        BTreeMap::from([("can0".into(), Box::new(bus) as Box<dyn CanBus>)]),
    )?;
    driver.connect()?;
    peer.send(&CanFrame::new(
        p::odrive_id(1, p::od::HEARTBEAT),
        &[0, 0, 0, 0, 8, 0, 0, 0],
        false,
    )?)?;
    assert!(driver.read()?.is_empty());
    peer.send(&CanFrame::new(
        p::odrive_id(1, p::od::ENCODER_ESTIMATES),
        &[0; 8],
        false,
    )?)?;
    let first = driver.read()?["knee"].age;
    std::thread::sleep(Duration::from_millis(20));
    peer.send(&CanFrame::new(
        p::odrive_id(1, p::od::HEARTBEAT),
        &[0, 0, 0, 0, 8, 0, 0, 0],
        false,
    )?)?;
    assert!(driver.read()?["knee"].age > first + 0.01);
    driver.disconnect()?;
    Ok(())
}
#[test]
fn settings_overwrite_atomically_on_windows() -> Result<()> {
    let folder = tempfile::tempdir()?;
    let mut settings = HardwareSettings {
        robot: "test".into(),
        ..Default::default()
    };
    settings.save(folder.path())?;
    settings.limits.max_speed = 1.;
    settings.save(folder.path())?;
    assert_eq!(
        HardwareSettings::load(folder.path(), "test", &json!({}))?
            .limits
            .max_speed,
        1.
    );
    Ok(())
}
