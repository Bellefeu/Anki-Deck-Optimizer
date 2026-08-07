#!/usr/bin/env python3
"""Build PRISM for whichever operating system this is running on.

There is no cross compiling here and there cannot be: a frozen interpreter is
native code, and the web view it drives is a different library on each system.
So this script does one platform, the one it is standing on, and the release
workflow runs it once per runner.

    python3 appbuild/build.py                 # everything for this platform
    python3 appbuild/build.py --no-installers # just the frozen application

What comes out:

    macOS    dist/PRISM.app, dist/PRISM-<version>-macOS-<arch>.dmg
    Windows  dist/PRISM.exe, dist/PRISM-<version>-windows-portable.zip
    Linux    dist/PRISM-<version>-linux-<arch>.tar.gz, dist/prism_<version>_<arch>.deb

The payload is checked against scripts/UPDATE_MANIFEST.json before anything is
frozen. A build whose payload disagrees with the manifest would produce an
application that refuses to create a workspace, which is a far more confusing
failure at the far end than a refusal here.
"""

from __future__ import annotations

import argparse
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"
# The single folder the release workflow uploads. Everything a user downloads
# is copied here and nothing else is, so no other file needs to know a name.
RELEASE = DIST / "release"
WORK = BUILD / "pyinstaller"
ICONS = BUILD / "icons"
PAYLOAD = BUILD / "payload"

sys.path.insert(0, str(ROOT / "control_center"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import workspace as ws  # noqa: E402
import icons as icon_art  # noqa: E402


APP_NAME = "PRISM"
BUNDLE_ID = "com.bellefeu.prism"
PUBLISHER = "Bellefeu"
DESCRIPTION = "Dashboard for the Anki LLM Optimizer"
HOMEPAGE = "https://github.com/Bellefeu/Anki-LLM-Optimizer"
ENTRY = ROOT / "control_center/desktop.py"

# Left out of the freeze. tkinter alone costs about ten megabytes and a Tcl/Tk
# framework that macOS then wants signed; the window supplies the folder
# picker, and the fallback path asks for a typed path instead.
EXCLUDED = (
    "tkinter", "test", "lib2to3", "pydoc_data",
    "numpy", "PIL", "matplotlib", "pytest", "pip",
)

# Excluding these does not trim the application, it stops PyInstaller.
#
# PyInstaller ships a pre-safe-import hook that aliases setuptools' vendored
# copy of distutils onto the name "distutils". If distutils has already been
# marked excluded when that hook runs, the alias raises outright and the whole
# analysis dies. Excluding setuptools breaks the same hook from the other end,
# by removing what the alias points at.
#
# Only Windows ever reached it, because pythonnet and clr_loader are the only
# dependencies that drag setuptools into the graph, and they exist on no other
# platform. A local macOS build cannot reproduce this.
UNSAFE_TO_EXCLUDE = ("distutils", "setuptools")

HIDDEN = {
    "common": ("app", "updater", "workspace", "state_io", "webview"),
    "darwin": ("webview.platforms.cocoa", "objc", "Foundation", "AppKit", "WebKit"),
    "win32": ("webview.platforms.edgechromium", "webview.platforms.winforms",
              "clr", "clr_loader", "pythonnet"),
    "linux": ("webview.platforms.gtk", "gi", "gi.repository.Gtk", "gi.repository.WebKit2"),
}


class BuildError(RuntimeError):
    pass


def announce(message):
    print(f"\n\033[1m==>\033[0m {message}", flush=True)


def run(argv, **kwargs):
    printable = " ".join(str(part) for part in argv)
    print(f"    $ {printable}", flush=True)
    result = subprocess.run([str(part) for part in argv], **kwargs)
    if result.returncode != 0:
        raise BuildError(f"Command failed ({result.returncode}): {printable}")
    return result


def system():
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def machine():
    value = platform.machine().lower()
    return {"amd64": "x86_64", "x86_64": "x86_64",
            "arm64": "arm64", "aarch64": "arm64"}.get(value, value)


def debian_arch():
    return {"x86_64": "amd64", "arm64": "arm64"}.get(machine(), machine())


def macos_label(arch=None):
    """What Apple's own customers call the two Macs, not what uname does.

    Someone standing in front of a release page knows whether their Mac is
    Apple Silicon or Intel. Almost nobody knows whether it is arm64.
    """
    return {"arm64": "AppleSilicon", "x86_64": "Intel"}.get(arch or machine(), arch or machine())


def version():
    return ws.APP_VERSION


# --------------------------------------------------------------------------
# Preflight


def check_tooling():
    try:
        import PyInstaller  # noqa: F401
    except ImportError as exc:
        raise BuildError(
            "PyInstaller is not installed. Run:\n"
            "    python3 -m pip install -r appbuild/requirements-build.txt"
        ) from exc
    try:
        import webview  # noqa: F401
    except ImportError as exc:
        raise BuildError(
            "pywebview is not installed, so the built application would have no "
            "window. Run:\n"
            "    python3 -m pip install -r appbuild/requirements-build.txt"
        ) from exc
    if sys.version_info < (3, 10):
        raise BuildError(
            f"PRISM targets Python 3.10 or newer; this is {platform.python_version()}."
        )


def stage_payload():
    """Copy the publisher-owned tree the application will carry inside it."""
    problems = ws.verify_payload(ROOT)
    if problems["missing"] or problems["altered"]:
        detail = []
        if problems["missing"]:
            detail.append("missing: " + ", ".join(problems["missing"]))
        if problems["altered"]:
            detail.append("altered: " + ", ".join(problems["altered"]))
        raise BuildError(
            "The working tree does not match scripts/UPDATE_MANIFEST.json, so the "
            "payload would be rejected by the application it is built into.\n  "
            + "\n  ".join(detail)
            + "\n\nRebuild the manifest first:\n"
            f"    python3 control_center/build_manifest.py {version()}"
        )
    if PAYLOAD.exists():
        shutil.rmtree(PAYLOAD)
    relatives = list(ws.payload_files(ROOT)) + list(ws.UNHASHED_PAYLOAD)
    for relative in relatives:
        target = PAYLOAD / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    print(f"    {len(relatives)} payload files staged in {PAYLOAD.relative_to(ROOT)}")
    return PAYLOAD


def draw_icons():
    icon_art.write_icons(ICONS, quiet=True)
    print(f"    icons written to {ICONS.relative_to(ROOT)}")
    return ICONS


# --------------------------------------------------------------------------
# Freezing


def windows_version_resource(destination):
    """A VERSIONINFO block so Explorer's properties panel says PRISM."""
    parts = [int(piece) for piece in version().split(".")] + [0, 0, 0, 0]
    quad = ", ".join(str(piece) for piece in parts[:4])
    # Owns its own directory rather than trusting a caller to have made it.
    # This is written before PyInstaller runs, which is what creates the work
    # folder, and only on Windows, so getting the order wrong here fails on
    # exactly one of the four runners.
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({quad}), prodvers=({quad}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0),
  ),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '{PUBLISHER}'),
      StringStruct('FileDescription', '{DESCRIPTION}'),
      StringStruct('FileVersion', '{version()}'),
      StringStruct('InternalName', '{APP_NAME}'),
      StringStruct('OriginalFilename', '{APP_NAME}.exe'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{version()}'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ],
)
""", encoding="utf-8")
    return destination


def freeze(*, onefile, name=APP_NAME):
    WORK.mkdir(parents=True, exist_ok=True)
    separator = os.pathsep
    icon = {"darwin": ICONS / "PRISM.icns", "win32": ICONS / "PRISM.ico"}.get(
        system(), ICONS / "prism-256.png")

    argv = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--log-level", "WARN",
        "--name", name,
        "--onefile" if onefile else "--onedir",
        "--distpath", str(DIST), "--workpath", str(WORK), "--specpath", str(WORK),
        "--paths", str(ROOT / "control_center"),
        "--paths", str(ROOT / "scripts"),
        "--add-data", f"{PAYLOAD}{separator}payload",
        "--add-data", f"{ROOT / 'control_center/static'}{separator}control_center/static",
        "--icon", str(icon),
    ]
    if system() in ("darwin", "win32"):
        argv.append("--windowed")
    if system() == "darwin":
        argv += ["--osx-bundle-identifier", BUNDLE_ID]
    if system() == "win32":
        resource = windows_version_resource(WORK / "version.txt")
        argv += ["--version-file", str(resource)]
    for module in HIDDEN["common"] + HIDDEN.get(system(), ()):
        argv += ["--hidden-import", module]
    argv += ["--collect-submodules", "webview"]
    for module in EXCLUDED:
        argv += ["--exclude-module", module]
    argv.append(str(ENTRY))
    run(argv, cwd=str(ROOT))


# --------------------------------------------------------------------------
# macOS


MACOS_PLIST = {
    "CFBundleName": APP_NAME,
    "CFBundleDisplayName": APP_NAME,
    "CFBundleIdentifier": BUNDLE_ID,
    "CFBundleExecutable": APP_NAME,
    "CFBundleIconFile": "PRISM.icns",
    "CFBundlePackageType": "APPL",
    "LSApplicationCategoryType": "public.app-category.education",
    "LSMinimumSystemVersion": "11.0",
    "NSHighResolutionCapable": True,
    # PRISM is drawn on pure black and reads as a dark application whatever
    # the system theme is, so it must not be given the light-mode shim.
    "NSRequiresAquaSystemAppearance": False,
    "NSHumanReadableCopyright": f"{PUBLISHER}. MIT licensed.",
}


def finish_macos(*, installers):
    bundle = DIST / f"{APP_NAME}.app"
    if not bundle.is_dir():
        raise BuildError(f"PyInstaller did not produce {bundle}")

    plist_path = bundle / "Contents/Info.plist"
    with open(plist_path, "rb") as handle:
        info = plistlib.load(handle)
    info.update(MACOS_PLIST)
    info["CFBundleShortVersionString"] = version()
    info["CFBundleVersion"] = version()
    with open(plist_path, "wb") as handle:
        plistlib.dump(info, handle)
    print(f"    Info.plist updated for {APP_NAME} {version()}")

    # Apple Silicon refuses to run an unsigned bundle outright, so an ad-hoc
    # signature is not cosmetic. It is not a Developer ID and Gatekeeper will
    # still ask; it is what makes the ask possible instead of a hard refusal.
    run(["codesign", "--force", "--deep", "--timestamp=none", "--sign", "-", str(bundle)])
    run(["codesign", "--verify", "--deep", "--strict", str(bundle)])

    if not installers:
        return {"built": [bundle], "deliverables": []}
    image = build_dmg(bundle)
    return {"built": [bundle], "deliverables": [image] if image else []}


def build_dmg(bundle):
    if not shutil.which("hdiutil"):
        print("    hdiutil is unavailable, skipping the disk image")
        return None
    staging = BUILD / "dmg"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.copytree(bundle, staging / bundle.name, symlinks=True)
    os.symlink("/Applications", staging / "Applications")
    output = DIST / f"{APP_NAME}-{version()}-macOS-{macos_label()}.dmg"
    output.unlink(missing_ok=True)
    run(["hdiutil", "create", "-volname", APP_NAME, "-srcfolder", str(staging),
         "-ov", "-format", "UDZO", "-quiet", str(output)])
    return output


# --------------------------------------------------------------------------
# Windows


def finish_windows(*, installers):
    executable = DIST / f"{APP_NAME}.exe"
    if not executable.is_file():
        raise BuildError(f"PyInstaller did not produce {executable}")
    if not installers:
        return {"built": [], "deliverables": [executable]}

    # A second, unpacked build for people whose antivirus dislikes a single
    # file that unpacks itself into the temporary folder on every launch, and
    # for anyone who would rather PRISM started in well under a second.
    announce("Building the portable folder")
    freeze(onefile=False, name=f"{APP_NAME}-portable")
    folder = DIST / f"{APP_NAME}-portable"
    if not folder.is_dir():
        raise BuildError(f"PyInstaller did not produce {folder}")
    archive = DIST / f"{APP_NAME}-{version()}-windows-portable.zip"
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                bundle.write(path, Path(APP_NAME) / path.relative_to(folder))
    return {"built": [folder], "deliverables": [executable, archive]}


# --------------------------------------------------------------------------
# Linux


DESKTOP_ENTRY = f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
GenericName=Anki deck optimizer
Comment={DESCRIPTION}
Exec=prism %U
Icon=prism
Terminal=false
Categories=Education;Office;
StartupWMClass={APP_NAME}
Keywords=anki;flashcards;study;decks;
"""

LAUNCHER = f"""#!/bin/sh
# Start PRISM from wherever this folder was unpacked.
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$here/{APP_NAME}/{APP_NAME}" "$@"
"""

INSTALLER = f"""#!/bin/sh
# Install PRISM for the current user only. No root, no system packages.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
target="$HOME/.local/opt/prism"
mkdir -p "$target" "$HOME/.local/bin" "$HOME/.local/share/applications"
rm -rf "$target"
cp -R "$here/{APP_NAME}" "$target"
ln -sf "$target/{APP_NAME}" "$HOME/.local/bin/prism"
for size in 16 24 32 48 64 128 256 512; do
  dir="$HOME/.local/share/icons/hicolor/${{size}}x${{size}}/apps"
  mkdir -p "$dir"
  cp "$here/icons/prism-$size.png" "$dir/prism.png" 2>/dev/null || true
done
sed 's|^Exec=prism|Exec='"$HOME"'/.local/bin/prism|' "$here/prism.desktop" \\
  > "$HOME/.local/share/applications/prism.desktop"
command -v update-desktop-database >/dev/null 2>&1 \\
  && update-desktop-database "$HOME/.local/share/applications" || true
echo "PRISM installed. Launch it from your applications menu, or run: prism"
"""

# WebKitGTK is deliberately not bundled. It pulls in most of a browser engine
# plus the distribution's own graphics stack, and a bundled copy goes stale
# against system security updates. Both names are offered because 4.1 and 4.0
# coexist across supported releases.
DEB_DEPENDS = ("libgtk-3-0", "gir1.2-webkit2-4.1 | gir1.2-webkit2-4.0",
               "python3-gi", "libglib2.0-0")


def finish_linux(*, installers):
    folder = DIST / APP_NAME
    if not folder.is_dir():
        raise BuildError(f"PyInstaller did not produce {folder}")
    if not installers:
        return {"built": [folder], "deliverables": []}
    deliverables = [build_tarball(folder)]
    package = build_deb(folder)
    if package:
        deliverables.append(package)
    return {"built": [folder], "deliverables": deliverables}


def build_tarball(folder):
    staging = BUILD / "tarball"
    if staging.exists():
        shutil.rmtree(staging)
    root = staging / f"{APP_NAME}-{version()}"
    root.mkdir(parents=True)
    shutil.copytree(folder, root / APP_NAME, symlinks=True)
    shutil.copytree(ICONS, root / "icons")
    (root / "prism.desktop").write_text(DESKTOP_ENTRY, encoding="utf-8")
    for name, body in (("prism", LAUNCHER), ("install.sh", INSTALLER)):
        script = root / name
        script.write_text(body, encoding="utf-8")
        script.chmod(0o755)
    (root / "README.txt").write_text(
        f"{APP_NAME} {version()}\n\n"
        "Run ./prism to start it straight from this folder, or ./install.sh to\n"
        "add it to your applications menu for this user only.\n\n"
        "PRISM draws its window with WebKitGTK, which is not bundled. If the\n"
        "window does not open, install it:\n\n"
        "  Debian, Ubuntu:  sudo apt install python3-gi gir1.2-webkit2-4.1\n"
        "  Fedora:          sudo dnf install python3-gobject webkit2gtk4.1\n"
        "  Arch:            sudo pacman -S python-gobject webkit2gtk-4.1\n",
        encoding="utf-8",
    )
    output = DIST / f"{APP_NAME}-{version()}-linux-{machine()}.tar.gz"
    output.unlink(missing_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        archive.add(root, arcname=root.name)
    return output


def build_deb(folder):
    if not shutil.which("dpkg-deb"):
        print("    dpkg-deb is unavailable, skipping the Debian package")
        return None
    staging = BUILD / "deb"
    if staging.exists():
        shutil.rmtree(staging)
    opt = staging / "opt/prism"
    opt.parent.mkdir(parents=True)
    shutil.copytree(folder, opt, symlinks=True)

    (staging / "usr/bin").mkdir(parents=True)
    os.symlink("/opt/prism/PRISM", staging / "usr/bin/prism")

    applications = staging / "usr/share/applications"
    applications.mkdir(parents=True)
    (applications / "prism.desktop").write_text(DESKTOP_ENTRY, encoding="utf-8")

    for size in (16, 24, 32, 48, 64, 128, 256, 512):
        source = ICONS / f"prism-{size}.png"
        if not source.is_file():
            continue
        target = staging / f"usr/share/icons/hicolor/{size}x{size}/apps"
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target / "prism.png")

    doc = staging / "usr/share/doc/prism"
    doc.mkdir(parents=True)
    shutil.copy2(ROOT / "LICENSE", doc / "copyright")

    control = staging / "DEBIAN"
    control.mkdir(parents=True)
    size_kb = sum(path.stat().st_size for path in staging.rglob("*") if path.is_file()) // 1024
    (control / "control").write_text(
        f"Package: prism\n"
        f"Version: {version()}\n"
        f"Section: education\n"
        f"Priority: optional\n"
        f"Architecture: {debian_arch()}\n"
        f"Depends: {', '.join(DEB_DEPENDS)}\n"
        f"Installed-Size: {size_kb}\n"
        f"Maintainer: {PUBLISHER} <noreply@users.noreply.github.com>\n"
        f"Homepage: {HOMEPAGE}\n"
        f"Description: {DESCRIPTION}\n"
        f" PRISM stages study modules, reviews rebuilt Anki decks and keeps the\n"
        f" optimizer toolkit up to date, in one window.\n",
        encoding="utf-8",
    )
    output = DIST / f"prism_{version()}_{debian_arch()}.deb"
    output.unlink(missing_ok=True)
    run(["dpkg-deb", "--root-owner-group", "--build", str(staging), str(output)])
    return output


# --------------------------------------------------------------------------


def human(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def weigh(path):
    if path is None:
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-installers", action="store_true",
                        help="Freeze the application but skip the .dmg, .zip, .deb and .tar.gz.")
    parser.add_argument("--keep-build", action="store_true",
                        help="Leave the intermediate build folder in place.")
    args = parser.parse_args(argv)

    if system() not in ("darwin", "win32", "linux"):
        raise BuildError(f"PRISM has no build recipe for {sys.platform}.")

    announce(f"PRISM {version()} for {system()} {machine()}, Python {platform.python_version()}")
    check_tooling()

    announce("Checking and staging the toolkit payload")
    stage_payload()

    announce("Drawing the application icon")
    draw_icons()

    announce("Freezing the application")
    if DIST.exists():
        shutil.rmtree(DIST)
    freeze(onefile=(system() == "win32"))

    announce("Packaging")
    installers = not args.no_installers
    result = {
        "darwin": finish_macos,
        "win32": finish_windows,
        "linux": finish_linux,
    }[system()](installers=installers)

    if not args.keep_build and WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)

    # Everything meant for a human to download is gathered into one folder,
    # so that the release workflow can upload a directory without knowing a
    # single filename. Naming them in both places is how the first run
    # shipped a step that copied dist/PRISM.exe on macOS.
    deliverables = [path for path in result["deliverables"] if path is not None]
    if installers and not deliverables:
        raise BuildError("Packaging produced nothing to publish.")
    RELEASE.mkdir(parents=True, exist_ok=True)
    collected = []
    for path in deliverables:
        target = RELEASE / path.name
        shutil.copy2(path, target)
        collected.append(target)

    announce("Done")
    for path in result["built"]:
        print(f"    built        {path.relative_to(ROOT)}  ({human(weigh(path))})")
    for path in collected:
        print(f"    to publish   {path.relative_to(ROOT)}  ({human(weigh(path))})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BuildError as error:
        print(f"\nBuild failed: {error}\n", file=sys.stderr)
        sys.exit(2)
