"""
Duplicate Finder — desktop launcher (Tkinter).

A small native window that is the user-facing front door:
  • pick a folder
  • press Scan
  • watch live progress + a log
  • open the review screen (the existing Flask UI) in the browser

Cross-platform (Windows + macOS + Linux), ships with Python's standard library,
and bundles cleanly with PyInstaller. The heavy lifting still lives in
dedup.main.run_scan; this module only drives it and reports progress.
"""

import os
# Mute FFmpeg/OpenCV decoder chatter before cv2 is imported downstream.
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")
os.environ.setdefault("OPENCV_LOG_LEVEL", "FATAL")

import queue
import threading
import time
import webbrowser

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

REVIEW_URL = "http://127.0.0.1:5000"


class DuplicateFinderApp:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.payload = None
        self.server_started = False
        self.scanning = False

        root.title("Duplicate Finder")
        root.minsize(640, 520)

        self._build_ui()
        self.root.after(100, self._drain_queue)

    # ── layout ───────────────────────────────────────────────────────────────
    def _build_ui(self):
        pad = {"padx": 14, "pady": 8}
        wrap = ttk.Frame(self.root, padding=16)
        wrap.pack(fill="both", expand=True)

        title = ttk.Label(wrap, text="Duplicate Finder",
                          font=("Segoe UI", 18, "bold"))
        title.pack(anchor="w")
        ttk.Label(wrap,
                  text="Find duplicate and near-duplicate photos and videos.",
                  foreground="#666").pack(anchor="w", pady=(0, 12))

        # folder row
        row = ttk.Frame(wrap)
        row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text="Folder to scan:").pack(side="left")
        self.folder_var = tk.StringVar()
        self.folder_entry = ttk.Entry(row, textvariable=self.folder_var)
        self.folder_entry.pack(side="left", fill="x", expand=True, padx=8)
        self.browse_btn = ttk.Button(row, text="Browse…", command=self.browse)
        self.browse_btn.pack(side="left")

        # action row
        act = ttk.Frame(wrap)
        act.pack(fill="x", pady=(6, 10))
        self.scan_btn = ttk.Button(act, text="Scan for duplicates",
                                   command=self.start_scan)
        self.scan_btn.pack(side="left")
        self.review_btn = ttk.Button(act, text="Open review in browser",
                                     command=self.open_review, state="disabled")
        self.review_btn.pack(side="left", padx=8)

        # progress
        self.progress = ttk.Progressbar(wrap, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(4, 2))
        self.status_var = tk.StringVar(value="Choose a folder and press Scan.")
        ttk.Label(wrap, textvariable=self.status_var,
                  foreground="#444").pack(anchor="w", pady=(0, 8))

        # log panel
        ttk.Label(wrap, text="Log").pack(anchor="w")
        self.log = scrolledtext.ScrolledText(wrap, height=14, state="disabled",
                                             wrap="word", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, pady=(2, 0))

    # ── actions ────────────────────────────────────────────────────────────--
    def browse(self):
        d = filedialog.askdirectory(title="Choose a folder to scan")
        if d:
            self.folder_var.set(d)

    def start_scan(self):
        if self.scanning:
            return
        folder = self.folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("No folder",
                                   "Please choose a valid folder to scan.")
            return
        self.scanning = True
        self.payload = None
        self.scan_btn.config(state="disabled")
        self.browse_btn.config(state="disabled")
        self.review_btn.config(state="disabled")
        self.progress.config(value=0)
        self._clear_log()
        self._append_log(f"Scanning: {folder}\n")
        threading.Thread(target=self._worker, args=(folder,), daemon=True).start()

    def _worker(self, folder):
        # Runs off the UI thread. Communicates back only via the queue.
        from .main import run_scan   # imported here so the window shows instantly

        def progress(done, total, label):
            self.q.put(("progress", (done, total, label)))

        def log(message):
            self.q.put(("log", str(message)))

        try:
            payload = run_scan(folder, write_json=True,
                               progress=progress, log=log)
            self.q.put(("done", payload))
        except Exception as e:  # noqa: BLE001 - surface any failure to the user
            self.q.put(("error", str(e)))

    def open_review(self):
        if not self.payload or not self.payload.get("groups"):
            messagebox.showinfo("Nothing to review",
                                "No duplicates were found in that folder.")
            return
        if not self.server_started:
            try:
                from .ui import review as _review
                _review.STATE["data"] = self.payload
                _review.STATE["root"] = self.payload["root"]
                threading.Thread(
                    target=lambda: _review.app.run(
                        host="127.0.0.1", port=5000, debug=False,
                        threaded=True, use_reloader=False),
                    daemon=True).start()
                self.server_started = True
                time.sleep(1.0)   # give the server a moment to bind
            except Exception as e:  # noqa: BLE001
                messagebox.showerror("Could not start review", str(e))
                return
        webbrowser.open(REVIEW_URL)

    # ── queue pump (UI thread) ─────────────────────────────────────────────--
    def _drain_queue(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "progress":
                    done, total, label = data
                    pct = int(done / total * 100) if total else 0
                    self.progress.config(value=pct)
                    self.status_var.set(f"{label}… {pct}%  ({done:,}/{total:,})")
                elif kind == "log":
                    self._append_log(data.rstrip("\n") + "\n")
                elif kind == "done":
                    self._on_done(data)
                elif kind == "error":
                    self._on_error(data)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_queue)

    def _on_done(self, payload):
        self.scanning = False
        self.payload = payload
        self.scan_btn.config(state="normal")
        self.browse_btn.config(state="normal")
        self.progress.config(value=100)
        groups = payload.get("groups", []) if payload else []
        summary = (payload or {}).get("summary", {})
        if groups:
            self.review_btn.config(state="normal")
            self.status_var.set(
                f"Done — {summary.get('duplicate_files', 0)} extra copies in "
                f"{len(groups)} group(s). Click ‘Open review in browser’.")
            self.open_review()   # open automatically, like the terminal flow
        else:
            self.status_var.set("Done — no duplicates found. 🎉")

    def _on_error(self, msg):
        self.scanning = False
        self.scan_btn.config(state="normal")
        self.browse_btn.config(state="normal")
        self.status_var.set("Scan failed.")
        self._append_log(f"\nERROR: {msg}\n")
        messagebox.showerror("Scan failed", msg)

    # ── log helpers ────────────────────────────────────────────────────────--
    def _append_log(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.config(state="disabled")

    def _clear_log(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")


def main():
    root = tk.Tk()
    DuplicateFinderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
