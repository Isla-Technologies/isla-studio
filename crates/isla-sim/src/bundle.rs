use crate::{
    ffi, geometry as g,
    native::{a3, a4, slice},
    profile::{array, number, string},
    session::stand_drift,
    Built, RobotProfile,
};
use anyhow::Result;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

fn label(name: &str) -> String {
    let mut text = name.replace(['-', '_'], " ");
    if text
        .rsplit_once(' ')
        .is_some_and(|(_, n)| n.parse::<usize>().is_ok())
    {
        text = text.rsplit_once(' ').unwrap().0.into();
    }
    let mut chars = text.chars();
    chars
        .next()
        .map(|c| c.to_uppercase().collect::<String>() + chars.as_str())
        .unwrap_or_default()
}
fn hull(mut points: Vec<[f64; 2]>) -> Vec<[f64; 2]> {
    points.sort_by(|a, b| a[0].total_cmp(&b[0]).then(a[1].total_cmp(&b[1])));
    points.dedup();
    if points.len() < 3 {
        return points;
    }
    let half = |points: Vec<[f64; 2]>| {
        let mut chain: Vec<[f64; 2]> = Vec::new();
        for p in points {
            while chain.len() >= 2 {
                let a = chain[chain.len() - 2];
                let b = chain[chain.len() - 1];
                if (b[0] - a[0]) * (p[1] - b[1]) - (b[1] - a[1]) * (p[0] - b[0]) > 0. {
                    break;
                }
                chain.pop();
            }
            chain.push(p);
        }
        chain.pop();
        chain
    };
    let mut lower = half(points.clone());
    points.reverse();
    lower.extend(half(points));
    lower
}
impl Built {
    pub fn meta(&self, profile: &RobotProfile) -> Result<Value> {
        unsafe {
            let model = &self.model;
            let m = model.raw();
            let info = &self.info;
            let rod_bodies = array(&info["rods"])
                .iter()
                .flat_map(|r| array(&r["rods"]))
                .map(|r| string(r, "body"))
                .collect::<BTreeSet<_>>();
            let bodies=(0..m.nbody as usize).map(|b|{let name=model.name(1,b);let kind=if b==0{"world"}else if rod_bodies.contains(name.as_str()){"rod"}else if name==crate::build::VIRTUAL_BODY{"virtual"}else if *m.body_jntnum.add(b)>0{"link"}else{"part"};json!({"id":b,"name":name,"label":label(&name),"parent":*m.body_parentid.add(b),"pos":a3(m.body_pos,b),"quat":a4(m.body_quat,b),"mass":*m.body_mass.add(b),"link":g::link_of(model,b),"kind":kind,"com":a3(m.body_ipos,b)})}).collect::<Vec<_>>();
            let joints=(0..m.njnt as usize).map(|j|{let name=model.name(3,j);let motor=array(&info["motors"]).iter().find(|motor|motor["joint"]==name).map(|motor|{let mut value=motor.clone();let kind=&profile.data["actuator_types"][string(motor,"type")];value["label"]=kind["label"].clone();value["driver"]=kind["driver"].clone();value["protocol"]=kind["protocol"].clone();value});json!({"id":j,"name":name,"label":label(&name),"type":*m.jnt_type.add(j),"body":*m.jnt_bodyid.add(j),"qpos":*m.jnt_qposadr.add(j),"dof":*m.jnt_dofadr.add(j),"axis":a3(m.jnt_axis,j),"pos":a3(m.jnt_pos,j),"range":if *m.jnt_limited.add(j){Some(slice(m.jnt_range.add(j*2),2))}else{None},"passive":array(&info["passive"]).iter().any(|p|p==&name),"motor":motor})}).collect::<Vec<_>>();
            let geoms=(0..m.ngeom as usize).filter(|&i|*m.geom_contype.add(i)==0&&*m.geom_conaffinity.add(i)==0&&g::is_drawn(model,i)).map(|i|{let material=*m.geom_matid.add(i);let rgba=if material>=0{slice(m.mat_rgba.add(material as usize*4),4)}else{slice(m.geom_rgba.add(i*4),4)};json!({"id":i,"body":*m.geom_bodyid.add(i),"type":*m.geom_type.add(i),"mesh":if *m.geom_type.add(i)==ffi::mjtGeom_mjGEOM_MESH as i32{*m.geom_dataid.add(i)}else{-1},"pos":a3(m.geom_pos,i),"quat":a4(m.geom_quat,i),"size":a3(m.geom_size,i),"material":if material>=0{model.name(ffi::mjtObj_mjOBJ_MATERIAL as i32,material as usize)}else{String::new()},"rgba":rgba})}).collect::<Vec<_>>();
            let struts = array(&info["struts"])
                .iter()
                .map(|s| {
                    let mut out = s.clone();
                    for (label, key) in [("upper", "upper_site"), ("lower", "lower_site")] {
                        let id = model.id(ffi::mjtObj_mjOBJ_SITE as i32, string(s, key))?;
                        out[label] = json!({"body":*m.site_bodyid.add(id),"pos":a3(m.site_pos,id)});
                    }
                    Ok(out)
                })
                .collect::<Result<Vec<_>>>()?;
            let rods=array(&info["rods"]).iter().map(|r|{let pieces=array(&r["rods"]).iter().map(|p|{let mut out=p.clone();out["body"]=json!(model.id(1,string(p,"body"))?);Ok(out)}).collect::<Result<Vec<_>>>()?;Ok(json!({"name":r["name"],"side":r["side"],"moved":model.id(1,string(r,"moved_body"))?,"direction":r["direction"],"base_pos":r["base_pos"],"segment_cad":r["length_cad"],"rods":pieces}))}).collect::<Result<Vec<_>>>()?;
            let links=(1..m.nbody as usize).filter(|&b|*m.body_jntnum.add(b)>0||*m.body_parentid.add(b)==0).map(|b|{let members=(1..m.nbody as usize).filter(|&member|g::link_of(model,member)==b).collect::<Vec<_>>();let j=(0..m.njnt as usize).find(|&j|*m.jnt_bodyid.add(j)==b as i32);json!({"body":b,"joint":j.map(|v|v as i32).unwrap_or(-1),"label":label(&model.name(1,b)),"mass":members.iter().map(|&i|*m.body_mass.add(i)).sum::<f64>(),"members":members})}).collect::<Vec<_>>();
            let sites = (0..m.nsite as usize)
                .filter_map(|s| {
                    let name = model.name(ffi::mjtObj_mjOBJ_SITE as i32, s);
                    if name.starts_with("isla_sole_") || info["imu_site"] == name {
                        Some((
                            name,
                            json!({"body":*m.site_bodyid.add(s),"pos":a3(m.site_pos,s)}),
                        ))
                    } else {
                        None
                    }
                })
                .collect::<BTreeMap<_, _>>();
            let actuators=(0..m.nu as usize).map(|a|json!({"id":a,"name":model.name(ffi::mjtObj_mjOBJ_ACTUATOR as i32,a),"joint":model.name(3,*m.actuator_trnid.add(a*2) as usize)})).collect::<Vec<_>>();
            Ok(
                json!({"version":4,"robot":profile.to_dict(),"info":info,"nq":m.nq,"nv":m.nv,"nu":m.nu,"qpos0":slice(m.qpos0,m.nq as usize),"stand":info["stand_qpos"],"bodies":bodies,"joints":joints,"geoms":geoms,"struts":struts,"rods":rods,"links":links,"sites":sites,"actuators":actuators}),
            )
        }
    }

    pub fn meshes(&self) -> Result<(Vec<u8>, Value)> {
        unsafe {
            let m = self.model.raw();
            let mut vertices = Vec::<[f32; 3]>::new();
            let mut faces = Vec::<[u32; 3]>::new();
            let mut edges = Vec::<[[f32; 3]; 2]>::new();
            let mut index = Vec::new();
            for mesh in 0..m.nmesh as usize {
                let start = *m.mesh_vertadr.add(mesh) as usize;
                let count = *m.mesh_vertnum.add(mesh) as usize;
                let fstart = *m.mesh_faceadr.add(mesh) as usize;
                let fcount = *m.mesh_facenum.add(mesh) as usize;
                let v = slice(m.mesh_vert.add(start * 3), count * 3)
                    .chunks_exact(3)
                    .map(|v| [v[0], v[1], v[2]])
                    .collect::<Vec<_>>();
                let f = slice(m.mesh_face.add(fstart * 3), fcount * 3)
                    .chunks_exact(3)
                    .map(|f| [f[0] as u32, f[1] as u32, f[2] as u32])
                    .collect::<Vec<_>>();
                let (v, f) = simplify(v, f);
                let mut adjacency: BTreeMap<(u32, u32), Vec<g::V3>> = BTreeMap::new();
                for face in &f {
                    let a = g::V3::from(v[face[0] as usize].map(f64::from));
                    let b = g::V3::from(v[face[1] as usize].map(f64::from));
                    let c = g::V3::from(v[face[2] as usize].map(f64::from));
                    let normal = (b - a).cross(&(c - a));
                    let normal = normal / normal.norm().max(1e-12);
                    for (a, b) in [(face[0], face[1]), (face[1], face[2]), (face[2], face[0])] {
                        adjacency
                            .entry((a.min(b), a.max(b)))
                            .or_default()
                            .push(normal);
                    }
                }
                let lines = adjacency
                    .into_iter()
                    .filter(|(_, n)| n.len() == 1 || n[0].dot(&n[1]) < 28_f64.to_radians().cos())
                    .map(|((a, b), _)| [v[a as usize], v[b as usize]])
                    .collect::<Vec<_>>();
                index.push(json!({"mesh":mesh,"name":self.model.name(ffi::mjtObj_mjOBJ_MESH as i32,mesh),"v":[vertices.len(),v.len()],"f":[faces.len(),f.len()],"e":[edges.len(),lines.len()],"original_faces":fcount}));
                vertices.extend(v);
                faces.extend(f);
                edges.extend(lines);
            }
            let mut blob = Vec::new();
            for n in [vertices.len(), faces.len(), edges.len()] {
                blob.extend_from_slice(&(u32::try_from(n)?).to_le_bytes());
            }
            for p in vertices {
                for n in p {
                    blob.extend_from_slice(&n.to_le_bytes());
                }
            }
            for p in faces {
                for n in p {
                    blob.extend_from_slice(&n.to_le_bytes());
                }
            }
            for line in edges {
                for p in line {
                    for n in p {
                        blob.extend_from_slice(&n.to_le_bytes());
                    }
                }
            }
            Ok((blob, json!(index)))
        }
    }

    pub fn card(&self, profile: &RobotProfile) -> Result<Value> {
        unsafe {
            let model = &self.model;
            let mut data = model.data()?;
            data.qpos_mut()
                .copy_from_slice(&serde_json::from_value::<Vec<f64>>(
                    self.info["stand_qpos"].clone(),
                )?);
            data.forward(model);
            let base = model.id(1, string(&self.info, "base"))?;
            let rotation = g::mat(data.raw().xmat, base);
            let mut forward = rotation
                * g::V3::from(serde_json::from_value::<[f64; 3]>(
                    self.info["forward"].clone(),
                )?);
            forward.z = 0.;
            forward /= forward.norm().max(1e-9);
            let left = g::V3::z().cross(&forward);
            let pos = a3(data.raw().xpos, base);
            let origin = g::V3::new(pos[0], pos[1], 0.);
            let project = |point: g::V3| {
                let p = point - origin;
                [-p.dot(&left), p.z]
            };
            let mut parts = Vec::new();
            let mut bounds = [
                f64::INFINITY,
                f64::INFINITY,
                f64::NEG_INFINITY,
                f64::NEG_INFINITY,
            ];
            for b in 1..model.raw().nbody as usize {
                for geom in g::visual_geoms(model, b) {
                    let points = g::geom_points(model, &data, geom);
                    let outline = hull(points.iter().map(|&p| project(p)).collect());
                    if outline.len() < 3 {
                        continue;
                    }
                    let area = outline
                        .iter()
                        .zip(outline.iter().cycle().skip(1))
                        .take(outline.len())
                        .map(|(a, b)| a[0] * b[1] - a[1] * b[0])
                        .sum::<f64>()
                        .abs()
                        * 0.5;
                    if area < 2e-5 {
                        continue;
                    }
                    for p in &outline {
                        bounds[0] = bounds[0].min(p[0]);
                        bounds[1] = bounds[1].min(p[1]);
                        bounds[2] = bounds[2].max(p[0]);
                        bounds[3] = bounds[3].max(p[1]);
                    }
                    let depth = points
                        .iter()
                        .map(|p| (p - origin).dot(&forward))
                        .sum::<f64>()
                        / points.len() as f64;
                    let outline = if outline.len() > 40 {
                        (0..40)
                            .map(|i| outline[i * (outline.len() - 1) / 39])
                            .collect()
                    } else {
                        outline
                    };
                    parts.push((depth,json!({"link":g::link_of(model,b),"points":outline.into_iter().flatten().collect::<Vec<_>>()})));
                }
            }
            parts.sort_by(|a, b| a.0.total_cmp(&b.0));
            if parts.is_empty() {
                bounds = [0.; 4];
            }
            let motors = array(&self.info["motors"]);
            let mut counts: BTreeMap<String, usize> = BTreeMap::new();
            for motor in motors {
                *counts.entry(string(motor, "type").into()).or_default() += 1;
            }
            let actuators=counts.iter().map(|(key,count)|{let kind=&profile.data["actuator_types"][key];json!({"type":key,"label":kind["label"],"count":count,"peak_torque":kind["peak_torque"],"gear_ratio":kind["gear_ratio"]})}).collect::<Vec<_>>();
            let motor_points = motors
                .iter()
                .map(|motor| {
                    model
                        .id(3, string(motor, "joint"))
                        .map(|j| project(g::V3::from(a3(data.raw().xanchor, j))))
                })
                .collect::<Result<Vec<_>>>()?;
            let drift = stand_drift(self)?;
            Ok(
                json!({"id":profile.id(),"name":profile.name(),"about":profile.data["about"],"imported":profile.data.get("imported_from").is_some(),"mass":self.info["total_mass"],"cad_mass":self.info["cad_mass"],"virtual_mass":self.info["virtual_mass"],"height":self.info["height"],"hip_height":self.info["hip_height"],"motors":motors.len(),"dof":model.raw().nv-6,"passive":array(&self.info["passive"]).len(),"feet":array(&self.info["feet"]).len(),"struts":array(&self.info["struts"]).len(),"rods":array(&self.info["rods"]).len(),"actuators":actuators,"peak_torque":actuators.iter().map(|a|number(&a["peak_torque"],0.)).fold(0.,f64::max),"design":profile.data["design"].as_object().into_iter().flat_map(|o|o.values().map(|v|v["label"].clone())).collect::<Vec<_>>(),"stands_alone":drift<10.,"stand_drift":drift,"blueprint":{"parts":parts.into_iter().map(|p|p.1).collect::<Vec<_>>(),"motors":motor_points,"bounds":bounds}}),
            )
        }
    }
}

fn simplify(vertices: Vec<[f32; 3]>, faces: Vec<[u32; 3]>) -> (Vec<[f32; 3]>, Vec<[u32; 3]>) {
    if faces.len() <= 1500 || vertices.is_empty() {
        return (vertices, faces);
    }
    let mut low = vertices[0];
    let mut high = vertices[0];
    for p in &vertices {
        for i in 0..3 {
            low[i] = low[i].min(p[i]);
            high[i] = high[i].max(p[i]);
        }
    }
    let cell = (((0..3).map(|i| (high[i] - low[i]).powi(2)).sum::<f32>()).sqrt() / 55.).max(0.0007);
    let mut buckets: BTreeMap<[i64; 3], (usize, [f64; 3], usize)> = BTreeMap::new();
    let mut inverse = Vec::new();
    for p in vertices {
        let key = p.map(|v| (v / cell).floor() as i64);
        let count = buckets.len();
        let bucket = buckets.entry(key).or_insert((count, [0.; 3], 0));
        for i in 0..3 {
            bucket.1[i] += p[i] as f64;
        }
        bucket.2 += 1;
        inverse.push(bucket.0 as u32);
    }
    let mut merged = vec![[0.; 3]; buckets.len()];
    for (_, (id, sum, count)) in buckets {
        merged[id] = sum.map(|v| (v / count as f64) as f32);
    }
    let mut seen = BTreeSet::new();
    let faces = faces
        .into_iter()
        .map(|f| f.map(|i| inverse[i as usize]))
        .filter(|f| {
            if f[0] == f[1] || f[1] == f[2] || f[0] == f[2] {
                return false;
            }
            let mut key = *f;
            key.sort();
            seen.insert(key)
        })
        .collect();
    (merged, faces)
}
