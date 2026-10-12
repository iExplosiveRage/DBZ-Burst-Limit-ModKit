"""Shared helpers: the roster table, settings, cache folder, messages."""
import json
import os
import re
import sys

# The game's 21 characters: id (match request / slot.toml `base`), file code, name.
# (The id order is the game's own; it is not the order of the select screen.)
CHARACTERS = [
    (0, "GOK", "Goku"), (1, "GHS", "Kid Gohan"), (2, "GHM", "Teen Gohan"), (3, "VGT", "Vegeta"),
    (4, "KLL", "Krillin"), (5, "TRX", "Trunks"), (6, "PIC", "Piccolo"), (7, "TSH", "Tien"),
    (8, "YMC", "Yamcha"), (9, "RAD", "Raditz"), (10, "NAP", "Nappa"), (11, "GNY", "Captain Ginyu"),
    (12, "RCM", "Recoome"), (13, "FRZ", "Frieza"), (14, "16G", "Android #16"),
    (15, "17G", "Android #17"), (16, "18G", "Android #18"), (17, "CEL", "Cell"),
    (18, "SBM", "Saibamen"), (19, "BRL", "Broly"), (20, "BDK", "Bardock"),
]
CODE_TO_ID = {c: i for i, c, _ in CHARACTERS}
ID_TO_CODE = {i: c for i, c, _ in CHARACTERS}
ID_TO_NAME = {i: n for i, _, n in CHARACTERS}

# Codes the game itself uses in file names (never valid for a new slot).
RESERVED_CODES = set(CODE_TO_ID) | {"CMN", "RANDOM"}

# The longest archive path the game copies (it keeps 57 bytes of a path, with the end zero).
MAX_PATH = 56


class KitError(Exception):
    """An error with a message meant for the user (printed without a traceback)."""


def parse_base(text):
    """'VGT', 'vegeta' or '3' -> (id, code)."""
    t = str(text).strip()
    if t.isdigit() and int(t) in ID_TO_CODE:
        return int(t), ID_TO_CODE[int(t)]
    up = t.upper()
    if up in CODE_TO_ID:
        return CODE_TO_ID[up], up
    for i, c, n in CHARACTERS:
        if n.lower().replace(" ", "") == t.lower().replace(" ", ""):
            return i, c
    raise KitError("unknown base character %r - use a code (GOK, VGT, KLL, ...) or an id 0-20.\n"
                   "Characters: %s" % (text, ", ".join("%s=%s" % (c, n) for _, c, n in CHARACTERS)))


CODE_RE = re.compile(r"^[A-Z0-9]{3}$")


def check_code(code):
    """A new slot's 3-character file code: upper-case letters / digits, not one of the game's."""
    c = str(code).strip().upper()
    if not CODE_RE.match(c):
        raise KitError("the slot code must be exactly 3 letters or digits (A-Z, 0-9), e.g. TST - got %r" % code)
    if c in RESERVED_CODES:
        raise KitError("the code %s is one of the game's own characters - pick a code of your own" % c)
    return c


# ----------------------------------------------------------------------------- folders / settings

def _appdata(var, fallback):
    base = os.environ.get(var) or os.path.join(os.path.expanduser("~"), fallback)
    return os.path.join(base, "blmodkit")


def config_path():
    return os.path.join(_appdata("APPDATA", ".config"), "config.json")


def load_config():
    try:
        with open(config_path(), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    p = config_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=1)


def cache_dir(*parts):
    """%LOCALAPPDATA%\\blmodkit\\cache\\... - data read from the user's game (never shipped)."""
    p = os.path.join(_appdata("LOCALAPPDATA", ".cache"), "cache", *parts)
    os.makedirs(p, exist_ok=True)
    return p


# ----------------------------------------------------------------------------- messages

def info(msg):
    print(msg)
    sys.stdout.flush()


def warn(msg):
    print("WARNING: " + msg)
    sys.stdout.flush()


class Report:
    """Collects check results: errors (the mod won't work as meant), warnings, notes."""

    def __init__(self):
        self.errors, self.warnings, self.notes = [], [], []

    def error(self, msg):
        self.errors.append(msg)

    def warning(self, msg):
        self.warnings.append(msg)

    def note(self, msg):
        self.notes.append(msg)

    def print(self, title):
        print("=" * 72)
        print(title)
        print("=" * 72)
        for n in self.notes:
            print("  ok    " + n)
        for w in self.warnings:
            print("  WARN  " + w)
        for e in self.errors:
            print("  ERROR " + e)
        print("-" * 72)
        if self.errors:
            print("RESULT: FAILED - %d error(s), %d warning(s)" % (len(self.errors), len(self.warnings)))
        else:
            print("RESULT: PASSED - %d warning(s)" % len(self.warnings))
        return not self.errors


def human_size(n):
    return "%.2f MB" % (n / 1e6) if n >= 1e6 else "%.1f KB" % (n / 1e3)
