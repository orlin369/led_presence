#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test independent controllers, shared products and registry migrations."""

import copy
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from led_presence.core.installation import Installation
from led_presence.lights.controller_config import controller_config
from led_presence.web.app import create_app


class ControllerTests(unittest.TestCase):
    """Verify multi-controller behaviour with mocks and loopback UDP."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(self.directory.name, start_worker=False)
        self.installation = self.app.extensions["installation"]
        self.client = self.app.test_client()
        self.config = copy.deepcopy(self.installation.config)

    def tearDown(self):
        self.installation.close()
        self.directory.cleanup()

    def two_controllers(self):
        second = copy.deepcopy(self.config["lighting"]["controllers"][0])
        second.update(id="lights-2", name="Lights 2")
        second["controller"]["device_host"] = "192.0.2.2"
        second["controller"]["mounting"]["left_wall"] = 2300
        self.config["lighting"]["controllers"].append(second)
        self.installation.save(self.config)

    def test_independent_locations_namespaced_bars_and_product_reference(self):
        self.two_controllers()
        state = self.installation.snapshot()["lighting"]
        self.assertEqual(len(state["controllers"]), 2)
        self.assertEqual(len(state["bars"]), 18)
        self.assertEqual(len({bar["key"] for bar in state["bars"]}), 18)
        self.assertEqual(state["controllers"][1]["controller"][0], 2300)
        self.assertEqual(len(self.config["lighting"]["products"]), 1)
        self.assertEqual(
            {g["product_id"] for g in self.config["lighting"]["controllers"]},
            {self.config["lighting"]["products"][0]["id"]},
        )

    def test_change_stops_only_affected_controller_and_disable_closes_reader(
        self,
    ):
        self.two_controllers()
        first, second = [
            self.installation.lighting.services[key]
            for key in ("lights-1", "lights-2")
        ]
        first.stop_output = Mock(wraps=first.stop_output)
        second.stop_output = Mock(wraps=second.stop_output)
        self.config["lighting"]["controllers"][0]["controller"]["mounting"][
            "left_wall"
        ] += 100
        self.installation.save(self.config)
        first.stop_output.assert_called_once()
        second.stop_output.assert_not_called()
        self.config["lighting"]["controllers"][0]["enabled"] = False
        self.installation.save(self.config)
        self.assertTrue(first.stop_event.is_set())
        self.assertNotIn("lights-1", self.installation.lighting.services)
        self.assertIs(self.installation.lighting.services["lights-2"], second)

    def test_shared_product_change_stops_all_controllers_using_it(self):
        self.two_controllers()
        services = list(self.installation.lighting.services.values())
        for service in services:
            service.stop_output = Mock(wraps=service.stop_output)
        self.config["lighting"]["products"][0]["length_mm"] = 400
        self.installation.save(self.config)
        for service in services:
            service.stop_output.assert_called_once()

    def test_invalid_registry_and_explicit_output_selection(self):
        self.two_controllers()
        self.assertEqual(
            self.client.post(
                "/api/lighting/output", json={"enabled": True}
            ).status_code,
            400,
        )
        for change in ("id", "address", "product"):
            config = copy.deepcopy(self.config)
            first, second = config["lighting"]["controllers"]
            if change == "id":
                second["id"] = first["id"]
            elif change == "address":
                second["controller"]["device_host"] = first["controller"][
                    "device_host"
                ]
            else:
                second["product_id"] = "missing"
            self.assertEqual(
                self.client.put("/api/config", json=config).status_code, 400
            )

    def test_empty_registry_and_disabled_output(self):
        self.two_controllers()
        self.config["lighting"]["controllers"][1]["enabled"] = False
        self.installation.save(self.config)
        self.assertEqual(
            self.client.post(
                "/api/lighting/output",
                json={"enabled": True, "controller_id": "lights-2"},
            ).status_code,
            400,
        )
        self.config["lighting"]["controllers"] = []
        self.installation.save(self.config)
        self.assertEqual(
            self.client.get("/api/lighting").get_json()["controllers"], []
        )
        self.assertEqual(self.installation.lighting.services, {})

    def test_two_udp_streams_and_stopping_one_keeps_other_active(self):
        self.two_controllers()
        receivers = []
        for index, group in enumerate(self.config["lighting"]["controllers"]):
            receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(3)
            self.addCleanup(receiver.close)
            receivers.append(receiver)
            group["controller"].update(
                device_host="127.0.0.1",
                component_id=index,
                ddp_port=receiver.getsockname()[1],
            )
            group["animation"]["idle_pct"] = 10
        self.config["lighting"]["products"][0].update(
            verified=True, pixels_per_bar=12
        )
        self.installation.save(self.config)
        workers = []
        for group in self.config["lighting"]["controllers"]:
            service = self.installation.lighting.services[group["id"]]
            service.rpc = Mock(side_effect=self.two_streams_rpc)
            response = self.client.post(
                "/api/lighting/output",
                json={"enabled": True, "controller_id": group["id"]},
            )
            self.assertEqual(response.status_code, 200)
            worker = threading.Thread(target=service.run, daemon=True)
            worker.start()
            workers.append(worker)
        try:
            for receiver in receivers:
                packet, _ = receiver.recvfrom(2000)
                self.assertEqual(len(packet), 334)
                self.assertEqual(packet[0], 0x41)
            response = self.client.post(
                "/api/lighting/output",
                json={"enabled": False, "controller_id": "lights-1"},
            )
            self.assertEqual(response.status_code, 200)
            state = response.get_json()["controllers"]
            self.assertFalse(state[0]["output_enabled"])
            self.assertTrue(state[1]["output_enabled"])
            self.assertEqual(len(receivers[1].recvfrom(2000)[0]), 334)
        finally:
            self.installation.lighting.close()
            for worker in workers:
                worker.join(timeout=3)

    def test_v5_migration_preserves_original_controller_and_product(self):
        group = self.config["lighting"]["controllers"][0]
        old = copy.deepcopy(self.config)
        old["schema_version"] = 5
        old["lighting"] = controller_config(self.config["lighting"], group)
        old["lighting"]["controller"]["mounting"].update(
            left_wall=830, front_wall=1200, height=0
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "config.json")
            path.write_text(json.dumps(old), encoding="utf-8")
            installation = Installation(path, start_workers=False)
            try:
                new = installation.config
                self.assertEqual(new["schema_version"], 6)
                self.assertEqual(
                    controller_config(
                        new["lighting"], new["lighting"]["controllers"][0]
                    ),
                    old["lighting"],
                )
                self.assertEqual(new["sensors"], old["sensors"])
                self.assertTrue(
                    Path(directory, "config.v5.backup.json").exists()
                )
            finally:
                installation.close()

    def two_streams_rpc(self, controller, method, **params):
        if method == "LedStrip.GetConfig":
            return {
                "num_leds": 108,
                "ddp_port": controller["ddp_port"],
                "effects": ["ddp", "fade"],
            }
        if method == "LedStrip.GetStatus":
            return {"on": True, "brightness": 50, "effect": "fade"}
        return None


def main():
    """Run this test module directly."""
    unittest.main()


if __name__ == "__main__":
    main()
