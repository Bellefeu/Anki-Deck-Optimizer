#!/usr/bin/env python3
"""Tests for everything that turns PRISM into a downloadable application.

Standard library only, and no PyInstaller: these cover the parts that decide
whether a built application behaves, which is the payload it carries, the
folder it lays down, the settings it keeps, the icon it wears and the recipe
that assembles them. Freezing itself is proved by the release workflow.
"""

from __future__ import annotations

import ast
import json
import os
import re
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "control_center"), str(ROOT / "scripts"), str(ROOT / "appbuild")]

import build as recipe  # noqa: E402
import desktop  # noqa: E402
import icons  # noqa: E402
import workspace as ws  # noqa: E402


def private_home(parent):
    """Point every per-user location at a throwaway folder."""
    home = Path(parent) / "home"
    home.mkdir(parents=True, exist_ok=True)
    return mock.patch.dict(os.environ, {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "APPDATA": str(home / "AppData/Roaming"),
        "LOCALAPPDATA": str(home / "AppData/Local"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_STATE_HOME": str(home / ".local/state"),
    })


class VersionTests(unittest.TestCase):
    def test_the_application_version_matches_the_release_it_is_built_from(self):
        """A build copies its payload out of the manifest, so shipping an
        application that claims a different number than the toolkit inside it
        would make every support conversation start with a wrong fact."""
        manifest = json.loads((ROOT / "scripts/UPDATE_MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(ws.APP_VERSION, manifest["release_version"])

    def test_the_version_is_a_plain_three_part_number(self):
        parts = ws.APP_VERSION.split(".")
        self.assertEqual(len(parts), 3, ws.APP_VERSION)
        for part in parts:
            self.assertTrue(part.isdigit(), ws.APP_VERSION)


class PayloadTests(unittest.TestCase):
    def test_the_checkout_is_its_own_payload_and_matches_the_manifest(self):
        report = ws.verify_payload(ROOT)
        self.assertEqual(report["missing"], [], "rebuild scripts/UPDATE_MANIFEST.json")
        self.assertEqual(report["altered"], [], "rebuild scripts/UPDATE_MANIFEST.json")

    def test_build_tooling_and_tests_never_reach_a_workspace(self):
        carried = ws.payload_files(ROOT)
        for excluded in ws.PAYLOAD_EXCLUDED:
            with self.subTest(excluded=excluded):
                self.assertNotIn(excluded, carried)
                self.assertTrue((ROOT / excluded).is_file(),
                                "the exclusion names a file that no longer exists")
        for relative in carried:
            with self.subTest(relative=relative):
                self.assertFalse(relative.startswith("appbuild/"))
                self.assertFalse(relative.startswith(".github/"))

    def test_the_allowlist_travels_with_the_files_it_describes(self):
        """The manifest cannot carry its own hash, so it is copied outside the
        hash checked set. Forgetting it leaves a workspace that reports itself
        as a legacy install and cannot be updated."""
        self.assertIn("scripts/UPDATE_MANIFEST.json", ws.UNHASHED_PAYLOAD)
        self.assertNotIn("scripts/UPDATE_MANIFEST.json", ws.payload_files(ROOT))

    def test_the_dashboard_and_the_launchers_are_part_of_the_payload(self):
        carried = ws.payload_files(ROOT)
        for relative in ("control_center/app.py", "control_center/desktop.py",
                         "control_center/workspace.py", "control_center/static/app.js",
                         "control_center/static/prism-field.js", "START HERE.md",
                         "PRISM - Mac.command", "PRISM - Windows.cmd",
                         "scripts/build_deck.py", "control_center/templates/PROFILE.template.md"):
            with self.subTest(relative=relative):
                self.assertIn(relative, carried)


class WorkspaceTests(unittest.TestCase):
    def test_a_new_workspace_is_a_project_whose_every_file_verifies(self):
        with tempfile.TemporaryDirectory() as temp, private_home(temp):
            destination = (Path(temp) / "PRISM").resolve()
            created = ws.create_workspace(destination, source=ROOT)
            self.assertEqual(created, destination)
            self.assertTrue(ws.is_workspace(created))

            report = ws.inspect_workspace(created)
            self.assertEqual(report, {"missing": [], "altered": []})

            manifest = json.loads((created / "scripts/UPDATE_MANIFEST.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["release_version"], ws.APP_VERSION)

            for folder in ws.STUDY_FOLDERS:
                with self.subTest(folder=folder):
                    self.assertTrue((created / folder).is_dir())
            self.assertTrue((created / "PROFILE.md").is_file())
            self.assertEqual(
                (created / "PROFILE.md").read_bytes(),
                (created / "control_center/templates/PROFILE.template.md").read_bytes(),
            )
            for excluded in ws.PAYLOAD_EXCLUDED:
                with self.subTest(excluded=excluded):
                    self.assertFalse((created / excluded).exists())
            if os.name != "nt":
                self.assertTrue(os.access(created / "PRISM - Mac.command", os.X_OK))
                self.assertTrue(os.access(created / "control_center/launch.sh", os.X_OK))

    def test_creating_never_writes_into_a_folder_that_already_holds_work(self):
        with tempfile.TemporaryDirectory() as temp, private_home(temp):
            occupied = Path(temp) / "occupied"
            occupied.mkdir()
            treasure = occupied / "thesis.docx"
            treasure.write_bytes(b"eight months of work")

            self.assertTrue(ws.workspace_blockers(occupied))
            with self.assertRaises(ws.WorkspaceError):
                ws.create_workspace(occupied, source=ROOT)
            self.assertEqual(treasure.read_bytes(), b"eight months of work")

            # An existing workspace is refused for a different reason, and
            # the message has to send the user to Open rather than to Create.
            good = (Path(temp) / "PRISM").resolve()
            ws.create_workspace(good, source=ROOT)
            with self.assertRaises(ws.WorkspaceError) as refused:
                ws.create_workspace(good, source=ROOT)
            self.assertIn("Open it instead", str(refused.exception))

    def test_an_interrupted_creation_leaves_nothing_that_looks_like_a_project(self):
        with tempfile.TemporaryDirectory() as temp, private_home(temp):
            destination = (Path(temp) / "PRISM").resolve()
            real_copy = ws.shutil.copy2
            calls = {"n": 0}

            def fail_partway(source, target, *args, **kwargs):
                calls["n"] += 1
                if calls["n"] > 6:
                    raise OSError("the disk filled up")
                return real_copy(source, target, *args, **kwargs)

            with mock.patch.object(ws.shutil, "copy2", fail_partway):
                with self.assertRaises(OSError):
                    ws.create_workspace(destination, source=ROOT)
            self.assertFalse(destination.exists())
            leftovers = [path.name for path in Path(temp).iterdir()
                         if path.name.startswith(".prism-new-")]
            self.assertEqual(leftovers, [])

    def test_restoring_puts_back_what_is_missing_and_never_touches_an_edit(self):
        with tempfile.TemporaryDirectory() as temp, private_home(temp):
            created = ws.create_workspace((Path(temp) / "PRISM").resolve(), source=ROOT)
            (created / "scripts/deps.py").unlink()
            edited = created / "scripts/cleanup.py"
            edited.write_text("# mine now\n", encoding="utf-8")

            report = ws.inspect_workspace(created)
            self.assertEqual(report["missing"], ["scripts/deps.py"])
            self.assertEqual(report["altered"], ["scripts/cleanup.py"])

            result = ws.restore_missing(created)
            self.assertEqual(result["restored"], ["scripts/deps.py"])
            self.assertEqual(result["altered"], ["scripts/cleanup.py"])
            self.assertTrue((created / "scripts/deps.py").is_file())
            self.assertEqual(edited.read_text(encoding="utf-8"), "# mine now\n")

    def test_a_lost_allowlist_is_reported_and_restored(self):
        with tempfile.TemporaryDirectory() as temp, private_home(temp):
            created = ws.create_workspace((Path(temp) / "PRISM").resolve(), source=ROOT)
            (created / "scripts/UPDATE_MANIFEST.json").unlink()
            self.assertIn("scripts/UPDATE_MANIFEST.json", ws.inspect_workspace(created)["missing"])
            ws.restore_missing(created)
            self.assertEqual(ws.inspect_workspace(created), {"missing": [], "altered": []})

    def test_a_folder_picked_for_a_new_workspace_is_used_or_extended(self):
        with tempfile.TemporaryDirectory() as temp, private_home(temp):
            empty = (Path(temp) / "empty").resolve()
            empty.mkdir()
            self.assertEqual(ws.suggest_inside(empty), empty)

            busy = (Path(temp) / "Documents").resolve()
            busy.mkdir()
            (busy / "notes.txt").write_text("hello", encoding="utf-8")
            self.assertEqual(ws.suggest_inside(busy), busy / "PRISM")

            (busy / "PRISM").mkdir()
            (busy / "PRISM/something.txt").write_text("x", encoding="utf-8")
            self.assertEqual(ws.suggest_inside(busy), busy / "PRISM 2")


class SettingsTests(unittest.TestCase):
    def test_settings_round_trip_and_a_damaged_file_is_survivable(self):
        with tempfile.TemporaryDirectory() as temp, private_home(temp):
            self.assertEqual(ws.load_settings()["workspace"], None)

            created = ws.create_workspace((Path(temp) / "PRISM").resolve(), source=ROOT)
            ws.remember_workspace(created)
            self.assertEqual(ws.remembered_workspace(), created)
            self.assertEqual(ws.load_settings()["recent"], [str(created)])

            ws.save_window_state({"width": 1300, "height": 900, "x": 40, "y": 60})
            self.assertEqual(ws.load_settings()["window"]["width"], 1300)
            self.assertEqual(ws.remembered_workspace(), created,
                             "saving geometry must not disturb the workspace")

            ws.config_path().write_text("{ this is not json", encoding="utf-8")
            recovered = ws.load_settings()
            self.assertEqual(recovered["workspace"], None)
            self.assertEqual(recovered["recent"], [])

    def test_a_remembered_folder_that_no_longer_exists_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp, private_home(temp):
            created = ws.create_workspace((Path(temp) / "PRISM").resolve(), source=ROOT)
            ws.remember_workspace(created)
            ws.shutil.rmtree(created)
            self.assertIsNone(ws.remembered_workspace())
            self.assertEqual(ws.load_settings()["workspace"], str(created),
                             "the record stays so the path can still be shown")

    def test_settings_and_logs_are_kept_apart_and_under_the_user(self):
        with tempfile.TemporaryDirectory() as temp, private_home(temp):
            home = Path(temp) / "home"
            for path in (ws.config_dir(), ws.state_dir()):
                with self.subTest(path=path):
                    self.assertTrue(str(path).startswith(str(home)), path)
            self.assertNotEqual(ws.config_dir(), ws.state_dir())
            self.assertEqual(ws.session_path().parent, ws.state_dir())


class SessionTests(unittest.TestCase):
    def test_the_running_instance_record_is_readable_only_by_this_account(self):
        with tempfile.TemporaryDirectory() as temp, private_home(temp):
            desktop.write_session(51234, "a-token-that-authorises-writes")
            stored = desktop.read_session()
            self.assertEqual(stored["port"], 51234)
            self.assertEqual(stored["token"], "a-token-that-authorises-writes")
            self.assertEqual(stored["pid"], os.getpid())
            if os.name != "nt":
                mode = ws.session_path().stat().st_mode & 0o777
                self.assertEqual(mode, 0o600, f"instance.json is {oct(mode)}")

            desktop.clear_session()
            self.assertIsNone(desktop.read_session())

    def test_a_damaged_or_partial_record_is_treated_as_no_instance(self):
        with tempfile.TemporaryDirectory() as temp, private_home(temp):
            ws.state_dir().mkdir(parents=True, exist_ok=True)
            for content in ("", "not json", "[]", '{"port": "not a number"}', '{"token": "x"}'):
                with self.subTest(content=content):
                    ws.session_path().write_text(content, encoding="utf-8")
                    self.assertIsNone(desktop.read_session())

    def test_a_stale_record_is_cleared_rather_than_blocking_a_launch(self):
        with tempfile.TemporaryDirectory() as temp, private_home(temp):
            # Port 0 can never be connected to, so this stands in for a PRISM
            # that was killed without cleaning up after itself.
            desktop.write_session(0, "stale")
            self.assertFalse(desktop.focus_running_instance(timeout=0.2))
            self.assertFalse(ws.session_path().exists())


class WindowTests(unittest.TestCase):
    LAPTOP = [(0, 0, 1512, 982)]

    def test_geometry_is_restored_when_it_still_lands_on_a_screen(self):
        restored = desktop.clamp_geometry(
            {"width": 1300, "height": 880, "x": 100, "y": 60}, self.LAPTOP)
        self.assertEqual(restored, {"width": 1300, "height": 880, "x": 100, "y": 60})

    def test_a_window_left_on_a_monitor_that_is_gone_reopens_centred(self):
        """Restoring a position on a screen that is no longer attached hides
        the window somewhere the user cannot reach, and there is no obvious
        way back from that."""
        for stored in (
            {"width": 1300, "height": 880, "x": 3200, "y": 400},
            {"width": 1300, "height": 880, "x": -2000, "y": 100},
            {"width": 1300, "height": 880, "x": 200, "y": 4000},
        ):
            with self.subTest(stored=stored):
                restored = desktop.clamp_geometry(stored, self.LAPTOP)
                self.assertIsNone(restored["x"])
                self.assertIsNone(restored["y"])

    def test_absurd_and_missing_sizes_fall_back_to_something_usable(self):
        for stored in ({}, {"width": 12, "height": 9}, {"width": 99999, "height": 99999},
                       {"width": "wide", "height": None}):
            with self.subTest(stored=stored):
                restored = desktop.clamp_geometry(stored, self.LAPTOP)
                self.assertGreaterEqual(restored["width"], desktop.MINIMUM_SIZE[0])
                self.assertGreaterEqual(restored["height"], desktop.MINIMUM_SIZE[1])
                self.assertLessEqual(restored["width"], 6000)

    @staticmethod
    def _shell(width=1120, height=740, x=180, y=120):
        shell = desktop.Shell(None, None)
        shell.attach(None, object(), {"width": width, "height": height, "x": x, "y": y})
        return shell

    def test_a_window_does_not_shrink_by_a_title_bar_every_launch(self):
        """macOS answers a requested height with the height minus a title
        bar. Saving that and asking for it next time loses another one, and
        the window walks down to the minimum over a couple of weeks."""
        shell = self._shell(height=740)
        shell.open_calibration()
        shell.note_size(1120, 708)
        self.assertEqual(shell.geometry["height"], 740, "the platform echo was saved raw")
        shell.note_size(1000, 608)
        self.assertEqual(shell.geometry, {"width": 1000, "height": 640, "x": 180, "y": 120})

    def test_a_platform_that_reports_honestly_is_left_alone(self):
        shell = self._shell(width=1280, height=860)
        shell.open_calibration()
        shell.note_size(1280, 860)
        shell.note_size(1000, 700)
        self.assertEqual(shell.geometry["width"], 1000)
        self.assertEqual(shell.geometry["height"], 700)

    def test_a_resize_after_opening_is_never_mistaken_for_calibration(self):
        shell = self._shell(width=1280, height=860)
        shell.open_calibration(seconds=0)
        shell.note_size(900, 700)
        self.assertEqual(shell.geometry["width"], 900)
        self.assertEqual(shell.geometry["height"], 700)

    def test_an_implausible_gap_is_not_treated_as_a_title_bar(self):
        shell = self._shell(width=1280, height=860)
        shell.open_calibration()
        shell.note_size(400, 300)
        self.assertEqual(shell.geometry["width"], 400)
        self.assertEqual(shell.geometry["height"], 300)

    def test_geometry_is_only_saved_once_something_is_known(self):
        shell = desktop.Shell(None, None)
        with mock.patch.object(ws, "save_window_state") as save:
            shell.remember_geometry()
            save.assert_not_called()

    def test_the_window_is_never_smaller_than_the_layout_it_holds(self):
        """Below 980 pixels the dashboard drops to its compact layout, so the
        minimum has to sit near that rather than at some arbitrary number."""
        self.assertGreaterEqual(desktop.MINIMUM_SIZE[0], 860)
        self.assertLessEqual(desktop.MINIMUM_SIZE[0], desktop.DEFAULT_SIZE[0])
        self.assertLessEqual(desktop.MINIMUM_SIZE[1], desktop.DEFAULT_SIZE[1])


class RuntimeTests(unittest.TestCase):
    def test_every_platform_can_explain_a_missing_web_view(self):
        report = desktop.runtime_report()
        self.assertIn("engine", report)
        self.assertIsInstance(report["ok"], bool)
        if not report["ok"]:
            self.assertTrue(report["remedy"], "a missing runtime must say how to fix it")

    def test_the_linux_remedy_names_a_command_for_each_major_family(self):
        with mock.patch.object(desktop.sys, "platform", "linux"), \
                mock.patch.object(desktop, "gtk_webkit_version", return_value=None):
            remedy = desktop.runtime_report()["remedy"]
        for manager in ("apt install", "dnf install", "pacman -S"):
            with self.subTest(manager=manager):
                self.assertIn(manager, remedy)

    def test_the_windows_remedy_points_at_the_runtime_download(self):
        with mock.patch.object(desktop.sys, "platform", "win32"), \
                mock.patch.object(desktop, "webview2_runtime", return_value=None):
            report = desktop.runtime_report()
        self.assertFalse(report["ok"])
        self.assertIn("webview2", report["remedy"].lower())
        self.assertIn("https://", report["remedy"])

    def test_macos_is_never_reported_as_missing_a_web_view(self):
        with mock.patch.object(desktop.sys, "platform", "darwin"):
            self.assertTrue(desktop.runtime_report()["ok"])

    def test_the_shell_starts_without_a_window_toolkit_installed(self):
        """desktop.py has to be importable and answer --version on a machine
        with no pywebview at all, or a broken install becomes unreportable."""
        self.assertNotIn("webview", sys.modules.get("desktop").__dict__)
        with mock.patch.dict(sys.modules, {"webview": None}):
            self.assertIsNone(desktop.import_webview())


class IconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temp.name)
        icons.write_icons(cls.folder, quiet=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_the_icon_is_drawn_from_the_mark_the_dashboard_uses(self):
        rulings = icons.read_rulings()
        self.assertGreaterEqual(len(rulings), 20)
        for line in rulings:
            self.assertEqual(len(line), 4)
            for value in line:
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 1.0)

    def test_every_delivered_png_is_a_valid_image_of_the_size_it_claims(self):
        for size in icons.PNG_SIZES:
            with self.subTest(size=size):
                blob = (self.folder / f"prism-{size}.png").read_bytes()
                self.assertEqual(blob[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(blob[12:16], b"IHDR")
                width, height, depth, colour = struct.unpack(">IIBB", blob[16:26])
                self.assertEqual((width, height), (size, size))
                self.assertEqual((depth, colour), (8, 6), "8 bit RGBA")

    def test_the_windows_icon_indexes_every_entry_it_declares(self):
        blob = (self.folder / "PRISM.ico").read_bytes()
        reserved, kind, count = struct.unpack("<HHH", blob[:6])
        self.assertEqual((reserved, kind), (0, 1))
        self.assertEqual(count, len(icons.ICO_SIZES))
        seen = []
        for index in range(count):
            entry = blob[6 + index * 16:22 + index * 16]
            width, height, _, _, planes, bits, length, offset = struct.unpack("<BBBBHHII", entry)
            self.assertEqual((planes, bits), (1, 32))
            self.assertLessEqual(offset + length, len(blob), "an entry points past the file")
            self.assertEqual(blob[offset:offset + 8], b"\x89PNG\r\n\x1a\n")
            declared = struct.unpack(">II", blob[offset + 16:offset + 24])
            self.assertEqual(declared[0], 256 if width == 0 else width)
            seen.append(declared[0])
        self.assertEqual(sorted(seen), sorted(icons.ICO_SIZES))

    def test_the_macos_icon_is_a_well_formed_icns_container(self):
        blob = (self.folder / "PRISM.icns").read_bytes()
        self.assertEqual(blob[:4], b"icns")
        self.assertEqual(struct.unpack(">I", blob[4:8])[0], len(blob),
                         "the header length must describe the whole file")
        offset, found = 8, []
        while offset < len(blob):
            kind = blob[offset:offset + 4]
            length = struct.unpack(">I", blob[offset + 4:offset + 8])[0]
            self.assertGreater(length, 8)
            self.assertLessEqual(offset + length, len(blob))
            self.assertEqual(blob[offset + 8:offset + 16], b"\x89PNG\r\n\x1a\n")
            found.append(kind)
            offset += length
        self.assertEqual(offset, len(blob), "a chunk overran the container")
        # Retina Dock and Finder sizes are the ones a user actually sees.
        for required in (b"ic10", b"ic09", b"ic08", b"ic07"):
            with self.subTest(required=required):
                self.assertIn(required, found)

    def test_the_artwork_is_luminous_line_work_on_black(self):
        """Two failure modes are invisible in a unit test unless they are
        named: a blank tile, and a solid grey blob where the rulings have
        merged. The first has no bright pixels, the second has no dark ones."""
        size = 128
        blob = (self.folder / f"prism-{size}.png").read_bytes()
        pixels = self._decode(blob, size)
        opaque = [pixels[index:index + 4] for index in range(0, len(pixels), 4)
                  if pixels[index + 3] > 200]
        self.assertGreater(len(opaque), size * size * 0.5, "the tile is mostly transparent")
        luminance = [(3 * px[2] + 4 * px[1] + px[0]) / 8 for px in opaque]
        dark = sum(1 for value in luminance if value < 12) / len(luminance)
        lit = sum(1 for value in luminance if value > 90) / len(luminance)
        self.assertGreater(dark, 0.45, "the ground is not black; the rulings have merged")
        self.assertGreater(lit, 0.01, "nothing in the tile is actually lit")
        blue = sum(px[2] for px in opaque)
        red = sum(px[0] for px in opaque)
        self.assertGreater(blue, red * 1.5, "the ramp should read cold, not neutral")

    @staticmethod
    def _decode(blob, size):
        """Undo PNG filtering for an 8 bit RGBA image with no interlacing."""
        import zlib

        offset, data = 8, b""
        while offset < len(blob):
            length = struct.unpack(">I", blob[offset:offset + 4])[0]
            kind = blob[offset + 4:offset + 8]
            if kind == b"IDAT":
                data += blob[offset + 8:offset + 8 + length]
            offset += 12 + length
        raw = zlib.decompress(data)
        stride = size * 4
        out = bytearray()
        previous = bytearray(stride)
        position = 0
        for _ in range(size):
            filter_type = raw[position]
            line = bytearray(raw[position + 1:position + 1 + stride])
            position += 1 + stride
            if filter_type == 1:
                for index in range(4, stride):
                    line[index] = (line[index] + line[index - 4]) & 0xFF
            elif filter_type == 2:
                for index in range(stride):
                    line[index] = (line[index] + previous[index]) & 0xFF
            elif filter_type == 3:
                for index in range(stride):
                    left = line[index - 4] if index >= 4 else 0
                    line[index] = (line[index] + (left + previous[index]) // 2) & 0xFF
            elif filter_type == 4:
                for index in range(stride):
                    left = line[index - 4] if index >= 4 else 0
                    upper = previous[index]
                    corner = previous[index - 4] if index >= 4 else 0
                    estimate = left + upper - corner
                    deltas = (abs(estimate - left), abs(estimate - upper), abs(estimate - corner))
                    nearest = (left, upper, corner)[deltas.index(min(deltas))]
                    line[index] = (line[index] + nearest) & 0xFF
            out += line
            previous = line
        return bytes(out)


class RecipeTests(unittest.TestCase):
    def test_the_entry_point_and_every_hidden_import_exists(self):
        self.assertTrue(recipe.ENTRY.is_file())
        for module in recipe.HIDDEN["common"]:
            if module == "webview":
                continue
            with self.subTest(module=module):
                found = (ROOT / "control_center" / f"{module}.py").is_file() \
                    or (ROOT / "scripts" / f"{module}.py").is_file()
                self.assertTrue(found, f"{module} is declared but is not in the tree")

    def test_a_backend_is_named_for_each_platform_that_is_built(self):
        for platform_key in ("darwin", "win32", "linux"):
            with self.subTest(platform=platform_key):
                hidden = recipe.HIDDEN[platform_key]
                self.assertTrue(any("webview.platforms" in name for name in hidden))

    def test_the_freeze_never_drops_a_module_the_application_imports(self):
        """An exclusion that names something PRISM actually uses produces a
        build that only fails once a user clicks the wrong button."""
        sources = "\n".join(
            (ROOT / name).read_text(encoding="utf-8")
            for name in ("control_center/app.py", "control_center/desktop.py",
                         "control_center/workspace.py", "control_center/updater.py")
        )
        for module in recipe.EXCLUDED:
            with self.subTest(module=module):
                self.assertNotIn(f"\nimport {module}", sources)
                self.assertNotIn(f"\nfrom {module}", sources)

    def test_the_debian_package_declares_the_web_view_it_does_not_carry(self):
        depends = " ".join(recipe.DEB_DEPENDS)
        self.assertIn("webkit2", depends)
        self.assertIn("python3-gi", depends)
        self.assertIn("libgtk-3-0", depends)

    def test_the_desktop_entry_is_complete_enough_to_appear_in_a_menu(self):
        entry = recipe.DESKTOP_ENTRY
        self.assertTrue(entry.startswith("[Desktop Entry]"))
        for key in ("Type=Application", "Name=PRISM", "Exec=prism", "Icon=prism",
                    "Terminal=false", "Categories="):
            with self.subTest(key=key):
                self.assertIn(key, entry)

    def test_the_mac_bundle_is_described_as_a_dark_application(self):
        plist = recipe.MACOS_PLIST
        self.assertEqual(plist["CFBundleName"], "PRISM")
        self.assertEqual(plist["CFBundleIdentifier"], recipe.BUNDLE_ID)
        self.assertIs(plist["NSRequiresAquaSystemAppearance"], False)
        self.assertIs(plist["NSHighResolutionCapable"], True)
        self.assertEqual(plist["CFBundleIconFile"], "PRISM.icns")

    def test_no_exclusion_is_one_that_breaks_pyinstaller_itself(self):
        """Trimming distutils or setuptools does not make a smaller build, it
        makes no build: PyInstaller's own distutils hook aliases one onto the
        other and raises if either end has been excluded. Only the Windows
        runner reaches that hook, so nothing local catches it."""
        overlap = set(recipe.EXCLUDED) & set(recipe.UNSAFE_TO_EXCLUDE)
        self.assertEqual(overlap, set(), f"these exclusions break the build: {sorted(overlap)}")

    def test_no_exclusion_collides_with_a_pyinstaller_aliasing_hook(self):
        """The live version of the check above.

        PyInstaller aliases modules during analysis through the hooks in
        pre_safe_import_module, and excluding either end of an alias kills the
        build. Reading the installed hook directory catches a new collision
        the day PyInstaller adds one, rather than the next time somebody
        watches a Windows job fail. Skipped where PyInstaller is absent, which
        is the whole point of the standard-library test workflow; the release
        workflow installs it and runs this before it builds anything.
        """
        try:
            import PyInstaller.hooks.pre_safe_import_module as hooks
        except ImportError:
            self.skipTest("PyInstaller is not installed in this environment")

        names = {entry[5:-3] for entry in os.listdir(os.path.dirname(hooks.__file__))
                 if entry.startswith("hook-") and entry.endswith(".py")}
        self.assertTrue(names, "no pre-safe-import hooks were found to check against")
        # A hook on gi.repository.Gtk also makes excluding gi dangerous.
        risky = names | {name.split(".")[0] for name in names}
        collisions = sorted(set(recipe.EXCLUDED) & risky)
        self.assertEqual(collisions, [], f"these exclusions break PyInstaller: {collisions}")

    def test_the_windows_version_resource_makes_its_own_directory(self):
        """It is written before PyInstaller runs, which is what creates the
        work folder, and only on Windows. Depending on a caller to have made
        the folder first fails on exactly one of the four runners, which is
        the most expensive place to find out."""
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "does" / "not" / "exist" / "version.txt"
            recipe.windows_version_resource(destination)
            written = destination.read_text(encoding="utf-8")
        self.assertIn("VSVersionInfo", written)
        self.assertIn(f"'{ws.APP_VERSION}'", written)
        self.assertIn("'PRISM.exe'", written)

    def test_the_windows_freeze_command_can_be_assembled_from_any_platform(self):
        """The Windows-only branch of freeze() writes a file into the work
        folder, and nothing else on this machine will ever run it. Building
        the command with PyInstaller stubbed out exercises that branch here
        instead of on the one runner where the mistake is expensive."""
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp) / "never-created"
            captured = {}

            with mock.patch.object(recipe, "system", return_value="win32"), \
                    mock.patch.object(recipe, "WORK", work), \
                    mock.patch.object(recipe, "run",
                                      lambda argv, **kw: captured.update(argv=argv)):
                recipe.freeze(onefile=True)

            argv = captured["argv"]
            self.assertIn("--version-file", argv)
            resource = Path(argv[argv.index("--version-file") + 1])
            self.assertTrue(resource.is_file(), "the version resource was never written")
            self.assertIn("--onefile", argv)
            self.assertIn("--windowed", argv)
            for module in recipe.HIDDEN["win32"]:
                with self.subTest(module=module):
                    self.assertIn(module, argv)

    def test_the_release_workflow_uploads_what_the_build_gathered(self):
        """Naming artifacts in build.py and again in the workflow is how the
        first release run tried to copy dist/PRISM.exe on macOS. Only one of
        the two is allowed to know a filename."""
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertEqual(recipe.RELEASE.parent, recipe.DIST)
        self.assertIn("path: dist/release/*", workflow)
        for invented in ("dist/PRISM.exe", "dist/*.dmg", "dist/*.deb",
                         "dist/*.tar.gz", "dist/*.zip"):
            with self.subTest(invented=invented):
                self.assertNotIn(invented, workflow)

    def test_every_platform_reports_what_it_built_and_what_to_publish(self):
        """main() copies result["deliverables"] and prints result["built"], so
        a finish function returning the old flat list would fail at the very
        end of a long build with a confusing error."""
        tree = ast.parse((ROOT / "appbuild/build.py").read_text(encoding="utf-8"))
        for name in ("finish_macos", "finish_windows", "finish_linux"):
            function = next(node for node in tree.body
                            if isinstance(node, ast.FunctionDef) and node.name == name)
            returns = [node for node in ast.walk(function) if isinstance(node, ast.Return)]
            with self.subTest(function=name):
                self.assertTrue(returns, f"{name} returns nothing")
                for returned in returns:
                    self.assertIsInstance(returned.value, ast.Dict,
                                          f"{name} returns something other than a mapping")
                    keys = {key.value for key in returned.value.keys}
                    self.assertEqual(keys, {"built", "deliverables"},
                                     f"{name} returns {sorted(keys)}")

    def test_the_runner_images_are_ones_github_still_offers(self):
        """macos-13 was retired in December 2025 and its jobs never start."""
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        for retired in ("macos-13", "macos-12", "macos-11",
                        "ubuntu-18.04", "ubuntu-20.04", "windows-2019"):
            with self.subTest(retired=retired):
                self.assertNotIn(f"os: {retired}", workflow)
        self.assertIn("os: macos-15-intel", workflow)

    def test_the_guide_and_the_release_notes_name_files_a_build_can_emit(self):
        """A guide that names a download nobody can find is worse than one
        that names none, and nothing else in the repository would notice."""
        self.assertEqual(recipe.macos_label("arm64"), "AppleSilicon")
        self.assertEqual(recipe.macos_label("x86_64"), "Intel")

        guide = (ROOT / "START HERE.md").read_text(encoding="utf-8")
        notes = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        for label in ("AppleSilicon", "Intel"):
            with self.subTest(label=label):
                self.assertIn(label, guide)
                self.assertIn(f"macOS-{label}.dmg", notes)

        # Anything either surface names outright has to be a name a build can
        # actually produce. The Windows binary is the only fixed one.
        for surface, text in (("guide", guide), ("notes", notes)):
            for named in re.findall(r"`(PRISM[^`]*\.(?:dmg|exe))`", text):
                with self.subTest(surface=surface, named=named):
                    self.assertTrue(
                        named == "PRISM.exe" or named.startswith("PRISM-"),
                        f"{named} is not a name appbuild/build.py emits",
                    )

    def test_running_the_release_workflow_by_hand_publishes_nothing(self):
        """The rehearsal before a tag. If a hand run could publish, the first
        one would burn a version number on a build nobody had seen yet."""
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("if: startsWith(github.ref, 'refs/tags/v')", workflow)
        # A declared input that nothing reads reads as a control that works.
        declared = set(re.findall(r"^      (\w+):\n        description:", workflow, re.M))
        self.assertEqual(declared, set(), f"unused workflow inputs: {declared}")

    def test_architecture_names_are_normalised_for_each_kind_of_package(self):
        for reported, expected in (("AMD64", "x86_64"), ("x86_64", "x86_64"),
                                   ("arm64", "arm64"), ("aarch64", "arm64")):
            with self.subTest(reported=reported):
                with mock.patch.object(recipe.platform, "machine", return_value=reported):
                    self.assertEqual(recipe.machine(), expected)
        with mock.patch.object(recipe.platform, "machine", return_value="x86_64"):
            self.assertEqual(recipe.debian_arch(), "amd64")


class WorkflowTests(unittest.TestCase):
    def test_a_runner_is_configured_for_every_download_the_release_offers(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        for runner in ("macos-14", "macos-13", "windows-latest", "ubuntu-22.04"):
            with self.subTest(runner=runner):
                self.assertIn(runner, workflow)
        # The build refuses a payload that disagrees with the manifest, so the
        # manifest has to be committed rather than generated during a release.
        self.assertNotIn("build_manifest.py", workflow)
        self.assertIn("appbuild/build.py", workflow)
        self.assertIn("SHA256SUMS.txt", workflow)

    def test_the_tests_run_on_a_bare_interpreter(self):
        """PRISM's helper is standard library only. A pip install in the test
        workflow would let that quietly stop being true."""
        workflow = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")
        instructions = "\n".join(line for line in workflow.splitlines()
                                 if not line.lstrip().startswith("#"))
        self.assertNotIn("pip install", instructions)
        self.assertIn("appbuild.test_appbuild", workflow)
        self.assertIn("control_center.test_control_center", workflow)

    def test_the_build_tooling_cannot_be_shadowed_by_an_installed_package(self):
        """`packaging` is a real distribution that setuptools drags in, and a
        namespace directory of the same name loses to it on any machine that
        has one. This folder is named and shaped so it cannot happen again."""
        self.assertTrue((ROOT / "appbuild/__init__.py").is_file(),
                        "a regular package wins the import; a namespace one does not")
        self.assertFalse((ROOT / "packaging").exists())


class CopyTests(unittest.TestCase):
    def test_nothing_a_user_reads_carries_an_em_or_en_dash(self):
        for relative in ("appbuild/README.md",):
            with self.subTest(relative=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertNotIn("—", text)
                self.assertNotIn("–", text)

    def test_a_browser_tab_is_told_the_application_exists(self):
        """Updating the toolkit patches source files into a folder. It cannot
        turn a launcher script into a Mac app, so someone who updates in place
        and keeps double clicking the launcher has no way to learn that the
        application shipped unless the dashboard says so."""
        page = (ROOT / "control_center/static/index.html").read_text(encoding="utf-8")
        script = (ROOT / "control_center/static/app.js").read_text(encoding="utf-8")
        self.assertIn('id="app-offer"', page)
        self.assertIn('id="app-offer-link"', page)
        self.assertIn("renderApplicationOffer", script)
        # Gated on the shell, or the application would advertise itself to
        # someone already looking at it.
        self.assertIn('status.shell?.mode !== "desktop"', script)
        # The address comes from the updater, so there is one place that
        # knows where releases live.
        self.assertIn("status.releases_url", script)
        updater = (ROOT / "control_center/updater.py").read_text(encoding="utf-8")
        self.assertIn("RELEASES_PAGE", updater)
        self.assertIn("RELEASES_PAGE", (ROOT / "control_center/app.py").read_text(encoding="utf-8"))

    def test_the_retired_name_is_gone_from_every_surface_a_user_sees(self):
        for relative in ("START HERE.md", "README.md",
                         "control_center/static/index.html",
                         "control_center/static/app.js",
                         "control_center/README.md",
                         # Terminal output is a surface too. These four lines
                         # kept the retired label for two releases after the
                         # rename because nothing looked at them.
                         "PRISM - Mac.command",
                         "PRISM - Windows.cmd",
                         "control_center/launch.sh"):
            with self.subTest(relative=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertNotIn("Control Center", text)


if __name__ == "__main__":
    unittest.main()
