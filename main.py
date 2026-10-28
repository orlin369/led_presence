#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Launch the local LED Presence web application."""

from led_presence.web.app import create_app


def main():
    """Listen on all interfaces without debug mode or automatic reload."""
    create_app().run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    main()
