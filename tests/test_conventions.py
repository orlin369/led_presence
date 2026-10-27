#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Keep the project's structural development rules enforceable."""

import ast
import re
import tempfile
import unittest
from unittest.mock import patch

import main as entry_point
from led_presence.core.paths import ROOT


class ConventionTests(unittest.TestCase):
    """Check source layout and the application's startup contract."""

    def test_python_headers_classes_and_function_scope(self):
        paths = [
            ROOT / "main.py",
            *(ROOT / "led_presence").rglob("*.py"),
            *(ROOT / "tests").glob("*.py"),
        ]
        for path in paths:
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertTrue(
                    source.startswith(
                        "#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\n"
                    )
                )
                tree = ast.parse(source)
                classes = [
                    node
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ClassDef)
                ]
                self.assertLessEqual(len(classes), 1)
                if classes and not path.name.startswith("test_"):
                    expected = re.sub(
                        r"(?<!^)(?=[A-Z])", "_", classes[0].name
                    ).lower()
                    self.assertEqual(path.stem, expected)
                for node in ast.walk(tree):
                    self.assertNotIsInstance(node, ast.Lambda)
                    if isinstance(
                        node, (ast.FunctionDef, ast.AsyncFunctionDef)
                    ):
                        descendants = list(ast.walk(node))[1:]
                        self.assertFalse(
                            any(
                                isinstance(
                                    child,
                                    (ast.FunctionDef, ast.AsyncFunctionDef),
                                )
                                for child in descendants
                            ),
                            f"Nested function in {path.name}:{node.lineno}",
                        )

    def test_application_modules_use_package_namespaces(self):
        self.assertEqual(
            {path.name for path in ROOT.glob("*.py")}, {"main.py"}
        )
        package = ROOT / "led_presence"
        self.assertTrue((package / "__init__.py").is_file())
        for path in package.rglob("*.py"):
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertTrue((path.parent / "__init__.py").is_file())
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        self.assertEqual(node.level, 0)
                        self.assertNotIn(
                            "*", [alias.name for alias in node.names]
                        )
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        app = entry_point.create_app(directory.name, start_worker=False)
        self.addCleanup(app.extensions["installation"].close)
        self.assertEqual(app.root_path, str(package / "web"))
        with app.test_client().get("/static/app.js") as response:
            self.assertEqual(response.status_code, 200)

    def test_entry_point_calls_main_first(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        guard = tree.body[-1]
        self.assertIsInstance(guard, ast.If)
        self.assertEqual(ast.unparse(guard.test), "__name__ == '__main__'")
        self.assertEqual(ast.unparse(guard.body[0]), "main()")
        with patch("main.create_app") as factory:
            entry_point.main()
        factory.assert_called_once_with()
        factory.return_value.run.assert_called_once_with(
            host="127.0.0.1", port=5000, debug=False
        )


def main():
    """Run the convention checks directly."""
    unittest.main()


if __name__ == "__main__":
    main()
