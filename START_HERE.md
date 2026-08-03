# START HERE

Everything you need, in order. You do not need to understand the code.

You will be working in **Claude Cowork** (the desktop app). Every step below is either a
terminal command or a block of text you paste into Claude. Anything in a grey box is meant
to be copied exactly.

---

## PART 1 — ONE-TIME SETUP

### 1. Put this folder somewhere on your computer

Clone or download the repo. Anywhere is fine — Desktop, Documents, a Dropbox folder.

**One rule: it must be on your actual hard drive, not a cloud-only folder.** The pipeline
uses SQLite, and SQLite cannot write to a Google Drive / iCloud / OneDrive mount — every
write throws `disk I/O error`. This is not a bug in the scripts and there is no way around
it.

If you *do* want to keep it in Google Drive so it syncs across machines, that works, but
you must mark the folder **Available offline** first:

- Install Google Drive for desktop: <https://support.google.com/a/users/answer/13022292>
- Find the folder in Finder / File Explorer, right-click it, choose **Available offline**
- Drive sync glitches often. If Claude says a folder is empty when you can see files in
  it, pause and un-pause sync from the Drive menu bar icon. If that does not fix it, quit
  the Drive app completely and reopen it. That always forces a sync.

An unsynced folder reads as **empty**, not as an error — which looks exactly like "there is
no work to do". The scripts warn you about this, but know it going in.

### 2. Run the installer

Open a terminal **in the project folder** and run one line.

**macOS or Linux**

```
bash setup.sh
```

**Windows**

```
.\setup.ps1
```

That is it. It works out what you are missing — Python, poppler, tesseract, Node — and
installs only those. If you already have everything it skips straight through. It is safe
to run again any time.

A few things worth knowing:

- **It will ask for your password.** Installing system software requires it. The script
  prints every command before it runs, and never runs anything as administrator silently.
- **Want to see what it would do first?** `bash setup.sh --dry-run` prints the exact
  commands and changes nothing. Use that list to install by hand if you would rather.
- **macOS:** if you do not have Homebrew, it offers to install it. Say yes — it is how
  macOS gets these tools. Nothing else works without it.
- **Windows:** it uses `winget`, which ships with Windows 11 and recent Windows 10. If
  PowerShell refuses to run the script, run this once:
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
  Honestly, if you have WSL, installing WSL and using `bash setup.sh` inside it is the
  smoother path for this pipeline.
- **If it installs things and the next step still says they are missing:** close the
  terminal, open a new one, and run it again. A terminal that was already open does not
  see newly installed programs.

When it finishes installing it automatically runs the checks and self-tests, and prints
what to do next.

### 3. Confirm it says READY

The installer ends by running `scripts/bootstrap.py`, which checks everything and runs
~136 self-tests. It takes about a minute.

**It must end with `READY`.** If it says `NOT READY`, it prints exactly what is missing —
fix that and run `bash setup.sh` again. Do not skip ahead. Every problem it catches is one
that would otherwise surface halfway through a real deck.

You can re-run the checks alone at any time, without the installer:

```
python3 scripts/bootstrap.py
```

### 4. Open `PROFILE.md` and edit it

It is one page. It is your preferences — how aggressively to fix errors without asking,
whether you are keeping your Anki review history, how thorough to be. The defaults are
sensible; read them once so you know what you agreed to.

**The one to decide now:** the default **discards your review history**. If you want to
keep it, change that line before you build anything. It changes how a rebuilt deck must be
imported, and it is far cheaper to decide now than later.

### 5. Point Cowork at the folder

In Cowork, connect the project folder as your working folder. Everything after this
assumes Claude can see it.

---

## PART 2 — STAGE A MODULE

Two folders, two things to drop in. Names must match.

### Source captures → `Source Files/<module name>/`

Capture the course module as PDFs (GoFullPage or similar). One **folder per module**, named
however you want the module named everywhere else.

```
Source Files/
  Airway Anatomy and Management/
    screencapture-...-18_34_13.pdf
    screencapture-...-18_35_20.pdf
```

**Before you capture: expand every accordion on the page.** Any collapsed "References" or
`+` panel gets captured as a header and nothing else. Nothing downstream can recover text
that was never in the file. This is the single easiest way to silently lose content.

Capture every page. Do **not** merge the PDFs with an online tool — it destroys the text
layer and uploads your course material to a third party.

### Deck → `Anki Decks/<module name>.apkg`

Export from Anki as `.apkg`. The filename must match the folder name above.

```
Anki Decks/
  Airway Anatomy and Management.apkg
```

- Deck but no source folder → runs in **optimize-only** mode. Full editorial rubric, no
  gap-fill. That is supported, not an error.
- Source folder but no deck → **creation mode**, building a deck from scratch. Read
  `HANDOFF_REFERENCE.md` §1e first.

Stage as many modules as you like before starting.

---

## PART 3 — AUTO MODE (recommended)

Set it running and read the results later.

### Set up the scheduled task, once

In a **fresh Cowork chat**, paste this. Replace the folder path with yours.

> Please create a new scheduled task with the following exact specifications:
>
> Name: Auto anki optimize
> Description: Automatically build and verify/patch all queued decks via PROMPT_auto.md
> Prompt: Read scripts/PROMPT_auto.md and execute it.
> Repeats: Every 1 hour, turning itself off automatically 8 hours after it starts.
> Approvals: Skip all approvals.
> Folder: /full/path/to/your/project/folder
> Conditions: Scheduled tasks run even when the computer is asleep and offline.

### Then

Stage everything you want done, and hit **Run Now** on the task.

It picks up one module per run, builds it, saves the audit trail, and stops. Next hour it
takes the next one. When it hits a usage limit it just keeps checking until the limit
lifts. Expect roughly **2–5 hours per deck** including waiting out limits.

**Two things never happen unattended**, by design:

1. **`--pass` is never run.** Modules finish at `built-unverified` with their report
   written, and you decide after reading it. That is the one human checkpoint and
   automation does not get to remove it.
2. **A stale script or an empty-reading folder stops the run** instead of working around
   it. Those are the two failure modes that look like success from the outside.

### In the morning

For each finished module, open `COMPLETED/<module>/<module> (NOTES).docx` and read the
**JUDGEMENT CALLS** section. That is where content decisions landed. It takes about thirty
seconds per module and it is the last real check against losing information.

Happy with it? In a new session:

```
Approved. Pass it. Run: python3 scripts/verify_deck.py --pass "Airway Anatomy and Management"
```

One line per module. Not happy? See **Part 5**.

---

## PART 4 — MANUAL MODE

Same work, you drive. **Two sessions per module — they must be different sessions.**

### Session A — build

```
Read scripts/PROMPT_build.md and execute it.
```

Picks the next queued module, builds it, writes the audit trail, stops at one module.

### Session B — verify (must be a NEW session)

A builder auditing its own work shares its own blind spots. Type the module name exactly.

```
Read scripts/PROMPT_verify.md and execute it for Airway Anatomy and Management.
```

It audits the deck adversarially, patches what is wrong, re-verifies its own patch,
rebuilds the NOTES doc, and writes its report. Then it stops and shows you the judgement
calls.

Read them. If you agree:

```
Approved. Pass it.
```

Then it will offer to delete the scratch files and archive your originals. If you are
completely done with that deck:

```
Approved. Cleanup.
```

That is it. Two sessions and one command per module.

---

## PART 5 — IF YOU DISAGREE WITH A JUDGEMENT CALL

Do not pass. Say exactly what is wrong, then point it at the patch prompt:

```
[Say exactly what you disagree with in the judgement calls.] Now read scripts/PROMPT_patch.md and execute it.
```

It rewrites, re-verifies, and comes back to you. Repeat until you are happy, then pass.

---

## PART 6 — AFTER EVERY MODULE IS DONE

Run this **once**, at the very end, after everything is built, verified and passed:

```
Read scripts/PROMPT_dedupe.md and execute it.
```

Duplicates between decks cannot be found one module at a time — that is exactly why this
is a separate, final step.

---

## PART 7 — GRADING A DECK YOURSELF (optional but worth it)

If you want a hard look at whether a finished deck is actually better than what you
started with, paste this into a fresh session:

```
Compare COMPLETED/<module>/<module> (FINAL).apkg against the original in Anki Decks/ (or
its archived copy). Be objective, be subjective, and be extremely critical of clinical
accuracy in both.

Read COMPLETED/EXAMPLE/README.md first so you know what the audit trail contains, then
work from COMPLETED/<module>/audit/ — ops.json for what changed, changelog.json for why,
meta.json for what was left outstanding.

Report: cards added/edited/split/demoted/deleted; the multi-cloze rate before and after;
every factual claim you can check against an independent source and whether it holds; and
anything the rebuild lost that the original had. I need this to be a gold-standard deck.
End with a copy-pasteable list of specific fixes.
```

Then feed its own list back to it:

```
Fix the final deck with those suggestions and give me the updated .apkg to import. Fix:

[paste the fix list here]
```

---

## WHAT THE PIPELINE IS DOING, IN FOUR SENTENCES

You do not need this to use it, but it helps to know what you are trusting.

Your captures are images, so the text is pulled out by OCR. Rather than making the model
*look* at all 30–70 page pictures — which is the most expensive thing it can do and gives a
worse transcription than the OCR already has — it reads the OCR text and looks only at the
regions OCR could not handle: figures, low-confidence words, and every line with a number
in it. That is safe only because it is proved rather than guessed: the extractor asserts
`unaccounted_ink_px == 0`, meaning every meaningful pixel on every page is either inside a
high-confidence OCR word or on the visual read list, and any page it cannot prove is handed
over whole. Then a fourteen-rule editorial rubric is applied to every single card, every
checkable number is looked up against an outside source, and the deck is checked against
itself so it cannot ship two cards that contradict each other.

---

## WHEN SOMETHING GOES WRONG

| Symptom | What it actually is |
|---|---|
| `disk I/O error` | The folder is on a cloud mount. Move it to local disk. |
| A folder "is empty" but you can see files | Cloud sync has not materialized them. Quit and reopen the Drive app. |
| Claude reports a bug that sounds already-fixed | Stale script copy. Run `python3 scripts/check_version.py` before believing any bug report. |
| Counts do not reconcile at build time | Something edited the deck outside `ops.json`. The build is supposed to fail here — do not force it. |
| `unaccounted_ink_px` is not 0 | The coverage proof failed. Re-run extraction with `COVERAGE=page` and it reads whole pages instead. |
| A tool is "missing" right after installing it | Your terminal was open before the install. Close it, open a new one, re-run. |
| PowerShell won't run `setup.ps1` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, once. |
| Anything else | `bash setup.sh` again — it is safe to re-run and fixes most environment problems. |

**Read these, in this order, if you want more:**

1. `COMPLETED/EXAMPLE/README.md` — what a finished module looks like
2. `scripts/HANDOFF.md` — the actual job. §3b is the editorial rubric.
3. `scripts/HANDOFF_REFERENCE.md` — **do not read whole.** Pull one section:
   `python3 scripts/handoff.py 4b`
