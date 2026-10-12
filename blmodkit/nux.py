"""Reader / writer / builder for Dragon Ball Z: Burst Limit (Xbox 360) .NUX model files.

(Part of blmodkit; the format notes are in docs/NUX_FORMAT.md.)

Layout (all big-endian; full description in docs/NUX_FORMAT.md):

    0x000  #NUX header (0x40): magic, header size, offset of #NUB, NUT name[26], NUB name[26]
    0x040  NTXR texture block (Smash-4 NUT layout) + 0xCD filler + zero pad to 0x40
    nub    #NUB header (0x20) + (offset, index) table, zero pad to 0x40
           NDXR blocks (Smash-4 NUD layout), each 0x40 aligned; file ends 0x40 aligned

    nux = parse(open(p, 'rb').read());  data = serialize(nux)
    serialize(parse(f)) == f byte for byte (tested on every character NUX of the game).

Vertex data is kept as numpy *structured big-endian arrays* (Polygon.vs / Polygon.va), so the
round trip is lossless; Polygon accessors return plain arrays. build_ndxr() makes an NDXR from
plain arrays and recomputes every offset, size, bounding sphere and padding.
"""
import struct

import numpy as np

# ----------------------------------------------------------------------------- small helpers


def _u32(d, o):
    return struct.unpack_from(">I", d, o)[0]


def _u16(d, o):
    return struct.unpack_from(">H", d, o)[0]


def _align(x, a):
    return (x + a - 1) // a * a


def _cstr(b):
    return b.split(b"\0", 1)[0].decode("latin1")


RESTART = 0xFFFF
PRIM_LIST = 0x40          # polygon +0x22 bit: triangle list (never used by the game files)

# ----------------------------------------------------------------------------- vertex formats
# vertex size byte (poly +0x0E): high nibble = bone type, low nibble = normal type
# uv size byte    (poly +0x0F): high nibble = UV count, low nibble = colour type
# bone type 0 -> everything in the vertex stream (vs); otherwise position/normal/bones live in
# the "vertex add" stream (va) and the vs stream holds only colour + UVs.


def _posnrm_fields(ntype, skinned):
    if ntype == 1:            # f32 pos xyzw + f32 normal xyzw
        return [("pos", ">f4", (4,)), ("nrm", ">f4", (4,))]
    if ntype == 6:            # f32 pos xyz + f16 normal xyzw
        return [("pos", ">f4", (3,)), ("nrm", ">f2", (4,))]
    if ntype == 0:
        return [("pos", ">f4", (4,) if skinned else (3,))]
    if ntype == 7:            # f32 pos + f16 normal/bitangent/tangent
        return [("pos", ">f4", (3,)), ("nrm", ">f2", (4,)), ("bitan", ">f2", (4,)), ("tan", ">f2", (4,))]
    if ntype == 3:
        return [("pos", ">f4", (4,)), ("nrm", ">f4", (4,)), ("bitan", ">f4", (4,)), ("tan", ">f4", (4,))]
    raise ValueError("unsupported normal type %d" % ntype)


def _bone_fields(btype):
    if btype == 0:
        return []
    if btype == 1:            # u32 x4 bone index + f32 x4 weight
        return [("bone", ">u4", (4,)), ("weight", ">f4", (4,))]
    if btype == 2:
        return [("bone", ">u2", (4,)), ("weight", ">f2", (4,))]
    if btype == 4:
        return [("bone", "u1", (4,)), ("weight", "u1", (4,))]
    raise ValueError("unsupported bone type %d" % btype)


def _uv_fields(uvsz):
    ctype, nuv = uvsz & 0xF, uvsz >> 4
    f = []
    if ctype == 2:
        f.append(("color", "u1", (4,)))
    elif ctype == 4:
        f.append(("color", ">f2", (4,)))
    elif ctype != 0:
        raise ValueError("unsupported colour type %d" % ctype)
    if nuv:
        f.append(("uv", ">f2", (nuv, 2)))
    return f


def vertex_dtypes(vsz, uvsz):
    """-> (dtype of vs stream, dtype of va stream or None)."""
    btype, ntype = vsz >> 4, vsz & 0xF
    if btype == 0:
        return np.dtype(_posnrm_fields(ntype, False) + _uv_fields(uvsz)), None
    return np.dtype(_uv_fields(uvsz)), np.dtype(_posnrm_fields(ntype, True) + _bone_fields(btype))


# ----------------------------------------------------------------------------- strips


def strip_to_tris(idx):
    """Triangle strip with 0xFFFF restarts -> list of (a,b,c) (degenerates dropped).
    Parity flips on every strip position (degenerates included) and resets at a restart."""
    tris = []
    a = b = None
    flip = False
    for c in (int(x) for x in idx):
        if c == RESTART:
            a = b = None
            flip = False
            continue
        if a is not None:
            if a != b and b != c and a != c:
                tris.append((b, a, c) if flip else (a, b, c))
            flip = not flip
        a, b = b, c
    return tris


def list_to_tris(idx):
    idx = [int(x) for x in idx]
    return [tuple(idx[k:k + 3]) for k in range(0, len(idx) - 2, 3)
            if len(set(idx[k:k + 3])) == 3]


def canon_tris(tris):
    """Rotation-normalised, sorted oriented triangles (for set comparison, winding kept)."""
    out = []
    for t in tris:
        t = tuple(int(x) for x in t)
        k = t.index(min(t))
        out.append(t[k:] + t[:k])
    return sorted(out)


def tris_to_strips(tris, mode="strip"):
    """Oriented triangles -> uint16 index list that strip_to_tris() decodes back to the same
    oriented triangles.  Like every game index buffer, each strip (the last one too) is
    terminated by 0xFFFF.  mode 'restart': each triangle is its own 3-index strip (a pattern
    the game files use a lot); mode 'strip': greedy stripifier."""
    tris = [tuple(int(x) for x in t) for t in tris if len(set(int(x) for x in t)) == 3]
    if mode == "restart":
        out = []
        for t in tris:
            out.extend(t)
            out.append(RESTART)
        return np.array(out, np.uint16)
    edge = {}
    for i, (a, b, c) in enumerate(tris):
        for e, o in (((a, b), c), ((b, c), a), ((c, a), b)):
            edge.setdefault(e, []).append((i, o))
    used = np.zeros(len(tris), bool)

    def extend(s, local):
        # s: current strip (list); next triangle index k = len(s)-2
        while True:
            x, y = s[-2], s[-1]
            want = (x, y) if (len(s) - 2) % 2 == 0 else (y, x)
            nxt = None
            for i, o in edge.get(want, ()):
                if not used[i] and i not in local:
                    nxt = (i, o)
                    break
            if nxt is None:
                return
            local.add(nxt[0])
            s.append(nxt[1])

    out = []
    for i, (a, b, c) in enumerate(tris):
        if used[i]:
            continue
        best = None
        for s0 in ((a, b, c), (b, c, a), (c, a, b)):
            s, local = list(s0), {i}
            extend(s, local)
            if best is None or len(s) > len(best[0]):
                best = (s, local)
        for j in best[1]:
            used[j] = True
        out.extend(best[0])
        out.append(RESTART)
    return np.array(out, np.uint16)


# ----------------------------------------------------------------------------- materials


class Material:
    """0x20-byte header + 0x18 per texture + property chain (all game files: one empty 0x10
    property).  Header: +0 flags u32, +8 src blend u16, +0xA texture count u16 (computed),
    +0xC dst blend u16, +0x10 u16, +0x12 cull u16, rest 0. Texture entry: +0 texture id (GIDX),
    +0xC wrapS, wrapT, min, mag, +0x10 mip detail."""

    def __init__(self, header, textures, props):
        self.header = bytearray(header)          # 0x20 bytes (count field rewritten on write)
        self.textures = [[tid, bytearray(raw)] for tid, raw in textures]  # raw = 0x14 bytes after id
        self.props = bytes(props)

    @property
    def flags(self):
        return _u32(self.header, 0)

    @flags.setter
    def flags(self, v):
        struct.pack_into(">I", self.header, 0, v)

    @property
    def texture_ids(self):
        return [t[0] for t in self.textures]

    @classmethod
    def parse(cls, d, o):
        hdr = d[o:o + 0x20]
        tc = _u16(d, o + 0xA)
        tex = []
        q = o + 0x20
        for _ in range(tc):
            tex.append((_u32(d, q), d[q + 4:q + 0x18]))
            q += 0x18
        p0 = q
        while True:
            sz = _u32(d, q)
            if sz == 0:
                q += 0x10 + 4 * d[q + 0xB]
                break
            q += sz
        return cls(hdr, tex, d[p0:q])

    @classmethod
    def from_bytes(cls, b):
        return cls.parse(bytes(b), 0)

    def to_bytes(self):
        h = bytearray(self.header)
        struct.pack_into(">H", h, 0xA, len(self.textures))
        out = bytearray(h)
        for tid, raw in self.textures:
            out += struct.pack(">I", tid) + bytes(raw)
        out += self.props
        return bytes(out)

    def __eq__(self, other):
        return isinstance(other, Material) and self.to_bytes() == other.to_bytes()

    def __hash__(self):
        return hash(self.to_bytes())

    def __repr__(self):
        return "Material(flags=%08x tex=%s)" % (self.flags, [hex(t) for t in self.texture_ids])


# ----------------------------------------------------------------------------- polygon / mesh / ndxr


class Polygon:
    """One draw call.  vs / va: structured numpy arrays (see vertex_dtypes).  indices: uint16
    strip (0xFFFF restarts) or list (prim & 0x40).  materials: 1..4 Material (passes)."""

    def __init__(self, vsz, uvsz, vs, va, indices, materials, prim=0, pflag=0):
        self.vsz, self.uvsz = vsz, uvsz
        self.vs, self.va = vs, va
        self.indices = np.asarray(indices, np.uint16)
        self.materials = list(materials)
        self.prim, self.pflag = prim, pflag

    # -- accessors (copies, native dtypes)
    @property
    def nverts(self):
        return len(self.vs)

    @property
    def _geo(self):
        return self.va if self.va is not None else self.vs

    def positions(self):
        return np.array(self._geo["pos"][:, :3], np.float32)

    def normals(self):
        g = self._geo
        return np.array(g["nrm"][:, :3], np.float32) if "nrm" in g.dtype.names else None

    def bone_idx(self):
        g = self._geo
        return np.array(g["bone"], np.int64) if "bone" in g.dtype.names else None

    def weights(self):
        g = self._geo
        return np.array(g["weight"], np.float32) if "weight" in g.dtype.names else None

    def colors(self):
        return np.array(self.vs["color"]) if "color" in self.vs.dtype.names else None

    def uvs(self, channel=0):
        return np.array(self.vs["uv"][:, channel], np.float32) if "uv" in self.vs.dtype.names else None

    def triangles(self):
        t = list_to_tris(self.indices) if self.prim & PRIM_LIST else strip_to_tris(self.indices)
        return np.array(t, np.int64).reshape(-1, 3)


class Mesh:
    """0x30 header: +0 sphere (cx,cy,cz,r) +0x10 sphere2 (cx,cy,cz,0) +0x20 name offset,
    +0x24 bone flag (4 skinned / 0), +0x28 single-bind bone s16, +0x2A polygon count,
    +0x2C offset of its first polygon header."""

    def __init__(self, name, polygons, bone_flag=0, singlebind=-1, sphere=None, sphere2=None):
        self.name = name
        self.polygons = list(polygons)
        self.bone_flag = bone_flag
        self.singlebind = singlebind
        self.sphere = sphere          # None -> computed on write
        self.sphere2 = sphere2


class Ndxr:
    """NDXR header 0x30: magic, size, version 0x0200, mesh count, type (1 skinned / 0),
    bone count (skeleton - 2 for skinned files, 0 otherwise), +0x10 offset of the index buffer
    minus 0x30, +0x14/+0x18/+0x1C index / vertex / vertex-add buffer sizes, +0x20 sphere."""

    def __init__(self, meshes, nbones=0, type_=None, sphere=None, version=0x0200):
        self.meshes = list(meshes)
        self.nbones = nbones
        self.type = type_ if type_ is not None else (1 if nbones else 0)
        self.sphere = sphere
        self.version = version

    @property
    def polygons(self):
        return [p for m in self.meshes for p in m.polygons]


def compute_center(ndxr):
    """Bounding-sphere centre exactly as the game files have it: float32 running sum of the
    positions of every vertex of every polygon (in file order) divided by the vertex count."""
    s = np.zeros(3, np.float32)
    n = 0
    for p in ndxr.polygons:
        pos = p.positions()
        for v in pos:          # sequential float32 accumulation (bit exact)
            s = (s + v).astype(np.float32)
        n += len(pos)
    return (s / np.float32(max(n, 1))).astype(np.float32)


def _compute_center_fast(ndxr):
    pos = [p.positions() for p in ndxr.polygons]
    if not pos:
        return np.zeros(3, np.float32)
    P = np.concatenate(pos).astype(np.float32)
    s = np.zeros(3, np.float32)
    # np.add.accumulate on float32 is sequential (no pairwise summation)
    s = np.add.accumulate(P, axis=0, dtype=np.float32)[-1]
    return (s / np.float32(len(P))).astype(np.float32)


def parse_ndxr(d, b):
    assert d[b:b + 4] == b"NDXR", hex(b)
    size = _u32(d, b + 4)
    version, nmesh, typ, nbones = struct.unpack_from(">HHHH", d, b + 8)
    ics_rel, isz, vsz_, vasz = struct.unpack_from(">IIII", d, b + 0x10)
    sphere = struct.unpack_from(">4f", d, b + 0x20)
    ics = b + 0x30 + ics_rel
    vcs = ics + isz
    vas = vcs + vsz_
    names = vas + vasz
    o = b + 0x30
    mh = []
    for _ in range(nmesh):
        mh.append(d[o:o + 0x30])
        o += 0x30
    meshes = []
    mat_cache = {}
    for h in mh:
        npoly = _u16(h, 0x2A)
        assert _u32(h, 0x2C) == o - b, "mesh poly offset"
        assert h[0x24:0x28] in (b"\0\0\0\0", b"\0\0\0\4", b"\0\0\0\x08"), h[0x24:0x28].hex()
        polys = []
        for _ in range(npoly):
            ph = d[o:o + 0x30]
            ps, vs, va = struct.unpack_from(">III", ph, 0)
            vc = _u16(ph, 0xC)
            pvsz, puvsz = ph[0xE], ph[0xF]
            tp = struct.unpack_from(">4I", ph, 0x10)
            ic = _u16(ph, 0x20)
            prim, pflag = ph[0x22], ph[0x23]
            assert ph[0x24:0x30] == bytes(12), "poly tail"
            dvs, dva = vertex_dtypes(pvsz, puvsz)
            vsa = np.frombuffer(d, dvs, vc, vcs + vs).copy()
            vaa = np.frombuffer(d, dva, vc, vas + va).copy() if dva is not None else None
            if dva is None:
                assert va == 0
            idx = np.frombuffer(d, ">u2", ic, ics + ps).astype(np.uint16)
            mats = []
            for t in tp:
                if not t:
                    continue
                if t not in mat_cache:
                    mat_cache[t] = Material.parse(d, b + t)
                mats.append(mat_cache[t])
            assert all(tp[:len(mats)]) and not any(tp[len(mats):]), "material slots"
            polys.append(Polygon(pvsz, puvsz, vsa, vaa, idx, mats, prim, pflag))
            o += 0x30
        name = _cstr(d[names + _u32(h, 0x20):names + _u32(h, 0x20) + 256])
        meshes.append(Mesh(name, polys, _u32(h, 0x24), struct.unpack_from(">h", h, 0x28)[0],
                           struct.unpack_from(">4f", h, 0), struct.unpack_from(">4f", h, 0x10)))
    n = Ndxr(meshes, nbones, typ, sphere, version)
    n._raw_size = size
    return n


def serialize_ndxr(n, compute_sphere=False, radius=None):
    """NDXR -> bytes.  Layout (verified on all game files):
    header 0x30 | mesh headers | polygon headers (mesh order) | materials (first use order,
    shared objects written once), zero pad to 0x10 | index buffer (polygon order) | vertex
    buffer (polygon order) | vertex-add buffer | names (each NUL-terminated); each of the four
    sections is zero padded to 0x10.  No padding between polygons inside a buffer."""
    meshes = n.meshes
    polys = n.polygons
    if compute_sphere or n.sphere is None:
        c = _compute_center_fast(n)
        r = radius if radius is not None else (n.sphere[3] if n.sphere is not None else 3000.0)
        n.sphere = (float(c[0]), float(c[1]), float(c[2]), float(r))
        for m in meshes:
            m.sphere = n.sphere
            m.sphere2 = (n.sphere[0], n.sphere[1], n.sphere[2], 0.0)
    hdr_end = 0x30 + 0x30 * len(meshes) + 0x30 * len(polys)
    # materials
    mat_off = {}
    mat_blob = bytearray()
    for p in polys:
        for m in p.materials:
            if id(m) not in mat_off:
                mat_off[id(m)] = hdr_end + len(mat_blob)
                mat_blob += m.to_bytes()
    mats_end = _align(hdr_end + len(mat_blob), 0x10)
    mat_blob += bytes(mats_end - hdr_end - len(mat_blob))
    # buffers
    ib = bytearray()
    vb = bytearray()
    vab = bytearray()
    pinfo = []
    for p in polys:
        ps, vs, va = len(ib), len(vb), (len(vab) if p.va is not None else 0)
        ib += np.asarray(p.indices, ">u2").tobytes()
        vb += p.vs.tobytes()
        if p.va is not None:
            assert len(p.va) == len(p.vs)
            vab += p.va.tobytes()
        pinfo.append((ps, vs, va))
    ib += bytes(_align(len(ib), 0x10) - len(ib))
    vb += bytes(_align(len(vb), 0x10) - len(vb))
    vab += bytes(_align(len(vab), 0x10) - len(vab))
    # names
    nb = bytearray()
    name_off = []
    for m in meshes:
        name_off.append(len(nb))
        nb += m.name.encode("latin1") + b"\0"
    nb += bytes(_align(len(nb), 0x10) - len(nb))
    total = mats_end + len(ib) + len(vb) + len(vab) + len(nb)
    out = bytearray()
    out += b"NDXR" + struct.pack(">IHHHH", total, n.version, len(meshes), n.type, n.nbones)
    out += struct.pack(">IIII", mats_end - 0x30, len(ib), len(vb), len(vab))
    out += struct.pack(">4f", *n.sphere)
    pi = 0
    poff = 0x30 + 0x30 * len(meshes)
    for mi, m in enumerate(meshes):
        s1 = m.sphere if m.sphere is not None else n.sphere
        s2 = m.sphere2 if m.sphere2 is not None else (s1[0], s1[1], s1[2], 0.0)
        out += struct.pack(">4f", *s1) + struct.pack(">4f", *s2)
        out += struct.pack(">IIhHI", name_off[mi], m.bone_flag, m.singlebind, len(m.polygons), poff)
        poff += 0x30 * len(m.polygons)
    for p, (ps, vs, va) in zip(polys, pinfo):
        tp = [mat_off[id(m)] for m in p.materials] + [0] * (4 - len(p.materials))
        out += struct.pack(">IIIHBB", ps, vs, va, p.nverts, p.vsz, p.uvsz)
        out += struct.pack(">4I", *tp)
        out += struct.pack(">HBB", len(p.indices), p.prim, p.pflag) + bytes(12)
    out += mat_blob
    assert len(out) == mats_end
    out += ib + vb + vab + nb
    assert len(out) == total
    return bytes(out)


# ----------------------------------------------------------------------------- textures


class Texture:
    """NTXR entry: 0x30 header (+0 total, +8 data size, +0xC header size, +0x10 mips,
    +0x12 format 0=DXT1 1=DXT3 2=DXT5, +0x14 w, +0x16 h), mip size table (mips > 1 only,
    u32 per level, padded to 0x10 with 0xCD), eXt chunk (0x10), GIDX chunk (0x10, id at +8),
    then the linear DXT data (16-bit big-endian words)."""

    def __init__(self, fmt, width, height, mips, gidx, data, mip_sizes=None, ext=None, misc=None):
        self.fmt, self.width, self.height, self.mips = fmt, width, height, mips
        self.gidx = gidx
        self.data = bytes(data)
        self.mip_sizes = list(mip_sizes) if mip_sizes else None
        self.ext = ext if ext is not None else bytes.fromhex("65587400000000200000001000000000")
        self.misc = misc or {}

    @staticmethod
    def level_sizes(fmt, w, h, mips):
        bs = 8 if fmt == 0 else 16
        return [max(1, (w >> k) // 4) * max(1, (h >> k) // 4) * bs for k in range(mips)]

    @classmethod
    def parse(cls, d, pos):
        total, z04, ds, hs, z0e, mips, fmt, w, h = struct.unpack_from(">IIIHHHHHH", d, pos)
        r18 = d[pos + 0x18:pos + 0x30]
        q = pos + 0x30
        mip_sizes, mip_pad = None, b""
        if mips > 1:
            mip_sizes = list(struct.unpack_from(">%dI" % mips, d, q))
            q2 = q + 4 * mips
            q = _align(q2, 0x10)
            mip_pad = d[q2:q]
        ext = d[q:q + 0x10]
        gidx_hdr = d[q + 0x10:q + 0x18]
        gidx = _u32(d, q + 0x18)
        gidx_tail = d[q + 0x1C:q + 0x20]
        assert gidx_hdr == b"GIDX\0\0\0\x10", gidx_hdr
        assert q + 0x20 - pos == hs and total == hs + ds
        misc = dict(z04=z04, z0e=z0e, r18=r18, mip_pad=mip_pad, gidx_tail=gidx_tail)
        t = cls(fmt, w, h, mips, gidx, d[pos + hs:pos + hs + ds], mip_sizes, ext, misc)
        return t, total

    def to_bytes(self):
        m = self.misc
        mt = b""
        if self.mips > 1:
            sizes = self.mip_sizes or self.level_sizes(self.fmt, self.width, self.height, self.mips)
            mt = struct.pack(">%dI" % self.mips, *sizes)
            pad = _align(len(mt), 0x10) - len(mt)
            mp = m.get("mip_pad")
            mt += mp if mp is not None and len(mp) == pad else b"\xcd" * pad
        hs = 0x30 + len(mt) + 0x20
        h = struct.pack(">IIIHHHHHH", hs + len(self.data), m.get("z04", 0), len(self.data), hs,
                        m.get("z0e", 0), self.mips, self.fmt, self.width, self.height)
        h += m.get("r18", bytes(24))
        h += mt + self.ext + b"GIDX\0\0\0\x10" + struct.pack(">I", self.gidx) + m.get("gidx_tail", bytes(4))
        return h + self.data

    def decode(self, level=0):
        """-> h x w x 4 uint8 RGBA of one mip level."""
        from .ntxr import decode_dxt as decode
        sizes = self.mip_sizes or self.level_sizes(self.fmt, self.width, self.height, self.mips)
        off = sum(sizes[:level])
        return decode(self.fmt, max(1, self.width >> level), max(1, self.height >> level),
                      self.data[off:off + sizes[level]])


# ----------------------------------------------------------------------------- container


class NuxFile:
    def __init__(self):
        self.nut_name = ""         # e.g. BCGOK010_B.NUT  (header +0x0C, 26 bytes)
        self.nub_name = ""         # e.g. BCGOK010.NUB    (header +0x26, 26 bytes)
        self.nut_name_pad = None   # raw bytes after the NUL (None = zeros); MODEL files hold 0xFD junk
        self.nub_name_pad = None
        self.header_size = 0x40
        self.ntxr_version = 0x0100
        self.textures = []
        self.ntxr_cd = 20          # 0xCD filler bytes after the last texture (16/20/24 in game files)
        self.nub_flags = (2, 2)    # #NUB +0x10 two u16: (2,2) character files, (1,0) effect MODEL files
        self.nub_tag = b"\0NUD"
        self.models = []           # [(index, Ndxr)]; index 0xFF = body, 0..n = Face00.. expression id

    # convenience
    def model(self, index):
        for i, n in self.models:
            if i == index:
                return n
        raise KeyError(index)

    def texture_index(self, gidx):
        for i, t in enumerate(self.textures):
            if t.gidx == gidx:
                return i
        return -1


def _name_field(name, pad):
    b = name.encode("latin1") + b"\0"
    assert len(b) <= 26, name
    if pad is not None and len(b) + len(pad) == 26:
        return b + pad
    return b + bytes(26 - len(b))


def parse(d):
    d = bytes(d)
    assert d[:4] == b"#NUX"
    f = NuxFile()
    f.header_size = _u32(d, 4)
    nub = _u32(d, 8)
    for key, lo in (("nut", 0x0C), ("nub", 0x26)):
        raw = d[lo:lo + 26]
        name = _cstr(raw)
        pad = raw[len(name) + 1:]
        setattr(f, key + "_name", name)
        setattr(f, key + "_name_pad", None if pad == bytes(len(pad)) else pad)
    p = f.header_size
    assert d[p:p + 4] == b"NTXR"
    f.ntxr_version, cnt = struct.unpack_from(">HH", d, p + 4)
    assert d[p + 8:p + 0x10] == bytes(8)
    pos = p + 0x10
    for _ in range(cnt):
        t, total = Texture.parse(d, pos)
        f.textures.append(t)
        pos += total
    tail = d[pos:nub]
    f.ntxr_cd = len(tail) - len(tail.lstrip(b"\xcd"))
    assert tail[f.ntxr_cd:] == bytes(len(tail) - f.ntxr_cd), "NTXR tail"
    assert d[nub:nub + 4] == b"#NUB"
    nubsize, count, first = struct.unpack_from(">III", d, nub + 4)
    f.nub_flags = struct.unpack_from(">HH", d, nub + 0x10)
    f.nub_tag = d[nub + 0x14:nub + 0x18]
    assert d[nub + 0x18:nub + 0x20] == bytes(8)
    assert nub + nubsize == len(d)
    for k in range(count):
        off, idx = struct.unpack_from(">II", d, nub + 0x20 + 8 * k)
        f.models.append((idx, parse_ndxr(d, nub + first + off)))
    return f


def serialize(f):
    out = bytearray(b"#NUX")
    out += struct.pack(">I", f.header_size)
    tex = bytearray(b"NTXR" + struct.pack(">HH", f.ntxr_version, len(f.textures)) + bytes(8))
    for t in f.textures:
        tex += t.to_bytes()
    tex += b"\xcd" * f.ntxr_cd
    nub = _align(f.header_size + len(tex), 0x40)
    out += struct.pack(">I", nub)
    out += _name_field(f.nut_name, f.nut_name_pad) + _name_field(f.nub_name, f.nub_name_pad)
    out += bytes(f.header_size - len(out))
    out += tex
    out += bytes(nub - len(out))
    count = len(f.models)
    first = _align(0x20 + 8 * count, 0x40)
    blobs = []
    rel = 0
    table = bytearray()
    for idx, n in f.models:
        b = serialize_ndxr(n)
        table += struct.pack(">II", rel, idx)
        blobs.append(b + bytes(_align(len(b), 0x40) - len(b)))
        rel += len(blobs[-1])
    body = b"".join(blobs)
    hdr = b"#NUB" + struct.pack(">III", first + len(body), count, first)
    hdr += struct.pack(">HH", *f.nub_flags) + f.nub_tag + bytes(8) + table
    hdr += bytes(first - len(hdr))
    out += hdr + body
    return bytes(out)


def load(path):
    with open(path, "rb") as fh:
        return parse(fh.read())


def save(f, path):
    data = serialize(f)
    with open(path, "wb") as fh:
        fh.write(data)
    return len(data)


# ----------------------------------------------------------------------------- builder

BODY_FMT = (0x11, 0x12)   # skinned: va 64 B (pos f32x4, nrm f32x4, u32x4 bones, f32x4 weights); vs 8 B (RGBA8, half2 UV)
FACE_FMT = (0x06, 0x12)   # rigid:   vs 28 B (pos f32x3, nrm f16x4, RGBA8, half2 UV)


def build_polygon(vertices, triangles=None, normals=None, bone_idx=None, weights=None, colors=None,
                  uv=None, material=None, materials=None, vertex_format=None, uv_format=0x12,
                  indices=None, prim="strip", pflag=None, pos_w=1.0, nrm_w=None):
    """Plain arrays -> Polygon.  vertices Nx3, normals Nx3, bone_idx Nx4 (int), weights Nx4,
    colors Nx4 uint8, uv Nx2 (or N x k x 2), triangles Mx3 (oriented, as decoded).
    prim: 'strip' (greedy strips + 0xFFFF restarts, the game's format), 'restart'
    (one strip per triangle), 'list' (flag 0x40, never used by the game files) or give raw
    `indices` (+ prim int) to keep an existing strip.  material(s): Material or raw bytes."""
    V = np.asarray(vertices, np.float32).reshape(-1, 3)
    n = len(V)
    if vertex_format is None:
        vertex_format = 0x11 if bone_idx is not None else 0x06
    if pflag is None:
        pflag = 0x04 if (vertex_format >> 4) else 0x00
    dvs, dva = vertex_dtypes(vertex_format, uv_format)
    vs = np.zeros(n, dvs)
    va = np.zeros(n, dva) if dva is not None else None
    g = va if va is not None else vs
    pos = np.zeros((n, g.dtype["pos"].shape[0]), np.float32)
    pos[:, :3] = V
    if pos.shape[1] == 4:
        pos[:, 3] = pos_w
    g["pos"] = pos
    if "nrm" in g.dtype.names:
        N = np.zeros((n, 4), np.float32)
        if normals is not None:
            N[:, :3] = np.asarray(normals, np.float32).reshape(-1, 3)
        if nrm_w is None:
            nrm_w = 1.0
        N[:, 3] = nrm_w
        g["nrm"] = N
    if "bone" in g.dtype.names:
        assert bone_idx is not None and weights is not None, "skinned format needs bone_idx + weights"
        g["bone"] = np.asarray(bone_idx).reshape(-1, 4)
        g["weight"] = np.asarray(weights, np.float32).reshape(-1, 4)
    if "color" in vs.dtype.names:
        vs["color"] = np.asarray(colors, np.uint8).reshape(-1, 4) if colors is not None else 255
    if "uv" in vs.dtype.names and uv is not None:
        U = np.asarray(uv, np.float32)
        vs["uv"] = U.reshape(n, -1, 2)
    if indices is not None:
        idx = np.asarray(indices, np.uint16)
        primv = prim if isinstance(prim, int) else 0
    else:
        T = np.asarray(triangles, np.int64).reshape(-1, 3)
        assert len(T) == 0 or (T.min() >= 0 and T.max() < n and n <= 0xFFFF)
        if prim == "list":
            idx, primv = T.astype(np.uint16).reshape(-1), PRIM_LIST
        else:
            idx, primv = tris_to_strips(T, prim), 0
    mats = materials if materials is not None else [material]
    mats = [m if isinstance(m, Material) else Material.from_bytes(m) for m in mats]
    assert 1 <= len(mats) <= 4
    return Polygon(vertex_format, uv_format, vs, va, idx, mats, primv, pflag)


def build_ndxr(meshes, nbones=0, radius=3000.0, type_=None):
    """meshes = [{name, singlebind=-1, bone_flag=None, polygons=[Polygon or dict for
    build_polygon]}] -> Ndxr with every offset, size, sphere and pad computed on serialize.
    radius: the character files always store 3000.0; the centre is computed (bit exact)."""
    out = []
    skinned = False
    for m in meshes:
        polys = []
        for p in m["polygons"]:
            if isinstance(p, dict):
                p = build_polygon(**p)
            polys.append(p)
            skinned |= bool(p.vsz >> 4)
        bf = m.get("bone_flag")
        if bf is None:
            bf = 4 if any(p.vsz >> 4 for p in polys) else 0
        out.append(Mesh(m["name"], polys, bf, m.get("singlebind", -1)))
    n = Ndxr(out, nbones if skinned else 0, type_ if type_ is not None else (1 if skinned else 0))
    c = _compute_center_fast(n)
    n.sphere = (float(c[0]), float(c[1]), float(c[2]), float(radius))
    for m in n.meshes:
        m.sphere = n.sphere
        m.sphere2 = (n.sphere[0], n.sphere[1], n.sphere[2], 0.0)
    return n


def recompute_spheres(n, radius=None):
    c = _compute_center_fast(n)
    r = radius if radius is not None else (n.sphere[3] if n.sphere else 3000.0)
    n.sphere = (float(c[0]), float(c[1]), float(c[2]), float(r))
    for m in n.meshes:
        m.sphere = n.sphere
        m.sphere2 = (n.sphere[0], n.sphere[1], n.sphere[2], 0.0)


# ----------------------------------------------------------------------------- port helpers


def body(f):
    """The skinned body NDXR (table index 0xFF)."""
    return f.model(0xFF)


def replace_body(f, polygons, mesh_name=None, nbones=None, radius=None):
    """Replace every polygon of the body NDXR (index 0xFF) with `polygons` (Polygon objects or
    build_polygon kwargs).  Keeps the mesh name (XGOK_NW), bone count and radius unless given."""
    old = body(f)
    polys = [build_polygon(**p) if isinstance(p, dict) else p for p in polygons]
    m0 = old.meshes[0]
    new = build_ndxr([dict(name=mesh_name or m0.name, singlebind=m0.singlebind, bone_flag=m0.bone_flag,
                           polygons=polys)],
                     nbones=old.nbones if nbones is None else nbones,
                     radius=old.sphere[3] if radius is None else radius, type_=old.type)
    f.models = [(i, new if i == 0xFF else n) for i, n in f.models]
    return new


def empty_faces(f, mode="degenerate"):
    """Make every expression model (NUB index != 0xFF) draw nothing while keeping the NDXR,
    its mesh + name, its NUB index, the vertex format, material and bounding sphere.
      'degenerate' (recommended): each mesh keeps ONE polygon (its first, with its material)
                   of 3 copies of vertex 0 and the strip [0,1,2,0xFFFF]: one zero-area
                   triangle, nothing rasterised.  Every game polygon has >= 4 vertices and
                   >= 6 indices; this stays within the same structure (1 polygon, 1 material).
      'collapse':  keep every polygon, move all of its vertices onto vertex 0.
      'zero':      polygon count 0 (no game file has a mesh without polygons; not tested)."""
    for i, n in f.models:
        if i == 0xFF:
            continue
        sphere = n.sphere
        for m in n.meshes:
            if mode == "zero":
                m.polygons = []
                continue
            if mode == "collapse":
                for p in m.polygons:
                    g = p.va if p.va is not None else p.vs
                    g["pos"][:] = g["pos"][0]
                continue
            p = m.polygons[0]
            keep = np.zeros(3, np.int64)
            p.vs = p.vs[keep].copy()
            if p.va is not None:
                p.va = p.va[keep].copy()
            p.indices = np.array([0, 1, 2, RESTART], np.uint16)
            p.prim = 0
            m.polygons = [p]
        n.sphere = sphere  # keep the original sphere


def _encode_with_mips(t, rgba):
    from .ntxr import encode_dxt as encode, mip_chain
    rgba = np.asarray(rgba, np.uint8)
    assert rgba.shape[:2] == (t.height, t.width) and rgba.shape[2] == 4, (rgba.shape, t.width, t.height)
    data = b"".join(encode(t.fmt, m) for m in mip_chain(rgba, max(1, t.mips)))
    return data


def set_texture(f, index, rgba, new_gidx=None):
    """Replace texture `index` (same size / format / mip count) with an RGBA image
    (h x w x 4 uint8).  new_gidx: give it a new texture id and update every material that
    referenced the old id (recommended: see data/nux_format.md, texture ids)."""
    t = f.textures[index]
    data = _encode_with_mips(t, rgba)
    assert len(data) == len(t.data), (len(data), len(t.data))
    t.data = data
    if new_gidx is not None:
        retarget_texture_id(f, t.gidx, new_gidx)
        t.gidx = new_gidx
    return t


def all_materials(f):
    seen = {}
    for _, n in f.models:
        for p in n.polygons:
            for m in p.materials:
                seen[id(m)] = m
    return list(seen.values())


def retarget_texture_id(f, old, new):
    k = 0
    for m in all_materials(f):
        for tt in m.textures:
            if tt[0] == old:
                tt[0] = new
                k += 1
    return k


def add_texture(f, rgba, fmt=2, mips=None, gidx=None, id_base=0x100100A0):
    """Append a new texture (DXT1 fmt 0 / DXT5 fmt 2) and return its GIDX id.
    Default id: first free id >= id_base (0x1001 CC NN: CC = character, 00 for Goku; ids
    0x10010001..0x10010049 are taken by Goku's game files, so the default starts at 0xA0)."""
    rgba = np.asarray(rgba, np.uint8)
    h, w = rgba.shape[:2]
    if mips is None:
        mips = 3 if min(w, h) >= 512 else 1
    if gidx is None:
        used = {t.gidx for t in f.textures}
        gidx = id_base
        while gidx in used:
            gidx += 1
    assert f.texture_index(gidx) < 0, "duplicate texture id"
    t = Texture(fmt, w, h, mips, gidx, b"", None)
    t.data = _encode_with_mips(t, rgba)
    if mips > 1:
        t.mip_sizes = Texture.level_sizes(fmt, w, h, mips)
        assert sum(t.mip_sizes) == len(t.data)
    f.textures.append(t)
    return gidx


def rename(f, code):
    """Set the internal names to <code>_B.NUT / <code>.NUB (e.g. 'BCGOK010').  Mesh names
    (XGOK_NW, BCGOK000_Face0N) are left alone: BCGOK010 itself keeps BCGOK000_Face0N."""
    f.nut_name = code + "_B.NUT"
    f.nub_name = code + ".NUB"
    f.nut_name_pad = f.nub_name_pad = None


def decode_polygon(p):
    """-> dict of plain arrays (what build_polygon takes back)."""
    return dict(vertices=p.positions(), normals=p.normals(), bone_idx=p.bone_idx(), weights=p.weights(),
                colors=p.colors(), uv=p.uvs(), triangles=p.triangles(), material=p.materials[0] if len(p.materials) == 1 else None,
                materials=p.materials, vertex_format=p.vsz, uv_format=p.uvsz)


def dump(f, prefix):
    """Write <prefix>.npz (per polygon arrays, keys m<idx>_p<k>_<field>) and <prefix>.json
    (textures, models, meshes, polygons with material bytes as hex)."""
    import json
    arrs = {}
    info = dict(nut_name=f.nut_name, nub_name=f.nub_name, ntxr_cd=f.ntxr_cd,
                textures=[dict(index=i, gidx="%08x" % t.gidx, fmt=t.fmt, w=t.width, h=t.height, mips=t.mips)
                          for i, t in enumerate(f.textures)], models=[])
    for idx, n in f.models:
        mi = dict(index=idx, nbones=n.nbones, type=n.type, sphere=list(n.sphere), meshes=[])
        k = 0
        for m in n.meshes:
            md = dict(name=m.name, bone_flag=m.bone_flag, singlebind=m.singlebind, polygons=[])
            for p in m.polygons:
                key = "m%d_p%d_" % (idx, k)
                for fld, v in decode_polygon(p).items():
                    if isinstance(v, np.ndarray):
                        arrs[key + fld] = v
                arrs[key + "indices"] = p.indices
                md["polygons"].append(dict(key=key, nverts=p.nverts, nindices=len(p.indices),
                                           ntris=int(len(p.triangles())), vsz=p.vsz, uvsz=p.uvsz,
                                           prim=p.prim, pflag=p.pflag,
                                           materials=[mm.to_bytes().hex() for mm in p.materials],
                                           texture_ids=["%08x" % t for t in p.materials[0].texture_ids]))
                k += 1
            mi["meshes"].append(md)
        info["models"].append(mi)
    np.savez_compressed(prefix + ".npz", **arrs)
    with open(prefix + ".json", "w") as fh:
        json.dump(info, fh, indent=1)


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 4 and sys.argv[1] == "dump":
        dump(load(sys.argv[2]), sys.argv[3])
        sys.exit(0)
    for path in sys.argv[1:]:
        f = load(path)
        print("==", path, f.nut_name, f.nub_name, "textures", len(f.textures))
        for i, t in enumerate(f.textures):
            print("  tex %2d id %08x fmt %d %dx%d mips %d" % (i, t.gidx, t.fmt, t.width, t.height, t.mips))
        for idx, n in f.models:
            print("  model idx %3d bones %3d sphere (%.4f %.4f %.4f r %.1f)" % ((idx, n.nbones) + tuple(n.sphere)))
            for m in n.meshes:
                print("    mesh %-24s flag %d sb %d polys %d" % (m.name, m.bone_flag, m.singlebind, len(m.polygons)))
                for p in m.polygons:
                    print("      v %5d fmt %02x/%02x idx %5d tris %5d prim %02x/%02x mats %s" % (
                        p.nverts, p.vsz, p.uvsz, len(p.indices), len(p.triangles()), p.prim, p.pflag, p.materials))
