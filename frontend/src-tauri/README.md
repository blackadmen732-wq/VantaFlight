# VantaFlight Desktop Shell (Tauri)

This directory is the **scaffold** for packaging VantaFlight as a downloadable
Linux desktop app with [Tauri](https://tauri.app). It is intentionally **not
built** as part of the foundation PR.

The foundation runs as a local web app (Vite dev server + Python Flight Core).
Later, `npm run tauri dev` / `npm run tauri build` will wrap the same frontend
into a native window and produce `.AppImage` / `.deb` artifacts.

Before building for real you will need:

- The Rust toolchain (already available via `cargo`).
- Tauri v2 system dependencies (`libwebkit2gtk-4.1-dev`, `libgtk-3-dev`, etc.).
- An app icon at `icons/icon.png`.

No Tauri build is required to run or evaluate the foundation.
