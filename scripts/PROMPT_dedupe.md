# DEDUPE PROMPT — cross-deck consolidation

Execute this entire file as your instructions.

**Run only after the bulk of modules are built and verified.** This needs the finished
corpus and cannot work per module — which is exactly why per-module runs cannot catch
this class of problem.

## THE PROBLEM, STATED PRECISELY

Cross-deck duplicates in this collection are **not** textually similar. They are the same
principle written in completely different words, formats, and clinical framings, because
each was written months apart against a different source. Full-text similarity scores such
pairs around **0.2** — they are invisible to any matching score.

Worse, and this is the part that makes deletion dangerous:

> **A card that duplicates another deck's fact very often ALSO carries information found
> nowhere else in the collection.**

So the operation is **almost never "delete the duplicate."** It is **merge**: produce a
result that preserves the union of everything unique across the cards involved. A bare
delete silently destroys the unique portion, and no single-deck diff can detect it.

## SETUP

Copy `scripts/` and the whole `COMPLETED/` tree to **local disk** — SQLite
cannot run on the Google Drive FUSE mount (`disk I/O error` on any write).

Read `HANDOFF.md` section 3b, especially **Rule 0** (yield filter — merged content often
belongs in `Extra`), **Rule 5** (interference), and **Rule 8** (a merged card must not name
the source, by any name — strip attribution as you merge, keep the fact).

If the project root contains `PROFILE.md` or `USER_PROMPTS.md`, read them too. From
`USER_PROMPTS.md`, use **Every run** and **Final duplicate check**. These are user
preferences, not permission to weaken safety gates, factual verification, audit records,
or human approval.

## STEP 1 — SNAPSHOT FIRST. NON-NEGOTIABLE.

```bash
python3 verify_corpus.py --snapshot "COMPLETED" --out corpus_before.json
```

This records every distinctive term and numeric fact in the collection. Without it there
is no way to prove afterwards that a merge lost nothing. Do not proceed without it.

## STEP 2 — BUILD THE TOPIC WORKLIST

```bash
python3 find_duplicates.py "COMPLETED" --out dupes.json
```

Also take a per-deck claim index, which is what surfaces class D below:

```bash
for d in COMPLETED/*/*\ \(FINAL\).apkg; do
  python3 check_consistency.py "$d" --json "${d%.apkg}.claims.json" --all > /dev/null
done
```

Two decks giving different values for one `subject|attribute` is a cross-deck contradiction.
Nothing else in the pipeline can see it: each deck is internally consistent and each passed
its own verification.

The output is **topics**, not pairs — every card touching a concept, grouped across all
decks. This is deliberate. Reading all eleven cards about "5 mL volumes" together makes
duplicates obvious on sight, including ones sharing no vocabulary at all. A similarity
score cannot do this; reading the group can.

Report the topic count and the topics spanning the most decks.

## STEP 3 — WORK TOPIC BY TOPIC

For each topic, read **every** card in it, side by side. Then for each cluster of cards
covering the same principle, do this in order:

**First, inventory what is unique to each card.** Before deciding anything, list what each
card contains that the others do not — a mechanism, an exception, a number, a clinical
correlation, a mnemonic, an `Extra` field teaching something the others omit. **This step
is what prevents silent loss.** Do not skip it because two cards "obviously" say the same
thing.

**Then classify:**

**A. True duplicate, nothing unique on either side.**
Rare. Keep the instance in the most topically appropriate deck, delete the other.

**B. Same principle, but each card carries something the other lacks.** *This is the common
case.* Merge: rewrite the surviving card to carry the union of the unique content, then
delete the absorbed one. If the union will not fit one card without violating atomicity
(Rule 1), the correct result is **two atomic cards, not one crowded card** — or move the
secondary detail into `Extra` per Rule 0.

**C. Same stem, different answer.** NOT duplicates — an *interference* problem under Rule 5.
Structurally parallel cards differing only in a value teach pattern recognition instead of
the fact. Keep both. Add discriminating cues so each stem stands alone.

**D. Same question, incompatible answers — a CROSS-DECK CONTRADICTION.** Distinguish this from
C carefully. C is two different facts that look alike; D is one fact with two values, and the
collection is teaching both. This is Rule 10 at corpus scale, and it is the failure mode with
no per-deck defence at all: one deck says the transverse process is at 3–6 cm, another says
2–4 cm, and neither deck's verification can see the other.

Resolve it, do not merge it. Apply Rule 9 — look the value up against an independent authority
— then correct every deck that carries the wrong one, in the same proposal. If both are right
under different conditions, qualify **every** stem so none can be read as answering another's
question. Record the citation in `Textbook` on all of them (Rule 14) so the next reader can see
why this value won.

**Report D findings separately from A/B/C.** A cross-deck contradiction is a defect the corpus
has been carrying silently, not a consolidation opportunity, and the user should see the list
as its own thing.

**Also legitimate: keeping both.** Encountering a fact through two clinical lenses in the
decks you actively study is often better learning, not redundancy. Consolidate when the
framing is genuinely redundant, not merely overlapping. **When uncertain, keep both** —
a redundant card costs review minutes, a wrong merge costs knowledge.

## STEP 4 — PROPOSE, CHANGE NOTHING

Write `dedupe_ops.json` keyed by deck, in standard `ops.json` format:

```json
{"<deck name>": [
  {"nid":N, "op":"rewrite", "text":"...", "extra":"...",
   "why":"merged unique content from <deck>#<nid>: <what was carried over>"},
  {"nid":M, "op":"delete", "why":"absorbed into <deck>#<nid> - nothing unique lost"}
]}
```

Every `delete` must name where its content went. A `delete` whose `why` does not account
for the card's unique content is a bug, not a decision.

Present the full proposal with reasoning per cluster and **stop**. The user approves
deletions individually. Cross-deck deletion is the most destructive operation in this
pipeline.

## STEP 5 — APPLY (only after explicit approval)

Per approved deck: copy its ops into `work/<deck>/ops.json`, re-queue that deck, run
`build_deck.py`.

## STEP 6 — PROVE NOTHING WAS LOST

```bash
python3 verify_corpus.py --compare corpus_before.json "COMPLETED"
```

Every lost term and every lost number must be explained. **A vanished numeric fact — a
dose, a threshold, a nerve root — is almost never an acceptable merge outcome.** If
anything lost was unique content from an absorbed card, the merge dropped information and
must be redone.

Then re-run `verify_deck.py` on every modified deck: a cross-deck edit can break a card
that verified fine in isolation.

## STEP 7 — RECLAIM SCRATCH

```bash
python3 cleanup.py --yes --sweep --root "work"
```

The dedupe pass unpacks every deck in the corpus and leaves temp databases in `/tmp`.
Sweep them; the audit trail is preserved.

## REPORT

Topic count, the A/B/C classification breakdown, which decks overlap most, the corpus
no-loss result, and your proposal with per-cluster reasoning.

State clearly whether anything has been changed.
