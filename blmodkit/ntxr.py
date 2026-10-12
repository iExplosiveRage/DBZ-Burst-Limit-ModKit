"""NTXR textures: the .NUT texture banks and the texture block inside .NUX models.

NUT layout (big-endian):
    0x00  "NTXR"  u16 version 0x0100  u16 texture count  8 zero bytes
    0x10  texture entries back to back:
          +0x00 u32 entry size (header + data)  +0x08 u32 data size  +0x0C u16 header size
          +0x10 u16 mip count  +0x12 u16 format (0 DXT1, 1 DXT3, 2 DXT5)  +0x14 u16 w  +0x16 u16 h
          +0x18 24 zero bytes
          +0x30 (mips > 1) u32 size per level, padded to 0x10 with 0xCD
          then "eXt\\0" 00000020 00000010 00000000, "GIDX" 00000010 <u32 texture handle> 00000000
          then LINEAR (untiled) DXT blocks stored as 16-bit big-endian words, all levels.

The texture handle (GIDX) is how the game registers a texture: a texture whose handle is
already loaded is not made again, so every texture a mod changes needs a handle of its own.
"""
import struct

import numpy as np

EXT_CHUNK = bytes.fromhex("65587400000000200000001000000000")
FMT_NAMES = {0: "DXT1", 1: "DXT3", 2: "DXT5"}


def _align(x, a):
    return (x + a - 1) // a * a


def level_sizes(fmt, w, h, mips):
    bs = 8 if fmt == 0 else 16
    return [max(1, (max(1, w >> k) + 3) // 4) * max(1, (max(1, h >> k) + 3) // 4) * bs for k in range(mips)]


class Entry:
    """One texture entry kept as raw bytes (rewrites are exact) + parsed fields."""

    def __init__(self, raw):
        self.raw = bytearray(raw)
        (self.total, _z, self.data_size, self.header_size, _z2, self.mips, self.fmt,
         self.width, self.height) = struct.unpack_from(">IIIHHHHHH", self.raw, 0)
        if self.total != len(self.raw) or self.total != self.header_size + self.data_size:
            raise ValueError("bad NTXR entry sizes")
        q = 0x30
        if self.mips > 1:
            q = _align(q + 4 * self.mips, 0x10)
        if self.raw[q + 0x10:q + 0x18] != b"GIDX\0\0\0\x10":
            raise ValueError("NTXR entry without a GIDX chunk")
        self._gidx_at = q + 0x18

    @property
    def gidx(self):
        return struct.unpack_from(">I", self.raw, self._gidx_at)[0]

    @gidx.setter
    def gidx(self, v):
        struct.pack_into(">I", self.raw, self._gidx_at, v)

    @property
    def data(self):
        return bytes(self.raw[self.header_size:])

    def decode(self, level=0):
        sizes = level_sizes(self.fmt, self.width, self.height, self.mips)
        off = sum(sizes[:level])
        return decode_dxt(self.fmt, max(1, self.width >> level), max(1, self.height >> level),
                          self.raw[self.header_size + off:self.header_size + off + sizes[level]])

    def describe(self):
        return "%dx%d %s mips %d handle %08X" % (self.width, self.height, FMT_NAMES.get(self.fmt, self.fmt),
                                                  self.mips, self.gidx)


def make_entry(fmt, width, height, gidx, data, mips=1):
    """A complete texture entry (header + data) in the layout of the game's own files."""
    sizes = level_sizes(fmt, width, height, mips)
    assert len(data) == sum(sizes), (len(data), sizes)
    mt = b""
    if mips > 1:
        mt = struct.pack(">%dI" % mips, *sizes)
        mt += b"\xcd" * (_align(len(mt), 0x10) - len(mt))
    hs = 0x30 + len(mt) + 0x20
    h = struct.pack(">IIIHHHHHH", hs + len(data), 0, len(data), hs, 0, mips, fmt, width, height)
    h += bytes(24) + mt + EXT_CHUNK + b"GIDX\0\0\0\x10" + struct.pack(">I", gidx) + bytes(4)
    return Entry(h + bytes(data))


class Nut:
    """A .NUT file: header + entries (+ any tail bytes, none in the game's files)."""

    def __init__(self, raw):
        raw = bytes(raw)
        if raw[:4] != b"NTXR":
            raise ValueError("not an NTXR texture file")
        self.version = struct.unpack_from(">H", raw, 4)[0]
        count = struct.unpack_from(">H", raw, 6)[0]
        self.head_rest = raw[8:0x10]
        self.entries = []
        pos = 0x10
        for _ in range(count):
            total = struct.unpack_from(">I", raw, pos)[0]
            self.entries.append(Entry(raw[pos:pos + total]))
            pos += total
        self.tail = raw[pos:]

    @classmethod
    def load(cls, path):
        with open(path, "rb") as fh:
            return cls(fh.read())

    def to_bytes(self):
        out = [b"NTXR", struct.pack(">HH", self.version, len(self.entries)), self.head_rest]
        out += [bytes(e.raw) for e in self.entries]
        out.append(self.tail)
        return b"".join(out)


def scan_gidx(data):
    """[(handle, entry offset, pixel-hash)] of every NTXR texture inside a NUT / NUX / any file."""
    import hashlib
    out = []
    i = data.find(b"GIDX\0\0\0\x10")
    while i >= 0:
        # the "GIDX" tag is the last 0x10 bytes of the entry header (header size 0x50 with one
        # level, 0x60 / 0x70 with a mip table)
        for hs in (0x50, 0x60, 0x70):
            h = i - (hs - 0x10)
            if h < 0:
                continue
            total, _, dsize = struct.unpack_from(">III", data, h)
            hsize = struct.unpack_from(">H", data, h + 0x0C)[0]
            if hsize == hs and total == hsize + dsize and h + total <= len(data):
                gid = struct.unpack_from(">I", data, i + 8)[0]
                dig = hashlib.sha1(data[h + hsize:h + total]).hexdigest()[:16]
                out.append((gid, h, dig))
                break
        i = data.find(b"GIDX\0\0\0\x10", i + 8)
    return out


# ----------------------------------------------------------------------------- DXT codec

def _565(c):
    r = ((c >> 11) & 31) * 255 // 31
    g = ((c >> 5) & 63) * 255 // 63
    b = (c & 31) * 255 // 31
    return np.stack([r, g, b], -1).astype(np.int32)


def _to565(rgb):
    r = (rgb[..., 0].astype(np.int32) * 31 + 127) // 255
    g = (rgb[..., 1].astype(np.int32) * 63 + 127) // 255
    b = (rgb[..., 2].astype(np.int32) * 31 + 127) // 255
    return ((r << 11) | (g << 5) | b).astype(np.uint32)


def decode_dxt(fmt, w, h, data):
    """Linear big-endian-word DXT1/3/5 -> h x w x 4 uint8 RGBA."""
    bw, bh = (w + 3) // 4, (h + 3) // 4
    bs = 8 if fmt == 0 else 16
    raw = np.frombuffer(bytes(data[:bw * bh * bs]), dtype=">u2").astype("<u2").tobytes()
    blocks = np.frombuffer(raw, dtype=np.uint8).reshape(bh * bw, bs)
    col = blocks[:, bs - 8:]
    c0 = col[:, 0].astype(np.uint32) | (col[:, 1].astype(np.uint32) << 8)
    c1 = col[:, 2].astype(np.uint32) | (col[:, 3].astype(np.uint32) << 8)
    bits = (col[:, 4].astype(np.uint32) | (col[:, 5].astype(np.uint32) << 8) |
            (col[:, 6].astype(np.uint32) << 16) | (col[:, 7].astype(np.uint32) << 24))
    p0, p1 = _565(c0), _565(c1)
    four = (c0 > c1) | (fmt != 0)
    p2 = np.where(four[:, None], (2 * p0 + p1) // 3, (p0 + p1) // 2)
    p3 = np.where(four[:, None], (p0 + 2 * p1) // 3, 0)
    pal = np.stack([p0, p1, p2, p3], 1)
    idx = (bits[:, None] >> (2 * np.arange(16, dtype=np.uint32))) & 3
    rgb = np.take_along_axis(pal, idx[:, :, None].astype(np.int64).repeat(3, 2), 1)
    if fmt == 0:
        a = np.where((~four[:, None]) & (idx == 3), 0, 255)
    elif fmt == 1:
        ab = blocks[:, :8]
        a64 = np.zeros(len(ab), dtype=np.uint64)
        for k in range(8):
            a64 |= ab[:, k].astype(np.uint64) << np.uint64(8 * k)
        a = ((a64[:, None] >> (4 * np.arange(16, dtype=np.uint64))) & np.uint64(15)).astype(np.int32) * 17
    else:
        ab = blocks[:, :8]
        a0, a1 = ab[:, 0].astype(np.int32), ab[:, 1].astype(np.int32)
        a48 = np.zeros(len(ab), dtype=np.uint64)
        for k in range(6):
            a48 |= ab[:, 2 + k].astype(np.uint64) << np.uint64(8 * k)
        ai = ((a48[:, None] >> (3 * np.arange(16, dtype=np.uint64))) & np.uint64(7)).astype(np.int32)
        eight = a0 > a1
        pal_a = np.zeros((len(ab), 8), dtype=np.int32)
        pal_a[:, 0], pal_a[:, 1] = a0, a1
        for k in range(1, 7):
            pal_a[:, k + 1] = np.where(eight, ((7 - k) * a0 + k * a1) // 7, 0)
        for k in range(1, 5):
            pal_a[:, k + 1] = np.where(eight, pal_a[:, k + 1], ((5 - k) * a0 + k * a1) // 5)
        pal_a[:, 6] = np.where(eight, pal_a[:, 6], 0)
        pal_a[:, 7] = np.where(eight, pal_a[:, 7], 255)
        a = np.take_along_axis(pal_a, ai, 1)
    rgba = np.concatenate([rgb, a[:, :, None]], 2).astype(np.uint8)
    img = rgba.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return img[:h, :w]


def encode_dxt(fmt, img):
    """h x w x 4 uint8 RGBA -> linear DXT1 (fmt 0) / DXT5 (fmt 2) as big-endian words.
    Colour endpoints: the block's darkest and brightest pixel, slightly inset (a simple,
    fast fit that is good enough for flat cel-shaded art)."""
    img = np.ascontiguousarray(img, np.uint8)
    h, w = img.shape[:2]
    ph, pw = (h + 3) // 4 * 4, (w + 3) // 4 * 4
    pad = np.zeros((ph, pw, 4), np.uint8)
    pad[:h, :w] = img
    if ph > h:
        pad[h:, :w] = img[h - 1:h, :]
    if pw > w:
        pad[:, w:] = pad[:, w - 1:w]
    blocks = pad.reshape(ph // 4, 4, pw // 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 4)
    rgb = blocks[:, :, :3].astype(np.int32)
    n = len(rgb)
    # fully transparent pixels shouldn't pull the colour fit (DXT5)
    wgt = (blocks[:, :, 3] > 0) if fmt != 0 else np.ones(blocks.shape[:2], bool)
    wgt[~wgt.any(1)] = True
    lum = rgb @ np.array([299, 587, 114])
    big = np.int64(1) << 40
    lo = rgb[np.arange(n), np.where(wgt, lum, big).argmin(1)]
    hi = rgb[np.arange(n), np.where(wgt, lum, -big).argmax(1)]
    inset = (hi - lo) // 16
    lo, hi = np.clip(lo + inset, 0, 255), np.clip(hi - inset, 0, 255)
    c0, c1 = _to565(hi), _to565(lo)
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    eq = c0 == c1
    # a 4-colour block needs c0 > c1
    c0 = np.where(eq & (c0 < 0xFFFF), c0 + 1, c0)
    c1 = np.where(eq & (c0 == 0xFFFF) & (c1 == 0xFFFF), c1 - 1, c1)
    p0, p1 = _565(c0), _565(c1)
    p2 = (2 * p0 + p1) // 3
    p3 = (p0 + 2 * p1) // 3
    pal = np.stack([p0, p1, p2, p3], 1)
    dist = ((rgb[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(-1)
    idx = dist.argmin(2).astype(np.uint32)
    bits = (idx << (2 * np.arange(16, dtype=np.uint32))).sum(1).astype(np.uint32)
    color = np.zeros((n, 8), np.uint8)
    color[:, 0], color[:, 1] = c0 & 255, c0 >> 8
    color[:, 2], color[:, 3] = c1 & 255, c1 >> 8
    for k in range(4):
        color[:, 4 + k] = (bits >> (8 * k)) & 255
    if fmt == 0:
        out = color
    else:
        a = blocks[:, :, 3].astype(np.int32)
        a0, a1 = a.max(1), a.min(1)
        same = a0 == a1
        a1 = np.where(same & (a0 > 0), a0 - 1, a1)
        a0 = np.where(same & (a0 == 0), 1, a0)
        pal_a = np.zeros((n, 8), np.int32)
        pal_a[:, 0], pal_a[:, 1] = a0, a1
        for k in range(1, 7):
            pal_a[:, k + 1] = ((7 - k) * a0 + k * a1) // 7
        ai = np.abs(a[:, :, None] - pal_a[:, None, :]).argmin(2).astype(np.uint64)
        a48 = (ai << (3 * np.arange(16, dtype=np.uint64))).sum(1).astype(np.uint64)
        alpha = np.zeros((n, 8), np.uint8)
        alpha[:, 0], alpha[:, 1] = a0, a1
        for k in range(6):
            alpha[:, 2 + k] = ((a48 >> np.uint64(8 * k)) & np.uint64(255)).astype(np.uint8)
        out = np.concatenate([alpha, color], 1)
    return np.frombuffer(out.tobytes(), dtype="<u2").astype(">u2").tobytes()


def mip_chain(rgba, levels):
    """Alpha-weighted box-filter mips."""
    out = [np.asarray(rgba, np.uint8)]
    cur = out[0].astype(np.float32)
    for _ in range(1, levels):
        h, w = cur.shape[:2]
        nh, nw = max(1, h // 2), max(1, w // 2)
        c = cur[:nh * 2, :nw * 2].reshape(nh, 2, nw, 2, 4)
        a = c[..., 3:4]
        asum = a.sum((1, 3))
        rgb = np.where(asum > 0, (c[..., :3] * a).sum((1, 3)) / np.maximum(asum, 1e-6), c[..., :3].mean((1, 3)))
        cur = np.concatenate([rgb, a.mean((1, 3))], -1)
        out.append(np.clip(cur + 0.5, 0, 255).astype(np.uint8))
    return out


def encode_rgba(rgba, fmt=2, mips=1):
    return b"".join(encode_dxt(fmt, lv) for lv in mip_chain(rgba, mips))
