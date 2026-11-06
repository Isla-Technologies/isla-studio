// Which part of a robot's model is each motor's actuator, for the anatomy view's X-ray.
//
// A CAD export usually keeps every actuator as its own part, and an actuator has a
// clear signature: it sits on its joint's axis, around the joint's anchor, and it is
// the heaviest part there (Luma-1's housings weigh 1.1 kg macro and 0.32 kg micro).
// Robots whose motors are buried inside a link's mesh get a drawn cylinder instead,
// sized from the motor's torque: it shows where the motor is, not what it looks like.

import * as THREE from 'three';
import { Kinematics } from '../lib/kinematics';
import { GEOM, threeQuat, type RobotAssets } from '../lib/robot';
import type { GeomMeta, JointMeta, Meta } from '../lib/types';

export interface ActuatorFit {
  joint: JointMeta;
  /** The body the actuator rides on. */
  body: number;
  /** The actuator's centre and axis in that body's frame. */
  center: THREE.Vector3;
  axis: THREE.Vector3;
  /** Its radius about the axis and its width along it, in metres. */
  radius: number;
  width: number;
  /** The part's own geoms; empty when the actuator is drawn as a cylinder. */
  geoms: GeomMeta[];
}

/** Wider than this about the axis, or longer along it, and a part is a link, not a motor. */
const LARGEST_RADIUS = 0.11;
const LARGEST_WIDTH = 0.16;
/** The anchor may sit on the actuator's face rather than inside it. */
const MARGIN = 0.01;

interface Part {
  body: number;
  geoms: GeomMeta[];
  points: number[]; // world x, y, z in the standing pose
}

interface Candidate {
  joint: JointMeta;
  part: Part;
  mass: number;
  distance: number;
  radius: number;
  width: number;
  center: THREE.Vector3;
}

export function findActuators(meta: Meta, assets: RobotAssets): ActuatorFit[] {
  const kin = new Kinematics(meta);
  kin.compute(meta.stand);
  const one = new THREE.Vector3(1, 1, 1);
  const bodyMatrix = (b: number) => new THREE.Matrix4().compose(new THREE.Vector3(kin.xpos[b * 3], kin.xpos[b * 3 + 1], kin.xpos[b * 3 + 2]), threeQuat(kin.xquat, b * 4), one);

  // Every drawn mesh part, its points placed in the standing pose.
  const parts = new Map<number, Part>();
  const v = new THREE.Vector3();
  for (const geom of meta.geoms) {
    if (geom.type !== GEOM.MESH || geom.mesh < 0) continue;
    const kind = meta.bodies[geom.body].kind;
    if (kind === 'virtual' || kind === 'rod') continue;
    const position = assets.solids[geom.mesh]?.getAttribute('position');
    if (!position) continue;
    const m = bodyMatrix(geom.body).multiply(new THREE.Matrix4().compose(new THREE.Vector3(...geom.pos), threeQuat(geom.quat), one));
    let part = parts.get(geom.body);
    if (!part) parts.set(geom.body, (part = { body: geom.body, geoms: [], points: [] }));
    part.geoms.push(geom);
    for (let i = 0; i < position.count; i++) {
      v.fromBufferAttribute(position, i).applyMatrix4(m);
      part.points.push(v.x, v.y, v.z);
    }
  }

  // Every part that could be a motor's actuator: on its axis, around its anchor, motor-sized.
  const candidates: Candidate[] = [];
  const anchor = new THREE.Vector3();
  const axis = new THREE.Vector3();
  for (const joint of meta.joints) {
    if (!joint.motor) continue;
    const child = meta.bodies[joint.body];
    const links = new Set([child.link, meta.bodies[child.parent]?.link ?? child.link]);
    anchor.fromArray(kin.anchors, joint.id * 3);
    axis.fromArray(kin.axes, joint.id * 3).normalize();
    for (const part of parts.values()) {
      if (!links.has(meta.bodies[part.body].link)) continue;
      const p = part.points;
      const count = p.length / 3;
      if (!count) continue;
      let ox = 0, oy = 0, oz = 0, rmax = 0, lo = Infinity, hi = -Infinity;
      let cx = 0, cy = 0, cz = 0;
      const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
      for (let i = 0; i < p.length; i += 3) {
        const dx = p[i] - anchor.x, dy = p[i + 1] - anchor.y, dz = p[i + 2] - anchor.z;
        const t = dx * axis.x + dy * axis.y + dz * axis.z;
        const rx = dx - t * axis.x, ry = dy - t * axis.y, rz = dz - t * axis.z;
        ox += rx; oy += ry; oz += rz;
        rmax = Math.max(rmax, Math.hypot(rx, ry, rz));
        lo = Math.min(lo, t);
        hi = Math.max(hi, t);
        cx += p[i]; cy += p[i + 1]; cz += p[i + 2];
        for (let k = 0; k < 3; k++) {
          min[k] = Math.min(min[k], p[i + k]);
          max[k] = Math.max(max[k], p[i + k]);
        }
      }
      const inside = [anchor.x, anchor.y, anchor.z].every((a, k) => a > min[k] - MARGIN && a < max[k] + MARGIN);
      const off = Math.hypot(ox, oy, oz) / count; // how far the part's middle sits off the axis
      if (!inside || off > Math.max(0.004, 0.08 * rmax) || rmax > LARGEST_RADIUS || hi - lo > LARGEST_WIDTH) continue;
      candidates.push({
        joint,
        part,
        mass: meta.bodies[part.body].mass,
        distance: Math.hypot(cx / count - anchor.x, cy / count - anchor.y, cz / count - anchor.z),
        radius: rmax,
        width: hi - lo,
        center: anchor.clone().addScaledVector(axis, (lo + hi) / 2),
      });
    }
  }

  // Heaviest first; between parts of the same weight, the one nearest the anchor. A part drives one joint.
  candidates.sort((a, b) => (Math.abs(a.mass - b.mass) > 0.02 * Math.max(a.mass, b.mass) ? b.mass - a.mass : a.distance - b.distance));
  const chosen = new Map<number, Candidate>();
  const taken = new Set<number>();
  for (const c of candidates) {
    if (chosen.has(c.joint.id) || taken.has(c.part.body)) continue;
    chosen.set(c.joint.id, c);
    taken.add(c.part.body);
  }

  const fits: ActuatorFit[] = [];
  for (const joint of meta.joints) {
    if (!joint.motor) continue;
    const c = chosen.get(joint.id);
    if (c) {
      const inverse = bodyMatrix(c.part.body).invert();
      const turn = new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().extractRotation(inverse));
      axis.fromArray(kin.axes, joint.id * 3).normalize();
      fits.push({ joint, body: c.part.body, center: c.center.clone().applyMatrix4(inverse), axis: axis.clone().applyQuaternion(turn), radius: c.radius, width: c.width, geoms: c.part.geoms });
    } else {
      // No part of its own: a cylinder at the joint, about a 60 mm motor at 25 N·m, growing with the cube root of torque.
      const radius = Math.min(0.07, Math.max(0.018, 0.03 * Math.cbrt(Math.max(joint.motor.peak_torque, 1) / 25)));
      fits.push({ joint, body: joint.body, center: new THREE.Vector3(...joint.pos), axis: new THREE.Vector3(...joint.axis).normalize(), radius, width: 1.1 * radius, geoms: [] });
    }
  }
  return fits;
}
