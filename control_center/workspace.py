#!/usr/bin/env python3
"""Where PRISM keeps its own settings, and how it lays down a workspace.

A downloaded PRISM has no repository around it. It carries a payload instead:
every publisher-owned file named in ``scripts/UPDATE_MANIFEST.json``, verified
by the same hashes the updater uses. Laying down a workspace is that payload
copied into a folder the user chose, plus the empty study folders, plus a
starting profile. From that moment the folder is an ordinary project and the
existing release updater owns it.

Running from a source checkout, the payload is simply the checkout, so both
ways of starting PRISM exercise the same code.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


# The version of the PRISM application itself: the number on the downloaded
# file. It is built from a given release and must match that release's
# scripts/UPDATE_MANIFEST.json, which a test enforces.
#
# On a user's machine the two can and should drift apart. The release updater
# can carry a workspace's toolkit forward without anyone downloading a new
# application, so an installed PRISM 1.5.0 may quite correctly be looking at a
# 1.6.0 workspace. Both numbers are shown, which is why there are two.
APP_VERSION = "1.5.0"

CONFIG_VERSION = 1
MANIFEST_REL = "scripts/UPDATE_MANIFEST.json"

# Created empty in a new workspace. The pipeline expects all four to exist.
STUDY_FOLDERS = ("Source Files", "Anki Decks", "COMPLETED", "work")

# Never copied out of the payload into a workspace, whatever the manifest
# says: these are either the publisher's own build tooling or a file the
# workspace has to own privately from its first minute.
PAYLOAD_EXCLUDED = (
    "control_center/test_control_center.py",
    "control_center/build_manifest.py",
)

# The allowlist cannot list its own hash, so it is copied alongside rather
# than through the hash checked set. The release updater installs it the
# same way, for the same reason.
UNHASHED_PAYLOAD = (MANIFEST_REL,)


class WorkspaceError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Locations


def frozen():
    """True when running from a built PRISM rather than a checkout."""
    return bool(getattr(sys, "frozen", False))


def resource_root():
    """The folder holding files bundled alongside the code."""
    if frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[1]


def payload_root():
    """The folder a new workspace is copied from."""
    if frozen():
        return resource_root() / "payload"
    return Path(__file__).resolve().parents[1]


def _home():
    return Path(os.path.expanduser("~"))


def config_dir():
    """Per-user settings, following each platform's own convention."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or (_home() / "AppData/Roaming")
        return Path(base) / "PRISM"
    if sys.platform == "darwin":
        return _home() / "Library/Application Support/PRISM"
    base = os.environ.get("XDG_CONFIG_HOME") or (_home() / ".config")
    return Path(base) / "prism"


def state_dir():
    """Logs and the running-instance record, which are not settings."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (_home() / "AppData/Local")
        return Path(base) / "PRISM"
    if sys.platform == "darwin":
        return _home() / "Library/Logs/PRISM"
    base = os.environ.get("XDG_STATE_HOME") or (_home() / ".local/state")
    return Path(base) / "prism"


def config_path():
    return config_dir() / "settings.json"


def log_path():
    return state_dir() / "prism.log"


def session_path():
    return state_dir() / "instance.json"


def default_workspace_parent():
    documents = _home() / "Documents"
    return documents if documents.is_dir() else _home()


def suggested_workspace():
    return default_workspace_parent() / "PRISM"


# --------------------------------------------------------------------------
# Settings


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), delete=False, suffix=".tmp",
    )
    try:
        with handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def load_settings():
    """Settings as stored, repaired to a usable shape. Never raises."""
    blank = {"version": CONFIG_VERSION, "workspace": None, "recent": [], "window": {}}
    try:
        stored = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return blank
    if not isinstance(stored, dict):
        return blank
    settings = dict(blank)
    if isinstance(stored.get("workspace"), str) and stored["workspace"].strip():
        settings["workspace"] = stored["workspace"]
    if isinstance(stored.get("recent"), list):
        settings["recent"] = [item for item in stored["recent"] if isinstance(item, str)][:8]
    if isinstance(stored.get("window"), dict):
        settings["window"] = stored["window"]
    return settings


def save_settings(settings):
    merged = dict(settings)
    merged["version"] = CONFIG_VERSION
    _write_json(config_path(), merged)
    return merged


def remember_workspace(root):
    """Record ``root`` as the workspace to reopen, and in the recent list."""
    root = str(Path(root).resolve())
    settings = load_settings()
    recent = [item for item in settings["recent"] if item != root]
    settings["workspace"] = root
    settings["recent"] = [root] + recent[:7]
    return save_settings(settings)


def remembered_workspace():
    """The stored workspace, if it is still a project folder on disk."""
    stored = load_settings()["workspace"]
    if not stored:
        return None
    candidate = Path(stored)
    return candidate if is_workspace(candidate) else None


def save_window_state(window):
    settings = load_settings()
    settings["window"] = window
    return save_settings(settings)


# --------------------------------------------------------------------------
# The payload


def is_workspace(path):
    """The same shape test the dashboard already uses for a project folder."""
    path = Path(path)
    return (path / "scripts").is_dir() and (path / "control_center/app.py").is_file()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_manifest(source=None):
    """The manifest that names every file a workspace is built from."""
    source = Path(source) if source else payload_root()
    path = source / MANIFEST_REL
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkspaceError(
            f"This build carries no toolkit payload. Expected {path}."
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"The bundled toolkit manifest is unreadable: {exc}") from exc
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise WorkspaceError("The bundled toolkit manifest lists no files.")
    return manifest


def payload_files(source=None):
    """Relative path to expected hash, for everything a workspace receives."""
    manifest = payload_manifest(source)
    return {
        relative: str(digest)
        for relative, digest in manifest["files"].items()
        if relative not in PAYLOAD_EXCLUDED
    }


def toolkit_version(source=None):
    return str(payload_manifest(source).get("release_version") or "unknown")


def verify_payload(source=None):
    """Missing and altered payload files, checked before anything is copied."""
    source = Path(source) if source else payload_root()
    missing, altered = [], []
    for relative, expected in payload_files(source).items():
        candidate = source / relative
        if not candidate.is_file():
            missing.append(relative)
        elif sha256_file(candidate) != expected:
            altered.append(relative)
    missing.extend(relative for relative in UNHASHED_PAYLOAD
                   if not (source / relative).is_file())
    return {"missing": sorted(missing), "altered": sorted(altered)}


# --------------------------------------------------------------------------
# Laying one down


def _describe(problems):
    parts = []
    if problems["missing"]:
        parts.append(f"{len(problems['missing'])} missing ({', '.join(problems['missing'][:3])})")
    if problems["altered"]:
        parts.append(f"{len(problems['altered'])} altered ({', '.join(problems['altered'][:3])})")
    return "; ".join(parts)


def workspace_blockers(destination):
    """Why ``destination`` cannot become a new workspace, or an empty list."""
    destination = Path(destination)
    if destination.exists() and not destination.is_dir():
        return ["A file already sits at that path."]
    if is_workspace(destination):
        return ["That folder is already a PRISM workspace. Open it instead."]
    if destination.is_dir():
        visible = [child for child in destination.iterdir() if not child.name.startswith(".")]
        if visible:
            return [
                "That folder already has files in it. Choose an empty folder or a "
                "new name, so nothing you own can be written over."
            ]
    return []


def suggest_inside(parent, name="PRISM"):
    """A workspace path under ``parent`` that is free to create.

    Folder pickers hand back a parent most of the time and an empty folder
    the rest of the time, and the user means the same thing either way, so
    the empty folder is used directly and anything else gets a child.
    """
    parent = Path(parent).expanduser()
    if not workspace_blockers(parent):
        return parent
    candidate = parent / name
    suffix = 2
    while workspace_blockers(candidate) and suffix < 100:
        candidate = parent / f"{name} {suffix}"
        suffix += 1
    return candidate


def create_workspace(destination, *, source=None, callback=None):
    """Copy the payload into ``destination`` and return the finished path.

    The copy is staged beside the destination and moved into place at the end,
    so an interrupted first run never leaves a half built workspace that the
    dashboard would then treat as real.
    """
    # Resolved up front so that everything downstream, the settings file, the
    # window title and any error message, all name the same real path.
    destination = Path(destination).expanduser().resolve()
    blockers = workspace_blockers(destination)
    if blockers:
        raise WorkspaceError(blockers[0])

    source = Path(source) if source else payload_root()
    problems = verify_payload(source)
    if problems["missing"] or problems["altered"]:
        raise WorkspaceError(
            "This PRISM build's toolkit payload failed its own integrity check "
            f"({_describe(problems)}). Download PRISM again."
        )

    def emit(message, *, step=None):
        if callback:
            callback(message, step=step)

    files = payload_files(source)
    emit(f"Preparing {len(files)} toolkit files", step="prepare")

    parent = destination.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkspaceError(f"Cannot create {parent}: {exc}") from exc
    staging = Path(tempfile.mkdtemp(prefix=".prism-new-", dir=str(parent)))
    try:
        for index, (relative, expected) in enumerate(sorted(files.items()), start=1):
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, target)
            if sha256_file(target) != expected:
                raise WorkspaceError(f"Copying {relative} did not reproduce it exactly.")
            if index % 12 == 0 or index == len(files):
                emit(f"Copied {index} of {len(files)} files", step="copy")

        for relative in UNHASHED_PAYLOAD:
            origin = source / relative
            if not origin.is_file():
                raise WorkspaceError(f"The bundled payload is missing {relative}.")
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, target)

        for folder in STUDY_FOLDERS:
            (staging / folder).mkdir(parents=True, exist_ok=True)
            keep = staging / folder / ".gitkeep"
            if not keep.exists():
                keep.write_bytes(b"")

        template = staging / "control_center/templates/PROFILE.template.md"
        profile = staging / "PROFILE.md"
        if template.is_file() and not profile.exists():
            shutil.copy2(template, profile)
        emit("Writing your starting preferences", step="profile")

        if not is_workspace(staging):
            raise WorkspaceError("The new workspace did not come out with the expected shape.")

        if destination.exists():
            # Only ever an empty directory, guaranteed by workspace_blockers.
            destination.rmdir()
        os.replace(staging, destination)
        staging = None
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)

    _mark_executable(destination)
    emit(f"Workspace ready at {destination}", step="done")
    return destination


def _mark_executable(root):
    """Restore the executable bit the launchers need, which copying can drop."""
    if sys.platform == "win32":
        return
    for relative in (
        "PRISM - Mac.command",
        "control_center/launch.sh",
        "control_center/install/setup.sh",
        "control_center/app.py",
    ):
        candidate = Path(root) / relative
        if candidate.is_file():
            try:
                candidate.chmod(candidate.stat().st_mode | 0o111)
            except OSError:
                pass


def inspect_workspace(root):
    """What is missing or edited in an existing workspace, against the payload."""
    root = Path(root)
    if not is_workspace(root):
        raise WorkspaceError(f"{root} is not a PRISM workspace.")
    expected = payload_files()
    missing, altered = [], []
    for relative, digest in expected.items():
        candidate = root / relative
        if not candidate.is_file():
            missing.append(relative)
        elif sha256_file(candidate) != digest:
            altered.append(relative)
    missing.extend(relative for relative in UNHASHED_PAYLOAD
                   if not (root / relative).is_file())
    return {"missing": sorted(missing), "altered": sorted(altered)}


def restore_missing(root, *, callback=None):
    """Put back publisher files a workspace has lost. Edited files are left alone.

    Restoring a file the user changed would silently discard their work, and
    the release updater already has a reviewed, backed up path for that.
    """
    root = Path(root)
    report = inspect_workspace(root)
    source = payload_root()
    restored = []
    for relative in report["missing"]:
        origin = source / relative
        if not origin.is_file():
            continue
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, target)
        restored.append(relative)
        if callback:
            callback(f"Restored {relative}", step="restore")
    for folder in STUDY_FOLDERS:
        (root / folder).mkdir(parents=True, exist_ok=True)
    if restored:
        _mark_executable(root)
    return {"restored": sorted(restored), "altered": report["altered"]}
