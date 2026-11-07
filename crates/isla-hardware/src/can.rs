use crate::{
    base::{MotorCommands, MotorStates},
    config::{BusConfig, HardwareSettings, ProtocolConfig},
    protocols::{self as p, od},
    MotorState, RobotDriver,
};
use anyhow::{bail, Context, Result};
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
    io::{Read, Write},
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CanFrame {
    pub id: u16,
    pub data: Vec<u8>,
    pub remote: bool,
}
impl CanFrame {
    pub fn new(id: u16, data: &[u8], remote: bool) -> Result<Self> {
        if id > 0x7ff || data.len() > 8 {
            bail!("Invalid classic CAN frame");
        }
        Ok(Self {
            id,
            data: data.into(),
            remote,
        })
    }
}
pub trait CanBus: Send {
    fn send(&mut self, frame: &CanFrame) -> Result<()>;
    fn receive(&mut self) -> Result<Option<CanFrame>>;
    fn close(&mut self) -> Result<()> {
        Ok(())
    }
}
pub fn ports() -> Value {
    let ports = serialport::available_ports()
        .unwrap_or_default()
        .into_iter()
        .map(
            |p| serde_json::json!({"device":p.port_name,"description":format!("{:?}",p.port_type)}),
        )
        .collect::<Vec<_>>();
    let interfaces = if cfg!(target_os = "linux") {
        vec!["slcan", "socketcan"]
    } else {
        vec!["slcan"]
    };
    serde_json::json!({"ports":ports,"interfaces":interfaces})
}

/// A pair of native in-memory buses for transport and driver tests. No echo to sender.
pub struct MemoryBus {
    incoming: Arc<Mutex<VecDeque<CanFrame>>>,
    outgoing: Arc<Mutex<VecDeque<CanFrame>>>,
}
impl MemoryBus {
    pub fn pair() -> (Self, Self) {
        let a = Arc::new(Mutex::new(VecDeque::new()));
        let b = Arc::new(Mutex::new(VecDeque::new()));
        (
            Self {
                incoming: a.clone(),
                outgoing: b.clone(),
            },
            Self {
                incoming: b,
                outgoing: a,
            },
        )
    }
}
impl CanBus for MemoryBus {
    fn send(&mut self, f: &CanFrame) -> Result<()> {
        self.outgoing
            .lock()
            .map_err(|_| anyhow::anyhow!("CAN queue poisoned"))?
            .push_back(f.clone());
        Ok(())
    }
    fn receive(&mut self) -> Result<Option<CanFrame>> {
        Ok(self
            .incoming
            .lock()
            .map_err(|_| anyhow::anyhow!("CAN queue poisoned"))?
            .pop_front())
    }
}

/// Lawicel SLCAN (CANable serial firmware); classic 11-bit frames only.
pub struct SlcanBus {
    port: Box<dyn serialport::SerialPort>,
    buffer: Vec<u8>,
}
impl SlcanBus {
    pub fn open(config: &BusConfig) -> Result<Self> {
        let code = match config.bitrate {
            10_000 => 0,
            20_000 => 1,
            50_000 => 2,
            100_000 => 3,
            125_000 => 4,
            250_000 => 5,
            500_000 => 6,
            800_000 => 7,
            1_000_000 => 8,
            _ => bail!("SLCAN does not support bitrate {}", config.bitrate),
        };
        let (channel, baud) = config
            .channel
            .rsplit_once('@')
            .map(|(a, b)| Ok::<_, anyhow::Error>((a, b.parse::<u32>()?)))
            .transpose()?
            .unwrap_or((&config.channel, 115_200));
        let mut port = serialport::new(channel, baud)
            .timeout(Duration::from_millis(1))
            .open()
            .with_context(|| format!("Cannot open SLCAN serial port {channel}"))?;
        port.write_all(format!("C\rS{code}\rO\r").as_bytes())?;
        port.flush()?;
        Ok(Self {
            port,
            buffer: vec![],
        })
    }
}
pub fn slcan_encode(frame: &CanFrame) -> Result<String> {
    CanFrame::new(frame.id, &frame.data, frame.remote)?;
    let mut out = format!(
        "{}{:03X}{}",
        if frame.remote { 'r' } else { 't' },
        frame.id,
        if frame.remote { 8 } else { frame.data.len() }
    );
    if !frame.remote {
        for b in &frame.data {
            out.push_str(&format!("{b:02X}"));
        }
    }
    out.push('\r');
    Ok(out)
}
pub fn slcan_decode(line: &[u8]) -> Result<Option<CanFrame>> {
    if line.is_empty() {
        return Ok(None);
    }
    if line[0] == 7 {
        bail!("SLCAN adapter rejected a command");
    }
    if !matches!(line[0], b't' | b'r') {
        return Ok(None);
    }
    let line = std::str::from_utf8(line)?;
    if !line.is_ascii() || line.len() < 5 {
        bail!("Malformed SLCAN frame");
    }
    let id = u16::from_str_radix(&line[1..4], 16)?;
    let len = usize::from_str_radix(&line[4..5], 16)?;
    if len > 8 {
        bail!("SLCAN payload exceeds eight bytes");
    }
    let remote = line.as_bytes()[0] == b'r';
    if !remote && line.len() < 5 + 2 * len {
        bail!("Truncated SLCAN frame");
    }
    let mut data = vec![];
    if !remote {
        for i in 0..len {
            data.push(u8::from_str_radix(&line[5 + 2 * i..7 + 2 * i], 16)?);
        }
    }
    Ok(Some(CanFrame::new(id, &data, remote)?))
}
impl CanBus for SlcanBus {
    fn send(&mut self, f: &CanFrame) -> Result<()> {
        self.port.write_all(slcan_encode(f)?.as_bytes())?;
        Ok(())
    }
    fn receive(&mut self) -> Result<Option<CanFrame>> {
        loop {
            if let Some(end) = self.buffer.iter().position(|b| *b == b'\r' || *b == 7) {
                let mut line: Vec<_> = self.buffer.drain(..=end).collect();
                if line.last() == Some(&7) {
                    bail!("SLCAN adapter rejected a command");
                }
                line.pop();
                if let Some(frame) = slcan_decode(&line)? {
                    return Ok(Some(frame));
                }
            } else {
                let mut block = [0u8; 256];
                match self.port.read(&mut block) {
                    Ok(0) => return Ok(None),
                    Ok(n) => self.buffer.extend_from_slice(&block[..n]),
                    Err(e)
                        if matches!(
                            e.kind(),
                            std::io::ErrorKind::TimedOut | std::io::ErrorKind::WouldBlock
                        ) =>
                    {
                        return Ok(None)
                    }
                    Err(e) => return Err(e.into()),
                }
                if self.buffer.len() > 4096 {
                    self.buffer.clear();
                    bail!("SLCAN adapter sent an unterminated frame");
                }
            }
        }
    }
    fn close(&mut self) -> Result<()> {
        self.port.write_all(b"C\r")?;
        Ok(())
    }
}

#[cfg(target_os = "linux")]
mod linux {
    use super::*;
    use socketcan::{CanSocket, EmbeddedFrame, Frame, Socket};
    pub struct SocketCanBus(CanSocket);
    impl SocketCanBus {
        pub fn open(channel: &str) -> Result<Self> {
            let socket = CanSocket::open(channel)?;
            socket.set_nonblocking(true)?;
            Ok(Self(socket))
        }
    }
    impl CanBus for SocketCanBus {
        fn send(&mut self, f: &CanFrame) -> Result<()> {
            let frame = if f.remote {
                socketcan::CanFrame::remote_from_raw_id(f.id as u32, 8)
            } else {
                socketcan::CanFrame::from_raw_id(f.id as u32, &f.data)
            }
            .context("Invalid CAN frame")?;
            self.0.write_frame(&frame)?;
            Ok(())
        }
        fn receive(&mut self) -> Result<Option<CanFrame>> {
            match self.0.read_frame() {
                Ok(f) if f.is_extended() || f.is_error_frame() => Ok(None),
                Ok(f) => Ok(Some(CanFrame::new(
                    f.raw_id() as u16,
                    f.data(),
                    f.is_remote_frame(),
                )?)),
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => Ok(None),
                Err(e) => Err(e.into()),
            }
        }
    }
}
pub fn open_bus(config: &BusConfig) -> Result<Box<dyn CanBus>> {
    match config.interface.as_str() {
        "slcan"=>Ok(Box::new(SlcanBus::open(config)?)),
        #[cfg(target_os="linux")]
        "socketcan"=>Ok(Box::new(linux::SocketCanBus::open(&config.channel)?)),
        #[cfg(not(target_os="linux"))]
        "socketcan"=>bail!("SocketCAN is available on Linux only; choose SLCAN for this computer"),
        "gs_usb"|"pcan"|"kvaser"=>bail!("The {} adapter has no native driver in this build. On Linux use its SocketCAN interface; otherwise use SLCAN firmware or a supported adapter",config.interface),
        "virtual"=>bail!("Choose the practice robot driver for simulated hardware; virtual CAN buses are available through the test API"),
        other=>bail!("Unsupported CAN interface: {other}"),
    }
}

struct Motor {
    joint: String,
    bus: String,
    node: u16,
    direction: f64,
    zero: f64,
    protocol: ProtocolConfig,
    position: f64,
    velocity: f64,
    torque: f64,
    temperature: Option<f64>,
    fault: Option<String>,
    updated: Option<Instant>,
    gains: (f64, f64),
}
pub struct CanDriver {
    motors: BTreeMap<String, Motor>,
    settings: HardwareSettings,
    buses: BTreeMap<String, Box<dyn CanBus>>,
    connected: bool,
    provided: bool,
}
impl CanDriver {
    pub fn new(profile: &Value, settings: HardwareSettings) -> Result<Self> {
        settings.validate()?;
        let mut motors = BTreeMap::new();
        let mut addresses = BTreeSet::new();
        for m in profile["motors"]
            .as_array()
            .context("Robot has no motor map")?
        {
            let joint = m["joint"]
                .as_str()
                .context("Motor has no joint")?
                .to_owned();
            let bus = m["bus"].as_str().unwrap_or("can0").to_owned();
            let node = m["node"]
                .as_u64()
                .context("Motor node must be a nonnegative integer")?;
            let protocol = settings
                .protocols
                .get(m["type"].as_str().unwrap_or(""))
                .cloned()
                .unwrap_or_default();
            protocol.validate()?;
            if node > if protocol.protocol == "mit" { 127 } else { 63 } {
                bail!(
                    "{joint}: node exceeds the {} protocol range",
                    protocol.protocol
                );
            }
            if !addresses.insert((bus.clone(), node)) {
                bail!("Duplicate CAN address on {bus}: {node}");
            }
            let direction = m["direction"].as_f64().unwrap_or(1.);
            let zero = m["zero"].as_f64().unwrap_or(0.);
            if !matches!(direction, -1.0 | 1.0) || !zero.is_finite() {
                bail!("{joint}: direction must be -1 or 1 and zero must be finite");
            }
            let motor = Motor {
                joint: joint.clone(),
                bus,
                node: node as u16,
                direction,
                zero,
                protocol,
                position: 0.,
                velocity: 0.,
                torque: 0.,
                temperature: None,
                fault: None,
                updated: None,
                gains: (-1., -1.),
            };
            if motors.insert(joint.clone(), motor).is_some() {
                bail!("Duplicate motor joint: {joint}");
            }
        }
        Ok(Self {
            motors,
            settings,
            buses: BTreeMap::new(),
            connected: false,
            provided: false,
        })
    }
    pub fn with_buses(
        profile: &Value,
        settings: HardwareSettings,
        buses: BTreeMap<String, Box<dyn CanBus>>,
    ) -> Result<Self> {
        let mut out = Self::new(profile, settings)?;
        out.buses = buses;
        out.provided = true;
        Ok(out)
    }
    fn require(&self) -> Result<()> {
        if !self.connected {
            bail!("Not connected to the CAN buses");
        }
        Ok(())
    }
    fn send(
        buses: &mut BTreeMap<String, Box<dyn CanBus>>,
        m: &Motor,
        id: u16,
        data: &[u8],
        remote: bool,
    ) -> Result<()> {
        buses
            .get_mut(&m.bus)
            .with_context(|| format!("{} is not open", m.bus))?
            .send(&CanFrame::new(id, data, remote)?)
            .with_context(|| format!("{}: could not send", m.bus))
    }
    fn poll(&mut self) -> Result<()> {
        for (bus, transport) in &mut self.buses {
            // Bound work per tick, so a noisy bus cannot starve the watchdog.
            for _ in 0..1024 {
                let Some(frame) = transport.receive()? else {
                    break;
                };
                if frame.remote {
                    continue;
                }
                let now = Instant::now();
                let mit = self.motors.values_mut().find(|m| {
                    m.bus == *bus
                        && m.protocol.protocol == "mit"
                        && frame.data.first().copied().map(u16::from) == Some(m.node)
                        && frame.id <= 0x7f
                        && frame.data.len() >= 6
                });
                if let Some(m) = mit {
                    let (_, q, v, t) = p::mit_reply(&frame.data, (&m.protocol).into())?;
                    m.position = q;
                    m.velocity = v;
                    m.torque = t;
                    m.updated = Some(now);
                    continue;
                }
                let (node, cmd) = p::odrive_split(frame.id);
                let Some(m) = self
                    .motors
                    .values_mut()
                    .find(|m| m.bus == *bus && m.node == node && m.protocol.protocol == "odrive")
                else {
                    continue;
                };
                match cmd {
                    od::ENCODER_ESTIMATES => {
                        let (q, v) = p::odrive_pair(&frame.data)?;
                        m.position = q * std::f64::consts::TAU / m.protocol.gear_ratio;
                        m.velocity = v * std::f64::consts::TAU / m.protocol.gear_ratio;
                        m.updated = Some(now);
                    }
                    od::HEARTBEAT => {
                        let (error, _) = p::odrive_heartbeat(&frame.data)?;
                        m.fault = if error == 0 {
                            None
                        } else {
                            Some(format!("error 0x{error:08X}"))
                        };
                    }
                    od::GET_TEMPERATURE => m.temperature = Some(p::odrive_pair(&frame.data)?.1),
                    od::GET_TORQUES => {
                        m.torque = p::odrive_pair(&frame.data)?.1 * m.protocol.gear_ratio
                    }
                    _ => {}
                }
            }
        }
        Ok(())
    }
}
impl RobotDriver for CanDriver {
    fn name(&self) -> &str {
        "CAN bus"
    }
    fn connect(&mut self) -> Result<()> {
        if self.connected {
            return Ok(());
        }
        let wanted: BTreeSet<_> = self.motors.values().map(|m| m.bus.clone()).collect();
        for name in wanted {
            if self.buses.contains_key(&name) {
                continue;
            }
            let c = self
                .settings
                .buses
                .iter()
                .find(|c| c.name == name)
                .with_context(|| format!("Choose an adapter for {name} first"))?;
            if c.channel.is_empty() {
                bail!("Choose an adapter channel for {name} first");
            }
            match open_bus(c) {
                Ok(bus) => {
                    self.buses.insert(name, bus);
                }
                Err(e) => {
                    for bus in self.buses.values_mut() {
                        let _ = bus.close();
                    }
                    self.buses.clear();
                    return Err(e);
                }
            }
        }
        self.connected = true;
        Ok(())
    }
    fn disconnect(&mut self) -> Result<()> {
        let result = self.relax();
        for bus in self.buses.values_mut() {
            let _ = bus.close();
        }
        self.buses.clear();
        self.connected = false;
        for m in self.motors.values_mut() {
            m.updated = None;
        }
        result
    }
    fn scan(&mut self) -> Result<Vec<String>> {
        self.require()?;
        for m in self.motors.values() {
            if m.protocol.protocol == "mit" {
                Self::send(
                    &mut self.buses,
                    m,
                    m.node,
                    &p::mit_command(0., 0., 0., 0., 0., (&m.protocol).into())?,
                    false,
                )?;
            } else {
                Self::send(
                    &mut self.buses,
                    m,
                    p::odrive_id(m.node, od::ENCODER_ESTIMATES),
                    &[],
                    true,
                )?;
            }
        }
        let deadline = Instant::now() + Duration::from_millis(250);
        while Instant::now() < deadline {
            self.poll()?;
            std::thread::sleep(Duration::from_millis(2));
        }
        Ok(self
            .motors
            .values()
            .filter(|m| m.updated.is_some_and(|t| t.elapsed().as_secs_f64() < 0.5))
            .map(|m| m.joint.clone())
            .collect())
    }
    fn read(&mut self) -> Result<MotorStates> {
        self.require()?;
        self.poll()?;
        Ok(self
            .motors
            .values()
            .filter_map(|m| {
                m.updated.map(|t| {
                    (
                        m.joint.clone(),
                        MotorState {
                            position: m.direction * (m.position - m.zero),
                            velocity: m.direction * m.velocity,
                            torque: m.direction * m.torque,
                            temperature: m.temperature,
                            fault: m.fault.clone(),
                            age: t.elapsed().as_secs_f64(),
                        },
                    )
                })
            })
            .collect())
    }
    fn enable(&mut self) -> Result<()> {
        self.require()?;
        for m in self.motors.values() {
            let result = if m.protocol.protocol == "mit" {
                Self::send(&mut self.buses, m, m.node, &p::MIT_ENTER, false)
            } else {
                (|| {
                    Self::send(
                        &mut self.buses,
                        m,
                        p::odrive_id(m.node, od::CLEAR_ERRORS),
                        &[],
                        false,
                    )?;
                    Self::send(
                        &mut self.buses,
                        m,
                        p::odrive_id(m.node, od::SET_CONTROLLER_MODE),
                        &p::odrive_controller_mode(od::POSITION_CONTROL, od::PASSTHROUGH),
                        false,
                    )?;
                    Self::send(
                        &mut self.buses,
                        m,
                        p::odrive_id(m.node, od::SET_AXIS_STATE),
                        &p::odrive_axis_state(od::CLOSED_LOOP),
                        false,
                    )
                })()
            };
            if let Err(e) = result {
                let _ = self.relax();
                return Err(e);
            }
        }
        Ok(())
    }
    fn command(&mut self, commands: &MotorCommands) -> Result<()> {
        self.require()?;
        // Validate the complete packet before sending any of it.
        for command in commands.values() {
            command.validate()?;
        }
        for (joint, c) in commands {
            let m = self
                .motors
                .get_mut(joint)
                .with_context(|| format!("Unknown motor joint: {joint}"))?;
            let target = m.direction * c.position + m.zero;
            let velocity = m.direction * c.velocity;
            let torque = m.direction * c.torque;
            if m.protocol.protocol == "mit" {
                let limit = if c.torque_limit > 0. {
                    c.torque_limit
                } else {
                    m.protocol.t_max
                };
                Self::send(
                    &mut self.buses,
                    m,
                    m.node,
                    &p::mit_command(
                        target,
                        velocity,
                        c.kp,
                        c.kd,
                        torque.clamp(-limit, limit),
                        (&m.protocol).into(),
                    )?,
                    false,
                )?;
            } else {
                let ratio = m.protocol.gear_ratio;
                if (m.gains.0 - c.kp).abs() > 1e-3 || (m.gains.1 - c.kd).abs() > 1e-3 {
                    let (pg, vg) = p::odrive_gains(c.kp, c.kd, ratio)?;
                    Self::send(
                        &mut self.buses,
                        m,
                        p::odrive_id(m.node, od::SET_POS_GAIN),
                        &pg,
                        false,
                    )?;
                    Self::send(
                        &mut self.buses,
                        m,
                        p::odrive_id(m.node, od::SET_VEL_GAINS),
                        &vg,
                        false,
                    )?;
                    m.gains = (c.kp, c.kd);
                }
                let limit = if c.torque_limit > 0. {
                    c.torque_limit
                } else {
                    m.protocol.t_max
                };
                Self::send(
                    &mut self.buses,
                    m,
                    p::odrive_id(m.node, od::SET_INPUT_POS),
                    &p::odrive_set_input_pos(
                        target * ratio / std::f64::consts::TAU,
                        velocity * ratio / std::f64::consts::TAU,
                        torque.clamp(-limit, limit) / ratio,
                    )?,
                    false,
                )?;
                Self::send(
                    &mut self.buses,
                    m,
                    p::odrive_id(m.node, od::ENCODER_ESTIMATES),
                    &[],
                    true,
                )?;
            }
        }
        Ok(())
    }
    fn relax(&mut self) -> Result<()> {
        if !self.connected {
            return Ok(());
        }
        let mut first = None;
        for m in self.motors.values() {
            let result = if m.protocol.protocol == "mit" {
                Self::send(&mut self.buses, m, m.node, &p::MIT_EXIT, false)
            } else {
                Self::send(
                    &mut self.buses,
                    m,
                    p::odrive_id(m.node, od::SET_AXIS_STATE),
                    &p::odrive_axis_state(od::IDLE),
                    false,
                )
            };
            if let Err(e) = result {
                if first.is_none() {
                    first = Some(e);
                }
            }
        }
        match first {
            Some(e) => Err(e),
            None => Ok(()),
        }
    }
    fn set_zero(&mut self, joint: &str) -> Result<f64> {
        self.require()?;
        self.poll()?;
        let m = self.motors.get_mut(joint).context("Unknown motor joint")?;
        if !m
            .updated
            .is_some_and(|t| t.elapsed().as_secs_f64() < self.settings.limits.state_timeout)
        {
            bail!("Cannot zero a motor without a fresh encoder reading");
        }
        m.zero = m.position;
        Ok(m.zero)
    }
}
impl Drop for CanDriver {
    fn drop(&mut self) {
        let _ = self.disconnect();
    }
}
