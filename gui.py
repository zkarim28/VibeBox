"""
A small Tkinter control window for the party-game server.

`server.py` calls `gui.run(ctx)` on the main thread. Everything the window
needs is passed in through `ctx` so this file never imports `server` (no
circular import). Set GUI=0 (or run with --no-gui) to skip it.
"""

import base64
import signal
import threading
import time
import tkinter as tk
from tkinter import ttk
import webbrowser

BG = "#0b1120"
CARD = "#0f172a"
FG = "#e8edf6"
MUTED = "#94a3b8"
ACCENT = "#38bdf8"


def run(ctx):
    """Show the window and pump events until it's closed. A manual pump (rather
    than tk.mainloop) so Ctrl+C in the terminal reliably tears everything down
    on macOS. Worker threads never touch tkinter — they hand results back
    through plain attributes that this loop polls on the main thread."""
    app = _App(ctx)
    try:
        signal.signal(signal.SIGINT, lambda *_: setattr(app, "_running", False))
    except (ValueError, OSError):
        pass
    try:
        while app._running:
            app.update()
            app._pump()
            time.sleep(0.02)
    except (tk.TclError, KeyboardInterrupt):
        pass
    finally:
        try:
            app.destroy()
        except tk.TclError:
            pass


class _App(tk.Tk):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.already_public = bool(ctx["already_public"])
        self._qr_img = None
        self._running = True
        self._tunnel_result = None   # set by the worker thread, read by _pump()
        self._frames = 0

        self.title("Party Games")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.quit_now)

        wrap = tk.Frame(self, bg=BG, padx=24, pady=20)
        wrap.pack()
        tk.Label(wrap, text="Party Games", bg=BG, fg=FG,
                 font=("Helvetica", 20, "bold")).pack(anchor="w")
        self.status = tk.Label(wrap, text="", bg=BG, fg=MUTED,
                               font=("Helvetica", 12))
        self.status.pack(anchor="w", pady=(2, 14))
        self.content = tk.Frame(wrap, bg=BG)
        self.content.pack(fill="x")

        if self.already_public:
            self._show_running(public=True)
        else:
            self._show_mode_picker()

        self._pop_to_front()

    # ---------------------------------------------------------------- helpers --
    def _pop_to_front(self):
        self.update_idletasks()
        self.lift()
        self.attributes("-topmost", True)   # _pump() drops this after ~0.5s
        try:
            self.focus_force()
        except tk.TclError:
            pass

    def _clear(self):
        for w in self.content.winfo_children():
            w.destroy()

    def _copy(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()

    # ------------------------------------------------------------ mode picker --
    def _show_mode_picker(self):
        self._clear()
        self.status.config(text="How should phones connect?", fg=MUTED)
        ttk.Button(self.content, text="Local   —   same WiFi only",
                   command=lambda: self._show_running(public=False)
                   ).pack(fill="x", pady=4, ipady=8)
        ttk.Button(self.content, text="Public   —   anyone with the link + code",
                   command=self._go_public).pack(fill="x", pady=4, ipady=8)
        tk.Label(self.content,
                 text="Public opens a Cloudflare tunnel (needs cloudflared).",
                 bg=BG, fg=MUTED, font=("Helvetica", 10)).pack(anchor="w", pady=(8, 0))

    def _go_public(self):
        self._clear()
        self.status.config(text="Starting a Cloudflare tunnel…", fg=ACCENT)
        tk.Label(self.content, text="This takes a few seconds.",
                 bg=BG, fg=MUTED).pack(anchor="w")
        self._tunnel_result = None
        threading.Thread(target=self._run_tunnel, daemon=True).start()

    def _run_tunnel(self):
        # off the main thread — MUST NOT touch tkinter. _pump() picks this up.
        try:
            self._tunnel_result = self.ctx["start_tunnel"]() or "failed"
        except Exception:
            self._tunnel_result = "failed"

    def _pump(self):
        """Called every loop on the main thread — apply anything a worker left."""
        self._frames += 1
        if self._frames == 25:
            try:
                self.attributes("-topmost", False)
            except tk.TclError:
                pass
        r = self._tunnel_result
        if r is None:
            return
        self._tunnel_result = None
        if r == "failed":
            self._tunnel_failed()
        else:
            self.ctx["set_public_url"](r[1])
            self.ctx["set_tunnel"](r)
            self._show_running(public=True)

    def _tunnel_failed(self):
        self._clear()
        self.status.config(text="The tunnel didn't start.", fg="#f87171")
        tk.Label(self.content, justify="left", bg=BG, fg=MUTED,
                 text="See the terminal for cloudflared's output.\n"
                      "Install it with:   brew install cloudflared"
                 ).pack(anchor="w")
        ttk.Button(self.content, text="Run Local instead",
                   command=lambda: self._show_running(public=False)
                   ).pack(fill="x", pady=(12, 0), ipady=4)
        ttk.Button(self.content, text="Try the tunnel again",
                   command=self._go_public).pack(fill="x", pady=(6, 0), ipady=4)

    # --------------------------------------------------------------- running ---
    def _show_running(self, public):
        self._clear()
        self.status.config(
            text=("●  Public — via Cloudflare tunnel" if public
                  else "●  Local — same WiFi only"),
            fg=(ACCENT if public else MUTED))
        self.ctx["banner"](public and not self.already_public)   # mirror to terminal

        host = self.ctx["host_url"]()
        play = self.ctx["play_url"]()

        self._link_row("HOST SCREEN — open this on the laptop",
                       host, host, open_btn=True)
        self._link_row("PLAYERS JOIN AT",
                       play.split("?")[0], play, open_btn=False)

        row = tk.Frame(self.content, bg=BG)
        row.pack(anchor="w", pady=(6, 2))
        tk.Label(row, text="ROOM CODE", bg=BG, fg=MUTED,
                 font=("Helvetica", 10)).pack(side="left")
        tk.Label(row, text=self.ctx["room_code"], bg=BG, fg=FG,
                 font=("Menlo", 22, "bold")).pack(side="left", padx=10)

        self._show_qr()

        ttk.Button(self.content, text="Quit", command=self.quit_now
                   ).pack(fill="x", pady=(14, 0), ipady=4)

    def _link_row(self, label, shown, payload, open_btn):
        f = tk.Frame(self.content, bg=BG)
        f.pack(fill="x", pady=(10, 0))
        tk.Label(f, text=label, bg=BG, fg=MUTED,
                 font=("Helvetica", 10)).pack(anchor="w")
        e = tk.Entry(f, font=("Menlo", 11), relief="flat", bd=6,
                     readonlybackground=CARD, fg=FG)
        e.insert(0, shown)
        e.config(state="readonly")
        e.pack(fill="x", pady=(3, 5))
        btns = tk.Frame(f, bg=BG)
        btns.pack(anchor="w")
        ttk.Button(btns, text="Copy link",
                   command=lambda: self._copy(payload)).pack(side="left")
        if open_btn:
            ttk.Button(btns, text="Open in browser",
                       command=lambda: webbrowser.open(payload)
                       ).pack(side="left", padx=(6, 0))

    def _show_qr(self):
        try:
            img = tk.PhotoImage(data=base64.b64encode(self.ctx["qr_png"]()).decode())
            f = max(1, img.width() // 200)
            if f > 1:
                img = img.subsample(f, f)
            self._qr_img = img
            tk.Label(self.content, image=img, bg=BG, bd=0).pack(pady=(12, 0))
            tk.Label(self.content, text="scan to join", bg=BG, fg=MUTED,
                     font=("Helvetica", 9)).pack()
        except Exception:
            pass

    # ------------------------------------------------------------------ quit ---
    def quit_now(self):
        self._running = False   # the pump loop in run() tears things down
