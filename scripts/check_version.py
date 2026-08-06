#!/usr/bin/env python3
"""Detect stale or modified pipeline files.

The problem this solves: a session running an older copy of a script diagnoses a bug
that was already fixed, and hand-patches around it. That has happened. Scripts live in
two places - the handoff folder in Drive, and whatever local copy a session made - and
they drift.

    python3 check_version.py            # verify the local set against VERSION.json
    python3 check_version.py --write     # regenerate VERSION.json (do this after edits)

Called automatically by selftest.py.
"""

import os, sys, json, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "VERSION.json")

TRACKED = [
    "deps.py", "bootstrap.py", "build_queue.py", "extract_source.py", "build_deck.py",
    "update_handoff.py", "verify_deck.py", "verify_corpus.py",
    "find_duplicates.py", "cleanup.py", "archive_inputs.py", "selftest.py",
    "check_consistency.py", "handoff.py", "deck_digest.py",
    "build_notes.js", "handoff_template.md", "handoff_reference_template.md",
    "PROMPT_build.md", "PROMPT_verify.md", "PROMPT_patch.md", "PROMPT_dedupe.md",
    "PROMPT_auto.md", "next_action.py", "state_io.py", "check_version.py",
    "project_state.template.json",
]


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def write():
    man = {"version": None, "files": {}}
    for f in TRACKED:
        p = os.path.join(HERE, f)
        man["files"][f] = sha(p) if os.path.exists(p) else None
    # version = hash of all the hashes, so any change bumps it
    joined = "".join(f"{k}:{v}" for k, v in sorted(man["files"].items()))
    man["version"] = hashlib.sha256(joined.encode()).hexdigest()[:12]
    json.dump(man, open(MANIFEST, "w"), indent=1)
    print(f"VERSION.json written - pipeline version {man['version']}")
    print(f"  {sum(1 for v in man['files'].values() if v)}/{len(TRACKED)} files present")
    return man


def check(quiet=False):
    if not os.path.exists(MANIFEST):
        if not quiet:
            print("  no VERSION.json - run: python3 check_version.py --write")
        return True, None
    man = json.load(open(MANIFEST))
    missing, changed = [], []
    for f, expect in man["files"].items():
        p = os.path.join(HERE, f)
        if not os.path.exists(p):
            if expect: missing.append(f)
            continue
        if expect and sha(p) != expect:
            changed.append(f)

    ok = not missing and not changed
    if not quiet:
        print(f"  pipeline version: {man['version']}")
        if missing:
            print(f"  !! MISSING ({len(missing)}): {', '.join(missing)}")
        if changed:
            print(f"  !! DIFFERENT from the manifest ({len(changed)}): {', '.join(changed)}")
            print("     Either these are stale copies, or they were edited locally.")
            print("     A session running a stale script will diagnose bugs that are")
            print("     already fixed. Re-copy from the handoff folder, or if the edits")
            print("     are intentional run: python3 check_version.py --write")
        if ok:
            print("  all tracked files match the manifest")
    return ok, man["version"]


if __name__ == "__main__":
    if "--write" in sys.argv:
        write(); sys.exit(0)
    ok, _ = check()
    sys.exit(0 if ok else 1)
