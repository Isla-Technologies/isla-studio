//! The local HTTP/WebSocket service. Heavy model operations run outside Tokio.
use crate::{
    paths::{confined, read_json, write_json, Paths},
    runs::TrainingManager,
};
use anyhow::{bail, Context, Result};
use axum::{
    body::{Body, Bytes},
    extract::{
        ws::{Message, WebSocket},
        DefaultBodyLimit, Path as RoutePath, State, WebSocketUpgrade,
    },
    http::{HeaderMap, Method, StatusCode, Uri},
    response::{IntoResponse, Response},
    routing::{any, get},
    Json, Router,
};
use futures_util::{SinkExt, StreamExt};
use isla_hardware::{HardwareLink, HardwareSettings};
use isla_sim::{LiveSession, RobotProfile};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    fs,
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    thread,
    time::{Duration, Instant},
};
use tokio::sync::broadcast;
use tower_http::services::{ServeDir, ServeFile};

type Shared = Arc<Platform>;
pub struct Platform {
    pub paths: Paths,
    pub events: broadcast::Sender<Value>,
    inner: Mutex<Inner>,
    training: Mutex<TrainingManager>,
    stop: AtomicBool,
    workers: Mutex<Vec<thread::JoinHandle<()>>>,
}
struct Inner {
    session: LiveSession,
    hardware: HardwareLink,
    settings: HardwareSettings,
    display: HashMap<String, isla_sim::Built>,
}

impl Platform {
    pub fn new(paths: Paths, robot: Option<&str>) -> Result<Shared> {
        paths.ensure()?;
        let profiles = RobotProfile::list(&paths.robots)?;
        let remembered = read_json(&paths.settings.join("studio.json"))
            .ok()
            .and_then(|v| v["robot"].as_str().map(str::to_owned));
        let wanted = robot.or(remembered.as_deref()).unwrap_or("luma_1");
        let profile = profiles
            .into_iter()
            .find(|p| p.data["id"] == wanted)
            .or_else(|| RobotProfile::list(&paths.robots).ok()?.into_iter().next())
            .context("No robot profiles in the hangar")?;
        let id = profile.data["id"].as_str().context("Robot has no id")?;
        let settings = HardwareSettings::load(&paths.settings, id, &profile.data)?;
        let (events, _) = broadcast::channel(240);
        let hardware = HardwareLink::new(profile.data.clone(), settings.clone())?;
        let session = LiveSession::new(profile, None)?;
        let training = TrainingManager::new(paths.clone(), events.clone());
        Ok(Arc::new(Self {
            paths,
            events,
            inner: Mutex::new(Inner {
                session,
                hardware,
                settings,
                display: HashMap::new(),
            }),
            training: Mutex::new(training),
            stop: AtomicBool::new(false),
            workers: Mutex::new(Vec::new()),
        }))
    }
    pub fn start(self: &Arc<Self>) {
        let state = self.clone();
        let simulation = thread::spawn(move || {
            let mut frame_at = Instant::now();
            let mut last = frame_at;
            let mut debt = 0.0_f64;
            while !state.stop.load(Ordering::Acquire) {
                let started = Instant::now();
                let elapsed = started.duration_since(last).as_secs_f64();
                last = started;
                if let Ok(mut inner) = state.inner.lock() {
                    debt = (debt + elapsed * inner.session.rate).min(0.05);
                    while debt >= 0.002 {
                        if let Err(error) = inner.session.step() {
                            let _=state.events.send(json!({"type":"error","message":format!("Simulation stopped: {error}")}));
                            let _ = inner
                                .session
                                .command(&json!({"type":"sim.play","playing":false}));
                        }
                        debt -= 0.002;
                    }
                    if frame_at.elapsed() >= Duration::from_secs_f64(1. / 30.) {
                        let targets = inner
                            .session
                            .actuators
                            .iter()
                            .zip(inner.session.data.ctrl())
                            .map(|((name, _, _), value)| (name.clone(), *value))
                            .collect();
                        let _ = inner.hardware.set_follow(targets);
                        let _ = state.events.send(inner.session.frame());
                        let _ = state.events.send(inner.hardware.status());
                        frame_at = Instant::now();
                    }
                }
                thread::sleep(Duration::from_millis(2).saturating_sub(started.elapsed()));
            }
        });
        let state = self.clone();
        let watcher = thread::spawn(move || {
            while !state.stop.load(Ordering::Acquire) {
                if let Ok(mut training) = state.training.lock() {
                    training.poll();
                }
                thread::sleep(Duration::from_millis(400));
            }
        });
        self.workers.lock().unwrap().extend([simulation, watcher]);
    }
    pub fn close(&self) {
        self.stop.store(true, Ordering::Release);
        if let Ok(mut inner) = self.inner.lock() {
            let _ = inner.hardware.disconnect();
        }
        if let Ok(mut workers) = self.workers.lock() {
            for worker in workers.drain(..) {
                let _ = worker.join();
            }
        }
    }
    fn profile(&self, id: &str) -> Result<RobotProfile> {
        crate::paths::validate_name(id)?;
        RobotProfile::load(&self.paths.robots, id)
    }
    fn active(&self) -> Result<String> {
        Ok(self
            .inner
            .lock()
            .map_err(|_| anyhow::anyhow!("Simulation lock failed"))?
            .session
            .profile
            .data["id"]
            .as_str()
            .unwrap_or("")
            .to_owned())
    }
    fn command(&self, message: &Value) -> Result<()> {
        let mut inner = self
            .inner
            .lock()
            .map_err(|_| anyhow::anyhow!("Simulation lock failed"))?;
        match message["type"].as_str().unwrap_or("") {
            "hw.estop" => inner.hardware.estop("Emergency stop pressed"),
            "hw.jump" => inner.hardware.jump(),
            "sim.command" => {
                let command: [f64; 3] = serde_json::from_value(message["command"].clone())?;
                inner.hardware.set_command(command)?;
                inner.session.command(message)
            }
            _ => inner.session.command(message),
        }
    }
}

#[derive(Debug)]
pub struct ApiError(StatusCode, String);
impl ApiError {
    fn missing(message: impl Into<String>) -> Self {
        Self(StatusCode::NOT_FOUND, message.into())
    }
}
impl From<anyhow::Error> for ApiError {
    fn from(error: anyhow::Error) -> Self {
        Self(StatusCode::BAD_REQUEST, format!("{error:#}"))
    }
}
impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.0, Json(json!({"detail":self.1}))).into_response()
    }
}

pub fn router(state: Shared) -> Router {
    let dist = state.paths.ui.clone();
    let router = Router::new()
        .route("/api/{*path}", any(api))
        .route("/ws", get(websocket));
    let router = if dist.join("index.html").is_file() {
        router.fallback_service(
            ServeDir::new(&dist).not_found_service(ServeFile::new(dist.join("index.html"))),
        )
    } else {
        router.fallback(||async{(StatusCode::SERVICE_UNAVAILABLE,Json(json!({"message":"Studio's interface is not built: run npm ci and npm run build in ui/."})))})
    };
    router
        .layer(DefaultBodyLimit::max(64 * 1024 * 1024))
        .with_state(state)
}

/// Browsers must originate from this service. CLI requests without Origin remain supported.
fn check_origin(headers: &HeaderMap) -> Result<()> {
    if let Some(origin) = headers.get("origin") {
        let origin = origin.to_str()?;
        let uri: Uri = origin.parse()?;
        let host = headers.get("host").context("Missing Host")?.to_str()?;
        if !matches!(uri.scheme_str(), Some("http" | "https"))
            || uri.authority().map(|a| a.as_str()) != Some(host)
        {
            bail!("Cross-origin access is not allowed");
        }
    }
    Ok(())
}

async fn api(
    State(state): State<Shared>,
    RoutePath(path): RoutePath<String>,
    method: Method,
    headers: HeaderMap,
    body: Bytes,
) -> Result<Response, ApiError> {
    check_origin(&headers).map_err(|e| ApiError(StatusCode::FORBIDDEN, e.to_string()))?;
    tokio::task::spawn_blocking(move || dispatch(&state, &path, &method, &body))
        .await
        .map_err(|e| ApiError(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
}

fn dispatch(
    state: &Shared,
    path: &str,
    method: &Method,
    bytes: &[u8],
) -> Result<Response, ApiError> {
    let segments: Vec<_> = path.split('/').collect();
    let upload = method == Method::PUT && segments.starts_with(&["robots", "upload"]);
    let body: Value = if bytes.is_empty() || upload {
        json!({})
    } else {
        serde_json::from_slice(bytes)
            .map_err(|e| ApiError(StatusCode::BAD_REQUEST, format!("Invalid JSON: {e}")))?
    };
    let result: Value = match (method.as_str(), segments.as_slice()) {
        ("GET", ["status"]) => {
            json!({"version":env!("CARGO_PKG_VERSION"),"runtime":"rust","os":std::env::consts::OS,"os_release":"","machine":std::env::consts::ARCH,
            "mujoco":"3.15.0","cpus":thread::available_parallelism().map(|n|n.get()).unwrap_or(1),"gpu":gpu_status(),"robot":state.active()?,"can":Value::Null})
        }
        ("GET", ["robots"]) => {
            let active = state.active()?;
            let runs = state.training.lock().unwrap().summaries();
            let mut cards = Vec::new();
            for profile in RobotProfile::list(&state.paths.robots)? {
                let id = profile.data["id"].as_str().unwrap_or("").to_owned();
                let mut card = match isla_sim::build(&profile, None, "display")
                    .and_then(|b| b.card(&profile))
                {
                    Ok(value) => value,
                    Err(error) => {
                        json!({"id":id,"name":profile.data["name"],"about":profile.data["about"],"problem":error.to_string()})
                    }
                };
                let belongs = |run: &&Value| {
                    run["robot"] == id
                        || profile.data["aliases"]
                            .as_array()
                            .is_some_and(|a| a.contains(&run["robot"]))
                };
                card["active"] = json!(id == active);
                card["runs"] = json!(runs.iter().filter(belongs).count());
                card["policies"] = json!(runs
                    .iter()
                    .filter(belongs)
                    .filter(|r| r["has_policy"] == true)
                    .count());
                cards.push(card);
            }
            json!(cards)
        }
        ("POST", ["robots", id, "activate"]) => {
            let profile = state
                .profile(id)
                .map_err(|e| ApiError::missing(e.to_string()))?;
            // Build first: a failed activation leaves the previous robot usable.
            let session = LiveSession::new(profile, None)?;
            let settings =
                HardwareSettings::load(&state.paths.settings, id, &session.profile.data)?;
            let hardware = HardwareLink::new(session.profile.data.clone(), settings.clone())?;
            let mut inner = state.inner.lock().unwrap();
            inner.hardware.disconnect()?;
            inner.session = session;
            inner.hardware = hardware;
            inner.settings = settings;
            inner.display.remove(*id);
            write_json(
                &state.paths.settings.join("studio.json"),
                &json!({"robot":id}),
            )?;
            let _ = state.events.send(json!({"type":"robot","robot":id}));
            json!({"ok":true,"robot":id})
        }
        ("POST", ["robots", "import"]) => {
            let source = body["path"].as_str().unwrap_or("").trim().trim_matches('"');
            if source.is_empty() {
                return Err(ApiError(
                    StatusCode::BAD_REQUEST,
                    "Enter the path of the robot's model file (.xml or .urdf).".into(),
                ));
            }
            import(state, source, &body)?
        }
        ("PUT", ["robots", "upload", token, rest @ ..]) if !rest.is_empty() => {
            let folder = upload_folder(state, token)?;
            let target = confined(&folder, rest.join("/"))?;
            let extension = target
                .extension()
                .and_then(|e| e.to_str())
                .unwrap_or("")
                .to_ascii_lowercase();
            if !["xml", "urdf", "mjcf", "stl", "obj", "msh", "png"].contains(&extension.as_str()) {
                bail_api("Only model, mesh and texture files are imported")?;
            }
            fs::create_dir_all(target.parent().unwrap()).map_err(anyhow::Error::from)?;
            fs::write(target, bytes).map_err(anyhow::Error::from)?;
            json!({"ok":true})
        }
        ("POST", ["robots", "upload", token, "import"]) => {
            let folder = upload_folder(state, token)?;
            let source = confined(&folder, body["model"].as_str().unwrap_or(""))?;
            let result = import(state, &source.to_string_lossy(), &body);
            let _ = fs::remove_dir_all(&folder);
            result?
        }
        ("DELETE", ["robots", id]) => {
            let profile = state
                .profile(id)
                .map_err(|e| ApiError::missing(e.to_string()))?;
            if state.active()? == *id {
                bail_api("This robot is loaded. Load another robot before removing it.")?;
            }
            let runs = state.training.lock().unwrap().summaries();
            if runs.iter().any(|r| {
                r["robot"] == *id
                    || profile.data["aliases"]
                        .as_array()
                        .is_some_and(|a| a.contains(&r["robot"]))
            }) {
                bail_api("Training runs use this robot. Delete those runs first.")?;
            }
            let folder = confined(&state.paths.robots, id)?;
            fs::remove_dir_all(folder).map_err(anyhow::Error::from)?;
            if let Ok(cache) = confined(&state.paths.cache, id) {
                let _ = fs::remove_dir_all(cache);
            }
            state.inner.lock().unwrap().display.remove(*id);
            json!({"ok":true})
        }
        ("GET", ["robots", id, "meta" | "meshes.bin"]) => {
            let profile = state
                .profile(id)
                .map_err(|e| ApiError::missing(e.to_string()))?;
            let mut inner = state.inner.lock().unwrap();
            let built = if inner.session.profile.data["id"] == *id {
                &inner.session.built
            } else {
                if !inner.display.contains_key(*id) {
                    if inner.display.len() >= 4 {
                        inner.display.clear();
                    }
                    inner
                        .display
                        .insert(id.to_string(), isla_sim::build(&profile, None, "display")?);
                }
                &inner.display[*id]
            };
            let (blob, index) = built.meshes()?;
            if segments[2] == "meshes.bin" {
                return Ok((
                    [
                        ("Content-Type", "application/octet-stream"),
                        ("Cache-Control", "max-age=3600"),
                    ],
                    Body::from(blob),
                )
                    .into_response());
            }
            let mut meta = built.meta(&profile)?;
            meta["meshes"] = index;
            meta["design_values"] = profile.complete_design(None);
            meta
        }
        ("POST", ["robots", id, "analysis"]) => {
            state
                .profile(id)
                .map_err(|e| ApiError::missing(e.to_string()))?;
            return Err(ApiError(StatusCode::NOT_IMPLEMENTED,"Design analysis is still being ported. Use the reference backend for this operation.".into()));
        }
        ("POST", ["robots", id, "design"]) => {
            let mut profile = state
                .profile(id)
                .map_err(|e| ApiError::missing(e.to_string()))?;
            let design = profile.complete_design(body.get("design"));
            profile.data["chosen_design"] = design.clone();
            let replacement = if state.active()? == *id {
                Some(LiveSession::new(profile.clone(), Some(&design))?)
            } else {
                None
            };
            profile.save()?;
            let mut inner = state.inner.lock().unwrap();
            if let Some(session) = replacement {
                inner.session = session;
            }
            inner.display.remove(*id);
            let _ = state
                .events
                .send(json!({"type":"design","robot":id,"design":design}));
            json!({"ok":true,"design":design})
        }
        ("PUT", ["robots", id, "motors"]) => {
            let mut profile = state
                .profile(id)
                .map_err(|e| ApiError::missing(e.to_string()))?;
            if state.active()? == *id
                && state.inner.lock().unwrap().hardware.status()["state"] != "disconnected"
            {
                bail_api("Disconnect the robot before changing its motor map")?;
            }
            if let Some(motors) = profile.data["motors"].as_array_mut() {
                for change in body["motors"].as_array().into_iter().flatten() {
                    if let Some(motor) = motors.iter_mut().find(|m| m["joint"] == change["joint"]) {
                        for key in ["bus", "node", "direction", "zero"] {
                            if let Some(value) = change.get(key) {
                                motor[key] = value.clone();
                            }
                        }
                    }
                }
            }
            profile.save()?;
            json!({"ok":true,"motors":profile.data["motors"]})
        }
        ("GET", ["skills"]) => isla_learn::skills::catalogue(),
        ("GET", ["runs"]) => json!(state.training.lock().unwrap().summaries()),
        ("POST", ["runs"]) => {
            ensure_training_ready()?;
            let name = body["name"].as_str().unwrap_or("run").trim();
            crate::paths::validate_name(name)?;
            if confined(&state.paths.runs, name)?.exists() {
                return Err(ApiError(
                    StatusCode::CONFLICT,
                    format!("A run named {name} already exists"),
                ));
            }
            let mut setup = body.get("setup").cloned().unwrap_or_else(|| json!({}));
            setup["robot"] = json!(state.active()?);
            let setup = isla_learn::skills::TrainingSetup::from_value(setup)?.to_value();
            let inner = state.inner.lock().unwrap();
            state.training.lock().unwrap().start(
                name,
                &setup,
                &inner.session.profile.data,
                &inner.session.profile.folder,
                body["device"].as_str().unwrap_or("auto"),
            )?;
            json!({"ok":true,"run":name})
        }
        ("GET", ["runs", name]) => state
            .training
            .lock()
            .unwrap()
            .detail(name)
            .map_err(|e| ApiError::missing(e.to_string()))?,
        ("POST", ["runs", name, "stop" | "resume" | "evaluate"]) => {
            state
                .paths
                .run(name)
                .map_err(|e| ApiError::missing(e.to_string()))?;
            if segments[2] != "stop" {
                ensure_training_ready()?;
            }
            let mut training = state.training.lock().unwrap();
            match segments[2] {
                "stop" => training.stop(name)?,
                "resume" => training.resume(name, body["device"].as_str().unwrap_or("auto"))?,
                _ => training.evaluate(name)?,
            };
            json!({"ok":true})
        }
        ("GET", ["runs", name, "evaluation"]) => {
            let folder = state
                .paths
                .run(name)
                .map_err(|e| ApiError::missing(e.to_string()))?;
            json!({"testing":state.training.lock().unwrap().evaluating(name),"report":read_json(&folder.join("evaluation.json")).ok()})
        }
        ("GET", ["runs", name, "policy.json"]) => {
            let path = state
                .paths
                .run(name)
                .map_err(|e| ApiError::missing(e.to_string()))?
                .join("policy.json");
            read_json(&path).map_err(|_| ApiError::missing("This run has no policy yet"))?
        }
        ("DELETE", ["runs", name]) => {
            state
                .paths
                .run(name)
                .map_err(|e| ApiError::missing(e.to_string()))?;
            state.training.lock().unwrap().remove(name)?;
            json!({"ok":true})
        }
        ("POST", ["sim", "policy"]) => load_policy(state, &body)?,
        ("GET", ["hardware"]) => {
            let inner = state.inner.lock().unwrap();
            let mut value = inner.hardware.status();
            value["settings"] =
                serde_json::to_value(&inner.settings).map_err(anyhow::Error::from)?;
            value
        }
        ("PUT", ["hardware", "settings"]) => {
            let mut inner = state.inner.lock().unwrap();
            if inner.hardware.status()["state"] == "armed" {
                bail_api("Disarm before changing settings")?;
            }
            let mut current = serde_json::to_value(&inner.settings).map_err(anyhow::Error::from)?;
            merge(&mut current, &body);
            current["robot"] = inner.session.profile.data["id"].clone();
            let settings = HardwareSettings::from_value(current)?;
            inner.hardware.update_settings(settings.clone())?;
            settings.save(&state.paths.settings)?;
            inner.settings = settings;
            json!({"ok":true,"settings":inner.settings})
        }
        ("GET", ["hardware", "ports"]) => isla_hardware::can::ports(),
        ("POST", ["hardware", action]) => hardware_action(state, action, &body)?,
        ("POST", ["gpu", "install"]) => {
            return Err(ApiError(
                StatusCode::NOT_IMPLEMENTED,
                gpu_status()["problem"].as_str().unwrap().into(),
            ))
        }
        _ => {
            return Err(ApiError::missing(format!(
                "No endpoint {} /api/{path}",
                method
            )))
        }
    };
    Ok(Json(result).into_response())
}

fn bail_api(message: &str) -> Result<(), ApiError> {
    Err(ApiError(StatusCode::BAD_REQUEST, message.into()))
}
fn upload_folder(state: &Platform, token: &str) -> Result<PathBuf> {
    if !(8..=40).contains(&token.len())
        || !token
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
    {
        bail!("Bad upload name");
    }
    let uploads = state.paths.cache.join("uploads");
    fs::create_dir_all(&uploads)?;
    let folder = confined(&uploads, token)?;
    fs::create_dir_all(&folder)?;
    Ok(folder)
}
fn import(state: &Platform, source: &str, body: &Value) -> Result<Value> {
    let _ = (state, body);
    if !std::path::Path::new(source).is_file() {
        bail!("No model at {source}");
    }
    bail!("Robot import is still being ported. Use the reference backend to import it; native Studio can then open its profile.")
}
fn merge(target: &mut Value, patch: &Value) {
    if let (Some(a), Some(b)) = (target.as_object_mut(), patch.as_object()) {
        for (key, value) in b {
            if value.is_object() {
                merge(a.entry(key).or_insert_with(|| json!({})), value);
            } else {
                a.insert(key.clone(), value.clone());
            }
        }
    }
}
pub fn gpu_status() -> Value {
    json!({"ready":false,"installed":false,"name":null,"problem":"Native Rust GPU training has not passed compatibility validation. CPU training is available; the reference Python backend retains CUDA/Warp support.","installable":false})
}

fn ensure_training_ready() -> Result<(), ApiError> {
    Err(ApiError(StatusCode::NOT_IMPLEMENTED,"The Rust training environment and evaluator are still being ported. Use the reference backend for training and evaluation.".into()))
}

struct LivePolicy {
    runner: isla_learn::PolicyRunner,
    design: std::collections::BTreeMap<String, f64>,
}
impl isla_sim::session::Controller for LivePolicy {
    fn targets(
        &mut self,
        dt: f64,
        q: [f64; 4],
        gyro: [f64; 3],
        positions: &std::collections::BTreeMap<String, f64>,
        velocities: &std::collections::BTreeMap<String, f64>,
    ) -> Result<std::collections::BTreeMap<String, f64>> {
        self.runner.step(dt, q, gyro, positions, velocities)
    }
    fn command(&mut self, c: [f64; 3]) -> Result<()> {
        self.runner.set_command(c[0], c[1], c[2])
    }
    fn jump(&mut self) -> Result<()> {
        if !self.runner.jump() {
            bail!("This policy cannot jump now")
        };
        Ok(())
    }
    fn reset(&mut self) -> Result<()> {
        self.runner =
            isla_learn::PolicyRunner::new(self.runner.policy.clone(), Some(&self.design))?;
        Ok(())
    }
    fn steers(&self) -> bool {
        self.runner.policy.commands.is_some()
    }
    fn can_jump(&self) -> bool {
        self.runner.can_jump()
    }
    fn jumping(&self) -> bool {
        self.runner.jumping.is_some()
    }
}
fn load_policy(state: &Platform, body: &Value) -> Result<Value> {
    let mut inner = state.inner.lock().unwrap();
    let Some(name) = body["run"].as_str().filter(|s| !s.is_empty()) else {
        inner.session.controller = None;
        inner.session.policy_name.clear();
        inner.session.set_mode("stand")?;
        return Ok(json!({"ok":true}));
    };
    let folder = state.paths.run(name)?;
    let policy = isla_learn::Policy::load(folder.join("policy.json"))?;
    if !policy.robot.is_empty() && policy.robot != inner.session.profile.id() {
        bail!("That policy is for {}", policy.robot);
    }
    let result = json!({"ok":true,"design":policy.design,"skill":policy.skill});
    if !policy.design.is_empty() && body["use_design"].as_bool().unwrap_or(true) {
        inner.session =
            LiveSession::new(inner.session.profile.clone(), Some(&json!(policy.design)))?;
    }
    let design = serde_json::from_value(inner.session.built.info["design"].clone())?;
    let runner = isla_learn::PolicyRunner::new(policy, Some(&design))?;
    inner.session.controller = Some(Box::new(LivePolicy { runner, design }));
    inner.session.policy_name = name.to_owned();
    inner.session.reset()?;
    inner.session.set_mode("policy")?;
    Ok(result)
}
fn hardware_action(state: &Platform, action: &str, body: &Value) -> Result<Value> {
    let mut inner = state.inner.lock().unwrap();
    match action {
        "connect" => {
            let profile = inner.session.profile.clone();
            let joints = inner.hardware.connect(&profile)?;
            return Ok(json!({"ok":true,"joints":joints}));
        }
        "disconnect" => inner.hardware.disconnect()?,
        "scan" => return Ok(json!({"ok":true,"joints":inner.hardware.scan()?})),
        "arm" => inner.hardware.arm()?,
        "disarm" => inner.hardware.disarm()?,
        "estop" => inner
            .hardware
            .estop(body["reason"].as_str().unwrap_or("Emergency stop pressed"))?,
        "clear" => inner.hardware.clear()?,
        "mode" => {
            let mode = body["mode"].as_str().context("Missing mode")?;
            if mode == "policy" {
                let name = body["run"]
                    .as_str()
                    .unwrap_or(&inner.session.policy_name)
                    .to_owned();
                let policy = isla_learn::Policy::load(state.paths.run(&name)?.join("policy.json"))?;
                if !policy.robot.is_empty() && policy.robot != inner.session.profile.id() {
                    bail!("That policy is for {}", policy.robot);
                }
                let design = serde_json::from_value(inner.session.built.info["design"].clone())?;
                inner
                    .hardware
                    .set_policy(Some(policy), &name, Some(&design))?;
            }
            inner.hardware.set_mode(mode)?;
        }
        "zero" => {
            let joint = body["joint"].as_str().context("Missing joint")?;
            let zero = inner.hardware.set_zero(joint)?;
            if let Some(motors) = inner.session.profile.data["motors"].as_array_mut() {
                if let Some(motor) = motors.iter_mut().find(|m| m["joint"] == joint) {
                    motor["zero"] = json!(zero);
                }
            }
            inner.session.profile.save()?;
            return Ok(json!({"ok":true,"zero":zero}));
        }
        "inject" => inner.hardware.inject(
            body["joint"].as_str().context("Missing joint")?,
            body["fault"].as_str(),
        )?,
        _ => bail!("No hardware action {action}"),
    }
    Ok(json!({"ok":true}))
}

async fn websocket(
    State(state): State<Shared>,
    headers: HeaderMap,
    upgrade: WebSocketUpgrade,
) -> Result<Response, ApiError> {
    check_origin(&headers).map_err(|e| ApiError(StatusCode::FORBIDDEN, e.to_string()))?;
    Ok(upgrade
        .max_message_size(64 * 1024)
        .on_upgrade(move |socket| stream(state, socket)))
}
async fn stream(state: Shared, socket: WebSocket) {
    let mut events = state.events.subscribe();
    let (mut sender, mut receiver) = socket.split();
    let hello = {
        let inner = state.inner.lock().unwrap();
        json!({"type":"hello","robot":inner.session.profile.data["id"],"hardware":inner.hardware.status()})
    };
    if sender
        .send(Message::Text(hello.to_string().into()))
        .await
        .is_err()
    {
        return;
    }
    loop {
        if state.stop.load(Ordering::Acquire) {
            break;
        }
        tokio::select! {
            next=events.recv()=>match next {
                Ok(value)=>{if sender.send(Message::Text(value.to_string().into())).await.is_err(){break;}},
                Err(broadcast::error::RecvError::Lagged(_))=>continue,
                Err(_)=>break,
            },
            next=receiver.next()=>match next {
                Some(Ok(Message::Text(text)))=>{
                    let command=serde_json::from_str::<Value>(&text).map_err(anyhow::Error::from).and_then(|value|state.command(&value));
                    if let Err(error)=command {if sender.send(Message::Text(json!({"type":"error","message":error.to_string()}).to_string().into())).await.is_err(){break;}}
                },
                Some(Ok(Message::Ping(bytes)))=>{if sender.send(Message::Pong(bytes)).await.is_err(){break;}},
                Some(Ok(Message::Close(_)))|None|Some(Err(_))=>break,
                _=>{},
            },
            _=tokio::time::sleep(Duration::from_secs(10))=>{if state.stop.load(Ordering::Acquire){break;}}
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn origins_cannot_control_another_site() {
        let mut headers = HeaderMap::new();
        headers.insert("host", "127.0.0.1:8848".parse().unwrap());
        assert!(check_origin(&headers).is_ok());
        headers.insert("origin", "https://attacker.example".parse().unwrap());
        assert!(check_origin(&headers).is_err());
        headers.insert("origin", "http://127.0.0.1:8848".parse().unwrap());
        assert!(check_origin(&headers).is_ok());
    }
}
