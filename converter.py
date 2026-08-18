"""Quick Convert - right-click file converter popup.

Launched by Explorer with one file path per instance. The first instance
becomes the primary (binds a localhost port); later instances (Explorer spawns
one per selected file) hand their path to the primary over the socket and exit,
so a multi-file selection opens a single window.

The handoff protocol is deliberately minimal but guarded: the client sends a
magic marker and waits for an acknowledgement, so a launch never silently
disappears into an unrelated process that happens to hold the port.
"""

import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import traceback

import engines
import register

__version__ = "1.0.0"

PORT = 47653          # fixed port for single-instance handoff
MAGIC = "QUICKCONVERTv1"      # first line of a valid handoff
ACK = b"QC-OK"                # primary's reply confirming it processed the paths
CONN_TIMEOUT = 3.0    # per-connection socket timeout (accepted socks are blocking)


def _dpi_aware():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def _is_unc(p):
    """True for UNC paths (\\\\host\\share...) that could coerce SMB auth."""
    q = p.replace("/", "\\")
    if q.startswith("\\\\?\\"):
        q = q[4:]
        if q[:4].upper() == "UNC\\":
            return True
    return q.startswith("\\\\")


def _clean_paths(raw, trusted):
    """Normalize and validate incoming paths.

    Untrusted (socket) input must not reach os.path.exists() as a UNC path -
    that triggers an outbound SMB authentication. Trusted input (this process's
    own argv / file dialog) is only existence-checked.
    """
    out = []
    for p in raw:
        if not p:
            continue
        if not trusted and _is_unc(p):
            continue
        try:
            if os.path.exists(p):
                out.append(os.path.abspath(p))
        except OSError:
            continue
    return out


# ---------------------------------------------------------------- handoff

def _serve_conn(conn):
    """Read one handoff request; return its path list, or [] if not ours.

    Reads until EOF (TCP is a stream - one recv may not hold the whole
    payload), validates the magic marker, and acknowledges valid requests.
    """
    conn.settimeout(CONN_TIMEOUT)
    chunks = []
    try:
        while True:
            b = conn.recv(65536)
            if not b:
                break
            chunks.append(b)
    except (socket.timeout, OSError):
        return []
    lines = b"".join(chunks).decode("utf-8", errors="replace").split("\n")
    if not lines or lines[0] != MAGIC:
        return []  # foreign or malformed - do not acknowledge
    try:
        conn.sendall(ACK)
    except OSError:
        pass
    return [p for p in lines[1:] if p]


def _send_to_primary(paths):
    """Hand paths to an existing primary. True only if it acknowledges."""
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=CONN_TIMEOUT) as s:
            s.sendall((MAGIC + "\n" + "\n".join(paths)).encode("utf-8"))
            s.shutdown(socket.SHUT_WR)
            s.settimeout(CONN_TIMEOUT)
            ack = s.recv(64)
        return ack.strip() == ACK
    except OSError:
        return False


def _listen(port):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", port))
    server.listen(64)
    return server


def _become_primary_or_handoff(paths):
    """Return a listening socket if we are primary; None if we handed off;
    'fallback' if the port is held by something that is not a Quick Convert
    primary (so we should show our own standalone window)."""
    try:
        return _listen(PORT)
    except OSError:
        pass
    if _send_to_primary(paths):
        return None
    return "fallback"


def _accept_forever(server, inbox):
    server.settimeout(None)
    while True:
        try:
            conn, _ = server.accept()
        except OSError:
            return
        with conn:
            for p in _clean_paths(_serve_conn(conn), trusted=False):
                inbox.put(p)


class App:
    PAD = 12

    def __init__(self, root, paths, inbox):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.root = root
        self.paths = paths
        self.inbox = inbox
        self.uiq = queue.Queue()
        self.converting = False
        self.outputs = []

        root.title("Quick Convert")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        try:
            ttk.Style().theme_use("vista")
        except Exception:
            pass

        self.frame = ttk.Frame(root, padding=self.PAD)
        self.frame.grid(sticky="nsew")

        self.header = ttk.Label(self.frame, font=("Segoe UI", 10, "bold"))
        self.header.grid(row=0, column=0, sticky="w")
        self.sub = ttk.Label(self.frame, foreground="#555")
        self.sub.grid(row=1, column=0, sticky="w", pady=(0, 8))

        self.choice_frame = ttk.LabelFrame(self.frame, text="Convert to",
                                           padding=8)
        self.choice_frame.grid(row=2, column=0, sticky="ew")
        self.choice_var = tk.StringVar()

        self.quality_row = ttk.Frame(self.frame)
        self.quality_var = tk.IntVar(value=85)
        ttk.Label(self.quality_row, text="Quality:").grid(row=0, column=0)
        self.quality_scale = ttk.Scale(
            self.quality_row, from_=10, to=100, variable=self.quality_var,
            length=180, command=self._on_quality_change)
        self.quality_scale.grid(row=0, column=1, padx=6)
        self.quality_label = ttk.Label(self.quality_row, width=4, text="85")
        self.quality_label.grid(row=0, column=2)

        self.combine_var = tk.BooleanVar(value=True)
        self.combine_check = ttk.Checkbutton(
            self.frame, text="Combine into one PDF", variable=self.combine_var)

        self.button = ttk.Button(self.frame, text="Convert",
                                 command=self.on_convert)
        self.button.grid(row=6, column=0, pady=(10, 4), sticky="ew")
        self.progress = ttk.Progressbar(self.frame, mode="determinate")
        self.status = ttk.Label(self.frame, foreground="#555")

        self.rebuild()
        self.root.after(120, self.poll)
        root.attributes("-topmost", True)
        root.after(400, lambda: root.attributes("-topmost", False))
        root.focus_force()

    # ------------------------------------------------------------- layout

    def rebuild(self):
        prev = self.choice_var.get()
        names = [os.path.basename(p) for p in self.paths]
        shown = ", ".join(names[:3]) + (f"  (+{len(names) - 3} more)"
                                        if len(names) > 3 else "")
        self.header.config(text=shown)
        fams = sorted({engines.family_of(p) for p in self.paths})
        count = f"{len(self.paths)} file" + ("s" if len(self.paths) > 1 else "")
        self.sub.config(text=f"{count} - {', '.join(fams)}")

        for w in self.choice_frame.winfo_children():
            w.destroy()
        self.choices = engines.targets_for(self.paths)
        keys = [f"{k}:{v}" for k, v, _ in self.choices]
        cols = 3
        for i, (kind, value, label) in enumerate(self.choices):
            rb = self.ttk.Radiobutton(self.choice_frame, text=label,
                                      value=f"{kind}:{value}",
                                      variable=self.choice_var,
                                      command=self.on_choice)
            rb.grid(row=i // cols, column=i % cols, sticky="w",
                    padx=4, pady=2)
        # Keep the user's earlier pick if it still applies; else default.
        if prev in keys:
            self.choice_var.set(prev)
        elif keys:
            self.choice_var.set(keys[0])
        # Newly handed-off files land here after a previous run finished; make
        # sure the window is back in a convertible state.
        if not self.converting:
            self._show_convert_button()
        self.on_choice()

    def _show_convert_button(self):
        self.button.config(text="Convert", command=self.on_convert)
        self.button.state(["!disabled"])
        self.progress.grid_remove()
        self.status.grid_remove()

    def selected(self):
        kind, _, value = self.choice_var.get().partition(":")
        return kind, value

    def on_choice(self):
        kind, value = self.selected()
        fams = {engines.family_of(p) for p in self.paths}
        lossy = (kind == "conv" and fams == {"image"}
                 and value in engines.LOSSY_IMAGE_TARGETS)
        if lossy:
            self.quality_label.config(text=str(self.quality_var.get()))
            self.quality_row.grid(row=3, column=0, sticky="w", pady=(8, 0))
        else:
            self.quality_row.grid_forget()
        combinable = (kind == "conv" and value == "pdf"
                      and fams == {"image"} and len(self.paths) > 1)
        if combinable:
            self.combine_check.grid(row=4, column=0, sticky="w", pady=(6, 0))
        else:
            self.combine_check.grid_forget()

    def _on_quality_change(self, v):
        self.quality_var.set(round(float(v)))
        self.quality_label.config(text=str(self.quality_var.get()))

    # ------------------------------------------------------------- convert

    def on_convert(self):
        if self.converting or not self.choice_var.get():
            return
        self.converting = True
        self.button.state(["disabled"])
        for w in self.choice_frame.winfo_children():
            w.state(["disabled"])
        self.progress.config(maximum=max(len(self.paths), 1), value=0)
        self.progress.grid(row=7, column=0, sticky="ew", pady=(2, 2))
        self.status.grid(row=8, column=0, sticky="w")
        kind, value = self.selected()
        combine = self.combine_check.winfo_ismapped() and self.combine_var.get()
        quality = self.quality_var.get()
        # Snapshot the paths so a late handoff can't mutate the list mid-run.
        batch = list(self.paths)
        threading.Thread(target=self.worker,
                         args=(batch, kind, value, quality, combine),
                         daemon=True).start()

    def worker(self, batch, kind, value, quality, combine):
        outputs, errors = [], []
        total = len(batch)
        try:
            if kind == "compress":
                self.uiq.put(("status", "Compressing...", 0))
                outputs = engines.make_archive(batch, value)
                self.uiq.put(("tick", total))
            elif kind == "extract":
                for i, p in enumerate(batch, 1):
                    self.uiq.put(("status",
                                  f"Extracting {os.path.basename(p)}...", i - 1))
                    try:
                        outputs += engines.extract_archive(p)
                    except Exception as e:
                        errors.append((p, str(e)))
                    self.uiq.put(("tick", i))
            elif combine:
                self.uiq.put(("status", "Building PDF...", 0))
                outputs = engines.images_to_pdf(batch, quality)
                self.uiq.put(("tick", total))
            else:
                for i, p in enumerate(batch, 1):
                    self.uiq.put(("status",
                                  f"Converting {os.path.basename(p)}"
                                  f"  ({i}/{total})...", i - 1))
                    try:
                        outputs += engines.convert_one(p, value, quality)
                    except Exception as e:
                        errors.append((p, str(e)))
                    self.uiq.put(("tick", i))
        except Exception as e:
            errors.append(("", str(e)))
        self.uiq.put(("done", outputs, errors))

    def poll(self):
        try:
            self._drain_ui_queue()
            if not self.converting:
                self._drain_inbox()
        except Exception:
            # A callback bug must never kill the poll loop and freeze the UI.
            traceback.print_exc()
        finally:
            self.root.after(120, self.poll)

    def _drain_ui_queue(self):
        try:
            while True:
                msg = self.uiq.get_nowait()
                if msg[0] == "status":
                    self.status.config(text=msg[1])
                    self.progress.config(value=msg[2])
                elif msg[0] == "tick":
                    self.progress.config(value=msg[1])
                elif msg[0] == "done":
                    self.finish(msg[1], msg[2])
        except queue.Empty:
            pass

    def _drain_inbox(self):
        changed = False
        try:
            while True:
                p = self.inbox.get_nowait()
                if p not in self.paths and os.path.exists(p):
                    self.paths.append(p)
                    changed = True
        except queue.Empty:
            pass
        if changed:
            self.rebuild()

    def finish(self, outputs, errors):
        from tkinter import messagebox
        self.converting = False
        self.outputs = outputs
        self.progress.config(value=self.progress["maximum"])
        if errors:
            detail = "\n\n".join(
                f"{os.path.basename(p) or 'Selection'}:\n{e}"
                for p, e in errors[:6])
            self.status.config(
                text=f"Done with {len(errors)} error(s), "
                     f"{len(outputs)} file(s) created.")
            messagebox.showerror("Quick Convert - errors", detail,
                                 parent=self.root)
        else:
            self.status.config(text=f"Done - {len(outputs)} file(s) created.")
        if outputs:
            self.button.config(text="Show in folder", command=self.reveal)
        else:
            self.button.config(text="Close", command=self.root.destroy)
        self.button.state(["!disabled"])

    def reveal(self):
        subprocess.Popen(["explorer", "/select,", self.outputs[0]])

    def on_close(self):
        if self.converting:
            from tkinter import messagebox
            if not messagebox.askyesno(
                    "Quick Convert",
                    "A conversion is still running. Closing now may leave a "
                    "partial file behind.\n\nClose anyway?",
                    parent=self.root, default="no"):
                return
        self.root.destroy()


def _run_installer(action, classic_menu=False):
    """Handle `--install` / `--uninstall` with a GUI confirmation dialog.

    Lets the standalone exe act as its own installer (double-clicking it, or
    the bundled Install.bat, runs `QuickConvert.exe --install`). Pass
    `--classic-menu` to also force the old top-level menu.
    """
    import tkinter as tk
    from tkinter import messagebox
    hidden = tk.Tk()
    hidden.withdraw()
    try:
        if action == "--install":
            register.install(classic_menu=classic_menu)
            where = ("at the top of the right-click menu" if classic_menu
                     else "under 'Show more options' in the right-click menu "
                          "(or press Shift+F10)")
            messagebox.showinfo(
                "Quick Convert",
                f"Quick Convert is installed.\n\nRight-click any file or folder "
                f"and find 'Quick Convert' {where}.")
        else:
            register.uninstall()
            messagebox.showinfo(
                "Quick Convert",
                "Quick Convert has been removed from the right-click menu.")
    except Exception:
        messagebox.showerror("Quick Convert - setup error",
                             traceback.format_exc())
    finally:
        hidden.destroy()


def main():
    _dpi_aware()
    argv = sys.argv[1:]
    if argv and argv[0] in ("--install", "--uninstall"):
        _run_installer(argv[0], classic_menu=("--classic-menu" in argv))
        return
    if argv and argv[0] == "--selftest":
        out = (argv[1] if len(argv) > 1
               else os.path.join(tempfile.gettempdir(), "quickconvert-selftest.txt"))
        text = "\n".join(f"{s:9} {n:32} {d}"
                         for n, s, d in engines.run_selftest())
        with open(out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        return
    paths = _clean_paths(argv, trusted=True)

    if not paths:
        import tkinter as tk
        from tkinter import filedialog
        hidden = tk.Tk()
        hidden.withdraw()
        picked = filedialog.askopenfilenames(title="Quick Convert - choose files")
        hidden.destroy()
        paths = _clean_paths(picked, trusted=True)
        if not paths:
            return

    server = _become_primary_or_handoff(paths)
    if server is None:
        return  # handed off to the primary instance

    if server == "fallback":
        # Port is held by a foreign process; degrade to a standalone window on
        # an ephemeral port rather than silently doing nothing.
        server = _listen(0)

    # Open the window immediately. Sibling instances from a multi-file
    # right-click hand off asynchronously and stream into the list through
    # `inbox`, so there is no up-front wait (this is what the popup feels like
    # opening "instantly" - no fixed grace delay before the first paint).
    inbox = queue.Queue()
    threading.Thread(target=_accept_forever, args=(server, inbox),
                     daemon=True).start()

    import tkinter as tk
    root = tk.Tk()
    App(root, paths, inbox)
    root.mainloop()
    server.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # pythonw has no console - surface crashes in a dialog instead
        try:
            import tkinter as tk
            from tkinter import messagebox
            hidden = tk.Tk()
            hidden.withdraw()
            messagebox.showerror("Quick Convert - crash",
                                 traceback.format_exc())
        except Exception:
            pass
        raise
