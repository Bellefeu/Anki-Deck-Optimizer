#!/usr/bin/env python3
"""Regenerate HANDOFF.md from project_state.json.

HANDOFF.md is a GENERATED artifact. Never hand-edit it — edit project_state.json
(or the static prose in handoff_template.md) and re-run this.

Called automatically at the end of build_deck.py, so the handoff is always
rewritten by the same run that changed the state.
"""

import json, os, sys, datetime
from state_io import (atomic_write_json, load_state, normalize_state,
                      save_state)

HERE     = os.path.dirname(os.path.abspath(__file__))
STATE    = os.path.join(HERE, "project_state.json")
TEMPLATE = os.path.join(HERE, "handoff_template.md")
REF_TEMPLATE = os.path.join(HERE, "handoff_reference_template.md")
OUTDIR   = os.environ.get("HANDOFF_OUTDIR", os.path.join(HERE, "_out"))

STATUS_LABEL = {
    "built-unverified": "BUILT — needs visual verification",
    "verified":         "COMPLETE & VERIFIED",
    "in-progress":      "IN PROGRESS",
    "pending":          "NOT STARTED",
}


def render_status(st):
    L = []
    L.append("## 7. STATUS — GENERATED, DO NOT HAND-EDIT")
    L.append("")
    L.append(f"*Regenerated automatically on run {st.get('run_count', 0)} "
             f"({st.get('last_updated') or 'not run yet'}). "
             "Source of truth: `project_state.json`.*")
    L.append("")

    mods = st.get("modules", [])
    done = [m for m in mods if m.get("status") == "verified"]
    built = [m for m in mods if m.get("status") == "built-unverified"]

    L.append(f"**Progress:** {len(done)} verified · {len(built)} built awaiting verification · "
             f"{len(st.get('pending_modules', []))} listed as pending")
    L.append("")

    if mods:
        L.append("| Module | Deck ID | Status | Cards | Added | Edited |")
        L.append("|---|---|---|---|---|---|")
        for m in mods:
            # .get throughout: this renders a status table, and a missing optional
            # field must not raise. Callers include verify_deck.py --pass, which has
            # already flipped the module to verified by the time it gets here - a
            # KeyError would abort the rest of the pass, including archiving the
            # inputs, and leave the run half-applied.
            L.append(f"| {m.get('name','?')} | `{m.get('deck_id','?')}` "
                     f"| {STATUS_LABEL.get(m.get('status'), m.get('status','?'))} "
                     f"| {m.get('cards_before','?')} → {m.get('cards_after','?')} "
                     f"| {m.get('added','?')} | {m.get('edited','?')} |")
        L.append("")

    # Outstanding work is the part that must never silently disappear
    blockers = [(m, o) for m in mods for o in m.get("outstanding", [])]
    if blockers:
        L.append("### ⚠ Outstanding — resolve before treating a module as finished")
        L.append("")
        for m, o in blockers:
            L.append(f"- **{m.get('name','?')}** — {o}")
        L.append("")
        nxt = built[0].get("name") if built else None
        if nxt:
            L.append(f"**If image rendering works this session, re-verify `{nxt}` FIRST** "
                     f"before starting a new module. It closes real outstanding debt instead of "
                     f"accumulating a second layer of it.")
            L.append("")
    else:
        L.append("### ✓ No outstanding verification debt")
        L.append("")

    for m in mods:
        if m.get("gaps_filled"):
            L.append(f"**{m.get('name','?')} — gaps filled:** "
                     + "; ".join(m.get("gaps_filled", [])) + ".")
            L.append("")

    pend = st.get("pending_modules", [])
    if pend:
        L.append("### Pending modules")
        L.append("")
        for p in pend:
            L.append(f"- {p}")
        L.append("")
    else:
        L.append("### Pending modules")
        L.append("")
        L.append("Not yet enumerated (~29 remaining). Append names to `pending_modules` "
                 "in `project_state.json` as they are scheduled.")
        L.append("")

    return "\n".join(L)


def render_status_reference(st):
    """History. Needed when diagnosing a repeat failure or writing a post-mortem,
    not when building a deck - so it lives in HANDOFF_REFERENCE.md."""
    L = ["## STATUS HISTORY — GENERATED, DO NOT HAND-EDIT", ""]
    inc = st.get("incidents", [])
    if inc:
        L.append("### Incident log — mistakes already made, do not repeat")
        L.append("")
        for i in inc:
            L.append(f"- **{i.get('issue', 'Unlabelled incident')}** "
                     f"({i.get('date', 'date unknown')}) — {i.get('detail', 'No detail recorded.')}")
            L.append(f"  - *Mitigation:* {i.get('mitigation', 'No mitigation recorded.')}")
        L.append("")

    env = st.get("environment_findings", [])
    if env:
        L.append("### Verified environment facts")
        L.append("")
        for e in env:
            L.append(f"- {e}")
        L.append("")

    log = st.get("session_log", [])
    if log:
        L.append("### Session log")
        L.append("")
        for s in log[-8:]:
            run = s.get("run", "?")
            date = s.get("date", "date unknown")
            module = s.get("module", "unknown module")
            summary = s.get("summary") or s.get("action") or "No summary recorded."
            L.append(f"- **Run {run}** ({date}) — {module}: {summary}")
        L.append("")

    return "\n".join(L)


def recompute_totals(st):
    """Single source of truth for totals. Called by BOTH regenerate() and record_run()
    so the two can never disagree."""
    mods = st.get("modules", [])
    st["totals"] = {
        "modules_complete": sum(1 for m in mods if m.get("status") != "pending"),
        "modules_verified": sum(1 for m in mods if m.get("status") == "verified"),
        "cards_before": sum(m.get("cards_before", 0) for m in mods),
        "cards_after":  sum(m.get("cards_after", 0) for m in mods),
    }
    return st


def regenerate():
    st = load_state(STATE, create=True)
    added = normalize_state(st)
    # recompute before rendering, and write the corrected state back
    before = json.dumps(st.get("totals"), sort_keys=True)
    recompute_totals(st)
    totals_changed = json.dumps(st.get("totals"), sort_keys=True) != before
    if added or totals_changed:
        save_state(st, STATE)
        if added:
            print(f"  (initialized missing state: {', '.join(added)})")
        if totals_changed:
            print(f"  (totals recomputed: {st['totals']})")
    with open(TEMPLATE, encoding="utf-8") as f:
        tpl = f.read()

    if "<!--STATUS_SECTION-->" not in tpl:
        raise SystemExit("FATAL: template is missing the <!--STATUS_SECTION--> placeholder")

    out = tpl.replace("<!--STATUS_SECTION-->", render_status(st))

    ref = None
    if os.path.exists(REF_TEMPLATE):
        with open(REF_TEMPLATE, encoding="utf-8") as f:
            ref = f.read()
        if "<!--REFERENCE_STATUS-->" not in ref:
            raise SystemExit("FATAL: reference template is missing "
                             "<!--REFERENCE_STATUS-->")
        ref = ref.replace("<!--REFERENCE_STATUS-->", render_status_reference(st))

    try:
        os.makedirs(OUTDIR, exist_ok=True)
    except OSError as e:
        print(f"  (could not create {OUTDIR}: {e} - writing beside the scripts only)")
    written = []
    for path in (os.path.join(HERE, "HANDOFF.md"), os.path.join(OUTDIR, "HANDOFF.md")):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(out)
            written.append(path)
        except OSError:
            pass
    if ref is not None:
        for path in (os.path.join(HERE, "HANDOFF_REFERENCE.md"),
                     os.path.join(OUTDIR, "HANDOFF_REFERENCE.md")):
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(ref)
            except OSError:
                pass
    try:
        atomic_write_json(os.path.join(OUTDIR, "project_state.json"), st)
    except OSError:
        pass

    print(f"HANDOFF.md regenerated ({len(out)} bytes) — run {st['run_count']}, "
          f"{len(st['modules'])} module(s) tracked")
    if ref is not None:
        print(f"HANDOFF_REFERENCE.md regenerated ({len(ref)} bytes) — "
              f"read on demand, not every session")
    return out


def record_run(module_name, deck_id, before, after, added, edited,
               outstanding=None, gaps=None, summary="", status="built-unverified"):
    """Called by build_deck.py. Updates state, then regenerates the handoff."""
    st = load_state(STATE, create=True)
    normalize_state(st)

    st["run_count"] += 1
    st["last_updated"] = datetime.date.today().isoformat()

    entry = {
        "name": module_name, "deck_id": deck_id, "status": status,
        "date": st["last_updated"], "cards_before": before, "cards_after": after,
        "added": added, "edited": edited,
        "outstanding": outstanding or [], "gaps_filled": gaps or [],
    }
    # replace in place if this module was seen before, else append
    for i, m in enumerate(st["modules"]):
        if m["name"] == module_name:
            entry = {**m, **entry}
            st["modules"][i] = entry
            break
    else:
        st["modules"].append(entry)

    st["session_log"].append({
        "run": st["run_count"], "date": st["last_updated"],
        "module": module_name, "summary": summary or f"Built {module_name}.",
    })
    recompute_totals(st)

    save_state(st, STATE)
    return regenerate()


if __name__ == "__main__":
    regenerate()
