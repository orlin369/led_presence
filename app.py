import copy
import atexit
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import threading
import time

from flask import Flask, jsonify, redirect, render_template, request, url_for
import requests
import websocket

from geometry import place_track
from lighting import default_lighting
from light_controllers import LightControllers, validate_controllers, wrap_single_lighting

ROOT = Path(__file__).resolve().parent


def migrate_config(config):
    """Upgrade legacy units and flat placement fields without losing measurements."""
    result = copy.deepcopy(config)
    if result.get("schema_version") == 1:
        result["schema_version"] = 2
        result["room"] = {key.replace("_m", "_mm"): value * 1000 for key, value in result["room"].items()}
        sensor = {key.replace("_m", "_mm") if key.endswith("_m") else key:
                  value * 1000 if key.endswith("_m") else value for key, value in result.pop("sensor").items()}
        sensor.update(id="sensor-1", name="Presence 1", device_host=result.pop("device_host"), enabled=True)
        result["sensors"] = [sensor]
    if result.get("schema_version") == 2:
        for sensor in result["sensors"]:
            sensor["mounting"] = {"height": sensor.pop("mount_height_mm"),
                                  "left_wall": sensor.pop("x_mm"), "front_wall": sensor.pop("y_mm"),
                                  "yaw": sensor.pop("yaw_deg"), "pitch": sensor.pop("tilt_deg"), "roll": 0}
        result["schema_version"] = 3
    if result.get("schema_version") == 3:
        result["lighting"] = default_lighting()
        result["schema_version"] = 4
    if result.get("schema_version") == 4:
        # Keep measured offsets; LEFT now means the reference wall in the sensor-facing view.
        result["schema_version"] = 5
    if result.get("schema_version") == 5:
        result["lighting"] = wrap_single_lighting(result["lighting"])
        result["schema_version"] = 6
    return result


def validate_config(config):
    if config.get("schema_version") != 6:
        raise ValueError("schema_version must be 6 (multiple light controllers; mm and degrees)")
    if not isinstance(config["installation_name"], str) or not config["installation_name"].strip():
        raise ValueError("Provide an installation name")
    if not isinstance(config["calibrated"], bool):
        raise ValueError("calibrated must be true or false")

    def number(group, key, positive=False):
        value = group[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{key} must be a finite number")
        if positive and not 0 < value <= 1_000_000:
            raise ValueError(f"{key} must be between 0 and 1000000 mm")
        if not positive and abs(value) > 1_000_000:
            raise ValueError(f"{key} is outside the supported range")

    for key in ("width_mm", "depth_mm", "height_mm"):
        number(config["room"], key, positive=True)
    if not isinstance(config["sensors"], list) or len(config["sensors"]) > 32:
        raise ValueError("sensors must be a list of up to 32 sensors")
    ids, hosts = set(), set()
    for sensor in config["sensors"]:
        if not isinstance(sensor["id"], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", sensor["id"]):
            raise ValueError("Sensor IDs must use 1-64 letters, digits, underscores or hyphens")
        if sensor["id"] in ids:
            raise ValueError("Sensor IDs must be unique")
        ids.add(sensor["id"])
        if not isinstance(sensor["name"], str) or not sensor["name"].strip():
            raise ValueError("Provide a name for each sensor")
        host = str(ipaddress.IPv4Address(sensor["device_host"]))
        if host in hosts:
            raise ValueError("Each sensor must have a different IPv4 address")
        hosts.add(host)
        if not isinstance(sensor["enabled"], bool):
            raise ValueError("Sensor enabled must be true or false")
        for key in ("height", "left_wall", "front_wall", "yaw", "pitch", "roll"):
            number(sensor["mounting"], key, positive=key == "height")
    if not isinstance(config["physical"]["notes"], str):
        raise ValueError("physical.notes must be text")
    validate_controllers(config["lighting"])
    return config


class SensorReader:
    """One independent connection and reconnect loop per physical sensor."""
    def __init__(self, sensor):
        self.sensor_id = sensor["id"]
        self.host = sensor["device_host"]
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.ws = None
        self.state = {"connected": False, "error": None, "tracks": [], "last_event_at": None,
                      "device": {}, "status": {}, "mounting": {}, "zones": []}

    def start(self):
        threading.Thread(target=self.run, daemon=True, name=f"shelly-{self.sensor_id}").start()

    def stop(self):
        self.stop_event.set()
        if self.ws:
            self.ws.close()

    def snapshot(self, sensor):
        with self.lock:
            result = copy.deepcopy(self.state)
        result.update(id=sensor["id"], name=sensor["name"], enabled=sensor["enabled"], device_host=self.host)
        result["stale"] = not result["last_event_at"] or (bool(result["tracks"]) and time.time() - result["last_event_at"] > 5)
        result["tracks"] = [place_track(track, sensor) for track in result["tracks"]]
        return result

    def handle_message(self, message):
        with self.lock:
            for event in message.get("params", {}).get("events", []):
                if event.get("component") == "presence" and event.get("event") in ("track", "no_track"):
                    self.state["tracks"] = event.get("object", []) if event["event"] == "track" else []
                    self.state["last_event_at"] = time.time()

    def run(self):
        session = requests.Session()
        session.trust_env = False
        try:
            while not self.stop_event.is_set():
                try:
                    def rpc(method):
                        response = session.get(f"http://{self.host}/rpc/{method}", timeout=4)
                        response.raise_for_status()
                        result = response.json()
                        if isinstance(result, dict) and "code" in result and "message" in result:
                            raise ValueError(result["message"])
                        return result

                    device = rpc("Shelly.GetDeviceInfo")
                    sensor_config = rpc("Presence.GetConfig").get("sensor", {})
                    mounting = {"height_mm": sensor_config.get("height", 0) * 1000,
                                "tilt_deg": sensor_config.get("tilt"), "flipped": sensor_config.get("flipped")}
                    if self.stop_event.is_set():
                        break
                    self.ws = websocket.create_connection(f"ws://{self.host}/rpc", timeout=2, http_no_proxy=[self.host])
                    with self.lock:
                        self.state.update(connected=True, error=None, device=device, mounting=mounting,
                                          tracks=[], last_event_at=None)
                    refresh_at = renew_at = 0
                    while not self.stop_event.is_set():
                        now = time.monotonic()
                        if now >= renew_at:
                            self.ws.send(json.dumps({"id": 1, "src": f"led_{self.sensor_id}", "method": "Presence.LiveTrack"}))
                            renew_at = now + 25
                        if now >= refresh_at:
                            raw_status = rpc("Shelly.GetStatus")
                            # Only publish understood fields; distances exposed by this app are always mm.
                            status = {key: raw_status[key] for key in ("wifi", "illuminance:0", "sys") if key in raw_status}
                            components = rpc("Shelly.GetComponents?dynamic_only=true").get("components", [])
                            zones = [{"key": zone["key"], "name": zone.get("config", {}).get("name"),
                                      "occupied": zone["status"].get("value"), "num_objects": zone["status"].get("num_objects")}
                                     for zone in components if zone["key"].startswith("presencezone:")]
                            with self.lock:
                                self.state.update(status=status, zones=zones)

                            refresh_at = now + 5
                        try:
                            payload = self.ws.recv()
                            if not payload:
                                raise ConnectionError("Sensor closed the connection")
                            message = json.loads(payload)
                            if "error" in message:
                                raise ValueError(str(message["error"]))
                            self.handle_message(message)
                        except websocket.WebSocketTimeoutException:
                            continue
                except Exception as exc:
                    with self.lock:
                        self.state.update(connected=False, error=str(exc), tracks=[], last_event_at=None)
                finally:
                    with self.lock:
                        self.state["connected"] = False
                    if self.ws:
                        self.ws.close()
                        self.ws = None
                self.stop_event.wait(3)
        finally:
            session.close()


class Installation:
    def __init__(self, path, start_workers=True):
        self.path = path
        self.lock = threading.RLock()
        self.start_workers = start_workers
        self.readers = {}
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text((ROOT / "config.example.json").read_text(encoding="utf-8"), encoding="utf-8")
        original = json.loads(path.read_text(encoding="utf-8"))
        self.config = validate_config(migrate_config(original))
        if original.get("schema_version") in (1, 2, 3, 4, 5):
            # Preserve the exact old configuration once, before persisting the migration.
            backup = path.with_name(f'config.v{original["schema_version"]}.backup.json')
            if not backup.exists():
                backup.write_bytes(path.read_bytes())
            self.write_config(self.config)
        self.reconcile()
        self.lighting = LightControllers(self, start_workers)

    def write_config(self, config):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(config, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def reconcile(self):
        active = {sensor["id"]: sensor for sensor in self.config["sensors"] if sensor["enabled"]}
        for key, reader in list(self.readers.items()):
            if key not in active or active[key]["device_host"] != reader.host:
                reader.stop()
                del self.readers[key]
        for key, sensor in active.items():
            if key not in self.readers:
                reader = self.readers[key] = SensorReader(sensor)
                if self.start_workers:
                    reader.start()

    def save(self, config):
        validate_config(config)
        with self.lock:
            self.lighting.stop_changed(self.config["lighting"], config["lighting"])
            self.write_config(config)
            self.config = copy.deepcopy(config)
            self.reconcile()
            self.lighting.reconcile(self.config["lighting"])

    def close(self):
        self.lighting.close()
        with self.lock:
            for reader in self.readers.values():
                reader.stop()

    def snapshot(self, include_lighting=True):
        with self.lock:
            config = copy.deepcopy(self.config)
            sensors = []
            for sensor in config["sensors"]:
                reader = self.readers.get(sensor["id"])
                state = (reader or SensorReader(sensor)).snapshot(sensor)
                sensors.append(state)
        live_tracks = [track for sensor in sensors if sensor["connected"] and not sensor["stale"] for track in sensor["tracks"]]
        result = {"config": config, "units": "mm", "sensors": sensors, "tracks": live_tracks}
        if include_lighting:
            result["lighting"] = self.lighting.preview(config["lighting"], live_tracks)
        return result


def create_app(instance_path=None, start_worker=True):
    path = Path(instance_path or os.environ.get("LED_PRESENCE_INSTANCE", ROOT / "instance")).resolve()
    app = Flask(__name__, instance_path=str(path))
    installation = Installation(path / "config.json", start_workers=start_worker)
    app.extensions["installation"] = installation
    atexit.register(installation.close)

    @app.get("/")
    def index():
        return render_template("index.html", geometry_page=False)

    @app.get("/geometry")
    def geometry():
        return render_template("geometry.html", geometry_page=True)

    @app.get("/devices")
    def devices():
        return render_template("devices.html", devices_page=True)

    @app.get("/lights")
    def lights():
        return redirect(url_for("devices", _anchor="lights"))

    @app.get("/nolu")
    def nolu():
        return redirect(url_for("devices", _anchor="lights"))

    @app.get("/api/state")
    def state():
        return jsonify(installation.snapshot())

    @app.get("/api/lighting")
    def lighting_state():
        state = installation.snapshot(include_lighting=False)
        return jsonify(installation.lighting.preview(state["config"]["lighting"], state["tracks"]))

    @app.post("/api/lighting/output")
    def lighting_output():
        if request.headers.get("Origin") and request.headers["Origin"] != request.host_url.rstrip("/"):
            return jsonify(error="Cross-origin writes are not allowed"), 403
        if not request.is_json:
            return jsonify(error="Send application/json"), 415
        data = request.get_json()
        if not isinstance(data, dict) or not isinstance(data.get("enabled"), bool):
            return jsonify(error="enabled must be true or false"), 400
        try:
            with installation.lock:
                installation.lighting.set_output(data["enabled"], installation.config["lighting"], data.get("controller_id"))
        except (ValueError, requests.RequestException) as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(installation.snapshot()["lighting"])

    @app.route("/api/config", methods=["GET", "PUT"])
    def config():
        if request.method == "GET":
            return jsonify(installation.snapshot()["config"])
        if request.headers.get("Origin") and request.headers["Origin"] != request.host_url.rstrip("/"):
            return jsonify(error="Cross-origin writes are not allowed"), 403
        if not request.is_json:
            return jsonify(error="Send application/json"), 415
        try:
            installation.save(request.get_json())
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(installation.snapshot()["config"])

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, debug=False)
