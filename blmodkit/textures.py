"""Texture handles (GIDX) and NUX texture-set names: allocation and clash checks.

Why it matters
  * The game registers each NTXR texture by its handle; a texture whose handle is already
    loaded is not made again. A slot file that keeps the base character's handles but has
    other pixels (a recolour, a new model) can show the other fighter's texture - or vice versa.
  * A .NUX names its texture set inside (+0x0C, e.g. "BCVGT000_B.NUT"); the game keeps ONE
    set per name, and a second file with the same name gets the first one's textures (if its
    materials then ask for handles that set doesn't have, the game crashes).
    The game build renames a slot's sets at load time too (BurstLimitSlotsNuxTextures), but
    the kit makes the files clean on their own.

Handle ranges used by the game (whole archive): 0x00004E20-0x00008BE2 (UI, effect NUTs),
0x00021151-0x00029759 (effect models), 0x06100010-0x0610xxxx (drama), 0x10000101-0x10012015
(stages, characters), 0x1FFF0000-0x1FFF0007. The kit gives every slot code its own block:
    0x30000000 + base36(code) * 0x800  (0x800 handles per code, codes AAA-ZZZ / 000-999)
so two slots made with the kit never collide, and nothing of the game sits in 0x30000000-0x35B1FFFF.
"""
import json
import os
import struct

from .ntxr import scan_gidx
from .util import KitError, cache_dir, info

HANDLE_RANGE_BASE = 0x30000000
HANDLES_PER_CODE = 0x800


def _base36(code):
    v = 0
    for ch in code.upper():
        v = v * 36 + (int(ch) if ch.isdigit() else ord(ch) - ord("A") + 10)
    return v


def handle_block(code):
    """(first, end) of the handle block of a slot code."""
    first = HANDLE_RANGE_BASE + _base36(code) * HANDLES_PER_CODE
    return first, first + HANDLES_PER_CODE


class HandleMap:
    """Old handle -> new handle for one slot (the same old handle always gets the same new one,
    so textures shared between a character's files stay shared)."""

    def __init__(self, code, old_handles):
        first, end = handle_block(code)
        olds = sorted(set(old_handles))
        if len(olds) > end - first:
            raise KitError("too many textures (%d) for one slot's handle block" % len(olds))
        self.map = {o: first + i for i, o in enumerate(olds)}
        self.next_free = first + len(olds)
        self.end = end

    def __getitem__(self, old):
        if old not in self.map:
            if self.next_free >= self.end:
                raise KitError("the slot's handle block is full")
            self.map[old] = self.next_free
            self.next_free += 1
        return self.map[old]


def nux_set_names(data):
    """(texture-set name, NUB name) written inside a .NUX header."""
    def cstr(b):
        return b.split(b"\0", 1)[0].decode("latin1")
    return cstr(data[0x0C:0x26]), cstr(data[0x26:0x40])


# ----------------------------------------------------------------------------- game index

def game_index(game, force=False):
    """Every texture handle and NUX set name in the user's archive (NUX / NUT files).
    -> dict(handles={hex: [[file, pixel-hash], ...]}, sets={upper name: [file, ...]}).
    Built once (about a minute) and cached per archive."""
    p = os.path.join(cache_dir("index"), "textures_%s.json" % game.stamp())
    if os.path.exists(p) and not force:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    files = [x for x in game.cpk.paths() if x.endswith((".NUX", ".NUT")) and "/JP/" not in x]
    info("Indexing the textures of your game (%d files, once - cached afterwards)..." % len(files))
    handles, sets = {}, {}
    for k, path in enumerate(files):
        data = game.read(path)
        if path.endswith(".NUX") and data[:4] == b"#NUX":
            name, _ = nux_set_names(data)
            sets.setdefault(name.upper(), []).append(path)
        for gid, _off, dig in scan_gidx(data):
            handles.setdefault("%08X" % gid, []).append([path, dig])
        if (k + 1) % 50 == 0:
            info("  %d / %d" % (k + 1, len(files)))
    idx = dict(handles=handles, sets=sets)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(idx, fh)
    return idx


def file_textures(path):
    """[(handle, pixel-hash)] of a mod file (.NUX / .NUT)."""
    with open(path, "rb") as fh:
        data = fh.read()
    return [(g, d) for g, _o, d in scan_gidx(data)]


def renumber_nut(data, hmap):
    """A .NUT with every texture handle mapped through hmap (same size)."""
    from .ntxr import Nut
    nut = Nut(data)
    for e in nut.entries:
        e.gidx = hmap[e.gidx]
    out = nut.to_bytes()
    assert len(out) == len(data)
    return out


def renumber_nux(data, hmap, set_name=None, nub_name=None):
    """A .NUX with its texture handles (textures + every material's references) mapped through
    hmap and new internal names. Same size; parsed with the full model reader (round-trip
    checked first)."""
    from . import nux
    f = nux.parse(data)
    if nux.serialize(f) != bytes(data):
        raise KitError("a NUX file didn't read back byte for byte - not changed")
    for t in f.textures:
        t.gidx = hmap[t.gidx]
    for m in nux.all_materials(f):
        for tt in m.textures:
            tt[0] = hmap[tt[0]]
    if set_name:
        f.nut_name, f.nut_name_pad = set_name, None
    if nub_name:
        f.nub_name, f.nub_name_pad = nub_name, None
    out = nux.serialize(f)
    if len(out) != len(data):
        raise KitError("internal: NUX size changed while renumbering")
    return out


def nux_handles(data):
    """Texture handles of a .NUX (textures only, header walk)."""
    return [g for g, _o, _d in scan_gidx(data)]


def nut_handles(data):
    from .ntxr import Nut
    return [e.gidx for e in Nut(data).entries]


def material_handles(data):
    """Handles the NUX's materials ask for (they must all be textures of the same file)."""
    from . import nux
    f = nux.parse(data)
    return sorted({tt[0] for m in nux.all_materials(f) for tt in m.textures})


def nub_count(data):
    """Number of models in a .NUX (#NUB count)."""
    nub = struct.unpack_from(">I", data, 8)[0]
    if data[nub:nub + 4] != b"#NUB":
        return None
    return struct.unpack_from(">I", data, nub + 8)[0]
