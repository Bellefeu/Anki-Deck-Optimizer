#!/usr/bin/env python3
"""The local helper behind PRISM, the dashboard for the Anki LLM Optimizer.

It serves the interface and the project API on the loopback interface only,
with a random token on every request. ``desktop.py`` hosts it inside the
application window; running this file directly opens it in a browser instead.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import workspace as ws

# In a checkout the payload is the checkout itself, so this is the repository
# root either way; in a build it is the toolkit carried inside the app.
ROOT_FROM_FILE = ws.payload_root()
SCRIPTS = ROOT_FROM_FILE / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from state_io import atomic_write_bytes, load_state, summary as state_summary

from updater import (RELEASES_PAGE, UpdateError, check_latest, current_version,
                     install_update)


def _static_root():
    if ws.frozen():
        return ws.resource_root() / "control_center" / "static"
    return Path(__file__).resolve().parent / "static"


STATIC = _static_root()
LOOPBACK_NAMES = {"127.0.0.1", "localhost", "::1", ""}
SOURCE_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff",
    ".webp", ".txt", ".md", ".csv", ".rtf",
}
USER_PROMPTS_TEMPLATE = """# USER PROMPT ADD-ONS

These are your preferences. They add context to the pipeline prompts but never
override safety gates, factual verification, audit requirements, or human approval.

## Every run

- (Add preferences that should apply everywhere.)

## Build

- (Add build-only preferences.)

## Verify and patch

- (Add verification preferences.)

## Automatic mode

- (Add unattended-run preferences.)

## Final duplicate check

- (Add cross-deck preferences.)
"""
RESET_CONFIRMATION = "RESET TO DEFAULTS"


def is_project(path):
    return ws.is_workspace(path)


def safe_child(root, candidate):
    root = Path(root).resolve()
    candidate = Path(candidate).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("Path is outside the selected project.") from exc
    return candidate


def clean_module_name(value):
    value = re.sub(r"\s+", " ", str(value or "")).strip().strip(".")
    if not value or len(value) > 140:
        raise ValueError("Enter a module name between 1 and 140 characters.")
    if any(char in value for char in ("/", "\\", "\0")) or value in (".", ".."):
        raise ValueError("Module names cannot contain slashes.")
    if re.search(r"[<>:\"|?*]", value):
        raise ValueError("Module name contains a character Windows cannot save.")
    return value


def preference_defaults(root):
    template = Path(root) / "control_center/templates/PROFILE.template.md"
    if not template.is_file():
        raise RuntimeError("The default profile template is missing. Run an update first.")
    return template.read_text(encoding="utf-8"), USER_PROMPTS_TEMPLATE


def open_native(path):
    path = str(Path(path))
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        opener = shutil.which("xdg-open") or shutil.which("gio")
        if not opener:
            raise RuntimeError("No desktop file opener was found.")
        command = [opener, "open", path] if Path(opener).name == "gio" else [opener, path]
        subprocess.Popen(command)


def launch_setup(root):
    root = Path(root).resolve()
    if sys.platform == "win32":
        command = [
            "powershell.exe", "-NoExit", "-ExecutionPolicy", "Bypass",
            "-File", str(root / "control_center/install/setup.ps1"),
        ]
        subprocess.Popen(command, cwd=root,
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        return "PowerShell opened. Follow the setup messages there."
    if sys.platform == "darwin":
        script = (f'cd {shlex_quote(str(root))}; '
                  'bash control_center/install/setup.sh')
        escaped = script.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.Popen([
            "osascript", "-e", f'tell application "Terminal" to do script "{escaped}"',
            "-e", 'tell application "Terminal" to activate',
        ])
        return "Terminal opened. Follow the setup messages there."

    terminals = [
        ("x-terminal-emulator", ["x-terminal-emulator", "-e"]),
        ("gnome-terminal", ["gnome-terminal", "--"]),
        ("konsole", ["konsole", "-e"]),
        ("xfce4-terminal", ["xfce4-terminal", "-e"]),
    ]
    shell_command = (f"cd {shlex_quote(str(root))}; "
                     "bash control_center/install/setup.sh; exec bash")
    for executable, prefix in terminals:
        if shutil.which(executable):
            subprocess.Popen(prefix + ["bash", "-lc", shell_command])
            return "A terminal opened. Follow the setup messages there."
    raise RuntimeError(
        "No graphical terminal was found. Run "
        "`bash control_center/install/setup.sh` manually."
    )


def shlex_quote(value):
    return "'" + value.replace("'", "'\"'\"'") + "'"


def completed_dir(root, state):
    recorded = state.get("paths", {}).get("completed")
    if recorded and Path(recorded).is_dir():
        return Path(recorded)
    return Path(root) / "COMPLETED"


def extract_report_judgements(report):
    if not report or not Path(report).is_file():
        return []
    text = Path(report).read_text(encoding="utf-8", errors="replace")
    match = re.search(
        r"(?ims)^#{1,4}\s+JUDG(?:E)?MENT CALLS[^\n]*\n(.*?)(?=^#{1,4}\s+|\Z)",
        text,
    )
    if not match:
        return []
    lines = [line.strip() for line in match.group(1).splitlines() if line.strip()]
    return lines[:40]


def extract_changelog_judgements(audit):
    path = Path(audit) / "changelog.json"
    if not path.is_file():
        return []
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    calls = []
    for item in entries if isinstance(entries, list) else []:
        if not isinstance(item, list) or not item:
            continue
        if item[0] in {"DEMOTE", "DELETE", "SPLIT"}:
            nid = item[1] if len(item) > 1 else "?"
            why = item[2] if len(item) > 2 else "Review this content decision."
            calls.append(f"{item[0].title()} · note {nid} · {why}")
    return calls[:40]


def deck_rows(root):
    state_path = Path(root) / "scripts/project_state.json"
    if state_path.exists():
        state = load_state(state_path)
    else:
        state = {"modules": [], "paths": {}}
    completed = completed_dir(root, state)
    rows = []
    seen = set()
    for module in state.get("modules", []):
        name = module.get("name", "Unnamed module")
        seen.add(name.lower())
        folder = completed / name
        reports = sorted((folder / "audit").glob("VERIFY_REPORT_*.md"), reverse=True)
        report = reports[0] if reports else None
        notes = folder / f"{name} (NOTES).docx"
        final = folder / f"{name} (FINAL).apkg"
        judgements = extract_report_judgements(report)
        if not judgements:
            judgements = extract_changelog_judgements(folder / "audit")
        rows.append({
            "name": name,
            "status": module.get("status", "unknown"),
            "verified": module.get("status") == "verified",
            "cards_before": module.get("cards_before"),
            "cards_after": module.get("cards_after"),
            "judgements": judgements,
            "judgement_count": len(judgements),
            "has_notes": notes.is_file(),
            "has_report": bool(report),
            "has_deck": final.is_file(),
            "notes_path": str(notes) if notes.is_file() else None,
            "report_path": str(report) if report else None,
            "folder_path": str(folder) if folder.is_dir() else None,
        })
    if completed.is_dir():
        for folder in completed.iterdir():
            if not folder.is_dir() or folder.name.lower() in seen or folder.name == "EXAMPLE":
                continue
            notes = folder / f"{folder.name} (NOTES).docx"
            judgements = extract_changelog_judgements(folder / "audit")
            rows.append({
                "name": folder.name, "status": "untracked", "verified": False,
                "cards_before": None, "cards_after": None,
                "judgements": judgements,
                "judgement_count": len(judgements), "has_notes": notes.is_file(),
                "has_report": False, "has_deck": False,
                "notes_path": str(notes) if notes.is_file() else None,
                "report_path": None, "folder_path": str(folder),
            })
    order = {"in-progress": 0, "built-unverified": 1, "pending": 2, "verified": 3}
    rows.sort(key=lambda row: (order.get(row["status"], 9), row["name"].lower()))
    return rows


class Jobs:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs = {}

    def start(self, label, operation):
        job_id = uuid.uuid4().hex
        job = {"id": job_id, "label": label, "status": "running", "step": "start",
               "messages": [], "result": None, "error": None, "created": time.time()}
        with self._lock:
            self._jobs[job_id] = job

        def update(message, *, step=None):
            with self._lock:
                job["messages"].append(str(message))
                job["messages"] = job["messages"][-80:]
                if step:
                    job["step"] = step

        def runner():
            try:
                result = operation(update)
                with self._lock:
                    job["result"] = result
                    job["status"] = "done"
                    job["step"] = "done"
            except Exception as exc:
                with self._lock:
                    job["error"] = str(exc)
                    job["status"] = "error"
                    job["step"] = "error"

        threading.Thread(target=runner, name=f"control-center-{job_id[:7]}", daemon=True).start()
        return job_id

    def get(self, job_id):
        with self._lock:
            value = self._jobs.get(job_id)
            return json.loads(json.dumps(value)) if value else None


EMPTY_PIPELINE = {"modules": 0, "verified": 0, "built_unverified": 0,
                  "in_progress": 0, "run_count": 0, "statuses": {}}

NO_WORKSPACE = ("No workspace is open yet. Create one or open an existing "
                "folder first.")


class Application:
    def __init__(self, root, shell=None):
        self._lock = threading.Lock()
        self.root = None
        if root is not None:
            resolved = Path(root).resolve()
            if not is_project(resolved):
                raise ValueError(f"Not an Anki Optimizer project: {resolved}")
            self.root = resolved
        self.token = secrets.token_urlsafe(32)
        self.jobs = Jobs()
        self.shell = dict(shell or {})
        self.shell.setdefault("mode", "browser")
        self.shell.setdefault("chrome", "system")
        self.shell.setdefault("platform", sys.platform)
        self.shell.setdefault("app_version", ws.APP_VERSION)
        # Filled in by start_server; the window needs the address to load.
        self.host = "127.0.0.1"
        self.port = 0
        # Set by desktop.py so the page can drive the window it lives in.
        self.shell_actions = None

    @property
    def url(self):
        return f"http://{self.host}:{self.port}/"

    def require_root(self):
        root = self.root
        if root is None:
            raise ValueError(NO_WORKSPACE)
        return root

    def toolkit_version(self):
        return current_version(self.require_root())

    def set_root(self, path):
        if not path:
            raise ValueError("Paste or choose an Anki LLM Optimizer folder.")
        path = Path(path).expanduser().resolve()
        if not is_project(path):
            raise ValueError("That folder is not an Anki LLM Optimizer project.")
        with self._lock:
            self.root = path
        ws.remember_workspace(path)
        return path

    def staging_directory(self, kind, module=""):
        root = self.require_root()
        if kind == "source":
            target = root / "Source Files"
            if str(module or "").strip():
                target /= clean_module_name(module)
        elif kind == "deck":
            target = root / "Anki Decks"
        else:
            raise ValueError("Choose either the source or deck destination.")
        target = safe_child(root, target)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def machine_health(self):
        """What the build pipeline needs, which is separate from what PRISM
        itself needs. PRISM carries its own runtime; these are the tools the
        scripts shell out to once a module is actually being built.

        Every lookup goes through ws.find_tool rather than the bare PATH,
        because a double-clicked application does not inherit the one a
        terminal builds. See the note above search_path in workspace.py.
        """
        return {
            "python": _system_python(),
            "poppler": bool(ws.find_tool("pdftotext")),
            "tesseract": bool(ws.find_tool("tesseract")),
            "node": bool(ws.find_tool("node")),
        }

    def status(self):
        root = self.root
        base = {
            "ready": root is not None,
            "app_version": ws.APP_VERSION,
            "shell": dict(self.shell),
            "health": self.machine_health(),
            # Read in a browser tab, this is the only signal that the same
            # dashboard exists as an application. Updating the toolkit in
            # place cannot turn a launcher script into one.
            "releases_url": RELEASES_PAGE,
        }
        if root is None:
            base.update({"project": "", "version": "", "pipeline": dict(EMPTY_PIPELINE),
                         "staged": {"source_modules": 0, "decks": 0}})
            return base
        state_path = root / "scripts/project_state.json"
        if state_path.exists():
            pipeline = state_summary(load_state(state_path))
        else:
            pipeline = dict(EMPTY_PIPELINE)
        source = root / "Source Files"
        decks = root / "Anki Decks"
        base.update({
            "project": str(root),
            "version": current_version(root),
            "pipeline": pipeline,
            "staged": {
                "source_modules": len([p for p in source.iterdir() if p.is_dir()]) if source.is_dir() else 0,
                "decks": len(list(decks.glob("*.apkg"))) if decks.is_dir() else 0,
            },
        })
        return base

    def workspace_options(self):
        """Everything the first run screen needs to offer a choice."""
        recent, seen = [], set()
        for stored in ws.load_settings()["recent"]:
            resolved = str(Path(stored))
            if resolved in seen or not ws.is_workspace(resolved):
                continue
            seen.add(resolved)
            recent.append(resolved)
        try:
            toolkit = ws.toolkit_version()
            available = True
        except ws.WorkspaceError:
            toolkit, available = "", False
        return {
            "ready": self.root is not None,
            "workspace": str(self.root) if self.root else None,
            "suggested": str(ws.suggested_workspace()),
            "recent": recent[:6],
            "can_create": available,
            "toolkit": toolkit,
            "app_version": ws.APP_VERSION,
            "native_picker": self.shell.get("mode") == "desktop",
        }

    def allowed_open_path(self, candidate):
        if not candidate:
            raise ValueError("No file was selected.")
        root = self.require_root()
        candidate = Path(candidate).resolve()
        try:
            return safe_child(root, candidate)
        except ValueError:
            # A completed folder may be configured outside the project. Only the
            # exact paths already shown in the deck review list may be opened.
            allowed = set()
            for row in deck_rows(root):
                for key in ("notes_path", "report_path", "folder_path"):
                    if row.get(key):
                        allowed.add(Path(row[key]).resolve())
            if candidate not in allowed:
                raise ValueError("That file is outside the selected project.")
            return candidate


def _system_python():
    """The version of the interpreter the pipeline scripts would run under.

    Not PRISM's own. A build carries a private interpreter that cannot pip
    install the packages the pipeline needs, so reporting its version would
    tell the user their machine is ready when it is not. A checkout does run
    under a machine interpreter, but not necessarily one new enough, and
    macOS still ships 3.9. Asking the same question either way means the
    application and the browser tab give the same answer.
    """
    return ws.system_python()[1]


class Handler(BaseHTTPRequestHandler):
    server_version = "PRISM/1"

    @property
    def app(self):
        return self.server.application  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):
        if getattr(self.server, "verbose", False):  # type: ignore[attr-defined]
            super().log_message(fmt, *args)

    def _json(self, value, status=200):
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, message, status=400):
        self._json({"ok": False, "error": str(message)}, status)

    def _authorized(self):
        return secrets.compare_digest(self.headers.get("X-Control-Token", ""), self.app.token)

    def _require_token(self):
        if not self._authorized():
            self._error("This request did not come from PRISM on this computer.", 403)
            return False
        return True

    def _loopback_only(self):
        """Refuse anything addressed to a name that is not this machine.

        The socket already listens on 127.0.0.1, but a page on another site
        can still point a browser at a hostname that resolves there. Checking
        the names in the request closes that off before any handler runs.
        """
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip().strip("[]").lower()
        if host not in LOOPBACK_NAMES:
            return False
        origin = self.headers.get("Origin")
        if origin and origin != "null":
            hostname = (urllib.parse.urlparse(origin).hostname or "").lower()
            if hostname not in LOOPBACK_NAMES:
                return False
        return True

    def _page(self):
        page = (STATIC / "index.html").read_text(encoding="utf-8")
        shell = self.app.shell
        classes = " ".join((
            f"shell-{shell.get('mode', 'browser')}",
            f"chrome-{shell.get('chrome', 'system')}",
            f"platform-{shell.get('platform', sys.platform)}",
        ))
        return (page
                .replace("__CONTROL_TOKEN__", self.app.token)
                .replace("__BODY_CLASS__", classes)
                .replace("__APP_VERSION__", ws.APP_VERSION))

    def _read_json(self, limit=1024 * 1024):
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise ValueError("Request is too large.")
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8") or "{}")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if not self._loopback_only():
            self._error("PRISM only answers requests addressed to this computer.", 403)
            return
        if parsed.path == "/":
            payload = self._page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            return
        if parsed.path.startswith("/static/"):
            relative = parsed.path.removeprefix("/static/")
            try:
                path = safe_child(STATIC, STATIC / relative)
            except ValueError:
                self.send_error(404); return
            if not path.is_file():
                self.send_error(404); return
            payload = path.read_bytes()
            kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(payload)
            return
        if not self._require_token():
            return
        try:
            if parsed.path == "/api/status":
                self._json({"ok": True, **self.app.status()})
            elif parsed.path == "/api/workspace":
                self._json({"ok": True, **self.app.workspace_options()})
            elif parsed.path == "/api/guide":
                guide = self.app.require_root() / "START HERE.md"
                if not guide.is_file():
                    raise RuntimeError("START HERE.md is missing. Run an update first.")
                self._json({
                    "ok": True,
                    "file": guide.name,
                    "markdown": guide.read_text(encoding="utf-8"),
                })
            elif parsed.path == "/api/decks":
                self._json({"ok": True, "decks": deck_rows(self.app.require_root())})
            elif parsed.path == "/api/preferences":
                root = self.app.require_root()
                profile = root / "PROFILE.md"
                default_profile, default_prompts = preference_defaults(root)
                if not profile.exists():
                    atomic_write_bytes(profile, default_profile.encode("utf-8"))
                prompts = root / "USER_PROMPTS.md"
                self._json({
                    "ok": True,
                    "profile": profile.read_text(encoding="utf-8") if profile.exists() else "",
                    "prompts": prompts.read_text(encoding="utf-8") if prompts.exists()
                    else default_prompts,
                })
            elif parsed.path.startswith("/api/jobs/"):
                job = self.app.jobs.get(parsed.path.rsplit("/", 1)[-1])
                self._json({"ok": bool(job), "job": job}, 200 if job else 404)
            else:
                self.send_error(404)
        except (ValueError, UpdateError) as exc:
            self._error(exc, 400)
        except Exception as exc:
            self._error(exc, 500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if not self._loopback_only():
            self._error("PRISM only answers requests addressed to this computer.", 403)
            return
        if not self._require_token():
            return
        try:
            if parsed.path == "/api/project":
                data = self._read_json()
                selected = self.app.set_root(data.get("path"))
                self._json({"ok": True, "project": str(selected)})
            elif parsed.path == "/api/project/select":
                selected = self._native_folder_dialog()
                if selected:
                    self.app.set_root(selected)
                self._json({"ok": True, "project": str(self.app.root or ""),
                            "changed": bool(selected)})
            elif parsed.path.startswith("/api/workspace/"):
                self._workspace_action(parsed.path.rsplit("/", 1)[-1])
            elif parsed.path == "/api/shell/focus":
                actions = self.app.shell_actions
                raised = bool(actions and actions.focus())
                self._json({"ok": True, "focused": raised})
            elif parsed.path == "/api/preferences":
                data = self._read_json(limit=2 * 1024 * 1024)
                root = self.app.require_root()
                profile = str(data.get("profile", ""))
                prompts = str(data.get("prompts", ""))
                if len(profile) > 500_000 or len(prompts) > 500_000:
                    raise ValueError("Preference text is unexpectedly large.")
                atomic_write_bytes(root / "PROFILE.md", profile.encode("utf-8"), backup=True)
                atomic_write_bytes(root / "USER_PROMPTS.md", prompts.encode("utf-8"), backup=True)
                self._json({"ok": True, "message": "Preferences saved safely."})
            elif parsed.path == "/api/preferences/reset":
                data = self._read_json()
                if data.get("confirmation") != RESET_CONFIRMATION:
                    raise ValueError("The preference reset was not fully confirmed.")
                root = self.app.require_root()
                profile, prompts = preference_defaults(root)
                atomic_write_bytes(root / "PROFILE.md", profile.encode("utf-8"), backup=True)
                atomic_write_bytes(root / "USER_PROMPTS.md", prompts.encode("utf-8"), backup=True)
                self._json({
                    "ok": True, "profile": profile, "prompts": prompts,
                    "message": "Default preferences restored. Your previous files were backed up.",
                })
            elif parsed.path == "/api/stage":
                self._stage_upload(parsed)
            elif parsed.path == "/api/staging/open":
                data = self._read_json()
                target = self.app.staging_directory(data.get("kind"), data.get("module", ""))
                open_native(target)
                self._json({"ok": True, "path": str(target)})
            elif parsed.path == "/api/open":
                data = self._read_json()
                candidate = self.app.allowed_open_path(data.get("path"))
                if not candidate.exists():
                    raise ValueError("That file no longer exists.")
                open_native(candidate)
                self._json({"ok": True})
            elif parsed.path == "/api/setup":
                message = launch_setup(self.app.require_root())
                self._json({"ok": True, "message": message})
            elif parsed.path == "/api/update/check":
                root = self.app.require_root()
                job_id = self.app.jobs.start("Check for updates", lambda log: self._check_job(root, log))
                self._json({"ok": True, "job_id": job_id}, 202)
            elif parsed.path == "/api/update/install":
                data = self._read_json()
                root = self.app.require_root()
                allow = bool(data.get("allow_incomplete"))
                job_id = self.app.jobs.start(
                    "Install update",
                    lambda log: install_update(root, callback=log, allow_incomplete=allow),
                )
                self._json({"ok": True, "job_id": job_id}, 202)
            else:
                self.send_error(404)
        except (ValueError, UpdateError, ws.WorkspaceError, json.JSONDecodeError) as exc:
            self._error(exc, 400)
        except Exception as exc:
            self._error(exc, 500)

    def _workspace_action(self, action):
        data = self._read_json()
        if action == "open":
            selected = self.app.set_root(data.get("path"))
            self._json({"ok": True, "workspace": str(selected),
                        **self.app.workspace_options()})
        elif action == "browse":
            creating = data.get("mode") == "create"
            start = ws.default_workspace_parent() if creating else None
            chosen = self._native_folder_dialog(start=start)
            payload = {"ok": True, "path": str(chosen) if chosen else ""}
            if chosen and creating:
                payload["suggestion"] = str(ws.suggest_inside(Path(chosen)))
            self._json(payload)
        elif action == "create":
            requested = str(data.get("path") or "").strip()
            if not requested:
                raise ValueError("Choose where the new workspace should live.")
            destination = Path(requested).expanduser()
            if not destination.is_absolute():
                raise ValueError("Give the full path to the new workspace folder.")
            created = ws.create_workspace(destination)
            self.app.set_root(created)
            self._json({"ok": True, "workspace": str(created),
                        **self.app.workspace_options()})
        elif action == "repair":
            report = ws.restore_missing(self.app.require_root())
            restored = len(report["restored"])
            self._json({
                "ok": True, **report,
                "message": (f"Restored {restored} missing toolkit "
                            f"{'file' if restored == 1 else 'files'}."
                            if restored else "Nothing was missing from the toolkit."),
            })
        elif action == "inspect":
            self._json({"ok": True, **ws.inspect_workspace(self.app.require_root())})
        else:
            self.send_error(404)

    def _check_job(self, root, log):
        # The last line the activity log is left showing is the only record of
        # how this ended, so the check reports its own answer rather than
        # stopping on the sentence that describes work still in progress.
        log("Checking the latest stable GitHub release…", step="check")
        result = check_latest(root)
        if result["available"]:
            log(f"Version {result['latest']} is available. "
                f"Version {result['installed']} is installed.", step="done")
        else:
            log(f"Version {result['installed']} is the latest stable release. "
                "Nothing to install.", step="done")
        return result

    def _native_folder_dialog(self, start=None):
        """The window's own folder chooser when there is a window, Tk otherwise."""
        actions = self.app.shell_actions
        if actions is not None:
            chosen = actions.pick_folder(
                "Choose a folder", start or self.app.root or ws.default_workspace_parent(),
            )
            return str(chosen) if chosen else None
        try:
            import tkinter
            from tkinter import filedialog
            window = tkinter.Tk()
            window.withdraw()
            window.attributes("-topmost", True)
            selected = filedialog.askdirectory(
                title="Choose your PRISM workspace folder",
                initialdir=str(start or self.app.root or ws.default_workspace_parent()),
                mustexist=True,
            )
            window.destroy()
            return selected or None
        except Exception as exc:
            raise ValueError(
                "The native folder picker is unavailable. Paste the folder path into "
                f"the project field instead. ({exc})"
            ) from exc

    def _stage_upload(self, parsed):
        query = urllib.parse.parse_qs(parsed.query)
        kind = (query.get("kind") or [""])[0]
        module = clean_module_name((query.get("module") or [""])[0])
        incoming = Path((query.get("name") or [""])[0]).name
        overwrite = (query.get("overwrite") or ["0"])[0] == "1"
        if not incoming:
            raise ValueError("Dropped file has no name.")
        extension = Path(incoming).suffix.lower()
        root = self.app.require_root()
        if kind == "deck":
            if extension != ".apkg":
                raise ValueError("The deck drop zone accepts .apkg files only.")
            target = root / "Anki Decks" / f"{module}.apkg"
        elif kind == "source":
            if extension not in SOURCE_EXTENSIONS:
                raise ValueError(f"Unsupported source type: {extension or 'no extension'}")
            target = root / "Source Files" / module / incoming
        else:
            raise ValueError("Unknown drop zone.")
        target = safe_child(root, target)
        if target.exists() and not overwrite:
            self._error(f"{target.name} already exists. Confirm replacement to continue.", 409)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 4 * 1024 * 1024 * 1024:
            raise ValueError("File size is empty or exceeds the 4 GB staging limit.")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{secrets.token_hex(5)}.upload")
        remaining = length
        try:
            with open(temporary, "wb") as handle:
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("Upload ended before the complete file arrived.")
                    handle.write(chunk)
                    remaining -= len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        self._json({"ok": True, "name": target.name, "path": str(target),
                    "module": module, "kind": kind})


def start_server(root, port=0, verbose=False, shell=None):
    """Bind the loopback helper and serve it on a background thread.

    Returns the server and the application, so a caller that owns a window can
    read ``application.url`` and shut the whole thing down when the window
    closes. Raises before anything is started if ``root`` is not a project.
    """
    application = Application(root, shell=shell)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.application = application  # type: ignore[attr-defined]
    server.verbose = verbose  # type: ignore[attr-defined]
    application.host, application.port = server.server_address[0], server.server_address[1]
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.25},
        name="prism-http", daemon=True,
    )
    thread.start()
    server.worker = thread  # type: ignore[attr-defined]
    return server, application


def serve(root, port=0, open_browser=True, verbose=False):
    server, application = start_server(root, port=port, verbose=verbose)
    print(f"PRISM: {application.url}")
    print("Keep this window open while using PRISM. Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.35, lambda: webbrowser.open(application.url)).start()
    try:
        while True:
            time.sleep(0.4)
    except KeyboardInterrupt:
        print("\nPRISM closed.")
    finally:
        server.shutdown()
        server.server_close()


def cli_path(value):
    """A path as a shell handed it over, with Windows quoting undone.

    ``%~dp0`` in a .cmd file always ends in a backslash, and a backslash in
    front of the closing quote escapes that quote instead of ending the
    argument, so ``--root "%~dp0"`` reaches us with a stray quote stuck on the
    end. The launcher no longer writes it that way, but older copies are still
    on disk, and a quote is not a character a Windows path can hold anyway.
    """
    if value is None or sys.platform != "win32":
        return value
    return value.strip().strip('"') or None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None,
                        help="The project folder to open. Defaults to the one PRISM "
                             "last used, or the checkout this file sits in.")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    root = cli_path(args.root)
    if root is None:
        remembered = ws.remembered_workspace()
        candidate = Path(__file__).resolve().parents[1]
        root = str(remembered) if remembered else (str(candidate) if is_project(candidate) else None)
    serve(root, args.port, not args.no_browser, args.verbose)


if __name__ == "__main__":
    main()
