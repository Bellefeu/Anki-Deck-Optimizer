# Anki Deck Optimization — starter kit

**New here? Read [`START HERE.md`](START%20HERE.md).** It is a stepwise walkthrough with
every command and every prompt you need to copy, in order.

A pipeline for rebuilding study decks against a course module: it restructures
cards that test badly, fills gaps the module covers and the deck misses, verifies the
factual claims, and refuses to ship a deck that contradicts itself.

It is meant to be driven by an AI agent. You hand the agent a prompt file; the scripts
do everything that must be deterministic — database surgery, validation gates, the audit
trail — and the agent does the editorial judgement the scripts cannot.

**Easiest:** double-click `Prism Control Center - Windows.cmd` on Windows or
`Prism Control Center - Mac.command` on macOS. The local Prism dashboard displays the
complete Start Here guide with copyable prompts and handles setup, staging, preferences,
deck review, and safe updates. If macOS blocks the first launch,
follow the five picture-free steps at the top of [`START HERE.md`](START%20HERE.md).

**Impatient in a terminal?** `python3 scripts/bootstrap.py`

---

## What you need

**Anki**, an agent that can run shell commands and read images, and your own access to
the course you are studying. That is the whole list — the installer handles the rest.

Behind the scenes it needs Python 3.10+, Node.js, `poppler-utils` and `tesseract-ocr`.
You do not have to install any of those yourself.

## Five minutes to your first run

```bash
bash control_center/install/setup.sh  # macOS / Linux  - installs everything missing
.\control_center\install\setup.ps1    # Windows        - same, via winget
# then it runs bootstrap.py for you and prints what to do next

# edit PROFILE.md - it is short and it is yours

# capture a module (expand every accordion first), then:
#   Source Files/<module name>/*.pdf
#   Anki Decks/<module name>.apkg

cd scripts
python3 build_queue.py "../Source Files" "../Anki Decks"
python3 next_action.py --status
```

Then hand your agent `scripts/PROMPT_build.md`.

## Layout

```
Prism Control Center - *   local dashboard launchers; no terminal knowledge needed
START HERE.md              stepwise walkthrough. Start with this.
PROFILE.md                 your private runtime preferences (made by setup)
Source Files/              input: one folder of captures per module
Anki Decks/                input: one .apkg per module
COMPLETED/                 output: one folder per finished module
  EXAMPLE/                 a redacted audit trail - read this first
work/                      scratch, resumable, safe to delete
scripts/
  HANDOFF.md               the job. Read in full every session. §3b is the rubric.
  HANDOFF_REFERENCE.md     situational. Read a section on demand, never whole.
  PROMPT_build.md          hand this to your agent to process a module
  PROMPT_verify.md         verification, in a separate session
  PROMPT_patch.md          apply corrections from a verification report
  PROMPT_auto.md           one phase, unattended, on a schedule
  PROMPT_dedupe.md         cross-deck duplicate hunting
  bootstrap.py             run this first
  selftest.py              isolated safety checks. Run in any new environment.
control_center/            Prism dashboard, installer, templates, and safe patcher
```

## The four things that will save you a day each

1. **SQLite cannot run on a Google Drive / cloud FUSE mount.** Any write throws
   `disk I/O error`. Copy everything to local disk, work there, copy the finished
   output back. This is not a script bug and there is no workaround.

2. **A cloud mount lies about being empty.** An unhydrated folder reads as zero files.
   `build_queue.py` says so itself — believe it and stop, rather than concluding there
   is no work.

3. **Run `check_version.py` before diagnosing any bug.** Scripts drift between a Drive
   copy and a local copy, and you will otherwise spend an hour on a bug that was fixed
   last week.

4. **Never edit the deck database directly.** Every change goes through `ops.json`. The
   changelog is built from the ops, so a direct edit produces counts that do not
   reconcile — and the build fails on that mismatch by design.


## What makes it trustworthy rather than fast

The pipeline reads course prose as OCR text and reads *pixels* only for regions OCR
could not transcribe — figures, low-confidence words, and every line carrying a number.
That is a large saving, but it is only safe because it is proved rather than guessed:
`extract_source.py` asserts `unaccounted_ink_px == 0`, meaning every informative pixel on
every page is either inside a high-confidence OCR word box or on the visual read list.
Every failure branch hands over the whole page instead. Set `COVERAGE=page` to disable
the whole mechanism and read pages whole.

The same instinct runs through the rest: `selftest.py` runs more than 140 checks, including that the
OCR text is byte-identical to the pre-optimization pipeline; `handoff.py check` asserts
the documentation split lost no section; `build_deck.py` reconciles the card arithmetic
and fails on a mismatch.

## Licence, content, and a word about clinical accuracy

MIT — see [`LICENSE`](LICENSE). Use it, change it, share it.

**The MIT "AS IS, without warranty" clause is not boilerplate here.** This pipeline
rewrites clinical flashcards, and while it verifies every checkable claim against an
outside source and refuses to ship a deck that contradicts itself, it is a tool, not a
clinician. Nothing it produces has been reviewed by anyone but you. Read the JUDGEMENT
CALLS section of every NOTES document before you trust a deck, and never study from a
card whose provenance you have not checked. That review step is deliberately the one
thing automation is not allowed to do for you.

**No course material is included, and the `.gitignore` is an allowlist specifically to
keep it that way.** Course captures, source decks and finished `.apkg` files are ignored
by default; a new file is invisible to git until someone deliberately un-ignores it.
Use your own subscription and capture your own modules. The worked example in
`COMPLETED/EXAMPLE/` has real structure and invented content for exactly this reason.
