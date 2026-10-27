#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Configuration validation and lossless schema migrations."""

import copy
import ipaddress
import math
import re

from led_presence.lights.controller_config import (
    validate_controllers,
    wrap_single_lighting,
)
from led_presence.lights.lighting import default_lighting


def validate_number(group, key, positive=False):
    """Validate finite room dimensions and mounting values."""
    value = group[key]
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{key} must be a finite number")
    if positive and not 0 < value <= 1_000_000:
        raise ValueError(f"{key} must be between 0 and 1000000 mm")
    if not positive and abs(value) > 1_000_000:
        raise ValueError(f"{key} is outside the supported range")


def migrate_config(config):
    """Upgrade legacy units and fields while preserving measurements."""
    result = copy.deepcopy(config)
    if result.get("schema_version") == 1:
        result["schema_version"] = 2
        result["room"] = {
            key.replace("_m", "_mm"): value * 1000
            for key, value in result["room"].items()
        }
        sensor = {
            key.replace("_m", "_mm") if key.endswith("_m") else key: value
            * 1000
            if key.endswith("_m")
            else value
            for key, value in result.pop("sensor").items()
        }
        sensor.update(
            id="sensor-1",
            name="Presence 1",
            device_host=result.pop("device_host"),
            enabled=True,
        )
        result["sensors"] = [sensor]
    if result.get("schema_version") == 2:
        for sensor in result["sensors"]:
            sensor["mounting"] = {
                "height": sensor.pop("mount_height_mm"),
                "left_wall": sensor.pop("x_mm"),
                "front_wall": sensor.pop("y_mm"),
                "yaw": sensor.pop("yaw_deg"),
                "pitch": sensor.pop("tilt_deg"),
                "roll": 0,
            }
        result["schema_version"] = 3
    if result.get("schema_version") == 3:
        result["lighting"] = default_lighting()
        result["schema_version"] = 4
    if result.get("schema_version") == 4:
        # Keep measured offsets; LEFT now means the reference wall in the
        # sensor-facing view.
        result["schema_version"] = 5
    if result.get("schema_version") == 5:
        result["lighting"] = wrap_single_lighting(result["lighting"])
        result["schema_version"] = 6
    return result


def validate_config(config):
    """Reject unsupported schemas and invalid installation parameters."""
    if config.get("schema_version") != 6:
        raise ValueError(
            "schema_version must be 6 (multiple light controllers; mm and "
            "degrees)"
        )
    if (
        not isinstance(config["installation_name"], str)
        or not config["installation_name"].strip()
    ):
        raise ValueError("Provide an installation name")
    if not isinstance(config["calibrated"], bool):
        raise ValueError("calibrated must be true or false")

    for key in ("width_mm", "depth_mm", "height_mm"):
        validate_number(config["room"], key, positive=True)
    if not isinstance(config["sensors"], list) or len(config["sensors"]) > 32:
        raise ValueError("sensors must be a list of up to 32 sensors")
    ids, hosts = set(), set()
    for sensor in config["sensors"]:
        if not isinstance(sensor["id"], str) or not re.fullmatch(
            r"[a-zA-Z0-9_-]{1,64}", sensor["id"]
        ):
            raise ValueError(
                "Sensor IDs must use 1-64 letters, digits, underscores or "
                "hyphens"
            )
        if sensor["id"] in ids:
            raise ValueError("Sensor IDs must be unique")
        ids.add(sensor["id"])
        if not isinstance(sensor["name"], str) or not sensor["name"].strip():
            raise ValueError("Provide a name for each sensor")
        host = str(ipaddress.IPv4Address(sensor["device_host"]))
        if host in hosts:
            raise ValueError("Each sensor must have a different IPv4 address")
        hosts.add(host)
        if not isinstance(sensor["enabled"], bool):
            raise ValueError("Sensor enabled must be true or false")
        for key in (
            "height",
            "left_wall",
            "front_wall",
            "yaw",
            "pitch",
            "roll",
        ):
            validate_number(sensor["mounting"], key, positive=key == "height")
    if not isinstance(config["physical"]["notes"], str):
        raise ValueError("physical.notes must be text")
    validate_controllers(config["lighting"])
    return config
