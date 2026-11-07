use crate::{
    ffi,
    native::{slice, v3},
    Data, Model,
};
use nalgebra::{Matrix3, Quaternion, SymmetricEigen, UnitQuaternion, Vector3};
pub type V3 = Vector3<f64>;
pub type M3 = Matrix3<f64>;
pub fn vec(a: [f64; 3]) -> V3 {
    V3::from(a)
}
pub fn out(v: V3) -> [f64; 3] {
    [v.x, v.y, v.z]
}
pub fn quat(q: [f64; 4]) -> UnitQuaternion<f64> {
    UnitQuaternion::new_normalize(Quaternion::new(q[0], q[1], q[2], q[3]))
}
pub fn qout(q: UnitQuaternion<f64>) -> [f64; 4] {
    let q = q.quaternion();
    [q.w, q.i, q.j, q.k]
}
pub unsafe fn mat(ptr: *const f64, index: usize) -> M3 {
    M3::from_row_slice(slice(ptr.add(index * 9), 9))
}
pub fn to_local(data: &Data, body: usize, p: V3) -> V3 {
    unsafe { mat(data.raw().xmat, body).transpose() * (p - v3(data.raw().xpos, body)) }
}
pub fn dir_to_local(data: &Data, body: usize, p: V3) -> V3 {
    unsafe { mat(data.raw().xmat, body).transpose() * p }
}
pub fn local_pose(data: &Data, body: usize, p: V3, q: UnitQuaternion<f64>) -> ([f64; 3], [f64; 4]) {
    let rotation = unsafe { UnitQuaternion::from_matrix(&mat(data.raw().xmat, body)) };
    (out(to_local(data, body, p)), qout(rotation.inverse() * q))
}
pub fn link_of(model: &Model, mut body: usize) -> usize {
    unsafe {
        let m = model.raw();
        while body > 0 && *m.body_jntnum.add(body) == 0 && *m.body_parentid.add(body) > 0 {
            body = *m.body_parentid.add(body) as usize;
        }
        body
    }
}
pub fn is_drawn(model: &Model, g: usize) -> bool {
    unsafe {
        let m = model.raw();
        let k = *m.geom_type.add(g);
        if k == ffi::mjtGeom_mjGEOM_PLANE as i32
            || k == ffi::mjtGeom_mjGEOM_HFIELD as i32
            || *m.geom_group.add(g) >= 3
        {
            return false;
        }
        let material = *m.geom_matid.add(g);
        let alpha = if material >= 0 {
            *m.mat_rgba.add(material as usize * 4 + 3)
        } else {
            *m.geom_rgba.add(g * 4 + 3)
        };
        alpha >= 0.01
    }
}
pub fn visual_geoms(model: &Model, body: usize) -> Vec<usize> {
    unsafe {
        let m = model.raw();
        let own = (0..m.ngeom as usize)
            .filter(|g| *m.geom_bodyid.add(*g) == body as i32)
            .collect::<Vec<_>>();
        let visual = own
            .iter()
            .copied()
            .filter(|g| {
                is_drawn(model, *g)
                    && *m.geom_contype.add(*g) == 0
                    && *m.geom_conaffinity.add(*g) == 0
            })
            .collect::<Vec<_>>();
        if !visual.is_empty() {
            visual
        } else {
            own.into_iter().filter(|g| is_drawn(model, *g)).collect()
        }
    }
}
pub fn geom_points(model: &Model, data: &Data, g: usize) -> Vec<V3> {
    unsafe {
        let m = model.raw();
        let d = data.raw();
        let kind = *m.geom_type.add(g);
        let size = v3(m.geom_size, g);
        let mut p = vec![];
        match kind {
            ffi::mjtGeom_mjGEOM_MESH => {
                let mesh = *m.geom_dataid.add(g) as usize;
                let start = *m.mesh_vertadr.add(mesh) as usize;
                let n = *m.mesh_vertnum.add(mesh) as usize;
                for v in slice(m.mesh_vert.add(start * 3), n * 3).chunks_exact(3) {
                    p.push(V3::new(v[0] as f64, v[1] as f64, v[2] as f64));
                }
            }
            ffi::mjtGeom_mjGEOM_BOX => {
                for x in [-1., 1.] {
                    for y in [-1., 1.] {
                        for z in [-1., 1.] {
                            p.push(V3::new(x * size.x, y * size.y, z * size.z));
                        }
                    }
                }
            }
            ffi::mjtGeom_mjGEOM_SPHERE | ffi::mjtGeom_mjGEOM_ELLIPSOID => {
                let s = if kind == ffi::mjtGeom_mjGEOM_SPHERE {
                    V3::repeat(size.x)
                } else {
                    size
                };
                for axis in 0..3 {
                    for sign in [-1., 1.] {
                        let mut v = V3::zeros();
                        v[axis] = s[axis] * sign;
                        p.push(v);
                    }
                }
            }
            ffi::mjtGeom_mjGEOM_CYLINDER | ffi::mjtGeom_mjGEOM_CAPSULE => {
                for z in [-size.y, size.y] {
                    for i in 0..12 {
                        let t = i as f64 * std::f64::consts::TAU / 12.;
                        p.push(V3::new(t.cos() * size.x, t.sin() * size.x, z));
                    }
                }
                if kind == ffi::mjtGeom_mjGEOM_CAPSULE {
                    p.extend([
                        V3::new(0., 0., size.y + size.x),
                        V3::new(0., 0., -size.y - size.x),
                    ]);
                }
            }
            _ => {}
        }
        let r = mat(d.geom_xmat, g);
        let t = v3(d.geom_xpos, g);
        p.into_iter().map(|p| t + r * p).collect()
    }
}
pub fn body_points(model: &Model, data: &Data, b: usize) -> Vec<V3> {
    visual_geoms(model, b)
        .into_iter()
        .flat_map(|g| geom_points(model, data, g))
        .collect()
}
pub fn aabb(p: &[V3]) -> Option<(V3, V3)> {
    if p.is_empty() {
        return None;
    }
    let mut lo = p[0];
    let mut hi = p[0];
    for p in p {
        lo = lo.inf(p);
        hi = hi.sup(p);
    }
    Some((lo, hi))
}
pub fn body_center(model: &Model, data: &Data, b: usize) -> V3 {
    if let Some((lo, hi)) = aabb(&body_points(model, data, b)) {
        (lo + hi) * 0.5
    } else {
        unsafe { v3(data.raw().xipos, b) }
    }
}
pub fn principal(p: &[V3]) -> (V3, M3) {
    if p.is_empty() {
        return (V3::zeros(), M3::identity());
    }
    let mean = p.iter().fold(V3::zeros(), |a, p| a + p) / p.len() as f64;
    let covariance = p
        .iter()
        .fold(M3::zeros(), |a, p| a + (p - mean) * (p - mean).transpose());
    let eig = SymmetricEigen::new(covariance);
    let mut order = [0, 1, 2];
    order.sort_by(|a, b| eig.eigenvalues[*b].total_cmp(&eig.eigenvalues[*a]));
    let mut r = M3::from_columns(&order.map(|i| eig.eigenvectors.column(i).into_owned()));
    if r.determinant() < 0. {
        r.set_column(2, &(-r.column(2)));
    }
    (mean, r)
}
pub fn box_inertia(m: f64, size: [f64; 3]) -> [f64; 3] {
    let [a, b, c] = size;
    [
        m * (b * b + c * c) / 12.,
        m * (a * a + c * c) / 12.,
        m * (a * a + b * b) / 12.,
    ]
}
pub fn cylinder_inertia(m: f64, r: f64, l: f64) -> [f64; 3] {
    let side = m * (3. * r * r + l * l) / 12.;
    [side, side, m * r * r / 2.]
}
pub fn quat_from_z(v: V3) -> [f64; 4] {
    qout(
        UnitQuaternion::rotation_between(&V3::z(), &v).unwrap_or_else(|| {
            UnitQuaternion::from_axis_angle(&V3::x_axis(), std::f64::consts::PI)
        }),
    )
}
