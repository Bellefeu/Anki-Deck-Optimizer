#!/usr/bin/env python3
"""End-to-end smoke test. Run this FIRST in any new environment.

Builds a synthetic deck, exercises every operation, and verifies the result -
without touching real data. Takes about 10 seconds and catches environment
problems before they surface halfway through a real module.

    python3 selftest.py
"""

import os, sys, json, shutil, sqlite3, zipfile, subprocess, tempfile, hashlib, re, glob, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PASS, FAIL = [], []


def run_isolated():
    """Run the destructive fixture suite in a private copy of the scripts.

    Older versions temporarily replaced the user's live project_state.json and
    restored it in `finally`. A power loss or forced process kill could strand
    the synthetic state. The outer process now copies the toolkit and launches
    the real suite there, so the live state is never opened for writing.
    """
    root = tempfile.mkdtemp(prefix="anki_pipeline_selftest_")
    scripts = os.path.join(root, "scripts")
    os.makedirs(scripts, exist_ok=True)
    try:
        for name in os.listdir(HERE):
            source = os.path.join(HERE, name)
            if not os.path.isfile(source) or name.endswith((".bak", ".pyc")):
                continue
            shutil.copy2(source, os.path.join(scripts, name))
        state = os.path.join(scripts, "project_state.json")
        if not os.path.exists(state):
            template = os.path.join(scripts, "project_state.template.json")
            if not os.path.exists(template):
                print("SELFTEST ISOLATION FAILED: project_state template is missing")
                return 1
            shutil.copy2(template, state)
        env = dict(os.environ, ANKI_SELFTEST_ISOLATED="1")
        result = subprocess.run(
            [sys.executable, os.path.join(scripts, "selftest.py")],
            cwd=scripts, env=env, text=True, capture_output=True, timeout=360,
        )
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        return result.returncode
    except subprocess.TimeoutExpired:
        print("SELFTEST FAILED: isolated suite exceeded 6 minutes")
        return 1
    finally:
        for attempt in range(6):
            try:
                shutil.rmtree(root)
                break
            except FileNotFoundError:
                break
            except OSError as exc:
                if attempt == 5:
                    print(f"SELFTEST WARNING: could not remove temporary folder: {exc}")
                else:
                    time.sleep(min(0.1 * (2 ** attempt), 1.0))


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail and not cond else ""))
    return cond


def main():
    print("=== PIPELINE SELF-TEST ===\n")

    # ---------- 1. dependencies ----------
    print("--- 1. Dependencies ---")
    try:
        from deps import require
        require("zstandard", "PIL",
                cli=["pdftotext", "pdftoppm", "pdfinfo", "pdfimages", "tesseract"], quiet=True)
        check("toolchain available", True)
    except SystemExit:
        check("toolchain available", False, "see the install commands printed above")
        return report()
    import zstandard

    # ---------- 1b. version integrity ----------
    print("\n--- 1b. Version integrity ---")
    try:
        from check_version import check as vcheck
        vok, ver = vcheck()
        check("all files match VERSION.json", vok,
              "stale or locally-edited scripts - re-copy from the handoff folder")
    except Exception as e:
        check("version manifest readable", False, str(e))

    # ---------- 1c. state schema ----------
    print("\n--- 1c. Fresh-state schema ---")
    try:
        from update_handoff import normalize_state
        sparse = {"project": "test", "run_count": 7,
                  "session_log": [{"keep": True}]}
        added = normalize_state(sparse)
        check("missing runtime state is initialized",
              added == ["schema_version", "queue_built", "modules", "pending_modules", "paths"]
              and sparse["queue_built"] is False
              and sparse["modules"] == [] and sparse["pending_modules"] == []
              and sparse["paths"] == {},
              f"added={added}, state={sparse}")
        check("existing state history is preserved",
              sparse["run_count"] == 7 and sparse["session_log"] == [{"keep": True}])
        before = json.dumps(sparse, sort_keys=True)
        check("state initialization is idempotent",
              normalize_state(sparse) == [] and json.dumps(sparse, sort_keys=True) == before)
    except Exception as e:
        check("state schema helper imports", False, str(e))

    # ---------- 2. required files ----------
    print("\n--- 2. Files present ---")
    needed = ["deps.py", "bootstrap.py", "extract_source.py", "build_queue.py", "build_deck.py",
              "update_handoff.py", "verify_deck.py", "verify_corpus.py",
              "find_duplicates.py", "cleanup.py", "archive_inputs.py",
              "check_version.py", "state_io.py", "build_notes.js",
              "handoff_template.md", "project_state.template.json", "project_state.json"]
    for f in needed:
        check(f, os.path.exists(os.path.join(HERE, f)))
    check("template has placeholder",
          "<!--STATUS_SECTION-->" in open(os.path.join(HERE, "handoff_template.md"),
                                         encoding="utf-8").read())

    work = tempfile.mkdtemp(prefix="selftest_")

    # ---------- 3. build a synthetic .apkg from scratch ----------
    print("\n--- 3. Synthetic deck ---")
    MID, DID = 1766255887245, 1999000000001
    db = os.path.join(work, "c.anki21")
    con = sqlite3.connect(db)
    con.executescript("""
      create table notes (id integer primary key, guid text, mid integer, mod integer,
        usn integer, tags text, flds text, sfld text, csum integer, flags integer, data text);
      create table cards (id integer primary key, nid integer, did integer, ord integer,
        mod integer, usn integer, type integer, queue integer, due integer, ivl integer,
        factor integer, reps integer, lapses integer, left integer, odue integer,
        odid integer, flags integer, data text);
      create table notetypes (id integer primary key, name text, mtime_secs integer,
        usn integer, config blob);
      create table decks (id integer primary key, name text);
      create table col (id integer primary key);
    """)
    cur = con.cursor()
    cur.execute("insert into notetypes values (?,?,?,?,?)", (MID, "ZzzAnki Master Cloze", 0, -1, b""))
    cur.execute("insert into decks values (?,?)", (DID, "ZzzAnki\x1fTest"))

    def csum(s):
        return int(hashlib.sha1(re.sub(r"<[^>]+>", "", s).strip().encode()).hexdigest()[:8], 16)

    # Fields 3-7 and media are populated deliberately. A fixture with only Text and
    # Extra cannot catch a write that blanks the other five fields, or a repack that
    # discards the media blobs - both of which shipped silently against a real deck.
    PN = "PERSONALNOTE-{}: a long hand-written study note that must survive every op."
    seed = [
        ("A laundry-list card testing {{c1::alpha}}, {{c1::beta}}, and {{c1::gamma}} subunits.",
         " Needs splitting.<br><br>The <b>subunits</b> are distinct.<br><br><img src=\"figA.png\">",
         [PN.format("split"), "ZZZ-split", "MQ-split", "TB-split", "AR-split"]),
        ("A card with awkward phrasing about the {{c1::vagus}} nerve.",
         " Needs rewriting.<br><br>The <b>vagus nerve</b> is cranial nerve ten.",
         [PN.format("rewrite"), "ZZZ-rw", "MQ-rw", "TB-rw", "AR-rw"]),
        ("A low-yield card about a {{c1::minor}} historical detail.",
         " Should be demoted.<br><br>This is <b>trivia</b>.",
         ["", "", "", "", ""]),
        ("A duplicate card about the {{c1::vagus}} nerve.",
         " Should be deleted.<br><br>Redundant with an earlier card.",
         ["", "", "", "", ""]),
        ("A card whose Extra needs one small {{c1::correction}}.",
         " Contains <u><b>WRONGTERM</b></u> in the back.<br><br>[sound:clipB.mp3]",
         [PN.format("edit"), "ZZZ-ed", "MQ-ed", "TB-ed", "AR-ed"]),
    ]
    for i, (t, e, tail) in enumerate(seed):
        nid = 1000 + i
        cur.execute("insert into notes values (?,?,?,?,?,?,?,?,?,?,?)",
                    (nid, f"guid{i:06d}", MID, 0, -1, "",
                     "\x1f".join([t, e] + tail),
                     re.sub(r"<[^>]+>", "", t).strip(), csum(t), 0, ""))
        cur.execute("insert into cards values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (2000 + i, nid, DID, 0, 0, -1, 0, 0, nid, 0, 0, 0, 0, 0, 0, 0, 0, ""))
    con.commit(); con.close()
    check("synthetic deck built", True)

    apkg = os.path.join(work, "Test.apkg")
    comp = zstandard.ZstdCompressor().compress(open(db, "rb").read())
    open(os.path.join(work, "collection.anki21b"), "wb").write(comp)
    open(os.path.join(work, "collection.anki2"), "wb").write(b"")
    open(os.path.join(work, "media"), "wb").write(
        zstandard.ZstdCompressor().compress(b'{"0":"figA.png","1":"clipB.mp3"}'))
    open(os.path.join(work, "meta"), "wb").write(bytes([8, 3]))
    # numbered blobs are the actual media payload - a repack that lists only the
    # four well-known members throws these away and every <img> in the deck breaks
    for blob, body in (("0", b"\x89PNG\r\n\x1a\nFAKE"), ("1", b"ID3FAKEAUDIO")):
        open(os.path.join(work, blob), "wb").write(body)
    with zipfile.ZipFile(apkg, "w", zipfile.ZIP_STORED) as z:
        for p in ("collection.anki21b", "collection.anki2", "media", "meta", "0", "1"):
            z.write(os.path.join(work, p), p)
    check("packed to .apkg", os.path.exists(apkg))
    check("fixture carries media blobs",
          len([n for n in zipfile.ZipFile(apkg).namelist() if n.isdigit()]) == 2)

    # ---------- 4. exercise every operation ----------
    print("\n--- 4. All five operations ---")
    state_backup = json.load(open(os.path.join(HERE, "project_state.json")))
    moddir = os.path.join(HERE, "work", "__selftest__")
    os.makedirs(moddir, exist_ok=True)
    try:
        st = dict(state_backup)
        # Reproduce the public v1.0 seed shape that exposed this regression:
        # build_deck could finish and write an .apkg, then record_run crashed because
        # these runtime keys were absent. The real state is restored in `finally`.
        for key in ("run_count", "queue_built", "modules", "paths"):
            st.pop(key, None)
        st["pending_modules"] = [{"name": "__selftest__", "deck_id": DID, "apkg": apkg,
                                  "pdf": None, "mode": "optimize-only",
                                  "cards_before": 5, "status": "pending"}]
        json.dump(st, open(os.path.join(HERE, "project_state.json"), "w"), indent=2)

        json.dump([
            {"nid": 1000, "op": "split", "why": "laundry list", "into": [
                {"text": "The first subunit is {{c1::alpha}}.", "extra": " Split.<br><br>An <b>alpha</b> subunit."},
                {"text": "The second subunit is {{c1::beta}}.", "extra": " Split.<br><br>A <b>beta</b> subunit."},
                {"text": "The third subunit is {{c1::gamma}}.",
                 "extra": " Split.<br><br>A <b>gamma</b> subunit.<br><br><img src=\"figA.png\">"}]},
            {"nid": 1001, "op": "rewrite", "why": "phrasing",
             "text": "The tenth cranial nerve is the {{c1::vagus}} nerve.",
             "extra": " Rewritten.<br><br>The <b>vagus nerve</b> carries parasympathetic fibers."},
            {"nid": 1002, "op": "demote", "into_nid": 1001, "why": "Rule 0",
             # must carry EVERY distinctive term from the retiring card, or the
             # loss gate refuses it - that refusal is itself under test below
             "facts": ["A low-yield card about a minor historical detail is demoted here.",
                       "This is trivia and should be demoted rather than reviewed."]},
            {"nid": 1003, "op": "delete", "why": "duplicate"},
            {"nid": 1004, "op": "edit", "field": 1, "find": "WRONGTERM", "replace": "RIGHTTERM",
             "why": "correction"},
        ], open(os.path.join(moddir, "ops.json"), "w"), indent=1)
        json.dump([{"text": "A newly created card about {{c1::atropine}}.",
                    "extra": " New.<br><br><b>Atropine</b> is an antimuscarinic."}],
                  open(os.path.join(moddir, "new_cards.json"), "w"))
        json.dump({"outstanding": [], "gaps_filled": [], "summary": "selftest"},
                  open(os.path.join(moddir, "meta.json"), "w"))

        # SOURCE_NAME is what turns on the brand half of Rule 8. The subprocess gets
        # its own environment, so it has to be passed explicitly - setting os.environ
        # later in this file would be too late for this dict.
        env = dict(os.environ, COMPLETED_DIR=os.path.join(work, "COMPLETED"),
                   SOURCE_NAME="Crest")
        r = subprocess.run([sys.executable, os.path.join(HERE, "build_deck.py"), "__selftest__"],
                           capture_output=True, text=True, cwd=HERE, env=env, timeout=180)
        out = r.stdout
        check("build completed", "BUILD OK" in out, out[-400:])
        recorded = json.load(open(os.path.join(HERE, "project_state.json")))
        check("build records into legacy sparse state",
              recorded.get("run_count") == 1
              and any(m.get("name") == "__selftest__" for m in recorded.get("modules", [])),
              str(recorded))
        check("split executed",   "split: 1" in out)
        check("rewrite executed", "rewrite: 1" in out)
        check("demote executed",  "demote: 1" in out)
        check("delete executed",  "delete: 1" in out)
        check("edit executed",    "edit: 1" in out)
        check("ops all applied",  "5/5 applied" in out)
        check("no sfld/csum drift", "sfld/csum drift: 0" in out)
        check("no house-rule violations", "house-rule violations: 0" in out)

        # 5 - 1 split + 3 parts - 1 demoted - 1 deleted + 1 new = 6
        final = os.path.join(work, "COMPLETED", "__selftest__", "__selftest__ (FINAL).apkg")
        check("output .apkg exists", os.path.exists(final))
        if os.path.exists(final):
            with zipfile.ZipFile(final) as z:
                raw = zstandard.ZstdDecompressor().decompress(
                    z.read("collection.anki21b"), max_output_size=100_000_000)
            p = os.path.join(work, "v.db"); open(p, "wb").write(raw)
            c2 = sqlite3.connect(p); cc = c2.cursor()
            cc.execute("select count(*) from notes"); n = cc.fetchone()[0]
            check("final note count == 6", n == 6, f"got {n}")
            cc.execute("select flds from notes")
            allf = " ".join(r[0] for r in cc.fetchall())
            check("demoted payload preserved in Extra", "historical detail is demoted" in allf and "trivia" in allf)
            check("edit applied", "RIGHTTERM" in allf and "WRONGTERM" not in allf)
            cc.execute("select count(*) from notes n left join cards c on c.nid=n.id where c.id is null")
            check("no notes without cards", cc.fetchone()[0] == 0)

            # ---- every field, and every media reference, must survive ----
            cc.execute("select id, flds from notes")
            rows = {nid: (f.split("\x1f") + [""] * 7)[:7] for nid, f in cc.fetchall()}
            tails = [tuple(f[2:]) for f in rows.values()]
            check("rewrite preserves fields 3-7",
                  1001 in rows and rows[1001][2:] ==
                  ["PERSONALNOTE-rewrite: a long hand-written study note that must survive every op.",
                   "ZZZ-rw", "MQ-rw", "TB-rw", "AR-rw"],
                  f"got {rows.get(1001, ['?'] * 7)[2:]}")
            check("edit preserves fields 3-7",
                  1004 in rows and rows[1004][2:] ==
                  ["PERSONALNOTE-edit: a long hand-written study note that must survive every op.",
                   "ZZZ-ed", "MQ-ed", "TB-ed", "AR-ed"],
                  f"got {rows.get(1004, ['?'] * 7)[2:]}")
            check("split carries fields 3-7 onto a replacement",
                  ("PERSONALNOTE-split: a long hand-written study note that must survive every op.",
                   "ZZZ-split", "MQ-split", "TB-split", "AR-split") in tails)
            check("new card leaves fields 3-7 empty",
                  any(f[0].startswith("A newly created card") and not any(x for x in f[2:])
                      for f in rows.values()))
            check("media reference survives a split", "figA.png" in allf)
            check("media reference survives an edit", "clipB.mp3" in allf)
            c2.close()

            with zipfile.ZipFile(final) as z:
                names = z.namelist()
            check("media blobs carried into the output",
                  {"0", "1"} <= set(names), f"got {[n for n in names if n.isdigit()]}")
            check("media map carried into the output", "media" in names)
            check("preservation gate reported clean",
                  "preserved: fields 3-7 intact" in out and "PRESERVATION FAILURES" not in out)
            check("all source blobs verified present",
                  "all 2 source media blob(s) present in the output" in out)

        # ---------- 4b. the loss gate must REFUSE a lossy demotion ----------
        print("\n--- 4b. Demote loss gate ---")
        json.dump([{"nid": 1004, "op": "demote", "into_nid": 1001,
                    "fact": "one sentence", "why": "deliberately lossy"}],
                  open(os.path.join(moddir, "ops.json"), "w"))
        json.dump([], open(os.path.join(moddir, "new_cards.json"), "w"))
        st["pending_modules"] = [{"name": "__selftest__", "deck_id": DID, "apkg": apkg,
                                  "pdf": None, "mode": "optimize-only",
                                  "cards_before": 5, "status": "pending"}]
        json.dump(st, open(os.path.join(HERE, "project_state.json"), "w"), indent=2)
        r3 = subprocess.run([sys.executable, os.path.join(HERE, "build_deck.py"), "__selftest__"],
                            capture_output=True, text=True, cwd=HERE, env=env, timeout=180)
        check("lossy demote refused", "DEMOTE REFUSED" in r3.stdout, r3.stdout[-300:])
        check("build fails on refusal", "BUILD HAD PROBLEMS" in r3.stdout)

        # ---------- 4c. the preservation gate must REFUSE a lossy removal ----------
        print("\n--- 4c. Preservation gate ---")
        json.dump([{"nid": 1001, "op": "delete", "why": "deletes a note carrying Personal Notes"}],
                  open(os.path.join(moddir, "ops.json"), "w"))
        json.dump(st, open(os.path.join(HERE, "project_state.json"), "w"), indent=2)
        r4 = subprocess.run([sys.executable, os.path.join(HERE, "build_deck.py"), "__selftest__"],
                            capture_output=True, text=True, cwd=HERE, env=env, timeout=180)
        check("removal that would destroy Personal Notes is refused",
              "PRESERVATION FAILURES" in r4.stdout and "Personal Notes" in r4.stdout,
              r4.stdout[-400:])
        check("preservation failure fails the build", "BUILD HAD PROBLEMS" in r4.stdout)

        # the same delete is allowed once the loss is declared
        json.dump([{"nid": 1001, "op": "delete", "why": "loss declared",
                    "drop_tail": True, "drop_media": True}],
                  open(os.path.join(moddir, "ops.json"), "w"))
        json.dump(st, open(os.path.join(HERE, "project_state.json"), "w"), indent=2)
        r5 = subprocess.run([sys.executable, os.path.join(HERE, "build_deck.py"), "__selftest__"],
                            capture_output=True, text=True, cwd=HERE, env=env, timeout=180)
        check("declared loss is permitted", "BUILD OK" in r5.stdout, r5.stdout[-400:])
        # r5 deleted a note and added none, so a derived "before" (n_notes - added)
        # gives 4 while the true source count is 5. Deriving it was a real bug:
        # splits create notes the `added` counter never sees, and a 203-card deck
        # was recorded as 233.
        rec = next((m for m in json.load(open(os.path.join(HERE, "project_state.json")))
                    .get("modules", []) if m["name"] == "__selftest__"), None)
        check("cards_before is measured, not derived",
              rec is not None and rec.get("cards_before") == 5,
              f"recorded {rec.get('cards_before') if rec else 'nothing'}, expected 5")

        # a rewrite that silently drops the card's image must be caught
        json.dump([{"nid": 1004, "op": "rewrite", "why": "drops the sound reference",
                    "text": "A rewritten card about {{c1::correction}}.",
                    "extra": " No media here.<br><br>The <b>reference</b> is gone."}],
                  open(os.path.join(moddir, "ops.json"), "w"))
        json.dump(st, open(os.path.join(HERE, "project_state.json"), "w"), indent=2)
        r6 = subprocess.run([sys.executable, os.path.join(HERE, "build_deck.py"), "__selftest__"],
                            capture_output=True, text=True, cwd=HERE, env=env, timeout=180)
        check("rewrite that drops a media reference is refused",
              "clipB.mp3" in r6.stdout and "PRESERVATION FAILURES" in r6.stdout,
              r6.stdout[-400:])

        # ---------- 4d. in-place patch of an already-built deck ----------
        # Nothing exercised --patch before, which is how two bugs shipped in it:
        # the repack deleted its own source (SRC == the output on a patch), and the
        # changelog was overwritten with only the patch's entries.
        print("\n--- 4d. Patch path (--patch) ---")
        st["pending_modules"] = []
        st["modules"] = [m for m in st.get("modules", []) if m["name"] != "__selftest__"] + [
            {"name": "__selftest__", "deck_id": DID, "status": "built-unverified",
             "cards_before": 5, "cards_after": 6, "added": 1, "edited": 5}]
        json.dump(st, open(os.path.join(HERE, "project_state.json"), "w"), indent=2)

        # rebuild the deck we are about to patch, and record a build changelog
        json.dump([{"nid": 1004, "op": "edit", "field": 1, "find": "WRONGTERM",
                    "replace": "RIGHTTERM", "why": "correction"}],
                  open(os.path.join(moddir, "ops.json"), "w"))
        json.dump([], open(os.path.join(moddir, "new_cards.json"), "w"))
        st_build = dict(st)
        st_build["pending_modules"] = [{"name": "__selftest__", "deck_id": DID, "apkg": apkg,
                                       "pdf": None, "mode": "optimize-only",
                                       "cards_before": 5, "status": "pending"}]
        json.dump(st_build, open(os.path.join(HERE, "project_state.json"), "w"), indent=2)
        subprocess.run([sys.executable, os.path.join(HERE, "build_deck.py"), "__selftest__"],
                       capture_output=True, text=True, cwd=HERE, env=env, timeout=180)
        built = os.path.join(work, "COMPLETED", "__selftest__", "__selftest__ (FINAL).apkg")
        blobs_before = 0
        if os.path.exists(built):
            with zipfile.ZipFile(built) as z:
                blobs_before = len([n for n in z.namelist() if n.isdigit()])
        clog = os.path.join(moddir, "changelog.json")
        pre_entries = len(json.load(open(clog))) if os.path.exists(clog) else 0
        json.dump(st, open(os.path.join(HERE, "project_state.json"), "w"), indent=2)

        json.dump([{"nid": 1001, "op": "edit", "field": 0, "find": "awkward phrasing",
                    "replace": "clear phrasing", "why": "verify: patch-path test"}],
                  open(os.path.join(moddir, "patch_ops.json"), "w"))
        rp = subprocess.run([sys.executable, os.path.join(HERE, "build_deck.py"),
                             "--patch", "__selftest__"],
                            capture_output=True, text=True, cwd=HERE, env=env, timeout=180)
        check("patch build OK", "BUILD OK" in rp.stdout, rp.stdout[-500:])
        check("patch applied its op", "1/1 applied" in rp.stdout, rp.stdout[-300:])
        check("patch reconciled", "reconciled" in rp.stdout, rp.stdout[-300:])
        # the bug: SRC and the output are the same file on a patch, so removing the
        # output first destroyed the media inventory and the repack died
        blobs_after = 0
        if os.path.exists(built):
            with zipfile.ZipFile(built) as z:
                blobs_after = len([n for n in z.namelist() if n.isdigit()])
        check("patch preserves media blobs in place",
              blobs_before > 0 and blobs_after == blobs_before,
              f"{blobs_before} before, {blobs_after} after")
        # the second bug: build_notes.js reads this file, so overwriting it loses
        # every split, rewrite and add the build recorded
        post = json.load(open(clog)) if os.path.exists(clog) else []
        check("patch accumulates the changelog",
              len(post) > 1 and len(post) >= pre_entries + 1,
              f"{pre_entries} build entries + 1 patch entry, got {len(post)}")

        # idempotent: re-reading the merged file must not double it up
        json.dump([{"nid": 1001, "op": "edit", "field": 0, "find": "clear phrasing",
                    "replace": "plain phrasing", "why": "verify: patch-path test 2"}],
                  open(os.path.join(moddir, "patch_ops.json"), "w"))
        subprocess.run([sys.executable, os.path.join(HERE, "build_deck.py"),
                        "--patch", "__selftest__"],
                       capture_output=True, text=True, cwd=HERE, env=env, timeout=180)
        post2 = json.load(open(clog)) if os.path.exists(clog) else []
        check("repeated patches do not duplicate changelog entries",
              len(post2) == len(post) + 1, f"{len(post)} -> {len(post2)}")
        os.remove(os.path.join(moddir, "patch_ops.json"))

        # ---------- 5. creation mode ----------
        print("\n--- 5. Creation mode (empty deck) ---")
        ec = sqlite3.connect(db)
        ec.execute("delete from notes"); ec.execute("delete from cards"); ec.commit(); ec.close()
        empty = os.path.join(work, "Empty.apkg")
        open(os.path.join(work, "collection.anki21b"), "wb").write(
            zstandard.ZstdCompressor().compress(open(db, "rb").read()))
        with zipfile.ZipFile(empty, "w", zipfile.ZIP_STORED) as z:
            for p in ("collection.anki21b", "collection.anki2", "media", "meta"):
                z.write(os.path.join(work, p), p)
        st["pending_modules"] = [{"name": "__selftest__", "deck_id": DID, "apkg": empty,
                                  "pdf": None, "mode": "optimize-only",
                                  "cards_before": 0, "status": "pending"}]
        json.dump(st, open(os.path.join(HERE, "project_state.json"), "w"), indent=2)
        json.dump([], open(os.path.join(moddir, "ops.json"), "w"))
        json.dump([{"text": "A created card about {{c1::atropine}}.",
                    "extra": " New.<br><br><b>Atropine</b> is an antimuscarinic."}],
                  open(os.path.join(moddir, "new_cards.json"), "w"))
        r2 = subprocess.run([sys.executable, os.path.join(HERE, "build_deck.py"), "__selftest__"],
                            capture_output=True, text=True, cwd=HERE, env=env, timeout=180)
        check("creation mode detected", "CREATION mode" in r2.stdout)
        check("creation build OK", "BUILD OK" in r2.stdout, r2.stdout[-300:])

        # ---------- 5b. rule 8: a card never names its source ----------
        # The brand half of Rule 8 only works once the brand is named, so configure
        # it the way a real user would. The generic constructions work regardless.
        os.environ["SOURCE_NAME"] = "Crest"
        # "According to <SOURCE>, ..." must never reach a card. The detector also has
        # to stay blind to a brand name that collides with a term in the material
        # ("Crest" the source vs. "the iliac crest") or people mute it and lose Rule 8.
        print("\n--- 5b. Rule 8 (no source named in a card) ---")
        sys.path.insert(0, HERE)
        from build_deck import source_attribution as _sa
        check("attribution in Text detected",
              bool(_sa("According to Crest, the thoracolumbar fascia has {{c1::three}} layers.")))
        check("attribution in Extra detected",
              bool(_sa(" <b>Crest</b> emphasizes that desiccated absorbent is the prerequisite.")))
        check("paraphrased attribution detected",
              bool(_sa("The module states that the nerve runs deep.")))
        check("colliding domain term NOT flagged",
              not _sa("The iliac crest lies at the level of L4.")
              and not _sa("The crest of the ilium is the landmark."))
        check("clean card NOT flagged",
              not _sa("The thoracolumbar fascia has {{c1::three}} layers."))

        st2 = dict(st)
        st2["modules"] = [m for m in st2.get("modules", []) if m.get("name") != "__selftest__"]
        st2["pending_modules"] = [{"name": "__selftest__", "deck_id": DID, "apkg": empty,
                                   "pdf": None, "mode": "optimize-only",
                                   "cards_before": 0, "status": "pending"}]
        json.dump(st2, open(os.path.join(HERE, "project_state.json"), "w"), indent=2)
        json.dump([{"text": "According to Crest, the thoracolumbar fascia has {{c1::three}} layers.",
                    "extra": " Per Crest.<br><br>The <b>thoracolumbar fascia</b> has three layers."}],
                  open(os.path.join(moddir, "new_cards.json"), "w"))
        r8 = subprocess.run([sys.executable, os.path.join(HERE, "build_deck.py"), "__selftest__"],
                            capture_output=True, text=True, cwd=HERE, env=env, timeout=180)
        check("rule 8 violation reported", "RULE 8 FAIL" in r8.stdout, r8.stdout[-500:])
        check("rule 8 violation fails the build", "BUILD HAD PROBLEMS" in r8.stdout, r8.stdout[-500:])

    finally:
        json.dump(state_backup, open(os.path.join(HERE, "project_state.json"), "w"), indent=2)
        shutil.rmtree(moddir, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
        subprocess.run([sys.executable, os.path.join(HERE, "update_handoff.py")],
                       capture_output=True, cwd=HERE)

    orchestrator_tests()
    queue_loop_tests()
    pass_archive_tests()
    consistency_tests()
    coverage_gate_tests()
    return report()


def orchestrator_tests():
    """next_action.py decides what every unattended run does. Nothing exercised it
    before, which is how a silent VERIFY loop shipped: it looked for the verify
    report in a local COMPLETED that a fresh scratch dir never has, read "missing"
    as "not verified", and re-issued VERIFY on an already-verified module every
    run - advancing the queue never."""
    print("\n--- 6. Orchestrator decision table (next_action.py) ---")
    tmp = tempfile.mkdtemp(prefix="selftest-na-")
    # COMPLETED lives OUTSIDE the script dir on purpose: that is the real shape of a
    # scheduled run, where scripts are copied to fresh local scratch and COMPLETED
    # stays behind in Drive. Putting it beside the scripts would let the local
    # fallback succeed and hide the very bug these cases exist to catch.
    comp = tempfile.mkdtemp(prefix="selftest-completed-")
    try:
        shutil.copy(os.path.join(HERE, "next_action.py"), tmp)

        def state(modules, queue, paths=None):
            st = {"modules": modules, "pending_modules": queue}
            if paths: st["paths"] = paths
            json.dump(st, open(os.path.join(tmp, "project_state.json"), "w"))

        def run(env_completed="__unset__"):
            env = dict(os.environ)
            env.pop("COMPLETED_DIR", None)
            if env_completed != "__unset__":
                env["COMPLETED_DIR"] = env_completed
            r = subprocess.run([sys.executable, os.path.join(tmp, "next_action.py")],
                               capture_output=True, text=True, cwd=tmp, env=env, timeout=60)
            return r.stdout, r.returncode

        built = [{"name": "M", "status": "built-unverified"}]
        pending = [{"name": "N", "status": "pending", "cards_before": 10}]

        state([], pending)
        out, rc = run(comp)
        check("queued module -> BUILD", "ACTION: BUILD" in out and rc == 0, out)

        os.makedirs(os.path.join(comp, "M", "audit"), exist_ok=True)
        state(built, [])
        out, rc = run(comp)
        check("built, no report -> VERIFY", "ACTION: VERIFY" in out and rc == 0, out)

        open(os.path.join(comp, "M", "audit", "VERIFY_REPORT_2026-01-01.md"), "w").close()
        out, rc = run(comp)
        check("built + report -> AWAIT_USER", "AWAIT_USER" in out and rc == 1, out)

        # the queue must keep moving without the user's --pass
        state(built, pending)
        out, rc = run(comp)
        check("report present, queue non-empty -> next BUILD",
              "ACTION: BUILD" in out and "MODULE: N" in out and rc == 0, out)

        # the regression: fresh scratch dir, COMPLETED nowhere to be found
        state(built, [])
        out, rc = run()
        check("COMPLETED unfindable -> BLOCKED, not a silent re-VERIFY",
              "BLOCKED" in out and "ACTION: VERIFY" not in out and rc == 2, out)

        # a stale recorded path must not be trusted just because it is written down
        state(built, [], paths={"completed": os.path.join(tmp, "gone")})
        out, rc = run()
        check("stale recorded COMPLETED path rejected",
              "BLOCKED" in out and rc == 2, out)

        # ...but a good recorded path removes the need for the env var
        state(built, [], paths={"completed": comp})
        out, rc = run()
        check("recorded COMPLETED path used when it exists",
              "AWAIT_USER" in out and rc == 1, out)

        state([], [{"name": "N", "status": "pending", "cards_before": 10,
                    "apkg": os.path.join(tmp, "gone", "N.apkg")}])
        out, rc = run(comp)
        check("queued deck missing -> BLOCKED, not a doomed BUILD",
              "BLOCKED" in out and "ACTION: BUILD" not in out and rc == 2, out)

        json.dump({"action": "BUILD", "module": "M", "ts": __import__("time").time()},
                  open(os.path.join(tmp, ".pipeline.lock"), "w"))
        out, rc = run(comp)
        check("live lock -> exit 2, no work claimed",
              "BLOCKED" in out and rc == 2, out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(comp, ignore_errors=True)


def queue_loop_tests():
    """The multi-deck loop: queue -> build -> verify -> next deck, one phase per run.

    The failure this guards is a rebuild loop. Every unattended run refreshes the
    queue, and a finished module's resting state is `built-unverified` because
    `--pass` is the user's call - so if build_queue.py re-queues anything that is not
    yet `verified`, deck one is rebuilt from scratch every run, its verification is
    discarded, and deck two is never reached."""
    print("\n--- 7. Multi-deck queue loop (build_queue.py) ---")
    import zstandard
    root = tempfile.mkdtemp(prefix="selftest-queue-")
    try:
        decks   = os.path.join(root, "Anki Decks");        os.makedirs(decks)
        pdfs    = os.path.join(root, "Source Files");  os.makedirs(pdfs)
        scripts = os.path.join(root, "scripts");           os.makedirs(scripts)
        for f in ("build_queue.py", "deps.py", "state_io.py"):
            shutil.copy(os.path.join(HERE, f), scripts)
        STATE_P = os.path.join(scripts, "project_state.json")
        json.dump({"modules": [], "pending_modules": []}, open(STATE_P, "w"))

        def mk(name):
            db = os.path.join(root, name + ".anki21")
            if os.path.exists(db): os.remove(db)
            con = sqlite3.connect(db)
            con.executescript("""
              create table notes (id integer primary key, guid text, mid integer,
                mod integer, usn integer, tags text, flds text, sfld text,
                csum integer, flags integer, data text);
              create table decks (id integer primary key, name text);
              create table notetypes (id integer primary key, name text,
                mtime_secs integer, usn integer, config blob);
              create table col (id integer primary key);
            """)
            con.execute("insert into decks values (?,?)",
                        (1770000000000 + sum(ord(c) for c in name), "ZzzAnki\x1f" + name))
            for i in range(3):
                con.execute("insert into notes values (?,?,?,?,?,?,?,?,?,?,?)",
                            (i + 1, f"g{name}{i}", 1, 0, -1, "", f"Q{i}\x1fA{i}",
                             f"Q{i}", 0, 0, ""))
            con.commit(); con.close()
            with zipfile.ZipFile(os.path.join(decks, name + ".apkg"), "w",
                                 zipfile.ZIP_STORED) as z:
                z.writestr("collection.anki21b",
                           zstandard.ZstdCompressor().compress(open(db, "rb").read()))
                z.writestr("media", zstandard.ZstdCompressor().compress(b"{}"))
                z.writestr("meta", bytes([8, 3]))

        def run_queue(*args):
            r = subprocess.run([sys.executable, os.path.join(scripts, "build_queue.py")]
                               + list(args), capture_output=True, text=True,
                               cwd=scripts, timeout=120)
            return r.stdout + r.stderr, json.load(open(STATE_P))

        def set_state(modules, pending):
            st = json.load(open(STATE_P))
            st["modules"] = modules
            st["pending_modules"] = pending
            json.dump(st, open(STATE_P, "w"))

        mk("Alpha"); mk("Beta")
        out, st = run_queue(pdfs, decks)
        check("both decks queued",
              sorted(m["name"] for m in st["pending_modules"]) == ["Alpha", "Beta"], out)
        check("input paths recorded for later runs",
              st.get("paths", {}).get("deck_dir") == os.path.abspath(decks),
              str(st.get("paths")))

        # Alpha is built and now sits at built-unverified, awaiting the user's --pass
        set_state([{"name": "Alpha", "status": "built-unverified"}],
                  [m for m in st["pending_modules"] if m["name"] != "Alpha"])
        out, st = run_queue(pdfs, decks)
        check("refresh does NOT re-queue a built module",
              [m["name"] for m in st["pending_modules"]] == ["Beta"],
              str([m["name"] for m in st["pending_modules"]]) + out)

        # recorded paths mean a later run needs no arguments at all
        set_state([{"name": "Alpha", "status": "verified"},
                   {"name": "Beta",  "status": "built-unverified"}], [])
        out, st = run_queue()
        check("recorded paths reused with no arguments", "using recorded" in out, out)
        check("every deck tracked -> queue stays empty",
              st["pending_modules"] == [], str(st["pending_modules"]) + out)

        # a good recorded COMPLETED must survive a refresh that cannot derive one
        comp = os.path.join(root, "COMPLETED"); os.makedirs(comp, exist_ok=True)
        st = json.load(open(STATE_P)); st.setdefault("paths", {})["completed"] = comp
        json.dump(st, open(STATE_P, "w"))
        stray = os.path.join(root, "elsewhere", "Anki Decks"); os.makedirs(stray, exist_ok=True)
        out, st = run_queue(pdfs, stray)
        check("a refresh from an odd folder keeps the known COMPLETED",
              st["paths"]["completed"] == comp, str(st.get("paths")) + out)
        check("a scan that finds no decks does not overwrite good input paths",
              st["paths"]["deck_dir"] == os.path.abspath(decks), str(st.get("paths")) + out)

        # a deck staged later is picked up without disturbing the finished ones
        mk("Gamma")
        out, st = run_queue()
        check("newly staged deck is picked up on the next refresh",
              [m["name"] for m in st["pending_modules"]] == ["Gamma"],
              str([m["name"] for m in st["pending_modules"]]) + out)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def pass_archive_tests():
    """`--pass` must move the ORIGINAL deck and its source captures out of the input
    folders. It resolves those folders from paths recorded in project_state.json,
    because a --pass normally runs from a local scratch copy where the conventional
    relative paths resolve to nothing - and archiving that moves nothing while
    printing success leaves an already-optimized deck sitting in the input folder,
    where a later run will take it as source and gap-fill it a second time."""
    print("\n--- 8. Archive on --pass (verify_deck.py --pass) ---")
    import zstandard
    root = tempfile.mkdtemp(prefix="selftest-pass-")
    try:
        decks   = os.path.join(root, "Anki Decks");             os.makedirs(decks)
        srcd   = os.path.join(root, "Source Files");        os.makedirs(srcd)
        arch    = os.path.join(root, "Old Anki Decks and Files"); os.makedirs(arch)
        scripts = os.path.join(root, "scripts");                 os.makedirs(scripts)
        for f in ("verify_deck.py", "archive_inputs.py", "deps.py", "cleanup.py",
                  "update_handoff.py", "state_io.py", "handoff_template.md"):
            shutil.copy(os.path.join(HERE, f), scripts)

        NAME = "Archive Test"
        db = os.path.join(root, "t.anki21")
        con = sqlite3.connect(db)
        con.executescript("""
          create table notes (id integer primary key, guid text, mid integer, mod integer,
            usn integer, tags text, flds text, sfld text, csum integer, flags integer, data text);
          create table decks (id integer primary key, name text);
          create table notetypes (id integer primary key, name text, mtime_secs integer,
            usn integer, config blob);
          create table col (id integer primary key);
        """)
        con.execute("insert into decks values (?,?)", (1770000000042, "ZzzAnki\x1f" + NAME))
        con.execute("insert into notes values (1,'g',1,0,-1,'','Q\x1fA','Q',0,0,'')")
        con.commit(); con.close()
        with zipfile.ZipFile(os.path.join(decks, NAME + ".apkg"), "w", zipfile.ZIP_STORED) as z:
            z.writestr("collection.anki21b",
                       zstandard.ZstdCompressor().compress(open(db, "rb").read()))
            z.writestr("media", zstandard.ZstdCompressor().compress(b"{}"))
            z.writestr("meta", bytes([8, 3]))
        # the source as a capture FOLDER, which is the shape a real module has
        capt = os.path.join(srcd, NAME); os.makedirs(capt)
        open(os.path.join(capt, "capture01.pdf"), "wb").write(b"%PDF-1.4\n%%EOF\n")

        json.dump({"run_count": 1, "last_updated": "2026-01-01",
                   "modules": [{"name": NAME, "deck_id": 1770000000042,
                                "status": "built-unverified",
                                "cards_before": 1, "cards_after": 1,
                                "added": 0, "edited": 0}],
                   "pending_modules": [],
                   "paths": {"deck_dir": decks, "source_dir": srcd, "archive_dir": arch}},
                  open(os.path.join(scripts, "project_state.json"), "w"))

        env = dict(os.environ)
        for k in ("ANKI_DECK_DIR", "SOURCE_DIR", "ARCHIVE_DIR"):
            env.pop(k, None)          # force resolution via the recorded paths
        env["HANDOFF_OUTDIR"] = os.path.join(root, "_out")
        r = subprocess.run([sys.executable, os.path.join(scripts, "verify_deck.py"),
                            "--pass", NAME], capture_output=True, text=True,
                           cwd=scripts, env=env, timeout=120)
        out = r.stdout + r.stderr

        st = json.load(open(os.path.join(scripts, "project_state.json")))
        check("--pass marks the module verified",
              st["modules"][0]["status"] == "verified", out)
        check("original deck moved out of the input folder",
              not glob.glob(os.path.join(decks, "*.apkg")), out)
        check("deck landed in the archive",
              os.path.exists(os.path.join(arch, NAME, "Anki Deck", NAME + ".apkg")), out)
        check("source captures landed in the archive",
              os.path.exists(os.path.join(arch, NAME, "Files", "capture01.pdf")), out)
        check("archiving reported, not silently skipped",
              "inputs archived under" in out and "NOT ARCHIVED" not in out, out)

        # and it must say so loudly when the folders cannot be found
        json.dump({"run_count": 1, "last_updated": "2026-01-01",
                   "modules": [{"name": "Ghost", "status": "built-unverified",
                                "cards_before": 1, "cards_after": 1, "added": 0, "edited": 0}],
                   "pending_modules": [], "paths": {}},
                  open(os.path.join(scripts, "project_state.json"), "w"))
        r = subprocess.run([sys.executable, os.path.join(scripts, "verify_deck.py"),
                            "--pass", "Ghost"], capture_output=True, text=True,
                           cwd=scripts, env=env, timeout=120)
        check("unfindable input folder is reported, not swallowed",
              "INPUTS NOT ARCHIVED" in (r.stdout + r.stderr), r.stdout + r.stderr)

        # a hand-edited record missing an optional field must not abort the pass
        # before the inputs are archived
        os.makedirs(decks, exist_ok=True)
        with zipfile.ZipFile(os.path.join(decks, "Sparse Rec.apkg"), "w") as z:
            z.writestr("meta", bytes([8, 3]))
        json.dump({"run_count": 1, "last_updated": "2026-01-01",
                   "modules": [{"name": "Sparse Rec", "status": "built-unverified"}],
                   "pending_modules": [],
                   "paths": {"deck_dir": decks, "source_dir": srcd, "archive_dir": arch}},
                  open(os.path.join(scripts, "project_state.json"), "w"))
        r = subprocess.run([sys.executable, os.path.join(scripts, "verify_deck.py"),
                            "--pass", "Sparse Rec"], capture_output=True, text=True,
                           cwd=scripts, env=env, timeout=120)
        out = r.stdout + r.stderr
        check("a record missing optional fields still reaches the archive step",
              "Traceback" not in out and "marked VERIFIED" in out, out)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def consistency_tests():
    """check_consistency.py — Rule 10. Unit-level, no deck needed.

    The point of these is that the detector must fire on a real contradiction and stay
    quiet on a deliberate contrast pair. A gate that flags the thoracic/lumbar contrast
    cards is a gate that gets switched off within a week.
    """
    print("\n--- 9. Consistency detector (rule 10) ---")
    try:
        import check_consistency as cc
    except Exception as e:
        return check("check_consistency imports", False, str(e))
    check("check_consistency imports", True)

    def index(pairs):
        texts = [t + " " + e for t, e in pairs]
        canon, abbrev, _ = cc.build_lexicon(texts)
        claims = []
        for i, (t, e) in enumerate(pairs):
            topic = cc.note_topic(t, canon, abbrev)
            claims += cc.extract(1000 + i, "Text", t, canon, abbrev, topic)
            claims += cc.extract(1000 + i, "Extra", e, canon, abbrev, topic)
        return claims

    # a real contradiction: one question, two depths, no qualifier to tell them apart
    hit = cc.collide(index([
        ("The transverse process is contacted at a depth of {{c1::2 to 4}} cm during a "
         "thoracic paravertebral block.", " "),
        ("During a thoracic paravertebral block the provider should expect to contact the "
         "transverse process at a depth of {{c1::3 to 6}} cm.", " "),
    ]), "numeric")
    check("catches a genuine depth contradiction", len(hit) >= 1,
          f"expected >=1 collision, got {len(hit)}")

    # a deliberate contrast pair: same structure, different region, both correct
    quiet = cc.collide(index([
        ("During a landmark paravertebral block the transverse process is contacted at a "
         "depth of {{c1::2 to 4}} cm in the thoracic region.", " "),
        ("During a landmark paravertebral block the transverse process is contacted at a "
         "depth of {{c1::5 to 8}} cm in the lumbar region.", " "),
    ]), "numeric")
    check("stays quiet on a thoracic/lumbar contrast pair", len(quiet) == 0,
          f"expected 0 collisions, got {len(quiet)}: {[k for k, _, _ in quiet]}")

    # laterality must survive on anatomy or two correct nerve cards look like a conflict
    nerves = cc.collide(index([
        ("The lateral pectoral nerve arises from the brachial plexus at roots {{c1::C5 to C7}}.", " "),
        ("The medial pectoral nerve arises from the brachial plexus at roots {{c1::C8 to T1}}.", " "),
    ]), "numeric")
    check("keeps lateral/medial pectoral nerve apart", len(nerves) == 0,
          f"expected 0, got {[k for k, _, _ in nerves]}")

    # a needle spec in Extra must inherit the card's procedure, or it never meets the front
    needle = cc.collide(index([
        ("For a paravertebral block the preferred needle is an 8-10 cm {{c1::Tuohy}} needle.", " "),
        ("When a nerve stimulator is used for a paravertebral block the initial current is "
         "{{c1::2.5 to 5.0}} mA.", " A 5 cm 22-gauge B-bevel needle is used."),
    ]), "numeric")
    check("Extra inherits the card's procedure for needle specs", len(needle) >= 1,
          f"expected >=1 collision, got {len(needle)}")

    # Rule 13: a weight-based dose with no drug named
    check("mg/kg with no drug is detectable",
          bool(cc.DOSE_UNIT_RE.search("approximately 0.4 mg/kg")) and
          not cc.DRUG_RE.search("The maximum local anesthetic dose is 0.4 mg/kg."))
    check("mg/kg with a drug named is clean",
          bool(cc.DRUG_RE.search("0.4 mL/kg of 0.25% bupivacaine")))

    # the singulariser must not maul real words
    check("singulariser leaves 'process' and 'serratus' alone",
          cc._singular("process") == "process" and cc._singular("serratus") == "serratus"
          and cc._singular("nerves") == "nerve",
          f"{cc._singular('process')} / {cc._singular('serratus')} / {cc._singular('nerves')}")


def coverage_gate_tests():
    """extract_source.py's coverage gate decides what NEVER gets looked at, so it
    is the one part of the pipeline where a bug is silent by construction: a
    dropped figure looks exactly like a page that had no figure. Everything here
    is about proving the gate over-reads rather than under-reads."""
    print("\n--- 9. Visual-read coverage gate ---")
    try:
        import extract_source as ex
        from PIL import Image, ImageDraw
        import numpy as np
    except Exception as e:
        check("extract_source imports", False, str(e))
        return

    # --- the footer regex, against what tesseract actually emits.
    # It reads the 'o' of 'of' as a zero almost every time; the strict spelling
    # matched nothing, which is why every module so far reported a bogus
    # "captured 4 of 31 course pages".
    for raw, want in [("Page 4 0f 39", ("4", "39")), ("Page 8 of 39", ("8", "39")),
                      ("Page 12 Of 107", ("12", "107")), ("Paqe 3 0t 39", ("3", "39")),
                      ("Page 22 01 39", ("22", "39"))]:
        got = ex.PAGE_FOOTER.findall(raw)
        check(f"footer parses {raw!r}", got and got[0] == want, f"got {got}")
    check("footer does not match prose 'page 4 of the module'",
          not ex.PAGE_FOOTER.findall("see page 4 of the module text"))

    # --- the numeric trigger. It must fire on every Rule 9 target and must NOT
    # fire on ordinary prose, or every text line lands on the read list.
    for tok in ["3-6", "T11", "0.25%", "50,000", "12", "mmHg", "VIII", "1.5"]:
        check(f"numeric trigger fires on {tok!r}", bool(ex.NUMERICISH.search(tok)))
    for tok in ["liver", "the", "cross-section", "Coronary", "a", "In-Depth"]:
        check(f"numeric trigger quiet on {tok!r}", not ex.NUMERICISH.search(tok))

    # --- token arithmetic must match Anthropic's documented 28x28 patch rule,
    # because every saving in the report is computed from it.
    check("vis_tokens 1275x1651 page == 2714", ex.vis_tokens(1275, 1651) == 2714)
    check("vis_tokens 28x28 == 1", ex.vis_tokens(28, 28) == 1)
    check("vis_tokens rounds up", ex.vis_tokens(29, 29) == 4)

    # --- the handoff split must not have lost a section
    r = subprocess.run([sys.executable, os.path.join(HERE, "handoff.py"), "check"],
                       capture_output=True, text=True, cwd=HERE)
    check("handoff split lost nothing (handoff.py check)", r.returncode == 0,
          (r.stdout + r.stderr).strip()[:300])

    tmp = tempfile.mkdtemp(prefix="cov_")
    try:
        # --- a synthetic page: prose OCR will read, plus a figure it cannot.
        W, H = 1000, 1400
        im = Image.new("RGB", (W, H), "white")
        d = ImageDraw.Draw(im)
        for i in range(12):                       # prose block
            d.text((60, 80 + i * 30), "The liver is the largest internal organ "
                                      "and sits in the right upper quadrant",
                   fill=(0, 0, 0))
        d.rectangle([0, 470, W, 474], fill=(20, 90, 130))   # banner hairline (bucket C)
        rng = np.random.default_rng(7)                       # a "figure": textured art
        art = rng.integers(40, 210, size=(300, 400, 3), dtype=np.uint8)
        im.paste(Image.fromarray(art), (200, 700))
        src = os.path.join(tmp, "page.png"); im.save(src)

        text, words = ex.ocr_page(src, tmp)
        check("synthetic page OCRs to words", len(words) > 20, f"{len(words)} words")

        bands, num_bands, st = ex.analyze_page(src, words)
        check("coverage proof holds (unaccounted == 0)",
              st["unaccounted_ink_px"] == 0, f"{st['unaccounted_ink_px']} px left over")

        # the figure must be inside a band - this is the whole point
        covered = any(y0 <= 700 and y1 >= 1000 for y0, y1 in bands) or \
                  any(y0 <= 850 <= y1 for y0, y1 in bands)
        check("untranscribable figure lands on the read list", covered,
              f"bands={bands}")

        # prose the OCR read exactly must NOT be on the read list, or nothing saved
        band_px = sum(y1 - y0 for y0, y1 in bands)
        check("prose is elided, not re-sent", band_px < H * 0.75,
              f"{band_px}/{H} px banded")

        # bucket C must stay tiny and must be reported
        check("typographic furniture is reported", "furniture_px" in st)
        check("typographic furniture stays under the cap",
              st["furniture_frac"] <= ex.FURNITURE_MAX_FRAC,
              f"{st['furniture_frac']:.3f}")

        # --- the composed sheet must contain every banded pixel, unaltered
        sheet = os.path.join(tmp, "sheet.png")
        ex.compose_sheet(src, bands, sheet)
        sh = Image.open(sheet)
        want_h = sum(y1 - y0 for y0, y1 in bands) + max(0, len(bands) - 1) * ex.SEP_H
        check("sheet height == banded height + separators", sh.size[1] == want_h,
              f"{sh.size[1]} vs {want_h}")
        check("sheet keeps full page width (layout context preserved)",
              sh.size[0] == W)
        first = np.asarray(Image.open(src).crop((0, bands[0][0], W, bands[0][1])))
        check("sheet pixels are byte-identical to the page (no resampling)",
              np.array_equal(np.asarray(sh)[:bands[0][1] - bands[0][0]], first))

        # --- fail-safe branches must promote to a whole-page read, never shrink it
        plan, _ = ex.build_read_plan(src, [], tmp, "t")
        check("no OCR boxes + ink -> whole page", plan["mode"] == "page")

        blank = os.path.join(tmp, "blank.png")
        Image.new("RGB", (400, 400), "white").save(blank)
        plan, _ = ex.build_read_plan(blank, [], tmp, "b")
        check("truly blank page -> nothing to read", plan["mode"] == "none")

        old = ex.COVERAGE
        try:
            ex.COVERAGE = "page"
            plan, _ = ex.build_read_plan(src, words, tmp, "t2")
            check("COVERAGE=page restores whole-page reading", plan["mode"] == "page")
        finally:
            ex.COVERAGE = old

        # --- page order must not depend on how the run was resumed. `parts` is
        # appended as work completes, so an interrupted-and-resumed extraction
        # used to emit the right bytes in the wrong order - which silently
        # reorders the module and makes two extractions non-comparable.
        shuffled = [{"img": f"out/pages/p{n:02d}-1.png", "text": f"T{n}"}
                    for n in (9, 1, 12, 3)]
        order = [x["img"] for x in
                 sorted(shuffled, key=lambda x: ex.natural_key(x["img"]))]
        check("page order is deterministic regardless of resume order",
              order == ["out/pages/p01-1.png", "out/pages/p03-1.png",
                        "out/pages/p09-1.png", "out/pages/p12-1.png"], str(order))

        # --- the extracted TEXT must be unaffected by any of this. If this ever
        # fails, the optimisation changed the content and must be reverted.
        direct = subprocess.run(["tesseract", src, "-", "--psm", "1"],
                                capture_output=True, text=True).stdout
        check("OCR text is byte-identical to the pre-coverage pipeline",
              text == direct, f"{len(text)} vs {len(direct)} bytes")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def report():
    print("\n" + "=" * 55)
    print(f"  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("\n  FAILURES:")
        for f in FAIL: print(f"    - {f}")
        print("\n  Do NOT run a real module until these are resolved.")
        return 1
    print("\n  Pipeline is healthy. Safe to process a real module.")
    return 0


if __name__ == "__main__":
    if os.environ.get("ANKI_SELFTEST_ISOLATED") == "1":
        sys.exit(main())
    sys.exit(run_isolated())
