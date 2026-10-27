"""Independent light installations sharing one room and product catalogue."""
import copy
import re
import threading
import time

from lighting import LightingService, render_frame, validate_lighting


def wrap_single_lighting(config):
    if "controllers" in config:
        return copy.deepcopy(config)
    product = copy.deepcopy(config["product"])
    group = {key: copy.deepcopy(config[key]) for key in ("controller", "bars", "animation")}
    group.update(id="lights-1", name="Lights 1", enabled=True, product_id=product["id"])
    return {"products": [product], "controllers": [group]}


def controller_config(lighting, group):
    product = next((p for p in lighting["products"] if p["id"] == group["product_id"]), None)
    if product is None:
        raise ValueError(f'Unknown product for {group["id"]}')
    return {"controller": copy.deepcopy(group["controller"]), "product": copy.deepcopy(product),
            "bars": copy.deepcopy(group["bars"]), "animation": copy.deepcopy(group["animation"])}


def validate_controllers(config):
    if not isinstance(config["products"], list) or not 1 <= len(config["products"]) <= 16:
        raise ValueError("Configure between 1 and 16 light products")
    product_ids = [p["id"] for p in config["products"]]
    if len(set(product_ids)) != len(product_ids):
        raise ValueError("Light product IDs must be unique")
    if not isinstance(config["controllers"], list) or len(config["controllers"]) > 16:
        raise ValueError("Configure up to 16 light controllers")
    ids, addresses = set(), set()
    for group in config["controllers"]:
        if not isinstance(group["id"], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", group["id"]) or group["id"] in ids:
            raise ValueError("Light controller IDs must be unique identifiers")
        ids.add(group["id"])
        if not isinstance(group["name"], str) or not group["name"].strip():
            raise ValueError("Provide a name for every light controller")
        if not isinstance(group["enabled"], bool):
            raise ValueError("Light controller enabled must be true or false")
        validate_lighting(controller_config(config, group))
        key = (group["controller"]["device_host"], group["controller"]["component_id"])
        if key in addresses:
            raise ValueError("Each light controller must have a distinct address and component ID")
        addresses.add(key)
    # Validate unused catalogue entries too, using a minimal temporary installation.
    from lighting import default_lighting
    for product in config["products"]:
        sample = default_lighting()
        sample["product"] = product
        for bar in sample["bars"]:
            bar["product_id"] = product["id"]
        validate_lighting(sample)


class ControllerInstallation:
    def __init__(self, installation, controller_id):
        self.installation = installation
        self.controller_id = controller_id

    def snapshot(self, include_lighting=False):
        state = self.installation.snapshot(include_lighting=False)
        lighting = state["config"]["lighting"]
        group = next(g for g in lighting["controllers"] if g["id"] == self.controller_id)
        return {"config": {"lighting": controller_config(lighting, group)}, "tracks": state["tracks"]}


class LightControllers:
    def __init__(self, installation, start_workers):
        self.installation = installation
        self.start_workers = start_workers
        self.lock = threading.RLock()
        self.services = {}
        self.reconcile(installation.config["lighting"])

    def stop_changed(self, before, after):
        with self.lock:
            groups = {g["id"]: g for g in after["controllers"]}
            for group in before["controllers"]:
                new = groups.get(group["id"])
                changed = new is None or not new["enabled"] or controller_config(before, group) != controller_config(after, new)
                if changed and group["id"] in self.services:
                    self.services[group["id"]].stop_output()

    def reconcile(self, config):
        with self.lock:
            active = {g["id"]: g for g in config["controllers"] if g["enabled"]}
            for key in list(self.services):
                if key not in active:
                    self.services.pop(key).close()
            for key in active:
                if key not in self.services:
                    self.services[key] = LightingService(ControllerInstallation(self.installation, key), self.start_workers)

    def preview(self, config, tracks):
        with self.lock:
            groups = []
            bars = []
            for group in config["controllers"]:
                normalized = controller_config(config, group)
                service = self.services.get(group["id"])
                if service:
                    frame = service.preview(normalized, tracks)
                else:
                    layout, _ = render_frame(normalized, [], time.monotonic())
                    frame = {**layout, "output_enabled": False, "controller_connected": False,
                             "controller_pixels": None, "error": None, "frames_sent": 0, "last_sent_at": None,
                             "product_verified": normalized["product"]["verified"],
                             "mapped_pixels": len(group["bars"]) * normalized["product"]["pixels_per_bar"]}
                frame.update(id=group["id"], name=group["name"], enabled=group["enabled"])
                groups.append(frame)
                for bar in frame["bars"]:
                    bars.append({**bar, "controller_id": group["id"], "controller_name": group["name"],
                                 "key": f'{group["id"]}:{bar["id"]}'})
            return {"controllers": groups, "bars": bars, "output_enabled": any(g["output_enabled"] for g in groups)}

    def set_output(self, enabled, config, controller_id=None):
        groups = config["controllers"]
        if controller_id is None and len(groups) == 1:
            controller_id = groups[0]["id"]
        group = next((g for g in groups if g["id"] == controller_id), None)
        if group is None:
            raise ValueError("Select a valid light controller")
        with self.lock:
            service = self.services.get(controller_id)
            if not service:
                if enabled:
                    raise ValueError("Enable this controller on the Devices page first")
                return
            service.set_output(enabled, controller_config(config, group))

    def close(self):
        with self.lock:
            for service in self.services.values():
                service.close()
