#!/usr/bin/env python3
"""Delete regenerable scratch. Preserve the audit trail. Never touch deliverables.

Roughly 15 MB per module is page images and unpacked databases - about 1.5 GB across
100 modules - and all of it regenerates from the source PDF in one command. What must
survive is tiny (~60 KB per module) and irreplaceable: the record of what was decided
and why.

SAFETY: by default only cleans modules whose status is "verified" in
project_state.json. Before verification the page images may still be needed - a failed
verification that forces a re-read would otherwise mean re-OCRing the whole module.

    python3 cleanup.py                        # dry run, verified modules only
    python3 cleanup.py --yes                  # actually delete
    python3 cleanup.py --yes --all            # include unverified (asks first)
    python3 cleanup.py --yes --module "X"     # one module
    python3 cleanup.py --root "<path>/_work"  # clean a DIFFERENT work dir (e.g. in Drive)
    python3 cleanup.py --yes --sweep --root "<path>/_work"
                                              # clear an old backlog regardless of status
    python3 cleanup.py --purge --root "<path>/_work" --completed "<path>/COMPLETED"
                                              # EMPTY the whole scratch root (dry run)
    python3 cleanup.py --purge --yes --root "<path>/_work" --completed "<path>/COMPLETED"
"""

import os, sys, json, shutil

HERE  = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "project_state.json")
WORK  = os.path.join(HERE, "work")

# Regenerable in one command from the source PDF - safe to delete.
DISPOSABLE_DIRS  = ["apex/pages", "apex/strips", "_build", "apex/figs", "io_figs"]
DISPOSABLE_FILES = ["apex/manifest.json"]          # resume checkpoint, useless once done

# The audit trail. Small, irreplaceable, never deleted.
KEEP = {"ops.json", "fixes.json", "new_cards.json", "meta.json",
        "changelog.json", "progress.json", "extract_report.json",
        "content.txt", "content_ocr.txt"}


def warn_if_maybe_dehydrated(path, label):
    """A cloud-sync mount serves files on demand. A folder that reads as empty may be
    dehydrated rather than actually empty - `find` returning nothing means 'not
    materialized right now', not 'not there'. Never treat emptiness as fact on a mount."""
    if not os.path.isdir(path):
        print(f"  !! {label} does not exist: {path}")
        return
    entries = os.listdir(path)
    if entries:
        return
    marker = any(m in path for m in
                 ("CloudStorage", "Google Drive", "GoogleDrive", "My Drive", "/Volumes/"))
    print(f"  !! {label} reads as EMPTY: {path}")
    if marker:
        print("     This path looks like a cloud-sync mount. An empty read there usually")
        print("     means the files are not materialized locally, NOT that they are absent.")
        print("     Open the folder in Finder/Explorer to force hydration, or mark it")
        print("     'available offline', then re-run. Do NOT conclude the folder is empty.")


def size_of(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    t = 0
    for r, _, fs in os.walk(path):
        for f in fs:
            try: t += os.path.getsize(os.path.join(r, f))
            except OSError: pass
    return t


def targets(moddir):
    out = []
    for d in DISPOSABLE_DIRS:
        p = os.path.join(moddir, d)
        if os.path.isdir(p): out.append(p)
    for f in DISPOSABLE_FILES:
        p = os.path.join(moddir, f)
        if os.path.isfile(p): out.append(p)
    # any local copy of a source PDF/deck - the originals live in Drive
    for r, _, fs in os.walk(moddir):
        for f in fs:
            if f.lower().endswith((".pdf", ".apkg")):
                p = os.path.join(r, f)
                # skip anything already covered by a directory above
                if not any(p.startswith(d + os.sep) for d in out if os.path.isdir(d)):
                    out.append(p)
    return out


def do_purge(root, go, argv):
    """Empty an entire scratch root. Everything in it is either a copy of a script that
    lives authoritatively in the handoff folder, or regenerable module scratch."""
    completed = None
    if "--completed" in argv:
        completed = argv[argv.index("--completed") + 1]

    print(f"=== PURGE SCRATCH ROOT ===  [{root}]"
          + ("" if go else "   (DRY RUN - add --yes to delete)") + "\n")

    st = json.load(open(STATE)) if os.path.exists(STATE) else {"modules": []}
    mods = st.get("modules", [])
    unverified = [m["name"] for m in mods if m.get("status") != "verified"]

    print("  SAFETY CHECKS")
    ok = True
    if unverified:
        print(f"    !! {len(unverified)} module(s) not verified: {', '.join(unverified[:5])}")
        print("       Their scratch may still be needed. Purge only verified work.")
        ok = False
    else:
        print(f"    all {len(mods)} tracked module(s) verified")

    if completed:
        for m in mods:
            adir = os.path.join(completed, m["name"], "audit")
            has = os.path.isdir(adir) and os.listdir(adir)
            print(f"    audit trail for {m['name']}: {'present' if has else 'MISSING'}")
            if not has: ok = False
    else:
        print("    (pass --completed \"<COMPLETED path>\" to verify audit trails exist first)")

    # orphan check - a scratch folder with no verified module behind it is in-progress work
    verified = {m["name"].lower() for m in mods if m.get("status") == "verified"}
    KNOWN_NONMODULE = {"__pycache__", "work", "_out", "_build", "logs", ".ipynb_checkpoints"}
    orphans = []
    for sub in (os.listdir(os.path.join(root, "work")) if os.path.isdir(os.path.join(root, "work")) else []):
        if os.path.isdir(os.path.join(root, "work", sub)) and sub.lower() not in verified:
            orphans.append(f"work/{sub}")
    for sub in (os.listdir(root) if os.path.isdir(root) else []):
        p = os.path.join(root, sub)
        if os.path.isdir(p) and sub not in KNOWN_NONMODULE and sub.lower() not in verified:
            if any(f.endswith((".json", ".apkg")) for f in os.listdir(p)) or \
               os.path.isdir(os.path.join(p, "apex")):
                orphans.append(sub)
    if orphans:
        print(f"    !! {len(orphans)} scratch folder(s) with no verified module behind them:")
        for o in orphans[:6]: print(f"       {o}")
        print("       This looks like work in progress, or a build that crashed before")
        print("       recording itself. Refusing to purge.")
        ok = False
    else:
        print("    no orphaned module scratch")

    entries = sorted(os.listdir(root)) if os.path.isdir(root) else []
    if not entries:
        print("\n  Already empty."); return 0

    total = sum(size_of(os.path.join(root, e)) for e in entries)
    print(f"\n  {len(entries)} item(s), {total/1e6:.1f} MB:")
    for e in entries:
        p = os.path.join(root, e)
        kind = "dir " if os.path.isdir(p) else "file"
        print(f"    {kind}  {e}   ({size_of(p)/1e6:.2f} MB)")

    if not ok:
        print("\n  SAFETY CHECKS FAILED - refusing to purge.")
        print("  Verify the outstanding modules first, or use --force if you are certain.")
        if "--force" not in argv:
            return 1
        print("  --force given; proceeding anyway.")

    if not go:
        print("\n  Dry run. Re-run with --yes to delete.")
        return 0

    freed = 0
    for e in entries:
        p = os.path.join(root, e)
        try:
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
            freed += 1
        except OSError as err:
            print(f"    could not remove {e}: {err}")
    import glob as _g
    for pat in ("*.dupdb", "*.corpus", "*.anki21", "*.db", "*.apkg.db"):
        for p in _g.glob(os.path.join("/tmp", pat)):
            try: os.remove(p)
            except OSError: pass
    print(f"\n  removed {freed}/{len(entries)} item(s), reclaimed ~{total/1e6:.1f} MB")
    print("  Scripts are re-copied from the handoff folder each session; nothing here was unique.")
    return 0


def main():
    purge  = "--purge" in sys.argv
    go     = "--yes" in sys.argv
    do_all = "--all" in sys.argv or "--sweep" in sys.argv
    sweep  = "--sweep" in sys.argv
    only   = sys.argv[sys.argv.index("--module") + 1] if "--module" in sys.argv else None
    work   = sys.argv[sys.argv.index("--root") + 1] if "--root" in sys.argv else WORK

    globals()["WORK"] = work
    if not os.path.isdir(work):
        print(f"No work directory at {work} - nothing to clean.")
        return 0
    warn_if_maybe_dehydrated(work, "work directory")

    status = {}
    if os.path.exists(STATE):
        st = json.load(open(STATE))
        status = {m["name"]: m.get("status", "?") for m in st.get("modules", [])}

    mods = sorted(d for d in os.listdir(work) if os.path.isdir(os.path.join(work, d)))
    if only:
        mods = [m for m in mods if m.lower() == only.lower()]
        if not mods: sys.exit(f"No scratch folder for '{only}'")

    if purge:
        return do_purge(work, go, sys.argv)

    print("=== SCRATCH CLEANUP ===" + (f"  [{work}]" if work != WORK else "")
          + ("" if go else "   (DRY RUN - add --yes to actually delete)") + "\n")
    if sweep:
        print("  SWEEP MODE - ignoring verification status. Use for clearing an old backlog.\n")

    total, skipped, plan = 0, [], []
    for m in mods:
        moddir = os.path.join(work, m)
        st = status.get(m, "unknown")
        if st != "verified" and not do_all and not sweep:
            skipped.append((m, st))
            continue
        tg = targets(moddir)
        sz = sum(size_of(p) for p in tg)
        kept = sum(size_of(os.path.join(moddir, f))
                   for f in os.listdir(moddir)
                   if os.path.isfile(os.path.join(moddir, f)) and f in KEEP)
        if tg:
            plan.append((m, st, tg, sz, kept))
            total += sz

    for m, st, tg, sz, kept in plan:
        print(f"  {m}   [{st}]")
        print(f"    delete {len(tg)} item(s), {sz/1e6:.1f} MB")
        for p in tg[:4]:
            print(f"      - {os.path.relpath(p, work)}")
        if len(tg) > 4: print(f"      ... and {len(tg)-4} more")
        print(f"    keep audit trail: {kept/1000:.0f} KB")

    if skipped:
        print("\n  SKIPPED (not verified - page images may still be needed):")
        for m, st in skipped:
            print(f"    {m}   [{st}]")
        print("    Use --all to include these, once you are sure.")

    if not plan:
        print("\n  Nothing to clean.")
        return 0

    print(f"\n  total reclaimable: {total/1e6:.1f} MB")

    if not go:
        print("\n  Dry run. Re-run with --yes to delete.")
        return 0

    if do_all and skipped:
        r = input("\n  --all includes UNVERIFIED modules. Type 'yes' to confirm: ").strip()
        if r.lower() != "yes":
            print("  Aborted."); return 1

    # tools leave scratch databases in /tmp; harmless but they accumulate
    import glob as _g
    tmp = [p for pat in ("*.dupdb", "*.corpus", "*.anki21", "*.db", "*.apkg.db")
           for p in _g.glob(os.path.join("/tmp", pat))]
    if tmp:
        tsz = sum(size_of(p) for p in tmp)
        print(f"  plus {len(tmp)} stray temp file(s) in /tmp ({tsz/1e6:.1f} MB)")
        total += tsz

    freed = 0
    if go:
        for p in tmp:
            try: os.remove(p); freed += 1
            except OSError: pass
    for m, st, tg, sz, kept in plan:
        for p in tg:
            try:
                if os.path.isdir(p): shutil.rmtree(p)
                else: os.remove(p)
                freed += 1
            except OSError as e:
                print(f"    could not remove {p}: {e}")
    print(f"\n  removed {freed} item(s), reclaimed ~{total/1e6:.1f} MB")
    print("  Audit trail intact. Page images regenerate with extract_apex.py if ever needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
