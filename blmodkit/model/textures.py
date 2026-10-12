"""Source materials -> Burst Limit toon textures: colour textures (+ alpha = shading offset) and toon ramps.

The game's character pixel shader (decoded from the game, see shade.py):
    column u = 0.67 * texel.alpha + 42.075 * (1 + N.L)        (alpha 127 -> columns 85..170)
    colour   = texel.rgb - ramp[u].rgb                          (SUBTRACTIVE: a ramp stores what it takes away)
Ramps are 256x256 DXT1 with identical rows; the base characters use band edges at columns 114 / 151:
    [0,114) shadow, [114,151) mid, [151,256) lit.
What each source material gives:
    xv2   (Xenoverse 2 Blender importer: DYT ramp row + mask): lit = DYT row median over x 0.775-0.92,
          shadow = median over x 0.55-0.69, ink lines from mask alpha (lerp lit -> shadow plateau)
    image (any base-colour image; FighterZ: base x ilm.alpha lines, shadow tint = the 'sss' image)
    flat  (no image: the material colour)
Palettes:
    own  (default): one ramp per distinct shading: delta = lit * (1 - r'), r' = shadow ratio softened toward the
         game's 0.6 (--soften), so lit shows the model's colour and shadow its own shadow colour
    base: skin materials use the base character's skin ramp, everything else (except eyes / teeth) its cloth
         ramp (Goku: 0x10010049), copied with their retail ids - the model takes the game's palette
    --skin-tone base: skin texels become the base character's skin texel (Goku: white, the ramp gives the
         colour) keeping the line art -> the skin colour of the base character
"""
import os
import re

import numpy as np

from . import uvraster

LIT_X, SHADOW_X = (0.775, 0.92), (0.55, 0.69)
FLAT_X = 0.9
INK_STRENGTH = 0.35
INK_LO = (1 - 98.5 / 128 / FLAT_X) / INK_STRENGTH
INK_HI = (1 - 88.5 / 128 / FLAT_X) / INK_STRENGTH
GAME_SHADOW = 0.6
MID_T = 0.4
ALPHA = 127
BAND_MID, BAND_LIT = 114, 151
PAD = 8

SKIN_RE = re.compile(r"(skin|face|body|hand|head|ear\b|neck|^arm|_arm|cheek|nose|bust_skin|flesh)",
                     re.I)
NOT_SKIN_RE = re.compile(r"(cloth|wear|gi\b|shirt|pant|glove|boot|shoe|belt|hair|eye|tooth|teeth|nail|armou?r|mouth|tongue|"
                         r"mask|metal|cape|scarf|sock|leg_)", re.I)
EYE_RE = re.compile(r"(eye|iris|pupil|sclera|tooth|teeth)", re.I)


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def lin2srgb(c):
    c = np.clip(np.asarray(c, float), 0, 1)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * c ** (1 / 2.4) - 0.055)


def pow2_at_most(n, cap):
    p = 1
    while p * 2 <= min(n, cap):
        p *= 2
    return max(p, 4)


class Images:
    def __init__(self, exp_dir, info):
        self.dir = os.path.join(exp_dir, "img")
        self.info = info
        self.cache = {}

    def get(self, name):
        if name is None:
            return None
        if name not in self.cache:
            e = self.info.get(name)
            if not e or e.get("missing") or "npy" not in e:
                self.cache[name] = None
            else:
                self.cache[name] = np.load(os.path.join(self.dir, e["npy"])).astype(np.float64)
        return self.cache[name]


def classify(name, override=None):
    if override:
        return override
    if EYE_RE.search(name):
        return "eye"
    if SKIN_RE.search(name) and not NOT_SKIN_RE.search(name):
        return "skin"
    return "other"


def build(exp, exp_dir, fitted, base, palette="own", skin_tone="model", soften=0.5, shadow_default=GAME_SHADOW,
          max_size=1024, overrides=None, log=print):
    """-> dict(textures=[{name, rgba}], ramps=[{name, rgba | retail gidx}], tri_tex, tri_ramp, vsrc, uv (NUX: v down),
    materials report)"""
    overrides = overrides or {}
    imgs = Images(exp_dir, exp["images"])
    mats = exp["materials"]
    tris = fitted["tris"]
    tmat = fitted["tri_material"]
    uv_shift = fitted["uv"].astype(np.float64)
    uv_orig = fitted["uv_orig"].astype(np.float64)
    report = {}

    # ---------------------------------------------------------------- 1. per material colours
    M = []
    for mi, m in enumerate(mats):
        ov = overrides.get(m["name"], {})
        e = dict(name=m["name"], kind=m["kind"], cls=classify(m["name"], ov.get("class")), src=m)
        if m["kind"] == "xv2" and imgs.get(m.get("dyt")) is not None:
            dyt = imgs.get(m["dyt"])
            H = dyt.shape[0]
            row = dyt[min(int(round((16 * m["band"] + 2) * H / 128.0)), H - 1), :, :3]
            Wd = row.shape[0]

            def med(x0, x1):
                return np.median(row[int(round(x0 * Wd)):max(int(round(x1 * Wd)), int(round(x0 * Wd)) + 1)], 0)
            e.update(lit=med(*LIT_X), shadow=med(*SHADOW_X), key=("mask", m.get("mask") or m["dyt"]),
                     how="xv2 DYT band %d" % int(m["band"]))
            e["ink"] = e["shadow"].copy()
            # Xenoverse importer quirk: mouth / tongue materials point at an unused light-blue DYT band;
            # rebuild them from the skin row (band 0) of the same DYT: dark red mouth, red-pink tongue
            if re.search(r"mouth|tongue", m["name"], re.I) and e["lit"][2] > e["lit"][0] + 30:
                skin = dyt[min(int(round(2 * H / 128.0)), H - 1), :, :3]
                sc = Wd / 128.0

                def rng(a, b):
                    return np.median(skin[int(a * sc):max(int(b * sc), int(a * sc) + 1)], 0)
                if re.search(r"tongue", m["name"], re.I):
                    e["lit"], e["ink"] = rng(94, 95), rng(58, 89)
                else:
                    e["lit"], e["ink"] = rng(58, 89), rng(24, 55)
                e["shadow"] = e["lit"] * 0.6
                e["how"] += " (light-blue importer band replaced by the skin row's dark reds)"
            mk = imgs.get(m.get("mask"))
            if e["cls"] == "eye" and mk is not None and mk[..., 2].max() > 128:
                e["iris"] = dyt[min(12, H - 1), Wd // 2, :3]
            e["clamp_tile"] = True
        elif m["kind"] == "image" and imgs.get(m.get("image")) is not None:
            e.update(key=("image", m["image"], m.get("ilm")), how="image " + m["image"],
                     extension=m.get("extension", "REPEAT"))
            if m.get("ilm"):
                e["how"] += " x ilm alpha"
        else:
            c = np.array(m.get("colour", [0.8, 0.8, 0.8, 1]), float)[:3]
            e.update(kind="flat", key=("flat",), lit=lin2srgb(c) * 255.0, how="flat colour")
            if m["kind"] != "flat":
                log("  WARNING: material %s: its image has no pixels - flat colour used" % m["name"])
        for k in ("lit", "shadow"):
            if k in ov:
                e[k] = np.array(ov[k], float)
                e["how"] = e.get("how", "") + " (%s from bone map)" % k
        if "ramp" in ov:
            e["ramp_mode"] = ov["ramp"]
        if m.get("alpha_from_image") or m.get("blend_method") in ("BLEND", "CLIP"):
            e["alpha_warning"] = True
        M.append(e)

    # ---------------------------------------------------------------- 2. split vertices per texture key
    keys = []
    for e in M:
        if e["key"] not in keys:
            keys.append(e["key"])
    tri_key = np.array([keys.index(M[m]["key"]) for m in tmat], np.int64)
    pair = tris * len(keys) + tri_key[:, None]
    uniq, inv = np.unique(pair.reshape(-1), return_inverse=True)
    vsrc = uniq // len(keys)
    vkey = uniq % len(keys)
    tris2 = inv.reshape(-1, 3)
    uv_new = uv_shift[vsrc].copy()
    flat_cells = {}

    # ---------------------------------------------------------------- 3. bake one texture per key
    textures = []
    tri_tex = np.zeros(len(tris), np.int64)
    for ki, key in enumerate(keys):
        tsel = np.nonzero(tri_key == ki)[0]
        if len(tsel) == 0:
            continue
        tri_tex[tsel] = len(textures)
        vsel = np.unique(tris2[tsel])
        members = sorted({int(tmat[t]) for t in tsel})
        kind = key[0]
        if kind == "flat":
            n = len(members)
            cells = int(np.ceil(np.sqrt(n)))
            size = 64
            cell = size // max(cells, 1)
            img = np.zeros((size, size, 3))
            for j, mi in enumerate(members):
                cx, cy = j % cells, j // cells
                img[cy * cell:(cy + 1) * cell, cx * cell:(cx + 1) * cell] = M[mi]["lit"]
                flat_cells[mi] = ((cx + 0.5) * cell / size, 1.0 - (cy + 0.5) * cell / size)
            for t in tsel:
                u, v = flat_cells[int(tmat[t])]
                uv_new[tris2[t]] = (u, v)
            rgba = np.zeros((size, size, 4), np.uint8)
            rgba[..., :3] = np.clip(np.round(img), 0, 255)
            rgba[..., 3] = ALPHA
            textures.append(dict(name="flat_colours", rgba=rgba, members=members, cover=None))
            for mi in members:
                M[mi]["texture"] = len(textures) - 1
            continue
        if kind == "mask":
            src = imgs.get(key[1])
            clamp_tile = True
        else:
            src = imgs.get(key[1])
            clamp_tile = False
        sh, sw = src.shape[:2]
        H, W = pow2_at_most(sh, max_size), pow2_at_most(sw, max_size)
        # domain: the [0,1) tile, or the UV bounding box when the islands leave the tile (remap)
        uvk = uv_shift[vsrc[vsel]]
        lo, hi = uvk.min(0), uvk.max(0)
        remap = bool((lo < -0.01).any() or (hi > 1.01).any())
        if remap:
            uo = uv_orig[vsrc[vsel]]
            lo, hi = uo.min(0) - 0.02, uo.max(0) + 0.02
            span = hi - lo
            uv_new[vsel] = (uv_orig[vsrc[vsel]] - lo) / span
            tile = np.floor(np.median(uo, 0))
        else:
            lo, span, tile = np.zeros(2), np.ones(2), np.zeros(2)
        rr, cc = np.mgrid[0:H, 0:W]
        u_t, v_t = (cc + 0.5) / W, 1.0 - (rr + 0.5) / H
        u_s, v_s = lo[0] + u_t * span[0], lo[1] + v_t * span[1]
        if clamp_tile and remap:
            u_s, v_s = np.clip(u_s - tile[0], 0, 1), np.clip(v_s - tile[1], 0, 1)
            mode = "EXTEND"
        else:
            mode = "REPEAT" if not remap else M[members[0]].get("extension", "REPEAT")
        # material id per texel
        tuv = uv_new[tris2[tsel]]
        idm = uvraster.raster_ids(tuv, tmat[tsel].astype(np.int32), (H, W), wrap=not remap)
        idm = uvraster.conservative(idm, tuv, tmat[tsel].astype(np.int32), (H, W))
        covered = idm >= 0
        same_size = (not remap) and (sh, sw) == (H, W)
        if kind == "mask":
            ms = uvraster.sample(src, u_s, v_s, "EXTEND" if clamp_tile and remap else "REPEAT")
            A = ms[..., 3] / 255.0
            img = np.zeros((H, W, 3))
            for mi in members:
                e = M[mi]
                sel = idm == mi
                if not sel.any():
                    continue
                if "iris" in e:
                    b = smoothstep(0.15, 0.85, ms[..., 2] / 255.0)[..., None]
                    col = e["lit"][None, None] * (1 - b) + e["iris"][None, None] * b
                else:
                    s = smoothstep(INK_LO, INK_HI, A)[..., None]
                    col = e["lit"][None, None] * (1 - s) + e["ink"][None, None] * s
                img[sel] = col[sel]
            img, grown = uvraster.dilate(img, covered, PAD)
            dom = max(members, key=lambda mi: int((idm == mi).sum()))
            img[~grown] = M[dom]["lit"]
        else:
            if same_size:
                img = src[..., :3].copy()
                ilm = M[members[0]]["src"].get("ilm")
            else:
                img = uvraster.sample(src, u_s, v_s, mode)[..., :3]
                ilm = M[members[0]]["src"].get("ilm")
            il = imgs.get(ilm) if ilm else None
            if il is not None:
                img = img * (uvraster.sample(il, u_s, v_s, mode)[..., 3:4] / 255.0)
            for mi in members:
                e = M[mi]
                sel = idm == mi
                if "lit" not in e:
                    e["lit"] = np.median(img[sel], 0) if sel.any() else np.median(img.reshape(-1, 3), 0)
                if "shadow" not in e:
                    sss = imgs.get(e["src"].get("sss"))
                    if sss is not None:
                        ss = uvraster.sample(sss, u_s, v_s, mode)[..., :3]
                        ratio = np.median(ss[sel], 0) / 255.0 if sel.any() else np.full(3, shadow_default)
                        e["shadow"] = e["lit"] * np.clip(ratio, 0, 1)
                        e["how"] += " + sss shadow"
        textures.append(dict(name=str(key[1]), rgba=None, img=img, idm=idm, members=members, remap=remap,
                             size=(W, H)))
        for mi in members:
            M[mi]["texture"] = len(textures) - 1

    # ---------------------------------------------------------------- 4. shading: ramps + texel adjustments
    ramps = []
    ramp_index = {}

    def own_ramp(e):
        L = np.maximum(np.asarray(e["lit"], float), 1.0)
        Sh = np.asarray(e.get("shadow", L * shadow_default), float)
        ok = L >= 12
        r = np.full(3, shadow_default)
        if ok.any():
            r[ok] = Sh[ok] / L[ok]
            r[~ok] = r[ok].mean()
        r = np.clip((1 - soften) * r + soften * GAME_SHADOW, 0, 1)
        delta = L * (1 - r)
        row = np.zeros((256, 3))
        row[:BAND_MID] = delta
        row[BAND_MID:BAND_LIT] = (1 - MID_T) * delta
        stored = np.clip(np.round(row), 0, 255).astype(np.uint8)
        k = ("own",) + tuple(int(v) for v in stored[0]) + tuple(int(v) for v in stored[BAND_MID])
        if k not in ramp_index:
            img = np.zeros((256, 256, 4), np.uint8)
            img[:, :, :3] = stored[None]
            img[:, :, 3] = 255
            ramp_index[k] = len(ramps)
            ramps.append(dict(name="own %s" % e["name"], rgba=img, shadow=stored[0].tolist(),
                              mid=stored[BAND_MID].tolist(), lit=[0, 0, 0]))
        return ramp_index[k], r

    def retail_ramp(gid, what):
        k = ("retail", gid)
        if k not in ramp_index:
            ramp_index[k] = len(ramps)
            rr = base.ramps[gid]
            ramps.append(dict(name="%s ramp %08x (base character's own)" % (what, gid), retail=gid,
                              shadow=rr["shadow"].tolist(), mid=rr["mid"].tolist(), lit=rr["lit"].tolist()))
        return ramp_index[k]

    mat_ramp = {}
    for mi, e in enumerate(M):
        if "texture" not in e:
            continue
        mode = e.get("ramp_mode")
        if mode is None:
            if palette == "base" and e["cls"] == "skin" and base.skin_ramp is not None:
                mode = "base_skin"
            elif palette == "base" and e["cls"] == "other" and base.cloth_ramp is not None:
                mode = "base_cloth"
            elif skin_tone == "base" and e["cls"] == "skin" and base.skin_ramp is not None:
                mode = "base_skin"
            else:
                mode = "own"
        e["ramp_mode"] = mode
        if mode == "base_skin":
            mat_ramp[mi] = retail_ramp(base.skin_ramp, "skin")
        elif mode == "base_cloth":
            mat_ramp[mi] = retail_ramp(base.cloth_ramp, "cloth")
        else:
            mat_ramp[mi], e["shadow_ratio"] = own_ramp(e)

    for ti, t in enumerate(textures):
        if t.get("img") is None:
            continue
        img = t["img"]
        idm = t["idm"]
        for mi in t["members"]:
            e = M[mi]
            sel = idm == mi
            if not sel.any():
                continue
            L = np.maximum(np.asarray(e["lit"], float), 1.0)
            if e["ramp_mode"] == "base_skin" and (skin_tone == "base" or palette == "base"):
                rr = base.ramps[base.skin_ramp]
                if skin_tone == "base":
                    # base skin texel with the model's line art as darkening
                    lum = (img[sel] @ np.array([0.299, 0.587, 0.114])) / max(L @ np.array([0.299, 0.587, 0.114]), 1)
                    img[sel] = base.skin_texel[None] * np.clip(lum, 0, 1.2)[:, None]
                else:
                    img[sel] = img[sel] + rr["lit"][None]
            elif e["ramp_mode"] == "base_cloth":
                img[sel] = img[sel] + base.ramps[base.cloth_ramp]["lit"][None]
        rgba = np.zeros(img.shape[:2] + (4,), np.uint8)
        rgba[..., :3] = np.clip(np.round(img), 0, 255)
        rgba[..., 3] = ALPHA
        t["rgba"] = rgba

    tri_ramp = np.array([mat_ramp[int(m)] for m in tmat], np.int64)
    uv_nux = np.stack([uv_new[:, 0], 1.0 - uv_new[:, 1]], 1).astype(np.float32)
    for mi, e in enumerate(M):
        if "texture" not in e:
            continue
        report[e["name"]] = dict(kind=e["kind"], cls=e["cls"], how=e.get("how"), texture=textures[e["texture"]]["name"],
                                 lit=[int(round(float(v))) for v in e["lit"]],
                                 shadow=[int(round(float(v))) for v in e.get("shadow", np.asarray(e["lit"]) * shadow_default)],
                                 ramp=ramps[mat_ramp[mi]]["name"], ramp_mode=e["ramp_mode"])
        if e.get("alpha_warning"):
            report[e["name"]]["warning"] = ("uses transparency in the source - Burst Limit's body material is opaque "
                                            "(texture alpha = shading offset), cut-out parts show as solid")
            log("  WARNING: material %s uses transparency; it will be drawn opaque" % e["name"])
    return dict(textures=textures, ramps=ramps, tri_tex=tri_tex, tri_ramp=tri_ramp, vsrc=vsrc, tris=tris2,
                uv=uv_nux, materials=report, keys=[list(map(str, k)) for k in keys])
