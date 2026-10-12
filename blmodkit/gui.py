"""blmodkit GUI - a simple window over the kit's commands.

    python -m blmodkit gui      (or double-click BLModKit.bat / blmodkit_gui.pyw in the kit folder)

Only tkinter (ships with Python) + numpy / Pillow. Every button calls the same functions as the
command line. Long jobs run in a worker thread and print into the log pane at the bottom.
"""
import argparse
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk

from . import __version__
from .util import CHARACTERS, ID_TO_CODE, KitError, RESERVED_CODES, Report, check_code, load_config, save_config

BG, PANEL, PANEL2, FIELD = "#15151b", "#1f1f28", "#2b2b38", "#0e0e13"
FG, MUTED, BORDER = "#ececf2", "#9a9aae", "#3a3a4c"
ACCENT, ACCENT_HI, ACCENT_LO = "#ff8a1c", "#ffab52", "#a8540a"
OK, WARN, ERR = "#7bd67b", "#ffc84a", "#ff5c5c"

CHAR_CHOICES = ["%s - %s" % (c, n) for _i, c, n in CHARACTERS]
HUE_PRESETS = [("Red", 0), ("Orange", 30), ("Yellow", 60), ("Green", 120), ("Cyan", 190), ("Blue", 230),
               ("Purple", 280), ("Pink", 326)]
FX_SAMPLES = [(255, 226, 90), (90, 170, 255), (255, 140, 40), (250, 245, 255), (200, 60, 255), (125, 95, 60)]


def char_code(choice):
    return str(choice).split(" ")[0].strip().upper()


def char_choice(code):
    for c in CHAR_CHOICES:
        if char_code(c) == str(code).upper():
            return c
    return CHAR_CHOICES[0]


def open_path(p):
    if not p or not os.path.exists(p):
        raise KitError("not found: %s" % p)
    if sys.platform == "win32":
        os.startfile(p)  # noqa: S606 - opens the user's own file / folder
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", p])


def default_mods_dir(game):
    """<exe folder>\\mods: the game folder itself when it has burstlimit.exe, else game_data_root's parent."""
    if not game:
        return ""
    from .game import find_cpk
    g = os.path.abspath(game)
    cands = []
    if os.path.isfile(os.path.join(g, "burstlimit.exe")):
        cands.append(os.path.join(g, "mods"))
    try:
        _cpk, root = find_cpk(g)
    except Exception:  # noqa: BLE001
        root = None
    if root:
        cands.append(os.path.join(os.path.dirname(os.path.abspath(root)), "mods"))
    for c in cands:
        if os.path.isdir(c):
            return c
    return cands[0] if cands else ""


def find_blender_quiet(explicit=None):
    try:
        from .model.blender import find_blender
        return find_blender(explicit or None)
    except Exception:  # noqa: BLE001
        return ""


def folder_name(name):
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_")
    return s


def font(size, bold=False):
    for f in (("segoeuib.ttf" if bold else "segoeui.ttf"), "arialbd.ttf" if bold else "arial.ttf",
              "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


# ----------------------------------------------------------------------------- output capture

class _Stream:
    """sys.stdout / sys.stderr while the GUI runs: everything goes to the log pane (thread safe)."""

    def __init__(self, q, orig):
        self.q, self.orig, self.local = q, orig, threading.local()
        self.encoding = "utf-8"

    def write(self, s):
        if not s:
            return 0
        self.q.put(s)
        buf = getattr(self.local, "buf", None)
        if buf is not None:
            buf.append(s)
        if self.orig is not None:
            try:
                self.orig.write(s)
            except Exception:  # noqa: BLE001
                pass
        return len(s)

    def flush(self):
        if self.orig is not None:
            try:
                self.orig.flush()
            except Exception:  # noqa: BLE001
                pass

    def isatty(self):
        return False


# ----------------------------------------------------------------------------- art preview composite

def compose_art(out):
    """The three pictures in a select-screen-like frame (big picture as player 1, the card on the
    selected and the list plate, the HUD face on the battle HUD)."""
    W, H = 20 + 368 + 24 + 512 + 20, 20 + 720 + 20
    sheet = Image.new("RGBA", (W, H), (21, 21, 27, 255))
    d = ImageDraw.Draw(sheet)
    g = np.linspace(0, 1, 720)[:, None, None]
    grad = ((1 - g) * np.array([70, 40, 110]) + g * np.array([12, 10, 24])) * np.ones((720, 368, 1))
    bg = Image.fromarray(np.dstack([grad, np.full((720, 368, 1), 255)]).astype(np.uint8), "RGBA")
    bg.alpha_composite(Image.fromarray(out["portrait"], "RGBA"))
    sheet.alpha_composite(bg, (20, 20))
    d.rectangle([19, 19, 20 + 368, 20 + 720], outline=(58, 58, 76, 255))
    x = 20 + 368 + 24
    f, fb = font(16), font(16, True)
    d.text((x, 16), "Character select: selected / in the list", font=fb, fill=(255, 171, 82, 255))
    for k, plate in enumerate(((232, 226, 214), (70, 60, 58))):
        y = 44 + k * 140
        im = Image.new("RGBA", (512, 120), plate + (255,))
        im.alpha_composite(Image.fromarray(out["card"], "RGBA"))
        sheet.alpha_composite(im, (x, y))
        if k == 0:
            d.rectangle([x - 3, y - 3, x + 514, y + 122], outline=(255, 138, 28, 255), width=3)
    y = 330
    d.text((x, y - 26), "Battle HUD: player 1 / player 2", font=fb, fill=(255, 171, 82, 255))
    hud = Image.fromarray(out["hud"], "RGBA")
    for k, im in enumerate((hud, hud.transpose(Image.FLIP_LEFT_RIGHT))):
        hx = x + k * 260
        plate = Image.new("RGBA", (236, 268), (24, 20, 30, 255))
        plate.alpha_composite(im)
        sheet.alpha_composite(plate, (hx, y))
        d.rectangle([hx, y + 276, hx + 236, y + 290], fill=(40, 34, 50, 255))
        d.rectangle([hx + 2, y + 278, hx + 200, y + 288], fill=(255, 196, 40, 255))
    d.text((x, y + 304), "card 512x120  -  big picture 368x720  -  HUD face 236x268", font=f,
           fill=(154, 154, 174, 255))
    return sheet


def checker(w, h, s=10):
    yy, xx = np.mgrid[0:h, 0:w]
    v = np.where(((xx // s) + (yy // s)) % 2 == 0, 48, 36).astype(np.uint8)
    return Image.fromarray(np.dstack([v, v, v, np.full_like(v, 255)]), "RGBA")


# ----------------------------------------------------------------------------- the app

class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.busy = False
        self.run_buttons = []
        self.logq = queue.Queue()
        self.events = queue.Queue()
        self.stream = _Stream(self.logq, sys.__stdout__)
        self._old = (sys.stdout, sys.stderr)
        sys.stdout = sys.stderr = self.stream
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        self.mod_paths = {}
        self.last_result = {}
        root.report_callback_exception = self._tk_error
        self._style()
        root.title("blmodkit %s - DBZ Burst Limit modder kit" % __version__)
        root.configure(bg=BG)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = min(int(1360 * self.scale), int(sw * 0.94)), min(int(900 * self.scale), int(sh * 0.9))
        root.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2), max(0, (sh - h) // 3)))
        root.minsize(int(900 * self.scale), int(600 * self.scale))
        self._icon()
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(60, self._poll)
        self.refresh_mods()
        if not self.cfg.get("game"):
            self.nb.select(self.tab_setup)

    # ------------------------------------------------------------------ look
    def _style(self):
        st = ttk.Style(self.root)
        st.theme_use("clam")
        self.root.option_add("*Font", ("Segoe UI", 10))
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#000000")
        st.configure(".", background=PANEL, foreground=FG, fieldbackground=FIELD, bordercolor=BORDER,
                     lightcolor=PANEL2, darkcolor=PANEL, troughcolor=FIELD, insertcolor=FG,
                     selectbackground=ACCENT, selectforeground="#000000", font=("Segoe UI", 10))
        st.configure("TFrame", background=PANEL)
        st.configure("Bg.TFrame", background=BG)
        st.configure("TLabel", background=PANEL, foreground=FG)
        st.configure("Bg.TLabel", background=BG)
        st.configure("Muted.TLabel", foreground=MUTED)
        st.configure("Head.TLabel", foreground=ACCENT_HI, font=("Segoe UI Semibold", 12))
        st.configure("Ok.TLabel", foreground=OK)
        st.configure("Err.TLabel", foreground=ERR)
        st.configure("Warn.TLabel", foreground=WARN)
        st.configure("TLabelframe", background=PANEL, bordercolor=BORDER)
        st.configure("TLabelframe.Label", background=PANEL, foreground=ACCENT_HI, font=("Segoe UI Semibold", 10))
        st.configure("TButton", background=PANEL2, foreground=FG, bordercolor=BORDER, padding=(10, 4))
        st.map("TButton", background=[("disabled", PANEL), ("pressed", ACCENT_LO), ("active", "#3a3a4a")],
               foreground=[("disabled", MUTED)])
        st.configure("Accent.TButton", background=ACCENT, foreground="#111111", bordercolor=ACCENT_LO,
                     font=("Segoe UI Semibold", 10), padding=(14, 5))
        st.map("Accent.TButton", background=[("disabled", "#5a3a1c"), ("pressed", ACCENT_LO), ("active", ACCENT_HI)],
               foreground=[("disabled", "#8a7a6a")])
        st.configure("TEntry", fieldbackground=FIELD, foreground=FG, insertcolor=FG)
        st.map("TEntry", fieldbackground=[("disabled", PANEL)], foreground=[("disabled", MUTED)])
        st.configure("TSpinbox", fieldbackground=FIELD, foreground=FG, arrowcolor=FG, background=PANEL2,
                     insertcolor=FG)
        st.map("TSpinbox", fieldbackground=[("disabled", PANEL)], foreground=[("disabled", MUTED)])
        st.configure("TCombobox", fieldbackground=FIELD, foreground=FG, arrowcolor=FG, background=PANEL2)
        st.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", FG)],
               selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", FG)])
        for w in ("TCheckbutton", "TRadiobutton"):
            st.configure(w, background=PANEL, foreground=FG, indicatorbackground=FIELD, indicatorforeground=ACCENT)
            st.map(w, background=[("active", PANEL)], indicatorbackground=[("selected", ACCENT)],
                   foreground=[("disabled", MUTED)])
        st.configure("Horizontal.TScale", background=ACCENT, troughcolor=FIELD, bordercolor=BORDER,
                     lightcolor=ACCENT_HI, darkcolor=ACCENT_LO)
        st.configure("TNotebook", background=BG, bordercolor=BORDER, tabmargins=(6, 6, 6, 0))
        st.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(16, 6),
                     font=("Segoe UI Semibold", 10), bordercolor=BORDER)
        st.map("TNotebook.Tab", background=[("selected", ACCENT)], foreground=[("selected", "#111111")],
               expand=[("selected", (1, 1, 1, 0))])
        st.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=FIELD, bordercolor=BORDER,
                     lightcolor=ACCENT_HI, darkcolor=ACCENT_LO)
        st.configure("Treeview", background=FIELD, fieldbackground=FIELD, foreground=FG, rowheight=int(22 * self.scale))
        st.map("Treeview", background=[("selected", ACCENT_LO)])
        st.configure("Treeview.Heading", background=PANEL2, foreground=ACCENT_HI)
        st.configure("TPanedwindow", background=BG)
        st.configure("Vertical.TScrollbar", background=PANEL2, troughcolor=FIELD, arrowcolor=FG)

    def _icon(self):
        im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.ellipse([2, 2, 62, 62], fill=(255, 138, 28, 255), outline=(20, 20, 20, 255), width=3)
        d.text((32, 33), "BL", font=font(26, True), fill=(20, 20, 20, 255), anchor="mm")
        self._icon_img = ImageTk.PhotoImage(im)
        self.root.iconphoto(True, self._icon_img)

    # ------------------------------------------------------------------ small widget helpers
    def frame(self, parent, **kw):
        return ttk.Frame(parent, padding=kw.pop("padding", 0), **kw)

    def path_row(self, parent, row, label, var, kind="dir", types=None, on_pick=None, width=60):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
        e = ttk.Entry(parent, textvariable=var, width=width)
        e.grid(row=row, column=1, sticky="ew", pady=3)

        def pick():
            init = var.get() if os.path.isdir(var.get()) else os.path.dirname(var.get() or "")
            if kind == "dir":
                p = filedialog.askdirectory(parent=self.root, initialdir=init or None, title=label)
            else:
                p = filedialog.askopenfilename(parent=self.root, initialdir=init or None, title=label,
                                               filetypes=types or [("All files", "*.*")])
            if p:
                var.set(os.path.normpath(p))
                if on_pick:
                    on_pick()
        ttk.Button(parent, text="Browse...", command=pick).grid(row=row, column=2, padx=(6, 0), pady=3)
        return e

    def slider(self, parent, var, frm, to, res, length=200, on_change=None):
        f = ttk.Frame(parent)

        def moved(v):
            var.set(round(round(float(v) / res) * res, 4))
        s = ttk.Scale(f, from_=frm, to=to, orient="horizontal", length=int(length * self.scale), command=moved)
        try:
            s.set(float(var.get()))
        except (tk.TclError, ValueError):
            pass
        s.pack(side="left", fill="x", expand=True)
        e = ttk.Entry(f, textvariable=var, width=7)
        e.pack(side="left", padx=(6, 0))

        def sync(*_):
            try:
                v = float(var.get())
                if abs(s.get() - v) > res / 2:
                    s.set(v)
            except (tk.TclError, ValueError):
                pass
            if on_change:
                on_change()
        var.trace_add("write", sync)
        return f

    def run_button(self, parent, text, cmd, accent=True):
        b = ttk.Button(parent, text=text, command=cmd, style="Accent.TButton" if accent else "TButton")
        self.run_buttons.append(b)
        return b

    # ------------------------------------------------------------------ layout
    def _build(self):
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x")
        logo = tk.Canvas(head, width=int(330 * self.scale), height=int(54 * self.scale), bg=BG, highlightthickness=0)
        logo.pack(side="left", padx=(10, 0), pady=6)
        s = self.scale
        logo.create_polygon(0, 8 * s, 300 * s, 8 * s, 280 * s, 46 * s, 0, 46 * s, fill=ACCENT, outline="")
        logo.create_polygon(300 * s, 8 * s, 318 * s, 8 * s, 298 * s, 46 * s, 280 * s, 46 * s, fill=ACCENT_LO, outline="")
        logo.create_text(14 * s, 27 * s, text="BURST LIMIT  MODKIT", anchor="w", fill="#111111",
                         font=("Segoe UI Black", 17, "italic"))
        right = ttk.Frame(head, style="Bg.TFrame")
        right.pack(side="right", padx=12)
        ttk.Label(right, text="Current mod:", style="Bg.TLabel").grid(row=0, column=0, padx=(0, 6))
        self.mod_var = tk.StringVar()
        self.mod_combo = ttk.Combobox(right, textvariable=self.mod_var, width=34, state="readonly")
        self.mod_combo.grid(row=0, column=1)
        self.mod_combo.bind("<<ComboboxSelected>>", lambda e: self.mod_changed())
        ttk.Button(right, text="Browse...", command=self.browse_mod).grid(row=0, column=2, padx=4)
        ttk.Button(right, text="Refresh", command=self.refresh_mods).grid(row=0, column=3)
        self.mod_info = ttk.Label(right, text="", style="Bg.TLabel", foreground=MUTED)
        self.mod_info.grid(row=1, column=0, columnspan=4, sticky="e")

        pw = ttk.PanedWindow(self.root, orient="vertical")
        pw.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.nb = ttk.Notebook(pw)
        pw.add(self.nb, weight=4)
        self.tab_setup = self.frame(self.nb, padding=14)
        self.tab_new = self.frame(self.nb, padding=14)
        self.tab_art = self.frame(self.nb, padding=10)
        self.tab_model = self.frame(self.nb, padding=10)
        self.tab_fx = self.frame(self.nb, padding=14)
        self.tab_check = self.frame(self.nb, padding=10)
        for t, n in ((self.tab_setup, "1  Setup"), (self.tab_new, "2  New character"), (self.tab_art, "3  Art"),
                     (self.tab_model, "4  Model"), (self.tab_fx, "5  Effects"), (self.tab_check, "6  Check & test")):
            self.nb.add(t, text=n)
        self.build_setup()
        self.build_new()
        self.build_art()
        self.build_model()
        self.build_fx()
        self.build_check()
        self.build_log(pw)

    def build_log(self, pw):
        f = self.frame(pw, padding=(8, 6))
        pw.add(f, weight=1)
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.status = ttk.Label(top, text="Ready.", style="Muted.TLabel")
        self.status.pack(side="left")
        ttk.Button(top, text="Clear log", command=lambda: self.log.delete("1.0", "end")).pack(side="right")
        self.progress = ttk.Progressbar(top, mode="determinate", value=0, length=int(260 * self.scale))
        self.progress.pack(side="right", padx=10)
        body = ttk.Frame(f)
        body.pack(fill="both", expand=True, pady=(6, 0))
        self.log = tk.Text(body, height=8, bg=FIELD, fg="#d4d4de", insertbackground=FG, relief="flat",
                           font=("Consolas", 9), wrap="none", borderwidth=0, highlightthickness=1,
                           highlightbackground=BORDER)
        sb = ttk.Scrollbar(body, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("err", foreground=ERR)
        self.log.tag_configure("warn", foreground=WARN)
        self.log.tag_configure("ok", foreground=OK)
        self.log.tag_configure("head", foreground=ACCENT_HI)

    # ------------------------------------------------------------------ 1. setup
    def build_setup(self):
        t = self.tab_setup
        t.columnconfigure(1, weight=1)
        ttk.Label(t, text="Where your game is", style="Head.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(t, text="The kit has no game files: every command reads them from your own copy.",
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 10))
        self.game_var = tk.StringVar(value=self.cfg.get("game", ""))
        self.mods_var = tk.StringVar(value=self.cfg.get("mods_dir") or default_mods_dir(self.cfg.get("game")))
        self.blender_var = tk.StringVar(value=self.cfg.get("blender") or find_blender_quiet())
        self.exe_var = tk.StringVar(value=self.cfg.get("game_exe", ""))
        if not self.exe_var.get() and self.mods_var.get():
            exe = os.path.join(os.path.dirname(self.mods_var.get()), "burstlimit.exe")
            if os.path.isfile(exe):
                self.exe_var.set(exe)
        self.path_row(t, 2, "Game (game_data_root)", self.game_var, on_pick=self.game_picked)
        self.game_status = ttk.Label(t, text="")
        self.game_status.grid(row=3, column=1, sticky="w")
        self.path_row(t, 4, "Mods folder", self.mods_var)
        ttk.Label(t, text="Default: the mods folder next to burstlimit.exe (game_data_root's parent folder).",
                  style="Muted.TLabel").grid(row=5, column=1, sticky="w")
        self.path_row(t, 6, "Blender (for Model)", self.blender_var, kind="file",
                      types=[("blender.exe", "blender.exe"), ("Programs", "*.exe")])
        b = ttk.Frame(t)
        b.grid(row=7, column=1, sticky="w")
        ttk.Button(b, text="Auto-detect", command=lambda: self.blender_var.set(find_blender_quiet())).pack(side="left")
        ttk.Label(b, text="  Blender 4.x / 5.x, only needed for importing models.", style="Muted.TLabel").pack(side="left")
        self.path_row(t, 8, "burstlimit.exe (optional)", self.exe_var, kind="file", types=[("Programs", "*.exe")])
        ttk.Label(t, text="Only used by 'Launch game' on the Check & test tab.",
                  style="Muted.TLabel").grid(row=9, column=1, sticky="w")
        bb = ttk.Frame(t)
        bb.grid(row=10, column=1, sticky="w", pady=14)
        ttk.Button(bb, text="Save settings", style="Accent.TButton", command=self.save_setup).pack(side="left")
        self.setup_saved = ttk.Label(bb, text="")
        self.setup_saved.pack(side="left", padx=10)
        self.game_var.trace_add("write", lambda *_: self.validate_game())
        self.validate_game()

    def game_picked(self):
        d = default_mods_dir(self.game_var.get())
        if d:
            self.mods_var.set(d)
            exe = os.path.join(os.path.dirname(d), "burstlimit.exe")
            if os.path.isfile(exe) and not self.exe_var.get():
                self.exe_var.set(exe)

    def validate_game(self):
        from .game import find_cpk
        g = self.game_var.get().strip()
        if not g:
            self.game_status.configure(text="Not set - pick the folder that has default.xex and LONG2DATA.",
                                       style="Warn.TLabel")
            return False
        cpk, root = find_cpk(g)
        if not cpk:
            self.game_status.configure(text="No LONG2DATA_US.CPK found in or under this folder.", style="Err.TLabel")
            return False
        xex = os.path.isfile(os.path.join(root or "", "default.xex"))
        self.game_status.configure(text="Found %s%s" % (cpk, "" if xex else "  (default.xex missing: hits and "
                                                                             "Ultimates need it)"),
                                   style="Ok.TLabel" if xex else "Warn.TLabel")
        return True

    def save_setup(self):
        if not self.validate_game():
            raise KitError("pick your game folder first (the folder with default.xex and LONG2DATA)")
        self.cfg["game"] = os.path.abspath(self.game_var.get().strip())
        for k, v in (("mods_dir", self.mods_var), ("blender", self.blender_var), ("game_exe", self.exe_var)):
            val = v.get().strip()
            if val:
                self.cfg[k] = os.path.abspath(val)
            else:
                self.cfg.pop(k, None)
        save_config(self.cfg)
        from .util import config_path
        print("Saved settings: %s" % config_path())
        self.setup_saved.configure(text="Saved.", style="Ok.TLabel")
        self.refresh_mods()

    # ------------------------------------------------------------------ current mod
    @property
    def game(self):
        g = self.game_var.get().strip() or self.cfg.get("game")
        if not g:
            raise KitError("set your game folder on the Setup tab first")
        return g

    @property
    def mods_dir(self):
        return self.mods_var.get().strip()

    def refresh_mods(self, select=None):
        self.mod_paths = {}
        d = self.mods_dir
        if d and os.path.isdir(d):
            for n in sorted(os.listdir(d), key=str.lower):
                if os.path.isfile(os.path.join(d, n, "slot.toml")):
                    self.mod_paths[n] = os.path.join(d, n)
        cur = self.mod_var.get()
        if cur in self.mod_paths or (cur and os.path.isdir(cur)):
            if cur not in self.mod_paths:
                self.mod_paths[cur] = cur
        self.mod_combo["values"] = list(self.mod_paths)
        if select:
            name = os.path.basename(select) if os.path.dirname(os.path.abspath(select)) == os.path.abspath(d or "") \
                else select
            self.mod_paths.setdefault(name, select)
            self.mod_combo["values"] = list(self.mod_paths)
            self.mod_var.set(name)
        elif not cur and self.mod_paths:
            self.mod_var.set(next(iter(self.mod_paths)))
        self.mod_changed()

    def browse_mod(self):
        p = filedialog.askdirectory(parent=self.root, initialdir=self.mods_dir or None, title="Slot mod folder")
        if p:
            self.refresh_mods(select=os.path.normpath(p))

    @property
    def mod(self):
        m = self.mod_paths.get(self.mod_var.get()) or self.mod_var.get()
        if not m:
            raise KitError("pick a mod at the top (Current mod), or make one on the New character tab")
        if not os.path.isfile(os.path.join(m, "slot.toml")):
            raise KitError("%s has no slot.toml - make the slot on the New character tab first" % m)
        return m

    def slot_info(self):
        from . import slotfile
        try:
            _d, slots = slotfile.read(self.mod)
            return slots[0] if slots else None
        except Exception:  # noqa: BLE001
            return None

    def mod_changed(self):
        s = self.slot_info()
        if s:
            self.mod_info.configure(text="%s  \"%s\"  plays as %s  -  %d colour(s)" % (
                s.code, s.name, ID_TO_CODE.get(s.base, "?"), int(s.colours)))
            if hasattr(self, "model_base"):
                self.model_base.set(char_choice(ID_TO_CODE.get(s.base, "GOK")))
        else:
            self.mod_info.configure(text="no mod selected" if not self.mod_var.get() else "no slot.toml")
        if hasattr(self, "art_status"):
            self.art_changed()

    # ------------------------------------------------------------------ 2. new character
    def build_new(self):
        t = self.tab_new
        t.columnconfigure(1, weight=1)
        ttk.Label(t, text="Make a new extra character from one of the game's 21", style="Head.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        self.new_base = tk.StringVar(value=char_choice("VGT"))
        self.new_code = tk.StringVar()
        self.new_name = tk.StringVar()
        self.new_out = tk.StringVar()
        self.new_enable = tk.BooleanVar(value=True)
        self.new_force = tk.BooleanVar(value=False)
        self.new_colours = tk.StringVar(value="all")
        self.new_maxform = tk.StringVar(value="0")
        self.new_author = tk.StringVar()
        self._out_auto = True
        r = 1
        ttk.Label(t, text="Base character").grid(row=r, column=0, sticky="w", pady=3, padx=(0, 10))
        ttk.Combobox(t, textvariable=self.new_base, values=CHAR_CHOICES, state="readonly", width=30).grid(
            row=r, column=1, sticky="w")
        r += 1
        ttk.Label(t, text="It plays as this character (moves, CPU, Ultimates) and starts with its files.",
                  style="Muted.TLabel").grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(t, text="New code (3 letters)").grid(row=r, column=0, sticky="w", pady=3, padx=(0, 10))
        cf = ttk.Frame(t)
        cf.grid(row=r, column=1, sticky="w")
        ttk.Entry(cf, textvariable=self.new_code, width=8).pack(side="left")
        self.code_status = ttk.Label(cf, text="")
        self.code_status.pack(side="left", padx=10)
        r += 1
        ttk.Label(t, text="Display name").grid(row=r, column=0, sticky="w", pady=3, padx=(0, 10))
        nf = ttk.Frame(t)
        nf.grid(row=r, column=1, sticky="w")
        ttk.Entry(nf, textvariable=self.new_name, width=30).pack(side="left")
        self.name_status = ttk.Label(nf, text="")
        self.name_status.pack(side="left", padx=10)
        r += 1
        e = self.path_row(t, r, "Mod folder", self.new_out, on_pick=lambda: setattr(self, "_out_auto", False))
        e.bind("<Key>", lambda ev: setattr(self, "_out_auto", False))
        r += 1
        of = ttk.Frame(t)
        of.grid(row=r, column=1, sticky="w", pady=6)
        ttk.Label(of, text="Colours").pack(side="left")
        ttk.Combobox(of, textvariable=self.new_colours, values=["all", "1", "2", "3", "4"], width=5).pack(
            side="left", padx=(4, 16))
        ttk.Label(of, text="Highest form (0 = none, experimental)").pack(side="left")
        ttk.Spinbox(of, textvariable=self.new_maxform, from_=0, to=7, width=4).pack(side="left", padx=(4, 16))
        ttk.Label(of, text="Author").pack(side="left")
        ttk.Entry(of, textvariable=self.new_author, width=16).pack(side="left", padx=4)
        r += 1
        ttk.Checkbutton(t, text="Switch it on (add it to mods.toml)", variable=self.new_enable).grid(
            row=r, column=1, sticky="w")
        r += 1
        ttk.Checkbutton(t, text="Write into the folder even if it isn't empty", variable=self.new_force).grid(
            row=r, column=1, sticky="w")
        r += 1
        bf = ttk.Frame(t)
        bf.grid(row=r, column=1, sticky="w", pady=14)
        self.run_button(bf, "Create character", self.do_new).pack(side="left")
        r += 1
        self.new_result = ttk.Label(t, text="", style="Ok.TLabel")
        self.new_result.grid(row=r, column=1, sticky="w")
        r += 1
        rb = ttk.Frame(t)
        rb.grid(row=r, column=1, sticky="w", pady=4)
        self.new_open_toml = ttk.Button(rb, text="Open slot.toml", state="disabled",
                                        command=lambda: subprocess.Popen(["notepad.exe", self.last_result["slot_toml"]])
                                        if sys.platform == "win32" else open_path(self.last_result["slot_toml"]))
        self.new_open_toml.pack(side="left")
        self.new_open_dir = ttk.Button(rb, text="Open folder", state="disabled",
                                       command=lambda: open_path(self.last_result["new_dir"]))
        self.new_open_dir.pack(side="left", padx=6)
        for v in (self.new_code, self.new_name):
            v.trace_add("write", lambda *_: self.validate_new())
        self.mods_var.trace_add("write", lambda *_: self.validate_new())
        self.validate_new()

    def validate_new(self):
        code = self.new_code.get().strip().upper()
        ok = True
        if not code:
            self.code_status.configure(text="3 letters / digits, e.g. TST", style="Muted.TLabel")
            ok = False
        else:
            try:
                check_code(code)
                from .newslot import other_codes
                used = other_codes(self.mods_dir, os.path.join(self.mods_dir or ".", "\0none")) if self.mods_dir else {}
                if code in used:
                    raise KitError("already used by the mod %s" % used[code])
                self.code_status.configure(text="OK", style="Ok.TLabel")
            except KitError as e:
                self.code_status.configure(text=str(e).split(" - ")[0], style="Err.TLabel")
                ok = False
        name = self.new_name.get()
        if name and (not all(32 <= ord(c) < 127 for c in name) or '"' in name):
            self.name_status.configure(text="plain ASCII only, no quotes", style="Err.TLabel")
            ok = False
        elif len(name) > 24:
            self.name_status.configure(text="long names may not fit the HUD", style="Warn.TLabel")
        else:
            self.name_status.configure(text="")
        if self._out_auto and self.mods_dir:
            stem = folder_name(name) or code
            self.new_out.set(os.path.join(self.mods_dir, stem) if stem else "")
        return ok

    def do_new(self):
        if not self.validate_new():
            raise KitError("fix the code / name first (see the notes next to them)")
        base, code, name = char_code(self.new_base.get()), self.new_code.get().strip().upper(), self.new_name.get()
        out = self.new_out.get().strip()
        if not out:
            raise KitError("pick the mod folder")
        col = self.new_colours.get().strip().lower()
        colours = None if col in ("", "all") else int(col)
        mf = int(self.new_maxform.get() or 0)
        game, author, force, enable = self.game, self.new_author.get(), self.new_force.get(), self.new_enable.get()

        def job():
            from .game import Game
            from .newslot import run
            return run(Game(game), base, code, name or code, out, colours, mf, author, force, enable)

        def done(res, _out):
            p = os.path.join(res, "slot.toml")
            self.last_result.update(slot_toml=p, new_dir=res)
            self.new_result.configure(text="Created: %s" % p)
            self.new_open_toml.configure(state="normal")
            self.new_open_dir.configure(state="normal")
            self.refresh_mods(select=res)
        self.run_job("New character %s" % code, job, done)

    # ------------------------------------------------------------------ 3. art
    def build_art(self):
        t = self.tab_art
        t.columnconfigure(1, weight=1)
        t.rowconfigure(0, weight=1)
        c = ttk.Frame(t, padding=(0, 0, 10, 0))
        c.grid(row=0, column=0, sticky="ns")
        self.art_src = tk.StringVar(value=self.cfg.get("last_art_src", ""))
        self.art_subject = tk.StringVar(value="full")
        self.art_auto = tk.BooleanVar(value=True)
        self.art_fx, self.art_fy, self.art_fs = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.art_zoom = tk.DoubleVar(value=1.0)
        self.art_z = {n: tk.StringVar(value="1.0") for n in ("card", "portrait", "hud")}
        self.art_sx = {n: tk.StringVar(value="0") for n in ("card", "portrait", "hud")}
        self.art_sy = {n: tk.StringVar(value="0") for n in ("card", "portrait", "hud")}
        self.art_style = tk.StringVar(value="cel")
        self.art_colours = tk.StringVar(value="12")
        self.art_ink = tk.DoubleVar(value=55.0)
        self.art_crop = tk.StringVar()
        self.art_flip = tk.BooleanVar(value=False)
        self.art_tol = tk.StringVar(value="40")
        self.art_only = tk.StringVar(value="all three")

        f = ttk.Frame(c)
        f.pack(fill="x")
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="Picture").grid(row=0, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.art_src, width=28).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(f, text="Browse...", command=self.pick_art).grid(row=0, column=2)
        af = ttk.Frame(c)
        af.pack(side="bottom", fill="x", pady=(8, 0))
        self.src_canvas = tk.Canvas(c, width=int(300 * self.scale), height=int(190 * self.scale), bg=FIELD,
                                    highlightthickness=1, highlightbackground=BORDER, cursor="crosshair")
        self.src_canvas.pack(pady=6)
        self.src_canvas.bind("<Button-1>", self.art_click)
        ttk.Label(c, text="Click the point between the eyes to set the face.", style="Muted.TLabel").pack(anchor="w")

        fr = ttk.LabelFrame(c, text="Framing", padding=6)
        fr.pack(fill="x", pady=4)
        sf = ttk.Frame(fr)
        sf.grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(sf, text="Picture shows").pack(side="left")
        for v in ("full", "bust", "head"):
            ttk.Radiobutton(sf, text=v, value=v, variable=self.art_subject).pack(side="left", padx=4)
        ttk.Checkbutton(fr, text="Guess the face", variable=self.art_auto, command=self.art_auto_toggled).grid(
            row=1, column=0, sticky="w")
        ff = ttk.Frame(fr)
        ff.grid(row=2, column=0, columnspan=4, sticky="w", pady=2)
        self.face_spins = []
        for lbl, v in (("Face X", self.art_fx), ("Y", self.art_fy), ("eyes-chin px", self.art_fs)):
            ttk.Label(ff, text=lbl).pack(side="left", padx=(0, 3))
            sp = ttk.Spinbox(ff, textvariable=v, from_=0, to=20000, increment=2, width=6)
            sp.pack(side="left", padx=(0, 8))
            self.face_spins.append(sp)
        ttk.Label(fr, text="Zoom").grid(row=3, column=0, sticky="w")
        self.slider(fr, self.art_zoom, 0.4, 2.5, 0.05, 160, self.art_changed).grid(row=3, column=1, columnspan=3,
                                                                                 sticky="ew")
        g = ttk.Frame(fr)
        g.grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))
        for j, h in enumerate(("", "zoom", "move X", "move Y")):
            ttk.Label(g, text=h, style="Muted.TLabel").grid(row=0, column=j)
        for i, n in enumerate(("card", "portrait", "hud")):
            ttk.Label(g, text={"card": "Card", "portrait": "Big picture", "hud": "HUD face"}[n]).grid(
                row=i + 1, column=0, sticky="w", padx=(0, 6))
            ttk.Spinbox(g, textvariable=self.art_z[n], from_=0.3, to=3.0, increment=0.05, width=5).grid(
                row=i + 1, column=1, padx=2)
            ttk.Spinbox(g, textvariable=self.art_sx[n], from_=-400, to=400, increment=2, width=5).grid(
                row=i + 1, column=2, padx=2)
            ttk.Spinbox(g, textvariable=self.art_sy[n], from_=-400, to=400, increment=2, width=5).grid(
                row=i + 1, column=3, padx=2)

        lk = ttk.LabelFrame(c, text="Look and source", padding=6)
        lk.pack(fill="x", pady=4)
        sf = ttk.Frame(lk)
        sf.grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(sf, text="Style").pack(side="left")
        for v in ("cel", "flat", "none"):
            ttk.Radiobutton(sf, text=v, value=v, variable=self.art_style).pack(side="left", padx=4)
        ttk.Label(sf, text="  Colours").pack(side="left")
        ttk.Spinbox(sf, textvariable=self.art_colours, from_=2, to=32, width=4).pack(side="left", padx=4)
        ttk.Label(lk, text="Ink").grid(row=1, column=0, sticky="w")
        self.slider(lk, self.art_ink, 0, 160, 1, 160, self.art_changed).grid(row=1, column=1, columnspan=3,
                                                                           sticky="ew")
        sf = ttk.Frame(lk)
        sf.grid(row=2, column=0, columnspan=4, sticky="w", pady=2)
        ttk.Label(sf, text="Crop X0,Y0,X1,Y1").pack(side="left")
        ttk.Entry(sf, textvariable=self.art_crop, width=16).pack(side="left", padx=4)
        ttk.Checkbutton(sf, text="Mirror", variable=self.art_flip).pack(side="left", padx=4)
        sf = ttk.Frame(lk)
        sf.grid(row=3, column=0, columnspan=4, sticky="w")
        ttk.Label(sf, text="Background removal (no alpha)").pack(side="left")
        ttk.Spinbox(sf, textvariable=self.art_tol, from_=0, to=255, width=5).pack(side="left", padx=4)

        ttk.Label(af, text="Make").pack(side="left")
        ttk.Combobox(af, textvariable=self.art_only, values=["all three", "card", "portrait", "hud"],
                     state="readonly", width=10).pack(side="left", padx=4)
        self.run_button(af, "Apply to mod", self.do_art).pack(side="left", padx=6)
        self.art_open = ttk.Button(af, text="Open preview", command=self.open_art_preview)
        self.art_open.pack(side="left")

        pv = ttk.Frame(t)
        pv.grid(row=0, column=1, sticky="nsew")
        self.art_status = ttk.Label(pv, text="Pick a picture (PNG with a transparent background works best).",
                                    style="Muted.TLabel")
        self.art_status.pack(anchor="w")
        self.art_canvas = tk.Canvas(pv, bg=BG, highlightthickness=0)
        self.art_canvas.pack(fill="both", expand=True)
        self.art_canvas.bind("<Configure>", lambda e: self.show_art())
        self._art_img = None
        self._art_sheet = None
        self._art_thumb = None
        self._art_gen = 0
        self._art_after = None
        self._art_cache = {}
        self._art_quiet = False
        self._art_req = None
        self._art_event = threading.Event()
        threading.Thread(target=self._art_worker, daemon=True).start()
        vs = [self.art_src, self.art_subject, self.art_fx, self.art_fy, self.art_fs, self.art_style,
              self.art_colours, self.art_crop, self.art_flip, self.art_tol] + list(self.art_z.values()) + \
            list(self.art_sx.values()) + list(self.art_sy.values())
        for v in vs:
            v.trace_add("write", lambda *_: self.art_changed())
        self.art_auto_toggled()

    def pick_art(self):
        p = filedialog.askopenfilename(parent=self.root, title="Your character picture",
                                       filetypes=[("Pictures", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All", "*.*")])
        if p:
            self.art_src.set(os.path.normpath(p))

    def art_auto_toggled(self):
        for sp in self.face_spins:
            sp.configure(state="disabled" if self.art_auto.get() else "normal")
        self.art_changed()

    def art_params(self):
        def fl(v, what):
            try:
                return float(v.get())
            except (tk.TclError, ValueError):
                raise KitError("%s must be a number" % what)
        src = self.art_src.get().strip()
        if not src or not os.path.isfile(src):
            raise KitError("pick a picture (PNG) first")
        crop = None
        if self.art_crop.get().strip():
            try:
                crop = tuple(int(x) for x in self.art_crop.get().split(","))
                assert len(crop) == 4
            except (ValueError, AssertionError):
                raise KitError("crop must be X0,Y0,X1,Y1")
        auto = self.art_auto.get()
        p = dict(src=src, subject=self.art_subject.get(), auto=auto,
                 face=None if auto else (fl(self.art_fx, "Face X"), fl(self.art_fy, "Face Y")),
                 face_size=None if auto else fl(self.art_fs, "eyes-chin"),
                 zoom=fl(self.art_zoom, "Zoom"), style=self.art_style.get(), colours=int(fl(self.art_colours, "Colours")),
                 ink=fl(self.art_ink, "Ink"), crop=crop, flip=self.art_flip.get(), tol=int(fl(self.art_tol, "tolerance")))
        for n in ("card", "portrait", "hud"):
            p[n + "_zoom"] = fl(self.art_z[n], n + " zoom")
            p[n + "_shift"] = (fl(self.art_sx[n], n + " move X"), fl(self.art_sy[n], n + " move Y"))
        if p["face_size"] is not None and p["face_size"] <= 1:
            raise KitError("the eyes-to-chin size must be more than 1 pixel")
        return p

    def art_changed(self):
        if self._art_quiet or not hasattr(self, "art_canvas"):
            return
        if self._art_after:
            self.root.after_cancel(self._art_after)
        self._art_after = self.root.after(300, self._art_request)

    def _art_request(self):
        self._art_after = None
        try:
            p = self.art_params()
        except KitError as e:
            self.art_status.configure(text=str(e), style="Muted.TLabel")
            return
        self._art_gen += 1
        self._art_req = (self._art_gen, p)
        self.art_status.configure(text="Rendering preview...", style="Muted.TLabel")
        self._art_event.set()

    def _art_worker(self):
        from . import art
        while True:
            self._art_event.wait()
            self._art_event.clear()
            gen, p = self._art_req
            try:
                key = (p["src"], os.path.getmtime(p["src"]), p["crop"], p["flip"], p["tol"])
                src = self._art_cache.get(key)
                if src is None:
                    src = art.load_source(p["src"], p["tol"], p["crop"], p["flip"])
                    self._art_cache = {key: src}
                gx, gy, gs = art.guess_face(src, p["subject"])
                face = p["face"] or (gx, gy)
                fsz = p["face_size"] or gs
                styles = ["flat", "cel"] if p["style"] == "cel" else [p["style"]]
                for k, style in enumerate(styles):
                    if gen != self._art_gen:
                        break
                    opts = dict(zoom=p["zoom"], style=style, colours=p["colours"], ink=p["ink"])
                    for n in ("card", "portrait", "hud"):
                        opts[n + "_zoom"] = p[n + "_zoom"]
                        opts[n + "_shift"] = p[n + "_shift"]
                    out = {n: art.render(src, face, fsz, n, opts) for n in ("card", "portrait", "hud")}
                    sheet = compose_art(out)
                    self.events.put(("art", gen, sheet, src if k == 0 else None, (face, fsz, (gx, gy, gs)),
                                     k == len(styles) - 1))
            except Exception as e:  # noqa: BLE001
                self.events.put(("art_err", gen, str(e)))

    def _art_result(self, gen, sheet, src, face_info, final):
        if gen != self._art_gen:
            return
        self._art_sheet = sheet
        face, fsz, guess = face_info
        if src is not None:
            self._art_src_arr = src
        if self.art_auto.get():
            self._art_quiet = True
            self.art_fx.set("%.0f" % guess[0])
            self.art_fy.set("%.0f" % guess[1])
            self.art_fs.set("%.0f" % guess[2])
            self._art_quiet = False
        self._art_face = (face, fsz)
        self.show_art()
        self.show_src()
        self.art_status.configure(text=("Preview (as the game shows it). Face (%.0f, %.0f), eyes-to-chin %.0f px%s"
                                        % (face[0], face[1], fsz, " - guessed" if self.art_auto.get() else ""))
                                  + ("" if final else "  - cel style coming..."), style="Muted.TLabel")

    def show_art(self):
        if self._art_sheet is None:
            return
        cw, ch = max(50, self.art_canvas.winfo_width()), max(50, self.art_canvas.winfo_height())
        k = min(cw / self._art_sheet.width, ch / self._art_sheet.height)
        im = self._art_sheet.resize((max(1, int(self._art_sheet.width * k)), max(1, int(self._art_sheet.height * k))),
                                    Image.LANCZOS)
        self._art_img = ImageTk.PhotoImage(im)
        self.art_canvas.delete("all")
        self.art_canvas.create_image(cw // 2, 0, image=self._art_img, anchor="n")

    def show_src(self):
        src = getattr(self, "_art_src_arr", None)
        if src is None:
            return
        cw, ch = int(self.src_canvas["width"]), int(self.src_canvas["height"])
        h, w = src.shape[:2]
        k = min(cw / w, ch / h)
        tw, th = max(1, int(w * k)), max(1, int(h * k))
        bg = checker(tw, th)
        bg.alpha_composite(Image.fromarray(src, "RGBA").resize((tw, th), Image.LANCZOS))
        self._art_thumb = ImageTk.PhotoImage(bg)
        self._src_map = (k, (cw - tw) // 2, (ch - th) // 2)
        c = self.src_canvas
        c.delete("all")
        c.create_image(self._src_map[1], self._src_map[2], image=self._art_thumb, anchor="nw")
        (fx, fy), fsz = self._art_face
        x, y = self._src_map[1] + fx * k, self._src_map[2] + fy * k
        c.create_line(x - 9, y, x + 9, y, fill=ACCENT, width=2)
        c.create_line(x, y - 9, x, y + 9, fill=ACCENT, width=2)
        c.create_line(x, y, x, y + fsz * k, fill=ACCENT_HI, dash=(3, 2))
        c.create_line(x - 6, y + fsz * k, x + 6, y + fsz * k, fill=ACCENT_HI)

    def art_click(self, ev):
        if getattr(self, "_src_map", None) is None:
            return
        k, ox, oy = self._src_map
        fx, fy = (ev.x - ox) / k, (ev.y - oy) / k
        self._art_quiet = True
        self.art_auto.set(False)
        self.art_fx.set("%.0f" % fx)
        self.art_fy.set("%.0f" % fy)
        if not self.art_fs.get():
            self.art_fs.set("%.0f" % self._art_face[1])
        self._art_quiet = False
        self.art_auto_toggled()

    def do_art(self):
        p = self.art_params()
        mod = self.mod
        only = None if self.art_only.get() == "all three" else self.art_only.get()
        self.cfg["last_art_src"] = p["src"]
        s = self.slot_info()
        pv = os.path.join(mod, "art", "%s_art_preview.png" % s.code) if s else None

        def job():
            from . import art
            art.run(p["src"], mod, None, p["face"], p["face_size"], p["subject"], p["zoom"], p["style"], p["colours"],
                    p["ink"], p["crop"], p["flip"], p["tol"], p["card_zoom"], p["portrait_zoom"], p["hud_zoom"],
                    p["card_shift"], p["portrait_shift"], p["hud_shift"], only)
            return pv

        def done(pv, _out):
            self.last_result["art_preview"] = pv
            self.art_status.configure(text="Installed into %s  -  preview: %s" % (mod, pv), style="Ok.TLabel")
        self.run_job("Art", job, done)

    def open_art_preview(self):
        pv = self.last_result.get("art_preview")
        if not pv:
            s = self.slot_info()
            pv = os.path.join(self.mod, "art", "%s_art_preview.png" % s.code) if s else None
        if not pv or not os.path.isfile(pv):
            if self._art_sheet is None:
                raise KitError("no preview yet - pick a picture or press Apply to mod")
            pv = os.path.join(tempfile.gettempdir(), "blmodkit_art_preview.png")
            self._art_sheet.convert("RGB").save(pv)
        open_path(pv)

    # ------------------------------------------------------------------ 4. model
    def build_model(self):
        t = self.tab_model
        t.columnconfigure(1, weight=1)
        t.rowconfigure(0, weight=1)
        c = ttk.Frame(t, padding=(0, 0, 12, 0))
        c.grid(row=0, column=0, sticky="ns")
        c.columnconfigure(1, weight=1)
        self.model_src = tk.StringVar()
        self.model_base = tk.StringVar(value=char_choice("GOK"))
        self.model_colour = tk.StringVar(value="0")
        self.model_scale = tk.StringVar(value="auto")
        self.model_maxtex = tk.StringVar(value="1024")
        self.model_lips = tk.BooleanVar(value=True)
        self.model_palette = tk.BooleanVar(value=False)
        self.model_skin = tk.BooleanVar(value=False)
        self.model_dry = tk.BooleanVar(value=False)
        self.model_map = tk.StringVar()
        ttk.Label(c, text="Put your own rigged model on the slot", style="Head.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self.path_row(c, 1, "Model file", self.model_src, kind="file", width=34,
                      types=[("Models", "*.blend *.fbx *.glb *.gltf *.dae"), ("All", "*.*")])
        ttk.Label(c, text="Base character").grid(row=2, column=0, sticky="w", pady=3, padx=(0, 10))
        ttk.Combobox(c, textvariable=self.model_base, values=CHAR_CHOICES, state="readonly", width=24).grid(
            row=2, column=1, sticky="w")
        ttk.Label(c, text="Must be the character the slot plays as.", style="Muted.TLabel").grid(
            row=3, column=1, sticky="w")
        ttk.Label(c, text="Costume (colour)").grid(row=4, column=0, sticky="w", pady=3, padx=(0, 10))
        ttk.Spinbox(c, textvariable=self.model_colour, from_=0, to=3, width=4).grid(row=4, column=1, sticky="w")
        ttk.Label(c, text="Size").grid(row=5, column=0, sticky="w", pady=3, padx=(0, 10))
        sf = ttk.Frame(c)
        sf.grid(row=5, column=1, sticky="w")
        ttk.Combobox(sf, textvariable=self.model_scale, values=["auto", "1", "0.8", "0.9", "1.1", "1.2"],
                     width=7).pack(side="left")
        ttk.Label(sf, text="  auto, or a number (1 = the base's size)", style="Muted.TLabel").pack(side="left")
        ttk.Label(c, text="Largest texture").grid(row=6, column=0, sticky="w", pady=3, padx=(0, 10))
        ttk.Combobox(c, textvariable=self.model_maxtex, values=["512", "1024", "2048"], state="readonly",
                     width=7).grid(row=6, column=1, sticky="w")
        op = ttk.LabelFrame(c, text="Options", padding=6)
        op.grid(row=7, column=0, columnspan=3, sticky="ew", pady=8)
        ttk.Checkbutton(op, text="Lip sync: mouth moves with the voice (needs jaw / lip bones)",
                        variable=self.model_lips).pack(anchor="w")
        ttk.Checkbutton(op, text="Use the game's palette (base character's skin / cloth ramps)",
                        variable=self.model_palette).pack(anchor="w")
        ttk.Checkbutton(op, text="Skin tone of the base character (Goku's skin)", variable=self.model_skin).pack(
            anchor="w")
        ttk.Checkbutton(op, text="Dry run (build into the work folder, leave the mod as it is)",
                        variable=self.model_dry).pack(anchor="w")
        self.path_row(c, 8, "Bone map (optional)", self.model_map, kind="file", width=34,
                      types=[("bone_map.json", "*.json")])
        bf = ttk.Frame(c)
        bf.grid(row=9, column=0, columnspan=3, sticky="w", pady=12)
        self.run_button(bf, "Import model", self.do_model).pack(side="left")
        self.model_btns = []
        for txt, key in (("Open preview", "model_preview"), ("Work folder", "model_work"),
                         ("bone_map.json", "model_map")):
            b = ttk.Button(bf, text=txt, state="disabled", command=lambda k=key: open_path(self.last_result[k]))
            b.pack(side="left", padx=(6, 0))
            self.model_btns.append(b)
        self.model_result = ttk.Label(c, text="Takes 20-40 s. Blender runs in the background; your file is never "
                                              "saved.", style="Muted.TLabel", wraplength=int(420 * self.scale))
        self.model_result.grid(row=10, column=0, columnspan=3, sticky="w")
        self.model_canvas = tk.Canvas(t, bg=FIELD, highlightthickness=1, highlightbackground=BORDER)
        self.model_canvas.grid(row=0, column=1, sticky="nsew")
        self.model_canvas.bind("<Configure>", lambda e: self.show_model())
        self._model_im = None

    def do_model(self):
        src = self.model_src.get().strip()
        if not os.path.isfile(src):
            raise KitError("pick the model file (.blend, .fbx, .glb, .gltf or .dae)")
        mod = self.mod
        scale = self.model_scale.get().strip().lower() or "1"
        if scale != "auto":
            try:
                float(scale)
            except ValueError:
                raise KitError("size must be auto or a number like 0.8")
        argv = ["--blend" if src.lower().endswith(".blend") else "--fbx", src, "--base", char_code(self.model_base.get()),
                "--mod", mod, "--colour", str(int(self.model_colour.get() or 0)), "--scale", scale,
                "--max-texture", self.model_maxtex.get(), "--game", self.game,
                "--palette", "base" if self.model_palette.get() else "own",
                "--skin-tone", "goku" if self.model_skin.get() else "model"]
        if self.model_lips.get():
            argv.append("--lips")
        if self.model_dry.get():
            argv.append("--dry-run")
        if self.model_map.get().strip():
            argv += ["--bone-map", self.model_map.get().strip()]
        bl = self.blender_var.get().strip()
        if bl:
            argv += ["--blender", bl]

        def job():
            from .model import cli
            a = cli.add_arguments(argparse.ArgumentParser()).parse_args(argv)
            return cli.run(a)

        def done(rep, _out):
            pv = rep.get("preview")
            self.last_result.update(model_preview=pv, model_work=os.path.dirname(rep["bone_map"]),
                                    model_map=rep["bone_map"])
            for b in self.model_btns:
                b.configure(state="normal")
            self.model_result.configure(text="Done in %s s: slot %s colour %d, scale %.3g, rig %s.%s" % (
                rep.get("seconds"), rep["slot"], rep["colour"], rep["scale"], rep["rig"],
                " (dry run - the mod wasn't changed)" if self.model_dry.get() else ""), style="Ok.TLabel")
            self._model_im = Image.open(pv).convert("RGB") if pv and os.path.isfile(pv) else None
            self.show_model()
        self.run_job("Import model", job, done)

    def show_model(self):
        c = self.model_canvas
        c.delete("all")
        cw, ch = max(50, c.winfo_width()), max(50, c.winfo_height())
        if self._model_im is None:
            c.create_text(cw // 2, ch // 2, text="The preview appears here after the import.", fill=MUTED)
            return
        k = min(cw / self._model_im.width, ch / self._model_im.height)
        im = self._model_im.resize((max(1, int(self._model_im.width * k)), max(1, int(self._model_im.height * k))),
                                   Image.LANCZOS)
        self._model_tk = ImageTk.PhotoImage(im)
        c.create_image(cw // 2, ch // 2, image=self._model_tk)

    # ------------------------------------------------------------------ 5. effects
    def build_fx(self):
        t = self.tab_fx
        self.fx_hue = tk.DoubleVar(value=120.0)
        self.fx_sat = tk.DoubleVar(value=1.0)
        self.fx_aura = tk.DoubleVar(value=1.0)
        self.fx_earthy = tk.BooleanVar(value=True)
        self.fx_source = tk.StringVar(value="base")
        self.fx_dry = tk.BooleanVar(value=False)
        ttk.Label(t, text="Give the slot's effects (ki blasts, beams, sparks, aura) one colour",
                  style="Head.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        s = self.scale
        self.fx_strip = tk.Canvas(t, width=int(420 * s), height=int(30 * s), bg=PANEL, highlightthickness=0,
                                  cursor="hand2")
        self.fx_strip.grid(row=1, column=1, sticky="w")
        self.fx_strip.bind("<Button-1>", lambda e: self.fx_hue.set(round(e.x / (420 * s) * 359)))
        self.fx_strip.bind("<B1-Motion>", lambda e: self.fx_hue.set(round(min(max(e.x / (420 * s), 0), 1) * 359)))
        self.fx_swatch = tk.Canvas(t, width=int(110 * s), height=int(110 * s), bg=PANEL, highlightthickness=0)
        self.fx_swatch.grid(row=1, column=2, rowspan=4, padx=20, sticky="n")
        ttk.Label(t, text="Hue").grid(row=2, column=0, sticky="w", pady=3, padx=(0, 10))
        self.slider(t, self.fx_hue, 0, 359, 1, 360, self.fx_changed).grid(row=2, column=1, sticky="ew")
        pf = ttk.Frame(t)
        pf.grid(row=3, column=1, sticky="w", pady=4)
        for n, h in HUE_PRESETS:
            ttk.Button(pf, text=n, command=lambda h=h: self.fx_hue.set(h), padding=(6, 2)).pack(side="left", padx=1)
        ttk.Label(t, text="Saturation").grid(row=4, column=0, sticky="w", pady=3, padx=(0, 10))
        self.slider(t, self.fx_sat, 0.0, 1.5, 0.05, 360, self.fx_changed).grid(row=4, column=1, sticky="ew")
        ttk.Label(t, text="Aura size").grid(row=5, column=0, sticky="w", pady=3, padx=(0, 10))
        self.slider(t, self.fx_aura, 0.5, 1.5, 0.05, 360).grid(row=5, column=1, sticky="ew")
        ttk.Label(t, text="Smaller character: 0.8. Bigger: 1.2.", style="Muted.TLabel").grid(row=6, column=1,
                                                                                          sticky="w")
        ttk.Checkbutton(t, text="Keep dust / rock browns", variable=self.fx_earthy,
                        command=self.fx_changed).grid(row=7, column=1, sticky="w", pady=(8, 0))
        sf = ttk.Frame(t)
        sf.grid(row=8, column=1, sticky="w", pady=4)
        ttk.Label(sf, text="Start from").pack(side="left")
        ttk.Radiobutton(sf, text="the base character's original effects (repeatable)", value="base",
                        variable=self.fx_source).pack(side="left", padx=4)
        ttk.Radiobutton(sf, text="the mod's current files", value="mod", variable=self.fx_source).pack(side="left")
        ttk.Checkbutton(t, text="Dry run (only report)", variable=self.fx_dry).grid(row=9, column=1, sticky="w")
        ttk.Label(t, text="Effect colours before / after", style="Muted.TLabel").grid(row=10, column=1, sticky="w",
                                                                                      pady=(14, 2))
        self.fx_samples = tk.Canvas(t, width=int(420 * s), height=int(64 * s), bg=PANEL, highlightthickness=0)
        self.fx_samples.grid(row=11, column=1, sticky="w")
        self.run_button(t, "Recolour effects", self.do_fx).grid(row=12, column=1, sticky="w", pady=14)
        strip = np.zeros((1, 420, 3), np.uint8)
        from .fx import hsv2rgb
        strip[0] = hsv2rgb(np.linspace(0, 359, 420), np.ones(420), np.ones(420))
        self._fx_strip_img = ImageTk.PhotoImage(Image.fromarray(strip).resize((int(420 * s), int(22 * s))))
        self.fx_changed()

    def _hex(self, rgb):
        return "#%02x%02x%02x" % tuple(int(v) for v in rgb)

    def fx_changed(self):
        if not hasattr(self, "fx_samples"):
            return
        from .fx import HueMap, hsv2rgb
        try:
            hue, sat = float(self.fx_hue.get()), float(self.fx_sat.get())
        except (tk.TclError, ValueError):
            return
        s = self.scale
        c = self.fx_strip
        c.delete("all")
        c.create_image(0, 0, image=self._fx_strip_img, anchor="nw")
        x = hue / 359 * 420 * s
        c.create_polygon(x - 6, 30 * s, x + 6, 30 * s, x, 22 * s, fill=FG, outline="")
        col = hsv2rgb(np.array(hue), np.array(min(1.0, 0.9 * sat)), np.array(1.0))
        w = self.fx_swatch
        w.delete("all")
        w.create_oval(6 * s, 6 * s, 104 * s, 104 * s, fill=self._hex(col), outline=FG, width=2)
        w.create_text(55 * s, 55 * s, text="%d" % hue, fill="#111111", font=("Segoe UI Black", 16))
        hm = HueMap(hue, sat, self.fx_earthy.get())
        f = self.fx_samples
        f.delete("all")
        for i, rgb in enumerate(FX_SAMPLES):
            x0 = i * 70 * s
            after = hm(np.array([rgb]))[0]
            f.create_rectangle(x0, 0, x0 + 60 * s, 28 * s, fill=self._hex(rgb), outline="")
            f.create_rectangle(x0, 32 * s, x0 + 60 * s, 60 * s, fill=self._hex(after), outline="")

    def do_fx(self):
        mod, game = self.mod, self.game
        hue, sat, aura = float(self.fx_hue.get()), float(self.fx_sat.get()), float(self.fx_aura.get())
        earthy, source, dry = self.fx_earthy.get(), self.fx_source.get(), self.fx_dry.get()

        def job():
            from .fx import run
            from .game import Game
            run(Game(game), mod, hue, None, sat, source, earthy, aura, dry)
        self.run_job("Recolour effects (hue %d)" % hue, job,
                     lambda r, o: self.set_status("Effects recoloured to hue %d. Restart the game to see them." % hue))

    # ------------------------------------------------------------------ 6. check & test
    def build_check(self):
        t = self.tab_check
        t.columnconfigure(0, weight=1)
        t.rowconfigure(2, weight=3)
        t.rowconfigure(5, weight=2)
        bf = ttk.Frame(t)
        bf.grid(row=0, column=0, sticky="ew")
        self.run_button(bf, "Run check", self.do_check).pack(side="left")
        self.run_button(bf, "Simulate hits", self.do_hits, accent=False).pack(side="left", padx=6)
        self.hits_verbose = tk.BooleanVar(value=False)
        self.hits_base = tk.BooleanVar(value=True)
        ttk.Checkbutton(bf, text="every string", variable=self.hits_verbose).pack(side="left")
        ttk.Checkbutton(bf, text="compare with the base", variable=self.hits_base).pack(side="left", padx=6)
        ttk.Button(bf, text="Launch game", command=self.launch_game).pack(side="right")
        ttk.Button(bf, text="Open mods folder", command=lambda: open_path(self.mods_dir)).pack(side="right", padx=6)
        ttk.Button(bf, text="Open mod folder", command=lambda: open_path(self.mod)).pack(side="right")
        self.check_summary = ttk.Label(t, text="Check the mod after every change.", style="Muted.TLabel")
        self.check_summary.grid(row=1, column=0, sticky="w", pady=(8, 4))
        tf = ttk.Frame(t)
        tf.grid(row=2, column=0, sticky="nsew")
        self.check_tree = ttk.Treeview(tf, columns=("level", "msg"), show="headings", height=8)
        self.check_tree.heading("level", text="")
        self.check_tree.heading("msg", text="Result")
        self.check_tree.column("level", width=int(70 * self.scale), stretch=False, anchor="center")
        self.check_tree.column("msg", width=int(900 * self.scale))
        for tag, colr in (("error", ERR), ("warn", WARN), ("ok", OK)):
            self.check_tree.tag_configure(tag, foreground=colr)
        sb = ttk.Scrollbar(tf, command=self.check_tree.yview)
        self.check_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.check_tree.pack(fill="both", expand=True)
        self.check_detail = ttk.Label(t, text="", wraplength=int(1100 * self.scale), style="Muted.TLabel")
        self.check_detail.grid(row=3, column=0, sticky="w", pady=4)
        self.check_tree.bind("<<TreeviewSelect>>", lambda e: self.check_detail.configure(
            text=self.check_tree.item(self.check_tree.selection()[0], "values")[1] if self.check_tree.selection()
            else ""))
        ttk.Label(t, text="Hit simulation (rush / smash strings vs standing Goku and Krillin)",
                  style="Muted.TLabel").grid(row=4, column=0, sticky="w", pady=(6, 2))
        self.hits_text = tk.Text(t, height=8, bg=FIELD, fg="#d4d4de", relief="flat", font=("Consolas", 9),
                                 wrap="none", highlightthickness=1, highlightbackground=BORDER)
        self.hits_text.grid(row=5, column=0, sticky="nsew")

    def do_check(self):
        mod, game = self.mod, self.game

        def job():
            from . import check
            from .game import Game
            g = None
            try:
                g = Game(game)
            except KitError as e:
                print("NOTE: %s (checks that compare with the game are skipped)" % e)
            rep = Report()
            ok = check.run(mod, g, None, report=rep)
            return ok, rep

        def done(res, _out):
            ok, rep = res
            tr = self.check_tree
            tr.delete(*tr.get_children())
            for m in rep.errors:
                tr.insert("", "end", values=("ERROR", m), tags=("error",))
            for m in rep.warnings:
                tr.insert("", "end", values=("WARN", m), tags=("warn",))
            for m in rep.notes:
                tr.insert("", "end", values=("ok", m), tags=("ok",))
            self.check_summary.configure(
                text=("PASSED - %d warning(s)" % len(rep.warnings)) if ok else
                ("FAILED - %d error(s), %d warning(s)" % (len(rep.errors), len(rep.warnings))),
                style="Ok.TLabel" if ok and not rep.warnings else "Warn.TLabel" if ok else "Err.TLabel")
        self.run_job("Check", job, done)

    def do_hits(self):
        mod, game, verbose, base = self.mod, self.game, self.hits_verbose.get(), self.hits_base.get()

        def job():
            from .game import Game
            from .hits.report import run
            run(Game(game), mod, None, "GOK,KLL", verbose, None, base)

        def done(_r, out):
            self.hits_text.delete("1.0", "end")
            self.hits_text.insert("end", out or "")
        self.run_job("Hit simulation", job, done, capture=True)

    def launch_game(self):
        exe = self.exe_var.get().strip()
        if not exe or not os.path.isfile(exe):
            raise KitError("set the path to burstlimit.exe on the Setup tab first")
        subprocess.Popen([exe], cwd=os.path.dirname(exe))
        self.set_status("Started %s" % exe)

    # ------------------------------------------------------------------ jobs
    def set_status(self, text):
        self.status.configure(text=text)

    def run_job(self, title, fn, done=None, capture=False):
        if self.busy:
            messagebox.showinfo("blmodkit", "Another job is still running - wait for it to finish.", parent=self.root)
            return
        self.busy = True
        for b in self.run_buttons:
            b.state(["disabled"])
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self._job_t0 = time.time()
        self._job_title = title
        self.set_status("%s ..." % title)
        self.log.insert("end", "\n=== %s ===\n" % title, ("head",))
        self.log.see("end")
        stream = self.stream

        def work():
            if capture:
                stream.local.buf = []
            res = err = tb = None
            try:
                res = fn()
            except BaseException as e:  # noqa: BLE001 - shown in a dialog
                err, tb = e, traceback.format_exc()
            out = "".join(stream.local.buf) if capture else None
            stream.local.buf = None
            self.events.put(("done", title, res, err, tb, out, done))
        threading.Thread(target=work, daemon=True).start()
        self._tick()

    def _tick(self):
        if self.busy:
            self.set_status("%s ... %d s" % (self._job_title, time.time() - self._job_t0))
            self.root.after(500, self._tick)

    def _finish(self, title, res, err, tb, out, done):
        self.busy = False
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        for b in self.run_buttons:
            b.state(["!disabled"])
        dt = time.time() - self._job_t0
        if err is None:
            self.set_status("%s: done in %.0f s." % (title, dt))
            if done:
                try:
                    done(res, out)
                except Exception as e:  # noqa: BLE001
                    self.error(e, traceback.format_exc())
        else:
            self.set_status("%s: failed." % title)
            self.error(err, tb)

    def error(self, e, tb=None):
        if isinstance(e, KitError):
            msg = str(e)
        elif isinstance(e, SystemExit):
            msg = "The command stopped (exit code %s). See the log." % e.code
        elif isinstance(e, (FileNotFoundError, RuntimeError)):
            msg = str(e)
        else:
            msg = "Something went wrong: %s: %s\n\nThe details are in the log." % (type(e).__name__, e)
            if tb:
                self.logq.put(tb)
        self.logq.put("ERROR: %s\n" % msg)
        messagebox.showerror("blmodkit", msg[:1500], parent=self.root)

    def _tk_error(self, exc, val, tb):
        self.error(val, "".join(traceback.format_exception(exc, val, tb)))

    def _poll(self):
        try:
            chunks = []
            while True:
                chunks.append(self.logq.get_nowait())
        except queue.Empty:
            pass
        if chunks:
            for line in "".join(chunks).splitlines(True):
                s = line.strip()
                tag = ("err",) if s.startswith("ERROR") or "FAILED" in s else ("warn",) if (
                    s.startswith("WARN") or s.startswith("NOTE")) else ("ok",) if (
                    s.startswith("ok ") or "PASSED" in s) else ()
                self.log.insert("end", line, tag)
                m = re.search(r"simulated (\d+) / (\d+) strings", line)
                if m and self.busy:
                    self.progress.stop()
                    self.progress.configure(mode="determinate", maximum=int(m.group(2)), value=int(m.group(1)))
            n = int(self.log.index("end-1c").split(".")[0])
            if n > 6000:
                self.log.delete("1.0", "%d.0" % (n - 5000))
            self.log.see("end")
        try:
            while True:
                ev = self.events.get_nowait()
                if ev[0] == "done":
                    self._finish(*ev[1:])
                elif ev[0] == "art":
                    self._art_result(*ev[1:])
                elif ev[0] == "art_err" and ev[1] == self._art_gen:
                    self.art_status.configure(text="Preview: %s" % ev[2], style="Err.TLabel")
        except queue.Empty:
            pass
        self.root.after(60, self._poll)

    def close(self):
        if self.busy and not messagebox.askyesno("blmodkit", "A job is still running. Quit anyway?",
                                                 parent=self.root):
            return
        try:
            if self.cfg.get("game"):
                cur = load_config()
                if self.cfg.get("last_art_src"):
                    cur["last_art_src"] = self.cfg["last_art_src"]
                    save_config(cur)
        except Exception:  # noqa: BLE001
            pass
        sys.stdout, sys.stderr = self._old
        self.root.destroy()


def make_root():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
    return tk.Tk()


def main():
    root = make_root()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
