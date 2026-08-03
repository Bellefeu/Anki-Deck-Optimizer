#!/usr/bin/env python3
"""Prove that a cross-deck merge lost nothing.

The dedupe pass is the only operation in this pipeline that deletes content across
deck boundaries, and the danger is specific: a card that duplicates another deck's
fact very often ALSO carries something found nowhere else. Delete it and that content
is gone silently, with no single-deck diff able to catch it.

This compares the whole corpus before and after. Every distinctive term present
before must still be present somewhere after.

Usage:
    # before the merge
    python3 verify_corpus.py --snapshot "COMPLETED" --out corpus_before.json
    # after
    python3 verify_corpus.py --compare corpus_before.json "COMPLETED"
"""

import os, sys, re, json, glob, zipfile, sqlite3
from collections import defaultdict

from deps import require
require("zstandard", quiet=True)
import zstandard

STOP = set("""the a an of in is to and for with by that this it as on at from or be which
its during when if not are was were has have had can may must should would there their
they them then than these those what who how why where such other more most some any
each both all than into onto upon about after before between within without also very
often used using use provides provide following follows given give gives card cards""".split())


def load(apkg):
    with zipfile.ZipFile(apkg) as z:
        n = z.namelist()
        raw = (zstandard.ZstdDecompressor().decompress(z.read("collection.anki21b"),
               max_output_size=500_000_000) if "collection.anki21b" in n
               else z.read("collection.anki21" if "collection.anki21" in n else "collection.anki2"))
    p = os.path.join("/tmp", os.path.basename(apkg) + ".corpus")
    open(p, "wb").write(raw)
    con = sqlite3.connect(p)
    con.create_collation("unicase", lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower()))
    cur = con.cursor()
    cur.execute("select id, flds from notes")
    out = [(nid, f.split("\x1f")[0], f.split("\x1f")[1]) for nid, f in cur.fetchall()]
    con.close()
    return out


NUM = re.compile(r"\d+(?:\.\d+)?\s*(?:mg/kg|mcg/kg|mg|mcg|kg|mL|L|cm|mm|gauge|MHz|mA|%)", re.I)


def harvest(root):
    """Collect every distinctive term and numeric fact in the corpus."""
    terms, nums, per_deck = set(), set(), {}
    total = 0
    for a in sorted(glob.glob(os.path.join(root, "*", "*(FINAL).apkg"))):
        deck = os.path.basename(os.path.dirname(a))
        notes = load(a)
        per_deck[deck] = len(notes)
        total += len(notes)
        for nid, t, e in notes:
            body = re.sub(r"\{\{c\d+::(.*?)\}\}", r"\1", t + " " + e)
            body = re.sub(r"<[^>]+>", " ", body)
            for m in NUM.finditer(body):
                nums.add(re.sub(r"\s+", "", m.group(0).lower()))
            for w in re.findall(r"[A-Za-z]{5,}", body):
                w = w.lower()
                if w not in STOP:
                    terms.add(w)
    return {"decks": per_deck, "total_cards": total,
            "terms": sorted(terms), "numbers": sorted(nums)}


def main():
    if "--snapshot" in sys.argv:
        root = sys.argv[sys.argv.index("--snapshot") + 1]
        outf = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "corpus_before.json"
        snap = harvest(root)
        json.dump(snap, open(outf, "w"), indent=1)
        print(f"Snapshot written -> {outf}")
        print(f"  decks: {len(snap['decks'])}  cards: {snap['total_cards']}")
        print(f"  distinctive terms: {len(snap['terms'])}  numeric facts: {len(snap['numbers'])}")
        return 0

    if "--compare" in sys.argv:
        i = sys.argv.index("--compare")
        before = json.load(open(sys.argv[i + 1]))
        after = harvest(sys.argv[i + 2])

        print("=== CORPUS NO-LOSS CHECK ===\n")
        print(f"  cards : {before['total_cards']} -> {after['total_cards']}"
              f"   ({after['total_cards'] - before['total_cards']:+d})")
        print(f"  decks : {len(before['decks'])} -> {len(after['decks'])}\n")

        lost_t = sorted(set(before["terms"]) - set(after["terms"]))
        lost_n = sorted(set(before["numbers"]) - set(after["numbers"]))

        print(f"  terms lost   : {len(lost_t)}")
        print(f"  numbers lost : {len(lost_n)}")

        if lost_n:
            print("\n  !! NUMERIC FACTS THAT NO LONGER EXIST ANYWHERE:")
            for n in lost_n: print(f"       {n}")
            print("     A vanished dose or threshold is almost never an acceptable merge.")
        if lost_t:
            print(f"\n  !! TERMS THAT NO LONGER EXIST ANYWHERE ({len(lost_t)}):")
            for t in lost_t[:60]: print(f"       {t}")
            if len(lost_t) > 60: print(f"       ... and {len(lost_t)-60} more")

        print("\n  per-deck card counts:")
        for d in sorted(set(before["decks"]) | set(after["decks"])):
            b, a = before["decks"].get(d, 0), after["decks"].get(d, 0)
            flag = "   <-- shrank" if a < b else ""
            print(f"    {d:<44} {b:>5} -> {a:>5}{flag}")

        ok = not lost_n and not lost_t
        print("\n" + "=" * 58)
        if ok:
            print("  PASS - nothing distinctive was lost.")
        else:
            print("  REVIEW REQUIRED - every lost item above must be an intentional,")
            print("  justified removal. If any was unique content on a merged card,")
            print("  the merge dropped information and must be redone.")
        return 0 if ok else 1

    sys.exit(__doc__)


if __name__ == "__main__":
    sys.exit(main())
