# BUILD PROMPT — process one module

Execute this entire file as your instructions.

## SETUP

Working folder layout (all paths relative to it):

```
scripts/         scripts + current state + these prompts
Source Files/    input: one folder of capture PDFs per module
Anki Decks/      input decks, one .apkg per module
COMPLETED/       finished output, one subfolder per module
work/            scratch (local, regenerable)
```

**CRITICAL — SQLite cannot run on the Google Drive FUSE mount.** Any write throws
`disk I/O error`. Copy the scripts and all inputs to **local disk** (e.g. `~/anki-run/`),
do every step there, and copy only the finished deliverables back to Drive at the end.
This is not optional and is not a script bug.

## STEP 0 — CONTEXT AND HEALTH CHECK

Copy **everything** from `scripts/` to local disk — every script, not
just the ones named below. `update_handoff.py` is imported by `build_deck.py` and
`verify_deck.py`; if it is missing they crash on import.

Read `HANDOFF.md` completely. Start with its CRITICAL PATH header, then read the whole
file. **Section 3b (the editorial rubric) is the actual job** — passes 1 and 2 mean
applying all fourteen of its rules to every card. Load `project_state.json`.

**Do NOT read `HANDOFF_REFERENCE.md` up front.** It holds the situational sections
(creation mode, archiving, known limitations, the verification pass, cleanup,
scheduling, capture/OCR notes, Drive IDs, history). `HANDOFF.md` indexes which
section applies when; pull one with `python3 handoff.py 4b` if and when a phase
needs it. Everything you read stays in context and is re-sent on every subsequent
turn, so reading it "just in case" costs the whole session, not one turn.

Then run, from local disk:

```bash
python3 deps.py
python3 check_version.py     # stale scripts cause phantom bug reports
python3 selftest.py          # first run in a new environment only
```

**If `check_version.py` reports differing files, re-copy them from Drive before doing
anything else.** A stale script will make you diagnose bugs that are already fixed.

**And if any input folder reads as empty, do not believe it.** A cloud mount hydrates on
demand; an empty read means "not materialized", not "not there". Force hydration by opening
the folder, or by reading a file in it with the file tools, then re-check.

Do not proceed if selftest fails.

## STEP 1 — QUEUE

```bash
python3 build_queue.py "<local>/Source Files" "<local>/Anki Decks"
```

Report: module name, deck_id, card count, mode, any unpaired PDFs. A deck with no
matching source is queued `optimize-only` — that is correct, not an error. Stop and say
so if nothing queued.

## STEP 2 — EXTRACT (skip if optimize-only)

```bash
python3 extract_apex.py "<module .pdf or folder>" "work/<module>/apex"
```

Report `ocr_used`, `course_pages_missing`, `unaccounted_ink_px`, and the visual-token
line from the read plan.

- `ocr_used: false` → text layer present, numbers exact, skip the NOTES verification section
- `ocr_used: true` → every number goes in the verification list. With GoFullPage
  capture this is always the case.

**`unaccounted_ink_px` must be 0.** It is the assertion that every informative pixel
on every page is either inside a high-confidence OCR word box or on the visual read
list. If it is not 0 the extractor says so and exits non-zero — re-run with
`COVERAGE=page` and read whole pages rather than proceeding.

**The completeness gate parses `Page N of M` out of OCR text.** The regex now tolerates
the substitutions tesseract actually makes ("0f" for "of"), which is what produced the
bogus alarms in the earlier module reports. A missing-pages alarm is now usually real —
but still confirm it against the read targets before re-capturing.

OCR may be killed by the sandbox on long modules; the extractor is resumable, so just
re-run it.

## STEP 3 — VISUAL READ (before any other analysis)

Read every image in `read_targets`, in order, from the first. Do not begin card
analysis, gap analysis, or `.apkg` inspection until this is done. If rendering fails or
returns blank, STOP and report the exact filename.

**What you are being handed.** For most pages `read_targets` is one composed sheet per
page rather than the whole page: the page at full width and full resolution, with the
bands that need eyes stacked in reading order and a grey rule where prose was elided.
The elided prose is not lost — it is in `content_ocr.txt` verbatim, transcribed more
reliably than you would read it off a picture. **Read `content_ocr.txt` alongside the
sheets; together they are the page.** A page that could not be proved is handed to you
whole, as before.

`verify_targets` is a *separate* list you do not read here. Those are the lines carrying
numbers, and they belong to pass 1 / Rule 9, where comparing the transcription against
the pixels is the point.

**Delegate this step to a subagent.** Page images are the largest thing the pipeline
touches, and anything in the main thread's context is re-sent on every later turn of the
run — so reading thirty pages inline means paying for thirty pages on every subsequent
turn, through build, notes and save-back. Spawn a subagent whose whole job is:

> Read `work/<module>/apex/content_ocr.txt`, then every image in `read_targets` in
> order. Return: (a) a section-by-section outline of the module, (b) every figure with
> what it depicts and whether it is IO-card material, keyed to its page, (c) anything
> the OCR text renders wrongly or ambiguously, (d) any collapsed accordion or truncated
> control visible in a sheet, since that means content missing from the capture itself.

Its context dies with it; you keep the outline. If a later pass needs a specific figure
again, read that one sheet directly.

## STEP 3b — BUILD THE CLAIM INDEX (before any card is written or edited)

**Rule 10.** A contradiction is a property of a *pair*, and the passes below are card-by-card,
so they structurally cannot find one. The Truncal deck shipped 11 contradictions this way —
depth 3–6 cm *and* 2–4 cm, needle Quincke *and* Tuohy, arm at 90° *and* at the side. Every
pair was read separately and approved separately.

Run the extractor over the deck you are about to work on:

```bash
python3 check_consistency.py "<the deck you are processing>" --json "work/<module>/claims_before.json" --all
```

It prints the collisions already present and writes the claim index. Read section **G1a**
before you touch a card: those are contradictions the deck arrived with, and they are
yours to resolve. Sections **H** and **I** are your Rule 9 and Rule 13 worklists.

The rows it writes look like this — one per assertion, not one per card:

```json
[{"subject":"paravertebral block","attribute":"transverse process depth",
  "value":"3 to 6","unit":"cm","nid":1782794334894,"field":"Text"}]
```

Index anything with a number, a unit, a spinal level, a nerve root, a named needle, a
direction (medial/lateral, cephalad/caudad, superficial/deep), an order, or a laterality.

**Any group holding more than one distinct value is a collision and goes on the worklist.**
Resolve each exactly one of three ways — merge, qualify both stems so neither reads as
answering the other's question, or correct the wrong one under Rule 9. Leaving both is not
an option.

The script gates on measured quantities only. **G1b, G3 and J are worklists you still have
to read** — a spinal-level conflict and a Quincke-vs-Tuohy conflict are real defects that
print without failing the run, because some of their siblings are legitimate contrasts and
a gate that cries wolf is a gate nobody reads.

Then keep the index open through STEP 4. **Before writing any new card, look up its subject.
If the deck already asserts something about it, you are editing that card, not adding one.**
That single check would have prevented all 11.

## STEP 4 — THE FOUR PASSES

Apply rubric 3b to every card, in this order of precedence:

| | |
|---|---|
| Rule 0 | yield filter — card, demote to Extra, or drop |
| Rule 1 | atomicity test — would failing this mean I don't know the concept, or that I blanked one of several independent items? |
| Rule 2 | cloze targeting — not inferable AND determinate |
| Rule 3 | cloze mechanics — 1-2 words, units outside, abbreviations inside and expanded every time, no punctuation inside, max 3 |
| Rule 4 | clinical pearl phrasing, no laundry lists |
| Rule 5 | interference — discriminating cues on near-identical cards |
| Rule 6 | Extra field structure |
| Rule 7 | source authoritative for scope, medical fact authoritative for accuracy |
| Rule 8 | **never name the source in a card — no "Apex" anywhere in any field** |
| Rule 9 | **every checkable claim gets looked up — distance, dose, level, structure-on-a-path, direction, percentage** |
| Rule 10 | **the deck must not contradict itself — build the claim index, resolve every collision** |
| Rule 11 | **inherited cards are not pre-approved — one standard across the whole deck** |
| Rule 12 | epistemic register — no *exactly / precisely / consistently / always* on a range |
| Rule 13 | **direction, depth and dose cards are held higher; a dose names its drug** |
| Rule 14 | populate `Textbook`, tag every card |

**Rule 8 in full, because it is new and absolute.** A card states the fact and never says
who asserted it. "Apex" — and every paraphrase of it — must not appear in `Text`, in
`Extra`, or in any other field, on an edited card or a newly written one.

- BAD:  `According to Apex, the thoracolumbar fascia has {{c1::three}} layers.`
- GOOD: `The thoracolumbar fascia has {{c1::three}} layers.`
- BAD:  `Apex ranks carbon monoxide production as {{c1::desflurane}} greater than isoflurane.`
- GOOD: `Carbon monoxide production is greatest with {{c1::desflurane}}, and lower with isoflurane.`

"Per Apex", "the Apex module", "as taught in Apex", "the module states", "the lecture
emphasizes" are the same violation — do not launder the attribution into a generic noun.
**Strip the attribution and keep the fact**; this never justifies dropping content. The one
place attribution belongs is the NOTES doc's `verify_items`, which is not a card.

The passes:

1. **Content & clinical** — read as a clinician, then **verify under Rule 9**. Reading catches
   prose that sounds wrong; it does not catch a number that is off by a factor of two. Look up
   every distance, dose, spinal level, structure-named-on-a-needle-path, direction, and
   percentage. All six of Truncal's hard errors were one search away and none were caught by
   reading.
2. **Structure & atomicity** — split laundry lists, rewrite chopped syntax, move clozes onto the highest-yield target.
3. **Apex gap-fill** (skip if optimize-only). Check the claim index before adding anything.
4. **Hostile audit** — adversarially hunt for loss and gaps against BOTH the original
   deck and the source. **Run this one as a subagent too.** It needs the whole deck and
   the whole source and returns a list of findings; done inline, both stay in context
   for the rest of the session. Give it the deck, `content_ocr.txt`, `ops.json` and
   `new_cards.json`, and ask for findings only. Do the same for the Rule 9 lookup sweep
   if the deck is large — it is a lot of search traffic for a short ledger.

   **Subagents are for reading, never for deciding.** Every op still goes through
   `ops.json` in the main thread, and a DEMOTE or DELETE is still yours to call. A
   subagent that returns "I fixed it" has been used wrong.

Audit Text and Extra **separately for Rule 8** — attribution hides in `Extra` and concatenating
masks it. **Read them together for Rules 9, 10 and 12**, because a card that contradicts its own
`Extra` is a hard fail and you cannot see it one field at a time. Truncal shipped one: the front
put the needle tip inside the innermost intercostal muscle, the back correctly put it in the
plane above.

**Calibrate effort to the deck — but only the STRUCTURAL effort.** Measure the multi-cloze rate
first. Above ~50% means a pre-Claudify deck needing mass restructuring — expect to split or
rewrite most of it, and a handful of ops would mean the passes were skimmed. Under ~10% means
Claudify-era quality where targeted *restructuring* is the correct output.

**It never means fewer content checks.** A deck can be 98% atomic, mechanically flawless and
factually wrong on every third number — Truncal was exactly that, and this paragraph is why its
build touched 3 `Extra` fields out of 145 inherited cards. **Rules 9, 10, 11 and 12 are per-card
and unconditional at any multi-cloze rate.** Report the rate so the op count can be judged
against it, and **report how many inherited cards you left byte-identical** — if that is most of
them, justify it.

Fix factual errors directly — **including errors in the source module itself.** Rule 7 makes
established medical fact authoritative for correctness; the source is authoritative only for
scope. If the module states something anatomically wrong, correct it and record the
discrepancy in `notes_config.json`'s `verify_items`. Do not propagate it verbatim.

Surface genuine inter-source disagreements rather than resolving them silently — in the
NOTES doc's `verify_items`, never inside the card itself (Rule 8).

**Errors inherited from the ORIGINAL DECK are the same job.** Rule 11: a card that arrived in
the deck is not pre-approved, and "it was already there" has never been a reason to ship it.
Truncal's rib-angle error, its innermost-intercostal error and its "partial autonomic
neuropathy" all came through from the original untouched because nobody re-read them.

**Rule 13 — hold direction, depth and dose cards higher than the rest.** Verify medial/lateral,
cephalad/caudad, superficial/deep and which-injection-first against an actual technique
description rather than inferring them from neighbouring prose; give every depth the landmark
it is measured from and the spinal level if it varies; and make every dose name its drug and
concentration, then check the arithmetic against every other volume card for that block. A card
you cannot confirm gets demoted to `Extra` and logged in `verify_items` — an unverified anatomy
fact is a gap, an unverified needle direction is a hazard.

**Rule 14 — fill `Textbook` and tag every card, as you go.** Both are empty across every deck
built so far, and both are far more expensive to retrofit than to add now. `Textbook` takes the
module name and page, or the external reference for anything Rule 9 corrected. Tags take three
axes minimum: topic hierarchy, source, and NCE exam domain. This is not a card-body attribution
and does not violate Rule 8 — `Textbook` is metadata behind its own collapsible button and is
invisible during recall.

**Every change must go through `ops.json`.** Never edit the deck database directly — the
changelog is built from the ops, so a direct edit produces counts that do not reconcile and
an audit trail that under-reports what happened. The build now fails on that mismatch.

Decks over ~200 cards: work in batches of ~50, **appending** to `ops.json` and
`new_cards.json`, recording position in `work/<module>/progress.json`.

## STEP 5 — BUILD

Write to `work/<module>/`:

```
new_cards.json   [{"text": "...", "extra": "..."}, ...]
ops.json         [{"nid":N, "op":"split"|"rewrite"|"edit"|"demote"|"delete", ...}]
meta.json        {"outstanding":[...], "gaps_filled":[...], "summary":"..."}
```

Then:

```bash
COMPLETED_DIR="<local>/COMPLETED" HANDOFF_OUTDIR="<local>/_out" python3 build_deck.py
```

Then re-run the consistency check against the deck you just built, comparing to the
index you took in STEP 3b:

```bash
python3 check_consistency.py "COMPLETED/<module>/<module> (FINAL).apkg" \
    --baseline "work/<module>/claims_before.json" --all
```

**Any collision flagged `not in baseline` is one your own edits created** — the commonest
way that happens is appending new cards without checking the index, which is exactly the
failure this whole step exists to prevent. Fix them before moving on.

Confirm `BUILD OK`, ops applied == ops intended, and that `sfld/csum drift` is either 0 or
matches the source deck's pre-existing count. On an EDIT MISMATCH the find-string probably
failed because the target is HTML-wrapped — fix and rebuild.

## STEP 6 — NOTES DOC

**Do not hand-edit `build_notes.js`.** Write `work/<module>/notes_config.json`:

```json
{ "source_label": "<module> APEX", "source_pages": N, "ocr_used": true|false,
  "cards_before": N, "cards_after": N,
  "verify_items": [["Category", "the values", "why OCR-risky"]],
  "io": [{ "id":"IO-01", "file":"IO-01_topic.png", "page":N, "priority":"HIGH",
           "topic":"...", "tests":"...", "whyImage":"...", "occlude":"...", "labels":"..." }] }
```

Put the cropped figures in `work/<module>/io_figs/`, then:

```bash
MODULE="<module>" COMPLETED_DIR="<local>/COMPLETED" node build_notes.js
```

Each IO entry needs its cropped source figure as a **visual reference only** — those
figures must never go into a card. **Verify each crop visually before it ships**; crops
derived by coordinate are easy to get subtly wrong.

If `ocr_used` is `false` the verification section is omitted automatically. Section 4 is
generated from `meta.json`'s `outstanding` list.

## STEP 7 — SAVE BACK

Copy to Drive:
- the three deliverables → `COMPLETED/<module>/`
- **the audit trail** → `COMPLETED/<module>/audit/`:
  `ops.json`, `new_cards.json`, `meta.json`, `changelog.json`, `extract_report.json`.
  These are the only record of *why* each card was changed. A previous run lost them and
  its NOTES doc became the sole (and inaccurate) audit trail.
- regenerated `HANDOFF.md` and `project_state.json` → `scripts/`
- any script you fixed → `scripts/`

Then reclaim scratch:
```bash
python3 cleanup.py                 # dry run - shows what is reclaimable
```

## REPORT

Cards before/after, cards added, ops by type, the deck's multi-cloze rate, whether OCR was
used, gaps filled, IO cards flagged, and anything you were unsure about.

Plus, from `extract_report.json`: `unaccounted_ink_px` (must be 0), how many pages were
promoted to a whole-page read and why, and `visual_tokens_baseline` vs
`visual_tokens_plan`. If any page was promoted for a reason other than being genuinely
figure-dense, say which — that is the gate telling you something about the capture.

Also, every run:

- **Claim-index result** — `check_consistency.py` G1a/G1b/G2/G3 counts before and after,
  and how each collision was resolved (merged / qualified / corrected). **Zero new
  collisions against the baseline** is the number to report.
- **Rule 9 ledger** — how many checkable claims you looked up, how many disagreed with the
  module, and every claim you could not confirm.
- **Inherited cards left byte-identical**, as a count and a fraction. If it is most of them,
  justify it.
- **Rule 13 cards** — how many direction/depth/dose cards the deck contains and that each was
  verified against a technique description.

**Call out every DEMOTE and DELETE explicitly** — those are the judgment calls I review.

Do not start a second module. Do not mark anything verified.
