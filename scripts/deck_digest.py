#!/usr/bin/env python3
"""Read a deck compactly, and prove every card was actually looked at.

Two jobs, and the second is the important one.

1. COMPACT READ. A deck is 40-48k tokens and it sits in context for the whole
   session, re-sent on every turn. Presentational markup (`<b>`, `<u>`, `<span
   style>`, nbsp, repeated `<br>`) is 6-12% of that and carries nothing a rubric
   pass acts on. `digest` strips exactly those, turns structural markup into
   single-character markers, and leaves cloze syntax, punctuation and every word
   byte-identical.

2. COVERAGE LEDGER. Nothing in the pipeline currently checks that all N cards
   were considered. A pass that skimmed 180 of 227 produces the same shaped
   report as one that read all 227. `ledger` writes every nid with structural
   flags, and `reconcile` checks a finished `ops.json` against it and prints
   what was never touched and never explicitly cleared.

   **The flags are a priority ORDER, never a filter.** Rules 9, 10, 11 and 12
   are per-card and unconditional at any flag count - a deck can be 100%
   mechanically clean and factually wrong on every third number, which is
   exactly what Truncal was. Reading the flagged cards first means the expensive
   judgement lands where it pays; it does not license reading fewer.

    python3 deck_digest.py digest    "<deck.apkg|deck.txt>" [out.txt]
    python3 deck_digest.py ledger    "<deck.apkg|deck.txt>" [ledger.json]
    python3 deck_digest.py reconcile <ledger.json> <ops.json> [new_cards.json]
"""

import os, re, sys, json, glob, shutil, zipfile, sqlite3, tempfile
from deps import require

SEP = "\x1f"

# Presentational only. Nothing here changes what a card asserts, so removing it
# cannot change a rubric judgement. Structural markup is converted, not dropped.
PRESENTATIONAL = re.compile(
    r"</?(b|i|u|em|strong|span|font|small|big|sub|sup)\b[^>]*>", re.I)
STRUCTURAL = [(re.compile(r"<br\s*/?>", re.I), "⏎"),
              (re.compile(r"</(div|p|li|tr)>", re.I), "⏎"),
              (re.compile(r"<li\b[^>]*>", re.I), "⏎- "),
              (re.compile(r"</?(div|p|ul|ol|table|tbody|tr|td|th)\b[^>]*>", re.I), "")]

CLOZE = re.compile(r"\{\{c(\d+)::(.*?)\}\}", re.S)
ABBREV = re.compile(r"\b[A-Z]{2,6}\b")
UNIT_IN_CLOZE = re.compile(
    r"\{\{c\d+::[^}]*\b(mg|mcg|ml|mL|cm|mm|kg|g|%|mmHg|cmH2O|psi|mA|Hz|Fr)\b[^}]*\}\}")


def compact(html):
    """Strip presentation, keep every word and every cloze byte-identical."""
    t = html.replace("&nbsp;", " ")
    t = PRESENTATIONAL.sub("", t)
    for rx, rep in STRUCTURAL:
        t = rx.sub(rep, t)
    t = re.sub(r"<[^>]+>", "", t)                 # any tag we did not name
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"(⏎\s*){3,}", "⏎⏎", t)
    return t.strip()


def read_deck(path):
    """[(nid, [fields...])] from a .apkg or a ' | '-separated export."""
    if path.endswith(".txt"):
        rows = []
        for i, line in enumerate(open(path, encoding="utf-8")):
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            rows.append((i + 1, line.split(" | ")))
        return rows

    require("zstandard", quiet=True)
    tmp = tempfile.mkdtemp(prefix="digest_")
    try:
        with zipfile.ZipFile(path) as z:
            z.extractall(tmp)
        db = None
        for name in ("collection.anki21b", "collection.anki21", "collection.anki2"):
            cand = os.path.join(tmp, name)
            if os.path.exists(cand):
                db = cand
                break
        if db is None:
            sys.exit(f"no collection database inside {path}")
        if db.endswith("21b"):                     # zstd-compressed since 2021
            import zstandard
            plain = db[:-1]
            with open(db, "rb") as f, open(plain, "wb") as o:
                zstandard.ZstdDecompressor().copy_stream(f, o)
            db = plain
        con = sqlite3.connect(db)
        rows = [(nid, flds.split(SEP))
                for nid, flds in con.execute("select id, flds from notes")]
        con.close()
        return rows
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def flags_for(text, extra):
    """Structural tells. A priority order for attention, never a filter."""
    f = []
    # Strip the cloze scaffolding before content tests - "{{c1::" carries a digit
    # and an ordinal that belong to Anki, not to the card, and matching them
    # flagged literally every card as numeric.
    bare_text = CLOZE.sub(lambda m: m.group(2), text)
    ids = {m.group(1) for m in CLOZE.finditer(text)}
    targets = [m.group(2) for m in CLOZE.finditer(text)]
    if len(ids) > 1:
        f.append(f"multi-cloze:{len(ids)}")
    if len(ids) > 3:
        f.append("over-3-clozes")                       # Rule 3
    for t in targets:
        if len(t.split()) > 2:
            f.append("long-cloze"); break               # Rule 3
    for t in targets:
        if re.search(r"[.,;:]", t):
            f.append("punctuation-in-cloze"); break     # Rule 3
    if UNIT_IN_CLOZE.search(text):
        f.append("unit-inside-cloze")                   # Rule 3
    if text.count("{{c") and not targets:
        f.append("malformed-cloze")
    if re.search(r"per the module|the module states|the lecture|as taught in|"
                 r"according to the (?:course|source|module)", bare_text + extra, re.I):
        f.append("RULE-8-attribution")                  # Rule 8, absolute
    if re.search(r"\b(always|never|exactly|precisely|consistently)\b", bare_text, re.I):
        f.append("epistemic-register")                  # Rule 12
    if re.search(r"\d", bare_text):
        f.append("has-number")                          # Rule 9
    if re.search(r"\b(medial|lateral|cephalad|caudad|superficial|deep|"
                 r"anterior|posterior|proximal|distal)\b", bare_text, re.I):
        f.append("direction")                           # Rule 13
    if re.search(r"\b(mg|mcg|ml|mL|kg|%)\b", bare_text):
        f.append("dose-or-volume")                      # Rule 13
    if len(text) > 900:
        f.append("long-front")                          # Rule 1
    if re.search(r"(?:^|⏎)\s*[-•]\s.*(?:⏎)\s*[-•]\s", bare_text):
        f.append("laundry-list")                        # Rule 4
    bare = [a for a in ABBREV.findall(bare_text) if a not in ("IO", "NCE")]
    if bare and not extra:
        f.append("abbrev-no-extra")                     # Rule 3
    if not extra.strip():
        f.append("empty-extra")                         # Rule 6
    return f


def cmd_digest(path, out=None):
    rows = read_deck(path)
    lines, raw, comp = [], 0, 0
    for nid, flds in rows:
        text = flds[0] if flds else ""
        extra = flds[1] if len(flds) > 1 else ""
        raw += len(text) + len(extra)
        ct, ce = compact(text), compact(extra)
        comp += len(ct) + len(ce)
        lines.append(f"{nid}\t{ct}" + (f"\t{ce}" if ce else ""))
    body = "\n".join(lines)
    dest = out or (os.path.splitext(path)[0] + ".digest.txt")
    open(dest, "w", encoding="utf-8").write(body + "\n")
    print(f"  {len(rows)} cards -> {dest}")
    print(f"  raw {raw:,} chars (~{raw//4:,} tok)  ->  compact {comp:,} "
          f"(~{comp//4:,} tok)   {100*(1-comp/max(raw,1)):.0f}% less")
    print("  presentation only: every word, cloze and punctuation mark is unchanged.")
    print("  EDIT AGAINST THE RAW DECK, NOT THIS - ops.json find-strings must match "
          "the real field bytes.")


def cmd_ledger(path, out=None):
    rows = read_deck(path)
    entries, counts = [], {}
    for nid, flds in rows:
        text = flds[0] if flds else ""
        extra = flds[1] if len(flds) > 1 else ""
        f = flags_for(text, extra)
        for x in f:
            counts[x] = counts.get(x, 0) + 1
        entries.append({"nid": nid, "flags": f, "n_flags": len(f),
                        "len_text": len(text), "len_extra": len(extra)})
    entries.sort(key=lambda e: (-e["n_flags"], e["nid"]))
    dest = out or (os.path.splitext(path)[0] + ".ledger.json")
    json.dump({"deck": path, "cards": len(entries), "flag_counts": counts,
               "entries": entries}, open(dest, "w"), indent=1)
    ids = {m for e in entries for m in ([e["nid"]] if "multi-cloze" in
           " ".join(e["flags"]) else [])}
    print(f"  {len(entries)} cards -> {dest}")
    print(f"  multi-cloze rate: {100*len(ids)/max(len(entries),1):.0f}%")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"    {v:4d}  {k}")
    print("\n  Highest-flag cards come first. That is a READING ORDER.")
    print("  Rules 9/10/11/12 still apply to every card, flagged or not.")


def cmd_reconcile(ledger, ops, new_cards=None):
    led = json.load(open(ledger))
    all_nids = {e["nid"] for e in led["entries"]}
    touched = set()
    for op in json.load(open(ops)):
        if "nid" in op:
            touched.add(op["nid"])
    added = len(json.load(open(new_cards))) if new_cards and os.path.exists(new_cards) else 0
    untouched = sorted(all_nids - touched)
    flagged_untouched = [e for e in led["entries"]
                         if e["nid"] in set(untouched) and e["n_flags"] > 0]
    print(f"  deck cards        : {len(all_nids)}")
    print(f"  cards with an op  : {len(touched)}")
    print(f"  cards untouched   : {len(untouched)}  "
          f"({100*len(untouched)/max(len(all_nids),1):.0f}% left byte-identical)")
    print(f"  new cards added   : {added}")
    if flagged_untouched:
        print(f"\n  !! {len(flagged_untouched)} untouched cards carry structural flags.")
        print("     Leaving them is allowed - it is a judgement, not an error - but "
              "PROMPT_build requires you to justify it in the report.")
        for e in flagged_untouched[:20]:
            print(f"       {e['nid']}  {','.join(e['flags'])}")
        if len(flagged_untouched) > 20:
            print(f"       ... and {len(flagged_untouched)-20} more")
    else:
        print("\n  Every flagged card has an op. Nothing structural was skipped.")
    return 0


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "digest":
        cmd_digest(args[0], args[1] if len(args) > 1 else None)
    elif cmd == "ledger":
        cmd_ledger(args[0], args[1] if len(args) > 1 else None)
    elif cmd == "reconcile":
        return cmd_reconcile(args[0], args[1], args[2] if len(args) > 2 else None)
    else:
        print(__doc__); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
