"""Burst Limit skeletons (#AMO / #AMG inside a costume .AMB) and motions (#AMM), enough for fitting and previews.

Model space: Y up, the character faces +Z, the character's left is +X.
Engine facts used here (from the game code, see the porting notes):
  * bone world = parent world @ T(bind t) @ R(sample) @ S(rest scale); the rest scale (#AMG +0x20) is also in the
    load-time inverse bind, so a mesh for a scaled skeleton must be authored at the scaled size;
  * a motion rotation key REPLACES the bind rotation (Euler Rz*Ry*Rx, s16 binary angles), a translation key is
    ADDED to the bind translation; tracks bind by bone NAME;
  * #AMG flag 0x20000000 (+0x30) keeps a joint out of the shadow-map fit.
"""
import struct

import numpy as np

ANG = 2 * np.pi / 65536.0


def _u32(d, o):
    return struct.unpack_from(">I", d, o)[0]


def amb_chunks(d):
    """[(table entry offset, chunk offset, size)] of an .AMB container"""
    n = _u32(d, 0x10)
    t = _u32(d, 0x14)
    return [(t + 16 * k, _u32(d, t + 16 * k), _u32(d, t + 16 * k + 4)) for k in range(n)]


def _amo_offset(d):
    for _, o, z in amb_chunks(d):
        if z and d[o:o + 4] == b"#AMO":
            return o
    a = d.find(b"#AMO")
    if a < 0:
        raise ValueError("no #AMO skeleton in this AMB")
    return a


def amo(d):
    """AMB bytes -> list of dict(index, name, parent, t, q (xyzw), s, flags, amg)"""
    a = _amo_offset(d)
    n = _u32(d, a + 0x10)
    h = a + _u32(d, a + 0x14)
    nm = a + _u32(d, a + 0x24)
    g = a + _u32(d, a + 0x30)
    bones = []
    for i in range(n):
        r = h + 0x20 * i
        _idx, _xo, _ch, _sib, par = struct.unpack_from(">IIIII", d, r)
        name = d[nm + 0x20 * i: nm + 0x20 * i + 0x20].split(b"\0")[0].decode("latin1")
        gr = g + 0x20 + 0x50 * i
        q = struct.unpack_from(">4f", d, gr)
        t = struct.unpack_from(">3f", d, gr + 0x10)
        s = struct.unpack_from(">3f", d, gr + 0x20)
        fl = _u32(d, gr + 0x30)
        p = (par - _u32(d, a + 0x14)) // 0x20 if par else -1
        bones.append(dict(index=i, name=name, parent=p, t=np.array(t, float), q=np.array(q, float),
                          s=np.array(s, float), flags=fl, amg=gr))
    return bones


def quat_mat(q):
    x, y, z, w = q
    n = (x * x + y * y + z * z + w * w) ** 0.5
    x, y, z, w = x / n, y / n, z / n, w / n
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def euler_mat(v):
    ax, ay, az = v[0] * ANG, v[1] * ANG, v[2] * ANG
    cx, sx, cy, sy, cz, sz = np.cos(ax), np.sin(ax), np.cos(ay), np.sin(ay), np.cos(az), np.sin(az)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def bind_world(bones):
    W = np.zeros((len(bones), 4, 4))
    for i, b in enumerate(bones):
        M = np.eye(4)
        M[:3, :3] = quat_mat(b["q"]) * b["s"][None, :]
        M[:3, 3] = b["t"]
        W[i] = M if b["parent"] < 0 else W[b["parent"]] @ M
    return W


class Rig:
    """a base character's skeleton: bones, bind world matrices, joint positions by name"""

    def __init__(self, amb_bytes):
        self.amb = bytes(amb_bytes)
        self.bones = amo(self.amb)
        self.names = [b["name"] for b in self.bones]
        self.index = {n: i for i, n in enumerate(self.names)}
        self.W = bind_world(self.bones)
        self.parent = [b["parent"] for b in self.bones]

    def __len__(self):
        return len(self.bones)

    def joint(self, name):
        return self.W[self.index[name], :3, 3].copy()

    def has(self, name):
        return name in self.index

    def ancestors(self, i):
        out = []
        while i >= 0:
            out.append(i)
            i = self.parent[i]
        return out

    def tree_dist(self, a, b):
        sa = {x: k for k, x in enumerate(self.ancestors(a))}
        for k, x in enumerate(self.ancestors(b)):
            if x in sa:
                return sa[x] + k
        return 99


def set_root_scale(amb_bytes, scale, root="XGOK_BODY", nux_io=None):
    """AMB with a uniform rest scale on the root bone and the #BND low-poly body scaled to match.
    The model's own vertices must be written at the same scale (see the module notes)."""
    d = bytearray(amb_bytes)
    bones = amo(d)
    b0 = next((b for b in bones if b["parent"] < 0), bones[0])
    struct.pack_into(">3f", d, b0["amg"] + 0x20, scale, scale, scale)
    nbnd = 0
    if nux_io is not None:
        for _, o, z in amb_chunks(d):
            if z and d[o:o + 4] == b"#BND":
                blk = b"NDXR" + bytes(d[o + 4:o + z])
                n = nux_io.parse_ndxr(blk, 0)
                for p in n.polygons:
                    g = p.va if p.va is not None else p.vs
                    pos = np.array(g["pos"], np.float64)
                    pos[:, :3] *= scale
                    g["pos"] = pos.astype(g["pos"].dtype)
                for m in n.meshes:
                    for attr in ("sphere", "sphere2"):
                        sp = getattr(m, attr)
                        if sp is not None:
                            setattr(m, attr, (sp[0] * scale, sp[1] * scale, sp[2] * scale, sp[3]))
                if n.sphere is not None:
                    n.sphere = (n.sphere[0] * scale, n.sphere[1] * scale, n.sphere[2] * scale, n.sphere[3])
                nb = nux_io.serialize_ndxr(n)
                if len(nb) > z:
                    raise ValueError("#BND grew")
                d[o + 4:o + len(nb)] = nb[4:]
                nbnd += sum(p.nverts for p in n.polygons)
    return bytes(d), b0["name"], nbnd


def set_flag(amb_bytes, bone_name, flag):
    d = bytearray(amb_bytes)
    for b in amo(d):
        if b["name"] == bone_name:
            fl = _u32(d, b["amg"] + 0x30)
            struct.pack_into(">I", d, b["amg"] + 0x30, fl | flag)
    return bytes(d)


# --------------------------------------------------------------------------------------------- motions
class AMM:
    def __init__(self, d, A):
        self.d, self.A = d, A
        (self.hsize, _, self.ver, self.nmot, self.mtab, self.ntrk, self.nameoff) = struct.unpack_from(">7I", d, A + 4)
        self.names = [d[A + self.nameoff + 32 * k:A + self.nameoff + 32 * k + 32].split(b"\0")[0].decode("latin1")
                      for k in range(self.ntrk)]

    def entry(self, m):
        return struct.unpack_from(">HHI", self.d, self.A + self.mtab + 8 * m)

    def motion(self, m):
        d, A = self.d, self.A
        fr, extra, mo = self.entry(m)
        if not mo:
            return None
        tracks = {}
        for t in range(self.ntrk):
            nc, co = struct.unpack_from(">II", d, A + mo + 8 * t)
            chans = {}
            for c in range(nc):
                ty, _ty2, nk, tf, to, vo = struct.unpack_from(">HHHHII", d, A + co + 16 * c)
                times = np.frombuffer(d, np.uint8, nk, A + to).astype(np.int32) if nk else np.zeros(0, np.int32)
                if ty == 0:
                    vals = np.frombuffer(d, ">i2", 3 * nk, A + vo).reshape(-1, 3).astype(np.float64)
                else:
                    vals = np.frombuffer(d, ">f4", 3 * nk, A + vo).reshape(-1, 3).astype(np.float64)
                chans[ty] = dict(times=times, vals=vals, tflag=tf)
            if chans:
                tracks[self.names[t]] = chans
        return dict(frames=fr, extra=extra, tracks=tracks)


def find_amms(d):
    out = []
    for _, o, z in amb_chunks(d):
        if z and d[o:o + 4] == b"#AMM":
            out.append(o)
    return out


def _sample(ch, f, angle=False):
    t, v = ch["times"], ch["vals"]
    if len(v) == 0:
        return None
    if ch["tflag"] == 0:
        t = np.arange(len(v))
    if f <= t[0]:
        return v[0]
    if f >= t[-1]:
        return v[-1]
    k = int(np.searchsorted(t, f, "right") - 1)
    a, b = v[k], v[k + 1]
    u = (f - t[k]) / max(t[k + 1] - t[k], 1)
    if angle:
        return a + ((b - a + 32768) % 65536 - 32768) * u
    return a + (b - a) * u


def pose_world(bones, layers):
    """layers: [(motion dict, frame)]; later layers override earlier ones per track/channel"""
    W = np.zeros((len(bones), 4, 4))
    for i, b in enumerate(bones):
        R = quat_mat(b["q"])
        T = b["t"].copy()
        S = b["s"].copy()
        for mo, f in layers:
            ch = mo["tracks"].get(b["name"])
            if not ch and b["name"].startswith("X"):
                ch = mo["tracks"].get(b["name"][1:])     # lip banks key the XGOK_M_* bones as GOK_M_*
            if not ch:
                continue
            if 0 in ch:
                R = euler_mat(_sample(ch[0], f, True))
            if 1 in ch:
                T = b["t"] + _sample(ch[1], f)
        M = np.eye(4)
        M[:3, :3] = R * S[None, :]
        M[:3, 3] = T
        W[i] = M if b["parent"] < 0 else W[b["parent"]] @ M
    return W


def skin(P, N, bidx, bw, Wpose, Wbind):
    S = Wpose @ np.linalg.inv(Wbind)
    Ph = np.concatenate([P, np.ones((len(P), 1))], 1)
    M = np.zeros((len(P), 4, 4))
    for k in range(bidx.shape[1]):
        M += bw[:, k, None, None] * S[bidx[:, k]]
    Q = np.einsum("nij,nj->ni", M, Ph)[:, :3]
    if N is None:
        return Q, None
    Nn = np.einsum("nij,nj->ni", M[:, :3, :3], N)
    Nn /= np.linalg.norm(Nn, axis=1, keepdims=True) + 1e-12
    return Q, Nn
