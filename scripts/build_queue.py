#!/usr/bin/env python3
"""Scan the Apex PDF folder and the Anki deck folder, pair them by filename,
read each deck's real deck_id straight out of the .apkg, and write the result
into project_state.json as the pending work queue.

Run this once after dropping in new files. Then each module run just takes the
next item off the queue - no per-module hand-editing of deck IDs or paths.

Usage:
    python3 build_queue.py                      # uses default paths below
    python3 build_queue.py <pdf_dir> <deck_dir> # or point it somewhere else
"""

import os, sys, json, re, zipfile, sqlite3, tempfile, glob

from deps import require
require("zstandard", quiet=True)
import zstandard

HERE  = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "project_state.json")

# Defaults assume the Google Drive desktop mount. Override via argv.
DEFAULT_SRC_DIR  = os.environ.get("SOURCE_DIR", "./Source Files")
DEFAULT_DECK_DIR = os.environ.get("ANKI_DECK_DIR", "./Anki Decks")


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


def normalize(name):
    """Filename -> match key. Tolerates spaces/underscores/hyphens/case drift,
    so 'Regional_-_Truncal.apkg' pairs with 'Regional - Truncal.pdf'."""
    stem = os.path.splitext(os.path.basename(name))[0]
    stem = stem.lower()
    stem = re.sub(r"[\s_\-]+", " ", stem)       # collapse separators
    stem = re.sub(r"\b(apex|module|deck)\b", "", stem)   # drop noise words
    return re.sub(r"\s+", " ", stem).strip()


def deck_info(apkg_path):
    """Open an .apkg and return (deck_id, deck_name, note_count) for the
    deepest non-Default deck - that's the real subject deck."""
    with zipfile.ZipFile(apkg_path) as z:
        names = z.namelist()
        if "collection.anki21b" in names:
            blob = z.read("collection.anki21b")
            raw = zstandard.ZstdDecompressor().decompress(blob, max_output_size=500_000_000)
        elif "collection.anki21" in names:
            raw = z.read("collection.anki21")
        else:
            raw = z.read("collection.anki2")

    tmp = tempfile.NamedTemporaryFile(suffix=".anki21", delete=False)
    tmp.write(raw); tmp.close()

    con = sqlite3.connect(tmp.name)
    con.create_collation("unicase", lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower()))
    cur = con.cursor()
    cur.execute("select id, name from decks")
    decks = [(i, n) for i, n in cur.fetchall() if n != "Default"]
    # deepest path = the actual subject deck
    deck_id, deck_name = max(decks, key=lambda d: d[1].count("\x1f")) if decks else (None, None)
    cur.execute("select count(*) from notes"); n_notes = cur.fetchone()[0]
    con.close()
    os.unlink(tmp.name)

    pretty = deck_name.split("\x1f")[-1] if deck_name else None
    return deck_id, pretty, deck_name, n_notes


def resolve_dir(argv_val, env_var, state_key, default):
    """argv > environment > the path a previous run recorded > local default.

    `env_var` and `state_key` may each be a tuple, tried in order. That is how the
    old APEX_PDF_DIR / apex_pdf_dir names keep working after the input folder was
    renamed to "Source Files" - a stale environment or a state file written by an
    older run still resolves instead of silently falling through to the default,
    which would surface as an empty scan and read exactly like "nothing to do".

    A recorded path is re-validated before it is trusted. A later run may reach the
    same folder by a different route than the run that recorded it, and an absolute
    path that no longer resolves must never be accepted silently - it would surface
    as an empty scan, which reads exactly like "nothing left to do"."""
    if argv_val:
        return argv_val
    env_vars = (env_var,) if isinstance(env_var, str) else tuple(env_var)
    keys     = (state_key,) if isinstance(state_key, str) else tuple(state_key)
    for ev in env_vars:
        v = os.environ.get(ev)
        if v:
            return v
    try:
        with open(STATE, encoding="utf-8") as f:
            paths = json.load(f).get("paths", {})
    except Exception:
        paths = {}
    for k in keys:
        rec = paths.get(k)
        if rec and os.path.isdir(rec):
            print(f"  using recorded {k}: {rec}")
            return rec
    return default


def main():
    pdf_dir  = resolve_dir(sys.argv[1] if len(sys.argv) > 1 else None,
                           ("SOURCE_DIR", "APEX_PDF_DIR"),
                           ("source_dir", "apex_pdf_dir"), DEFAULT_SRC_DIR)
    deck_dir = resolve_dir(sys.argv[2] if len(sys.argv) > 2 else None,
                           "ANKI_DECK_DIR", "deck_dir", DEFAULT_DECK_DIR)

    for d, lbl in ((pdf_dir, "PDF"), (deck_dir, "deck")):
        if not os.path.isdir(d):
            sys.exit(f"ERROR: {lbl} folder not found: {d}")
        warn_if_maybe_dehydrated(d, f"{lbl} folder")

    # An Apex source is EITHER a single .pdf OR a folder of capture PDFs.
    # extract_apex.py accepts both; the queue must too, or a folder of captures
    # silently downgrades the module to optimize-only and skips gap-fill.
    pdfs = {}
    for f in os.listdir(pdf_dir):
        p = os.path.join(pdf_dir, f)
        if f.lower().endswith(".pdf"):
            pdfs[normalize(f)] = p
        elif os.path.isdir(p) and glob.glob(os.path.join(p, "*.pdf")):
            pdfs[normalize(f)] = p
    decks = {normalize(f): os.path.join(deck_dir, f)
             for f in os.listdir(deck_dir) if f.lower().endswith(".apkg")}

    matched   = sorted(set(pdfs) & set(decks))
    pdf_only  = sorted(set(pdfs) - set(decks))
    deck_only = sorted(set(decks) - set(pdfs))

    print(f"PDFs found:  {len(pdfs)}")
    print(f"Decks found: {len(decks)}")
    print(f"PAIRED:      {len(matched)}\n")

    with open(STATE, encoding="utf-8") as f:
        st = json.load(f)

    # Every module already tracked is out of the queue, whatever its status. Only
    # "verified" was excluded before, which is safe when the queue is built once by
    # hand but not when every unattended run refreshes it: a module sitting at
    # built-unverified (the normal resting state, since --pass is the user's call)
    # would be re-queued and rebuilt from scratch, discarding its verification.
    done = {m["name"]: m.get("status", "?") for m in st.get("modules", [])}

    queue = []

    def add(key, deck_path, pdf_path, mode):
        try:
            did, pretty, full, n = deck_info(deck_path)
        except Exception as e:
            print(f"  !! could not read {os.path.basename(deck_path)}: {e}")
            return
        if pretty in done:
            print(f"  -- skipping {pretty} (already tracked: {done[pretty]})")
            return
        queue.append({
            "name": pretty, "deck_id": did,
            "deck_path": full.replace("\x1f", "::") if full else None,
            "apkg": deck_path, "pdf": pdf_path,
            "mode": mode, "cards_before": n, "status": "pending",
        })
        tag = "" if mode == "full" else "   [OPTIMIZE-ONLY - no Apex source]"
        print(f"  OK  {pretty:<38} deck_id={did}  cards={n}{tag}")

    for key in matched:
        add(key, decks[key], pdfs[key], "full")

    # A deck with no Apex module still needs passes 1, 2 and 4. These are often the
    # OLDEST decks - the ones most in need of restructuring. Never drop them.
    for key in deck_only:
        add(key, decks[key], None, "optimize-only")

    st["pending_modules"] = queue
    st["queue_built"] = True

    # Record where COMPLETED/ lives, so next_action.py can find verification reports
    # from a fresh scratch dir in a later run. It sits beside the deck and PDF folders
    # in the project root. next_action.py re-validates this before trusting it - an
    # absolute path recorded here can go stale if a later session reaches the same
    # folder by a different route.
    # COMPLETED normally sits beside the deck folder, but deriving it from deck_dir is
    # only a guess - point this at a deck folder outside the project root and the guess
    # is wrong. A previously recorded path that still exists beats a fresh guess, or
    # this run would overwrite a good path with a bad one and block the next run.
    prev = st.get("paths", {}).get("completed")
    derived = os.path.join(os.path.dirname(os.path.abspath(deck_dir)), "COMPLETED")
    completed = os.environ.get("COMPLETED_DIR") \
        or (derived if os.path.isdir(derived) else None) \
        or (prev if prev and os.path.isdir(prev) else derived)
    paths = st.setdefault("paths", {})
    paths["completed"] = os.path.abspath(completed)
    # Only record input folders that actually produced decks. A single run pointed at
    # the wrong folder would otherwise overwrite known-good paths permanently, and
    # every later run would scan an empty folder and report "0 pending" - which reads
    # exactly like "every deck is finished".
    if decks:
        paths.update({
            "project_root":  os.path.dirname(os.path.abspath(deck_dir)),
            "source_dir":    os.path.abspath(pdf_dir),
            "deck_dir":      os.path.abspath(deck_dir),
            "archive_dir":   os.path.join(os.path.dirname(os.path.abspath(deck_dir)),
                                          "Old Anki Decks and Files"),
        })
    elif paths.get("deck_dir"):
        print("  (no decks found here - keeping the previously recorded input paths)")

    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)

    print(f"\nCOMPLETED recorded as: {st['paths']['completed']}"
          + ("" if os.path.isdir(completed) else "   !! does not exist yet"))

    if pdf_only:
        print("\n!! PDFs with no matching deck - these will NOT be processed:")
        for k in pdf_only: print(f"   {os.path.basename(pdfs[k])}")
        print("   Either the deck is missing or the filenames do not match.")

    n_opt = sum(1 for m in queue if m.get("mode") == "optimize-only")
    if n_opt:
        print(f"\n   {n_opt} deck(s) queued OPTIMIZE-ONLY: no Apex module, so pass 3")
        print("   (gap-fill) is skipped. Passes 1, 2 and 4 still run in full.")

    print(f"\nQueue written to project_state.json: {len(queue)} module(s) pending")
    if not queue:
        print("\n  A queue of 0 is only trustworthy if the input folders genuinely read")
        print("  their contents. On a cloud-sync mount, verify the folders are hydrated")
        print("  before reporting an empty queue as fact.")


if __name__ == "__main__":
    main()
