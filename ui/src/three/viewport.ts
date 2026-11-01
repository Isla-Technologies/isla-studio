// The main view: the robot, live, on a perspective grid floor.
//
// Every visual geom of the model is one InstancedMesh, so the same code draws one
// robot (the live simulation) or sixteen (practice robots from a training run,
// each with its own rod lengths). Bodies are posed in the browser from joint
// positions (kinematics.ts); the server streams nothing else.

import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { designOffsets, Kinematics, rotate } from '../lib/kinematics';
import { GEOM, threeQuat, type RobotAssets } from '../lib/robot';
import type { GeomMeta, Meta } from '../lib/types';
import { ShockAbsorbers, shockBolt, solidFor, surfaceFor, surfaceKey } from './parts';
import { PALETTES, type Theme } from './theme';

const MAX_ROBOTS = 16;
const SPACING = 1.5;

export interface Pose {
  qpos: ArrayLike<number>;
  design: Record<string, number>;
}

export function gridMaterial(): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    uniforms: { center: { value: new THREE.Vector2() }, minorGrey: { value: 0.17 }, majorGrey: { value: 0.32 } },
    vertexShader: /* glsl */ `
      varying vec3 vWorld;
      void main() {
        vec4 w = modelMatrix * vec4(position, 1.0);
        vWorld = w.xyz;
        gl_Position = projectionMatrix * viewMatrix * w;
      }`,
    fragmentShader: /* glsl */ `
      uniform vec2 center;
      uniform float minorGrey;
      uniform float majorGrey;
      varying vec3 vWorld;
      float line(float coord, float width) {
        float d = abs(fract(coord - 0.5) - 0.5) / fwidth(coord);
        return 1.0 - min(d / width, 1.0);
      }
      void main() {
        vec2 p = vWorld.xy;
        float minor = max(line(p.x * 4.0, 0.8), line(p.y * 4.0, 0.8));
        float major = max(line(p.x, 1.0), line(p.y, 1.0));
        float dist = length(p - center);
        float fade = 1.0 - smoothstep(2.0, 11.0, dist);
        vec3 col = mix(vec3(minorGrey), vec3(majorGrey), major);
        float a = max(minor * 0.55, major) * fade;
        if (a < 0.01) discard;
        gl_FragColor = vec4(col, a);
      }`,
  });
}

interface GeomDraw {
  geom: GeomMeta;
  mesh: THREE.InstancedMesh;
  local: THREE.Matrix4;
  base: THREE.MeshStandardMaterial;
}

interface RodDraw {
  body: number;
  top: THREE.Vector3;
  bottom: THREE.Vector3;
  direction: THREE.Vector3;
  rodName: string;
  mesh: THREE.InstancedMesh;
}

export class MainView {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera: THREE.PerspectiveCamera;
  readonly controls: OrbitControls;
  private meta: Meta;
  private kin: Kinematics;
  private draws: GeomDraw[] = [];
  private rods: RodDraw[] = [];
  private shocks: ShockAbsorbers;
  private bolt = new THREE.Vector3();
  private lower = new THREE.Vector3();
  private upper = new THREE.Vector3();
  private grid: THREE.Mesh;
  private shadowFloor: THREE.Mesh;
  private com: THREE.Group;
  private command: THREE.ArrowHelper;
  private highlight = new THREE.MeshStandardMaterial({ color: 0xff2b2b, emissive: 0xff2b2b, emissiveIntensity: 0.35, roughness: 0.5 });
  private highlighted = new Set<number>();
  private count = 1;
  private follow = true;
  private lastBase = new THREE.Vector3();
  private m4 = new THREE.Matrix4();
  private m4b = new THREE.Matrix4();
  private q = new THREE.Quaternion();
  private v = new THREE.Vector3();
  private s = new THREE.Vector3(1, 1, 1);
  private tmp = new Float64Array(3);
  private tmp2 = new Float64Array(3);
  private poses: Pose[] = [];
  private resizeObserver: ResizeObserver;
  private disposed = false;
  onPick?: (body: number) => void;
  onPush?: (force: [number, number, number]) => void;

  constructor(private canvas: HTMLCanvasElement, assets: RobotAssets) {
    this.meta = assets.meta;
    this.kin = new Kinematics(assets.meta);
    THREE.Object3D.DEFAULT_UP.set(0, 0, 1);
    const r = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
    r.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    r.outputColorSpace = THREE.SRGBColorSpace;
    r.toneMapping = THREE.ACESFilmicToneMapping;
    r.toneMappingExposure = 1.0;
    r.shadowMap.enabled = true;
    r.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer = r;
    this.scene.background = new THREE.Color(0x050505);
    this.scene.fog = new THREE.Fog(0x050505, 8, 22);
    const pmrem = new THREE.PMREMGenerator(r);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.scene.environmentIntensity = 0.28;

    this.camera = new THREE.PerspectiveCamera(34, 1, 0.02, 80);
    this.camera.up.set(0, 0, 1);
    this.camera.position.set(1.9, -2.3, 1.25);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.target.set(0, 0, 0.5);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.1;
    this.controls.maxPolarAngle = Math.PI * 0.49;
    this.controls.minDistance = 0.5;
    this.controls.maxDistance = 14;

    const key = new THREE.DirectionalLight(0xffffff, 2.1);
    key.position.set(2.5, -3, 4.5);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.camera.left = -3;
    key.shadow.camera.right = 3;
    key.shadow.camera.top = 3;
    key.shadow.camera.bottom = -3;
    key.shadow.bias = -0.0005;
    key.shadow.normalBias = 0.02;
    const rim = new THREE.DirectionalLight(0xffffff, 1.6);
    rim.position.set(-3, 4, 2.5);
    const fill = new THREE.HemisphereLight(0xffffff, 0x080808, 0.35);
    this.scene.add(key, key.target, rim, fill);
    this.keyLight = key;
    this.hemi = fill;

    this.grid = new THREE.Mesh(new THREE.PlaneGeometry(60, 60), gridMaterial());
    this.grid.renderOrder = -1;
    this.shadowFloor = new THREE.Mesh(new THREE.PlaneGeometry(60, 60), new THREE.ShadowMaterial({ opacity: 0.45 }));
    this.shadowFloor.receiveShadow = true;
    this.shadowFloor.position.z = 0.0005;
    this.scene.add(this.grid, this.shadowFloor);

    this.buildRobot(assets);
    this.shocks = new ShockAbsorbers(this.meta.struts.map((s) => s.length_cad), MAX_ROBOTS);
    this.shocks.setCount(1); // One robot to start: unused instances must not draw.
    this.scene.add(this.shocks.group);

    // Centre of mass: a red ring on the floor under it, and a dashed drop line.
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.03, 0.042, 40), new THREE.MeshBasicMaterial({ color: 0xff2b2b, transparent: true, opacity: 0.9, depthWrite: false }));
    const drop = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 0, 1)]),
      new THREE.LineDashedMaterial({ color: 0xff2b2b, dashSize: 0.025, gapSize: 0.02, transparent: true, opacity: 0.7 }),
    );
    drop.computeLineDistances();
    this.com = new THREE.Group();
    this.com.add(ring, drop);
    this.com.userData = { ring, drop };
    this.scene.add(this.com);
    this.command = new THREE.ArrowHelper(new THREE.Vector3(1, 0, 0), new THREE.Vector3(), 0.3, 0xff2b2b, 0.07, 0.045);
    this.command.visible = false;
    this.scene.add(this.command);

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(canvas);
    this.bindPointer();
    this.resize();
  }

  private keyLight: THREE.DirectionalLight;
  private hemi: THREE.HemisphereLight;

  /** Recolour the stage for the dark or the light theme; the robot keeps its own materials. */
  setTheme(theme: Theme): void {
    const p = PALETTES[theme];
    (this.scene.background as THREE.Color).set(p.background);
    (this.scene.fog as THREE.Fog).color.set(p.background);
    const grid = (this.grid.material as THREE.ShaderMaterial).uniforms;
    grid.minorGrey.value = p.gridMinor;
    grid.majorGrey.value = p.gridMajor;
    (this.shadowFloor.material as THREE.ShadowMaterial).opacity = p.shadow;
    this.hemi.groundColor.set(p.hemiGround);
    this.hemi.intensity = p.hemi;
    this.renderer.toneMappingExposure = p.exposure;
    this.command.setColor(p.accent);
    const { ring, drop } = this.com.userData as { ring: THREE.Mesh; drop: THREE.Line };
    (ring.material as THREE.MeshBasicMaterial).color.set(p.accent);
    (drop.material as THREE.LineDashedMaterial).color.set(p.accent);
    this.highlight.color.set(p.accent);
    this.highlight.emissive.set(p.accent);
  }

  private buildRobot(assets: RobotAssets): void {
    const meta = this.meta;
    const rodBodies = new Set(meta.rods.flatMap((r) => r.rods.map((p) => p.body)));
    const materials = new Map<string, THREE.MeshStandardMaterial>();
    for (const geom of meta.geoms) {
      if (rodBodies.has(geom.body) && geom.type === GEOM.CYLINDER) continue; // Drawn from the design instead.
      if (meta.bodies[geom.body].kind === 'virtual') continue;
      const geometry = solidFor(geom, assets);
      if (!geometry) continue;
      const key = surfaceKey(geom);
      let material = materials.get(key);
      if (!material) {
        material = surfaceFor(key);
        materials.set(key, material);
      }
      const mesh = new THREE.InstancedMesh(geometry, material, MAX_ROBOTS);
      mesh.castShadow = true;
      mesh.receiveShadow = false;
      mesh.count = 1;
      mesh.frustumCulled = false;
      mesh.userData.body = geom.body;
      const local = new THREE.Matrix4().compose(
        new THREE.Vector3(...geom.pos),
        threeQuat(geom.quat),
        new THREE.Vector3(1, 1, 1),
      );
      this.draws.push({ geom, mesh, local, base: material });
      this.scene.add(mesh);
    }
    const carbon = materials.get('carbon_fiber_dark_gray') ?? surfaceFor('carbon_fiber_dark_gray');
    for (const rod of meta.rods) {
      for (const piece of rod.rods) {
        const geometry = new THREE.CylinderGeometry(piece.radius, piece.radius, 1, 20).rotateX(Math.PI / 2).translate(0, 0, 0.5);
        const mesh = new THREE.InstancedMesh(geometry, carbon, MAX_ROBOTS);
        mesh.castShadow = true;
        mesh.count = 1;
        mesh.frustumCulled = false;
        mesh.userData.body = piece.body;
        this.rods.push({
          body: piece.body,
          top: new THREE.Vector3(...piece.top),
          bottom: new THREE.Vector3(...piece.bottom),
          direction: new THREE.Vector3(...piece.direction),
          rodName: rod.name,
          mesh,
        });
        this.scene.add(mesh);
      }
    }
  }

  setFollow(on: boolean): void {
    this.follow = on;
  }

  /** Show these robots (one for the live view, up to sixteen practice robots). */
  setPoses(poses: Pose[]): void {
    this.poses = poses.slice(0, MAX_ROBOTS);
    const count = Math.max(1, this.poses.length);
    if (count !== this.count) {
      this.count = count;
      for (const d of this.draws) d.mesh.count = count;
      for (const r of this.rods) r.mesh.count = count;
      this.shocks.setCount(count);
    }
  }

  setHighlight(bodies: number[]): void {
    const next = new Set(bodies);
    for (const d of this.draws) {
      const on = next.has(d.geom.body);
      d.mesh.material = on ? this.highlight : d.base;
    }
    this.highlighted = next;
  }

  setCommand(command: [number, number, number] | null, heading: number): void {
    if (!command || Math.hypot(command[0], command[1]) < 0.03) {
      this.command.visible = false;
      return;
    }
    const c = Math.cos(heading), s = Math.sin(heading);
    const dir = new THREE.Vector3(c * command[0] - s * command[1], s * command[0] + c * command[1], 0);
    const length = dir.length();
    this.command.setDirection(dir.normalize());
    this.command.setLength(0.15 + length * 0.6, 0.07, 0.045);
    this.command.visible = true;
  }

  private offsetOf(index: number): THREE.Vector3 {
    if (this.count <= 1) return new THREE.Vector3();
    const side = Math.ceil(Math.sqrt(this.count));
    const row = Math.floor(index / side), col = index % side;
    return new THREE.Vector3((col - (side - 1) / 2) * SPACING, (row - (side - 1) / 2) * SPACING, 0);
  }

  /** Pose every robot and draw a frame. */
  frame(): void {
    if (this.disposed) return;
    const meta = this.meta;
    const kin = this.kin;
    let baseSum = new THREE.Vector3();
    this.poses.forEach((pose, index) => {
      const offset = this.offsetOf(index);
      kin.compute(pose.qpos, designOffsets(meta, pose.design));
      const exts: Record<string, number> = {};
      for (const [key, param] of Object.entries(meta.robot.design)) if (param.kind === 'rod' && param.rod) exts[param.rod] = (pose.design[key] ?? param.default) / 1000;
      for (const d of this.draws) {
        const b = d.geom.body;
        this.v.set(kin.xpos[b * 3] + offset.x, kin.xpos[b * 3 + 1] + offset.y, kin.xpos[b * 3 + 2]);
        threeQuat(kin.xquat, b * 4, this.q);
        this.m4.compose(this.v, this.q, this.s).multiply(d.local);
        d.mesh.setMatrixAt(index, this.m4);
      }
      for (const r of this.rods) {
        const ext = exts[r.rodName] ?? 0;
        const bottom = r.bottom.clone().addScaledVector(r.direction, ext);
        const span = bottom.clone().sub(r.top);
        const length = span.length();
        const localQ = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), span.normalize());
        this.m4b.compose(r.top, localQ, new THREE.Vector3(1, 1, length));
        this.v.set(kin.xpos[r.body * 3] + offset.x, kin.xpos[r.body * 3 + 1] + offset.y, kin.xpos[r.body * 3 + 2]);
        threeQuat(kin.xquat, r.body * 4, this.q);
        this.m4.compose(this.v, this.q, this.s).multiply(this.m4b);
        r.mesh.setMatrixAt(index, this.m4);
      }
      meta.struts.forEach((strut, k) => {
        const a = kin.point(strut.upper.body, strut.upper.pos, this.tmp);
        this.upper.set(a[0] + offset.x, a[1] + offset.y, a[2]);
        const b = kin.point(strut.lower.body, strut.lower.pos, this.tmp2);
        this.lower.set(b[0] + offset.x, b[1] + offset.y, b[2]);
        this.shocks.place(index, k, this.lower, this.upper, shockBolt(meta, kin, strut, this.bolt));
      });
      baseSum.add(new THREE.Vector3(kin.xpos[3] + offset.x, kin.xpos[4] + offset.y, 0));
    });
    for (const d of this.draws) d.mesh.instanceMatrix.needsUpdate = true;
    for (const r of this.rods) r.mesh.instanceMatrix.needsUpdate = true;
    this.shocks.commit();

    if (this.poses.length) {
      baseSum = baseSum.multiplyScalar(1 / this.poses.length);
      // The camera keeps the live robot where it is on screen, through resets too: skipping
      // a jump (a reset after a long walk, or a page opened with the robot far from the
      // start) left the camera on empty floor.
      if (this.follow && this.count === 1) {
        const delta = baseSum.clone().sub(this.lastBase);
        this.camera.position.add(delta);
        this.controls.target.add(delta);
      }
      this.lastBase.copy(baseSum);
      // The command arrow stands under the robot wherever it walks.
      this.command.position.set(baseSum.x, baseSum.y, 0.01);
      (this.grid.material as THREE.ShaderMaterial).uniforms.center.value.set(this.controls.target.x, this.controls.target.y);
      this.keyLight.position.set(this.controls.target.x + 2.5, this.controls.target.y - 3, 4.5);
      this.keyLight.target.position.set(this.controls.target.x, this.controls.target.y, 0);
      this.keyLight.target.updateMatrixWorld();
    }
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }

  /** Centre of mass marker, for the live robot. */
  setCom(com: [number, number, number] | null): void {
    this.com.visible = !!com && this.count === 1;
    if (!com) return;
    const { ring, drop } = this.com.userData as { ring: THREE.Mesh; drop: THREE.Line };
    ring.position.set(com[0], com[1], 0.002);
    drop.position.set(com[0], com[1], 0);
    drop.scale.set(1, 1, Math.max(com[2], 0.01));
  }

  /** The live robot's heading (yaw) from its base quaternion. */
  heading(): number {
    const q = this.kin.xquat;
    const w = q[4], x = q[5], y = q[6], z = q[7];
    // Forward axis of the base, rotated into the world, projected on the floor.
    const f = this.meta.info.forward;
    rotate(this.tmp, 0, [w, x, y, z], 0, f[0], f[1], f[2]);
    return Math.atan2(this.tmp[1], this.tmp[0]);
  }

  frameRobot(): void {
    // Sized to the robot: a 1 m robot is seen from 3 m, a 1.6 m one from further back.
    const tall = Number.isFinite(this.meta.info.height) && this.meta.info.height > 0 ? this.meta.info.height : 1;
    const z = Math.max(this.meta.info.hip_height ? this.meta.info.hip_height + 0.05 : 0.5, tall * 0.48);
    const target = new THREE.Vector3(this.lastBase.x, this.lastBase.y, z);
    const near = Math.max(3.0, tall * 2.7);
    const distance = this.count > 1 ? near + 0.5 + Math.sqrt(this.count) * 1.2 * Math.max(1, tall) : near;
    const dir = new THREE.Vector3(0.62, -0.72, 0.32).normalize();
    this.camera.position.copy(target).addScaledVector(dir, distance);
    this.controls.target.copy(target);
    this.controls.update();
  }

  private resize(): void {
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  private ray(event: PointerEvent): THREE.Intersection | null {
    const rect = this.canvas.getBoundingClientRect();
    const ndc = new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    const caster = new THREE.Raycaster();
    caster.setFromCamera(ndc, this.camera);
    const targets = [...this.draws.map((d) => d.mesh), ...this.rods.map((r) => r.mesh)];
    return caster.intersectObjects(targets, false)[0] ?? null;
  }

  private bindPointer(): void {
    let down: { x: number; y: number; shift: boolean; point: THREE.Vector3 | null } | null = null;
    this.canvas.addEventListener('pointerdown', (event) => {
      const hit = event.shiftKey ? this.ray(event) : null;
      down = { x: event.clientX, y: event.clientY, shift: event.shiftKey, point: hit ? hit.point.clone() : null };
      if (down.shift && down.point) this.controls.enabled = false;
    });
    this.canvas.addEventListener('pointerup', (event) => {
      this.controls.enabled = true;
      if (!down) return;
      const moved = Math.hypot(event.clientX - down.x, event.clientY - down.y);
      if (down.shift && down.point && moved > 8 && this.onPush) {
        // A shove: drag length sets the force, along the floor in the drag's direction.
        const rect = this.canvas.getBoundingClientRect();
        const start = down.point.clone().project(this.camera);
        const end = new THREE.Vector3(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1, start.z).unproject(this.camera);
        const dir = end.sub(down.point);
        dir.z = 0;
        const force = Math.min(150, 40 + moved * 0.5);
        if (dir.lengthSq() > 1e-8) {
          dir.normalize().multiplyScalar(force);
          this.onPush([dir.x, dir.y, 0]);
        }
      } else if (moved < 5) {
        const hit = this.ray(event);
        if (hit && this.onPick) this.onPick(hit.object.userData.body as number);
      }
      down = null;
    });
    this.canvas.addEventListener('dblclick', () => this.frameRobot());
  }

  dispose(): void {
    this.disposed = true;
    this.resizeObserver.disconnect();
    this.controls.dispose();
    this.renderer.dispose();
  }

  get highlightedBodies(): Set<number> {
    return this.highlighted;
  }
}
