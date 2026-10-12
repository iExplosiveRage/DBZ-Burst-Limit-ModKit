"""slot.toml: what the game reads (burstlimit_slots.cpp) and how the kit writes it.

A slot mod folder (mods\\<Folder>\\) holds:
    slot.toml   one or more [[slot]] tables (below)
    mod.toml    name / author / version / description shown in the game's Mods menu
    art\\       the select-screen card and big picture (PNG)
    PAC\\...    the slot's own game files under NEW names (PAC/CMN/CH/BC<code>/...)
    SOUND\\...  its sound banks (SOUND/CSB/CMN|US|JP/...)

The mods overlay adds to the archive exactly the paths a slot.toml names (anything else
that matches no game file is ignored), so every file of a slot must be listed here.
"""
import os

from . import tomlmini
from .util import KitError, MAX_PATH

# [[slot]] keys the game reads.
SLOT_KEYS = ("code", "base", "name", "colours", "colors", "max_form", "card", "portrait", "tint",
             "ultimates", "dramas", "files")
# [slot.files] keys: one archive path each (any left out = the base character's file).
FILE_KEYS = ("m0", "m1", "l000", "se_csb", "vox_amk", "bsp", "esp", "eas", "fx_model", "arst", "bar", "bufac")
# ... plus vox_csb (a file NAME: the game looks in SOUND/CSB/US/ and SOUND/CSB/JP/) and
# model / skeleton (one pattern per colour, or one for all; {c} colour digit, {m} model index).
LIST_KEYS = ("model", "skeleton")
ALL_FILE_KEYS = FILE_KEYS + ("vox_csb",) + LIST_KEYS

FILE_WHAT = {
    "m0": "moveset (moves, hitboxes, CPU script)", "m1": "hand poses", "l000": "lip poses",
    "se_csb": "sound effects bank", "vox_csb": "voice bank", "vox_amk": "lip-sync keys for the voice",
    "bsp": "effect scripts", "esp": "effect textures", "eas": "effect textures 2",
    "fx_model": "effect models", "arst": "ARST effect model", "bar": "aura / ki effects",
    "bufac": "battle HUD face", "model": "costume model (NUX)", "skeleton": "costume skeleton (AMB)",
}

MAX_SLOTS_ON_PAGE = 9


def expand(pattern, c, m):
    for k, v in (("{c}", c), ("{m}", m), ("{C}", c), ("{M}", m)):
        pattern = pattern.replace(k, str(v))
    return pattern


def norm(path):
    return path.replace("\\", "/").strip().upper()


class Slot:
    """One [[slot]] as the game will see it."""

    def __init__(self, table, folder):
        self.t = table
        self.folder = folder
        self.code = str(table.get("code", "")).upper()
        self.base = table.get("base", 0)
        self.name = table.get("name") or self.code
        self.colours = table.get("colours", table.get("colors", 1))
        self.max_form = table.get("max_form", 0)
        self.files = table.get("files") or {}

    @property
    def models(self):
        """Model indices the game uses: 1 without forms, 0-7 (probed) with them."""
        return 1 if not self.max_form else 8

    def file(self, key):
        v = self.files.get(key)
        return v if isinstance(v, str) else None

    def patterns(self, key):
        v = self.files.get(key)
        if isinstance(v, str):
            return [v]
        if isinstance(v, list):
            return [x for x in v if isinstance(x, str)]
        return []

    def costume_paths(self, key, probe=None):
        """{(colour, model): archive path} the game will look for ('model' / 'skeleton').
        probe(path) -> bool tells whether a path exists (model indices > 0 are only probed)."""
        pats = self.patterns(key)
        out = {}
        if not pats:
            return out
        for c in range(max(1, int(self.colours))):
            pat = pats[c] if c < len(pats) else pats[0]
            for m in range(self.models):
                p = expand(pat, c, m)
                if m > 0 and (p == expand(pat, c, 0) or (probe and not probe(p))):
                    continue
                out[(c, m)] = p
        return out

    def voice_paths(self):
        v = self.file("vox_csb")
        if not v:
            return []
        name = v.replace("\\", "/").split("/")[-1]
        return ["SOUND/CSB/US/" + name, "SOUND/CSB/JP/" + name]

    def declared(self):
        """Every archive path this slot names (upper case) - the files the mod must contain."""
        out = []
        for k in FILE_KEYS:
            if self.file(k):
                out.append(norm(self.file(k)))
        for k in LIST_KEYS:
            for pat in self.patterns(k):
                for c in range(max(1, int(self.colours))):
                    for m in range(self.models):
                        out.append(norm(expand(pat, c, m)))
        out += [norm(p) for p in self.voice_paths()]
        return out


def read(folder):
    """-> (raw toml dict, [Slot]) of a mod folder; KitError when it has no readable slot.toml."""
    p = os.path.join(folder, "slot.toml")
    if not os.path.isfile(p):
        raise KitError("%s has no slot.toml" % folder)
    try:
        data = tomlmini.load(p)
    except tomlmini.TomlError as e:
        raise KitError("%s: %s" % (p, e))
    slots = [Slot(t, folder) for t in data.get("slot", []) if isinstance(t, dict)]
    return data, slots


def mod_file(folder, archive_path):
    """The file in the mod folder for an archive path (case-insensitive, like Windows), or None."""
    p = os.path.join(folder, *archive_path.replace("\\", "/").split("/"))
    if os.path.isfile(p):
        return p
    # look up each component case-insensitively
    cur = folder
    for part in archive_path.replace("\\", "/").split("/"):
        try:
            names = {n.upper(): n for n in os.listdir(cur)}
        except OSError:
            return None
        if part.upper() not in names:
            return None
        cur = os.path.join(cur, names[part.upper()])
    return cur if os.path.isfile(cur) else None


# ----------------------------------------------------------------------------- writing

SLOT_TEMPLATE = '''# Extra character slot - made with blmodkit {kit_version}
# (python -m blmodkit new-slot --base {base_code} --code {code} ...)
#
# The game reads this file at start from every mod that is switched on. On the Versus /
# Training character select, R2 switches a side to the page with the extra characters
# (R2 again or L2: back to the roster); Y picks a colour. Offline only.
#
# {name} plays as {base_name} (base = {base}) for all game logic - the move list layout,
# the CPU, the save's unlocks - but loads the files below instead of {base_name}'s.
# Any [slot.files] line you remove = {base_name}'s own file is used.
# Archive paths: at most 56 characters, printable ASCII, under PAC/ or SOUND/.
# Check the mod after every change: python -m blmodkit check "<this folder>"

[[slot]]
code = "{code}"          # 3-letter file code (BC{code}... file names)
base = {base}               # the character it plays as: {base} = {base_name}
name = {name_q}  # shown on the character select and the battle HUD (ASCII)
colours = {colours}            # costumes (Y on the select): one model + skeleton per colour
max_form = {max_form}           # highest form; 0 = can't transform (RB does nothing)
# Character select pictures (PNG): card 512x120, big picture 368x720.
# Make them from one image with: python -m blmodkit art --src picture.png --mod "<this folder>"
card = "art/{code}_CARD.png"
portrait = "art/{code}_PORTRAIT.png"
# Colour behind the big picture (ARGB hex); left out = {base_name}'s.
# tint = 0xFF0A8CDC
# The Ultimate window (up to 3): [name, {base_name}'s ultimate slot 0-2]. The name is ASCII
# text ("My Ultimate") or a PROPERNOUN text id (the numbers below are {base_name}'s own names).
# Entries listed here are always selectable; left out = {base_name}'s list, locked like in the save.
{ultimates_line}
# Versus rival scenes (written for {base_name}): "off" or "on".
dramas = "off"

[slot.files]
{files}
'''


def slot_toml_text(code, base, base_code, base_name, name, colours, max_form, files, kit_version, ultimates=None):
    """files: ordered list of (key, value) - value str or list of str."""
    rows = []
    for k, v in files:
        if isinstance(v, list):
            val = "[" + ", ".join(tomlmini.quote(x) for x in v) + "]"
        else:
            val = tomlmini.quote(v)
        rows.append("%-8s = %-44s # %s" % (k, val, FILE_WHAT.get(k, "")))
    if '"' in name or not all(32 <= ord(ch) < 127 for ch in name):
        raise KitError("the name must be plain ASCII text without quotes: %r" % name)
    return SLOT_TEMPLATE.format(kit_version=kit_version, base_code=base_code, code=code, name=name,
                                name_q=tomlmini.quote(name), base=base, base_name=base_name, colours=colours,
                                max_form=max_form, files="\n".join(rows),
                                ultimates_line=("ultimates = [" + ", ".join("[%d, %d]" % tuple(u) for u in ultimates) + "]")
                                if ultimates else '# ultimates = [["My Ultimate", 0]]')


def mod_toml_text(name, author, version, description):
    return ("name = %s\nauthor = %s\nversion = %s\ndescription = %s\n" %
            tuple(tomlmini.quote(x) for x in (name, author, version, description)))


def check_archive_path(p):
    """'' when fine, else what's wrong with an archive path a slot names."""
    if len(p) > MAX_PATH:
        return "%s is %d characters - the game reads at most %d" % (p, len(p), MAX_PATH)
    if not all(32 < ord(ch) < 127 for ch in p):
        return "%s has spaces or non-ASCII characters" % p
    up = norm(p)
    if not (up.startswith("PAC/") or up.startswith("SOUND/")):
        return "%s must start with PAC/ or SOUND/ (the archive's top folders)" % p
    if "//" in up or up.endswith("/"):
        return "%s is not a file path" % p
    return ""
