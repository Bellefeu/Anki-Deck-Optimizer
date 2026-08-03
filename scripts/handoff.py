#!/usr/bin/env python3
"""Read one section of the handoff instead of the whole thing.

HANDOFF.md is read in full by every session. It had grown to ~22k tokens, and
because everything in context is re-sent on every turn of an agentic run, that
is 22k tokens paid hundreds of times per module - for sections most phases never
use. So it is split in two:

    HANDOFF.md            the job. Rubric, gates, format, environment, status.
                          Read this in full, every session, as before.
    HANDOFF_REFERENCE.md  live but situational. Creation mode, archiving, known
                          limitations, the verification pass, cleanup,
                          scheduling, capture/OCR notes, Drive IDs, history.
                          Read a section when the phase needs it.

**Nothing was removed by the split.** `handoff.py check` asserts that every
section the pipeline expects is present in one file or the other, and
selftest.py runs it. If a section ever goes missing this fails loudly rather
than letting a session quietly work without it.

    python3 handoff.py list           # every section, and which file it is in
    python3 handoff.py 4b             # print section 4b
    python3 handoff.py "RULE 9"       # match by title too
    python3 handoff.py check          # assert the split lost nothing
"""

import os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(HERE, "HANDOFF.md")
REF  = os.path.join(HERE, "HANDOFF_REFERENCE.md")

# Every section that must exist somewhere after the split. This list is the
# contract: it is what makes the split auditable rather than a matter of trust.
EXPECTED = [
    "CRITICAL PATH", "0.", "0b.", "0c.", "0d.", "1.", "1b.", "1c.", "1d.",
    "1e.", "1f.", "2.", "3.", "3b.", "4.", "4b.", "4c.", "4d.", "4e.", "4f.",
    "5.", "6.", "6b.", "8.",
    "RULE 0", "RULE 1", "RULE 2", "RULE 3", "RULE 4", "RULE 5", "RULE 6",
    "RULE 7", "RULE 8", "RULE 9", "RULE 10", "RULE 11", "RULE 12", "RULE 13",
    "RULE 14", "WORKED TRANSFORMATIONS",
]


def sections(path):
    """[(heading, body, path)] for every ## / ### heading in a file."""
    if not os.path.exists(path):
        return []
    lines = open(path, encoding="utf-8").read().split("\n")
    idx = [i for i, l in enumerate(lines) if re.match(r"^#{2,3} ", l)]
    out = []
    for k, i in enumerate(idx):
        j = idx[k + 1] if k + 1 < len(idx) else len(lines)
        out.append((lines[i].lstrip("# ").strip(), "\n".join(lines[i:j]), path))
    return out


def all_sections():
    return sections(CORE) + sections(REF)


def find(query):
    q = query.lower().strip()
    hits = [s for s in all_sections() if s[0].lower().startswith(q)]
    if not hits:
        hits = [s for s in all_sections() if q in s[0].lower()]
    return hits


def cmd_list():
    for path, label in ((CORE, "HANDOFF.md"), (REF, "HANDOFF_REFERENCE.md")):
        secs = sections(path)
        if not secs:
            continue
        print(f"\n{label}  ({sum(len(b) for _, b, _ in secs):,} bytes)")
        for head, body, _ in secs:
            print(f"   {len(body):6d} B  ~{len(body)//4:5d} tok   {head[:70]}")


def cmd_check():
    have = [h.lower() for h, _, _ in all_sections()]
    missing = [e for e in EXPECTED
               if not any(x.startswith(e.lower()) or e.lower() in x for x in have)]
    if not os.path.exists(REF):
        print("  HANDOFF_REFERENCE.md not present - split not applied yet "
              "(this is fine on an un-migrated copy)")
    if missing:
        print("  !! SECTIONS MISSING FROM BOTH FILES:")
        for m in missing:
            print(f"       {m}")
        print("\n  The split lost something. Regenerate with update_handoff.py.")
        return 1
    print(f"  OK - all {len(EXPECTED)} expected sections present across "
          f"{len(all_sections())} headings; the split lost nothing.")
    return 0


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    arg = sys.argv[1]
    if arg == "list":
        cmd_list(); return 0
    if arg == "check":
        return cmd_check()
    hits = find(arg)
    if not hits:
        print(f"No section matching {arg!r}. Try: python3 handoff.py list")
        return 1
    for head, body, path in hits:
        print(f"<!-- from {os.path.basename(path)} -->")
        print(body)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
