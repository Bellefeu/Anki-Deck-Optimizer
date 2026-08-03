# ANKI DECK OPTIMIZATION — PROJECT HANDOFF

**Read this file completely before doing anything else.**

You are continuing a project that optimizes a student's Anki decks (thousands of cards
total, dozens of modules) and fills gaps against corresponding the corresponding course modules. Each
session is a fresh start with no memory of prior runs. This document is the memory.

---

## CRITICAL PATH — read this first, then the rest

If you read nothing else, read these five:

1. **Section 0** — do the visual read FIRST, before any other work.
2. **Section 3b** — the editorial rubric. This is the actual job. Nine rules with
   an operational test each. Passes 1 and 2 mean applying all of them to every card.
   **Rule 8 is absolute: a card never names the source, by any name.**
3. **Section 5** — two validation bugs that shipped once already. Do not repeat them.
4. **Section 2** — a new note without a matching `cards` row imports as nothing,
   silently.
5. **Section 4c** — a module is not done when it builds, only when it verifies.

Run `python3 selftest.py` before the first module in any new environment. It
exercises every operation on synthetic data in about ten seconds and will tell you
the toolchain is broken before you waste a run finding out.

---

## 0. FIRST ACTION OF EVERY SESSION — DO NOT SKIP

**Read the source capture visually, page by page, in order, starting at page 1, BEFORE any
other work.**

Image rendering has degraded mid-session before: pages rendered correctly early on, then
returned blank for the rest of the session and never recovered — including re-reads of
files that had worked minutes earlier. Text tooling (bash, sqlite, OCR, file writes)
never degraded.

Therefore: **spend the visual budget first.** Do not read the rulebook, inspect the
`.apkg`, or run OCR before the visual read is done. Those are all safe to defer; the
visual read is not.

If rendering fails partway, note the exact page number, report it, and fall back to the
OCR path (Section 6). A module can be finished across two scheduled runs if needed —
each Cowork run is a fresh session, which resets whatever the limit is.

---

## 0b. ENVIRONMENT — SQLITE CANNOT RUN ON THE DRIVE MOUNT

**Any SQLite write on the Google Drive FUSE mount throws `disk I/O error`.** Not a
permissions problem, not a script bug - the mount does not support the file locking
SQLite requires. Every script here touches SQLite.

**Always:** copy scripts and inputs to local disk, run everything there, copy only the
finished deliverables back to Drive. `selftest.py` will fail ~7 checks if run from the
mount and pass 29/29 from local disk; that is the diagnostic.

Two related mount behaviours:
- **Cloud-only files are invisible to bash.** A folder can look empty while Drive shows
  files in it. Reading a file with the file tools forces materialization; after that
  bash sees it. Mark folders "available offline" to avoid this.
- **Long-running processes get killed by the sandbox.** `extract_source.py` checkpoints
  after every page for exactly this reason - if it dies, re-run it and it resumes.

---

## 0c. VERSION DRIFT — CHECK BEFORE DIAGNOSING ANY BUG

```bash
python3 check_version.py
```

Scripts exist in two places - the handoff folder in Drive, and whatever local copy a
session made - and they drift. **A session running a stale script will diagnose a bug that
has already been fixed and hand-patch around it.** That has happened: a run reported
`regenerate()` not recomputing totals, twice, from a copy predating the fix.

`VERSION.json` holds a sha256 of every tracked file. The check reports which files differ
from the manifest. If any do, re-copy from `scripts/` before concluding
anything about the code. `selftest.py` runs this check first.

After deliberately editing a script, regenerate the manifest:

```bash
python3 check_version.py --write
```

**Report the pipeline version in every run summary.** It is the fastest way to tell whether
a reported defect is real or a stale copy.

---

## 0d. A CLOUD MOUNT LIES ABOUT EMPTINESS

Google Drive serves files on demand. **A folder that reads as empty may simply not be
materialized.** `find` returning nothing means "not hydrated right now", not "not there".

This has caused two wrong conclusions in real runs: a queue reported as 0 when the source
folder actually held 42 MB, and a scratch directory that looked like data loss but was
merely dehydrated. In the second case the run correctly refused to sweep a directory it
could not see - that was the right instinct.

Rules:

- **Never treat an empty read on a mount as fact.** Open the folder in Finder/Explorer to
  force hydration, or mark it "available offline", then re-read.
- `build_queue.py` and `cleanup.py` warn when a target folder reads as empty and the path
  looks like a cloud mount.
- **Never delete based on an empty read.** If contents cannot be seen, stop and say so.
- Reading a file with the file tools forces materialization; after that bash can see it.

---

## 1. DELIVERABLES (per module)

All three go in `/mnt/user-data/outputs/`:

| File | Contents |
|---|---|
| `<Deck> (FINAL).txt` | One card per line, `Text\|Extra`, matches the user's import flow |
| `<Deck> (FINAL).apkg` | Reimportable Anki package, all edits + new cards applied |
| `<Deck> (NOTES).docx` | Manual action items: IO card requests with cropped reference figures, changelog, OCR-verification list |

Reference implementations are attached: `build_deck.py` and `build_notes.js`. They work.
Change the deck ID and paths per module; don't rewrite them from scratch.

---

## 1b. QUEUE-DRIVEN WORKFLOW (current design)

Modules are processed one per session, off a queue. Nothing is hardcoded per module.

**Step 0 — verify the toolchain** (first module in any new environment only):
```bash
python3 selftest.py
```
Builds a synthetic deck, exercises all five operations plus creation mode, and checks
the result. 29 assertions, ~10 seconds. If it fails, fix that before touching real data.

**Step 1 — build the queue** (only when new files have been added):
```bash
python3 build_queue.py "<Source Files folder>" "<Anki Decks folder>"
```
Pairs PDFs to decks by normalized filename (tolerates spaces/underscores/hyphens/case),
reads each deck's real `deck_id` straight out of the `.apkg`, and writes the result to
`pending_modules` in `project_state.json`. This touches the `.apkg` but costs no visual
budget, so it is safe to run before the visual read.

**A deck with no matching source capture is queued anyway, in `mode: "optimize-only"`.** Pass 3
(gap-fill) is skipped; passes 1, 2 and 4 run in full. This matters because many decks are
lecture-derived with no the source counterpart, and those tend to be the OLDEST and worst decks -
exactly the ones most needing restructuring. Never skip a deck just because it has no
module. A PDF with no deck is still reported as an error.

**Step 2 — the session does the thinking** and writes three files to `work/<module>/`:

| File | Shape |
|---|---|
| `new_cards.json` | `[{"text": "...", "extra": "..."}, ...]` |
| `ops.json` | Restructuring on existing notes - see below. (`fixes.json` still read as legacy "edit" ops.) |
| `meta.json` | `{"outstanding": [...], "gaps_filled": [...], "summary": "..."}` |

**Patching an already-built deck.** A verification report is applied without a rebuild:

```bash
COMPLETED_DIR="<completed>" python3 build_deck.py --patch "<Module>"
```

It reads `work/<module>/patch_ops.json` (and optional `patch_cards.json`), applies them to
the deck in `COMPLETED/`, re-runs every gate, and rewrites `.txt` and `.apkg` in place. The
queue is untouched. This is how verification findings get fixed - never by hand.

**Two gates worth knowing about, both added after real failures:**

- **Demote loss gate.** A demotion whose carried payload does not contain every distinctive
  term from the retiring card is REFUSED and logged. A real run demoted a card carrying one
  sentence and silently dropped Boerhaave syndrome, mediastinitis, and an aspiration-risk
  point. Use `"facts": [...]` (a list) and carry everything.
- **Accounting gate.** The changelog must fully explain the deck's card-count delta. If
  anything changed the database outside the ops path, the counts will not reconcile and the
  build fails. Never edit the database directly.
- **Totals are computed in one place.** `recompute_totals()` in `update_handoff.py` is
  called by both `regenerate()` and `record_run()`. An earlier version had `record_run`
  compute totals while `regenerate()` did not, so `--pass` left `totals.modules_verified`
  stale while `modules[].status` said `verified` - the state file disagreeing with itself.
  Any number derivable from `modules` must be derived in exactly one function.
- **Patch counts accumulate.** A patch preserves the module's original `cards_before` and
  adds to `added`/`edited` rather than recomputing them. An earlier version derived
  `before` as `n_notes - added`, which on a patch (where `added` is usually 0) silently
  overwrote the original count with the current one.

**The NOTES changelog renders every operation type**, content-changing ones first
(`DEMOTE`, `DELETE`, `SPLIT`, `REWRITE`, then `EDIT`, `ADD`, skips). An earlier version
rendered only `EDIT` and `ADD`, so splits, demotions and rewrites - precisely the
operations that alter or remove content - were invisible in the audit document.

**Step 3 — build:**
```bash
COMPLETED_DIR="<Drive COMPLETED folder>" python3 build_deck.py
```
Takes the next pending module (or a named one), auto-creates `COMPLETED/<Module>/`,
applies fixes, inserts cards, runs every validation gate, exports `.txt` + `.apkg`,
round-trip verifies, pops the queue, and regenerates this handoff. One command.

**Step 4 — the NOTES doc.** `build_notes.js` is config-driven; do NOT hand-edit it.
Write `work/<module>/notes_config.json`:

```json
{ "source_label": "Truncal source", "source_pages": 30, "ocr_used": true,
  "cards_before": 150, "cards_after": 228,
  "verify_items": [["Nerve root ranges", "C5-C7 ...", "why this is OCR-risky"]],
  "io": [{ "id":"IO-01", "file":"IO-01_x.png", "page":12, "priority":"HIGH",
           "topic":"...", "tests":"...", "whyImage":"...", "occlude":"...",
           "labels":"..." }] }
```

Then:
```bash
MODULE="<module>" COMPLETED_DIR="<completed>" node build_notes.js
```
Crops go in `work/<module>/io_figs/`. When `ocr_used` is `false` the verification
section is replaced by a note saying it is unnecessary. Section 4 of the doc is
generated from `meta.json`'s `outstanding` list automatically.

**All nine scripts must be copied together.** `update_handoff.py` is imported by
`build_deck.py` and `verify_deck.py` - if it is missing, both crash on import even
though no prompt invokes it directly.

---

## 1c. READING THE SOURCE MODULE — RUN THE EXTRACTOR

```bash
python3 extract_source.py "<module.pdf OR module_folder>" "work/<module>/source"
```

**Accepts a folder of PDFs.** If a module was captured as several GoFullPage
exports (one per sub-module), put them in a folder named for the module and point
the extractor at it. **Never merge them with an online tool** - cloud merging
re-encodes the file, destroys any text layer, and uploads copyrighted material to
a third party. This was measured, not assumed: an iLovePDF-merged capture had a
zero-word text layer, exactly like its un-merged inputs. Merging rasters produces
rasters, and a PDF-to-Word conversion on top just substitutes someone else's OCR
for tesseract's - same digit risk, worse settings, no per-page provenance. If merging is genuinely needed, use local `pdfunite a.pdf b.pdf
out.pdf`.

Auto-detects three shapes: real text layer (no OCR, numbers exact), image-only
normal pages (rasterize + OCR), image-only single tall page (slice into 1600px
strips with 120px overlap, then OCR).

**Resumable.** Checkpoints after every page into `manifest.json`. Cowork kills
long-running shell processes - just re-run and it skips completed work.

**Completeness gate.** Parses "Page N of M" footers and reports missing pages. A
partial capture silently caps how good the gap-fill can be, so check
`course_pages_missing` in `extract_report.json` before writing any cards.

### The coverage gate — what STEP 3 actually reads now

GoFullPage output is 100% raster, so OCR always runs and the prose is always
available as exact text. Re-sending those same prose pixels to be *looked at* was
the single largest cost in the pipeline and bought nothing: reading a paragraph
as an image costs roughly eight times reading it as text, and returns a less
reliable transcription than the OCR already sitting in `content_ocr.txt`.

So the extractor now proves, per page, that every informative ink pixel is in
exactly one of three buckets:

| | |
|---|---|
| **A** | inside a high-confidence OCR word box → the text is already held exactly |
| **B** | everything else → goes on the visual read list |
| **C** | typographic furniture: hairline rules ≤6px tall spanning ≥40% of the width, and antialias fringe bands with no row over 30px |

Bucket B is figures, diagrams, icons, collapsed-accordion controls, every word
OCR was unsure about, and every token holding a digit, unit or roman numeral.
It ships as **one composed image per page**: full page width, the retained
y-bands stacked in reading order, a grey rule marking where prose was elided.
**Same dpi as the OCR raster — no resolution is traded away anywhere.**

`unaccounted_ink_px` is reported per page and **must be 0**. Every failure branch
promotes the page to a whole-page read: no OCR boxes, analysis exception, bucket
C over its cap, too many bands, or a sheet that costs as much as the page. The
gate can only ever cause *more* reading than needed, never less. Set
`COVERAGE=page` to disable it entirely and restore the old behaviour.

Measured on a real 23-page capture: 62,422 → 23,506 visual tokens for the
orientation read, `unaccounted_ink_px: 0`.

Two artifacts come out of it, and they are read at different times:

- `read_targets` — **STEP 3, mandatory.** Figures and anything OCR could not
  transcribe confidently.
- `verify_targets` — **pass 1 / Rule 9, when you build `verify_items`.** The
  lines carrying numbers, so the transcription can be checked against the pixels.
  Deferred, not dropped: the coverage proof is computed against the union of
  both, so skipping it defers a *known, listed* set, never an unknown gap.

Check `ocr_used` in the report: `false` means numbers are exact and the NOTES
verification section can be dropped; `true` means every figure goes in it. With
GoFullPage capture it is always `true`.

**Dependencies bootstrap themselves** via `from deps import require`. Run
`python3 deps.py` to check the whole toolchain.

---

## 1d. DECK OPTIMIZATION — THIS IS HALF THE JOB, NOT AN AFTERTHOUGHT

source gap-fill is only one of the four passes. On older decks the dominant work is
**restructuring what is already there**. Measured across the reference decks:

| Deck | Cards | Need splitting |
|---|---|---|
| Intro (Claudify-era) | 227 | 1% |
| Abx | 149 | **66%** |
| Coags | 119 | **78%** |
| Diuretics | 104 | **86%** |

A deck from before ~March is mostly multi-fact laundry-list clozes. Expect to
restructure the majority of it. If a run on an old deck reports only a handful of
ops, that is a red flag that passes 1 and 2 were done superficially.

`ops.json` supports four operations on existing notes:

```json
[
 {"nid":123, "op":"split",   "why":"three facts in one card",
  "into":[{"text":"...","extra":"..."}, {"text":"...","extra":"..."}]},
 {"nid":124, "op":"rewrite", "why":"cloze was on a low-yield term",
  "text":"...", "extra":"..."},
 {"nid":125, "op":"edit",    "field":1, "find":"...", "replace":"...", "why":"..."},
 {"nid":126, "op":"demote",  "into_nid":124, "fact":"Extra-field text to preserve",
  "why":"Rule 0: real but not board-testable"},
 {"nid":127, "op":"delete",  "why":"duplicate of 124"}
]
```

- **split** retires the original and creates N atomic replacements. This is the
  workhorse for old decks.
- **rewrite** replaces both fields - use when phrasing is bad or the cloze sits on
  the wrong term.
- **edit** is surgical find/replace. Remember the target may be HTML-wrapped.
- **demote** is the Rule 0 operation: retires a low-yield card and folds its fact into
  another card's `Extra`, so nothing is lost but the review cost goes to zero. Logged
  distinctly in the changelog so the user can review and reverse the judgment call.
- **delete** removes a note and its card. Use only for true duplicates or trivia, and
  always give a `why` - deletions are the one irreversible operation.

The build recomputes `sfld` and `csum` on **every** write and fails the build on
drift. An earlier version updated `flds` without them, silently corrupting sort
fields and duplicate hashes on edited notes.

`Extra` is normalized to a single leading space on every write, matching the
original house convention.

---

## 2. `.apkg` TECHNICAL SPEC (verified against the real file — trust this)

An `.apkg` is a ZIP containing:
- `collection.anki21b` — zstd-compressed SQLite (the real data)
- `collection.anki2` — legacy stub for old clients; pass through untouched
- `media` — zstd-compressed `{}` when there is no media
- `meta` — 2 bytes: `[8, 3]` (protobuf, format v3)

**Required steps:**
- `pip install zstandard --break-system-packages`
- SQLite will throw `no such collation sequence: unicase` — register it before any query:
  ```python
  con.create_collation("unicase", lambda a,b:(a.lower()>b.lower())-(a.lower()<b.lower()))
  ```
- `sqlite3` CLI is NOT installed. Use Python's `sqlite3` module.
- `xxd` is not installed either; use `od`.

**Note type:** `ZzzAnki Master Cloze`, id `1766255887245`. Seven fields in this order:
`Text`, `Extra`, `Personal Notes`, `ZzzAnki`, `Missed Questions`, `Textbook`,
`Additional Resources`. **Only `Text` and `Extra` are ever populated.** Leave the other
five empty — do not invent uses for them.

Fields are joined by `\x1f` inside the `flds` column. Deck names also use `\x1f` as the
path separator: `ZzzAnki\x1fYear 2\x1fPrinciples of Anesthesia II\x1f<Deck Name>`.

**Find a deck's ID** with `select id, name from decks`. `build_queue.py` records it
per module in `project_state.json`, so you rarely need to look it up by hand.

**Inserting a new note requires ALL of:**
1. Unique `id` (ms timestamp, must exceed current max)
2. Unique `guid` (10 random chars)
3. Correct `csum` — `int(sha1(html_stripped_first_field)[:8], 16)`
4. `sfld` — HTML-stripped first field
5. **A matching row in `cards`** (`ord` 0 for a single `c1`)

Miss #5 and the note **imports as nothing, silently.** Always verify:
```sql
select count(*) from notes n left join cards c on c.nid=n.id where c.id is null;  -- must be 0
```

**Scheduling history: see `PROFILE.md`.** The reference build discarded it deliberately —
the collection was being reset — and the scripts assume that default. If your `PROFILE.md`
says to preserve it, say so up front: it changes how a rebuilt deck must be imported, and
it is far cheaper to decide before the first build than after.

---

## 3. HOUSE FORMAT (derived from the reference decks this pipeline was built against)

**The split:** everything before the `|` is `Text`, everything after is `Extra`. Verified
with zero exceptions across all four gold decks and the real `.apkg`.

**Rules:**
- Exactly one `{{c1::...}}` cloze per card, on the highest-yield term. Never `c2`/`c3`.
- Hard max 3 clozes; 1 is the strong default.
- No semicolons in `Text`.
- `Text` ends in terminal punctuation.
- `Extra` pattern: `[optional plain lead-in sentence]<br><br><b>mechanism explanation</b><br><br>high-yield fact`
- Bold with `<b>`, emphasis with `<u><b>...</b></u>`. Keep tags balanced.
- Expand acronyms on first use: `Local Anesthetic Systemic Toxicity (LAST)`.

**Typical dimensions:** `Text` ≈ 140–156 chars, `Extra` ≈ 500–660 chars.

**When the gold decks conflict, the newest one wins.** The user's 15-step pipeline permits
list-style clozes (`{{c1::A}}, {{c1::B}}, {{c1::C}}`), but step 12 ("Claudify," adopted
~1 month ago) forbids them. Measured adherence:

| Deck | Cards | Multi-cloze | c2/c3 | >3 clozes |
|---|---|---|---|---|
| **Intro (Claudify)** | 227 | **1%** | 0 | 0 |
| Diuretics | 104 | 86% | 5 | 10 |
| Abx | 149 | 66% | 7 | 1 |
| Coags | 119 | 78% | 1 | 17 |

**Calibrate to Intro.** Split laundry-list clozes into atomic single-fact cards. Exception:
a card testing two halves of one genuine paired contrast (e.g. pec major vs. pec minor)
may stay together.

---

## 3b. EDITORIAL RUBRIC — HOW TO ACTUALLY REWRITE A CARD

This is the judgment work. It is not scriptable and it is where most of the value
is. Passes 1 and 2 mean applying every rule below to every card, one at a time.

These rules are the user's own, distilled from their 15-step pipeline and then
tightened with them. Where an early step conflicts with a later one, the tests
below settle it - do not fall back on "whichever prompt ran last."

---

### RULE 0 — THE YIELD FILTER (apply first, to every card)

The binding constraint on a massive card collection is **daily review minutes**, not
completeness. Every low-yield card taxes every future study day and steals reviews
from high-yield ones.

**Test: would this appear on a boards-style question, or change a clinical decision?**

- **Yes** -> it earns a card.
- **No, but it is real and worth knowing** -> **demote it into the `Extra` field** of
  the card it belongs to. Nothing is lost. It is seen on every review of the parent
  concept and costs zero additional reviews.
- **No, and it is trivia** (a researcher's name, an incidental aside) -> drop it, and
  log the drop in the changelog so the user can object.

This supersedes the original "when in doubt, create a card" instinct. That rule
existed to defend against silent data loss from a weaker tool; the hostile-audit pass
and the scripted no-loss diff now do that job mechanically. Demotion preserves the
zero-loss guarantee without paying the review cost.

---

### RULE 1 — THE ATOMICITY TEST (replaces "one fact per card")

Do not reason from the principle. Apply the test:

> **If I fail this card, does that mean I do not know the concept - or that I blanked
> on one of several independent items?**

- Failing means you don't know the concept -> **good card, leave it.**
- Failing means you blanked one item of several -> **split it.**

This resolves the atomize/consolidate oscillation in the original pipeline (steps
1-4 atomize, 5 consolidates, 12 atomizes, 13 consolidates). Apply the test **per
card**. Consolidate only when several cards test attributes of **one parent concept**
and failing any one of them genuinely means not knowing that concept.

---

### RULE 2 — CLOZE TARGETING (two-sided test)

Both must hold:

1. **Not inferable.** The answer cannot be deducible from the rest of the sentence,
   or you are testing grammar rather than recall.
2. **Determinate.** Only one answer can correctly fill the blank, or it is a guessing
   game.

The **topic** must be obvious; the **answer** must not. That is the distinction the
original "Question Clarity" and "don't make it obvious" rules were groping at.

Hide only the high-yield target - the specific nerve, exact root, distinct muscle,
precise number. Never hide connector words or generic nouns.

---

### RULE 3 — CLOZE MECHANICS (non-negotiable)

- **The cloze answer is 1-2 words.** Ideally one. `The Therapeutic Index is {{c1::4}}.`
- **No punctuation inside the cloze.**
- **Units stay OUTSIDE.** `{{c1::20}} mL`, never `{{c1::20 mL}}`.
- **Abbreviations go INSIDE, fully expanded, on every card that uses them.**
  `{{c1::Local Anesthetic Systemic Toxicity (LAST)}}` correct.
  `{{c1::Local Anesthetic Systemic Toxicity}} (LAST)` wrong.
  Never write bare `LAST` because an earlier card defined it.
- **Max 3 clozes, hard.** With 7 list items, keep **all 7 in the text** and bracket
  only the 3 highest-yield. Never drop items to satisfy the cap.
- **Never a semicolon. Never more than one `|`. Never a cloze after the `|`.**

---

### RULE 4 — CLINICAL PEARL PHRASING

Cards read like natural clinical pearls with enough context that the target is
obvious. No chopped syntax, no heavy parentheticals, never a guessing game.

Forbidden: bare list clozes such as
`Complications include {{c1::A}}, {{c1::B}}, and {{c1::C}}.`
Isolate items by their unique mechanism, anatomy, or clinical presentation.

Multiple clozes on one card are allowed **only** when the items share one unifying
mechanism or anatomic frame, and never more than 3:

- BAD:  `The femoral nerve innervates the {{c1::pectineus}} and {{c1::iliopsoas}}.`
- GOOD: `Within the distribution of the femoral nerve, the {{c1::pectineus}} and
  {{c1::iliopsoas}} muscles receive motor innervation.`

If the items carry different clinical relevance, split instead.

---

### RULE 5 — INTERFERENCE (new; nothing in the original pipeline covered this)

Structurally parallel cards that differ only in a value teach **pattern recognition
instead of the fact** - you learn the sentence shape and guess the number.

When two cards are near-identical except for a value, add a **discriminating cue** to
each so the stem itself is distinguishable. This matters most in pharmacology and in
block volumes, where a dozen cards share one frame.

- WEAK:  `The volume for a TAP block is {{c1::20}} mL.` / `The volume for a rectus
  sheath block is {{c1::10}} mL.`
- BETTER: anchor each to its distinguishing feature - the TAP's large fascial plane
  requiring bilateral coverage, versus the rectus sheath's small compartment per side.

---

### RULE 6 — THE `Extra` FIELD (undocumented in the original pipeline)

Measured across the gold-standard Intro deck: **227/227 cards have a back**, all use
`<b>`, 199/227 use `<u><b>`, and every one is 2-3 segments split by `<br><br>`.

The house structure:

1. *(optional)* a short plain lead-in - a class aside or extra detail
2. **Mechanism explanation**, with `<b>` on key terms. Explains *why*, not just *what*.
3. **A high-yield closer**, opening with a framing phrase and using `<u><b>` on the
   single most important item.

Observed closers, in frequency order: "A highly relevant clinical point is that...",
"A high yield clinical consideration is that...", "A critical high yield...",
"A high yield fact is that...", "An interesting high yield...".

`Extra` begins with a single leading space. It is where demoted low-yield facts go
under Rule 0 - which makes it a teaching surface, not a footnote.

---

### RULE 7 — SOURCE FIDELITY vs MEDICAL ACCURACY

The original pipeline said "never use the internet, ever." That was written to stop a
weaker model inventing content, but taken literally it means a wrong card the module
does not happen to cover stays wrong. The resolution:

- **The source module is authoritative for what to INCLUDE.** Do not import outside
  topics; scope comes from the module and the user's lectures.
- **Established medical fact is authoritative for whether a card is CORRECT.** Fix
  outright errors even when the module is silent.
- **Where the deck contradicts ITSELF, that is a hard error.** Find both cards and
  reconcile them.
- **Where the deck contradicts the module**, correct toward the module - unless the module
  is itself wrong. **The source can be wrong.** A verification run caught the module calling
  a structure the "tensor fascia latae" (a thigh muscle) where it means the thoracolumbar
  fascia; the build propagated it verbatim. Correct it, and record the discrepancy in the
  NOTES `verify_items` so the user sees the source erred.
- **Where it is a genuine inter-source disagreement** rather than an error, do NOT
  silently flip it. Surface it in the NOTES doc for the user to decide. (Example: the source
  positions the arm at the side for PECS; Blanco's original description abducts.)

---

### RULE 8 — NEVER NAME THE SOURCE INSIDE A CARD (non-negotiable)

**A card states the fact. It never says who asserted it.** The word "the source" — and every
paraphrase of it — must not appear anywhere in a card: not in `Text`, not in `Extra`, not
in any other field, not in a new card, not in a rewrite, not in a patch. This applies in
creation mode too, where there is no original deck to blame.

- BAD:  `According to <SOURCE>, the thoracolumbar fascia has {{c1::three}} layers.`
- GOOD: `The thoracolumbar fascia has {{c1::three}} layers.`

- BAD:  `<SOURCE> ranks carbon monoxide production as {{c1::desflurane}} greater than isoflurane and far greater than sevoflurane.`
- GOOD: `Carbon monoxide production is greatest with {{c1::desflurane}}, intermediate with isoflurane, and negligible with sevoflurane.`

- BAD (in `Extra`): ` the source emphasizes that <b>desiccated absorbent</b> is the prerequisite.`
- GOOD (in `Extra`): ` <b>Desiccated absorbent</b> is the prerequisite.`

The rewrite is almost always the sentence with the attribution clause deleted. **Strip the
attribution, keep the fact** — this rule never justifies dropping content, and a card that
loses its meaning without the citation was never a fact card to begin with.

**The paraphrases are the same violation.** "Per the source", "the source module", "as taught in
the source", "the module states", "this course teaches", "the lecture emphasizes", "the textbook
lists", "the source describes" — all banned. Do not launder the attribution into a generic
noun.

**One exception, and it is not a card: the NOTES doc.** Rule 7's inter-source disagreements
and source errors are recorded there by name, in `verify_items` — "the source positions the arm at
the side for PECS; Blanco's original description abducts." That is the correct and only home
for attribution. If a fact is genuinely contested, the card teaches it unattributed and the
NOTES doc carries the disagreement.

Why it matters: on review the job is to recall the fact, not its author. Attribution adds
words to read on every repetition, implies a settled fact is contested, makes the card read
as hearsay rather than knowledge, and dates the collection to a source that may be replaced.
---

### RULE 9 — VERIFY THE CHECKABLE CLAIM (Rule 7 has teeth only if this happens)

Rule 7 says established medical fact outranks the source. It does not say how you would
*know* the source is wrong. "Read as a clinician" catches prose that sounds wrong. It does
not catch a number that is quietly off by a factor of two, and it never has.

**Six classes of claim are CHECKABLE. Every one gets looked up before it ships — every
one, not a sample.** These are cheap: one lookup each, and they are exactly the claims a
board item is written from.

| Class | Truncal deck shipped | Actually |
|---|---|---|
| Distance or depth in cm/mm | rib angle 3–6 cm from midline | 6–8 cm at the lower ribs; medial to the angle is contraindicated |
| Dose or volume with a unit | SAP ceiling 0.4 **mg**/kg | 0.4 **mL**/kg — a unit class error |
| Spinal or nerve-root level | chest intercostals T1–T6 | T2–T6 |
| Named structure on a needle path | costotransverse ligament, US intercostal block | not on that path at all — it belongs to the paravertebral block |
| Direction, order, laterality | aim **lateral** to miss the inferior epigastric artery | lateral is where the artery is in ~49% of patients |
| Prevalence, percentage, "n levels" | single thoracic ESP covers 8–11 dermatomes | ~3–4 cranial and caudal, and the literature calls spread unpredictable |

All six shipped in a deck that had passed a build pass. None required specialist knowledge
to catch. Each was one search away.

**The standard.** A checkable claim needs agreement between the module and one independent
authority (NYSORA, StatPearls, BJA Ed, ASRA, Nagelhout, Barash, Miller, or a primary paper).

- **They agree** → ship it, and put the citation in `Textbook` (Rule 14).
- **They disagree and the module is wrong** → Rule 7: fix the card, record the discrepancy
  in `verify_items`.
- **They disagree and it is a genuine dispute** → teach the mainstream figure, record the
  disagreement in `verify_items`. Never split the difference and never average two numbers.
- **You cannot confirm it** → the card ships with the claim marked in `verify_items` as
  unconfirmed. Silence is not confirmation.

**A number you did not check is a number you asserted.** There is no third state where a
card is neither verified nor your responsibility.

---

### RULE 10 — THE DECK MUST NOT CONTRADICT ITSELF (a pass, not a hope)

Rule 7 already calls self-contradiction a hard error and says to reconcile both cards. It
gives no way to find them. Card-by-card passes structurally cannot: a contradiction is a
property of a *pair*, and the pair is usually a hundred cards apart.

The Truncal deck shipped **11 contradictions**, every one of them a pair the passes read
separately and approved separately:

- transverse process depth 3–6 cm *and* 2–4 cm
- paravertebral needle Quincke *and* Tuohy
- paravertebral needle 8–10 cm *and* 5 cm
- volume per level 5 mL *and* 3–7 mL *and* 7 mL
- PECS arm abducted 90° *and* at the side
- intercostal needle 1.5 in *and* 5 cm
- advance past rib 3 mm *and* 2–4 mm *and* 3–5 mm
- rib angle 3–6 cm *and* 5–7 cm *and* 7–10 cm
- chest intercostals T1–T6 *and* T2–T6
- abdominal wall T7–T12 *and* T7–T11 *and* T6–L1
- and one card whose front contradicted its own `Extra`

**Build a claim index before the passes, and check every card against it.**

The index is one row per assertion, not per card:

```
subject | attribute        | value      | unit | nid | field
PVB     | TP depth         | 3–6        | cm   | 28  | Text
PVB     | TP depth         | 2–4        | cm   | 183 | Text
PVB     | needle           | Quincke    | —    | 21  | Text
PVB     | needle           | Tuohy      | —    | 190 | Text
```

Sort by `subject|attribute`. Any group with more than one distinct value is a collision.
A collision has exactly three legal resolutions:

1. **They are the same claim** → merge into one card, delete the other.
2. **They are different claims that read as one** → keep both and add the discriminating
   qualifier to *both stems*, so neither can be read as answering the other's question.
   "Transverse process depth" became legal only once it said *at T5–T10* and *in the
   lumbar region*.
3. **One is wrong** → Rule 9, then delete or correct.

"Leave both and hope the learner works it out" is not on the list. Under FSRS both get
reinforced.

**Index `Text` and `Extra` together.** Rule 8 sweeps read them separately, and should. This
pass must read them as one document, because an `Extra` field is where half of these
collisions live: F#177's `Extra` gave a rib-angle distance that contradicted F#92's front,
and F#102's front contradicted its own back.

**A card is not allowed to contradict its own `Extra`.** That is a hard fail, not a style
note. If the mechanism paragraph on the back describes a different plane, depth, or level
than the cloze on the front, one of them is wrong and you have already been told which by
the fact that they differ.

---

### RULE 11 — INHERITED CARDS ARE NOT PRE-APPROVED (one standard per deck)

The Truncal build produced a "FINAL" deck that was the original plus 78 new cards. **145 of
150 original fronts came through byte-identical; 3 `Extra` fields out of 145 were touched.**
The new cards were written well. The old cards were never re-read. Every contradiction in
Rule 10 is a collision between the two, and the deck shipped with two visibly different
prose standards inside it — 13.2 hype terms per 1,000 words in the inherited body against
0.7 in the new.

Two things caused that, and both are now closed:

**The calibration paragraph was read as permission.** "Under ~10% multi-cloze means
Claudify-era quality where targeted fixes are the correct output, not superficiality" is
true, **and it is a statement about structure only.** A deck can be 98% atomic, mechanically
flawless, and factually wrong on every third number — Truncal was. Low multi-cloze rate
licenses skipping *restructuring*. It never licenses skipping Rule 9, Rule 10, or Rule 12.
**Those three are per-card and unconditional at any multi-cloze rate.**

**New cards were written without reading the old ones.** Before writing any new card, check
the claim index (Rule 10) for its subject. If the deck already asserts something about that
subject, you are editing that card, not adding one.

**The test:** pick any two cards in the finished deck, one you edited heavily and one you
did not touch. If a reader could tell which was which from the prose, the pass was not
applied to the whole deck. Report the number of inherited cards you left byte-identical —
if it is most of them, say why.

---

### RULE 12 — EPISTEMIC REGISTER (say what is known, at the confidence it is known)

A card that overstates certainty is wrong in a way no mechanical check sees, and it is how
soft ranges quietly become hard facts.

**Banned wherever they attach to a measurement, range, or biological claim:**
*exactly · precisely · perfectly · strictly · absolutely · consistently · always · never ·
guarantees*

- BAD: `the arm abducted to exactly 90 degrees` — nobody protracts a shoulder to a degree
- BAD: `advanced precisely 3 mm` — through a body wall, by hand
- BAD: `consistently blocks T10 to L1` — TAP coverage is famously variable
- GOOD: `the arm abducted to {{c1::90}} degrees` / `advanced {{c1::2 to 4}} mm` /
  `typically blocks T10 down to {{c1::L1}}`

**A range stays a range.** Do not convert 6–8 cm into "6 cm", and do not invent a midpoint.
If two sources give different ranges, that is Rule 9, not a rounding problem.

**Emphasis is not evidence.** *catastrophic, devastating, profound, critical, crucial,
massive, dangerously* earn their place when the consequence really is that — a pneumothorax
is not "potentially concerning". They are noise when applied to a routine anatomic fact, and
across 40k cards they train the reader to skim exactly the sentences that matter. The
Truncal deck used "highly" 79 times.

The `Extra` field is where this concentrates, because nobody diffs 18,000 words of
unsourced prose. Write the back at the same confidence as the front.

---

### RULE 13 — PROCEDURAL AND DOSE CARDS ARE HELD HIGHER

Most wrong cards cost a review. A wrong **direction, depth, or dose** card is rehearsed
until it is automatic, and it is rehearsed by someone who will hold the needle.

The Truncal deck shipped:

> *"After contacting bone during a landmark paravertebral block, the needle is redirected
> slightly superior and **medial** and advanced 1 cm deeper."* `Extra`: *"Redirecting
> medially reduces the risk of pneumothorax."*

Medial redirection aims the needle at the intervertebral foramen. Two cards in the same deck
said so — one stated a medial trajectory "drastically increases the incidence of unintended
total spinal anesthesia." The deck taught the rule and its inversion simultaneously.

**A card that tells the reader to move a needle, or to give a drug, gets three extra checks
before it ships:**

1. **Direction, laterality, and order are verified explicitly against a technique
   description** — not inferred from the surrounding prose, and not carried over from a
   neighbouring block. Medial/lateral, cephalad/caudad, superficial/deep, and
   which-injection-first are the highest-density error sites in the whole collection.
2. **Every depth or advancement carries the landmark it is measured from**, and the
   spinal level or rib if it varies with either. "2–4 cm" is meaningless; "2–4 cm at
   T5–T10" is a fact.
3. **A dose names its drug and concentration.** `{{c1::0.4}} mL/kg of 0.25% bupivacaine` is
   a dose. "0.4 mg/kg of local anesthetic" is not — bupivacaine, ropivacaine and lidocaine
   differ by an order of magnitude in toxic threshold, so the number alone cannot be acted
   on and cannot be checked. **Then do the arithmetic against every other volume card for
   that block**: the Truncal deck's stated SAP ceiling was 1.8× smaller than the volume its
   own neighbouring card told you to inject.

**If a procedural card cannot be confirmed, it does not ship as a card.** Demote it to
`Extra` under Rule 0 and record it in `verify_items`. An unverified anatomy fact is a gap;
an unverified needle direction is a hazard.

---

### RULE 14 — PROVENANCE: CITE IN `Textbook`, TAG EVERY CARD

Rule 8 bans attribution **inside the card body**, and that stays absolute. It has never
banned *metadata*. The note type ships a dedicated `Textbook` field behind its own
collapsible button — invisible during recall, one click away during review. That is the
sanctioned home for a citation and it is not a Rule 8 violation.

It is currently empty on **100% of every deck built so far.**

That single fact is why the errors in Rule 9 were expensive to find and would have been
trivial to prevent. `Nagelhout 7e p.441` or `NYSORA — Intercostal Nerve Block` sitting on
the card turns "which of these two numbers is right" from an afternoon into a glance, and
it is the only thing that makes a contradiction *resolvable* rather than merely detectable.

- **Every card carries a `Textbook` value.** Module name and page for anything sourced from
  the module; the external reference for anything Rule 9 corrected or confirmed.
- **Every card carries tags**, on three axes minimum:
  `#Topic::Regional::Truncal::Paravertebral` · `#Source::Truncal` ·
  `#NCE::Basic-Principles`
- Tags are what make 40,000 cards filterable, auditable, rebuildable after a bad import,
  and reviewable by exam domain. At 227 cards you can browse. At 40,000 you cannot, and
  retrofitting tags across a finished corpus is far more expensive than adding them at
  build time.

Where a claim is genuinely contested, `Textbook` carries the mainstream citation and
`verify_items` carries the disagreement. The card itself still states the fact plainly.


---

### WORKED TRANSFORMATIONS, FROM THE USER'S OWN DECKS

**Example 1 — five unrelated facts in one card (Coags).**

Before:
`Warfarin has an oral bioavailability of ~{{c1::100}}%, a half-life of ~{{c1::36-42}}
hours, and is ~{{c1::99}}% bound to plasma {{c1::albumin}}, so decreased protein
binding paradoxically causes an increase in {{c1::free warfarin}} levels.`

Fails the atomicity test outright - blanking the half-life fails the whole card while
knowing everything else. Split into four:
1. `The oral bioavailability of warfarin is approximately {{c1::100}}%.`
2. `The elimination half-life of warfarin is approximately {{c1::36-42}} hours.`
3. `Warfarin is approximately {{c1::99}}% bound to plasma albumin.`
4. `Because warfarin is highly protein bound, decreased protein binding paradoxically
   increases the {{c1::free}} warfarin level.`

Card 4 carries the clinical consequence, so it survives Rule 0 easily. Cards 1-3 are
numbers - keep them, but consider demoting bioavailability to `Extra` if it is not
tested in this program.

**Example 2 — three concepts mashed together (Coags).**

Before:
`Fresh Frozen Plasma (FFP) requires ~250 mL per unit (risking volume overload and
TRALI), whereas 10 units of Cryoprecipitate (concentrated {{c1::Fibrinogen}}, Factor
{{c1::VIII}}, Factor {{c1::XIII}}, and {{c1::von Willebrand Factor (vWF)}}) typically
raises fibrinogen by {{c1::70-100}} mg/dL.`

Three concepts, 5 clozes. Split into an FFP volume card, an FFP complication card, a
cryo-contents card (one parent concept, so keep them together at max 3 clozes with all
four items still present in the text), and a fibrinogen-rise card.

**Example 3 — a defensible list that still breaks the cap (Diuretics).**

`The four pharmacologically relevant nephron segments are the {{c1::proximal tubule}},
the {{c1::thick ascending limb}}, the {{c1::distal convoluted tubule}}, and the
{{c1::collecting duct}}.`

These four share one parent concept, so one card is defensible under the atomicity
test - but 4 clozes exceeds the cap and each cloze is 3-4 words, breaking Rule 3. Keep
all four segments in the text, bracket the 3 highest-yield, and add individual
"X is a pharmacologically relevant nephron segment" cards for the rest.

---

### WHAT "DONE" LOOKS LIKE ON AN OLD DECK

A pre-March deck is mostly laundry lists. Expect to `split` or `rewrite` the majority
of it, and to demote a meaningful fraction into `Extra` under Rule 0.

**Measure the deck's multi-cloze rate before judging the op count.** The rate is the
denominator that makes the count meaningful:

- **>50%** - a pre-Claudify deck. Expect to split or rewrite most of it. A handful of
  ops here means passes 1 and 2 were skimmed; redo the run.
- **10-50%** - mixed. Expect substantial but not wholesale restructuring.
- **<10%** - Claudify-era. **Targeted fixes are the correct output, not superficiality.**
  Regional - Truncal measured 3% and correctly produced 5 ops.

Measured rates: Diuretics 86%, Coags 78%, Abx 66%, Regional-Truncal 3%, Intro 1%.
Always report the rate alongside the op count.

---

## 4. PASS ARCHITECTURE

Four judgment passes, with a **scripted validation gate after each one** so a later pass
never builds on an earlier pass's breakage:

1. **Content & clinical** — read every card as a clinician. Factual errors, self-contradictions
   between cards, broken sentences, dead phrasing. Apply section 3b.
2. **Structure & atomicity** — split laundry lists into atomic cards, rewrite chopped syntax
   into clinical pearls, move every cloze onto the highest-yield 1-2 word target, enforce the
   unit/abbreviation/punctuation rules. Apply section 3b. On an old deck this is the biggest
   pass by far.
3. **source gap-fill** — page-by-page read, add new cards and edit existing ones
4. **Hostile audit** — adversarial hunt for loss/gaps against *both* the original deck and the source module

Plus a final scripted concept-level diff against the original deck, enforcing the user's
"no information lost" rule mechanically.

**Stopping rule:** log substantive changes per pass. If pass 4 still yields meaningful
changes, run a pass 5 for that deck. If pass 3 comes back nearly empty, stop early. Deck
quality varies enormously — early decks (pre-March) are rough and may need 6; recent ones
converge in 3.

**Mechanical checks belong in scripts, not passes.** A script finds every cloze-count
breach, `c2`/`c3`, semicolon, and unbalanced tag across hundreds of cards in about a
second, exactly and repeatably. Never spend a judgment pass on that.

---

## 4d. LARGE DECKS — WORK IN BATCHES

A 500-card deck where 80% needs splitting means ~400 ops plus replacement cards. That
is more JSON than one uninterrupted stretch of work reliably produces, and a session
that runs out partway leaves a half-written `ops.json` that will fail the build.

Work in batches of roughly 50 source cards:

1. Dump all Text/Extra fields to a working file, numbered.
2. Process cards 1-50. **Append** to `ops.json` and `new_cards.json` - never rewrite
   them from scratch.
3. Record the last index processed in `work/<module>/progress.json`.
4. Repeat. If the session ends, the next one resumes from that index.
5. Run `build_deck.py` only once, after every batch is complete.

The build is atomic - it either applies every op or fails the gate - so a partial
`ops.json` is never half-applied to a deck. It just produces a deck missing the work
that was never written.

Batching also improves quality. Judgment degrades across hundreds of consecutive card
rewrites, and a fresh batch re-anchors on the rubric rather than drifting toward
whatever pattern the last forty cards established.

---

## 5. VALIDATION GATE — KNOWN BUGS TO AVOID

Both of these were hit for real and both nearly shipped bad output.

**Never concatenate `Text` + `Extra` when validating.** `Text` ends with `.` and `Extra`
often begins with a capital, so gluing them creates a fake run-on junction. This produced
**59 false-positive errors** in a deck that had 2 real ones. **Audit each field separately.**

**Literal string matches fail on HTML-wrapped text.** A fix targeting `"LAST."` silently
did nothing because the card actually read `<u><b>LAST</b></u> is a major concern`. The
gate must log a `SKIP` when a pattern isn't found and **never assume an edit applied**.
Verify the edit count matches the intended count at the end of every build.

**Final gate must confirm, before shipping:**
- `pragma integrity_check` = ok
- 0 orphan cards, 0 notes without cards, 0 duplicate guids/ids
- 0 bad notetype or deck references
- 0 house-rule violations across the *whole* finished deck
- Round-trip: rebuild the `.apkg`, reread it, confirm note count matches

---

---

## REFERENCE MATERIAL — DELIBERATELY NOT IN THIS FILE

The sections below live in **`HANDOFF_REFERENCE.md`**, in this same folder. They
are live parts of the pipeline, not deprecated — they are simply not needed by
every session, and this file is read in full by every session. Everything in an
agentic run's context is re-sent on every turn, so a section no phase reads is
paid for hundreds of times per module and returns nothing.

**Read the reference file when the phase needs it, and only then:**

| `HANDOFF_REFERENCE.md` § | Read it when |
|---|---|
| 1e. CREATION MODE | building a deck from nothing (no input `.apkg`) |
| 1f. ARCHIVING FINISHED INPUTS | after `--pass`, moving inputs out of the queue |
| 4b. KNOWN LIMITATIONS | something behaves oddly, or before proposing a design change |
| 4c. THE VERIFICATION PASS | any VERIFY phase (`PROMPT_verify.md` also summarises it) |
| 4e. SCRATCH CLEANUP | reclaiming disk, or `cleanup.py` surprises you |
| 4f. UNATTENDED SCHEDULING | changing how the scheduled runs behave |
| 6. SOURCE CAPTURES — OCR AND THE COVERAGE GATE | capture quality, OCR fallbacks, figure/IP handling |
| 6b. GOOGLE DRIVE — WHERE FILES LIVE | you need a folder ID |
| STATUS HISTORY | diagnosing a repeat failure, or writing a post-mortem |

```bash
python3 handoff.py list        # every section, its size, and which file holds it
python3 handoff.py 4b          # print one section
python3 handoff.py check       # assert the split lost nothing
```

**Nothing was deleted in the split.** `handoff.py check` asserts every expected
section is present across the two files, and `selftest.py` runs it — so a
section going missing fails loudly instead of a session quietly working without
it.

## 7. STATUS — GENERATED, DO NOT HAND-EDIT

*Regenerated automatically on run 0 (None). Source of truth: `project_state.json`.*

**Progress:** 0 verified · 0 built awaiting verification · 0 listed as pending

### ✓ No outstanding verification debt

### Pending modules

Not yet enumerated (~29 remaining). Append names to `pending_modules` in `project_state.json` as they are scheduled.


## 8. OPERATOR PREFERENCES — SEE `PROFILE.md`

The judgement calls that are genuinely a matter of taste live in **`PROFILE.md`** in the
project root, not here. How aggressively to correct errors without asking, whether to
preserve scheduling history, how to crop IO figures, how much thoroughness to trade for
speed — those are yours to set, and they are not pipeline facts.

**Read `PROFILE.md` at the start of every session, right after this file.** It is short.
If it does not exist, run `python3 scripts/bootstrap.py` to create one from the defaults.

Everything in *this* file is a rule of the pipeline rather than a preference: the rubric
in §3b, the gates in §5, the format in §3. Those are not yours to adjust per session, and
a session that finds itself wanting to should say so in its report rather than quietly
doing it.
