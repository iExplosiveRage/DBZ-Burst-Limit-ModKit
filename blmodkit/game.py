"""The user's own game files: where they are and what a character's files are.

`--game` accepts any of: the game_data_root folder (with default.xex and LONG2DATA\\),
the LONG2DATA folder, the LONG2DATA_US.CPK file itself, or the folder of burstlimit.exe
(its game_data_root subfolder, or the game_data_root named in its burstlimit.toml).
`python -m blmodkit config --game <path>` remembers it, so later commands need no --game.
"""
import os
import re

from .cpk import Cpk
from .util import KitError, load_config, ID_TO_CODE

CPK_NAME = "LONG2DATA_US.CPK"


def _from_toml(folder):
    p = os.path.join(folder, "burstlimit.toml")
    try:
        text = open(p, "r", encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    m = re.search(r'^\s*game_data_root\s*=\s*"([^"]*)"', text, re.M)
    if m and m.group(1):
        return m.group(1).replace("\\\\", "\\")
    return None


def find_cpk(path):
    """-> (cpk path, game_data_root or None)."""
    cands = []
    if path:
        path = os.path.abspath(path)
        if os.path.isfile(path) and path.upper().endswith(".CPK"):
            return path, os.path.dirname(os.path.dirname(path))
        cands = [path, os.path.join(path, "game_data_root")]
        t = _from_toml(path)
        if t:
            cands.append(t if os.path.isabs(t) else os.path.join(path, t))
    for root in cands:
        for p in (os.path.join(root, "LONG2DATA", CPK_NAME), os.path.join(root, CPK_NAME)):
            if os.path.isfile(p):
                gr = root if os.path.isfile(os.path.join(root, "default.xex")) else os.path.dirname(root)
                return p, gr
    return None, None


def resolve_game(arg):
    """The game path from --game, else from the saved settings (config --game)."""
    path = arg or os.environ.get("BLMODKIT_GAME") or load_config().get("game")
    if not path:
        raise KitError("where is your game? Pass --game <game_data_root folder> (the folder with "
                       "default.xex and LONG2DATA), or save it once: python -m blmodkit config --game <folder>")
    cpk, root = find_cpk(path)
    if not cpk:
        raise KitError("no %s found in or under %s - point --game at your game_data_root folder" % (CPK_NAME, path))
    return cpk, root


class Game:
    """The user's game: the archive (read only) and default.xex."""

    def __init__(self, arg=None):
        self.cpk_path, self.root = resolve_game(arg)
        self.cpk = Cpk(self.cpk_path)
        self._image = None

    @property
    def xex_path(self):
        p = os.path.join(self.root or "", "default.xex")
        if not os.path.isfile(p):
            raise KitError("default.xex not found next to LONG2DATA (%s) - the hit simulator needs it" % p)
        return p

    def image(self):
        if self._image is None:
            from .xex import Image
            self._image = Image(self.xex_path)
        return self._image

    def read(self, path, head=None):
        return self.cpk.read(path, head)

    def has(self, path):
        return path in self.cpk

    def stamp(self):
        """Identifies this archive (for caches)."""
        st = os.stat(self.cpk_path)
        return "%d_%d" % (st.st_size, int(st.st_mtime))

    # ------------------------------------------------------------------ a character's files
    def character_files(self, code):
        """Every archive path that belongs to fighter `code` in battle, by slot.toml key.
        -> dict key -> path (or list of paths for 'model' / 'skeleton' / 'vox_csb')."""
        c = code.upper()
        ch = "PAC/CMN/CH/BC%s/" % c
        files = {}
        single = {
            "m0": ch + "BC%sM0.AMB" % c, "m1": ch + "BC%sM1.AMB" % c, "l000": ch + "BC%sL000.AMB" % c,
            "se_csb": "SOUND/CSB/CMN/BATTLE_%s_SE.CSB" % c, "vox_amk": ch + "BATTLE_%s_VOX.AMK" % c,
            "bsp": ch + "BSP_%s.AMB" % c, "esp": ch + "ESP%s.NUT" % c, "eas": ch + "EAS%s.NUT" % c,
            "fx_model": ch + "%s_MODEL.NUX" % c, "arst": ch + "ARST_%s.NUX" % c, "bar": ch + "BAR_%s.AMB" % c,
            "bufac": "PAC/CMN/FIX/BUFAC/BUFAC_%s.NUT" % c,
        }
        for k, p in single.items():
            if self.has(p):
                files[k] = p
        files["vox_csb"] = [p for p in ("SOUND/CSB/US/BATTLE_%s_VOX.CSB" % c, "SOUND/CSB/JP/BATTLE_%s_VOX.CSB" % c)
                            if self.has(p)]
        # lip poses of the other forms (L001 ...) are listed too (slot.toml takes only L000)
        files["lips_more"] = sorted(p for p in self.cpk.paths() if p.startswith(ch + "BC%sL0" % c) and
                                    p != single["l000"])
        rx = re.compile(re.escape(ch) + r"BC%s0(\d)(\d)\.(NUX|AMB)$" % re.escape(c))
        models, skels = {}, {}
        for p in self.cpk.paths():
            m = rx.match(p)
            if m:
                (models if m.group(3) == "NUX" else skels)[(int(m.group(1)), int(m.group(2)))] = p
        files["model"] = models
        files["skeleton"] = skels
        return files

    def all_character_paths(self, code):
        """Everything of a character for `extract` (battle files, HUD face, select/after-select art)."""
        c = code.upper()
        keys = ("PAC/CMN/CH/BC%s/" % c, "PAC/CMN/FIX/BUFAC/BUFAC_%s." % c, "PAC/CMN/FIX/CHRA/UICMN_%s." % c,
                "PAC/CMN/FIX/CHRA/_UICMN_%s." % c, "SOUND/CSB/CMN/BATTLE_%s_" % c, "SOUND/CSB/US/BATTLE_%s_" % c,
                "SOUND/CSB/JP/BATTLE_%s_" % c)
        return [p for p in self.cpk.paths() if p.startswith(keys)]


def base_code(base_id):
    if base_id not in ID_TO_CODE:
        raise KitError("slot.toml base %r is not a character id 0-20" % base_id)
    return ID_TO_CODE[base_id]
