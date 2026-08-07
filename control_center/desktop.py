#!/usr/bin/env python3
"""PRISM as an application: one window, one icon, no terminal, no browser.

The dashboard is still served over the loopback interface, because that is
what the whole front end is written against and because a 4 GB deck upload
wants a real HTTP body. What changes here is everything around it. The server
binds an ephemeral port, never leaves this machine, and is read by a system
web view hosted in a window this process owns: WKWebView on macOS, WebView2
on Windows, WebKitGTK on Linux. There is no address bar, no browser profile,
no second application in the dock.

    python3 control_center/desktop.py            # from a checkout
    PRISM.exe                                    # from a build
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import workspace as ws  # noqa: E402  (path is arranged just above)


WINDOW_TITLE = "PRISM"
DEFAULT_SIZE = (1280, 860)
MINIMUM_SIZE = (900, 620)
VOID = "#000000"

# Microsoft's published detection key for the WebView2 Evergreen Runtime.
WEBVIEW2_CLIENT = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_DOWNLOAD = "https://developer.microsoft.com/microsoft-edge/webview2/"

log = logging.getLogger("prism")


# --------------------------------------------------------------------------
# Logging


def start_logging(debug=False):
    level = logging.DEBUG if debug else logging.INFO
    handlers = [logging.StreamHandler(sys.stderr)]
    try:
        ws.state_dir().mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(ws.log_path(), encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        handlers=handlers,
        force=True,
    )
    log.debug("PRISM %s starting, frozen=%s", ws.APP_VERSION, ws.frozen())


# --------------------------------------------------------------------------
# Telling the user something went wrong before there is a window to say it in


def native_alert(title, message):
    """A message box drawn by the OS, for failures that precede the window."""
    try:
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
                None, str(message), str(title), 0x00000010 | 0x00040000,
            )
            return True
        if sys.platform == "darwin":
            script = (
                f'display dialog {json.dumps(str(message))} with title '
                f'{json.dumps(str(title))} buttons {{"OK"}} default button "OK" '
                'with icon caution'
            )
            subprocess.run(["osascript", "-e", script], check=False, timeout=120)
            return True
        for tool, argv in (
            ("zenity", ["zenity", "--error", f"--title={title}", f"--text={message}"]),
            ("kdialog", ["kdialog", "--title", str(title), "--error", str(message)]),
        ):
            import shutil as _shutil

            if _shutil.which(tool):
                subprocess.run(argv, check=False, timeout=120)
                return True
    except Exception:  # pragma: no cover - a failed alert must not mask the cause
        log.exception("Could not show a native alert")
    return False


def fail(title, message, *, code=1):
    log.error("%s: %s", title, message)
    print(f"{title}\n\n{message}", file=sys.stderr)
    native_alert(title, message)
    return code


# --------------------------------------------------------------------------
# Is there a web view on this machine


def webview2_runtime():
    """The installed WebView2 runtime version on Windows, or None."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:  # pragma: no cover - Windows only
        return None
    locations = (
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT}"),
        (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT}"),
    )
    for hive, key in locations:
        try:
            with winreg.OpenKey(hive, key) as handle:
                version, _ = winreg.QueryValueEx(handle, "pv")
                if version and str(version) not in ("", "0.0.0.0"):
                    return str(version)
        except OSError:
            continue
    return None


def gtk_webkit_version():
    """The WebKitGTK typelib version usable on this Linux machine, or None."""
    if sys.platform not in ("linux", "linux2"):
        return None
    try:
        import gi
    except ImportError:
        return None
    try:
        gi.require_version("Gtk", "3.0")
    except ValueError:
        return None
    for candidate in ("4.1", "4.0"):
        try:
            gi.require_version("WebKit2", candidate)
            return candidate
        except ValueError:
            continue
    return None


def runtime_report():
    """Whether a system web view is present, and what to say when it is not."""
    if sys.platform == "darwin":
        return {"ok": True, "engine": "WKWebView", "version": "", "remedy": ""}
    if sys.platform == "win32":
        version = webview2_runtime()
        return {
            "ok": bool(version),
            "engine": "WebView2",
            "version": version or "",
            "remedy": (
                "PRISM draws its window with the Microsoft Edge WebView2 runtime, "
                "which is missing on this PC. Install the free Evergreen Runtime "
                f"from {WEBVIEW2_DOWNLOAD} and start PRISM again."
            ),
        }
    version = gtk_webkit_version()
    return {
        "ok": bool(version),
        "engine": "WebKitGTK",
        "version": version or "",
        "remedy": (
            "PRISM draws its window with WebKitGTK, which is missing on this "
            "system. Install it with one of:\n\n"
            "  Debian, Ubuntu:  sudo apt install python3-gi gir1.2-webkit2-4.1\n"
            "  Fedora:          sudo dnf install python3-gobject webkit2gtk4.1\n"
            "  Arch:            sudo pacman -S python-gobject webkit2gtk-4.1\n\n"
            "Then start PRISM again."
        ),
    }


def import_webview():
    """The pywebview module, or None with the reason logged."""
    try:
        import webview  # noqa: F401

        return webview
    except Exception as exc:
        log.warning("pywebview is unavailable: %s", exc)
        return None


# --------------------------------------------------------------------------
# One PRISM at a time


def read_session():
    try:
        stored = json.loads(ws.session_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(stored, dict):
        return None
    if not all(isinstance(stored.get(key), value) for key, value in
               (("port", int), ("token", str))):
        return None
    return stored


def focus_running_instance(timeout=1.5):
    """Ask an already running PRISM to come forward. True if one answered."""
    session = read_session()
    if not session:
        return False
    request = urllib.request.Request(
        f"http://127.0.0.1:{session['port']}/api/shell/focus",
        data=b"{}",
        method="POST",
        headers={"X-Control-Token": session["token"], "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            answered = response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        answered = False
    if not answered:
        try:
            ws.session_path().unlink()
        except OSError:
            pass
    return answered


def write_session(port, token):
    path = ws.session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"pid": os.getpid(), "port": int(port), "token": token, "started": time.time()}
    ).encode("utf-8")
    # The token authorises every write the dashboard can make, so the record
    # of it is readable only by this account.
    handle = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "wb") as out:
        out.write(payload)


def clear_session():
    try:
        ws.session_path().unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------
# Window placement


def _whole(value, fallback):
    """A stored number, or the fallback. Settings on disk are user editable
    and can be truncated by a power cut, so nothing read from them is
    allowed to be the reason PRISM will not open."""
    try:
        if isinstance(value, bool) or value is None:
            raise TypeError
        return int(value)
    except (TypeError, ValueError):
        return fallback


def clamp_geometry(stored, screens):
    """Restored geometry, but never off every screen and never absurd."""
    width = _whole(stored.get("width"), DEFAULT_SIZE[0]) or DEFAULT_SIZE[0]
    height = _whole(stored.get("height"), DEFAULT_SIZE[1]) or DEFAULT_SIZE[1]
    width = max(MINIMUM_SIZE[0], min(width, 6000))
    height = max(MINIMUM_SIZE[1], min(height, 4000))
    x, y = stored.get("x"), stored.get("y")
    if isinstance(x, bool) or isinstance(y, bool):
        x = y = None
    if not isinstance(x, int) or not isinstance(y, int) or not screens:
        return {"width": width, "height": height, "x": None, "y": None}
    # A title bar the user cannot reach is worse than a window that reopens
    # centred, so an off screen position is simply discarded.
    visible = any(
        x + width > screen[0] + 40 and x < screen[0] + screen[2] - 40
        and y + 60 > screen[1] and y < screen[1] + screen[3] - 40
        for screen in screens
    )
    if not visible:
        return {"width": width, "height": height, "x": None, "y": None}
    return {"width": width, "height": height, "x": x, "y": y}


def screen_rectangles(webview):
    rectangles = []
    try:
        for screen in webview.screens:
            rectangles.append((
                int(getattr(screen, "x", 0) or 0), int(getattr(screen, "y", 0) or 0),
                int(screen.width), int(screen.height),
            ))
    except Exception:
        log.debug("Could not enumerate screens", exc_info=True)
    return rectangles


# --------------------------------------------------------------------------
# macOS chrome
#
# The dashboard is a black field with light falling off into it, and a grey
# title bar ruled across the top of that is exactly the hard boundary the
# design spends its effort avoiding. The window keeps its real title bar, so
# dragging, double click to zoom and the traffic lights all behave natively,
# but the bar is made transparent and the content is allowed to run under it.


def main_thread(work):
    """Run ``work`` on the thread AppKit insists on, or not at all.

    Every Cocoa call below has to happen on the main thread. Making them from
    the thread pywebview runs its start callback on does not raise; it takes
    the whole process down with no traceback, which is a memorable afternoon.
    """
    if sys.platform != "darwin":
        return False
    try:
        from PyObjCTools import AppHelper  # type: ignore

        AppHelper.callAfter(work)
        return True
    except Exception:
        log.debug("Could not reach the main thread", exc_info=True)
        return False


def apply_macos_chrome():
    """Make the title bar transparent and let the field run under it.

    Must already be on the main thread. Returns False while no window has
    been made yet, which is why the caller retries.
    """
    if sys.platform != "darwin":
        return False
    try:
        from AppKit import NSApp, NSColor  # type: ignore
    except Exception:
        log.debug("AppKit is unavailable, keeping the standard title bar", exc_info=True)
        return False

    NSWindowStyleMaskFullSizeContentView = 1 << 15
    NSWindowTitleHidden = 1
    applied = False
    try:
        windows = list(NSApp().windows())
    except Exception:
        log.debug("No application object yet", exc_info=True)
        return False
    for window in windows:
        try:
            window.setStyleMask_(window.styleMask() | NSWindowStyleMaskFullSizeContentView)
            window.setTitlebarAppearsTransparent_(True)
            window.setTitleVisibility_(NSWindowTitleHidden)
            window.setBackgroundColor_(NSColor.blackColor())
            window.setOpaque_(True)
            applied = True
        except Exception:
            log.debug("A window refused the unified title bar", exc_info=True)
    return applied


def schedule_macos_chrome(attempts=24, delay=0.12):
    """Keep asking the main thread until there is a window to restyle."""
    if sys.platform != "darwin":
        return
    try:
        from PyObjCTools import AppHelper  # type: ignore
    except Exception:
        log.debug("PyObjCTools is unavailable, keeping the standard title bar", exc_info=True)
        return

    remaining = {"count": attempts}

    def attempt():
        remaining["count"] -= 1
        try:
            done = apply_macos_chrome()
        except Exception:
            log.debug("Restyling the title bar failed", exc_info=True)
            return
        if not done and remaining["count"] > 0:
            AppHelper.callLater(delay, attempt)

    AppHelper.callAfter(attempt)


def apply_windows_identity():
    """Group PRISM under its own taskbar button and its own icon."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined]
            "Bellefeu.PRISM"
        )
    except Exception:
        log.debug("Could not set the Windows application identity", exc_info=True)


def chrome_mode():
    return "inset" if sys.platform == "darwin" else "system"


# --------------------------------------------------------------------------
# Menu


def build_menu(webview, shell):
    """A small native menu. Anything the backend cannot draw is skipped."""
    try:
        from webview.menu import Menu, MenuAction, MenuSeparator
    except Exception:
        log.debug("This pywebview build has no menu support", exc_info=True)
        return []

    def view(name):
        return lambda: shell.show_view(name)

    try:
        return [
            Menu("Workspace", [
                MenuAction("Open a different folder", shell.choose_workspace),
                MenuAction("Create a new workspace", shell.create_workspace_interactive),
                MenuSeparator(),
                MenuAction("Reveal the workspace", shell.reveal_workspace),
            ]),
            Menu("Go", [
                MenuAction("Home", view("home")),
                MenuAction("Start guide", view("guide")),
                MenuAction("Deck review", view("decks")),
                MenuAction("Preferences", view("preferences")),
                MenuAction("Updates and setup", view("updates")),
            ]),
            Menu("Window", [
                MenuAction("Reload", shell.reload),
                MenuAction("Full screen", shell.toggle_fullscreen),
            ]),
            Menu("Help", [
                MenuAction(f"PRISM {ws.APP_VERSION}", shell.about),
                MenuAction("Open the log folder", shell.reveal_logs),
            ]),
        ]
    except Exception:
        log.debug("Could not assemble the menu", exc_info=True)
        return []


# --------------------------------------------------------------------------
# The shell


def _raise_macos():
    """Deminiaturise, front and activate, all on the main thread.

    Deliberately Cocoa rather than pywebview: its cocoa backend calls
    ``deminiaturize_`` straight through with no main-thread dispatch, and
    this runs on whichever request thread the second launch arrived on.
    """
    try:
        from AppKit import NSApp  # type: ignore

        application = NSApp()
        for window in application.windows():
            try:
                if window.isMiniaturized():
                    window.deminiaturize_(None)
                window.makeKeyAndOrderFront_(None)
            except Exception:
                log.debug("A window would not come forward", exc_info=True)
        application.activateIgnoringOtherApps_(True)
    except Exception:
        log.debug("Could not activate the application", exc_info=True)


class Shell:
    """Everything the window can be asked to do from a menu or from the page."""

    def __init__(self, application, server):
        self.application = application
        self.server = server
        self.window = None
        self.webview = None
        # Geometry is tracked from the events the platform fires on its own
        # thread rather than read back on demand. Reading it means asking the
        # window toolkit about a window, which is only safe from the main
        # thread and not safe at all once the window has been destroyed.
        self.geometry = {}
        self._asked = None
        self._offset = (0, 0)
        self._calibrated = False
        self._echo_until = 0.0

    # -- lifecycle ---------------------------------------------------------

    def attach(self, webview, window, geometry=None):
        self.webview = webview
        self.window = window
        if geometry:
            self._asked = (int(geometry["width"]), int(geometry["height"]))
            self.geometry = {key: value for key, value in geometry.items() if value is not None}

    def open_calibration(self, seconds=4.0):
        """Begin the window in which a size report counts as the platform's
        own echo rather than as something the user did."""
        self._echo_until = time.monotonic() + seconds

    def focus(self):
        """Bring the window forward. Called by a second launch of PRISM."""
        if self.window is None:
            return False
        if sys.platform == "darwin":
            return main_thread(_raise_macos)
        # Every other backend dispatches these to its own UI thread.
        try:
            self.window.restore()
        except Exception:
            log.debug("restore() is unavailable on this backend", exc_info=True)
        try:
            self.window.on_top = True
            self.window.on_top = False
        except Exception:
            log.debug("Could not raise the window", exc_info=True)
        return True

    def note_size(self, width, height):
        """Record a size in the units ``create_window`` consumes.

        A platform does not necessarily report back the same measurement it
        was given. macOS answers a requested height with the height minus a
        title bar, so storing what it reports and then asking for that number
        next launch loses a title bar every session, and the window creeps
        down to the minimum over a fortnight. The gap is measured once, from
        the report that arrives while the window is still being opened, and
        added back to everything saved afterwards.
        """
        width, height = int(width), int(height)
        if (not self._calibrated and self._asked
                and time.monotonic() < self._echo_until):
            self._calibrated = True
            gap = (self._asked[0] - width, self._asked[1] - height)
            # A plausible title bar, not a window the user just dragged.
            if all(abs(value) <= 200 for value in gap):
                self._offset = gap
        self.geometry.update({"width": width + self._offset[0],
                              "height": height + self._offset[1]})

    def note_position(self, x, y):
        self.geometry.update({"x": int(x), "y": int(y)})

    def remember_geometry(self, *_):
        if not self.geometry:
            return
        try:
            ws.save_window_state(dict(self.geometry))
        except Exception:
            log.debug("Could not record the window position", exc_info=True)

    # -- page control ------------------------------------------------------

    def _evaluate(self, script):
        if self.window is None:
            return None
        try:
            return self.window.evaluate_js(script)
        except Exception:
            log.debug("Could not run script in the page: %s", script, exc_info=True)
            return None

    def show_view(self, name):
        self._evaluate(f"window.prismShell && window.prismShell.showView({json.dumps(name)})")

    def notify(self, message, kind="success"):
        self._evaluate(
            f"window.prismShell && window.prismShell.toast({json.dumps(message)}, {json.dumps(kind)})"
        )

    def refresh(self):
        self._evaluate("window.prismShell && window.prismShell.refresh()")

    def reload(self):
        if self.window is not None:
            try:
                self.window.load_url(self.application.url)
            except Exception:
                log.debug("Could not reload the window", exc_info=True)

    def toggle_fullscreen(self):
        if self.window is not None:
            try:
                self.window.toggle_fullscreen()
            except Exception:
                log.debug("Could not toggle full screen", exc_info=True)

    def about(self):
        toolkit = "no workspace open"
        if self.application.root is not None:
            try:
                toolkit = f"toolkit {self.application.toolkit_version()}"
            except Exception:
                toolkit = "toolkit version unavailable"
        self.notify(f"PRISM {ws.APP_VERSION}, {toolkit}", "success")

    # -- folders -----------------------------------------------------------

    def pick_folder(self, title, start=None):
        """A native folder chooser drawn by the window, not by Tk."""
        window = self.window
        if window is None or self.webview is None:
            return None
        try:
            chosen = window.create_file_dialog(
                self.webview.FOLDER_DIALOG,
                directory=str(start or ws.default_workspace_parent()),
                allow_multiple=False,
            )
        except Exception:
            log.debug("The native folder dialog failed", exc_info=True)
            return None
        if not chosen:
            return None
        return Path(chosen[0] if isinstance(chosen, (list, tuple)) else chosen)

    def choose_workspace(self):
        chosen = self.pick_folder("Choose a PRISM workspace")
        if not chosen:
            return
        try:
            self.application.set_root(chosen)
        except ValueError as exc:
            self.notify(str(exc), "error")
            return
        ws.remember_workspace(chosen)
        self.refresh()
        self.notify(f"Opened {chosen}", "success")

    def create_workspace_interactive(self):
        parent = self.pick_folder("Choose where the new workspace should live")
        if not parent:
            return
        destination = Path(parent)
        if ws.workspace_blockers(destination):
            destination = destination / "PRISM"
        try:
            created = ws.create_workspace(destination)
            self.application.set_root(created)
        except (ws.WorkspaceError, ValueError) as exc:
            self.notify(str(exc), "error")
            return
        ws.remember_workspace(created)
        self.refresh()
        self.notify(f"New workspace ready at {created}", "success")

    def reveal_workspace(self):
        root = self.application.root
        if root is None:
            self.notify("No workspace is open yet.", "error")
            return
        self._reveal(root)

    def reveal_logs(self):
        try:
            ws.state_dir().mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._reveal(ws.state_dir())

    def _reveal(self, path):
        try:
            if sys.platform == "win32":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            self.notify(f"Could not open {path}: {exc}", "error")


# --------------------------------------------------------------------------
# Start up


def resolve_workspace(explicit):
    """The folder to open, and whether the page should ask for one."""
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if not ws.is_workspace(candidate):
            raise ValueError(f"{candidate} is not a PRISM workspace.")
        ws.remember_workspace(candidate)
        return candidate
    remembered = ws.remembered_workspace()
    if remembered:
        return remembered
    if not ws.frozen():
        # A checkout is its own workspace, so developers never see first run
        # unless they ask for it.
        here = Path(__file__).resolve().parents[1]
        if ws.is_workspace(here):
            return here
    return None


def run(argv=None):
    parser = argparse.ArgumentParser(prog="PRISM", description=__doc__)
    parser.add_argument("--workspace", help="Open this folder instead of the remembered one.")
    parser.add_argument("--browser", action="store_true",
                        help="Use the default browser instead of the application window.")
    parser.add_argument("--port", type=int, default=0, help="Bind a fixed loopback port.")
    parser.add_argument("--debug", action="store_true", help="Verbose logging and web inspector.")
    parser.add_argument("--version", action="store_true", help="Print the version and exit.")
    args = parser.parse_args(argv)

    if args.version:
        print(f"PRISM {ws.APP_VERSION}")
        return 0

    start_logging(args.debug)
    apply_windows_identity()

    if not args.browser and focus_running_instance():
        log.info("Another PRISM is already running; brought it forward")
        return 0

    try:
        root = resolve_workspace(args.workspace)
    except ValueError as exc:
        return fail("PRISM cannot open that folder", str(exc))

    # app.py is imported late so that --version and the single instance check
    # stay fast and cannot be broken by a problem in the dashboard.
    import app as dashboard

    runtime = runtime_report()
    use_window = not args.browser and runtime["ok"]
    webview = import_webview() if use_window else None
    if use_window and webview is None:
        use_window = False
        runtime = dict(runtime, ok=False, remedy=(
            "PRISM could not load its window toolkit (pywebview). Falling back "
            "to your browser. The log in " + str(ws.state_dir()) + " has the detail."
        ))

    shell_info = {
        "mode": "desktop" if use_window else "browser",
        "chrome": chrome_mode() if use_window else "system",
        "app_version": ws.APP_VERSION,
        "platform": sys.platform,
    }

    try:
        server, application = dashboard.start_server(
            root, port=args.port, verbose=args.debug, shell=shell_info,
        )
    except OSError as exc:
        return fail("PRISM could not start", f"The local helper could not open a port: {exc}")

    shell = Shell(application, server)
    application.shell_actions = shell
    log.info("Serving %s", application.url)

    try:
        write_session(application.port, application.token)
    except OSError:
        log.warning("Could not record the running instance", exc_info=True)

    try:
        if use_window:
            code = _run_window(webview, application, shell, debug=args.debug)
        else:
            code = _run_browser(application, runtime)
    finally:
        clear_session()
        server.shutdown()
        server.server_close()
    return code


def _run_window(webview, application, shell, debug=False):
    geometry = clamp_geometry(ws.load_settings().get("window") or {}, screen_rectangles(webview))
    window = webview.create_window(
        WINDOW_TITLE,
        application.url,
        width=geometry["width"],
        height=geometry["height"],
        x=geometry["x"],
        y=geometry["y"],
        min_size=MINIMUM_SIZE,
        background_color=VOID,
        text_select=True,
        zoomable=True,
        confirm_close=False,
    )
    shell.attach(webview, window, geometry)

    def on_start():
        # The chrome can only be changed once the platform has made a window,
        # and only from the main thread, which this callback is not on.
        schedule_macos_chrome()

    for name, handler in (
        ("resized", lambda width, height: shell.note_size(width, height)),
        ("moved", lambda x, y: shell.note_position(x, y)),
        ("closing", shell.remember_geometry),
    ):
        try:
            setattr(window.events, name, getattr(window.events, name) + handler)
        except Exception:
            log.debug("Could not subscribe to window.%s", name, exc_info=True)

    menu = build_menu(webview, shell)
    shell.open_calibration()
    started = False
    for attempt in ({"menu": menu} if menu else {}, {}):
        try:
            webview.start(on_start, private_mode=False,
                          storage_path=str(ws.state_dir() / "webview"),
                          debug=debug, **attempt)
            started = True
            break
        except TypeError:
            log.debug("pywebview rejected these start options: %s", attempt, exc_info=True)
        except Exception as exc:
            return fail("PRISM could not open its window", str(exc))
    if not started:
        return fail("PRISM could not open its window",
                    "The window toolkit refused every supported set of options.")
    shell.remember_geometry()
    return 0


def _run_browser(application, runtime):
    if not runtime["ok"] and runtime["remedy"]:
        native_alert("PRISM is opening in your browser", runtime["remedy"])
    print(f"PRISM: {application.url}")
    print("Keep this window open while using PRISM. Press Ctrl+C to stop.")
    threading.Timer(0.35, lambda: webbrowser.open(application.url)).start()
    try:
        while True:
            time.sleep(0.4)
    except KeyboardInterrupt:
        print("\nPRISM closed.")
    return 0


def main(argv=None):
    try:
        return run(argv)
    except Exception as exc:  # pragma: no cover - last resort for a built app
        log.exception("PRISM stopped unexpectedly")
        return fail(
            "PRISM stopped unexpectedly",
            f"{exc}\n\nThe full detail is in {ws.log_path()}.",
        )


if __name__ == "__main__":
    sys.exit(main())
