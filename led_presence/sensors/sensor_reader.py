#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Independent Shelly Presence connection and tracking worker."""

import copy
import json
import threading
import time

import requests
import websocket

from led_presence.core.geometry import place_track


class SensorReader:
    """One independent connection and reconnect loop per physical sensor."""

    def __init__(self, sensor):
        self.sensor_id = sensor["id"]
        self.host = sensor["device_host"]
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.ws = None
        self.state = {
            "connected": False,
            "error": None,
            "tracks": [],
            "last_event_at": None,
            "device": {},
            "status": {},
            "mounting": {},
            "zones": [],
        }

    def start(self):
        threading.Thread(
            target=self.run, daemon=True, name=f"shelly-{self.sensor_id}"
        ).start()

    def stop(self):
        self.stop_event.set()
        if self.ws:
            self.ws.close()

    def snapshot(self, sensor):
        with self.lock:
            result = copy.deepcopy(self.state)
        result.update(
            id=sensor["id"],
            name=sensor["name"],
            enabled=sensor["enabled"],
            device_host=self.host,
        )
        result["stale"] = not result["last_event_at"] or (
            bool(result["tracks"])
            and time.time() - result["last_event_at"] > 5
        )
        result["tracks"] = [
            place_track(track, sensor) for track in result["tracks"]
        ]
        return result

    def handle_message(self, message):
        with self.lock:
            for event in message.get("params", {}).get("events", []):
                if event.get("component") == "presence" and event.get(
                    "event"
                ) in ("track", "no_track"):
                    self.state["tracks"] = (
                        event.get("object", [])
                        if event["event"] == "track"
                        else []
                    )
                    self.state["last_event_at"] = time.time()

    def run(self):
        session = requests.Session()
        session.trust_env = False
        try:
            while not self.stop_event.is_set():
                try:
                    device = self.rpc(session, "Shelly.GetDeviceInfo")
                    sensor_config = self.rpc(
                        session, "Presence.GetConfig"
                    ).get("sensor", {})
                    mounting = {
                        "height_mm": sensor_config.get("height", 0) * 1000,
                        "tilt_deg": sensor_config.get("tilt"),
                        "flipped": sensor_config.get("flipped"),
                    }
                    if self.stop_event.is_set():
                        break
                    self.ws = websocket.create_connection(
                        f"ws://{self.host}/rpc",
                        timeout=2,
                        http_no_proxy=[self.host],
                    )
                    with self.lock:
                        self.state.update(
                            connected=True,
                            error=None,
                            device=device,
                            mounting=mounting,
                            tracks=[],
                            last_event_at=None,
                        )
                    refresh_at = renew_at = 0
                    while not self.stop_event.is_set():
                        now = time.monotonic()
                        if now >= renew_at:
                            self.ws.send(
                                json.dumps(
                                    {
                                        "id": 1,
                                        "src": f"led_{self.sensor_id}",
                                        "method": "Presence.LiveTrack",
                                    }
                                )
                            )
                            renew_at = now + 25
                        if now >= refresh_at:
                            raw_status = self.rpc(session, "Shelly.GetStatus")
                            # Only publish understood fields; distances exposed
                            # by this app are always mm.
                            status = {
                                key: raw_status[key]
                                for key in ("wifi", "illuminance:0", "sys")
                                if key in raw_status
                            }
                            components = self.rpc(
                                session,
                                "Shelly.GetComponents?dynamic_only=true",
                            ).get("components", [])
                            zones = [
                                {
                                    "key": zone["key"],
                                    "name": zone.get("config", {}).get("name"),
                                    "occupied": zone["status"].get("value"),
                                    "num_objects": zone["status"].get(
                                        "num_objects"
                                    ),
                                }
                                for zone in components
                                if zone["key"].startswith("presencezone:")
                            ]
                            with self.lock:
                                self.state.update(status=status, zones=zones)

                            refresh_at = now + 5
                        try:
                            payload = self.ws.recv()
                            if not payload:
                                raise ConnectionError(
                                    "Sensor closed the connection"
                                )
                            message = json.loads(payload)
                            if "error" in message:
                                raise ValueError(str(message["error"]))
                            self.handle_message(message)
                        except websocket.WebSocketTimeoutException:
                            continue
                except Exception as exc:
                    with self.lock:
                        self.state.update(
                            connected=False,
                            error=str(exc),
                            tracks=[],
                            last_event_at=None,
                        )
                finally:
                    with self.lock:
                        self.state["connected"] = False
                    if self.ws:
                        self.ws.close()
                        self.ws = None
                self.stop_event.wait(3)
        finally:
            session.close()

    def rpc(self, session, method):
        response = session.get(f"http://{self.host}/rpc/{method}", timeout=4)
        response.raise_for_status()
        result = response.json()
        if (
            isinstance(result, dict)
            and "code" in result
            and "message" in result
        ):
            raise ValueError(result["message"])
        return result
