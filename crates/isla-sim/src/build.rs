use crate::{
    ffi, geometry as g,
    native::{a3, a4, slice, slice_mut, v3, Spec},
    profile::{arr, array, number, object, string},
    Data, Model, RobotProfile,
};
use anyhow::{bail, Context, Result};
use nalgebra::UnitQuaternion;
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashSet};
pub const STAND_KEY: &str = "isla_stand";
pub const GANTRY: &str = "isla_gantry";
pub const VIRTUAL_BODY: &str = "isla_virtual_mass";
pub struct Built {
    pub model: Model,
    pub info: Value,
    pub cad: Value,
    pub(crate) spec: Spec,
}
impl Built {
    pub fn to_xml(&self) -> Result<String> {
        self.spec.xml()
    }
}
pub fn axis(label: &str) -> [f64; 3] {
    match label {
        "-x" => [-1., 0., 0.],
        "+y" => [0., 1., 0.],
        "-y" => [0., -1., 0.],
        "+z" => [0., 0., 1.],
        "-z" => [0., 0., -1.],
        _ => [1., 0., 0.],
    }
}
fn body(model: &Model, name: &str) -> Result<usize> {
    model.id(ffi::mjtObj_mjOBJ_BODY as i32, name)
}
fn joint(model: &Model, name: &str) -> Result<usize> {
    model.id(ffi::mjtObj_mjOBJ_JOINT as i32, name)
}
pub fn measure(profile: &RobotProfile) -> Result<Value> {
    let model = Model::load(&profile.source_path()?)?;
    let mut data = model.data()?;
    data.forward(&model);
    measure_model(profile, &model, &data)
}
fn measure_model(profile: &RobotProfile, model: &Model, data: &Data) -> Result<Value> {
    unsafe {
        let m = model.raw();
        let d = data.raw();
        let mut result = json!({"cad_mass":model.total_mass()});
        let mut links: BTreeMap<usize, Vec<usize>> = BTreeMap::new();
        for b in 1..m.nbody as usize {
            links.entry(g::link_of(model, b)).or_default().push(b);
        }
        let mut best = f64::NEG_INFINITY;
        for b in 1..m.nbody as usize {
            for p in g::body_points(model, data, b) {
                if p.z > best {
                    best = p.z;
                    result["top"] = json!({"body":model.name(1,b),"local":g::out(g::to_local(data,b,p)),"cad_z":best});
                }
            }
        }
        result["links"] = json!(links
            .iter()
            .map(|(b, members)| (
                model.name(1, *b),
                members
                    .iter()
                    .map(|b| model.name(1, *b))
                    .collect::<Vec<_>>()
            ))
            .collect::<BTreeMap<_, _>>());
        if let Some(torso) = profile.data["torso"].as_str() {
            let b = body(model, torso)?;
            let members = &links[&g::link_of(model, b)];
            let mass = members.iter().map(|b| *m.body_mass.add(*b)).sum::<f64>();
            let com = members.iter().fold(g::V3::zeros(), |a, b| {
                a + v3(d.xipos, *b) * *m.body_mass.add(*b)
            }) / mass.max(1e-9);
            let parent = body(
                model,
                profile.data["mass"]["virtual_body"]
                    .as_str()
                    .unwrap_or(torso),
            )?;
            let (p, q) = g::local_pose(data, parent, com, UnitQuaternion::identity());
            result["torso_com"] = json!(g::out(com));
            result["torso_mass"] = json!(mass);
            result["virtual_pos"] = json!(p);
            result["virtual_quat"] = json!(q);
        }
        let mut feet = vec![];
        for foot in array(&profile.data["feet"]) {
            let link = body(model, string(foot, "body"))?;
            let sole = body(model, foot["sole"].as_str().unwrap_or(string(foot, "body")))?;
            let mut points = g::body_points(model, data, sole);
            if points.is_empty() {
                for b in &links[&link] {
                    points.extend(g::body_points(model, data, *b));
                }
            }
            let (lo, hi) = g::aabb(&points).context("Foot has no geometry")?;
            let (pos, q) = g::local_pose(data, link, (lo + hi) / 2., UnitQuaternion::identity());
            feet.push(json!({"side":foot["side"],"body":foot["body"],"pos":pos,"quat":q,"half":g::out((hi-lo)/2.),"bottom":lo.z}));
        }
        result["feet"] = json!(feet);
        let mut struts = vec![];
        for item in array(&profile.data["struts"]["items"]) {
            let upper = body(model, string(item, "upper"))?;
            let lower = body(model, string(item, "lower"))?;
            let top = g::body_center(model, data, upper);
            let bottom = g::body_center(model, data, lower);
            struts.push(json!({"name":item["name"],"position":item["position"],"side":item["side"],"upper_body":item["upper"],"lower_body":item["lower"],"upper_local":g::out(g::to_local(data,upper,top)),"lower_local":g::out(g::to_local(data,lower,bottom)),"length_cad":(top-bottom).norm(),"upper_world":g::out(top),"lower_world":g::out(bottom)}));
        }
        result["struts"] = json!(struts);
        let mut rods = vec![];
        for item in array(&profile.data["rods"]["items"]) {
            let start = joint(model, string(item, "from_joint"))?;
            let end = joint(model, string(item, "to_joint"))?;
            let axis = v3(d.xaxis, start);
            let span = v3(d.xanchor, end) - v3(d.xanchor, start);
            let span = span - axis * span.dot(&axis);
            let length = span.norm();
            if length < 1e-9 {
                bail!("Rod {} has zero span", item["name"])
            }
            let direction = span / length;
            let moved = body(model, string(item, "moved"))?;
            let parent = *m.body_parentid.add(moved) as usize;
            let mut pieces = vec![];
            for rod_name in array(&item["rods"]) {
                let name = rod_name.as_str().context("Rod name")?;
                let b = body(model, name)?;
                let points = g::body_points(model, data, b);
                if points.is_empty() {
                    bail!("Rod {name} has no geometry")
                };
                let (mean, frame) = g::principal(&points);
                let mut axis = frame.column(0).into_owned();
                if axis.dot(&direction) < 0. {
                    axis = -axis;
                }
                let projections = points
                    .iter()
                    .map(|p| (p - mean).dot(&axis))
                    .collect::<Vec<_>>();
                let lo = projections.iter().copied().fold(f64::INFINITY, f64::min);
                let hi = projections
                    .iter()
                    .copied()
                    .fold(f64::NEG_INFINITY, f64::max);
                let mut radial = points
                    .iter()
                    .zip(&projections)
                    .map(|(p, t)| (p - mean - axis * *t).norm())
                    .collect::<Vec<_>>();
                radial.sort_by(f64::total_cmp);
                let at = (radial.len() - 1) as f64 * 0.98;
                let r = radial[at.floor() as usize]
                    + (radial[at.ceil() as usize] - radial[at.floor() as usize]) * at.fract();
                let geoms = (0..m.ngeom as usize)
                    .filter(|g| *m.geom_bodyid.add(*g) == b as i32)
                    .map(|g| model.name(ffi::mjtObj_mjOBJ_GEOM as i32, g))
                    .collect::<Vec<_>>();
                pieces.push(json!({"body":name,"geoms":geoms,"top":g::out(g::to_local(data,b,mean+lo*axis)),"bottom":g::out(g::to_local(data,b,mean+hi*axis)),"direction":g::out(g::dir_to_local(data,b,direction)),"radius":r,"length":hi-lo,"mass":*m.body_mass.add(b)}));
            }
            rods.push(json!({"name":item["name"],"side":item["side"],"moved":item["moved"],"parent":model.name(1,parent),"direction":g::out(g::dir_to_local(data,parent,direction)),"base_pos":a3(m.body_pos,moved),"length_cad":length,"rods":pieces}));
        }
        result["rods"] = json!(rods);
        let foot_links = array(&profile.data["feet"])
            .iter()
            .map(|f| string(f, "body"))
            .collect::<HashSet<_>>();
        let rod_bodies = array(&profile.data["rods"]["items"])
            .iter()
            .flat_map(|r| array(&r["rods"]))
            .filter_map(Value::as_str)
            .collect::<HashSet<_>>();
        let mut boxes = vec![];
        for (link, members) in &links {
            let name = model.name(1, *link);
            if foot_links.contains(name.as_str()) {
                continue;
            }
            let points = members
                .iter()
                .filter(|b| !rod_bodies.contains(model.name(1, **b).as_str()))
                .flat_map(|b| g::body_points(model, data, *b))
                .collect::<Vec<_>>();
            if points.is_empty() {
                continue;
            }
            let (mean, frame) = g::principal(&points);
            let local = points
                .iter()
                .map(|p| frame.transpose() * (p - mean))
                .collect::<Vec<_>>();
            let (lo, hi) = g::aabb(&local).unwrap();
            let center = mean + frame * (lo + hi) / 2.;
            let (pos, q) = g::local_pose(data, *link, center, UnitQuaternion::from_matrix(&frame));
            boxes.push(json!({"body":name,"pos":pos,"quat":q,"half":g::out((hi-lo)/2.)}));
        }
        result["link_boxes"] = json!(boxes);
        Ok(result)
    }
}

pub fn strut_values(profile: &RobotProfile, design: &Value, position: &str) -> (f64, f64, f64) {
    let (mut k, mut c, mut preload) = (12000., 150., 0.003);
    for (key, p) in object(&profile.data["design"]) {
        if string(p, "position") != position {
            continue;
        }
        let value = number(&design[key], 0.);
        match string(p, "kind") {
            "strut_k" => k = value * 1000.,
            "strut_c" => c = value,
            "strut_preload" => preload = value / 1000.,
            _ => {}
        }
    }
    (k, c, preload)
}

pub fn build(profile: &RobotProfile, design: Option<&Value>, purpose: &str) -> Result<Built> {
    unsafe {
        if purpose != "display" && purpose != "train" {
            bail!("purpose is display or train")
        };
        profile.validate()?;
        let design = profile.complete_design(design);
        let mut spec = Spec::load(&profile.source_path()?)?;
        let original = spec.compile()?;
        let mut original_data = original.data()?;
        original_data.forward(&original);
        let cad = measure_model(profile, &original, &original_data)?;
        spec.set_string(spec.raw().modelname, profile.id())?;
        spec.raw_mut().option.timestep = 0.002;
        spec.raw_mut().option.integrator = ffi::mjtIntegrator_mjINT_IMPLICITFAST as i32;
        for e in spec.elements(ffi::mjtObj_mjOBJ_BODY) {
            let b = spec.api.mjs_asBody(e);
            let name = spec.name(e);
            if name == "world" || (*b).explicitinertial {
                continue;
            }
            let id = body(&original, &name)?;
            let m = original.raw();
            (*b).explicitinertial = true;
            (*b).mass = *m.body_mass.add(id);
            (*b).ipos = a3(m.body_ipos, id);
            (*b).iquat = a4(m.body_iquat, id);
            (*b).inertia = a3(m.body_inertia, id);
            (*b).fullinertia = [f64::NAN, 0., 0., 0., 0., 0.];
        }
        spec.raw_mut().compiler.inertiafromgeom = ffi::mjtInertiaFromGeom_mjINERTIAFROMGEOM_FALSE;
        let base = profile.data["base"]
            .as_str()
            .map(str::to_owned)
            .unwrap_or_else(|| original.name(1, 1));
        let base_ptr = spec.body(&base)?;
        let has_free = spec
            .elements(ffi::mjtObj_mjOBJ_JOINT)
            .iter()
            .any(|e| (*spec.api.mjs_asJoint(*e)).type_ == ffi::mjtJoint_mjJNT_FREE);
        if !has_free {
            let j = spec.api.mjs_addFreeJoint(base_ptr);
            spec.set_name((*j).element, &format!("{base}_free"))?;
            for e in spec.elements(ffi::mjtObj_mjOBJ_KEY) {
                let key = spec.api.mjs_asKey(e);
                let mut n = 0;
                let ptr = spec.api.mjs_getDouble((*key).qpos, &mut n);
                if n > 0 {
                    let mut values = vec![0., 0., 0., 1., 0., 0., 0.];
                    values.extend_from_slice(slice(ptr, n as usize));
                    spec.api
                        .mjs_setDouble((*key).qpos, values.as_ptr(), values.len() as i32);
                }
                let ptr = spec.api.mjs_getDouble((*key).qvel, &mut n);
                if n > 0 {
                    let mut values = vec![0.; 6];
                    values.extend_from_slice(slice(ptr, n as usize));
                    spec.api
                        .mjs_setDouble((*key).qvel, values.as_ptr(), values.len() as i32);
                }
            }
        }
        // Replace collision copies by measured boxes while preserving all visual geometry.
        let mut floor = false;
        let geoms = spec.elements(ffi::mjtObj_mjOBJ_GEOM);
        let mut has_visual = HashSet::new();
        for e in &geoms {
            let geom = spec.api.mjs_asGeom(*e);
            if (*geom).group < 3
                && ((*geom).rgba[3] >= 0.01
                    || !crate::native::text(spec.api.mjs_getString((*geom).material)).is_empty())
                && (*geom).contype == 0
                && (*geom).conaffinity == 0
            {
                has_visual.insert(spec.api.mjs_getParent(*e) as usize);
            }
        }
        for e in geoms {
            let geom = spec.api.mjs_asGeom(e);
            if (*geom).type_ == ffi::mjtGeom_mjGEOM_PLANE && !floor {
                spec.set_name(e, "floor")?;
                (*geom).contype = 1;
                (*geom).conaffinity = 0;
                (*geom).margin = 0.;
                floor = true;
                continue;
            }
            if (*geom).contype == 0 && (*geom).conaffinity == 0 {
                continue;
            }
            let drawn = (*geom).group < 3
                && ((*geom).rgba[3] >= 0.01
                    || !crate::native::text(spec.api.mjs_getString((*geom).material)).is_empty());
            if !drawn
                || has_visual.contains(&(spec.api.mjs_getParent(e) as usize))
                || (*geom).type_ == ffi::mjtGeom_mjGEOM_PLANE
            {
                spec.delete(e)?;
            } else {
                (*geom).contype = 0;
                (*geom).conaffinity = 0;
            }
        }
        if !floor {
            let f = spec.api.mjs_addGeom(spec.body("world")?, std::ptr::null());
            spec.set_name((*f).element, "floor")?;
            (*f).type_ = ffi::mjtGeom_mjGEOM_PLANE;
            (*f).size = [0., 0., 0.1];
            (*f).margin = 0.;
            (*f).contype = 1;
            (*f).conaffinity = 0;
        }
        if purpose == "display" {
            for bx in array(&cad["link_boxes"]) {
                let f = spec
                    .api
                    .mjs_addGeom(spec.body(string(bx, "body"))?, std::ptr::null());
                spec.set_name((*f).element, &format!("isla_box_{}", string(bx, "body")))?;
                (*f).type_ = ffi::mjtGeom_mjGEOM_BOX;
                (*f).pos = arr(&bx["pos"], [0.; 3]);
                (*f).quat = arr(&bx["quat"], [1., 0., 0., 0.]);
                (*f).size = arr(&bx["half"], [0.001; 3]).map(|v| v.max(1e-6));
                (*f).group = 3;
                (*f).margin = 0.;
                (*f).rgba = [1., 1., 1., 0.];
                (*f).contype = 0;
                (*f).conaffinity = 1;
            }
        }
        let virtual_mass = (number(&profile.data["mass"]["target_total"], 0.)
            - number(&cad["cad_mass"], 0.))
        .max(0.);
        if virtual_mass > 1e-6 && !cad["torso_com"].is_null() {
            let parent = profile.data["mass"]["virtual_body"]
                .as_str()
                .unwrap_or(string(&profile.data, "torso"));
            let b = spec.api.mjs_addBody(spec.body(parent)?, std::ptr::null());
            spec.set_name((*b).element, VIRTUAL_BODY)?;
            (*b).pos = arr(&cad["virtual_pos"], [0.; 3]);
            (*b).quat = arr(&cad["virtual_quat"], [1., 0., 0., 0.]);
            (*b).mass = virtual_mass;
            (*b).explicitinertial = true;
            (*b).ipos = [0.; 3];
            (*b).inertia = g::box_inertia(
                virtual_mass,
                arr(&profile.data["mass"]["virtual_box"], [0.2, 0.12, 0.2]),
            );
        }
        let mut feet = vec![];
        for foot in array(&cad["feet"]) {
            let side = string(foot, "side");
            let body = spec.body(string(foot, "body"))?;
            let pos = arr(&foot["pos"], [0.; 3]);
            let q = arr(&foot["quat"], [1., 0., 0., 0.]);
            let mut half = arr(&foot["half"], [0.01; 3]);
            half[2] = half[2].max(0.004);
            let name = format!("isla_sole_{side}");
            let touch = format!("isla_touch_{side}");
            let f = spec.api.mjs_addGeom(body, std::ptr::null());
            spec.set_name((*f).element, &name)?;
            (*f).type_ = ffi::mjtGeom_mjGEOM_BOX;
            (*f).pos = pos;
            (*f).quat = q;
            (*f).size = half;
            (*f).friction = [1., 0.005, 0.0001];
            (*f).condim = 3;
            (*f).margin = 0.;
            (*f).group = 3;
            (*f).rgba = [1., 1., 1., 0.];
            (*f).contype = 2;
            (*f).conaffinity = 3;
            let site = spec.api.mjs_addSite(body, std::ptr::null());
            spec.set_name((*site).element, &name)?;
            (*site).pos = g::out(g::vec(pos) - g::quat(q) * g::V3::new(0., 0., half[2]));
            (*site).quat = q;
            (*site).size = [0.005; 3];
            let site = spec.api.mjs_addSite(body, std::ptr::null());
            spec.set_name((*site).element, &touch)?;
            (*site).type_ = ffi::mjtGeom_mjGEOM_BOX;
            (*site).pos = pos;
            (*site).quat = q;
            (*site).size = [half[0] + 0.003, half[1] + 0.003, half[2] + 0.006];
            (*site).rgba = [1., 1., 1., 0.];
            for (kind, sensor, target) in [
                (ffi::mjtSensor_mjSENS_TOUCH, touch.clone(), touch.clone()),
                (
                    ffi::mjtSensor_mjSENS_FRAMEPOS,
                    format!("{name}_pos"),
                    name.clone(),
                ),
                (
                    ffi::mjtSensor_mjSENS_FRAMELINVEL,
                    format!("{name}_vel"),
                    name.clone(),
                ),
            ] {
                let s = spec.api.mjs_addSensor(spec.ptr.as_ptr());
                spec.set_name((*s).element, &sensor)?;
                (*s).type_ = kind;
                (*s).objtype = ffi::mjtObj_mjOBJ_SITE;
                spec.set_string((*s).objname, &target)?;
            }
            feet.push(json!({"side":side,"body":foot["body"],"geom":name,"site":name,"touch":touch,"half":half}));
        }
        let mut rods = vec![];
        for item in array(&cad["rods"]) {
            let extension = object(&profile.data["design"])
                .find(|(_, p)| string(p, "kind") == "rod" && p["rod"] == item["name"])
                .map(|(k, _)| number(&design[k], 0.) / 1000.)
                .unwrap_or(0.);
            let direction = g::vec(arr(&item["direction"], [0.; 3]));
            let base_pos = g::vec(arr(&item["base_pos"], [0.; 3]));
            (*spec.body(string(item, "moved"))?).pos = g::out(base_pos + extension * direction);
            let mut pieces = vec![];
            for piece in array(&item["rods"]) {
                let b = spec.body(string(piece, "body"))?;
                for name in array(&piece["geoms"]) {
                    if let Ok(e) = spec.find(ffi::mjtObj_mjOBJ_GEOM, name.as_str().unwrap_or("")) {
                        spec.delete(e)?;
                    }
                }
                let top = g::vec(arr(&piece["top"], [0.; 3]));
                let bottom = g::vec(arr(&piece["bottom"], [0.; 3]))
                    + extension * g::vec(arr(&piece["direction"], [0.; 3]));
                let length = (bottom - top).norm();
                let mass =
                    number(&piece["mass"], 0.) * length / number(&piece["length"], 1e-6).max(1e-6);
                (*b).mass = mass;
                (*b).explicitinertial = true;
                (*b).fullinertia = [f64::NAN, 0., 0., 0., 0., 0.];
                (*b).ipos = g::out((top + bottom) / 2.);
                (*b).iquat = g::quat_from_z(bottom - top);
                (*b).inertia = g::cylinder_inertia(mass, number(&piece["radius"], 0.01), length);
                if purpose == "display" {
                    let geom = spec.api.mjs_addGeom(b, std::ptr::null());
                    spec.set_name((*geom).element, &format!("{}__rod", string(piece, "body")))?;
                    (*geom).type_ = ffi::mjtGeom_mjGEOM_CYLINDER;
                    (*geom).fromto = [top.x, top.y, top.z, bottom.x, bottom.y, bottom.z];
                    (*geom).size = [number(&piece["radius"], 0.01), 0., 0.];
                    (*geom).contype = 0;
                    (*geom).conaffinity = 0;
                    (*geom).group = 2;
                    if spec
                        .find(ffi::mjtObj_mjOBJ_MATERIAL, "carbon_fiber_dark_gray")
                        .is_ok()
                    {
                        spec.set_string((*geom).material, "carbon_fiber_dark_gray")?;
                    }
                }
                let mut p = piece.clone();
                p["length_now"] = json!(length);
                p["bottom_now"] = json!(g::out(bottom));
                pieces.push(p);
            }
            rods.push(json!({"name":item["name"],"side":item["side"],"moved_body":item["moved"],"parent_body":item["parent"],"direction":item["direction"],"base_pos":item["base_pos"],"extension":extension,"length_cad":item["length_cad"],"rods":pieces}));
        }
        let mut struts = vec![];
        let half_stroke = number(&profile.data["struts"]["stroke_mm"], 24.) / 2000.;
        for item in array(&cad["struts"]) {
            let name = format!("isla_strut_{}", string(item, "name"));
            let upper = format!("{name}_upper");
            let lower = format!("{name}_lower");
            for (label, b, p) in [
                (&upper, "upper_body", "upper_local"),
                (&lower, "lower_body", "lower_local"),
            ] {
                let site = spec
                    .api
                    .mjs_addSite(spec.body(string(item, b))?, std::ptr::null());
                spec.set_name((*site).element, label)?;
                (*site).pos = arr(&item[p], [0.; 3]);
                (*site).size = [0.004, 0., 0.];
            }
            let (k, c, preload) = strut_values(profile, &design, string(item, "position"));
            let length = number(&item["length_cad"], 0.);
            let (lo, hi) = (length - half_stroke, length + half_stroke);
            let free = hi + preload;
            let tendon = spec.api.mjs_addTendon(spec.ptr.as_ptr(), std::ptr::null());
            spec.set_name((*tendon).element, &name)?;
            (*tendon).stiffness = [k, 0., 0.];
            (*tendon).damping = [c, 0., 0.];
            (*tendon).springlength = [free, free];
            (*tendon).limited = ffi::mjtLimited_mjLIMITED_TRUE;
            (*tendon).range = [lo, hi];
            (*tendon).width = 0.005;
            (*tendon).rgba = [0.95, 0.95, 0.95, 1.];
            for site in [&upper, &lower] {
                let c = crate::native::cstring(site)?;
                spec.api.mjs_wrapSite(tendon, c.as_ptr());
            }
            let sensor = spec.api.mjs_addSensor(spec.ptr.as_ptr());
            spec.set_name((*sensor).element, &format!("{name}_length"))?;
            (*sensor).type_ = ffi::mjtSensor_mjSENS_TENDONPOS;
            (*sensor).objtype = ffi::mjtObj_mjOBJ_TENDON;
            spec.set_string((*sensor).objname, &name)?;
            struts.push(json!({"name":item["name"],"position":item["position"],"side":item["side"],"tendon":name,"upper_site":upper,"lower_site":lower,"length_cad":length,"length_min":lo,"length_max":hi,"stiffness":k,"damping":c,"free_length":free}));
        }
        let motors = motors(&spec, profile)?;
        for e in spec.elements(ffi::mjtObj_mjOBJ_KEY) {
            let key = spec.api.mjs_asKey(e);
            spec.api.mjs_setDouble((*key).ctrl, std::ptr::null(), 0);
            spec.api.mjs_setDouble((*key).act, std::ptr::null(), 0);
        }
        if purpose == "train" {
            for e in spec.elements(ffi::mjtObj_mjOBJ_GEOM) {
                let geom = spec.api.mjs_asGeom(e);
                if (*geom).contype == 0 && (*geom).conaffinity == 0 {
                    spec.delete(e)?;
                }
            }
            for kind in [ffi::mjtObj_mjOBJ_MESH, ffi::mjtObj_mjOBJ_LIGHT] {
                for e in spec.elements(kind) {
                    spec.delete(e)?;
                }
            }
        } else {
            let b = spec.api.mjs_addBody(spec.body("world")?, std::ptr::null());
            spec.set_name((*b).element, GANTRY)?;
            (*b).mocap = true;
            let eq = spec
                .api
                .mjs_addEquality(spec.ptr.as_ptr(), std::ptr::null());
            spec.set_name((*eq).element, GANTRY)?;
            (*eq).type_ = ffi::mjtEq_mjEQ_WELD;
            (*eq).objtype = ffi::mjtObj_mjOBJ_BODY;
            spec.set_string((*eq).name1, &base)?;
            spec.set_string((*eq).name2, GANTRY)?;
            (*eq).data = [0., 0., 0., 0., 0., 0., 1., 0., 0., 0., 1.];
            (*eq).solref = [0.05, 1.];
            (*eq).active = false;
        }
        if spec.find(ffi::mjtObj_mjOBJ_KEY, STAND_KEY).is_err() {
            let key = spec.api.mjs_addKey(spec.ptr.as_ptr());
            spec.set_name((*key).element, STAND_KEY)?;
        }
        let mut model = spec.compile()?;
        let listed = motors
            .iter()
            .map(|m| string(m, "joint"))
            .collect::<HashSet<_>>();
        let passive = (0..model.raw().njnt as usize)
            .filter(|j| [2, 3].contains(&*model.raw().jnt_type.add(*j)))
            .map(|j| model.name(3, j))
            .filter(|j| !listed.contains(j.as_str()))
            .collect::<Vec<_>>();
        let mut info = json!({"robot":profile.id(),"name":profile.name(),"purpose":purpose,"design":design,"base":base,"torso":profile.data["torso"],"imu_site":profile.data["imu_site"],"forward":axis(profile.data["frame"]["forward"].as_str().unwrap_or("+x")),"left":axis(profile.data["frame"]["left"].as_str().unwrap_or("+y")),"feet":feet,"rods":rods,"struts":struts,"motors":motors,"passive":passive,"cad_mass":cad["cad_mass"],"virtual_mass":virtual_mass,"total_mass":model.total_mass()});
        let pose = solve_stand(&model, profile, &info)?;
        let key = model.id(ffi::mjtObj_mjOBJ_KEY as i32, STAND_KEY)?;
        let nq = model.raw().nq as usize;
        let nu = model.raw().nu as usize;
        slice_mut(model.raw_mut().key_qpos.add(key * nq), nq).copy_from_slice(&pose.qpos);
        slice_mut(model.raw_mut().key_ctrl.add(key * nu), nu).copy_from_slice(&pose.ctrl);
        let e = spec.find(ffi::mjtObj_mjOBJ_KEY, STAND_KEY)?;
        let k = spec.api.mjs_asKey(e);
        spec.api
            .mjs_setDouble((*k).qpos, pose.qpos.as_ptr(), pose.qpos.len() as i32);
        spec.api
            .mjs_setDouble((*k).ctrl, pose.ctrl.as_ptr(), pose.ctrl.len() as i32);
        let mut data = model.data()?;
        data.qpos_mut().copy_from_slice(&pose.qpos);
        data.forward(&model);
        info["stand_qpos"] = json!(pose.qpos);
        info["stand_ctrl"] = json!(pose.ctrl);
        info["stand_pitch"] = json!(pose.pitch);
        info["hip_height"] = json!(pose.hip_height);
        info["com_height"] = json!(v3(data.raw().subtree_com, body(&model, &base)?).z);
        info["height"] = json!(top_height(&model, &data, &cad)?);
        let mut sides = array(&info["rods"])
            .iter()
            .map(|r| string(r, "side"))
            .collect::<Vec<_>>();
        sides.sort();
        info["leg_length"] = json!(if let Some(side) = sides.first() {
            array(&info["rods"])
                .iter()
                .filter(|r| string(r, "side") == *side)
                .map(|r| number(&r["length_cad"], 0.) + number(&r["extension"], 0.))
                .sum::<f64>()
        } else {
            pose.hip_height
        });
        Ok(Built {
            model,
            info,
            cad,
            spec,
        })
    }
}

unsafe fn motors(spec: &Spec, profile: &RobotProfile) -> Result<Vec<Value>> {
    let mut by_joint = BTreeMap::new();
    for e in spec.elements(ffi::mjtObj_mjOBJ_ACTUATOR) {
        let a = spec.api.mjs_asActuator(e);
        by_joint.insert(crate::native::text(spec.api.mjs_getString((*a).target)), a);
    }
    let mut out = vec![];
    let mut keep = HashSet::new();
    for motor in array(&profile.data["motors"]) {
        let name = string(motor, "joint");
        let kind = &profile.data["actuator_types"][string(motor, "type")];
        let j = spec
            .api
            .mjs_asJoint(spec.find(ffi::mjtObj_mjOBJ_JOINT, name)?);
        let a = if let Some(a) = by_joint.get(name) {
            *a
        } else {
            let a = spec
                .api
                .mjs_addActuator(spec.ptr.as_ptr(), std::ptr::null());
            spec.set_name((*a).element, &format!("{name}_{}", string(motor, "type")))?;
            spec.set_string((*a).target, name)?;
            (*a).trntype = ffi::mjtTrn_mjTRN_JOINT;
            a
        };
        let kp = number(&kind["kp"], 50.);
        let mut kd = number(&kind["kd"], 2.);
        let peak = number(&kind["peak_torque"], 20.);
        let error = spec.api.mjs_setToPosition(
            a,
            kp,
            &mut kd,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            0.,
        );
        if !error.is_null() && !crate::native::text(error).is_empty() {
            bail!("{}", crate::native::text(error))
        };
        (*a).forcelimited = ffi::mjtLimited_mjLIMITED_TRUE;
        (*a).forcerange = [-peak, peak];
        let range = if profile.data["operating_ranges"][name].is_array() {
            arr(
                &profile.data["operating_ranges"][name],
                [-std::f64::consts::PI, std::f64::consts::PI],
            )
        } else if (*j).range[0] < (*j).range[1] {
            (*j).range
        } else {
            [-std::f64::consts::PI, std::f64::consts::PI]
        };
        (*a).ctrllimited = ffi::mjtLimited_mjLIMITED_TRUE;
        (*a).ctrlrange = range;
        for key in ["armature", "damping", "frictionloss"] {
            if let Some(n) = kind[key].as_f64() {
                match key {
                    "armature" => (*j).armature = n,
                    "damping" => (*j).damping = [n, 0., 0.],
                    _ => (*j).frictionloss = n,
                }
            }
        }
        let actuator = spec.name((*a).element);
        keep.insert(actuator.clone());
        let mut m = motor.clone();
        m["actuator"] = json!(actuator);
        m["kp"] = json!(kp);
        m["kd"] = json!(kd);
        m["peak_torque"] = json!(peak);
        m["low"] = json!(range[0]);
        m["high"] = json!(range[1]);
        out.push(m);
    }
    for e in spec.elements(ffi::mjtObj_mjOBJ_ACTUATOR) {
        if !keep.contains(&spec.name(e)) {
            spec.delete(e)?;
        }
    }
    Ok(out)
}

pub struct StandPose {
    pub qpos: Vec<f64>,
    pub ctrl: Vec<f64>,
    pub pitch: f64,
    pub hip_height: f64,
    pub gravity: [f64; 3],
}
pub fn solve_stand(model: &Model, profile: &RobotProfile, info: &Value) -> Result<StandPose> {
    unsafe {
        let m = model.raw();
        let mut data = model.data()?;
        let stand = &profile.data["stand"];
        let base = body(model, string(info, "base"))?;
        let free = (0..m.njnt as usize)
            .find(|j| *m.jnt_bodyid.add(*j) == base as i32 && *m.jnt_type.add(*j) == 0)
            .context("Base has no free joint")?;
        let adr = *m.jnt_qposadr.add(free) as usize;
        let mut qpos = slice(m.qpos0, m.nq as usize).to_vec();
        let keyframe = stand["keyframe"].as_str();
        if let Some(key) = keyframe.and_then(|k| model.id(ffi::mjtObj_mjOBJ_KEY as i32, k).ok()) {
            qpos.copy_from_slice(slice(m.key_qpos.add(key * m.nq as usize), m.nq as usize));
        }
        qpos[adr] = 0.;
        qpos[adr + 1] = 0.;
        if keyframe.is_none() {
            qpos[adr + 2] = 0.;
            qpos[adr + 3..adr + 7].copy_from_slice(&[1., 0., 0., 0.]);
        }
        let hips = array(&stand["hips"])
            .iter()
            .filter_map(Value::as_str)
            .collect::<Vec<_>>();
        let knees = array(&stand["knees"])
            .iter()
            .filter_map(Value::as_str)
            .collect::<Vec<_>>();
        let ankles = array(&stand["ankles"])
            .iter()
            .filter_map(Value::as_str)
            .collect::<Vec<_>>();
        let forward = g::vec(arr(&info["forward"], [1., 0., 0.]));
        let lean = g::V3::z().cross(&forward);
        data.forward(model);
        let mut pitch = BTreeMap::new();
        for name in hips.iter().chain(&knees).chain(&ankles) {
            pitch.insert(*name, v3(data.raw().xaxis, joint(model, name)?).dot(&lean));
        }
        let pose = |p: f64, qpos: &mut Vec<f64>, data: &mut Data| -> Result<()> {
            qpos[adr + 3..adr + 7].copy_from_slice(&[
                (p / 2.).cos(),
                (p / 2.).sin() * lean.x,
                (p / 2.).sin() * lean.y,
                (p / 2.).sin() * lean.z,
            ]);
            for ((hip, knee), ankle) in hips.iter().zip(&knees).zip(&ankles) {
                let sign = number(&stand["flexion_sign"][*knee], 1.);
                let knee_angle = sign * number(&stand["knee_flexion"], 0.);
                let ankle_angle = number(&stand["flexion_sign"][*ankle], -sign)
                    * number(&stand["ankle_offset"], 0.);
                qpos[*m.jnt_qposadr.add(joint(model, knee)?) as usize] = knee_angle;
                qpos[*m.jnt_qposadr.add(joint(model, ankle)?) as usize] = ankle_angle;
                let rest = p + pitch[knee] * knee_angle + pitch[ankle] * ankle_angle;
                let divisor = if pitch[hip] == 0. { 1. } else { pitch[hip] };
                qpos[*m.jnt_qposadr.add(joint(model, hip)?) as usize] = -rest / divisor;
            }
            data.qpos_mut().copy_from_slice(qpos);
            data.forward(model);
            Ok(())
        };
        let mut body_pitch = 0.;
        if !hips.is_empty() && !knees.is_empty() && !ankles.is_empty() {
            pose(0., &mut qpos, &mut data)?;
            let weight = model.total_mass() * g::vec(m.opt.gravity).norm();
            let toe = ankles
                .iter()
                .map(|a| {
                    let j = joint(model, a).unwrap();
                    *data.raw().qfrc_passive.add(*m.jnt_dofadr.add(j) as usize)
                        * v3(data.raw().xaxis, j).dot(&lean)
                })
                .sum::<f64>();
            let offset = toe / weight.max(1e-9);
            let eval = |p: f64, qpos: &mut Vec<f64>, data: &mut Data| -> Result<f64> {
                pose(p, qpos, data)?;
                let middle = ankles
                    .iter()
                    .map(|a| v3(data.raw().xanchor, joint(model, a).unwrap()))
                    .fold(g::V3::zeros(), |a, b| a + b)
                    / ankles.len() as f64;
                Ok((v3(data.raw().subtree_com, base) - middle).dot(&forward) - offset)
            };
            let (mut lo, mut hi) = (-0.5, 0.8);
            let mut flo = eval(lo, &mut qpos, &mut data)?;
            let fhi = eval(hi, &mut qpos, &mut data)?;
            if flo * fhi < 0. {
                for _ in 0..50 {
                    let mid = (lo + hi) / 2.;
                    let f = eval(mid, &mut qpos, &mut data)?;
                    if flo * f <= 0. {
                        hi = mid
                    } else {
                        lo = mid;
                        flo = f;
                    }
                }
                body_pitch = (lo + hi) / 2.;
            }
            pose(body_pitch, &mut qpos, &mut data)?;
        } else {
            data.qpos_mut().copy_from_slice(&qpos);
            data.forward(model);
        }
        let mut lowest = f64::INFINITY;
        for foot in array(&info["feet"]) {
            let id = model.id(ffi::mjtObj_mjOBJ_GEOM as i32, string(foot, "geom"))?;
            let r = g::mat(data.raw().geom_xmat, id);
            let half = v3(m.geom_size, id);
            lowest = lowest.min(
                v3(data.raw().geom_xpos, id).z
                    - r.row(2)
                        .iter()
                        .zip(half.iter())
                        .map(|(a, b)| a.abs() * b)
                        .sum::<f64>(),
            );
        }
        if lowest.is_finite() {
            qpos[adr + 2] -= lowest;
        }
        data.qpos_mut().copy_from_slice(&qpos);
        data.forward(model);
        let ctrl = (0..m.nu as usize)
            .map(|a| {
                let j = *m.actuator_trnid.add(a * 2) as usize;
                let q = qpos[*m.jnt_qposadr.add(j) as usize];
                if *m.actuator_ctrllimited.add(a) {
                    q.clamp(
                        *m.actuator_ctrlrange.add(a * 2),
                        *m.actuator_ctrlrange.add(a * 2 + 1),
                    )
                } else {
                    q
                }
            })
            .collect();
        let hip_height = if hips.is_empty() {
            v3(data.raw().xipos, base).z
        } else {
            hips.iter()
                .map(|h| v3(data.raw().xanchor, joint(model, h).unwrap()).z)
                .sum::<f64>()
                / hips.len() as f64
        };
        let gravity = g::out(g::mat(data.raw().xmat, base).transpose() * g::V3::new(0., 0., -1.));
        Ok(StandPose {
            qpos,
            ctrl,
            pitch: body_pitch,
            hip_height,
            gravity,
        })
    }
}
pub fn top_height(model: &Model, data: &Data, cad: &Value) -> Result<f64> {
    if cad["top"].is_null() {
        return Ok(0.);
    }
    let b = body(model, string(&cad["top"], "body"))?;
    Ok(unsafe {
        (v3(data.raw().xpos, b)
            + g::mat(data.raw().xmat, b) * g::vec(arr(&cad["top"]["local"], [0.; 3])))
        .z
    })
}
