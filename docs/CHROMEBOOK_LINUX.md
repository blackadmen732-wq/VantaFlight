# VantaFlight on a Chromebook

VantaFlight runs in ChromeOS's Linux (Crostini). The Python backend serves
the app on `http://127.0.0.1:8000`, and you use it in Chrome as an installed
app with its own window and icon. **After installing, it needs no internet
and no Wi-Fi** — the only network it uses is your drone's own Wi-Fi.

## 1. Turn on Linux (once, needs internet)

Settings → About ChromeOS → Developers → Linux development environment → Turn on.
School/work Chromebooks may have this blocked by the admin.

Check your CPU type in the Linux terminal: `uname -m`
(`x86_64` = Intel/AMD, `aarch64` = ARM). It matters only for offline bundles.

## 2. Install (once, needs internet)

```bash
sudo apt install -y git python3-venv
git clone https://github.com/blackadmen732-wq/VantaFlight.git
cd VantaFlight
scripts/install_chromebook_linux.sh
```

This creates the Python environment and builds the UI. Building the UI needs
Node 22 once (`curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash`,
reopen the terminal, `nvm install 22`). Node is not needed to run it.

**No internet on the Chromebook?** On another Linux computer with the same CPU
type and Python version, run `scripts/make_offline_bundle.sh`, copy the
`.tar.gz` over (USB stick, Files app), then:

```bash
tar xzf vantaflight-offline-*.tar.gz
scripts/install_chromebook_linux.sh --offline vantaflight-offline
```

## 3. Start it (offline from here on)

Open **VantaFlight** from the Linux apps folder, or run
`scripts/vantaflight-chrome.sh`. Chrome opens `http://127.0.0.1:8000`.
Use the ⋮ menu → **Install VantaFlight** once; from then on it has its own
window and icon in the launcher. Turn Wi-Fi off and it still works with the
mock drone, missions, failsafes, replay and training.

The Tauri AppImage/`.deb` also works in Crostini, but its WebKit window has
GPU/blank-window problems on some Chromebooks (`WEBKIT_DISABLE_DMABUF_RENDERER=1`
helps) and cannot use Chrome's Bluetooth or serial APIs. On a Chromebook,
prefer the Chrome app.

## 4. Connecting a drone

| Drone | How | Chromebook steps |
|---|---|---|
| Mock (built in) | no network | just Connect |
| Hopper (FTW) | its own Wi-Fi `Hopper_####`, camera at `http://192.168.2.1` | join the Wi-Fi, open the Hopper page → OPEN HOPPER CAMERA. Flight stays in FTW's apps (closed protocol). |
| Tello / Tello EDU | its own Wi-Fi `TELLO-XXXXXX`, SDK over UDP | join the Wi-Fi; Settings → Developers → Linux → **Port forwarding** → add **UDP 8890** (state; 11111 for video). Forwards switch off after every reboot. |
| PX4 SITL | simulator on this or another computer | see [PX4.md](PX4.md); forward UDP 14540 if the simulator runs elsewhere |

Joining a drone's Wi-Fi drops your internet — fine, VantaFlight doesn't use it.
`http://127.0.0.1:8000/api/network/check` tells you, in plain words, whether
the Hopper camera answers, whether you're on a Tello network, and which ports
to forward.

ChromeOS Linux cannot use Bluetooth directly ("Direct Bluetooth access is not
available", Chromium docs); Bluetooth drones are flown from their own Chrome
apps.

## Hopper connection model

Hopper is intentionally split into independent links:

- **Camera/Wi-Fi**: observe-only camera path
- **Telemetry**: normalized only when the active official interface exposes real values
- **Program deployment**: remains `UNKNOWN` until a supported deployment transport exists
- **Live control**: remains disabled unless an official FTW transport explicitly exposes the command capability

VantaFlight never fabricates a battery, position, altitude, velocity, command acknowledgement, or successful deployment.

## Hardware validation ladder

The desktop UI/software may be used before real control is enabled. Report progress separately as:

1. IMPLEMENTED
2. UNIT_TESTED
3. INTEGRATION_TESTED
4. FAST_SIM_VERIFIED
5. SITL_VERIFIED
6. REAL_CAMERA_VERIFIED
7. HOPPER_OBSERVE_VERIFIED
8. HOPPER_PROGRAM_VERIFIED
9. HOPPER_LIVE_CONTROL_VERIFIED
10. HOPPER_CLOSED_LOOP_VERIFIED
11. COMPETITION_VERIFIED

Do not claim a higher level from a lower one.
