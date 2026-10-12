"""DXT1 / DXT5 for Burst Limit NTXR textures: linear (untiled) blocks stored as 16-bit BIG-endian words.

decode(fmt, w, h, data) -> h x w x 4 uint8      (fmt 0 = DXT1, 1 = DXT3, 2 = DXT5)
encode(fmt, rgba)       -> bytes for one level  (Pillow's BC1/BC3 encoder when present, else a small
                                                  bounding-box encoder)
mip_chain(rgba, levels) -> [level0, level1, ...] box-filtered
"""
import io

import numpy as np

try:
    from PIL import Image
except ImportError:  # pragma: no cover - Pillow is a kit requirement
    Image = None


# ------------------------------------------------------------------------------------------------ decode
def _565(c):
    r = ((c >> 11) & 31) * 255 // 31
    g = ((c >> 5) & 63) * 255 // 63
    b = (c & 31) * 255 // 31
    return np.stack([r, g, b], -1).astype(np.int32)


def decode(fmt, w, h, data):
    bw, bh = (w + 3) // 4, (h + 3) // 4
    bs = 8 if fmt == 0 else 16
    raw = np.frombuffer(data[:bw * bh * bs], dtype=">u2").astype("<u2").tobytes()
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


# ------------------------------------------------------------------------------------------------ encode
def _to565(rgb):
    r = (rgb[..., 0].astype(np.int32) * 31 + 127) // 255
    g = (rgb[..., 1].astype(np.int32) * 63 + 127) // 255
    b = (rgb[..., 2].astype(np.int32) * 31 + 127) // 255
    return (r << 11) | (g << 5) | b


def _pad4(img):
    h, w = img.shape[:2]
    ph, pw = (h + 3) // 4 * 4, (w + 3) // 4 * 4
    if (ph, pw) == (h, w):
        return img
    pad = np.zeros((ph, pw, 4), np.uint8)
    pad[:h, :w] = img
    pad[h:, :w] = img[h - 1:h, :]
    pad[:, w:] = pad[:, w - 1:w]
    return pad


def _encode_simple(fmt, img):
    """bounding-box DXT1/DXT5 encoder (little-endian block bytes)"""
    pad = _pad4(img)
    ph, pw = pad.shape[:2]
    blocks = pad.reshape(ph // 4, 4, pw // 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 4)
    rgb = blocks[:, :, :3].astype(np.int32)
    lum = rgb @ np.array([299, 587, 114])
    lo = rgb[np.arange(len(rgb)), lum.argmin(1)]
    hi = rgb[np.arange(len(rgb)), lum.argmax(1)]
    c0, c1 = _to565(hi), _to565(lo)
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    p0, p1 = _565(c0), _565(c1)
    four = c0 > c1
    p2 = np.where(four[:, None], (2 * p0 + p1) // 3, (p0 + p1) // 2)
    p3 = np.where(four[:, None], (p0 + 2 * p1) // 3, (p0 + p1) // 2)
    pal = np.stack([p0, p1, p2, p3], 1)
    dist = ((rgb[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(-1)
    idx = dist.argmin(2).astype(np.uint32)
    bits = (idx << (2 * np.arange(16, dtype=np.uint32))).sum(1).astype(np.uint32)
    color = np.zeros((len(blocks), 8), np.uint8)
    color[:, 0], color[:, 1] = c0 & 255, c0 >> 8
    color[:, 2], color[:, 3] = c1 & 255, c1 >> 8
    for k in range(4):
        color[:, 4 + k] = (bits >> (8 * k)) & 255
    if fmt == 0:
        return color.tobytes()
    a = blocks[:, :, 3].astype(np.int32)
    a0, a1 = a.max(1), a.min(1)
    same = a0 == a1
    a1 = np.where(same & (a0 > 0), a0 - 1, a1)
    a0 = np.where(same & (a0 == 0), 1, a0)
    pal_a = np.zeros((len(a), 8), np.int32)
    pal_a[:, 0], pal_a[:, 1] = a0, a1
    for k in range(1, 7):
        pal_a[:, k + 1] = ((7 - k) * a0 + k * a1) // 7
    ai = np.abs(a[:, :, None] - pal_a[:, None, :]).argmin(2).astype(np.uint64)
    a48 = (ai << (3 * np.arange(16, dtype=np.uint64))).sum(1).astype(np.uint64)
    alpha = np.zeros((len(a), 8), np.uint8)
    alpha[:, 0], alpha[:, 1] = a0, a1
    for k in range(6):
        alpha[:, 2 + k] = ((a48 >> np.uint64(8 * k)) & np.uint64(255)).astype(np.uint8)
    return np.concatenate([alpha, color], 1).tobytes()


def _encode_pillow(fmt, img):
    pad = _pad4(img)
    ph, pw = pad.shape[:2]
    buf = io.BytesIO()
    if fmt == 0:
        Image.fromarray(np.ascontiguousarray(pad[..., :3]), "RGB").save(buf, "DDS", pixel_format="DXT1")
        n = (ph // 4) * (pw // 4) * 8
    else:
        Image.fromarray(np.ascontiguousarray(pad), "RGBA").save(buf, "DDS", pixel_format="DXT5")
        n = (ph // 4) * (pw // 4) * 16
    d = buf.getvalue()
    data = d[len(d) - n:]
    if len(data) != n:
        raise ValueError("unexpected DDS size")
    return data


_PILLOW_OK = None


def _pillow_ok():
    global _PILLOW_OK
    if _PILLOW_OK is None:
        try:
            t = np.zeros((8, 8, 4), np.uint8)
            t[..., 0] = 200
            t[..., 3] = 127
            _encode_pillow(2, t)
            _encode_pillow(0, t)
            _PILLOW_OK = True
        except Exception:
            _PILLOW_OK = False
    return _PILLOW_OK


def encode(fmt, img):
    """img: h x w x 4 uint8 -> one level of linear DXT, 16-bit big-endian words (the game's layout)"""
    img = np.ascontiguousarray(np.asarray(img, np.uint8))
    if fmt not in (0, 2):
        raise ValueError("only DXT1 (0) and DXT5 (2) are written")
    le = _encode_pillow(fmt, img) if _pillow_ok() else _encode_simple(fmt, img)
    if fmt == 0:
        # DXT1: keep every block in 4-colour mode (c0 > c1) - the 3-colour mode would turn index 3 into
        # transparent black on the game side; Pillow's encoder may emit it for flat blocks.
        b = np.frombuffer(le, np.uint8).reshape(-1, 8).copy()
        c0 = b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8)
        c1 = b[:, 2].astype(np.int32) | (b[:, 3].astype(np.int32) << 8)
        bad = c0 <= c1
        if bad.any():
            le = bytes(b.tobytes())
            simple = np.frombuffer(_encode_simple(0, img), np.uint8).reshape(-1, 8)
            b[bad] = simple[bad]
            le = b.tobytes()
    return np.frombuffer(le, dtype="<u2").astype(">u2").tobytes()


def mip_chain(img, levels):
    out = [np.asarray(img, np.uint8)]
    cur = out[0].astype(np.float32)
    for _ in range(1, levels):
        h, w = cur.shape[:2]
        nh, nw = max(1, h // 2), max(1, w // 2)
        cur = cur[:nh * 2, :nw * 2].reshape(nh, 2, nw, 2, 4).mean((1, 3)) if h > 1 and w > 1 else cur
        out.append(np.clip(cur + 0.5, 0, 255).astype(np.uint8))
    return out
