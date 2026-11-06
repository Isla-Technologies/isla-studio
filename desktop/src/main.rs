#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    fs::{self, OpenOptions},
    io::{Read, Write},
    net::{SocketAddr, TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    thread,
    time::{Duration, Instant},
};
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

struct Backend(Child);

impl Backend {
    fn stop(&mut self) {
        // EOF requests Uvicorn's normal shutdown, which disconnects the robot.
        drop(self.0.stdin.take());
        let deadline = Instant::now() + Duration::from_secs(20);
        while Instant::now() < deadline {
            if !matches!(self.0.try_wait(), Ok(None)) {
                return;
            }
            thread::sleep(Duration::from_millis(100));
        }
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

impl Drop for Backend {
    fn drop(&mut self) {
        self.stop();
    }
}

#[derive(Default)]
struct State {
    backend: Mutex<Option<Backend>>,
    closing: AtomicBool,
}

fn ready(port: u16) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(200)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(1)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(1)));
    if stream
        .write_all(b"GET /api/status HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut response = [0; 32];
    matches!(stream.read(&mut response), Ok(n) if n > 12 && response[..n].starts_with(b"HTTP/1.1 200"))
}

fn launch(payload: &Path, documents: &Path, local: &Path, state: &State) -> Result<u16, String> {
    let data = std::env::var_os("ISLA_DESKTOP_DATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| documents.join("Isla Studio"));
    fs::create_dir_all(&data).map_err(|e| format!("Cannot open {}: {e}", data.display()))?;
    fs::create_dir_all(local).map_err(|e| e.to_string())?;
    let packages = local.join("python313-packages");
    fs::create_dir_all(&packages).map_err(|e| e.to_string())?;
    let studio = payload.join("studio");
    let python = payload.join("python").join(if cfg!(windows) {
        "python.exe"
    } else {
        "bin/python3"
    });
    if !python.is_file() || !studio.join("ui/dist/index.html").is_file() {
        return Err("The installation is incomplete. Please reinstall Isla Studio.".into());
    }
    // Use a separate loopback port so a development Studio can stay open.
    let socket = TcpListener::bind(("127.0.0.1", 0)).map_err(|e| e.to_string())?;
    let port = socket.local_addr().map_err(|e| e.to_string())?.port();
    drop(socket);
    let log = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(local.join("studio.log"))
        .map_err(|e| e.to_string())?;
    let mut command = Command::new(python);
    command
        .args([
            "-u",
            "-m",
            "isla_rl.server",
            "--host",
            "127.0.0.1",
            "--port",
            &port.to_string(),
            "--no-browser",
            "--stop-with-stdin",
        ])
        .current_dir(&data)
        .stdin(Stdio::piped())
        .stdout(log.try_clone().map_err(|e| e.to_string())?)
        .stderr(log)
        .env(
            "PYTHONPATH",
            std::env::join_paths([&packages, &studio]).map_err(|e| e.to_string())?,
        )
        .env("PYTHONNOUSERSITE", "1")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("PIP_TARGET", &packages)
        .env("PIP_DISABLE_PIP_VERSION_CHECK", "1")
        .env("ISLA_CACHE", local.join("cache"));
    for (key, folder) in [
        ("ISLA_ROBOTS", "robots"),
        ("ISLA_RUNS", "runs"),
        ("ISLA_POLICIES", "policies"),
        ("ISLA_SETTINGS", "settings"),
    ] {
        command.env(key, data.join(folder));
    }
    #[cfg(windows)]
    command.creation_flags(0x08000000); // CREATE_NO_WINDOW, including Python's child processes.
    {
        // Register under the same lock that shutdown takes: closing during startup
        // must never race past an empty backend slot and leave a new child behind.
        let mut backend = state.backend.lock().map_err(|e| e.to_string())?;
        if state.closing.load(Ordering::SeqCst) {
            return Err("Studio is closing.".into());
        }
        let child = command
            .spawn()
            .map_err(|e| format!("Could not start the simulation: {e}"))?;
        *backend = Some(Backend(child));
    }
    let deadline = Instant::now() + Duration::from_secs(120);
    while Instant::now() < deadline {
        if state.closing.load(Ordering::SeqCst) {
            return Err("Studio is closing.".into());
        }
        if ready(port) {
            return Ok(port);
        }
        if let Some(backend) = state.backend.lock().map_err(|e| e.to_string())?.as_mut() {
            if let Some(status) = backend.0.try_wait().map_err(|e| e.to_string())? {
                return Err(format!(
                    "The simulation stopped ({status}).\nDetails: {}",
                    local.join("studio.log").display()
                ));
            }
        } else {
            return Err("Studio is closing.".into());
        }
        thread::sleep(Duration::from_millis(150));
    }
    Err(format!(
        "The simulation took too long to start.\nDetails: {}",
        local.join("studio.log").display()
    ))
}

fn main() {
    let state = Arc::new(State::default());
    let setup_state = state.clone();
    let app = tauri::Builder::default()
        .setup(move |app| {
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Isla Studio").inner_size(1440.0, 960.0).min_inner_size(1024.0, 700.0)
                .background_color(tauri::webview::Color(5, 5, 5, 255))
                .build()?;
            let payload = std::env::var_os("ISLA_DESKTOP_PAYLOAD").map(PathBuf::from)
                .unwrap_or(app.path().resource_dir()?.join("payload"));
            let documents = app.path().document_dir()?;
            let local = app.path().app_local_data_dir()?;
            let startup = setup_state.clone();
            thread::spawn(move || {
                match launch(&payload, &documents, &local, &startup) {
                    Ok(port) if !startup.closing.load(Ordering::SeqCst) => {
                        if let Ok(url) = format!("http://127.0.0.1:{port}").parse() {
                            let _ = window.navigate(url);
                        }
                    }
                    Err(error) if !startup.closing.load(Ordering::SeqCst) => {
                        let message = serde_json::to_string(&error).unwrap_or_default();
                        let _ = window.eval(&format!("document.getElementById('status').textContent='Studio could not start.';document.getElementById('detail').textContent={message};"));
                        if let Ok(mut backend) = startup.backend.lock() {
                            backend.take();
                        }
                    }
                    _ => {}
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Could not open Isla Studio");
    app.run(move |handle, event| {
        if let RunEvent::ExitRequested { api, .. } = event {
            if !state.closing.swap(true, Ordering::SeqCst) {
                api.prevent_exit();
                let state = state.clone();
                let handle = handle.clone();
                thread::spawn(move || {
                    if let Ok(mut backend) = state.backend.lock() {
                        backend.take();
                    }
                    handle.exit(0);
                });
            }
        }
    });
}
