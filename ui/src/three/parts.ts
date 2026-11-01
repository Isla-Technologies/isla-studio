// A robot's parts as Three.js draws them, shared by the live view, the anatomy
// HUD and the hangar: solids and wireframe edges for every MuJoCo geom type, and
// the surfaces they wear.
//
// Robots arrive with their own colours. The brand has one accent, so a robot is
// shown in greys: known CAD materials keep their finish, anything else becomes a
// grey as light as its own colour.

import * as THREE from 'three';
import { designOffsets, Kinematics, rodExtension, rotate } from '../lib/kinematics';
import { GEOM, threeQuat, type RobotAssets } from '../lib/robot';
import type { GeomMeta, Meta } from '../lib/types';

/** The solid shape of a part, in its geom frame, or null for types not drawn (planes, height fields). */
export function solidFor(geom: GeomMeta, assets: RobotAssets): THREE.BufferGeometry | null {
  const [a, b, c] = geom.size;
  switch (geom.type) {
    case GEOM.MESH:
      return geom.mesh >= 0 ? assets.solids[geom.mesh] : null;
    case GEOM.BOX:
      return new THREE.BoxGeometry(2 * a, 2 * b, 2 * c);
    case GEOM.CYLINDER:
      return new THREE.CylinderGeometry(a, a, 2 * b, 28).rotateX(Math.PI / 2);
    case GEOM.SPHERE:
      return new THREE.SphereGeometry(a, 24, 16);
    case GEOM.ELLIPSOID:
      return new THREE.SphereGeometry(1, 24, 16).scale(a, b, c);
    case GEOM.CAPSULE:
      return new THREE.CapsuleGeometry(a, 2 * b, 8, 20).rotateX(Math.PI / 2);
    default:
      return null;
  }
}

/** Line segments outlining a part: a mesh's feature edges, or a primitive drawn as a wireframe. */
export function edgesFor(geom: GeomMeta, assets: RobotAssets): THREE.BufferGeometry | null {
  const [a, b, c] = geom.size;
  const points: number[] = [];
  // A circle in the plane of two axes (0 x, 1 y, 2 z), around ``center``.
  const ring = (radius: [number, number], axes: [number, number], center: [number, number, number], from = 0, to = Math.PI * 2, steps = 40) => {
    for (let i = 0; i < steps; i++) {
      for (const t of [i, i + 1]) {
        const angle = from + ((to - from) * t) / steps;
        const p = [...center];
        p[axes[0]] += radius[0] * Math.cos(angle);
        p[axes[1]] += radius[1] * Math.sin(angle);
        points.push(p[0], p[1], p[2]);
      }
    }
  };
  const line = (p: [number, number, number], q: [number, number, number]) => points.push(...p, ...q);
  switch (geom.type) {
    case GEOM.MESH:
      return geom.mesh >= 0 ? assets.edges[geom.mesh] : null;
    case GEOM.BOX:
      return new THREE.EdgesGeometry(new THREE.BoxGeometry(2 * a, 2 * b, 2 * c));
    case GEOM.CYLINDER:
    case GEOM.CAPSULE: {
      const h = b;
      ring([a, a], [0, 1], [0, 0, h]);
      ring([a, a], [0, 1], [0, 0, -h]);
      for (const angle of [0, Math.PI / 2, Math.PI, (3 * Math.PI) / 2]) {
        line([a * Math.cos(angle), a * Math.sin(angle), -h], [a * Math.cos(angle), a * Math.sin(angle), h]);
      }
      if (geom.type === GEOM.CAPSULE) {
        for (const sign of [1, -1]) {
          const from = sign > 0 ? 0 : Math.PI;
          ring([a, a], [0, 2], [0, 0, sign * h], from, from + Math.PI, 20);
          ring([a, a], [1, 2], [0, 0, sign * h], from, from + Math.PI, 20);
        }
      }
      break;
    }
    case GEOM.SPHERE:
    case GEOM.ELLIPSOID: {
      const [rx, ry, rz] = geom.type === GEOM.SPHERE ? [a, a, a] : [a, b, c];
      ring([rx, ry], [0, 1], [0, 0, 0]);
      ring([rx, rz], [0, 2], [0, 0, 0]);
      ring([ry, rz], [1, 2], [0, 0, 0]);
      break;
    }
    default:
      return null;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
  return geometry;
}

/** Finishes of the CAD materials Isla's robots use. */
const FINISHES: Record<string, THREE.MeshStandardMaterialParameters> = {
  component_white: { color: 0xcfcfcf, roughness: 0.52, metalness: 0.04 },
  carbon_fiber_dark_gray: { color: 0x24262a, roughness: 0.32, metalness: 0.35 },
  component_metal: { color: 0x9a9da3, roughness: 0.28, metalness: 0.85 },
  visor_screen_tinted_black: { color: 0x050607, roughness: 0.08, metalness: 0.4 },
  eye_led: { color: 0xffffff, emissive: 0xffffff, emissiveIntensity: 1.4 },
};

/** Which surface a part wears: its material's name, or its colour's lightness. */
export function surfaceKey(geom: GeomMeta): string {
  if (geom.material && geom.material in FINISHES) return geom.material;
  return `grey:${Math.round(lightness(geom.rgba) * 20)}`;
}

/** The surface for a key from ``surfaceKey``. */
export function surfaceFor(key: string): THREE.MeshStandardMaterial {
  if (key in FINISHES) return new THREE.MeshStandardMaterial(FINISHES[key]);
  const level = Number(key.split(':')[1] ?? 4) / 20;
  // Keep greys in the range the brand's robots render in: never paper white, never a hole.
  const grey = 0.07 + 0.66 * level;
  return new THREE.MeshStandardMaterial({ color: new THREE.Color(grey, grey, grey), roughness: 0.5, metalness: 0.18 });
}

/** Perceived lightness (0 to 1) of a linear RGBA colour. */
function lightness(rgba: ArrayLike<number>): number {
  const [r, g, b] = [rgba[0] ?? 0.5, rgba[1] ?? 0.5, rgba[2] ?? 0.5];
  return Math.min(1, Math.max(0, Math.cbrt(0.2126 * r + 0.7152 * g + 0.0722 * b)));
}

/**
 * The robot in its standing pose as one group of solid parts, rods and shock
 * absorbers: for showing a robot that is not simulated (the hangar). Every mesh
 * keeps ``userData.body``.
 */
export function staticRobot(assets: RobotAssets, design: Record<string, number>): THREE.Group {
  const meta = assets.meta;
  const kin = new Kinematics(meta);
  kin.compute(meta.stand, designOffsets(meta, design));
  const group = new THREE.Group();
  const surfaces = new Map<string, THREE.MeshStandardMaterial>();
  const surface = (key: string) => {
    let material = surfaces.get(key);
    if (!material) surfaces.set(key, (material = surfaceFor(key)));
    return material;
  };
  const bodyMatrix = (body: number) =>
    new THREE.Matrix4().compose(new THREE.Vector3(kin.xpos[body * 3], kin.xpos[body * 3 + 1], kin.xpos[body * 3 + 2]), threeQuat(kin.xquat, body * 4), new THREE.Vector3(1, 1, 1));
  const rodBodies = new Set(meta.rods.flatMap((r) => r.rods.map((p) => p.body)));
  for (const geom of meta.geoms) {
    if (meta.bodies[geom.body].kind === 'virtual') continue;
    if (rodBodies.has(geom.body) && geom.type === GEOM.CYLINDER) continue; // Drawn from the design below.
    const solid = solidFor(geom, assets);
    if (!solid) continue;
    const mesh = new THREE.Mesh(solid, surface(surfaceKey(geom)));
    mesh.matrixAutoUpdate = false;
    mesh.matrix.copy(bodyMatrix(geom.body)).multiply(new THREE.Matrix4().compose(new THREE.Vector3(...geom.pos), threeQuat(geom.quat), new THREE.Vector3(1, 1, 1)));
    mesh.castShadow = true;
    mesh.userData.body = geom.body;
    group.add(mesh);
  }
  const carbon = surface('carbon_fiber_dark_gray');
  for (const rod of meta.rods) {
    const extension = rodExtension(meta, design, rod.name);
    for (const piece of rod.rods) {
      const top = new THREE.Vector3(...piece.top);
      const bottom = new THREE.Vector3(...piece.bottom).addScaledVector(new THREE.Vector3(...piece.direction), extension);
      const span = bottom.clone().sub(top);
      const geometry = new THREE.CylinderGeometry(piece.radius, piece.radius, 1, 20).rotateX(Math.PI / 2).translate(0, 0, 0.5);
      const mesh = new THREE.Mesh(geometry, carbon);
      mesh.matrixAutoUpdate = false;
      const local = new THREE.Matrix4().compose(top, new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), span.clone().normalize()), new THREE.Vector3(1, 1, span.length()));
      mesh.matrix.copy(bodyMatrix(piece.body)).multiply(local);
      mesh.castShadow = true;
      mesh.userData.body = piece.body;
      group.add(mesh);
    }
  }
  if (meta.struts.length) {
    const shocks = new ShockAbsorbers(meta.struts.map((s) => s.length_cad), 1);
    shocks.setCount(1);
    const a = new Float64Array(3), b = new Float64Array(3), bolt = new THREE.Vector3();
    meta.struts.forEach((strut, k) => {
      kin.point(strut.upper.body, strut.upper.pos, a);
      kin.point(strut.lower.body, strut.lower.pos, b);
      shocks.place(0, k, new THREE.Vector3(b[0], b[1], b[2]), new THREE.Vector3(a[0], a[1], a[2]), shockBolt(meta, kin, strut, bolt));
    });
    shocks.commit();
    group.add(shocks.group);
  }
  // Parts placed by hand still need their world matrices worked out before anyone measures them.
  group.updateMatrixWorld(true);
  group.userData.base = new THREE.Vector3(kin.xpos[3], kin.xpos[4], kin.xpos[5]);
  // Where the robot faces, along the floor: its forward axis turned by the base's standing pose.
  const f = meta.info.forward;
  const facing = new Float64Array(3);
  rotate(facing, 0, kin.xquat, 4, f[0], f[1], f[2]);
  const front = new THREE.Vector3(facing[0], facing[1], 0);
  group.userData.front = front.lengthSq() > 1e-9 ? front.normalize() : new THREE.Vector3(1, 0, 0);
  return group;
}

// Shock absorbers.

/** Small coilovers (eye to eye 70 to 120 mm), in metres. */
const SHOCK = {
  eyeOuter: 0.0056,
  eyeInner: 0.0025,
  eyeWidth: 0.0065,
  bodyStart: 0.004, // From the lower eye's centre.
  bodyShare: 0.45, // Of the length eye to eye at rest.
  bodyRadius: 0.0068,
  rodRadius: 0.0023,
  capRadius: 0.0066,
  capHeight: 0.006,
  capGap: 0.009, // From the upper eye's centre down to the cap's foot.
  seatRadius: 0.0108,
  seatHeight: 0.0022,
  nutHeight: 0.0016, // The preload locknut, just under the lower seat.
  seatAt: 0.35, // Lower seat: this share up the body.
  upperSeatGap: 0.0105,
  coilRadius: 0.0086,
  wireRadius: 0.0014,
  turns: 6,
};

class Helix extends THREE.Curve<THREE.Vector3> {
  constructor(private length: number) {
    super();
  }
  override getPoint(t: number, target = new THREE.Vector3()): THREE.Vector3 {
    const a = t * SHOCK.turns * Math.PI * 2;
    return target.set(Math.cos(a) * SHOCK.coilRadius, Math.sin(a) * SHOCK.coilRadius, t * this.length);
  }
}

/** Where a shock absorber sits: its two eyes' centres and the axis of their mounting bolts. */
export function shockLayout(restLength: number): { body: number; seatTop: number; springRest: number } {
  const body = SHOCK.bodyShare * restLength;
  const seatTop = SHOCK.bodyStart + SHOCK.seatAt * body + SHOCK.seatHeight;
  return { body, seatTop, springRest: restLength - SHOCK.upperSeatGap - seatTop };
}

/**
 * Coilover shock absorbers as real ones look: an eyelet with a chrome bushing on each
 * mounting bolt, a black damper body carrying a threaded spring seat, a chrome piston
 * rod into a top cap and seat, and a black coil spring between the seats that shortens
 * as the shock compresses. Instanced: the live view draws every strut of every
 * practice robot with one draw per part.
 */
export class ShockAbsorbers {
  readonly group = new THREE.Group();
  private eyes: THREE.InstancedMesh;
  private bushings: THREE.InstancedMesh;
  private bodies: THREE.InstancedMesh;
  private rods: THREE.InstancedMesh;
  private caps: THREE.InstancedMesh;
  private seats: THREE.InstancedMesh;
  private nuts: THREE.InstancedMesh;
  private springs: THREE.InstancedMesh[] = [];
  private springOf: { mesh: number; slot: number }[] = [];
  private groupSize: number[] = [];
  private layout: ReturnType<typeof shockLayout>[];
  private base = new THREE.Matrix4();
  private local = new THREE.Matrix4();
  private m = new THREE.Matrix4();
  private x = new THREE.Vector3();
  private y = new THREE.Vector3();
  private z = new THREE.Vector3();

  constructor(private restLengths: number[], robots: number) {
    const n = restLengths.length;
    this.group.name = 'shock-absorbers';
    this.layout = restLengths.map(shockLayout);
    const anodised = new THREE.MeshStandardMaterial({ color: 0x161618, roughness: 0.42, metalness: 0.55 });
    const coil = new THREE.MeshStandardMaterial({ color: 0x1d1d20, roughness: 0.34, metalness: 0.35 });
    // Brushed rather than mirror chrome: a mirror's highlight would bloom like a lamp in the hangar.
    const chrome = new THREE.MeshStandardMaterial({ color: 0xbfc1c4, roughness: 0.38, metalness: 1.0 });
    const ring = new THREE.Shape().absarc(0, 0, SHOCK.eyeOuter, 0, Math.PI * 2, false);
    ring.holes.push(new THREE.Path().absarc(0, 0, SHOCK.eyeInner, 0, Math.PI * 2, true));
    const eye = new THREE.ExtrudeGeometry(ring, { depth: SHOCK.eyeWidth, bevelEnabled: false, curveSegments: 24 })
      .translate(0, 0, -SHOCK.eyeWidth / 2)
      .rotateY(Math.PI / 2); // Its bore along the bolt (local x).
    const bushing = new THREE.CylinderGeometry(SHOCK.eyeInner, SHOCK.eyeInner, SHOCK.eyeWidth * 1.2, 16, 1, true).rotateZ(Math.PI / 2);
    const along = (geometry: THREE.CylinderGeometry, height: number) => geometry.rotateX(Math.PI / 2).translate(0, 0, height / 2);
    const make = (geometry: THREE.BufferGeometry, material: THREE.Material, per: number) => {
      const mesh = new THREE.InstancedMesh(geometry, material, Math.max(1, robots * n * per));
      mesh.castShadow = true;
      mesh.frustumCulled = false;
      mesh.count = 0;
      this.group.add(mesh);
      return mesh;
    };
    this.eyes = make(eye, anodised, 2);
    this.bushings = make(bushing, chrome, 2);
    this.bodies = make(along(new THREE.CylinderGeometry(SHOCK.bodyRadius, SHOCK.bodyRadius, 1, 28), 1), anodised, 1);
    this.rods = make(along(new THREE.CylinderGeometry(SHOCK.rodRadius, SHOCK.rodRadius, 1, 14), 1), chrome, 1);
    this.caps = make(along(new THREE.CylinderGeometry(SHOCK.capRadius * 0.9, SHOCK.capRadius, SHOCK.capHeight, 28), SHOCK.capHeight), anodised, 1);
    this.seats = make(along(new THREE.CylinderGeometry(SHOCK.seatRadius, SHOCK.seatRadius, SHOCK.seatHeight, 32), SHOCK.seatHeight), anodised, 2);
    this.nuts = make(along(new THREE.CylinderGeometry(SHOCK.seatRadius * 0.86, SHOCK.seatRadius * 0.86, SHOCK.nutHeight, 6), SHOCK.nutHeight), anodised, 1);
    // A spring is built at its rest length and only stretched by the stroke (its wire stays round).
    const byLength = new Map<number, number>();
    this.layout.forEach(({ springRest }, k) => {
      const key = Math.round(springRest * 2000);
      if (!byLength.has(key)) {
        byLength.set(key, this.springs.length);
        const helix = new THREE.TubeGeometry(new Helix(springRest), Math.round(SHOCK.turns * 28), SHOCK.wireRadius, 8, false);
        this.springs.push(make(helix, coil, 0));
        this.groupSize.push(0);
      }
      const mesh = byLength.get(key)!;
      this.springOf[k] = { mesh, slot: this.groupSize[mesh]++ };
    });
    this.springs.forEach((mesh, i) => {
      mesh.instanceMatrix = new THREE.InstancedBufferAttribute(new Float32Array(Math.max(1, robots * this.groupSize[i]) * 16), 16);
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    });
    for (const mesh of [this.eyes, this.bushings, this.bodies, this.rods, this.caps, this.seats, this.nuts]) mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  }

  /** How many robots' shock absorbers to draw. */
  setCount(robots: number): void {
    const n = this.restLengths.length;
    this.eyes.count = this.bushings.count = this.seats.count = robots * n * 2;
    this.bodies.count = this.rods.count = this.caps.count = this.nuts.count = robots * n;
    this.springs.forEach((mesh, i) => (mesh.count = robots * this.groupSize[i]));
  }

  /** Put robot ``robot``'s shock absorber ``k`` between its two eyes, its bolts along ``bolt``. */
  place(robot: number, k: number, lower: THREE.Vector3, upper: THREE.Vector3, bolt: THREE.Vector3): void {
    const n = this.restLengths.length;
    const length = this.z.subVectors(upper, lower).length();
    if (length < 1e-6) return;
    this.z.divideScalar(length);
    this.x.copy(bolt).addScaledVector(this.z, -bolt.dot(this.z));
    if (this.x.lengthSq() < 1e-10) this.x.set(1, 0, 0).addScaledVector(this.z, -this.z.x);
    this.x.normalize();
    this.y.crossVectors(this.z, this.x);
    this.base.makeBasis(this.x, this.y, this.z).setPosition(lower);
    const { body, seatTop, springRest } = this.layout[k];
    const one = robot * n + k;
    const at = (mesh: THREE.InstancedMesh, index: number, offset: number, stretch = 1) => {
      this.local.makeScale(1, 1, stretch).setPosition(0, 0, offset);
      mesh.setMatrixAt(index, this.m.multiplyMatrices(this.base, this.local));
    };
    at(this.eyes, one * 2, 0);
    at(this.eyes, one * 2 + 1, length);
    at(this.bushings, one * 2, 0);
    at(this.bushings, one * 2 + 1, length);
    at(this.bodies, one, SHOCK.bodyStart, body);
    const rodFrom = SHOCK.bodyStart + body;
    at(this.rods, one, rodFrom, Math.max(0.001, length - SHOCK.capGap - rodFrom));
    at(this.caps, one, length - SHOCK.capGap);
    at(this.seats, one * 2, seatTop - SHOCK.seatHeight);
    at(this.nuts, one, seatTop - SHOCK.seatHeight - SHOCK.nutHeight - 0.0006);
    at(this.seats, one * 2 + 1, length - SHOCK.upperSeatGap);
    const spring = this.springOf[k];
    const span = length - SHOCK.upperSeatGap - seatTop;
    at(this.springs[spring.mesh], robot * this.groupSize[spring.mesh] + spring.slot, seatTop, Math.max(0.05, span / springRest));
  }

  /** After placing: send the new places to the GPU. */
  commit(): void {
    for (const mesh of [this.eyes, this.bushings, this.bodies, this.rods, this.caps, this.seats, this.nuts, ...this.springs]) mesh.instanceMatrix.needsUpdate = true;
  }
}

/** The axis of a shock absorber's mounting bolts: the ankle joint it spans, else the robot's left. */
export function shockBolt(meta: Meta, kin: Kinematics, strut: Meta['struts'][number], out = new THREE.Vector3()): THREE.Vector3 {
  const link = meta.bodies[strut.lower.body].link;
  const joint = meta.joints.find((j) => j.body === link && j.type === 3);
  if (joint) return out.set(kin.axes[joint.id * 3], kin.axes[joint.id * 3 + 1], kin.axes[joint.id * 3 + 2]);
  const left = new Float64Array(3);
  rotate(left, 0, kin.xquat, 4, meta.info.left[0], meta.info.left[1], meta.info.left[2]);
  return out.set(left[0], left[1], left[2]);
}
