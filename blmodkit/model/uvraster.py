"""Small numpy UV rasterizer (triangle ids per texel) + dilation, for baking per-material colours into textures."""
import numpy as np


def raster_ids(uv_tris, ids, size, wrap=True):
    """UV triangles (T,3,2; v up) -> (H,W) int map of ids (-1 empty). A texel is inside when its centre is.
    wrap=True: each triangle is moved into the [0,1) tile by the floor of its centroid and also drawn at the
    neighbouring tile offsets (REPEAT addressing)."""
    H, W = size
    out = np.full((H, W), -1, np.int32)
    P = np.empty_like(uv_tris, dtype=np.float64)
    P[..., 0] = uv_tris[..., 0] * W - 0.5
    P[..., 1] = (1.0 - uv_tris[..., 1]) * H - 0.5
    if wrap:
        shift = np.floor(uv_tris.mean(1))
        P[..., 0] -= shift[:, None, 0] * W
        P[..., 1] += shift[:, None, 1] * H
    offs = [(0, 0)] + ([(dx, dy) for dx in (-W, 0, W) for dy in (-H, 0, H) if (dx, dy) != (0, 0)] if wrap else [])
    for t in range(len(P)):
        for dx, dy in offs:
            p = P[t] + np.array([dx, dy])
            x0, y0 = np.floor(p.min(0)).astype(int)
            x1, y1 = np.ceil(p.max(0)).astype(int)
            x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, W - 1), min(y1, H - 1)
            if x0 > x1 or y0 > y1:
                continue
            xs, ys = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
            a, b, c = p
            d = (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])
            if abs(d) < 1e-12:
                continue
            w1 = ((xs - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (ys - a[1])) / d
            w2 = ((b[0] - a[0]) * (ys - a[1]) - (xs - a[0]) * (b[1] - a[1])) / d
            w0 = 1 - w1 - w2
            m = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            out[ys[m], xs[m]] = ids[t]
    return out


def conservative(idmap, uv_tris, ids, size):
    """also mark texels touched by thin triangles (edge samples)"""
    H, W = size
    for t in range(len(uv_tris)):
        for k in range(3):
            a, b = uv_tris[t, k], uv_tris[t, (k + 1) % 3]
            n = int(max(abs(b[0] - a[0]) * W, abs(b[1] - a[1]) * H)) + 2
            s = np.linspace(0, 1, n)[:, None]
            q = a[None] * (1 - s) + b[None] * s
            u = np.mod(q[:, 0], 1.0)
            v = np.mod(q[:, 1], 1.0)
            c = np.clip((u * W).astype(int), 0, W - 1)
            r = np.clip(((1 - v) * H).astype(int), 0, H - 1)
            empty = idmap[r, c] < 0
            idmap[r[empty], c[empty]] = ids[t]
    return idmap


def dilate(img, mask, px):
    """grow the covered region by px texels (average of covered 8-neighbours)"""
    img = img.astype(np.float64).copy()
    mask = mask.copy()
    for _ in range(px):
        acc = np.zeros(img.shape, np.float64)
        cnt = np.zeros(mask.shape, np.float64)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                m = np.roll(np.roll(mask, dy, 0), dx, 1)
                v = np.roll(np.roll(img, dy, 0), dx, 1)
                acc += v * m[..., None]
                cnt += m
        new = (~mask) & (cnt > 0)
        if not new.any():
            break
        img[new] = acc[new] / cnt[new][:, None]
        mask = mask | new
    return img, mask


def sample(img, u, v, mode="REPEAT"):
    """bilinear sample of img (H,W,C; row 0 = top) at Blender UVs (v up); mode REPEAT / EXTEND / CLIP"""
    H, W = img.shape[:2]
    x = np.asarray(u, float) * W - 0.5
    y = (1.0 - np.asarray(v, float)) * H - 0.5
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    f = img.astype(np.float64)

    def at(yy, xx):
        if mode == "REPEAT":
            return f[np.mod(yy, H), np.mod(xx, W)]
        return f[np.clip(yy, 0, H - 1), np.clip(xx, 0, W - 1)]
    return ((at(y0, x0) * (1 - fx) + at(y0, x0 + 1) * fx) * (1 - fy)
            + (at(y0 + 1, x0) * (1 - fx) + at(y0 + 1, x0 + 1) * fx) * fy)
