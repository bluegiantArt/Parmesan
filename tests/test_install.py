"""Tests for the installer.

The installer writes two files into a live Houdini preferences directory, one
of which Houdini parses as XML and then executes as Python. A mistake there
fails silently -- the panel simply never appears in the menu -- so both the
XML and the injected Python are checked here rather than discovered by hand.

These run against a temporary directory; no Houdini required.
"""

import os
import tempfile
import unittest
import xml.etree.ElementTree as ET

import install


class PanelFileGeneration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.prefs = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def target_text(self) -> str:
        path = install._install_panel(self.prefs)
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_lands_in_the_python_panels_directory(self):
        path = install._install_panel(self.prefs)
        self.assertEqual(
            os.path.relpath(path, self.prefs),
            os.path.join("python_panels", "parmesan.pypanel"),
        )

    def test_result_is_still_well_formed_xml(self):
        # Injecting a Windows path with backslashes into XML is the obvious way
        # to break this.
        root = ET.fromstring(self.target_text())
        self.assertEqual(root.find("interface").get("name"), "parmesan")

    def test_injected_script_is_valid_python(self):
        script = ET.fromstring(self.target_text()).find("interface/script").text
        compile(script, "<pypanel>", "exec")

    def test_injected_script_defines_the_entry_point(self):
        script = ET.fromstring(self.target_text()).find("interface/script").text
        self.assertIn("def onCreateInterface()", script)

    def test_marker_is_replaced_not_merely_appended(self):
        text = self.target_text()
        self.assertNotIn(install.INJECTION_MARKER, text)
        self.assertIn(install.REPO_ROOT, text)

    def test_running_twice_overwrites_cleanly(self):
        install._install_panel(self.prefs)
        text = self.target_text()
        self.assertEqual(text.count("def onCreateInterface()"), 1)


class EnvFileEditing(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.prefs = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.env_path = os.path.join(self.prefs, "houdini.env")

    def read_env(self) -> str:
        with open(self.env_path, encoding="utf-8") as handle:
            return handle.read()

    def test_creates_the_file_when_absent(self):
        install._update_env(self.prefs)
        self.assertIn(install.REPO_ROOT, self.read_env())

    def test_second_run_does_not_duplicate_the_line(self):
        # A PYTHONPATH listed twice is harmless but it accumulates on every
        # reinstall, and a file full of repeated lines looks broken.
        install._update_env(self.prefs)
        install._update_env(self.prefs)
        self.assertEqual(self.read_env().count(install.REPO_ROOT), 1)

    def test_existing_content_is_preserved_and_backed_up(self):
        with open(self.env_path, "w", encoding="utf-8") as handle:
            handle.write("HOUDINI_SPLASH = 0\n")
        install._update_env(self.prefs)
        self.assertIn("HOUDINI_SPLASH = 0", self.read_env())
        backups = [n for n in os.listdir(self.prefs) if n.endswith(".bak")]
        self.assertEqual(len(backups), 1)

    def test_a_hand_written_variant_is_recognised(self):
        # Someone who added the path themselves, in their own style, should not
        # get a second entry bolted on underneath.
        with open(self.env_path, "w", encoding="utf-8") as handle:
            handle.write(f'PYTHONPATH="{install.REPO_ROOT}"\n')
        install._update_env(self.prefs)
        self.assertEqual(self.read_env().count(install.REPO_ROOT), 1)

    def test_a_commented_out_line_does_not_count(self):
        with open(self.env_path, "w", encoding="utf-8") as handle:
            handle.write(f'# PYTHONPATH = "{install.REPO_ROOT}"\n')
        install._update_env(self.prefs)
        self.assertEqual(self.read_env().count(install.REPO_ROOT), 2)

    def test_line_uses_the_platform_path_separator(self):
        expected = ";" if os.name == "nt" else ":"
        self.assertIn(f"{install.REPO_ROOT}{expected}$PYTHONPATH", install._env_line())

    def test_no_missing_newline_glues_lines_together(self):
        with open(self.env_path, "w", encoding="utf-8") as handle:
            handle.write("HOUDINI_SPLASH = 0")  # no trailing newline
        install._update_env(self.prefs)
        self.assertIn("HOUDINI_SPLASH = 0\n", self.read_env())


if __name__ == "__main__":
    unittest.main(verbosity=2)
