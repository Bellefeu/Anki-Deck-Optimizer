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

import glob
import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path


# The version of the PRISM application itself: the number on the downloaded
# file. It is built from a given release and must match that release's
# scripts/UPDATE_MANIFEST.json, which a test enforces.
#
# On a user's machine the two can and should drift apart. The release updater
# can carry a workspace's toolkit forward without anyone downloading a new
# application, so an installed PRISM 1.5.0 may quite correctly be looking at a
# 1.6.0 workspace. Both numbers are shown, which is why there are two.
APP_VERSION = "1.5.4"

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
# Finding the tools the pipeline shells out to
#
# A double-clicked application inherits the desktop launcher's environment,
# not the one a terminal assembles for itself. On macOS that leaves PATH as
# /usr/bin:/bin:/usr/sbin:/sbin, which is none of the places Homebrew installs
# into; on Windows a PATH that winget has just widened never reaches a process
# that was already running. Either way a plain shutil.which call reports the
# machine as unequipped while the very same command works in Terminal, which
# is exactly the wrong answer to give someone who has just run the setup. So
# tool lookups go through search_path() instead of the bare environment.


# The probe below spawns a login shell, so its answer is held briefly rather
# than recomputed for every status request. The window is short because the
# whole point is to notice a tool that was installed a moment ago.
_PATH_CACHE = {"value": None, "taken": 0.0}
_PATH_TTL_SECONDS = 15.0

# Where the installers behind the guided setup put things, consulted in
# addition to whatever the shell reports. A tool installed while PRISM is open
# is then found without anyone having to restart anything.
_UNIX_TOOL_DIRS = (
    "/opt/homebrew/bin", "/opt/homebrew/sbin",   # Homebrew on Apple silicon
    "/usr/local/bin", "/usr/local/sbin",         # Homebrew on Intel, and most Linux
    "/opt/local/bin", "/opt/local/sbin",         # MacPorts
    "/usr/bin", "/bin", "/usr/sbin", "/sbin",
    "/snap/bin",
)

# Same idea, for installs that carry a version number in the path.
_UNIX_TOOL_GLOBS = (
    "/Library/Frameworks/Python.framework/Versions/3.*/bin",  # python.org
    "~/Library/Python/3.*/bin",                               # pip --user, macOS
    "~/.local/bin",                                           # pip --user, Linux
    "~/.nvm/versions/node/*/bin",                             # nvm
)

_WINDOWS_TOOL_GLOBS = (
    r"%LOCALAPPDATA%\Microsoft\WinGet\Links",                 # winget portable packages
    r"%LOCALAPPDATA%\Programs\Python\Python3*",
    r"%LOCALAPPDATA%\Programs\Python\Python3*\Scripts",
    r"%PROGRAMFILES%\nodejs",
    r"%PROGRAMFILES%\Tesseract-OCR",
    r"%PROGRAMFILES%\poppler*\Library\bin",
)


# Plenty of login profiles greet you, and that banner arrives on the same
# stdout as the answer. The marker is what separates the two.
_PATH_MARKER = "__prism_path__"


def _login_shell_path():
    """What a terminal opened right now would have on PATH.

    ``-lc`` reads the login profile, which is where Homebrew's own installer
    tells people to put its shellenv line. An interactive shell would read a
    little more, but it can also block on a prompt, and blocking the dashboard
    to answer a question about PATH is not a trade worth making.
    """
    shell = os.environ.get("SHELL") or "/bin/sh"
    try:
        found = subprocess.run(
            [shell, "-lc", f'printf %s "{_PATH_MARKER}$PATH"'],
            capture_output=True, text=True, timeout=6,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if found.returncode != 0 or _PATH_MARKER not in found.stdout:
        return []
    answer = found.stdout.rsplit(_PATH_MARKER, 1)[1].strip()
    return [part for part in answer.split(os.pathsep) if part]


def _registry_path():
    """The PATH Windows would hand a newly opened console.

    An installer edits these two registry values and broadcasts the change.
    Long-running processes do not act on that broadcast, so PRISM reads them.
    """
    entries = []
    try:
        import winreg
    except ImportError:
        return entries
    places = (
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    )
    for hive, subkey in places:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                raw, _ = winreg.QueryValueEx(key, "Path")
        except OSError:
            continue
        expanded = os.path.expandvars(str(raw))
        entries.extend(part for part in expanded.split(os.pathsep) if part)
    return entries


def _known_tool_dirs():
    found = []
    if sys.platform == "win32":
        patterns = _WINDOWS_TOOL_GLOBS
    else:
        found.extend(_UNIX_TOOL_DIRS)
        patterns = _UNIX_TOOL_GLOBS
    for pattern in patterns:
        expanded = os.path.expandvars(os.path.expanduser(pattern))
        if any(char in expanded for char in "*?"):
            found.extend(sorted(glob.glob(expanded)))
        else:
            found.append(expanded)
    return [entry for entry in found if os.path.isdir(entry)]


def search_path(refresh=False):
    """The directories to look for a pipeline tool in, most trusted first."""
    now = time.monotonic()
    cached = _PATH_CACHE["value"]
    if cached is not None and not refresh and now - _PATH_CACHE["taken"] < _PATH_TTL_SECONDS:
        return cached

    entries = [part for part in os.environ.get("PATH", "").split(os.pathsep) if part]
    entries.extend(_registry_path() if sys.platform == "win32" else _login_shell_path())
    entries.extend(_known_tool_dirs())

    ordered, seen = [], set()
    for entry in entries:
        key = os.path.normcase(os.path.normpath(entry))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(entry)

    resolved = os.pathsep.join(ordered)
    _PATH_CACHE.update({"value": resolved, "taken": now})
    return resolved


def find_tool(name, refresh=False):
    """Locate one command the way a terminal would, or return None."""
    return shutil.which(name, path=search_path(refresh=refresh))


def tool_environment(refresh=False):
    """This process's environment with the terminal's PATH in place.

    Anything PRISM spawns that will itself go looking for pdftotext or node
    needs the widened PATH too, or the health check and the run disagree.
    """
    environment = dict(os.environ)
    environment["PATH"] = search_path(refresh=refresh)
    return environment


# The pipeline needs 3.10 or newer and macOS still ships 3.9, so a "python3"
# on PATH is interrogated rather than trusted.
_PYTHON_CANDIDATES = ("python3.13", "python3.12", "python3.11", "python3.10",
                      "python3", "python")
_PYTHON_CACHE = {"value": None, "taken": 0.0}


def system_python(refresh=False):
    """The interpreter the pipeline scripts run under, with its version.

    Deliberately not PRISM's own. A build carries a private interpreter that
    cannot pip install the packages the pipeline needs, and in a frozen build
    sys.executable is the application itself, so handing either one a script
    would fail in a way that looks like the script's fault.

    Returns a (path, version) pair, both empty when nothing suitable is here.
    """
    now = time.monotonic()
    cached = _PYTHON_CACHE["value"]
    if cached is not None and not refresh and now - _PYTHON_CACHE["taken"] < _PATH_TTL_SECONDS:
        return cached

    # Resolved once: refreshing inside the loop would re-probe the login
    # shell for every candidate name.
    where = search_path(refresh=refresh)
    found = ("", "")
    for candidate in _PYTHON_CANDIDATES:
        executable = shutil.which(candidate, path=where)
        if not executable:
            continue
        try:
            asked = subprocess.run(
                [executable, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
                capture_output=True, text=True, timeout=6,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        version = asked.stdout.strip()
        if asked.returncode != 0 or not re.fullmatch(r"3\.\d+\.\d+", version):
            continue
        if tuple(int(part) for part in version.split(".")) >= (3, 10, 0):
            found = (executable, version)
            break

    _PYTHON_CACHE.update({"value": found, "taken": now})
    return found


# --------------------------------------------------------------------------
# Reaching the network from a build
#
# The same shape of problem as the search path above, one layer down. A built
# PRISM carries the interpreter it was frozen with, and that interpreter's ssl
# module was compiled knowing where the certificate authorities lived on the
# machine that built it: a path inside the build runner's own Python. On the
# computer that downloads the application, nothing is there.
#
# Nothing warns about this. The trust store simply comes up empty and every
# HTTPS request fails with CERTIFICATE_VERIFY_FAILED, which reads to a user
# like GitHub being down rather than like a file that was never shipped.
#
# Where a real trust store does exist it stays in charge. A machine behind a
# corporate proxy has its own root installed there, and overriding that would
# break precisely the people whose network is already the most particular. So
# the certificates bundled into the build are consulted only when asking the
# system produced nothing at all.


def _trust_store_is_empty(context):
    """True when nothing on this machine will verify a certificate.

    Counting what the context holds is only half an answer, and taking it for
    the whole one is wrong in a way that matters. Certificates arrive by two
    routes. A cafile is read in full the moment the context is built, so a
    count settles it. A capath, which is how Debian and everything descended
    from it ship their authorities, is never read up front: OpenSSL goes to
    the directory for one certificate at a time, by name, when it needs it.
    The count on such a machine is zero and the machine is perfectly fine.

    Reading that zero as an empty store would push a working Debian onto the
    bundled copy, and on a machine whose administrator put a root into
    /etc/ssl/certs it would throw that root away. That is the exact failure
    this whole path exists to avoid, so the directory is looked at directly.
    """
    try:
        if context.cert_store_stats()["x509_ca"]:
            return False
    except (AttributeError, KeyError, TypeError, ValueError):
        # An ssl module that cannot answer is not evidence of an empty store.
        return False
    paths = ssl.get_default_verify_paths()
    if paths.cafile and os.path.isfile(paths.cafile):
        return False
    try:
        if paths.capath and os.listdir(paths.capath):
            return False
    except OSError:
        # No such directory, or no permission to look. Either way, nothing
        # here is going to verify anything.
        pass
    return True


def ssl_context():
    """A verifying SSL context that works in a build as well as a checkout.

    Always verifying. A frozen application that quietly stopped checking
    certificates would trade a visible failure for an invisible one, and this
    is the code path that downloads and then installs a release.
    """
    context = ssl.create_default_context()
    if not _trust_store_is_empty(context):
        return context
    try:
        import certifi
    except ImportError:
        # A checkout with nothing installed. The system store is empty here
        # too, so this will still fail, but it fails saying so.
        return context
    try:
        return ssl.create_default_context(cafile=certifi.where())
    except (OSError, ssl.SSLError):
        return context


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
