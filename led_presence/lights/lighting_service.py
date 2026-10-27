#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Controller RPC, DDP streaming and light state restoration."""

import copy
import math
import socket
import threading
import time

import requests

from led_presence.animations import presence_animation
from led_presence.lights.lighting import (
    build_layout,
    ddp_packets,
    program_pixel_positions,
    render_frame,
    validate_lighting,
)


class LightingService:
    """Stream one controller and preserve its previous light state."""

    def __init__(self, installation, start_worker):
        self.installation = installation
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.session = requests.Session()
        self.session.trust_env = False
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.state = {
            "output_enabled": False,
            "controller_connected": False,
            "controller_pixels": None,
            "error": None,
            "frames_sent": 0,
            "last_sent_at": None,
        }
        self.previous = None
        self.active_controller = None
        self.added_ddp = False
        self.smoothed = []
        self.previous_time = time.monotonic()
        self.layout = None
        self.program = None
        self.generation = 0
        self.state.update(
            program_running=False, program_sensor_id=None, program_error=None
        )
        if start_worker:
            threading.Thread(
                target=self.run, name="nolu-animation", daemon=True
            ).start()

    def rpc(self, controller, method, **params):
        response = self.session.post(
            f"http://{controller['device_host']}/rpc/{method}",
            json=params,
            timeout=3,
        )
        response.raise_for_status()
        result = response.json()
        if (
            isinstance(result, dict)
            and "code" in result
            and "message" in result
        ):
            raise ValueError(result["message"])
        return result

    def prepare_program(self, config):
        """
        Read device capabilities without changing its current light state.
        """
        controller = config["controller"]
        actual = self.rpc(
            controller, "LedStrip.GetConfig", id=controller["component_id"]
        )
        count = actual.get("num_leds")
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or not 1 <= count <= 10000
        ):
            raise ValueError(
                "Controller must report between 1 and 10000 configured LEDs"
            )
        if actual.get("ddp_port") != controller["ddp_port"]:
            raise ValueError(
                f"Controller DDP port is {actual.get('ddp_port')}; update "
                f"Devices first"
            )
        effects = self.rpc(
            controller,
            "LedStrip.ListAllEffects",
            id=controller["component_id"],
        )
        if not any(
            effect.get("key") == "ddp" for effect in effects.get("effects", [])
        ):
            raise ValueError(
                "This controller does not support DDP animation output"
            )
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
            if program is None and (
                not config["product"]["verified"]
                or config["product"]["pixels_per_bar"] < 1
            ):
                raise ValueError(
                    "Measure the bar length and confirm pixels per bar, then "
                    "mark the product verified"
                )
            controller = config["controller"]
            # Re-read after restoring any previous output; that restoration may
            # have removed an app-added DDP effect since the preflight.
            actual = self.rpc(
                controller, "LedStrip.GetConfig", id=controller["component_id"]
            )
            if program is not None and (
                isinstance(actual.get("num_leds"), bool)
                or not isinstance(actual.get("num_leds"), int)
                or not 1 <= actual["num_leds"] <= 10000
            ):
                raise ValueError(
                    "Controller must report between 1 and 10000 configured "
                    "LEDs"
                )
            total = config["product"]["pixels_per_bar"] * len(config["bars"])
            if program is None and actual["num_leds"] != total:
                raise ValueError(
                    f"Controller has {actual['num_leds']} pixels; layout "
                    f"maps {total}. Correct the mapping or device pixel "
                    f"count first."
                )
            if actual.get("ddp_port") != controller["ddp_port"]:
                raise ValueError(
                    f"Controller DDP port is {actual.get('ddp_port')}; "
                    f"update the installation port"
                )
            self.previous = self.rpc(
                controller, "LedStrip.GetStatus", id=controller["component_id"]
            )
            self.active_controller = copy.deepcopy(controller)
            try:
                if "ddp" not in actual.get("effects", []):
                    self.rpc(
                        controller,
                        "LedStrip.AddEffect",
                        id=controller["component_id"],
                        effect="ddp",
                    )
                    self.added_ddp = True
                self.rpc(
                    controller,
                    "LedStrip.Set",
                    id=controller["component_id"],
                    on=True,
                    brightness=100,
                    effect="ddp",
                )
                self.smoothed = []
                self.program = copy.deepcopy(program)
                if self.program is not None:
                    self.program["pixel_count"] = actual["num_leds"]
                self.state.update(
                    output_enabled=True,
                    error=None,
                    controller_connected=True,
                    controller_pixels=actual["num_leds"],
                )
                self.state.update(
                    program_running=program is not None,
                    program_sensor_id=program["sensor"]["id"]
                    if program
                    else None,
                    program_error=None,
                )
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
                    restore = {
                        key: self.previous[key]
                        for key in (
                            "on",
                            "brightness",
                            "effect",
                            "rgb",
                            "speed",
                            "bg_rgb",
                            "palette",
                            "intensity",
                            "reverse",
                        )
                        if key in self.previous
                    }
                    self.rpc(
                        controller,
                        "LedStrip.Set",
                        id=controller["component_id"],
                        **restore,
                    )
                    if self.added_ddp:
                        self.rpc(
                            controller,
                            "LedStrip.RemoveEffect",
                            id=controller["component_id"],
                            effect="ddp",
                        )
                    self.previous = self.active_controller = None
                    self.added_ddp = False
                except Exception as exc:
                    self.state["error"] = (
                        f"Output stopped; previous effect could not be "
                        f"restored: {exc}"
                    )
                    raise ValueError(self.state["error"]) from exc

    def preview(self, config, tracks):
        with self.lock:
            now = time.monotonic()
            if self.program is None:
                layout, target = render_frame(config, tracks, now)
            else:
                layout = build_layout(config)
                positions, estimated = program_pixel_positions(
                    config, layout, self.program["pixel_count"]
                )
                context = {
                    "time_s": now - self.program["started_at"],
                    "pixel_count": self.program["pixel_count"],
                    "tracks": [
                        copy.deepcopy(t)
                        for t in tracks
                        if t.get("sensor_id") == self.program["sensor"]["id"]
                    ],
                    "sensor": copy.deepcopy(self.program["sensor"]),
                    "room": copy.deepcopy(self.program["room"]),
                    "controller": copy.deepcopy(config["controller"]),
                    "pixel_positions": positions,
                    "animation_id": self.program.get(
                        "animation_id", presence_animation.DEFAULT_ANIMATION
                    ),
                    "state": self.program.setdefault("state", {}),
                }
                target = presence_animation.render(context)
                if (
                    not isinstance(target, (list, tuple))
                    or len(target) != context["pixel_count"]
                ):
                    raise ValueError(
                        "presence_animation.render must return exactly "
                        "pixel_count RGB triples"
                    )
                for pixel in target:
                    if (
                        not isinstance(pixel, (list, tuple))
                        or len(pixel) != 3
                        or any(
                            isinstance(c, bool)
                            or not isinstance(c, int)
                            or not 0 <= c <= 255
                            for c in pixel
                        )
                    ):
                        raise ValueError(
                            "presence_animation.render returned an invalid "
                            "RGB pixel"
                        )
                for bar in layout["bars"]:
                    bar["pixels"] = [None] * (
                        config["product"]["pixels_per_bar"] or 12
                    )
                    bar.pop("matrix")
            # Smooth both entries and exits between one-second sensor updates.
            alpha = 1 - math.exp(
                -(now - self.previous_time)
                / config["animation"]["fade_seconds"]
            )
            if len(self.smoothed) != len(target):
                self.smoothed = [[0.0, 0.0, 0.0] for _ in target]
            for index, pixel in enumerate(target):
                for channel in range(3):
                    self.smoothed[index][channel] += alpha * (
                        pixel[channel] - self.smoothed[index][channel]
                    )
            offset = 0
            logical_count = sum(len(bar["pixels"]) for bar in layout["bars"])
            for bar in layout["bars"]:
                count = len(bar["pixels"])
                bar["pixels"] = [
                    [
                        round(c)
                        for c in self.smoothed[
                            min(
                                len(self.smoothed) - 1,
                                (offset + i)
                                * len(self.smoothed)
                                // logical_count,
                            )
                        ]
                    ]
                    for i in range(count)
                ]
                bar["color"] = [
                    round(sum(p[c] for p in bar["pixels"]) / count)
                    for c in range(3)
                ]
                offset += count
            self.previous_time = now
            self.layout = layout
            return {
                **copy.deepcopy(self.state),
                **layout,
                "product_verified": config["product"]["verified"],
                "generation": self.generation,
                "output_pixels": [
                    [round(c) for c in pixel] for pixel in self.smoothed
                ],
                "mapping_mode": "position-program"
                if self.program
                else "spatial-bars",
                "program_mapping_estimated": estimated
                if self.program
                else False,
                "mapped_pixels": config["product"]["pixels_per_bar"]
                * len(config["bars"]),
                "preview_pixels_per_bar": config["product"]["pixels_per_bar"]
                or 12,
            }

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
                        actual = self.rpc(
                            config["controller"],
                            "LedStrip.GetConfig",
                            id=config["controller"]["component_id"],
                        )
                        self.state.update(
                            controller_connected=True,
                            controller_pixels=actual["num_leds"],
                        )
                        if (
                            self.previous is None
                            or self.state["output_enabled"]
                        ):
                            self.state["error"] = None
                        poll_at = time.monotonic() + 5
                        expected_pixels = (
                            self.program["pixel_count"]
                            if self.program
                            else frame["mapped_pixels"]
                        )
                        if self.state["output_enabled"] and (
                            actual["num_leds"] != expected_pixels
                            or actual.get("ddp_port")
                            != config["controller"]["ddp_port"]
                        ):
                            self.stop_output()
                            raise ValueError(
                                "Controller pixel count or DDP port changed; "
                                "output stopped"
                            )
                    if self.state["output_enabled"]:
                        pixels = frame["output_pixels"]
                        sequence = sequence % 15 + 1
                        for packet in ddp_packets(pixels, sequence):
                            self.socket.sendto(
                                packet,
                                (
                                    config["controller"]["device_host"],
                                    config["controller"]["ddp_port"],
                                ),
                            )
                        self.state["frames_sent"] += 1
                        self.state["last_sent_at"] = time.time()
            except Exception as exc:
                with self.lock:
                    if self.program is not None:
                        self.state["program_error"] = str(exc)
                    self.state.update(
                        error=str(exc), controller_connected=False
                    )
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
