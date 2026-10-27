#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test per-person colours shared by the dashboard and the lights."""

import json
import tempfile
import unittest

from led_presence.animations import presence_animation
from led_presence.core.paths import ROOT
from led_presence.core.track_colours import PALETTE, TrackColours
from led_presence.lights.lighting import default_lighting, render_frame
from led_presence.web.app import create_app


def track(key, x=1000, y=2000):
    """Build a minimal room-coordinate track."""
    return {"track_key": key, "room_x_mm": x, "room_y_mm": y}


def main():
    """Run this module's tests directly."""
    unittest.main()


class TrackColourTests(unittest.TestCase):
    """Colours are unique, stable and reach both web state and LEDs."""

    def test_simultaneous_people_get_unique_stable_colours(self):
        colours = TrackColours()
        first = colours.assign([track("s:1"), track("s:2"), track("s:3")], 0)
        assigned = [tuple(t["color"]) for t in first]
        self.assertEqual(len(set(assigned)), 3)
        again = colours.assign([track("s:3"), track("s:1")], 1)
        self.assertEqual(again[0]["color"], first[2]["color"])
        self.assertEqual(again[1]["color"], first[0]["color"])

    def test_brief_dropout_keeps_colour_and_expiry_frees_it(self):
        colours = TrackColours(hold_seconds=3)
        original = colours.assign([track("s:1"), track("s:2")], 0)
        colours.assign([track("s:2")], 1)
        returned = colours.assign([track("s:1"), track("s:2")], 2)
        self.assertEqual(returned[0]["color"], original[0]["color"])
        colours.assign([track("s:2")], 3)
        newcomer = colours.assign([track("s:2"), track("s:9")], 10)
        # s:1 expired, so the newcomer may reuse its colour.
        self.assertEqual(newcomer[1]["color"], original[0]["color"])

    def test_full_palette_reuses_absent_colours_before_repeating(self):
        colours = TrackColours(palette=PALETTE[:2], hold_seconds=60)
        colours.assign([track("s:1"), track("s:2")], 0)
        later = colours.assign([track("s:2"), track("s:3")], 1)
        self.assertNotEqual(later[0]["color"], later[1]["color"])
        crowded = colours.assign([track(f"s:{i}") for i in range(2, 6)], 2)
        self.assertEqual(len(crowded), 4)
        self.assertTrue(all(tuple(t["color"]) in PALETTE for t in crowded))

    def test_snapshot_tracks_carry_colours(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, start_worker=False)
            installation = app.extensions["installation"]
            try:
                config = json.loads(
                    (ROOT / "config.example.json").read_text(encoding="utf-8")
                )
                installation.save(config)
                reader = next(iter(installation.readers.values()))
                reader.state["connected"] = True
                reader.handle_message(
                    {
                        "params": {
                            "events": [
                                {
                                    "component": "presence",
                                    "event": "track",
                                    "object": [
                                        {"id": 1, "x": 0.5, "y": 1},
                                        {"id": 2, "x": -0.5, "y": 2},
                                    ],
                                }
                            ]
                        }
                    }
                )
                tracks = installation.snapshot()["tracks"]
                self.assertEqual(len({tuple(t["color"]) for t in tracks}), 2)
                again = installation.snapshot()["tracks"]
                self.assertEqual(
                    [t["color"] for t in again], [t["color"] for t in tracks]
                )
            finally:
                installation.close()

    def test_led_effects_use_the_person_colour(self):
        red, blue = [255, 0, 0], [0, 0, 255]
        for animation_id in presence_animation.ANIMATIONS:
            with self.subTest(animation=animation_id):
                frames = []
                for colour in (red, blue):
                    person = {**track("s:1", 1500, 1200), "color": colour}
                    frames.append(
                        presence_animation.render(
                            {
                                "time_s": 1,
                                "pixel_count": 100,
                                "sensor": {
                                    "mounting": {
                                        "left_wall": 0,
                                        "front_wall": 0,
                                    }
                                },
                                "room": {"width_mm": 3000},
                                "animation_id": animation_id,
                                "pixel_positions": [
                                    [i * 30, 1200, 0] for i in range(100)
                                ],
                                "tracks": [person],
                            }
                        )
                    )
                reds, blues = frames
                self.assertGreater(
                    sum(p[0] for p in reds), sum(p[2] for p in reds)
                )
                self.assertGreater(
                    sum(p[2] for p in blues), sum(p[0] for p in blues)
                )

    def test_controller_effect_uses_the_person_colour(self):
        config = default_lighting()
        config["animation"].update(radius_mm=400, idle_pct=0)
        config["product"]["pixels_per_bar"] = 10
        person = {**track("s:1", 200, 100), "color": [0, 255, 0]}
        _, pixels = render_frame(config, [person], 1)
        self.assertGreater(sum(p[1] for p in pixels), 0)
        self.assertEqual(sum(p[0] + p[2] for p in pixels), 0)


if __name__ == "__main__":
    main()
