use crate::{
    base::{MotorCommands, MotorStates},
    MotorCommand, SafetyLimits,
};
use anyhow::{bail, Result};
use std::collections::BTreeMap;

#[derive(Clone, Copy, Debug)]
pub struct JointLimits {
    pub low: f64,
    pub high: f64,
    pub peak_torque: f64,
    pub kp: f64,
    pub kd: f64,
}

pub struct SafetyFilter {
    pub joints: BTreeMap<String, JointLimits>,
    pub limits: SafetyLimits,
    pub targets: BTreeMap<String, f64>,
}
impl SafetyFilter {
    pub fn new(joints: BTreeMap<String, JointLimits>, limits: SafetyLimits) -> Result<Self> {
        limits.validate()?;
        for (joint, spec) in &joints {
            if ![spec.low, spec.high, spec.peak_torque, spec.kp, spec.kd]
                .iter()
                .all(|v| v.is_finite())
                || spec.low > spec.high
                || spec.peak_torque <= 0.
                || spec.kp < 0.
                || spec.kd < 0.
            {
                bail!("Invalid limits for {joint}");
            }
        }
        Ok(Self {
            joints,
            limits,
            targets: BTreeMap::new(),
        })
    }
    pub fn start(&mut self, measured: &BTreeMap<String, f64>) -> Result<()> {
        if measured.values().any(|v| !v.is_finite()) {
            bail!("Cannot arm with invalid encoder readings");
        }
        self.targets = self
            .joints
            .keys()
            .filter_map(|j| measured.get(j).map(|v| (j.clone(), *v)))
            .collect();
        Ok(())
    }
    pub fn commands(
        &mut self,
        desired: &BTreeMap<String, f64>,
        dt: f64,
        since_armed: f64,
    ) -> Result<MotorCommands> {
        if !dt.is_finite()
            || dt < 0.
            || !since_armed.is_finite()
            || desired.values().any(|v| !v.is_finite())
        {
            bail!("Invalid target or control-loop timing");
        }
        let ramp = if self.limits.soft_start <= 0. {
            1.
        } else {
            (since_armed / self.limits.soft_start).clamp(0., 1.)
        };
        let share = self.limits.stiffness_fraction * ramp;
        // A stalled loop cannot turn its elapsed pause into one large target jump.
        let step = self.limits.max_speed * dt.min(self.limits.state_timeout);
        let mut out = BTreeMap::new();
        for (joint, spec) in &self.joints {
            let Some(last) = self.targets.get_mut(joint) else {
                continue;
            };
            let mut low = spec.low + self.limits.limit_margin;
            let mut high = spec.high - self.limits.limit_margin;
            if low > high {
                low = (spec.low + spec.high) / 2.;
                high = low;
            }
            let goal = desired
                .get(joint)
                .copied()
                .unwrap_or(*last)
                .clamp(low, high);
            *last = goal.clamp(*last - step, *last + step);
            out.insert(
                joint.clone(),
                MotorCommand {
                    position: *last,
                    kp: spec.kp * share,
                    kd: spec.kd * share,
                    torque_limit: spec.peak_torque * self.limits.torque_fraction,
                    ..Default::default()
                },
            );
        }
        Ok(out)
    }
    pub fn check(&self, states: &MotorStates, armed: bool) -> Option<String> {
        for joint in self.joints.keys() {
            let Some(s) = states.get(joint) else {
                return Some(format!("{joint}: its motor is not answering"));
            };
            if !s.finite() {
                return Some(format!("{joint}: invalid motor readings"));
            }
            if s.age > self.limits.state_timeout {
                return Some(format!("{joint}: no data for {:.0} ms", s.age * 1000.));
            }
            if let Some(fault) = &s.fault {
                if !fault.is_empty() {
                    return Some(format!("{joint}: the drive reports {fault}"));
                }
            }
            if let Some(t) = s.temperature {
                if t > self.limits.max_temperature {
                    return Some(format!(
                        "{joint}: {t:.0} degC is over the {:.0} degC limit",
                        self.limits.max_temperature
                    ));
                }
            }
            if armed {
                if let Some(target) = self.targets.get(joint) {
                    let error = (s.position - target).abs();
                    if error > self.limits.max_tracking_error {
                        return Some(format!("{joint}: {error:.2} rad from its target"));
                    }
                }
            }
        }
        None
    }
}
