"""fix-textures: give an existing slot mod its own texture handles and texture-set names.

    python -m blmodkit fix-textures --mod mods\\My_Slot [--dry-run]

For slots made by hand (files copied from a game character and renamed): every texture handle
of the slot's .NUX / .NUT files that is outside the slot's own handle block is moved into it
(textures and the materials that use them, consistently across the slot's files), and every
.NUX whose texture-set name is the game's (or another file's) gets a name of its own from its
file name. Sizes don't change. The original files are backed up first to
%LOCALAPPDATA%\\blmodkit\\backup\\<mod>\\<time>\\.
"""
import os
import shutil
import time

from . import slotfile
from . import textures as T
from .util import cache_dir, info, warn


def slot_texture_files(mod, slot):
    """[(archive path, file)] of the slot's .NUX / .NUT files."""
    out = []
    for k in ("esp", "eas", "fx_model", "arst", "bufac"):
        p = slot.file(k)
        if p:
            f = slotfile.mod_file(mod, p)
            if f:
                out.append((p, f))
    for p in slot.costume_paths("model", lambda x: slotfile.mod_file(mod, x) is not None).values():
        f = slotfile.mod_file(mod, p)
        if f:
            out.append((p, f))
    return out


def run(mod, game=None, dry_run=False):
    _, slots = slotfile.read(mod)
    game_sets = set(T.game_index(game)["sets"]) if game is not None else set()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(cache_dir("backup", os.path.basename(os.path.abspath(mod))), stamp)
    for slot in slots:
        first, end = T.handle_block(slot.code)
        files = slot_texture_files(mod, slot)
        if not files:
            info("[%s] no .NUX / .NUT files" % slot.code)
            continue
        datas = {p: open(f, "rb").read() for p, f in files}
        used = set()
        for p, d in datas.items():
            used |= {g for g, _o, _d in T.scan_gidx(d)}
        inside = sorted(g for g in used if first <= g < end)
        outside = sorted(g for g in used if not first <= g < end)
        hmap = T.HandleMap(slot.code, [])
        hmap.map = {g: g for g in inside}
        hmap.next_free = (max(inside) + 1) if inside else first
        for g in outside:
            _ = hmap[g]
        names_seen = {}
        changed = 0
        for p, f in files:
            d = datas[p]
            new_names = None
            if d[:4] == b"#NUX":
                s, b = T.nux_set_names(d)
                stem = os.path.splitext(p.replace("\\", "/").split("/")[-1])[0]
                if s.upper() in game_sets or s.upper() in names_seen or (game is None and slot.code not in s.upper()):
                    new_names = ((stem + "_B.NUT") if stem.upper().startswith("BC") else (stem + ".nut"),
                                 stem + ".NUB")
                names_seen[(new_names[0] if new_names else s).upper()] = p
                out = T.renumber_nux(d, hmap, *(new_names or (None, None)))
            else:
                out = T.renumber_nut(d, hmap)
            if out != d:
                changed += 1
                note = []
                if new_names:
                    note.append("set %s -> %s" % (T.nux_set_names(d)[0], new_names[0]))
                moved = sum(1 for g, _o, _d in T.scan_gidx(d) if not first <= g < end)
                if moved:
                    note.append("%d handle(s) moved" % moved)
                info("  [%s] %-44s %s" % (slot.code, p, ", ".join(note)))
                if not dry_run:
                    dst = os.path.join(backup, *p.split("/"))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(f, dst)
                    with open(f, "wb") as fh:
                        fh.write(out)
        info("[%s] %d handle(s) moved into %08X-%08X, %d file(s) %s" % (
            slot.code, len(outside), first, end - 1, changed, "would change (dry run)" if dry_run else "changed"))
    if not dry_run and os.path.isdir(backup):
        info("Originals backed up to %s" % backup)
    if game is None:
        warn("done without your game: texture-set names were renamed only when they lacked the slot code")
