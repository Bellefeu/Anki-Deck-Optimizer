# PRISM

PRISM is the dashboard for the Anki LLM Optimizer. The helper behind it uses only Python's
standard library, binds to `127.0.0.1`, and requires a random per-run token on every
request.

## The three files here

| File | Role |
| --- | --- |
| `app.py` | The loopback helper: static assets, project API, jobs, uploads. |
| `desktop.py` | The application: one window over a system web view, menus, single instance, first run. |
| `workspace.py` | Where settings live, and how a downloaded PRISM lays down a project folder. |

## Two ways in

**As an application.** `PRISM.exe`, `PRISM.app` or the Linux packages, built by
`packaging/build.py` and published from `.github/workflows/release.yml`. A build carries a
frozen interpreter and a `payload/` copy of every publisher-owned file, so it needs no
Python and no repository. See [`packaging/README.md`](../packaging/README.md).

**From a clone.** `PRISM - Windows.cmd`, `PRISM - Mac.command`, or
`bash control_center/launch.sh` still open the same dashboard in the default browser, and
`python3 control_center/desktop.py` opens it in a window if `pywebview` is installed. Both
need Python 3.10 or newer on the machine.

The dashboard can stage source files and one `.apkg`, open their exact destination
folders, turn the live `START HERE.md` into a copyable in-app walkthrough, edit
`PROFILE.md` and `USER_PROMPTS.md`, open deck review artifacts, launch setup, check the
toolkit against the copy inside the build, and install stable releases. The active review
deck is shared between Start and Decks: prompt previews replace `<module>` automatically,
while the Decks decision workspace produces exact pass commands or appends typed feedback
to a module-specific patch prompt. Resetting preferences requires two confirmations and
keeps the previously saved files as `.bak` recovery copies.

A project folder that predates PRISM is opened the same way as any other: choose it on the
first screen or with **Choose folder**, then install the latest release into it. That uses
the legacy backup and rollback path; users should not merge folders by hand.

## Where PRISM keeps its own state

Nothing about the application lives in a project folder. Per user:

| Platform | Settings | Logs and running instance |
| --- | --- | --- |
| macOS | `~/Library/Application Support/PRISM` | `~/Library/Logs/PRISM` |
| Windows | `%APPDATA%\PRISM` | `%LOCALAPPDATA%\PRISM` |
| Linux | `$XDG_CONFIG_HOME/prism` | `$XDG_STATE_HOME/prism` |

`settings.json` holds the remembered workspace, the recent list, and the window geometry.
`instance.json` holds the port and token of a running PRISM, mode `0600`, so a second
launch brings the first window forward instead of starting a rival server.

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
python3 control_center/build_manifest.py 1.5.0
python3 scripts/selftest.py
python3 -m unittest control_center.test_control_center packaging.test_packaging
git diff --check
```

Commit both generated manifests with the code. Tag that exact commit with the same
semantic version, such as `v1.5.0`, then publish a non-draft, non-prerelease GitHub
Release from that tag. The updater downloads GitHub's source archive for that tag and
rejects any file whose hash differs from the committed allowlist.

For the next release, increase the version (for example, `1.5.1` for a bug fix or `1.6.0`
for a new feature). Never reuse a published version number.

The manifest has to be rebuilt before the application is, because a build copies its
payload out of the manifest and refuses to start a workspace if a hash disagrees. The
release workflow runs `build_manifest.py` and then `packaging/build.py`, in that order,
for exactly this reason.

`APP_VERSION` in `workspace.py` is the number on the downloaded file. It must equal the
manifest's `release_version` at build time, and `test_packaging.py` fails the build if it
does not. The two numbers exist because they legitimately drift apart afterwards: the
release updater can carry a workspace's toolkit forward without anyone downloading a new
application, so an installed PRISM 1.5.0 may correctly be looking at a 1.6.0 workspace.
The rail shows both.
