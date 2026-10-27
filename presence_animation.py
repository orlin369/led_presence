"""Editable animation program for the dashboard Start/Stop button.

Stop the Flask app before editing this file, then restart it. No hot reload is
performed. Edit render(context) or the constants below; networking, smoothing
and restoring the previous light effect are handled by the app.

context contains:
  time_s: elapsed seconds since Start
  pixel_count: actual configured LED count reported by this controller
  tracks: fresh room-coordinate tracks from the selected sensor only (mm)
  sensor: selected sensor definition, including mounting (mm / degrees)
  room: room dimensions (mm)
  controller: this light controller's settings and mounting
  pixel_positions: room-coordinate [x, y, z] positions in electrical order (mm)
  state: persistent dictionary for this controller, cleared on each Start

Return exactly pixel_count RGB triples, with integer channels from 0 to 255.
Each person projects a bright core and coloured halo onto the nearest lights.
Movement leaves a fading wake; distance changes colour and halo width.
"""
import colorsys
import math

NAME = "Presence ribbons"
DEFAULT_ANIMATION = "ribbons"
ANIMATIONS = {
    "spotlight": {"name": "Spotlight", "description": "A small, fixed-width pool of light follows each person."},
    "ribbons": {"name": "Presence ribbons", "description": "Coloured halos follow people and leave a fading wake."},
    "ripples": {"name": "Ripples", "description": "Waves spread out from each person's position on the lights."},
    "fireflies": {"name": "Fireflies", "description": "A cluster of twinkling sparks follows each person."},
}
MAX_BRIGHTNESS_PCT = 25
TRAIL_SECONDS = 0.7
HALO_WIDTH_MM = 250


def render(context):
    count = context["pixel_count"]
    positions = context.get("pixel_positions") or [
        [(i + .5) / count * context["room"]["width_mm"], 0, 0] for i in range(count)]
    now = context["time_s"]
    animation = context.get("animation_id", DEFAULT_ANIMATION)
    if animation not in ANIMATIONS:
        raise ValueError("Unknown animation")
    state = context.setdefault("state", {})
    lifetime = TRAIL_SECONDS if animation == "ribbons" else .12
    decay = math.exp(-max(0, now - state.get("time", now)) / lifetime)
    previous = state.get("trail", [[0., 0., 0.] for _ in range(count)])
    pixels = [[channel * decay for channel in pixel] for pixel in previous]
    mounting = context["sensor"]["mounting"]
    for track in context["tracks"]:
        x, y = track["room_x_mm"], track["room_y_mm"]
        # Project onto the physical light path rather than requiring people
        # to stand on the bars. Height is intentionally ignored for radar tracks.
        anchor = min(positions, key=lambda p: (p[0] - x)**2 + (p[1] - y)**2)
        depth = math.hypot(x - mounting["left_wall"], y - mounting["front_wall"])
        proximity = math.exp(-depth / 4000)
        hue = .62 - .52 * proximity
        colour = colorsys.hsv_to_rgb(hue, .9, 1)
        width = HALO_WIDTH_MM * (.65 + .7 * proximity)
        for i, position in enumerate(positions):
            distance = math.hypot(position[0] - anchor[0], position[1] - anchor[1])
            core = math.exp(-.5 * (distance / 65)**2)
            halo = math.exp(-.5 * (distance / width)**2)
            wave = .75 + .25 * math.cos(distance / 110 - now * 3)
            if animation == "spotlight":
                core = math.exp(-.5 * (distance / 85)**2)
                halo = core * .25
                wave = 1
            elif animation == "ripples":
                phase = (now * 240) % 650
                ring = math.exp(-.5 * ((distance - phase) / 55)**2)
                core = math.exp(-.5 * (distance / 60)**2) * .35
                halo = ring * math.exp(-distance / 900)
                wave = 1
            elif animation == "fireflies":
                # Stable per-address phases avoid random flicker on every poll.
                spark = (.5 + .5 * math.sin(now * 3 + i * 2.399))**14
                core = 0
                halo = math.exp(-.5 * (distance / 300)**2) * spark
                wave = 1.5
            for c in range(3):
                emission = min(1, core * .8 + colour[c] * halo * wave * .65)
                pixels[i][c] = max(pixels[i][c], emission)
    state.update(time=now, trail=pixels)
    scale = 255 * MAX_BRIGHTNESS_PCT / 100
    return [tuple(round(min(1, c) * scale) for c in pixel) for pixel in pixels]
