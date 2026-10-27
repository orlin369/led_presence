# LED Presence

A local Flask application that turns real Shelly Presence Gen4 readings into spatial light animations. Multiple sensors and multiple Shelly NOLU Bars controllers share one room. Each controller has its own location, connected bars, animation and output connection. Distances use **mm** and angles use **degrees**.

## Run locally

Requires Python 3.12 or newer and network access to the devices.

```powershell
Set-Location 'C:\Users\User\Documents\Git\orlin369\led_presence'
.\.venv\Scripts\Activate.ps1
python app.py
```

If PowerShell blocks activation, allow local scripts for this window only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

First-time setup:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Open **http://127.0.0.1:5000**. Stop with Ctrl+C; use `deactivate` to leave the environment. The app uses Flask's local development server on loopback without debug mode or the reloader. On macOS/Linux, use `.venv/bin/python`.

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

**Presence aura** blends soft glows around detected people; **Presence ripples** produces moving waves. Each controller has independent brightness, reach, speed, fade and idle glow. Influence uses horizontal room distance because radar Z is not calibrated floor height. The animation smooths between one-second sensor updates. Without fresh tracks and with zero idle glow, it fades to black.

The backend renews `Presence.LiveTrack` every 25 seconds and receives real object events over WebSocket. Offline/stale tracks are excluded. Track IDs include the sensor ID. Tracks are not fused across sensors, so detection totals are not unique-person counts. A no-track event stays valid while connected until another track event.

Controllers start in preview-only mode. Select one on **Devices ? light controls**, verify product measurements, then use **Start presence lighting**. The app checks that bar count multiplied by pixels per bar matches that controller's configured `num_leds`, and that its DDP port matches. The original controller reports **100 pixels**, which does not divide evenly into nine identical bars; confirm the actual count and correct the device configuration first. The app does not guess counts or change `num_leds`.

Each enabled controller has an independent worker and UDP connection. Start/stop targets a controller ID; the API requires a selection when several exist. One offline controller does not stop another animation. Streaming targets 20 fps using RGB888 DDP, byte offsets and a final PUSH flag. Brightness is encoded in pixels, so controller brightness is temporarily 100. Protocol and colour order remain unchanged.

**Stop & restore previous effect** restores only the selected controller's previous supported state and removes DDP if the app added it. Output continues with the browser closed. Normal shutdown attempts restoration; after abrupt termination, select another effect in the controller UI if needed. UDP confirms sending, not physical display. Physical DDP rendering still needs confirmation after real pixel mapping is configured.

References: [Shelly Presence API](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Presence/), [LedFx DDP implementation](https://docs.ledfx.app/en/v2.1.6/devices/ddp.html).

## Separate app instances

Multiple controllers share this room. Separate installations can use another configuration directory and port:

```powershell
$env:LED_PRESENCE_INSTANCE = 'C:\LED-Presence\room-b'
.\.venv\Scripts\python.exe -m flask --app 'app:create_app()' run --host 127.0.0.1 --port 5001
```

Do not enable the reloader, which duplicates device readers and animation workers.

## Checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests cover coordinate transforms, migration, registry validation, separate locations, shared products, independent output, simultaneous loopback UDP streams, stopping one while another continues, effect restoration, bar geometry and DDP framing.

## License

[Apache License 2.0](LICENSE).
