#!/usr/bin/env python3
"""Build the publisher-owned allowlist used by the safe updater."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "scripts/UPDATE_MANIFEST.json"

ROOT_FILES = {
    ".gitattributes", ".gitignore", "LICENSE", "README.md", "START HERE.md",
    "PRISM - Mac.command", "PRISM - Windows.cmd",
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


def tracked_files():
    """Everything git holds, or None when this is not a checkout.

    The two lists above name their files, but control_center is taken whole,
    and a real folder on a real machine collects things nobody meant to
    publish: a .DS_Store, an editor's swap file, a scratch copy. Any of them
    reaching the allowlist produces a manifest that only the machine which
    built it can satisfy, and every fresh checkout reports the file missing.
    That is a broken release rather than a broken build, which is the worse
    of the two, so the walk below is filtered by what git actually tracks.
    """
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z"], capture_output=True, text=True,
            cwd=str(ROOT), timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if listed.returncode != 0:
        return None
    return {ROOT / name for name in listed.stdout.split("\0") if name}


def patchable_files():
    paths = {ROOT / name for name in ROOT_FILES}
    paths.update(ROOT / "scripts" / name for name in SCRIPT_FILES)
    control = ROOT / "control_center"
    carried = [path for path in control.rglob("*")
               if path.is_file() and path.name != "UPDATE_MANIFEST.json"
               and "__pycache__" not in path.parts]
    known = tracked_files()
    if known is not None:
        carried = [path for path in carried if path in known]
    paths.update(carried)
    missing = [str(path.relative_to(ROOT)) for path in paths if not path.is_file()]
    if missing:
        raise SystemExit("Cannot build manifest; files missing:\n  " + "\n  ".join(sorted(missing)))
    return sorted(paths)


def eol_attributes(relatives):
    """The eol attribute git would apply to each path on checkout."""
    try:
        asked = subprocess.run(
            ["git", "check-attr", "eol", "--stdin", "-z"],
            input="\0".join(relatives), capture_output=True, text=True,
            cwd=str(ROOT), timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if asked.returncode != 0:
        return {}
    # NUL separated triples: path, attribute, value.
    fields = asked.stdout.split("\0")
    return {fields[index]: fields[index + 2]
            for index in range(0, len(fields) - 2, 3)}


def checkout_bytes(data, eol):
    """``data`` as git would write it into a fresh checkout."""
    if eol == "crlf":
        return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    if eol == "lf":
        return data.replace(b"\r\n", b"\n")
    return data


def unnormalised(relatives):
    """Files whose working copy is not what a fresh checkout would hold.

    The manifest is hashed from the working tree, but every consumer reads a
    checkout instead: a clone, a GitHub zipball, or the payload build.py
    copies out of a runner's checkout. A file edited by a tool that writes
    LF where .gitattributes says CRLF hashes correctly on the machine that
    built the manifest and nowhere else, which shows up as an integrity
    failure on every other computer and never on this one.
    """
    attributes = eol_attributes(relatives)
    wrong = []
    for relative in relatives:
        eol = attributes.get(relative, "")
        if eol not in ("crlf", "lf"):
            continue
        data = (ROOT / relative).read_bytes()
        if data != checkout_bytes(data, eol):
            wrong.append((relative, eol))
    return wrong


def build(version, requires_setup=False):
    paths = patchable_files()
    relatives = [path.relative_to(ROOT).as_posix() for path in paths]
    wrong = unnormalised(relatives)
    if wrong:
        detail = "\n  ".join(f"{name} should be checked out as {eol}"
                             for name, eol in wrong)
        raise SystemExit(
            "These files do not match what git would check out, so a manifest "
            "built from them would fail on every computer except this one:\n  "
            + detail
            + "\n\nLet git rewrite them, then build the manifest again:\n"
            + "\n".join(f'  rm "{name}" && git checkout -- "{name}"'
                        for name, _ in wrong)
        )
    files = {
        path.relative_to(ROOT).as_posix(): digest(path)
        for path in paths
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
