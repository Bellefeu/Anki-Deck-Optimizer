# PROFILE — your preferences, not the pipeline's rules

Read at the start of every session, right after `scripts/HANDOFF.md`. Everything here
is a judgement call that is legitimately yours. Everything in HANDOFF.md is not:
the rubric in §3b, the gates in §5 and the format in §3 are rules, and a session that
wants to bend one should say so in its report rather than quietly doing it.

Edit freely. These are the defaults the pipeline was built around, not commandments.

## Corrections

- **Fix factual and clinical errors directly; do not stop to ask.** Log every change in
  the NOTES doc. Established medical fact outranks the source for correctness; the
  source only decides scope (Rule 7).
- Surface genuine inter-source disagreements in the NOTES doc's `verify_items`, never
  inside a card.

## Scheduling history

- **Not preserved.** The reference build was resetting its collection, and the scripts
  assume that.
- If you need to keep your review history, decide now, not after the first build: it
  changes how a rebuilt deck must be imported. Say so explicitly in your first session.

## Scope of work

- One module at a time. Pilot on a small deck before scaling.
- Cleanup and source gap-fill combined per module, not as separate passes.

## Image-occlusion figures

- Crop to the relevant figure only, not the full page.
- Source figures are a **visual reference for you**, never card content. Build the card
  with your own image.

## Effort and cost

- Thoroughness over speed. The rubric is per-card and unconditional.
- The pipeline is built to be economical without cutting rigor: prose is read as OCR
  text, only untranscribable regions are read as pixels, and `unaccounted_ink_px == 0`
  proves nothing was skipped. If you ever distrust that, set `COVERAGE=page` and it
  reads whole pages like it used to.
- Delegate the visual read and the hostile audit to subagents. They read; they never
  decide. Every op still goes through `ops.json`.

## Your modules

List what you intend to work through, so a session can see the plan:

- (add yours)
