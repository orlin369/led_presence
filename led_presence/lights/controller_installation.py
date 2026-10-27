#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-controller view of a shared room installation."""

from led_presence.lights.controller_config import controller_config


class ControllerInstallation:
    """Expose one controller's settings with shared room tracks."""

    def __init__(self, installation, controller_id):
        self.installation = installation
        self.controller_id = controller_id

    def snapshot(self, include_lighting=False):
        state = self.installation.snapshot(include_lighting=False)
        lighting = state["config"]["lighting"]
        group = next(
            g for g in lighting["controllers"] if g["id"] == self.controller_id
        )
        return {
            "config": {"lighting": controller_config(lighting, group)},
            "tracks": state["tracks"],
        }

    def sync_controller_brightness(self, brightness_pct, generation):
        """Forward an observed hardware dimmer change to the coordinator."""
        manager = getattr(self.installation, "lighting", None)
        if manager is not None:
            manager.observe_controller_brightness(
                self.controller_id, brightness_pct, generation
            )
