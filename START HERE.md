# START HERE

Everything you need, in the order you will actually use it. You do not need to
understand the code.

You can use either **Claude Cowork** or **Codex in the ChatGPT desktop app**. Both can
read this folder, run the scripts, and follow the prompt files. A normal web chat cannot
work directly in a folder on your computer.

The easiest path is the **Prism Control Center**. It handles updates, setup, staging,
preferences, deck review, and copy-ready prompts. When the text says to paste or run a
code-style box, copy that box exactly; other boxes are folder-layout examples. Inside
Prism, choose a review deck once and every `<module>` token is replaced with that deck's
real name before you copy.

### Open Prism

- **Windows:** double-click **Prism Control Center - Windows.cmd**.
- **Mac:** double-click **Prism Control Center - Mac.command**.

#### If your Mac says Apple could not verify the file

This can appear the first time because the launcher came from GitHub and is not signed
by an Apple Developer ID. Prism has not run yet.

1. Click **Done**. Do not click **Move to Trash**.
2. Open the **Apple menu** at the top-left of your screen.
3. Click **System Settings**, then **Privacy & Security**.
4. Scroll down and click **Open Anyway** beside the blocked Prism launcher.
5. Confirm, then click **Open**.

You only need to approve this copy once. These are
[Apple's current steps](https://support.apple.com/en-us/102445).

The project root intentionally has only two launchers. Setup scripts and templates live
inside `control_center/`, so a new user never has to guess which technical file to run.
Your browser opens a local page named **Prism**. Keep the small terminal window open while
you use it. Prism runs only on your computer; it does not upload your decks to a website.
If Python is missing, the launcher opens the guided installer first; open Prism again
after setup finishes.

---

## PART 1 — KEEP THE TOOLKIT UPDATED

Make this your first stop whenever you return to the project. Open **Update** in Prism and
click **Check for updates**. If a stable update is ready, click **Install update**. If you
just downloaded the project, the check simply confirms that you are current.

The updater:

- replaces only publisher-owned toolkit files;
- never replaces your decks, source files, audit JSON, finished work, profile, prompt
  add-ons, or verified status;
- tests the download before changing live files;
- makes a dated backup in `.pipeline_backups`;
- restores the previous toolkit and state if a final check fails; and
- pauses if it finds an unfinished resumable build.

It uses the latest stable GitHub **Release**. It does not run `git pull`, and it does not
require you to understand Git.

### Updating an older copy that does not have Prism

Do this once. Do not merge a fresh download into your real project by hand.

1. Keep your old project folder exactly where it is.
2. Download the newest repo ZIP from GitHub and extract it somewhere else, such as
   Downloads. This temporary copy is only a helper.
3. Open Prism from the temporary copy.
4. Click **Choose folder** and choose your **old project folder**—the one with your real
   decks and progress.
5. Open **Update**, click **Check for updates**, then **Install update**.
6. Close the helper and use Prism inside your old project from now on. You may delete the
   temporary downloaded copy.

Prism treats the old folder as a legacy install: it backs up every toolkit file it will
replace, preserves runtime state and study files, then tests the installed update.

---

## PART 2 — ONE-TIME SETUP

### 1. Put this folder on your computer

Clone or download the repo. Your Desktop or Documents folder is fine.

- Q: How do I download this “repo,” and what is a repo?
- A: A repository, or repo, is a project on GitHub. Click the green **Code** button,
  choose **Download ZIP**, extract it, and move the extracted folder somewhere you can
  find again.

**The folder must be available on your actual hard drive, not cloud-only.** The pipeline
uses SQLite. A cloud-only folder can cause a `disk I/O error` or appear empty.

If you keep it in Google Drive, mark it **Available offline** first:

- Install Google Drive for desktop: <https://support.google.com/a/users/answer/13022292>
- Right-click the project in Finder or File Explorer and choose **Available offline**.
- If a visible folder reads as empty, pause and resume Drive sync. If needed, quit Drive
  completely and reopen it.

An unhydrated folder reads as **empty**, not as an error. That can look exactly like
“there is no work to do,” so stop and fix sync before continuing.

### 2. Run the guided installer

Open **Update** in Prism and click **Open guided setup**. Follow the words in the terminal.
The installer finds Python, Poppler, Tesseract, and Node, installs only what is missing,
then runs the checks.

If Prism cannot open yet, open a terminal in the project folder and run the command for
your operating system.

macOS or Linux:

```bash
bash control_center/install/setup.sh
```

Windows PowerShell:

```powershell
.\control_center\install\setup.ps1
```

Useful details:

- It may ask for your password before installing system software. It prints commands and
  never elevates silently.
- Preview without changing anything: use `bash control_center/install/setup.sh --dry-run`
  on macOS/Linux or `.\control_center\install\setup.ps1 -DryRun` on Windows.
- macOS uses Homebrew; Windows uses `winget`.
- If PowerShell refuses the script, run
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.
- If a newly installed tool still looks missing, close the terminal, open a new one, and
  run setup again so the new shell sees the updated PATH.
- The commands below use `python3`. On Windows, use `py` if `python3` is unavailable.

### 3. Confirm READY

Setup ends by running `scripts/bootstrap.py`, which checks the environment and runs more
than 140 self-tests. The final status must begin with **`READY`**; `READY, with warnings`
is usable, but read each warning. If it says `NOT READY`, fix the item it names and run
setup again. Re-run the checks alone any time with:

```bash
python3 scripts/bootstrap.py
```

### 4. Set your preferences

Open **Prefs** in Prism. The left editor is your profile: how aggressively to fix errors,
whether to preserve Anki review history, and how thorough the work should be. The right
editor is for your own prompt add-ons. Put universal preferences under **Every run**, or
add notes for Build, Verify, Auto, or the final duplicate check. Click **Save preferences**.

**Decide now whether to keep review history.** The default discards it. This changes how
a rebuilt deck must be imported and is far cheaper to settle before the first build.

### 5. Connect your desktop agent

#### Claude Cowork

Connect this project folder as the working folder.

#### ChatGPT Codex

1. Install the [ChatGPT desktop app](https://learn.chatgpt.com/docs/quickstart), then sign in.
2. Choose **Codex** and open this project folder.
3. If asked **Local** or **Worktree**, choose **Local**. Decks, source files, results, and
   progress must remain together here.
4. Start with **Ask for approval** in the permission menu. Approve web access when Codex
   checks medical facts. Do not use **Full access**.

For the best clinical review, choose **5.6 Sol** with **High** reasoning if your account
offers it; otherwise use the strongest Codex model available. Official help:
[Quickstart](https://learn.chatgpt.com/docs/quickstart) and
[Permissions](https://learn.chatgpt.com/docs/permission-modes).

---

## PART 3 — STAGE YOUR MODULES

Two destinations, one shared module name.

**Easy way:** open **Home** in Prism, type the module name, then drop source files and an
`.apkg` deck into the two large boxes. Prism renames the deck to match and asks before
replacing a staged file. You may stage as many modules as you like.

You may also use either input by itself:

- Deck only → optimize the existing deck without source gap-fill.
- Source only → create a new deck from scratch.

### Source captures → `Source Files/<module>/`

Text files are cheap. Image-only PDFs and screenshots are expensive: 30+ pages can use a
large share of an LLM limit. Prefer text exports whenever they preserve the content.

| Type | What happens | Notes |
|---|---|---|
| `.pdf` | Text extraction or OCR plus a coverage gate | Best for page captures |
| `.jpg`, `.jpeg`, `.png`, `.gif`, `.bmp`, `.tif`, `.tiff`, `.webp` | Wrapped as PDF, then processed | Screenshots can be dropped directly |
| `.txt`, `.md`, `.csv`, `.rtf` | Read directly as text | Cheapest for transcripts and notes |

Export PowerPoint and Word files to PDF first. Do not use an online converter: it can
degrade quality and uploads course material to a third party. For web captures, keep
individual PDFs rather than merging them with an online service that may destroy the text
layer.

```text
Source Files/
  <module>/
    lecture-slides.pdf
    notes.txt
    extra-diagrams.jpg
```

### Anki deck → `Anki Decks/<module>.apkg`

Export the deck from Anki as `.apkg`. Its filename must match the source folder.

```text
Anki Decks/
  <module>.apkg
```

---

## PART 4 — RUN THE PIPELINE

Choose one path. **Automatic mode is recommended**; manual mode is there when scheduling
is unavailable or when you want to drive every phase yourself.

### Path A — automatic mode

First, use one normal chat to test the automatic prompt:

```text
Read scripts/PROMPT_auto.md and execute it.
```

This is a real pipeline run, not a preview: it may complete the next build or verification
phase. Fix any setup error it finds before scheduling repeated runs.

#### Schedule Claude Cowork

In the Cowork project connected to this folder, open **Scheduled → New task → Create with
Claude**, then paste this entire scheduler-creation prompt:

```text
Create a scheduled Cowork task with these settings.

Name: Auto Anki Optimize
Description: Advance the Anki optimization pipeline by one safe, resumable phase per run.
Instructions for every run:
Read scripts/PROMPT_auto.md and execute it.

Schedule: Run once per hour for the next 8 hours, then stop and do not run again.
Working folder: This project folder. The task requires its local files.
Approval mode: Automatically approve. Do not use Skip all approvals.
```

Confirm that Claude shows the correct name, hourly schedule, instructions, and working
folder, then click **Schedule**. Because this task uses local files, the computer must be
awake and Claude Desktop must remain open for each run. See
[Claude scheduled tasks](https://support.claude.com/en/articles/13854387-schedule-recurring-tasks-in-claude-cowork).

#### Schedule ChatGPT Codex

1. Open **Settings → General → Permissions** and turn on **Auto-review**.
2. Return to this Local Codex project and choose **Approve for me**.
3. In a fresh Codex task in this project, paste this entire scheduler-creation prompt:

```text
Create a standalone scheduled task with these settings.

Name: Auto Anki Optimize
Description: Advance the Anki optimization pipeline by one safe, resumable phase per run.
Instructions for every run:
Read scripts/PROMPT_auto.md and execute it.

Schedule: Run once per hour for the next 8 hours, then stop and do not run again.
Project: This Local project.
Run mode: Local project. Do not use an isolated worktree or cloud environment.
Permissions: Keep the workspace boundary and use Approve for me. Do not use Full access.
```

Open **Scheduled** in the sidebar and confirm the name, instructions, eight-hour schedule,
Local project, and run mode. Use **Run now** if you want the first scheduled phase to start
immediately. The computer must be awake and online and the desktop app must remain open. See
[Scheduled tasks](https://learn.chatgpt.com/docs/automations). If Scheduled is not enabled
for your account, use Path B.

Each scheduled run starts a fresh session, performs one safe phase, and stops. A later run
continues a resumable build or handles the next phase. Expect roughly **2–5 hours per
deck**, including time between runs. Add another eight-hour schedule later if queued work
remains.

Two things never happen unattended:

1. `--pass` is never run. A completed review waits for your approval.
2. Stale scripts or an empty-reading folder stop the run instead of guessing.

### Path B — manual mode

Use two different sessions per module. A builder must not audit its own work.

#### Session A — build

```text
Read scripts/PROMPT_build.md and execute it.
```

This picks the next queued module, builds it, writes the audit trail, and stops.

#### Session B — verify

Start a **new session** and use:

```text
Read scripts/PROMPT_verify.md and execute it for <module>.
```

It audits adversarially, patches defects, re-verifies the patch, rebuilds the NOTES doc,
writes a report, and stops at the human decision.

---

## PART 5 — REVIEW AND CORRECT

Open **Decks** in Prism and choose **Needs review**. Select a deck, open its NOTES or
verification report, and read every **JUDGEMENT CALL**. This is the deliberate human gate
against losing important information.

The selected deck has a decision workspace directly beneath its judgement calls. Prism
inserts the exact deck name in every prompt—no manual renaming.

If clinical accuracy matters and you plan to run the deeper check in Part 6, do that
before final approval, then return here.

### If you agree

When this is your final review—including Part 6 if you are using it—copy the approval
prompt from the deck's review workspace:

```text
Approved. Pass it. Run: python3 scripts/verify_deck.py --pass "<module>"
```

This marks the module verified, updates the handoff state, archives its original inputs,
and reclaims its completed scratch.

### If you disagree

Do not pass. In the same deck workspace, type exactly what should change. Prism appends
the patch instruction and module name, previews the finished prompt, and copies it as one
message:

```text
[Describe what should change.] Apply that correction to "<module>". Now read scripts/PROMPT_patch.md and execute it for "<module>".
```

The patch session rewrites, re-verifies, and returns to the human gate. Repeat until you
are satisfied, then use the approval prompt.

---

## PART 6 — RUN AN OPTIONAL DEEP QUALITY CHECK

Do this **before final approval** whenever clinical accuracy matters. In a fresh session,
choose the prompt that matches how the deck started. Prism fills every `<module>` using
the deck selector above its prompt library.

### Compare an original deck with the rebuild

```text
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

### Grade a deck created from source only

```text
Grade COMPLETED/<module>/<module> (FINAL).apkg against the source material in
Source Files/<module>/. There is no prior deck — this was built from scratch.

Read COMPLETED/EXAMPLE/README.md first so you know what the audit trail contains, then
work from COMPLETED/<module>/audit/ — ops.json for what was created, changelog.json for
why, meta.json for what was left outstanding.

Report: total cards created; the multi-cloze rate; every factual claim you can check
against the source material and whether it holds; and any source content missed entirely.
I need this to be a gold-standard deck. End with a copy-pasteable list of specific fixes.
```

Feed the critique back through the audited patch workflow in a fresh session:

```text
Treat the following as a correction report for "<module>". Confirm each finding, apply every
valid correction, and keep the audit trail. Now read scripts/PROMPT_patch.md and execute it
for "<module>".

[Paste the fix list here]
```

After the patch is independently re-verified, re-read its judgement calls and return to
Part 5 for final approval.

---

## PART 7 — FINISH THE COLLECTION

After every module is built and passed, run this **once**:

```text
Read scripts/PROMPT_dedupe.md and execute it.
```

Cross-deck duplicates cannot be found one module at a time. This intentionally stays a
separate final step.

---

## WHAT THE PIPELINE IS DOING

Your captures may be images, so Prism extracts text with OCR. It reads pixels only where
OCR is unreliable: figures, low-confidence words, and every line containing a number.
The coverage gate proves every meaningful pixel is either inside a high-confidence word
or on the visual-read list; an unprovable page is handed over whole. A fourteen-rule
editorial rubric is applied to every card, checkable claims are verified independently,
and the finished deck is checked against itself for contradictions.

---

## WHEN SOMETHING GOES WRONG

| Symptom | What it actually is |
|---|---|
| `disk I/O error` | The folder is on a cloud mount. Move it to local disk. |
| A visible folder reads as empty | Cloud sync has not materialized it. Restart the Drive app. |
| An agent reports a familiar already-fixed bug | Run `python3 scripts/check_version.py` before diagnosing stale scripts. |
| Counts do not reconcile | Something edited the deck outside `ops.json`; the build is correctly refusing it. |
| `unaccounted_ink_px` is not 0 | Re-run extraction with `COVERAGE=page` so it reads whole pages. |
| A tool is missing right after setup | Open a new terminal and re-run setup. |
| PowerShell blocks the installer | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once. |
| macOS blocks the launcher | Click **Done**, then **Privacy & Security → Open Anyway**. |
| A dependency or readiness check fails | Open Prism and click **Open guided setup** again. |
| Anything else | Copy the exact error into a fresh desktop-agent session in this project. Do not work around a safety stop. |

**Read more in this order:**

1. `COMPLETED/EXAMPLE/README.md` — what a finished module looks like.
2. `scripts/HANDOFF.md` — the actual job; §3b is the editorial rubric.
3. `scripts/HANDOFF_REFERENCE.md` — pull one section on demand, for example
   `python3 scripts/handoff.py 4b`. Do not read the entire reference by default.
