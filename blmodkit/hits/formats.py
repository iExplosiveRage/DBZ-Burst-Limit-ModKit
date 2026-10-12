"""Readers for the moveset (.AMB) pieces the hit simulator needs. Big-endian (Xbox 360).

Moveset BC<code>M0.AMB = #AMB container with chunks: 0 #CPU, 1 #SPX, 2 #BCM (command tree),
3 #AMM (motions), 4 #AMM, 5 #BSK (move records), 6-7 #BSK.

#AMM motion bank: header (+0x10 motion count, +0x14 motion table, +0x18 track count, +0x1C track
names, char[32] each); motion table entry = u16 frames, u16 extra (source size class), u32 offset;
a motion = per track (u32 channel count, u32 channel table); channel = u16 type (0 rotation,
1 translation, 2 scale), u16, u16 key count, u16 time flag (1 = u8 frame list), u32 times, u32 values.
Rotation keys: 3 x s16 Euler X,Y,Z (0x10000 = 360 deg), R = Rz*Ry*Rx; translation keys are
added to the bind translation. Tracks bind to skeleton bones by NAME.

#BSK: header 8 x u32 (+16 slot count, +20 slot table, +24 hit count, +28 hit table); record =
u16 motion, u16 bank, ..., +0x2C channel count, +0x30 channel table; channel = u16 type, u16 key
count, u32 keys; key = 20 bytes (u16 frame, u8 seq, u8 kind, params). Type 1 = hitbox: u32 hit id,
u16 class, u8 bone (logical joint), u8 size, s16 x, y, z (x0.1, bone-local), s16 w. Type 0 =
cancel windows, type 4 = speed. Hit table entry: 8 x (u16, u16, u16 type, u16, f32, f32, u32).

#BCM: command records (0x40 bytes + links); the rush / smash strings are paths in this tree.

#AMO skeleton (BC<code>0YZ.AMB): +0x10 bone count, +0x14 hierarchy (0x20 per bone: index, xform,
child, sibling, parent record offset), +0x24 names (0x20 each), +0x30 #AMG (bind pose, 0x50 per
bone at +0x20: quaternion xyzw, translation, scale).
"""
import struct

import numpy as np

ANG = 2 * np.pi / 65536.0


def amb_entries(d):
    n, toff = struct.unpack_from(">II", d, 0x10)
    return [struct.unpack_from(">IIiI", d, toff + 16 * i) for i in range(n)]


def chunk(d, k):
    o, s = amb_entries(d)[k][:2]
    return d[o:o + s]


# ----------------------------------------------------------------------------- #AMM

class AMM:
    def __init__(self, d, A):
        self.d, self.A = d, A
        (_hs, _z, _ver, self.nmot, self.mtab, self.ntrk, nameoff) = struct.unpack_from(">7I", d, A + 4)
        self.names = [d[A + nameoff + 32 * k:A + nameoff + 32 * k + 32].split(b"\0")[0].decode("latin1")
                      for k in range(self.ntrk)]
        self._cache = {}

    def motion_entry(self, m):
        return struct.unpack_from(">HHI", self.d, self.A + self.mtab + 8 * m)

    def named_motion(self, m):
        if m in self._cache:
            return self._cache[m]
        d, A = self.d, self.A
        fr, extra, mo = self.motion_entry(m)
        if not mo:
            return None
        tracks = {}
        for t in range(self.ntrk):
            nc, co = struct.unpack_from(">II", d, A + mo + 8 * t)
            chans = {}
            for c in range(nc):
                ty, _t2, nk, tf, to, vo = struct.unpack_from(">HHHHII", d, A + co + 16 * c)
                times = np.frombuffer(d, np.uint8, nk, A + to).astype(np.int32) if nk else np.zeros(0, np.int32)
                if ty == 0:
                    vals = np.frombuffer(d, ">i2", 3 * nk, A + vo).reshape(-1, 3).astype(np.float64)
                else:
                    vals = np.frombuffer(d, ">f4", 3 * nk, A + vo).reshape(-1, 3).astype(np.float64)
                chans[ty] = dict(times=times, vals=vals, tflag=tf)
            if chans:
                tracks[self.names[t]] = chans
        res = dict(frames=fr, extra=extra, tracks=tracks)
        self._cache[m] = res
        return res


def load_bank(d):
    """The attacker motion bank (#AMM, chunk 3) of a moveset."""
    return AMM(d, amb_entries(d)[3][0])


def sample(ch, f, angle=False):
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


def _rx(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def _ry(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def _rz(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def euler_mat(v):
    return _rz(v[2] * ANG) @ _ry(v[1] * ANG) @ _rx(v[0] * ANG)


def quat_mat(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


# ----------------------------------------------------------------------------- #AMO skeleton

def skeleton(d):
    a = d.find(b"#AMO")
    if a < 0:
        raise ValueError("no #AMO skeleton")
    u32 = lambda o: struct.unpack_from(">I", d, o)[0]  # noqa: E731
    n, h0, nm, g = u32(a + 0x10), u32(a + 0x14), a + u32(a + 0x24), a + u32(a + 0x30)
    h = a + h0
    bones = []
    for i in range(n):
        _idx, _xo, _ch, _sib, par = struct.unpack_from(">IIIII", d, h + 0x20 * i)
        name = d[nm + 0x20 * i:nm + 0x20 * i + 0x20].split(b"\0")[0].decode("latin1")
        gr = g + 0x20 + 0x50 * i
        bones.append(dict(name=name, parent=(par - h0) // 0x20 if par else -1,
                          q=np.array(struct.unpack_from(">4f", d, gr)), t=np.array(struct.unpack_from(">3f", d, gr + 0x10)),
                          s=np.array(struct.unpack_from(">3f", d, gr + 0x20))))
    return bones


def local_pose(sk, nm, f):
    tr = nm["tracks"] if nm else {}
    Rl, Tl = [], []
    for b in sk:
        R, T = quat_mat(b["q"]), b["t"].astype(np.float64)
        ch = tr.get(b["name"])
        if ch:
            if 0 in ch:
                R = euler_mat(sample(ch[0], f, True))
            if 1 in ch:
                T = T + sample(ch[1], f)
        Rl.append(R)
        Tl.append(T)
    return Rl, Tl


def world_pose(sk, Rl, Tl):
    WR, WT = [None] * len(sk), [None] * len(sk)
    for i, b in enumerate(sk):
        p = b["parent"]
        if p < 0:
            WR[i], WT[i] = Rl[i], Tl[i]
        else:
            WR[i] = WR[p] @ Rl[i]
            WT[i] = WR[p] @ Tl[i] + WT[p]
    return WR, WT


# ----------------------------------------------------------------------------- #BSK / #BCM

REC = ">HHIfIIIiiiIII"


class BSK:
    def __init__(self, c):
        hdr = struct.unpack_from(">8I", c, 0)
        nsl, stab, nhit, htab = hdr[4], hdr[5], hdr[6], hdr[7]
        self.recs, self.hits = {}, []
        for s in range(nsl):
            o = struct.unpack_from(">I", c, stab + 4 * s)[0]
            if not o:
                continue
            r = list(struct.unpack_from(REC, c, o))
            chans = []
            for j in range(r[11]):
                t, n, ko = struct.unpack_from(">HHI", c, r[12] + 8 * j)
                chans.append((t, [bytes(c[ko + 20 * k:ko + 20 * k + 20]) for k in range(n)]))
            self.recs[s] = (r, chans)
        self.hits = [bytes(c[htab + 0xA0 * i:htab + 0xA0 * (i + 1)]) for i in range(nhit)]


def key_fields(t, raw):
    fr, seq, kind = struct.unpack_from(">HBB", raw, 0)
    if t == 1:
        p = struct.unpack_from(">IHBBhhhh", raw, 4)
    elif t in (3, 4):
        p = struct.unpack_from(">fIII", raw, 4)
    else:
        p = struct.unpack_from(">IIII", raw, 4)
    return fr, seq, kind, p


def hitkeys(bsk, slot):
    out = []
    for t, keys in bsk.recs[slot][1]:
        if t != 1:
            continue
        for raw in keys:
            fr, seq, kind, p = key_fields(1, raw)
            hid, cls, bone, size, x, y, z, w = p
            out.append(dict(fr=fr, seq=seq, kind=kind, hit=hid, cls=cls, bone=bone, size=size, x=x, y=y, z=z))
    return out


class BCMRec:
    def __init__(self, f):
        self.f = f
        self.links = []

    @property
    def slots(self):
        return self.f[10:14]


BCM_HEAD = ">HHhhIHHHhIHHHHI10H8B"


def bcm_root(c):
    _cnt, first = struct.unpack_from(">II", c, 4)
    cache = {}

    def rec(off):
        if off in cache:
            return cache[off]
        f = struct.unpack_from(BCM_HEAD, c, off)
        r = BCMRec(f)
        cache[off] = r
        r.links = [rec(o) for o in struct.unpack_from(">%dI" % f[6], c, off + 0x40)]
        return r
    return rec(first)
