// Prevents an extra console window on Windows in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! VantaFlight desktop shell.
//!
//! The window opens on a small splash page while this process brings up the
//! Flight Core (the Python/FastAPI backend bundled as a resource):
//!
//! 1. If a VantaFlight backend already answers on the port, use it and leave
//!    it running on exit (someone else started it, e.g. `./start.sh`).
//! 2. Otherwise make sure a private Python venv with the backend's
//!    requirements exists in the app's data directory (first launch, or after
//!    an update changed the requirements), start uvicorn from it, and stop it
//!    again when the app exits.
//!
//! The backend also serves the built UI, so the window then simply navigates
//! to it and the app talks to its API same-origin, exactly like in a browser.

use std::fs;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent, WebviewWindow};

const DEFAULT_PORT: u16 = 8000;
const HEALTH_TIMEOUT: Duration = Duration::from_secs(60);

/// The backend process we started, if any. Killed on exit.
struct Backend(Mutex<Option<Child>>);

fn port() -> u16 {
    std::env::var("VANTAFLIGHT_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(DEFAULT_PORT)
}

/// True when a VantaFlight backend answers `/api/health` on `port`.
fn backend_healthy(port: u16) -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(500)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let request = format!(
        "GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut body = String::new();
    let _ = stream.read_to_string(&mut body);
    body.starts_with("HTTP/1.1 200") && body.contains("\"status\":\"ok\"")
}

/// Show a line of progress (or an error) on the splash page.
fn splash(window: &WebviewWindow, message: &str, failed: bool) {
    let msg = serde_json::to_string(message).unwrap_or_else(|_| "\"\"".into());
    let _ = window.eval(&format!("window.vfStatus && window.vfStatus({msg}, {failed})"));
}

/// A command for a system Python. The AppImage runtime exports PYTHONHOME and
/// its own LD_LIBRARY_PATH for the bundled libraries; both break the system
/// interpreter (it cannot even find `encodings`), so they are dropped here.
fn python(program: impl AsRef<std::ffi::OsStr>) -> Command {
    let mut cmd = Command::new(program);
    cmd.env_remove("PYTHONHOME");
    if std::env::var_os("APPIMAGE").is_some() {
        cmd.env_remove("LD_LIBRARY_PATH");
    }
    cmd
}

fn run(cmd: &mut Command, log: &Path) -> Result<(), String> {
    let out = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(log)
        .map_err(|e| format!("cannot write {}: {e}", log.display()))?;
    let err = out.try_clone().map_err(|e| e.to_string())?;
    let status = cmd
        .stdout(Stdio::from(out))
        .stderr(Stdio::from(err))
        .status()
        .map_err(|e| format!("{:?} failed to start: {e}", cmd.get_program()))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("{:?} exited with {status}; see {}", cmd.get_program(), log.display()))
    }
}

/// Create (or refresh) the backend's venv. Returns its python.
fn ensure_venv(window: &WebviewWindow, data: &Path, backend: &Path, log: &Path) -> Result<PathBuf, String> {
    let venv = data.join("venv");
    let venv_python = venv.join("bin").join("python");
    let requirements = backend.join("requirements.txt");
    let wanted = format!(
        "{}\n{}",
        env!("CARGO_PKG_VERSION"),
        fs::read_to_string(&requirements).map_err(|e| format!("missing {}: {e}", requirements.display()))?
    );
    let stamp = venv.join(".vantaflight-requirements");
    if venv_python.exists() && fs::read_to_string(&stamp).ok().as_deref() == Some(wanted.as_str()) {
        return Ok(venv_python);
    }

    splash(window, "First launch: setting up the Flight Core (this takes a few minutes)…", false);
    let check = python("python3")
        .args(["-c", "import sys, venv; assert sys.version_info >= (3, 10)"])
        .status();
    if !matches!(check, Ok(s) if s.success()) {
        return Err("Python 3.10+ with venv is required. Install it with: sudo apt install python3 python3-venv".into());
    }
    if !venv_python.exists() {
        run(python("python3").args(["-m", "venv"]).arg(&venv), log)?;
    }
    splash(window, "Installing Flight Core packages…", false);
    run(
        python(&venv_python).args(["-m", "pip", "install", "--disable-pip-version-check", "-r"]).arg(&requirements),
        log,
    )?;
    fs::write(&stamp, wanted).map_err(|e| e.to_string())?;
    Ok(venv_python)
}

fn start_backend(app: &tauri::AppHandle, window: &WebviewWindow, port: u16) -> Result<Option<Child>, String> {
    if backend_healthy(port) {
        return Ok(None);
    }
    let resources = app.path().resource_dir().map_err(|e| e.to_string())?;
    let backend = resources.join("backend");
    let ui = resources.join("ui");
    let data = app.path().app_data_dir().map_err(|e| e.to_string())?;
    fs::create_dir_all(&data).map_err(|e| e.to_string())?;
    let log = data.join("backend.log");

    let venv_python = ensure_venv(window, &data, &backend, &log)?;
    splash(window, "Starting the Flight Core…", false);
    let out = fs::OpenOptions::new().create(true).append(true).open(&log).map_err(|e| e.to_string())?;
    let err = out.try_clone().map_err(|e| e.to_string())?;
    let child = python(venv_python)
        .args(["-m", "uvicorn", "vantaflight.main:app", "--host", "127.0.0.1", "--port"])
        .arg(port.to_string())
        .current_dir(&data)
        .env("PYTHONPATH", &backend)
        .env("VANTAFLIGHT_DB", data.join("vantaflight.db"))
        .env("VANTAFLIGHT_FRONTEND_DIST", &ui)
        .stdout(Stdio::from(out))
        .stderr(Stdio::from(err))
        .spawn()
        .map_err(|e| format!("could not start the Flight Core: {e}"))?;
    Ok(Some(child))
}

fn boot(app: tauri::AppHandle) {
    let Some(window) = app.get_webview_window("main") else { return };
    let port = port();
    match start_backend(&app, &window, port) {
        Ok(child) => {
            let ours = child.is_some();
            *app.state::<Backend>().0.lock().unwrap() = child;
            let deadline = Instant::now() + HEALTH_TIMEOUT;
            while !backend_healthy(port) {
                let exited = app
                    .state::<Backend>()
                    .0
                    .lock()
                    .unwrap()
                    .as_mut()
                    .map(|c| matches!(c.try_wait(), Ok(Some(_))))
                    .unwrap_or(false);
                if exited || Instant::now() > deadline {
                    let hint = if ours { " (see backend.log in the app data folder)" } else { "" };
                    splash(&window, &format!("The Flight Core did not start{hint}."), true);
                    return;
                }
                std::thread::sleep(Duration::from_millis(250));
            }
            let url = format!("http://127.0.0.1:{port}/");
            if let Ok(url) = url.parse() {
                let _ = window.navigate(url);
            }
        }
        Err(e) => splash(&window, &e, true),
    }
}

fn stop_backend(app: &tauri::AppHandle) {
    if let Some(mut child) = app.state::<Backend>().0.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn main() {
    tauri::Builder::default()
        .manage(Backend(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();
            std::thread::spawn(move || boot(handle));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building VantaFlight")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                stop_backend(app);
            }
        });
}
