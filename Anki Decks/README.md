# Anki Decks — the decks you want optimized go here

Export from Anki as **`.apkg`** (Notes in Plain Text is not enough — the pipeline
needs the collection database for referential integrity, csum/sfld gates and the
accounting reconciliation).

Name each file to match its module folder in `Source Files/`:

```
Anki Decks/
  Regional - Truncal.apkg          <- pairs with "Source Files/Regional - Truncal/"
  Airway Anatomy and Management.apkg
```

A deck with **no matching module folder** is queued `optimize-only`: it gets the
full editorial rubric but no source gap-fill. That is a supported mode, not an error.

A module folder with **no matching deck** is `creation mode` — building a deck from
nothing. See `HANDOFF_REFERENCE.md` §1e before starting one.

Nothing in this folder is tracked by git.
