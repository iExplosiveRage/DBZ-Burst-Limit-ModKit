"""Preview PNG of a costume model as the game shades it (shade.py), in the rest pose and in two base-moveset poses,
next to the base character. Software z-buffer rasterizer (numpy), no GPU or game needed.

    python -m blmodkit.model.preview <model.NUX> <skeleton.AMB> <out.png> [--game ...] [--base GOK]
"""
import numpy as np
from PIL import Image, ImageDraw

from . import nux_io, shade, skeleton

LIGHT = np.array([-0.35, 0.55, 0.75])


def model_arrays(nux_bytes):
    f = nux_io.parse(nux_bytes)
    tex = {t.gidx: t for t in f.textures}
    dec = {}
    P, N, UV, BI, BW, T, TT, TR = [], [], [], [], [], [], [], []
    off = 0
    for p in nux_io.body(f).polygons:
        m = p.materials[0]
        if len(m.textures) < 2:
            continue
        ramp, col = m.texture_ids[0], m.texture_ids[1]
        for g in (ramp, col):
            if g not in dec and g in tex:
                dec[g] = tex[g].decode()
        P.append(p.positions())
        N.append(p.normals())
        UV.append(p.uvs())
        BI.append(p.bone_idx())
        BW.append(p.weights())
        tr = p.triangles()
        T.append(tr + off)
        TT.append(np.full(len(tr), col))
        TR.append(np.full(len(tr), ramp))
        off += p.nverts
    return dict(pos=np.concatenate(P).astype(np.float64), nrm=np.concatenate(N).astype(np.float64),
                uv=np.concatenate(UV).astype(np.float64), bidx=np.concatenate(BI), bw=np.concatenate(BW),
                tris=np.concatenate(T), tri_tex=np.concatenate(TT), tri_ramp=np.concatenate(TR), images=dec)


def rot(yaw, pitch=0.0):
    a, b = np.radians(yaw), np.radians(pitch)
    Ry = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
    Rx = np.array([[1, 0, 0], [0, np.cos(b), -np.sin(b)], [0, np.sin(b), np.cos(b)]])
    return Rx @ Ry


def render(m, P, N, W=360, H=560, yaw=0.0, frame=None, bg=(52, 54, 64)):
    """P/N posed positions / normals (model space). frame = (centre, height) shared between renders"""
    R = rot(yaw)
    q = P @ R.T
    if frame is None:
        lo, hi = q.min(0), q.max(0)
        frame = ((lo + hi) / 2, (hi[1] - lo[1]) * 1.08)
    c, hgt = frame
    s = H / hgt
    X = (q[:, 0] - c[0]) * s + W / 2
    Y = H / 2 - (q[:, 1] - c[1]) * s
    Z = q[:, 2]
    Nv = N @ R.T
    L = LIGHT / np.linalg.norm(LIGHT)
    img = np.zeros((H, W, 3))
    img[:] = bg
    zb = np.full((H, W), -np.inf)
    idb = np.full((H, W), -1, np.int64)
    tris = m["tris"]
    tx, ty, tz = X[tris], Y[tris], Z[tris]
    for i in range(len(tris)):
        x0, x1 = int(max(0, np.floor(tx[i].min()))), int(min(W - 1, np.ceil(tx[i].max())))
        y0, y1 = int(max(0, np.floor(ty[i].min()))), int(min(H - 1, np.ceil(ty[i].max())))
        if x1 < x0 or y1 < y0:
            continue
        px, py = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        ax, ay, bx, by, qx, qy = tx[i, 0], ty[i, 0], tx[i, 1], ty[i, 1], tx[i, 2], ty[i, 2]
        den = (by - qy) * (ax - qx) + (qx - bx) * (ay - qy)
        if abs(den) < 1e-12:
            continue
        w0 = ((by - qy) * (px - qx) + (qx - bx) * (py - qy)) / den
        w1 = ((qy - ay) * (px - qx) + (ax - qx) * (py - qy)) / den
        w2 = 1 - w0 - w1
        msk = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not msk.any():
            continue
        z = w0 * tz[i, 0] + w1 * tz[i, 1] + w2 * tz[i, 2]
        sub = zb[y0:y1 + 1, x0:x1 + 1]
        msk &= z > sub
        if not msk.any():
            continue
        t = tris[i]
        n = w0[..., None] * Nv[t[0]] + w1[..., None] * Nv[t[1]] + w2[..., None] * Nv[t[2]]
        n /= np.linalg.norm(n, axis=-1, keepdims=True) + 1e-12
        u = w0 * m["uv"][t[0], 0] + w1 * m["uv"][t[1], 0] + w2 * m["uv"][t[2], 0]
        v = w0 * m["uv"][t[0], 1] + w1 * m["uv"][t[1], 1] + w2 * m["uv"][t[2], 1]
        T = m["images"].get(int(m["tri_tex"][i]))
        Rm = m["images"].get(int(m["tri_ramp"][i]))
        if T is None:
            texel = np.zeros(msk.shape + (4,))
            texel[..., :3] = 200
            texel[..., 3] = 127
        else:
            th, tw = T.shape[:2]
            texel = T[(np.mod(v, 1.0) * th).astype(int) % th, (np.mod(u, 1.0) * tw).astype(int) % tw].astype(float)
        nl = n @ (R @ L)
        col = shade.shade(texel, nl, Rm) if Rm is not None else texel[..., :3]
        sub[msk] = z[msk]
        idb[y0:y1 + 1, x0:x1 + 1][msk] = i
        img[y0:y1 + 1, x0:x1 + 1][msk] = shade.tone(col)[msk]
    # outline on depth jumps / silhouette
    e = np.zeros((H, W), bool)
    fin = np.isfinite(zb)
    zz = np.where(fin, zb, -1e9)
    for dy, dx in ((0, 1), (1, 0)):
        jump = (np.abs(zz[:H - dy, :W - dx] - zz[dy:, dx:]) > 0.35) | (fin[:H - dy, :W - dx] != fin[dy:, dx:])
        e[:H - dy, :W - dx] |= jump
        e[dy:, dx:] |= jump
    img[e] *= 0.15
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)), frame


def posed(m, rig, layers):
    if not layers:
        return m["pos"], m["nrm"]
    Wp = skeleton.pose_world(rig.bones, layers)
    return skeleton.skin(m["pos"], m["nrm"], m["bidx"], m["bw"], Wp, rig.W)


def sheet(model_nux, model_amb, base_nux, base_amb, moveset=None, title="", out=None, lip_bank=None):
    m = model_arrays(model_nux)
    b = model_arrays(base_nux) if base_nux is not None else None
    rig = skeleton.Rig(model_amb)
    brig = skeleton.Rig(base_amb) if base_amb is not None else None
    poses = [("rest pose, front", [], 0.0), ("rest pose, side", [], 70.0)]
    if moveset is not None:
        amms = skeleton.find_amms(moveset)
        if amms:
            bank = skeleton.AMM(moveset, amms[0])
            for label, mi, fr, yaw in (("idle (motion 0)", 0, 0, -30.0), ("punch (motion 30)", 30, 24, -40.0)):
                try:
                    mo = bank.motion(mi)
                except Exception:
                    mo = None
                if mo is not None:
                    poses.append((label, [(mo, fr)], yaw))
    if lip_bank is not None:
        amms = skeleton.find_amms(lip_bank)
        if amms:
            bank = skeleton.AMM(lip_bank, amms[0])
            jaw = next((n for n in rig.names if n.endswith("_M_JAW")), None)
            best, bk = None, -1.0
            for k in range(bank.nmot):
                try:
                    mo = bank.motion(k)
                except Exception:
                    continue
                tr = mo["tracks"].get(jaw) or mo["tracks"].get(jaw[1:]) if (mo and jaw) else None
                if not tr or 0 not in tr:
                    continue
                v = np.abs(((tr[0]["vals"] + 32768) % 65536) - 32768).max()
                if v > bk:
                    best, bk = (k, mo), v
            if best is not None:
                poses.append(("mouth: lip pose %d (close-up)" % best[0], [(best[1], 0)], -25.0, "head"))
    tiles = []
    for item in poses:
        label, layers, yaw = item[:3]
        close = len(item) > 3
        bframe = None
        P, N = posed(m, rig, layers)
        frame = None
        if b is not None:
            BP, BN = posed(b, brig, layers)
            if not layers and not close:   # rest pose: a root-scaled model is scaled about the hips - stand it on the base's floor
                P = P + np.array([0.0, BP[:, 1].min() - P[:, 1].min(), 0.0])
            allp = np.vstack([P, BP]) @ rot(yaw).T
            lo, hi = allp.min(0), allp.max(0)
            frame = ((lo + hi) / 2, (hi[1] - lo[1]) * 1.08)
        if close:
            hn = next(n for n in rig.names if n.endswith("_HEAD"))
            sc = float(rig.bones[0]["s"][1])
            Wp = skeleton.pose_world(rig.bones, layers)
            hc = Wp[rig.index[hn], :3, 3] + np.array([0.0, 0.6 * sc, 0.0])
            frame = (rot(yaw) @ hc, 5.0 * sc)
            if b is not None:
                BWp = skeleton.pose_world(brig.bones, layers)
                bframe = (rot(yaw) @ (BWp[brig.index[hn], :3, 3] + np.array([0.0, 0.6, 0.0])), 5.0)
        im, frame = render(m, P, N, yaw=yaw, frame=frame)
        tiles.append((label, im))
        if b is not None:
            bim, _ = render(b, BP, BN, yaw=yaw, frame=bframe if close else frame)
            tiles.append((label + " - base", bim))
    W, H = tiles[0][1].size
    cols = len(tiles) if len(tiles) <= 4 else 4
    rows = (len(tiles) + cols - 1) // cols
    sh = Image.new("RGB", (cols * W, rows * (H + 22) + 26), (30, 31, 38))
    d = ImageDraw.Draw(sh)
    d.text((8, 6), title, fill=(235, 235, 235))
    for k, (label, im) in enumerate(tiles):
        x, y = (k % cols) * W, 26 + (k // cols) * (H + 22)
        sh.paste(im, (x, y))
        d.text((x + 6, y + H + 4), label, fill=(220, 220, 220))
    if out:
        sh.save(out)
    return sh


if __name__ == "__main__":
    import argparse
    from . import gamefiles
    ap = argparse.ArgumentParser()
    ap.add_argument("nux")
    ap.add_argument("amb")
    ap.add_argument("out")
    ap.add_argument("--game")
    ap.add_argument("--base", default="GOK")
    a = ap.parse_args()
    g = gamefiles.open_game(a.game)
    bn, ba = gamefiles.costume_paths(a.base, 0, 0)
    mv = g.read("PAC/CMN/CH/BC%s/BC%sM0.AMB" % (a.base, a.base))
    sheet(open(a.nux, "rb").read(), open(a.amb, "rb").read(), g.read(bn), g.read(ba), mv, a.nux, a.out)
    print(a.out)
