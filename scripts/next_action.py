#!/usr/bin/env python3
"""Decide the next pipeline action from state alone. No human input.

This is what makes unattended scheduling work. A recurring Cowork task runs this,
gets told exactly one phase to execute, does it, and stops. When a run dies on a
usage limit, the NEXT scheduled run reads state and resumes - the schedule is the
retry loop.

    python3 next_action.py              # what should happen next?
    python3 next_action.py --claim      # same, plus take the lock
    python3 next_action.py --release    # release the lock when the phase is done
    python3 next_action.py --status     # full picture, no lock

Exit codes:  0 there is work    1 idle (nothing to do)    2 blocked (another run active)
"""

import os, sys, json, glob, time, datetime

HERE  = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "project_state.json")
WORK  = os.path.join(HERE, "work")
LOCK  = os.path.join(HERE, ".pipeline.lock")
LOCK_STALE_MIN = 180          # a phase that has run this long is presumed dead


def completed_dir():
    """Locate COMPLETED/. Order: explicit COMPLETED_DIR, then the path build_queue.py
    recorded in project_state.json, then a COMPLETED beside these scripts.

    Returns None when none of them exist, and callers must NOT read that as "no
    verify report". Every scheduled run starts from a fresh scratch dir with no
    local COMPLETED, so treating "not found" as "not verified" re-issues VERIFY on
    a module that was verified last run - forever, silently, one run per hour.
    Absolute paths also go stale: a session may reach Drive over a mount whose
    path changes between runs, so a recorded path is used only if it still exists.
    """
    env = os.environ.get("COMPLETED_DIR")
    if env:
        return env if os.path.isdir(env) else None
    try:
        rec = json.load(open(STATE)).get("paths", {}).get("completed")
    except Exception:
        rec = None
    for cand in (rec, os.path.join(HERE, "COMPLETED")):
        if cand and os.path.isdir(cand):
            return cand
    return None


def read_lock():
    if not os.path.exists(LOCK):
        return None
    try:
        lk = json.load(open(LOCK))
    except Exception:
        return None
    age = (time.time() - lk.get("ts", 0)) / 60
    lk["age_min"] = round(age, 1)
    lk["stale"] = age > LOCK_STALE_MIN
    return lk


def take_lock(action, module):
    json.dump({"action": action, "module": module, "ts": time.time(),
               "started": datetime.datetime.now().isoformat(timespec="seconds")},
              open(LOCK, "w"), indent=1)


def release_lock():
    if os.path.exists(LOCK):
        os.remove(LOCK)
        print("lock released")
    else:
        print("no lock held")


def has_verify_report(module, completed):
    return bool(glob.glob(os.path.join(completed, module, "audit", "VERIFY_REPORT_*.md")))


def build_progress(module):
    """Is a build partway through? Written by the session when batching a large deck."""
    p = os.path.join(WORK, module, "progress.json")
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p))
    except Exception:
        return None


def decide(st, completed):
    """Returns (action, module, why). Order matters: finish what is started first.

    `completed` may be None (see completed_dir). Any decision that depends on
    whether a verification report exists is then unsafe, so we refuse to guess.
    """
    mods = st.get("modules", [])
    queue = st.get("pending_modules", [])

    if completed is None and any(m.get("status") == "built-unverified" for m in mods):
        return ("BLOCKED_NO_COMPLETED", None,
                "cannot tell whether the built module has been verified")

    # 1. a build interrupted partway through - resume before starting anything new
    for m in queue:
        pr = build_progress(m["name"])
        if pr and not pr.get("complete"):
            return ("RESUME_BUILD", m["name"],
                    f"batch {pr.get('last_index','?')} of {pr.get('total','?')} done")

    # 2. a module built but never verified
    for m in mods:
        if m.get("status") == "built-unverified" and not has_verify_report(m["name"], completed):
            return ("VERIFY", m["name"], "built, no verification report yet")

    # 3. a module verified-but-failing whose patch has not been applied
    for m in mods:
        if m.get("status") == "in-progress":
            return ("PATCH", m["name"], "build did not pass all gates")

    # 4. next module in the queue
    for m in queue:
        if m.get("status") == "pending":
            # The queue stores absolute paths, recorded by whichever run built it.
            # A later run may reach the same Drive folder by a different route, so
            # check the deck is actually there before committing a whole phase to
            # it - otherwise the run dies mid-BUILD, after the lock is taken and
            # after the source has been read, which is far more expensive to
            # recover from than stopping here.
            apkg = m.get("apkg")
            if apkg and not os.path.exists(apkg):
                return ("BLOCKED_STALE_QUEUE", m["name"], apkg)
            return ("BUILD", m["name"], f"{m.get('cards_before','?')} cards, mode={m.get('mode','full')}")

    # 5. everything processed - awaiting the user's --pass decisions
    awaiting = [m["name"] for m in mods
                if m.get("status") == "built-unverified" and has_verify_report(m["name"], completed)]
    if awaiting:
        return ("AWAIT_USER", None,
                f"{len(awaiting)} module(s) verified and awaiting your --pass: "
                + ", ".join(awaiting[:5]))
    return ("IDLE", None, "queue empty, nothing built awaiting verification")


PROMPT_FOR = {
    "BUILD":        "PROMPT_build.md",
    "RESUME_BUILD": "PROMPT_build.md",
    "VERIFY":       "PROMPT_verify.md",
    "PATCH":        "PROMPT_patch.md",
}


def main():
    if "--release" in sys.argv:
        release_lock(); return 0

    if not os.path.exists(STATE):
        print("No project_state.json - run build_queue.py first."); return 1
    st = json.load(open(STATE))

    if "--status" in sys.argv:
        mods = st.get("modules", [])
        completed = completed_dir()
        print("=== PIPELINE STATUS ===")
        print(f"  COMPLETED: {completed or '!! NOT FOUND - see completed_dir()'}")
        print(f"  queued : {len(st.get('pending_modules', []))}")
        for m in mods:
            rep = ("report" if has_verify_report(m["name"], completed) else "no report") \
                  if completed else "unknown"
            print(f"  {m['name']:<38} {m.get('status','?'):<18} {rep}")
        lk = read_lock()
        if lk:
            print(f"\n  lock: {lk['action']} on {lk['module']} "
                  f"({lk['age_min']} min ago){' - STALE' if lk['stale'] else ''}")
        act, mod, why = decide(st, completed)
        print(f"\n  next: {act}" + (f" -> {mod}" if mod else "") + f"   ({why})")
        return 0

    lk = read_lock()
    if lk and not lk["stale"]:
        print(f"BLOCKED: another run is doing {lk['action']} on {lk['module']} "
              f"({lk['age_min']} min ago).")
        print("  Exit without doing anything. The next scheduled run will retry.")
        return 2
    if lk and lk["stale"]:
        print(f"  (clearing a stale lock: {lk['action']} on {lk['module']}, "
              f"{lk['age_min']} min old - presumed dead)")
        os.remove(LOCK)

    completed = completed_dir()
    action, module, why = decide(st, completed)

    if action == "BLOCKED_STALE_QUEUE":
        print(f"BLOCKED: the queued deck for '{module}' is not where the queue says it is.")
        print(f"  missing: {why}")
        print("  The queue was built in a different environment, or the file moved.")
        print("  Rebuild it against this environment, then re-run:")
        print('    python3 build_queue.py "<Source Files>" "<Anki Decks>"')
        return 2

    if action == "BLOCKED_NO_COMPLETED":
        print("BLOCKED: cannot locate COMPLETED/ - " + why + ".")
        print("  Refusing to guess: assuming 'not verified' would re-run VERIFY on an")
        print("  already-verified module every scheduled run, forever.")
        print("  Fix by pointing at it explicitly, then re-run:")
        print('    COMPLETED_DIR="<path to COMPLETED>" python3 next_action.py --claim')
        print("  Or re-run build_queue.py, which records the path in project_state.json.")
        return 2

    print(f"ACTION: {action}")
    if module:
        print(f"MODULE: {module}")
    print(f"REASON: {why}")

    if action in PROMPT_FOR:
        print(f"PROMPT: {PROMPT_FOR[action]}")
        print(f"\n  Execute {PROMPT_FOR[action]} for '{module}', then run:")
        print(f"    python3 next_action.py --release")
        if "--claim" in sys.argv:
            take_lock(action, module)
            print("  lock taken")
        return 0

    if action == "AWAIT_USER":
        print("\n  Nothing further can proceed without you. Review each module's")
        print("  JUDGEMENT CALLS section, then run:")
        print('    python3 verify_deck.py --pass "<module>"')
        return 1

    print("\n  Nothing to do. Stage decks and sources, then run build_queue.py.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
