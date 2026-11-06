// The anatomy view: the robot from its own model as an X-ray of the machine. The
// shell is dark glass drawn by its edges in white; inside it every actuator is a
// solid red part with crisp red edges, seen through the glass, lit harder the harder
// its motor works. The interface selects, labels and marks each one.
//
// One scene and one depth buffer make the X-ray, by drawing in this order:
//   1. the actuators (and shock absorbers): shaded solids, then their edges;
//   2. the shell's depth, its nearest surface only, skipped where an actuator is in front;
//   3. the shell's glass on that nearest surface, which dims the actuators behind it;
//   4. the shell's edges on its near side, over any actuator they pass in front of.
// Bloom gives the white lines their glow; the red parts stay crisp. Labels are an HTML
// layer the component draws over this canvas from ``projectJoints``.

import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import { designOffsets, Kinematics, rodExtension } from '../lib/kinematics';
import { GEOM, threeQuat, type RobotAssets } from '../lib/robot';
import type { Meta } from '../lib/types';
import { findActuators, type ActuatorFit } from './actuators';
import { edgesFor, solidFor } from './parts';
import { PALETTES, type Theme } from './theme';

/** The draw order above. */
const ORDER = { actuator: 1, actuatorEdges: 2, shellDepth: 3, glass: 4, shellEdges: 5 } as const;

interface Part {
  body: number;
  link: number;
  lines: THREE.LineSegments | null;
  /** The shell's glass (also what a click on a part hits) and its depth-only twin. */
  fill: THREE.Mesh | null;
  depth: THREE.Mesh | null;
  local: THREE.Matrix4;
}

/** An actuator: its solid, its edges and how hard it works. */
interface Actuator {
  fit: ActuatorFit;
  group: THREE.Group;
  /** Its solids: what a click hits. */
  solids: THREE.Mesh[];
  surface: THREE.ShaderMaterial;
  edges: THREE.LineBasicMaterial;
  /** Share of peak torque, eased over a few frames (0 to 1, on a square-root scale). */
  load: number;
}

/** What an actuator shows: how hard it works, the motor's state, and whether it is selected or pointed at. */
export interface ActuatorLevel {
  load: number;
  mode: 'ok' | 'armed' | 'off' | 'fault';
  lit: boolean;
}

export interface ScreenPoint {
  x: number;
  y: number;
  depth: number;
  visible: boolean;
  /** The actuator's radius on screen, in CSS pixels; 0 for a joint without one. */
  size?: number;
}

const VIEW_VERTEX = /* glsl */ `
varying vec3 vNormal;
varying vec3 vView;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vView = -mv.xyz;
  vNormal = normalMatrix * normal;
  gl_Position = projectionMatrix * mv;
}`;

// An actuator: a solid lit from the upper left of the view, with a rim of light at its silhouette.
const ACTUATOR_FRAGMENT = /* glsl */ `
uniform vec3 shade;
uniform vec3 lit;
uniform vec3 rimColor;
uniform float rim;
varying vec3 vNormal;
varying vec3 vView;
void main() {
  vec3 n = normalize(vNormal) * (gl_FrontFacing ? 1.0 : -1.0);
  vec3 v = normalize(vView);
  float key = max(dot(n, normalize(vec3(-0.35, 0.55, 0.75))), 0.0);
  float edge = pow(1.0 - abs(dot(n, v)), 2.0);
  gl_FragColor = vec4(mix(shade, lit, key) + rimColor * rim * edge, 1.0);
}`;

// The shell's glass: a tint over what is behind it, with a sheen where it turns away from you.
const GLASS_FRAGMENT = /* glsl */ `
uniform vec3 color;
uniform float opacity;
uniform vec3 sheenColor;
uniform float sheen;
varying vec3 vNormal;
varying vec3 vView;
void main() {
  float s = sheen * pow(1.0 - abs(dot(normalize(vNormal), normalize(vView))), 3.0);
  gl_FragColor = vec4(mix(color, sheenColor, s), opacity + (1.0 - opacity) * s);
}`;

/** A near-white the light theme's actuators are tinted from. */
const PAPER = new THREE.Color(0xf6f6f4);

export class AnatomyView {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera: THREE.PerspectiveCamera;
  readonly controls: OrbitControls;
  readonly kin: Kinematics;
  private composer: EffectComposer;
  private bloom: UnrealBloomPass;
  private sharedGeometries: Set<THREE.BufferGeometry>;
  private parts: Part[] = [];
  private actuators: Actuator[] = [];
  private actuatorOf = new Map<number, Actuator>(); // by joint
  private rodParts: { body: number; link: number; rod: string; top: THREE.Vector3; bottom: THREE.Vector3; dir: THREE.Vector3; lines: THREE.LineSegments }[] = [];
  private springs: THREE.Line[] = [];
  private virtualBox: THREE.LineSegments | null = null;
  private lineMats = new Map<number, THREE.LineBasicMaterial>(); // per link
  private depthMat = new THREE.MeshBasicMaterial({ colorWrite: false, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 2, polygonOffsetUnits: 2 });
  private glassMat = new THREE.ShaderMaterial({
    uniforms: { color: { value: new THREE.Color() }, opacity: { value: 0.3 }, sheenColor: { value: new THREE.Color() }, sheen: { value: 0.2 } },
    vertexShader: VIEW_VERTEX,
    fragmentShader: GLASS_FRAGMENT,
    side: THREE.DoubleSide,
    transparent: true,
    depthWrite: false,
    // A little nearer than the depth pass, so the nearest surface always passes and those behind it never do.
    polygonOffset: true,
    polygonOffsetFactor: 1,
    polygonOffsetUnits: 1,
  });
  private hot = new Set<number>();
  private lineOpacity = 0.78;
  /** The opacity the line count allows, before the theme's gain. */
  private baseOpacity = 0.78;
  private faulty = new Set<number>();
  private resizeObserver: ResizeObserver;
  private m4 = new THREE.Matrix4();
  private m4b = new THREE.Matrix4();
  private v = new THREE.Vector3();
  private q = new THREE.Quaternion();
  private one = new THREE.Vector3(1, 1, 1);
  private tmp = new Float64Array(3);
  private tmp2 = new Float64Array(3);
  private meta: Meta;
  private center = new THREE.Vector3(0, 0, 0.5);
  private framed = false;
  /** Line colour and accent of the current theme, and whether it is the dark one. */
  private ink = new THREE.Color(PALETTES.dark.ink);
  private accent = new THREE.Color(PALETTES.dark.accent);
  private dark = true;
  private floorLines: { material: THREE.LineBasicMaterial; tick: boolean }[] = [];

  constructor(private canvas: HTMLCanvasElement, assets: RobotAssets) {
    this.meta = assets.meta;
    this.sharedGeometries = new Set([...assets.solids, ...assets.edges]);
    this.kin = new Kinematics(assets.meta);
    THREE.Object3D.DEFAULT_UP.set(0, 0, 1);
    const r = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    r.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    r.outputColorSpace = THREE.SRGBColorSpace;
    r.setClearColor(0x050505, 1);
    this.renderer = r;
    this.scene.background = new THREE.Color(0x050505);
    this.camera = new THREE.PerspectiveCamera(26, 1, 0.05, 40);
    this.camera.up.set(0, 0, 1);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;
    this.controls.enablePan = false;
    this.controls.minDistance = 0.8;
    this.controls.maxDistance = 8;
    this.composer = new EffectComposer(r);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.4, 0.3, 0.72);
    this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());
    this.build(assets);
    this.buildFloor();
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(canvas);
    this.resize();
  }

  private material(link: number): THREE.LineBasicMaterial {
    let m = this.lineMats.get(link);
    if (!m) {
      m = new THREE.LineBasicMaterial({ color: this.ink.clone(), transparent: true, opacity: this.lineOpacity });
      this.lineMats.set(link, m);
    }
    return m;
  }

  private build(assets: RobotAssets): void {
    const meta = this.meta;
    const fits = findActuators(meta, assets);
    const actuatorGeoms = new Set(fits.flatMap((f) => f.geoms.map((g) => g.id)));
    // A dense CAD robot (Luma-1 draws some 60,000 edges) would saturate into a white
    // mass at full strength: the more lines, the fainter each, so it still reads as a wireframe.
    let segments = 0;
    for (const geom of meta.geoms) if (!actuatorGeoms.has(geom.id)) segments += (edgesFor(geom, assets)?.getAttribute('position')?.count ?? 0) / 2;
    this.baseOpacity = Math.min(0.78, Math.max(0.15, 0.78 * Math.sqrt(4000 / Math.max(segments, 1))));
    this.lineOpacity = this.baseOpacity;
    const rodBodies = new Set(meta.rods.flatMap((r) => r.rods.map((p) => p.body)));
    for (const geom of meta.geoms) {
      const body = meta.bodies[geom.body];
      if (body.kind === 'virtual') continue;
      if (rodBodies.has(geom.body) && geom.type === GEOM.CYLINDER) continue;
      if (actuatorGeoms.has(geom.id)) continue; // Drawn as an actuator, below.
      const edgeGeometry = edgesFor(geom, assets);
      if (!edgeGeometry) continue;
      const solid = solidFor(geom, assets);
      const lines = new THREE.LineSegments(edgeGeometry, this.material(body.link));
      lines.matrixAutoUpdate = false;
      lines.renderOrder = ORDER.shellEdges;
      lines.userData.body = geom.body;
      this.scene.add(lines);
      let fill: THREE.Mesh | null = null;
      let depth: THREE.Mesh | null = null;
      if (solid) {
        depth = new THREE.Mesh(solid, this.depthMat);
        depth.matrixAutoUpdate = false;
        depth.renderOrder = ORDER.shellDepth;
        this.scene.add(depth);
        fill = new THREE.Mesh(solid, this.glassMat);
        fill.matrixAutoUpdate = false;
        fill.renderOrder = ORDER.glass;
        fill.userData.body = geom.body;
        this.scene.add(fill);
      }
      const local = new THREE.Matrix4().compose(new THREE.Vector3(...geom.pos), threeQuat(geom.quat), new THREE.Vector3(1, 1, 1));
      this.parts.push({ body: geom.body, link: body.link, lines, fill, depth, local });
    }
    // Carbon rods: a tube of eight lines and two rings, stretched by the design.
    const tube = new THREE.EdgesGeometry(new THREE.CylinderGeometry(1, 1, 1, 8, 1, true).rotateX(Math.PI / 2).translate(0, 0, 0.5), 1);
    for (const rod of meta.rods) {
      for (const piece of rod.rods) {
        const link = meta.bodies[piece.body].link;
        const lines = new THREE.LineSegments(tube, this.material(link));
        lines.matrixAutoUpdate = false;
        lines.renderOrder = ORDER.shellEdges;
        lines.userData.body = piece.body;
        lines.userData.radius = piece.radius;
        this.scene.add(lines);
        this.rodParts.push({
          body: piece.body,
          link,
          rod: rod.name,
          top: new THREE.Vector3(...piece.top),
          bottom: new THREE.Vector3(...piece.bottom),
          dir: new THREE.Vector3(...piece.direction),
          lines,
        });
      }
    }
    // Shock absorbers: a red coil between their two mounts, drawn with the actuators so they show through the shell.
    const turns = 9, steps = turns * 16;
    const coil: THREE.Vector3[] = [];
    for (let i = 0; i <= steps; i++) {
      const t = i / steps, a = t * turns * Math.PI * 2;
      coil.push(new THREE.Vector3(Math.cos(a) * 0.011, Math.sin(a) * 0.011, 0.12 + t * 0.76));
    }
    coil.unshift(new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 0, 0.12));
    coil.push(new THREE.Vector3(0, 0, 0.88), new THREE.Vector3(0, 0, 1));
    const coilGeometry = new THREE.BufferGeometry().setFromPoints(coil);
    for (const _strut of meta.struts) {
      const line = new THREE.Line(coilGeometry, new THREE.LineBasicMaterial({ color: this.accent.clone() }));
      line.matrixAutoUpdate = false;
      line.renderOrder = ORDER.actuatorEdges;
      this.scene.add(line);
      this.springs.push(line);
    }
    // The virtual mass, as a dashed box where it sits inside the shell.
    const virtual = meta.bodies.find((b) => b.kind === 'virtual');
    if (virtual) {
      const box = new THREE.EdgesGeometry(new THREE.BoxGeometry(0.2, 0.12, 0.2));
      const dashed = new THREE.LineSegments(box, new THREE.LineDashedMaterial({ color: this.accent.clone(), dashSize: 0.012, gapSize: 0.01 }));
      dashed.computeLineDistances();
      dashed.matrixAutoUpdate = false;
      dashed.renderOrder = ORDER.actuatorEdges;
      dashed.userData.body = virtual.id;
      this.scene.add(dashed);
      this.virtualBox = dashed;
    }
    for (const fit of fits) this.addActuator(fit, assets);
  }

  /** An actuator: its own part from the model, or a drawn motor where it has none. */
  private addActuator(fit: ActuatorFit, assets: RobotAssets): void {
    const group = new THREE.Group();
    group.matrixAutoUpdate = false;
    const surface = new THREE.ShaderMaterial({
      uniforms: { shade: { value: new THREE.Color() }, lit: { value: new THREE.Color() }, rimColor: { value: new THREE.Color() }, rim: { value: 0 } },
      vertexShader: VIEW_VERTEX,
      fragmentShader: ACTUATOR_FRAGMENT,
      side: THREE.DoubleSide,
      // Behind its own edges, so they draw over it.
      polygonOffset: true,
      polygonOffsetFactor: 1,
      polygonOffsetUnits: 1,
    });
    const edges = new THREE.LineBasicMaterial({ color: this.accent.clone() });
    const solids: THREE.Mesh[] = [];
    const add = (solid: THREE.BufferGeometry, lines: THREE.BufferGeometry, place: (o: THREE.Object3D) => void) => {
      const body = new THREE.Mesh(solid, surface);
      body.renderOrder = ORDER.actuator;
      body.userData.joint = fit.joint.id;
      const outline = new THREE.LineSegments(lines, edges);
      outline.renderOrder = ORDER.actuatorEdges;
      for (const o of [body, outline]) {
        place(o);
        group.add(o);
      }
      solids.push(body);
    };
    if (fit.geoms.length) {
      for (const geom of fit.geoms) {
        const solid = solidFor(geom, assets), lines = edgesFor(geom, assets);
        if (!solid || !lines) continue;
        add(solid, lines, (o) => {
          o.position.fromArray(geom.pos);
          threeQuat(geom.quat, 0, o.quaternion);
        });
      }
    } else {
      // A drawn motor: a can with its stator ring, along the joint's axis.
      const { radius: r, width: w } = fit;
      const solid = new THREE.CylinderGeometry(r, r, w, 36);
      const points: number[] = [];
      const ring = (radius: number, y: number, steps = 36) => {
        for (let i = 0; i < steps; i++) {
          const a0 = (i / steps) * Math.PI * 2, a1 = ((i + 1) / steps) * Math.PI * 2;
          points.push(radius * Math.cos(a0), y, radius * Math.sin(a0), radius * Math.cos(a1), y, radius * Math.sin(a1));
        }
      };
      ring(r, w / 2);
      ring(r, -w / 2);
      ring(0.55 * r, w / 2);
      ring(0.55 * r, -w / 2);
      for (let i = 0; i < 12; i++) {
        const a = (i / 12) * Math.PI * 2;
        points.push(r * Math.cos(a), -w / 2, r * Math.sin(a), r * Math.cos(a), w / 2, r * Math.sin(a));
      }
      const lines = new THREE.BufferGeometry();
      lines.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
      const turn = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), fit.axis);
      add(solid, lines, (o) => {
        o.position.copy(fit.center);
        o.quaternion.copy(turn);
      });
    }
    this.scene.add(group);
    const actuator: Actuator = { fit, group, solids, surface, edges, load: 0 };
    this.actuators.push(actuator);
    this.actuatorOf.set(fit.joint.id, actuator);
  }

  private buildFloor(): void {
    const ring = (radius: number, opacity: number) => {
      const points: THREE.Vector3[] = [];
      for (let i = 0; i <= 96; i++) {
        const a = (i / 96) * Math.PI * 2;
        points.push(new THREE.Vector3(Math.cos(a) * radius, Math.sin(a) * radius, 0));
      }
      const material = new THREE.LineBasicMaterial({ color: 0x3a3a3a, transparent: true, opacity });
      this.floorLines.push({ material, tick: false });
      return new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), material);
    };
    const floor = new THREE.Group();
    floor.add(ring(0.35, 0.8), ring(0.6, 0.45), ring(0.9, 0.2));
    const ticks: THREE.Vector3[] = [];
    for (let i = 0; i < 36; i++) {
      const a = (i / 36) * Math.PI * 2;
      const r0 = i % 9 === 0 ? 0.3 : 0.33;
      ticks.push(new THREE.Vector3(Math.cos(a) * r0, Math.sin(a) * r0, 0), new THREE.Vector3(Math.cos(a) * 0.35, Math.sin(a) * 0.35, 0));
    }
    const tickMaterial = new THREE.LineBasicMaterial({ color: 0x5c5c5c });
    this.floorLines.push({ material: tickMaterial, tick: true });
    floor.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(ticks), tickMaterial));
    floor.userData.floor = true;
    this.scene.add(floor);
    this.floor = floor;
  }

  private floor: THREE.Group | null = null;

  /** Pose the robot. */
  update(qpos: ArrayLike<number>, design: Record<string, number>, strutLoad?: number[]): void {
    const kin = this.kin;
    kin.compute(qpos, designOffsets(this.meta, design));
    for (const p of this.parts) {
      const b = p.body;
      this.v.set(kin.xpos[b * 3], kin.xpos[b * 3 + 1], kin.xpos[b * 3 + 2]);
      threeQuat(kin.xquat, b * 4, this.q);
      this.m4.compose(this.v, this.q, this.one).multiply(p.local);
      for (const o of [p.lines, p.fill, p.depth]) {
        if (!o) continue;
        o.matrix.copy(this.m4);
        o.matrixWorldNeedsUpdate = true;
      }
    }
    for (const a of this.actuators) {
      const b = a.fit.body;
      this.v.set(kin.xpos[b * 3], kin.xpos[b * 3 + 1], kin.xpos[b * 3 + 2]);
      threeQuat(kin.xquat, b * 4, this.q);
      a.group.matrix.compose(this.v, this.q, this.one);
      a.group.matrixWorldNeedsUpdate = true;
    }
    for (const r of this.rodParts) {
      const ext = rodExtension(this.meta, design, r.rod);
      const bottom = r.bottom.clone().addScaledVector(r.dir, ext);
      const span = bottom.clone().sub(r.top);
      const radius = r.lines.userData.radius as number;
      this.m4b.compose(r.top, new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), span.clone().normalize()), new THREE.Vector3(radius, radius, span.length()));
      this.v.set(kin.xpos[r.body * 3], kin.xpos[r.body * 3 + 1], kin.xpos[r.body * 3 + 2]);
      threeQuat(kin.xquat, r.body * 4, this.q);
      this.m4.compose(this.v, this.q, this.one).multiply(this.m4b);
      r.lines.matrix.copy(this.m4);
      r.lines.matrixWorldNeedsUpdate = true;
    }
    this.meta.struts.forEach((strut, k) => {
      const a = kin.point(strut.lower.body, strut.lower.pos, this.tmp);
      const b = kin.point(strut.upper.body, strut.upper.pos, this.tmp2);
      const from = new THREE.Vector3(a[0], a[1], a[2]);
      const span = new THREE.Vector3(b[0], b[1], b[2]).sub(from);
      const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), span.clone().normalize());
      const line = this.springs[k];
      line.matrix.compose(from, q, new THREE.Vector3(1, 1, span.length()));
      line.matrixWorldNeedsUpdate = true;
      // A shock absorber near the end of its stroke burns brighter.
      const load = Math.min(1, Math.abs(strutLoad?.[k] ?? 0));
      (line.material as THREE.LineBasicMaterial).color.copy(this.accent).multiplyScalar(this.dark ? 0.55 + 0.45 * load : 1);
    });
    if (this.virtualBox) {
      const b = this.virtualBox.userData.body as number;
      this.v.set(kin.xpos[b * 3], kin.xpos[b * 3 + 1], kin.xpos[b * 3 + 2]);
      threeQuat(kin.xquat, b * 4, this.q);
      this.virtualBox.matrix.compose(this.v, this.q, this.one);
      this.virtualBox.matrixWorldNeedsUpdate = true;
    }
    // Keep the robot centred: the floor rings sit under its base.
    const bx = kin.xpos[3], by = kin.xpos[4];
    if (this.floor) this.floor.position.set(bx, by, 0);
    const hip = this.meta.info.hip_height || 0.4;
    this.center.set(bx, by, Math.max(0.35, hip + 0.05));
    if (!this.framed) {
      this.home();
      this.framed = true;
    } else {
      const delta = this.center.clone().sub(this.controls.target);
      delta.z = 0;
      if (delta.length() > 1e-4) {
        this.camera.position.add(delta);
        this.controls.target.add(delta);
      }
    }
  }

  /** Mark links: selected ones red, faulted ones red too (their actuators blink). */
  mark(selected: number[], faults: number[]): void {
    const next = new Set(selected);
    const bad = new Set(faults);
    for (const [link, m] of this.lineMats) {
      const on = next.has(link) || bad.has(link);
      m.color.copy(on ? this.accent : this.ink);
      m.opacity = on ? 1 : this.lineOpacity;
    }
    this.hot = next;
    this.faulty = bad;
  }

  /**
   * Light the actuators for this frame: brighter the more of its peak torque a motor
   * gives, and brighter still when selected or pointed at. A motor that does not
   * reply goes grey; a faulted one blinks; an armed one breathes.
   */
  drive(level: (joint: number) => ActuatorLevel | undefined, now: number): void {
    const c = new THREE.Color();
    for (const a of this.actuators) {
      const l = level(a.fit.joint.id);
      const mode = l?.mode ?? 'ok';
      // Walking asks a motor for a tenth to a third of its peak: the square root spreads that range so it shows.
      const target = mode === 'off' ? 0 : Math.sqrt(Math.min(1, Math.max(0, l?.load ?? 0)));
      a.load += (target - a.load) * 0.3;
      const lit = l?.lit ? 1 : 0;
      const gain = mode === 'fault' ? (Math.floor(now / 250) % 2 ? 1.2 : 0.35) : mode === 'armed' ? 1 + 0.1 * Math.sin((now / 1600) * Math.PI * 2) : 1;
      const color = mode === 'off' ? this.ink : this.accent;
      const u = a.surface.uniforms;
      const shade = u.shade.value as THREE.Color, bright = u.lit.value as THREE.Color;
      (u.rimColor.value as THREE.Color).copy(color);
      if (this.dark) {
        // A dark red solid, lit from one side, its edges in red: brighter as it works or when chosen.
        const k = (mode === 'off' ? 0.35 : 0.8 + 0.9 * a.load + 0.5 * lit) * gain;
        shade.copy(color).multiplyScalar(0.02 * k);
        bright.copy(color).multiplyScalar(0.17 * k);
        u.rim.value = 0.22 * k;
        a.edges.color.copy(color).multiplyScalar(Math.min(1, (mode === 'off' ? 0.4 : 0.85 + 0.25 * a.load + 0.3 * lit) * gain));
      } else {
        // By day, a part tinted red from paper white, deeper as it works or when chosen.
        const tint = Math.min(1, (mode === 'off' ? 0.1 : 0.2 + 0.35 * a.load + 0.25 * lit) * gain);
        bright.copy(PAPER).lerp(color, tint);
        shade.copy(PAPER).lerp(color, Math.min(1, tint + 0.25)).multiplyScalar(0.86);
        u.rim.value = 0;
        a.edges.color.copy(c.copy(PAPER).lerp(color, Math.min(1, (mode === 'off' ? 0.45 : 0.85 + 0.15 * lit) * gain)));
      }
    }
  }

  /** Where world points fall on this canvas, in CSS pixels. */
  project(points: Float64Array, count: number, out: ScreenPoint[]): void {
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    for (let i = 0; i < count; i++) {
      this.v.set(points[i * 3], points[i * 3 + 1], points[i * 3 + 2]).project(this.camera);
      const p = out[i] ?? (out[i] = { x: 0, y: 0, depth: 0, visible: false });
      p.x = (this.v.x * 0.5 + 0.5) * w;
      p.y = (-this.v.y * 0.5 + 0.5) * h;
      p.depth = this.v.z;
      p.visible = this.v.z < 1 && p.x > -20 && p.x < w + 20 && p.y > -20 && p.y < h + 20;
    }
  }

  /** Where joints fall on this canvas: each motor's actuator (centre and size), or a joint's anchor if it has none. Call after ``update``. */
  projectJoints(joints: number[], out: ScreenPoint[]): void {
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    const right = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 0);
    const edge = new THREE.Vector3();
    joints.forEach((joint, i) => {
      const a = this.actuatorOf.get(joint);
      if (a) this.v.copy(a.fit.center).applyMatrix4(a.group.matrix);
      else this.v.fromArray(this.kin.anchors, joint * 3);
      edge.copy(this.v).addScaledVector(right, a?.fit.radius ?? 0).project(this.camera);
      this.v.project(this.camera);
      const p = out[i] ?? (out[i] = { x: 0, y: 0, depth: 0, visible: false });
      p.x = (this.v.x * 0.5 + 0.5) * w;
      p.y = (-this.v.y * 0.5 + 0.5) * h;
      p.depth = this.v.z;
      p.visible = this.v.z < 1 && p.x > -20 && p.x < w + 20 && p.y > -20 && p.y < h + 20;
      p.size = a ? Math.abs(edge.x - this.v.x) * 0.5 * w : 0;
    });
  }

  /** The joint whose actuator is under this point, if any. */
  pickActuator(clientX: number, clientY: number): number | null {
    this.scene.updateMatrixWorld();
    const hit = this.caster(clientX, clientY).intersectObjects(
      this.actuators.flatMap((a) => a.solids),
      false,
    )[0];
    return hit ? (hit.object.userData.joint as number) : null;
  }

  pick(clientX: number, clientY: number): number | null {
    const fills = this.parts.map((p) => p.fill).filter((f): f is THREE.Mesh => !!f);
    const hit = this.caster(clientX, clientY).intersectObjects(fills, false)[0];
    return hit ? (hit.object.userData.body as number) : null;
  }

  private caster(clientX: number, clientY: number): THREE.Raycaster {
    const rect = this.canvas.getBoundingClientRect();
    const ndc = new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    const caster = new THREE.Raycaster();
    caster.setFromCamera(ndc, this.camera);
    return caster;
  }

  home(view: 'front' | 'side' | 'three' = 'three'): void {
    const dirs = { front: new THREE.Vector3(0, -1, 0.12), side: new THREE.Vector3(1, 0, 0.12), three: new THREE.Vector3(0.55, -0.78, 0.22) };
    // The model faces its forward axis: turn the view so "front" looks at its face.
    const f = this.meta.info.forward;
    const yaw = Math.atan2(f[1], f[0]) - Math.atan2(-1, 0);
    const d = dirs[view].clone().applyAxisAngle(new THREE.Vector3(0, 0, 1), yaw).normalize();
    const height = (this.meta.info.height || 1) * 1.0;
    const distance = height * 3.1;
    this.controls.target.copy(this.center);
    this.camera.position.copy(this.center).addScaledVector(d, distance);
    this.controls.update();
  }

  /** Recolour for the dark or the light theme: ink lines on a pale ground by day, no glow. */
  setTheme(theme: Theme): void {
    const p = PALETTES[theme];
    this.ink.set(p.ink);
    this.accent.set(p.accent);
    this.dark = theme === 'dark';
    this.renderer.setClearColor(p.background, 1);
    (this.scene.background as THREE.Color).set(p.background);
    const glass = this.glassMat.uniforms;
    (glass.color.value as THREE.Color).set(p.fill);
    glass.opacity.value = p.fillOpacity;
    (glass.sheenColor.value as THREE.Color).set(p.ink);
    glass.sheen.value = p.sheen;
    this.lineOpacity = Math.min(0.85, this.baseOpacity * p.lineGain);
    if (this.virtualBox) (this.virtualBox.material as THREE.LineDashedMaterial).color.set(p.accent);
    for (const f of this.floorLines) f.material.color.set(f.tick ? p.tick : p.ring);
    this.bloom.strength = p.bloom;
    this.bloom.enabled = p.bloom > 0;
    this.mark([...this.hot], [...this.faulty]);
  }

  render(): void {
    this.controls.update();
    this.composer.render();
  }

  private resize(): void {
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.composer.setSize(w, h);
    this.bloom.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  dispose(): void {
    this.resizeObserver.disconnect();
    this.controls.dispose();
    const geometries = new Set<THREE.BufferGeometry>();
    const materials = new Set<THREE.Material>([this.depthMat, this.glassMat]);
    this.scene.traverse((object) => {
      if (!(object instanceof THREE.Mesh || object instanceof THREE.Line)) return;
      if (!this.sharedGeometries.has(object.geometry)) geometries.add(object.geometry);
      for (const material of Array.isArray(object.material) ? object.material : [object.material]) materials.add(material);
    });
    // Model meshes belong to the asset cache and are also used by the other views.
    for (const geometry of geometries) geometry.dispose();
    for (const material of materials) material.dispose();
    for (const pass of this.composer.passes) pass.dispose();
    this.composer.dispose();
    this.renderer.dispose();
  }

  get marked(): { hot: Set<number>; faulty: Set<number> } {
    return { hot: this.hot, faulty: this.faulty };
  }
}
