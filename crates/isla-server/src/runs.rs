use crate::paths::{confined, read_json, validate_name, write_json, Paths};
use anyhow::{bail, Context, Result};
use serde_json::{json, Map, Value};
use std::{
    collections::HashMap,
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    time::{SystemTime, UNIX_EPOCH},
};
use tokio::sync::broadcast;

pub fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

pub fn folders(paths: &Paths) -> Vec<PathBuf> {
    let mut folders: Vec<_> = fs::read_dir(&paths.runs)
        .into_iter()
        .flatten()
        .flatten()
        .filter(|e| e.file_type().is_ok_and(|k| k.is_dir()))
        .filter_map(|e| {
            let name = e.file_name().to_string_lossy().into_owned();
            paths.run(&name).ok()
        })
        .collect();
    folders.sort();
    folders
}

pub fn progress(folder: &Path) -> Vec<Value> {
    let Ok(mut reader) = csv::Reader::from_path(folder.join("progress.csv")) else {
        return Vec::new();
    };
    let Ok(headers) = reader.headers().cloned() else {
        return Vec::new();
    };
    reader
        .records()
        .filter_map(|row| {
            let row = row.ok()?;
            let mut values = Map::new();
            for (key, value) in headers.iter().zip(row.iter()) {
                if value.is_empty() {
                    continue;
                }
                values.insert(
                    key.into(),
                    if matches!(key, "device" | "phase") {
                        json!(value)
                    } else {
                        json!(value.parse::<f64>().ok()?)
                    },
                );
            }
            Some(Value::Object(values))
        })
        .collect()
}

pub fn status(folder: &Path) -> Value {
    let mut result = read_json(&folder.join("status.json")).unwrap_or_else(
        |_| json!({"state":if folder.join("checkpoint.npz").exists() {"stopped"} else {"new"}}),
    );
    if result["state"] == "running" && now() - result["updated"].as_f64().unwrap_or(0.) > 60. {
        result["state"] = json!("stopped");
    }
    result
}

#[derive(Default)]
struct Tail {
    offset: u64,
    latest: Map<String, Value>,
}

pub struct TrainingManager {
    pub paths: Paths,
    processes: HashMap<String, Child>,
    evaluations: HashMap<String, Child>,
    tails: HashMap<String, Tail>,
    publish: broadcast::Sender<Value>,
}

impl TrainingManager {
    pub fn new(paths: Paths, publish: broadcast::Sender<Value>) -> Self {
        Self {
            paths,
            processes: HashMap::new(),
            evaluations: HashMap::new(),
            tails: HashMap::new(),
            publish,
        }
    }
    fn emit(&self, value: Value) {
        let _ = self.publish.send(value);
    }
    pub fn running(&self, name: &str) -> bool {
        self.processes.contains_key(name)
            || self
                .paths
                .run(name)
                .ok()
                .is_some_and(|path| status(&path)["state"] == "running")
    }
    pub fn evaluating(&self, name: &str) -> bool {
        self.evaluations.contains_key(name)
    }
    pub fn snapshot(&self, name: &str) -> Value {
        self.tails
            .get(name)
            .map(|t| Value::Object(t.latest.clone()))
            .unwrap_or_else(|| json!({}))
    }
    pub fn summaries(&self) -> Vec<Value> {
        let mut values: Vec<_> = folders(&self.paths)
            .iter()
            .filter_map(|p| self.summary(p).ok())
            .collect();
        values.sort_by(|a, b| {
            b["modified"]
                .as_f64()
                .unwrap_or(0.)
                .total_cmp(&a["modified"].as_f64().unwrap_or(0.))
        });
        values
    }
    pub fn summary(&self, folder: &Path) -> Result<Value> {
        let setup = read_json(&folder.join("setup.json"))?;
        let name = folder
            .file_name()
            .context("Invalid run folder")?
            .to_string_lossy();
        let rows = progress(folder);
        let last = rows.last().cloned().unwrap_or_else(|| json!({}));
        let modified = fs::read_dir(folder)?
            .flatten()
            .filter_map(|e| {
                e.metadata()
                    .ok()?
                    .modified()
                    .ok()?
                    .duration_since(UNIX_EPOCH)
                    .ok()
            })
            .map(|d| d.as_secs_f64())
            .fold(0., f64::max);
        let default_steps = isla_learn::skills::skills()
            .get(setup["skill"].as_str().unwrap_or("walk"))
            .map(|skill| skill.steps)
            .unwrap_or(100_000_000);
        Ok(
            json!({"name":name,"robot":setup["robot"],"skill":setup["skill"],"codesign":setup["codesign"]["enabled"].as_bool().unwrap_or(false),
            "robots":setup["robots"],"total":setup["steps"].as_u64().unwrap_or(default_steps),"steps":last["steps"].as_f64().unwrap_or(0.),
            "score":last["score"],"seconds":last["seconds"].as_f64().unwrap_or(0.),"device":last["device"],"phase":last["phase"],
            "has_policy":folder.join("policy.json").is_file(),"status":status(folder)["state"],"modified":modified,
            "running":self.running(&name),"evaluated":folder.join("evaluation.json").is_file(),"evaluating":self.evaluating(&name)}),
        )
    }
    pub fn detail(&self, name: &str) -> Result<Value> {
        let folder = self.paths.run(name)?;
        Ok(
            json!({"summary":self.summary(&folder)?,"setup":read_json(&folder.join("setup.json"))?,"progress":progress(&folder),
            "codesign":read_json(&folder.join("codesign.json")).unwrap_or_else(|_|json!([])),"latest":self.snapshot(name)}),
        )
    }
    pub fn start(
        &mut self,
        name: &str,
        setup: &Value,
        profile: &Value,
        profile_folder: &Path,
        device: &str,
    ) -> Result<()> {
        validate_name(name)?;
        validate_device(device)?;
        let folder = confined(&self.paths.runs, name)?;
        if folder.exists() {
            bail!("A run named {name} already exists");
        }
        let temporary = tempfile::tempdir_in(&self.paths.runs)?;
        let staged = temporary.path().join(name);
        fs::create_dir(&staged)?;
        let mut snapshot = profile.clone();
        snapshot["_folder"] = json!(profile_folder);
        write_json(&staged.join("setup.json"), setup)?;
        write_json(&staged.join("robot.json"), &snapshot)?;
        write_json(
            &staged.join("status.json"),
            &json!({"state":"new","updated":now()}),
        )?;
        fs::rename(&staged, &folder)?;
        self.spawn(name, device)
    }
    pub fn resume(&mut self, name: &str, device: &str) -> Result<()> {
        validate_device(device)?;
        let folder = self.paths.run(name)?;
        if self.running(name) {
            bail!("{name} is already training");
        }
        if folder.join("stop").exists() {
            fs::remove_file(folder.join("stop"))?;
        }
        self.spawn(name, device)
    }
    fn spawn(&mut self, name: &str, device: &str) -> Result<()> {
        let folder = self.paths.run(name)?;
        let output = OpenOptions::new()
            .create(true)
            .append(true)
            .open(folder.join("train.log"))?;
        let errors = OpenOptions::new()
            .create(true)
            .append(true)
            .open(folder.join("train.err"))?;
        let mut command = self.command("train", &folder)?;
        command
            .args(["--device", device])
            .stdout(output)
            .stderr(errors)
            .stdin(Stdio::null());
        detach(&mut command);
        let child = command
            .spawn()
            .context("Could not start the Rust training worker")?;
        self.processes.insert(name.to_owned(), child);
        self.emit(json!({"type":"train.state","run":name,"state":"running"}));
        Ok(())
    }
    fn command(&self, operation: &str, folder: &Path) -> Result<Command> {
        let executable = std::env::var_os("ISLA_EXECUTABLE")
            .map(PathBuf::from)
            .unwrap_or(std::env::current_exe()?);
        let mut command = Command::new(executable);
        command
            .arg(operation)
            .arg(folder)
            .current_dir(&self.paths.root)
            .env("ISLA_ROOT", &self.paths.root)
            .env("ISLA_ROBOTS", &self.paths.robots)
            .env("ISLA_RUNS", &self.paths.runs)
            .env("ISLA_SETTINGS", &self.paths.settings)
            .env("ISLA_CACHE", &self.paths.cache);
        Ok(command)
    }
    pub fn evaluate(&mut self, name: &str) -> Result<()> {
        let folder = self.paths.run(name)?;
        if !folder.join("policy.json").is_file() {
            bail!("{name} has no policy yet: it saves one after its first round of practice");
        }
        if self.evaluating(name) {
            bail!("{name} is being tested already");
        }
        let output = File::create(folder.join("evaluate.log"))?;
        let mut command = self.command("evaluate", &folder)?;
        command
            .stdout(output.try_clone()?)
            .stderr(output)
            .stdin(Stdio::null());
        detach(&mut command);
        self.evaluations.insert(name.to_owned(), command.spawn()?);
        self.emit(json!({"type":"evaluation","run":name,"state":"running"}));
        Ok(())
    }
    pub fn stop(&self, name: &str) -> Result<()> {
        fs::write(self.paths.run(name)?.join("stop"), b"stop\n")?;
        self.emit(json!({"type":"train.state","run":name,"state":"stopping"}));
        Ok(())
    }
    pub fn remove(&mut self, name: &str) -> Result<()> {
        let folder = self.paths.run(name)?;
        if self.running(name) {
            bail!("Stop the run before deleting it");
        }
        if self.evaluating(name) {
            bail!("Wait for the policy test to finish before deleting its run");
        }
        fs::remove_dir_all(folder)?;
        self.tails.remove(name);
        Ok(())
    }
    pub fn poll(&mut self) {
        for folder in folders(&self.paths) {
            let name = folder.file_name().unwrap().to_string_lossy().into_owned();
            let Ok(mut file) = File::open(folder.join("train.log")) else {
                continue;
            };
            let Ok(metadata) = file.metadata() else {
                continue;
            };
            let prime = !self.tails.contains_key(&name);
            let tail = self.tails.entry(name.clone()).or_default();
            if metadata.len() < tail.offset {
                tail.offset = 0;
                tail.latest.clear();
            }
            let skip_partial = prime && metadata.len() > 400_000;
            if skip_partial {
                tail.offset = metadata.len() - 400_000;
            }
            if file.seek(SeekFrom::Start(tail.offset)).is_err() {
                continue;
            }
            let mut bytes = Vec::new();
            if file.take(4_000_000).read_to_end(&mut bytes).is_err() {
                continue;
            }
            let Some(end) = bytes.iter().rposition(|b| *b == b'\n') else {
                continue;
            };
            tail.offset += (end + 1) as u64;
            for (index, line) in bytes[..=end].split(|b| *b == b'\n').enumerate() {
                if skip_partial && index == 0 {
                    continue;
                }
                let Ok(value) = serde_json::from_slice::<Value>(line) else {
                    continue;
                };
                let Some(kind) = value["type"].as_str() else {
                    continue;
                };
                if ![
                    "started",
                    "progress",
                    "codesign",
                    "swarm",
                    "preparing",
                    "finished",
                    "stopped",
                    "error",
                    "saved",
                    "continued",
                ]
                .contains(&kind)
                {
                    continue;
                }
                tail.latest.insert(kind.to_owned(), value.clone());
                if !prime {
                    let _ = self
                        .publish
                        .send(json!({"type":"train","run":name,"message":value}));
                }
            }
        }
        let finished: Vec<_> = self
            .processes
            .iter_mut()
            .filter_map(|(name, child)| {
                child
                    .try_wait()
                    .ok()
                    .flatten()
                    .map(|status| (name.clone(), status))
            })
            .collect();
        for (name, status) in finished {
            self.processes.remove(&name);
            self.emit(
                json!({"type":"train.state","run":name,"state":"ended","code":status.code()}),
            );
        }
        let finished: Vec<_> = self
            .evaluations
            .iter_mut()
            .filter_map(|(name, child)| {
                child
                    .try_wait()
                    .ok()
                    .flatten()
                    .map(|status| (name.clone(), status))
            })
            .collect();
        for (name, status) in finished {
            self.evaluations.remove(&name);
            let message = fs::read_to_string(self.paths.runs.join(&name).join("evaluate.log"))
                .ok()
                .and_then(|text| text.lines().last().map(str::to_owned));
            self.emit(json!({"type":"evaluation","run":name,"state":if status.success(){"done"}else{"error"},"message":if status.success(){None}else{message}}));
        }
    }
}

pub fn validate_device(device: &str) -> Result<()> {
    if !matches!(device, "cpu" | "auto" | "gpu") {
        bail!("Device must be auto, cpu or gpu");
    }
    if device == "gpu" {
        bail!("The native Rust GPU training path is not validated yet. Use CPU or the reference Python trainer.");
    }
    Ok(())
}

fn detach(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000200);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn partial_progress_does_not_hide_complete_rows() -> Result<()> {
        let root = tempfile::tempdir()?;
        fs::write(
            root.path().join("progress.csv"),
            "iteration,steps,score,device\n1,100,1.2,cpu\n2,200,",
        )?;
        let rows = progress(root.path());
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0]["steps"], 100.);
        Ok(())
    }
    #[test]
    fn crashed_training_is_not_reported_as_running_forever() -> Result<()> {
        let root = tempfile::tempdir()?;
        write_json(
            &root.path().join("status.json"),
            &json!({"state":"running","updated":now()-120.}),
        )?;
        assert_eq!(status(root.path())["state"], "stopped");
        Ok(())
    }
}
