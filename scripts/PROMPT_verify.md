# VERIFY PROMPT — audit one finished module

Execute this entire file as your instructions.

**Run this in a session that did NOT build the deck.** A builder auditing its own work
shares its own blind spots. Approach the deck adversarially: assume something is wrong and
try to find it.

## SETUP

Copy everything from `scripts/` to **local disk** — SQLite cannot run on the
Google Drive FUSE mount (`disk I/O error` on any write). Copy the module's finished `.apkg`
and its original from `Anki Decks/` locally too.

Read `HANDOFF.md`, especially **section 3b** (the editorial rubric) and **section 4c** (this
pass). Load `project_state.json`.

## STEP 1 — MECHANICAL

```bash
python3 verify_deck.py "COMPLETED/<module>/<module> (FINAL).apkg" "Anki Decks/<module>.apkg"
```

It prints the canonical path for your report — `COMPLETED/<module>/audit/VERIFY_REPORT_<date>.md`.
**Write your report there**, so any later session finds it by module name alone.

Report all six sections verbatim:

| | |
|---|---|
| A | integrity — sqlite, orphans, notes without cards, duplicate guids |
| B | house rules — including **rule 8 source attribution**, checked in Text and Extra |
| C | cloze mechanics (rubric rule 3) |
| D | sfld/csum drift |
| E | information loss vs the original |
| F | 12 sampled cards |

`verify_deck.py` now also runs `check_consistency.py` automatically and prints its output as
sections **G-K**. Report those verbatim too:

| | |
|---|---|
| G1a | measured collisions — one subject+attribute, two values. **Gating.** |
| G1b | spinal level / count collisions — soft |
| G2 | a card whose `Extra` contradicts its own `Text`. **Gating** for measured quantities. |
| G3 | categorical conflicts — needle type, needle direction, body/limb position. Soft. |
| H | the Rule 9 worklist — every checkable claim, grouped by class |
| I | the Rule 13 worklist — direction/depth cards, and **doses with no drug named** (gating) |
| J | Rule 12 — certainty language sitting on a measurement |
| K | Rule 14 — `Textbook` and tag coverage |

**G is the check that catches what nothing else can.** A contradiction is a property of a pair,
and every other section reads one card at a time. A deck built by *appending* to an original is
where these cluster: Truncal's FINAL kept 145 of 150 original fronts byte-identical and added 78
new cards written without reading them, producing 11 collisions that every card-by-card pass had
approved. **Report the counts even when they are zero**, so the check is visibly run.

**Do not treat G1b, G3 or J as noise you may skip.** They print without gating because some of
their hits are legitimate contrasts, not because they are unimportant — the Quincke-vs-Tuohy
conflict and the T1–T6-vs-T2–T6 conflict both land there. Read every one and say which you
dismissed and why.

If `check_consistency.py` is missing from the script folder, `verify_deck.py` says so and fails.
Re-copy it; do not report a verification pass without sections G-K.

**A mechanical PASS is necessary, not sufficient.** Sections C and E are worklists, not
verdicts:

- A >3-word cloze may be correct if it is a required abbreviation expansion (rule 3 puts the
  expansion *inside* the brackets).
- Every term in the loss diff must trace to an intentional correction, a demotion, or a
  rewrite. **Find the source of each one** and say which. An unexplained loss is a failure.
- Drift is only acceptable if it matches the original deck's pre-existing count. Compare.

## STEP 2 — JUDGMENT

Read the 12 sampled cards against rubric 3b. For each, state whether it holds up on:

- **Rule 1** — would failing it mean not knowing the concept, or blanking one of several independent items?
- **Rule 2** — is the answer inferable from the sentence? Is more than one answer defensible?
- **Rule 3** — cloze length, units outside, abbreviation handling
- **Rule 4** — does it read like a clinical pearl, or like chopped syntax?
- **Rule 6** — does Extra follow the house structure?
- **Rule 8** — does the card name its source anywhere in any field?

Then read 10 more cards of your own choosing, weighted toward the newest ones and anything
that looks unusual.

**Rule 8 gets a whole-deck sweep, not just the sample.** The script's section B catches the
common constructions, but read the deck for anything that attributes a fact rather than
stating it — "According to Apex...", "Per Apex...", "Apex ranks/describes/emphasizes...",
"the module states...", "as taught in the lecture...". Every hit is a defect and gets
patched in Step 5.

- BAD:  `According to Apex, the thoracolumbar fascia has {{c1::three}} layers.`
- GOOD: `The thoracolumbar fascia has {{c1::three}} layers.`

The repair is a `rewrite` that deletes the attribution clause and keeps the fact intact —
never a delete, and never a rewording that quietly loses content. Check `Extra` as
carefully as `Text`; attribution survives there most often. The word "apex" is also
anatomical (apex of the heart, apex beat) — those are fine, and the script excludes them.
Attribution that belongs to a genuine Rule 7 inter-source disagreement moves to the NOTES
doc's `verify_items`; it does not stay on the card.

## STEP 2b — THE CHECKABLE-CLAIM AUDIT (Rule 9)

**Do not sample this one.** Section **H** of the consistency output has already listed every
card carrying a distance, a depth, a dose, a volume, a spinal level, a nerve root, a named
structure on a needle path, a direction, or a percentage. Run it with `--all` to get the
untruncated list:

```bash
python3 check_consistency.py "COMPLETED/<module>/<module> (FINAL).apkg" --all
```

Look each one up against an independent authority — NYSORA, StatPearls, BJA Ed, ASRA,
Nagelhout, Barash, Miller, or the primary paper.

This is the pass the pipeline has never had, and it is where the real defects were:

| Shipped in a deck that passed build | Actually |
|---|---|
| rib angle 3–6 cm from midline | 6–8 cm at the lower ribs; medial to the angle is contraindicated |
| SAP ceiling 0.4 **mg**/kg | 0.4 **mL**/kg — a unit class error, and 1.8× inconsistent with the same deck's volume card |
| needle tip *inside* the innermost intercostal | the plane between internal and innermost; deep to innermost is pleura |
| costotransverse ligament on the US intercostal path | not on that path — it belongs to the paravertebral block |
| aim **lateral** to miss the inferior epigastric | lateral is where the artery is in ~49% of patients |
| single thoracic ESP covers 8–11 dermatomes | ~3–4 cranial and caudal; the literature calls spread unpredictable |

None needed specialist knowledge. Each was one search.

**Rule 13 gets its own sweep on top.** Every card that tells the reader to move a needle or give
a drug: confirm the direction, the landmark the depth is measured from, and that the dose names
its drug and concentration. Truncal shipped a card instructing medial redirection during a
paravertebral block — the manoeuvre every source warns against — while another card in the same
deck said a medial trajectory causes total spinal. Treat a wrong direction as a failing finding
regardless of how the rest of the deck scores.

Report every claim you could not confirm. Silence is not confirmation.

## STEP 3 — THE RULE 0 AUDIT

**This is the part that matters most.** Open the NOTES doc changelog and review every
`DEMOTE` and `DELETE`.

For each, ask: was this genuinely not board-testable and not decision-changing? A demotion
that should have stayed a card is a silent gap in coverage. A deletion that was not truly
trivia is unrecoverable.

Report each one with your own verdict — agree or disagree.

## STEP 4 — CLINICAL SPOT-CHECK

Every factual correction the build made, verified against the source module. Quote what the
source actually says. Flag anything you cannot confirm.

Also confirm each IO figure matches its stated topic — the crops are derived
programmatically and may be off-center or mismatched.

## STEP 5 — WRITE *AND APPLY* THE PATCH (if it does not pass)

Do not hand the user a list of edits to make manually. Fix it in this session.

Write `work/<module>/patch_ops.json` in standard ops format, and
`work/<module>/patch_cards.json` for any card that must be added:

```json
[ {"nid":N, "op":"rewrite", "text":"...", "extra":"...", "why":"verify: <finding>"},
  {"nid":M, "op":"edit", "field":1, "find":"...", "replace":"...", "why":"verify: <finding>"} ]
```

A **lossy demotion** is repaired either by reinstating the card (`patch_cards.json`) or by
a `rewrite` on the parent carrying the full payload. The build refuses a demotion that
would drop distinctive terms, so a bare re-demote will be rejected — carry everything.

Then apply it:

```bash
COMPLETED_DIR="<local>/COMPLETED" HANDOFF_OUTDIR="<local>/_out" \
  python3 build_deck.py --patch "<module>"
```

This patches the **already-built deck in `COMPLETED/`** — not a rebuild — and regenerates
`.txt` and `.apkg` in place. Confirm `BUILD OK`, ops applied == ops intended, and
`ACCOUNTING: reconciled`.

**Sort your fixes into two classes and treat them differently.**

**Mechanical** — one correct answer, no content judgement: bare abbreviations, cloze
re-targeting, duplicated clauses, unit-inside-cloze, stale counts, wording that is simply
wrong. Apply these freely.

**Judgement** — anything that decides what content matters: lossy demotions, deletions,
half-landed contradictions spanning several cards, source-vs-fact conflicts. Apply them too,
but **list every one in a dedicated "JUDGEMENT CALLS APPLIED" section of your report**, with
what you changed and why. This is the section the user reads. Fixing a self-contradiction
means searching the WHOLE deck for the term and reconciling every card that mentions it —
not editing the one the report named.

## STEP 5b — RE-VERIFY THE PATCH (mandatory, never skip)

You just fixed problems you found yourself, so nothing has independently checked the fix.
Close that gap with the scripted gates, which do not care who wrote the patch:

```bash
python3 verify_deck.py "COMPLETED/<module>/<module> (FINAL).apkg" "Anki Decks/<module>.apkg"
```

Confirm: every previously-failing check now passes, the information-loss diff shows **no new
losses**, `sfld/csum` drift did not increase, and **G1a is clean or its remaining groups are
ones you have explicitly justified**. Report sections A–E and G–K again, post-patch.

Reconciling a contradiction by editing one card of the pair is how half-landed fixes happen.
Re-running G is what proves the pair actually closed — and it will catch a *new* collision your
patch introduced, which is the other common failure.

If the patch introduced a regression, fix it and re-run. Do not report a pass on a deck whose
post-patch verification you have not run.

Then rebuild the NOTES doc — its counts and `verify_items` are now stale:

```bash
MODULE="<module>" COMPLETED_DIR="<local>/COMPLETED" node build_notes.js
```

And reconcile `project_state.json` counts (`totals.cards_after`, `added`, `edited`) against
the actual deck, then `python3 update_handoff.py`.

## STEP 6 — RECLAIM SCRATCH

```bash
python3 cleanup.py --yes --module "<module>"      # after a PASS
python3 cleanup.py                                 # dry run to see the backlog
```

Also worth running once against any older work directory that predates automatic cleanup:

```bash
python3 cleanup.py --yes --sweep --root "<path to an old scratch dir>"
```

## REPORT

Write the full report to `COMPLETED/<module>/audit/VERIFY_REPORT_<date>.md` and summarise it
in chat. It must contain, in this order:

1. **Verdict** — did the deck as built pass, before any patching?
2. **Sections A–F** from the initial mechanical run, plus **G1a/G1b/G2/G3** and the
   **H, I, J, K** worklists.
3. **JUDGEMENT CALLS APPLIED** — every content-affecting fix, with reasoning. The single most
   important section for the user.
4. **Mechanical fixes applied** — list, no commentary needed.
5. **Post-patch verification** — sections A–E and G–K after the patch.
6. **Still outstanding** — anything you could not resolve, including IO crops needing a
   visual check and every Rule 9 claim you could not confirm.

**These findings are failing, not advisory:** a wrong direction/depth/dose card (Rule 13), a
card that contradicts another card or its own `Extra` (Rule 10), and a dose with no drug named
(Rule 13). A deck with any of them does not pass, whatever sections A–F say.

**Do not run `--pass`.** Marking a module verified stays the user's decision after reading
the judgement calls — that is the one check no script can replace.

## AFTER THE REPORT — WAIT FOR APPROVAL, THEN FINISH IT

Stop after reporting and wait. Do not run `--pass` on your own initiative.

If the user replies with an approval — "approved", "pass it", "looks good, pass" or
similar — then complete these four steps **in this session**, while everything is still
set up locally:

```bash
HANDOFF_OUTDIR="<local>/_out" python3 verify_deck.py --pass "<module>"
```

That sets `status: verified`, clears the outstanding list, regenerates `HANDOFF.md`, and
auto-reclaims the module's scratch.

Then:
1. Copy the regenerated `HANDOFF.md` and `project_state.json` back to
   `scripts/` in Drive. **This is the step that is easy to forget and it
   matters** - without it the next session reads a stale state and re-queues a finished
   module.
2. Confirm the three deliverables and the `audit/` folder are in `COMPLETED/<module>/`.
3. Report the remaining queue depth so the user knows what is next.
4. Offer to empty the scratch root now that the module is closed out:
   ```bash
   python3 cleanup.py --purge --root "<scratch root>" --completed "<COMPLETED path>"
   ```
   Run the dry run, show it, and purge on approval. Nothing in scratch is unique once the
   audit trail is in `COMPLETED/<module>/audit/`.

If the user instead disputes a judgement call, do not pass. Write a corrected patch,
re-apply, re-verify, and report again.


When the user does run `verify_deck.py --pass "<module>"`, that marks the module verified
and automatically reclaims its scratch space (~15 MB of regenerable page images), keeping
the audit trail. No separate cleanup step is needed.

If it does not, list precisely what must be fixed and whether it needs a rebuild or a
targeted patch.
