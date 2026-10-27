"""Convert device metres at the boundary; all application geometry uses mm."""
import math


def place_track(track, sensor):
    mounting = sensor["mounting"]
    angle = math.radians(mounting["yaw"])
    reported = {f"{key}_mm": float(track[key]) * 1000
                for key in ("x", "y", "z", "minz", "maxz") if key in track}
    x, y = reported["x_mm"], reported["y_mm"]
    return {
        "id": track["id"],
        "sensor_id": sensor["id"],
        "sensor_name": sensor["name"],
        "track_key": f'{sensor["id"]}:{track["id"]}',
        "room_x_mm": mounting["left_wall"] + x * math.cos(angle) - y * math.sin(angle),
        "room_y_mm": mounting["front_wall"] + x * math.sin(angle) + y * math.cos(angle),
        "reported_mm": reported,
    }
