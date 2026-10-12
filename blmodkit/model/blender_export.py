"""Runs INSIDE Blender (background, embedded scripts disabled): exports a rigged model in its REST pose.

    blender -b --disable-autoexec <model.blend> --python blender_export.py -- --out DIR [options]
    blender -b --factory-startup            --python blender_export.py -- --out DIR --import <model.fbx|.glb|.gltf>

The .blend is never saved (nothing here calls a save operator). Options:
    --armature NAME     armature object to use (default: the one deforming the most vertices)
    --include REGEX     only mesh objects whose name matches
    --exclude REGEX     drop mesh objects whose name matches
    --include-hidden    also take meshes hidden in the viewport (render-disabled ones are always skipped)
    --keep-shape-keys   evaluate shape keys as they are set in the file (default: basis shape only)
    --use-pose          take the armature's current pose as the rest pose (files whose rest pose isn't the bind)
    --max-image N       largest image side written (default 2048)

Writes into DIR:
    export.npz    positions/normals/uv per split vertex (world space, Blender units), triangles, materials,
                  weights (bone index into bone_names, -1 empty), bones (head/tail/matrix world, parent, deform)
    export.json   report: objects taken/skipped and why, materials (what the colour comes from), images,
                  armature, units, warnings
    img/<n>.npy   every image used by a material, uint8 RGBA, rows TOP-DOWN (row 0 = top, v = 1)
"""
import json
import os
import re
import sys

import bpy
import numpy as np

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def opt(name, default=None, flag=False):
    if name not in argv:
        return default
    if flag:
        return True
    return argv[argv.index(name) + 1]


OUT = opt("--out")
ARM_NAME = opt("--armature")
INC = opt("--include")
EXC = opt("--exclude")
IMPORT = opt("--import")
INCLUDE_HIDDEN = opt("--include-hidden", False, True)
KEEP_SHAPE = opt("--keep-shape-keys", False, True)
USE_POSE = opt("--use-pose", False, True)
MAX_IMG = int(opt("--max-image", "2048"))
IMG_DIR = os.path.join(OUT, "img")
WARN = []

LINE_WORDS = re.compile(r"(outline|_line$|^line_|edge_line|toon_?line|inverted.?hull)", re.I)
NONCOLOUR_IMG = re.compile(r"(normal|_nrm|_nor\b|_n\.|bump|rough|metal|spec|_ao\b|occlu|height|disp|emiss|_orm|"
                           r"opacity|_mask(?!_0)|_ilm|_sss|shadow|_decal|_detail|matcap)", re.I)


def m2l(m):
    return [[float(v) for v in row] for row in m]


# ------------------------------------------------------------------------------------------- import
if IMPORT:
    ext = os.path.splitext(IMPORT)[1].lower()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=IMPORT, automatic_bone_orientation=False, ignore_leaf_bones=False)
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=IMPORT)
    elif ext == ".dae":
        bpy.ops.wm.collada_import(filepath=IMPORT)
    else:
        raise SystemExit("unsupported import format " + ext)

os.makedirs(IMG_DIR, exist_ok=True)

# ------------------------------------------------------------------------------------------- armature
arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
if not arms:
    raise SystemExit("EXPORT_ERROR no armature in the file")


def deformers(ob):
    out = set()
    for m in ob.modifiers:
        if m.type == "ARMATURE" and m.object is not None and m.show_render:
            out.add(m.object.name)
    if ob.parent is not None and ob.parent.type == "ARMATURE" and ob.parent_type in ("ARMATURE", "BONE"):
        out.add(ob.parent.name)
    return out


meshes_all = [o for o in bpy.data.objects if o.type == "MESH"]
if ARM_NAME:
    arm = bpy.data.objects[ARM_NAME]
else:
    score = {a.name: 0 for a in arms}
    for o in meshes_all:
        for a in deformers(o):
            if a in score:
                score[a] += len(o.data.vertices)
    arm = bpy.data.objects[max(score, key=score.get)]
A = arm.data
bone_names = [b.name for b in A.bones]
BI = {n: i for i, n in enumerate(bone_names)}

# ------------------------------------------------------------------------------------------- meshes
objects = []
skipped = {}
for o in sorted(meshes_all, key=lambda x: x.name):
    if arm.name not in deformers(o):
        skipped[o.name] = "not deformed by / parented to armature " + arm.name
        continue
    if o.hide_render:
        skipped[o.name] = "disabled for rendering"
        continue
    try:
        vis = o.visible_get()
    except Exception:
        vis = not o.hide_get()
    if not vis and not INCLUDE_HIDDEN:
        skipped[o.name] = "hidden (use --include-hidden)"
        continue
    if INC and not re.search(INC, o.name):
        skipped[o.name] = "not matched by --include"
        continue
    if EXC and re.search(EXC, o.name):
        skipped[o.name] = "matched by --exclude"
        continue
    if LINE_WORDS.search(o.name):
        skipped[o.name] = "looks like an outline (inverted hull) mesh"
        continue
    objects.append(o)
if not objects:
    raise SystemExit("EXPORT_ERROR no skinned mesh found for armature " + arm.name)

# rest pose, basis shape, outline solidify off
for a in bpy.data.armatures:
    a.pose_position = "POSE" if USE_POSE else "REST"
for o in objects:
    for m in o.modifiers:
        if m.type == "SOLIDIFY" and (getattr(m, "use_flip_normals", False) or m.material_offset):
            m.show_viewport = False
            m.show_render = False
            WARN.append("%s: solidify modifier %s (outline) switched off" % (o.name, m.name))
    sk = o.data.shape_keys
    if sk is not None and not KEEP_SHAPE:
        for kb in sk.key_blocks[1:]:
            kb.mute = True
dg = bpy.context.evaluated_depsgraph_get()
dg.update()

# ------------------------------------------------------------------------------------------- materials
images = {}


def save_image(img):
    if img is None:
        return None
    if img.name in images:
        return img.name
    info = dict(name=img.name, size=list(img.size), colorspace=img.colorspace_settings.name,
                filepath=img.filepath, packed=bool(img.packed_file), source=img.source)
    w, h = img.size
    if not (w and h) or not img.has_data:
        try:
            img.reload()
            w, h = img.size
        except Exception:
            pass
    if not (w and h):
        info["missing"] = True
        WARN.append("image %s has no pixels (file missing?): %s" % (img.name, img.filepath))
        images[img.name] = info
        return img.name
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)[::-1]
    step = 1
    while max(w, h) // step > MAX_IMG:
        step *= 2
    if step > 1:
        hh, ww = h // step * step, w // step * step
        px = px[:hh, :ww].reshape(hh // step, step, ww // step, step, 4).mean((1, 3))
        info["downsampled"] = step
    u8 = np.clip(np.round(px * 255.0), 0, 255).astype(np.uint8)
    fn = re.sub(r"[^A-Za-z0-9_.-]", "_", img.name) + ".npy"
    k = 1
    while os.path.exists(os.path.join(IMG_DIR, fn)) and fn not in [v.get("npy") for v in images.values()]:
        fn = re.sub(r"\.npy$", "_%d.npy" % k, fn)
        k += 1
    np.save(os.path.join(IMG_DIR, fn), u8)
    info["npy"] = fn
    info["shape"] = list(u8.shape)
    info["alpha_range"] = [int(u8[..., 3].min()), int(u8[..., 3].max())]
    images[img.name] = info
    return img.name


def upstream_images(sock, seen=None, path=()):
    """[(image node, socket path)] reachable upstream of a socket (depth first)"""
    seen = seen if seen is not None else set()
    out = []
    for lk in sock.links:
        n = lk.from_node
        key = (n.name, lk.from_socket.identifier)
        if key in seen:
            continue
        seen.add(key)
        p = path + ((n.type, lk.to_socket.name),)
        if n.type == "TEX_IMAGE" and n.image is not None:
            out.append((n, p))
        if n.type in ("NORMAL_MAP", "BUMP", "DISPLACEMENT"):
            continue
        for s in n.inputs:
            if s.is_linked:
                out += upstream_images(s, seen, p + ((n.type, s.name),))
        if n.type == "GROUP" and n.node_tree is not None:
            for gn in n.node_tree.nodes:
                if gn.type == "TEX_IMAGE" and gn.image is not None:
                    out.append((gn, p + (("GROUP_INNER", gn.label or gn.name),)))
    return out


def uv_of(node):
    s = node.inputs.get("Vector")
    if s is None or not s.is_linked:
        return None
    n = s.links[0].from_node
    for _ in range(6):
        if n.type == "UVMAP":
            return n.uv_map or None
        nxt = [x for x in n.inputs if x.is_linked]
        if not nxt:
            return None
        n = nxt[0].links[0].from_node
    return None


def material_info(mat):
    e = dict(name=mat.name if mat else "", kind="flat", colour=[0.8, 0.8, 0.8, 1.0])
    if mat is None:
        return e
    e["blend_method"] = getattr(mat, "blend_method", None)
    e["colour"] = [float(c) for c in mat.diffuse_color]
    if LINE_WORDS.search(mat.name):
        e["outline"] = True
    nt = mat.node_tree if mat.use_nodes else None
    if nt is None:
        return e
    nodes = list(nt.nodes)
    outs = [n for n in nodes if n.type == "OUTPUT_MATERIAL"]
    out = next((n for n in outs if n.is_active_output), outs[0] if outs else None)
    # Xenoverse 2 importer: 'XV2 Toon Shade' style group with Band / Shadow Start / Ink Strength + dyt + mask
    grp = next((n for n in nodes if n.type == "GROUP" and "Band" in n.inputs), None)
    imgs_all = [n for n in nodes if n.type == "TEX_IMAGE" and n.image is not None]
    if grp is not None:
        dyt = [n for n in imgs_all if "dyt" in n.image.name.lower()]
        dyt_lit = [n for n in dyt if "lit" in (n.label or "").lower()] or dyt
        mask = [n for n in imgs_all if re.search(r"mask|msk", n.image.name, re.I)]
        if dyt_lit:
            e.update(kind="xv2", dyt=save_image(dyt_lit[0].image), band=float(grp.inputs["Band"].default_value),
                     ink_strength=float(grp.inputs["Ink Strength"].default_value) if "Ink Strength" in grp.inputs else 0.35,
                     shadow_start=float(grp.inputs["Shadow Start"].default_value) if "Shadow Start" in grp.inputs else 0.55,
                     mask=save_image(mask[0].image) if mask else None,
                     mask_extension=mask[0].extension if mask else "REPEAT",
                     uv_map=uv_of(mask[0]) if mask else None)
            return e
    found = upstream_images(out.inputs["Surface"]) if out is not None and out.inputs["Surface"].is_linked else []
    if not found:
        found = [(n, ()) for n in imgs_all]

    def score(item):
        n, p = item
        nm = n.image.name.lower()
        s = 0
        if re.search(r"(base|diff|albedo|col(or|our)?\b|_col|_d\b|_bc\b|tex)", nm):
            s += 3
        if NONCOLOUR_IMG.search(nm):
            s -= 6
        if n.image.colorspace_settings.name.lower() in ("non-color", "raw", "linear"):
            s -= 2
        names = [x[1].lower() for x in p]
        if any(x in ("base color", "color", "basecolor", "diffuse", "albedo", "color1", "a") for x in names):
            s += 2
        if any(x in ("normal", "roughness", "metallic", "specular", "alpha", "emission strength") for x in names):
            s -= 3
        return s

    cand = sorted(found, key=score, reverse=True)
    for n, _p in found:   # FighterZ style helper maps
        nm = n.image.name.lower()
        if re.search(r"_sss|sss_|shadow", nm) and "sss" not in e:
            e["sss"] = save_image(n.image)
        if re.search(r"_ilm|ilm_", nm) and "ilm" not in e:
            e["ilm"] = save_image(n.image)
    if cand and score(cand[0]) > -4:
        n = cand[0][0]
        e.update(kind="image", image=save_image(n.image), extension=n.extension, interpolation=n.interpolation,
                 uv_map=uv_of(n))
        al = out.inputs["Surface"].links[0].from_node if out is not None and out.inputs["Surface"].is_linked else None
        if al is not None and "Alpha" in al.inputs and al.inputs["Alpha"].is_linked:
            e["alpha_from_image"] = True
    # constant colour of the main shader
    sh = out.inputs["Surface"].links[0].from_node if out is not None and out.inputs["Surface"].is_linked else None
    if sh is not None:
        for nm in ("Base Color", "Color"):
            s = sh.inputs.get(nm)
            if s is not None and hasattr(s, "default_value"):
                try:
                    e["colour"] = [float(c) for c in s.default_value]
                except TypeError:
                    pass
                break
    return e


# ------------------------------------------------------------------------------------------- geometry
material_names, materials = [], []
P, N, UV, VM, VS, TRI, TMAT, TMESH, WB, WV = [], [], [], [], [], [], [], [], [], []
objrep = {}
voff = 0
for mi, ob in enumerate(objects):
    me = ob.data
    oe = ob.evaluated_get(dg)
    em = oe.to_mesh()
    nv = len(em.vertices)
    mw = np.array(ob.matrix_world, np.float64)
    co = np.empty(nv * 3, np.float32)
    em.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3).astype(np.float64) @ mw[:3, :3].T + mw[:3, 3]
    em.calc_loop_triangles()
    T = len(em.loop_triangles)
    tv = np.empty(T * 3, np.int32)
    em.loop_triangles.foreach_get("vertices", tv)
    tl = np.empty(T * 3, np.int32)
    em.loop_triangles.foreach_get("loops", tl)
    tm = np.empty(T, np.int32)
    em.loop_triangles.foreach_get("material_index", tm)
    L = len(em.loops)
    cn = np.empty(L * 3, np.float32)
    em.corner_normals.foreach_get("vector", cn)
    cn = cn.reshape(-1, 3).astype(np.float64) @ np.linalg.inv(mw[:3, :3])
    cn /= np.maximum(np.linalg.norm(cn, axis=1, keepdims=True), 1e-12)
    # material slots
    local = []
    for slot in ob.material_slots:
        m = slot.material
        nm = m.name if m else ""
        if nm not in material_names:
            material_names.append(nm)
            materials.append(material_info(m))
        local.append(material_names.index(nm))
    if not local:
        if "" not in material_names:
            material_names.append("")
            materials.append(material_info(None))
        local = [material_names.index("")]
    tmat = np.array(local, np.int32)[np.clip(tm, 0, len(local) - 1)]
    # uv layer: the one the main material's image uses, else the active render layer
    want = None
    for li in np.unique(tmat):
        u = materials[li].get("uv_map")
        if u:
            want = u
            break
    ul = (em.uv_layers.get(want) if want else None) or next((u for u in em.uv_layers if u.active_render), None) \
        or (em.uv_layers[0] if len(em.uv_layers) else None)
    if ul is not None:
        uv = np.empty(L * 2, np.float32)
        ul.uv.foreach_get("vector", uv)
        uv = uv.reshape(-1, 2)
    else:
        uv = np.zeros((L, 2), np.float32)
        WARN.append("%s has no UV map" % ob.name)
    # drop outline materials
    keep_t = np.array([not materials[m].get("outline") for m in tmat], bool)
    tv, tl = tv.reshape(T, 3)[keep_t], tl.reshape(T, 3)[keep_t]
    tmat = tmat[keep_t]
    T = len(tv)
    # split vertices per (vertex, uv, normal)
    loops = tl.reshape(-1)
    lv = tv.reshape(-1)
    key = np.concatenate([lv[:, None].astype(np.float64), np.round(uv[loops].astype(np.float64), 6),
                          np.round(cn[loops], 4)], axis=1)
    uk, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    src_loop, src_v = loops[first], lv[first]
    nE = len(uk)
    P.append(co[src_v])
    N.append(cn[src_loop])
    UV.append(uv[src_loop])
    VM.append(np.full(nE, mi, np.int32))
    VS.append(src_v.astype(np.int32))
    TRI.append(inv.reshape(T, 3).astype(np.int32) + voff)
    TMAT.append(tmat)
    TMESH.append(np.full(T, mi, np.int32))
    # weights (evaluated mesh keeps the deform-vertex layer)
    gnames = [g.name for g in ob.vertex_groups]
    rigid = None
    if ob.parent is not None and ob.parent == arm and ob.parent_type == "BONE" and ob.parent_bone in BI:
        rigid = BI[ob.parent_bone]
    nonbone = {}
    maxinf = 0
    rows = []
    if rigid is not None and not any(m.type == "ARMATURE" for m in ob.modifiers):
        rows = [[(rigid, 1.0)] for _ in range(nv)]
        maxinf = 1
    else:
        for v in em.vertices:
            lst = []
            for g in v.groups:
                if g.weight <= 0 or g.group >= len(gnames):
                    continue
                gn = gnames[g.group]
                if gn not in BI:
                    nonbone[gn] = nonbone.get(gn, 0) + 1
                    continue
                lst.append((BI[gn], g.weight))
            lst.sort(key=lambda t: -t[1])
            rows.append(lst)
            maxinf = max(maxinf, len(lst))
        unweighted = sum(1 for r in rows if not r)
        if unweighted:
            WARN.append("%s: %d vertices without bone weights (they follow the nearest weighted ones)"
                        % (ob.name, unweighted))
    kk = max(1, maxinf)
    wb = np.full((nv, kk), -1, np.int32)
    wv = np.zeros((nv, kk), np.float32)
    for i, lst in enumerate(rows):
        for k, (bb, w) in enumerate(lst):
            wb[i, k] = bb
            wv[i, k] = w
    WB.append(wb[src_v])
    WV.append(wv[src_v])
    objrep[ob.name] = dict(vertices=nv, export_vertices=int(nE), triangles=int(T), uv_layer=ul.name if ul else None,
                           rigid_bone=bone_names[rigid] if rigid is not None else None, max_influences=maxinf,
                           nonbone_groups=nonbone, modifiers=[m.type for m in ob.modifiers],
                           materials=[material_names[i] for i in sorted(set(local))])
    oe.to_mesh_clear()
    voff += nE

# ------------------------------------------------------------------------------------------- bones
amw = arm.matrix_world
bh, bt, bm, bp, bd = [], [], [], [], []
for b in A.bones:
    if USE_POSE:      # the file's current pose is taken as the rest pose (mesh evaluated in it too)
        pb = arm.pose.bones[b.name]
        bh.append(list(amw @ pb.head))
        bt.append(list(amw @ pb.tail))
        bm.append(m2l(amw @ pb.matrix))
    else:
        bh.append(list(amw @ b.head_local))
        bt.append(list(amw @ b.tail_local))
        bm.append(m2l(amw @ b.matrix_local))
    bp.append(BI[b.parent.name] if b.parent else -1)
    bd.append(bool(b.use_deform))

KMAX = max(a.shape[1] for a in WB)
np.savez_compressed(
    os.path.join(OUT, "export.npz"),
    mesh_names=np.array([o.name for o in objects]), material_names=np.array(material_names),
    bone_names=np.array(bone_names),
    positions=np.concatenate(P).astype(np.float32), normals=np.concatenate(N).astype(np.float32),
    uv=np.concatenate(UV).astype(np.float32), vertex_mesh=np.concatenate(VM), vertex_source=np.concatenate(VS),
    weight_bone=np.concatenate([np.pad(a, ((0, 0), (0, KMAX - a.shape[1])), constant_values=-1) for a in WB]),
    weight_value=np.concatenate([np.pad(a, ((0, 0), (0, KMAX - a.shape[1]))) for a in WV]),
    tris=np.concatenate(TRI), tri_material=np.concatenate(TMAT), tri_mesh=np.concatenate(TMESH),
    bone_parent=np.array(bp, np.int32), bone_head=np.array(bh, np.float32), bone_tail=np.array(bt, np.float32),
    bone_matrix=np.array(bm, np.float32), bone_deform=np.array(bd, bool))
sc = bpy.context.scene
rep = dict(source=IMPORT or bpy.data.filepath, blender=bpy.app.version_string, armature=arm.name,
           armatures=[a.name for a in arms], unit_scale=sc.unit_settings.scale_length,
           objects=objrep, skipped=skipped, materials=materials, images=images, warnings=WARN,
           totals=dict(vertices=int(sum(len(p) for p in P)), triangles=int(sum(len(t) for t in TRI)),
                       bones=len(bone_names)))
with open(os.path.join(OUT, "export.json"), "w") as fh:
    json.dump(rep, fh, indent=1)
print("EXPORT_OK", rep["totals"])
