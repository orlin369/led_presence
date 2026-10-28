#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exercise polygon validation, draft isolation and conflict-safe saves."""

import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from led_presence.config.configuration import migrate_config, validate_config
from led_presence.core.room_geometry import room_outline, validate_outline
from led_presence.web.app import create_app


class RoomGeometryTests(unittest.TestCase):
    """Keep legacy coordinates intact while supporting concave floor plans."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(self.directory.name, start_worker=False)
        self.client = self.app.test_client()
        self.installation = self.app.extensions["installation"]
        self.config = copy.deepcopy(self.installation.config)
        self.original_bytes = self.installation.path.read_bytes()

    def tearDown(self):
        self.installation.close()
        self.directory.cleanup()

    def l_shaped(self):
        config = copy.deepcopy(self.config)
        room = config["room"]
        w, d = room["width_mm"], room["depth_mm"]
        room["outline_mm"] = [
            [0, 0],
            [w, 0],
            [w, d / 2],
            [w / 2, d / 2],
            [w / 2, d],
            [0, d],
        ]
        return config

    def test_legacy_rectangle_and_concave_polygon_preserve_coordinates(self):
        self.assertEqual(migrate_config(self.config), self.config)
        self.assertNotIn("outline_mm", self.config["room"])
        self.assertEqual(len(room_outline(self.config["room"])), 4)
        config = self.l_shaped()
        self.assertEqual(validate_config(config), config)
        self.assertEqual(config["sensors"], self.config["sensors"])
        self.assertEqual(config["lighting"], self.config["lighting"])
        config["room"]["outline_mm"].reverse()
        validate_config(config)
        # Collinear corners are valid when users split an existing wall.
        config["room"]["outline_mm"] = [
            [0, 0],
            [500, 0],
            [1000, 0],
            [1000, 1000],
            [0, 1000],
        ]
        validate_config(config)

    def test_invalid_polygons_are_rejected_without_writing(self):
        cases = [
            None,
            [],
            [[0, 0], [1000, 0]],
            [[0, 0], [1000, 1000], [0, 1000], [1000, 0]],
            [[0, 0], [1000, 0], [1000, 1000], [0, 0]],
            [[0, 0], [1000, 0], [500, 0], [0, 1000]],
            [[0, 0], [1000, 0], [500, 0]],
            [[0, 0], [1000, 0], [0, float("nan")]],
            [[0, 0], [1000, 0], [False, 1000]],
            [[-1, 0], [1000, 0], [0, 1000]],
            [[0, 0], [1000001, 0], [0, 1000]],
            [[0, 0], [1000, 0], [0, 1000, 5]],
            [[0, 0], [1000, 0], [1000, 1000], [500, 0], [0, 1000]],
            [[i, i] for i in range(65)],
        ]
        for points in cases:
            with self.subTest(points=points):
                config = copy.deepcopy(self.config)
                config["room"]["outline_mm"] = points
                with self.assertRaises(ValueError):
                    validate_outline(config["room"])
                response = self.client.put("/api/config", json=config)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    self.installation.path.read_bytes(), self.original_bytes
                )

    def test_draft_preview_never_changes_configuration_or_light_state(self):
        config = self.l_shaped()
        config["sensors"][0]["mounting"]["left_wall"] += 100
        group = config["lighting"]["controllers"][0]
        group["controller"]["mounting"]["height"] += 200
        group["bars"][0].update(yaw=30, pitch=20, roll=10)
        with patch.object(self.installation, "save") as save:
            response = self.client.post(
                "/api/geometry/draft", json={"config": config}
            )
            self.assertEqual(response.status_code, 200)
            save.assert_not_called()
        frame = response.get_json()
        self.assertEqual(frame["config"], config)
        self.assertEqual(frame["tracks"], [])
        self.assertEqual(
            frame["lighting"]["controllers"][0]["controller"][2],
            group["controller"]["mounting"]["height"],
        )
        self.assertEqual(self.installation.config, self.config)
        self.assertEqual(
            self.installation.path.read_bytes(), self.original_bytes
        )

    def test_save_persists_polygon_and_stops_only_changed_controller(self):
        config = self.l_shaped()
        group = config["lighting"]["controllers"][0]
        group["controller"]["mounting"]["left_wall"] += 150
        service = self.installation.lighting.services[group["id"]]
        with patch.object(service, "stop_output") as stop:
            response = self.client.put(
                "/api/geometry/draft",
                json={"config": config, "base_config": self.config},
            )
            self.assertEqual(response.status_code, 200)
            stop.assert_called_once()
        self.assertEqual(self.installation.config, config)
        self.assertEqual(
            json.loads(self.installation.path.read_text()), config
        )
        self.assertEqual(
            self.installation.config["sensors"], self.config["sensors"]
        )

    def test_stale_editor_cannot_overwrite_another_saved_change(self):
        other = copy.deepcopy(self.config)
        other["sensors"][0]["name"] = "Renamed in Devices"
        self.installation.save(other)
        with patch.object(self.installation, "save") as save:
            response = self.client.put(
                "/api/geometry/draft",
                json={"config": self.l_shaped(), "base_config": self.config},
            )
            self.assertEqual(response.status_code, 409)
            save.assert_not_called()
        self.assertEqual(self.installation.config, other)
        response = self.client.put(
            "/api/geometry/draft", json={"config": self.l_shaped()}
        )
        self.assertEqual(response.status_code, 409)

    def test_draft_validation_and_cross_origin_guard(self):
        for method in (self.client.post, self.client.put):
            self.assertEqual(method("/api/geometry/draft").status_code, 415)
            for data in (None, [], {}, {"config": []}):
                response = method(
                    "/api/geometry/draft",
                    data=json.dumps(data),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)
            self.assertEqual(
                method(
                    "/api/geometry/draft",
                    json={"config": self.config},
                    headers={"Origin": "https://example.com"},
                ).status_code,
                403,
            )
            bad = self.l_shaped()
            bad["room"]["outline_mm"][0] = [-1, 0]
            self.assertEqual(
                method(
                    "/api/geometry/draft", json={"config": bad}
                ).status_code,
                400,
            )
        self.assertEqual(self.installation.config, self.config)

    def test_editor_and_renderer_assets_use_separate_drafts(self):
        response = self.client.get("/geometry/editor")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('aria-current="page">Geometry</a>', page)
        self.assertIn('id="editor-plan"', page)
        self.assertIn('data-draft="true"', page)
        self.assertIn('type="module"', page)
        self.assertNotIn("/static/app.js", page)
        self.assertNotIn("/static/lighting.js", page)
        self.assertIn(
            "/geometry/editor",
            self.client.get("/geometry").get_data(as_text=True),
        )
        self.assertNotIn(
            'data-draft="true"', self.client.get("/3d").get_data(as_text=True)
        )
        self.assertEqual(
            self.installation.path.read_bytes(), self.original_bytes
        )


def main():
    """Run geometry tests directly."""
    unittest.main()


if __name__ == "__main__":
    main()
