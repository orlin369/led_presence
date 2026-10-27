#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stable per-person colours shared by the dashboard and the lights."""

import threading
import time

# Saturated colours stay distinguishable on LEDs, where pastel shades wash
# out towards white. Order alternates hues so the first few people differ
# the most.
PALETTE = (
    (255, 40, 40),
    (40, 255, 70),
    (40, 100, 255),
    (255, 200, 0),
    (255, 40, 220),
    (0, 230, 255),
    (255, 110, 0),
    (150, 60, 255),
)
# A brief radar dropout should not repaint a person who reappears with the
# same track ID.
HOLD_SECONDS = 3


class TrackColours:
    """Give each live track a unique colour while it remains present."""

    def __init__(self, palette=PALETTE, hold_seconds=HOLD_SECONDS):
        self.palette = palette
        self.hold_seconds = hold_seconds
        self.lock = threading.Lock()
        # track_key -> [palette index, last seen monotonic seconds]
        self.assigned = {}

    def assign(self, tracks, now=None):
        """Add an RGB `color` to each track, reusing earlier assignments."""
        now = time.monotonic() if now is None else now
        live = {track["track_key"] for track in tracks}
        with self.lock:
            for key, (_, seen) in list(self.assigned.items()):
                if key not in live and now - seen > self.hold_seconds:
                    del self.assigned[key]
            for track in tracks:
                entry = self.assigned.get(track["track_key"])
                if entry is None:
                    entry = [self.free_index(live), now]
                    self.assigned[track["track_key"]] = entry
                entry[1] = now
                track["color"] = list(self.palette[entry[0]])
        return tracks

    def free_index(self, live):
        """Pick an unused colour, freeing the oldest absent track's colour."""
        used = {index for index, _ in self.assigned.values()}
        for index in range(len(self.palette)):
            if index not in used:
                return index
        absent = None
        for key, (_, seen) in self.assigned.items():
            if key not in live and (
                absent is None or seen < self.assigned[absent][1]
            ):
                absent = key
        if absent is not None:
            return self.assigned.pop(absent)[0]
        # More people than colours: repeat the palette rather than fail.
        return len(self.assigned) % len(self.palette)
