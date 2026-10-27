# LED Presence

A local Flask application that turns real Shelly Presence Gen4 readings into spatial light animations. Multiple sensors and multiple Shelly NOLU Bars controllers share one room. Each controller has its own location, connected bars, animation and output connection. Distances use **mm** and angles use **degrees**.

## Installation

All platforms need Python 3.12 or newer, Git and a computer on the same network as the Shelly devices. Each section installs the app into a local virtual environment (`.venv`) and starts it. Afterwards, open **http://127.0.0.1:5000** on the same computer and stop the app with Ctrl+C.

The app uses Flask's local development server on loopback only, without debug mode or the reloader. On first start it creates `instance/config.json` from `config.example.json`; enter your own device addresses on **Devices** and your measurements on **Geometry** (see [Per-installation configuration](#per-installation-configuration)).

### Windows

Install Python from [python.org](https://www.python.org/downloads/) (tick **Add python.exe to PATH**) and [Git for Windows](https://git-scm.com/download/win). Then, in PowerShell:

```powershell
git clone https://github.com/orlin369/led_presence.git
Set-Location led_presence
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

To start it later, from the repository folder:

```powershell
.\.venv\Scripts\Activate.ps1
python main.py
```

If PowerShell blocks activation, allow local scripts for this window only, then activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

Use `deactivate` to leave the environment.

### Linux

Install Python and Git with your package manager. On Debian or Ubuntu, `venv` is a separate package:

```bash
sudo apt install python3 python3-venv git
```

Check that `python3 --version` reports 3.12 or newer. Then:

```bash
git clone https://github.com/orlin369/led_presence.git
cd led_presence
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py
```

To start it later, run `.venv/bin/python main.py` from the repository folder, or `source .venv/bin/activate` followed by `python main.py`.

### macOS

The system `python3` may be older than 3.12. Install a current version from [python.org](https://www.python.org/downloads/macos/) or with [Homebrew](https://brew.sh/) (`brew install python@3.12 git`). Then, in Terminal:

```bash
git clone https://github.com/orlin369/led_presence.git
cd led_presence
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py
```

To start it later, run `.venv/bin/python main.py` from the repository folder, or `source .venv/bin/activate` followed by `python main.py`. macOS may ask whether Python can find devices on the local network; allow it, or the sensors and controllers cannot be reached.

## Pages and setup flow

1. **Devices** (`/devices`): add, name, enable, disable or remove sensors and light controllers. Set IP addresses, controller component IDs, DDP ports and product types here. Below the registry, select a controller for its live preview, animation settings and independent start/stop controls (`/devices#lights`). Device IDs stay stable when names or locations change.
2. **Geometry** (`/geometry`): separate **Room**, **Sensors** and **Lights** tabs. Room sets dimensions and calibration notes. Sensors sets mounting for registered sensors and shows live readings. Lights selects a controller and sets its location, shared product measurements and bar connections. Direct links: `/geometry#room`, `/geometry#sensors`, `/geometry#lights`.
3. **Dashboard** (`/`): the shared room plan, presence summary and object coordinates. All sensor and controller locations are shown; bar labels include the controller name. Viewpoint controls change only the drawing.

There is no separate Lights navigation tab. Geometry's Lights tab is for physical placement and connections; Devices contains operating settings and controls. Old `/lights` and `/nolu` links redirect to `/devices#lights`. Unsaved light edits stay with their controller when switching selections.

Saving device identity preserves existing geometry and animation. Saving sensor geometry preserves device names, addresses and enabled states. Saving one controller preserves the others. A change to a shared product affects every controller using it and stops/restores those controllers' active output first. A change to one controller stops only its output. Removing or disabling a device stops its reader while other devices continue independently.

Room and Sensors tabs have independent saves and retain unsaved edits. Click-to-place works only on Sensors. Tabs support arrow keys, Home and End.

## Per-installation configuration

The active configuration is **`instance/config.json`**, excluded from Git. Back it up separately. `config.example.json` supplies defaults only when creating a new instance. Editing the example does not override an existing installation. Stop the app before editing active JSON directly, then restart it. The browser picks up updated values when its form has no unsaved edits.

Schema version **6** adds multiple controllers and a shared product catalogue. Existing configuration migrates automatically, preserving the room, sensors, original controller location, bar connections, product and animation. The old file is backed up as `instance/config.v<old-version>.backup.json`. Earlier migrations convert metres to mm and adopt sensor-facing reference walls without changing entered mounting distances.

| Configuration | Meaning |
| --- | --- |
| `installation_name`, `calibrated`, `physical.notes` | Installation label and verification status/notes |
| `room.width_mm`, `depth_mm`, `height_mm` | Shared room dimensions |
| `sensors[]` | Sensor IDs, names, addresses, enabled states and mounting |
| `lighting.products[]` | Shared bar definitions: ID, name, length, pixels per bar, verification |
| `lighting.controllers[]` | Controller IDs, names, enabled states, product references, device settings, bars and animation |
| `lighting.controllers[].controller` | IPv4 address, component ID, DDP port and mounting |
| `lighting.controllers[].product_id` | Reference to a shared product |
| `lighting.controllers[].bars[]` | Connected bar instances and electrical pixel order |
| `lighting.controllers[].animation` | This controller's presence effect settings |

Up to 32 sensors, 16 controllers and 16 product definitions are supported. Each controller supports up to 64 bars and 10000 mapped pixels. Controller address/component pairs must be unique; sensor IPs and IDs must be unique within their category. The current connector uses local Shelly Presence and LedStrip/DDP APIs with authentication disabled. Empty sensor/controller lists are allowed.

## Mounting and room references

Sensors and controllers use the same mounting structure:

```json
"mounting": {
  "height": 700,
  "left_wall": 1700,
  "front_wall": 390,
  "yaw": 0,
  "pitch": 0,
  "roll": 0
}
```

**FACING THE SELECTED SENSOR** is the default view. In the zero-yaw reference view, LEFT REFERENCE WALL is on the viewer's left, FRONT REFERENCE WALL near the sensor, RIGHT WALL on the right and BACK WALL behind the viewer. Origin is the FRONT/LEFT intersection. Room X increases towards RIGHT WALL and Y towards BACK WALL. Wall names stay attached to the same boundaries in every viewpoint.

`left_wall` and `front_wall` measure from the named reference walls; height is above the floor. Yaw 0 points towards BACK WALL. Pitch is up/down tilt; roll rotates around the local forward axis. The radar transform currently applies horizontal placement and yaw only. Device mounting height/tilt are displayed separately and are not changed by saving app settings.

Device +X points opposite to room +X. Device metres are converted to mm once:

```text
reported_x_mm = device_x * 1000
reported_y_mm = device_y * 1000
room_x_mm = mounting.left_wall - reported_x_mm * cos(yaw) + reported_y_mm * sin(yaw)
room_y_mm = mounting.front_wall + reported_x_mm * sin(yaw) + reported_y_mm * cos(yaw)
```

Reported Z/minZ/maxZ are converted to mm without another vertical/tilt correction. Verify reporting conventions using known points before extending to full 3D. Room height is stored for future extensions and does not clip tracks. Millimetre units do not imply millimetre sensor accuracy. Example dimensions and initial locations are illustrative; measure and verify the actual installation.

## Unified NOLU product and connected bars

Define an identical bar type once in `lighting.products`. Length and pixel count apply to every controller referencing it. Zero pixels means unknown; software preview uses 12 samples per bar until a count is supplied. The starter 300 mm length is illustrative. Each new controller starts with a nine-bar chain.

```json
{
  "id": "bar-2",
  "product_id": "shelly-nolu-bar",
  "connected_to": "bar-1",
  "wire_order": 2,
  "yaw": 0,
  "pitch": 0,
  "roll": 0,
  "reversed": false
}
```

The first bar connects to `controller`; other bars start at their parent's end. Joint angles are relative to the parent's orientation. Lengths and rotations propagate through the chain. The matrix order is yaw, pitch, roll in the local basis, followed by conversion to viewer-reference axes and the fixed **180-degree NOLU product-frame alignment**. This corrects calculated positions without adding offsets to saved fields or moving the controller anchor.

A controller-rooted tree can represent branches geometrically; match supported physical wiring. Cycles, missing parents and duplicate IDs/orders are rejected. Reconnect children before removing a parent. `wire_order` is the actual electrical sequence within this controller, starting at 1. `reversed` flips pixels inside a bar without changing geometry. Different controllers may each have `bar-1`; aggregate identities include the controller ID.

## Presence animation and physical output

### Editable dashboard animation program

The first sensor in the Devices list is always the reference for the sensor-facing view and dashboard animation. There is no reference-sensor dropdown. Choose an effect in the **Animation** dropdown, then use **Start animation** beside it. You can change effects while running; each controller clears its previous trail and blends into the new effect. The button becomes **Stop animation**. Fresh tracks from the first sensor drive every enabled light controller. If it is disabled or offline, the app does not substitute another sensor. Changing the first sensor stops an active program; restart to use the new reference. The program runs on the server, so changing pages or closing the browser does not stop it; reloading the dashboard shows its current effect and state. App startup leaves it stopped.

Available effects:

- **Spotlight:** a small, fixed-width light spot follows each person in their colour; its width does not change with proximity.
- **Presence ribbons:** coloured halos follow people and leave a fading wake.
- **Ripples:** waves spread out from each person's projected position along the lights.
- **Fireflies:** clusters of twinkling sparks follow people.

All effects fade to black without fresh tracks. The browser remembers the last dropdown choice; a new browser defaults to Spotlight. Effects and their labels are defined in `led_presence/animations/presence_animation.py` (`ANIMATIONS`); `context.animation_id` selects the renderer. Restart the app after editing this file.

Edit **[led_presence/animations/presence_animation.py](led_presence/animations/presence_animation.py)** while the application is stopped:

```powershell
# Stop the app with Ctrl+C first, then edit led_presence/animations/presence_animation.py.
python -m py_compile led_presence/animations/presence_animation.py
python main.py
```

The file is imported once at startup. There is no hot reload or browser code editor. Its `render(context)` function receives elapsed time, the configured LED count, the selected sensor, fresh room-coordinate tracks, room dimensions and controller settings. Return exactly `pixel_count` RGB triples, with integer channels from 0 to 255. Keep the function fast and non-blocking; the app handles transport, smoothing and restoration.

The initial program, **Presence ribbons**, projects each person onto the nearest section of the lights using room coordinates and the saved bar geometry. A bright core follows movement, surrounded by a halo and fading trail in the person's colour. Distance from the sensor changes the halo width. Multiple people create separate pools of light, and an empty/offline source fades to black. Effects return full-range colour; brightness (default 25%) is applied when frames are sent. Constants near the top of the file control halo width and trail duration.

The program reads the actual configured LED count (27 on the original controller: nine bars with three addressable LEDs each). Until pixels per bar are verified, it estimates LED positions at equal density along the bars in electrical order; the preview labels this approximation. It honours each bar's reversed wiring and the controller's saved location and orientation. Accurate positioning requires verified bar dimensions and pixel counts matching the controller. `render(context)` also receives `pixel_positions` in mm and a persistent per-controller `state` dictionary, reset on Start. Spatial effects below retain their stricter pixel-mapping checks.

Start checks every enabled destination before changing lights. If startup fails, already-started controllers are restored. Stop restores previous effects and supported settings. Invalid RGB output stops that controller, restores its effect and reports an error. Editing the room or selected sensor stops the program to avoid stale coordinates. Stop and restart after enabling additional controllers to include them.

**Presence aura** blends soft glows around detected people; **Presence ripples** produces moving waves. Each controller has independent brightness, reach, speed, fade and idle glow. Influence uses horizontal room distance because radar Z is not calibrated floor height. The animation smooths between one-second sensor updates. Without fresh tracks and with zero idle glow, it fades to black.

The backend renews `Presence.LiveTrack` every 25 seconds and receives real object events over WebSocket. Offline/stale tracks are excluded. Track IDs include the sensor ID. Tracks are not fused across sensors, so detection totals are not unique-person counts. A no-track event stays valid while connected until another track event.

Each live track gets a unique colour from a fixed LED-friendly palette (`led_presence/core/track_colours.py`). The dashboard draws the person in that colour and every animation lights them in it. A person keeps their colour while tracked and for 3 seconds after a dropout. Beyond eight simultaneous people, colours repeat. Sensor markers keep their own per-sensor colours.

Controllers start in preview-only mode. For **spatial per-bar effects**, select one on **Devices → light controls**, verify product measurements, then use **Start presence lighting**. Bar count multiplied by pixels per bar must match that controller's `num_leds`, and the DDP port must match. Each Shelly NOLU Bar has **3 addressable LEDs**, verified with DDP test frames. The original nine-bar controller therefore uses `num_leds` 27. If the device reports more LEDs than physically exist, light aimed at the missing addresses is invisible and the lamp appears dark or unresponsive. The dashboard program uses the reported count directly with estimated positions until mapping is verified. Neither path changes `num_leds`.

Each enabled controller has an independent worker and UDP connection. Start/stop targets a controller ID; the API requires a selection when several exist. One offline controller does not stop another animation. Streaming targets 20 fps using RGB888 DDP, byte offsets and a final PUSH flag. Brightness is encoded in pixels, so controller brightness is temporarily 100. Protocol and colour order remain unchanged.

**Stop & restore previous effect** restores only the selected controller's previous supported state and removes DDP if the app added it. Output continues with the browser closed. Normal shutdown attempts restoration; after abrupt termination, select another effect in the controller UI if needed. UDP confirms sending, not physical display. Physical DDP rendering still needs confirmation after real pixel mapping is configured.

References: [Shelly Presence API](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Presence/), [LedFx DDP implementation](https://docs.ledfx.app/en/v2.1.6/devices/ddp.html).

## Separate app instances

Multiple controllers share this room. Separate installations can use another configuration directory and port:

```powershell
$env:LED_PRESENCE_INSTANCE = 'C:\LED-Presence\room-b'
.\.venv\Scripts\python.exe -m flask --app 'led_presence.web.app:create_app()' run --host 127.0.0.1 --port 5001
```

Do not enable the reloader, which duplicates device readers and animation workers.

## Development rules

The project rules are in [AGENTS.md](AGENTS.md). Python uses PEP 8, a
portable interpreter line and UTF-8 declaration, one class per module,
and module-level helpers instead of nested functions. `main.py` enters
through `main()`; `led_presence/web/app.py` contains the Flask factory and HTTP handlers.
Imports do not start device workers. Configuration, connections and
streaming live in separate modules. `.editorconfig` sets UTF-8 and spaces.

Python application code uses the `led_presence` package namespace, with
absolute imports and minimal package initializers. Keep new modules in the
package for their responsibility:

```text
main.py                         # Launcher: main()
led_presence/
  core/                         # Installation, room geometry, shared paths
  config/                       # Installation validation and migrations
  sensors/                      # Sensor readers
  lights/                       # Controller configuration, geometry, output
  animations/                   # Editable presence effects
  web/                          # Flask factory and HTTP handlers
    templates/                  # Page templates
    static/                     # JavaScript and CSS
tests/                          # Isolated functional and convention checks
```

For example: `from led_presence.sensors.sensor_reader import SensorReader`.
Only the launcher belongs at the repository root; avoid flat helper modules
or changes to `sys.path` to work around package imports.

Install development tools into the virtual environment:

```powershell
python -m pip install -r requirements-dev.txt
```

## Checks

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Use `python -m ruff format .` to format Python files. The test suite also checks headers, one class per file, function scope and the `main()` entry point.

Tests cover coordinate transforms, migration, registry validation, separate locations, shared products, independent output, simultaneous loopback UDP streams, stopping one while another continues, effect restoration, bar geometry, DDP framing, the editable animation contract, selected-sensor filtering, all-controller preflight/rollback, source changes and recovery from invalid program output.

## License

[Apache License 2.0](LICENSE).
