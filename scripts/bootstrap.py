#!/usr/bin/env python3
"""First command to run in a fresh copy. Verifies the environment, then tells you
exactly what to do next.

    python3 scripts/bootstrap.py

Idempotent - run it again any time something feels off. It does not touch your
inputs, your decks, or anything in COMPLETED/.
"""

import os, sys, shutil, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

OK, WARN, BAD = "  ok  ", " warn ", " FAIL "
problems, warnings = [], []


def say(tag, msg, detail=""):
    print(f"[{tag}] {msg}" + (f"\n         {detail}" if detail else ""))


PROFILE = """# PROFILE — your preferences, not the pipeline's rules

Read at the start of every session, right after `scripts/HANDOFF.md`. Everything here
is a judgement call that is legitimately yours. Everything in HANDOFF.md is not:
the rubric in §3b, the gates in §5 and the format in §3 are rules, and a session that
wants to bend one should say so in its report rather than quietly doing it.

Edit freely. These are the defaults the pipeline was built around, not commandments.

## Corrections

- **Fix factual and clinical errors directly; do not stop to ask.** Log every change in
  the NOTES doc. Established medical fact outranks the source for correctness; the
  source only decides scope (Rule 7).
- Surface genuine inter-source disagreements in the NOTES doc's `verify_items`, never
  inside a card.

## Scheduling history

- **Not preserved.** The reference build was resetting its collection, and the scripts
  assume that.
- If you need to keep your review history, decide now, not after the first build: it
  changes how a rebuilt deck must be imported. Say so explicitly in your first session.

## Scope of work

- One module at a time. Pilot on a small deck before scaling.
- Cleanup and source gap-fill combined per module, not as separate passes.

## Image-occlusion figures

- Crop to the relevant figure only, not the full page.
- Source figures are a **visual reference for you**, never card content. Build the card
  with your own image.

## Effort and cost

- Thoroughness over speed. The rubric is per-card and unconditional.
- The pipeline is built to be economical without cutting rigor: prose is read as OCR
  text, only untranscribable regions are read as pixels, and `unaccounted_ink_px == 0`
  proves nothing was skipped. If you ever distrust that, set `COVERAGE=page` and it
  reads whole pages like it used to.
- Delegate the visual read and the hostile audit to subagents. They read; they never
  decide. Every op still goes through `ops.json`.

## Your modules

List what you intend to work through, so a session can see the plan:

- (add yours)
"""


def main():
    print("=== ANKI DECK OPTIMIZATION — BOOTSTRAP ===\n")
    print(f"project root: {ROOT}\n")

    # 1. folder skeleton
    print("--- 1. Folder skeleton ---")
    for d in ("Source Files", "Anki Decks", "COMPLETED", "work"):
        p = os.path.join(ROOT, d)
        if os.path.isdir(p):
            say(OK, f"{d}/")
        else:
            os.makedirs(p, exist_ok=True)
            say(OK, f"{d}/  (created)")

    # 2. dependencies
    print("\n--- 2. Dependencies ---")
    try:
        from deps import require
        require("PIL", "numpy", "zstandard",
                cli=["pdftotext", "pdftoppm", "pdfinfo", "pdfimages", "tesseract"],
                quiet=True)
        say(OK, "python packages and CLI tools")
    except SystemExit:
        problems.append("dependencies missing - open Prism and run guided setup")
        say(BAD, "dependencies missing",
            "open Prism and click Open guided setup; it installs everything, "
            "then re-runs this check")
    except Exception as e:
        problems.append(f"deps check failed: {e}")
        say(BAD, "deps check failed", str(e))

    if shutil.which("node"):
        say(OK, "node (needed for the NOTES doc)")
    else:
        warnings.append("node not found - build_notes.js cannot run; guided setup installs it")
        say(WARN, "node not found",
            "the NOTES doc step will fail until you run Prism's guided setup")

    # 3. state file
    print("\n--- 3. Project state ---")
    sp = os.path.join(HERE, "project_state.json")
    try:
        from state_io import (ensure_state, load_state, load_template,
                              migrate_state, save_state)
        created = not os.path.exists(sp)
        ensure_state(sp)
        st = load_state(sp)
        migrated, added = migrate_state(st, template=load_template())
        if created or added:
            save_state(migrated, sp, backup=not created)
            st = migrated
            detail = ", ".join(added) if added else "copied the safe template"
            say(OK, "initialized missing runtime state", detail)
        if st.get("modules") or st.get("run_count"):
            say(WARN, f"state already has {len(st.get('modules', []))} module(s), "
                      f"run_count={st.get('run_count')}",
                      "expected in a kit you have already used; unexpected in a fresh clone")
        else:
            say(OK, "clean - 0 modules, run_count 0")
        say(OK, f"{len(st.get('incidents', []))} incidents + "
                f"{len(st.get('environment_findings', []))} environment findings retained",
                "these are pipeline knowledge, not history - do not clear them")
        if st.get("paths"):
            say(WARN, "paths block is non-empty",
                      "build_queue.py rewrites it for this machine on the next run")
    except Exception as e:
        problems.append(f"project_state.json unreadable: {e}")
        say(BAD, "project_state.json unreadable", str(e))

    # 4. profile
    print("\n--- 4. Your preferences ---")
    pf = os.path.join(ROOT, "PROFILE.md")
    if os.path.exists(pf):
        say(OK, "PROFILE.md exists (left alone)")
    else:
        template = os.path.join(ROOT, "control_center", "templates", "PROFILE.template.md")
        from state_io import atomic_write_bytes
        content = open(template, "rb").read() if os.path.exists(template) else PROFILE.encode("utf-8")
        atomic_write_bytes(pf, content)
        say(OK, "PROFILE.md created from defaults", "read it and edit before your first run")

    # 5. handoff integrity
    print("\n--- 5. Handoff ---")
    if not os.path.exists(os.path.join(HERE, "HANDOFF.md")):
        r = subprocess.run([sys.executable, os.path.join(HERE, "update_handoff.py")],
                           capture_output=True, text=True, cwd=HERE)
        say(OK if r.returncode == 0 else BAD, "HANDOFF.md generated")
    r = subprocess.run([sys.executable, os.path.join(HERE, "handoff.py"), "check"],
                       capture_output=True, text=True, cwd=HERE)
    if r.returncode == 0:
        say(OK, "handoff split intact - no section lost")
    else:
        problems.append("handoff.py check failed")
        say(BAD, "handoff.py check failed", r.stdout.strip()[:200])

    # 6. version manifest
    print("\n--- 6. Script versions ---")
    r = subprocess.run([sys.executable, os.path.join(HERE, "check_version.py")],
                       capture_output=True, text=True, cwd=HERE)
    print("        " + r.stdout.strip().replace("\n", "\n        "))
    if "match the manifest" not in r.stdout:
        warnings.append("check_version reports drift - re-run with --write if you edited a script on purpose")

    # 7. selftest
    print("\n--- 7. Self-test (about 30s) ---")
    if os.environ.get("SKIP_SELFTEST"):
        say(WARN, "skipped (SKIP_SELFTEST set)")
    else:
        r = subprocess.run([sys.executable, os.path.join(HERE, "selftest.py")],
                           capture_output=True, text=True, cwd=HERE)
        tail = [l for l in r.stdout.strip().split("\n") if "passed" in l or "FAIL" in l]
        print("        " + "\n        ".join(tail[-6:]) if tail else "        (no output)")
        if r.returncode != 0:
            problems.append("selftest failed - do NOT process a real module until it passes")
            say(BAD, "self-test failed")
        else:
            say(OK, "self-test passed")

    # ---- verdict
    print("\n" + "=" * 60)
    if problems:
        print("  NOT READY\n")
        for p in problems:
            print(f"    - {p}")
        print("\n  Fix these, then run bootstrap again.")
        return 1
    if warnings:
        print("  READY, with warnings\n")
        for w in warnings:
            print(f"    - {w}")
        print()
    else:
        print("  READY\n")

    print("""  Next:

    1. Return to the Prism Control Center. If it is not open, double-click:
         Windows: PRISM - Windows.cmd
         Mac:     PRISM - Mac.command
    2. Open Prefs, read your profile, and click Save preferences.
    3. Open Home, enter a module name, and drop in source files and/or an .apkg.
    4. Follow START HERE.md to run the pipeline with your agent.

       Terminal steps are still available in START HERE.md if you prefer them.

  Read first, in this order:
    START HERE.md                 stepwise walkthrough - every command, in order
    COMPLETED/EXAMPLE/README.md   what a finished module looks like
    scripts/HANDOFF.md            the job. §3b is the actual rubric.
    scripts/HANDOFF_REFERENCE.md  do NOT read whole - pull sections on demand
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
