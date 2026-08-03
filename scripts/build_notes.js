const fs = require('fs');
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, ImageRun,
  AlignmentType, BorderStyle, PageBreak, Table, TableRow, TableCell,
  WidthType, ShadingType
} = require('docx');

// Everything module-specific comes from JSON the session writes. Nothing here is
// hand-edited per module - same contract as ops.json / new_cards.json.
const MODULE  = process.env.MODULE || process.argv[2];
if (!MODULE) { console.error('Usage: MODULE="<name>" node build_notes.js'); process.exit(1); }

const WORKROOT  = process.env.WORKROOT  || 'work';
const COMPLETED = process.env.COMPLETED_DIR || 'COMPLETED';
const MODDIR    = `${WORKROOT}/${MODULE}`;
const FIG       = process.env.FIG_DIR || `${MODDIR}/io_figs/`;

const rd = (f, dflt) => {
  try { return JSON.parse(fs.readFileSync(`${MODDIR}/${f}`, 'utf8')); }
  catch (e) { if (dflt !== undefined) return dflt; throw e; }
};

const changelog = rd('changelog.json', []);
const meta      = rd('meta.json', { outstanding: [], gaps_filled: [], summary: '' });
const cfg       = rd('notes_config.json', {});

// IO card requests come from notes_config.json:
//   { "source_label": "Truncal APEX", "source_pages": 30,
//     "cards_before": 150, "cards_after": 228,
//     "verify_items": [["Nerve root ranges","C5-C7 ...","why it's OCR-risky"], ...],
//     "io": [ { id, file, page, priority, topic, tests, whyImage, occlude, labels } ] }
const IO = cfg.io || [];

const H = (t, lvl) => new Paragraph({ text: t, heading: lvl, spacing: { before: 240, after: 120 } });
const P = (runs, opts = {}) => new Paragraph({ children: runs, spacing: { after: 100 }, ...opts });
const label = (t) => new TextRun({ text: t, bold: true, color: '1F3864' });
const body  = (t) => new TextRun({ text: t });

const children = [];

// ---------- title ----------
children.push(new Paragraph({
  text: `${MODULE} — Manual Action Items`,
  heading: HeadingLevel.TITLE,
  alignment: AlignmentType.CENTER,
}));
children.push(P([new TextRun({ text: `Companion to "${MODULE} (FINAL).apkg" and ".txt"`, italics: true, color: '666666' })],
  { alignment: AlignmentType.CENTER }));
const adds0 = changelog.filter(c => c[0] === 'ADD').length;
const ops0  = changelog.filter(c => ['EDIT','REWRITE','SPLIT','DEMOTE','DELETE'].includes(c[0])).length;
children.push(P([body(
  `Source: ${cfg.source_label || 'lecture material'}`
  + (cfg.source_pages ? ` (${cfg.source_pages} pages)` : '')
  + `. Deck: ${cfg.cards_before ?? '?'} existing + ${adds0} new = ${cfg.cards_after ?? '?'} total, ${ops0} restructuring operation(s).`)],
  { alignment: AlignmentType.CENTER }));

// ---------- section 1: IO requests ----------
children.push(new Paragraph({ children: [new PageBreak()] }));
children.push(H('Section 1 — Image Occlusion cards to create manually', HeadingLevel.HEADING_1));
children.push(P([body('Each item below needs a card you build yourself using your own image. The Apex figure is reproduced beneath each entry '),
                 new TextRun({ text: 'purely as a visual reference', bold: true }),
                 body(' so you know exactly which view to recreate — it is not licensed for use in the cards themselves.')]));

IO.forEach((item) => {
  children.push(new Paragraph({ children: [new PageBreak()] }));
  children.push(H(`${item.id} — ${item.topic}`, HeadingLevel.HEADING_2));

  children.push(new Table({
    columnWidths: [2200, 7000],
    width: { size: 9200, type: WidthType.DXA },
    rows: [
      ['Priority', item.priority],
      ['Apex page', `p. ${item.page}`],
      ['What it tests', item.tests],
      ['Why an image', item.whyImage],
      ['Occlusion plan', item.occlude],
      ['Labels in figure', item.labels],
    ].map(([k, v]) => new TableRow({
      children: [
        new TableCell({
          width: { size: 2200, type: WidthType.DXA },
          shading: { type: ShadingType.CLEAR, fill: 'E8EEF7' },
          children: [P([label(k)])],
        }),
        new TableCell({
          width: { size: 7000, type: WidthType.DXA },
          children: [P([body(v)])],
        }),
      ],
    })),
  }));

  children.push(P([new TextRun({ text: 'Reference figure (Apex — do not use in card):', bold: true, size: 20, color: '888888' })],
    { spacing: { before: 200, after: 80 } }));

  const buf = fs.readFileSync(FIG + item.file);
  children.push(new Paragraph({
    children: [new ImageRun({ data: buf, type: 'png', transformation: { width: 560, height: Math.round(560 * 0.5) } })],
    alignment: AlignmentType.CENTER,
  }));
});

// ---------- section 2: changelog ----------
children.push(new Paragraph({ children: [new PageBreak()] }));
children.push(H('Section 2 — Change log', HeadingLevel.HEADING_1));

const OP_LABEL = {
  EDIT:      ['Edits to existing cards', 'surgical correction'],
  REWRITE:   ['Cards rewritten', 'both fields replaced'],
  SPLIT:     ['Cards split into atomic cards', 'laundry list broken up'],
  DEMOTE:    ['Cards demoted into Extra (Rule 0)', 'retired as a card, payload preserved'],
  DELETE:    ['Cards deleted', 'removed entirely'],
  ADD:       ['Cards added', 'new'],
  'DUPE-SKIP':['Skipped as duplicates', 'already present'],
  SKIP:      ['Operations that did NOT apply', 'REVIEW THESE'],
};
const byKind = {};
changelog.forEach(c => { (byKind[c[0]] = byKind[c[0]] || []).push(c); });

// Content-changing operations first - these are what the user must review.
['DEMOTE', 'DELETE', 'SPLIT', 'REWRITE', 'EDIT', 'ADD', 'DUPE-SKIP', 'SKIP'].forEach(kind => {
  const rows = byKind[kind] || [];
  if (!rows.length) return;
  const [title, note] = OP_LABEL[kind] || [kind, ''];
  children.push(H(`${title} (${rows.length})`, HeadingLevel.HEADING_2));
  if (kind === 'DEMOTE' || kind === 'DELETE' || kind === 'SKIP') {
    children.push(P([new TextRun({
      text: kind === 'SKIP'
        ? 'These operations were requested but did not apply. Each is a defect, not a decision.'
        : 'These changed or removed content. Confirm each was the right call.',
      bold: true, color: 'AA3333' })]));
  } else if (note) {
    children.push(P([new TextRun({ text: note, italics: true, color: '888888', size: 20 })]));
  }
  rows.forEach(([, nid, why], i) => {
    children.push(P([label(`${String(i + 1).padStart(2, '0')}. `),
                     ...(nid !== '-' ? [label(`note ${nid} — `)] : []),
                     body(String(why || ''))]));
  });
});

const edits = byKind.EDIT || [];
const adds  = byKind.ADD  || [];

// ---------- section 3: verification-needed items ----------
children.push(new Paragraph({ children: [new PageBreak()] }));
if (cfg.ocr_used !== false && (cfg.verify_items || []).length) {
  children.push(H('Section 3 — Items to verify before studying', HeadingLevel.HEADING_1));
  children.push(P([body('The source had no text layer, so its content was recovered by OCR. OCR is reliable for prose but misreads digits, en-dashes and decimals. Check the values below against your own copy before trusting them on a card.')]));
  (cfg.verify_items || []).forEach(([k, v, note]) => {
    children.push(H(k, HeadingLevel.HEADING_3));
    children.push(P([body(v)]));
    if (note) children.push(P([new TextRun({ text: note, italics: true, color: '888888', size: 20 })]));
  });
} else if (cfg.ocr_used === false) {
  children.push(H('Section 3 — OCR verification not required', HeadingLevel.HEADING_1));
  children.push(P([body('The source PDF had a real text layer, so every figure was extracted exactly rather than recognised. No OCR verification list is needed for this module.')]));
}

// Section 4 - open questions the build deliberately did not resolve
if ((meta.outstanding || []).length) {
  children.push(new Paragraph({ children: [new PageBreak()] }));
  children.push(H('Section 4 — Open items and unresolved conflicts', HeadingLevel.HEADING_1));
  meta.outstanding.forEach(o => children.push(P([label('• '), body(String(o))])));
}

const doc = new Document({
  sections: [{
    properties: { page: { size: { width: 12240, height: 15840 } } },
    children,
  }],
});

Packer.toBuffer(doc).then((buf) => {
  const outDir = `${COMPLETED}/${MODULE}`;
  fs.mkdirSync(outDir, { recursive: true });
  const out = `${outDir}/${MODULE} (NOTES).docx`;
  fs.writeFileSync(out, buf);
  console.log('Wrote', out, (buf.length / 1024).toFixed(0) + ' KB');
  console.log('IO requests:', IO.length, '| edits:', edits.length, '| adds:', adds.length);
});
