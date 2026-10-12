"""blmodkit import-model: put a rigged humanoid model on a character slot.

    python -m blmodkit import-model --blend <file.blend> | --fbx <file> --base GOK --mod <slot mod folder>
           [--colour 0] [--scale auto|<f>] [--bone-map map.json] [--skin-tone goku|model] [--palette own|base]
           [--lips] [--face-from FACES.NUX] [--game <game folder or CPK>] [--work DIR] [--no-preview]
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time

import numpy as np

from .. import __version__
from ..util import ID_TO_NAME, KitError, check_code, parse_base
from . import assemble, basechar, blender, bonemap, fit, gamefiles, preview, textures

REF_SHOULDER_M = 1.30      # the base fighters' shoulder height in Xenoverse / FighterZ metres
SKIN_TONE_ALIASES = {"goku": "base", "base": "base", "model": "model", "own": "model", "keep": "model"}


def add_arguments(ap):
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--blend", help="Blender file with the rigged model (opened read-only, never saved)")
    src.add_argument("--fbx", help="FBX (also .glb / .gltf / .dae) with the rigged model")
    ap.add_argument("--base", default="GOK", help="base character code whose skeleton and moveset the model uses "
                                                  "(default GOK)")
    ap.add_argument("--mod", required=True, help="the slot mod folder (<game>/mods/<name>, with or without slot.toml)")
    ap.add_argument("--colour", "--color", type=int, default=0, help="costume / colour index 0-3 (default 0)")
    ap.add_argument("--scale", default="1", help="size: 1 = the base character's size (default), auto = the model's "
                    "own size when its units are known, or a factor like 0.8 (root rest scale)")
    ap.add_argument("--bone-map", help="edited bone_map.json (see the one written into the work folder)")
    ap.add_argument("--skin-tone", default="model", help="goku/base = skin takes the base character's skin colour; "
                                                         "model = keep the model's (default)")
    ap.add_argument("--palette", default="own", choices=("own", "base"),
                    help="own = ramps from the model's own shading (default); base = the base character's skin / "
                         "cloth ramps (matches the game's palette)")
    ap.add_argument("--soften", type=float, default=0.5, help="shadow ratios softened toward the game's 0.6 "
                                                              "(0 = the model's own, 1 = the game's; default 0.5)")
    ap.add_argument("--max-texture", type=int, default=1024, help="largest texture side (default 1024)")
    ap.add_argument("--lips", action="store_true", help="weight the mouth to the base lip bones (lip sync) when the "
                                                        "model has jaw / lip bones")
    ap.add_argument("--face-from", help="NUX whose face models (expression slots 0-7) are copied in")
    ap.add_argument("--no-crotch", action="store_true", help="don't fold thigh twist weights in the crotch zone")
    ap.add_argument("--drop-stray", action="store_true", help="drop triangles whose vertices lie far from every bone "
                                                              "they are weighted to (broken sources)")
    ap.add_argument("--code", help="3-letter slot code (when the mod has no slot.toml yet)")
    ap.add_argument("--name", help="character name for a new slot.toml")
    ap.add_argument("--game", help="your game: the folder with game_data_root, game_data_root, or LONG2DATA_US.CPK")
    ap.add_argument("--blender", help="path to blender.exe")
    ap.add_argument("--armature", help="armature object to use when the file has several")
    ap.add_argument("--include", help="regex: only these mesh objects")
    ap.add_argument("--exclude", help="regex: drop these mesh objects")
    ap.add_argument("--include-hidden", action="store_true", help="also take meshes hidden in the viewport")
    ap.add_argument("--use-pose", action="store_true", help="take the armature's current pose in the file as the rest "
                    "pose (for files whose rest pose isn't the pose the mesh was bound in)")
    ap.add_argument("--work", help="work folder for the export, bone map, report and preview "
                                   "(default <game>/modkit_work/<mod>)")
    ap.add_argument("--no-preview", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="build everything into the work folder only")
    return ap


def _md5(*parts):
    return hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def _slot_code(mod_dir, want=None):
    """(code, base id or None) of the mod's slot.toml, or --code for a new one"""
    from .. import slotfile
    if os.path.exists(os.path.join(mod_dir, "slot.toml")):
        _raw, slots = slotfile.read(mod_dir)
        for s in slots:
            if want is None or s.code == want.upper():
                return s.code, int(s.base)
        raise KitError("%s/slot.toml has no slot with code %s" % (mod_dir, want))
    if not want:
        raise KitError("%s has no slot.toml: make the slot first (python -m blmodkit new-slot ...) or pass "
                       "--code XXX and --name \"Name\" to create a minimal one" % mod_dir)
    return check_code(want), None


def auto_scale(rig_family, shoulder, unit_scale):
    """size from the model's shoulder height (the base fighters' is ~1.30 Xenoverse / FighterZ metres: Kid Buu's
    1.02 m -> 0.79, the size the Kid Buu slot settled on)"""
    h = shoulder * (unit_scale or 1.0)
    if rig_family in ("xv2", "fighterz") or 0.4 <= h <= 2.6:
        m = h
    elif 40 <= h <= 260:
        m = h / 100.0
    else:
        return 1.0, "unknown units (shoulder height %.3f) - kept the base size" % h
    raw = m / REF_SHOULDER_M
    s = float(np.clip(raw, 0.6, 1.3))
    if abs(s - 1.0) < 0.04:
        return 1.0, "shoulders %.2f m high, within 4%% of the base size" % m
    why = "shoulders %.2f m high vs %.2f m" % (m, REF_SHOULDER_M)
    if s != raw:
        why += " (%.2f limited to %.2f)" % (raw, s)
    return round(s, 3), why


def run(a, log=print):
    t0 = time.time()
    source = os.path.abspath(a.blend or a.fbx)
    if not os.path.isfile(source):
        raise KitError("model file not found: %s" % source)
    mod_dir = os.path.abspath(a.mod)
    os.makedirs(mod_dir, exist_ok=True)
    base_id, base_code = parse_base(a.base)
    if not 0 <= a.colour <= 3:
        raise KitError("--colour must be 0-3")
    code, slot_base = _slot_code(mod_dir, a.code)
    if slot_base is not None and slot_base != base_id:
        raise KitError("the slot %s plays as character %d (%s) - import with --base %s (its skeleton and moveset "
                       "are that character's)" % (code, slot_base, ID_TO_NAME.get(slot_base), slot_base))
    skin_tone = SKIN_TONE_ALIASES.get(a.skin_tone.lower())
    if skin_tone is None:
        raise KitError("--skin-tone: goku|base|model")
    exe_dir = os.path.dirname(os.path.dirname(mod_dir))
    work = os.path.abspath(a.work or os.path.join(exe_dir, "modkit_work", os.path.basename(mod_dir),
                                                  "%s_colour%d" % (code, a.colour)))
    os.makedirs(work, exist_ok=True)
    log("import-model: %s -> slot %s colour %d (base %s)" % (os.path.basename(source), code, a.colour, base_code))
    log("  work folder: %s" % work)

    # ---- game files
    try:
        files = gamefiles.open_game(a.game)
    except KitError:
        files = gamefiles.open_game(os.path.dirname(os.path.dirname(mod_dir)))
    base = basechar.BaseChar(files, base_code, 0)
    log("  base %s: %s (%d bones), skin ramp %s, cloth ramp %s" % (
        base_code, base.nux_name, len(base.rig), "%08x" % base.skin_ramp if base.skin_ramp else "-",
        "%08x" % base.cloth_ramp if base.cloth_ramp else "-"))

    # ---- 1. Blender export (cached)
    st = os.stat(source)
    key = _md5(source, st.st_size, int(st.st_mtime), a.armature, a.include, a.exclude, a.include_hidden, a.use_pose,
               os.path.getmtime(blender.EXPORT_SCRIPT))
    exp_dir = os.path.join(work, "export")
    stamp = os.path.join(exp_dir, "key.txt")
    if os.path.exists(stamp) and open(stamp).read() == key and os.path.exists(os.path.join(exp_dir, "export.npz")):
        log("  export: cached")
    else:
        if os.path.isdir(exp_dir):
            shutil.rmtree(exp_dir)
        log("  export: running Blender in the background (the model file is not changed)")
        blender.export(source, exp_dir, a.blender, a.armature, a.include, a.exclude, a.include_hidden,
                       use_pose=a.use_pose, log=log)
        open(stamp, "w").write(key)
    ex = dict(np.load(os.path.join(exp_dir, "export.npz")))
    exj = json.load(open(os.path.join(exp_dir, "export.json"), encoding="utf-8"))
    for w in exj.get("warnings", []):
        log("  export warning: " + w)
    log("  model: %d vertices, %d triangles, %d bones, %d materials (armature %s)" % (
        exj["totals"]["vertices"], exj["totals"]["triangles"], exj["totals"]["bones"], len(exj["materials"]),
        exj["armature"]))

    # ---- 2. bone map
    names = [str(n) for n in ex["bone_names"]]
    parents = [int(p) for p in ex["bone_parent"]]
    wb = ex["weight_bone"]
    weighted = np.zeros(len(names), bool)
    weighted[np.unique(wb[wb >= 0])] = True
    rig_family, roles = bonemap.auto_map(names, parents, ex["bone_head"], ex["bone_deform"].tolist(),
                                         weighted.tolist())
    mat_over = {}
    if a.bone_map:
        _r, user_roles, mat_over, _doc = bonemap.load(a.bone_map)
        roles = bonemap.merge(roles, user_roles)
        log("  bone map: %s (+ edits from %s)" % (rig_family, a.bone_map))
    else:
        log("  bone map: detected rig %s" % rig_family)
    map_path = os.path.join(work, "bone_map.json")
    bonemap.save(map_path, rig_family, roles, base_code)
    miss = bonemap.missing_required(roles)
    if miss:
        raise KitError("the bone map has no bone for %s. Edit %s (set those roles on the right bones) and run again "
                         "with --bone-map %s" % (", ".join(miss), map_path, map_path))
    # where each bone's vertices are (weighted centroid) - for bones the map leaves on 'auto'
    P = ex["positions"].astype(np.float64)
    wv = np.where(wb >= 0, ex["weight_value"], 0.0)
    acc = np.zeros((len(names), 3))
    tot = np.zeros(len(names))
    for k in range(wb.shape[1]):
        ok = wb[:, k] >= 0
        np.add.at(acc, wb[ok, k], P[ok] * wv[ok, k][:, None])
        np.add.at(tot, wb[ok, k], wv[ok, k])
    centroid = np.where(tot[:, None] > 1e-6, acc / np.maximum(tot, 1e-12)[:, None], np.nan)
    resolved = bonemap.resolve(names, parents, roles, ex["bone_head"], centroid)
    lip_roles = [r for r in set(resolved.values()) if r.replace("follow:", "") in fit.LIP_MAP and r != "follow:head"]
    if a.lips and not lip_roles:
        log("  NOTE: --lips: the model has no jaw / lip bones the importer knows - face stays rigid on HEAD")

    # ---- 3. fit
    fitted = fit.fit(ex, resolved, base.rig, base_code, base.body_arrays, lips=a.lips and bool(lip_roles),
                     crotch=not a.no_crotch, drop_stray=a.drop_stray, log=log)
    fr = fitted["report"]
    log("  fit: similarity x%.4f, neck stretch %.3f, soles %s, %d vertices, max weight moved by the 4-bone limit %.3f"
        % (fr["similarity"]["scale"], fr["neck_squash"],
           {s: v.get("fitted") for s, v in fr["soles"].items()}, fr["vertices"], fr["weights_moved_by_4_limit_max"]))

    # ---- 4. scale
    if str(a.scale).lower() == "auto":
        scale, why = auto_scale(rig_family, fr["similarity"]["source_shoulder"], exj.get("unit_scale", 1.0))
        log("  scale auto: %.3f (%s)" % (scale, why))
    else:
        scale = float(a.scale)
    if not 0.4 <= scale <= 1.6:
        raise KitError("--scale %.3f is out of range (0.4-1.6)" % scale)
    if scale != 1.0:
        log("  WARNING: size %.3f uses a uniform root rest scale. The base moveset's root travel (steps, dashes, "
            "knock-backs) is drawn %.0f%% as long, but the game moves the fighter the full distance when each move "
            "ends, so moves end with a small jump of (1 - scale) x travel (Kid Buu at 0.79: up to ~11 units on a back "
            "step). Hits and hurt spheres scale with the model. Only a moveset re-authored for the size avoids the "
            "jump." % (scale, scale * 100))

    # ---- 5. textures + ramps
    tx = textures.build(exj, exp_dir, fitted, base, palette=a.palette, skin_tone=skin_tone, soften=a.soften,
                        max_size=a.max_texture, overrides=mat_over, log=log)
    log("  textures: %d colour, %d ramps (%s)" % (len(tx["textures"]), len(tx["ramps"]),
                                                  ", ".join(r["name"] for r in tx["ramps"])))
    # material section of the bone map: what each material became (editable: class / lit / shadow / ramp)
    doc = json.load(open(map_path, encoding="utf-8"))
    doc["materials"] = {k: {"class": v["cls"], "lit": v["lit"], "shadow": v["shadow"], "ramp": v["ramp_mode"]}
                        for k, v in tx["materials"].items()}
    doc["materials_about"] = ("class: skin / eye / other; lit / shadow: RGB 0-255; ramp: own / base_skin / base_cloth. "
                              "Edit and pass the file back with --bone-map.")
    json.dump(doc, open(map_path, "w", encoding="utf-8"), indent=1)

    # ---- 6. NUX + AMB
    rel_dir = "PAC/CMN/CH/BC%s" % code
    stem = "BC%s0%d0" % (code, a.colour)
    out_dir = os.path.join(work, "out") if a.dry_run else os.path.join(mod_dir, *rel_dir.split("/"))
    os.makedirs(out_dir, exist_ok=True)
    nux_path = os.path.join(out_dir, stem + ".NUX")
    amb_path = os.path.join(out_dir, stem + ".AMB")
    nux_data, nrep = assemble.build_nux(base, fitted, tx, code, a.colour, mod_dir, nux_path, scale=scale,
                                        face_from=a.face_from, log=log)
    amb_data, arep = assemble.build_amb(base, scale)
    with open(nux_path, "wb") as fh:
        fh.write(nux_data)
    with open(amb_path, "wb") as fh:
        fh.write(amb_data)
    log("  wrote %s (%.2f MB, %d textures, ids %s, %d polygons) and %s" % (
        nux_path, len(nux_data) / 1e6, nrep["textures"], nrep["texture_handles"], nrep["body_polygons"], amb_path))

    # ---- 7. slot.toml
    srep = None
    if not a.dry_run:
        srep = assemble.update_slot_toml(mod_dir, code, a.colour, "%s/%s.NUX" % (rel_dir, stem),
                                         "%s/%s.AMB" % (rel_dir, stem), name=a.name, base_id=base_id,
                                         base_code=base_code, base_name=ID_TO_NAME[base_id],
                                         kit_version=__version__, log=log)
        if not os.path.exists(os.path.join(mod_dir, "mod.toml")):
            from .. import slotfile
            with open(os.path.join(mod_dir, "mod.toml"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(slotfile.mod_toml_text(a.name or code, "", "0.1", "Extra character %s (model imported "
                                                "with blmodkit import-model)." % (a.name or code)))
        log("  slot.toml: model %s, skeleton %s" % (srep["model"], srep["skeleton"]))

    # ---- 8. preview
    prev = None
    if not a.no_preview:
        try:
            mv = files.read("PAC/CMN/CH/BC%s/BC%sM0.AMB" % (base_code, base_code))
        except Exception:
            mv = None
        lipb = None
        if a.lips and lip_roles:
            try:
                lipb = files.read("PAC/CMN/CH/BC%s/BC%sL000.AMB" % (base_code, base_code))
            except Exception:
                lipb = None
        prev = os.path.join(work, "preview_%s.png" % stem)
        preview.sheet(nux_data, amb_data, base.nux_bytes, base.amb_bytes, mv,
                      "%s  %s (slot %s colour %d, scale %.3g) next to %s" % (
                          os.path.basename(source), stem, code, a.colour, scale, base_code), prev, lip_bank=lipb)
        log("  preview: %s" % prev)

    rep = dict(source=source, slot=code, colour=a.colour, base=base_code, scale=scale, rig=rig_family,
               bone_map=map_path, export=exj["totals"], fit=fr, textures=tx["materials"],
               ramps=[{k: v for k, v in r.items() if k != "rgba"} for r in tx["ramps"]], nux=nrep, amb=arep,
               slot_toml=srep, preview=prev, files=[nux_path, amb_path], seconds=round(time.time() - t0, 1))
    json.dump(rep, open(os.path.join(work, "import_report.json"), "w", encoding="utf-8"), indent=1, default=str)
    log("done in %.0f s - report %s" % (time.time() - t0, os.path.join(work, "import_report.json")))
    return rep


def main(argv=None):
    ap = add_arguments(argparse.ArgumentParser(prog="blmodkit import-model", description=__doc__,
                                               formatter_class=argparse.RawDescriptionHelpFormatter))
    a = ap.parse_args(argv)
    try:
        run(a)
    except (KitError, FileNotFoundError, RuntimeError) as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
