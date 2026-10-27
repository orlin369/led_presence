#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test selectable presence effects and their server-side lifecycle."""

import copy
import socket
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from led_presence.animations import presence_animation
from led_presence.lights.lighting import build_layout, program_pixel_positions
from led_presence.web.app import create_app


def pixel_brightness(sample):
    """Rank indexed pixels by total brightness."""
    return sum(sample[1])


class ProgramTests(unittest.TestCase):
    """Exercise rendering, selection, streaming and failure recovery."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(self.directory.name, start_worker=False)
        self.installation = self.app.extensions["installation"]
        self.client = self.app.test_client()
        self.config = copy.deepcopy(self.installation.config)
        self.sensor_id = self.config["sensors"][0]["id"]
        self.installation.readers[self.sensor_id].state["connected"] = True
        self.restores = []
        self.mock_services()

    def tearDown(self):
        self.installation.close()
        self.directory.cleanup()

    def mock_services(self):
        for key, service in self.installation.lighting.services.items():
            service.rpc = Mock(side_effect=self.mock_services_rpc)

    def start(self):
        return self.client.post(
            "/api/animation",
            json={"enabled": True, "sensor_id": self.sensor_id},
        )

    def track(self, sensor_id, kind="track"):
        self.installation.readers[sensor_id].handle_message(
            {
                "params": {
                    "events": [
                        {
                            "component": "presence",
                            "event": kind,
                            "object": [{"id": 1, "x": 0.2, "y": 2}],
                        }
                    ]
                }
            }
        )

    def test_editable_program_contract_presence_motion_and_empty_room(self):
        context = {
            "time_s": 0.8,
            "pixel_count": 100,
            "sensor": self.config["sensors"][0],
            "room": self.config["room"],
            "tracks": [],
        }
        self.assertEqual(presence_animation.render(context), [(0, 0, 0)] * 100)
        context["tracks"] = [{"room_x_mm": 1000, "room_y_mm": 2000}]
        first = presence_animation.render(context)
        self.assertEqual(len(first), 100)
        self.assertTrue(
            all(
                len(pixel) == 3
                and all(isinstance(c, int) and 0 <= c <= 255 for c in pixel)
                for pixel in first
            )
        )
        self.assertGreater(max(map(sum, first)), 0)
        first_peak = max(enumerate(first), key=pixel_brightness)[0]
        self.assertGreater(len(set(first)), 10)
        context["tracks"][0]["room_x_mm"] = 2500
        context["time_s"] = 1.8
        moved = presence_animation.render(context)
        self.assertGreater(
            max(enumerate(moved), key=pixel_brightness)[0], first_peak + 20
        )
        self.assertGreater(
            sum(moved[first_peak]), 0
        )  # A wake remains behind movement.
        context["time_s"] = 2
        self.assertNotEqual(presence_animation.render(context), first)
        context["tracks"] = []
        context["time_s"] = 20
        self.assertEqual(presence_animation.render(context), [(0, 0, 0)] * 100)

    def test_actual_led_count_with_estimated_mapping(
        self,
    ):
        self.assertEqual(
            self.config["lighting"]["products"][0]["pixels_per_bar"], 0
        )
        response = self.start()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["running"])
        frame = self.installation.snapshot()["lighting"]["controllers"][0]
        self.assertEqual(frame["mapping_mode"], "position-program")
        self.assertTrue(frame["program_mapping_estimated"])
        self.assertEqual(len(frame["output_pixels"]), 100)
        self.assertEqual(frame["mapped_pixels"], 0)
        stop = self.client.post("/api/animation", json={"enabled": False})
        self.assertEqual(stop.status_code, 200)
        self.assertFalse(stop.get_json()["running"])
        self.assertEqual(self.restores[0][1]["brightness"], 77)
        self.assertEqual(self.restores[0][1]["rgb"], [10, 20, 30])

    def test_only_selected_sensor_tracks_reach_program_and_exit_fades_black(
        self,
    ):
        second = copy.deepcopy(self.config["sensors"][0])
        second.update(
            id="sensor-2", name="Other sensor", device_host="192.0.2.3"
        )
        self.config["sensors"].append(second)
        self.installation.save(self.config)
        self.installation.readers["sensor-2"].state["connected"] = True
        self.track("sensor-2")
        self.assertEqual(self.start().status_code, 200)
        frame = self.installation.snapshot()["lighting"]["controllers"][0]
        self.assertTrue(
            all(pixel == [0, 0, 0] for pixel in frame["output_pixels"])
        )
        self.track(self.sensor_id)
        service = self.installation.lighting.services["lights-1"]
        service.previous_time -= 2
        with patch(
            "led_presence.lights.lighting_service.presence_animation.render",
            wraps=presence_animation.render,
        ) as render:
            self.installation.snapshot()
            context = render.call_args.args[0]
            self.assertEqual(
                {t["sensor_id"] for t in context["tracks"]}, {self.sensor_id}
            )
        self.track(self.sensor_id, "no_track")
        service.previous_time -= 10
        service.program["started_at"] -= 10
        frame = self.installation.snapshot()["lighting"]["controllers"][0]
        self.assertTrue(
            all(pixel == [0, 0, 0] for pixel in frame["output_pixels"])
        )

    def test_projection_follows_physical_bar_positions_and_reversed_wiring(
        self,
    ):
        # Use the manager adapter's normalized single-controller configuration.
        from led_presence.lights.controller_config import controller_config

        config = controller_config(
            self.config["lighting"], self.config["lighting"]["controllers"][0]
        )
        layout = build_layout(config)
        positions, estimated = program_pixel_positions(config, layout, 100)
        self.assertEqual(len(positions), 100)
        self.assertTrue(estimated)
        start, end = layout["bars"][0]["start"], layout["bars"][0]["end"]
        self.assertLess(
            sum((a - b) ** 2 for a, b in zip(positions[0], start)),
            sum((a - b) ** 2 for a, b in zip(positions[0], end)),
        )
        config["bars"][0]["reversed"] = True
        reversed_positions, _ = program_pixel_positions(
            config, build_layout(config), 100
        )
        self.assertLess(
            sum((a - b) ** 2 for a, b in zip(reversed_positions[0], end)),
            sum((a - b) ** 2 for a, b in zip(reversed_positions[0], start)),
        )
        config["product"].update(verified=True, pixels_per_bar=10)
        self.assertFalse(
            program_pixel_positions(
                config, build_layout(config), 10 * len(config["bars"])
            )[1]
        )
        context = {
            "time_s": 1,
            "pixel_count": 100,
            "sensor": self.config["sensors"][0],
            "room": self.config["room"],
            "pixel_positions": positions,
            "tracks": [
                {
                    "room_x_mm": positions[5][0] + 1000,
                    "room_y_mm": positions[5][1],
                }
            ],
        }
        # A person can stand far from the bars and still illuminate the nearest
        # section.
        self.assertGreater(
            max(map(sum, presence_animation.render(context))), 50
        )

    def test_multiple_people_and_depth_change_the_spatial_effect(self):
        context = {
            "time_s": 1,
            "pixel_count": 100,
            "sensor": self.config["sensors"][0],
            "room": self.config["room"],
            "pixel_positions": [[i * 30, 1200, 0] for i in range(100)],
            "tracks": [
                {"room_x_mm": 600, "room_y_mm": 2200},
                {"room_x_mm": 2400, "room_y_mm": 2200},
            ],
        }
        pixels = presence_animation.render(context)
        self.assertGreater(sum(pixels[20]), sum(pixels[50]) + 50)
        self.assertGreater(sum(pixels[80]), sum(pixels[50]) + 50)
        context["state"] = {}
        context["tracks"] = [{"room_x_mm": 600, "room_y_mm": 3500}]
        far = presence_animation.render(context)
        self.assertNotEqual(far[20], pixels[20])

    def test_all_selectable_animations_are_distinct_and_fade_without_people(
        self,
    ):
        frames = []
        for animation_id in presence_animation.ANIMATIONS:
            context = {
                "time_s": 1,
                "pixel_count": 100,
                "sensor": self.config["sensors"][0],
                "room": self.config["room"],
                "animation_id": animation_id,
                "pixel_positions": [[i * 30, 1200, 0] for i in range(100)],
                "tracks": [{"room_x_mm": 1500, "room_y_mm": 2300}],
            }
            frame = presence_animation.render(context)
            self.assertEqual(len(frame), 100)
            self.assertTrue(
                all(
                    isinstance(c, int) and 0 <= c <= 255
                    for p in frame
                    for c in p
                )
            )
            frames.append(tuple(frame))
            context.update(time_s=30, tracks=[])
            self.assertEqual(
                presence_animation.render(context), [(0, 0, 0)] * 100
            )
        self.assertEqual(len(set(frames)), len(presence_animation.ANIMATIONS))

    def test_spotlight_width_does_not_grow_with_proximity(self):
        context = {
            "time_s": 1,
            "pixel_count": 100,
            "sensor": self.config["sensors"][0],
            "room": self.config["room"],
            "animation_id": "spotlight",
            "pixel_positions": [[i * 30, 1200, 0] for i in range(100)],
            "tracks": [{"room_x_mm": 1500, "room_y_mm": 1500}],
        }
        near = presence_animation.render(context)
        context["state"] = {}
        context["tracks"][0]["room_y_mm"] = 3500
        far = presence_animation.render(context)
        self.assertEqual(
            sum(max(p) > 15 for p in near), sum(max(p) > 15 for p in far)
        )

    def test_live_selection_resets_trails_without_hardware_changes(
        self,
    ):
        response = self.client.post(
            "/api/animation",
            json={
                "enabled": True,
                "sensor_id": self.sensor_id,
                "animation_id": "spotlight",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["animation_id"], "spotlight")
        self.assertEqual(len(response.get_json()["animations"]), 4)
        service = self.installation.lighting.services["lights-1"]
        service.program["state"] = {"old_trail": True}
        before = service.rpc.call_count
        response = self.client.post(
            "/api/animation",
            json={
                "enabled": True,
                "sensor_id": self.sensor_id,
                "animation_id": "ripples",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["animation_id"], "ripples")
        self.assertEqual(service.program["animation_id"], "ripples")
        self.assertEqual(service.program["state"], {})
        self.assertEqual(service.rpc.call_count, before)
        for invalid in ("missing", [], 1):
            response = self.client.post(
                "/api/animation",
                json={
                    "enabled": True,
                    "sensor_id": self.sensor_id,
                    "animation_id": invalid,
                },
            )
            self.assertEqual(response.status_code, 400)
        self.assertEqual(service.program["animation_id"], "ripples")
        self.assertEqual(service.rpc.call_count, before)

    def test_start_requires_enabled_online_source_and_explicit_json(self):
        self.installation.readers[self.sensor_id].state["connected"] = False
        self.assertEqual(self.start().status_code, 400)
        self.assertEqual(
            self.client.post(
                "/api/animation",
                json={"enabled": True, "sensor_id": "missing"},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/animation", json={"enabled": "yes"}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post("/api/animation", data="start").status_code, 415
        )
        self.assertEqual(
            self.client.post(
                "/api/animation",
                json={"enabled": False},
                headers={"Origin": "https://other.example"},
            ).status_code,
            403,
        )

    def test_first_sensor_is_reference_and_reordering_stops_program(self):
        second = copy.deepcopy(self.config["sensors"][0])
        second.update(id="sensor-2", name="Second", device_host="192.0.2.3")
        self.config["sensors"].append(second)
        self.installation.save(self.config)
        self.installation.readers["sensor-2"].state["connected"] = True
        # Legacy callers cannot override the configured first sensor.
        response = self.client.post(
            "/api/animation",
            json={"enabled": True, "sensor_id": "sensor-2"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["sensor_id"], self.sensor_id)
        self.config["sensors"].reverse()
        self.installation.save(self.config)
        self.assertFalse(self.installation.lighting.program_state()["running"])
        response = self.client.post("/api/animation", json={"enabled": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["sensor_id"], "sensor-2")
        self.client.post("/api/animation", json={"enabled": False})
        self.config["sensors"][0]["enabled"] = False
        self.installation.save(self.config)
        self.assertEqual(self.start().status_code, 400)
        self.config["sensors"] = []
        self.installation.save(self.config)
        self.assertEqual(self.start().status_code, 400)

    def test_preflight_failure_does_not_change_any_light(self):
        second = copy.deepcopy(self.config["lighting"]["controllers"][0])
        second.update(id="lights-2", name="Offline controller")
        second["controller"]["device_host"] = "192.0.2.2"
        self.config["lighting"]["controllers"].append(second)
        self.installation.save(self.config)
        self.mock_services()
        self.installation.lighting.services[
            "lights-2"
        ].rpc.side_effect = ValueError("Cannot connect")
        self.assertEqual(self.start().status_code, 400)
        for service in self.installation.lighting.services.values():
            self.assertFalse(service.state["output_enabled"])
            self.assertFalse(
                any(
                    call.args[1] == "LedStrip.Set"
                    for call in service.rpc.call_args_list
                )
            )

    def test_failure_starting_second_controller_restores_both(self):
        second = copy.deepcopy(self.config["lighting"]["controllers"][0])
        second.update(id="lights-2", name="Second controller")
        second["controller"]["device_host"] = "192.0.2.2"
        self.config["lighting"]["controllers"].append(second)
        self.installation.save(self.config)
        self.mock_services()
        service = self.installation.lighting.services["lights-2"]
        self.original_rpc = service.rpc.side_effect
        service.rpc.side_effect = (
            self.failure_starting_second_controller_restores_both_rpc
        )
        self.assertEqual(self.start().status_code, 400)
        self.assertEqual(len(self.restores), 2)
        for service in self.installation.lighting.services.values():
            self.assertFalse(service.state["output_enabled"])
            self.assertIsNone(service.previous)

    def test_two_controllers_stream_100_pixels_and_stop_together(
        self,
    ):
        second = copy.deepcopy(self.config["lighting"]["controllers"][0])
        second.update(id="lights-2", name="Second controller")
        second["controller"]["device_host"] = "127.0.0.1"
        second["controller"]["component_id"] = 1
        self.config["lighting"]["controllers"].append(second)
        receivers = []
        for group in self.config["lighting"]["controllers"]:
            receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(3)
            self.addCleanup(receiver.close)
            receivers.append(receiver)
            group["controller"].update(
                device_host="127.0.0.1", ddp_port=receiver.getsockname()[1]
            )
        self.installation.save(self.config)
        self.mock_services()
        self.track(self.sensor_id)
        self.assertEqual(self.start().status_code, 200)
        workers = []
        try:
            for service in self.installation.lighting.services.values():
                worker = threading.Thread(target=service.run, daemon=True)
                worker.start()
                workers.append(worker)
            for receiver in receivers:
                packet, _ = receiver.recvfrom(2000)
                self.assertEqual(len(packet), 310)
                self.assertEqual(packet[0], 0x41)
            self.assertEqual(
                self.client.post(
                    "/api/animation", json={"enabled": False}
                ).status_code,
                200,
            )
            self.assertTrue(
                all(
                    not service.state["output_enabled"]
                    for service in self.installation.lighting.services.values()
                )
            )
            self.assertEqual(len(self.restores), 2)
        finally:
            self.installation.lighting.close()
            for worker in workers:
                worker.join(timeout=3)

    def test_room_or_source_edit_stops_program(self):
        self.assertEqual(self.start().status_code, 200)
        self.config["room"]["width_mm"] += 100
        self.installation.save(self.config)
        self.assertFalse(self.installation.lighting.program_state()["running"])
        self.assertEqual(self.start().status_code, 200)
        self.config["sensors"][0]["enabled"] = False
        self.installation.save(self.config)
        self.assertFalse(self.installation.lighting.program_state()["running"])

    def test_bad_animation_output_stops_stream_and_restores_previous_effect(
        self,
    ):
        self.assertEqual(self.start().status_code, 200)
        service = self.installation.lighting.services["lights-1"]
        self.restored_event = threading.Event()
        self.original_rpc = service.rpc.side_effect
        service.rpc.side_effect = self.restore_notifying_rpc
        with patch(
            "led_presence.lights.lighting_service.presence_animation.render",
            return_value=[(999, 0, 0)] * 100,
        ):
            worker = threading.Thread(target=service.run, daemon=True)
            worker.start()
            try:
                self.assertTrue(self.restored_event.wait(3))
            finally:
                service.stop_event.set()
                worker.join(timeout=3)
        self.assertFalse(service.state["output_enabled"])
        self.assertFalse(self.installation.lighting.program_state()["running"])
        self.assertIn(
            "invalid RGB", self.installation.lighting.program_state()["error"]
        )
        # A successful device poll must not hide the program failure.
        service.state["error"] = None
        self.assertIn(
            "invalid RGB", self.installation.lighting.program_state()["error"]
        )

    def mock_services_rpc(self, controller, method, **params):
        if method == "LedStrip.GetConfig":
            return {
                "num_leds": 100,
                "ddp_port": controller["ddp_port"],
                "effects": ["fade"],
            }
        if method == "LedStrip.ListAllEffects":
            return {"effects": [{"key": "ddp"}]}
        if method == "LedStrip.GetStatus":
            return {
                "on": True,
                "brightness": 77,
                "effect": "fade",
                "rgb": [10, 20, 30],
                "speed": 50,
            }
        if method == "LedStrip.Set" and params.get("effect") == "fade":
            self.restores.append((controller, params))
        return None

    def failure_starting_second_controller_restores_both_rpc(
        self, controller, method, **params
    ):
        if method == "LedStrip.Set" and params.get("effect") == "ddp":
            raise ValueError("Could not select DDP")
        return self.original_rpc(controller, method, **params)

    def restore_notifying_rpc(self, controller, method, **params):
        result = self.original_rpc(controller, method, **params)
        if method == "LedStrip.Set" and params.get("effect") == "fade":
            self.restored_event.set()
        return result


def main():
    """Run this test module directly."""
    unittest.main()


if __name__ == "__main__":
    main()
