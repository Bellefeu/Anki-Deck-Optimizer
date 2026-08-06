#!/usr/bin/env python3
"""Build the publisher-owned allowlist used by the safe updater."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "scripts/UPDATE_MANIFEST.json"

ROOT_FILES = {
    ".gitattributes", ".gitignore", "LICENSE", "README.md", "START_HERE.md",
    "setup.sh", "setup.ps1", "PROFILE.template.md",
    "OPEN_CONTROL_CENTER.command", "OPEN_CONTROL_CENTER.cmd", "open_control_center.sh",
    "Source Files/README.md", "Anki Decks/README.md", "COMPLETED/README.md",
}

SCRIPT_FILES = {
    "deps.py", "bootstrap.py", "build_queue.py", "extract_source.py",
    "build_deck.py", "update_handoff.py", "verify_deck.py", "verify_corpus.py",
    "find_duplicates.py", "cleanup.py", "archive_inputs.py", "selftest.py",
    "check_consistency.py", "handoff.py", "deck_digest.py", "build_notes.js",
    "check_version.py", "state_io.py", "VERSION.json",
    "project_state.template.json", "handoff_template.md",
    "handoff_reference_template.md", "PROMPT_build.md", "PROMPT_verify.md",
    "PROMPT_patch.md", "PROMPT_dedupe.md", "PROMPT_auto.md", "next_action.py",
}


def digest(path):
    value = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def patchable_files():
    paths = {ROOT / name for name in ROOT_FILES}
    paths.update(ROOT / "scripts" / name for name in SCRIPT_FILES)
    control = ROOT / "control_center"
    paths.update(path for path in control.rglob("*")
                 if path.is_file() and path.name != "UPDATE_MANIFEST.json"
                 and "__pycache__" not in path.parts)
    missing = [str(path.relative_to(ROOT)) for path in paths if not path.is_file()]
    if missing:
        raise SystemExit("Cannot build manifest; files missing:\n  " + "\n  ".join(sorted(missing)))
    return sorted(paths)


def build(version, requires_setup=False):
    files = {
        path.relative_to(ROOT).as_posix(): digest(path)
        for path in patchable_files()
    }
    manifest = {
        "format": 1,
        "release_version": version,
        "state_schema": 1,
        "requires_setup": bool(requires_setup),
        "files": files,
        "preservation_contract": [
            "scripts/project_state.json",
            "PROFILE.md",
            "USER_PROMPTS.md",
            "Source Files/**",
            "Anki Decks/**",
            "COMPLETED/**",
            "Old Anki Decks and Files/**",
            "work/**",
            "scripts/work/**",
        ],
    }
    OUTPUT.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)}: {version}, {len(files)} files")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--requires-setup", action="store_true")
    args = parser.parse_args()
    build(args.version, args.requires_setup)


if __name__ == "__main__":
    main()
