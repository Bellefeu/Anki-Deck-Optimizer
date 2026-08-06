#!/usr/bin/env python3
"""Safe reads, migrations, and atomic writes for pipeline runtime state.

`project_state.json` is the pipeline's memory.  A partial write can lose verified
statuses, paths, and the audit history, so every writer goes through this module.
The shipped template is code; the runtime file is user data and is never replaced
by an update.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
STATE = HERE / "project_state.json"
TEMPLATE = HERE / "project_state.template.json"
CURRENT_SCHEMA = 1

_RUNTIME_DEFAULTS = {
    "run_count": lambda: 0,
    "queue_built": lambda: False,
    "modules": list,
    "pending_modules": list,
    "paths": dict,
    "session_log": list,
}


class StateError(RuntimeError):
    """The state file is missing, malformed, or would lose history."""


def _json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def atomic_write_bytes(path, payload: bytes, *, backup=False) -> Path:
    """Replace one file atomically, optionally keeping its prior bytes as `.bak`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        backup_path = path.with_name(path.name + ".bak")
        _atomic_replace(backup_path, path.read_bytes())
    _atomic_replace(path, payload)
    return path


def _atomic_replace(path: Path, payload: bytes) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                    dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        # Best effort: make the directory entry durable on POSIX. Windows cannot
        # open a directory this way, and os.replace is already atomic there.
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except (OSError, TypeError):
            pass
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def atomic_write_json(path, value, *, backup=False) -> Path:
    return atomic_write_bytes(path, _json_bytes(value), backup=backup)


def validate_state(state) -> None:
    if not isinstance(state, dict):
        raise StateError("project_state.json must contain one JSON object")
    for key in ("modules", "pending_modules", "session_log"):
        if key in state and not isinstance(state[key], list):
            raise StateError(f"state key {key!r} must be a list")
    if "paths" in state and not isinstance(state["paths"], dict):
        raise StateError("state key 'paths' must be an object")
    if "run_count" in state and (not isinstance(state["run_count"], int)
                                 or state["run_count"] < 0):
        raise StateError("state key 'run_count' must be a non-negative integer")
    names = [m.get("name") for m in state.get("modules", []) if isinstance(m, dict)]
    duplicates = sorted({name for name in names if name and names.count(name) > 1})
    if duplicates:
        raise StateError("duplicate module records: " + ", ".join(duplicates))


def normalize_state(state) -> list[str]:
    """Add missing v1 runtime keys without replacing any existing value."""
    validate_state(state)
    added = []
    if "schema_version" not in state:
        state["schema_version"] = 1
        added.append("schema_version")
    for key, make_default in _RUNTIME_DEFAULTS.items():
        if key not in state:
            state[key] = make_default()
            added.append(key)
    return added


def _merge_pipeline_knowledge(state, template) -> list[str]:
    """Add publisher knowledge while retaining every user-added record."""
    merged = []
    known_findings = set(state.setdefault("environment_findings", []))
    for finding in template.get("environment_findings", []):
        if finding not in known_findings:
            state["environment_findings"].append(finding)
            known_findings.add(finding)
            merged.append("environment finding")

    incidents = state.setdefault("incidents", [])
    incident_keys = {(item.get("date"), item.get("issue"))
                     for item in incidents if isinstance(item, dict)}
    for item in template.get("incidents", []):
        key = (item.get("date"), item.get("issue"))
        if key not in incident_keys:
            incidents.append(copy.deepcopy(item))
            incident_keys.add(key)
            merged.append("incident")
    return merged


def migrate_state(state, *, template=None) -> tuple[dict, list[str]]:
    """Return a migrated copy. Sequential migrations will be added here."""
    migrated = copy.deepcopy(state)
    changes = normalize_state(migrated)
    version = migrated.get("schema_version", 1)
    if not isinstance(version, int) or version < 1:
        raise StateError(f"unsupported state schema: {version!r}")
    if version > CURRENT_SCHEMA:
        raise StateError(
            f"state schema {version} is newer than this updater supports "
            f"({CURRENT_SCHEMA}); refusing to downgrade it"
        )

    # Future shape changes belong in explicit, one-step migrations:
    # if version == 1: migrated = migrate_v1_to_v2(migrated); version = 2

    if template is not None:
        changes.extend(_merge_pipeline_knowledge(migrated, template))
    validate_state(migrated)
    return migrated, changes


def load_template(path=TEMPLATE) -> dict:
    path = Path(path)
    if not path.exists():
        raise StateError(f"state template is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"state template is unreadable: {exc}") from exc
    validate_state(value)
    return value


def ensure_state(path=STATE, template_path=TEMPLATE) -> Path:
    path = Path(path)
    if path.exists():
        return path
    template = load_template(template_path)
    migrated, _ = migrate_state(template, template=template)
    atomic_write_json(path, migrated)
    return path


def load_state(path=STATE, *, create=False, template_path=TEMPLATE) -> dict:
    path = Path(path)
    if create:
        ensure_state(path, template_path)
    if not path.exists():
        raise StateError(f"state file is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"project_state.json is unreadable: {exc}") from exc
    validate_state(value)
    return value


def save_state(state, path=STATE, *, backup=True) -> Path:
    validate_state(state)
    return atomic_write_json(path, state, backup=backup)


def state_invariants(state) -> dict:
    """History that an updater is never allowed to silently remove or rewrite."""
    validate_state(state)
    modules = []
    for item in state.get("modules", []):
        modules.append({
            "name": item.get("name"),
            "deck_id": item.get("deck_id"),
            "status": item.get("status"),
        })
    return {
        "run_count": state.get("run_count", 0),
        "modules": modules,
        "session_log": copy.deepcopy(state.get("session_log", [])),
        "pending_modules": copy.deepcopy(state.get("pending_modules", [])),
    }


def assert_preserved(before, after) -> None:
    """Fail if migration changed existing progress or verification decisions."""
    b = state_invariants(before)
    a = state_invariants(after)
    for key in ("run_count", "modules", "session_log", "pending_modules"):
        if a[key] != b[key]:
            raise StateError(f"update would change preserved state key {key!r}")


def summary(state) -> dict:
    modules = state.get("modules", [])
    counts = {}
    for item in modules:
        status = item.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {
        "modules": len(modules),
        "verified": counts.get("verified", 0),
        "built_unverified": counts.get("built-unverified", 0),
        "in_progress": counts.get("in-progress", 0),
        "run_count": state.get("run_count", 0),
        "statuses": counts,
    }


if __name__ == "__main__":
    current = load_state(create=True)
    migrated, notes = migrate_state(current, template=load_template())
    assert_preserved(current, migrated)
    save_state(migrated)
    print(json.dumps({"summary": summary(migrated), "changes": notes}, indent=2))
