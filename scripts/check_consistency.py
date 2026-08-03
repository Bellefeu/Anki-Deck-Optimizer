#!/usr/bin/env python3
"""Rule 10 / 9 / 13 / 12 / 14 — the checks a card-by-card pass structurally cannot do.

A contradiction is a property of a PAIR of cards. Every pass in this pipeline reads one
card at a time, so none of them can find one. The Truncal deck shipped 11 contradictions
that way - transverse process depth 3-6 cm AND 2-4 cm, paravertebral needle Quincke AND
Tuohy, PECS arm at 90 degrees AND at the side - each half read separately and approved
separately. verify_deck.py checks information LOSS against the original; nothing checked
information CONFLICT inside the finished deck.

This does. It extracts one row per assertion, keys it by subject and attribute, and
prints every group holding more than one distinct value.

It makes NO medical judgement and needs none. It does not know that 6-8 cm is the right
rib-angle distance; it knows the deck says 3-6 AND 5-7 AND 7-10, which is enough, because
a deck that contradicts itself is wrong somewhere no matter which value is right.

    python3 check_consistency.py "COMPLETED/<Module>/<Module> (FINAL).apkg"
    python3 check_consistency.py deck.apkg --json claims.json
    python3 check_consistency.py deck.apkg --baseline claims_before.json   # new only
    python3 check_consistency.py deck.apkg --all                           # no truncation

Exit codes:  0 clean · 1 collisions or a dose with no drug (gate) · 2 usage error

DESIGN NOTE — why the gate is narrow. A noisy gate gets ignored, which is worse than no
gate. Only two things fail the run: a numeric/level/count value that differs across cards
answering the same question, and a weight-based dose with no drug named. Everything softer
(categorical conflicts, certainty language, the Rule 9 and Rule 13 worklists) prints but
never fails, because those need a human to adjudicate and a red build teaches people to
pass --force.
"""

import sys, os, re, json, zipfile, sqlite3, itertools
from collections import defaultdict

try:
    from deps import require
    require("zstandard", quiet=True)
except Exception:                                   # standalone use outside the pipeline
    pass
import zstandard


# ─────────────────────────────── loading ───────────────────────────────

def load(apkg):
    """Same reader as verify_deck.py — kept byte-compatible on purpose."""
    with zipfile.ZipFile(apkg) as z:
        names = z.namelist()
        blob = z.read("collection.anki21b") if "collection.anki21b" in names else None
        raw = (zstandard.ZstdDecompressor().decompress(blob, max_output_size=500_000_000)
               if blob else z.read("collection.anki21" if "collection.anki21" in names
                                   else "collection.anki2"))
    p = os.path.join("/tmp", os.path.basename(apkg) + ".consistency.db")
    open(p, "wb").write(raw)
    con = sqlite3.connect(p)
    con.create_collation("unicase", lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower()))
    return con


def notes_of(con):
    """[(nid, Text, Extra, Textbook, tags)] — Textbook is field 6 (Rule 14)."""
    out = []
    for nid, flds, tags in con.execute("select id, flds, tags from notes order by id"):
        f = (flds.split("\x1f") + [""] * 7)[:7]
        out.append((nid, f[0], f[1], f[5], (tags or "").strip()))
    return out


def plain(s, keep_cloze_marks=False):
    """HTML out, cloze braces out, answer kept. A cloze answer is the card's assertion."""
    s = re.sub(r"\{\{c\d+::(.*?)(?:::.*?)?\}\}",
               (r"«\1»" if keep_cloze_marks else r"\1"), s or "", flags=re.S)
    s = re.sub(r"<br\s*/?>", " . ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return re.sub(r"\s+", " ", s).strip()


def sentences(s):
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+|\s+\.\s+", s) if x.strip()]


# ─────────────────────── subject lexicon, mined from the deck ───────────────────────
#
# Hardcoding "paravertebral block, TAP block, ..." would make this useless on a
# pharmacology deck. Subjects are mined from the corpus instead: any noun phrase ending
# in a domain head noun is a candidate.

# The one place a domain lexicon is genuinely required. Everything else in this file is
# mined from the deck, but "what counts as a thing a claim can be ABOUT" has to be told.
# Extending it is how the tool reaches a new subject area: on the airway and machine
# decks the regional-only list indexed 21 and 17 assertions out of ~280 notes each,
# because it had never heard of a blade, a cuff or a vaporizer.
HEAD_NOUNS = (
    # regional / anatomy
    r"block|nerve|muscle|artery|vein|ligament|space|fascia|plane|membrane|"
    r"process|sheath|groove|joint|approach|technique|catheter|needle|probe|"
    r"transducer|injection|line|border|margin|dermatome|root|ramus|rami|plexus|"
    # airway
    r"tube|blade|cuff|mask|airway|laryngoscope|bougie|stylet|scope|balloon|"
    r"cannula|cricothyrotomy|tracheostomy|intubation|ventilation|"
    # equipment
    r"valve|vaporizer|vaporiser|flowmeter|circuit|absorber|canister|ventilator|"
    r"cylinder|regulator|alarm|monitor|sensor|analyzer|analyser|scavenger|"
    r"manifold|bellows|hose|connector|filter|humidifier|blender|"
    # pharmacology / physiology
    r"drug|agent|receptor|channel|agonist|antagonist|blocker|inhibitor|"
    r"pressure|volume|flow|rate|dose|concentration|gradient|capacity|resistance|"
    r"compliance|threshold|ratio|coefficient|clearance|half-life|onset|duration|"
    r"temperature|output|saturation|"
    # general
    r"syndrome|reflex|sign|test|scale|index|curve|score|grade|class|stage|phase")

SUBJ_RE = re.compile(
    r"\b((?:[A-Za-z][A-Za-z0-9'-]*\s+){0,3}(?:" + HEAD_NOUNS + r")e?s?)\b", re.I)


def _singular(tok):
    """Conservative. A naive -s/-es stripper turns 'process' into 'proces' and
    'serratus' into 'serratu', which then fail to match anything at all."""
    if len(tok) < 5 or tok.endswith(("ss", "us", "is", "as", "os")):
        return tok
    if tok.endswith("ies"):
        return tok[:-3] + "y"
    if tok.endswith("es") and tok[-3] in "sxzhc":
        return tok[:-2]
    if tok.endswith("s"):
        return tok[:-1]
    return tok

# Words that qualify a technique without changing WHICH technique it is. Stripping them
# is what makes "landmark thoracic paravertebral block" collide with "paravertebral
# block" — the entire point, since that pair carried the depth contradiction.
QUALIFIERS = {
    "a", "an", "the", "this", "that", "each", "every", "any", "single", "one", "two",
    "landmark", "ultrasound", "ultrasound-guided", "guided", "continuous", "shot",
    "single-shot", "classic", "standard", "conventional", "modified", "bilateral",
    "unilateral", "successful", "failed", "typical", "primary", "main", "correct",
    "initial", "final", "same", "his", "her", "their", "its", "our", "your", "my",
    "of", "for", "with", "in", "on", "at", "to", "and", "or", "is", "are", "be",
    "performing", "during", "when", "after", "before", "using", "targeted", "target",
    "from", "through", "into", "between", "within", "along", "near", "toward",
    "towards", "under", "over", "below", "above", "by", "across", "onto", "past",
    "beyond", "around", "against", "up", "down", "out",
}

# Stripped only when they LEAD the phrase. SUBJ_RE takes up to three words before the
# head noun, so "contact the transverse process" yields "contact transverse process",
# which no longer matches the "transverse process" every other card uses.
LEADING_VERB = re.compile(
    r"^(contact|reach|encounter|advanc|insert|redirect|withdraw|identify|visuali[sz]e|"
    r"palpate|locate|find|enter|exit|pierce|puncture|traverse|cross|block|inject|"
    r"deposit|plac|position|scan|align|rotat|slid|target|avoid|reveal|show|"
    r"display|form|cover|supply|innervate|arise|emerge|divide|give|run|travel|"
    r"course|lie|sit|rest|pass|is|are|be|was|were|has|have|had)\w*$")

# Stripped from a TECHNIQUE subject only. "landmark thoracic paravertebral block" and
# "paravertebral block" are one technique; "lateral pectoral nerve" and "medial pectoral
# nerve" are two nerves with different roots. Collapsing the second pair invented a
# contradiction out of a correct contrast card.
POSITIONAL = {
    "thoracic", "lumbar", "cervical", "sacral", "abdominal", "posterior", "anterior",
    "lateral", "medial", "superior", "inferior", "deep", "superficial", "proximal",
    "distal", "internal", "external", "innermost", "left", "right", "upper", "lower",
    "greater", "lesser", "long", "short", "transverse", "spinous",
}
TECHNIQUE_HEAD = re.compile(
    r"\b(block|approach|technique|injection|catheter|needle|probe|transducer|line|"
    r"test|scale|index|curve)$", re.I)

# The subset that names a PROCEDURE rather than a piece of kit. Only these can stop an
# Extra field from inheriting the card's topic.
PROCEDURE_HEAD = re.compile(r"\b(block|approach|technique|injection|intubation|"
                            r"ventilation|cricothyrotomy|tracheostomy)$", re.I)

FUNCTION_WORDS = {
    "alongside", "because", "since", "although", "while", "where", "which", "whose",
    "there", "here", "then", "than", "also", "both", "either", "neither", "such",
    "these", "those", "some", "many", "most", "more", "less", "other", "another",
    "given", "named", "called", "known", "seen", "found", "used", "made", "via",
}

ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5"}


def norm_subject(phrase):
    """Canonical subject. Positional adjectives survive on anatomy, not on techniques."""
    raw = (phrase or "").strip()
    p = re.sub(r"[^a-z0-9\s-]", " ", raw.lower())
    toks = [_singular(ROMAN.get(t, t)) for t in p.split()]
    drop = set(QUALIFIERS) | FUNCTION_WORDS
    if TECHNIQUE_HEAD.search(" ".join(toks)):
        drop |= POSITIONAL
    toks = [t for t in toks if t not in drop]
    while len(toks) > 1 and LEADING_VERB.match(toks[0]):
        toks = toks[1:]
    return " ".join(toks)


def expand_abbrev(s, abbrev):
    return " ".join(abbrev.get(t, t) for t in s.split()) if abbrev else s


def build_lexicon(texts):
    """Attested subjects, an abbreviation map, and the variant-collapsing map.

    The collapse stops at TWO tokens. Reducing 'paravertebral block' to bare 'block'
    lumps every block in the deck into one subject and buries real conflicts under a
    hundred unrelated volumes — the first version of this script did exactly that.
    """
    abbrev = {}
    for t in texts:
        for m in re.finditer(r"\b((?:[A-Z][A-Za-z-]+\s+){1,3})\(([A-Z]{2,6})\)", t):
            full = norm_subject(m.group(1))
            if full:
                abbrev[m.group(2).lower()] = full

    counts = defaultdict(int)
    for t in texts:
        for m in SUBJ_RE.finditer(t):
            s = expand_abbrev(norm_subject(m.group(1)), abbrev)
            if 2 <= len(s.split()) <= 5:
                counts[s] += 1

    canon = {}
    for s in counts:
        toks = s.split()
        best = s
        for i in range(1, len(toks) - 1):        # never down to a single bare head noun
            suf = " ".join(toks[i:])
            if counts.get(suf, 0) >= 1:
                best = suf
        canon[s] = best
    return canon, abbrev, counts


REGION_RE = re.compile(r"\b(thoracic|lumbar|cervical|sacral|pediatric|paediatric)\b", re.I)

# A quantity noun is a poor subject: "the volume of local anesthetic injected at each
# level" is a claim about the BLOCK, not about "volume". These only win when the
# sentence offers nothing better - adding them to HEAD_NOUNS without this demotion
# silently dropped a real paravertebral volume contradiction the tool had been catching.
PREP_BEFORE = re.compile(
    r"\b(from|through|into|between|within|along|near|toward|towards|under|over|"
    r"below|above|across|onto|past|beyond|around|against|at|in|of|by|for|with|"
    r"on|during)\s+(?:the|a|an|its|their)?\s*$", re.I)

PREP_LEADS = re.compile(
    r"\s*(from|through|into|between|within|along|near|toward|towards|under|over|"
    r"below|above|across|onto|past|beyond|around|against|at|in|of|by|for|with|on)\s",
    re.I)

QUANTITY_HEAD = re.compile(
    r"\b(pressure|volume|flow|rate|dose|concentration|gradient|capacity|resistance|"
    r"compliance|threshold|ratio|coefficient|clearance|onset|duration|temperature|"
    r"output|saturation|line|margin|border|score|grade|class|stage|phase)$", re.I)


def subject_at(sent, canon, abbrev, before=None):
    """Nearest lexicon hit at or before the claim. None for a bare head noun.

    A claim keyed to just "block" or "needle" cannot be compared against anything
    meaningfully, and indexing it manufactures collisions between unrelated cards.

    A spinal region named anywhere in the sentence is folded onto an ANATOMICAL
    subject. Without it, "the transverse process is contacted at 2-4 cm in the thoracic
    region" and "...5-8 cm in the lumbar region" look like one structure with two
    depths, and the deliberate thoracic/lumbar contrast pair reports as a contradiction
    while the real thoracic-vs-thoracic conflict (2-4 against 3-6) hides inside it.
    Technique subjects deliberately do NOT get this - "thoracic paravertebral block"
    and "paravertebral block" must stay one subject.
    """
    cands = []
    for m in SUBJ_RE.finditer(sent):
        cand = expand_abbrev(norm_subject(m.group(1)), abbrev)
        cand = canon.get(cand, cand)
        if len(cand.split()) < 2:
            continue
        # "the lateral pectoral nerve arises FROM the brachial plexus at roots C5-C7" is
        # a claim about the nerve, not the plexus; "the VOLUME of local anesthetic for a
        # paravertebral block" is a claim about the block, not about volume. Both are
        # PREFERENCES, not vetoes - a demoted candidate still wins over nothing, and
        # making them absolute silently dropped collisions this tool had been catching.
        rank = (2 if QUANTITY_HEAD.search(cand) else
                1 if (PREP_BEFORE.search(sent[max(0, m.start() - 14):m.start()])
                      or PREP_LEADS.match(m.group(1))) else 0)
        cands.append((m.start(), cand, rank))
    if not cands:
        return None

    if before is None:
        best = min(cands, key=lambda c: (c[2], c[0]))[1]
    else:
        earlier = [c for c in cands if c[0] <= before]
        pool = earlier or cands
        # nearest to the claim, breaking ties toward the stronger candidate
        best = min(pool, key=lambda c: (c[2], -c[0] if earlier else c[0]))[1]

    if not TECHNIQUE_HEAD.search(best):
        regions = [(m.start(), m.group(1).lower()) for m in REGION_RE.finditer(sent)]
        if regions and not any(r in best for _, r in regions):
            ref = before if before is not None else 0
            near = min(regions, key=lambda r: abs(r[0] - ref))[1]
            best = f"{near} {best}"
    return best


# ─────────────────────────── attribute taxonomy ───────────────────────────
#
# First matching cue set wins, so order matters: specific measurements before generic
# ones. "advanced a further 2 to 4 mm" must be `advancement`, not `depth-to-landmark`,
# or it collides with the depth-to-bone claims and buries the real signal. Likewise
# "20 mL in 5 mL increments" must split into `volume` and `volume-increment`, or one
# compound sentence reports itself as a contradiction.

ATTR_RULES = [
    # "0.2 mL/kg per injection" and "0.4 mL/kg in total" are two attributes, not two
    # answers. Total-vs-per-unit is a universal distinction, so it is split first.
    ("total",                 r"\btotals?\b|cumulative|combined|altogether|in all|"
                              r"across (?:the|both|all)"),
    ("volume-increment",      r"increments?\b|aliquots?\b|divided doses"),
    ("distance-from-midline", r"from the mid ?line|lateral to the (?:mid ?line|spinous)|"
                              r"lateral to the spinous process"),
    ("advancement",           r"advanc\w+|walk\w* off|past the|further|deeper|beyond the"),
    ("depth-to-landmark",     r"depth of|contact\w*|at a depth|encounter\w*|reach\w+|"
                              r"is (?:met|found) at|lies at|located at"),
    ("needle-length",         r"needle (?:length|is|of)|length is|cm needle|inch needle|"
                              r"mm needle|needle measur"),
    ("volume-per-level",      r"per level|at each level|per side|per injection"),
    ("volume",                r"volume|inject\w*|bolus|hydrodissect|deposit\w*"),
    ("probe-position",        r"transducer|probe|scan\w*"),
]

UNIT_RE = re.compile(
    r"(?<![\w.])((?:\d+(?:\.\d+)?)(?:\s*(?:-|–|—|to)\s*(?:\d+(?:\.\d+)?))?)\s*"
    r"(cmH2?O|cm ?H2O|mmHg|kPa|psi|bar\b|"
    r"L/min|liters?/min|litres?/min|mL/min|mL/kg/min|"
    r"mL/kg|mg/kg|mcg/kg|micrograms?/kg|mg/mL|mcg/mL|"
    r"mL|milliliters?|mg|mcg|micrograms?|L\b|"
    r"cm|centimet\w+|mm|millimet\w+|inch(?:es)?|gauge|mA|MHz|%|degrees?)",
    re.I)

LEVEL_RE = re.compile(r"\b([TLCS]\d{1,2})\s*(?:-|–|—|to|through)\s*([TLCS]?\d{1,2})\b"
                      r"|\b([TLCS]\d{1,2})\b")

WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
COUNT_RE = re.compile(r"\b(" + "|".join(WORD_NUM) + r"|\d{1,2})\s+"
                      r"(layers?|levels?|branches?|leaves|leaflets?|divisions?|"
                      r"segments?|dermatomes?|compartments?|heads?)\b", re.I)

UNIT_FAMILY = {
    "ml": "volume", "milliliter": "volume", "milliliters": "volume",
    "ml/kg": "dose-volumetric",
    "mg/kg": "dose-mass", "mcg/kg": "dose-mass", "micrograms/kg": "dose-mass",
    "microgram/kg": "dose-mass",
    "mg": "mass", "mcg": "mass", "micrograms": "mass", "microgram": "mass",
    "cm": "length", "centimeter": "length", "centimeters": "length",
    "centimetre": "length", "centimetres": "length",
    "mm": "length", "millimeter": "length", "millimeters": "length",
    "millimetre": "length", "millimetres": "length",
    "inch": "length", "inches": "length",
    "gauge": "gauge", "ma": "current", "mhz": "frequency",
    "%": "percent", "degree": "angle", "degrees": "angle",
    "cmh2o": "pressure", "cm h2o": "pressure", "cmho": "pressure",
    "mmhg": "pressure", "kpa": "pressure", "psi": "pressure", "bar": "pressure",
    "l/min": "gas-flow", "liter/min": "gas-flow", "liters/min": "gas-flow",
    "litre/min": "gas-flow", "litres/min": "gas-flow", "ml/min": "gas-flow",
    "ml/kg/min": "gas-flow", "l": "volume",
    "mg/ml": "concentration", "mcg/ml": "concentration",
}
TO_MM = {"cm": 10.0, "centimeter": 10.0, "centimeters": 10.0, "centimetre": 10.0,
         "centimetres": 10.0, "mm": 1.0, "millimeter": 1.0, "millimeters": 1.0,
         "millimetre": 1.0, "millimetres": 1.0, "inch": 25.4, "inches": 25.4}

NUMERIC_ATTRS = ("distance-from-midline", "depth-to-landmark", "advancement",
                 "needle-length", "volume", "volume-per-level", "volume-increment",
                 "probe-position", "spinal-level", "percent", "count", "gauge",
                 "angle", "dose-mass", "dose-volumetric", "current", "frequency",
                 "length", "mass")


def attr_for(sent, pos):
    """Attribute from the cue words nearest the claim, then from the whole sentence."""
    window = sent[max(0, pos - 90):pos + 40].lower()
    for name, cues in ATTR_RULES:
        if re.search(cues, window):
            return name
    low = sent.lower()
    for name, cues in ATTR_RULES:
        if re.search(cues, low):
            return name
    return None


def norm_range(v):
    nums = re.findall(r"\d+(?:\.\d+)?", v)
    return "-".join(f"{float(n):g}" for n in nums) if nums else v.strip()


def mm_span(value, unit):
    f = TO_MM.get(unit.lower())
    if not f:
        return None
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", value)]
    return (min(nums) * f, max(nums) * f) if nums else None


# ───────────────── categorical contrast families, mined from the deck ─────────────────
#
# Quincke vs Tuohy is not a numeric conflict, so the unit scanner cannot see it. Terms
# sharing a role form a contrast family, and two cards answering one question with
# different members of one family is the same defect wearing different clothes.
#
# These are SOFT findings. An anatomy deck names muscles in almost every sentence, so a
# family hit is evidence a human should look, not evidence of a defect. Only single
# members are indexed: a sentence naming two layers is describing the plane between
# them, not asserting one of them.

FAMILY_SEEDS = {
    "needle-type": ["tuohy", "quincke", "b-bevel", "blunt", "pencil-point", "whitacre",
                    "sprotte", "echogenic", "hypodermic"],
    "needle-direction": ["medial to lateral", "lateral to medial", "cephalad to caudad",
                         "caudad to cephalad", "in-plane", "out-of-plane"],
    # Body and limb are separate families on purpose. "supine with the arm abducted to
    # 90 degrees" and "supine with the arm placed at the side" agree on the body and
    # contradict on the limb; one combined family sees two members in each sentence,
    # calls both descriptions rather than claims, and misses the conflict entirely.
    "body-position": ["supine", "prone", "lateral decubitus", "sitting", "standing"],
    "limb-position": ["at the side", "abducted", "adducted", "externally rotated",
                      "forward", "overhead", "flexed", "extended"],
}

NEEDLE_PATH_CUE = re.compile(
    r"\btarget\w*|endpoint|tip (?:is|lies|rests|stops)|deep to|just below|between the|"
    r"travers\w+|passes? through|pierc\w+|injected? (?:into|between)|deposit\w+", re.I)

DIRECTION_TOKENS = re.compile(
    r"\b(medial(?:ly)?|lateral(?:ly)?|cephalad|caudad|cranial(?:ly)?|caudal(?:ly)?|"
    r"superficial(?:ly)?|anterior(?:ly)?|posterior(?:ly)?|superior(?:ly)?|"
    r"inferior(?:ly)?|in-plane|out-of-plane|clockwise|counter-?clockwise)\b", re.I)

PROCEDURAL_VERB = re.compile(
    r"\b(advanc\w+|insert\w+|redirect\w+|withdraw\w+|angled?|tilt\w*|aim\w*|direct\w+|"
    r"walk\w* off|thread\w*|puncture[ds]?|pierc\w+|inject\w*|position\w*|placed?|"
    r"slid\w+|rotat\w+|approach\w*|orient\w*)\b", re.I)

# -caine catches every local anaesthetic without listing them; the rest are the classes
# that actually appear in a weight-based dose in this collection.
DRUG_RE = re.compile(
    r"\b(\w*caine|\w*fentanil|\w*curium|\w*onium|midazolam|diazepam|lorazepam|ketamine|"
    r"propofol|etomidate|dexmedetomidine|succinylcholine|rocuronium|vecuronium|"
    r"epinephrine|adrenaline|norepinephrine|phenylephrine|ephedrine|neostigmine|"
    r"sugammadex|glycopyrrolate|atropine|clonidine|dexamethasone|morphine|"
    r"hydromorphone|ketorolac|acetaminophen|heparin|protamine|tranexamic)\b", re.I)

DOSE_UNIT_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|micrograms?|mL)\s*/\s*kg\b", re.I)

# Rule 6's house closer is "A high yield discriminator is that <the other card> says X".
# That sentence names a DIFFERENT card's value on purpose, so a front/back comparison
# against it fails every correctly-built deck. Cross-references are excluded from G2 but
# NOT from G1: a cross-reference carrying the wrong number is a real defect, and one of
# them (a rib-angle distance quoted as 5-7 cm against a card saying 3-6) was exactly that.
CROSS_REF = re.compile(
    r"discriminator|contrast|corresponding figure|compared (?:with|to)|whereas|unlike|"
    r"as opposed to|rather than|the other|counterpart|elsewhere in|"
    r"\b(?:higher|lower|shorter|longer|larger|smaller|deeper|shallower|greater|"
    r"wider|narrower|fewer|more)\b", re.I)

HYPE = re.compile(r"\b(exactly|precisely|perfectly|strictly|absolutely|consistently|"
                  r"always|never|guarantee[sd]?|invariabl\w+)\b", re.I)


# ─────────────────────────────── extraction ───────────────────────────────

INSTRUMENT_ATTRS = ("needle-length", "gauge", "needle-type")


def note_topic(text, canon, abbrev):
    """The procedure a card is about, taken from its Text field.

    An Extra field routinely says "A 5 cm 22-gauge B-bevel needle is used" without ever
    naming the block - the front already did. Keyed on its own words that claim becomes
    a fact about "b-bevel needle" and never meets the front's "8-10 cm Tuohy". Both are
    answers to "what needle for this block", so the block is the subject.
    """
    best = None
    for sent in sentences(plain(text)):
        for m in SUBJ_RE.finditer(sent):
            s = expand_abbrev(norm_subject(m.group(1)), abbrev)
            s = canon.get(s, s)
            if len(s.split()) >= 2 and PROCEDURE_HEAD.search(s):
                return s
            if best is None and len(s.split()) >= 2:
                best = s
    return best


def extract(nid, field, text, canon, abbrev, topic=None):
    claims = []
    for sent in sentences(plain(text)):
        low = sent.lower()
        per_sent = set()          # a compound sentence asserts a set, not a contradiction

        # The procedure this sentence names, if any. An Extra that names a DIFFERENT
        # procedure is cross-referencing, not contradicting.
        own_proc = None
        for m in SUBJ_RE.finditer(sent):
            cand = expand_abbrev(norm_subject(m.group(1)), abbrev)
            cand = canon.get(cand, cand)
            if len(cand.split()) >= 2 and PROCEDURE_HEAD.search(cand):
                own_proc = cand
                break

        drugs = [(m.start(), m.group(1).lower()) for m in DRUG_RE.finditer(sent)]

        def add(subject, attribute, value, unit="", span=None, kind="numeric", at=0,
                xref=None):
            head = attribute.split("[")[0]
            if ("dose" in attribute or head == "mass") and drugs:
                # A dose belongs to the DRUG. "below intubating dose" is not a thing
                # that has a dose, and keying to it hid a real succinylcholine conflict.
                subject = min(drugs, key=lambda d: abs(d[0] - at))[1]
            elif head in INSTRUMENT_ATTRS:
                # "an 8-10 cm Tuohy needle" and an Extra's bare "a 5 cm B-bevel needle
                # is used" both answer "what needle for this block". Keyed to the needle
                # phrase they never meet; keyed to the procedure they do.
                subject = own_proc or topic or subject
            elif subject is None:
                subject = topic
            if not subject or (attribute, subject) in per_sent:
                return
            per_sent.add((attribute, subject))
            claims.append(dict(nid=nid, field=field, subject=subject, attribute=attribute,
                               value=value, unit=unit, span=span, kind=kind, sent=sent,
                               xref=bool(CROSS_REF.search(sent)) if xref is None else xref))

        for m in UNIT_RE.finditer(sent):
            value, unit = m.group(1), m.group(2)
            fam = UNIT_FAMILY.get(unit.lower().rstrip("."), unit.lower())
            attr = attr_for(sent, m.start()) or {
                "volume": "volume", "length": "length", "gauge": "gauge",
                "angle": "angle", "percent": "percent"}.get(fam, fam)
            add(subject_at(sent, canon, abbrev, m.start()), f"{attr}[{fam}]",
                norm_range(value), unit, mm_span(value, unit), at=m.start(),
                xref=bool(CROSS_REF.search(sent)))

        if len(LEVEL_RE.findall(sent)) >= 3:
            level_iter = []           # "T7-T11 = true intercostal, T7-T12 = TAP, ..."
        else:
            level_iter = list(LEVEL_RE.finditer(sent))
        for m in level_iter:
            g = [x for x in m.groups() if x]
            lvl = "-".join(g).upper()
            subj = subject_at(sent, canon, abbrev, m.start())
            # "the L4 transverse process" and "the C7 spinous process" IDENTIFY the
            # landmark; they do not make competing claims about one landmark. Folding
            # the level into the subject is what keeps those two apart. Without it the
            # deck appears to say the transverse process is at both L4 and T5.
            if subj and re.search(re.escape(lvl.split("-")[0]) + r"[\s-]+\w*\s*" +
                                  re.escape(subj.split()[-1]), sent, re.I):
                per_sent.discard(("spinal-level", subj))
                add(f"{lvl.lower()} {subj}", "identity", lvl)
                continue
            add(subj, "spinal-level", lvl)

        for m in COUNT_RE.finditer(sent):
            n = m.group(1).lower()
            add(subject_at(sent, canon, abbrev, m.start()),
                f"count[{m.group(2).lower().rstrip('s')}]", str(WORD_NUM.get(n, n)))

        for fam, members in FAMILY_SEEDS.items():
            found = sorted({t for t in members
                            if re.search(r"\b" + re.escape(t) + r"\b", low)})
            if len(found) != 1:                     # a set is a description, not a claim
                continue
            if fam == "needle-direction" and not PROCEDURAL_VERB.search(sent):
                continue
            if fam == "needle-type" and not re.search(r"needle|catheter", low):
                continue
            if fam in ("body-position", "limb-position") and not re.search(
                    r"position\w*|patient is|placed|lying|lies|arm\b", low):
                continue
            add(subject_at(sent, canon, abbrev), fam, found[0], kind="categorical")
    return claims


# ─────────────────────────────── reporting ───────────────────────────────

def overlaps(a, b):
    return bool(a) and bool(b) and not (a[1] < b[0] or b[1] < a[0])


def show(s, n=100):
    return s if len(s) <= n else s[:n - 1] + "…"


def collide(claims, kind):
    """Groups sharing subject+attribute but holding >1 distinct value."""
    groups = defaultdict(list)
    for c in claims:
        if c["kind"] == kind:
            groups[(c["subject"], c["attribute"])].append(c)
    out = []
    for key, g in sorted(groups.items()):
        if len({c["value"] for c in g}) < 2:
            continue
        if len({(c["nid"], c["field"]) for c in g}) < 2:
            continue
        spans = [c["span"] for c in g if c["span"]]
        disjoint = any(not overlaps(a, b) for a, b in itertools.combinations(spans, 2))
        out.append((key, g, disjoint))
    return out


def print_group(key, g, disjoint, indent="  "):
    subj, attr = key
    print(f"\n{indent}■ {subj}  ·  {attr}" + ("   ‼ ranges do not overlap" if disjoint else ""))
    seen = set()
    for c in sorted(g, key=lambda x: (x["value"], x["nid"])):
        k = (c["value"], c["nid"], c["field"])
        if k in seen:
            continue
        seen.add(k)
        print(f"{indent}    {c['value']:<12} {c['unit']:<6} [{c['nid']}] {c['field']}: {show(c['sent'], 88)}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    deck = args[0]
    if not os.path.exists(deck):
        sys.exit(f"no such deck: {deck}")
    ALL = "--all" in sys.argv
    cap = 10_000 if ALL else 12

    jout = None
    if "--json" in sys.argv:
        i = sys.argv.index("--json")
        jout = sys.argv[i + 1] if len(sys.argv) > i + 1 else "claims.json"
    baseline = None
    if "--baseline" in sys.argv:
        i = sys.argv.index("--baseline")
        baseline = {tuple(k) for k in json.load(open(sys.argv[i + 1])).get("collision_keys", [])}

    con = load(deck)
    rows = notes_of(con)
    print("=" * 68)
    print(f"CONSISTENCY — {os.path.basename(deck)}")
    print(f"  {len(rows)} notes")

    canon, abbrev, _ = build_lexicon([plain(t) + " " + plain(e) for _, t, e, _, _ in rows])
    claims = []
    for nid, t, e, _tb, _tags in rows:
        topic = note_topic(t, canon, abbrev)
        claims += extract(nid, "Text", t, canon, abbrev, topic)
        claims += extract(nid, "Extra", e, canon, abbrev, topic)
    print(f"  {len(claims)} assertions indexed across "
          f"{len({c['subject'] for c in claims})} subjects "
          f"({len(abbrev)} abbreviations resolved)")

    fail = 0

    # ---------- G1a. measured collisions — THE GATE ----------
    #
    # Only measured quantities gate. A spinal level is not a single-valued property of
    # a nerve - the same nerve legitimately carries different levels in different roles,
    # and Extra fields cross-reference neighbouring cards' levels constantly as house
    # style. Gating on those produces a red build on a correct deck, and a gate that
    # cries wolf is a gate nobody reads.
    print("\n--- G1a. MEASURED COLLISIONS (Rule 10 — gating) ---")
    numeric = [x for x in collide(claims, "numeric")
               if "[" in x[0][1] and not x[0][1].startswith("count")]
    levels = [x for x in collide(claims, "numeric") if x not in numeric]
    new_only = [c for c in numeric if baseline is None or tuple(c[0]) not in baseline]
    print(f"  groups with >1 distinct value: {len(numeric)}"
          + (f"   ({len(new_only)} not in baseline)" if baseline is not None else ""))
    for key, g, dj in (new_only if baseline is not None else numeric)[:cap]:
        print_group(key, g, dj)
    shown = len(new_only if baseline is not None else numeric)
    if shown > cap:
        print(f"\n  … {shown - cap} more. Re-run with --all.")
    if new_only:
        print("\n  Resolve each exactly one of three ways (Rule 10): merge into one card,")
        print("  qualify BOTH stems so neither answers the other's question, or correct")
        print("  the wrong one under Rule 9. Leaving both is not an option.")
        fail += 1

    # ---------- G1b. levels and counts — soft ----------
    print("\n--- G1b. LEVEL / COUNT COLLISIONS (soft — read, do not assume) ---")
    print(f"  groups: {len(levels)}")
    for key, g, dj in levels[:cap]:
        print_group(key, g, dj)
    if len(levels) > cap and not ALL:
        print(f"\n  … {len(levels) - cap} more. Re-run with --all.")
    if levels:
        print("\n  Many are legitimate: one nerve carries different levels in different")
        print("  roles, and an Extra may cross-reference a neighbour. The ones that matter")
        print("  are two cards answering the SAME question - T1-T6 against T2-T6 for the")
        print("  chest intercostals was a real defect hiding in a list like this.")

    # ---------- G2. a card that contradicts its own Extra ----------
    print("\n--- G2. FRONT/BACK CONFLICT (Rule 10) ---")
    per_note = defaultdict(list)
    for c in claims:
        if c["kind"] == "numeric" and not c.get("xref"):
            per_note[(c["nid"], c["subject"], c["attribute"])].append(c)
    fb = []
    for (nid, subj, attr), g in sorted(per_note.items()):
        t = {c["value"] for c in g if c["field"] == "Text"}
        e = {c["value"] for c in g if c["field"] == "Extra"}
        if t and e and not (t & e):
            fb.append((nid, subj, attr, sorted(t), sorted(e), g))
    hard = [x for x in fb if "[" in x[2] and not x[2].startswith("count")]
    soft = [x for x in fb if x not in hard]
    print(f"  cards whose Extra states a different value than their own Text: "
          f"{len(fb)}   ({len(hard)} measured -> gating, {len(soft)} level/count -> soft)")
    for nid, subj, attr, t, e, g in (hard + soft)[:cap]:
        print(f"\n  ■ [{nid}] {subj} · {attr}")
        print(f"      Text : {', '.join(t)}")
        print(f"      Extra: {', '.join(e)}")
        for c in g[:2]:
            print(f"        {c['field']}: {show(c['sent'], 88)}")
    if hard:
        print("\n  A hard fail, not a style note. One of the two is wrong and the card has")
        print("  already told you they differ.")
        fail += 1
    if soft:
        print("\n  The level/count hits are soft: an Extra legitimately cross-references a")
        print("  neighbouring card's levels. Read them, do not assume.")

    # ---------- G3. categorical conflicts — soft ----------
    print("\n--- G3. CATEGORICAL CONFLICTS (soft — read, do not assume) ---")
    cat = collide(claims, "categorical")
    print(f"  groups: {len(cat)}   (needle type, needle direction, body/limb position)")
    for key, g, dj in cat[:cap]:
        print_group(key, g, dj)
    print("\n  Quincke vs Tuohy shows up here, not in G1. Some of these are legitimate"
          "\n  alternatives ('if the patient cannot sit, use lateral decubitus') — read them.")

    # ---------- H. Rule 9 worklist ----------
    print("\n--- H. RULE 9 WORKLIST — claims that must be looked up ---")
    buckets = defaultdict(set)
    for c in claims:
        head = c["attribute"].split("[")[0]
        if head in NUMERIC_ATTRS or "dose" in c["attribute"]:
            buckets[head].add((c["subject"],
                               c["value"] + (" " + c["unit"] if c["unit"] else ""), c["nid"]))
    print(f"  {sum(len(v) for v in buckets.values())} checkable claims across "
          f"{len(buckets)} classes. Every one gets an independent source.")
    for head in sorted(buckets, key=lambda h: -len(buckets[h])):
        items = sorted(buckets[head])
        print(f"\n  {head}  ({len(items)})")
        for subj, val, nid in items[:(6 if not ALL else 10_000)]:
            print(f"      [{nid}] {subj}: {val}")
        if not ALL and len(items) > 6:
            print(f"      … {len(items) - 6} more")

    # ---------- I. Rule 13 ----------
    print("\n--- I. RULE 13 — direction / depth / dose cards ---")
    proc, dose_no_drug = [], []
    for nid, t, e, _tb, _tags in rows:
        pt = plain(t)
        if DIRECTION_TOKENS.search(pt) and PROCEDURAL_VERB.search(pt):
            proc.append((nid, pt))
        both = pt + " " + plain(e)
        if DOSE_UNIT_RE.search(both) and not DRUG_RE.search(both):
            dose_no_drug.append((nid, pt))
    print(f"  cards stating a needle/probe direction or manoeuvre: {len(proc)}")
    for nid, pt in proc[:(8 if not ALL else 10_000)]:
        print(f"      [{nid}] {show(pt, 96)}")
    if len(proc) > 8 and not ALL:
        print(f"      … {len(proc) - 8} more")
    print("      Verify each against a technique description, not neighbouring prose.")

    print(f"\n  weight-based doses with NO drug named: {len(dose_no_drug)}")
    for nid, pt in dose_no_drug[:cap]:
        print(f"      [{nid}] {show(pt, 96)}")
    if dose_no_drug:
        print("      '0.4 mg/kg of local anesthetic' is not a dose — bupivacaine,")
        print("      ropivacaine and lidocaine differ by an order of magnitude. Name the")
        print("      drug and concentration (Rule 13).")
        fail += 1

    # ---------- J. Rule 12 ----------
    print("\n--- J. RULE 12 — certainty language on a measurement ---")
    hype = []
    for nid, t, e, _tb, _tags in rows:
        for fname, s in (("Text", plain(t)), ("Extra", plain(e))):
            for m in HYPE.finditer(s):
                if re.search(r"\d|\b[TLCS]\d", s[m.end():m.end() + 60]):
                    hype.append((nid, fname, show(s[max(0, m.start() - 28):m.end() + 52], 92)))
    print(f"  hits: {len(hype)}")
    for nid, fname, snip in hype[:cap]:
        print(f"      [{nid}] {fname}: …{snip}…")
    if len(hype) > cap and not ALL:
        print(f"      … {len(hype) - cap} more. Re-run with --all.")

    # ---------- K. Rule 14 ----------
    print("\n--- K. RULE 14 — provenance ---")
    no_tb = sum(1 for _, _, _, tb, _ in rows if not tb.strip())
    no_tag = sum(1 for _, _, _, _, tg in rows if not tg)
    print(f"  Textbook empty : {no_tb}/{len(rows)}")
    print(f"  untagged       : {no_tag}/{len(rows)}")
    if no_tb or no_tag:
        print("  Cheap now, expensive to retrofit across a finished corpus.")

    if jout:
        json.dump({"deck": os.path.basename(deck), "notes": len(rows),
                   "claims": [{k: v for k, v in c.items() if k != "span"} for c in claims],
                   "collision_keys": [list(k) for k, _, _ in numeric]},
                  open(jout, "w"), indent=1)
        print(f"\n  claim index written: {jout}")

    print("\n" + "=" * 68)
    print("CONSISTENCY RESULT:", "PASS" if fail == 0 else f"FAIL ({fail} gating categories)")
    if fail == 0:
        print("""
No numeric self-contradiction, and no dose without a drug. That is NOT a clinical
pass — this tool has no idea whether the deck's numbers are RIGHT, only that it gives
one answer per question. G3 and sections H and I are still yours to work through.
""")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
