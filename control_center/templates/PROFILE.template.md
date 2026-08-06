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

## Your source's name

Rule 8 says a card never names where it came from. The generic forms — "the module
states", "according to the course" — are always caught. If your course has a brand
name, set it here and every bare mention of it gets caught too:

- **SOURCE_NAME:** (leave blank, or put your course's name here)

Set it as an environment variable, or add `"source_name": "YourCourse"` to
`scripts/project_state.json`.

If that name also appears as an ordinary word in your material — a course called
"Crest" against "the iliac crest" — it is already handled. Extend
`FALSE_POSITIVE_BEFORE` / `FALSE_POSITIVE_AFTER` in `scripts/build_deck.py` if your
particular collision needs it.

## Your modules

List what you intend to work through, so a session can see the plan:

- (add yours)
