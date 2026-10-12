"""new-slot: a ready-to-load extra-character mod made from one of the game's characters.

    python -m blmodkit new-slot --base VGT --code TST --name "Test Vegeta" --out mods\\Test_Vegeta

Every file comes from the user's own LONG2DATA_US.CPK and is written under the new code's
names (PAC/CMN/CH/BCTST/BCTSTM0.AMB, ...). Files are copied byte for byte, except:
  * .NUX models (costumes, effect models, ARST): their internal texture-set name gets the new
    code (BCVGT000_B.NUT -> BCTST000_B.NUT) and every texture handle is moved into the slot's
    own handle block (textures + the materials that use them);
  * .NUT texture banks (effect textures, HUD face): handles moved the same way.
So the new slot can never take the base character's textures (or the other way round),
even after you recolour or replace them.
"""
import os

from . import __version__
from . import slotfile
from . import textures as T
from .util import (KitError, ID_TO_NAME, MAX_PATH, check_code, human_size, info, parse_base,
                   warn)

# the order the files are listed in slot.toml
KEY_ORDER = ("m0", "m1", "l000", "se_csb", "vox_csb", "vox_amk", "bsp", "esp", "eas", "fx_model",
             "arst", "bar", "bufac")


def rename_path(path, bcode, code):
    """PAC/CMN/CH/BCVGT/ESPVGT.NUT -> PAC/CMN/CH/BCTST/ESPTST.NUT (every occurrence of the code)."""
    return path.replace(bcode, code)


def set_names_for(old_set, old_nub, bcode, code, new_path):
    """New internal names of a NUX: the base code replaced by the new one (both cases); when the
    old name has no code in it, one made from the new file name."""
    def sub(n):
        for a, b in ((bcode, code), (bcode.lower(), code.lower())):
            n = n.replace(a, b)
        return n
    s, b = sub(old_set), sub(old_nub)
    stem = os.path.splitext(new_path.split("/")[-1])[0]
    if s == old_set:
        s = (stem + "_B.NUT")[:25]
    if b == old_nub:
        b = (stem + ".NUB")[:25]
    return s, b


def skeleton_for(fs, c, m):
    """The base's skeleton for colour c, model m. Recolours share the skeleton of the colour
    they recolour (Y1 uses Y0's): the nearest lower colour with one."""
    for cc in range(c, -1, -1):
        if (cc, m) in fs["skeleton"]:
            return fs["skeleton"][(cc, m)]
    return None


def collect_handles(game, fs, plan):
    olds = []
    for src, _dst, kind in plan:
        if kind in ("nux", "nut"):
            d = game.read(src)
            olds += T.nux_handles(d) if kind == "nux" else T.nut_handles(d)
    return olds


def make_placeholder_art(folder, code, name):
    """Plain placeholder pictures (dark plate + the name) until `art` makes real ones."""
    from PIL import Image, ImageDraw, ImageFont
    art = os.path.join(folder, "art")
    os.makedirs(art, exist_ok=True)

    def font(size):
        for f in ("arialbd.ttf", "segoeuib.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
            try:
                return ImageFont.truetype(f, size)
            except OSError:
                continue
        return ImageFont.load_default()

    card = Image.new("RGBA", (512, 120), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rectangle([0, 0, 380, 119], fill=(40, 30, 70, 255))
    d.polygon([(380, 0), (440, 0), (380, 119)], fill=(40, 30, 70, 255))
    d.text((20, 18), name[:22], font=font(34), fill=(255, 230, 120, 255), stroke_width=3, stroke_fill=(0, 0, 0, 255))
    d.text((20, 70), "code " + code, font=font(22), fill=(220, 220, 240, 255), stroke_width=2,
           stroke_fill=(0, 0, 0, 255))
    card.save(os.path.join(art, "%s_CARD.png" % code))
    por = Image.new("RGBA", (368, 720), (0, 0, 0, 0))
    d = ImageDraw.Draw(por)
    d.ellipse([84, 40, 284, 260], fill=(40, 30, 70, 255), outline=(0, 0, 0, 255), width=6)
    d.rounded_rectangle([34, 250, 334, 720], radius=60, fill=(40, 30, 70, 255), outline=(0, 0, 0, 255), width=6)
    d.text((184, 140), "?", font=font(120), fill=(255, 230, 120, 255), anchor="mm", stroke_width=4,
           stroke_fill=(0, 0, 0, 255))
    d.text((184, 420), name[:14], font=font(30), fill=(255, 255, 255, 255), anchor="mm", stroke_width=3,
           stroke_fill=(0, 0, 0, 255))
    por.save(os.path.join(art, "%s_PORTRAIT.png" % code))


def write_file(folder, archive_path, data):
    p = os.path.join(folder, *archive_path.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(data)
    return p


def other_codes(mods_dir, skip):
    """slot codes of the other mod folders next to this one."""
    out = {}
    try:
        names = os.listdir(mods_dir)
    except OSError:
        return out
    for n in names:
        f = os.path.join(mods_dir, n)
        if os.path.normcase(os.path.abspath(f)) == os.path.normcase(os.path.abspath(skip)):
            continue
        if os.path.isfile(os.path.join(f, "slot.toml")):
            try:
                _, slots = slotfile.read(f)
            except KitError:
                continue
            for s in slots:
                out[s.code] = n
    return out


def plan_files(game, bcode, code, colours, models, fs):
    """[(source path, new path, kind)] - kind: copy / nux / nut. And the slot.toml file lines."""
    plan, lines = [], []
    for k in KEY_ORDER:
        if k == "vox_csb":
            if fs["vox_csb"]:
                for src in fs["vox_csb"]:
                    plan.append((src, rename_path(src, bcode, code), "copy"))
                lines.append((k, "BATTLE_%s_VOX.CSB" % code))
            continue
        src = fs.get(k)
        if not src:
            continue
        kind = "nux" if src.endswith(".NUX") else "nut" if src.endswith(".NUT") else "copy"
        dst = rename_path(src, bcode, code)
        plan.append((src, dst, kind))
        lines.append((k, dst))
    for c in range(colours):
        for m in models:
            src = fs["model"].get((c, m))
            sk = skeleton_for(fs, c, m)
            if not src or not sk:
                raise KitError("%s has no model / skeleton for colour %d model %d" % (bcode, c, m))
            plan.append((src, "PAC/CMN/CH/BC%s/BC%s0%d%d.NUX" % (code, code, c, m), "nux"))
            plan.append((sk, "PAC/CMN/CH/BC%s/BC%s0%d%d.AMB" % (code, code, c, m), "copy"))
    lines.append(("model", ["PAC/CMN/CH/BC%s/BC%s0{c}{m}.NUX" % (code, code)]))
    lines.append(("skeleton", ["PAC/CMN/CH/BC%s/BC%s0{c}{m}.AMB" % (code, code)]))
    for _s, dst, _k in plan:
        if len(dst) > MAX_PATH:
            raise KitError("%s would be longer than %d characters" % (dst, MAX_PATH))
    return plan, lines


def base_ultimates(game, base_id):
    """The base character's Ultimate window list [(text id, ultimate slot)] from default.xex
    (pointer table 0x826DF6D8 + id * 4 -> u16 count, then (u16 text, u16 slot) pairs), or None."""
    import struct
    try:
        im = game.image().data
        p = struct.unpack_from(">I", im, 0x826DF6D8 + base_id * 4 - 0x82000000)[0]
        n = struct.unpack_from(">H", im, p - 0x82000000)[0]
        if not 1 <= n <= 3:
            return None
        return [struct.unpack_from(">HH", im, p - 0x82000000 + 2 + 4 * i) for i in range(n)]
    except Exception:  # noqa: BLE001 - optional (no default.xex): the base's locked list stays
        return None


def form_count(game, base_id):
    """The base character's form count (character record +4 in default.xex), or None."""
    try:
        return game.image().u16(0x826BC2A8 + base_id * 980 + 4)
    except Exception:  # noqa: BLE001 - optional information
        return None


def run(game, base, code, name, out, colours=None, max_form=0, author="", force=False, enable=False):
    base_id, bcode = parse_base(base)
    code = check_code(code)
    name = name or code
    out = os.path.abspath(out)
    if any(p.startswith("PAC/CMN/CH/BC%s/" % code) or "_%s_" % code in p for p in game.cpk.paths()):
        raise KitError("your game already has files with the code %s - pick another code" % code)
    if os.path.isdir(out) and os.listdir(out):
        if not force:
            raise KitError("%s already exists and isn't empty (use --force to write into it)" % out)
    clash = other_codes(os.path.dirname(out), out)
    if code in clash:
        raise KitError("the mod %s already uses the code %s - every slot needs its own code" % (clash[code], code))

    fs = game.character_files(bcode)
    if "m0" not in fs:
        raise KitError("no moveset for %s in your game's archive (is this the US game?)" % bcode)
    avail = sorted({c for (c, m) in fs["model"] if m == 0})
    n_avail = 0
    while n_avail in avail:
        n_avail += 1
    if colours is None:
        colours = n_avail
    if colours < 1 or colours > n_avail:
        raise KitError("%s has %d colour(s) in your files - --colours must be 1-%d" % (bcode, n_avail, n_avail))
    if max_form:
        fc = form_count(game, base_id)
        if fc is not None and max_form > fc - 1:
            raise KitError("%s has %d form(s): --max-form can be 0-%d" % (bcode, fc, fc - 1))
        models = sorted({m for (c, m) in fs["model"] if c == 0})
    else:
        models = [0]

    plan, lines = plan_files(game, bcode, code, colours, models, fs)
    hmap = T.HandleMap(code, collect_handles(game, fs, plan))
    first, end = T.handle_block(code)
    info("New slot %s \"%s\" from %s (%s): %d colour(s), model(s) %s, max_form %d" % (
        code, name, bcode, ID_TO_NAME[base_id], colours, models, max_form))
    info("Texture handles: %08X-%08X (%d used)" % (first, end - 1, len(hmap.map)))
    os.makedirs(out, exist_ok=True)
    total = 0
    for src, dst, kind in plan:
        data = game.read(src)
        if kind == "nux":
            old_set, old_nub = T.nux_set_names(data)
            s, b = set_names_for(old_set, old_nub, bcode, code, dst)
            data = T.renumber_nux(data, hmap, s, b)
            note = "set %s -> %s, handles moved" % (old_set, s)
        elif kind == "nut":
            data = T.renumber_nut(data, hmap)
            note = "handles moved"
        else:
            note = "copied"
        write_file(out, dst, data)
        total += len(data)
        info("  %-44s %9s  (%s)" % (dst, human_size(len(data)), note))

    make_placeholder_art(out, code, name)
    ults = base_ultimates(game, base_id)
    if ults:
        info("Ultimates: %s's list %s (always selectable for the slot)" % (bcode, ults))
    else:
        warn("couldn't read %s's Ultimate list from default.xex - the slot shows %s's list, locked as in "
             "the save" % (bcode, bcode))
    text = slotfile.slot_toml_text(code, base_id, bcode, ID_TO_NAME[base_id], name, colours, max_form, lines,
                                   __version__, ults)
    with open(os.path.join(out, "slot.toml"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    with open(os.path.join(out, "mod.toml"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(slotfile.mod_toml_text(
            name, author or "unknown", "0.1",
            "Extra character %s (code %s, plays as %s). R2 on the Versus / Training character select. "
            "Made with blmodkit; needs no game files from anyone else - they come from your own copy."
            % (name, code, ID_TO_NAME[base_id])))
    info("Wrote %s: %d game files (%s), slot.toml, mod.toml, placeholder art" % (out, len(plan), human_size(total)))
    if enable:
        enable_mod(out)
    info("Next: python -m blmodkit art --src <picture.png> --mod \"%s\"   then   python -m blmodkit check \"%s\""
         % (out, out))
    return out


def enable_mod(folder):
    """Add the folder to mods\\mods.toml's enabled list (the game reads it at start)."""
    from . import tomlmini
    mods = os.path.dirname(os.path.abspath(folder))
    name = os.path.basename(os.path.abspath(folder))
    p = os.path.join(mods, "mods.toml")
    enabled = []
    if os.path.isfile(p):
        try:
            enabled = list(tomlmini.load(p).get("enabled", []))
        except tomlmini.TomlError as e:
            warn("can't read %s (%s) - not changed" % (p, e))
            return
    if name in enabled:
        info("%s is already on in %s" % (name, p))
        return
    enabled.append(name)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("enabled = [" + ", ".join(tomlmini.quote(x) for x in enabled) + "]\n")
    info("Switched %s on in %s (restart the game to load it)" % (name, p))
