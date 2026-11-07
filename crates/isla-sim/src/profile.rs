use anyhow::{bail, Context, Result};
use serde_json::{json, Value};
use std::{
    collections::HashSet,
    fs,
    io::Write,
    path::{Path, PathBuf},
};

#[derive(Clone, Debug)]
pub struct RobotProfile {
    pub folder: PathBuf,
    pub data: Value,
}
impl RobotProfile {
    pub fn load(root: &Path, id: &str) -> Result<Self> {
        let requested = Path::new(id);
        let mut folder = if requested.is_absolute() || requested.join("robot.json").is_file() {
            requested.to_path_buf()
        } else {
            root.join(id)
        };
        if !folder.join("robot.json").is_file() {
            for entry in fs::read_dir(root)?.flatten() {
                let p = entry.path().join("robot.json");
                if let Ok(bytes) = fs::read(p) {
                    if let Ok(v) = serde_json::from_slice::<Value>(&bytes) {
                        if array(&v["aliases"]).iter().any(|a| a.as_str() == Some(id)) {
                            folder = entry.path();
                            break;
                        }
                    }
                }
            }
        }
        let data = serde_json::from_slice(
            &fs::read(folder.join("robot.json"))
                .with_context(|| format!("No robot profile at {}", folder.display()))?,
        )?;
        let p = Self {
            folder: folder.canonicalize()?,
            data,
        };
        p.validate()?;
        Ok(p)
    }
    pub fn list(root: &Path) -> Result<Vec<Self>> {
        let mut entries = if root.exists() {
            fs::read_dir(root)?
                .flatten()
                .map(|e| e.path())
                .collect::<Vec<_>>()
        } else {
            vec![]
        };
        entries.sort();
        Ok(entries
            .iter()
            .filter_map(|p| Self::load(root, &p.to_string_lossy()).ok())
            .collect())
    }
    pub fn id(&self) -> &str {
        self.data["id"].as_str().unwrap_or("")
    }
    pub fn name(&self) -> &str {
        self.data["name"].as_str().unwrap_or(self.id())
    }
    pub fn source_path(&self) -> Result<PathBuf> {
        Ok(self
            .folder
            .join(
                self.data["source"]
                    .as_str()
                    .context("Profile needs source")?,
            )
            .canonicalize()
            .context("The robot's model is missing")?)
    }
    pub fn validate(&self) -> Result<()> {
        if self.data["format"] != "isla-robot" {
            bail!("Not an Isla robot profile")
        };
        if self.id().is_empty() || self.name().is_empty() {
            bail!("A robot needs an id and a name")
        };
        self.source_path()?;
        let mut joints = HashSet::new();
        let mut nodes = HashSet::new();
        for motor in array(&self.data["motors"]) {
            let joint = string(motor, "joint");
            if joint.is_empty() || !joints.insert(joint.to_string()) {
                bail!("Joint {joint} has invalid or duplicate motors")
            };
            if self.data["actuator_types"]
                .get(string(motor, "type"))
                .is_none()
            {
                bail!("Motor {joint} uses unknown actuator type")
            };
            let direction = motor["direction"].as_i64().unwrap_or(1);
            if direction != 1 && direction != -1 {
                bail!("Motor {joint}: direction must be 1 or -1")
            };
            if !nodes.insert((
                motor["bus"].as_str().unwrap_or("can0").to_owned(),
                motor["node"].as_u64().unwrap_or(1),
            )) {
                bail!("Two motors share a CAN node")
            };
        }
        for (key, p) in object(&self.data["design"]) {
            if !["rod", "strut_k", "strut_c", "strut_preload"].contains(&string(p, "kind")) {
                bail!("Design value {key} has unknown kind")
            };
            let lo = p["range"][0]
                .as_f64()
                .context("Design range needs two numbers")?;
            let hi = p["range"][1]
                .as_f64()
                .context("Design range needs two numbers")?;
            if !lo.is_finite() || !hi.is_finite() || lo > hi {
                bail!("Invalid design range for {key}")
            };
        }
        Ok(())
    }
    pub fn save(&self) -> Result<()> {
        self.validate()?;
        let mut f = tempfile::NamedTempFile::new_in(&self.folder)?;
        serde_json::to_writer_pretty(&mut f, &self.data)?;
        writeln!(f)?;
        f.persist(self.folder.join("robot.json"))?;
        Ok(())
    }
    pub fn complete_design(&self, values: Option<&Value>) -> Value {
        let mut out = serde_json::Map::new();
        for (key, p) in object(&self.data["design"]) {
            let lo = number(&p["range"][0], 0.);
            let hi = number(&p["range"][1], 0.);
            let default = number(&self.data["chosen_design"][key], number(&p["default"], lo));
            let chosen = values
                .and_then(|v| v.get(key))
                .and_then(Value::as_f64)
                .filter(|v| v.is_finite())
                .unwrap_or(default);
            out.insert(key.clone(), json!(chosen.clamp(lo, hi)));
        }
        Value::Object(out)
    }
    pub fn to_dict(&self) -> Value {
        let mut out = self.data.clone();
        out["design_values"] = self.complete_design(None);
        for (key, _p) in object(&self.data["design"]) {
            out["design"][key]["key"] = json!(key);
        }
        out
    }
}
pub fn array(v: &Value) -> &[Value] {
    v.as_array().map(Vec::as_slice).unwrap_or(&[])
}
pub fn object(v: &Value) -> impl Iterator<Item = (&String, &Value)> {
    v.as_object().into_iter().flat_map(|m| m.iter())
}
pub fn string<'a>(v: &'a Value, key: &str) -> &'a str {
    v[key].as_str().unwrap_or("")
}
pub fn number(v: &Value, default: f64) -> f64 {
    v.as_f64().filter(|x| x.is_finite()).unwrap_or(default)
}
pub fn arr<const N: usize>(v: &Value, default: [f64; N]) -> [f64; N] {
    let mut out = default;
    for (i, a) in array(v).iter().take(N).enumerate() {
        out[i] = number(a, default[i]);
    }
    out
}
