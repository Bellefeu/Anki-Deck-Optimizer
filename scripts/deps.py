#!/usr/bin/env python3
"""Dependency bootstrap. Import this at the top of every script in the pipeline.

Checks for required Python packages and CLI tools, installs what's missing and
installable, and fails loudly with a copy-pasteable fix for anything it can't
handle itself.

    from deps import require
    require("zstandard")                       # single package
    require("zstandard", "PIL", cli=["pdftotext"])
"""

import importlib, os, shutil, subprocess, sys
from pathlib import Path

# import name -> pip name, where they differ
PIP_NAME = {
    "PIL": "pillow",
    "fitz": "pymupdf",
    "docx": "python-docx",
    "pytesseract": "pytesseract",
}

# CLI tool -> what provides it, for the error message
CLI_SOURCE = {
    "pdftotext": "poppler-utils",
    "pdftoppm":  "poppler-utils",
    "pdfinfo":   "poppler-utils",
    "pdfimages": "poppler-utils",
    "tesseract": "tesseract-ocr",
    "node":      "Node.js",
}


def _pip_install(pkg):
    """Try the install strategies that actually work in this environment."""
    attempts = [
        [sys.executable, "-m", "pip", "install", pkg, "--break-system-packages", "-q"],
        [sys.executable, "-m", "pip", "install", pkg, "-q"],
        [sys.executable, "-m", "pip", "install", pkg, "--user", "-q"],
    ]
    for cmd in attempts:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if r.returncode == 0:
                return True
        except Exception:
            continue
    return False


_PATH_WIDENED = []


def widen_path():
    """Put the directories PRISM searches onto this process's PATH.

    Installed and on the PATH are different questions, and only the second one
    is what a bare shutil.which can ask. The tesseract installer on Windows
    does not tick the PATH box, so a machine that has just been through guided
    setup has C:\\Program Files\\Tesseract-OCR\\tesseract.exe sitting there
    while every script here reports the toolchain missing and refuses to run.
    The dashboard already knew better: workspace.find_tool searches the
    registry and the install directories too, which is why its health panel
    disagreed with bootstrap on the same machine.

    So the answer comes from workspace.py rather than from a second list that
    would drift out of step with it. Widening PATH rather than returning it
    fixes both halves at once: the checks below see the tool, and so does
    every subprocess.run(["tesseract", ...]) the pipeline makes later.
    """
    if _PATH_WIDENED:
        return _PATH_WIDENED[0]
    widened = os.environ.get("PATH", "")
    control_center = Path(__file__).resolve().parents[1] / "control_center"
    if control_center.is_dir():
        try:
            sys.path.insert(0, str(control_center))
            import workspace
            widened = workspace.search_path()
            os.environ["PATH"] = widened
        except Exception:
            # A workspace without the dashboard beside it still works; it just
            # gets the narrower answer it would have had anyway.
            pass
    _PATH_WIDENED.append(widened)
    return widened


def require(*modules, cli=None, quiet=False):
    """Ensure Python modules and CLI tools are available. Installs what it can."""
    missing_fatal = []
    if cli:
        widen_path()

    for mod in modules:
        try:
            importlib.import_module(mod)
            if not quiet:
                print(f"  [deps] {mod}: ok")
            continue
        except ImportError:
            pass

        pkg = PIP_NAME.get(mod, mod)
        print(f"  [deps] {mod}: missing - installing '{pkg}'...")
        if _pip_install(pkg):
            importlib.invalidate_caches()
            try:
                importlib.import_module(mod)
                print(f"  [deps] {mod}: installed ok")
                continue
            except ImportError:
                pass
        missing_fatal.append(f"pip install {pkg} --break-system-packages")

    for tool in (cli or []):
        if shutil.which(tool):
            if not quiet:
                print(f"  [deps] {tool}: ok")
        else:
            src = CLI_SOURCE.get(tool, tool)
            missing_fatal.append(f"install {src}  (provides '{tool}')")

    if missing_fatal:
        print("\n!! MISSING DEPENDENCIES - run these, then retry:\n")
        for m in missing_fatal:
            print(f"    {m}")
        print()
        sys.exit(1)


if __name__ == "__main__":
    print("Checking the full pipeline toolchain...\n")
    require("zstandard", "PIL",
            cli=["pdftotext", "pdftoppm", "pdfinfo", "pdfimages", "tesseract"])
    print("\nAll dependencies satisfied.")
