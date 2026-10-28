#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test Flask pages, sensor geometry and configuration persistence."""

import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from led_presence.core.geometry import place_track
from led_presence.core.installation import Installation
from led_presence.core.paths import ROOT
from led_presence.web.app import create_app


class AppTests(unittest.TestCase):
    """Exercise isolated app instances without physical sensor connections."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(self.directory.name, start_worker=False)
        self.client = self.app.test_client()
        self.installation = self.app.extensions["installation"]
        self.config = json.loads(
            (ROOT / "config.example.json").read_text(encoding="utf-8")
        )
        self.config["room"].update(
            width_mm=6000, depth_mm=8000, height_mm=3000
        )
        self.config["sensors"][0]["mounting"].update(
            height=2000, left_wall=3000, front_wall=0, yaw=0, pitch=15, roll=0
        )
        self.installation.save(self.config)

    def tearDown(self):
        self.installation.close()
        self.directory.cleanup()

    def two_sensors(self):
        second = copy.deepcopy(self.config["sensors"][0])
        second.update(
            id="sensor-2", name="Presence 2", device_host="192.0.2.2"
        )
        second["mounting"].update(left_wall=1000, yaw=90)
        self.config["sensors"].append(second)
        self.installation.save(self.config)

    @staticmethod
    def event(kind="track", **extra):
        return {
            "params": {
                "events": [{"component": "presence", "event": kind, **extra}]
            }
        }

    def test_home_and_initial_state(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        state = self.client.get("/api/state").get_json()
        self.assertEqual(state["units"], "mm")
        self.assertFalse(state["sensors"][0]["connected"])
        self.assertTrue(state["sensors"][0]["stale"])
        self.assertEqual(state["tracks"], [])

    def test_bundled_fonts_are_served_without_system_installation(self):
        for style in ("Regular", "Italic"):
            with self.client.get(
                f"/static/fonts/Roboto-{style}.ttf"
            ) as response:
                self.assertEqual(response.status_code, 200)
                self.assertTrue(
                    response.get_data().startswith(b"\x00\x01\x00\x00")
                )
        with self.client.get("/static/fonts/OFL.txt") as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"SIL OPEN FONT LICENSE", response.get_data())
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn('as="font"', page)
        self.assertIn("fonts/Roboto-Regular.ttf", page)

    def test_three_d_page_uses_live_geometry_and_separate_renderer(self):
        response = self.client.get("/3d")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('aria-current="page">3D</a>', page)
        self.assertLess(page.index(">Dashboard</a>"), page.index(">3D</a>"))
        self.assertLess(page.index(">3D</a>"), page.index(">Devices</a>"))
        self.assertIn("/static/three-d.js", page)
        self.assertNotIn("/static/app.js", page)
        self.assertIn('id="scene"', page)
        self.assertIn('id="scene-objects"', page)
        self.assertIn("not calibrated room height", page)
        self.two_sensors()
        state = self.client.get("/api/state").get_json()
        self.assertEqual(state["config"]["room"]["height_mm"], 3000)
        self.assertEqual(len(state["config"]["sensors"]), 2)
        controller = state["lighting"]["controllers"][0]
        self.assertEqual(len(controller["controller"]), 3)
        for bar in controller["bars"]:
            self.assertEqual(len(bar["start"]), 3)
            self.assertEqual(len(bar["end"]), 3)
        for path in ("/", "/devices", "/geometry"):
            page = self.client.get(path).get_data(as_text=True)
            self.assertIn('href="/3d"', page)

    def test_pages_have_separate_installation_editors(self):
        pages = {}
        for path in ("/", "/devices", "/geometry"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            pages[path] = response.get_data(as_text=True)
            for destination in ("/", "/devices", "/geometry"):
                self.assertIn(f'href="{destination}"', pages[path])
        dashboard, geometry, lights = (
            pages["/"],
            pages["/geometry"],
            pages["/devices"],
        )
        self.assertEqual(
            self.client.get("/nolu").headers["Location"], "/devices#lights"
        )
        self.assertEqual(
            self.client.get("/lights").headers["Location"], "/devices#lights"
        )
        self.assertNotIn('id="settings"', dashboard)
        self.assertNotIn('id="lighting-settings"', dashboard)
        self.assertNotIn('id="bar-editors"', dashboard)
        self.assertNotIn('id="lighting-start"', dashboard)
        self.assertNotIn('id="lighting-preview"', dashboard)
        self.assertNotIn('id="sensor-status"', dashboard)
        self.assertNotIn("/static/lighting.js", dashboard)
        self.assertIn('id="settings"', geometry)
        self.assertIn('id="room-settings"', geometry)
        self.assertIn('id="sensor-status"', geometry)
        self.assertIn('role="tab" id="geometry-tab-room"', geometry)
        self.assertIn('role="tab" id="geometry-tab-sensors"', geometry)
        self.assertIn(
            'id="geometry-panel-sensors" '
            'aria-labelledby="geometry-tab-sensors" hidden',
            geometry,
        )
        self.assertIn('id="lighting-settings"', geometry)
        self.assertIn('id="bar-editors"', geometry)
        self.assertIn('id="lighting-settings"', lights)
        self.assertNotIn('id="bar-editors"', lights)
        self.assertNotIn('data-light="product.length_mm"', lights)
        self.assertIn('data-light="animation.mode"', lights)
        self.assertIn('id="lighting-start"', lights)
        self.assertIn('id="lighting-preview"', lights)
        self.assertNotIn('id="settings"', lights)

    def test_mm_conversion_rotation_and_sensor_identity(self):
        sensor = self.config["sensors"][0]
        sensor["mounting"].update(left_wall=2000, front_wall=3000, yaw=90)
        original = {"id": 7, "x": 1, "y": 2, "z": -0.2, "minz": 1, "maxz": 2}
        track = place_track(original, sensor)
        self.assertAlmostEqual(track["room_x_mm"], 4000)
        self.assertAlmostEqual(track["room_y_mm"], 4000)
        self.assertEqual(track["reported_mm"]["z_mm"], -200)
        self.assertEqual(track["reported_mm"]["minz_mm"], 1000)
        self.assertEqual(track["reported_mm"]["maxz_mm"], 2000)
        self.assertEqual(track["track_key"], "sensor-1:7")
        self.assertEqual(original["x"], 1)

    def test_sensor_facing_left_reference_and_known_forward_position(self):
        sensor = self.config["sensors"][0]
        sensor["mounting"].update(left_wall=1700, front_wall=390, yaw=0)
        forward = place_track({"id": 1, "x": 0, "y": 2}, sensor)
        self.assertEqual(forward["room_x_mm"], 1700)
        self.assertEqual(forward["room_y_mm"], 2390)
        device_right = place_track({"id": 1, "x": 0.3, "y": 2}, sensor)
        self.assertEqual(device_right["room_x_mm"], 1400)

    def test_v4_wall_reference_migration_keeps_entered_measurements(self):
        old = copy.deepcopy(self.config)
        old["schema_version"] = 4
        old["sensors"][0]["mounting"].update(
            left_wall=1700, front_wall=390, height=700
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "config.json")
            path.write_text(json.dumps(old), encoding="utf-8")
            installation = Installation(path, start_workers=False)
            self.assertEqual(installation.config["schema_version"], 6)
            self.assertEqual(installation.config["sensors"], old["sensors"])
            self.assertEqual(installation.config["lighting"], old["lighting"])
            self.assertTrue(Path(directory, "config.v4.backup.json").exists())
            installation.close()

    def test_configuration_persists_and_instances_are_isolated(self):
        self.two_sensors()
        self.config["room"]["width_mm"] = 9000
        self.assertEqual(
            self.client.put("/api/config", json=self.config).status_code, 200
        )
        restarted = create_app(self.directory.name, start_worker=False)
        loaded = restarted.test_client().get("/api/config").get_json()
        self.assertEqual(loaded["room"]["width_mm"], 9000)
        self.assertEqual(len(loaded["sensors"]), 2)
        restarted.extensions["installation"].close()
        with tempfile.TemporaryDirectory() as other:
            separate = create_app(other, start_worker=False)
            self.assertEqual(
                separate.test_client()
                .get("/api/config")
                .get_json()["room"]["width_mm"],
                json.loads(
                    (ROOT / "config.example.json").read_text(encoding="utf-8")
                )["room"]["width_mm"],
            )
            separate.extensions["installation"].close()

    def test_v1_migration_preserves_measurements_and_original_backup(self):
        old = {
            "schema_version": 1,
            "installation_name": "Existing room",
            "calibrated": True,
            "device_host": "192.168.88.184",
            "room": {"width_m": 5.25, "depth_m": 8, "height_m": 3},
            "sensor": {
                "x_m": 1.25,
                "y_m": -0.5,
                "yaw_deg": 45,
                "mount_height_m": 2.1,
                "tilt_deg": 15,
            },
            "physical": {"notes": "Keep this note"},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "config.json")
            path.write_text(json.dumps(old), encoding="utf-8")
            original = path.read_bytes()
            installation = Installation(path, start_workers=False)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["schema_version"], 6)
            self.assertEqual(saved["room"]["width_mm"], 5250)
            self.assertEqual(
                saved["sensors"][0]["mounting"]["left_wall"], 1250
            )
            self.assertEqual(
                saved["sensors"][0]["mounting"]["front_wall"], -500
            )
            self.assertEqual(saved["sensors"][0]["mounting"]["height"], 2100)
            self.assertEqual(saved["sensors"][0]["mounting"]["yaw"], 45)
            self.assertTrue(saved["calibrated"])
            self.assertEqual(saved["physical"]["notes"], "Keep this note")
            self.assertEqual(
                Path(directory, "config.v1.backup.json").read_bytes(), original
            )
            installation.close()
            # Loading v5 again must not multiply distances a second time.
            installation = Installation(path, start_workers=False)
            self.assertEqual(installation.config["room"]["width_mm"], 5250)
            installation.close()

    def test_invalid_config_never_overwrites_saved_parameters(self):
        original = Path(self.directory.name, "config.json").read_bytes()
        for value in (-1, 0, float("inf"), True):
            invalid = copy.deepcopy(self.config)
            invalid["room"]["width_mm"] = value
            self.assertEqual(
                self.client.put("/api/config", json=invalid).status_code, 400
            )
        for field, value in (
            ("device_host", "not-an-ip"),
            ("id", "bad id"),
            ("enabled", 1),
        ):
            invalid = copy.deepcopy(self.config)
            invalid["sensors"][0][field] = value
            self.assertEqual(
                self.client.put("/api/config", json=invalid).status_code, 400
            )
        self.assertEqual(
            self.client.put("/api/config", json={}).status_code, 400
        )
        self.assertEqual(
            Path(self.directory.name, "config.json").read_bytes(), original
        )

    def test_nested_mounting_round_trip_and_validation(self):
        mounting = self.config["sensors"][0]["mounting"]
        mounting.update(
            height=700,
            left_wall=1700,
            front_wall=390,
            yaw=45,
            pitch=-10,
            roll=5,
        )
        response = self.client.put("/api/config", json=self.config)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["sensors"][0]["mounting"], mounting
        )
        for key in (
            "height",
            "left_wall",
            "front_wall",
            "yaw",
            "pitch",
            "roll",
        ):
            invalid = copy.deepcopy(self.config)
            invalid["sensors"][0]["mounting"][key] = float("nan")
            self.assertEqual(
                self.client.put("/api/config", json=invalid).status_code, 400
            )

    def test_v2_migration_preserves_all_sensors_and_mm_values(self):
        self.two_sensors()
        old = copy.deepcopy(self.config)
        old["schema_version"] = 2
        for sensor in old["sensors"]:
            mounting = sensor.pop("mounting")
            sensor.update(
                x_mm=mounting["left_wall"],
                y_mm=mounting["front_wall"],
                mount_height_mm=mounting["height"],
                yaw_deg=mounting["yaw"],
                tilt_deg=mounting["pitch"],
            )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "config.json")
            path.write_text(json.dumps(old), encoding="utf-8")
            original = path.read_bytes()
            installation = Installation(path, start_workers=False)
            self.assertEqual(installation.config, self.config)
            self.assertEqual(
                Path(directory, "config.v2.backup.json").read_bytes(), original
            )
            installation.close()

    def test_duplicate_sensor_ids_and_addresses_are_rejected(self):
        self.two_sensors()
        for key in ("id", "device_host"):
            invalid = copy.deepcopy(self.config)
            invalid["sensors"][1][key] = invalid["sensors"][0][key]
            self.assertEqual(
                self.client.put("/api/config", json=invalid).status_code, 400
            )

    def test_cross_origin_write_is_rejected(self):
        response = self.client.put(
            "/api/config",
            json=self.config,
            headers={"Origin": "https://other.example"},
        )
        self.assertEqual(response.status_code, 403)

    def test_simultaneous_tracks_have_distinct_identity_and_transforms(self):
        self.two_sensors()
        for reader in self.installation.readers.values():
            reader.state["connected"] = True
            reader.handle_message(
                self.event(object=[{"id": 1, "x": 1, "y": 2}])
            )
        state = self.installation.snapshot()
        self.assertEqual(len(state["tracks"]), 2)
        self.assertEqual(
            {t["track_key"] for t in state["tracks"]},
            {"sensor-1:1", "sensor-2:1"},
        )
        self.assertEqual(state["tracks"][0]["room_x_mm"], 2000)
        self.assertAlmostEqual(state["tracks"][1]["room_x_mm"], 3000)
        # One sensor going offline or stale does not remove another sensor's
        # tracks.
        second = self.installation.readers["sensor-2"]
        second.state["connected"] = False
        self.assertEqual(len(self.installation.snapshot()["tracks"]), 1)
        second.state.update(connected=True, last_event_at=time.time() - 10)
        self.assertEqual(len(self.installation.snapshot()["tracks"]), 1)
        self.installation.readers["sensor-1"].handle_message(
            self.event("no_track")
        )
        self.assertEqual(self.installation.snapshot()["tracks"], [])

    def test_editing_geometry_keeps_connection_but_changes_position(self):
        reader = self.installation.readers["sensor-1"]
        reader.state["connected"] = True
        reader.handle_message(self.event(object=[{"id": 1, "x": 1, "y": 2}]))
        self.config["sensors"][0]["mounting"]["left_wall"] = 5000
        self.installation.save(self.config)
        self.assertIs(self.installation.readers["sensor-1"], reader)
        self.assertEqual(
            self.installation.snapshot()["tracks"][0]["room_x_mm"], 4000
        )

    def test_add_disable_enable_readdress_remove_readers(self):
        self.two_sensors()
        first = self.installation.readers["sensor-1"]
        second = self.installation.readers["sensor-2"]
        self.config["sensors"][1]["enabled"] = False
        self.installation.save(self.config)
        self.assertTrue(second.stop_event.is_set())
        self.assertNotIn("sensor-2", self.installation.readers)
        self.config["sensors"][1]["enabled"] = True
        self.installation.save(self.config)
        replacement = self.installation.readers["sensor-2"]
        self.assertIsNot(replacement, second)
        self.config["sensors"][1]["device_host"] = "192.0.2.3"
        self.installation.save(self.config)
        self.assertTrue(replacement.stop_event.is_set())
        self.assertEqual(
            self.installation.readers["sensor-2"].host, "192.0.2.3"
        )
        self.config["sensors"].pop()
        self.installation.save(self.config)
        self.assertIs(self.installation.readers["sensor-1"], first)
        self.assertEqual(len(self.installation.readers), 1)
        self.config["sensors"] = []
        self.installation.save(self.config)
        self.assertTrue(first.stop_event.is_set())
        self.assertEqual(self.installation.snapshot()["sensors"], [])

    def test_worker_starts_for_each_enabled_sensor_only(self):
        with patch(
            "led_presence.core.installation.SensorReader.start"
        ) as start:
            self.installation.start_workers = True
            self.two_sensors()
            self.assertEqual(
                start.call_count, 1
            )  # first reader already exists
            self.installation.save(self.config)
            self.assertEqual(
                start.call_count, 1
            )  # saving twice does not duplicate workers


def main():
    """Run this test module directly."""
    unittest.main()


if __name__ == "__main__":
    main()
