#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Editable animation program for the dashboard Start/Stop button.

Stop the Flask app before editing this file, then restart it. No hot reload is
performed. Edit render(context) or the constants below; networking, smoothing
and restoring the previous light effect are handled by the app.

context contains:
  time_s: elapsed seconds since Start
  pixel_count: actual configured LED count reported by this controller
  tracks: fresh room-coordinate tracks from the selected sensor only (mm);
          each has the person's dashboard `color` as [r, g, b] (0-255)
  sensor: selected sensor definition, including mounting (mm / degrees)
  room: room dimensions (mm)
  controller: this light controller's settings and mounting
  pixel_positions: room-coordinate [x, y, z] positions in electrical order (mm)
  state: persistent dictionary for this controller, cleared on each Start

Return exactly pixel_count RGB triples, with integer channels from 0 to 255.
Each person projects a bright core and halo onto the nearest lights in the
same colour the dashboard shows for them. Movement leaves a fading wake;
distance changes halo width.
"""

import colorsys
import math

NAME = "Presence ribbons"
DEFAULT_ANIMATION = "ribbons"
ANIMATIONS = {
    "spotlight": {
        "name": "Spotlight",
        "description": "A small, fixed-width pool of light follows each "
        "person.",
    },
    "ribbons": {
        "name": "Presence ribbons",
        "description": "Coloured halos follow people and leave a fading wake.",
    },
    "ripples": {
        "name": "Ripples",
        "description": "Waves spread out from each person's position on the "
        "lights.",
    },
    "fireflies": {
        "name": "Fireflies",
        "description": "A cluster of twinkling sparks follows each person.",
    },
}
DEFAULT_BRIGHTNESS_PCT = 25
TRAIL_SECONDS = 0.7
HALO_WIDTH_MM = 250


def nearest_position(positions, x, y):
    """Find the closest light sample in the horizontal room plane."""
    closest = positions[0]
    best_distance = math.inf
    for position in positions:
        distance = (position[0] - x) ** 2 + (position[1] - y) ** 2
        if distance < best_distance:
            closest = position
            best_distance = distance
    return closest


def track_colour(track, proximity):
    """Use the person's dashboard colour, or a proximity hue without one."""
    if track.get("color"):
        return [channel / 255 for channel in track["color"]]
    return colorsys.hsv_to_rgb(0.62 - 0.52 * proximity, 0.9, 1)


def render(context):
    """Render the selected effect using fresh tracks and controller state."""
    count = context["pixel_count"]
    positions = context.get("pixel_positions") or [
        [(i + 0.5) / count * context["room"]["width_mm"], 0, 0]
        for i in range(count)
    ]
    now = context["time_s"]
    animation = context.get("animation_id", DEFAULT_ANIMATION)
    if animation not in ANIMATIONS:
        raise ValueError("Unknown animation")
    state = context.setdefault("state", {})
    lifetime = TRAIL_SECONDS if animation == "ribbons" else 0.12
    decay = math.exp(-max(0, now - state.get("time", now)) / lifetime)
    previous = state.get("trail", [[0.0, 0.0, 0.0] for _ in range(count)])
    pixels = [[channel * decay for channel in pixel] for pixel in previous]
    mounting = context["sensor"]["mounting"]
    for track in context["tracks"]:
        x, y = track["room_x_mm"], track["room_y_mm"]
        # Project onto the physical light path rather than requiring people
        # to stand on the bars. Height is intentionally ignored for radar
        # tracks.
        anchor = nearest_position(positions, x, y)
        depth = math.hypot(
            x - mounting["left_wall"], y - mounting["front_wall"]
        )
        proximity = math.exp(-depth / 4000)
        colour = track_colour(track, proximity)
        width = HALO_WIDTH_MM * (0.65 + 0.7 * proximity)
        for i, position in enumerate(positions):
            distance = math.hypot(
                position[0] - anchor[0], position[1] - anchor[1]
            )
            core = math.exp(-0.5 * (distance / 65) ** 2)
            halo = math.exp(-0.5 * (distance / width) ** 2)
            wave = 0.75 + 0.25 * math.cos(distance / 110 - now * 3)
            if animation == "spotlight":
                core = math.exp(-0.5 * (distance / 85) ** 2)
                halo = core * 0.25
                wave = 1
            elif animation == "ripples":
                phase = (now * 240) % 650
                ring = math.exp(-0.5 * ((distance - phase) / 55) ** 2)
                core = math.exp(-0.5 * (distance / 60) ** 2) * 0.35
                halo = ring * math.exp(-distance / 900)
                wave = 1
            elif animation == "fireflies":
                # Stable per-address phases avoid random flicker on every poll.
                spark = (0.5 + 0.5 * math.sin(now * 3 + i * 2.399)) ** 14
                core = 0
                halo = math.exp(-0.5 * (distance / 300) ** 2) * spark
                wave = 1.5
            for c in range(3):
                # Tint the core with the person's colour so it stays
                # recognisable; a little white keeps the centre brightest.
                emission = min(
                    1,
                    colour[c] * (core * 0.8 + halo * wave * 0.65) + core * 0.2,
                )
                pixels[i][c] = max(pixels[i][c], emission)
    state.update(time=now, trail=pixels)
    # Return full-range RGB; transport applies the dashboard brightness.
    scale = 255
    return [tuple(round(min(1, c) * scale) for c in pixel) for pixel in pixels]
