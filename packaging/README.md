# Building PRISM

Everything in this folder is publisher build tooling. None of it is listed in
`scripts/UPDATE_MANIFEST.json`, so none of it is ever copied into a user's workspace.

```bash
python3 -m pip install -r packaging/requirements-build.txt
python3 packaging/build.py
```

The result lands in `dist/`.

| Platform | Built on | Delivered |
| --- | --- | --- |
| macOS | `macos-14` (arm64), `macos-13` (x86_64) | `PRISM.app`, `PRISM-<version>-macOS-<arch>.dmg` |
| Windows | `windows-latest` | `PRISM.exe`, `PRISM-<version>-windows-portable.zip` |
| Linux | `ubuntu-22.04` | `prism_<version>_amd64.deb`, `PRISM-<version>-linux-x86_64.tar.gz` |

There is no cross compiling. A frozen interpreter is native code and the web view it
drives is a different library on each system, so `build.py` only ever builds for the
machine it is standing on. `.github/workflows/release.yml` runs it once per runner.

## What a build actually contains

1. **A frozen CPython**, courtesy of PyInstaller. This is why a user never installs
   Python to run PRISM.
2. **The dashboard**, at `control_center/static` inside the bundle.
3. **The payload**, at `payload/` inside the bundle: a copy of every publisher-owned file
   named in `scripts/UPDATE_MANIFEST.json`, minus the two entries in
   `workspace.PAYLOAD_EXCLUDED`, plus the manifest itself. This is what a new workspace is
   made of, and `workspace.create_workspace` re-checks every hash as it copies.

`build.py` refuses to start if the working tree disagrees with the manifest. A build that
skipped that check would produce an application which passes its own tests and then
refuses to create a workspace on a user's machine, which is a far worse place to find out.
Rebuild the manifest first:

```bash
python3 control_center/build_manifest.py <version>
```

## The web view is never bundled

PRISM hosts the system web view rather than shipping a browser engine:

| Platform | Engine | Where it comes from |
| --- | --- | --- |
| macOS | WKWebView | Always present. |
| Windows | WebView2 | Present on Windows 11 and on any Windows 10 with current Edge. `desktop.webview2_runtime()` reads the registry and, if it is missing, PRISM says so and falls back to the browser. |
| Linux | WebKitGTK 4.1 or 4.0 | A system package. The `.deb` declares it; the tarball's README names the command for each distribution family. |

Bundling WebKitGTK would mean carrying most of a browser engine plus the distribution's
graphics stack, and a bundled copy goes stale against system security updates. The cost of
not bundling it is one dependency line, which is the better trade.

## Signing

These builds are unsigned. There is no Apple Developer ID and no Windows code-signing
certificate, so:

- **macOS** builds are ad-hoc signed by `build.py`. That is not cosmetic: Apple Silicon
  refuses to run an unsigned bundle outright, so the ad-hoc signature is what turns a hard
  refusal into a Gatekeeper prompt the user can answer. They approve once, under
  **System Settings, Privacy & Security, Open Anyway**.
- **Windows** builds trip SmartScreen on first run. **More info**, then **Run anyway**.

Adding real signing later is two steps in `release.yml`: `codesign` with a Developer ID
plus `notarytool submit --wait` and `stapler staple` on macOS, and `signtool` on Windows,
both gated on repository secrets so the workflow keeps working without them.

## The icon

`icons.py` draws it, using nothing but the standard library, from the same twenty two
ruled lines that draw the prism mark in `control_center/static/index.html`. It reads that
file directly, so the icon and the interface cannot drift apart, and no binary art is ever
committed. Preview it on its own:

```bash
python3 packaging/icons.py build/icons
```

It draws three masters, not one. Twenty two rulings are the mark, but they only survive
down to about 128px; below that the crossings merge into a grey lozenge, so each smaller
band drops rulings and thickens the ones that remain until the silhouette is doing the
work. macOS gets Apple's inset squircle, Windows and Linux get a nearly full bleed tile.

## Tests

```bash
python3 -m unittest packaging.test_packaging
```

Standard library only, and no PyInstaller. They cover the payload, the workspace a build
lays down, the settings it keeps, the containers the icons are wrapped in and the recipe
that assembles them. Freezing itself is proved by the release workflow.

## Publishing

Tag the commit whose manifest, `APP_VERSION` and tag all agree. The publish job refuses
the release if they do not.

```bash
python3 control_center/build_manifest.py 1.5.0     # after every source change
python3 -m unittest control_center.test_control_center packaging.test_packaging
git commit -am "Release 1.5.0" && git tag v1.5.0 && git push --tags
```

`release.yml` then builds all four targets, writes `SHA256SUMS.txt` across every artifact,
and publishes the GitHub Release the in-app updater reads.
