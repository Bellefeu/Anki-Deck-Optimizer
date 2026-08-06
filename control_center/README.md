# Prism Control Center

Prism is a local dashboard for the Anki LLM Optimizer. It uses only Python's standard
library, binds to `127.0.0.1`, and places a random token on every write request.

## For users

- Windows: `OPEN_CONTROL_CENTER.cmd`
- macOS: `OPEN_CONTROL_CENTER.command`
- Linux: `open_control_center.sh`

The dashboard can stage source files and one `.apkg`, edit `PROFILE.md` and
`USER_PROMPTS.md`, open deck review artifacts, launch setup, and install stable releases.

For a pre-Prism installation, a user can launch Prism from a separate fresh download,
select the old project as the active folder, and install the latest release into it. This
uses the same legacy backup and rollback path; users should not merge folders by hand.

## Update safety model

`scripts/UPDATE_MANIFEST.json` is a complete allowlist of publisher-owned files and their
SHA-256 hashes. An update is downloaded to a temporary directory, path-checked,
hash-checked, and self-tested before the live project lock is taken. The updater then:

1. migrates a copy of the current state;
2. proves module status, pending work, run count, and session history are unchanged;
3. backs up the old toolkit and runtime state;
4. atomically replaces allowlisted files only;
5. writes migrated state last; and
6. restores the backup if any installed-file check fails.

The update archive never supplies `scripts/project_state.json`, `PROFILE.md`, or
`USER_PROMPTS.md`. Study inputs, completed decks, audit records, and work folders are also
outside the allowlist.

## Publishing a stable update

Only a GitHub Release should be offered to users. Before tagging a release:

```bash
python3 scripts/check_version.py --write
python3 control_center/build_manifest.py 1.1.0
python3 scripts/selftest.py
python3 -m unittest control_center/test_control_center.py
git diff --check
```

Commit both generated manifests with the code. Tag that exact commit with the same
semantic version, such as `v1.1.0`, then publish a non-draft, non-prerelease GitHub
Release from that tag. The updater downloads GitHub's source archive for that tag and
rejects any file whose hash differs from the committed allowlist.

For the next release, increase the version (for example, `1.1.1` for a bug fix or `1.2.0`
for a new feature). Never reuse a published version number.
