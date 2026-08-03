#!/usr/bin/env python3
"""Dependency bootstrap. Import this at the top of every script in the pipeline.

Checks for required Python packages and CLI tools, installs what's missing and
installable, and fails loudly with a copy-pasteable fix for anything it can't
handle itself.

    from deps import require
    require("zstandard")                       # single package
    require("zstandard", "PIL", cli=["pdftotext"])
"""

import importlib, shutil, subprocess, sys

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


def require(*modules, cli=None, quiet=False):
    """Ensure Python modules and CLI tools are available. Installs what it can."""
    missing_fatal = []

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
