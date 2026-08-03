# EXAMPLE — what a finished module looks like

**Nothing here is real course material.** The structure, field names and semantics are
exactly what `build_deck.py` and `build_notes.js` read and write; the *content* is
invented so this can ship. Read it before your first build so you know the target.

The three deliverables a real module produces — `(FINAL).apkg`, `(FINAL).txt`,
`(NOTES).docx` — are not included, because they would be derived course content. The
audit trail is the part worth studying anyway: it is the only record of *why* each card
changed.

## `audit/ops.json` — every change goes through this file

Never edit the deck database directly. The changelog is built from the ops, so a direct
edit produces counts that do not reconcile, and the build now fails on that mismatch.

| op | what it does | when |
|---|---|---|
| `edit` | find/replace inside a field | small mechanical fixes; `find` must match the field's **real bytes**, including any HTML wrapping |
| `rewrite` | replace `text` and/or `extra` wholesale | the card's phrasing is wrong, not just a token |
| `split` | one note becomes several | Rule 1 — failing the card meant blanking one of several independent items |
| `demote` | move a claim from the front to `Extra` | Rule 13 — you could not confirm it, so it stops being tested |
| `delete` | remove the note | Rule 0 — genuinely no yield, or a verbatim duplicate |

Every op carries a `why`. That field is what makes the audit trail worth keeping.

**`edit` is the one that fails silently.** A fix targeting `LAST.` does nothing if the
card reads `<u><b>LAST</b></u> is a major concern`. The build asserts
`edits_applied == edits_intended` for exactly this reason — if it reports an EDIT
MISMATCH, your find-string lost to HTML wrapping.

## `audit/new_cards.json`
Cards you wrote that did not exist before. Check the claim index (`check_consistency.py`)
before adding any — if the deck already asserts something about that subject, you are
editing that card, not adding one.

## `audit/meta.json`
`outstanding` becomes §4 of the NOTES doc — the things a human still has to resolve.
`gaps_filled` and `summary` feed the status table. The card-count reconciliation line is
worth writing every time; splits create replacement notes that the `added` counter never
sees, and the arithmetic is how you catch that.

## `audit/changelog.json`
`[OP, nid, human-readable reason]`. Accumulates across a build and any later patch — a
patch that overwrites it instead of appending silently guts the NOTES doc, which has
happened.

## `audit/notes_config.json`
Input to `build_notes.js`. **Do not hand-edit `build_notes.js`.**
`verify_items` is where OCR-risky numbers go for a human to eyeball; if `ocr_used` is
`false` the whole section is omitted automatically. `io` entries request image-occlusion
cards — each needs its cropped source figure as a **visual reference only**, and those
figures must never go into a card.

## `audit/apex/extract_report.json`
Output of `extract_apex.py`. The fields that matter on every run:

- **`unaccounted_ink_px` must be `0`.** It asserts that every informative pixel on every
  page is either inside a high-confidence OCR word box or on the visual read list.
- `pages_read_whole` — pages the coverage gate could not prove and handed over intact.
  A short list is normal; a long one says something about the capture.
- `course_pages_missing` — pages your capture never contained.
- `visual_tokens_baseline` vs `visual_tokens_plan` — what reading every page whole would
  have cost, versus what this plan costs.
