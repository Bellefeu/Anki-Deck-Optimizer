#!/usr/bin/env python3
"""Build a finished deck for ONE module taken from the queue.

Nothing here is module-specific. The session does the thinking and writes three
JSON files; this script does the mechanical build, validation, and export.

Inputs the session writes to ./work/<module>/ :

  new_cards.json   [{"text": "...", "extra": "..."}, ...]

  ops.json         Restructuring operations on EXISTING notes. Four kinds:
                   {"nid":N, "op":"edit",    "field":1, "find":"...", "replace":"...", "why":"..."}
                   {"nid":N, "op":"rewrite", "text":"...", "extra":"...", "why":"..."}
                   {"nid":N, "op":"split",   "into":[{"text":"...","extra":"..."}, ...], "why":"..."}
                   {"nid":N, "op":"demote",  "into_nid":M, "fact":"...", "why":"..."}
                   {"nid":N, "op":"delete",  "why":"..."}

                   (fixes.json is still read for backward compatibility and
                    treated as a list of "edit" ops.)

  meta.json        {"outstanding": [...], "gaps_filled": [...], "summary": "..."}

SPLIT is the operation the old low-quality decks need most: 66-86% of their
cards are multi-fact laundry lists that must become atomic single-fact cards.
A split retires the original note and creates N replacements.

Usage:
    python3 build_deck.py                 # next pending module in the queue
    python3 build_deck.py "Regional - Truncal"
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deps import require
require("zstandard", quiet=True)

import sqlite3, zstandard, zipfile, hashlib, time, re, json, string, random

_ENTITY = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]{1,9}|#\d{1,5}|#x[0-9A-Fa-f]{1,5});")


def has_semicolon(text):
    """House rule: no semicolons in Text. HTML entities (&nbsp; &gt; &lt;) end in a
    semicolon but are not punctuation - checking the raw string flags them and
    blocks the build on cards that are actually fine."""
    return ";" in _ENTITY.sub("", text)


# ---------- RULE 8: a card never names its source ----------
# "According to <SOURCE>, the thoracolumbar fascia is..." is wrong; "The thoracolumbar
# fascia is..." is right. The fact is asserted in the deck's own voice, in every
# field, on every card.
#
# Deliberately written as attribution CONSTRUCTIONS rather than a bare brand token
# match: a brand name can also be a domain word (a source called "Crest" vs. the
# segment), so a token match would cry wolf on every cardiology card and get muted.
# _BARE_SOURCE below then catches the configured brand name used as a proper noun, minus the
# anatomical phrasings.
_SRC_NOUN = (r"(?:the module|this module|"
             r"the lecture|the textbook|the course material|the source module)")
_ATTRIB_VERB = (r"(?:states?|says?|notes?|lists?|ranks?|describes?|emphasi[sz]es|teaches|"
                r"defines?|reports?|claims?|considers?|recommends?|specifies|indicates?|"
                r"classifies|distinguishes|groups?|calls?|places?|prefers?|suggests?|"
                r"mentions?|stresses|uses?)")
ATTRIB_RE = re.compile(
    r"\b(?:according to|based on|per|as (?:taught|described|stated|presented|noted|defined)"
    r"(?:\s+(?:in|by))?|cited in|in|from)\s+" + _SRC_NOUN + r"\b"
    r"|\b" + _SRC_NOUN + r"(?:'s)?\s+" + _ATTRIB_VERB + r"\b"
    r"|\b" + _SRC_NOUN + r"(?:'s)?\s+(?:module|text|textbook|chapter|course|"
    r"curriculum|material|content)\b",
    re.I)

# The name of the course/product the deck is built from, if it has one.
# Rule 8 forbids naming the source inside a card, and a brand name is the most
# common way it leaks. Set SOURCE_NAME (env, or "source_name" in
# project_state.json) and every bare mention of it is flagged too.
#
# Left unset, the generic constructions above still catch "the module states",
# "according to the course", and friends - you just lose the brand check.
#
# FALSE_POSITIVE_AFTER exists because a brand name can collide with a real
# term used in the material itself. A source named "Crest" must not turn "the
# iliac crest" into a Rule 8 violation; the words that legitimately follow the
# domain sense go here, and a match followed by one of them is left alone.
# Extend these if your SOURCE_NAME collides with a word in your own material.
# A collision can sit on either side of the name, so both directions are checked:
#   trailing - a source named "Angle" vs. "the angle OF the rib"
#   leading  - a source named "Crest" vs. "the ILIAC crest"
FALSE_POSITIVE_AFTER  = ("of", "beat", "impulse", "cordis", "region", "sign", "line")
FALSE_POSITIVE_BEFORE = ("iliac", "the", "a", "an", "anterior", "posterior", "superior",
                         "inferior", "medial", "lateral")


def _source_name():
    """Read lazily, not at import, so a caller can set SOURCE_NAME after import and
    so an edited project_state.json takes effect without a restart."""
    import json
    n = os.environ.get("SOURCE_NAME", "").strip()
    if n:
        return n
    try:
        return (json.load(open(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "project_state.json"))).get("source_name") or "").strip()
    except Exception:
        return ""


_BARE_CACHE = {}


def _bare_source_re():
    n = _source_name()
    if not n:
        return None
    if n not in _BARE_CACHE:
        # Trailing guard is a lookahead; the leading guard is checked in code because
        # Python's re requires a FIXED-WIDTH lookbehind and this list is not.
        _BARE_CACHE[n] = re.compile(
            r"\b" + re.escape(n) + r"\b"
            r"(?!\s+(?:" + "|".join(FALSE_POSITIVE_AFTER) + r"))", re.I)
    return _BARE_CACHE[n]


def _preceded_by_domain_word(plain, start):
    """True if the word immediately before `start` marks the domain sense."""
    before = plain[:start].rstrip()
    if not before:
        return False
    return before.split()[-1].lower().strip("(\"'") in FALSE_POSITIVE_BEFORE


def source_attribution(text):
    """Rule 8 hits in one field. Returns a list of offending snippets (empty = clean).

    A brand name that collides with a term in the material itself (a source named
    "Crest" vs. "the iliac crest") is not reported - see FALSE_POSITIVE_AFTER."""
    plain = re.sub(r"<[^>]+>", " ", text or "")
    spans = [m.span() for m in ATTRIB_RE.finditer(plain)]
    hits = [plain[a:b].strip() for a, b in spans]
    bare = _bare_source_re()
    if bare is None:
        return hits
    for m in bare.finditer(plain):
        if _preceded_by_domain_word(plain, m.start()):
            continue
        if not any(a <= m.start() < b for a, b in spans):
            hits.append("..." + plain[max(0, m.start() - 30):m.end() + 30].strip() + "...")
    return hits


HERE      = os.path.dirname(os.path.abspath(__file__))
STATE     = os.path.join(HERE, "project_state.json")
WORKROOT  = os.path.join(HERE, "work")
# Where finished modules land. Point at the Drive mount so output syncs itself.
COMPLETED = os.environ.get("COMPLETED_DIR", os.path.join(HERE, "COMPLETED"))
MID       = 1766255887245          # ZzzAnki Master Cloze


PATCH = "--patch" in sys.argv
ARGS  = [a for a in sys.argv[1:] if not a.startswith("--")]


def pick_module():
    with open(STATE, encoding="utf-8") as f:
        st = json.load(f)

    if PATCH:
        # Patch an ALREADY-BUILT deck in place, using COMPLETED/ as the source.
        # This is how a verification report gets applied without a full rebuild.
        if not ARGS:
            sys.exit("--patch requires a module name: build_deck.py --patch \"<Module>\"")
        name = ARGS[0]
        built = os.path.join(COMPLETED, name, f"{name} (FINAL).apkg")
        if not os.path.exists(built):
            sys.exit(f"No built deck to patch at {built}")
        known = next((m for m in st.get("modules", []) if m["name"].lower() == name.lower()), None)
        if not known:
            sys.exit(f"'{name}' is not in project_state.json modules.")
        return st, {"name": known["name"], "deck_id": known["deck_id"],
                    "apkg": built, "mode": "patch"}

    q = st.get("pending_modules", [])
    if not q:
        sys.exit("Queue is empty. Run build_queue.py first.")
    if ARGS:
        for m in q:
            if m["name"].lower() == ARGS[0].lower():
                return st, m
        sys.exit(f"'{ARGS[0]}' is not in the queue.")
    for m in q:
        if m.get("status") == "pending":
            return st, m
    sys.exit("No pending modules left in the queue.")


def load_session_inputs(name):
    d = os.path.join(WORKROOT, name)
    def rd(fn, default):
        p = os.path.join(d, fn)
        if not os.path.exists(p):
            print(f"  (no {fn} - continuing with none)")
            return default
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    ops = rd("patch_ops.json", []) if PATCH else rd("ops.json", [])
    if not PATCH:
        for f in rd("fixes.json", []):     # legacy format -> edit ops
            ops.append({**f, "op": "edit"})
    cards = rd("patch_cards.json", []) if PATCH else rd("new_cards.json", [])
    meta = rd("meta.json", {"outstanding": [], "gaps_filled": [], "summary": ""})

    if not ops and not cards:
        sys.exit("Nothing to do: no ops and no cards found in work/<module>/.\n"
                 "  The session must WRITE these files - editing the database directly\n"
                 "  bypasses the changelog and produces an untrustworthy audit trail.")
    return cards, ops, meta


def guid():
    chars = string.ascii_letters + string.digits + "!#$%&()*+,-./:;<=>?@[]^_`{|}~"
    return "".join(random.choice(chars) for _ in range(10))


def normalize_extra(e):
    """House convention: Extra begins with a single leading space."""
    return " " + e.lstrip() if e.strip() else e


FIELD_NAMES = ["Text", "Extra", "Personal Notes", "ZzzAnki",
               "Missed Questions", "Textbook", "Additional Resources"]

MEDIA_REF = re.compile(r'<img[^>]+src="([^"]+)"'
                       r"|<img[^>]+src='([^']+)'"
                       r'|\[sound:([^\]]+)\]'
                       r'|<object[^>]+data="([^"]+)"', re.I)


def media_refs(text):
    """Every media filename a field points at, in any of Anki's reference forms."""
    out = set()
    for m in MEDIA_REF.finditer(text or ""):
        out.add(next(g for g in m.groups() if g))
    return out


def snapshot(cur):
    """{nid: [7 fields]} - taken before any op runs, compared after they all have."""
    snap = {}
    for nid, flds in cur.execute("select id, flds from notes").fetchall():
        snap[nid] = (flds.split("\x1f") + [""] * 7)[:7]
    return snap


def preservation_gate(pre, post, ops, changelog):
    """Nothing on a card may be lost that the ops do not explicitly account for.

    Two failure modes this guards shipped silently once each:
      - fields 3-7 blanked by a write that only knew about Text and Extra. A real
        deck had 14 notes carrying hand-written study notes in Personal Notes.
      - media references dropped by a rewrite or a split. A real deck had 160
        notes pointing at 90 image blobs.

    An op may opt out per note with "drop_tail": true or "drop_media": true,
    which keeps the loss deliberate and visible in the audit trail.
    """
    allow_tail = {o["nid"] for o in ops if o.get("drop_tail")}
    allow_media = {o["nid"] for o in ops if o.get("drop_media")}
    kind = {}
    for c in changelog:
        if c[0] in ("SPLIT", "DEMOTE", "DELETE", "REWRITE", "EDIT"):
            kind[c[1]] = c[0]

    problems = []
    live_tails = [tuple(f[2:]) for f in post.values()]

    for nid, fields in pre.items():
        tail = tuple(fields[2:])
        if not any(x.strip() for x in tail):
            continue
        populated = [FIELD_NAMES[i + 2] for i, x in enumerate(tail) if x.strip()]
        if nid in post:
            if tuple(post[nid][2:]) != tail:
                problems.append(f"note {nid}: {', '.join(populated)} changed or was blanked "
                                f"by a {kind.get(nid, 'write').lower()}")
        elif nid in allow_tail:
            continue
        elif kind.get(nid) == "SPLIT":
            if tail not in live_tails:
                problems.append(f"note {nid}: split did not carry {', '.join(populated)} "
                                f"onto any replacement card")
        else:
            problems.append(f"note {nid}: {kind.get(nid, 'removal').lower()} would destroy "
                            f"{', '.join(populated)}. Move the content first, or add "
                            f'"drop_tail": true to the op if the loss is intended.')

    pre_media, post_media, owner = set(), set(), {}
    for nid, fields in pre.items():
        for r in media_refs("\x1f".join(fields)):
            pre_media.add(r); owner.setdefault(r, []).append(nid)
    for fields in post.values():
        post_media |= media_refs("\x1f".join(fields))
    for r in sorted(pre_media - post_media):
        nids = owner[r]
        if all(n in allow_media for n in nids):
            continue
        problems.append(f"media '{r}' is no longer referenced by any card "
                        f"(was on note{'s' if len(nids) > 1 else ''} "
                        f"{', '.join(str(n) for n in nids[:4])}"
                        f"{'...' if len(nids) > 4 else ''})")
    return problems


def read_tail(cur, nid):
    """Fields 3-7 (Personal Notes, ZzzAnki, Missed Questions, Textbook,
    Additional Resources). Usually empty, but a real deck had 14 notes carrying
    long hand-written study notes in Personal Notes - blanking them on every
    edit/rewrite was silent, unrecoverable data loss."""
    cur.execute("select flds from notes where id=?", (nid,))
    row = cur.fetchone()
    if not row:
        return ["", "", "", "", ""]
    tail = row[0].split("\x1f")[2:]
    return (tail + ["", "", "", "", ""])[:5]


def write_fields(cur, nid, text, extra, tail=None):
    """Write both fields AND recompute sfld/csum. Never update flds without this
    - stale sort fields and duplicate hashes were a real shipped bug.
    Fields 3-7 are carried over untouched unless an explicit tail is given."""
    if tail is None:
        tail = read_tail(cur, nid)
    tail = (list(tail) + ["", "", "", "", ""])[:5]
    flds = "\x1f".join([text, normalize_extra(extra)] + tail)
    sfld = re.sub(r"<[^>]+>", "", text).strip()
    cur.execute("update notes set flds=?, sfld=?, csum=?, mod=?, usn=-1 where id=?",
                (flds, sfld, csum(text), int(time.time()), nid))


def csum(first_field):
    txt = re.sub(r"\[sound:.*?\]", "", first_field)
    txt = re.sub(r"<[^>]+>", "", txt).strip()
    return int(hashlib.sha1(txt.encode("utf-8")).hexdigest()[:8], 16)


def main():
    st, mod = pick_module()
    NAME, DID, SRC = mod["name"], mod["deck_id"], mod["apkg"]
    print(f"=== {'PATCHING' if PATCH else 'BUILDING'}: {NAME} (deck_id={DID}) ===")
    if PATCH:
        print(f"    source: already-built deck in COMPLETED/ (in-place patch)\n")
    else:
        print()

    NEW_CARDS, OPS, META = load_session_inputs(NAME)
    print(f"  new cards: {len(NEW_CARDS)} | ops: {len(OPS)}\n")

    build = os.path.join(WORKROOT, NAME, "_build")
    out   = os.path.join(COMPLETED, NAME)          # per-module output subfolder
    os.makedirs(build, exist_ok=True)
    os.makedirs(out,   exist_ok=True)

    with zipfile.ZipFile(SRC) as z:
        z.extractall(build)

    dctx = zstandard.ZstdDecompressor()
    with open(f"{build}/collection.anki21b", "rb") as f:
        raw = dctx.decompress(f.read(), max_output_size=500_000_000)
    db = f"{build}/collection.anki21"
    with open(db, "wb") as f:
        f.write(raw)

    con = sqlite3.connect(db)
    con.create_collation("unicase", lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower()))
    cur = con.cursor()
    changelog = []
    cur.execute("select count(*) from notes"); notes_before = cur.fetchone()[0]
    pre_fields = snapshot(cur)          # for the preservation gate, below

    # max() is None on an empty deck - creation mode starts from a blank .apkg
    cur.execute("select max(id) from notes"); _nb = cur.fetchone()[0] or 0
    cur.execute("select max(id) from cards"); _cb = cur.fetchone()[0] or 0
    next_nid = max(_nb + 1, int(time.time() * 1000))
    next_cid = max(_cb + 1, int(time.time() * 1000))
    if _nb == 0:
        print("  >> EMPTY SOURCE DECK - running in CREATION mode\n")

    # Every note this run wrote or rewrote. Rule 8 is enforced hard on these and
    # only reported on the rest: a legacy card the run never opened is a backlog
    # item for a patch session, not a reason to refuse to produce deliverables.
    touched = set()

    def new_note(text, extra, tail=None):
        """Insert a note + its card. Returns the new note id.
        `tail` carries fields 3-7 forward when a split replaces a note that had
        Personal Notes on it."""
        nonlocal next_nid, next_cid
        now = int(time.time())
        tail = (list(tail or []) + ["", "", "", "", ""])[:5]
        cur.execute("""insert into notes (id,guid,mid,mod,usn,tags,flds,sfld,csum,flags,data)
                       values (?,?,?,?,?,?,?,?,?,?,?)""",
                    (next_nid, guid(), MID, now, -1, "",
                     "\x1f".join([text, normalize_extra(extra)] + tail),
                     re.sub(r"<[^>]+>", "", text).strip(), csum(text), 0, ""))
        cur.execute("""insert into cards (id,nid,did,ord,mod,usn,type,queue,due,ivl,factor,
                                          reps,lapses,left,odue,odid,flags,data)
                       values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (next_cid, next_nid, DID, 0, now, -1, 0, 0, next_nid,
                     0, 0, 0, 0, 0, 0, 0, 0, ""))
        nid = next_nid
        touched.add(nid)
        next_nid += 1; next_cid += 1
        return nid

    def retire(nid):
        cur.execute("delete from cards where nid=?", (nid,))
        cur.execute("delete from notes where id=?", (nid,))

    # ---------- restructuring operations on existing notes ----------
    op_counts = {"edit": 0, "rewrite": 0, "split": 0, "demote": 0, "delete": 0}
    split_created = 0

    for op in OPS:
        nid  = op["nid"]
        kind = op.get("op", "edit")
        touched.add(nid)
        cur.execute("select flds from notes where id=?", (nid,))
        row = cur.fetchone()
        if not row:
            changelog.append(("SKIP", nid, f"{kind}: note not found")); continue
        fields = row[0].split("\x1f")

        if kind == "edit":
            fidx = op.get("field", 1)
            if op["find"] not in fields[fidx]:
                changelog.append(("SKIP", nid, f"pattern not present: {op['find'][:40]}")); continue
            fields[fidx] = fields[fidx].replace(op["find"], op["replace"])
            write_fields(cur, nid, fields[0], fields[1])
            changelog.append(("EDIT", nid, op.get("why", "")))
            op_counts["edit"] += 1

        elif kind == "rewrite":
            write_fields(cur, nid, op["text"], op["extra"])
            changelog.append(("REWRITE", nid, op.get("why", "")))
            op_counts["rewrite"] += 1

        elif kind == "split":
            parts = op.get("into", [])
            if len(parts) < 2:
                changelog.append(("SKIP", nid, "split needs >=2 parts")); continue
            tail = (fields[2:] + ["", "", "", "", ""])[:5]
            made = [new_note(p["text"], p["extra"], tail if i == 0 else None)
                    for i, p in enumerate(parts)]
            retire(nid)
            split_created += len(made)
            changelog.append(("SPLIT", nid,
                              f"{op.get('why','')} -> {len(made)} atomic cards {made}"))
            op_counts["split"] += 1

        elif kind == "demote":
            # Rule 0: fact is real but low-yield. Retire the card, preserve the fact
            # in a parent card's Extra. Logged distinctly so it is reviewable.
            tgt = op.get("into_nid")
            cur.execute("select flds from notes where id=?", (tgt,))
            trow = cur.fetchone()
            if not trow:
                changelog.append(("SKIP", nid, f"demote target {tgt} not found")); continue
            tf = trow[0].split("\x1f")
            # 'facts' (list) is preferred; 'fact' (string) kept for compatibility.
            facts = op.get("facts") or ([op["fact"]] if op.get("fact") else [])
            facts = [f.strip() for f in facts if f and f.strip()]
            if not facts:
                changelog.append(("SKIP", nid, "demote needs facts to preserve")); continue

            # LOSS GATE: every distinctive term on the retiring card must survive
            # somewhere. A demotion that drops its payload is the failure mode this
            # operation is most prone to - one sentence carried, the rest deleted.
            src_body = re.sub(r"<[^>]+>", " ", fields[0] + " " + fields[1])
            carried  = " ".join(facts) + " " + tf[1] + " " + tf[0]
            carried_l = re.sub(r"<[^>]+>", " ", carried).lower()
            src_terms = {w.lower() for w in re.findall(r"[A-Za-z]{6,}", src_body)}
            lost = sorted(w for w in src_terms if w not in carried_l)
            if lost:
                changelog.append(("SKIP", nid,
                    f"DEMOTE REFUSED - would lose: {', '.join(lost[:12])}"
                    + (f" (+{len(lost)-12} more)" if len(lost) > 12 else "")
                    + ". Carry the full payload in 'facts', or use op:'delete' if it is truly trivia."))
                continue

            tf[1] = tf[1].rstrip() + "".join("<br><br>" + f for f in facts)
            write_fields(cur, tgt, tf[0], tf[1])
            retire(nid)
            changelog.append(("DEMOTE", nid,
                              f"{op.get('why','')} -> folded into note {tgt} "
                              f"({len(facts)} passage(s), no terms lost)"))
            op_counts["demote"] += 1

        elif kind == "delete":
            retire(nid)
            changelog.append(("DELETE", nid, op.get("why", "")))
            op_counts["delete"] += 1

        else:
            changelog.append(("SKIP", nid, f"unknown op '{kind}'"))

    if any(op_counts.values()):
        print("=== RESTRUCTURING ===")
        for k, v in op_counts.items():
            if v: print(f"  {k}: {v}")
        if split_created:
            print(f"  -> {op_counts['split']} laundry-list cards became {split_created} atomic cards")
        if op_counts["demote"] or op_counts["delete"]:
            print(f"  -> Rule 0: {op_counts['demote']} demoted to Extra, "
                  f"{op_counts['delete']} deleted. REVIEW THESE in the NOTES changelog.")
        print()

    # ---------- insert new notes ----------
    cur.execute("select flds from notes")
    seen = {re.sub(r"<[^>]+>", "", f.split("\x1f")[0]).strip().lower() for (f,) in cur.fetchall()}

    added = 0
    for c in NEW_CARDS:
        key = re.sub(r"<[^>]+>", "", c["text"]).strip().lower()
        if key in seen:
            changelog.append(("DUPE-SKIP", "-", c["text"][:70])); continue
        new_note(c["text"], c["extra"])
        changelog.append(("ADD", "-", c["text"][:70]))
        seen.add(key); added += 1

    con.commit()

    # ---------- validation gate ----------
    print("=== VALIDATION ===")
    cur.execute("pragma integrity_check"); integrity = cur.fetchone()[0]
    print("sqlite integrity:", integrity)
    cur.execute("select count(*) from notes"); n_notes = cur.fetchone()[0]
    print(f"notes={n_notes} (added {added})")

    ok = True
    for label, q in {
        "orphan cards": "select count(*) from cards c left join notes n on c.nid=n.id where n.id is null",
        "notes with no card": "select count(*) from notes n left join cards c on c.nid=n.id where c.id is null",
        "duplicate guids": "select count(*) from (select guid from notes group by guid having count(*)>1)",
        "bad notetype ref": "select count(*) from notes where mid not in (select id from notetypes)",
        "bad deck ref": "select count(*) from cards where did not in (select id from decks)",
    }.items():
        cur.execute(q); v = cur.fetchone()[0]
        print(f"  {label}: {v}")
        if v: ok = False

    # PRESERVATION GATE - fields 3-7 and every media reference must survive
    lost = preservation_gate(pre_fields, snapshot(cur), OPS, changelog)
    n_media_refs = len(set().union(*[media_refs("\x1f".join(f))
                                     for f in snapshot(cur).values()]) or set())
    print(f"  preserved: fields 3-7 intact, {n_media_refs} media reference(s) live"
          if not lost else f"  !! PRESERVATION FAILURES: {len(lost)}")
    for msg in lost:
        print(f"     {msg}")
    if lost:
        ok = False

    cur.execute("select id, flds from notes")
    viol = 0
    for nid, f in cur.fetchall():
        t, e = f.split("\x1f")[0], f.split("\x1f")[1]
        n_c1 = len(re.findall(r"\{\{c1::", t))
        if n_c1 == 0: viol += 1; print(f"  RULE FAIL {nid}: no cloze")
        if n_c1 > 3:  viol += 1; print(f"  RULE FAIL {nid}: >3 clozes")
        if "{{c2::" in t or "{{c3::" in t: viol += 1; print(f"  RULE FAIL {nid}: c2/c3")
        if has_semicolon(t): viol += 1; print(f"  RULE FAIL {nid}: semicolon")
        if t.count("{{") != t.count("}}"): viol += 1; print(f"  RULE FAIL {nid}: unbalanced braces")
        for tag in ("b", "u", "i"):
            if e.count(f"<{tag}>") != e.count(f"</{tag}>"):
                viol += 1; print(f"  RULE FAIL {nid}: unbalanced <{tag}>")
    print(f"  house-rule violations: {viol}")
    if viol: ok = False

    # RULE 8 - no card names its source. Checked in Text AND Extra: attribution
    # survives in Extra most often, and the term-level diffs elsewhere are blind to it.
    cur.execute("select id, flds from notes")
    attrib_new, attrib_legacy = [], []
    for nid, f in cur.fetchall():
        parts = (f.split("\x1f") + ["", ""])[:7]
        for fname, s in (("Text", parts[0]), ("Extra", parts[1])):
            for hit in source_attribution(s):
                (attrib_new if nid in touched else attrib_legacy).append((nid, fname, hit))
    for nid, fname, hit in attrib_new:
        print(f"  RULE 8 FAIL {nid} ({fname}): names the source - {hit[:80]}")
    if attrib_new:
        print(f"  !! {len(attrib_new)} rule-8 violation(s) on cards THIS RUN wrote or edited.")
        print("     Strip the attribution, keep the fact, and rebuild. Do not ship these.")
        ok = False
    if attrib_legacy:
        print(f"  RULE 8 WARN: {len(attrib_legacy)} pre-existing violation(s) on cards this "
              f"run did not touch:")
        for nid, fname, hit in attrib_legacy[:8]:
            print(f"     [{nid}] {fname}: {hit[:80]}")
        print("     Not blocking (the run did not create them), but they are defects.")
        print("     Add rewrite ops for them - verify_deck.py fails on these.")

    # sfld/csum drift - the bug that shipped once already
    import hashlib as _h
    cur.execute("select id, flds, sfld, csum from notes")
    drift = 0
    for _nid, _flds, _sfld, _csum in cur.fetchall():
        first = _flds.split("\x1f")[0]
        exp_s = re.sub(r"<[^>]+>", "", re.sub(r"\[sound:.*?\]", "", first)).strip()
        exp_c = int(_h.sha1(exp_s.encode("utf-8")).hexdigest()[:8], 16)
        if (_sfld or "").strip() != exp_s or _csum != exp_c:
            drift += 1
    print(f"  sfld/csum drift: {drift}"
          + ("   (pre-existing in source deck - not introduced here)" if drift else ""))

    # ---------- export ----------
    cur.execute("select flds from notes order by id")
    lines = ["|".join(f.split("\x1f")[:2]) for (f,) in cur.fetchall()]
    txt_path = os.path.join(out, f"{NAME} (FINAL).txt")
    with open(txt_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"\nWrote {txt_path} ({len(lines)} cards)")
    con.close()

    with open(db, "rb") as f:
        comp = zstandard.ZstdCompressor().compress(f.read())
    with open(f"{build}/collection.anki21b", "wb") as f:
        f.write(comp)

    apkg_path = os.path.join(out, f"{NAME} (FINAL).apkg")
    # An .apkg holds its media as numbered blobs ("0", "1", ...) alongside the
    # "media" name map. Repacking only the four well-known parts silently drops
    # every image in the deck - a real deck had 90 blobs behind 160 <img> tags.
    # Read the source archive BEFORE touching the destination: on --patch the two
    # are the SAME file, so removing the destination first destroys the member
    # list and the blob inventory we are about to check against.
    with zipfile.ZipFile(SRC) as _z:
        members   = _z.namelist()
        src_blobs = {n for n in members if n.isdigit()}
    if os.path.exists(apkg_path): os.remove(apkg_path)
    for extra_part in ("collection.anki21b", "collection.anki2", "media", "meta"):
        if extra_part not in members:
            members.append(extra_part)
    with zipfile.ZipFile(apkg_path, "w", zipfile.ZIP_STORED) as z:
        written = 0
        for part in members:
            p = os.path.join(build, part)
            if os.path.exists(p):
                z.write(p, part); written += 1
    n_media = max(0, written - 4)
    print(f"Repacked {written} archive members ({n_media} media blob(s) carried over)")
    with zipfile.ZipFile(apkg_path) as _z:
        out_blobs = {n for n in _z.namelist() if n.isdigit()}
    missing_blobs = sorted(src_blobs - out_blobs, key=int)
    if missing_blobs:
        ok = False
        print(f"  !! {len(missing_blobs)} media blob(s) lost in the repack: "
              f"{', '.join(missing_blobs[:10])}{'...' if len(missing_blobs) > 10 else ''}")
    elif src_blobs:
        print(f"  all {len(src_blobs)} source media blob(s) present in the output")
    print(f"Wrote {apkg_path} ({os.path.getsize(apkg_path)//1024} KB)")

    # ---------- round-trip verify ----------
    with zipfile.ZipFile(apkg_path) as z:
        blob = z.read("collection.anki21b")
    vraw = zstandard.ZstdDecompressor().decompress(blob, max_output_size=500_000_000)
    vp = f"{build}/verify.anki21"
    with open(vp, "wb") as f: f.write(vraw)
    v = sqlite3.connect(vp)
    v.create_collation("unicase", lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower()))
    vc = v.cursor()
    vc.execute("pragma integrity_check"); iv = vc.fetchone()[0]
    vc.execute("select count(*) from notes"); vn = vc.fetchone()[0]
    v.close()
    print(f"Round-trip verify: integrity={iv}, notes={vn}")

    # On --patch the changelog must ACCUMULATE. Overwriting it with the patch's own
    # entries destroys the build's history, and build_notes.js reads this file - a
    # NOTES rebuild after a patch then silently loses every split, rewrite, add and
    # IO figure request the build recorded. Prior entries come from scratch if it
    # survived, otherwise from the audit trail in COMPLETED/. Deduplicated, so
    # re-running the same patch does not double up.
    clog_path = os.path.join(WORKROOT, NAME, "changelog.json")
    if PATCH:
        prior = []
        for cand in (clog_path, os.path.join(COMPLETED, NAME, "audit", "changelog.json")):
            if os.path.exists(cand):
                try:
                    with open(cand, encoding="utf-8") as f:
                        prior = json.load(f)
                except Exception as e:
                    print(f"  (could not read prior changelog {cand}: {e})")
                if prior:
                    break
        seen = {json.dumps(c, sort_keys=True) for c in changelog}
        carried = [c for c in prior if json.dumps(c, sort_keys=True) not in seen]
        # NB: written to disk, but `changelog` itself is left alone - the
        # ops-applied gate below counts it against len(OPS), so folding the
        # build's history into it would break that check.
        to_write = carried + changelog
        if carried:
            print(f"  changelog: carried {len(carried)} prior entr"
                  f"{'y' if len(carried) == 1 else 'ies'} forward "
                  f"({len(to_write)} total)")
    else:
        to_write = changelog
    with open(clog_path, "w") as f:
        json.dump(to_write, f, indent=1)

    applied = sum(1 for c in changelog
                  if c[0] in ("EDIT", "REWRITE", "SPLIT", "DEMOTE", "DELETE"))
    edits_applied = applied
    if applied != len(OPS):
        ok = False
        print(f"\n!! OP MISMATCH: intended {len(OPS)}, applied {applied}")
        for s in [c for c in changelog if c[0] == "SKIP"]:
            print(f"   SKIPPED: note {s[1]} - {s[2]}")
        print("   A find-pattern likely failed because the target text is HTML-wrapped.")
    else:
        print(f"Ops: {applied}/{len(OPS)} applied, 0 silent failures")

    # ACCOUNTING GATE - the changelog must fully explain the deck's actual delta.
    # A run that edits the database outside the ops path produces counts that do not
    # reconcile, and an audit trail that silently under-reports what happened.
    n_add    = sum(1 for c in changelog if c[0] == "ADD")
    n_split  = sum(1 for c in changelog if c[0] == "SPLIT")
    n_demote = sum(1 for c in changelog if c[0] == "DEMOTE")
    n_del    = sum(1 for c in changelog if c[0] == "DELETE")
    split_made = sum(int(re.search(r"-> (\d+) atomic", c[2]).group(1))
                     for c in changelog
                     if c[0] == "SPLIT" and re.search(r"-> (\d+) atomic", c[2]))
    expected = notes_before + n_add + split_made - n_split - n_demote - n_del
    print("\n=== ACCOUNTING ===")
    print(f"  before {notes_before} + {n_add} added + {split_made} from splits"
          f" - {n_split} split-parents - {n_demote} demoted - {n_del} deleted = {expected}")
    print(f"  actual: {n_notes}")
    if expected != n_notes:
        ok = False
        print(f"  !! MISMATCH of {n_notes - expected}. The changelog does not explain the deck.")
        print("     Something changed the database outside the ops path. Do not ship this.")
    else:
        print("  reconciled - the changelog fully explains the deck")

    build_ok = ok and iv == "ok" and vn == n_notes
    print("\nBUILD OK" if build_ok else "\nBUILD HAD PROBLEMS")

    # ---------- update state, pop the queue, regenerate handoff ----------
    from update_handoff import record_run
    outstanding = list(META.get("outstanding", []))
    if not build_ok:
        outstanding.append("BUILD DID NOT PASS ALL GATES - re-run before using output")

    with open(STATE, encoding="utf-8") as f:
        st2 = json.load(f)
    if not PATCH:
        st2["pending_modules"] = [m for m in st2.get("pending_modules", []) if m["name"] != NAME]
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st2, f, indent=2)

    # A patch must not recompute cards_before - on a patch `added` is usually 0, so
    # n_notes - added yields the CURRENT count and silently overwrites the original.
    # `notes_before` is measured, not derived. Deriving it as n_notes - added is
    # wrong on any deck with splits: a split adds replacement notes that the
    # `added` counter never sees, so the derived "before" is inflated by exactly
    # the number of split replacements (a real run reported 233 for a 203-card deck).
    prior = next((m for m in st2.get("modules", []) if m["name"] == NAME), None)
    if PATCH and prior:
        before_count = prior.get("cards_before", notes_before)
        added_count  = prior.get("added", 0) + added
        edited_count = prior.get("edited", 0) + edits_applied
    else:
        before_count, added_count, edited_count = notes_before, added, edits_applied

    record_run(
        module_name = NAME,
        deck_id     = DID,
        before      = before_count,
        after       = n_notes,
        added       = added_count,
        edited      = edited_count,
        outstanding = outstanding,
        gaps        = META.get("gaps_filled", []),
        summary     = META.get("summary", f"Built {NAME}."),
        status      = "built-unverified" if build_ok else "in-progress",
    )
    print(f"Queue remaining: {len(st2['pending_modules'])} module(s)")


if __name__ == "__main__":
    main()
