// Loading a robot for the 3D views: its model description and its meshes.

import * as THREE from 'three';
import { toCreasedNormals } from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import { api } from './api';
import type { Meta } from './types';

export interface RobotAssets {
  meta: Meta;
  /** Solid geometry per mesh index, with creased normals. */
  solids: THREE.BufferGeometry[];
  /** Feature edges per mesh index, for the wireframe view. */
  edges: THREE.BufferGeometry[];
}

/** Meshes per robot, prepared once: the hangar and the workspace share them. */
const prepared = new Map<string, Promise<Pick<RobotAssets, 'solids' | 'edges'>>>();

/** Forget a robot's meshes (it was removed or re-imported). */
export function forgetRobot(id: string): void {
  prepared.delete(id);
}

/** A robot's model description (always fresh: designs change it) and its meshes. */
export async function loadRobot(id: string, onStep?: (text: string) => void): Promise<RobotAssets> {
  onStep?.('Reading the robot model');
  const meta = await api.get<Meta>(`/api/robots/${id}/meta`);
  let parts = prepared.get(id);
  if (!parts) {
    parts = prepareMeshes(id, meta, onStep);
    prepared.set(id, parts);
    parts.catch(() => prepared.delete(id));
  }
  return { meta, ...(await parts) };
}

async function prepareMeshes(id: string, meta: Meta, onStep?: (text: string) => void): Promise<Pick<RobotAssets, 'solids' | 'edges'>> {
  const blob = await api.binary(`/api/robots/${id}/meshes.bin`);
  onStep?.('Preparing meshes');
  const header = new Uint32Array(blob, 0, 3);
  const [nv, nf, ne] = [header[0], header[1], header[2]];
  let offset = 12;
  const verts = new Float32Array(blob, offset, nv * 3);
  offset += nv * 12;
  const faces = new Uint32Array(blob, offset, nf * 3);
  offset += nf * 12;
  const lines = new Float32Array(blob, offset, ne * 6);
  const solids: THREE.BufferGeometry[] = [];
  const edges: THREE.BufferGeometry[] = [];
  for (const m of meta.meshes) {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(verts.slice(m.v[0] * 3, (m.v[0] + m.v[1]) * 3), 3));
    g.setIndex(new THREE.BufferAttribute(faces.slice(m.f[0] * 3, (m.f[0] + m.f[1]) * 3), 1));
    const creased = toCreasedNormals(g, Math.PI / 5);
    creased.computeBoundingSphere();
    g.dispose();
    solids.push(creased);
    const e = new THREE.BufferGeometry();
    e.setAttribute('position', new THREE.BufferAttribute(lines.slice(m.e[0] * 6, (m.e[0] + m.e[1]) * 6), 3));
    edges.push(e);
  }
  return { solids, edges };
}

/** Quaternion (w, x, y, z) as Three.js wants it. */
export function threeQuat(q: ArrayLike<number>, offset = 0, out = new THREE.Quaternion()): THREE.Quaternion {
  return out.set(q[offset + 1], q[offset + 2], q[offset + 3], q[offset]);
}

/** MuJoCo geom types. */
export const GEOM = { PLANE: 0, HFIELD: 1, SPHERE: 2, CAPSULE: 3, ELLIPSOID: 4, CYLINDER: 5, BOX: 6, MESH: 7 } as const;
