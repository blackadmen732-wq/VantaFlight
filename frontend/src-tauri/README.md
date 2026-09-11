# VantaFlight Desktop Shell (Tauri)

This directory builds the React frontend into a Linux desktop shell using
[Tauri 2](https://v2.tauri.app/).

## Build on Ubuntu 22.04

Install the [Tauri system dependencies](https://v2.tauri.app/start/prerequisites/):

```bash
sudo apt-get update
sudo apt-get install -y pkg-config libwebkit2gtk-4.1-dev build-essential \
  curl wget file libxdo-dev libssl-dev librsvg2-dev \
  libayatana-appindicator3-dev patchelf
```

With nvm and rustup installed, run from the repository root:

```bash
source "$HOME/.nvm/nvm.sh"
nvm install 22.22.2
nvm use 22.22.2
rustup toolchain install 1.94.0 --profile minimal --component rustfmt --component clippy
bash scripts/install.sh
cd frontend
npm run tauri build
```

The Rust toolchain is pinned in `rust-toolchain.toml`, and `Cargo.lock` records
the resolved desktop dependencies. Packages are written under
`src-tauri/target/release/bundle/`.

To check Rust formatting and lints from `frontend`:

```bash
cargo +1.94.0 fmt --manifest-path src-tauri/Cargo.toml --check
cargo +1.94.0 clippy --manifest-path src-tauri/Cargo.toml --locked --all-targets -- -D warnings
```

To regenerate the bundled icon from `frontend`:

```bash
npm run tauri icon -- src-tauri/app-icon.svg --output src-tauri/icons --png 512
```

## Integration status

A successful package build verifies compilation and bundling only. The Python
Flight Core is not bundled or launched by this shell. Production API/WebSocket
routing from the packaged frontend still needs implementation and end-to-end
verification before this is a standalone flight-control app.

For the existing flight workflows, run the Python backend and Vite dev server
as described in the root README. The Vite development proxy supplies the
API/WebSocket routing.
