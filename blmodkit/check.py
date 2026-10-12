"""check: is a slot mod ready to load? Prints clear errors / warnings and returns True when it passes.

    python -m blmodkit check mods\\My_Slot

What is checked (per [[slot]] of slot.toml):
  slot.toml   readable; keys known (typos are reported); code / base / colours / max_form / name /
              ultimates / dramas values; the code unique among the switched-on mods
  files       every path: <= 56 characters, ASCII, under PAC/ or SOUND/, present in the mod folder,
              not one of the game's own paths (that would replace the game's file for everyone),
              the right kind of file; every colour has its model + skeleton
  sizes       moveset (m0) within the size the game loads (tested: 4.66 MB / 4.8 MB fine, 5.24 MB
              crashed); costume models <= ~8.9 MB; effect models <= 16 models
  textures    every texture handle (GIDX) of the slot's .NUX / .NUT files: not used by the game or by
              another switched-on mod with other pixels; every material's texture is in its own file;
              .NUX texture-set names unique (vs the game, other mods, the mod's other files)
  art         card 512x120, big picture 368x720 (PNG or NUT), HUD faces 236x268, one per form
  voice       US and JP voice banks and the lip-sync keys
  other files files the game would ignore or that would replace a game file
"""
import os

from . import slotfile
from . import textures as T
from .ntxr import Nut
from .util import ID_TO_CODE, ID_TO_NAME, MAX_PATH, RESERVED_CODES, Report, human_size

M0_TESTED = 4_700_000      # Kid Buu's 4.66 MB moveset: fine
M0_LIMIT = 4_800_000       # 4.8 MB (padded Gohan moveset) loads; 5.24 MB crashed while loading the battle
NUX_LIMIT = 8_900_000      # biggest costume model tested: 8.63 MB
FX_MODELS_LIMIT = 16       # effect model list: 16 entries, no bounds check in the game

KIND_EXT = {"m0": ".AMB", "m1": ".AMB", "l000": ".AMB", "se_csb": ".CSB", "vox_amk": ".AMK", "bsp": ".AMB",
            "esp": ".NUT", "eas": ".NUT", "fx_model": ".NUX", "arst": ".NUX", "bar": ".AMB", "bufac": ".NUT"}
MAGIC = {".NUX": b"#NUX", ".NUT": b"NTXR", ".CSB": b"@UTF", ".AMB": b"#AMB", ".AMK": b"#AMK"}


def _read(path, n=None):
    with open(path, "rb") as fh:
        return fh.read() if n is None else fh.read(n)


def enabled_mods(mods_dir):
    from . import tomlmini
    p = os.path.join(mods_dir, "mods.toml")
    if not os.path.isfile(p):
        return None
    try:
        return list(tomlmini.load(p).get("enabled", []))
    except tomlmini.TomlError:
        return None


def mod_texture_files(folder):
    out = []
    for root, _dirs, files in os.walk(folder):
        for f in files:
            if f.upper().endswith((".NUX", ".NUT")):
                out.append(os.path.join(root, f))
    return out


def png_size(path):
    from PIL import Image
    with Image.open(path) as im:
        return im.size


def run(mod, game=None, mods_dir=None, report=None):
    """report: a util.Report to fill (the GUI reads its errors / warnings / notes afterwards)."""
    mod = os.path.abspath(mod)
    rep = report if report is not None else Report()
    title = "blmodkit check: %s" % mod
    try:
        data, slots = slotfile.read(mod)
    except Exception as e:  # noqa: BLE001
        rep.error(str(e))
        return rep.print(title)
    if not slots:
        rep.error("slot.toml has no [[slot]] table")
        return rep.print(title)
    for k in data:
        if k != "slot":
            rep.warning("slot.toml: unknown top-level key %r (ignored by the game)" % k)
    mods_dir = os.path.abspath(mods_dir or os.path.dirname(mod))
    me = os.path.basename(mod)
    en = enabled_mods(mods_dir)
    if en is None:
        rep.warning("no mods.toml in %s - is this the game's mods folder? (switch the mod on in the game's "
                    "Mods menu)" % mods_dir)
        en = []
    elif me in en:
        rep.note("switched on in mods.toml")
    else:
        rep.warning("not switched on: add it in the game's Mods menu (or mods.toml enabled = [..., \"%s\"]) and "
                    "restart the game" % me)
    others = [os.path.join(mods_dir, m) for m in en if m != me and os.path.isdir(os.path.join(mods_dir, m))]

    # what the other switched-on mods use
    other_codes, other_handles, other_sets, other_slot_count = {}, {}, {}, 0
    for o in others:
        if os.path.isfile(os.path.join(o, "slot.toml")):
            try:
                _, os_slots = slotfile.read(o)
                for s in os_slots:
                    other_codes[s.code] = os.path.basename(o)
                    other_slot_count += 1
            except Exception:  # noqa: BLE001
                pass
        for f in mod_texture_files(o):
            rel = os.path.basename(o) + "/" + os.path.relpath(f, o).replace("\\", "/")
            d = _read(f)
            for g, _off, dig in T.scan_gidx(d):
                other_handles.setdefault(g, []).append((rel, dig))
            if d[:4] == b"#NUX":
                other_sets.setdefault(T.nux_set_names(d)[0].upper(), []).append(rel)

    gidx = None
    if game is not None:
        gidx = T.game_index(game)
        game_paths = set(game.cpk.paths())
    else:
        game_paths = set()

    declared_all = set()
    own_handles = {}     # handle -> [(file, digest)]
    own_sets = {}        # set name -> [(file, sha of file)]
    if len(slots) + other_slot_count > slotfile.MAX_SLOTS_ON_PAGE:
        rep.warning("%d extra characters in the switched-on mods - the R2 page shows %d (mod folders in "
                    "alphabetical order)" % (len(slots) + other_slot_count, slotfile.MAX_SLOTS_ON_PAGE))
    for slot in slots:
        check_slot(slot, mod, rep, game, game_paths, other_codes, declared_all, own_handles, own_sets)

    # ---- textures across files (grouped per file: (file, other file) -> [handles])
    shared_same, shared_diff, mod_same, mod_diff = {}, {}, {}, {}
    for g, users in sorted(own_handles.items()):
        digs = {d for _f, d in users}
        if len(digs) > 1:
            rep.error("texture handle %08X is in %s with DIFFERENT pixels - each changed texture needs its "
                      "own handle" % (g, ", ".join(sorted({f for f, _ in users}))))
        if gidx is not None:
            hit = gidx["handles"].get("%08X" % g)
            if hit:
                same = any(d in digs for _f, d in hit)
                key = (users[0][0], sorted({f for f, _d in hit})[0])
                (shared_same if same else shared_diff).setdefault(key, []).append(g)
        if g in other_handles:
            same = any(d in digs for _f, d in other_handles[g])
            key = (users[0][0], other_handles[g][0][0])
            (mod_same if same else mod_diff).setdefault(key, []).append(g)

    def hl(gs):
        return ", ".join("%08X" % g for g in gs[:4]) + (" ... (%d)" % len(gs) if len(gs) > 4 else "")
    for (f, o), gs in sorted(shared_diff.items()):
        rep.error("%s: texture handle(s) %s are also the game's (%s) with OTHER pixels - one fighter would show "
                  "the other's texture. Make the slot with new-slot (it moves every handle into the slot's own "
                  "block)" % (f, hl(gs), o))
    for (f, o), gs in sorted(shared_same.items()):
        rep.warning("%s: %d texture handle(s) (%s) are the game's own (%s) with the same pixels - fine until you "
                    "change those textures" % (f, len(gs), hl(gs), o))
    for (f, o), gs in sorted(mod_diff.items()):
        rep.error("%s: texture handle(s) %s are also in the switched-on mod file %s with OTHER pixels - the two "
                  "can show each other's textures" % (f, hl(gs), o))
    for (f, o), gs in sorted(mod_same.items()):
        rep.warning("%s: texture handle(s) %s are also in the switched-on mod file %s (same pixels)" % (f, hl(gs), o))
    first, end = T.handle_block(slots[0].code)
    n_in = sum(1 for g in own_handles if first <= g < end)
    if own_handles:
        rep.note("%d texture handle(s); %d inside the slot's own block %08X-%08X" % (
            len(own_handles), n_in, first, end - 1))
    for name, users in sorted(own_sets.items()):
        files = sorted({f for f, _h in users})
        if len({h for _f, h in users}) > 1:
            rep.error("texture-set name %s is inside %d different files of this mod (%s) - the game keeps one "
                      "set per name, the others get its textures" % (name, len(files), ", ".join(files)))
        if gidx is not None and name in gidx["sets"]:
            rep.error("texture-set name %s (%s) is the game's own (%s) - give the file a name of its own "
                      "(new-slot does)" % (name, files[0], gidx["sets"][name][0]))
        if name in other_sets:
            rep.error("texture-set name %s (%s) is also in the switched-on mod file %s" % (
                name, files[0], other_sets[name][0]))
    if own_sets and not rep.errors:
        rep.note("%d .NUX texture-set name(s), all unique" % len(own_sets))

    # ---- files in the folder nobody asked for
    pictures = set()
    for slot in slots:
        for key in ("card", "portrait"):
            if slot.t.get(key):
                pictures.add(slotfile.norm(slot.t[key]))
    for root, _dirs, files in os.walk(mod):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), mod).replace("\\", "/")
            up = rel.upper()
            if up in ("SLOT.TOML", "MOD.TOML") or up in pictures or up in declared_all:
                continue
            if up.startswith("ART/"):
                continue          # previews / sources next to the pictures
            if up in game_paths:
                rep.warning("%s replaces the game's own file for everyone (not only this character)" % rel)
            elif any(p.endswith("/" + f.upper()) for p in game_paths):
                rep.warning("%s has the name of a game file - the game will use it INSTEAD of that file "
                            "everywhere; rename or remove it" % rel)
            else:
                rep.note("%s is not named in slot.toml - the game ignores it" % rel)
    return rep.print(title)


def check_slot(slot, mod, rep, game, game_paths, other_codes, declared_all, own_handles, own_sets):
    t = slot.t
    tag = "[%s] " % (slot.code or "?")
    for k in t:
        if k not in slotfile.SLOT_KEYS:
            rep.warning(tag + "unknown key %r in [[slot]] (typo? the game ignores it)" % k)
    for k in slot.files:
        if k not in slotfile.ALL_FILE_KEYS:
            rep.warning(tag + "unknown key %r in [slot.files] (typo? the game ignores it) - known: %s" % (
                k, ", ".join(slotfile.ALL_FILE_KEYS)))
    # ---- values
    code = slot.code
    if len(code) != 3 or not code.isalnum() or not code.isascii():
        rep.error(tag + "code must be 3 letters / digits (got %r) - the game skips this slot" % t.get("code"))
    elif code in RESERVED_CODES:
        rep.error(tag + "code %s is one of the game's characters - its file names would replace the game's" % code)
    elif code in other_codes:
        rep.error(tag + "code %s is also used by the switched-on mod %s" % (code, other_codes[code]))
    else:
        rep.note(tag + "code %s" % code)
    base = t.get("base", 0)
    if not isinstance(base, int) or base not in ID_TO_CODE:
        rep.error(tag + "base must be a character id 0-20 (got %r) - the game skips this slot" % base)
        base = None
    else:
        rep.note(tag + "plays as %s (%s)" % (ID_TO_NAME[base], ID_TO_CODE[base]))
    colours = t.get("colours", t.get("colors", 1))
    if not isinstance(colours, int) or not 1 <= colours <= 8:
        rep.error(tag + "colours must be 1-8 (got %r) - the game skips this slot" % colours)
        colours = 1
    mf = t.get("max_form", 0)
    if not isinstance(mf, int) or mf < 0:
        rep.error(tag + "max_form must be 0 or more (got %r)" % mf)
    elif mf and game is not None and base is not None:
        try:
            fc = game.image().u16(0x826BC2A8 + base * 980 + 4)
            if mf > fc - 1:
                rep.warning(tag + "max_form %d but %s has %d form(s) - forms above %d don't exist" % (
                    mf, ID_TO_NAME[base], fc, fc - 1))
        except Exception:  # noqa: BLE001
            pass
    name = t.get("name", "")
    if not isinstance(name, str) or not name:
        rep.warning(tag + "no name: the code is shown")
    elif not all(32 <= ord(c) < 127 for c in name):
        rep.error(tag + "name %r has non-ASCII characters - the game's font can't show them" % name)
    elif len(name) > 24:
        rep.warning(tag + "name %r is long (%d characters) - it may not fit the HUD" % (name, len(name)))
    dr = t.get("dramas", "off")
    if dr not in ("off", "on", "base"):
        rep.warning(tag + "dramas should be \"off\" or \"on\" (got %r): treated as off" % dr)
    if "tint" in t and not isinstance(t["tint"], int):
        rep.error(tag + "tint must be a number like 0xFF0A8CDC")
    ults = t.get("ultimates")
    if ults is not None:
        if not isinstance(ults, list) or len(ults) > 3:
            rep.error(tag + "ultimates must be a list of up to 3 [name, slot] pairs")
        else:
            for u in ults:
                if not (isinstance(u, list) and len(u) == 2 and isinstance(u[0], (str, int)) and
                        isinstance(u[1], int) and 0 <= u[1] <= 2):
                    rep.error(tag + "ultimate entry %r isn't [\"Name\" or text id, base ultimate slot 0-2]" % (u,))
                elif isinstance(u[0], str) and not all(32 <= ord(c) < 127 for c in u[0]):
                    rep.error(tag + "ultimate name %r has non-ASCII characters" % u[0])
    if not isinstance(slot.files, dict) or not slot.files:
        rep.error(tag + "no [slot.files] - the game skips this slot")
        return

    own = 0

    def present(key, path, what):
        """the file in the mod for an archive path, with the path rules; None when unusable"""
        bad = slotfile.check_archive_path(path)
        if bad:
            rep.error(tag + "%s: %s - the base character's file is used instead" % (key, bad))
            return None
        up = slotfile.norm(path)
        declared_all.add(up)
        if up in game_paths:
            rep.error(tag + "%s: %s is one of the game's own paths - your file would REPLACE it for everyone; "
                      "use the slot's own names (BC%s...)" % (key, path, code))
        f = slotfile.mod_file(mod, path)
        if not f:
            rep.error(tag + "%s: %s is not in the mod folder - the base character's %s is used" % (key, path, what))
            return None
        return f

    def file_kind_ok(key, f, ext):
        head = _read(f, 4)
        want = MAGIC.get(ext)
        if want and head != want and not (ext == ".CSB" and head[:4] in (b"@UTF",)):
            rep.error(tag + "%s: %s doesn't look like a %s file (starts with %r)" % (key, os.path.basename(f), ext,
                                                                                     head))
            return False
        return True

    for key in slotfile.FILE_KEYS:
        path = slot.file(key)
        what = slotfile.FILE_WHAT[key]
        if not path:
            rep.note(tag + "%s: not set - %s's %s is used" % (key, ID_TO_NAME.get(base, "the base"), what))
            continue
        f = present(key, path, what)
        if not f:
            continue
        ext = KIND_EXT[key]
        if not path.upper().endswith(ext):
            rep.warning(tag + "%s: %s should be a %s file" % (key, path, ext))
        if not file_kind_ok(key, f, ext):
            continue
        own += 1
        size = os.path.getsize(f)
        if key == "m0":
            if size > M0_LIMIT:
                rep.error(tag + "moveset %s is %s - above %s the game can crash while loading the battle "
                          "(5.24 MB did); remove unused motions" % (path, human_size(size), human_size(M0_LIMIT)))
            elif size > M0_TESTED:
                rep.warning(tag + "moveset %s is %s - close to the limit (tested fine up to %s, 5.24 MB "
                            "crashed)" % (path, human_size(size), human_size(M0_LIMIT)))
            else:
                rep.note(tag + "moveset %s (%s, limit ~%s)" % (path, human_size(size), human_size(M0_TESTED)))
        if ext in (".NUX", ".NUT"):
            textures_of(tag, key, path, f, rep, own_handles, own_sets)
        if key == "fx_model":
            n = T.nub_count(_read(f))
            if n is not None and n > FX_MODELS_LIMIT:
                rep.error(tag + "fx_model %s has %d models - the game's list holds %d" % (path, n, FX_MODELS_LIMIT))
        if key == "bufac":
            check_bufac(tag, f, rep, mf if isinstance(mf, int) else 0)

    # voice
    vox = slot.file("vox_csb")
    if vox:
        name_only = vox.replace("\\", "/").split("/")[-1]
        if name_only != vox:
            rep.note(tag + "vox_csb: only the name %s is used (the game looks in SOUND/CSB/US/ and JP/)" % name_only)
        if len(name_only) + 13 > MAX_PATH:
            rep.error(tag + "vox_csb name %s is too long" % name_only)
        found = []
        for p in slot.voice_paths():
            declared_all.add(slotfile.norm(p))
            f = slotfile.mod_file(mod, p)
            if f:
                found.append(p.split("/")[2])
                if p.upper() in game_paths:
                    rep.error(tag + "%s is the game's own voice bank path" % p)
                else:
                    file_kind_ok("vox_csb", f, ".CSB")
        if not found:
            rep.error(tag + "voice bank %s is in neither SOUND/CSB/US/ nor SOUND/CSB/JP/ of the mod - the base "
                      "character's voice is used" % name_only)
        else:
            own += 1
            if len(found) == 1:
                rep.warning(tag + "voice bank only for %s - with the other language the base character's voice "
                            "plays" % found[0])
            else:
                rep.note(tag + "voice banks US + JP")
            if not slot.file("vox_amk"):
                rep.warning(tag + "voice without vox_amk (lip-sync keys): the mouth follows the base character's lines")
    else:
        rep.note(tag + "no own voice (vox_csb): the base character's voice")

    # costumes
    if not slot.patterns("model") or not slot.patterns("skeleton"):
        rep.warning(tag + "model / skeleton not set: the base character's costumes are shown")
    else:
        def probe(p):
            return slotfile.mod_file(mod, p) is not None
        models = slot.costume_paths("model", probe if slot.max_form else None)
        skels = slot.costume_paths("skeleton", probe if slot.max_form else None)
        for (c, m), p in sorted(models.items()):
            sp = skels.get((c, m))
            fm = present("model", p, "model")
            fs = present("skeleton", sp, "skeleton") if sp else None
            if not fm or not fs:
                rep.error(tag + "colour %d model %d: needs both a model (.NUX) and a skeleton (.AMB) - the base "
                          "character's pair is shown" % (c, m))
                continue
            own += 1
            if not file_kind_ok("model", fm, ".NUX") or not file_kind_ok("skeleton", fs, ".AMB"):
                continue
            size = os.path.getsize(fm)
            if size > NUX_LIMIT:
                rep.error(tag + "costume model %s is %s - above ~%s (the biggest tested) it may not load" % (
                    p, human_size(size), human_size(NUX_LIMIT)))
            textures_of(tag, "model", p, fm, rep, own_handles, own_sets)
        rep.note(tag + "%d costume(s) (colours x models) with model + skeleton" % len(models))
    if not own:
        rep.error(tag + "none of the slot's own files are there - the game won't use this slot")

    # art
    for key, size, what in (("card", (512, 120), "card"), ("portrait", (368, 720), "big picture")):
        rel = t.get(key)
        if not rel:
            rep.warning(tag + "no %s: Teen Gohan's is shown" % what)
            continue
        f = os.path.join(mod, *rel.replace("\\", "/").split("/"))
        if not os.path.isfile(f):
            rep.error(tag + "%s %s is not in the mod - Teen Gohan's is shown" % (what, rel))
            continue
        head = _read(f, 4)
        if head == b"NTXR":
            try:
                e = Nut(_read(f)).entries[0]
                got = (e.width, e.height)
            except Exception:  # noqa: BLE001
                rep.error(tag + "%s %s is a NUT the game can't read" % (what, rel))
                continue
        else:
            try:
                got = png_size(f)
            except Exception:  # noqa: BLE001
                rep.error(tag + "%s %s is not a picture the game reads (PNG or NUT)" % (what, rel))
                continue
            if head != b"\x89PNG":
                rep.error(tag + "%s %s must be a PNG (or a NUT)" % (what, rel))
                continue
        if got != size:
            rep.warning(tag + "%s %s is %dx%d - the game resizes it to %dx%d" % (what, rel, got[0], got[1], *size))
        else:
            rep.note(tag + "%s %s %dx%d" % (what, rel, *got))


def textures_of(tag, key, path, f, rep, own_handles, own_sets):
    d = _read(f)
    rel = path
    hs = T.scan_gidx(d)
    for g, _off, dig in hs:
        own_handles.setdefault(g, []).append((rel, dig))
    if d[:4] == b"#NUX":
        import hashlib
        own_sets.setdefault(T.nux_set_names(d)[0].upper(), []).append((rel, hashlib.sha1(d).hexdigest()))
        try:
            have = {g for g, _o, _d in hs}
            want = T.material_handles(d)
            missing = [g for g in want if g not in have]
            if missing:
                rep.error(tag + "%s: its materials use texture handle(s) %s that aren't in the file - the game "
                          "crashes or draws the wrong texture" % (path, ", ".join("%08X" % g for g in missing[:6])))
        except Exception as e:  # noqa: BLE001
            rep.error(tag + "%s: the model can't be read (%s)" % (path, e))


def check_bufac(tag, f, rep, max_form):
    try:
        nut = Nut(_read(f))
    except Exception as e:  # noqa: BLE001
        rep.error(tag + "bufac: not a NUT the game can read (%s)" % e)
        return
    sizes = {(e.width, e.height) for e in nut.entries}
    if sizes != {(236, 268)}:
        rep.warning(tag + "bufac: faces should be 236x268 (found %s)" % sorted(sizes))
    if len(nut.entries) < max_form + 1:
        rep.error(tag + "bufac: %d face(s) for %d form(s) - the HUD needs one per form" % (len(nut.entries),
                                                                                          max_form + 1))
    else:
        rep.note(tag + "bufac: %d HUD face(s) 236x268" % len(nut.entries))
