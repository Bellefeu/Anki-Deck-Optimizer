#!/usr/bin/env python3
"""Find CANDIDATE cross-deck near-duplicates for a model to judge.

Design note, learned the hard way: lexical similarity does not find these. Real
cross-deck duplicates are the same principle in completely different words -
"LAST presents first with CNS symptoms" vs "the earliest manifestation involves
perioral numbness and tinnitus". Full-text similarity scores those around 0.2.

So this script optimises RECALL, not precision. It casts wide using signals that
survive rephrasing, and deliberately returns false positives. The model reading the
output does the judging. A missed duplicate is invisible forever; a false positive
costs one line of reading.

Signals that survive rephrasing:
  - numbers bound to units      7 mg/kg, 20 mL, 22-gauge, 20%   (highest signal)
  - expanded abbreviations      LAST <-> local anesthetic systemic toxicity
  - rare content terms          weighted by inverse document frequency

Usage:
    python3 find_duplicates.py "COMPLETED" [--threshold 0.35] [--out dupes.json]
"""

import os, sys, re, json, glob, zipfile, sqlite3, math, itertools
from collections import defaultdict, Counter

from deps import require
require("zstandard", quiet=True)
import zstandard

# Bidirectional expansion so an abbreviation and its long form fingerprint alike.
ABBREV = {
    "last": "local anesthetic systemic toxicity",
    "cns": "central nervous system",
    "cv": "cardiovascular",
    "tlf": "thoracolumbar fascia",
    "tap": "transversus abdominis plane",
    "ql": "quadratus lumborum",
    "esp": "erector spinae plane",
    "pvb": "paravertebral block",
    "pvs": "paravertebral space",
    "sap": "serratus anterior plane",
    "asis": "anterior superior iliac spine",
    "copd": "chronic obstructive pulmonary disease",
    "map": "mean arterial pressure",
    "gfr": "glomerular filtration rate",
    "ffp": "fresh frozen plasma",
    "vwf": "von willebrand factor",
    "pdph": "post dural puncture headache",
    "iim": "internal intercostal membrane",
    "eom": "external oblique muscle",
    "iom": "internal oblique muscle",
    "tam": "transversus abdominis muscle",
}
STOP = set("""the a an of in is to and for with by that this it as on at from or be which
its during when if not are was were has have had can may must should would there their
they them then than these those what who how why where such other more most some any
each both all than into onto upon about after before between within without also very
often used using use provides provide following follows given give gives""".split())


def load_notes(apkg):
    with zipfile.ZipFile(apkg) as z:
        names = z.namelist()
        if "collection.anki21b" in names:
            raw = zstandard.ZstdDecompressor().decompress(
                z.read("collection.anki21b"), max_output_size=500_000_000)
        else:
            raw = z.read("collection.anki21" if "collection.anki21" in names else "collection.anki2")
    p = os.path.join("/tmp", os.path.basename(apkg) + ".dupdb")
    open(p, "wb").write(raw)
    con = sqlite3.connect(p)
    con.create_collation("unicase", lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower()))
    cur = con.cursor()
    cur.execute("select id, flds from notes")
    out = [(nid, f.split("\x1f")[0], f.split("\x1f")[1]) for nid, f in cur.fetchall()]
    con.close()
    return out


NUM_UNIT = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:-\s*\d+(?:\.\d+)?\s*)?"
    r"(mg/kg|mcg/kg|mg|mcg|kg|ml|l\b|cm|mm|gauge|mhz|ma\b|%|hours|hour|minutes|min|degrees)",
    re.I)


def fingerprint(text):
    """Return (numeric_facts, content_terms). Both survive heavy rephrasing."""
    t = re.sub(r"\{\{c\d+::(.*?)\}\}", r"\1", text)
    t = re.sub(r"<[^>]+>", " ", t).lower()

    nums = set()
    for m in NUM_UNIT.finditer(t):
        unit = m.group(2).lower().rstrip(".")
        unit = {"l": "ml", "hour": "hours", "min": "minutes"}.get(unit, unit)
        nums.add(f"{m.group(1)}{unit}")
    # bare spinal levels are strong identifiers too: T10, L1, C5
    for m in re.finditer(r"\b([ctl]\d{1,2})\b", t):
        nums.add(m.group(1))

    # expand abbreviations both directions before tokenising
    for ab, full in ABBREV.items():
        if re.search(rf"\b{ab}\b", t):
            t += " " + full
        if full in t:
            t += " " + ab

    words = re.findall(r"[a-z]{3,}", t)
    terms = {w for w in words if w not in STOP}
    return nums, terms


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "COMPLETED"
    thr  = float(sys.argv[sys.argv.index("--threshold") + 1]) if "--threshold" in sys.argv else 0.35
    outf = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "dupes.json"

    apkgs = sorted(glob.glob(os.path.join(root, "*", "*(FINAL).apkg")))
    if not apkgs:
        sys.exit(f"No '*(FINAL).apkg' found under {root}")

    print("=== CROSS-DECK DUPLICATE CANDIDATES ===")
    print(f"  decks: {len(apkgs)}  |  threshold: {thr}  (LOW BY DESIGN - expect false positives)\n")

    cards = []
    for a in apkgs:
        deck = os.path.basename(os.path.dirname(a))
        n = load_notes(a)
        for nid, t, e in n:
            nums, terms = fingerprint(t + " " + e)
            if terms:
                cards.append({"deck": deck, "nid": nid, "text": t,
                              "nums": nums, "terms": terms})
        print(f"  {deck:<42} {len(n):>5} cards")
    print(f"\n  total: {len(cards)} cards")

    # ---- inverse document frequency: rare terms identify concepts ----
    df = Counter()
    for c in cards:
        df.update(c["terms"])
    N = len(cards)
    idf = {w: math.log(N / (1 + n)) for w, n in df.items()}

    # ---- TOPIC CLUSTERING ----
    # Pairwise similarity cannot find semantic duplicates: the same principle in
    # different words scores ~0.2. What DOES work is grouping every card touching a
    # concept, across all decks, and reading the group. That is how a human does it:
    # "show me every card about LAST" - then the duplicates are obvious on sight.
    topic_cards = defaultdict(list)
    for i, c in enumerate(cards):
        # a card's topics = its rarest terms, plus every numeric fact it asserts
        for w in sorted(c["terms"], key=lambda w: -idf.get(w, 0))[:6]:
            if idf.get(w, 0) > 1.5:          # ignore terms common across the corpus
                topic_cards[w].append(i)
        for n in c["nums"]:
            # bare spinal levels (t10, l1, c5) appear across dozens of unrelated
            # facts - only numbers BOUND TO A UNIT identify a specific assertion
            if re.match(r"^[ctl]\d{1,2}$", n):
                continue
            topic_cards[f"#{n}"].append(i)

    # Only topics spanning MORE THAN ONE deck can contain a cross-deck duplicate.
    topics = []
    for topic, idxs in topic_cards.items():
        decks = {cards[i]["deck"] for i in idxs}
        if len(decks) < 2 or len(idxs) < 2 or len(idxs) > 60:
            continue
        topics.append({
            "topic": topic,
            "n_cards": len(idxs),
            "n_decks": len(decks),
            "decks": sorted(decks),
            "cards": [{"deck": cards[i]["deck"], "nid": cards[i]["nid"],
                       "text": cards[i]["text"]} for i in idxs],
        })
    topics.sort(key=lambda t: (-t["n_decks"], -t["n_cards"]))

    print(f"\n  cross-deck topics to review: {len(topics)}")
    print(f"  (a topic is a concept appearing in 2+ decks - duplicates can only live here)\n")

    print("  topics spanning the most decks:")
    for t in topics[:20]:
        print(f"    {t['n_decks']:>2} decks, {t['n_cards']:>3} cards   {t['topic']}")

    print("\n  --- sample worklist (first topic) ---")
    if topics:
        t = topics[0]
        print(f"  TOPIC: {t['topic']}   ({t['n_cards']} cards across {t['n_decks']} decks)")
        for c in t["cards"][:8]:
            print(f"    [{c['deck']} #{c['nid']}] {c['text'][:105]}")

    out = {"topics": topics}

    json.dump(out, open(outf, "w"), indent=1)
    print(f"\n  full worklist -> {outf}")
    print(f"    topics: {len(out['topics'])}")
    print("""
  NOTHING WAS CHANGED. These are worklists, not verdicts.

  Work topic by topic. Read every card in a topic together - duplicates that share no
  vocabulary are obvious once the cards sit side by side, and invisible to any
  similarity score.

  Consolidation is always a MERGE, never a bare delete. A card repeating another deck's
  fact very often carries something found nowhere else, and that content must survive
  into the merged result. Run verify_corpus.py afterwards to prove nothing was lost.
""")


if __name__ == "__main__":
    main()
