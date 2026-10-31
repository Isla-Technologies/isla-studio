// Shapes of what the platform's server sends.

export interface DesignParam {
  key: string;
  label: string;
  unit: string;
  kind: 'rod' | 'strut_k' | 'strut_c' | 'strut_preload';
  range: [number, number];
  default: number;
  step: number;
  help: string;
  rod: string | null;
  position: string | null;
}

export interface MotorView {
  joint: string;
  type: string;
  bus: string;
  node: number;
  direction: number;
  zero: number;
  group: string;
}

export interface ActuatorType {
  label: string;
  peak_torque: number;
  no_load_speed?: number;
  corner_speed?: number;
  kp: number;
  kd: number;
  driver?: string;
  protocol?: string;
  assumed?: string[];
  note?: string;
}

export interface RobotView {
  id: string;
  name: string;
  aliases?: string[];
  about: string;
  frame: { forward: string; left: string };
  mass: { target_total?: number; note?: string };
  actuator_types: Record<string, ActuatorType>;
  motors: MotorView[];
  operating_ranges: Record<string, [number, number]>;
  struts: { stroke_mm?: number; note?: string; items?: unknown[] };
  rods: { note?: string };
  design: Record<string, DesignParam>;
  design_values: Record<string, number>;
}

export interface BodyMeta {
  id: number;
  name: string;
  label: string;
  parent: number;
  pos: [number, number, number];
  quat: [number, number, number, number];
  mass: number;
  link: number;
  kind: 'world' | 'part' | 'rod' | 'virtual' | 'link';
  com: [number, number, number];
}

export interface JointMotor {
  type: string;
  label: string;
  peak_torque: number;
  kp: number;
  kd: number;
  bus: string;
  node: number;
  direction: number;
  zero: number;
  group: string;
  low: number;
  high: number;
  actuator: string;
  driver: string;
  protocol: string;
}

export interface JointMeta {
  id: number;
  name: string;
  label: string;
  type: number; // 0 free, 1 ball, 2 slide, 3 hinge
  body: number;
  qpos: number;
  dof: number;
  axis: [number, number, number];
  pos: [number, number, number];
  range: [number, number] | null;
  passive: boolean;
  motor: JointMotor | null;
}

export interface GeomMeta {
  id: number;
  body: number;
  type: number; // 2 sphere, 3 capsule, 5 cylinder, 6 box, 7 mesh
  mesh: number;
  pos: [number, number, number];
  quat: [number, number, number, number];
  size: [number, number, number];
  material: string;
  rgba: [number, number, number, number];
}

export interface StrutMeta {
  name: string;
  position: string;
  side: string;
  upper: { body: number; pos: [number, number, number] };
  lower: { body: number; pos: [number, number, number] };
  length_min: number;
  length_max: number;
  length_cad: number;
}

export interface RodPiece {
  body: number;
  top: [number, number, number];
  bottom: [number, number, number];
  direction: [number, number, number];
  radius: number;
  length: number;
}

export interface RodMeta {
  name: string;
  side: string;
  moved: number;
  direction: [number, number, number];
  base_pos: [number, number, number];
  segment_cad: number;
  rods: RodPiece[];
}

export interface LinkMeta {
  body: number;
  joint: number;
  label: string;
  members: number[];
  mass: number;
}

export interface MeshIndex {
  mesh: number;
  name: string;
  v: [number, number];
  f: [number, number];
  e: [number, number];
  original_faces: number;
}

export interface Meta {
  version: number;
  robot: RobotView;
  info: {
    base: string;
    torso: string | null;
    forward: number[];
    left: number[];
    cad_mass: number;
    virtual_mass: number;
    total_mass: number;
    height: number;
    hip_height: number;
    stand_pitch: number;
    passive: string[];
  };
  nq: number;
  nv: number;
  nu: number;
  qpos0: number[];
  stand: number[];
  bodies: BodyMeta[];
  joints: JointMeta[];
  geoms: GeomMeta[];
  struts: StrutMeta[];
  rods: RodMeta[];
  links: LinkMeta[];
  sites: Record<string, { body: number; pos: [number, number, number] }>;
  actuators: { id: number; name: string; joint: string }[];
  meshes: MeshIndex[];
  design_values: Record<string, number>;
}

/** One simulation frame: joint positions and live telemetry. */
export interface Frame {
  type: 'frame';
  source: 'sim' | 'mirror';
  t: number;
  qpos: number[];
  motors: [number, number, number, number][]; // q, dq, torque, target per actuator
  struts: [number, number][]; // length (m), force (N)
  touch: Record<string, number>;
  com: [number, number, number];
  tilt: number;
  mode: string;
  gantry: boolean;
  stands_alone: boolean;
  steers: boolean;
  can_jump: boolean;
  jumping: boolean;
  playing: boolean;
  policy: string;
  command: [number, number, number];
  message: string;
}

/** A robot's card in the hangar: its numbers and a front-view blueprint. */
export interface RobotCard {
  id: string;
  name: string;
  about: string;
  imported: boolean;
  active: boolean;
  runs: number;
  policies: number;
  problem?: string;
  mass: number;
  cad_mass: number;
  virtual_mass: number;
  height: number;
  hip_height: number;
  motors: number;
  dof: number;
  passive: number;
  feet: number;
  struts: number;
  rods: number;
  actuators: { type: string; label: string; count: number; peak_torque: number; gear_ratio: number | null }[];
  peak_torque: number;
  design: string[];
  stands_alone: boolean;
  stand_drift: number;
  blueprint: {
    parts: { link: number; points: number[] }[];
    motors: [number, number][];
    bounds: [number, number, number, number];
  };
}

export interface JointStatus {
  online: boolean;
  q: number | null;
  dq: number | null;
  tau: number | null;
  temp: number | null;
  fault: string | null;
  target: number | null;
  bus: string;
  node: number;
}

export interface HardwareStatus {
  type: 'hardware';
  state: 'disconnected' | 'connected' | 'armed' | 'fault';
  mode: 'hold' | 'follow' | 'policy';
  fault: string | null;
  driver: string;
  driver_name: string;
  rate: number;
  armed_for: number;
  policy: string;
  can_jump: boolean;
  joints: Record<string, JointStatus>;
  imu: { quat: number[]; gyro: number[] } | null;
  limits: Record<string, number>;
}

export interface ProgressMessage {
  type: 'progress';
  iteration: number;
  steps: number;
  seconds: number;
  rate: number;
  score: number | null;
  length: number | null;
  falls: number | null;
  speed_error: number | null;
  impact: number | null;
  power: number | null;
  std: number;
  learning_rate: number;
  value_loss: number;
  device: string;
  phase: string;
  total: number;
  rewards: Record<string, number>;
}

export interface CodesignReport {
  type: 'codesign';
  phase: string;
  generation: number;
  searched: string[];
  mean: Record<string, number>;
  spread: Record<string, number>;
  chosen: Record<string, number>;
  chosen_metrics: { height: number; hip_height: number; com_height: number; pitch: number };
  best: { design: Record<string, number>; score: number | null; metrics: Record<string, number> };
  candidates: { design: number[]; score: number | null; seconds: number; height: number }[];
  keys: string[];
  iteration?: number;
}

export interface SwarmMessage {
  type: 'swarm';
  shape: [number, number, number]; // time, robots, nq
  dt: number;
  qpos: string; // base64 float32
  designs: number[][];
}

export interface RunSummary {
  name: string;
  robot: string;
  skill: string;
  codesign: boolean;
  robots: number;
  total: number;
  steps: number;
  score: number | null;
  seconds: number;
  device: string | null;
  has_policy: boolean;
  status: string;
  running: boolean;
  evaluated?: boolean;
  evaluating?: boolean;
  phase?: string;
  modified: number;
}

/** A policy tried in full physics: ``python -m isla_rl.learn evaluate`` (isla_rl/learn/evaluate.py). */
export interface Evaluation {
  policy: string;
  robot: string;
  skill: string;
  design: Record<string, number>;
  mass: number;
  steps?: number;
  tested?: string;
  walking?: {
    commands: { forward: number[]; sideways: number; turning: number };
    forward: { asked: number; forward: number; left: number; turn: number; max_tilt: number; fell: boolean; sway?: number; wobble?: number; bounce?: number }[];
    backward: EvaluatedMove;
    left: EvaluatedMove;
    right: EvaluatedMove;
    turn_left: EvaluatedMove;
    turn_right: EvaluatedMove;
    curve: EvaluatedMove;
    shoves: { standing: EvaluatedShoves; walking: EvaluatedShoves };
    roam: { seconds: number; falls: number; speed_error: number };
  };
  jumping?: {
    stands: boolean;
    shoves: EvaluatedShoves;
    jumps: { rise_cm: number; clearance_cm: number; airborne_ms: number; landing_load: number; max_tilt: number; landed: boolean }[];
    landed: string;
    rise_cm_median: number;
    rise_cm_best: number;
    clearance_cm_median: number;
    clearance_cm_best: number;
    airborne_ms_median: number;
    airborne_ms_best: number;
  };
  standing?: { stands: boolean; shoves: EvaluatedShoves };
  /** Loads by kind of trial, from the trials the robot came through standing. */
  phases?: Record<string, { label: string; trials: number; falls: number; shocks: Evaluation['shocks']; motors: Evaluation['motors'] }>;
  shocks: {
    name: string;
    position: string;
    stroke_mm: number;
    used_mm: number;
    closest_to_bottom_mm: number;
    closest_to_top_mm: number;
    on_stop_share: number;
    shaft_speed_peak: number;
    spring_force_peak: number;
    force_peak: number;
    stop_force_peak: number;
    preload_force: number;
  }[];
  motors: {
    joint: string;
    type: string;
    rating: number;
    torque_peak: number;
    torque_rms: number;
    speed_peak: number;
    no_load_speed: number | null;
    saturated_share: number;
  }[];
}

export interface EvaluatedMove {
  asked: number[];
  forward: number;
  left: number;
  turn: number;
  fell: boolean;
  sway?: number;
  wobble?: number;
  bounce?: number;
}

export interface EvaluatedShoves {
  speeds: number[];
  directions: number;
  held: boolean[][];
  every_side_up_to: number;
  some_sides_up_to: number;
}
