# Development rules

These rules apply to maintained project code, including tests. Local instance
data, virtual environments and third-party dependencies are excluded.

- Follow PEP 8 for Python: four spaces, `snake_case` functions and modules,
  `PascalCase` classes, uppercase constants and a maximum of 79 characters
  per code line. Use Ruff to format code, sort imports and check style.
- Begin every Python file with `#!/usr/bin/env python3`, followed by
  `# -*- coding: utf-8 -*-`. This portable interpreter path works with the
  active virtual environment; Windows users launch it with `python`.
  Non-Python files keep their native syntax and use UTF-8 encoding.
- Put each Python class in its own module, named after it in `snake_case`.
  Test modules may use the `test_` prefix and contain one test class each.
- Organize application modules under the `led_presence` package namespace.
  Group them by responsibility in `core`, `config`, `sensors`, `lights`,
  `animations` and `web` packages. Only the `main.py` launcher belongs at the
  repository root. Put templates and static assets under `led_presence/web`.
  Use explicit absolute imports such as
  `from led_presence.sensors.sensor_reader import SensorReader`.
  Do not use wildcard imports, modify `sys.path` or add flat compatibility
  modules to bypass the package structure. Keep package initializers minimal
  and free of hardware or application startup side effects.
- Do not define Python functions inside functions. Use module-level helpers or
  class methods. Prefer named helpers or standard-library operators to
  anonymous callbacks when they hide logic.
- The application entry point is `main.py`. Its `if __name__ == "__main__"`
  guard calls `main()` first. Importing a module must not start the app or
  connect to hardware. Directly executable utility and test modules also
  enter through a `main()` function.
- Keep HTTP handlers, configuration, geometry, device connections and light
  transport in their respective modules. Avoid circular imports.
- Use millimetres for application geometry and degrees for angles. Convert
  device units at the boundary and make units explicit in names or context.
- Keep installation-specific measurements, addresses and secrets in the
  ignored instance configuration. Preserve user data during migrations.
- Validate external input before changing configuration or hardware. Set
  network timeouts and restore the previous light state when output stops.
- Document public modules, classes and non-obvious behaviour. Explain the
  reason for unusual transforms or protocol details in comments.
- Add meaningful tests for behaviour changes. Tests use temporary instance
  directories and mocked hardware or loopback networking.
- Before finishing, run the style, structure and test commands in README.md.
  Update the README when launch commands, configuration or behaviour change.
