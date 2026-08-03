# AUTO PROMPT — one pipeline phase, unattended

Execute this entire file. It is designed to run on a schedule with nobody watching.

**Do exactly one phase, then stop.** Do not chain phases. Do not start a second module.
The schedule is the loop: if this run dies on a usage limit, the next scheduled run reads
state and resumes. That only works if each run leaves state consistent.

## STEP 0 — SETUP

Copy **everything** from `scripts/` to local disk. All scripts, not a subset.
**SQLite cannot run on the Google Drive mount** (`disk I/O error` on any write), so every
step happens locally and only finished output is copied back.

```bash
python3 deps.py
python3 check_version.py
```

If `check_version.py` reports differing files, **stop and report it.** A stale script will
make you diagnose bugs that are already fixed. Do not work around it.

## STEP 1 — REFRESH THE QUEUE, THEN ASK WHAT TO DO

Rebuild the queue first, every run, before asking what to do:

```bash
python3 build_queue.py "<Drive>/Source Files" "<Drive>/Anki Decks"
```

This is safe to repeat and is what makes the pipeline self-starting. It pairs each deck
with its source module by filename, queues decks with no module as `optimize-only`, and
**skips every module already tracked in `project_state.json`, whatever its status** — so a
deck that is built, verified, or awaiting your `--pass` is never rebuilt. It also re-records
the absolute paths for *this* environment, which matters because a later run may reach the
same Drive folder by a different route than the run that queued it.

After the first successful run the paths are remembered, so plain `python3 build_queue.py`
works from then on. Pass them explicitly whenever it cannot find the folders.

A queue of zero on a cloud mount is not proof there is no work — an unhydrated folder reads
as empty. `build_queue.py` says so itself; believe it and stop rather than reporting "all
decks complete".

Then ask what to do:

```bash
python3 next_action.py --claim
```

It needs to find `COMPLETED/` to tell a verified module from an unverified one, and a
fresh scratch dir does not contain it. `build_queue.py` records the path; if this run
reports it cannot find it, point at it explicitly rather than proceeding:

```bash
COMPLETED_DIR="<path to COMPLETED>" python3 next_action.py --claim
```

Act on the exit code:

| Code | Meaning | What to do |
|---|---|---|
| 2 | another run holds the lock, **or** `COMPLETED/` could not be located, **or** the queued deck is missing | **Stop immediately.** Say so and exit. Do not proceed. The message says which. |
| 1 | `IDLE` or `AWAIT_USER` | Report the message and stop. Nothing to do. |
| 0 | there is work | It prints `ACTION`, `MODULE` and `PROMPT`. Continue. |

## STEP 2 — DO THAT ONE PHASE

Read the prompt file it named and execute it for the module it named:

- `BUILD` / `RESUME_BUILD` → `PROMPT_build.md`
- `VERIFY` → `PROMPT_verify.md`
- `PATCH` → `PROMPT_patch.md`

**Read the core handoff only.** `HANDOFF.md`, not `HANDOFF_REFERENCE.md`. Pull a
reference section with `python3 handoff.py <section>` if the phase needs one. An
unattended run has no one to notice it reading 6k tokens of scheduling notes on every
turn of a three-hour job.

**Check `unattended_ink_px == 0`** in `extract_report.json` after STEP 2 of a BUILD.
If it is not 0, `extract_source.py` has already exited non-zero — re-run it with
`COVERAGE=page`, note it in the report, and continue. Do not proceed on a failed
coverage proof.

**Delegate the visual read and the hostile audit to subagents**, as `PROMPT_build.md`
now specifies. This matters more unattended than interactively: a run that carries
thirty page images through to save-back is the one that dies on a usage limit
mid-phase.

**What unattended running does NOT relax.** The claim index (Rule 10), the checkable-claim
lookups (Rule 9), and the direction/depth/dose sweep (Rule 13) are the checks most likely to be
quietly dropped when a run is under time or usage pressure — and they are the ones that caught
nothing for the first several modules precisely because nothing forced them.

The claim index is scripted and cheap, so there is no version of "no time for it":

```bash
python3 check_consistency.py "<deck>" --all
```

The lookups it lists are *not* scriptable, and those are the ones that get dropped. If you
cannot complete them for the whole deck this run, **checkpoint and stop; do not ship the phase
without them.** Record the G1a collision count and the Rule 9 ledger in the report every time,
including when both are zero, so a skipped check is visible rather than indistinguishable from
a clean one.

Two deviations from those prompts, because nobody is watching:

**Never wait for approval.** Where a prompt says to present something and wait, instead
apply it and record it. `PROMPT_verify.md` patches in the same session already; do that.
Log every judgement call in the report as normal - the user reads it later.

**Never run `--pass`.** Marking a module verified stays the user's decision, always. Leave
finished modules at `built-unverified` with their report written. That is the one human
checkpoint and unattended running does not remove it.

## STEP 3 — CHECKPOINT AS YOU GO

You may run out of usage partway through. Leave state resumable at every point:

- On a deck over ~200 cards, work in batches of ~50 source cards, **appending** to
  `ops.json` and `new_cards.json`, and after each batch write
  `work/<module>/progress.json`:
  ```json
  {"last_index": 150, "total": 400, "complete": false}
  ```
  `next_action.py` sees an incomplete `progress.json` and resumes that module before
  starting anything new. Set `"complete": true` when the passes are done.
- `extract_source.py` already checkpoints per page. If it is killed, just re-run it.
- **Never leave a half-written `ops.json` without a matching `progress.json`.** A later run
  cannot tell the difference between "finished" and "died partway" without it.

## STEP 4 — FINISH THE PHASE

Copy to Drive:
- deliverables → `COMPLETED/<module>/`
- audit trail → `COMPLETED/<module>/audit/` (`ops.json`, `new_cards.json`, `meta.json`,
  `changelog.json`, `extract_report.json`, and `source/content*.txt`)
- regenerated `HANDOFF.md` and `project_state.json` → `scripts/`

Then release the lock — **do this even if the phase failed**, or the next run is blocked
for three hours until the lock goes stale:

```bash
python3 next_action.py --release
```

## REPORT

Short. Nobody is reading it live:

- pipeline version from `check_version.py`
- the action taken and on which module
- whether it completed or checkpointed partway
- counts: cards before/after, ops by type
- anything that needs a human, especially `DEMOTE` and `DELETE` calls
- what `next_action.py --status` says is next

If you stopped because of a lock, a stale script, or an empty-reading folder, say which -
those are the three failure modes that look like success from outside.
