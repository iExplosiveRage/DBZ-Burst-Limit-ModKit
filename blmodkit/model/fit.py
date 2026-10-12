"""Fit a source model onto the base character's Burst Limit skeleton (rest to rest) and transfer the weights.

Generalised from the Kid Buu (Xenoverse 2), Ultimate Gohan (FighterZ / Xenoverse 1) ports:
 1. orientation + similarity from the skeleton itself: up = hips -> head, left = right upper arm -> left upper arm,
    forward = left x up; scale S makes the hip-to-shoulder height the base character's; hips land on its hips;
 2. per-role rest-to-rest maps, blended with the model's own weights: limbs (clavicle, upper arm, forearm, palm,
    finger segments, thigh, shin) rotate + stretch along the bone onto the base joints (cross-section kept, the hand
    scales uniformly), torso stays, the neck squashes so the head joint lands on the base HEAD, head + face
    translate, feet go ankle -> ankle with a vertical scale that puts the soles on the base character's soles;
 3. weights by role onto the base bones; limb segments are spread over the base twist bones with the base model's
    OWN weight profile along that segment (sampled from its body mesh), the palm copies the base hand's palm bones
    from the nearest base hand vertices, fingers by name, face rigid on HEAD (or the lip bones with lips=True);
    crotch zone of trousers: twist bones folded into LEGDIR (smaller stretch in wide stances);
    at most 4 influences: a dropped bone's weight goes to the nearest kept bone in the base bone tree.
"""
import numpy as np

from ..util import KitError

FINGERS = ("thumb", "index", "middle", "ring", "pinky")
LIP_MAP = {   # role -> [(base bone suffix after 'X<code>_', share)]; 'HEAD' = <code>_HEAD
    "jaw": [("M_JAW", 1.0)], "jaw_half": [("M_JAW", 0.5), ("HEAD", 0.5)],
    "lip_lower_L": [("M_LLLIP", 1.0)], "lip_lower_C": [("M_LCLIP", 1.0)], "lip_lower_R": [("M_LRLIP", 1.0)],
    "lip_upper_L": [("M_ULLIP", 1.0)], "lip_upper_C": [("M_UCLIP", 1.0)], "lip_upper_R": [("M_URLIP", 1.0)],
    "mouth_corner_L": [("M_LCHEEK", 0.5), ("M_ULLIP", 0.25), ("M_LLLIP", 0.25)],
    "mouth_corner_R": [("M_RCHEEK", 0.5), ("M_URLIP", 0.25), ("M_LRLIP", 0.25)],
    "cheek_lower_L": [("M_JAW", 0.5), ("HEAD", 0.5)], "cheek_lower_R": [("M_JAW", 0.5), ("HEAD", 0.5)],
    "teeth_upper": [("HEAD", 1.0)], "teeth_lower": [("M_JAW", 1.0)],
    "tongue1": [("M_TONGUE1", 1.0)], "tongue2": [("M_TONGUE2", 1.0)], "tongue3": [("M_TONGUE3", 1.0)],
}
HEADLIKE = set(LIP_MAP) | {"head"}


class FitError(KitError):
    pass


# --------------------------------------------------------------------------------------------- math
def rot_between(u, v):
    u = u / np.linalg.norm(u)
    v = v / np.linalg.norm(v)
    c = float(u @ v)
    w = np.cross(u, v)
    s = np.linalg.norm(w)
    if s < 1e-9:
        if c > 0:
            return np.eye(3)
        a = np.cross(u, [1.0, 0, 0])
        if np.linalg.norm(a) < 1e-6:
            a = np.cross(u, [0, 1.0, 0])
        a /= np.linalg.norm(a)
        return 2 * np.outer(a, a) - np.eye(3)
    k = w / s
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    a = np.arctan2(s, c)
    return np.eye(3) + np.sin(a) * K + (1 - np.cos(a)) * K @ K


class Xf:
    """affine p -> A p + b"""

    def __init__(self, A=None, b=None, name=""):
        self.A = np.eye(3) if A is None else A
        self.b = np.zeros(3) if b is None else b
        self.name = name
        self.info = {}

    @staticmethod
    def anchored(A, o, c, name=""):
        return Xf(A, c - A @ o, name)

    def __call__(self, p):
        return p @ self.A.T + self.b

    @staticmethod
    def mix(items, name=""):
        return Xf(sum(w * x.A for x, w in items), sum(w * x.b for x, w in items), name)


class ShearXf(Xf):
    """p + d * clamp(((p - o).u) / |u|^2, 0, 1): moves the segment's end by d without extrapolating past it
    (the neck: hair or a hood weighted to the neck must not fly off when the neck stretches a lot)"""

    def __init__(self, o, u, d, name=""):
        super().__init__(np.eye(3) + np.outer(d, u) / max(u @ u, 1e-12), np.zeros(3), name)
        self.b = -self.A @ o + o
        self.o, self.u, self.d = o, u, d

    def __call__(self, p):
        t = np.clip(((p - self.o) @ self.u) / max(self.u @ self.u, 1e-12), 0.0, 1.0)
        return p + t[..., None] * self.d


def seg_xf(o, ob, c, cb, perp=1.0, name=""):
    """map segment o->ob onto c->cb: rotate the direction, stretch along it, scale the cross-section by perp"""
    u, v = ob - o, cb - c
    lu, lv = np.linalg.norm(u), np.linalg.norm(v)
    if lu < 1e-9 or lv < 1e-9:
        return Xf.anchored(np.eye(3), o, c, name)
    k = lv / lu
    uh = u / lu
    D = perp * np.eye(3) + (k - perp) * np.outer(uh, uh)
    R = rot_between(u, v)
    x = Xf.anchored(R @ D, o, c, name)
    x.info = dict(stretch=round(float(k), 4), perp=round(float(perp), 4),
                  rot_deg=round(float(np.degrees(np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1)))), 2))
    return x


# --------------------------------------------------------------------------------------------- base names
class Base:
    """bone names of a base character (code GOK -> GOK_LARM1, XGOK_LF_LINDEX1 ...) on its skeleton"""

    def __init__(self, rig, code):
        self.rig, self.code = rig, code.upper()

    def name(self, key, side=None):
        P = self.code
        if key.startswith("X"):
            return "X%s_%s" % (P, key[1:].replace("{s}", side or ""))
        return "%s_%s" % (P, key.replace("{s}", side or ""))

    def idx(self, key, side=None, required=False):
        n = self.name(key, side)
        i = self.rig.index.get(n)
        if i is None and required:
            raise FitError("the base skeleton has no bone %s" % n)
        return i

    def J(self, key, side=None):
        return self.rig.joint(self.name(key, side))

    def finger(self, side, f, n):
        return "X%s_%sF_%s%s%d" % (self.code, side, side, f.upper(), n)


# --------------------------------------------------------------------------------------------- fit
def fit(ex, roles, rig, base_code, base_body, lips=False, crotch=True, drop_stray=False, log=print):
    """ex: export.npz arrays; roles: resolved {bone: role}; rig: skeleton.Rig of the base costume;
    base_body: dict(pos, bidx, bw) of the base character's body mesh (model space).
    -> dict(pos, nrm, bidx, bw, uv, tris, tri_material, tri_mesh, vertex_mesh, report)"""
    B = Base(rig, base_code)
    names = [str(n) for n in ex["bone_names"]]
    BI = {n: i for i, n in enumerate(names)}
    parents = [int(p) for p in ex["bone_parent"]]
    heads = ex["bone_head"].astype(np.float64)
    tails = ex["bone_tail"].astype(np.float64)
    P = ex["positions"].astype(np.float64)
    Nrm = ex["normals"].astype(np.float64)
    WB = ex["weight_bone"].astype(np.int64)
    WV = ex["weight_value"].astype(np.float64)
    WV = np.where(WB >= 0, WV, 0.0)
    V = len(P)
    rep = {}

    role_bone = {}
    for n in names:
        r = roles[n]
        if r and not r.startswith(("follow:", "bone:")) and r not in ("auto", "ignore") and r != "spine":
            role_bone.setdefault(r, n)
    missing = [r for r in ("pelvis", "head") + tuple("%s_%s" % (s, k) for s in "LR" for k in (
        "upperarm", "forearm", "hand", "thigh", "shin", "foot")) if r not in role_bone]
    if missing:
        raise FitError("the bone map has no bone for: %s - edit bone_map.json and pass it with --bone-map"
                       % ", ".join(missing))

    def h(role):
        return heads[BI[role_bone[role]]]

    # vertices without weights take the nearest weighted vertex's weights
    wsum = WV.sum(1)
    empty = wsum <= 1e-8
    if empty.any():
        ok = np.nonzero(~empty)[0]
        for v in np.nonzero(empty)[0]:
            j = ok[np.argmin(((P[ok] - P[v]) ** 2).sum(1))]
            WB[v], WV[v] = WB[j], WV[j]
        rep["unweighted_vertices_fixed"] = int(empty.sum())
        wsum = WV.sum(1)
    WV /= wsum[:, None]

    # ---- 1. orientation + similarity ---------------------------------------------------------------
    hip = (h("L_thigh") + h("R_thigh")) / 2
    sho = (h("L_upperarm") + h("R_upperarm")) / 2
    up = h("head") - hip
    up /= np.linalg.norm(up)
    left = h("L_upperarm") - h("R_upperarm")
    left -= up * (left @ up)
    left /= np.linalg.norm(left)
    fwd = np.cross(left, up)
    R0 = np.stack([left, up, fwd])            # rows: source dirs -> BL X (left), Y (up), Z (forward)
    toe_dir = None
    for s in "LR":
        if "%s_toe" % s in role_bone:
            d = h("%s_toe" % s) - h("%s_foot" % s)
            toe_dir = (toe_dir if toe_dir is not None else 0) + d @ fwd
    if toe_dir is not None and toe_dir < 0:
        log("  WARNING: the toes point backwards - are the model's left/right bone names swapped?")
    g_hip = (B.J("{s}LEG1", "L") + B.J("{s}LEG1", "R")) / 2
    g_sho = (B.J("{s}ARM1", "L") + B.J("{s}ARM1", "R")) / 2
    S = (g_sho[1] - g_hip[1]) / ((sho - hip) @ up)
    T = g_hip - S * (R0 @ hip)

    def al(p):
        return (np.asarray(p, np.float64) @ R0.T) * S + T

    Pal = al(P)
    Nal = Nrm @ R0.T
    J = {n: al(heads[i]) for n, i in BI.items()}
    Jt = {n: al(tails[i]) for n, i in BI.items()}
    sole_h = (P @ up).min()
    rep["similarity"] = dict(scale=float(S), source_height=float((P @ up).max() - sole_h),
                             source_shoulder=float(sho @ up - sole_h),
                             left=left.round(4).tolist(), up=up.round(4).tolist(), forward=fwd.round(4).tolist())

    def jr(role):
        return J[role_bone[role]]

    # sanity: a wrong bone map shows as a broken skeleton - stop with a readable message
    problems = []
    for s_ in "LR":
        for a_, b_, ga, gb in (("upperarm", "forearm", "{s}ARM1", "{s}ARM2"), ("forearm", "hand", "{s}ARM2", "{s}HANDXR"),
                               ("thigh", "shin", "{s}LEG1", "{s}LEG2"), ("shin", "foot", "{s}LEG2", "{s}FOOT")):
            ls = np.linalg.norm(jr(s_ + "_" + b_) - jr(s_ + "_" + a_))
            lg = np.linalg.norm(B.J(gb, s_) - B.J(ga, s_))
            if not 0.25 < ls / max(lg, 1e-9) < 4.0:
                problems.append("%s %s is %.2fx the base's (%s -> %s)" % (s_, a_, ls / max(lg, 1e-9),
                                                                         role_bone[s_ + "_" + a_], role_bone[s_ + "_" + b_]))
        if not jr(s_ + "_foot")[1] < jr(s_ + "_shin")[1] < jr(s_ + "_thigh")[1]:
            problems.append("%s leg joints are not above each other (thigh %s, shin %s, foot %s)" % (
                s_, role_bone[s_ + "_thigh"], role_bone[s_ + "_shin"], role_bone[s_ + "_foot"]))
        side = 1 if s_ == "L" else -1
        if not side * jr(s_ + "_hand")[0] > side * jr(s_ + "_upperarm")[0] - 0.5:
            problems.append("%s hand is not outside the %s shoulder" % (s_, s_))
    if problems:
        raise FitError("the bone map gives a broken skeleton: %s. Check bone_map.json (the joint roles must be on the "
                       "bones whose HEAD is that joint) and pass it back with --bone-map" % "; ".join(problems))

    def tail_or_continue(bone, prev_vec):
        """end of a chain's last bone: its tail when that looks like a real bone (imported FBX tails are often
        arbitrary), else the previous segment continued"""
        h0, t0 = J[bone], Jt[bone]
        v, pv = t0 - h0, np.asarray(prev_vec, float)
        lv, lp = np.linalg.norm(v), np.linalg.norm(pv)
        if lp < 1e-9:
            return t0
        if lv > 1e-9 and (v @ pv) / (lv * lp) > 0.85 and 0.3 < lv / lp < 3.0:
            return t0
        return h0 + pv

    def up_bl(k):
        return np.array([0.0, k * S, 0.0])

    # ---- source role of every bone (follow / auto resolved) -------------------------------------------
    def base_role(n):
        r = roles[n]
        if r.startswith("follow:"):
            r = r[7:]
        return r

    # dominant role per vertex (for soles / crotch)
    vrole = np.array([base_role(names[b]) if b >= 0 else "pelvis" for b in WB[np.arange(V), WV.argmax(1)]])

    # ---- 2. per-role maps ---------------------------------------------------------------------------
    X = {}
    ident = Xf(name="torso")
    for r in ("pelvis", "spine", "chest"):
        X[r] = ident
    neck_o = jr("neck") if "neck" in role_bone else jr("head") - up_bl(0.05)
    g_head = B.J("HEAD")
    # neck: neck joint fixed, head joint -> base HEAD (stretch / shear along the neck only)
    u = jr("head") - neck_o
    d = (g_head - neck_o) - u
    neck = ShearXf(neck_o, u, d, "neck")
    kn = float((g_head[1] - neck_o[1]) / max(u[1], 1e-6))
    neck.info = dict(stretch=round(kn, 4))
    head = Xf.anchored(np.eye(3), jr("head"), g_head, "head")
    X["neck"] = neck
    for r in HEADLIKE:
        X[r] = head
    hand_k = {}
    soles = {}
    gk_pos = np.asarray(base_body["pos"], np.float64)
    for s, sg in (("L", 1), ("R", -1)):
        ua = seg_xf(jr(s + "_upperarm"), jr(s + "_forearm"), B.J("{s}ARM1", s), B.J("{s}ARM2", s), name=s + " upper arm")
        fa = seg_xf(jr(s + "_forearm"), jr(s + "_hand"), B.J("{s}ARM2", s), B.J("{s}HANDXR", s), name=s + " forearm")
        X[s + "_upperarm"], X[s + "_forearm"] = ua, fa
        X[s + "_elbow"] = Xf.mix([(ua, 0.5), (fa, 0.5)], s + " elbow")
        X[s + "_shoulderhelper"] = ua
        if s + "_clavicle" in role_bone:
            X[s + "_clavicle"] = seg_xf(jr(s + "_clavicle"), jr(s + "_upperarm"), jr(s + "_clavicle"),
                                        B.J("{s}ARM1", s), name=s + " clavicle")
        # palm: wrist -> middle knuckle (or the hand bone's tail), uniform
        mid = s + "_middle1"
        knuckle_src = jr(mid) if mid in role_bone else tail_or_continue(
            role_bone[s + "_hand"], 0.35 * (jr(s + "_hand") - jr(s + "_forearm")))
        gm = B.finger(s, "middle", 1)
        knuckle_dst = rig.joint(gm) if rig.has(gm) else B.J("{s}HANDXR", s) + (B.J("{s}HANDXR", s) - B.J("{s}ARM2", s)) * 0.35
        k = np.linalg.norm(knuckle_dst - B.J("{s}HANDXR", s)) / max(np.linalg.norm(knuckle_src - jr(s + "_hand")), 1e-6)
        hand_k[s] = round(float(k), 4)
        hd = seg_xf(jr(s + "_hand"), knuckle_src, B.J("{s}HANDXR", s), knuckle_dst, perp=k, name=s + " palm")
        X[s + "_hand"] = hd
        for f in FINGERS:
            prev = hd
            for j in (1, 2, 3):
                r = "%s_%s%d" % (s, f, j)
                if r not in role_bone:
                    X[r] = prev
                    continue
                bo = jr(r)
                nxt = "%s_%s%d" % (s, f, j + 1)
                if j < 3 and nxt in role_bone:
                    bb = jr(nxt)
                else:
                    bb = tail_or_continue(role_bone[r], bo - (jr("%s_%s%d" % (s, f, j - 1)) if j > 1 and
                                                              "%s_%s%d" % (s, f, j - 1) in role_bone else jr(s + "_hand")))
                gname = B.finger(s, f, j)
                if not rig.has(gname):
                    X[r] = prev
                    continue
                c = rig.joint(gname)
                gnext = B.finger(s, f, j + 1)
                if j < 3 and rig.has(gnext):
                    cb = rig.joint(gnext)
                else:
                    d2 = c - (rig.joint(B.finger(s, f, j - 1)) if j > 1 and rig.has(B.finger(s, f, j - 1))
                              else B.J("{s}HANDXR", s))
                    st = prev.info.get("stretch", 1.0) if prev is not hd else 1.0
                    cb = c + d2 / max(np.linalg.norm(d2), 1e-9) * np.linalg.norm(bb - bo) * st
                prev = seg_xf(bo, bb, c, cb, perp=k, name=r)
                X[r] = prev
        th = seg_xf(jr(s + "_thigh"), jr(s + "_shin"), B.J("{s}LEG1", s), B.J("{s}LEG2", s), name=s + " thigh")
        sn = seg_xf(jr(s + "_shin"), jr(s + "_foot"), B.J("{s}LEG2", s), B.J("{s}FOOT", s), name=s + " shin")
        X[s + "_thigh"], X[s + "_shin"] = th, sn
        X[s + "_knee"] = Xf.mix([(th, 0.5), (sn, 0.5)], s + " knee")
        X[s + "_hiphelper"] = Xf.mix([(ident, 0.5), (th, 0.5)], s + " hip")
        foot_v = np.isin(vrole, [s + "_foot", s + "_toe"])
        sole_src = Pal[foot_v, 1].min() if foot_v.any() else Pal[np.sign(Pal[:, 0]) == sg, 1].min()
        sole_dst = gk_pos[np.sign(gk_pos[:, 0]) == sg, 1].min()
        ky = (B.J("{s}FOOT", s)[1] - sole_dst) / max(jr(s + "_foot")[1] - sole_src, 1e-6)
        ky = float(np.clip(ky, 0.4, 2.5))
        ft = Xf.anchored(np.diag([1.0, ky, 1.0]), jr(s + "_foot"), B.J("{s}FOOT", s), s + " foot")
        ft.info = dict(stretch=round(ky, 4))
        X[s + "_foot"] = X[s + "_toe"] = ft
        soles[s] = dict(base=round(float(sole_dst), 3), source_aligned=round(float(sole_src), 3))

    def xf_of(n):
        r = base_role(n)
        if r.startswith("bone:"):
            j = parents[BI[n]]
            while j >= 0:
                rr = base_role(names[j])
                if not rr.startswith("bone:"):
                    return X.get(rr, ident)
                j = parents[j]
            return ident
        return X.get(r, ident)

    bone_xf = [xf_of(n) for n in names]
    Pfit = np.zeros_like(Pal)
    Afit = np.zeros((V, 3, 3))
    for k in range(WB.shape[1]):
        for bi in np.unique(WB[:, k]):
            if bi < 0:
                continue
            m = WB[:, k] == bi
            x = bone_xf[bi]
            w = WV[m, k][:, None]
            Pfit[m] += w * x(Pal[m])
            Afit[m] += w[:, :, None] * x.A[None]
    Nfit = np.einsum("nji,nj->ni", np.linalg.inv(Afit), Nal)
    Nfit /= np.linalg.norm(Nfit, axis=1, keepdims=True) + 1e-12
    for s, sg in (("L", 1), ("R", -1)):
        fv = np.isin(vrole, [s + "_foot", s + "_toe"])
        if fv.any():
            soles[s]["fitted"] = round(float(Pfit[fv, 1].min()), 3)

    # ---- 3. weights --------------------------------------------------------------------------------
    NB = len(rig)
    gk_bi = np.asarray(base_body["bidx"], np.int64)
    gk_bw = np.asarray(base_body["bw"], np.float64)
    gk_w = np.zeros((len(gk_pos), NB))
    for k in range(gk_bi.shape[1]):
        np.add.at(gk_w, (np.arange(len(gk_pos)), gk_bi[:, k]), gk_bw[:, k])

    def gi_list(keys, side):
        out = []
        for key in keys:
            i = B.idx(key, side)
            if i is not None:
                out.append(i)
        return out

    def profile(gi, a, b, side):
        gi = np.array(gi)
        sub = gk_w[:, gi]
        tot = sub.sum(1)
        sel = (tot >= 0.5) & (np.sign(gk_pos[:, 0]) == side)
        if sel.sum() < 4:
            return None
        t = ((gk_pos[sel] - a) @ (b - a)) / ((b - a) @ (b - a))
        edges = np.arange(-0.5, 1.5001, 0.05)
        cen = (edges[:-1] + edges[1:]) / 2
        acc = np.zeros((len(cen), len(gi)))
        cnt = np.zeros(len(cen))
        ix = np.clip(np.digitize(t, edges) - 1, 0, len(cen) - 1)
        np.add.at(acc, ix, sub[sel])
        np.add.at(cnt, ix, tot[sel])
        ok = cnt > 0
        prof = np.zeros_like(acc)
        prof[ok] = acc[ok] / cnt[ok, None]
        okc = cen[ok]
        for i in np.nonzero(~ok)[0]:
            prof[i] = prof[np.nonzero(ok)[0][np.argmin(np.abs(okc - cen[i]))]]
        for _ in range(2):
            pp = np.vstack([prof[:1], prof, prof[-1:]])
            prof = 0.25 * pp[:-2] + 0.5 * pp[1:-1] + 0.25 * pp[2:]
        prof /= np.maximum(prof.sum(1, keepdims=True), 1e-12)
        return dict(gi=gi, a=a, b=b, cen=cen, prof=prof)

    def eval_profile(pr, p):
        t = ((p - pr["a"]) @ (pr["b"] - pr["a"])) / ((pr["b"] - pr["a"]) @ (pr["b"] - pr["a"]))
        t = np.clip(t, pr["cen"][0], pr["cen"][-1])
        out = np.stack([np.interp(t, pr["cen"], pr["prof"][:, j]) for j in range(len(pr["gi"]))], -1)
        return out / np.maximum(out.sum(1, keepdims=True), 1e-12)

    PROF, PALM = {}, {}
    for s, sg in (("L", 1), ("R", -1)):
        a1 = B.J("{s}ARM1", s)
        PROF[s + "_clavicle"] = profile(gi_list(["{s}SHOULDER", "{s}CLAVICLE"], s), np.array([0.0, a1[1], 0.0]), a1, sg)
        PROF[s + "_upperarm"] = profile(gi_list(["{s}ARMDIR", "{s}AROLL1", "{s}AROLL2", "{s}ARM1"], s), a1,
                                        B.J("{s}ARM2", s), sg)
        PROF[s + "_forearm"] = profile(gi_list(["{s}ARM2", "{s}AROLL3", "{s}AROLL4", "{s}HANDXR"], s),
                                       B.J("{s}ARM2", s), B.J("{s}HANDXR", s), sg)
        PROF[s + "_thigh"] = profile(gi_list(["{s}LEGDIR", "{s}LEGROLL1", "{s}LEGROLL2", "{s}LEG1"], s),
                                     B.J("{s}LEG1", s), B.J("{s}LEG2", s), sg)
        PROF[s + "_shin"] = profile(gi_list(["{s}LEG2", "{s}LEGROLL3", "{s}LEGROLL4"], s), B.J("{s}LEG2", s),
                                    B.J("{s}FOOT", s), sg)
        pg = gi_list(["{s}HANDXR", "{s}HANDROT"], s) + [rig.index[B.finger(s, x, 0)[:-1]] for x in ("rh", "ch", "lh", "tu")
                                                          if rig.has(B.finger(s, x, 0)[:-1])]
        pg = np.array(pg)
        tot = gk_w[:, pg].sum(1)
        sel = (tot >= 0.3) & (np.sign(gk_pos[:, 0]) == sg)
        PALM[s] = dict(gi=pg, pos=gk_pos[sel], w=gk_w[sel][:, pg] / tot[sel, None]) if sel.sum() >= 4 else None

    def palm_weights(s, p, k=4):
        pm = PALM[s]
        out = np.zeros((len(p), len(pm["gi"])))
        for c0 in range(0, len(p), 2048):
            q = p[c0:c0 + 2048]
            d2 = ((q[:, None, :] - pm["pos"][None]) ** 2).sum(-1)
            nn = np.argsort(d2, 1)[:, :k]
            dd = np.sqrt(np.take_along_axis(d2, nn, 1)) + 1e-3
            w = 1 / dd
            w /= w.sum(1, keepdims=True)
            out[c0:c0 + 2048] = (pm["w"][nn] * w[:, :, None]).sum(1)
        return out

    g_st, g_ch = B.J("STOMACH"), B.J("CHEST")
    head_i = B.idx("HEAD", required=True)

    def single(role):
        """role -> [(base bone index, share)] for one-bone rules, or None"""
        if role.startswith("bone:"):
            nm = role[5:]
            if not rig.has(nm):
                raise FitError("bone map: the base skeleton has no bone %s" % nm)
            return [(rig.index[nm], 1.0)]
        if role == "pelvis":
            return [(B.idx("WAIST", required=True), 1.0)]
        if role == "chest":
            return [(B.idx("CHEST", required=True), 1.0)]
        if role == "neck":
            return [(B.idx("NECK", required=True), 1.0)]
        if role == "head":
            return [(head_i, 1.0)]
        if role in LIP_MAP:
            if not lips:
                return [(head_i, 1.0)]
            out = []
            for suf, sh in LIP_MAP[role]:
                i = head_i if suf == "HEAD" else rig.index.get("X%s_%s" % (B.code, suf))
                out.append((head_i if i is None else i, sh))
            return out
        if len(role) > 2 and role[1] == "_" and role[0] in "LR":
            s, r = role[0], role[2:]
            t = {"elbow": "{s}ELBOW", "knee": "{s}KNEE", "hiphelper": "{s}HIP", "shoulderhelper": "{s}ARMDIR",
                 "foot": "{s}FOOT", "toe": "{s}TOE"}
            if r in t:
                i = B.idx(t[r], s)
                if i is None:
                    i = B.idx("{s}FOOT", s) if r == "toe" else None
                if i is not None:
                    return [(i, 1.0)]
            for f in FINGERS:
                if r.startswith(f):
                    nm = B.finger(s, f, int(r[-1]))
                    if rig.has(nm):
                        return [(rig.index[nm], 1.0)]
                    return None   # -> palm
        return None

    acc = np.zeros((V, NB))
    unknown = set()
    for k in range(WB.shape[1]):
        for bi in np.unique(WB[:, k]):
            if bi < 0:
                continue
            m = np.nonzero(WB[:, k] == bi)[0]
            w = WV[m, k]
            role = base_role(names[bi])
            one = single(role)
            if one is not None:
                for gi, sh in one:
                    np.add.at(acc, (m, np.full(len(m), gi)), w * sh)
                continue
            if role == "spine":
                # by the bone's own joint height between the base STOMACH and CHEST joints
                yb = bone_xf[bi](J[names[bi]][None])[0, 1]
                to_chest = float(np.clip((yb - g_st[1]) / max(g_ch[1] - g_st[1], 1e-6), 0, 1))
                to_chest = 0.0 if to_chest < 0.5 else 1.0
                np.add.at(acc, (m, np.full(len(m), B.idx("STOMACH", required=True))), w * (1 - to_chest))
                np.add.at(acc, (m, np.full(len(m), B.idx("CHEST", required=True))), w * to_chest)
                continue
            s = role[0] if len(role) > 2 and role[1] == "_" else None
            pr = PROF.get(role)
            if pr is not None:
                pw = eval_profile(pr, Pfit[m])
                for j, g_i in enumerate(pr["gi"]):
                    np.add.at(acc, (m, np.full(len(m), g_i)), w * pw[:, j])
                continue
            if s and (role.endswith("_hand") or any(role[2:].startswith(f) for f in FINGERS)) and PALM.get(s):
                pw = palm_weights(s, Pfit[m])
                for j, g_i in enumerate(PALM[s]["gi"]):
                    np.add.at(acc, (m, np.full(len(m), g_i)), w * pw[:, j])
                continue
            # anything else (unknown role): the nearest base joint of the torso / limbs by position
            unknown.add(role)
            np.add.at(acc, (m, np.full(len(m), B.idx("WAIST", required=True))), w)
    if unknown:
        rep["unknown_roles_on_waist"] = sorted(unknown)
        log("  WARNING: roles the importer doesn't know went to the waist: %s" % sorted(unknown))
    acc /= np.maximum(acc.sum(1, keepdims=True), 1e-12)

    # crotch: fold the thigh twist bones into LEGDIR, HIP half into WAIST / half into LEGDIR
    crotch_n = 0
    if crotch:
        hipw = abs(B.J("{s}LEG1", "L")[0] - B.J("{s}LEG1", "R")[0])
        half_w, top, bottom = 0.9 * hipw, g_hip[1] - 0.1 * hipw, g_hip[1] - 2.25 * hipw
        zone_roles = {"pelvis", "L_thigh", "R_thigh", "L_hiphelper", "R_hiphelper"}
        inz = np.isin(vrole, list(zone_roles))

        def ramp(v):
            v = np.clip(v, 0, 1)
            return v * v * (3 - 2 * v)
        ax, y = np.abs(Pfit[:, 0]), Pfit[:, 1]
        fall = ramp((half_w - ax) / (half_w * 0.5)) * ramp((top - y) / 1.0) * ramp((y - bottom) / 1.0)
        fall = np.where(inz, fall, 0.0)
        waist = B.idx("WAIST", required=True)
        for s in "LR":
            d = B.idx("{s}LEGDIR", s)
            if d is None:
                continue
            for key in ("{s}LEGROLL1", "{s}LEGROLL2", "{s}LEG1"):
                i = B.idx(key, s)
                if i is None:
                    continue
                mv = acc[:, i] * fall
                acc[:, i] -= mv
                acc[:, d] += mv
            i = B.idx("{s}HIP", s)
            if i is not None:
                mv = acc[:, i] * fall
                acc[:, i] -= mv
                acc[:, d] += 0.5 * mv
                acc[:, waist] += 0.5 * mv
        crotch_n = int((fall > 0).sum())

    # 4 influences
    order = np.argsort(-acc, 1)
    nnz = (acc > 1e-6).sum(1)
    moved = np.zeros(V)
    for v in np.nonzero(nnz > 4)[0]:
        keep = list(order[v, :4])
        for bd in order[v, 4:nnz[v]]:
            moved[v] += acc[v, bd]
            tgt = min(keep, key=lambda kb: (rig.tree_dist(bd, kb), -acc[v, kb]))
            acc[v, tgt] += acc[v, bd]
            acc[v, bd] = 0
    order = np.argsort(-acc, 1)
    top4 = order[:, :4]
    tw = np.take_along_axis(acc, top4, 1)
    tw /= tw.sum(1, keepdims=True)
    tw[tw < 1e-4] = 0
    tw /= tw.sum(1, keepdims=True)
    bidx = np.where(tw > 0, top4, top4[:, :1]).astype(np.uint32)
    bw = tw.astype(np.float32)

    # ---- stray vertices: far from every bone they are weighted to (broken rips: a hand's vertices lying on the
    # floor) - they would be dragged across the whole model by the fit and the animations
    seg_a, seg_b = heads.copy(), tails.copy()
    for i in range(len(names)):
        kids = [k for k in range(len(names)) if parents[k] == i]
        if kids:
            seg_b[i] = heads[kids].mean(0)
    hgt = float((P @ up).max() - (P @ up).min())
    dmin = np.full(V, np.inf)
    for k in range(WB.shape[1]):
        ok = (WB[:, k] >= 0) & (WV[:, k] > 0.05)
        b = WB[ok, k]
        a_, ab = seg_a[b], seg_b[b] - seg_a[b]
        t = np.clip(((P[ok] - a_) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-12), 0, 1)
        d = np.linalg.norm(P[ok] - (a_ + t[:, None] * ab), axis=1)
        dmin[ok] = np.minimum(dmin[ok], d)
    stray = dmin > 0.25 * hgt
    rep["stray_vertices"] = int(stray.sum())
    tris_in = ex["tris"].astype(np.int64)
    if stray.any():
        if drop_stray:
            keep_t = ~stray[tris_in].any(1)
            rep["stray_triangles_dropped"] = int((~keep_t).sum())
            log("  dropped %d triangles with %d stray vertices (far from every bone they are weighted to)"
                % ((~keep_t).sum(), stray.sum()))
        elif stray.mean() > 0.02:
            raise FitError("%d of %d vertices (%.0f%%) lie far from every bone they are weighted to - the model's "
                           "weights or its rest pose are broken (check it in Blender: pose the armature). "
                           "--drop-stray removes those triangles, --use-pose takes the file's current pose as the rest "
                           "pose" % (stray.sum(), V, 100 * stray.mean()))
        else:
            keep_t = np.ones(len(tris_in), bool)
            log("  WARNING: %d vertices lie far from every bone they are weighted to (a broken source?) - they will "
                "stretch across the model. --drop-stray removes their triangles" % stray.sum())
    else:
        keep_t = np.ones(len(tris_in), bool)

    # ---- 4. UVs: Blender convention (v up) -> each triangle shifted into its tile, then V down ---------
    uv = ex["uv"].astype(np.float64)
    tris = tris_in[keep_t]
    shift = np.floor(uv[tris].mean(1))                       # (T,2)
    # split vertices whose triangles want different tile shifts
    key = {}
    newv = []
    tris2 = tris.copy()
    vshift = np.full((V, 2), np.nan)
    for t in range(len(tris)):
        for c in range(3):
            v = tris[t, c]
            sh = shift[t]
            if np.isnan(vshift[v, 0]):
                vshift[v] = sh
            elif not np.array_equal(vshift[v], sh):
                kk = (v, sh[0], sh[1])
                if kk not in key:
                    key[kk] = V + len(newv)
                    newv.append((v, sh))
                tris2[t, c] = key[kk]
    src = np.concatenate([np.arange(V), np.array([v for v, _ in newv], np.int64)]) if newv else np.arange(V)
    sh_all = np.concatenate([np.nan_to_num(vshift), np.array([s for _, s in newv]).reshape(-1, 2)]) if newv else \
        np.nan_to_num(vshift)
    uv_t = uv[src] - sh_all

    out = dict(pos=Pfit[src].astype(np.float32), nrm=Nfit[src].astype(np.float32), uv=uv_t.astype(np.float32), uv_orig=uv[src].astype(np.float32),
               bidx=bidx[src], bw=bw[src], tris=tris2, tri_material=ex["tri_material"].astype(np.int64)[keep_t],
               tri_mesh=ex["tri_mesh"].astype(np.int64)[keep_t], vertex_mesh=ex["vertex_mesh"][src].astype(np.int64),
               src_vertex=src, vrole=vrole[src], scale_similarity=S)
    # ---- report ------------------------------------------------------------------------------------
    joints = {}
    for r, gk in (("L_upperarm", ("{s}ARM1", "L")), ("L_forearm", ("{s}ARM2", "L")), ("L_hand", ("{s}HANDXR", "L")),
                  ("L_thigh", ("{s}LEG1", "L")), ("L_shin", ("{s}LEG2", "L")), ("L_foot", ("{s}FOOT", "L")),
                  ("R_upperarm", ("{s}ARM1", "R")), ("R_forearm", ("{s}ARM2", "R")), ("R_hand", ("{s}HANDXR", "R")),
                  ("R_thigh", ("{s}LEG1", "R")), ("R_shin", ("{s}LEG2", "R")), ("R_foot", ("{s}FOOT", "R")),
                  ("head", ("HEAD", None))):
        g = B.J(*gk)
        f = X[r](jr(r))
        joints[r] = dict(source=role_bone[r], base=B.name(*gk), error=round(float(np.linalg.norm(f - g)), 4))
    rep.update(joints=joints, soles=soles, neck_squash=round(float(kn), 4), hand_scale=hand_k,
               transforms={k: dict(name=x.name, **x.info) for k, x in X.items() if x.info},
               crotch_vertices=crotch_n, weights_moved_by_4_limit_max=round(float(moved.max()), 4),
               bones_used=sorted({rig.names[i] for i in np.unique(bidx[bw > 0])}),
               vertices=int(len(src)), triangles=int(len(tris2)), uv_tile_splits=len(newv),
               bbox=[Pfit.min(0).round(3).tolist(), Pfit.max(0).round(3).tolist()],
               base_bbox=[gk_pos.min(0).round(3).tolist(), gk_pos.max(0).round(3).tolist()])
    out["report"] = rep
    return out
