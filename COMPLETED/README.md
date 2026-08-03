# COMPLETED — finished modules land here, one folder each

Written by `build_deck.py`; you do not create anything here by hand.

```
COMPLETED/<module>/
  <module> (FINAL).apkg      import this into Anki
  <module> (FINAL).txt       one card per line, Text|Extra
  <module> (NOTES).docx      what changed, what to verify, IO card requests
  audit/                     ops.json, new_cards.json, meta.json, changelog.json,
                             extract_report.json, apex/content*.txt
```

**The `audit/` folder is the only record of *why* each card changed.** A previous
run lost it and its NOTES doc became the sole — and inaccurate — audit trail. Keep it.

See `EXAMPLE/` for a redacted real audit trail showing the shape of a finished run.

Nothing in this folder is tracked by git except `EXAMPLE/`.
