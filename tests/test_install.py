"""Tests for the installer.

The installer writes two files into a live Houdini preferences directory, one
of which Houdini parses as XML and then executes as Python. A mistake there
fails silently -- the panel simply never appears in the menu -- so both the
XML and the injected Python are checked here rather than discovered by hand.

These run against a temporary directory; no Houdini required.
"""

import os
import shutil
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

    def test_registers_in_the_pane_tab_menu(self):
        # Without this element the interface still exists, but only inside a
        # Python Panel pane's own dropdown -- it never appears under
        # New Pane Tab Type, which is where anyone will look for it first.
        iface = ET.fromstring(self.target_text()).find("interface")
        self.assertIsNotNone(
            iface.find("includeInPaneTabMenu"),
            "panel would not appear in Houdini's New Pane Tab Type menu",
        )

    def test_menu_entries_carry_a_position(self):
        iface = ET.fromstring(self.target_text()).find("interface")
        for tag in ("includeInPaneTabMenu", "includeInToolbarMenu"):
            element = iface.find(tag)
            self.assertIsNotNone(element, f"{tag} missing")
            self.assertTrue(element.get("menu_position"), f"{tag} has no position")

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


class Updating(unittest.TestCase):
    """update() writes files from a downloaded archive, so the unpacking half
    is tested here. The download itself is not: it is one urlretrieve call."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def make_archive(self, wrapper="Parmesan-main"):
        """A GitHub-shaped zip: everything nested under one folder."""
        import zipfile

        root = os.path.join(self.tmp, "src", wrapper) if wrapper else os.path.join(self.tmp, "src")
        os.makedirs(os.path.join(root, "parmesan"))
        with open(os.path.join(root, "install.py"), "w") as handle:
            handle.write("# new\n")
        with open(os.path.join(root, "parmesan", "panel.py"), "w") as handle:
            handle.write("# new panel\n")

        path = os.path.join(self.tmp, "archive.zip")
        base = os.path.join(self.tmp, "src")
        with zipfile.ZipFile(path, "w") as handle:
            for folder, _, files in os.walk(base):
                for name in files:
                    full = os.path.join(folder, name)
                    handle.write(full, os.path.relpath(full, base))
        return path

    def extract(self, archive):
        import zipfile

        out = os.path.join(self.tmp, "out")
        with zipfile.ZipFile(archive) as handle:
            install._safe_extract(handle, out)
        return out

    def test_finds_the_folder_github_wraps_everything_in(self):
        root = install._archive_root(self.extract(self.make_archive()))
        self.assertIsNotNone(root)
        self.assertTrue(os.path.exists(os.path.join(root, "install.py")))

    def test_also_copes_with_an_unwrapped_archive(self):
        root = install._archive_root(self.extract(self.make_archive(wrapper="")))
        self.assertIsNotNone(root)

    def test_an_archive_without_parmesan_in_it_is_rejected(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        self.assertIsNone(install._archive_root(empty))

    def test_copy_replaces_tracked_items(self):
        root = install._archive_root(self.extract(self.make_archive()))
        dest = os.path.join(self.tmp, "dest")
        os.makedirs(dest)
        changed = install._copy_over(root, dest)
        self.assertIn("install.py", changed)
        self.assertIn("parmesan", changed)

    def test_stale_files_inside_a_replaced_package_are_cleared(self):
        # A module deleted upstream must not linger and keep getting imported.
        root = install._archive_root(self.extract(self.make_archive()))
        dest = os.path.join(self.tmp, "dest")
        os.makedirs(os.path.join(dest, "parmesan"))
        stale = os.path.join(dest, "parmesan", "removed_upstream.py")
        with open(stale, "w") as handle:
            handle.write("old\n")
        install._copy_over(root, dest)
        self.assertFalse(os.path.exists(stale))

    def test_files_we_did_not_put_there_are_left_alone(self):
        # An update must never delete something it does not own -- someone
        # will keep a scene file next to the code.
        root = install._archive_root(self.extract(self.make_archive()))
        dest = os.path.join(self.tmp, "dest")
        os.makedirs(dest)
        precious = os.path.join(dest, "my_scene.hip")
        with open(precious, "w") as handle:
            handle.write("precious\n")
        install._copy_over(root, dest)
        self.assertTrue(os.path.exists(precious))

    def fake_repo(self):
        """Stand in for the installed folder: the code as it already is, plus
        a user file beside it that an update must not touch."""
        root = os.path.join(self.tmp, "Parmesan-main3")
        os.makedirs(os.path.join(root, "parmesan"), exist_ok=True)
        with open(os.path.join(root, "install.py"), "w") as handle:
            handle.write("# installed\n")
        with open(os.path.join(root, "my_scene.hip"), "w") as handle:
            handle.write("precious\n")
        return root

    def run_update_from(self, path):
        """update_from() writes to REPO_ROOT, so point that at a scratch dir."""
        root = self.fake_repo()
        original = install.REPO_ROOT
        install.REPO_ROOT = root
        try:
            return root, install.update_from(path, verbose=False)
        finally:
            install.REPO_ROOT = original

    def test_update_from_a_zip(self):
        root, report = self.run_update_from(self.make_archive())
        self.assertTrue(os.path.exists(os.path.join(root, "parmesan", "panel.py")))
        self.assertTrue(any("Next:" in line for line in report))

    def test_update_from_an_unzipped_folder(self):
        source = os.path.join(self.tmp, "src", "Parmesan-main")
        self.make_archive()  # also leaves the folder on disk
        root, _ = self.run_update_from(source)
        self.assertTrue(os.path.exists(os.path.join(root, "parmesan", "panel.py")))

    def test_the_github_wrapper_folder_is_not_copied_in(self):
        # Copying the wrapper by hand is how people end up with
        # Parmesan-main3/Parmesan-main/parmesan/.
        root, _ = self.run_update_from(self.make_archive())
        self.assertFalse(os.path.exists(os.path.join(root, "Parmesan-main")))

    def test_a_path_wrapped_in_quotes_still_works(self):
        # Windows "Copy as path" hands you the path with quotes around it.
        archive = self.make_archive()
        root, report = self.run_update_from(f'"{archive}"')
        self.assertTrue(os.path.exists(os.path.join(root, "parmesan", "panel.py")))

    def test_pointing_at_a_download_folder_picks_the_newest_zip(self):
        # The update command should be one fixed line. Browsers rename repeat
        # downloads, so an exact filename would need editing every time.
        import time

        downloads = os.path.join(self.tmp, "downloads")
        os.makedirs(downloads)
        archive = self.make_archive()
        for name, age in (
            ("Parmesan-main.zip", 900),
            ("Parmesan-main (1).zip", 600),
            ("Parmesan-main (2).zip", 5),
        ):
            target = os.path.join(downloads, name)
            shutil.copy2(archive, target)
            os.utime(target, (time.time() - age, time.time() - age))

        self.assertEqual(
            os.path.basename(install._newest_archive(downloads)),
            "Parmesan-main (2).zip",
        )
        root, report = self.run_update_from(downloads)
        self.assertTrue(os.path.exists(os.path.join(root, "parmesan", "panel.py")))
        self.assertTrue(any("Newest Parmesan archive" in line for line in report))

    def test_a_folder_with_no_archives_returns_nothing(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        self.assertIsNone(install._newest_archive(empty))

    def test_unrelated_zips_in_the_folder_are_ignored(self):
        downloads = os.path.join(self.tmp, "downloads2")
        os.makedirs(downloads)
        with open(os.path.join(downloads, "textures.zip"), "w") as handle:
            handle.write("not ours\n")
        self.assertIsNone(install._newest_archive(downloads))

    def test_a_missing_path_fails_clearly(self):
        _, report = self.run_update_from(os.path.join(self.tmp, "nope.zip"))
        self.assertTrue(report[0].startswith("FAIL"))

    def test_updating_from_itself_is_refused(self):
        root = self.fake_repo()
        original = install.REPO_ROOT
        install.REPO_ROOT = root
        try:
            report = install.update_from(root, verbose=False)
        finally:
            install.REPO_ROOT = original
        self.assertTrue(any("same folder" in line for line in report))

    def test_something_that_is_not_parmesan_is_refused(self):
        junk = os.path.join(self.tmp, "junk")
        os.makedirs(junk)
        with open(os.path.join(junk, "readme.txt"), "w") as handle:
            handle.write("not parmesan\n")
        _, report = self.run_update_from(junk)
        self.assertTrue(any("does not look like" in line for line in report))

    def test_an_archive_escaping_its_destination_is_refused(self):
        import zipfile

        path = os.path.join(self.tmp, "bad.zip")
        with zipfile.ZipFile(path, "w") as handle:
            handle.writestr("../escaped.txt", "nope")
        with zipfile.ZipFile(path) as handle:
            with self.assertRaises(ValueError):
                install._safe_extract(handle, os.path.join(self.tmp, "out"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escaped.txt")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
