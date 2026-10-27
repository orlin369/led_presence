#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Persistent room configuration and device lifecycle."""

import copy
import json
import threading

from led_presence.config.configuration import migrate_config, validate_config
from led_presence.core.paths import ROOT
from led_presence.lights.light_controllers import LightControllers
from led_presence.sensors.sensor_reader import SensorReader


class Installation:
    """Own the persisted room settings and all device workers."""

    def __init__(self, path, start_workers=True):
        self.path = path
        self.lock = threading.RLock()
        self.start_workers = start_workers
        self.readers = {}
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(
                (ROOT / "config.example.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        original = json.loads(path.read_text(encoding="utf-8"))
        self.config = validate_config(migrate_config(original))
        if original.get("schema_version") in (1, 2, 3, 4, 5):
            # Preserve the exact old configuration once, before persisting the
            # migration.
            backup = path.with_name(
                f"config.v{original['schema_version']}.backup.json"
            )
            if not backup.exists():
                backup.write_bytes(path.read_bytes())
            self.write_config(self.config)
        self.reconcile()
        self.lighting = LightControllers(self, start_workers)

    def write_config(self, config):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(config, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def reconcile(self):
        active = {
            sensor["id"]: sensor
            for sensor in self.config["sensors"]
            if sensor["enabled"]
        }
        for key, reader in list(self.readers.items()):
            if key not in active or active[key]["device_host"] != reader.host:
                reader.stop()
                del self.readers[key]
        for key, sensor in active.items():
            if key not in self.readers:
                reader = self.readers[key] = SensorReader(sensor)
                if self.start_workers:
                    reader.start()

    def save(self, config):
        validate_config(config)
        with self.lock:
            source_id = self.lighting.program_sensor_id
            if source_id is not None:
                old_source = next(
                    (
                        s
                        for s in self.config["sensors"]
                        if s["id"] == source_id
                    ),
                    None,
                )
                new_source = next(
                    (s for s in config["sensors"] if s["id"] == source_id),
                    None,
                )
                if (
                    self.config["room"] != config["room"]
                    or old_source != new_source
                    or not config["sensors"]
                    or config["sensors"][0]["id"] != source_id
                ):
                    self.lighting.stop_program()
            self.lighting.stop_changed(
                self.config["lighting"], config["lighting"]
            )
            self.write_config(config)
            self.config = copy.deepcopy(config)
            self.reconcile()
            self.lighting.reconcile(self.config["lighting"])

    def close(self):
        self.lighting.close()
        with self.lock:
            for reader in self.readers.values():
                reader.stop()

    def snapshot(self, include_lighting=True):
        with self.lock:
            config = copy.deepcopy(self.config)
            sensors = []
            for sensor in config["sensors"]:
                reader = self.readers.get(sensor["id"])
                state = (reader or SensorReader(sensor)).snapshot(sensor)
                sensors.append(state)
        live_tracks = [
            track
            for sensor in sensors
            if sensor["connected"] and not sensor["stale"]
            for track in sensor["tracks"]
        ]
        result = {
            "config": config,
            "units": "mm",
            "sensors": sensors,
            "tracks": live_tracks,
        }
        if include_lighting:
            result["lighting"] = self.lighting.preview(
                config["lighting"], live_tracks
            )
        return result
