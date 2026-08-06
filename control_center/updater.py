#!/usr/bin/env python3
"""Release-based, rollback-safe updater for the Anki LLM Optimizer.

Only publisher-owned files listed in UPDATE_MANIFEST.json are replaced. Runtime
state, profiles, decks, sources, audit trails, completed work, and scratch are
outside the update allowlist. Updates are staged and tested before live files
change, and failures after installation begins restore the timestamped backup.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


REPOSITORY = "Bellefeu/Anki-LLM-Optimizer"
RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MANIFEST_REL = Path("scripts/UPDATE_MANIFEST.json")
STATE_REL = Path("scripts/project_state.json")
PROFILE_REL = Path("PROFILE.md")
LOCK_REL = Path("scripts/.pipeline.lock")
BACKUP_DIR = Path(".pipeline_backups")
USER_AGENT = "Anki-LLM-Optimizer-Control-Center/1"


class UpdateError(RuntimeError):
    pass


def _emit(callback, message, *, step=None):
    if callback:
        callback(message, step=step)


def _request_json(url, timeout=20):
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("No published update release is available yet.") from exc
        raise UpdateError(f"GitHub returned HTTP {exc.code} while checking updates.") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise UpdateError(f"Could not reach GitHub: {exc}") from exc


def _version_tuple(value):
    text = str(value or "0").lstrip("vV")
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", text)
    if not match:
        raise UpdateError(f"Unsupported release version: {value!r}")
    return tuple(int(group) for group in match.groups())


def load_manifest(root):
    path = Path(root) / MANIFEST_REL
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise UpdateError(f"Update manifest is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise UpdateError(f"Update manifest is invalid JSON: {exc}") from exc
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest):
    if not isinstance(manifest, dict) or manifest.get("format") != 1:
        raise UpdateError("Unsupported update manifest format.")
    _version_tuple(manifest.get("release_version"))
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise UpdateError("Update manifest has no patchable files.")
    for raw_path, digest in files.items():
        path = PurePosixPath(raw_path)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise UpdateError(f"Unsafe path in update manifest: {raw_path!r}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
            raise UpdateError(f"Invalid SHA-256 for {raw_path}")


def current_version(root):
    try:
        return load_manifest(root)["release_version"]
    except UpdateError:
        # Installations from before the Control Center have only the content hash.
        version_file = Path(root) / "scripts/VERSION.json"
        try:
            return "legacy-" + json.loads(version_file.read_text(encoding="utf-8"))["version"]
        except Exception:
            return "legacy-unknown"


def check_latest(root):
    release = _request_json(RELEASE_API)
    if release.get("draft") or release.get("prerelease"):
        raise UpdateError("GitHub's latest release is not a stable published release.")
    latest = release.get("tag_name")
    installed = current_version(root)
    update_available = installed.startswith("legacy-") or _version_tuple(latest) > _version_tuple(installed)
    return {
        "installed": installed,
        "latest": str(latest).lstrip("vV"),
        "available": update_available,
        "name": release.get("name") or latest,
        "notes": release.get("body") or "",
        "published_at": release.get("published_at"),
        "zipball_url": release.get("zipball_url"),
        "html_url": release.get("html_url"),
    }


def _download(url, destination, callback=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as response, open(destination, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            copied = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                copied += len(chunk)
                if total:
                    _emit(callback, f"Downloaded {copied / 1e6:.1f} of {total / 1e6:.1f} MB")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"Update download failed: {exc}") from exc


def _safe_extract(archive, destination):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as package:
        for info in package.infolist():
            member = PurePosixPath(info.filename)
            if member.is_absolute() or ".." in member.parts:
                raise UpdateError(f"Unsafe path in release archive: {info.filename}")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise UpdateError(f"Symbolic links are not allowed in releases: {info.filename}")
            target = destination.joinpath(*member.parts).resolve()
            try:
                target.relative_to(destination)
            except ValueError as exc:
                raise UpdateError(f"Archive path escapes staging folder: {info.filename}") from exc
        package.extractall(destination)


def _release_root(extracted):
    matches = list(Path(extracted).glob("*/scripts/UPDATE_MANIFEST.json"))
    direct = Path(extracted) / MANIFEST_REL
    if direct.exists():
        matches.append(direct)
    matches = list(dict.fromkeys(path.resolve() for path in matches))
    if len(matches) != 1:
        raise UpdateError("Release archive does not contain exactly one update manifest.")
    return matches[0].parent.parent


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_release(staged_root, manifest):
    for relative, expected in manifest["files"].items():
        path = Path(staged_root) / Path(relative)
        if not path.is_file():
            raise UpdateError(f"Release is missing allowlisted file: {relative}")
        actual = _sha256(path)
        if actual != expected:
            raise UpdateError(f"Checksum mismatch for {relative}")


def _load_staged_state_io(staged_root):
    path = Path(staged_root) / "scripts/state_io.py"
    if not path.exists():
        raise UpdateError("Release is missing scripts/state_io.py")
    name = "anki_update_staged_state_io"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _scan_incomplete_progress(root):
    results = []
    for base in (Path(root) / "work", Path(root) / "scripts/work"):
        if not base.is_dir():
            continue
        for path in base.glob("*/progress.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                results.append(str(path))
                continue
            if not data.get("complete"):
                results.append(str(path))
    return results


def _take_lock(root):
    path = Path(root) / LOCK_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({
        "action": "UPDATE",
        "module": "pipeline toolkit",
        "ts": _dt.datetime.now().timestamp(),
        "started": _dt.datetime.now().isoformat(timespec="seconds"),
    }, indent=2).encode("utf-8")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise UpdateError(
            "A pipeline run or another update is active. Pause the scheduled task "
            "and wait for the current run to release its lock."
        ) from exc
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return path


def _backup(root, current_manifest, target_manifest):
    root = Path(root)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    old_version = current_manifest.get("release_version", "legacy") if current_manifest else "legacy"
    target_version = target_manifest["release_version"]
    backup = root / BACKUP_DIR / f"{stamp}-{old_version}-to-{target_version}"
    backup.mkdir(parents=True, exist_ok=False)
    paths = set((current_manifest or {}).get("files", {}).keys())
    # A legacy installation has no old manifest. Back up every existing file the
    # incoming release plans to replace so its first rollback is still complete.
    paths.update(target_manifest.get("files", {}).keys())
    paths.update(str(path.as_posix()) for path in (
        STATE_REL, PROFILE_REL, Path("scripts/HANDOFF.md"),
        Path("scripts/HANDOFF_REFERENCE.md"), MANIFEST_REL,
    ))
    present = []
    for relative in sorted(paths):
        source = root / relative
        if not source.is_file():
            continue
        target = backup / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        present.append(relative)
    (backup / "backup.json").write_text(json.dumps({
        "created_at": _dt.datetime.now().isoformat(),
        "old_version": old_version,
        "target_version": target_version,
        "files": present,
    }, indent=2) + "\n", encoding="utf-8")
    return backup, set(present)


def _atomic_copy(source, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".update",
                               dir=str(target.parent))
    os.close(fd)
    try:
        shutil.copy2(source, tmp)
        os.replace(tmp, target)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _remove_tree(path):
    """Remove a private staging tree, retrying short filesystem races."""
    for attempt in range(6):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == 5:
                return
            time.sleep(min(0.1 * (2 ** attempt), 1.0))


def _restore(root, backup, backed_up, created):
    root, backup = Path(root), Path(backup)
    for relative in backed_up:
        source = backup / relative
        if source.exists():
            _atomic_copy(source, root / relative)
    for relative in created:
        path = root / relative
        if path.is_file():
            path.unlink()


def _run_staged_selftest(staged_root, callback=None):
    _emit(callback, "Running the full test suite in a private staging copy…", step="test")
    try:
        result = subprocess.run(
            [sys.executable, str(Path(staged_root) / "scripts/selftest.py")],
            cwd=str(Path(staged_root) / "scripts"), capture_output=True, text=True,
            timeout=420,
        )
    except subprocess.TimeoutExpired as exc:
        raise UpdateError("Staged self-test exceeded seven minutes.") from exc
    if result.returncode != 0:
        tail = "\n".join((result.stdout + "\n" + result.stderr).splitlines()[-18:])
        raise UpdateError("The downloaded update failed its self-test:\n" + tail)
    passed = re.search(r"(\d+) passed, 0 failed", result.stdout)
    return int(passed.group(1)) if passed else None


def install_update(root, *, release=None, archive=None, staged_directory=None,
                   callback=None, allow_incomplete=False):
    """Install one release and return a preservation report.

    `archive` and `staged_directory` are intentionally public test hooks. The GUI
    uses GitHub's latest stable release; regression tests use local fixtures.
    """
    root = Path(root).resolve()
    if not (root / "scripts").is_dir():
        raise UpdateError(f"Not an Anki Optimizer project: {root}")
    current_manifest = None
    try:
        current_manifest = load_manifest(root)
    except UpdateError:
        pass

    incomplete = _scan_incomplete_progress(root)
    if incomplete and not allow_incomplete:
        raise UpdateError(
            "An interrupted build has resumable progress. Resume or finish it before "
            "updating, or explicitly approve an update-in-progress.\n" + "\n".join(incomplete)
        )

    temp = Path(tempfile.mkdtemp(prefix="anki_optimizer_update_"))
    lock = None
    backup = None
    created = set()
    backed_up = set()
    install_started = False
    expected_release_version = None
    try:
        if staged_directory:
            staged_root = Path(staged_directory).resolve()
        else:
            if archive:
                package = Path(archive)
            else:
                release = release or check_latest(root)
                if not release.get("available"):
                    raise UpdateError("This project already has the latest stable release.")
                if not release.get("zipball_url"):
                    raise UpdateError("Latest GitHub release has no source archive.")
                expected_release_version = str(release.get("latest") or "").lstrip("vV")
                package = temp / "release.zip"
                _emit(callback, "Downloading the stable GitHub release…", step="download")
                _download(release["zipball_url"], package, callback)
            extracted = temp / "extracted"
            extracted.mkdir()
            _safe_extract(package, extracted)
            staged_root = _release_root(extracted)

        manifest = load_manifest(staged_root)
        target_version = manifest["release_version"]
        if expected_release_version and target_version != expected_release_version:
            raise UpdateError(
                f"Release tag says {expected_release_version}, but its manifest says "
                f"{target_version}. The publisher must correct the release."
            )
        _emit(callback, f"Verifying release {target_version}…", step="verify")
        verify_release(staged_root, manifest)

        staged_state_io = _load_staged_state_io(staged_root)
        live_state_path = root / STATE_REL
        if live_state_path.exists():
            before = staged_state_io.load_state(live_state_path)
        else:
            before = staged_state_io.load_template(staged_root / "scripts/project_state.template.json")
        template = staged_state_io.load_template(staged_root / "scripts/project_state.template.json")
        migrated, migration_notes = staged_state_io.migrate_state(before, template=template)
        staged_state_io.assert_preserved(before, migrated)
        before_summary = staged_state_io.summary(before)

        tests = _run_staged_selftest(staged_root, callback)
        lock = _take_lock(root)
        backup, backed_up = _backup(root, current_manifest, manifest)
        _emit(callback, f"Backup saved to {backup.name}", step="backup")

        new_paths = set(manifest["files"])
        retired_paths = (set(current_manifest["files"]) - new_paths
                         if current_manifest else set())
        installed_paths = new_paths | {MANIFEST_REL.as_posix()}
        created = {path for path in installed_paths if not (root / path).exists()}
        for generated in (STATE_REL, PROFILE_REL, Path("scripts/HANDOFF.md"),
                          Path("scripts/HANDOFF_REFERENCE.md")):
            if not (root / generated).exists():
                created.add(generated.as_posix())
        install_started = True
        for index, relative in enumerate(sorted(new_paths), start=1):
            _atomic_copy(staged_root / relative, root / relative)
            _emit(callback, f"Installed {index}/{len(new_paths)} safe toolkit files")
        # A publisher may move or rename its own files between releases. Only
        # paths named by the previously verified publisher manifest may be
        # retired. Private/runtime paths never enter this set, and every retired
        # file is already in the rollback backup.
        for relative in sorted(retired_paths):
            old_path = root / relative
            if old_path.is_file() or old_path.is_symlink():
                old_path.unlink()
                _emit(callback, f"Retired old toolkit file: {relative}")
        # The manifest cannot hash itself, so it is copied separately after every
        # file it describes has passed verification.
        _atomic_copy(staged_root / MANIFEST_REL, root / MANIFEST_REL)
        verify_release(root, manifest)

        # State is installed last, from the migrated in-memory copy. The update
        # bundle never supplies or overwrites project_state.json.
        staged_state_io.save_state(migrated, live_state_path, backup=True)
        if not (root / PROFILE_REL).exists():
            profile_template = root / "control_center/templates/PROFILE.template.md"
            if profile_template.exists():
                _atomic_copy(profile_template, root / PROFILE_REL)

        regen = subprocess.run(
            [sys.executable, str(root / "scripts/update_handoff.py")],
            cwd=str(root / "scripts"), capture_output=True, text=True, timeout=60,
        )
        if regen.returncode != 0:
            raise UpdateError("Handoff regeneration failed:\n" + regen.stderr[-1000:])

        check = subprocess.run(
            [sys.executable, str(root / "scripts/check_version.py")],
            cwd=str(root / "scripts"), capture_output=True, text=True, timeout=60,
        )
        if check.returncode != 0:
            raise UpdateError("Installed files do not match VERSION.json:\n" + check.stdout[-1000:])

        after = staged_state_io.load_state(live_state_path)
        staged_state_io.assert_preserved(before, after)
        after_summary = staged_state_io.summary(after)
        _emit(callback, "Update complete. All deck statuses and history were preserved.", step="done")
        return {
            "version": target_version,
            "backup": str(backup),
            "tests_passed": tests,
            "before": before_summary,
            "after": after_summary,
            "migration_notes": migration_notes,
            "requires_setup": bool(manifest.get("requires_setup")),
        }
    except Exception:
        if install_started and backup:
            _emit(callback, "A check failed. Restoring the previous toolkit and state…", step="rollback")
            _restore(root, backup, backed_up, created)
        raise
    finally:
        if lock:
            try:
                lock.unlink()
            except FileNotFoundError:
                pass
        _remove_tree(temp)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--archive")
    parser.add_argument("--staged-directory")
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args(argv)

    if args.check:
        print(json.dumps(check_latest(args.root), indent=2))
        return 0
    if args.install:
        def log(message, **_):
            print(message, flush=True)
        result = install_update(
            args.root, archive=args.archive, staged_directory=args.staged_directory,
            callback=log, allow_incomplete=args.allow_incomplete,
        )
        print(json.dumps(result, indent=2))
        return 0
    parser.error("choose --check or --install")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UpdateError as exc:
        print(f"UPDATE STOPPED: {exc}", file=sys.stderr)
        raise SystemExit(1)
