#!/usr/bin/env python3
"""Regression tests for Prism's data-preserving updater and deck review."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock
from http.server import ThreadingHTTPServer


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "control_center"))

import app
import state_io
import updater
import workspace


def copy_project(destination):
    shutil.copytree(
        ROOT, destination, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            ".git", "__pycache__", ".pipeline_backups", "build", "dist",
            "anki_pipeline_selftest_*", "anki_optimizer_update_*",
            "selftest_*", "cov_*", "tmp*",
        ),
    )


def used_state(root):
    template = json.loads((root / "scripts/project_state.template.json").read_text(encoding="utf-8"))
    template.update({
        "run_count": 17,
        "queue_built": True,
        "paths": {
            "project_root": str(root),
            "source_dir": str(root / "Source Files"),
            "deck_dir": str(root / "Anki Decks"),
            "completed": str(root / "COMPLETED"),
            "archive_dir": str(root / "Old Anki Decks and Files"),
        },
        "modules": [
            {"name": "Airway", "deck_id": 101, "status": "verified",
             "cards_before": 90, "cards_after": 112},
            {"name": "Regional", "deck_id": 102, "status": "built-unverified",
             "cards_before": 75, "cards_after": 81},
        ],
        "pending_modules": [
            {"name": "Pharmacology", "deck_id": 103, "status": "pending",
             "apkg": str(root / "Anki Decks/Pharmacology.apkg")},
        ],
        "session_log": [
            {"date": "2026-08-01", "module": "Airway", "action": "BUILD"},
            {"date": "2026-08-02", "module": "Airway", "action": "VERIFY"},
        ],
    })
    return template


def make_used_fixture(parent, name="used-project"):
    root = Path(parent) / name
    copy_project(root)
    (root / "scripts/project_state.json").write_text(
        json.dumps(used_state(root), indent=2) + "\n", encoding="utf-8",
    )
    (root / "PROFILE.md").write_text("# MY PRIVATE PROFILE\n\nKeep review history.\n", encoding="utf-8")
    (root / "USER_PROMPTS.md").write_text(
        "# MY PRIVATE ADD-ONS\n\n## Every run\n\nUse my preferred abbreviations.\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("old publisher file with a known bug\n", encoding="utf-8")
    (root / "Source Files/Airway").mkdir(parents=True, exist_ok=True)
    (root / "Source Files/Airway/lecture.txt").write_bytes(b"private course text\n")
    (root / "Anki Decks").mkdir(parents=True, exist_ok=True)
    (root / "Anki Decks/Pharmacology.apkg").write_bytes(b"private staged deck")
    audit = root / "COMPLETED/Airway/audit"
    audit.mkdir(parents=True, exist_ok=True)
    (audit / "changelog.json").write_text(
        json.dumps([["DEMOTE", 44, "low-yield detail"]]) + "\n", encoding="utf-8",
    )
    (audit / "VERIFY_REPORT_2026-08-02.md").write_text(
        "# Verification\n\n## JUDGEMENT CALLS\n\n- Confirm the difficult airway wording.\n",
        encoding="utf-8",
    )
    (root / "COMPLETED/Airway/Airway (FINAL).apkg").write_bytes(b"private completed deck")
    (root / "COMPLETED/Airway/Airway (NOTES).docx").write_bytes(b"private notes document")
    finished = root / "work/Airway"
    finished.mkdir(parents=True, exist_ok=True)
    (finished / "progress.json").write_text('{"complete": true}\n', encoding="utf-8")
    return root


def private_snapshot(root):
    paths = [
        "scripts/project_state.json", "PROFILE.md", "USER_PROMPTS.md",
        "Source Files/Airway/lecture.txt", "Anki Decks/Pharmacology.apkg",
        "COMPLETED/Airway/audit/changelog.json",
        "COMPLETED/Airway/audit/VERIFY_REPORT_2026-08-02.md",
        "COMPLETED/Airway/Airway (FINAL).apkg",
        "COMPLETED/Airway/Airway (NOTES).docx", "work/Airway/progress.json",
    ]
    return {relative: (root / relative).read_bytes() for relative in paths}


class PackagingTests(unittest.TestCase):
    def test_root_has_only_the_beginner_facing_launchers_and_start_guide(self):
        expected = (
            "START HERE.md",
            "PRISM - Mac.command",
            "PRISM - Windows.cmd",
        )
        for relative in expected:
            with self.subTest(relative=relative):
                self.assertTrue((ROOT / relative).is_file())
        for retired in (
            "START_HERE.md", "OPEN_CONTROL_CENTER.command",
            "OPEN_CONTROL_CENTER.cmd", "open_control_center.sh",
            "setup.sh", "setup.ps1", "PROFILE.template.md",
            "Prism Control Center - Mac.command",
            "Prism Control Center - Windows.cmd",
        ):
            with self.subTest(retired=retired):
                self.assertFalse((ROOT / retired).exists())
        if os.name != "nt":
            self.assertTrue(os.access(ROOT / "PRISM - Mac.command", os.X_OK))
            self.assertTrue(os.access(ROOT / "control_center/launch.sh", os.X_OK))
            self.assertTrue(os.access(ROOT / "control_center/install/setup.sh", os.X_OK))

        page = (ROOT / "control_center/static/index.html").read_text(encoding="utf-8")
        self.assertNotIn("&gt;_", page)
        self.assertIn('id="reset-preferences"', page)
        self.assertIn('data-open-staging="source"', page)
        self.assertIn('data-open-staging="deck"', page)
        self.assertIn('data-view="guide"', page)
        self.assertIn('id="prompt-card-grid"', page)
        self.assertIn('id="prompt-deck-select"', page)
        self.assertIn('id="open-context-review"', page)
        script = (ROOT / "control_center/static/app.js").read_text(encoding="utf-8")
        self.assertIn('navigator.clipboard', script)
        self.assertIn('function guidePromptMeta', script)
        self.assertIn('function contextualizeGuidePrompt', script)
        self.assertIn('function patchPrompt', script)
        self.assertIn('className = "judgement-feedback"', script)
        self.assertIn('appendGuideInline(item, lines[index].trim());', script)

    def test_every_dom_hook_the_dashboard_queries_exists_in_the_page(self):
        """A redesign that renames or drops an element must not silently
        break the script that drives it."""
        script = (ROOT / "control_center/static/app.js").read_text(encoding="utf-8")
        page = (ROOT / "control_center/static/index.html").read_text(encoding="utf-8")
        queried = sorted(set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', script)))
        self.assertGreater(len(queried), 30, "the hook scan found suspiciously few selectors")
        present = set(re.findall(r'id="([A-Za-z0-9_-]+)"', page))
        missing = [name for name in queried if name not in present]
        self.assertEqual(missing, [], f"app.js queries ids the page does not define: {missing}")

    def test_field_renderer_is_wired_up_and_every_scene_names_a_real_body(self):
        page = (ROOT / "control_center/static/index.html").read_text(encoding="utf-8")
        self.assertIn('src="/static/prism-field.js"', page)
        self.assertIn('id="prism-field"', page)
        self.assertLess(
            page.index('src="/static/prism-field.js"'),
            page.index('src="/static/app.js"'),
            "the field renderer must be defined before app.js mounts it",
        )
        field = (ROOT / "control_center/static/prism-field.js").read_text(encoding="utf-8")
        script = (ROOT / "control_center/static/app.js").read_text(encoding="utf-8")
        declared = set(re.findall(r"^\s+(\w+): \d+,", field, re.M))
        used = set(re.findall(r'body: "(\w+)"', script))
        self.assertTrue(used, "no field scenes were found in app.js")
        self.assertEqual(used - declared, set(), "a view asks for a body the renderer does not define")
        # Without a working GPU path the page must still be styled, not blank.
        self.assertIn("field-unavailable", field)
        self.assertIn("field-unavailable", (ROOT / "control_center/static/app.css").read_text(encoding="utf-8"))

    def test_every_uncopyable_prompt_can_explain_itself(self):
        """A disabled copy button must never be the only signal. Both the
        library and the inline guide prompts attach a visible reason."""
        script = (ROOT / "control_center/static/app.js").read_text(encoding="utf-8")
        self.assertIn("function promptRequirement", script)
        self.assertIn("function requirementNote", script)
        # Attached in all three places a copy button can appear.
        self.assertIn("const note = requirementNote(prompt.code);", script)
        self.assertIn("const note = promptMeta ? requirementNote(code) : null;", script)
        self.assertIn('requirementNote("", {requiresDeck: false, requiresFeedback: true})', script)
        # And kept current as the deck selection and correction text change.
        self.assertIn("$$('[data-prompt-requirement]')", script)
        self.assertNotIn("data-patch-hint", script)
        self.assertNotIn("patchHint", script)
        styles = (ROOT / "control_center/static/app.css").read_text(encoding="utf-8")
        for state in ("blocked", "ready"):
            with self.subTest(state=state):
                self.assertIn(f'.prompt-requirement[data-state="{state}"]', styles)

    def test_home_reads_as_one_descending_sequence(self):
        """State, then what is waiting on you, then what to add, then how
        the machine is. The module name gates staging, so it sits with the
        drop zones rather than opposite a heading."""
        page = (ROOT / "control_center/static/index.html").read_text(encoding="utf-8")
        order = [
            'id="metric-review"',
            'id="next-review-content"',
            'id="module-name"',
            'id="source-drop"',
            'id="health-pills"',
        ]
        positions = [page.index(marker) for marker in order]
        self.assertEqual(positions, sorted(positions), f"Home is out of order: {order}")

    def test_the_field_gives_way_to_the_text_as_the_page_scrolls(self):
        field = (ROOT / "control_center/static/prism-field.js").read_text(encoding="utf-8")
        script = (ROOT / "control_center/static/app.js").read_text(encoding="utf-8")
        self.assertIn("setDrift(progress)", field)
        self.assertIn('window.addEventListener("scroll", syncFieldDrift', script)
        # Home is bare text on the void below the fold, so it must recede
        # further than the views whose content sits on filled surfaces.
        recessions = dict(re.findall(r"(\w+): \{body:.*?recede: ([\d.]+)\}", script))
        self.assertEqual(len(recessions), 6, f"expected a recession per view, got {recessions}")
        self.assertEqual(float(recessions["home"]), 1.0)
        for view in ("welcome", "guide", "decks", "preferences", "updates"):
            with self.subTest(view=view):
                self.assertLess(float(recessions[view]), 1.0)

    def test_user_facing_surfaces_carry_no_em_or_en_dashes(self):
        for relative in (
            "START HERE.md",
            "control_center/templates/PROFILE.template.md",
            "control_center/static/index.html",
            "control_center/static/app.js",
            "control_center/static/app.css",
            "control_center/static/prism-field.js",
            "control_center/app.py",
            "control_center/desktop.py",
            "control_center/workspace.py",
        ):
            with self.subTest(relative=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertNotIn("—", text)
                self.assertNotIn("–", text)

    def test_the_page_carries_the_placeholders_the_server_fills_in(self):
        """The window tells the page which chrome it is living in, and the
        page has to have somewhere to put that before it is any use."""
        page = (ROOT / "control_center/static/index.html").read_text(encoding="utf-8")
        self.assertIn('class="__BODY_CLASS__"', page)
        self.assertIn('content="__APP_VERSION__"', page)
        self.assertIn('content="__CONTROL_TOKEN__"', page)
        self.assertIn("<title>PRISM</title>", page)
        styles = (ROOT / "control_center/static/app.css").read_text(encoding="utf-8")
        for rule in ("body.chrome-inset", "body.needs-workspace .app-shell"):
            with self.subTest(rule=rule):
                self.assertIn(rule, styles)

    def test_first_run_offers_both_ways_into_a_workspace(self):
        page = (ROOT / "control_center/static/index.html").read_text(encoding="utf-8")
        for hook in ('id="welcome"', 'id="welcome-create"', 'id="welcome-open"',
                     'id="welcome-browse"', 'id="welcome-path"', 'id="welcome-recent"'):
            with self.subTest(hook=hook):
                self.assertIn(hook, page)
        script = (ROOT / "control_center/static/app.js").read_text(encoding="utf-8")
        self.assertIn("window.prismShell", script)
        # The window drives the page through exactly these three, so a rename
        # here has to be a deliberate one.
        for member in ("showView(", "toast(", "refresh("):
            with self.subTest(member=member):
                self.assertIn(member, script)
        self.assertIn('"/api/workspace/create"', script)
        self.assertIn('"/api/workspace/open"', script)

    def test_guide_leads_with_updates_and_uses_contextual_module_tokens(self):
        guide = (ROOT / "START HERE.md").read_text(encoding="utf-8")
        update = guide.index("## PART 1: KEEP THE TOOLKIT UPDATED")
        setup = guide.index("## PART 2: ONE-TIME SETUP")
        stage = guide.index("## PART 3: STAGE YOUR MODULES")
        run = guide.index("## PART 4: RUN THE PIPELINE")
        review = guide.index("## PART 5: REVIEW AND CORRECT")
        self.assertLess(update, setup)
        self.assertLess(setup, stage)
        self.assertLess(stage, run)
        self.assertLess(run, review)
        self.assertIn('verify_deck.py --pass "<module>"', guide)
        self.assertIn('PROMPT_patch.md and execute it for "<module>"', guide)
        self.assertNotIn('verify_deck.py --pass "Airway Anatomy and Management"', guide)

    def test_automatic_sections_copy_complete_platform_specific_scheduler_prompts(self):
        guide = (ROOT / "START HERE.md").read_text(encoding="utf-8")
        claude = guide.split("#### Schedule Claude Cowork", 1)[1].split(
            "#### Schedule ChatGPT Codex", 1)[0]
        chatgpt = guide.split("#### Schedule ChatGPT Codex", 1)[1].split(
            "### Path B: manual mode", 1)[0]

        for section in (claude, chatgpt):
            self.assertIn("Name: Auto Anki Optimize", section)
            self.assertIn("Description:", section)
            self.assertIn("Instructions for every run:", section)
            self.assertIn("Read scripts/PROMPT_auto.md and execute it.", section)
            self.assertIn("Run once per hour for the next 8 hours", section)
        self.assertIn("Create a scheduled Cowork task", claude)
        self.assertIn("Working folder: This project folder", claude)
        self.assertIn("Approval mode: Automatically approve", claude)
        self.assertIn("Create a standalone scheduled task", chatgpt)
        self.assertIn("Project: This Local project", chatgpt)
        self.assertIn("Do not use an isolated worktree", chatgpt)

        script = (ROOT / "control_center/static/app.js").read_text(encoding="utf-8")
        self.assertIn('["Claude scheduler", "Automate eight hours in Cowork"]', script)
        self.assertIn('["ChatGPT scheduler", "Automate eight hours in Codex"]', script)

    def test_guide_toc_captures_each_chapter_target(self):
        script = (ROOT / "control_center/static/app.js").read_text(encoding="utf-8")
        self.assertIn('const targetChapter = chapter;', script)
        self.assertIn('() => targetChapter.scrollIntoView', script)
        self.assertNotIn('() => chapter.scrollIntoView', script)


class StateTests(unittest.TestCase):
    def test_sparse_state_migration_adds_defaults_without_rewriting_history(self):
        before = {"project": "used", "run_count": 9,
                  "session_log": [{"event": "keep me"}],
                  "modules": [{"name": "Airway", "status": "verified"}]}
        migrated, changes = state_io.migrate_state(before)
        state_io.assert_preserved(before, migrated)
        self.assertIn("schema_version", changes)
        self.assertEqual(migrated["run_count"], 9)
        self.assertEqual(migrated["modules"][0]["status"], "verified")
        self.assertEqual(migrated["session_log"], [{"event": "keep me"}])
        self.assertNotIn("schema_version", before)

    def test_atomic_save_leaves_a_recoverable_previous_copy(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as temp:
            path = Path(temp) / "state.json"
            first, _ = state_io.migrate_state({"project": "first"})
            second = copy.deepcopy(first)
            second["project"] = "second"
            state_io.save_state(first, path, backup=False)
            state_io.save_state(second, path, backup=True)
            self.assertEqual(json.loads(path.read_text())["project"], "second")
            self.assertEqual(json.loads((Path(str(path) + ".bak")).read_text())["project"], "first")


class ReviewTests(unittest.TestCase):
    def test_deck_review_finds_verified_and_judgement_calls(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as temp:
            root = make_used_fixture(temp)
            rows = app.deck_rows(root)
            airway = next(row for row in rows if row["name"] == "Airway")
            regional = next(row for row in rows if row["name"] == "Regional")
            self.assertTrue(airway["verified"])
            self.assertEqual(airway["judgement_count"], 1)
            self.assertIn("difficult airway", airway["judgements"][0])
            self.assertFalse(regional["verified"])

    def test_module_names_reject_path_characters(self):
        for unsafe in ("../escape", "folder/name", "bad\\name", "bad:name", ""):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                app.clean_module_name(unsafe)
        self.assertEqual(app.clean_module_name("  Airway   Anatomy  "), "Airway Anatomy")

    def test_staging_destinations_use_the_exact_project_subfolders(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as temp:
            root = make_used_fixture(temp, "staging-project")
            application = app.Application(root)
            source = application.staging_directory("source", "Cardiac Basics")
            deck = application.staging_directory("deck", "ignored module name")
            self.assertEqual(source, root / "Source Files/Cardiac Basics")
            self.assertEqual(deck, root / "Anki Decks")
            self.assertTrue(source.is_dir())
            self.assertTrue(deck.is_dir())
            with self.assertRaises(ValueError):
                application.staging_directory("source", "../outside")


class HttpTests(unittest.TestCase):
    def test_loopback_api_requires_token_and_stages_a_source_file(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as temp:
            root = make_used_fixture(temp, "api-project")
            application = app.Application(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
            server.application = application
            server.verbose = False
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                page = urllib.request.urlopen(base + "/", timeout=5).read().decode("utf-8")
                self.assertIn(application.token, page)
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(base + "/api/status", timeout=5)
                self.assertEqual(denied.exception.code, 403)

                request = urllib.request.Request(
                    base + "/api/status", headers={"X-Control-Token": application.token})
                status = json.load(urllib.request.urlopen(request, timeout=5))
                self.assertEqual(status["pipeline"]["verified"], 1)

                guide_request = urllib.request.Request(
                    base + "/api/guide", headers={"X-Control-Token": application.token})
                guide = json.load(urllib.request.urlopen(guide_request, timeout=5))
                self.assertEqual(guide["file"], "START HERE.md")
                self.assertEqual(
                    guide["markdown"],
                    (root / "START HERE.md").read_text(encoding="utf-8"),
                )
                self.assertIn("Read scripts/PROMPT_auto.md and execute it.", guide["markdown"])

                payload = b"private staged source\n"
                target = (base + "/api/stage?kind=source&module=Cardiac%20Basics"
                          "&name=lecture.txt")
                upload = urllib.request.Request(
                    target, data=payload, method="POST",
                    headers={"X-Control-Token": application.token,
                             "Content-Type": "application/octet-stream"},
                )
                result = json.load(urllib.request.urlopen(upload, timeout=5))
                self.assertTrue(result["ok"])
                self.assertEqual(
                    (root / "Source Files/Cardiac Basics/lecture.txt").read_bytes(), payload)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_without_a_workspace_the_api_says_so_instead_of_failing(self):
        """A downloaded PRISM starts with no project at all. Every endpoint
        that needs one has to refuse politely, and the two that do not, status
        and workspace, have to keep answering so first run can be drawn."""
        application = app.Application(None)
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        server.application = application
        server.verbose = False
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def get(path):
            request = urllib.request.Request(
                base + path, headers={"X-Control-Token": application.token})
            return json.load(urllib.request.urlopen(request, timeout=5))

        try:
            status = get("/api/status")
            self.assertFalse(status["ready"])
            self.assertEqual(status["project"], "")
            self.assertEqual(status["pipeline"]["modules"], 0)
            self.assertEqual(status["app_version"], workspace.APP_VERSION)

            options = get("/api/workspace")
            self.assertFalse(options["ready"])
            self.assertTrue(options["suggested"])
            self.assertTrue(options["can_create"], "the checkout is its own payload")

            for path in ("/api/decks", "/api/guide", "/api/preferences"):
                with self.subTest(path=path):
                    with self.assertRaises(urllib.error.HTTPError) as refused:
                        get(path)
                    self.assertEqual(refused.exception.code, 400)
                    detail = json.loads(refused.exception.read())
                    self.assertIn("workspace", detail["error"].lower())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_a_request_addressed_to_another_name_is_refused(self):
        """The socket is on the loopback interface, but a page elsewhere can
        still aim a browser at a name that resolves to it. The token would
        stop the write; this stops the request reaching a handler at all."""
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as temp:
            root = make_used_fixture(temp, "origin-project")
            application = app.Application(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
            server.application = application
            server.verbose = False
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                for headers in (
                    {"Host": "prism.example.com"},
                    {"Origin": "https://prism.example.com"},
                ):
                    with self.subTest(headers=headers):
                        request = urllib.request.Request(
                            base + "/api/status",
                            headers={"X-Control-Token": application.token, **headers},
                        )
                        with self.assertRaises(urllib.error.HTTPError) as refused:
                            urllib.request.urlopen(request, timeout=5)
                        self.assertEqual(refused.exception.code, 403)

                allowed = urllib.request.Request(
                    base + "/api/status",
                    headers={"X-Control-Token": application.token,
                             "Origin": f"http://localhost:{server.server_address[1]}"},
                )
                self.assertTrue(json.load(urllib.request.urlopen(allowed, timeout=5))["ok"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_folder_open_and_dual_confirm_preference_reset(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as temp:
            root = make_used_fixture(temp, "preference-project")
            original_profile = (root / "PROFILE.md").read_bytes()
            original_prompts = (root / "USER_PROMPTS.md").read_bytes()
            application = app.Application(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
            server.application = application
            server.verbose = False
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"

            def post(path, body):
                request = urllib.request.Request(
                    base + path, data=json.dumps(body).encode("utf-8"), method="POST",
                    headers={"X-Control-Token": application.token,
                             "Content-Type": "application/json"},
                )
                return json.load(urllib.request.urlopen(request, timeout=5))

            try:
                with mock.patch.object(app, "open_native") as opener:
                    opened = post("/api/staging/open", {
                        "kind": "source", "module": "Cardiac Basics",
                    })
                    expected = root / "Source Files/Cardiac Basics"
                    self.assertEqual(opened["path"], str(expected))
                    opener.assert_called_once_with(expected)

                with self.assertRaises(urllib.error.HTTPError) as denied:
                    post("/api/preferences/reset", {"confirmation": "yes"})
                self.assertEqual(denied.exception.code, 400)
                self.assertEqual((root / "PROFILE.md").read_bytes(), original_profile)

                reset = post("/api/preferences/reset", {
                    "confirmation": app.RESET_CONFIRMATION,
                })
                defaults = (root / "control_center/templates/PROFILE.template.md").read_text(
                    encoding="utf-8")
                self.assertEqual(reset["profile"], defaults)
                self.assertEqual(reset["prompts"], app.USER_PROMPTS_TEMPLATE)
                self.assertEqual((root / "PROFILE.md.bak").read_bytes(), original_profile)
                self.assertEqual((root / "USER_PROMPTS.md.bak").read_bytes(), original_prompts)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


class UpdateTests(unittest.TestCase):
    def test_successful_update_preserves_used_project_bytes_and_statuses(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as temp:
            root = make_used_fixture(temp)
            retired = root / "OLD_CONTROL_CENTER.command"
            retired.write_bytes(b"old publisher launcher\n")
            old_manifest_path = root / "scripts/UPDATE_MANIFEST.json"
            old_manifest = json.loads(old_manifest_path.read_text(encoding="utf-8"))
            old_manifest["files"][retired.name] = hashlib.sha256(
                retired.read_bytes()).hexdigest()
            old_manifest_path.write_text(
                json.dumps(old_manifest, indent=2) + "\n", encoding="utf-8")
            before = private_snapshot(root)
            result = updater.install_update(root, staged_directory=ROOT)
            after = private_snapshot(root)

            # State may gain publisher knowledge through a migration, so compare the
            # preservation contract semantically and every other private file bytewise.
            state_io.assert_preserved(json.loads(before.pop("scripts/project_state.json")),
                                      json.loads(after.pop("scripts/project_state.json")))
            self.assertEqual(before, after)
            self.assertEqual(result["before"], result["after"])
            self.assertNotIn("known bug", (root / "README.md").read_text(encoding="utf-8"))
            backup = Path(result["backup"])
            self.assertIn("known bug", (backup / "README.md").read_text(encoding="utf-8"))
            self.assertFalse(retired.exists())
            self.assertEqual((backup / retired.name).read_bytes(), b"old publisher launcher\n")
            expected_release = json.loads(
                (ROOT / "scripts/UPDATE_MANIFEST.json").read_text(encoding="utf-8")
            )["release_version"]
            self.assertEqual(
                json.loads((root / "scripts/UPDATE_MANIFEST.json").read_text())["release_version"],
                expected_release,
            )
            self.assertFalse((root / "scripts/.pipeline.lock").exists())

    def test_failed_first_update_rolls_back_legacy_toolkit_and_private_data(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as temp:
            root = make_used_fixture(temp, "rollback-project")
            # Simulate an installation from before Prism existed: publisher files
            # are present, but there is no old update manifest to name them.
            (root / "scripts/UPDATE_MANIFEST.json").unlink()
            staged = Path(temp) / "bad-release"
            copy_project(staged)
            bad_check = staged / "scripts/check_version.py"
            bad_check.write_text("raise SystemExit(7)\n", encoding="utf-8")
            manifest_path = staged / "scripts/UPDATE_MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"]["scripts/check_version.py"] = hashlib.sha256(
                bad_check.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

            private_before = private_snapshot(root)
            toolkit_before = {
                path: (root / path).read_bytes()
                for path in ("README.md", "scripts/check_version.py")
            }
            with mock.patch.object(updater, "_run_staged_selftest", return_value=999):
                with self.assertRaises(updater.UpdateError):
                    updater.install_update(root, staged_directory=staged)

            self.assertEqual(private_snapshot(root), private_before)
            for path, content in toolkit_before.items():
                self.assertEqual((root / path).read_bytes(), content, path)
            self.assertFalse((root / "scripts/UPDATE_MANIFEST.json").exists())
            self.assertFalse((root / "scripts/.pipeline.lock").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
