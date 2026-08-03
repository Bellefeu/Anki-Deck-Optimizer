# Source Files — your captured files go here

One **folder per file group**, named exactly how you want the module named everywhere
downstream. Inside it, the PDF captures for that module — as many as it took, in any
order; `extract_source.py` sorts them naturally.

```
Source Files/
  Airway Anatomy and Management/
    screencapture-...-18_34_13.pdf
    screencapture-...-18_35_20.pdf
  Pharmacology - Opioids/
    ...
```

A single `Module Name.pdf` also works if the whole module fit in one capture.

## Capturing well — this is what limits everything downstream

1. **Expand every accordion first.** A full-page capture records only what is rendered.
   A collapsed "References" panel, or any `+` disclosure, contributes its header and
   nothing else — and no amount of care later recovers text that was never captured.
2. **Capture every page.** The extractor parses `Page N of M` footers and reports which
   are missing, but it can only report on what you gave it.
3. **Do not merge the PDFs with an online tool.** Cloud merging re-encodes, destroys any
   text layer, and uploads copyrighted course material to a third party. This was
   measured, not assumed: a merged capture came back with a zero-word text layer,
   exactly like its inputs. Point the extractor at the folder instead. If you genuinely
   must merge, use `pdfunite` locally.
4. **Name the folder deliberately.** `build_queue.py` pairs a module folder here with a
   deck in `Anki Decks/` by name, so `Pharmacology - Opioids/` pairs with
   `Pharmacology - Opioids.apkg`.

## If your captures have a real text layer

Most browser full-page capture tools rasterize — they paint the page to a canvas, and
the text is gone before the PDF is written. If yours preserves text, the extractor
detects it automatically (`ocr_used: false`), skips OCR entirely, and your numbers are
exact rather than transcribed. Worth testing once:

```bash
pdftotext -layout "Source Files/<module>/<one capture>.pdf" - | wc -w
```

Anything above ~50 means you have a text layer and the pipeline gets meaningfully more
reliable for free.

---

These captures are your own course material. Nothing in this folder is tracked by git —
see `.gitignore` in the project root.
