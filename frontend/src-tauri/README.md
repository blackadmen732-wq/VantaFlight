# VantaFlight Desktop App (Tauri)

Packages VantaFlight 1.0.0 as an AppImage and a `.deb` with the VantaFlight icon.

## Build

```bash
sudo apt install libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev   # once
cd frontend
npx tauri build
```

Bundles land in `src-tauri/target/release/bundle/{appimage,deb}/`.

## How it runs

The window shows `public/splash.html` while `src/main.rs` brings up the
Flight Core. If a VantaFlight backend already answers on port 8000
(`VANTAFLIGHT_PORT`), the app uses it and leaves it running. Otherwise it
creates a venv in `~/.local/share/com.vantaflight.app` (on first launch, or when
an update changes `requirements.txt`), starts uvicorn from the bundled backend,
and stops it when the window closes. The backend serves the bundled UI, so the
window navigates to `http://127.0.0.1:8000/`.

Needs `python3` (3.10+) and `python3-venv` on the system; the `.deb` declares
them. `backend.log` in the data folder records setup and backend output.

Icons were generated from the VantaFlight logo with `npx tauri icon`.
