#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Launch the local LED Presence web application."""

from led_presence.web.app import create_app


def main():
    """Start the local server without debug mode or automatic reload."""
    create_app().run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
