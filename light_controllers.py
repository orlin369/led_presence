"""Independent light installations sharing one room and product catalogue."""
import copy
import re
import threading
import time
import presence_animation

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
        self.program_sensor_id = None
        self.program_animation_id = presence_animation.DEFAULT_ANIMATION
        self.program_ids = []
        self.program_started_at = None
        self.program_error = None
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
            return {"controllers": groups, "bars": bars, "output_enabled": any(g["output_enabled"] for g in groups),
                    "program": self.program_state()}

    def program_state(self):
        with self.lock:
            active, errors, pending = [], [], []
            for key in self.program_ids:
                service = self.services.get(key)
                if not service:
                    continue
                with service.lock:
                    if service.state["program_running"]:
                        active.append(key)
                    message = service.state.get("program_error") or service.state["error"]
                    if message:
                        errors.append(f'{key}: {message}')
                    if not service.state["program_running"] and service.previous is not None:
                        pending.append(key)
            return {"running": bool(active), "sensor_id": self.program_sensor_id,
                    "animation_id": self.program_animation_id,
                    "animations": [{"id": key, **value} for key, value in presence_animation.ANIMATIONS.items()],
                    "controller_ids": active, "restoration_pending": pending,
                    "error": self.program_error or ("; ".join(errors) if errors else None)}

    def start_program(self, sensor_id, config, animation_id=None):
        animation_id = presence_animation.DEFAULT_ANIMATION if animation_id is None else animation_id
        if not isinstance(animation_id, str) or animation_id not in presence_animation.ANIMATIONS:
            raise ValueError("Select a supported animation")
        sensor = next((s for s in config["sensors"] if s["id"] == sensor_id and s["enabled"]), None)
        if sensor is None:
            raise ValueError("Select an enabled presence sensor")
        source = next(s for s in self.installation.snapshot(include_lighting=False)["sensors"] if s["id"] == sensor_id)
        if not source["connected"]:
            raise ValueError("Selected presence sensor is offline")
        groups = [g for g in config["lighting"]["controllers"] if g["enabled"]]
        if not groups:
            raise ValueError("Enable at least one light controller on Devices")
        with self.lock:
            if self.program_state()["running"] and self.program_sensor_id == sensor_id:
                if animation_id != self.program_animation_id:
                    switched_at = time.monotonic()
                    for key in self.program_ids:
                        service = self.services.get(key)
                        if service:
                            with service.lock:
                                if service.program is not None:
                                    service.program.update(animation_id=animation_id, state={}, started_at=switched_at)
                                    service.generation += 1
                    self.program_animation_id = animation_id
                return
            # Preflight every destination before changing any physical light.
            for group in groups:
                try:
                    self.services[group["id"]].prepare_program(controller_config(config["lighting"], group))
                except Exception as exc:
                    raise ValueError(f'{group["name"]}: {exc}') from exc
            self.stop_program()
            self.program_sensor_id = sensor_id
            self.program_animation_id = animation_id
            self.program_started_at = time.monotonic()
            self.program_error = None
            context = {"sensor": copy.deepcopy(sensor), "room": copy.deepcopy(config["room"]),
                       "started_at": self.program_started_at, "animation_id": animation_id}
            try:
                for group in groups:
                    self.program_ids.append(group["id"])
                    self.services[group["id"]].set_output(True, controller_config(config["lighting"], group),
                                                          program=context)
            except Exception as exc:
                message = str(exc)
                try:
                    self.stop_program()
                except ValueError as restoration:
                    message += f"; {restoration}"
                self.program_error = message
                raise ValueError(message) from exc

    def stop_program(self):
        with self.lock:
            failed = []
            for key in self.program_ids:
                service = self.services.get(key)
                if service:
                    try:
                        service.stop_output()
                    except ValueError as exc:
                        failed.append((key, str(exc)))
            self.program_ids = [key for key, _ in failed]
            self.program_error = "; ".join(error for _, error in failed) or None
            if failed:
                raise ValueError(self.program_error)
            self.program_sensor_id = None
            self.program_started_at = None

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
