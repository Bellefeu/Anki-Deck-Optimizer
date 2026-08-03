# PATCH PROMPT — apply a verification report to an already-built deck

Execute this entire file as your instructions.

This does **not** rebuild the deck and does **not** re-derive the verification. It consumes
an existing verify report and applies its findings to the deck already sitting in
`COMPLETED/`, producing corrected `.apkg` and `.txt` files in place.

**You need only the module name.** The report is discovered automatically:

```bash
ls -t "COMPLETED/<module>/audit/"VERIFY_REPORT_*.md | head -1
```

The newest `VERIFY_REPORT_*.md` in that folder is the one to apply — the same
newest-wins convention the handoff uses. If none exists (an older run may have produced
the report only in chat), ask the user to paste or attach it, and write it to that path
yourself so the next session can find it.

**When to use this prompt instead of `PROMPT_verify.md`:** verify now patches in the same
session by default. Use this one when the report predates that change, when you want to
apply findings selectively rather than wholesale, or when a patch needs a fresh session
because the previous one ran out of room.

## SETUP

Copy **everything** from `scripts/` to **local disk** — SQLite cannot run on
the Google Drive FUSE mount (`disk I/O error` on any write). All nine scripts, not just the
ones named here; `update_handoff.py` is imported by `build_deck.py`.

Copy locally too:
- `COMPLETED/<module>/<module> (FINAL).apkg` — the deck being patched
- `COMPLETED/<module>/audit/` if it exists — prior ops and changelog
- `work/<module>/source/` if it survives — needed for source verification and IO crops

Read `HANDOFF.md`, especially **section 3b** (the editorial rubric) and the two gates noted
in section 1b. Load `project_state.json`.

Then:
```bash
python3 deps.py
python3 check_version.py     # stale scripts cause phantom bug reports
```

## STEP 1 — READ THE REPORT AND CONFIRM EACH FINDING

For every item in the report, **verify it against the deck yourself before fixing it.** A
report is evidence, not instruction. Pull the actual note and confirm the defect is real and
still present — a prior partial patch may already have addressed some of it.

Report which findings you confirmed, which you could not reproduce, and which you disagree
with. Do not silently skip a finding you disagree with; say so and explain.

## STEP 2 — WRITE THE PATCH

Write `work/<module>/patch_ops.json`, and `work/<module>/patch_cards.json` if a card must
be reinstated or added:

```json
[ {"nid":N, "op":"rewrite", "text":"...", "extra":"...", "why":"verify: <finding>"},
  {"nid":M, "op":"edit", "field":1, "find":"...", "replace":"...", "why":"verify: <finding>"},
  {"nid":P, "op":"demote", "into_nid":Q, "facts":["...","..."], "why":"verify: <finding>"} ]
```

Rules that matter here specifically:

- **A lossy demotion is repaired by carrying the ENTIRE payload.** Use `"facts"` (a list),
  not `"fact"`. The build refuses a demotion that would drop any distinctive term from the
  retiring card, so an under-filled repair will be rejected — that refusal is the gate
  working, not a bug. If the content genuinely deserves to be a card again, reinstate it via
  `patch_cards.json` instead.
- **A half-landed correction needs `rewrite`, not another `edit`.** Surgical find/replace is
  what left the contradiction half-fixed. When reconciling a self-contradiction, search the
  WHOLE deck for the term first, list every card that mentions it, and fix all of them.
- **Snapshot the claim index before you patch**, so you can prove the patch added nothing:
  ```bash
  python3 check_consistency.py "COMPLETED/<module>/<module> (FINAL).apkg" \
      --json "work/<module>/claims_before.json" --all
  ```
  and after applying, re-run with `--baseline work/<module>/claims_before.json`. Anything
  flagged `not in baseline` is a collision your patch created.
- **Every value you change goes back through the claim index (Rule 10).** Changing one number
  is how a *new* contradiction gets created — the fix to one card silently orphans the `Extra`
  of another that cross-referenced the old value. Before writing the op, grep the whole deck
  for the old value and for the subject, and patch every hit in the same pass. Correcting the
  Truncal rib-angle distance on one card immediately invalidated a cross-reference in a
  different card's `Extra` ninety cards away.
- **Verify the replacement, not just the defect (Rule 9).** A report saying a number is wrong
  is not authority for what the right number is. Look up distances, doses, spinal levels,
  structures-on-a-needle-path, directions and percentages before writing them in, and record
  the source in `Textbook` (Rule 14). Rule 13 applies with full force here: a patch that
  changes a needle direction, a depth, or a dose is verified against a technique description
  and the dose names its drug and concentration.
- **A source error gets corrected AND recorded.** Per Rule 7, established medical fact
  outranks the module. Fix the card, then add the discrepancy to `notes_config.json`'s
  `verify_items` so the NOTES doc shows the source erred.
- **A card must never name its source — Rule 8, non-negotiable.** No source name in `Text`,
  `Extra`, or any other field, and no paraphrase of it ("per the source", "the source module",
  "the module states", "as taught in the lecture"). This applies to the cards you patch
  *and* to every replacement string you write.

  - BAD:  `According to <SOURCE>, the thoracolumbar fascia has {{c1::three}} layers.`
  - GOOD: `The thoracolumbar fascia has {{c1::three}} layers.`

  Repair with `rewrite` — an `edit` find/replace that snips "According to <SOURCE>, " leaves a
  lowercase orphan ("the thoracolumbar fascia has...") and a stale `sfld`. Strip the
  attribution and keep the fact; never drop content to satisfy this rule. If the
  attribution existed because two sources genuinely disagree, move it to `verify_items` in
  the NOTES doc, which is not a card. A source name that doubles as a domain term is
  fine and is not a violation.
- Apply the non-blocking items in the same patch. Cheap now, another whole cycle later.

## STEP 3 — APPLY

```bash
COMPLETED_DIR="<local>/COMPLETED" HANDOFF_OUTDIR="<local>/_out" \
  python3 build_deck.py --patch "<module>"
```

Confirm:
- `BUILD OK`
- ops applied == ops intended (no silent SKIPs)
- `ACCOUNTING: reconciled` — the changelog explains the deck's delta
- no new `sfld/csum` drift

If an op was SKIPPED, the find-string probably failed because the target is HTML-wrapped.
Fix and re-run. **Never patch around a refusal by editing the database directly** — that is
what produced the unreconcilable counts the report flagged.

## STEP 4 — RE-VERIFY MECHANICALLY

```bash
python3 verify_deck.py "COMPLETED/<module>/<module> (FINAL).apkg" "Anki Decks/<module>.apkg"
```

Confirm every previously-failing check now passes, and that the information-loss diff shows
no NEW losses. Report sections A–E.

## STEP 5 — REBUILD THE NOTES DOC

Its counts and `verify_items` are now stale. Update `work/<module>/notes_config.json` with
the corrected `cards_before` / `cards_after`, add any newly-found source errors to
`verify_items`, then:

```bash
MODULE="<module>" COMPLETED_DIR="<local>/COMPLETED" node build_notes.js
```

If IO crops were flagged as unverified, **check each one visually against its source figure
now** and fix or replace any that are off-centre or mismatched.

## STEP 6 — RECONCILE STATE

Correct any wrong counts in `project_state.json` — `totals.cards_after`, `added`, `edited`
must match the deck. Then regenerate:

```bash
HANDOFF_OUTDIR="<local>/_out" python3 update_handoff.py
```

## STEP 7 — SAVE BACK, THEN CLEAN

Copy to Drive:
- corrected `.apkg`, `.txt`, `.docx` → `COMPLETED/<module>/`
- **audit trail** → `COMPLETED/<module>/audit/`: `patch_ops.json`, `patch_cards.json`,
  `changelog.json`, `notes_config.json`, `meta.json`. The report found a run whose audit
  trail was missing entirely — do not repeat that.
- `HANDOFF.md`, `project_state.json` → `scripts/`

Then:

```bash
python3 cleanup.py                  # dry run - see what is reclaimable
python3 cleanup.py --yes --module "<module>"
```

## REPORT

Which findings you confirmed, fixed, or disputed. The gate results from Step 3. The
re-verification from Step 4. Anything still outstanding.

**Do not mark the module verified.** A patch author verifying their own patch has the same
blind-spot problem as a builder verifying their own build. The next verification runs fresh.

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

