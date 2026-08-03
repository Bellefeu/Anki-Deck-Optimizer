#!/usr/bin/env python3
"""Move a finished module's ORIGINAL inputs into an archive.

Two reasons this matters beyond tidiness:

  1. It preserves the originals. Once a module is verified, its source deck and captures
     are the only record of what the deck looked like before optimization.
  2. It keeps the input folders truthful. A finished deck left in "Anki Decks/" alongside
     unprocessed ones is how a future run eventually grabs an ALREADY-OPTIMIZED deck as
     its source and adds the gap-fill cards a second time. Moving it out makes that
     impossible rather than merely unlikely.

Layout produced:

    Old Anki Decks and Files/
      <Module>/
        Anki Deck/   <module>.apkg
        Files/       the source PDF, or the whole capture folder

Usage:
    python3 archive_inputs.py --module "<Module Name>" \\
        --decks "Anki Decks" --source "Source Files" \\
        --archive "Old Anki Decks and Files"          # dry run
    ... --yes                                          # actually move
"""

import os, sys, re, shutil, json, glob, zipfile, sqlite3, tempfile

from deps import require
require("zstandard", quiet=True)
import zstandard

HERE  = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "project_state.json")


def normalize(name):
    """Same matching rule build_queue.py uses, so archiving finds what queueing paired."""
    stem = os.path.splitext(os.path.basename(name))[0].lower()
    stem = re.sub(r"[\s_\-]+", " ", stem)
    stem = re.sub(r"\b(module|deck)\b", "", stem)
    return re.sub(r"\s+", " ", stem).strip()


def deck_name_inside(apkg):
    """The deck name recorded INSIDE the .apkg. Filenames drift; this does not."""
    try:
        with zipfile.ZipFile(apkg) as z:
            names = z.namelist()
            if "collection.anki21b" in names:
                raw = zstandard.ZstdDecompressor().decompress(
                    z.read("collection.anki21b"), max_output_size=500_000_000)
            else:
                raw = z.read("collection.anki21" if "collection.anki21" in names
                             else "collection.anki2")
        tmp = tempfile.NamedTemporaryFile(suffix=".anki21", delete=False)
        tmp.write(raw); tmp.close()
        con = sqlite3.connect(tmp.name)
        con.create_collation("unicase", lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower()))
        cur = con.cursor()
        cur.execute("select name from decks")
        decks = [n for (n,) in cur.fetchall() if n != "Default"]
        con.close(); os.unlink(tmp.name)
        if not decks:
            return None
        return max(decks, key=lambda d: d.count("\x1f")).split("\x1f")[-1]
    except Exception:
        return None


def find_deck(folder, key):
    """Match on filename first, then on the deck name inside each .apkg."""
    if not os.path.isdir(folder):
        return None
    apkgs = [os.path.join(folder, f) for f in sorted(os.listdir(folder))
             if f.lower().endswith(".apkg")]
    for p in apkgs:
        if normalize(p) == key:
            return p
    for p in apkgs:
        inner = deck_name_inside(p)
        if inner and normalize(inner) == key:
            print(f"  (matched by deck name inside the file: '{os.path.basename(p)}' -> '{inner}')")
            return p
    return None


def find(folder, key, exts=None, allow_dir=False):
    if not os.path.isdir(folder):
        return None
    for f in sorted(os.listdir(folder)):
        p = os.path.join(folder, f)
        if normalize(f) != key:
            continue
        if os.path.isdir(p):
            if allow_dir and glob.glob(os.path.join(p, "*.pdf")):
                return p
        elif exts and f.lower().endswith(exts):
            return p
    return None


def arg(flag, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def main():
    module = arg("--module")
    if not module:
        sys.exit('Required: --module "<Module Name>"')
    decks   = arg("--decks",   "Anki Decks")
    srcdir  = arg("--source", "Source Files")
    archive = arg("--archive", "Old Anki Decks and Files")
    go      = "--yes" in sys.argv

    # Only archive a module that is actually finished.
    if os.path.exists(STATE):
        st = json.load(open(STATE))
        rec = next((m for m in st.get("modules", []) if m["name"].lower() == module.lower()), None)
        if rec and rec.get("status") != "verified" and "--force" not in sys.argv:
            sys.exit(f"'{module}' is {rec.get('status')}, not verified. "
                     "Archiving its inputs now would remove the source a re-run needs. "
                     "Use --force only if you are certain.")

    key = normalize(module)
    src_deck = find_deck(decks, key)

    # Prefer the DECK FILENAME's key for locating the source, because that is the key
    # build_queue.py used to pair them. Fall back to the module-name key.
    keys = []
    if src_deck:
        keys.append(normalize(src_deck))
    keys.append(key)
    src_source = None
    for k in dict.fromkeys(keys):
        src_source = find(srcdir, k, exts=(".pdf",), allow_dir=True)
        if src_source:
            if k != key:
                print(f"  (source located by the deck's filename key '{k}', "
                      f"not the module name)")
            break

    print(f"=== ARCHIVE INPUTS: {module} ==="
          + ("" if go else "   (DRY RUN - add --yes to move)") + "\n")
    print(f"  deck   : {src_deck or 'NOT FOUND in ' + decks}")
    print(f"  source : {src_source or 'none found in ' + srcdir + ' (optimize-only module?)'}")

    if not src_deck and not src_source:
        print("\n  Nothing to archive - inputs may already have been moved.")
        return 0

    dest_root = os.path.join(archive, module)
    dest_deck = os.path.join(dest_root, "Anki Deck")
    dest_file = os.path.join(dest_root, "Files")
    print(f"\n  -> {dest_deck}")
    print(f"  -> {dest_file}")

    if not go:
        print("\n  Dry run. Re-run with --yes to move.")
        return 0

    os.makedirs(dest_deck, exist_ok=True)
    os.makedirs(dest_file, exist_ok=True)
    moved = []

    jobs = []
    if src_deck:
        jobs.append((src_deck, dest_deck, False))
    if src_source:
        # a folder of captures gets its CONTENTS moved into Files/, not the folder itself
        jobs.append((src_source, dest_file, os.path.isdir(src_source)))

    for src, dest, flatten in jobs:
        if flatten:
            for f in sorted(os.listdir(src)):
                sp, tp = os.path.join(src, f), os.path.join(dest, f)
                if os.path.exists(tp):
                    print(f"    already archived, skipping: {f}"); continue
                try:
                    shutil.move(sp, tp); moved.append(tp); print(f"    moved {f}")
                except OSError as e:
                    print(f"    !! could not move {f}: {e}")
            try:
                os.rmdir(src)
            except OSError:
                pass
            continue
        target = os.path.join(dest, os.path.basename(src))
        if os.path.exists(target):
            print(f"    already archived, skipping: {os.path.basename(src)}")
            continue
        try:
            shutil.move(src, target)
            moved.append(target)
            print(f"    moved {os.path.basename(src)}")
        except OSError as e:
            # Drive mounts sometimes refuse a move; fall back to copy + best-effort delete
            try:
                if os.path.isdir(src): shutil.copytree(src, target)
                else: shutil.copy2(src, target)
                moved.append(target)
                print(f"    copied {os.path.basename(src)} (move failed: {e})")
                try:
                    shutil.rmtree(src) if os.path.isdir(src) else os.remove(src)
                except OSError:
                    print(f"    !! could not remove the original at {src} - delete it by hand,")
                    print(f"       or a future run may treat this finished deck as input")
            except OSError as e2:
                print(f"    !! FAILED to archive {src}: {e2}")

    print(f"\n  archived {len(moved)} item(s) to {dest_root}")
    print("  The input folders now contain only unprocessed modules.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
