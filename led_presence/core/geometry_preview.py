#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build draft geometry without device workers, animation or light output."""

import copy

from led_presence.lights.controller_config import controller_config
from led_presence.lights.lighting import build_layout


def geometry_preview(config):
    """Return the 3D renderer's state contract using geometry only."""
    groups, bars = [], []
    for group in config["lighting"]["controllers"]:
        layout = build_layout(controller_config(config["lighting"], group))
        frame = {
            **layout,
            "id": group["id"],
            "name": group["name"],
            "enabled": group["enabled"],
            "controller_on": None,
        }
        groups.append(frame)
        for bar in frame["bars"]:
            bar["color"] = [68, 149, 209]
            bars.append({**bar, "controller_id": group["id"]})
    return {
        "config": copy.deepcopy(config),
        "sensors": [],
        "tracks": [],
        "lighting": {"controllers": groups, "bars": bars},
    }
