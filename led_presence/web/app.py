#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Flask application factory and HTTP handlers."""

import atexit
import os
from pathlib import Path

import requests
from flask import (
    Flask,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from led_presence.core.installation import Installation
from led_presence.core.paths import ROOT


def index():
    return render_template("index.html", geometry_page=False)


def three_d():
    """Display the live installation in an interactive 3D room view."""
    return render_template("three-d.html", three_d_page=True)


def geometry():
    return render_template("geometry.html", geometry_page=True)


def devices():
    return render_template("devices.html", devices_page=True)


def lights():
    return redirect(url_for("devices", _anchor="lights"))


def nolu():
    return redirect(url_for("devices", _anchor="lights"))


def state():
    installation = current_app.extensions["installation"]
    return jsonify(installation.snapshot())


def lighting_state():
    installation = current_app.extensions["installation"]
    state = installation.snapshot(include_lighting=False)
    return jsonify(
        installation.lighting.preview(
            state["config"]["lighting"], state["tracks"]
        )
    )


def lighting_output():
    installation = current_app.extensions["installation"]
    if request.headers.get("Origin") and request.headers[
        "Origin"
    ] != request.host_url.rstrip("/"):
        return jsonify(error="Cross-origin writes are not allowed"), 403
    if not request.is_json:
        return jsonify(error="Send application/json"), 415
    data = request.get_json()
    if not isinstance(data, dict) or not isinstance(data.get("enabled"), bool):
        return jsonify(error="enabled must be true or false"), 400
    try:
        with installation.lock:
            installation.lighting.set_output(
                data["enabled"],
                installation.config["lighting"],
                data.get("controller_id"),
            )
    except (ValueError, requests.RequestException) as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(installation.snapshot()["lighting"])


def animation_program():
    installation = current_app.extensions["installation"]
    if request.headers.get("Origin") and request.headers[
        "Origin"
    ] != request.host_url.rstrip("/"):
        return jsonify(error="Cross-origin writes are not allowed"), 403
    if not request.is_json:
        return jsonify(error="Send application/json"), 415
    data = request.get_json()
    if not isinstance(data, dict) or not isinstance(data.get("enabled"), bool):
        return jsonify(error="enabled must be true or false"), 400
    try:
        with installation.lock:
            if data["enabled"]:
                installation.lighting.start_program(
                    installation.config,
                    data.get("animation_id"),
                )
            else:
                installation.lighting.stop_program()
    except (ValueError, requests.RequestException) as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(installation.lighting.program_state())


def controller_brightness():
    """Change one controller's brightness without changing its power state."""
    if request.headers.get("Origin") and request.headers[
        "Origin"
    ] != request.host_url.rstrip("/"):
        return jsonify(error="Cross-origin writes are not allowed"), 403
    if not request.is_json:
        return jsonify(error="Send application/json"), 415
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify(error="Provide brightness_pct from 0 to 100"), 400
    installation = current_app.extensions["installation"]
    try:
        installation.lighting.set_controller_brightness(
            data.get("controller_id"), data.get("brightness_pct")
        )
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(
        controller_id=data["controller_id"],
        brightness_pct=installation.lighting.brightness_by_controller[
            data["controller_id"]
        ],
    )


def config():
    installation = current_app.extensions["installation"]
    if request.method == "GET":
        return jsonify(installation.snapshot()["config"])
    if request.headers.get("Origin") and request.headers[
        "Origin"
    ] != request.host_url.rstrip("/"):
        return jsonify(error="Cross-origin writes are not allowed"), 403
    if not request.is_json:
        return jsonify(error="Send application/json"), 415
    try:
        installation.save(request.get_json())
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(installation.snapshot()["config"])


def create_app(instance_path=None, start_worker=True):
    """Build the Flask app with isolated settings and optional workers."""
    path = Path(
        instance_path
        or os.environ.get("LED_PRESENCE_INSTANCE", ROOT / "instance")
    ).resolve()
    app = Flask(__name__, instance_path=str(path))
    installation = Installation(
        path / "config.json", start_workers=start_worker
    )
    app.extensions["installation"] = installation
    atexit.register(installation.close)

    app.add_url_rule("/", view_func=index, methods=["GET"])
    app.add_url_rule("/3d", view_func=three_d, methods=["GET"])
    app.add_url_rule("/geometry", view_func=geometry, methods=["GET"])
    app.add_url_rule("/devices", view_func=devices, methods=["GET"])
    app.add_url_rule("/lights", view_func=lights, methods=["GET"])
    app.add_url_rule("/nolu", view_func=nolu, methods=["GET"])
    app.add_url_rule("/api/state", view_func=state, methods=["GET"])
    app.add_url_rule(
        "/api/lighting", view_func=lighting_state, methods=["GET"]
    )
    app.add_url_rule(
        "/api/lighting/output", view_func=lighting_output, methods=["POST"]
    )
    app.add_url_rule(
        "/api/animation", view_func=animation_program, methods=["POST"]
    )
    app.add_url_rule("/api/config", view_func=config, methods=["GET", "PUT"])
    app.add_url_rule(
        "/api/lighting/brightness",
        view_func=controller_brightness,
        methods=["POST"],
    )
    return app
