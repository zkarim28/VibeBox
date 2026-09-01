"""
A small Tkinter control window for the party-game server.

`server.py` calls `gui.run(ctx)` on the main thread. Everything the window
needs is passed in through `ctx` so this file never imports `server` (no
circular import). Set GUI=0 (or run with --no-gui) to skip it.

The window is a frameless, rounded "bubble" drawn on a Canvas — Tk has no
native rounded widgets, so buttons, chips and the panel itself are all
painted with smooth polygons. Drag it by the title bar; the ✕ closes it.

Every pixel dimension is a base-1x number multiplied by UI, a scale factor
_apply_scale() derives from the real rendered font height at startup — so the
layout keeps up with a HiDPI / fractionally-scaled Linux desktop where Tk
draws point-sized text much bigger. Force it with VIBEBOX_UI_SCALE=1.5 if the
auto-detect is ever off.
"""

import base64
import math
import os
import signal
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
import webbrowser

BG = "#0b1120"        # backdrop (mostly transparent when the platform allows)
CARD = "#151f37"      # the bubble
CARD_EDGE = "#2b3a5c" # hairline around the bubble
FG = "#eef2fb"
MUTED = "#93a1ba"
ACCENT = "#5cc8ff"
ACCENT_DK = "#38a9e6"
ACCENT_INK = "#05263b"
CHIP_PUBLIC = "#123048"
DANGER = "#ff8f8f"
GOOD = "#7fe0b6"
BTN = "#243352"
BTN_HOVER = "#30436a"

# Base (1x) metrics — tuned on a 96-DPI / macOS display. On Linux the window
# manager often renders point-sized fonts much larger (HiDPI panel, 125–200 %
# desktop scaling), so _apply_scale() measures the real font height at startup
# and blows every pixel dimension up by the same factor. Nothing here is used
# directly for drawing once the app is running — go through UI / _px().
_WIN_W, _GAP, _PAD, _RADIUS = 360, 16, 26, 30
WIN_W, GAP, PAD, RADIUS = _WIN_W, _GAP, _PAD, _RADIUS
MARGIN = GAP + PAD
UI = 1.0             # pixel scale factor, set by _apply_scale()

FAM = "Helvetica"   # replaced with a rounded family in _App.__init__ if present
MONO = "Menlo"      # replaced with an available monospace family if present


def _px(n):
    """Scale a base-metric pixel value to this display."""
    return max(1, int(round(n * UI)))


def _apply_scale(root):
    """Match the layout to however big Tk is actually drawing text here."""
    global UI, WIN_W, GAP, PAD, MARGIN, RADIUS
    forced = os.environ.get("VIBEBOX_UI_SCALE", "").strip()
    if forced:
        try:
            UI = min(4.0, max(0.5, float(forced)))
        except ValueError:
            UI = 1.0
    else:
        try:
            lh = tkfont.Font(root, family=FAM, size=13,
                             weight="bold").metrics("linespace")
            UI = min(3.0, max(1.0, lh / 18.0))   # 18 px ≈ size-13 bold at 1x
        except tk.TclError:
            UI = 1.0
        # don't let the tallest screen (running + QR ≈ 640 px at 1x) run off a
        # short display
        try:
            UI = min(UI, max(1.0, 0.92 * root.winfo_screenheight() / 640))
        except tk.TclError:
            pass
    WIN_W, GAP, PAD, RADIUS = _px(_WIN_W), _px(_GAP), _px(_PAD), _px(_RADIUS)
    MARGIN = GAP + PAD


def _round_rect(cv, x1, y1, x2, y2, r, **kw):
    """A rounded rectangle on canvas `cv`, via a smoothed polygon."""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return cv.create_polygon(pts, smooth=True, **kw)


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


class RoundBtn(tk.Canvas):
    """A pill-shaped button painted on a canvas. `kind` picks the palette."""

    PALETTE = {
        "normal": (BTN, BTN_HOVER, FG),
        "accent": (ACCENT, ACCENT_DK, ACCENT_INK),
        "ghost": (CARD, BTN, MUTED),
    }

    def __init__(self, parent, text, command, *, kind="normal",
                 width=None, height=None, subtitle=None, size=13, mono=False):
        width = WIN_W if width is None else _px(width)
        height = _px(46) if height is None else _px(height)
        super().__init__(parent, width=width, height=height, bg=CARD,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.command = command
        self._bw, self._bh = width, height
        self._kind = kind
        self._base, self._hover, self._fg = self.PALETTE[kind]
        self._cur = self._base
        self._text, self._subtitle = text, subtitle
        self._size, self._mono = size, mono
        self._flash = None
        self._pressed = False
        self._render()
        self.bind("<Enter>", lambda e: self._set(self._hover))
        self.bind("<Leave>", lambda e: (self._set(self._base),
                                        setattr(self, "_pressed", False)))
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _fnt(self, bold=True):
        fam = MONO if self._mono else FAM
        return (fam, self._size, "bold" if bold else "normal")

    def _render(self):
        self.delete("all")
        w, h = self._bw, self._bh
        r = _px(22) if self._subtitle else h / 2
        outline = CARD_EDGE if self._kind == "ghost" else ""
        _round_rect(self, 2, 2, w - 2, h - 2, r, fill=self._cur,
                    outline=outline, width=1)
        if self._flash:
            msg, col = self._flash
            self.create_text(w / 2, h / 2, text=msg, fill=col, font=self._fnt())
        elif self._subtitle:
            self.create_text(w / 2, h / 2 - _px(9), text=self._text, fill=self._fg,
                             font=(FAM, 13, "bold"))
            sub = ACCENT_INK if self._kind == "accent" else MUTED
            self.create_text(w / 2, h / 2 + _px(11), text=self._subtitle, fill=sub,
                             font=(FAM, 10))
        else:
            self.create_text(w / 2, h / 2, text=self._text, fill=self._fg,
                             font=self._fnt())

    def _set(self, colour):
        self._cur = colour
        self._render()

    def _on_press(self, _e):
        self._pressed = True
        self._set(self._hover)

    def _on_release(self, _e):
        if self._pressed and self.command:
            self.command()
        self._pressed = False

    def flash(self, msg, colour=GOOD):
        self._flash = (msg, colour)
        self._render()
        self.after(1200, self._unflash)

    def _unflash(self):
        self._flash = None
        try:
            self._render()
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
        self._placed = False
        self._spin_after = None

        global FAM, MONO
        fams = set(tkfont.families(self))
        for cand in ("SF Pro Rounded", "SF Compact Rounded", "Helvetica Neue",
                     "Cantarell", "Ubuntu", "Noto Sans", "DejaVu Sans"):
            if cand in fams:
                FAM = cand
                break
        for cand in ("Menlo", "SF Mono", "JetBrains Mono", "Fira Mono",
                     "DejaVu Sans Mono", "Liberation Mono", "Noto Sans Mono",
                     "Ubuntu Mono", "monospace"):
            if cand in fams:
                MONO = cand
                break

        _apply_scale(self)   # size the layout to this display's font rendering

        self.title("Party Games")
        self.overrideredirect(True)
        self._transparent = False
        try:
            self.wm_attributes("-transparent", True)
            self.configure(bg="systemTransparent")
            self._transparent = True
        except tk.TclError:
            self.configure(bg=BG)

        self.canvas = tk.Canvas(
            self, highlightthickness=0, bd=0,
            bg="systemTransparent" if self._transparent else BG)
        self.canvas.pack(fill="both", expand=True)

        self.inner = tk.Frame(self.canvas, bg=CARD)
        self.canvas.create_window(MARGIN, MARGIN, anchor="nw", window=self.inner)
        tk.Frame(self.inner, bg=CARD, width=WIN_W, height=1).pack()

        header = tk.Frame(self.inner, bg=CARD)
        header.pack(fill="x")
        title = tk.Label(header, text="Party Games", bg=CARD, fg=FG,
                         font=(FAM, 20, "bold"))
        title.pack(side="left")
        RoundBtn(header, "✕", self.quit_now, kind="ghost",
                 width=30, height=30, size=12).pack(side="right")

        self.status_holder = tk.Frame(self.inner, bg=CARD)
        self.status_holder.pack(fill="x", pady=(_px(8), _px(14)))

        self.content = tk.Frame(self.inner, bg=CARD)
        self.content.pack(fill="both", expand=True)

        if self.already_public:
            self._show_running(public=True)
        else:
            self._show_mode_picker()

        self._pop_to_front()

    # ---------------------------------------------------------------- helpers --
    def _drag_start(self, e):
        self._drag_off = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def _drag_move(self, e):
        self.geometry(f"+{e.x_root - self._drag_off[0]}"
                      f"+{e.y_root - self._drag_off[1]}")

    def _make_draggable(self, w):
        """Frameless window — so any non-button surface acts as the title bar."""
        if getattr(w, "_drag_bound", False):
            return
        w._drag_bound = True
        w.bind("<ButtonPress-1>", self._drag_start, add="+")
        w.bind("<B1-Motion>", self._drag_move, add="+")

    def _drag_sweep(self):
        stack = [self.canvas]
        while stack:
            w = stack.pop()
            if isinstance(w, RoundBtn):
                continue
            if isinstance(w, (tk.Frame, tk.Label, tk.Canvas)):
                self._make_draggable(w)
            stack.extend(w.winfo_children())

    def _relayout(self):
        self.update_idletasks()
        h = self.inner.winfo_reqheight() + 2 * MARGIN
        w = WIN_W + 2 * MARGIN
        if not self._placed:
            x = (self.winfo_screenwidth() - w) // 2
            y = (self.winfo_screenheight() - h) // 3
            self.geometry(f"{w}x{h}+{x}+{y}")
            self._placed = True
        else:
            self.geometry(f"{w}x{h}+{self.winfo_x()}+{self.winfo_y()}")
        self.canvas.delete("bubble")
        _round_rect(self.canvas, GAP, GAP, w - GAP, h - GAP, RADIUS,
                    fill=CARD, outline=CARD_EDGE, width=1, tags="bubble")
        self.canvas.tag_lower("bubble")
        self._drag_sweep()

    def _pop_to_front(self):
        self.update_idletasks()
        self.lift()
        self.attributes("-topmost", True)   # _pump() drops this after ~0.5s
        try:
            self.focus_force()
        except tk.TclError:
            pass

    def _clear(self):
        if self._spin_after:
            try:
                self.after_cancel(self._spin_after)
            except tk.TclError:
                pass
            self._spin_after = None
        for w in self.content.winfo_children():
            w.destroy()

    def _copy(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()

    def _set_status(self, text, fg, *, chip=False, chip_bg=BTN):
        for w in self.status_holder.winfo_children():
            w.destroy()
        if not chip:
            tk.Label(self.status_holder, text=text, bg=CARD, fg=fg, justify="left",
                     wraplength=WIN_W, font=(FAM, 12)).pack(anchor="w")
            return
        fnt = tkfont.Font(family=FAM, size=11, weight="bold")
        tw = fnt.measure(text) + _px(32)
        h = _px(28)
        cv = tk.Canvas(self.status_holder, width=tw, height=h, bg=CARD,
                       highlightthickness=0)
        cv.pack(anchor="w")
        _round_rect(cv, 1, 1, tw - 1, h - 1, _px(13), fill=chip_bg, outline="")
        cv.create_text(_px(16), h / 2, text=text, fill=fg, font=fnt, anchor="w")

    def _label(self, text):
        tk.Label(self.content, text=text, bg=CARD, fg=MUTED,
                 font=(FAM, 10, "bold")).pack(anchor="w", pady=(_px(12), _px(4)))

    # ------------------------------------------------------------ mode picker --
    def _show_mode_picker(self):
        self._clear()
        self._set_status("How should phones connect?", MUTED)

        RoundBtn(self.content, "Local", lambda: self._show_running(public=False),
                 kind="normal", height=60, subtitle="Same Wi-Fi only"
                 ).pack(fill="x", pady=_px(5))
        RoundBtn(self.content, "Public", self._go_public, kind="accent",
                 height=60, subtitle="Anyone with the link + code"
                 ).pack(fill="x", pady=_px(5))
        tk.Label(self.content,
                 text="Public opens a Cloudflare tunnel (needs cloudflared).",
                 bg=CARD, fg=MUTED, wraplength=WIN_W, justify="left",
                 font=(FAM, 10)).pack(anchor="w", pady=(_px(12), 0))
        self._relayout()

    def _go_public(self):
        self._clear()
        self._set_status("Opening a secure tunnel…", ACCENT, chip=True,
                         chip_bg=CHIP_PUBLIC)
        tk.Label(self.content, text="Setting up and waiting for it to come "
                 "online — up to a minute.", bg=CARD, wraplength=WIN_W, justify="left",
                 fg=MUTED, font=(FAM, 11)).pack(anchor="w")
        self._spin()
        self._tunnel_result = None
        threading.Thread(target=self._run_tunnel, daemon=True).start()
        self._relayout()

    def _spin(self):
        self._dot_h = _px(34)
        self._dots = tk.Canvas(self.content, width=WIN_W, height=self._dot_h,
                               bg=CARD, highlightthickness=0)
        self._dots.pack(pady=(_px(14), _px(4)))
        self._spin_phase = 0.0
        self._animate_dots()

    def _animate_dots(self):
        if not self._dots.winfo_exists():
            return
        self._dots.delete("all")
        cy = self._dot_h / 2
        for i in range(3):
            rad = _px(4) + _px(3) * (1 + math.sin(self._spin_phase + i * 0.9)) / 2
            x = WIN_W / 2 + (i - 1) * _px(24)
            self._dots.create_oval(x - rad, cy - rad, x + rad, cy + rad,
                                   fill=ACCENT, outline="")
        self._spin_phase += 0.35
        self._spin_after = self.after(55, self._animate_dots)

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
        self._set_status("The tunnel didn't start.", DANGER, chip=True,
                         chip_bg="#3a1420")
        hint = self.ctx.get("install_hint", lambda t: "brew install " + t)
        tk.Label(self.content, justify="left", bg=CARD, fg=MUTED,
                 wraplength=WIN_W, font=(FAM, 11),
                 text="See the terminal for cloudflared's output.\n"
                      "Install it with:   " + hint("cloudflared")
                 ).pack(anchor="w")
        RoundBtn(self.content, "Run Local instead",
                 lambda: self._show_running(public=False), kind="normal",
                 size=12).pack(fill="x", pady=(_px(14), 0))
        RoundBtn(self.content, "Try the tunnel again", self._go_public,
                 kind="ghost", size=12).pack(fill="x", pady=(_px(8), 0))
        self._relayout()

    # --------------------------------------------------------------- running ---
    def _show_running(self, public):
        self._clear()
        if public:
            self._set_status("●  Public — via Cloudflare tunnel", ACCENT,
                             chip=True, chip_bg=CHIP_PUBLIC)
        else:
            self._set_status("●  Local — same Wi-Fi only", MUTED, chip=True)
        self.ctx["banner"](public and not self.already_public)   # mirror to terminal

        host = self.ctx["host_url"]()
        play = self.ctx["play_url"]()

        self._label("HOST SCREEN — open this on the laptop")
        self._copy_pill(host, host)
        RoundBtn(self.content, "Open in browser",
                 lambda: webbrowser.open(host), kind="ghost", height=40,
                 size=11).pack(fill="x", pady=(_px(6), 0))

        self._label("PLAYERS JOIN AT")
        self._copy_pill(play.split("?")[0], play)

        row = tk.Frame(self.content, bg=CARD)
        row.pack(anchor="w", pady=(_px(14), _px(2)))
        tk.Label(row, text="ROOM CODE", bg=CARD, fg=MUTED,
                 font=(FAM, 10, "bold")).pack(side="left")
        tk.Label(row, text=self.ctx["room_code"], bg=CARD, fg=ACCENT,
                 font=(MONO, 22, "bold")).pack(side="left", padx=_px(10))

        self._show_qr()

        RoundBtn(self.content, "Quit", self.quit_now, kind="ghost",
                 size=12).pack(fill="x", pady=(_px(16), 0))
        self._relayout()

    def _copy_pill(self, shown, payload):
        btn = RoundBtn(self.content, shown, None, kind="ghost", height=42,
                       size=11, mono=True)

        def do():
            self._copy(payload)
            btn.flash("copied  ✓")

        btn.command = do
        btn.pack(fill="x")

    def _show_qr(self):
        try:
            img = tk.PhotoImage(data=base64.b64encode(self.ctx["qr_png"]()).decode())
            f = max(1, img.width() // _px(200))
            if f > 1:
                img = img.subsample(f, f)
            self._qr_img = img
            pad = _px(22)
            s = img.width()
            plate = tk.Canvas(self.content, width=s + pad, height=s + pad,
                              bg=CARD, highlightthickness=0)
            plate.pack(pady=(_px(16), _px(2)))
            _round_rect(plate, 0, 0, s + pad, s + pad, _px(18), fill="#ffffff",
                        outline="")
            plate.create_image((s + pad) / 2, (s + pad) / 2, image=img)
            tk.Label(self.content, text="scan to join", bg=CARD, fg=MUTED,
                     font=(FAM, 9)).pack(pady=(_px(4), 0))
        except Exception:
            pass

    # ------------------------------------------------------------------ quit ---
    def quit_now(self):
        self._running = False   # the pump loop in run() tears things down
