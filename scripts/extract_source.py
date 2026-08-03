#!/usr/bin/env python3
"""Inspect course module source and extract its content the best way available.

Accepts a single PDF, a FOLDER of PDFs, or a FOLDER containing any mix of
supported source files (PDFs, images, text files). If you captured a module as
several GoFullPage exports (one per sub-module), just put them in a folder named
for the module - do NOT merge them with an online tool. Merging through a cloud
service re-encodes the file, usually destroys the text layer, and uploads
copyrighted material to a third party. (Verified: the iLovePDF-merged
`Truncal.pdf` in the archive has a zero-word text layer, exactly like its
un-merged inputs. Merging rasters produces rasters.)

Supported source file types:
  - PDFs (.pdf)              -> existing pipeline (text layer or OCR)
  - Images (.jpg .png .gif   -> wrapped in a single-page PDF via Pillow,
    .bmp .tiff .webp)           then processed through OCR/coverage pipeline.
                                Transparent PNGs are composited onto white.
  - Text files (.txt .md     -> read directly into content.txt, no OCR needed.
    .csv .rtf)

Handles all three source shapes:
  1. Real text layer            -> extract text directly. NO OCR. Numbers exact.
  2. Image-only, normal pages   -> rasterize + OCR each page.
  3. Image-only, one tall page  -> rasterize + slice into overlapping strips + OCR.

RESUMABLE: checkpoints after every page. Re-run after an interruption and it
picks up where it stopped - Cowork kills long-running shell processes.

COMPLETENESS GATE: reads "Page N of M" footers and reports any missing pages,
so a partial capture is caught before any cards get written.

COVERAGE GATE
-------------
GoFullPage captures are always 100% raster, so OCR always runs and the prose is
always available as exact text. Re-sending those same prose pixels to be read
visually is the largest single cost in the pipeline and buys nothing.

Bucket B is: figures, diagrams, icons, collapsed-accordion controls, rules,
anything OCR read with low confidence, and - unconditionally - every token
containing a digit, a roman numeral, a dash, a percent or a unit, because
tesseract's confidence is least trustworthy exactly where Rule 9 cares most.

All of this is proved, not guessed: `unaccounted_ink_px` is reported per page
and must be 0 or the page is promoted to a whole-page read.

The coverage gate applies identically to stripped bands - the composed sheet
carries the same retained regions at the same dpi as the source, just without
the elided prose in between. Every figure, every icon, every ambiguous glyph is
visually available to the session. Only the prose whose text is already held
exactly is omitted.

Strip processing  (GoFullPage tall captures)
---------------------------------------------
A GoFullPage capture of a scrollable page is a single extremely tall image. At
OCR resolution the full-page image can exceed tesseract's memory limit, so the
strip pipeline slices it into STRIP_HEIGHT-px strips with STRIP_OVERLAP-px
overlap and OCRs each one independently. Banding then operates per strip in the
same reading order, one image per page as before.

Usage:
    python3 extract_source.py "<module.pdf | module_folder>" [output_dir]

Env knobs (all optional):
    OCR_DPI=150        rasterization dpi; also the dpi of every pixel you read
    CONF_OK=60         tesseract confidence at/above which a word counts as exact
    COVERAGE=strict    strict (default) | page
                       `page` disables banding and emits whole pages, i.e. the
                       pre-2026-08 behaviour. Use it to reproduce an old run, or
                       any time you distrust the gate.
"""

import os, sys, subprocess, re, json, glob, csv, io, math
from deps import require

require("PIL", "numpy", cli=["pdftotext", "pdftoppm", "pdfinfo"], quiet=True)
from PIL import Image, ImageFilter, ImageDraw
import numpy as np
Image.MAX_IMAGE_PIXELS = None

TEXT_LAYER_MIN_WORDS = 50
STRIP_HEIGHT, STRIP_OVERLAP, TALL_RATIO = 1600, 120, 3.0

OCR_DPI  = int(os.environ.get("OCR_DPI", "150"))
CONF_OK  = float(os.environ.get("CONF_OK", "60"))
COVERAGE = os.environ.get("COVERAGE", "strict").lower()

# Supported non-PDF source file types. PDFs are always accepted.
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp"}
TEXT_EXTS  = {".txt", ".md", ".csv", ".rtf"}

INK_LEVEL   = 238    # <= this grey value counts as ink
EDGE_LEVEL  = 24     # local gradient above which a pixel is textured (glyph/art)
FRINGE_PX   = 2      # dilation of accounted boxes that absorbs antialias fringe
BAND_PAD    = 10     # px of context above/below every retained band
BAND_GAP    = 18     # merge bands closer together than this
SEP_H       = 6      # height of the elision marker drawn between bands
MAX_BANDS   = 40     # more than this and the page is too fragmented to compose

# --- bucket C: typographic furniture. Residual ink that provably carries no
# content. Both rules are deliberately narrow and both are REPORTED per page
# with pixel counts, so this is an auditable classification, not a silent drop.
# If bucket C ever exceeds FURNITURE_MAX_FRAC of the page's informative ink the
# page is promoted to a whole-page read, so it cannot become a hiding place.
RULE_MIN_WIDTH_FRAC = 0.40   # a horizontal run this wide...
RULE_MAX_HEIGHT     = 6      # ...and this thin is a border/underline, not art
FRINGE_ROW_PX       = 30     # a row with fewer residual px than this is fringe
FRINGE_BAND_PX      = 150    # ...and a band of only such rows is antialiasing
FURNITURE_MAX_FRAC  = 0.15   # loose backstop only - see note below

# The real safety of bucket C is STRUCTURAL, not statistical. C1 can only ever
# take a run <= RULE_MAX_HEIGHT px tall (0.04 inch at 150 dpi - nothing legible
# or diagrammatic is that thin). C2 can only ever take a band in which no single
# row holds more than FRINGE_ROW_PX residual pixels, and a figure has hundreds
# per row for dozens of rows. Neither rule can swallow a figure, a label, or a
# glyph. FURNITURE_MAX_FRAC exists only to catch a pathological page where the
# rules misfire wholesale; it is not the guarantee.

# Tokens whose OCR must be eyeballed even at high confidence. Tesseract's
# confidence is least reliable on digits, dashes and units - the Rule 9 targets.
# A token containing a digit, a standalone unit, or a multi-character roman
# numeral. Deliberately NOT every hyphen and NOT single letters I/V/C/L/M -
# those matched half the prose on the page and dragged it all onto the read
# list for no gain. A range like "3-6" is caught by its digits.
NUMERICISH = re.compile(
    r"[0-9]"
    r"|\b(?![a-z])[IVXLCDM]{2,}\b"
    r"|^(mg|mcg|ug|ml|mL|cc|cm|mm|kg|kPa|mmHg|cmH2O|psi|mA|Hz|Fr|ga|%)$")


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def image_to_pdf(img_path, out_dir):
    """Wrap a standalone image in a single-page PDF for the OCR pipeline.

    Lossless: exact pixels, no re-encoding, native resolution preserved.
    Transparent PNGs are composited onto a white canvas so OCR sees dark
    text on white, not dark text on black."""
    im = Image.open(img_path)
    # Prevent transparent backgrounds from turning black on convert('RGB')
    if im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info):
        alpha = im.convert('RGBA').split()[-1]
        bg = Image.new('RGB', im.size, (255, 255, 255))
        bg.paste(im, mask=alpha)
        im = bg
    else:
        im = im.convert("RGB")
    stem = os.path.splitext(os.path.basename(img_path))[0]
    pdf_path = os.path.join(out_dir, f"{stem}.pdf")
    if not os.path.exists(pdf_path):
        # 300 DPI fallback is safer for OCR than 150 when native DPI is unknown
        dpi = im.info.get("dpi", (300, 300))
        if isinstance(dpi, tuple):
            dpi = dpi[0]
        im.save(pdf_path, "PDF", resolution=dpi)
    return pdf_path


def collect_sources(src):
    """Collect all supported source files. Returns (pdf_list, text_parts).

    - PDFs are returned as-is.
    - Images (.jpg, .png, etc.) are wrapped in single-page PDFs and added
      to the PDF list for processing through the OCR/coverage pipeline.
    - Text files (.txt, .md, .csv) are read directly and returned as text.

    For backwards compatibility, also accepts a single PDF file path."""
    if not os.path.isdir(src):
        # Single file path - must be a PDF (original behaviour)
        return [src], []

    convert_dir = os.path.join(src, ".converted")
    pdfs, text_parts = [], []
    images_found, texts_found = 0, 0

    for f in sorted(os.listdir(src), key=natural_key):
        p = os.path.join(src, f)
        if not os.path.isfile(p):
            continue
        ext = os.path.splitext(f)[1].lower()

        if ext == ".pdf":
            pdfs.append(p)
        elif ext in IMAGE_EXTS:
            os.makedirs(convert_dir, exist_ok=True)
            pdf = image_to_pdf(p, convert_dir)
            pdfs.append(pdf)
            images_found += 1
            print(f"  [convert] {f} -> {os.path.basename(pdf)}")
        elif ext in TEXT_EXTS:
            try:
                text = open(p, encoding="utf-8", errors="replace").read()
                text_parts.append(f"\n--- {f} ---\n{text}")
                texts_found += 1
                print(f"  [text]    {f} ({len(text.split())} words)")
            except Exception as e:
                print(f"  !! could not read {f}: {e}")

    if not pdfs and not text_parts:
        sys.exit(f"No supported source files in folder: {src}\n"
                 f"  Supported: .pdf, {' '.join(sorted(IMAGE_EXTS))}, "
                 f"{' '.join(sorted(TEXT_EXTS))}")

    if images_found:
        print(f"  {images_found} image(s) wrapped as PDF for OCR pipeline")
    if texts_found:
        print(f"  {texts_found} text file(s) read directly")

    return sorted(pdfs, key=natural_key), text_parts


def pdf_info(p):
    out = subprocess.run(["pdfinfo", p], capture_output=True, text=True).stdout
    pg = re.search(r"Pages:\s+(\d+)", out)
    sz = re.search(r"Page size:\s+([\d.]+) x ([\d.]+)", out)
    return {"pages": int(pg.group(1)) if pg else 0,
            "width": float(sz.group(1)) if sz else 0,
            "height": float(sz.group(2)) if sz else 0}


def text_of(p):
    r = subprocess.run(["pdftotext", "-layout", p, "-"], capture_output=True, text=True)
    return r.stdout


def slice_tall(img, outdir, tag):
    im = Image.open(img); w, h = im.size
    made, y, i = [], 0, 1
    while y < h:
        bot = min(y + STRIP_HEIGHT, h)
        q = os.path.join(outdir, f"{tag}-strip-{i:03d}.png")
        if not os.path.exists(q):
            im.crop((0, y, w, bot)).save(q)
        made.append(q)
        if bot >= h: break
        y = bot - STRIP_OVERLAP; i += 1
    return made


def valid_png(p):
    """A run killed mid-rasterize leaves 0-byte or truncated PNGs behind."""
    try:
        if os.path.getsize(p) == 0:
            return False
        im = Image.open(p); im.load()
        return True
    except Exception:
        return False


# "Page 4 of 39" as tesseract actually renders it. It reliably reads the "o"
# of "of" as a zero and sometimes the "f" as t/l/1, so the strict spelling
# matched almost nothing - which is why every module so far reported a bogus
# "captured 4 of 31 course pages" alarm. The capture was fine; the regex was not.
PAGE_FOOTER = re.compile(r"Pa[gq]e\s*(\d{1,3})\s*[o0O@][fftl1]\s*(\d{1,3})", re.I)


def completeness(text):
    """Parse 'Page N of M' footers. Returns (found, total, missing)."""
    hits = PAGE_FOOTER.findall(text)
    if not hits:
        return None, None, None
    found = {int(a) for a, _ in hits}
    total = max(int(b) for _, b in hits)
    return found, total, sorted(set(range(1, total + 1)) - found)


def vis_tokens(w, h):
    """Exact Claude visual-token cost of an image before any downscale.

    Claude sees images as 28x28 patches: ceil(w/28) * ceil(h/28), capped by the
    model's resolution tier (2576 px long edge / 4784 tokens on the
    high-resolution tier that Opus 5 uses). Everything emitted here is under the
    cap, so this is exact rather than an estimate.
    Source: platform.claude.com/docs/en/build-with-claude/vision
    """
    return math.ceil(w / 28) * math.ceil(h / 28)


# ---------------------------------------------------------------- OCR + boxes

def ocr_page(img, workdir):
    """One tesseract pass, two outputs: the prose text and the word boxes.

    `--psm 1` and the engine defaults are exactly what the pre-coverage pipeline
    used, so content_ocr.txt is byte-for-byte what it always was - asserted by
    selftest.py::test_ocr_text_byte_identical. Asking for `txt tsv` together
    costs one layout analysis instead of two, which is why adding the coverage
    gate did not make extraction slower."""
    base = os.path.join(workdir, "_ocr_" + os.path.basename(img).rsplit(".", 1)[0])
    subprocess.run(["tesseract", img, base, "--psm", "1", "txt", "tsv"],
                   capture_output=True, text=True)
    txt = tsv = ""
    try:
        txt = open(base + ".txt", encoding="utf-8", errors="replace").read()
    except OSError:
        pass
    try:
        tsv = open(base + ".tsv", encoding="utf-8", errors="replace").read()
    except OSError:
        pass
    for ext in (".txt", ".tsv"):
        try: os.remove(base + ext)
        except OSError: pass
    return txt, parse_boxes(tsv)


def parse_boxes(tsv):
    """Word boxes + confidence, used for the coverage proof only.

    This never touches the extracted text. If it returns nothing the caller
    reads the whole page - a parse failure must not silently shrink the read."""
    words = []
    try:
        for row in csv.DictReader(io.StringIO(tsv), delimiter="\t",
                                  quoting=csv.QUOTE_NONE):
            txt = (row.get("text") or "").strip()
            if not txt:
                continue
            try:
                conf = float(row["conf"])
                l, t = int(row["left"]), int(row["top"])
                w, h = int(row["width"]), int(row["height"])
            except (ValueError, KeyError, TypeError):
                continue
            words.append({"text": txt, "conf": conf, "box": (l, t, w, h)})
    except Exception:
        return []
    return words


# ------------------------------------------------------- page decomposition

def _dilate(mask, radius):
    """Binary dilation via PIL MaxFilter - avoids a scipy dependency."""
    if radius <= 0:
        return mask
    img = Image.fromarray((mask * 255).astype(np.uint8), mode="L")
    return np.asarray(img.filter(ImageFilter.MaxFilter(radius * 2 + 1))) > 127


def _bands_from_rows(rows, H, pad, gap):
    """Turn a boolean per-row mask into merged, padded (y0, y1) bands."""
    ys = np.where(rows)[0]
    if len(ys) == 0:
        return []
    runs, start, prev = [], int(ys[0]), int(ys[0])
    for y in ys[1:]:
        y = int(y)
        if y - prev > gap:
            runs.append([start, prev]); start = y
        prev = y
    runs.append([start, prev])
    out = []
    for y0, y1 in runs:
        y0, y1 = max(0, y0 - pad), min(H, y1 + pad + 1)
        if out and y0 <= out[-1][1]:
            out[-1][1] = max(out[-1][1], y1)
        else:
            out.append([y0, y1])
    return out


def _merge_bands(bands):
    """Merge overlapping (y0, y1) bands, in order."""
    out = []
    for b in sorted(bands):
        if out and b[0] <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b[1])
        else:
            out.append(list(b))
    return out


def analyze_page(img_path, words):
    """Split the page into 'text we already hold exactly' vs 'needs eyes'.

    Returns (bands, num_bands, stats), all full-width full-resolution y-ranges.

      bands      MANDATORY read. Figures, diagrams, icons, rules, collapsed
                 accordion controls, and every word OCR was not confident about.
      num_bands  RULE 9 read. Lines carrying a digit, a unit or a roman numeral.
                 Split out because they are read during the Rule 9 / verify_items
                 pass, where comparing the transcription to the pixels is the
                 point - not during the orientation read, where they are just
                 prose you already hold.

    stats['unaccounted_ink_px'] is 0 iff every informative ink pixel is either
    inside a high-confidence word box or inside bands ∪ num_bands. That equality
    is the entire no-loss guarantee - asserted here, not assumed. Note it is
    proved against the UNION, so skipping the numbers sheet is a deliberate
    deferral of a known list, never an unknown gap."""
    im = Image.open(img_path).convert("RGB")
    W, H = im.size
    grey = np.asarray(im.convert("L"))
    ink = grey <= INK_LEVEL

    # Flat fill (solid banners, coloured section headers, page background)
    # carries styling, not information. Texture = local gradient = glyph or art.
    edges = np.asarray(im.convert("L").filter(ImageFilter.FIND_EDGES))
    informative = ink & (edges > EDGE_LEVEL)

    # --- bucket A: pixels whose text we already hold exactly
    accounted = np.zeros((H, W), bool)
    needs_eyes = np.zeros((H, W), bool)
    numeric = np.zeros((H, W), bool)
    n_low = n_num = 0
    for wd in words:
        l, t, w, h = wd["box"]
        l0, t0 = max(0, l - 2), max(0, t - 2)
        l1, t1 = min(W, l + w + 2), min(H, t + h + 2)
        if wd["conf"] < CONF_OK:
            needs_eyes[t0:t1, l0:l1] = True          # OCR is unsure - look now
            n_low += 1
        else:
            accounted[t0:t1, l0:l1] = True
            if NUMERICISH.search(wd["text"]):        # Rule 9 - look at verify time
                numeric[t0:t1, l0:l1] = True
                n_num += 1

    # --- bucket B pass 1: informative ink no word box explains (figures, icons,
    #     rules, collapsed-accordion controls, anything OCR never saw)
    explained = _dilate(accounted, FRINGE_PX)
    residual = informative & ~explained

    # --- bucket C: strip typographic furniture out of the residual first, and
    #     record exactly what and how much, so it is auditable.
    furniture = np.zeros((H, W), bool)
    per_row = residual.sum(axis=1)

    # C1: hairline rules - section-banner borders, heading underlines, table
    #     rules. Wide and only a few px tall. No figure is 6 px tall.
    wide = per_row >= RULE_MIN_WIDTH_FRAC * W
    y = 0
    while y < H:
        if wide[y]:
            y2 = y
            while y2 + 1 < H and wide[y2 + 1]:
                y2 += 1
            if y2 - y + 1 <= RULE_MAX_HEIGHT:
                furniture[y:y2 + 1, :] |= residual[y:y2 + 1, :]
            y = y2 + 1
        else:
            y += 1

    # C2: antialias fringe - the sub-glyph halo around text that no dilation
    #     radius catches cleanly. Only whole bands that contain no substantial
    #     row at all, so a real mark next to text is never swallowed.
    rest = residual & ~furniture
    for y0, y1 in _bands_from_rows(rest.any(axis=1), H, BAND_PAD, BAND_GAP):
        blk = rest[y0:y1]
        if int(blk.sum()) < FRINGE_BAND_PX and not (blk.sum(axis=1) > FRINGE_ROW_PX).any():
            furniture[y0:y1, :] |= rest[y0:y1, :]

    furniture_px = int(furniture.sum())
    residual = residual & ~furniture
    needs_eyes |= residual

    bands = _bands_from_rows(needs_eyes.any(axis=1), H, BAND_PAD, BAND_GAP)

    # --- pass 2: sweep. Anything still outside a band becomes its own band, so
    #     unaccounted is 0 by construction rather than by threshold.
    covered_rows = np.zeros(H, bool)
    for y0, y1 in bands:
        covered_rows[y0:y1] = True
    leftover_rows = residual.any(axis=1) & ~covered_rows
    if leftover_rows.any():
        bands = _merge_bands(bands + _bands_from_rows(leftover_rows, H,
                                                     BAND_PAD, BAND_GAP))

    # --- numeric lines that the mandatory bands do not already contain
    covered_rows = np.zeros(H, bool)
    for y0, y1 in bands:
        covered_rows[y0:y1] = True
    num_bands = _bands_from_rows(numeric.any(axis=1) & ~covered_rows, H,
                                 BAND_PAD, BAND_GAP)

    # --- the proof, against the union of both sheets plus bucket C
    covered = accounted | furniture
    for y0, y1 in bands + num_bands:
        covered[y0:y1, :] = True
    unaccounted = int((informative & ~_dilate(covered, FRINGE_PX)).sum())

    def sheet_h(bs):
        return sum(b - a for a, b in bs) + max(0, len(bs) - 1) * SEP_H

    stats = {
        "page_px": [W, H],
        "page_tokens_if_read_whole": vis_tokens(W, H),
        "sheet_tokens": vis_tokens(W, sheet_h(bands)) if bands else 0,
        "numbers_tokens": vis_tokens(W, sheet_h(num_bands)) if num_bands else 0,
        "bands": len(bands),
        "num_bands": len(num_bands),
        "band_px": sum(b - a for a, b in bands),
        "words_total": len(words),
        "words_low_conf": n_low,
        "words_numeric": n_num,
        "informative_ink_px": int(informative.sum()),
        "furniture_px": furniture_px,
        "furniture_frac": round(furniture_px / max(int(informative.sum()), 1), 4),
        "unaccounted_ink_px": unaccounted,
    }
    return bands, num_bands, stats


def compose_sheet(img_path, bands, dest):
    """Stack the retained bands into one full-width image in reading order, with
    a marker rule where prose was elided. That prose is in content_ocr.txt
    verbatim - the rule says it was omitted on purpose, not lost."""
    im = Image.open(img_path).convert("RGB")
    W, _ = im.size
    total = sum(y1 - y0 for y0, y1 in bands) + max(0, len(bands) - 1) * SEP_H
    sheet = Image.new("RGB", (W, total), "white")
    d = ImageDraw.Draw(sheet)
    y = 0
    for i, (y0, y1) in enumerate(bands):
        sheet.paste(im.crop((0, y0, W, y1)), (0, y))
        y += y1 - y0
        if i < len(bands) - 1:
            d.rectangle([0, y + 2, W, y + 3], fill=(205, 210, 218))
            y += SEP_H
    sheet.save(dest)
    return dest


# --------------------------------------------------------------------- plan

def build_read_plan(img, words, sheets_dir, tag):
    """Decide how this page gets read and cut the sheets.

    Every early return is a promotion to a whole-page read. No branch reads less
    than the whole page without a proved-zero coverage gate."""
    whole = {"mode": "page", "targets": [img], "verify_targets": []}
    try:
        W, H = Image.open(img).size
    except Exception:
        W, H = 0, 0
    base = {"page_px": [W, H], "page_tokens_if_read_whole": vis_tokens(W, H),
            "sheet_tokens": 0, "numbers_tokens": 0, "bands": 0, "num_bands": 0,
            "unaccounted_ink_px": 0}

    if COVERAGE == "page":
        return whole, {**base, "reason": "COVERAGE=page (banding disabled)"}
    if not words:
        # A page with no OCR words is either blank (nothing to read) or a page
        # OCR failed on entirely (must be read whole). Tell them apart by ink.
        try:
            im = Image.open(img).convert("L")
            edges = np.asarray(im.filter(ImageFilter.FIND_EDGES))
            ink_px = int(((np.asarray(im) <= INK_LEVEL) & (edges > EDGE_LEVEL)).sum())
        except Exception:
            ink_px = -1
        if ink_px == 0:
            return ({"mode": "none", "targets": [], "verify_targets": []},
                    {**base, "informative_ink_px": 0,
                     "reason": "blank page - no informative ink"})
        return whole, {**base, "informative_ink_px": ink_px,
                       "reason": "no OCR word boxes - coverage unprovable"}

    try:
        bands, num_bands, stats = analyze_page(img, words)
    except Exception as e:
        return whole, {**base, "reason": f"coverage analysis failed ({e})"}

    if stats["unaccounted_ink_px"] > 0:
        return whole, {**stats, "reason": f"{stats['unaccounted_ink_px']} px of "
                       f"informative ink outside every band"}
    if stats["furniture_frac"] > FURNITURE_MAX_FRAC:
        return whole, {**stats, "reason": f"typographic-furniture bucket is "
                       f"{100 * stats['furniture_frac']:.1f}% of informative ink "
                       f"(cap {100 * FURNITURE_MAX_FRAC:.0f}%) - not trusted"}
    if len(bands) + len(num_bands) > MAX_BANDS:
        return whole, {**stats, "reason": f"{len(bands) + len(num_bands)} bands - "
                       f"too fragmented to compose"}
    if stats["sheet_tokens"] >= stats["page_tokens_if_read_whole"] * 0.85:
        return whole, {**stats, "reason": "figure dense; the sheet costs as much "
                       "as the page"}

    os.makedirs(sheets_dir, exist_ok=True)
    targets, verify = [], []
    if bands:
        dest = os.path.join(sheets_dir, f"{tag}-sheet.png")
        if not os.path.exists(dest):
            compose_sheet(img, bands, dest)
        targets.append(dest)
    if num_bands:
        dest = os.path.join(sheets_dir, f"{tag}-numbers.png")
        if not os.path.exists(dest):
            compose_sheet(img, num_bands, dest)
        verify.append(dest)
    mode = "sheet" if targets else ("numbers-only" if verify else "none")
    return ({"mode": mode, "targets": targets, "verify_targets": verify,
             "bands": bands, "num_bands": num_bands}, stats)


def plan_is_stale(plan):
    """A cached plan whose images were reclaimed must be rebuilt, not trusted."""
    return any(not os.path.exists(t) for t in
               plan.get("targets", []) + plan.get("verify_targets", []))


# --------------------------------------------------------------------- main

def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: python3 extract_source.py <module.pdf | module_folder> [outdir]")
    src = sys.argv[1]
    if not os.path.exists(src): sys.exit(f"Not found: {src}")
    outdir = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(src)[0] + "_extracted"
    os.makedirs(outdir, exist_ok=True)

    mpath = os.path.join(outdir, "manifest.json")
    man = json.load(open(mpath)) if os.path.exists(mpath) else {"done": [], "parts": []}
    man.setdefault("plans", {})
    def save(): json.dump(man, open(mpath, "w"), indent=1)

    print("=== COLLECTING SOURCES ===")
    pdfs, text_parts = collect_sources(src)
    if pdfs:
        print(f"\n=== SOURCE: {len(pdfs)} PDF(s) ===")
        for p in pdfs: print(f"  {os.path.basename(p)}")

    all_text, ocr_used, total_pages = [], False, 0
    pages_dir = os.path.join(outdir, "pages"); os.makedirs(pages_dir, exist_ok=True)
    sheets_dir = os.path.join(outdir, "read")
    read_targets, verify_targets, page_stats = [], [], []

    for idx, pdf in enumerate(pdfs, 1):
        tag = f"p{idx:02d}"
        info = pdf_info(pdf); total_pages += info["pages"]
        txt = text_of(pdf); nw = len(txt.split())
        tall = info["pages"] == 1 and info["height"] > info["width"] * TALL_RATIO
        print(f"\n[{tag}] {os.path.basename(pdf)}")
        print(f"     pages={info['pages']}  size={info['width']:.0f}x{info['height']:.0f}pt"
              f"  words={nw}  {'TALL CAPTURE' if tall else ''}")

        if nw >= TEXT_LAYER_MIN_WORDS:
            print("     >> text layer present - extracting directly, NO OCR")
            all_text.append(txt)
            continue

        ocr_used = True
        print("     >> no text layer - OCR fallback")
        require("pytesseract", cli=["tesseract"], quiet=True)
        stem = os.path.join(pages_dir, tag)

        def raster_pages():
            return [q for q in sorted(glob.glob(stem + "-*.png"), key=natural_key)
                    if "-strip-" not in os.path.basename(q)]

        raw = raster_pages()
        corrupt = [q for q in raw if not valid_png(q)]
        short = (not tall) and len(raw) != info["pages"]
        if not raw or corrupt or short:
            if raw:
                print(f"     !! bad rasterization ({len(raw)}/{info['pages']} pages, "
                      f"{len(corrupt)} unreadable) - re-rasterizing this capture")
                stale = set(glob.glob(stem + "-*.png"))
                for q in stale:
                    os.remove(q)
                man["parts"] = [x for x in man["parts"] if x["img"] not in stale]
                man["done"] = [x for x in man["done"] if x not in stale]
                man["plans"] = {k: v for k, v in man["plans"].items() if k not in stale}
                save()
            subprocess.run(["pdftoppm", "-png", "-r", str(OCR_DPI), pdf, stem], check=True)
        imgs = raster_pages()

        if tall and len(imgs) == 1:
            imgs = slice_tall(imgs[0], pages_dir, tag)
            print(f"     sliced into {len(imgs)} strips")

        for n, im in enumerate(imgs, 1):
            cached = man["plans"].get(im)
            need_ocr = im not in man["done"]
            need_plan = cached is None or plan_is_stale(cached["plan"])
            if need_ocr or need_plan:
                text, words = ocr_page(im, pages_dir)   # one pass, both outputs
                if need_ocr:
                    man["parts"].append({"img": im, "text": text})
                    man["done"].append(im)
                plan, stats = build_read_plan(im, words, sheets_dir, f"{tag}-{n:03d}")
                man["plans"][im] = {"plan": plan, "stats": stats}
                save()                                  # checkpoint every page
            entry = man["plans"][im]
            read_targets += entry["plan"]["targets"]
            verify_targets += entry["plan"].get("verify_targets", [])
            page_stats.append({"img": os.path.basename(im),
                               "mode": entry["plan"]["mode"], **entry["stats"]})
        print(f"     OCR complete: {len(imgs)} image(s)")

    # Sort by page path, not by the order pages happened to be OCR'd. `parts` is
    # appended as work completes, so a run resumed after an interruption used to
    # emit the same bytes in a different order - which silently reorders the
    # module's content and makes two extractions of one capture non-comparable.
    for part in sorted(man["parts"], key=lambda x: natural_key(x["img"])):
        all_text.append(f"\n--- {os.path.basename(part['img'])} ---\n{part['text']}")

    # Append any text files that were read directly (not OCR'd)
    all_text.extend(text_parts)

    joined = "".join(all_text)
    content = os.path.join(outdir, "content_ocr.txt" if ocr_used else "content.txt")
    open(content, "w", encoding="utf-8").write(joined)

    found, total, missing = completeness(joined)
    print("\n=== COMPLETENESS ===")
    if found is None:
        print("  no 'Page N of M' footers found - cannot verify capture completeness")
    elif missing:
        print(f"  !! INCOMPLETE: captured {len(found)}/{total} course pages")
        print(f"  !! MISSING: {missing}")
        print("  !! Gap-fill can only be as complete as the source. Re-capture before proceeding.")
    else:
        print(f"  complete: all {total} course pages present")

    # ------------------------------------------------------- coverage summary
    baseline = sum(s.get("page_tokens_if_read_whole", 0) for s in page_stats)
    mand = sum(s["page_tokens_if_read_whole"] if s["mode"] == "page"
               else s.get("sheet_tokens", 0) for s in page_stats)
    nums = sum(0 if s["mode"] == "page" else s.get("numbers_tokens", 0)
               for s in page_stats)
    unacc = sum(s.get("unaccounted_ink_px", 0) for s in page_stats
                if s["mode"] != "page")
    whole = [s for s in page_stats if s["mode"] == "page"]

    if page_stats:
        pct = lambda v: 100 * (1 - v / max(baseline, 1))
        print("\n=== VISUAL READ PLAN ===")
        print(f"  pages analysed             : {len(page_stats)}")
        print(f"  read as composed sheets    : {sum(1 for s in page_stats if s['mode']=='sheet')}")
        print(f"  read as whole pages        : {len(whole)}")
        print(f"  nothing to look at         : {sum(1 for s in page_stats if s['mode']=='none')}")
        print(f"  unaccounted informative ink: {unacc}   "
              f"{'OK - nothing is skipped' if unacc == 0 else '!! MUST BE 0'}")
        print()
        print(f"  whole pages (old plan)     : {baseline:>8,} tok")
        print(f"  STEP 3 orientation read    : {mand:>8,} tok   "
              f"({pct(mand):.0f}% less)   {len(read_targets)} images")
        print(f"  + Rule 9 numbers read      : {nums:>8,} tok   "
              f"({len(verify_targets)} images, read during pass 1 / verify_items)")
        print(f"  = both, if you read all    : {mand + nums:>8,} tok   "
              f"({pct(mand + nums):.0f}% less)")
        if whole:
            print("  promoted to whole-page read:")
            for s in whole[:15]:
                print(f"     {s['img']:16s} {s.get('reason','')}")

    report = {"source": src, "pdf_count": len(pdfs), "pdf_pages": total_pages,
              "ocr_used": ocr_used, "content": content,
              "read_targets": read_targets,
              "verify_targets": verify_targets,
              "course_pages_found": sorted(found) if found else None,
              "course_pages_total": total, "course_pages_missing": missing,
              "coverage_mode": COVERAGE, "ocr_dpi": OCR_DPI, "conf_ok": CONF_OK,
              "pages_analysed": len(page_stats),
              "pages_read_whole": [s["img"] for s in whole],
              "unaccounted_ink_px": unacc,
              "visual_tokens_baseline": baseline,
              "visual_tokens_plan": mand,
              "visual_tokens_numbers": nums,
              "page_stats": page_stats}
    json.dump(report, open(os.path.join(outdir, "extract_report.json"), "w"), indent=2)

    print(f"\n  ocr_used: {ocr_used}"
          + ("   -> every number goes in the verification list" if ocr_used
             else "   -> numbers are exact, skip the verification section"))
    print(f"  wrote {content} ({len(joined.split())} words)")
    if read_targets:
        print(f"  read these visually, in order: {os.path.dirname(read_targets[0])}"
              f"  ({len(read_targets)} items, listed in extract_report.json)")
    if unacc:
        print("\n  !! COVERAGE FAILED - re-run with COVERAGE=page and read whole pages.")
        sys.exit(3)


if __name__ == "__main__":
    main()
