#!/usr/bin/env python3
"""Independent verification of a FINISHED deck.

Run this in a session that did NOT build the deck. A builder checking its own work
shares its own blind spots; this is meant to be adversarial.

It does the mechanical half in full and gives the session a sampled worklist for
the judgment half. It NEVER sets verified=true on its own - a human read of the
NOTES doc is required, because the rubric's Rule 0 calls are subjective.

Usage:
    python3 verify_deck.py "COMPLETED/<Module>/<Module> (FINAL).apkg" [original.apkg]
    python3 verify_deck.py --pass "<Module>"     # mark verified after human review
"""

import sys, os, re, json, zipfile, sqlite3, hashlib, random
from deps import require
require("zstandard", quiet=True)
import zstandard

_ENTITY = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]{1,9}|#\d{1,5}|#x[0-9A-Fa-f]{1,5});")


def has_semicolon(text):
    """House rule: no semicolons in Text. HTML entities (&nbsp; &gt; &lt;) end in a
    semicolon but are not punctuation - checking the raw string flags them and
    blocks the build on cards that are actually fine."""
    return ";" in _ENTITY.sub("", text)


HERE  = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "project_state.json")
UNITS = r"\b(mL|mg|kg|mcg|cm|mm|MHz|mA|gauge|Hz)\b|%"


def load(apkg):
    with zipfile.ZipFile(apkg) as z:
        names = z.namelist()
        blob = z.read("collection.anki21b") if "collection.anki21b" in names else None
        raw = (zstandard.ZstdDecompressor().decompress(blob, max_output_size=500_000_000)
               if blob else z.read("collection.anki21" if "collection.anki21" in names
                                   else "collection.anki2"))
    p = os.path.join("/tmp", os.path.basename(apkg) + ".db")
    open(p, "wb").write(raw)
    con = sqlite3.connect(p)
    con.create_collation("unicase", lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower()))
    return con


def notes_of(con):
    cur = con.cursor()
    cur.execute("select id, flds, sfld, csum from notes order by id")
    return [(n, f.split("\x1f")[0], f.split("\x1f")[1], s, c) for n, f, s, c in cur.fetchall()]


def all_fields(con):
    """{nid: [7 fields]} - needed to audit fields 3-7, which notes_of() drops."""
    return {n: (f.split("\x1f") + [""] * 7)[:7]
            for n, f in con.execute("select id, flds from notes").fetchall()}


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--pass":
        name = sys.argv[2]
        st = json.load(open(STATE))
        hit = False
        for m in st["modules"]:
            if m["name"].lower() == name.lower():
                m["status"] = "verified"; m["outstanding"] = []; hit = True
        if not hit: sys.exit(f"'{name}' not found in state.")
        json.dump(st, open(STATE, "w"), indent=2)
        # Regenerating the handoff must never abort the pass. The status flip above is
        # already written; the file moves below are the part that still has to happen.
        try:
            from update_handoff import regenerate; regenerate()
        except Exception as e:
            print(f"  !! HANDOFF.md not regenerated ({e}) - continuing; re-run update_handoff.py")
        print(f"{name} marked VERIFIED.")

        import subprocess

        # 1. reclaim this module's scratch - the page images are no longer needed
        try:
            subprocess.run([sys.executable, os.path.join(HERE, "cleanup.py"),
                            "--yes", "--module", name], check=False)
        except Exception as e:
            print(f"  (cleanup skipped: {e})")

        # 2. archive the ORIGINAL inputs, so they are preserved and can never be
        #    re-queued as if unprocessed. Paths from env, with conventional defaults.
    #    A --pass normally runs from a local scratch copy, where the conventional
    #    relative paths resolve to nothing. Consult the paths build_queue.py recorded
    #    before falling back to them, and validate: a path that does not exist is
    #    worse than none, because archiving then "succeeds" having moved nothing.
        def _path(env_var, state_key, default):
            """env > recorded path > default. Each of env_var and state_key may be
            a tuple tried in order, so the pre-rename APEX_PDF_DIR / apex_pdf_dir
            names still resolve after the input folder became "Source Files"."""
            for ev in ((env_var,) if isinstance(env_var, str) else env_var):
                v = os.environ.get(ev)
                if v:
                    return v
            try:
                paths = json.load(open(STATE)).get("paths", {})
            except Exception:
                paths = {}
            for k in ((state_key,) if isinstance(state_key, str) else state_key):
                rec = paths.get(k)
                if rec and os.path.isdir(rec):
                    return rec
            return default

        decks   = _path("ANKI_DECK_DIR", "deck_dir",     "Anki Decks")
        apex    = _path(("SOURCE_DIR", "APEX_PDF_DIR"),
                        ("source_dir", "apex_pdf_dir"), "Source Files")
        archive = _path("ARCHIVE_DIR",   "archive_dir",  "Old Anki Decks and Files")

        if not os.path.isdir(decks):
            print(f"\n  !! INPUTS NOT ARCHIVED - deck folder not found: {decks}")
            print(f"     '{name}' IS marked verified; only the file move was skipped.")
            print("     Run build_queue.py once to record the paths, then:")
            print(f'       python3 archive_inputs.py --module "{name}" --yes')
        else:
            try:
                subprocess.run([sys.executable, os.path.join(HERE, "archive_inputs.py"),
                                "--module", name, "--decks", decks, "--apex", apex,
                                "--archive", archive, "--yes"], check=False)
            except Exception as e:
                print(f"  (input archiving failed to run: {e})")
            # Confirm the deck actually left the input folder. archive_inputs reports
            # its own failures, but a --pass that prints success while the original is
            # still sitting in "Anki Decks/" is exactly how an already-optimized deck
            # gets picked up as source and gap-filled a second time.
            leftover = None
            try:
                sys.path.insert(0, HERE)
                from archive_inputs import normalize as _norm, find_deck as _find_deck
                leftover = _find_deck(decks, _norm(name))
            except Exception:
                pass
            if leftover:
                print(f"\n  !! STILL IN THE INPUT FOLDER: {leftover}")
                print("     Move it out by hand. Left there, a later run can take an")
                print("     already-optimized deck as its source.")
            else:
                print(f"  inputs archived under {os.path.join(archive, name)}")

        # 3. offer to empty the whole scratch root - refuses unless everything is
        #    verified, audit trails are in place, and no orphan scratch exists
        completed = os.environ.get("COMPLETED_DIR", "COMPLETED")
        print("\n  Checking whether the scratch root can be emptied:")
        try:
            subprocess.run([sys.executable, os.path.join(HERE, "cleanup.py"),
                            "--purge", "--root", os.path.join(HERE, "work"),
                            "--completed", completed], check=False)
        except Exception as e:
            print(f"  (purge check skipped: {e})")
        return

    if len(sys.argv) < 2:
        sys.exit(__doc__)
    apkg = sys.argv[1]
    orig = sys.argv[2] if len(sys.argv) > 2 else None

    con = load(apkg); rows = notes_of(con); cur = con.cursor()
    print(f"=== VERIFYING {os.path.basename(apkg)} ===")
    print(f"  {len(rows)} notes\n")

    # Canonical report path. Written here so a later session finds it by module name
    # alone - the same discovery convention as the apex:deck pairing.
    import datetime
    mod = os.path.basename(os.path.dirname(os.path.abspath(apkg)))
    audit = os.path.join(os.path.dirname(os.path.abspath(apkg)), "audit")
    report_path = os.path.join(audit, f"VERIFY_REPORT_{datetime.date.today().isoformat()}.md")
    print(f"  write your report to: {report_path}")
    print(f"  (newest VERIFY_REPORT_*.md in that folder is what a patch session reads)\n")

    fail = 0

    # ---------- A. structural integrity ----------
    print("--- A. INTEGRITY ---")
    cur.execute("pragma integrity_check"); iv = cur.fetchone()[0]
    print(f"  sqlite: {iv}")
    if iv != "ok": fail += 1
    for label, q in {
        "orphan cards": "select count(*) from cards c left join notes n on c.nid=n.id where n.id is null",
        "notes w/o card": "select count(*) from notes n left join cards c on c.nid=n.id where c.id is null",
        "duplicate guids": "select count(*) from (select guid from notes group by guid having count(*)>1)",
    }.items():
        cur.execute(q); v = cur.fetchone()[0]
        print(f"  {label}: {v}")
        if v: fail += 1

    # ---------- B. house rules ----------
    print("\n--- B. HOUSE RULES ---")
    viol = []
    for nid, t, e, sfld, cs in rows:
        errs = []
        n_c1 = len(re.findall(r"\{\{c1::", t))
        if n_c1 == 0: errs.append("no cloze")
        if n_c1 > 3: errs.append(f"{n_c1} clozes")
        if "{{c2::" in t or "{{c3::" in t: errs.append("c2/c3")
        if has_semicolon(t): errs.append("semicolon")
        if t.count("{{") != t.count("}}"): errs.append("brace imbalance")
        for tag in "bui":
            if e.count(f"<{tag}>") != e.count(f"</{tag}>"): errs.append(f"<{tag}> imbalance")
        if not e.strip(): errs.append("empty Extra")
        if errs: viol.append((nid, errs))
    print(f"  violations: {len(viol)}")
    for nid, errs in viol[:8]: print(f"    [{nid}] {errs}")
    if viol: fail += 1

    # Rule 8 - a card never names its source. Text and Extra are checked separately;
    # attribution survives in Extra most often, and section E's term diff cannot see it
    # (the term is present in both decks, so nothing looks lost).
    #
    # The detector lives in build_deck.py so the build gate and this audit can never
    # disagree about what counts as a violation. Imported HERE, not at module scope:
    # `--pass` runs in scratch dirs that hold only a subset of the scripts, and a
    # top-level import there turns a missing file into a dead verification pass.
    sys.path.insert(0, HERE)
    from build_deck import source_attribution
    attrib = [(nid, fname, hit)
              for nid, t, e, sfld, cs in rows
              for fname, s in (("Text", t), ("Extra", e))
              for hit in source_attribution(s)]
    print(f"  rule 8 - source named in a card: {len(attrib)}")
    for nid, fname, hit in attrib[:12]:
        print(f"    [{nid}] {fname}: {hit[:90]}")
    if attrib:
        print("    Each is a defect: strip the attribution, keep the fact, patch with a")
        print("    rewrite. Anatomical 'apex' is excluded and not counted here.")
        fail += 1

    # ---------- C. rubric mechanics (section 3b rule 3) ----------
    print("\n--- C. CLOZE MECHANICS (rubric rule 3) ---")
    long_c, unit_c, punct_c, bare_abbr = [], [], [], []
    for nid, t, e, sfld, cs in rows:
        for m in re.findall(r"\{\{c1::(.*?)\}\}", t):
            inner = re.sub(r"<[^>]+>", "", m).strip()
            if len(inner.split()) > 3: long_c.append((nid, inner))
            if re.search(UNITS, inner): unit_c.append((nid, inner))
            if re.search(r"[,;:]|\.\s|\.$", inner): punct_c.append((nid, inner))
        for ab in re.findall(r"\b([A-Z]{3,})\b", t):
            if f"({ab})" not in t and ab not in ("ASIS", "TLF", "PVS", "IIM", "TAP", "QL"):
                bare_abbr.append((nid, ab))
    print(f"  cloze >3 words        : {len(long_c)}")
    for nid, x in long_c[:5]: print(f"    [{nid}] '{x}'")
    print(f"  units inside cloze    : {len(unit_c)}")
    print(f"  punctuation in cloze  : {len(punct_c)}")
    print(f"  possibly-bare abbrev  : {len(set(bare_abbr))}")

    # ---------- D. sfld/csum drift ----------
    print("\n--- D. SORT FIELD / CHECKSUM ---")
    drift = 0
    for nid, t, e, sfld, cs in rows:
        exp_s = re.sub(r"<[^>]+>", "", re.sub(r"\[sound:.*?\]", "", t)).strip()
        exp_c = int(hashlib.sha1(exp_s.encode()).hexdigest()[:8], 16)
        if (sfld or "").strip() != exp_s or cs != exp_c: drift += 1
    print(f"  drift: {drift}" + ("   (check whether pre-existing in the source deck)" if drift else ""))

    # ---------- E. no-information-lost diff ----------
    if orig and os.path.exists(orig):
        print("\n--- E. INFORMATION LOSS vs ORIGINAL ---")
        ocon = load(orig); orows = notes_of(ocon)
        def terms(rs):
            s = set()
            for _, t, e, _, _ in rs:
                for w in re.findall(r"\b[A-Za-z][A-Za-z\-]{4,}\b",
                                    re.sub(r"<[^>]+>", "", t + " " + e)):
                    s.add(w.lower())
            return s
        lost = sorted(terms(orows) - terms(rows))
        print(f"  original notes: {len(orows)} -> final: {len(rows)}")
        print(f"  terms present in original but absent from final: {len(lost)}")
        if lost:
            print(f"    {lost[:25]}")
            print("    Each must be an intentional correction or demotion. Check the changelog.")

        # Fields 3-7 and media references. Term-level diff above only reads Text
        # and Extra, so it is blind to a Personal Notes field that was blanked or
        # an <img> that stopped being referenced - both real, both silent.
        from build_deck import FIELD_NAMES, media_refs
        ofields, ffields = all_fields(ocon), all_fields(con)
        live_tails = {tuple(v[2:]) for v in ffields.values()}
        tail_lost = []
        for nid, f in ofields.items():
            tail = tuple(f[2:])
            if not any(x.strip() for x in tail):
                continue
            here = ffields.get(nid)
            if (tuple(here[2:]) if here else None) == tail or tail in live_tails:
                continue
            named = ", ".join(FIELD_NAMES[i + 2] for i, x in enumerate(tail) if x.strip())
            tail_lost.append(f"note {nid}: {named}")
        n_tails = sum(1 for f in ofields.values() if any(x.strip() for x in f[2:]))
        print(f"  notes carrying fields 3-7: {n_tails} in original, "
              f"{len(tail_lost)} not found in final")
        for m in tail_lost[:10]:
            print(f"    LOST {m}")
        if tail_lost:
            fail += 1

        def refs(d):
            out = set()
            for v in d.values():
                out |= media_refs("\x1f".join(v))
            return out
        media_lost = sorted(refs(ofields) - refs(ffields))
        print(f"  media references: {len(refs(ofields))} in original, "
              f"{len(media_lost)} no longer referenced")
        for m in media_lost[:10]:
            print(f"    LOST media {m}")
        if media_lost:
            fail += 1

    # ---------- F. judgment worklist ----------
    print("\n--- F. JUDGMENT SAMPLE (read these against rubric 3b) ---")
    random.seed(len(rows))
    for nid, t, e, _, _ in random.sample(rows, min(12, len(rows))):
        print(f"\n  [{nid}] {t[:150]}")
        print(f"        EXTRA: {e[:110]}...")

    # ---------- G-K. self-consistency, and the Rule 9/13 worklists ----------
    #
    # Delegated to check_consistency.py rather than reimplemented, so the build gate and
    # this audit can never disagree about what counts as a contradiction - the same
    # reason source_attribution() lives in build_deck.py and is imported here.
    #
    # Sections A-F look at each card alone and at the deck against its original. A
    # contradiction is a property of a PAIR of cards inside the finished deck, which is
    # why none of them ever caught one.
    print("\n--- G-K. SELF-CONSISTENCY + RULE 9/13 WORKLISTS ---")
    import subprocess
    cc = os.path.join(HERE, "check_consistency.py")
    if not os.path.exists(cc):
        print("  !! check_consistency.py MISSING from the script folder.")
        print("     Sections G-K did not run. Re-copy it from the handoff folder;")
        print("     do not report a verification pass without them.")
        fail += 1
    else:
        r = subprocess.run([sys.executable, cc, apkg], capture_output=True, text=True)
        print(r.stdout.rstrip())
        if r.stderr.strip():
            print(r.stderr.rstrip())
        if r.returncode == 1:
            fail += 1
        elif r.returncode not in (0, 1):
            print(f"  !! check_consistency.py exited {r.returncode} - sections G-K unreliable")
            fail += 1

    print("\n" + "=" * 60)
    print("MECHANICAL RESULT:", "PASS" if fail == 0 else f"FAIL ({fail} categories)")
    print("""
Still required before this counts as verified:
  0. Read the deck for rule 8 by eye as well. Section B catches the standard
     attribution constructions, not every paraphrase of "Apex says so".
  0b. Work section H (rule 9) and section I (rule 13) by hand. The script can
     list every checkable claim; only you can look one up. An unchecked number
     is a number you asserted.
  1. Read the 12 sampled cards above against rubric 3b - atomicity test,
     cloze targeting, clinical pearl phrasing.
  2. Read the NOTES doc: confirm every DEMOTE and DELETE was the right call.
  3. Spot-check any clinical correction against the source module.
  4. Confirm the IO figures match their stated topics.

Then:  python3 verify_deck.py --pass "<Module Name>"
""")


if __name__ == "__main__":
    main()
