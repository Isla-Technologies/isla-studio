//! MIT and ODrive CANSimple codecs, with checked finite input and Python-compatible ties-to-even rounding.
use crate::ProtocolConfig;
use anyhow::{bail, Result};

pub const MIT_ENTER: [u8; 8] = [255, 255, 255, 255, 255, 255, 255, 252];
pub const MIT_EXIT: [u8; 8] = [255, 255, 255, 255, 255, 255, 255, 253];
pub const MIT_ZERO: [u8; 8] = [255, 255, 255, 255, 255, 255, 255, 254];

#[derive(Clone, Copy, Debug)]
pub struct MitRanges {
    pub p_max: f64,
    pub v_max: f64,
    pub kp_max: f64,
    pub kd_max: f64,
    pub t_max: f64,
}
impl Default for MitRanges {
    fn default() -> Self {
        Self {
            p_max: 12.5,
            v_max: 65.,
            kp_max: 500.,
            kd_max: 5.,
            t_max: 18.,
        }
    }
}
impl From<&ProtocolConfig> for MitRanges {
    fn from(c: &ProtocolConfig) -> Self {
        Self {
            p_max: c.p_max,
            v_max: c.v_max,
            kp_max: c.kp_max,
            kd_max: c.kd_max,
            t_max: c.t_max,
        }
    }
}
impl MitRanges {
    pub fn validate(&self) -> Result<()> {
        if ![self.p_max, self.v_max, self.kp_max, self.kd_max, self.t_max]
            .iter()
            .all(|v| v.is_finite() && *v > 0.)
        {
            bail!("MIT ranges must be positive and finite");
        }
        Ok(())
    }
}
fn to_uint(x: f64, low: f64, high: f64, bits: u32) -> Result<u16> {
    if !x.is_finite() {
        bail!("A motor command must be finite");
    }
    Ok(
        ((x.clamp(low, high) - low) * f64::from((1u32 << bits) - 1) / (high - low))
            .round_ties_even() as u16,
    )
}
fn to_float(v: u16, low: f64, high: f64, bits: u32) -> f64 {
    f64::from(v) * (high - low) / f64::from((1u32 << bits) - 1) + low
}
pub fn mit_command(
    position: f64,
    velocity: f64,
    kp: f64,
    kd: f64,
    torque: f64,
    r: MitRanges,
) -> Result<[u8; 8]> {
    r.validate()?;
    let p = to_uint(position, -r.p_max, r.p_max, 16)?;
    let v = to_uint(velocity, -r.v_max, r.v_max, 12)?;
    let k = to_uint(kp, 0., r.kp_max, 12)?;
    let d = to_uint(kd, 0., r.kd_max, 12)?;
    let t = to_uint(torque, -r.t_max, r.t_max, 12)?;
    Ok([
        (p >> 8) as u8,
        p as u8,
        (v >> 4) as u8,
        ((v & 15) << 4 | k >> 8) as u8,
        k as u8,
        (d >> 4) as u8,
        ((d & 15) << 4 | t >> 8) as u8,
        t as u8,
    ])
}
pub fn mit_reply(data: &[u8], r: MitRanges) -> Result<(u8, f64, f64, f64)> {
    r.validate()?;
    if data.len() < 6 {
        bail!("An MIT reply has at least 6 bytes");
    }
    let p = u16::from_be_bytes([data[1], data[2]]);
    let v = u16::from(data[3]) << 4 | u16::from(data[4] >> 4);
    let t = u16::from(data[4] & 15) << 8 | u16::from(data[5]);
    Ok((
        data[0],
        to_float(p, -r.p_max, r.p_max, 16),
        to_float(v, -r.v_max, r.v_max, 12),
        to_float(t, -r.t_max, r.t_max, 12),
    ))
}
pub fn mit_reply_frame(
    node: u8,
    position: f64,
    velocity: f64,
    torque: f64,
    r: MitRanges,
) -> Result<[u8; 6]> {
    r.validate()?;
    let p = to_uint(position, -r.p_max, r.p_max, 16)?;
    let v = to_uint(velocity, -r.v_max, r.v_max, 12)?;
    let t = to_uint(torque, -r.t_max, r.t_max, 12)?;
    Ok([
        node,
        (p >> 8) as u8,
        p as u8,
        (v >> 4) as u8,
        ((v & 15) << 4 | t >> 8) as u8,
        t as u8,
    ])
}
pub mod od {
    pub const HEARTBEAT: u16 = 0x01;
    pub const ESTOP: u16 = 0x02;
    pub const SET_AXIS_STATE: u16 = 0x07;
    pub const ENCODER_ESTIMATES: u16 = 0x09;
    pub const SET_CONTROLLER_MODE: u16 = 0x0b;
    pub const SET_INPUT_POS: u16 = 0x0c;
    pub const SET_LIMITS: u16 = 0x0f;
    pub const GET_IQ: u16 = 0x14;
    pub const GET_TEMPERATURE: u16 = 0x15;
    pub const CLEAR_ERRORS: u16 = 0x18;
    pub const SET_POS_GAIN: u16 = 0x1a;
    pub const SET_VEL_GAINS: u16 = 0x1b;
    pub const GET_TORQUES: u16 = 0x1c;
    pub const IDLE: u32 = 1;
    pub const CLOSED_LOOP: u32 = 8;
    pub const POSITION_CONTROL: u32 = 3;
    pub const PASSTHROUGH: u32 = 1;
}
pub fn odrive_id(node: u16, command: u16) -> u16 {
    ((node & 0x3f) << 5) | (command & 0x1f)
}
pub fn odrive_split(id: u16) -> (u16, u16) {
    (id >> 5, id & 0x1f)
}
pub fn odrive_set_input_pos(turns: f64, velocity: f64, torque: f64) -> Result<[u8; 8]> {
    if ![turns, velocity, torque].iter().all(|v| v.is_finite()) || !(turns as f32).is_finite() {
        bail!("ODrive command must be finite and representable");
    }
    let v = (velocity * 1000.).round_ties_even().clamp(-32768., 32767.) as i16;
    let t = (torque * 1000.).round_ties_even().clamp(-32768., 32767.) as i16;
    let mut out = [0; 8];
    out[..4].copy_from_slice(&(turns as f32).to_le_bytes());
    out[4..6].copy_from_slice(&v.to_le_bytes());
    out[6..8].copy_from_slice(&t.to_le_bytes());
    Ok(out)
}
pub fn odrive_axis_state(state: u32) -> [u8; 4] {
    state.to_le_bytes()
}
pub fn odrive_controller_mode(control: u32, input: u32) -> [u8; 8] {
    let mut out = [0; 8];
    out[..4].copy_from_slice(&control.to_le_bytes());
    out[4..].copy_from_slice(&input.to_le_bytes());
    out
}
pub fn odrive_gains(kp: f64, kd: f64, gear_ratio: f64) -> Result<([u8; 4], [u8; 8])> {
    if ![kp, kd, gear_ratio].iter().all(|v| v.is_finite()) || kp < 0. || kd < 0. || gear_ratio <= 0.
    {
        bail!("Invalid ODrive gains");
    }
    let n2 = gear_ratio.max(1e-6).powi(2);
    let vel_gain = (kd / n2 * std::f64::consts::TAU).max(1e-6);
    let pos_gain = kp / n2 * std::f64::consts::TAU / vel_gain;
    let mut vg = [0; 8];
    vg[..4].copy_from_slice(&(vel_gain as f32).to_le_bytes());
    Ok(((pos_gain as f32).to_le_bytes(), vg))
}
pub fn odrive_pair(data: &[u8]) -> Result<(f64, f64)> {
    if data.len() < 8 {
        bail!("An ODrive pair has 8 bytes");
    }
    let a = f32::from_le_bytes(data[..4].try_into()?) as f64;
    let b = f32::from_le_bytes(data[4..8].try_into()?) as f64;
    if !a.is_finite() || !b.is_finite() {
        bail!("ODrive reported invalid readings");
    }
    Ok((a, b))
}
pub fn odrive_heartbeat(data: &[u8]) -> Result<(u32, u8)> {
    if data.len() < 5 {
        bail!("An ODrive heartbeat has at least 5 bytes");
    }
    Ok((u32::from_le_bytes(data[..4].try_into()?), data[4]))
}
