// MuJoCo's forward kinematics (mj_kinematics), in the browser.
//
// The server streams only joint positions; every body's pose follows from the
// model's tree, exactly as MuJoCo computes it: a body sits at its parent's pose
// times its own offset, then each of its joints turns (hinge, ball) or slides it
// about the joint's anchor. Quaternions are (w, x, y, z) as in MuJoCo.

import type { Meta } from './types';

const FREE = 0;
const BALL = 1;
const SLIDE = 2;
const HINGE = 3;

export function mulQuat(out: Float64Array, o: number, a: ArrayLike<number>, ao: number, b: ArrayLike<number>, bo: number): void {
  const aw = a[ao], ax = a[ao + 1], ay = a[ao + 2], az = a[ao + 3];
  const bw = b[bo], bx = b[bo + 1], by = b[bo + 2], bz = b[bo + 3];
  out[o] = aw * bw - ax * bx - ay * by - az * bz;
  out[o + 1] = aw * bx + ax * bw + ay * bz - az * by;
  out[o + 2] = aw * by - ax * bz + ay * bw + az * bx;
  out[o + 3] = aw * bz + ax * by - ay * bx + az * bw;
}

/** Rotate vector v by unit quaternion q, writing to out[o..o+2]. */
export function rotate(out: Float64Array, o: number, q: ArrayLike<number>, qo: number, vx: number, vy: number, vz: number): void {
  const w = q[qo], x = q[qo + 1], y = q[qo + 2], z = q[qo + 3];
  // t = 2 * cross(q.xyz, v); v' = v + w t + cross(q.xyz, t)
  const tx = 2 * (y * vz - z * vy);
  const ty = 2 * (z * vx - x * vz);
  const tz = 2 * (x * vy - y * vx);
  out[o] = vx + w * tx + (y * tz - z * ty);
  out[o + 1] = vy + w * ty + (z * tx - x * tz);
  out[o + 2] = vz + w * tz + (x * ty - y * tx);
}

export class Kinematics {
  readonly nbody: number;
  readonly parent: Int32Array;
  readonly bodyPos: Float64Array;
  readonly bodyQuat: Float64Array;
  readonly xpos: Float64Array;
  readonly xquat: Float64Array;
  readonly anchors: Float64Array; // per joint, world anchor
  readonly axes: Float64Array; // per joint, world axis
  private readonly jointsOf: number[][];
  private readonly jtype: Int32Array;
  private readonly jqpos: Int32Array;
  private readonly jaxis: Float64Array;
  private readonly jpos: Float64Array;
  private readonly qpos0: Float64Array;
  private readonly tmpQ = new Float64Array(4);
  private readonly tmpQ2 = new Float64Array(4);
  private readonly tmpV = new Float64Array(3);

  constructor(meta: Meta) {
    const n = meta.bodies.length;
    this.nbody = n;
    this.parent = new Int32Array(n);
    this.bodyPos = new Float64Array(n * 3);
    this.bodyQuat = new Float64Array(n * 4);
    this.xpos = new Float64Array(n * 3);
    this.xquat = new Float64Array(n * 4);
    this.jointsOf = Array.from({ length: n }, () => [] as number[]);
    for (const b of meta.bodies) {
      this.parent[b.id] = b.parent;
      this.bodyPos.set(b.pos, b.id * 3);
      this.bodyQuat.set(b.quat, b.id * 4);
    }
    const nj = meta.joints.length;
    this.jtype = new Int32Array(nj);
    this.jqpos = new Int32Array(nj);
    this.jaxis = new Float64Array(nj * 3);
    this.jpos = new Float64Array(nj * 3);
    this.anchors = new Float64Array(nj * 3);
    this.axes = new Float64Array(nj * 3);
    for (const j of meta.joints) {
      this.jointsOf[j.body].push(j.id);
      this.jtype[j.id] = j.type;
      this.jqpos[j.id] = j.qpos;
      this.jaxis.set(j.axis, j.id * 3);
      this.jpos.set(j.pos, j.id * 3);
    }
    this.qpos0 = Float64Array.from(meta.qpos0);
    this.xquat[0] = 1;
  }

  /**
   * Pose every body for joint positions ``qpos``. ``offsets`` maps a body to an
   * extra translation of its frame within its parent (rod length changes).
   */
  compute(qpos: ArrayLike<number>, offsets?: Map<number, [number, number, number]>): void {
    const { xpos, xquat, parent, bodyPos, bodyQuat, tmpQ, tmpQ2, tmpV } = this;
    for (let b = 1; b < this.nbody; b++) {
      const p = parent[b];
      let px = bodyPos[b * 3], py = bodyPos[b * 3 + 1], pz = bodyPos[b * 3 + 2];
      const shift = offsets?.get(b);
      if (shift) {
        px += shift[0];
        py += shift[1];
        pz += shift[2];
      }
      rotate(tmpV, 0, xquat, p * 4, px, py, pz);
      xpos[b * 3] = xpos[p * 3] + tmpV[0];
      xpos[b * 3 + 1] = xpos[p * 3 + 1] + tmpV[1];
      xpos[b * 3 + 2] = xpos[p * 3 + 2] + tmpV[2];
      mulQuat(xquat, b * 4, xquat, p * 4, bodyQuat, b * 4);
      for (const j of this.jointsOf[b]) {
        const type = this.jtype[j];
        const adr = this.jqpos[j];
        if (type === FREE) {
          xpos[b * 3] = qpos[adr];
          xpos[b * 3 + 1] = qpos[adr + 1];
          xpos[b * 3 + 2] = qpos[adr + 2];
          const w = qpos[adr + 3], x = qpos[adr + 4], y = qpos[adr + 5], z = qpos[adr + 6];
          const norm = Math.hypot(w, x, y, z) || 1;
          xquat[b * 4] = w / norm;
          xquat[b * 4 + 1] = x / norm;
          xquat[b * 4 + 2] = y / norm;
          xquat[b * 4 + 3] = z / norm;
          this.anchors.set([xpos[b * 3], xpos[b * 3 + 1], xpos[b * 3 + 2]], j * 3);
          continue;
        }
        const ax = this.jaxis[j * 3], ay = this.jaxis[j * 3 + 1], az = this.jaxis[j * 3 + 2];
        const jx = this.jpos[j * 3], jy = this.jpos[j * 3 + 1], jz = this.jpos[j * 3 + 2];
        // World axis and anchor, before this joint moves the body.
        rotate(this.axes, j * 3, xquat, b * 4, ax, ay, az);
        rotate(tmpV, 0, xquat, b * 4, jx, jy, jz);
        const anchorX = xpos[b * 3] + tmpV[0];
        const anchorY = xpos[b * 3 + 1] + tmpV[1];
        const anchorZ = xpos[b * 3 + 2] + tmpV[2];
        this.anchors[j * 3] = anchorX;
        this.anchors[j * 3 + 1] = anchorY;
        this.anchors[j * 3 + 2] = anchorZ;
        if (type === SLIDE) {
          const d = qpos[adr] - this.qpos0[adr];
          xpos[b * 3] += this.axes[j * 3] * d;
          xpos[b * 3 + 1] += this.axes[j * 3 + 1] * d;
          xpos[b * 3 + 2] += this.axes[j * 3 + 2] * d;
          continue;
        }
        if (type === HINGE) {
          const angle = qpos[adr] - this.qpos0[adr];
          const s = Math.sin(angle / 2);
          tmpQ[0] = Math.cos(angle / 2);
          tmpQ[1] = ax * s;
          tmpQ[2] = ay * s;
          tmpQ[3] = az * s;
        } else if (type === BALL) {
          tmpQ[0] = qpos[adr];
          tmpQ[1] = qpos[adr + 1];
          tmpQ[2] = qpos[adr + 2];
          tmpQ[3] = qpos[adr + 3];
        }
        mulQuat(tmpQ2, 0, xquat, b * 4, tmpQ, 0);
        xquat.set(tmpQ2, b * 4);
        // Turn about the anchor, not the body's origin.
        rotate(tmpV, 0, xquat, b * 4, jx, jy, jz);
        xpos[b * 3] = anchorX - tmpV[0];
        xpos[b * 3 + 1] = anchorY - tmpV[1];
        xpos[b * 3 + 2] = anchorZ - tmpV[2];
      }
      const o = b * 4;
      const norm = Math.hypot(xquat[o], xquat[o + 1], xquat[o + 2], xquat[o + 3]) || 1;
      xquat[o] /= norm;
      xquat[o + 1] /= norm;
      xquat[o + 2] /= norm;
      xquat[o + 3] /= norm;
    }
  }

  /** A point given in a body's frame, in the world. */
  point(body: number, local: ArrayLike<number>, out: Float64Array = new Float64Array(3)): Float64Array {
    rotate(out, 0, this.xquat, body * 4, local[0], local[1], local[2]);
    out[0] += this.xpos[body * 3];
    out[1] += this.xpos[body * 3 + 1];
    out[2] += this.xpos[body * 3 + 2];
    return out;
  }
}

/** Translations of the bodies the rods slide, for a design (values in mm). */
export function designOffsets(meta: Meta, design: Record<string, number>): Map<number, [number, number, number]> {
  const offsets = new Map<number, [number, number, number]>();
  const byRod: Record<string, number> = {};
  for (const [key, param] of Object.entries(meta.robot.design)) {
    if (param.kind === 'rod' && param.rod) byRod[param.rod] = design[key] ?? param.default;
  }
  for (const rod of meta.rods) {
    const ext = (byRod[rod.name] ?? 0) / 1000;
    const body = meta.bodies[rod.moved];
    // The meta's body position already carries the server's design; rebase on the rod's zero.
    offsets.set(rod.moved, [
      rod.base_pos[0] + ext * rod.direction[0] - body.pos[0],
      rod.base_pos[1] + ext * rod.direction[1] - body.pos[1],
      rod.base_pos[2] + ext * rod.direction[2] - body.pos[2],
    ]);
  }
  return offsets;
}

export function rodExtension(meta: Meta, design: Record<string, number>, rodName: string): number {
  for (const [key, param] of Object.entries(meta.robot.design)) {
    if (param.kind === 'rod' && param.rod === rodName) return (design[key] ?? param.default) / 1000;
  }
  return 0;
}
