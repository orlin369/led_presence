#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test bar geometry, DDP packets and output restoration."""

import copy
import json
import math
import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from led_presence.config.configuration import migrate_config
from led_presence.lights.controller_config import controller_config
from led_presence.lights.lighting import (
    build_layout,
    ddp_packets,
    default_lighting,
    render_frame,
    validate_lighting,
)
from led_presence.lights.lighting_service import LightingService
from led_presence.web.app import create_app


class LightingTests(unittest.TestCase):
    """Validate single-controller layout and transport without real lights."""

    def setUp(self):
        self.config = default_lighting()

    def test_shared_product_defines_all_nine_bars(self):
        self.config["product"]["length_mm"] = 400
        layout = build_layout(self.config)
        self.assertEqual(len(layout["bars"]), 9)
        for bar in layout["bars"]:
            self.assertAlmostEqual(math.dist(bar["start"], bar["end"]), 400)
        self.assertAlmostEqual(layout["bars"][-1]["end"][0], 3700)
        self.assertAlmostEqual(layout["bars"][-1]["end"][1], 100)
        self.assertEqual(layout["bars"][0]["start"], layout["controller"])
        for i in range(1, 9):
            self.assertEqual(
                layout["bars"][i]["start"], layout["bars"][i - 1]["end"]
            )

    def test_relative_joint_rotation_and_branching(self):
        self.config["bars"][1]["yaw"] = 90
        self.config["bars"][2]["connected_to"] = "bar-1"
        layout = build_layout(validate_lighting(self.config))
        first, second, third = layout["bars"][:3]
        self.assertEqual(second["start"], first["end"])
        self.assertAlmostEqual(second["end"][1], first["end"][1] - 300)
        self.assertEqual(third["start"], first["end"])
        self.assertAlmostEqual(third["end"][0], first["end"][0] + 300)

    def test_product_frame_correction_keeps_controller_and_fields_unchanged(
        self,
    ):
        self.config["controller"]["mounting"].update(
            left_wall=830, front_wall=1200, height=0
        )
        self.config["bars"][1]["yaw"] = 90
        original = copy.deepcopy(self.config)
        layout = build_layout(self.config)
        self.assertEqual(layout["controller"], [830, 1200, 0])
        self.assertEqual(layout["bars"][0]["start"], [830, 1200, 0])
        first, second = layout["bars"][:2]
        self.assertAlmostEqual(first["end"][0], 1130)
        self.assertAlmostEqual(first["end"][1], 1200)
        self.assertAlmostEqual(second["end"][0], 1130)
        self.assertAlmostEqual(second["end"][1], 900)
        self.assertEqual(self.config, original)

    def test_roll_and_pitch_propagate_through_connections(self):
        self.config["controller"]["mounting"]["roll"] = 90
        self.config["bars"][0]["yaw"] = 90
        first = build_layout(self.config)["bars"][0]
        self.assertAlmostEqual(first["end"][2], first["start"][2] + 300)
        self.config["controller"]["mounting"]["roll"] = 0
        self.config["bars"][0].update(yaw=0, pitch=90)
        first = build_layout(self.config)["bars"][0]
        self.assertAlmostEqual(first["end"][2], first["start"][2] + 300)

    def test_invalid_connections_orders_and_product_are_rejected(self):
        for field, value in (
            ("connected_to", "bar-9"),
            ("connected_to", "missing"),
            ("wire_order", 2),
            ("wire_order", 1.5),
            ("product_id", "other"),
            ("yaw", float("nan")),
        ):
            invalid = copy.deepcopy(self.config)
            invalid["bars"][0][field] = value
            with self.assertRaises((ValueError, KeyError)):
                validate_lighting(invalid)

    def test_presence_proximity_and_no_presence(self):
        self.config["animation"].update(radius_mm=100, idle_pct=0)
        self.config["product"]["pixels_per_bar"] = 10
        layout, empty = render_frame(self.config, [], 1)
        self.assertTrue(all(pixel == [0, 0, 0] for pixel in empty))
        track = {"track_key": "sensor-1:1", "room_x_mm": 200, "room_y_mm": 100}
        _, near = render_frame(self.config, [track], 1)
        self.assertGreater(
            sum(sum(p) for p in near[:10]), sum(sum(p) for p in near[-10:])
        )
        self.assertGreater(sum(sum(p) for p in near), 0)
        self.assertEqual(len(near), 90)

    def test_reverse_pixels_changes_electrical_order_not_geometry(self):
        track = {"track_key": "sensor-1:1", "room_x_mm": 100, "room_y_mm": 100}
        self.config["product"]["pixels_per_bar"] = 10
        before, pixels = render_frame(self.config, [track], 1)
        self.config["bars"][0]["reversed"] = True
        after, reversed_pixels = render_frame(self.config, [track], 1)
        self.assertEqual(pixels[:10], list(reversed(reversed_pixels[:10])))
        self.assertEqual(before["bars"][0]["start"], after["bars"][0]["start"])
        self.assertEqual(before["bars"][0]["end"], after["bars"][0]["end"])

    def test_ddp_rgb_payload_offsets_and_final_push(self):
        pixels = [[index % 256, 20, 30] for index in range(600)]
        packets = list(ddp_packets(pixels, sequence=17))
        self.assertEqual(len(packets), 2)
        self.assertEqual(
            struct.unpack("!BBBBIH", packets[0][:10]),
            (0x40, 1, 0x0B, 1, 0, 1440),
        )
        self.assertEqual(
            struct.unpack("!BBBBIH", packets[1][:10]),
            (0x41, 1, 0x0B, 1, 1440, 360),
        )
        self.assertEqual(
            b"".join(packet[10:] for packet in packets),
            bytes(c for pixel in pixels for c in pixel),
        )

    def make_service(self):
        service = LightingService(None, start_worker=False)
        self.addCleanup(service.close)
        service.rpc = Mock(return_value=None)
        return service

    def test_preview_only_never_controls_lights(self):
        service = self.make_service()
        state = service.preview(self.config, [])
        self.assertFalse(state["output_enabled"])
        self.assertFalse(state["product_verified"])
        self.assertEqual(state["mapped_pixels"], 0)
        service.rpc.assert_not_called()
        with self.assertRaisesRegex(ValueError, "confirm pixels"):
            service.set_output(True, self.config)
        service.rpc.assert_not_called()

    def test_mismatched_pixel_count_is_rejected_before_output(self):
        service = self.make_service()
        self.config["product"].update(verified=True, pixels_per_bar=12)
        service.rpc.return_value = {"num_leds": 100}
        with self.assertRaisesRegex(ValueError, "layout maps 108"):
            service.set_output(True, self.config)
        self.assertEqual(service.rpc.call_count, 1)
        self.assertFalse(service.state["output_enabled"])

    def test_start_stream_stop_and_restore_over_loopback_udp(self):
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(3)
        self.addCleanup(receiver.close)
        self.config["controller"].update(
            device_host="127.0.0.1", ddp_port=receiver.getsockname()[1]
        )
        self.config["product"].update(verified=True, pixels_per_bar=12)
        self.config["animation"]["idle_pct"] = 10
        installation = Mock()
        installation.snapshot.return_value = {
            "config": {"lighting": self.config},
            "tracks": [],
        }
        service = LightingService(installation, start_worker=False)
        self.addCleanup(service.close)
        self.previous_status = {
            "id": 0,
            "on": True,
            "effect": "fade",
            "brightness": 77,
            "rgb": [10, 20, 30],
            "speed": 50,
        }
        service.rpc = Mock(side_effect=self.loopback_rpc)
        service.set_output(True, self.config)
        worker = threading.Thread(target=service.run, daemon=True)
        worker.start()
        try:
            packet, _ = receiver.recvfrom(2000)
            flags, sequence, data_type, destination, offset, length = (
                struct.unpack("!BBBBIH", packet[:10])
            )
            self.assertEqual(
                (flags, data_type, destination, offset, length),
                (0x41, 0x0B, 1, 0, 324),
            )
            self.assertEqual(len(packet), 334)
            self.assertTrue(service.state["output_enabled"])
            service.stop_output()
            self.assertFalse(service.state["output_enabled"])
            restore = next(
                call
                for call in service.rpc.call_args_list
                if call.args[1] == "LedStrip.Set"
                and call.kwargs.get("effect") == "fade"
            )
            self.assertEqual(restore.kwargs["brightness"], 77)
            self.assertEqual(restore.kwargs["rgb"], [10, 20, 30])
            self.assertTrue(
                any(
                    call.args[1] == "LedStrip.RemoveEffect"
                    for call in service.rpc.call_args_list
                )
            )
        finally:
            service.close()
            worker.join(timeout=3)

    def test_v3_migration_preserves_sensors_and_adds_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, start_worker=False)
            installation = app.extensions["installation"]
            old = copy.deepcopy(installation.config)
            old.pop("lighting")
            old["schema_version"] = 3
            upgraded = migrate_config(old)
            self.assertEqual(upgraded["sensors"], old["sensors"])
            self.assertEqual(upgraded["schema_version"], 6)
            self.assertEqual(
                controller_config(
                    upgraded["lighting"],
                    upgraded["lighting"]["controllers"][0],
                ),
                self.config,
            )
            installation.close()

    def test_api_preview_save_and_output_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, start_worker=False)
            installation = app.extensions["installation"]
            client = app.test_client()
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(
                len(client.get("/api/lighting").get_json()["bars"]), 9
            )
            config = installation.config
            config["lighting"]["controllers"][0]["bars"][1]["yaw"] = 45
            self.assertEqual(
                client.put("/api/config", json=config).status_code, 200
            )
            saved = json.loads(
                Path(directory, "config.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                saved["lighting"]["controllers"][0]["bars"][1]["yaw"], 45
            )
            self.assertEqual(
                client.post(
                    "/api/lighting/output", json={"enabled": True}
                ).status_code,
                400,
            )
            self.assertEqual(
                client.post(
                    "/api/lighting/output", json={"enabled": False}
                ).status_code,
                200,
            )
            self.assertEqual(
                client.post(
                    "/api/lighting/output", json={"enabled": "true"}
                ).status_code,
                400,
            )
            self.assertEqual(
                client.post(
                    "/api/lighting/output",
                    json={"enabled": False},
                    headers={"Origin": "https://other.example"},
                ).status_code,
                403,
            )
            installation.close()

    def loopback_rpc(self, controller, method, **params):
        if method == "LedStrip.GetConfig":
            return {
                "num_leds": 108,
                "ddp_port": self.config["controller"]["ddp_port"],
                "effects": ["fade"],
            }
        if method == "LedStrip.GetStatus":
            return self.previous_status
        return None


def main():
    """Run this test module directly."""
    unittest.main()


if __name__ == "__main__":
    main()
