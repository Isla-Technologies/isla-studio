// The anatomy view: the robot from its own model as a glowing white wireframe,
// every motor a point the interface can mark, select and label.
//
// Feature edges of every part draw the robot; dark fills behind them dim the
// edges at the back so the front reads first. Bloom makes the lines glow. Motor
// points and their labels are an HTML layer the component draws over this
// canvas from the screen positions ``project`` gives it.

import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import { designOffsets, Kinematics, rodExtension } from '../lib/kinematics';
import { GEOM, threeQuat, type RobotAssets } from '../lib/robot';
import type { Meta } from '../lib/types';
import { edgesFor, solidFor } from './parts';
import { PALETTES, type Theme } from './theme';

interface Part {
  body: number;
  link: number;
  lines: THREE.LineSegments;
  fill: THREE.Mesh | null;
  local: THREE.Matrix4;
}

export interface ScreenPoint {
  x: number;
  y: number;
  depth: number;
  visible: boolean;
}

export class AnatomyView {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera: THREE.PerspectiveCamera;
  readonly controls: OrbitControls;
  readonly kin: Kinematics;
  private composer: EffectComposer;
  private bloom: UnrealBloomPass;
  private parts: Part[] = [];
  private rodParts: { body: number; link: number; rod: string; top: THREE.Vector3; bottom: THREE.Vector3; dir: THREE.Vector3; lines: THREE.LineSegments }[] = [];
  private springs: THREE.Line[] = [];
  private virtualBox: THREE.LineSegments | null = null;
  private lineMats = new Map<number, THREE.LineBasicMaterial>(); // per link
  private fillMat = new THREE.MeshBasicMaterial({ color: 0x070707, transparent: true, opacity: 0.86, depthWrite: true, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
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
  /** Line colour and accent of the current theme. */
  private ink = new THREE.Color(PALETTES.dark.ink);
  private accent = new THREE.Color(PALETTES.dark.accent);
  private floorLines: { material: THREE.LineBasicMaterial; tick: boolean }[] = [];

  constructor(private canvas: HTMLCanvasElement, assets: RobotAssets) {
    this.meta = assets.meta;
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
    // A dense CAD robot (Luma-1 draws some 60,000 edges) would saturate into a white
    // mass at full strength: the more lines, the fainter each, so it still reads as a wireframe.
    let segments = 0;
    for (const geom of meta.geoms) segments += (edgesFor(geom, assets)?.getAttribute('position')?.count ?? 0) / 2;
    this.baseOpacity = Math.min(0.78, Math.max(0.15, 0.78 * Math.sqrt(4000 / Math.max(segments, 1))));
    this.lineOpacity = this.baseOpacity;
    const rodBodies = new Set(meta.rods.flatMap((r) => r.rods.map((p) => p.body)));
    for (const geom of meta.geoms) {
      const body = meta.bodies[geom.body];
      if (body.kind === 'virtual') continue;
      if (rodBodies.has(geom.body) && geom.type === GEOM.CYLINDER) continue;
      const edgeGeometry = edgesFor(geom, assets);
      if (!edgeGeometry) continue;
      const solid = solidFor(geom, assets);
      const lines = new THREE.LineSegments(edgeGeometry, this.material(body.link));
      lines.matrixAutoUpdate = false;
      lines.userData.body = geom.body;
      this.scene.add(lines);
      let fill: THREE.Mesh | null = null;
      if (solid) {
        fill = new THREE.Mesh(solid, this.fillMat);
        fill.matrixAutoUpdate = false;
        fill.userData.body = geom.body;
        this.scene.add(fill);
      }
      const local = new THREE.Matrix4().compose(new THREE.Vector3(...geom.pos), threeQuat(geom.quat), new THREE.Vector3(1, 1, 1));
      this.parts.push({ body: geom.body, link: body.link, lines, fill, local });
    }
    // Carbon rods: a tube of eight lines and two rings, stretched by the design.
    const tube = new THREE.EdgesGeometry(new THREE.CylinderGeometry(1, 1, 1, 8, 1, true).rotateX(Math.PI / 2).translate(0, 0, 0.5), 1);
    for (const rod of meta.rods) {
      for (const piece of rod.rods) {
        const link = meta.bodies[piece.body].link;
        const lines = new THREE.LineSegments(tube, this.material(link));
        lines.matrixAutoUpdate = false;
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
    // Shock absorbers: a coil between their two mounts.
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
      const line = new THREE.Line(coilGeometry, new THREE.LineBasicMaterial({ color: this.accent.clone(), transparent: true, opacity: 0.95 }));
      line.matrixAutoUpdate = false;
      this.scene.add(line);
      this.springs.push(line);
    }
    // The virtual mass, as a dashed box where it sits.
    const virtual = meta.bodies.find((b) => b.kind === 'virtual');
    if (virtual) {
      const box = new THREE.EdgesGeometry(new THREE.BoxGeometry(0.2, 0.12, 0.2));
      const dashed = new THREE.LineSegments(box, new THREE.LineDashedMaterial({ color: 0xff2b2b, dashSize: 0.012, gapSize: 0.01, transparent: true, opacity: 0.75 }));
      dashed.computeLineDistances();
      dashed.matrixAutoUpdate = false;
      dashed.userData.body = virtual.id;
      this.scene.add(dashed);
      this.virtualBox = dashed;
    }
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
      p.lines.matrix.copy(this.m4);
      p.lines.matrixWorldNeedsUpdate = true;
      if (p.fill) {
        p.fill.matrix.copy(this.m4);
        p.fill.matrixWorldNeedsUpdate = true;
      }
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
      const load = strutLoad?.[k] ?? 0;
      (line.material as THREE.LineBasicMaterial).opacity = 0.55 + 0.45 * Math.min(1, Math.abs(load));
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

  /** Mark links: selected ones red, faulted ones red too (their points blink). */
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

  pick(clientX: number, clientY: number): number | null {
    const rect = this.canvas.getBoundingClientRect();
    const ndc = new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    const caster = new THREE.Raycaster();
    caster.setFromCamera(ndc, this.camera);
    const fills = this.parts.map((p) => p.fill).filter((f): f is THREE.Mesh => !!f);
    const hit = caster.intersectObjects(fills, false)[0];
    return hit ? (hit.object.userData.body as number) : null;
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
    this.renderer.setClearColor(p.background, 1);
    (this.scene.background as THREE.Color).set(p.background);
    this.fillMat.color.set(p.fill);
    this.fillMat.opacity = p.fillOpacity;
    this.lineOpacity = Math.min(0.85, this.baseOpacity * p.lineGain);
    for (const line of this.springs) (line.material as THREE.LineBasicMaterial).color.set(p.accent);
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
    this.composer.dispose();
    this.renderer.dispose();
  }

  get marked(): { hot: Set<number>; faulty: Set<number> } {
    return { hot: this.hot, faulty: this.faulty };
  }
}
