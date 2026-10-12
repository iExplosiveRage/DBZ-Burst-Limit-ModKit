"""recolor-fx: give a slot's effects (ki blasts, beams, aura, sparks...) one colour.

    python -m blmodkit recolor-fx --mod mods\\My_Slot --hue 326          # pink
    python -m blmodkit recolor-fx --mod mods\\My_Slot --hue 120 --saturation 0.8

What changes (the slot's effect files, same sizes and layout):
  bsp / bar   (.AMB)  the colour words of every particle node (#AME sprites / lines / planes /
                      models): RGB -> the target hue, saturation x --saturation, brightness kept,
                      alpha kept; whites / greys / blacks stay (neutral colours are not touched)
  esp / eas   (.NUT)  every texture: both endpoint colours of each DXT block mapped the same way
                      (indices kept, so shapes and soft edges stay exactly as they were)
  fx_model / arst (.NUX) the textures as above + the models' vertex colours
By default the base character's ORIGINAL effects (from your game) are recoloured and written over
the slot's files - so running it again with another hue starts from the originals. The slot's
own texture handles and texture-set names are kept. --source mod recolours the mod's current
files in place instead (for effects you already edited).
--keep-earthy (default) leaves dust / rock browns alone; --aura-scale scales the aura effects
(BAR) for a smaller or bigger character.
"""
import os
import struct

import numpy as np

from . import slotfile
from .ntxr import Nut, level_sizes
from .util import KitError, ID_TO_CODE, info, warn

FX_KEYS = ("bsp", "bar", "esp", "eas", "fx_model", "arst")


# ----------------------------------------------------------------------------- colour map

def rgb2hsv(rgb):
    rgb = np.asarray(rgb, np.float64) / 255.0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx, mn = rgb.max(-1), rgb.min(-1)
    d = mx - mn
    s = np.where(mx > 0, d / np.where(mx > 0, mx, 1), 0)
    dd = np.where(d > 0, d, 1)
    h = np.where(mx == r, ((g - b) / dd) % 6, np.where(mx == g, (b - r) / dd + 2, (r - g) / dd + 4)) * 60
    return np.where(d > 0, h, 0), s, mx


def hsv2rgb(h, s, v):
    h = (np.asarray(h) % 360) / 60.0
    i = np.floor(h).astype(int) % 6
    f = h - np.floor(h)
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.clip(np.stack([r, g, b], -1) * 255 + 0.5, 0, 255).astype(np.int32)


class HueMap:
    """RGB -> the target hue; saturation scaled; brightness kept; neutrals untouched."""

    def __init__(self, hue, saturation=1.0, keep_earthy=True):
        self.hue, self.sat, self.earthy = float(hue) % 360, float(saturation), keep_earthy

    def __call__(self, rgb):
        rgb = np.asarray(rgb)
        h, s, v = rgb2hsv(rgb)
        out = hsv2rgb(np.full_like(h, self.hue), np.clip(s * self.sat, 0, 1), v)
        keep = s < 0.04
        if self.earthy:
            keep |= (h <= 60) & (s < 0.5) & (v < 0.85)      # dust / rock browns
        return np.where(keep[..., None], rgb.astype(np.int32), out)


# ----------------------------------------------------------------------------- AMB effect files

def _u32(d, o):
    return struct.unpack_from(">I", d, o)[0]


def _u16(d, o):
    return struct.unpack_from(">H", d, o)[0]


def amb_entries(d, base=0):
    n, toff = struct.unpack_from(">II", d, base + 0x10)
    out = []
    for i in range(n):
        o, s, t, fl = struct.unpack_from(">IIiI", d, base + toff + 16 * i)
        out.append((i, (base + o) if o else None, s))
    return out


def all_ames(d):
    """Every #AME (particle effect tree) chunk in an effect .AMB: [(label, offset, size)]."""
    out = []
    for i, o, s in amb_entries(d):
        if o is None:
            continue
        if d[o:o + 4] == b"#AMB":
            for j, o2, s2 in amb_entries(d, o):
                if o2 and d[o2:o2 + 4] == b"#AME":
                    out.append(("%d.%d" % (i, j), o2, s2))
        elif d[o:o + 4] == b"#AME":
            out.append(("%d" % i, o, s))
    return out


def ame_nodes(d, a, size):
    """The nodes of one #AME (depth first) with their byte sizes."""
    first = _u32(d, a + 12)
    nodes = []

    def walk(off):
        while off:
            n = a + off
            nodes.append(dict(off=off, abs=n, type=_u16(d, n + 2), child=_u32(d, n + 20), next=_u32(d, n + 24)))
            if nodes[-1]["child"]:
                walk(nodes[-1]["child"])
            off = nodes[-1]["next"]
    walk(first)
    offs = sorted(x["off"] for x in nodes) + [size]
    sz = {offs[k]: offs[k + 1] - offs[k] for k in range(len(offs) - 1)}
    for x in nodes:
        x["size"] = sz[x["off"]]
    return nodes


# particle node type -> offsets of its RGBA colour words (sprites, lines, planes, models)
COLOUR_WORDS = {0x200: (0x74, 0x78), 0x201: (0x74, 0x78), 0x202: (0x68, 0x6C, 0x70, 0x74),
                0x203: (0x78, 0x7C), 0x204: (0x8C, 0x90)}
# node type -> float fields that are lengths (emitter offsets / spawn / velocity, sizes, widths...)
_EMIT = (32, 36, 40, 76, 80, 84, 88)
AME_LENGTHS = {0x100: _EMIT, 0x101: _EMIT, 0x102: _EMIT, 0x103: _EMIT + (108,),
               0x200: (32, 36, 40, 80, 84), 0x201: (32, 36, 40, 80, 84),
               0x202: (32, 36, 40, 80, 84, 88, 92, 96, 100), 0x203: (32, 36, 40, 88, 92, 96, 108, 116),
               0x204: (32, 36, 40, 80, 84, 88, 96, 100, 104), 0x300: (48,), 0x305: (48,)}


def recolour_amb(data, hmap):
    d = bytearray(data)
    n = 0
    for _lab, a, size in all_ames(bytes(d)):
        for nd in ame_nodes(d, a, size):
            for o in COLOUR_WORDS.get(nd["type"], ()):
                if o + 4 > nd["size"]:
                    continue
                at = nd["abs"] + o
                w = _u32(d, at)
                rgb = np.array([(w >> 24) & 255, (w >> 16) & 255, (w >> 8) & 255])
                c = hmap(rgb)
                nw = (int(c[0]) << 24) | (int(c[1]) << 16) | (int(c[2]) << 8) | (w & 255)
                if nw != w:
                    struct.pack_into(">I", d, at, nw)
                    n += 1
    return bytes(d), n


def scale_ames(data, k):
    """Multiply the length fields of every #AME node by k (the aura of a smaller / bigger fighter)."""
    d = bytearray(data)
    n = 0
    for _lab, a, size in all_ames(bytes(d)):
        for nd in ame_nodes(d, a, size):
            for o in AME_LENGTHS.get(nd["type"], ()):
                if o + 4 > nd["size"]:
                    continue
                at = nd["abs"] + o
                v = struct.unpack_from(">f", d, at)[0]
                if v != 0.0 and abs(v) < 1e6:
                    struct.pack_into(">f", d, at, v * k)
                    n += 1
    return bytes(d), n


# ----------------------------------------------------------------------------- textures

def _565(c):
    return np.stack([((c >> 11) & 31) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31], -1)


def _to565(rgb):
    r = (rgb[..., 0] * 31 + 127) // 255
    g = (rgb[..., 1] * 63 + 127) // 255
    b = (rgb[..., 2] * 31 + 127) // 255
    return ((r << 11) | (g << 5) | b).astype(np.uint32)


def recolour_dxt(fmt, data, hmap):
    """Both endpoint colours of every DXT colour block through hmap; indices (and the DXT1
    3-/4-colour mode) kept. data: one mip level as big-endian words."""
    bs = 8 if fmt == 0 else 16
    nb = len(data) // bs
    if not nb:
        return data
    raw = np.frombuffer(bytes(data[:nb * bs]), dtype=">u2").astype("<u2").tobytes()
    blk = np.frombuffer(raw, np.uint8).reshape(nb, bs).copy()
    col = blk[:, bs - 8:]
    c0 = col[:, 0].astype(np.uint32) | (col[:, 1].astype(np.uint32) << 8)
    c1 = col[:, 2].astype(np.uint32) | (col[:, 3].astype(np.uint32) << 8)
    bits = (col[:, 4].astype(np.uint32) | (col[:, 5].astype(np.uint32) << 8) |
            (col[:, 6].astype(np.uint32) << 16) | (col[:, 7].astype(np.uint32) << 24))
    four = c0 > c1
    n0, n1 = _to565(hmap(_565(c0))), _to565(hmap(_565(c1)))
    flip = np.where(four, n0 < n1, n0 > n1)
    a, b = np.where(flip, n1, n0), np.where(flip, n0, n1)
    idx = (bits[:, None] >> (2 * np.arange(16, dtype=np.uint32))) & 3
    sw4 = np.array([1, 0, 3, 2], np.uint32)
    sw3 = np.array([1, 0, 2, 3], np.uint32)
    idx = np.where(flip[:, None], np.where(four[:, None], sw4[idx], sw3[idx]), idx)
    eq = four & (a == b)
    up = eq & (a < 0xFFFF)
    a = np.where(up, a + 1, a)
    b = np.where(eq & ~up, b - 1, b)
    bits = (idx << (2 * np.arange(16, dtype=np.uint32))).sum(1).astype(np.uint32)
    col[:, 0], col[:, 1], col[:, 2], col[:, 3] = a & 255, a >> 8, b & 255, b >> 8
    for k in range(4):
        col[:, 4 + k] = (bits >> (8 * k)) & 255
    blk[:, bs - 8:] = col
    return np.frombuffer(blk.tobytes(), dtype="<u2").astype(">u2").tobytes() + bytes(data[nb * bs:])


def recolour_texture_bytes(fmt, w, h, mips, data, hmap):
    if fmt not in (0, 1, 2):
        return data
    out, off = b"", 0
    for k, sz in enumerate(level_sizes(fmt, w, h, mips)):
        out += recolour_dxt(fmt, data[off:off + sz], hmap)
        off += sz
    return out + bytes(data[off:])


def recolour_nut(data, hmap, handles=None):
    nut = Nut(data)
    for i, e in enumerate(nut.entries):
        new = recolour_texture_bytes(e.fmt, e.width, e.height, e.mips, e.data, hmap)
        e.raw[e.header_size:] = new
        if handles is not None and i < len(handles):
            e.gidx = handles[i]
    out = nut.to_bytes()
    assert len(out) == len(data)
    return out


def recolour_nux(data, hmap, handles=None, names=None):
    from . import nux
    f = nux.parse(data)
    remap = {}
    for i, t in enumerate(f.textures):
        t.data = recolour_texture_bytes(t.fmt, t.width, t.height, t.mips, t.data, hmap)
        if handles is not None and i < len(handles):
            remap[t.gidx] = handles[i]
            t.gidx = handles[i]
    nvc = 0
    for m in nux.all_materials(f):
        for tt in m.textures:
            tt[0] = remap.get(tt[0], tt[0])
    for _i, n in f.models:
        for p in n.polygons:
            if p.vs is not None and p.vs.dtype.names and "color" in p.vs.dtype.names:
                c = np.array(p.vs["color"])            # A R G B
                new = c.copy()
                new[:, 1:4] = hmap(c[:, 1:4].astype(np.int32)).astype(np.uint8)
                nvc += int((new != c).any(1).sum())
                p.vs["color"] = new
    if names:
        f.nut_name, f.nut_name_pad = names[0], None
        f.nub_name, f.nub_name_pad = names[1], None
    out = nux.serialize(f)
    assert len(out) == len(data)
    return out, nvc


# ----------------------------------------------------------------------------- command

def run(game, mod, hue, slot_code=None, saturation=1.0, source="base", keep_earthy=True, aura_scale=1.0,
        dry_run=False):
    from . import textures as T
    _, slots = slotfile.read(mod)
    slot = slots[0] if not slot_code else next((s for s in slots if s.code == slot_code.upper()), None)
    if slot is None:
        raise KitError("no such slot in %s" % mod)
    bcode = ID_TO_CODE.get(slot.base)
    base_files = game.character_files(bcode) if bcode else {}
    hmap = HueMap(hue, saturation, keep_earthy)
    info("Recolouring %s's effects to hue %.0f (saturation x%.2f) from the %s files" % (
        slot.code, hue, saturation, "base character's original" if source == "base" else "mod's current"))
    done = 0
    for key in FX_KEYS:
        path = slot.file(key)
        if not path:
            warn("%s: not set in slot.toml - %s's own is used in game (not recoloured)" % (key, bcode))
            continue
        target = slotfile.mod_file(mod, path) or os.path.join(mod, *path.split("/"))
        cur = open(target, "rb").read() if os.path.isfile(target) else None
        if source == "base":
            if key not in base_files:
                warn("%s: the base character has no %s file" % (key, key))
                continue
            data = game.read(base_files[key])
        else:
            if cur is None:
                warn("%s: %s is not in the mod" % (key, path))
                continue
            data = cur
        if key in ("bsp", "bar"):
            out, n = recolour_amb(data, hmap)
            msg = "%d colour words" % n
            if key == "bar" and aura_scale != 1.0:
                out, k = scale_ames(out, aura_scale)
                msg += ", aura scaled x%.3f (%d lengths)" % (aura_scale, k)
        elif key in ("esp", "eas"):
            handles = None
            if source == "base":
                handles = [e.gidx for e in Nut(cur).entries] if cur else None
                if handles is None or len(handles) != len(Nut(data).entries):
                    raise KitError("%s: the mod's %s doesn't match the base file - recolour with --source mod "
                                   "or make the slot again" % (key, path))
            out = recolour_nut(data, hmap, handles)
            msg = "%d textures" % len(Nut(out).entries)
        else:
            handles = names = None
            if source == "base":
                if not cur or cur[:4] != b"#NUX":
                    raise KitError("%s: %s missing in the mod - make the slot with new-slot first" % (key, path))
                handles = [g for g, _o, _d in T.scan_gidx(cur)]
                names = T.nux_set_names(cur)
            out, nvc = recolour_nux(data, hmap, handles, names)
            msg = "textures + %d vertex colours" % nvc
        if not dry_run:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(out)
        done += 1
        info("  %-40s %s%s" % (path, msg, " (dry run)" if dry_run else ""))
    if source == "mod":
        info("Note: --source mod recolours what is there; run it once per change (colours drift when repeated).")
    info("%d effect file(s) %s. Restart the game (effect texture files of a new size need a restart)." % (
        done, "checked" if dry_run else "recoloured"))
