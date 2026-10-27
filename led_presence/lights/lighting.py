#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NOLU product geometry, presence animation and DDP output.

Product dimensions are defined once; instances form a controller-rooted tree.
Transport follows RGB888 DDP v1 (10-byte header; byte offsets; final PUSH).
"""

import colorsys
import hashlib
import ipaddress
import math
import re
import struct
from operator import itemgetter


def default_lighting():
    return {
        "controller": {
            "device_host": "192.168.88.182",
            "component_id": 0,
            "ddp_port": 4048,
            "mounting": {
                "height": 2000,
                "left_wall": 100,
                "front_wall": 100,
                "yaw": 0,
                "pitch": 0,
                "roll": 0,
            },
        },
        "product": {
            "id": "shelly-nolu-bar",
            "name": "Shelly NOLU Bar",
            "length_mm": 300,
            "pixels_per_bar": 0,
            "verified": False,
        },
        "bars": [
            {
                "id": f"bar-{i}",
                "product_id": "shelly-nolu-bar",
                "connected_to": "controller" if i == 1 else f"bar-{i - 1}",
                "wire_order": i,
                "yaw": -90 if i == 1 else 0,
                "pitch": 0,
                "roll": 0,
                "reversed": False,
            }
            for i in range(1, 10)
        ],
        "animation": {
            "mode": "aura",
            "brightness_pct": 35,
            "radius_mm": 1800,
            "speed": 0.6,
            "fade_seconds": 0.6,
            "idle_pct": 0,
        },
    }


def validate_lighting(config):

    controller, product = config["controller"], config["product"]
    ipaddress.IPv4Address(controller["device_host"])
    validate_lighting_number(controller, "component_id", 0, 255, True)
    validate_lighting_number(controller, "ddp_port", 1, 65535, True)
    for key in ("height", "left_wall", "front_wall", "yaw", "pitch", "roll"):
        validate_lighting_number(
            controller["mounting"], key, -1_000_000, 1_000_000
        )
    if not isinstance(product["id"], str) or not re.fullmatch(
        r"[a-zA-Z0-9_-]{1,64}", product["id"]
    ):
        raise ValueError("Provide a valid lighting product ID")
    if not isinstance(product["name"], str) or not product["name"].strip():
        raise ValueError("Provide a product name")
    validate_lighting_number(product, "length_mm", 1, 10000)
    validate_lighting_number(product, "pixels_per_bar", 0, 1000, True)
    if not isinstance(product["verified"], bool):
        raise ValueError("Product verified must be true or false")
    bars = config["bars"]
    if not isinstance(bars, list) or not 1 <= len(bars) <= 64:
        raise ValueError("Configure between 1 and 64 bars")
    ids = set()
    for bar in bars:
        if (
            not isinstance(bar["id"], str)
            or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", bar["id"])
            or bar["id"] == "controller"
            or bar["id"] in ids
        ):
            raise ValueError(
                "Bar IDs must be unique identifiers other than controller"
            )
        ids.add(bar["id"])
        if bar["product_id"] != product["id"]:
            raise ValueError("Each bar must reference the defined product")
        validate_lighting_number(bar, "wire_order", 1, len(bars), True)
        for key in ("yaw", "pitch", "roll"):
            validate_lighting_number(bar, key, -360, 360)
        if not isinstance(bar["reversed"], bool):
            raise ValueError("Bar reversed must be true or false")
    if len({bar["wire_order"] for bar in bars}) != len(bars):
        raise ValueError(
            "Pixel orders must be unique, from 1 to the number of bars"
        )
    parents = {bar["id"]: bar["connected_to"] for bar in bars}
    for bar_id in ids:
        visited = set()
        current = bar_id
        while current != "controller":
            if current in visited:
                raise ValueError("Bar connections contain a cycle")
            visited.add(current)
            if current not in parents:
                raise ValueError(
                    "Every bar must connect back to the controller"
                )
            current = parents[current]
    if len(bars) * product["pixels_per_bar"] > 10000:
        raise ValueError("The installation supports at most 10000 pixels")
    animation = config["animation"]
    if animation["mode"] not in ("aura", "ripple"):
        raise ValueError("Animation mode must be aura or ripple")
    for key, low, high in (
        ("brightness_pct", 0, 100),
        ("radius_mm", 1, 20000),
        ("speed", 0.05, 3),
        ("fade_seconds", 0.05, 10),
        ("idle_pct", 0, 100),
    ):
        validate_lighting_number(animation, key, low, high)
    return config


def multiply(a, b):
    return [
        [sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
        for i in range(3)
    ]


def rotation(yaw, pitch, roll):
    """Local axes: X right, Y forward, Z up; yaw Z, pitch X, roll Y."""
    y, p, r = map(math.radians, (yaw, pitch, roll))
    cy, sy, cp, sp, cr, sr = (
        math.cos(y),
        math.sin(y),
        math.cos(p),
        math.sin(p),
        math.cos(r),
        math.sin(r),
    )
    return multiply(
        multiply(
            [[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]],
            [[1, 0, 0], [0, cp, -sp], [0, sp, cp]],
        ),
        [[cr, 0, sr], [0, 1, 0], [-sr, 0, cr]],
    )


def build_layout(config):
    m = config["controller"]["mounting"]
    root = [m["left_wall"], m["front_wall"], m["height"]]
    # Device-local X points opposite to room X measured from the viewer's LEFT
    # wall.
    basis = multiply(
        [[-1, 0, 0], [0, 1, 0], [0, 0, 1]],
        rotation(m["yaw"], m["pitch"], m["roll"]),
    )
    # Align the NOLU product frame with the room drawing: 180 degrees
    # in the main sensor-facing view (room +Y points down on that view).
    # Apply once at the controller so every joint/branch follows the same
    # frame.
    # The controller position and all entered mounting/joint angles stay
    # unchanged.
    basis = multiply([[-1, 0, 0], [0, -1, 0], [0, 0, 1]], basis)
    resolved = {"controller": {"end": root, "matrix": basis, "path_mm": 0}}
    definitions = {bar["id"]: bar for bar in config["bars"]}

    return {
        "controller": root,
        "bars": [
            resolve_bar(
                bar["id"],
                definitions,
                resolved,
                config["product"]["length_mm"],
            )
            for bar in sorted(config["bars"], key=itemgetter("wire_order"))
        ],
    }


def program_pixel_positions(config, layout, count):
    """
    Use verified addresses, or estimate equal density along electrical order.
    """
    bars = layout["bars"]
    per_bar = config["product"]["pixels_per_bar"]
    exact = config["product"]["verified"] and per_bar * len(bars) == count
    positions = []
    for index in range(count):
        address = (index + 0.5) * len(bars) / count
        bar_index = min(len(bars) - 1, int(address))
        bar = bars[bar_index]
        fraction = address - bar_index
        if bar["reversed"]:
            fraction = 1 - fraction
        positions.append(
            [a + (b - a) * fraction for a, b in zip(bar["start"], bar["end"])]
        )
    return positions, not exact


def render_frame(config, tracks, timestamp):
    """
    Horizontal proximity avoids assuming uncalibrated radar Z is floor height.
    """
    layout = build_layout(config)
    animation, product = config["animation"], config["product"]
    # Unknown physical pixel count still permits a clearly marked software
    # preview.
    samples = product["pixels_per_bar"] or 12
    pixels = []
    for bar in layout["bars"]:
        colors = []
        for index in range(samples):
            u = (index + 0.5) / samples
            if bar["reversed"]:
                u = 1 - u
            x, y, _ = [
                bar["start"][axis]
                + (bar["end"][axis] - bar["start"][axis]) * u
                for axis in range(3)
            ]
            rgb = [
                animation["idle_pct"] / 100 * channel
                for channel in (0.2, 0.45, 0.55)
            ]
            for track in tracks:
                distance = math.hypot(
                    x - track["room_x_mm"], y - track["room_y_mm"]
                )
                envelope = math.exp(
                    -0.5 * (distance / animation["radius_mm"]) ** 2
                )
                phase = timestamp * animation["speed"] * 2 * math.pi
                wave = 0.78 + 0.22 * math.sin(phase - distance / 550)
                if animation["mode"] == "ripple":
                    wave = (0.5 + 0.5 * math.cos(phase - distance / 280)) ** 3
                hue = (
                    int.from_bytes(
                        hashlib.sha256(track["track_key"].encode()).digest()[
                            :2
                        ],
                        "big",
                    )
                    / 65535
                )
                color = colorsys.hsv_to_rgb((0.42 + hue * 0.22) % 1, 0.75, 1)
                for channel in range(3):
                    rgb[channel] += color[channel] * envelope * wave
            brightness = animation["brightness_pct"] / 100
            colors.append(
                [round(255 * brightness * min(1, value)) for value in rgb]
            )
        bar["pixels"] = colors
        bar["color"] = [
            round(sum(pixel[c] for pixel in colors) / len(colors))
            for c in range(3)
        ]
        bar.pop("matrix")
        pixels.extend(colors)
    return layout, pixels


def ddp_packets(pixels, sequence=0):
    data = bytes(channel for pixel in pixels for channel in pixel)
    for offset in range(0, len(data), 1440):
        chunk = data[offset : offset + 1440]
        flags = 0x40 | (1 if offset + len(chunk) == len(data) else 0)
        yield (
            struct.pack(
                "!BBBBIH", flags, sequence & 15, 0x0B, 1, offset, len(chunk)
            )
            + chunk
        )


def validate_lighting_number(obj, key, low, high, integer=False):
    value = obj[key]
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not low <= value <= high
    ):
        raise ValueError(f"Lighting {key} must be between {low} and {high}")
    if integer and not isinstance(value, int):
        raise ValueError(f"Lighting {key} must be an integer")


def resolve_bar(bar_id, definitions, resolved, length):
    if bar_id in resolved:
        return resolved[bar_id]
    bar = definitions[bar_id]
    parent = resolve_bar(bar["connected_to"], definitions, resolved, length)
    matrix = multiply(
        parent["matrix"], rotation(bar["yaw"], bar["pitch"], bar["roll"])
    )
    start = parent["end"]
    end = [start[i] + matrix[i][1] * length for i in range(3)]
    result = {
        "id": bar_id,
        "connected_to": bar["connected_to"],
        "wire_order": bar["wire_order"],
        "start": start,
        "end": end,
        "matrix": matrix,
        "path_mm": parent["path_mm"] + length,
        "reversed": bar["reversed"],
    }
    resolved[bar_id] = result
    return result
