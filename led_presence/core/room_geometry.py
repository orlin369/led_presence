#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate simple floor polygons without changing the fixed room origin."""

import math


def room_outline(room):
    """Return the optional polygon, falling back to the legacy rectangle."""
    return room.get(
        "outline_mm",
        [
            [0, 0],
            [room["width_mm"], 0],
            [room["width_mm"], room["depth_mm"]],
            [0, room["depth_mm"]],
        ],
    )


def cross(a, b, c):
    """Return signed twice-area for three points in millimetres."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def on_segment(a, b, p):
    """Include endpoints when testing polygon edges for contact."""
    return (
        abs(cross(a, b, p)) < 1e-6
        and min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
        and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])
    )


def segments_intersect(a, b, c, d):
    """Reject crossings, touches and overlaps between non-adjacent walls."""
    ab_c, ab_d = cross(a, b, c), cross(a, b, d)
    cd_a, cd_b = cross(c, d, a), cross(c, d, b)
    return (ab_c * ab_d < 0 and cd_a * cd_b < 0) or any(
        (
            on_segment(a, b, c),
            on_segment(a, b, d),
            on_segment(c, d, a),
            on_segment(c, d, b),
        )
    )


def validate_outline(room):
    """Require a finite, non-degenerate simple polygon within room bounds.

    The field is optional in schema 6 so rectangular instances retain their
    exact configuration. Width/depth remain the positive coordinate envelope;
    the origin and legacy mounting offsets never move when corners are edited.
    """
    if "outline_mm" not in room:
        return
    points = room["outline_mm"]
    if not isinstance(points, list) or not 3 <= len(points) <= 64:
        raise ValueError("Room outline must contain 3–64 corners")
    for point in points:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError("Each room corner must be [X, Y] in mm")
        for axis, limit in enumerate((room["width_mm"], room["depth_mm"])):
            value = point[axis]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= limit
            ):
                raise ValueError("Room corners must lie within width/depth")
    if len({tuple(point) for point in points}) != len(points):
        raise ValueError("Room corners must be distinct")
    area = 0
    for i, a in enumerate(points):
        b, c = points[(i + 1) % len(points)], points[(i + 2) % len(points)]
        area += a[0] * b[1] - a[1] * b[0]
        if abs(cross(a, b, c)) < 1e-6:
            # Collinear continuation is useful after splitting a wall, but
            # reversing direction would make adjacent walls overlap.
            if (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (
                c[1] - b[1]
            ) <= 0:
                raise ValueError("Adjacent room walls must not overlap")
        for j in range(i + 1, len(points)):
            if j == i + 1 or (i == 0 and j == len(points) - 1):
                continue
            if segments_intersect(
                a, b, points[j], points[(j + 1) % len(points)]
            ):
                raise ValueError("Room walls must not cross or touch")
    if abs(area) < 1e-6:
        raise ValueError("Room outline must enclose an area")
