#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Light controller registry normalization and validation."""

import copy
import re

from led_presence.lights.lighting import default_lighting, validate_lighting


def wrap_single_lighting(config):
    """Migrate a single light configuration into the controller registry."""
    if "controllers" in config:
        return copy.deepcopy(config)
    product = copy.deepcopy(config["product"])
    group = {
        key: copy.deepcopy(config[key])
        for key in ("controller", "bars", "animation")
    }
    group.update(
        id="lights-1", name="Lights 1", enabled=True, product_id=product["id"]
    )
    return {"products": [product], "controllers": [group]}


def controller_config(lighting, group):
    """Combine one controller with its shared product definition."""
    product = next(
        (p for p in lighting["products"] if p["id"] == group["product_id"]),
        None,
    )
    if product is None:
        raise ValueError(f"Unknown product for {group['id']}")
    return {
        "controller": copy.deepcopy(group["controller"]),
        "product": copy.deepcopy(product),
        "bars": copy.deepcopy(group["bars"]),
        "animation": copy.deepcopy(group["animation"]),
    }


def validate_controllers(config):
    """Validate the controller registry, product references and addresses."""
    if (
        not isinstance(config["products"], list)
        or not 1 <= len(config["products"]) <= 16
    ):
        raise ValueError("Configure between 1 and 16 light products")
    product_ids = [p["id"] for p in config["products"]]
    if len(set(product_ids)) != len(product_ids):
        raise ValueError("Light product IDs must be unique")
    if (
        not isinstance(config["controllers"], list)
        or len(config["controllers"]) > 16
    ):
        raise ValueError("Configure up to 16 light controllers")
    ids, addresses = set(), set()
    for group in config["controllers"]:
        if (
            not isinstance(group["id"], str)
            or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", group["id"])
            or group["id"] in ids
        ):
            raise ValueError("Light controller IDs must be unique identifiers")
        ids.add(group["id"])
        if not isinstance(group["name"], str) or not group["name"].strip():
            raise ValueError("Provide a name for every light controller")
        if not isinstance(group["enabled"], bool):
            raise ValueError("Light controller enabled must be true or false")
        validate_lighting(controller_config(config, group))
        key = (
            group["controller"]["device_host"],
            group["controller"]["component_id"],
        )
        if key in addresses:
            raise ValueError(
                "Each light controller must have a distinct address and "
                "component ID"
            )
        addresses.add(key)
    # Validate unused catalogue entries too, using a minimal temporary
    # installation.
    for product in config["products"]:
        sample = default_lighting()
        sample["product"] = product
        for bar in sample["bars"]:
            bar["product_id"] = product["id"]
        validate_lighting(sample)
