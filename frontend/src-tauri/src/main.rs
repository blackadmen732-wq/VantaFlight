// Prevents an extra console window on Windows in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// Minimal Tauri desktop shell. The UI is the Vite/React app; the Flight Core
// (Python/FastAPI) runs locally and the frontend talks to it over HTTP/WS.
fn main() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running VantaFlight");
}
