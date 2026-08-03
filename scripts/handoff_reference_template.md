# ANKI DECK OPTIMIZATION — HANDOFF REFERENCE

Companion to `HANDOFF.md`. Everything here is live, load-bearing pipeline
knowledge. It is split out only because most sessions never need it, while
`HANDOFF.md` is read in full by every one of them.

**Do not read this file end to end.** Read the section the current phase needs —
`HANDOFF.md` carries the index of which section applies when, and
`python3 handoff.py <section>` prints one without loading the rest.

---

## 1e. CREATION MODE — BUILDING A DECK FROM NOTHING

The pipeline also creates decks. Drop in an **empty `.apkg`** plus source material
(lecture PPT exported to PDF, transcript, bulleted notes) and the same machinery
builds the deck from scratch. Verified working: an empty deck populates cleanly, all
gates pass, `sfld/csum` drift is 0.

**How to make the empty `.apkg`:** export any existing deck that uses the
`ZzzAnki Master Cloze` note type, then delete its notes in Anki and re-export.
Exporting a genuinely never-used deck may omit the note type entirely, which breaks
the build - the note type must survive. `build_deck.py` prints
`EMPTY SOURCE DECK - running in CREATION mode` when it detects this.

**Creation follows the user's original extraction rules, which differ from gap-fill:**

- **Exhaustive extraction.** Every fact, definition, number, example, and process step
  in the source becomes a card - subject to Rule 0's yield filter, which routes
  low-yield material into `Extra` rather than dropping it.
- **Learning Objectives are the highest-value target.** If the source has an objectives
  slide, nearly all exam questions come from those topics. Do NOT make cards *for* the
  objectives themselves - instead do a final pass confirming the deck covers every
  objective topic.
- **Speaker notes are high-yield.** PowerPoint presenter notes are prime test material
  and must be processed as carefully as the slides.
- **Marry the transcript to the slide.** Use the deck to identify the topic, then the
  transcript for the lecturer's actual explanation. If the lecturer says "the slide says
  X, but in practice we do Y," the card tests Y.
- **Process non-prose too:** figure captions, diagram labels, parenthetical notes, table
  entries. Captions especially.
- **Then apply rubric 3b in full.** Creation and optimization produce the same house
  format - a newly created deck must meet the gold standard on its first build, not
  after a later cleanup pass.

Since there is no source module, creation runs `mode: "optimize-only"` for queue
purposes: pass 3 is replaced by extraction from the lecture source.

---

---

## 1f. ARCHIVING FINISHED INPUTS

When a module is verified, its original deck and source captures are **moved out of the
input folders**:

```
Old Anki Decks and Files/
  <Module>/
    Anki Deck/   <the original .apkg>
    Files/       the source capture, or every capture from its folder
```

```bash
python3 archive_inputs.py --module "<Module>" --decks "Anki Decks" \
    --source "Source Files" --archive "Old Anki Decks and Files"        # dry run
... --yes                                                                  # move
```

`verify_deck.py --pass` runs this automatically. Paths come from `ANKI_DECK_DIR`,
`SOURCE_DIR` and `ARCHIVE_DIR`, defaulting to the names above. 

**Why moving, not copying.** It preserves the pre-optimization originals, and it makes one
specific accident impossible: a finished deck left in `Anki Decks/` will eventually be
picked up by a future run as its source, and the gap-fill cards get added a second time.
The duplicate guard catches exact repeats but not reworded ones. Moving the input out
removes the failure mode instead of relying on a guard.

**Deck files are matched by the deck name INSIDE the .apkg**, not only by filename - a real
deck is called `Truncal.apkg` while the deck inside is `Regional - Truncal`. Filename first,
then internal name. A capture folder has its contents flattened into `Files/` rather than
nested.

It refuses to archive a module that is not `verified`, since that would remove the source a
re-run needs.

---

---

## 4b. KNOWN LIMITATIONS — NOT YET SOLVED

**Self-consistency is now enforced in code: `check_consistency.py`.**

`verify_deck.py` runs it automatically as sections **G-K**, and a collision fails the
verification the same way an integrity error does. It can also be run alone:

```bash
python3 check_consistency.py "COMPLETED/<Module>/<Module> (FINAL).apkg"
python3 check_consistency.py deck.apkg --json claims.json      # the claim index
python3 check_consistency.py deck.apkg --baseline before.json  # new collisions only
python3 check_consistency.py deck.apkg --all                   # no truncation
```

| Section | What it does | Gates? |
|---|---|---|
| G1a | measured collisions - one subject+attribute, two values | **yes** |
| G1b | spinal level / count collisions | no - a nerve legitimately carries different levels in different roles |
| G2 | a card whose `Extra` states a different value than its own `Text` | **yes**, for measured quantities |
| G3 | categorical conflicts - needle type, needle direction, body/limb position | no |
| H | the Rule 9 worklist: every checkable claim, grouped by class | no |
| I | the Rule 13 worklist: direction/depth cards, plus **doses with no drug named** | **yes**, for the dose check |
| J | Rule 12 - certainty language sitting on a measurement | no |
| K | Rule 14 - `Textbook` and tag coverage | no |

**The gate is deliberately narrow.** A noisy gate gets ignored, which is worse than no
gate. Only a measured contradiction and a drug-less weight-based dose fail the run;
everything that needs adjudication prints and does not.

**It makes no medical judgement and needs none.** It does not know that 6-8 cm is the
right rib-angle distance. It knows the deck says 3-6 and 5-7, which is enough, because a
deck that contradicts itself is wrong somewhere no matter which value is right.

How it works, because the design is what makes it portable to a pharmacology deck:
subjects are **mined from the deck**, not hardcoded - any noun phrase ending in a domain
head noun is a candidate, abbreviations defined as `Erector Spinae Plane (ESP)` are
resolved, and a longer variant collapses onto the shortest attested form so "landmark
thoracic paravertebral block" and "paravertebral block" are one subject. The only
hardcoded list is `HEAD_NOUNS` - what counts as a thing a claim can be *about*. Extending
that list is how the tool reaches a new subject area; with the regional-only list it
indexed 21 assertions in a 267-card airway deck because it had never heard of a blade or
a cuff.

Two deliberate refinements are worth knowing before you "fix" an apparent false negative:
a spinal region named in the sentence is folded onto an anatomical subject, so the
thoracic/lumbar contrast pair does not report as a contradiction while the real
thoracic-vs-thoracic conflict inside it still does; and positional adjectives survive on
anatomy but not on techniques, so "lateral pectoral nerve" and "medial pectoral nerve"
stay two nerves. `selftest.py` section 9 locks both behaviours down.

Measured against the deck that motivated it: 7 gating collisions on
`Regional - Truncal (FINAL)`, including the transverse-process depth, the paravertebral
needle length, the intercostal advancement and the rib-angle distance - five of the
eleven contradictions a full manual audit found, at zero marginal cost. The Quincke/Tuohy
conflict lands in G3 and the dose-unit error in section I.

**Nothing enforces Rule 9, and nothing can** - a lookup is not scriptable. What section H
does is remove the excuse: it prints every claim that needs one, so a run cannot claim to
have checked them without having been handed the list.

**Rule 14 is unstarted.** `Textbook` is empty and tags are absent on 100% of every deck
built so far; section K reports it every run.

Two further gaps remain. Neither blocks a module run, but both compound across 30 decks.

**Cross-deck duplication is unchecked.** Each module is processed in isolation against
its own source. Concepts that recur across decks (LAST, sterile technique, standard
block complications, common pharmacology) will produce near-duplicate cards in every
deck that touches them. At 25-30k cards this is a real and invisible review tax. Nothing
currently detects it. A future pass should compare the finished corpus across modules
and flag near-duplicates for consolidation - it needs the whole corpus, so it cannot run
per module.

**Cross-deck duplication** has tools (`find_duplicates.py`, `verify_corpus.py`) and a
prompt (`PROMPT_dedupe.md`), but is unrun and is the hardest remaining problem.

Two facts govern the design. First, these duplicates are **not textually similar** - the
same principle written months apart against different sources scores ~0.2 on full-text
similarity. That is why `find_duplicates.py` outputs **topic clusters** rather than
scored pairs: reading every card about a concept together reveals duplicates that share
no vocabulary. Second, **a duplicating card usually also carries content found nowhere
else**, so the operation is a MERGE preserving the union, never a bare delete.
`verify_corpus.py` snapshots the whole corpus before and proves afterwards that no
distinctive term or numeric fact vanished. Snapshot first or the check is impossible.

**The four session prompts live in `scripts/`.** Each is self-contained -
the user's whole instruction is "read <file> and execute it."

| Prompt | Purpose | Output |
|---|---|---|
| `PROMPT_build.md` | process one module | the deck |
| `PROMPT_verify.md` | audit it, fresh session | a verdict + an applied patch + re-verification |
| `PROMPT_patch.md` | apply an existing report | a corrected deck, in place |
| `PROMPT_dedupe.md` | cross-deck consolidation | merge proposal |
| `PROMPT_auto.md` | **one phase, unattended** | whatever `next_action.py` says is next |

**Build and verify must be separate sessions.** A builder auditing its own build shares its
own blind spots.

**Verify and patch may share a session**, because the protections against information loss
are now mostly in code and do not care who wrote the patch: the demote loss gate and the
accounting gate in `build_deck.py`, and the information-loss diff in `verify_deck.py`. The
one thing merging costs is that nobody independently checks the fix - so
`PROMPT_verify.md` makes a **post-patch re-run of `verify_deck.py` mandatory**, and
requires every content-affecting change to be listed in a "JUDGEMENT CALLS APPLIED"
section for the user. Marking a module `verified` remains the user's decision; that is the
one check no script replaces.

**Reports live at `COMPLETED/<module>/audit/VERIFY_REPORT_<date>.md`**, newest wins.
`verify_deck.py` prints the canonical path. This makes reports discoverable by module name
alone, the same way `build_queue.py` pairs a deck to its source.

---

---

## 4c. THE VERIFICATION PASS

A module is not finished when it builds. It is finished when a **different session**
has audited it. A builder checking its own work shares its own blind spots, so
verification must run fresh, without the reasoning that produced the deck.

```bash
python3 verify_deck.py "COMPLETED/<Module>/<Module> (FINAL).apkg" "Anki Decks/<Module>.apkg"
```

It reports six sections:

| | Checks |
|---|---|
| **A. Integrity** | sqlite, orphan cards, notes without cards, duplicate guids |
| **B. House rules** | cloze counts, c2/c3, semicolons, brace and tag balance, empty Extra, **rule 8 source attribution in Text or Extra** |
| **C. Cloze mechanics** | rubric rule 3 - cloze length, units inside, punctuation inside, bare abbreviations |
| **D. sfld/csum drift** | the bug that shipped once; distinguish pre-existing from introduced |
| **E. Information loss** | term-level diff against the ORIGINAL deck - every lost term must be an intentional correction or demotion |
| **F. Judgment sample** | 12 random cards printed for a human/model read against rubric 3b |

**A mechanical PASS is necessary, not sufficient.** Sections A-E are scriptable.
Sections C and F are worklists, not verdicts - a 4-word cloze may be correct if it is
a required abbreviation expansion, and only reading the card tells you.

Before a module counts as verified, all four must be done:

1. Read the 12 sampled cards against rubric 3b - atomicity test, cloze targeting,
   clinical pearl phrasing.
2. Read the NOTES changelog and confirm every `DEMOTE` and `DELETE` was the right call.
   **This is the Rule 0 audit** - the one genuinely subjective judgment in the pipeline.
3. Spot-check every clinical correction against the source module.
4. Confirm the IO figures match their stated topics.

Then, and only then:

```bash
HANDOFF_OUTDIR="<local>/_out" python3 verify_deck.py --pass "<Module Name>"
```

which sets `status: "verified"`, clears the outstanding list, regenerates the handoff, and
auto-reclaims the module's scratch. **Nothing else may set `verified`.**

**Run it in the session that just did the verification or patch**, on the user's approval -
that session already has the scripts local, the state current, and the scratch present. A
fresh session would have to copy everything down from Drive and copy the state back for a
single command. **After passing, copy `HANDOFF.md` and `project_state.json` back to Drive**;
skipping that leaves the next session reading stale state and re-queueing a finished
module. If modules accumulate as
`built-unverified` forever, the status field stops carrying signal and the whole
tracking system quietly becomes decorative.

---

---

## 4e. SCRATCH CLEANUP

`work/` accumulates roughly **15 MB per module** in page images and unpacked databases -
about **1.5 GB across 100 modules** - and every byte of it regenerates from the source PDF
in one command. What must survive is ~60 KB per module and is irreplaceable.

**One name, two places.** The scratch the scripts read and write is always `work/`, created
next to them on local disk - that is what `build_deck.py`, `next_action.py`, `build_notes.js`
and `cleanup.py` all resolve by default. The `_work/` folder in Drive is a legacy scratch
root from before automatic cleanup; it is only ever a `--root` argument, never a path a
session writes to. The prompts said `_work/` for both until 2026-07-30, which meant a
session that followed them literally wrote patch ops and progress files where nothing
would read them.

```bash
python3 cleanup.py                    # dry run, verified modules only
python3 cleanup.py --yes              # delete
python3 cleanup.py --yes --all        # include unverified (prompts first)
```

**Copy the extracted text into `COMPLETED/<module>/audit/source/`** before cleaning:
`content.txt` or `content_ocr.txt`, `extract_report.json`, `manifest.json`. Roughly 90 KB,
and it is the only surviving record of what the module's source actually said - everything
else in `source/` regenerates, but only if the source PDF still exists. Cheap insurance.

| Deleted (regenerable) | Kept (audit trail) |
|---|---|
| `source/pages/`, `source/strips/` | `ops.json`, `new_cards.json`, `meta.json` |
| `_build/` unpacked databases | `changelog.json`, `progress.json` |
| `source/manifest.json` checkpoint | `extract_report.json`, extracted text |
| local copies of source PDFs/decks | |

**Gated on verification.** Only modules with `status: "verified"` are cleaned by default.
Before verification the page images may still be needed - a failed verification forcing a
re-read would otherwise mean re-OCRing the entire module.

**`--pass` closes a module out completely.** It marks the module verified, then:

1. reclaims that module's scratch (page images, `_build`)
2. archives the original deck and captures to `Old Anki Decks and Files/<Module>/`
3. runs the purge safety check on the scratch root and reports whether it can be emptied

Step 3 is a check, not an automatic deletion - emptying an entire folder stays a
deliberate act. If all three safety checks pass, re-run with `--yes` to do it.

**Emptying a whole scratch root.** Per-module cleanup only reaches `<root>/work/<module>/`.
It cannot see script copies, `__pycache__`, stray `.db` files, session checkpoints, or
module folders sitting at the root - which is what accumulates when the scripts are run
from a Drive scratch folder. Once every module is verified:

```bash
python3 cleanup.py --purge --root "<path>/_work" --completed "<path>/COMPLETED"        # dry run
python3 cleanup.py --purge --yes --root "<path>/_work" --completed "<path>/COMPLETED"
```

Three safety checks, all of which must pass:

1. every tracked module is `verified`
2. every module's `audit/` folder exists in `COMPLETED/` - the audit trail is the only
   thing in scratch that is not regenerable
3. **no orphaned module scratch** - a scratch folder with no verified module behind it is
   work in progress, or a build that crashed before `record_run` wrote its entry. Purging
   it would destroy unrecorded work.

`--force` overrides all three; use it only when you have checked by hand.

**Nothing in a scratch root is unique.** It is either a copy of a script that lives
authoritatively in `scripts/`, or regenerable module scratch. The whole
folder can be emptied between modules.

**Clearing an old backlog.** Scratch predating automatic cleanup, or scratch left in
Drive, is cleaned by pointing at it:

```bash
python3 cleanup.py --root "<path>/_work"                  # dry run
python3 cleanup.py --yes --sweep --root "<path>/_work"    # ignore status, clear it all
```

`--sweep` ignores verification status; use it only for a known-stale backlog. Cleanup also
purges the temp databases the tools leave in `/tmp`, which the dedupe pass generates in
bulk.

Never deleted: anything in `COMPLETED/`, anything in Drive, and the audit JSONs. The
changelog is the only record of why a card was demoted or deleted, and it costs nothing
to keep.

---

---

## 4f. UNATTENDED SCHEDULING

A recurring Cowork task is the retry loop. Each run does **one phase** and stops; if it
dies on a usage limit, the next run reads state and resumes.

**Every run refreshes the queue before it asks what to do.** `build_queue.py` runs first,
re-pairs decks with modules, and re-records the input paths for whatever environment this
run is in. That is what makes the pipeline self-starting - a deck dropped into `Anki Decks/`
is picked up by the next run with no hand-editing - and it is why the queue must exclude
**every** tracked module, not just the verified ones. A finished module rests at
`built-unverified` until you `--pass` it, so excluding only `verified` would re-queue it,
rebuild it from scratch every hour, and never reach the next deck.

The loop across sessions is therefore: refresh queue -> BUILD deck one -> refresh queue
(deck one now tracked, so it stays out) -> VERIFY deck one, which patches and re-verifies
in the same session -> refresh queue -> BUILD deck two -> and so on, until every deck is
tracked and the queue comes back empty. Only then does `AWAIT_USER` appear, listing what is
waiting on your `--pass`.

```bash
python3 next_action.py --status     # what is next, and why
python3 next_action.py --claim      # decide + take the lock
python3 next_action.py --release    # release when the phase ends
```

`next_action.py` decides from state alone, in priority order: resume an interrupted build,
verify anything built but unverified, patch anything that failed its gates, build the next
queued module, else idle. **Finishing what is started always outranks starting something
new** - otherwise a limit mid-build leaves half-done work behind forever.

A lock file prevents two runs colliding, and goes stale after 180 minutes so a dead run
cannot block the pipeline permanently.

**Finding `COMPLETED/` is what makes "verify once" work.** Whether a module still needs
verifying is decided by whether `COMPLETED/<module>/audit/VERIFY_REPORT_*.md` exists, so
`next_action.py` has to find that folder from a scratch dir that does not contain it. It
resolves, in order: `COMPLETED_DIR` in the environment, then `paths.completed` recorded in
`project_state.json` by `build_queue.py`, then a `COMPLETED/` beside the scripts - and it
re-checks that whichever it picks actually exists, because an absolute path recorded on one
run can be reached by a different route on the next.

If it can find none of them it **stops with exit 2 instead of deciding**. This is the one
place where a wrong guess is unrecoverable: reading "no COMPLETED" as "not verified" would
re-issue VERIFY on an already-verified module every single run, forever, and the queue
would never advance. Section 6 of `selftest.py` covers this whole decision table.

**The scheduled task prompt is `PROMPT_auto.md`.** Set it to run hourly.

**Two things never happen unattended:**
- `--pass` is never run. Modules finish at `built-unverified` with their report written,
  and the user decides. Unattended running does not remove the one human checkpoint.
- A stale-script or empty-folder condition stops the run rather than being worked around.

**Realistic throughput.** One phase per scheduled run, so a module needs 2-5 runs (more if
it is large enough to batch). At hourly cadence: two decks overnight is comfortable; thirty
decks is closer to a week than to 48 hours. Usage limits stretch this further. The pipeline
is unattended, not fast - the point is that it makes progress without you, not that it
finishes quickly.

---

---

## 6. SOURCE CAPTURES — OCR AND THE COVERAGE GATE

**Every source capture is 100% images with no text layer.** The user captures every page
with GoFullPage, which paints the DOM to a canvas via html2canvas and wraps it in
jsPDF; `pdftotext` returns zero words. Confirmed across all 74 PDFs in the working
folders - every one reports Producer `jsPDF 4.0.0` and a zero-word text layer. The
one iLovePDF-merged file reports Producer `iLovePDF` and also zero words.

**This means `ocr_used` is always `true`, and always will be** while capture goes
through GoFullPage. Do not write logic that optimises the `ocr_used: false` branch -
it has never once executed.

**Two consequences worth knowing about, neither of them token-related:**

1. *Collapsed content is not captured.* GoFullPage records what is rendered. A Rise
   accordion sitting closed - "References", "Old References", any "+" disclosure -
   contributes its header and nothing else. **Expand every accordion before
   capturing.** The coverage gate surfaces the collapsed "+" control as
   untranscribable ink, so it will appear on the visual read list, but the hidden
   text behind it is simply not in the file and no amount of care downstream
   recovers it.
2. *The completeness gate used to under-report badly.* Tesseract reads the footer
   "Page 4 of 39" as "Page 4 0f 39" - zero for the "o" - so the strict regex matched
   almost nothing, and every module reported a bogus alarm (Truncal 4/31, Airway
   5/31, Anesthesia Machine 23/107). `PAGE_FOOTER` now tolerates the substitutions
   and `selftest.py` pins the cases. **The historical "missing pages" alarms in the
   completed modules' reports should be re-read as false.**

If visual reading fails, fall back to:
```bash
pdftoppm -jpeg -r 150 module.pdf pages/page          # rasterize
tesseract pages/page-NN.jpg out --psm 1              # OCR prose/tables (good quality)
pdfimages -png -f N -l N module.pdf figs/pNN         # extract discrete figures
tesseract figs/pNN-001.png - --psm 11                # identify a figure by its own labels
```

Per-figure OCR is how figures get identified without seeing them — the embedded anatomical
labels name the view. Note that `pdfimages` also extracts *text blocks* rendered as images;
distinguish them by whether OCR returns prose (text block) or short labels (real figure).

**OCR is weak exactly where it hurts most: digits, en-dashes, decimals.** Every
OCR-derived number — nerve root ranges, volumes, dermatome counts, needle gauges, mA
settings, mg/kg doses — goes in the NOTES.docx verification section for the user to
eyeball. Do not silently trust them.

**Source figures are copyrighted IP.** They may appear in the NOTES.docx as cropped visual
references only (so the user knows which view to recreate). They must **never** go into a
card. When a topic genuinely needs an image — pattern recognition on sonoanatomy, spatial
coverage maps — write an IO card request in NOTES.docx with the cropped figure beneath it,
and let the user build the card with their own image.

---

---

## 6b. CLOUD SYNC — OPTIONAL, AND NOT HOW THIS PIPELINE RUNS

**Nothing in the pipeline requires a cloud connector.** Every script resolves its
folders as `argv` → environment variable → the path `build_queue.py` recorded on the
last run → a relative default. Point it at a local folder and it works; that is the
supported path and the one the scheduled runs use.

Record your own folder IDs in `project_state.json` under `drive` if you sync to a
cloud folder and want them written down. Nothing reads that block except a human —
it ships empty on purpose.

The rest of this section is the hard-won detail about working *through* a Drive
connector, kept because it is easy to lose a day to. Skip it if you are working from
a local folder or a synced desktop folder, which is the normal case.

**At session start:** search the HANDOFF folder, sort by `createdTime` descending, and read
the newest `HANDOFF_*.md` and `project_state_*.json`. Those are the current truth.

**CRITICAL — use the right read tool.** `read_file_content` markdown-escapes its output
(`_` becomes `\_`, `[` becomes `\[`, `>` becomes `\>`), which **breaks `json.loads()`**. For
`project_state.json` and any script, use **`download_file_content`** and base64-decode it —
that returns byte-exact content (verified by sha256 round-trip). `read_file_content` is only
safe for prose you are going to read with your eyes, never for anything parsed.

**At session end:** `build_deck.py` regenerates both. Upload them to the HANDOFF folder with
today's date in the filename.

**The Drive connector cannot overwrite** — `create_file` only creates. So every run leaves a
new dated copy and stale ones accumulate. Always take the newest by `createdTime`; never
assume a fixed filename is current.

**Large binaries probably will NOT transfer through the connector.** `download_file_content`
returns base64 into context, which is not viable for a 50 MB image-only PDF, and
`read_file_content` returns nothing useful for a PDF with no text layer (which every the source
module is). If the module PDF cannot be pulled from Drive, it must be attached to the session
directly — or, in Cowork on the desktop, read from the local Google Drive sync folder as an
ordinary file path, which bypasses the connector entirely. **Test this before relying on it
for an unattended scheduled run.**

---

---

<!--REFERENCE_STATUS-->
