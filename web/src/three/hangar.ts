// The hangar stage: one robot on a turntable in a lit bay, before it is loaded
// for training.
//
// The robot stands on a platform that turns about once a minute. When a robot is
// brought into the bay, a scanner ring rises from the platform and the robot
// appears below it, once. With reduced motion the platform stands still and the
// robot is simply there. The HUD around the robot (targeting brackets, the height
// line) is drawn by the page from ``onFrame``, where the robot is on screen.

import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import type { RobotAssets } from '../lib/robot';
import { staticRobot } from './parts';
import { PALETTES, type Theme } from './theme';
import { gridMaterial } from './viewport';

const RED = 0xff2b2b;
// Lines glow, the robot does not: red lines carry more red than white light can (HDR,
// red channel only, so the hue stays Signal Red once clamped) and bloom takes only that.
const SIGNAL = new THREE.Color(RED);
const GLOW = new THREE.Color(SIGNAL.r * 7, SIGNAL.g, SIGNAL.b);
/** The bay's own colours. By day (light) there is no bloom to carry a glow: plain accent
 * lines, a pale platform, and the light cone left out, as white light does not show on white. */
const BAY = {
  dark: { grid: [0.12, 0.24], disc: 0x0c0c0c, metalness: 0.75, roughness: 0.32, grey: 0x5c5c5c, dim: 0x2a2a2a, box: 0x3a3a3a, ground: 0x080808, fill: 0.2, shadow: 0.5 },
  light: { grid: [0.74, 0.56], disc: 0xd6d6d2, metalness: 0.15, roughness: 0.6, grey: 0x8e8e8a, dim: 0xc4c4c0, box: 0xb0b0ac, ground: 0x8f9092, fill: 0.5, shadow: 0.28 },
} as const;
const TURN = 0.1; // rad/s.
const SCAN_MS = 1100;
const RESUME_MS = 2500; // The platform waits this long after you let go of the view.

/** Where the robot is on the canvas (px): the box around it as it turns, and its upright axis. */
export interface ScreenBox {
  left: number;
  top: number;
  right: number;
  bottom: number;
  axisX: number;
  axisTop: number;
  axisBottom: number;
  settled: boolean;
}

export class HangarView {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera: THREE.PerspectiveCamera;
  readonly controls: OrbitControls;
  private composer: EffectComposer;
  private turntable = new THREE.Group();
  private robot: THREE.Group | null = null;
  private platform: THREE.Group | null = null;
  private rig: THREE.Group | null = null;
  private cone: THREE.Mesh | null = null;
  private lights = new THREE.Group();
  private scanRing: THREE.Mesh;
  private scanGlow: THREE.Mesh;
  private clip = new THREE.Plane(new THREE.Vector3(0, 0, -1), 100);
  private scanStart = -1;
  private height = 1;
  private radius = 0.4;
  private reach = 0.3; // How far the robot reaches from the turning axis.
  private point = new THREE.Vector3();
  onFrame?: (box: ScreenBox | null) => void;
  private front = new THREE.Vector3(0, -1, 0);
  private inset = { left: 0, bottom: 0, top: 0 };
  private resizeObserver: ResizeObserver;
  private raf = 0;
  private last = 0;
  private holdUntil = 0;
  private reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  private theme: Theme = 'dark';
  private grid: THREE.Mesh;
  private shadowFloor: THREE.Mesh;
  private bloom: UnrealBloomPass;

  constructor(private canvas: HTMLCanvasElement) {
    THREE.Object3D.DEFAULT_UP.set(0, 0, 1);
    const r = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
    r.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    r.outputColorSpace = THREE.SRGBColorSpace;
    // No tone mapping: filmic curves turn a bright red orange. Lights stay below clipping instead.
    r.toneMapping = THREE.NoToneMapping;
    r.shadowMap.enabled = true;
    r.shadowMap.type = THREE.PCFSoftShadowMap;
    r.localClippingEnabled = true;
    this.renderer = r;
    this.scene.background = new THREE.Color(0x050505);
    this.scene.fog = new THREE.Fog(0x050505, 6, 16);
    const pmrem = new THREE.PMREMGenerator(r);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.scene.environmentIntensity = 0.12;

    this.camera = new THREE.PerspectiveCamera(30, 1, 0.05, 60);
    this.camera.up.set(0, 0, 1);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.enablePan = false;
    this.controls.maxPolarAngle = Math.PI * 0.495;
    this.controls.addEventListener('start', () => (this.holdUntil = Infinity));
    this.controls.addEventListener('end', () => (this.holdUntil = performance.now() + RESUME_MS));

    const grid = new THREE.Mesh(new THREE.PlaneGeometry(60, 60), gridMaterial());
    grid.renderOrder = -1;
    const shadow = new THREE.Mesh(new THREE.PlaneGeometry(60, 60), new THREE.ShadowMaterial({ opacity: BAY.dark.shadow }));
    shadow.receiveShadow = true;
    shadow.position.z = 0.0005;
    this.grid = grid;
    this.shadowFloor = shadow;
    this.scene.add(grid, shadow, this.turntable, this.lights);

    // The scanner: a red hoop and a faint disc of light, risen once per arrival.
    this.scanRing = new THREE.Mesh(
      new THREE.RingGeometry(1, 1.035, 128),
      new THREE.MeshBasicMaterial({ color: GLOW, transparent: true, side: THREE.DoubleSide, depthWrite: false, blending: THREE.AdditiveBlending }),
    );
    this.scanGlow = new THREE.Mesh(
      new THREE.CircleGeometry(1, 96),
      new THREE.MeshBasicMaterial({ color: RED, transparent: true, opacity: 0.07, side: THREE.DoubleSide, depthWrite: false, blending: THREE.AdditiveBlending }),
    );
    this.scanRing.visible = this.scanGlow.visible = false;
    this.scene.add(this.scanRing, this.scanGlow);

    this.composer = new EffectComposer(r);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(512, 512), 0.7, 0.45, 1.4);
    this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(canvas);
    canvas.addEventListener('dblclick', () => this.frameRobot());
    this.resize();
    this.raf = requestAnimationFrame(this.loop);
  }

  /** Bring a robot into the bay. */
  show(assets: RobotAssets, design: Record<string, number>): void {
    if (this.robot) {
      this.turntable.remove(this.robot);
      this.robot.traverse((o) => {
        if (o instanceof THREE.Mesh) (o.material as THREE.Material).dispose();
      });
    }
    const robot = staticRobot(assets, design);
    const box = new THREE.Box3().setFromObject(robot);
    const center = box.getCenter(new THREE.Vector3());
    robot.position.set(-center.x, -center.y, 0); // Turn about the robot's own middle.
    this.height = Math.max(box.max.z, 0.3);
    const size = box.getSize(new THREE.Vector3());
    this.reach = Math.hypot(size.x, size.y) / 2;
    this.radius = Math.max(0.36, 0.62 * Math.hypot(size.x, size.y), 0.26 * this.height);
    this.front.copy(robot.userData.front as THREE.Vector3);
    this.robot = robot;
    this.turntable.add(robot);
    this.buildPlatform();
    this.buildLights();
    this.buildRig();
    this.turntable.rotation.z = 0;
    this.frameRobot();
    if (!this.reduced) {
      robot.traverse((o) => {
        if (o instanceof THREE.Mesh) {
          const material = o.material as THREE.Material;
          material.clippingPlanes = [this.clip];
          material.clipShadows = true;
        }
      });
      this.clip.constant = 0;
      this.scanStart = performance.now();
      this.scanRing.visible = this.scanGlow.visible = true;
      this.scanRing.scale.setScalar(this.radius * 1.02);
      this.scanGlow.scale.setScalar(this.radius * 1.02);
    }
  }

  /** Recolour the bay for the dark or the light theme. */
  setTheme(theme: Theme): void {
    this.theme = theme;
    const p = PALETTES[theme];
    (this.scene.background as THREE.Color).set(p.background);
    (this.scene.fog as THREE.Fog).color.set(p.background);
    const grid = (this.grid.material as THREE.ShaderMaterial).uniforms;
    // The bay is lit without tone mapping, so its floor needs less grey than the stage's to read the same.
    [grid.minorGrey.value, grid.majorGrey.value] = BAY[theme].grid;
    (this.shadowFloor.material as THREE.ShadowMaterial).opacity = BAY[theme].shadow;
    this.bloom.enabled = p.bloom > 0;
    // Added light shows on black but not on a pale ground: by day the scanner is painted on.
    for (const mesh of [this.scanRing, this.scanGlow]) {
      const material = mesh.material as THREE.MeshBasicMaterial;
      material.blending = theme === 'light' ? THREE.NormalBlending : THREE.AdditiveBlending;
      material.needsUpdate = true;
    }
    (this.scanRing.material as THREE.MeshBasicMaterial).color.copy(this.glow());
    (this.scanGlow.material as THREE.MeshBasicMaterial).color.set(p.accent);
    this.buildPlatform();
    this.buildLights();
    this.buildRig();
  }

  /** Red lines: brighter than white can be in the dark, for the bloom to take; the accent by day. */
  private glow(): THREE.Color {
    return this.theme === 'light' ? new THREE.Color(PALETTES.light.accent) : GLOW.clone();
  }

  /** Room the HUD takes (px): the panel on the left, the bays along the bottom or the top. */
  setInsets(left: number, bottom: number, top = 0): void {
    this.inset = { left, bottom, top };
    if (this.cone) this.cone.visible = top === 0 && this.theme === 'dark';
    this.resize();
    this.frameRobot();
  }

  frameRobot(): void {
    const w = this.canvas.clientWidth || 1, h = this.canvas.clientHeight || 1;
    const freeH = Math.max(120, h - this.inset.bottom - this.inset.top - 40);
    const freeW = Math.max(160, w - this.inset.left - 40);
    const tan = Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2));
    const tall = this.height * 1.12;
    const wide = this.radius * 2.6;
    const distance = Math.max((tall * h) / (0.86 * freeH * 2 * tan), (wide * w) / (0.86 * freeW * 2 * tan * this.camera.aspect));
    const direction = this.viewDirection().setZ(0.2).normalize();
    const target = new THREE.Vector3(0, 0, this.height * 0.5);
    this.camera.position.copy(target).addScaledVector(direction, distance);
    this.controls.target.copy(target);
    this.controls.minDistance = distance * 0.4;
    this.controls.maxDistance = distance * 2.2;
    // The fog starts behind the robot however far the framing puts the camera (narrow screens frame from further away).
    const fog = this.scene.fog as THREE.Fog;
    fog.near = distance * 1.1;
    fog.far = distance * 3.2;
    this.controls.update();
  }

  /** An empty bay, waiting for a robot: the platform and rig without one. */
  clear(): void {
    if (this.robot) {
      this.turntable.remove(this.robot);
      this.robot.traverse((o) => {
        if (o instanceof THREE.Mesh) (o.material as THREE.Material).dispose();
      });
      this.robot = null;
    }
    this.height = 1.0;
    this.radius = 0.42;
    this.front.set(0, -1, 0);
    this.scanStart = -1;
    this.scanRing.visible = this.scanGlow.visible = false;
    this.buildPlatform();
    this.buildLights();
    this.buildRig();
    this.frameRobot();
  }

  /** Along the floor, from the robot towards the camera's first view: ahead of it, a little to its left. */
  private viewDirection(): THREE.Vector3 {
    const side = new THREE.Vector3(0, 0, 1).cross(this.front).normalize();
    return this.front.clone().multiplyScalar(Math.cos(0.5)).addScaledVector(side, Math.sin(0.5)).normalize();
  }

  dispose(): void {
    cancelAnimationFrame(this.raf);
    this.resizeObserver.disconnect();
    this.controls.dispose();
    this.composer.dispose();
    this.renderer.dispose();
  }

  private loop = (now: number) => {
    this.raf = requestAnimationFrame(this.loop);
    const dt = this.last ? Math.min(0.05, (now - this.last) / 1000) : 0;
    this.last = now;
    if (!this.reduced && now > this.holdUntil) this.turntable.rotation.z += TURN * dt;
    if (this.scanStart >= 0) this.scan(now);
    this.controls.update();
    this.composer.render();
    if (this.onFrame) this.onFrame(this.robot ? this.screenBox() : null);
  };

  /** The robot's place on the canvas: a cylinder around it as it turns, and its axis. */
  private screenBox(): ScreenBox {
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    const toScreen = (x: number, y: number, z: number) => {
      this.point.set(x, y, z).project(this.camera);
      return [((this.point.x + 1) / 2) * w, ((1 - this.point.y) / 2) * h];
    };
    let left = Infinity, right = -Infinity, top = Infinity, bottom = -Infinity;
    for (let i = 0; i < 16; i++) {
      const a = (i / 16) * Math.PI * 2;
      for (const z of [0, this.height]) {
        const [x, y] = toScreen(Math.cos(a) * this.reach, Math.sin(a) * this.reach, z);
        left = Math.min(left, x);
        right = Math.max(right, x);
        top = Math.min(top, y);
        bottom = Math.max(bottom, y);
      }
    }
    const [axisX, axisBottom] = toScreen(0, 0, 0);
    const [, axisTop] = toScreen(0, 0, this.height);
    return { left, top, right, bottom, axisX, axisTop, axisBottom, settled: this.scanStart < 0 };
  }

  private scan(now: number): void {
    const t = Math.min(1, (now - this.scanStart) / SCAN_MS);
    const eased = 1 - Math.pow(1 - t, 3);
    const z = eased * (this.height + 0.04);
    this.clip.constant = z;
    this.scanRing.position.set(0, 0, z);
    this.scanGlow.position.set(0, 0, z);
    const fade = t < 0.85 ? 1 : 1 - (t - 0.85) / 0.15;
    (this.scanRing.material as THREE.MeshBasicMaterial).opacity = 0.95 * fade;
    (this.scanGlow.material as THREE.MeshBasicMaterial).opacity = 0.07 * fade;
    if (t >= 1) {
      this.scanStart = -1;
      this.scanRing.visible = this.scanGlow.visible = false;
      this.robot?.traverse((o) => {
        if (o instanceof THREE.Mesh) {
          const material = o.material as THREE.Material;
          material.clippingPlanes = null;
          material.needsUpdate = true;
        }
      });
    }
  }

  /** The platform: a dark disc with a glowing red rim, a red ring and degree ticks on top. */
  private buildPlatform(): void {
    if (this.platform) {
      this.turntable.remove(this.platform);
      disposeTree(this.platform);
    }
    const R = this.radius;
    const bay = BAY[this.theme];
    const group = new THREE.Group();
    const disc = new THREE.Mesh(
      new THREE.CylinderGeometry(R, R * 1.015, 0.035, 128).rotateX(Math.PI / 2),
      new THREE.MeshStandardMaterial({ color: bay.disc, metalness: bay.metalness, roughness: bay.roughness }),
    );
    disc.position.z = -0.0175;
    disc.receiveShadow = true;
    const rim = new THREE.Mesh(new THREE.CylinderGeometry(R * 1.016, R * 1.016, 0.004, 128, 1, true).rotateX(Math.PI / 2), new THREE.MeshBasicMaterial({ color: this.glow() }));
    rim.position.z = -0.008;
    const red = new THREE.LineBasicMaterial({ color: this.glow() });
    const grey = new THREE.LineBasicMaterial({ color: bay.grey });
    const dim = new THREE.LineBasicMaterial({ color: bay.dim });
    const circle = (radius: number, material: THREE.LineBasicMaterial) => {
      const points = Array.from({ length: 129 }, (_, i) => {
        const a = (i / 128) * Math.PI * 2;
        return new THREE.Vector3(Math.cos(a) * radius, Math.sin(a) * radius, 0.0015);
      });
      return new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), material);
    };
    const ticks: THREE.Vector3[] = [];
    const cardinal: THREE.Vector3[] = [];
    for (let i = 0; i < 72; i++) {
      const a = (i / 72) * Math.PI * 2;
      const major = i % 18 === 0;
      const inner = major ? R * 0.74 : i % 6 === 0 ? R * 0.86 : R * 0.9;
      const list = major ? cardinal : ticks;
      list.push(new THREE.Vector3(Math.cos(a) * inner, Math.sin(a) * inner, 0.0015), new THREE.Vector3(Math.cos(a) * R * 0.94, Math.sin(a) * R * 0.94, 0.0015));
    }
    group.add(
      disc,
      rim,
      circle(R * 0.955, red),
      circle(R * 0.62, grey),
      circle(R * 0.3, dim),
      new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(ticks), grey),
      new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(cardinal), red),
    );
    this.platform = group;
    this.turntable.add(group);
  }

  /** The bay around the turntable: floor markings squared to the first view, with red
   * corners, and an overhead rig whose light falls on the platform as a faint cone. */
  private buildRig(): void {
    if (this.rig) {
      this.scene.remove(this.rig);
      disposeTree(this.rig);
    }
    const H = this.height, R = this.radius;
    const group = new THREE.Group();
    const view = this.viewDirection();
    const across = new THREE.Vector3(0, 0, 1).cross(view).normalize();
    const half = R + 0.28;
    const at = (a: number, b: number) => view.clone().multiplyScalar(a).addScaledVector(across, b).setZ(0.0012);
    const corners = [at(half, half), at(half, -half), at(-half, -half), at(-half, half)];
    const box = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(corners), new THREE.LineDashedMaterial({ color: BAY[this.theme].box, dashSize: 0.09, gapSize: 0.07 }));
    box.computeLineDistances();
    const arm = Math.min(0.24, half * 0.3);
    const marks: THREE.Vector3[] = [];
    for (const [a, b] of [[1, 1], [1, -1], [-1, -1], [-1, 1]]) {
      const corner = at(a * half, b * half);
      marks.push(corner, at(a * (half - arm), b * half), corner, at(a * half, b * (half - arm)));
    }
    group.add(box, new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(marks), new THREE.LineBasicMaterial({ color: this.glow() })));

    const height = H * 1.55;
    const ring = Array.from({ length: 97 }, (_, i) => {
      const a = (i / 96) * Math.PI * 2;
      return new THREE.Vector3(Math.cos(a) * R * 0.9, Math.sin(a) * R * 0.9, height);
    });
    const lamps: THREE.Vector3[] = [];
    for (let i = 0; i < 8; i++) {
      const a = (i / 8) * Math.PI * 2;
      const x = Math.cos(a) * R * 0.9, y = Math.sin(a) * R * 0.9;
      lamps.push(new THREE.Vector3(x, y, height), new THREE.Vector3(x, y, height - 0.05));
    }
    const steel = new THREE.LineBasicMaterial({ color: BAY[this.theme].grey });
    group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(ring), steel), new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(lamps), steel));
    const cone = new THREE.Mesh(
      new THREE.CylinderGeometry(R * 0.9, R * 1.08, height, 64, 1, true).rotateX(Math.PI / 2).translate(0, 0, height / 2),
      new THREE.ShaderMaterial({
        transparent: true,
        depthWrite: false,
        side: THREE.FrontSide,
        blending: THREE.AdditiveBlending,
        vertexShader: /* glsl */ `
          varying float vHeight;
          varying vec3 vNormal;
          varying vec3 vView;
          void main() {
            vHeight = uv.y;
            vec4 view = modelViewMatrix * vec4(position, 1.0);
            vView = -view.xyz;
            vNormal = normalMatrix * normal;
            gl_Position = projectionMatrix * view;
          }`,
        fragmentShader: /* glsl */ `
          varying float vHeight;
          varying vec3 vNormal;
          varying vec3 vView;
          void main() {
            // Brightest where the light lands on the platform; fading out at the cone's sides.
            float facing = abs(dot(normalize(vNormal), normalize(vView)));
            float fall = pow(1.0 - vHeight, 1.6);
            gl_FragColor = vec4(vec3(1.0), 0.02 * fall * pow(facing, 3.0));
          }`,
      }),
    );
    cone.renderOrder = 2;
    cone.visible = this.inset.top === 0 && this.theme === 'dark'; // Narrow screens: the bays sit over where it would hang.
    this.cone = cone;
    group.add(cone);
    this.rig = group;
    this.scene.add(group);
  }

  /** The bay's lights, placed from where the robot faces. */
  private buildLights(): void {
    this.lights.clear();
    const H = this.height;
    const front = this.front.clone();
    const side = new THREE.Vector3(0, 0, 1).cross(front).normalize();
    const key = new THREE.DirectionalLight(0xffffff, 1.0);
    key.position.copy(front.clone().multiplyScalar(2.4).addScaledVector(side, 1.6).setZ(H * 3));
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    const reach = Math.max(1.5, H * 1.4);
    Object.assign(key.shadow.camera, { left: -reach, right: reach, top: reach, bottom: -reach });
    key.shadow.bias = -0.0005;
    key.shadow.normalBias = 0.02;
    const rim = new THREE.DirectionalLight(0xffffff, 1.15);
    rim.position.copy(front.clone().multiplyScalar(-3).addScaledVector(side, -1.2).setZ(H * 2.2));
    const pool = new THREE.SpotLight(0xffffff, 2.2 * H * H, 0, 0.5, 0.75, 2);
    pool.position.set(0, 0, H * 2.6);
    pool.target.position.set(0, 0, 0);
    const fill = new THREE.HemisphereLight(0xffffff, BAY[this.theme].ground, BAY[this.theme].fill);
    this.lights.add(key, key.target, rim, pool, pool.target, fill);

  }

  private resize(): void {
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.composer.setSize(w, h);
    this.camera.aspect = w / h;
    // Centre the robot in the room the HUD leaves, not in the whole canvas.
    this.camera.setViewOffset(w, h, -this.inset.left / 2, (this.inset.bottom - this.inset.top) / 2, w, h);
    this.camera.updateProjectionMatrix();
  }
}

function disposeTree(root: THREE.Object3D): void {
  root.traverse((o) => {
    if (o instanceof THREE.Mesh || o instanceof THREE.Line) {
      o.geometry.dispose();
      (o.material as THREE.Material).dispose();
    }
  });
}
