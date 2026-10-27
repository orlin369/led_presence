# LED Presence

A locally hosted Python Flask dashboard for multiple Shelly Presence Gen4 sensors. Displays real object tracks on one shared room plan, with separate presence, illumination, Wi-Fi and mounting readings for each sensor. All application distances, coordinates and geometry use **millimetres (mm)**. Angles use degrees. The backend connects to each device independently over local WebSocket RPC; the browser refreshes every second. No cloud service is required.

## Run locally

Requires Python 3.12 or newer and network access to the sensors.

To activate the existing virtual environment in PowerShell:

```powershell
Set-Location 'C:\Users\User\Documents\Git\orlin369\led_presence'
.\.venv\Scripts\Activate.ps1
```

Your prompt will show `(.venv)`. You can then run `python app.py`. Use `deactivate` to leave the environment.

If PowerShell reports that running scripts is disabled, allow local scripts for the current PowerShell session, then activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

This change expires when you close the PowerShell window and does not require administrator rights. You can also run the app without activation using `.\.venv\Scripts\python.exe app.py`.

For first-time setup:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Open **http://127.0.0.1:5000**. Stop with Ctrl+C. The starter uses Flask's development server on loopback, without debug mode, for local use. On macOS/Linux, use `.venv/bin/python`.

## Installation settings

All installation-specific geometry, device addresses and physical parameters live in **`instance/config.json`**, excluded from Git. The first run copies `config.example.json`. Saving the installation form writes this file atomically without changing the devices' mounting or detection settings. Back up the file separately.

**Edit `instance/config.json` for the current installation, not `config.example.json`.** The example is only used when creating a new instance and does not override an existing configuration. After editing the active JSON directly, stop the app with Ctrl+C and run `python app.py` again. The browser picks up the running app's updated configuration automatically when the form has no unsaved edits; reload the page to discard unsaved edits.

Existing configurations are automatically migrated to version 3. Version 1 distances are converted from metres to mm; version 2 flat placement fields are grouped under `mounting`, with tilt preserved as pitch and roll initialized to 0. The original is preserved as `instance/config.v1.backup.json` or `instance/config.v2.backup.json`. Loading version 3 does not convert values again.

Room dimensions and placement are examples, not measured geometry. Measure them and check coordinate directions using known physical points before marking the installation calibrated. Millimetre units do not imply millimetre sensor accuracy.

| Parameter | Meaning |
| --- | --- |
| `schema_version` | 3: millimetres, multiple sensors and nested mounting |
| `installation_name`, `calibrated` | Installation label and verification status |
| `room.width_mm`, `room.depth_mm`, `room.height_mm` | Rectangular room dimensions |
| `sensors` | Up to 32 sensor configurations; an empty list is allowed |
| `sensors[].id` | Stable, unique sensor identifier |
| `sensors[].name` | Human-readable name |
| `sensors[].device_host` | Unique IPv4 address; local authentication must currently be disabled |
| `sensors[].enabled` | Whether the app connects to this sensor |
| `sensors[].mounting.left_wall`, `sensors[].mounting.front_wall` | Distances in mm from the left and front reference walls |
| `sensors[].mounting.yaw` | Orientation: 0 degrees faces into the room from the front reference wall; positive turns counterclockwise on the plan |
| `sensors[].mounting.height` | Mounting height above floor in mm |
| `sensors[].mounting.pitch`, `sensors[].mounting.roll` | Up/down tilt and rotation about the facing direction, in degrees; stored for future 3D geometry |
| `physical.notes` | Installation and calibration notes |

Each sensor has a physical mounting object. Distances use mm and angles use degrees:

```json
"mounting": {
  "height": 700,
  "left_wall": 1700,
  "front_wall": 0,
  "yaw": 0,
  "pitch": 0,
  "roll": 0
}
```

`front_wall` preserves the second horizontal room-position measurement. Zero in this example is a placeholder, not a confirmed wall offset. Yaw 0 faces into the room from the front reference wall; positive yaw turns counterclockwise on the top view. Pitch means up/down tilt; roll means rotation around the facing direction. **Only yaw is currently applied to the 2D map.** Height, pitch and roll remain reference parameters until the sensor's vertical coordinate convention is calibrated. The device's reported tilt is displayed separately and is not changed by saving these values.

## Multiple sensors

Click **Add sensor**, enter its name, IP address, position and orientation, then **Save installation**. Select **Place on plan** for a sensor to set its distances from the reference walls by clicking the map. Changes preview immediately; save to apply them. Disable a sensor to stop its connection while retaining its configuration, or remove it from the installation.

Each sensor has its own colour, status, WebSocket connection and reconnect loop. One offline sensor does not block the others. Editing geometry does not restart a working connection. Removing, disabling or changing a sensor's address stops its old reader. Sensor IDs remain stable when names or positions change.

Track identifiers include the sensor ID, for example `sensor-1:7` and `sensor-2:7`. **Tracks are not fused across sensors**: the same person may appear more than once. The total is a detection count, not a unique-person count. The room is occupied when any online sensor reports occupancy; it is clear only when all enabled sensors are online and report clear. Otherwise coverage is unknown/partial.

## Room-plan viewpoint

Use the **Viewpoint** dropdown above the plan to look from the front, opposite, left or right wall. **Facing the selected sensor (my view)** shows the room looking toward the sensor selected with **Place on plan**, including its yaw. Your choice is remembered in this browser for this app address. Select another sensor to view it from the opposite direction.

Viewpoint changes rotate only the drawing: sensor configuration, reference-wall names and coordinate-table values remain fixed. Labels stay readable, and clicking to place a sensor still saves the correct mounting distances in every view.

## Geometry and device units

The **front reference wall** is the bottom edge of the plan and the **left reference wall** is its left edge. These are fixed room references, independent of which way a person or sensor is facing. The mounting form uses distance from these walls and height above the floor. Internally, these distances map to X and Y. The room origin `(0, 0)` is at their bottom-left intersection; +X points right and +Y up. At yaw 0 degrees, reported +X/+Y align with the room axes. The device protocol sends metres; `geometry.py` converts readings to mm once before computing positions:

```text
reported_x_mm = device_x * 1000
reported_y_mm = device_y * 1000
room_x_mm = sensor_x_mm + reported_x_mm * cos(yaw) - reported_y_mm * sin(yaw)
room_y_mm = sensor_y_mm + reported_x_mm * sin(yaw) + reported_y_mm * cos(yaw)
```

The API and table also expose reported Z, minZ and maxZ converted to mm. No extra tilt or vertical transform is applied. Device mounting height is converted to mm for comparison with local reference measurements. Diagnostic data contains selected understood fields rather than publishing a mixture of native device distance units. Room height is saved for future geometry extensions and does not clip tracks. Outside-room tracks remain visible with dashed outlines.

Future polygon rooms, calibration or track fusion can extend `geometry.py` separately from device connections. The app enables temporary live tracking with `Presence.LiveTrack` and renews it every 25 seconds; it does not change other device settings. Lost connections and stale tracks are excluded from the live map. A `no_track` event stays valid while connected until a new track event arrives.

Protocol reference: [Shelly Presence documentation](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Presence/).

## Separate app instances

Point `LED_PRESENCE_INSTANCE` at a different directory to use another installation's `config.json`. Use a separate port for each simultaneous instance:

```powershell
$env:LED_PRESENCE_INSTANCE = 'C:\LED-Presence\room-b'
.\.venv\Scripts\python.exe -m flask --app 'app:create_app()' run --host 127.0.0.1 --port 5001
```

Do not enable Flask's reloader, which would create duplicate readers. Stop the app before editing JSON directly; restart afterward. The UI applies saved changes immediately.

## Checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## License

[Apache License 2.0](LICENSE).
