"""Write the costume model (.NUX) + skeleton (.AMB) into a slot mod and point its slot.toml at them."""
import os
import re

import numpy as np

from .. import slotfile
from .. import textures as kit_textures
from ..ntxr import scan_gidx
from ..util import KitError
from . import nux_io, skeleton

BODY_FLAGS = 0x16308800
# The game's own costume models go up to 7.29 MB (Goku's 3rd costume); the ports proven in game were 5.8-6.3 MB.
NUX_WARN = 7_300_000
NUX_MAX = 8_000_000


class AssembleError(KitError):
    pass


# --------------------------------------------------------------------------------------------- texture ids
def used_handles(mod_dir, skip=None):
    """texture handles of every .NUX / .NUT in the mod folder (except `skip`, the file being replaced)"""
    used = {}
    for root, _dirs, files in os.walk(mod_dir):
        for f in files:
            if not f.upper().endswith((".NUX", ".NUT")):
                continue
            p = os.path.join(root, f)
            if skip and os.path.normcase(os.path.abspath(p)) == os.path.normcase(os.path.abspath(skip)):
                continue
            try:
                data = open(p, "rb").read()
            except OSError:
                continue
            for g, _o, _d in scan_gidx(data):
                used.setdefault(g, p)
    return used


def allocate_handles(code, mod_dir, out_path, need):
    """`need` free handles in the slot code's own block (the kit's rule: 0x30000000 + base36(code) * 0x800), not
    used by the mod's other files"""
    first, end = kit_textures.handle_block(code)
    used = used_handles(mod_dir, out_path)
    out = [g for g in range(first, end) if g not in used][:need]
    if len(out) < need:
        raise AssembleError("the slot's texture handle block %08X-%08X is full" % (first, end - 1))
    return out


# --------------------------------------------------------------------------------------------- geometry
def facing(pos, nrm, tris):
    a, b, c = pos[tris[:, 0]], pos[tris[:, 1]], pos[tris[:, 2]]
    fn = np.cross(b - a, c - a)
    vn = nrm[tris[:, 0]] + nrm[tris[:, 1]] + nrm[tris[:, 2]]
    return np.sign((fn * vn).sum(1))


def build_nux(base, fitted, tex, code, colour, mod_dir, out_path, scale=1.0, face_from=None, log=print):
    """-> (nux bytes, report)"""
    rep = {}
    f = nux_io.parse(base.nux_bytes)
    body = nux_io.body(f)
    template = base.template
    vsrc = tex["vsrc"]
    pos = (fitted["pos"][vsrc].astype(np.float64) * scale).astype(np.float32)
    nrm = fitted["nrm"][vsrc].astype(np.float32)
    bidx = fitted["bidx"][vsrc].astype(np.int64)
    bw = fitted["bw"][vsrc].astype(np.float32)
    uv = tex["uv"]
    tris = tex["tris"].astype(np.int64)
    # winding like the base character's
    gk = []
    for p in body.polygons:
        d = nux_io.decode_polygon(p)
        gk.append(facing(np.asarray(d["vertices"]), np.asarray(d["normals"])[:, :3], np.asarray(d["triangles"])).mean())
    base_sign = np.sign(np.mean(gk))
    fb = facing(pos, nrm, tris)
    flip = np.sign(fb.mean()) != base_sign
    if flip:
        tris = tris[:, [0, 2, 1]]
    rep["winding"] = dict(flipped=bool(flip), triangles_against_their_normals=int((fb != np.sign(fb.mean())).sum()))

    # handles: every texture (base ramp copies too) gets a handle of the slot's own block
    ff = nux_io.load(face_from) if face_from else None
    n_new = len(tex["textures"]) + len(tex["ramps"]) + (len(ff.textures) if ff else 0)
    ids = allocate_handles(code, mod_dir, out_path, n_new)
    rep["texture_handles"] = "%08X-%08X" % (ids[0], ids[-1])
    it = iter(ids)
    colour_ids = []
    f.textures = []
    for t in tex["textures"]:
        h, w = t["rgba"].shape[:2]
        mips = 3 if min(w, h) >= 256 else 1
        gid = next(it)
        nux_io.add_texture(f, t["rgba"], fmt=2, mips=mips, gidx=gid)
        colour_ids.append(gid)
    ramp_ids = []
    for r in tex["ramps"]:
        if "retail" in r:
            src = base.textures[r["retail"]]
            t = nux_io.Texture(src.fmt, src.width, src.height, src.mips, next(it), src.data, src.mip_sizes, src.ext,
                               dict(src.misc))
            f.textures.append(t)
            ramp_ids.append(t.gidx)
        else:
            gid = next(it)
            nux_io.add_texture(f, r["rgba"], fmt=0, mips=1, gidx=gid)
            ramp_ids.append(gid)

    # polygons: one per (colour texture, ramp); weld exact duplicates
    tri_tex, tri_ramp = tex["tri_tex"], tex["tri_ramp"]
    polygons = []
    rep["polygons"] = []
    white = np.full(4, 255, np.uint8)
    for ti in range(len(tex["textures"])):
        for ri in range(len(tex["ramps"])):
            sel = np.nonzero((tri_tex == ti) & (tri_ramp == ri))[0]
            if len(sel) == 0:
                continue
            t = tris[sel]
            used_v, local = np.unique(t.reshape(-1), return_inverse=True)
            key = np.concatenate([np.round(pos[used_v] / 1e-4), np.round(nrm[used_v] / 0.01), np.round(uv[used_v] / 1e-3),
                                  bidx[used_v], np.round(bw[used_v] * 1e4)], 1)
            _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
            used_v, local = used_v[first], inv.reshape(-1)[local]
            # a polygon holds at most 65535 vertices / indices: split big ones
            tri_local = local.reshape(-1, 3)
            chunks = [tri_local]
            if len(used_v) > 60000 or len(tri_local) * 4 > 60000:
                n = int(np.ceil(max(len(used_v) / 60000.0, len(tri_local) * 4 / 60000.0)))
                chunks = np.array_split(tri_local, n)
            for ch in chunks:
                uv_idx, loc = np.unique(ch.reshape(-1), return_inverse=True)
                vv = used_v[uv_idx]
                mat = nux_io.Material.from_bytes(template.to_bytes())
                mat.textures[0][0] = ramp_ids[ri]
                mat.textures[1][0] = colour_ids[ti]
                polygons.append(dict(vertices=pos[vv], normals=nrm[vv], bone_idx=bidx[vv], weights=bw[vv], uv=uv[vv],
                                     colors=np.tile(white, (len(vv), 1)), triangles=loc.reshape(-1, 3), material=mat))
                rep["polygons"].append(dict(texture=tex["textures"][ti]["name"], ramp=tex["ramps"][ri]["name"],
                                            triangles=int(len(ch)), vertices=int(len(vv))))
    nux_io.replace_body(f, polygons)
    # faces
    if face_from:
        faces = {i: n for i, n in ff.models if i != 0xFF}
        f.models = [(i, faces.get(i, n)) for i, n in f.models]
        # the face file's textures get handles of the slot's block too (materials of the face models retargeted)
        remap = {t.gidx: next(it) for t in ff.textures}
        for t in ff.textures:
            t.gidx = remap[t.gidx]
            f.textures.append(t)
        for i, n in ff.models:
            if i == 0xFF:
                continue
            for p in n.polygons:
                for m in p.materials:
                    for tt in m.textures:
                        tt[0] = remap.get(tt[0], tt[0])
        rep["faces"] = "from " + face_from
    else:
        nux_io.empty_faces(f, "degenerate")
        skin_ramp = ramp_ids[int(np.bincount(tri_ramp).argmax())]
        for i, n in f.models:
            if i == 0xFF:
                continue
            for p in n.polygons:
                for m in p.materials:
                    if len(m.textures) == 2:
                        m.textures[0][0] = skin_ramp
                        m.textures[1][0] = colour_ids[0]
                    else:
                        m.textures[0][0] = colour_ids[0]
        rep["faces"] = "empty (one zero-area triangle per face slot; the face is part of the body, rigid on HEAD)"
    referenced = {tid for m in nux_io.all_materials(f) for tid in m.texture_ids}
    f.textures = [t for t in f.textures if t.gidx in referenced]
    missing = sorted(referenced - {t.gidx for t in f.textures})
    if missing:
        raise AssembleError("materials reference missing textures %s" % [hex(x) for x in missing])
    name = "BC%s0%d0" % (code.upper(), colour)
    nux_io.rename(f, name)
    if scale != 1.0:
        for i, n in f.models:
            if i == 0xFF:
                continue
            for p in n.polygons:
                g = p.va if p.va is not None else p.vs
                pp = np.array(g["pos"], np.float64)
                pp[:, :3] *= scale
                g["pos"] = pp.astype(g["pos"].dtype)
    data = nux_io.serialize(f)
    # checks
    f2 = nux_io.parse(data)
    assert nux_io.serialize(f2) == data
    b2 = nux_io.body(f2)
    nv = sum(p.nverts for p in b2.polygons)
    nt = sum(len(p.triangles()) for p in b2.polygons)
    if nt != len(tris):
        raise AssembleError("triangle count changed while writing (%d -> %d)" % (len(tris), nt))
    for p in b2.polygons:
        bi, ww = p.bone_idx(), p.weights()
        if bi.max() >= len(base.rig) or np.abs(ww.sum(1) - 1).max() > 1e-4:
            raise AssembleError("bad bone indices / weights in the written model")
    rep.update(internal_name=name + "_B.NUT", bytes=len(data), textures=len(f2.textures),
               texture_bytes=sum(len(t.data) for t in f2.textures), body_polygons=len(b2.polygons),
               body_vertices=nv, body_triangles=nt,
               ids=["%08x" % t.gidx for t in f2.textures])
    if len(data) > NUX_MAX:
        raise AssembleError("the model is %.2f MB - over the %.1f MB the importer allows (the game's biggest costume "
                            "is 7.29 MB). Use --max-texture 512 or fewer / smaller source textures."
                            % (len(data) / 1e6, NUX_MAX / 1e6))
    if len(data) > NUX_WARN:
        log("  WARNING: the model is %.2f MB, bigger than any costume of the game (7.29 MB) - test it in a fight; "
            "--max-texture 512 makes it smaller" % (len(data) / 1e6))
    return data, rep


def build_amb(base, scale=1.0):
    d = base.amb_bytes
    root = None
    if scale != 1.0:
        d, root, nbnd = skeleton.set_root_scale(d, scale, nux_io=nux_io)
        return d, dict(root_rest_scale=scale, root=root, bnd_vertices_scaled=nbnd)
    return d, dict(root_rest_scale=1.0, note="the base character's skeleton, byte copy")


# --------------------------------------------------------------------------------------------- slot.toml
def _locate(lines, code):
    """line numbers in slot.toml of the [[slot]] with this code (else the first): slot, colours, files, model,
    skeleton"""
    slots, cur, section = [], None, None
    for i, line in enumerate(lines):
        st = line.strip()
        if st == "[[slot]]":
            cur = dict(slot=i, colours=None, files=None, model=None, skeleton=None, code=None)
            slots.append(cur)
            section = "slot"
            continue
        if st.startswith("["):
            section = "files" if st == "[slot.files]" else "other"
            if section == "files" and cur is not None:
                cur["files"] = i
            continue
        if cur is None or "=" not in st or st.startswith("#"):
            continue
        k, v = st.split("=", 1)
        k = k.strip()
        if section == "slot" and k == "code":
            cur["code"] = v.split("#")[0].strip().strip('"').strip("'").upper()
        elif section == "slot" and k in ("colours", "colors"):
            cur["colours"] = i
        elif section == "files" and k in ("model", "skeleton"):
            cur[k] = i
    return next((x for x in slots if x["code"] == code), slots[0] if slots else None)


def update_slot_toml(mod_dir, code, colour, nux_rel, amb_rel, name=None, base_id=0, base_code="GOK",
                     base_name="Goku", kit_version="", log=print):
    """point the slot's model / skeleton for this colour at the imported files; creates slot.toml when missing"""
    path = os.path.join(mod_dir, "slot.toml")
    pat_n = re.sub(r"BC%s0%d0\.NUX$" % (code, colour), "BC%s0{c}0.NUX" % code, nux_rel)
    pat_a = re.sub(r"BC%s0%d0\.AMB$" % (code, colour), "BC%s0{c}0.AMB" % code, amb_rel)
    if not os.path.exists(path):
        text = slotfile.slot_toml_text(code, base_id, base_code, base_name, name or code, colour + 1, 0,
                                       [("model", [pat_n]), ("skeleton", [pat_a])], kit_version)
        text = text.replace('card = "art/', '# card = "art/').replace('portrait = "art/', '# portrait = "art/')
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        log("  created %s" % path)
        if colour > 0:
            log("  NOTE: colours 0-%d of the new slot have no model yet (the base character's is shown)" % (colour - 1))
        return dict(model=[pat_n], skeleton=[pat_a], colours=colour + 1, changed=True)
    _raw, slots = slotfile.read(mod_dir)
    slot = next((x for x in slots if x.code == code), None)
    if slot is None:
        raise AssembleError("%s has no [[slot]] with code %s" % (path, code))
    colours = int(slot.colours)
    lines = open(path, encoding="utf-8").read().splitlines()
    loc = _locate(lines, code)
    out = {}
    changed = False
    for key, want, pat in (("model", nux_rel, pat_n), ("skeleton", amb_rel, pat_a)):
        lst = [str(x) for x in (slot.files.get(key) or [])]
        if isinstance(slot.files.get(key), str):
            lst = [slot.files.get(key)]
        cur = lst[colour] if colour < len(lst) else (lst[0] if lst else None)
        if cur is not None and slotfile.norm(slotfile.expand(cur, colour, 0)) == slotfile.norm(want):
            out[key] = lst
            continue
        n = max(colours, colour + 1)
        new = list(lst)
        if len(new) == 1 and "{c}" in new[0]:
            new = [new[0]] * n
        while len(new) <= colour:
            new.append(new[0] if new else pat)
        new[colour] = want
        out[key] = new
        changed = True
        line = "%-8s = [%s]" % (key, ", ".join('"%s"' % x for x in new))
        if loc and loc[key] is not None:
            old = lines[loc[key]]
            cmt = old[old.index("#"):] if "#" in old.split("]")[-1] else ""
            lines[loc[key]] = line + ("  " + cmt if cmt else "")
        elif loc and loc["files"] is not None:
            lines.insert(loc["files"] + 1, line)
            loc = _locate(lines, code)
        else:
            lines += ["", "[slot.files]", line]
            loc = _locate(lines, code)
    if colour >= colours:
        colours = colour + 1
        changed = True
        if loc and loc["colours"] is not None:
            old = lines[loc["colours"]]
            cmt = old[old.index("#"):] if "#" in old else ""
            lines[loc["colours"]] = "colours = %d" % colours + ("            " + cmt if cmt else "")
        log("  slot.toml: colours raised to %d" % colours)
    if changed:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")
    out.update(colours=colours, changed=changed)
    return out
