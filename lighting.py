"""NOLU product geometry, presence animation and DDP output.

Product dimensions are defined once; instances form a controller-rooted tree.
Transport follows RGB888 DDP v1 (10-byte header; byte offsets; final PUSH).
"""
import colorsys
import copy
import hashlib
import ipaddress
import math
import re
import socket
import struct
import threading
import time

import requests
import presence_animation


def default_lighting():
    return {
        "controller": {"device_host": "192.168.88.182", "component_id": 0, "ddp_port": 4048,
                       "mounting": {"height": 2000, "left_wall": 100, "front_wall": 100,
                                    "yaw": 0, "pitch": 0, "roll": 0}},
        "product": {"id": "shelly-nolu-bar", "name": "Shelly NOLU Bar", "length_mm": 300,
                    "pixels_per_bar": 0, "verified": False},
        "bars": [{"id": f"bar-{i}", "product_id": "shelly-nolu-bar",
                  "connected_to": "controller" if i == 1 else f"bar-{i-1}",
                  "wire_order": i, "yaw": -90 if i == 1 else 0, "pitch": 0, "roll": 0,
                  "reversed": False} for i in range(1, 10)],
        "animation": {"mode": "aura", "brightness_pct": 35, "radius_mm": 1800,
                      "speed": 0.6, "fade_seconds": 0.6, "idle_pct": 0},
    }


def validate_lighting(config):
    def number(obj, key, low, high, integer=False):
        value = obj[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"Lighting {key} must be between {low} and {high}")
        if integer and not isinstance(value, int):
            raise ValueError(f"Lighting {key} must be an integer")

    controller, product = config["controller"], config["product"]
    ipaddress.IPv4Address(controller["device_host"])
    number(controller, "component_id", 0, 255, True)
    number(controller, "ddp_port", 1, 65535, True)
    for key in ("height", "left_wall", "front_wall", "yaw", "pitch", "roll"):
        number(controller["mounting"], key, -1_000_000, 1_000_000)
    if not isinstance(product["id"], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", product["id"]):
        raise ValueError("Provide a valid lighting product ID")
    if not isinstance(product["name"], str) or not product["name"].strip():
        raise ValueError("Provide a product name")
    number(product, "length_mm", 1, 10000)
    number(product, "pixels_per_bar", 0, 1000, True)
    if not isinstance(product["verified"], bool):
        raise ValueError("Product verified must be true or false")
    bars = config["bars"]
    if not isinstance(bars, list) or not 1 <= len(bars) <= 64:
        raise ValueError("Configure between 1 and 64 bars")
    ids = set()
    for bar in bars:
        if not isinstance(bar["id"], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", bar["id"]) or bar["id"] == "controller" or bar["id"] in ids:
            raise ValueError("Bar IDs must be unique identifiers other than controller")
        ids.add(bar["id"])
        if bar["product_id"] != product["id"]:
            raise ValueError("Each bar must reference the defined product")
        number(bar, "wire_order", 1, len(bars), True)
        for key in ("yaw", "pitch", "roll"):
            number(bar, key, -360, 360)
        if not isinstance(bar["reversed"], bool):
            raise ValueError("Bar reversed must be true or false")
    if len({bar["wire_order"] for bar in bars}) != len(bars):
        raise ValueError("Pixel orders must be unique, from 1 to the number of bars")
    parents = {bar["id"]: bar["connected_to"] for bar in bars}
    for bar_id in ids:
        visited = set()
        current = bar_id
        while current != "controller":
            if current in visited:
                raise ValueError("Bar connections contain a cycle")
            visited.add(current)
            if current not in parents:
                raise ValueError("Every bar must connect back to the controller")
            current = parents[current]
    if len(bars) * product["pixels_per_bar"] > 10000:
        raise ValueError("The installation supports at most 10000 pixels")
    animation = config["animation"]
    if animation["mode"] not in ("aura", "ripple"):
        raise ValueError("Animation mode must be aura or ripple")
    for key, low, high in (("brightness_pct", 0, 100), ("radius_mm", 1, 20000),
                           ("speed", 0.05, 3), ("fade_seconds", 0.05, 10), ("idle_pct", 0, 100)):
        number(animation, key, low, high)
    return config


def multiply(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def rotation(yaw, pitch, roll):
    """Local axes: X right, Y forward, Z up; yaw Z, pitch X, roll Y."""
    y, p, r = map(math.radians, (yaw, pitch, roll))
    cy, sy, cp, sp, cr, sr = math.cos(y), math.sin(y), math.cos(p), math.sin(p), math.cos(r), math.sin(r)
    return multiply(multiply([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]],
                             [[1, 0, 0], [0, cp, -sp], [0, sp, cp]]),
                    [[cr, 0, sr], [0, 1, 0], [-sr, 0, cr]])


def build_layout(config):
    m = config["controller"]["mounting"]
    root = [m["left_wall"], m["front_wall"], m["height"]]
    # Device-local X points opposite to room X measured from the viewer's LEFT wall.
    basis = multiply([[-1, 0, 0], [0, 1, 0], [0, 0, 1]], rotation(m["yaw"], m["pitch"], m["roll"]))
    # Align the NOLU product frame with the room drawing: 180 degrees
    # in the main sensor-facing view (room +Y points down on that view).
    # Apply once at the controller so every joint/branch follows the same frame.
    # The controller position and all entered mounting/joint angles stay unchanged.
    basis = multiply([[-1, 0, 0], [0, -1, 0], [0, 0, 1]], basis)
    resolved = {"controller": {"end": root, "matrix": basis, "path_mm": 0}}
    definitions = {bar["id"]: bar for bar in config["bars"]}

    def resolve(bar_id):
        if bar_id in resolved:
            return resolved[bar_id]
        bar = definitions[bar_id]
        parent = resolve(bar["connected_to"])
        matrix = multiply(parent["matrix"], rotation(bar["yaw"], bar["pitch"], bar["roll"]))
        length = config["product"]["length_mm"]
        start = parent["end"]
        end = [start[i] + matrix[i][1] * length for i in range(3)]
        result = {"id": bar_id, "connected_to": bar["connected_to"], "wire_order": bar["wire_order"],
                  "start": start, "end": end, "matrix": matrix, "path_mm": parent["path_mm"] + length,
                  "reversed": bar["reversed"]}
        resolved[bar_id] = result
        return result

    return {"controller": root, "bars": [resolve(bar["id"]) for bar in sorted(config["bars"], key=lambda b: b["wire_order"])]}


def program_pixel_positions(config, layout, count):
    """Use verified addresses, or estimate equal density along electrical order."""
    bars = layout["bars"]
    per_bar = config["product"]["pixels_per_bar"]
    exact = config["product"]["verified"] and per_bar * len(bars) == count
    positions = []
    for index in range(count):
        address = (index + .5) * len(bars) / count
        bar_index = min(len(bars) - 1, int(address))
        bar = bars[bar_index]
        fraction = address - bar_index
        if bar["reversed"]:
            fraction = 1 - fraction
        positions.append([a + (b - a) * fraction for a, b in zip(bar["start"], bar["end"])])
    return positions, not exact


def render_frame(config, tracks, timestamp):
    """Horizontal proximity avoids assuming uncalibrated radar Z is floor height."""
    layout = build_layout(config)
    animation, product = config["animation"], config["product"]
    # Unknown physical pixel count still permits a clearly marked software preview.
    samples = product["pixels_per_bar"] or 12
    pixels = []
    for bar in layout["bars"]:
        colors = []
        for index in range(samples):
            u = (index + 0.5) / samples
            if bar["reversed"]:
                u = 1 - u
            x, y, _ = [bar["start"][axis] + (bar["end"][axis] - bar["start"][axis]) * u for axis in range(3)]
            rgb = [animation["idle_pct"] / 100 * channel for channel in (0.2, 0.45, 0.55)]
            for track in tracks:
                distance = math.hypot(x - track["room_x_mm"], y - track["room_y_mm"])
                envelope = math.exp(-0.5 * (distance / animation["radius_mm"]) ** 2)
                phase = timestamp * animation["speed"] * 2 * math.pi
                wave = 0.78 + 0.22 * math.sin(phase - distance / 550)
                if animation["mode"] == "ripple":
                    wave = (0.5 + 0.5 * math.cos(phase - distance / 280)) ** 3
                hue = int.from_bytes(hashlib.sha256(track["track_key"].encode()).digest()[:2], "big") / 65535
                color = colorsys.hsv_to_rgb((0.42 + hue * 0.22) % 1, 0.75, 1)
                for channel in range(3):
                    rgb[channel] += color[channel] * envelope * wave
            brightness = animation["brightness_pct"] / 100
            colors.append([round(255 * brightness * min(1, value)) for value in rgb])
        bar["pixels"] = colors
        bar["color"] = [round(sum(pixel[c] for pixel in colors) / len(colors)) for c in range(3)]
        bar.pop("matrix")
        pixels.extend(colors)
    return layout, pixels


def ddp_packets(pixels, sequence=0):
    data = bytes(channel for pixel in pixels for channel in pixel)
    for offset in range(0, len(data), 1440):
        chunk = data[offset:offset + 1440]
        flags = 0x40 | (1 if offset + len(chunk) == len(data) else 0)
        yield struct.pack("!BBBBIH", flags, sequence & 15, 0x0B, 1, offset, len(chunk)) + chunk


class LightingService:
    def __init__(self, installation, start_worker):
        self.installation = installation
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.session = requests.Session()
        self.session.trust_env = False
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.state = {"output_enabled": False, "controller_connected": False, "controller_pixels": None,
                      "error": None, "frames_sent": 0, "last_sent_at": None}
        self.previous = None
        self.active_controller = None
        self.added_ddp = False
        self.smoothed = []
        self.previous_time = time.monotonic()
        self.layout = None
        self.program = None
        self.generation = 0
        self.state.update(program_running=False, program_sensor_id=None, program_error=None)
        if start_worker:
            threading.Thread(target=self.run, name="nolu-animation", daemon=True).start()

    def rpc(self, controller, method, **params):
        response = self.session.post(f'http://{controller["device_host"]}/rpc/{method}', json=params, timeout=3)
        response.raise_for_status()
        result = response.json()
        if isinstance(result, dict) and "code" in result and "message" in result:
            raise ValueError(result["message"])
        return result

    def prepare_program(self, config):
        """Read device capabilities without changing its current light state."""
        controller = config["controller"]
        actual = self.rpc(controller, "LedStrip.GetConfig", id=controller["component_id"])
        count = actual.get("num_leds")
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10000:
            raise ValueError("Controller must report between 1 and 10000 configured LEDs")
        if actual.get("ddp_port") != controller["ddp_port"]:
            raise ValueError(f'Controller DDP port is {actual.get("ddp_port")}; update Devices first')
        effects = self.rpc(controller, "LedStrip.ListAllEffects", id=controller["component_id"])
        if not any(effect.get("key") == "ddp" for effect in effects.get("effects", [])):
            raise ValueError("This controller does not support DDP animation output")
        return actual

    def set_output(self, enabled, config, *, program=None):
        with self.lock:
            if not enabled:
                self.stop_output()
                return
            if self.state["output_enabled"]:
                if program is None and self.program is None:
                    return
                self.stop_output()
            if self.previous is not None:
                self.stop_output()
            validate_lighting(config)
            if program is None and (not config["product"]["verified"] or config["product"]["pixels_per_bar"] < 1):
                raise ValueError("Measure the bar length and confirm pixels per bar, then mark the product verified")
            controller = config["controller"]
            # Re-read after restoring any previous output; that restoration may
            # have removed an app-added DDP effect since the preflight.
            actual = self.rpc(controller, "LedStrip.GetConfig", id=controller["component_id"])
            if program is not None and (isinstance(actual.get("num_leds"), bool) or not isinstance(actual.get("num_leds"), int) or not 1 <= actual["num_leds"] <= 10000):
                raise ValueError("Controller must report between 1 and 10000 configured LEDs")
            total = config["product"]["pixels_per_bar"] * len(config["bars"])
            if program is None and actual["num_leds"] != total:
                raise ValueError(f'Controller has {actual["num_leds"]} pixels; layout maps {total}. Correct the mapping or device pixel count first.')
            if actual.get("ddp_port") != controller["ddp_port"]:
                raise ValueError(f'Controller DDP port is {actual.get("ddp_port")}; update the installation port')
            self.previous = self.rpc(controller, "LedStrip.GetStatus", id=controller["component_id"])
            self.active_controller = copy.deepcopy(controller)
            try:
                if "ddp" not in actual.get("effects", []):
                    self.rpc(controller, "LedStrip.AddEffect", id=controller["component_id"], effect="ddp")
                    self.added_ddp = True
                self.rpc(controller, "LedStrip.Set", id=controller["component_id"], on=True, brightness=100, effect="ddp")
                self.smoothed = []
                self.program = copy.deepcopy(program)
                if self.program is not None:
                    self.program["pixel_count"] = actual["num_leds"]
                self.state.update(output_enabled=True, error=None, controller_connected=True, controller_pixels=actual["num_leds"])
                self.state.update(program_running=program is not None, program_sensor_id=program["sensor"]["id"] if program else None,
                                  program_error=None)
                self.generation += 1
            except Exception:
                self.stop_output()
                raise

    def stop_output(self):
        with self.lock:
            self.state["output_enabled"] = False
            self.state.update(program_running=False, program_sensor_id=None)
            self.program = None
            self.generation += 1
            if self.previous is not None:
                controller = self.active_controller
                try:
                    restore = {key: self.previous[key] for key in ("on", "brightness", "effect", "rgb", "speed", "bg_rgb", "palette", "intensity", "reverse") if key in self.previous}
                    self.rpc(controller, "LedStrip.Set", id=controller["component_id"], **restore)
                    if self.added_ddp:
                        self.rpc(controller, "LedStrip.RemoveEffect", id=controller["component_id"], effect="ddp")
                    self.previous = self.active_controller = None
                    self.added_ddp = False
                except Exception as exc:
                    self.state["error"] = f"Output stopped; previous effect could not be restored: {exc}"
                    raise ValueError(self.state["error"]) from exc

    def preview(self, config, tracks):
        with self.lock:
            now = time.monotonic()
            if self.program is None:
                layout, target = render_frame(config, tracks, now)
            else:
                layout = build_layout(config)
                positions, estimated = program_pixel_positions(config, layout, self.program["pixel_count"])
                context = {"time_s": now - self.program["started_at"], "pixel_count": self.program["pixel_count"],
                           "tracks": [copy.deepcopy(t) for t in tracks if t.get("sensor_id") == self.program["sensor"]["id"]],
                           "sensor": copy.deepcopy(self.program["sensor"]), "room": copy.deepcopy(self.program["room"]),
                           "controller": copy.deepcopy(config["controller"]), "pixel_positions": positions,
                           "animation_id": self.program.get("animation_id", presence_animation.DEFAULT_ANIMATION),
                           "state": self.program.setdefault("state", {})}
                target = presence_animation.render(context)
                if not isinstance(target, (list, tuple)) or len(target) != context["pixel_count"]:
                    raise ValueError("presence_animation.render must return exactly pixel_count RGB triples")
                for pixel in target:
                    if not isinstance(pixel, (list, tuple)) or len(pixel) != 3 or any(isinstance(c, bool) or not isinstance(c, int) or not 0 <= c <= 255 for c in pixel):
                        raise ValueError("presence_animation.render returned an invalid RGB pixel")
                for bar in layout["bars"]:
                    bar["pixels"] = [None] * (config["product"]["pixels_per_bar"] or 12)
                    bar.pop("matrix")
            # Smooth both entries and exits between one-second sensor updates.
            alpha = 1 - math.exp(-(now - self.previous_time) / config["animation"]["fade_seconds"])
            if len(self.smoothed) != len(target):
                self.smoothed = [[0.0, 0.0, 0.0] for _ in target]
            for index, pixel in enumerate(target):
                for channel in range(3):
                    self.smoothed[index][channel] += alpha * (pixel[channel] - self.smoothed[index][channel])
            offset = 0
            logical_count = sum(len(bar["pixels"]) for bar in layout["bars"])
            for bar in layout["bars"]:
                count = len(bar["pixels"])
                bar["pixels"] = [[round(c) for c in self.smoothed[min(len(self.smoothed)-1, (offset+i)*len(self.smoothed)//logical_count)]] for i in range(count)]
                bar["color"] = [round(sum(p[c] for p in bar["pixels"]) / count) for c in range(3)]
                offset += count
            self.previous_time = now
            self.layout = layout
            return {**copy.deepcopy(self.state), **layout, "product_verified": config["product"]["verified"],
                    "generation": self.generation,
                    "output_pixels": [[round(c) for c in pixel] for pixel in self.smoothed],
                    "mapping_mode": "position-program" if self.program else "spatial-bars",
                    "program_mapping_estimated": estimated if self.program else False,
                    "mapped_pixels": config["product"]["pixels_per_bar"] * len(config["bars"]),
                    "preview_pixels_per_bar": config["product"]["pixels_per_bar"] or 12}

    def run(self):
        poll_at = 0
        sequence = 0
        while not self.stop_event.is_set():
            try:
                state = self.installation.snapshot(include_lighting=False)
                config = state["config"]["lighting"]
                frame = self.preview(config, state["tracks"])
                with self.lock:
                    if frame["generation"] != self.generation:
                        continue
                    if time.monotonic() >= poll_at:
                        actual = self.rpc(config["controller"], "LedStrip.GetConfig", id=config["controller"]["component_id"])
                        self.state.update(controller_connected=True, controller_pixels=actual["num_leds"])
                        if self.previous is None or self.state["output_enabled"]:
                            self.state["error"] = None
                        poll_at = time.monotonic() + 5
                        expected_pixels = self.program["pixel_count"] if self.program else frame["mapped_pixels"]
                        if self.state["output_enabled"] and (actual["num_leds"] != expected_pixels or actual.get("ddp_port") != config["controller"]["ddp_port"]):
                            self.stop_output()
                            raise ValueError("Controller pixel count or DDP port changed; output stopped")
                    if self.state["output_enabled"]:
                        pixels = frame["output_pixels"]
                        sequence = sequence % 15 + 1
                        for packet in ddp_packets(pixels, sequence):
                            self.socket.sendto(packet, (config["controller"]["device_host"], config["controller"]["ddp_port"]))
                        self.state["frames_sent"] += 1
                        self.state["last_sent_at"] = time.time()
            except Exception as exc:
                with self.lock:
                    if self.program is not None:
                        self.state["program_error"] = str(exc)
                    self.state.update(error=str(exc), controller_connected=False)
                    if self.state["output_enabled"]:
                        try:
                            self.stop_output()
                        except ValueError:
                            pass
                poll_at = time.monotonic() + 3
                self.stop_event.wait(1)
            self.stop_event.wait(0.05)

    def close(self):
        self.stop_event.set()
        try:
            self.stop_output()
        except ValueError:
            pass
        self.socket.close()
        self.session.close()
