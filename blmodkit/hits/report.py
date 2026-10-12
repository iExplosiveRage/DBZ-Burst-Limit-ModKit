"""hits: run the chain hit simulator on a slot's moveset and print the report.

    python -m blmodkit hits --mod mods\\My_Slot [-v] [--json results.json]

Uses the slot's moveset (m0) and its first skeleton (colour 0, model 0) - or the base character's
when the slot doesn't have its own - against standing Goku and Krillin (their idle hurt spheres,
radii from YOUR default.xex) - and, for comparison, the base character's own moveset. Prints, per dummy, ground and air: how many strings connect
completely and the share of hits that land, then the ground strings that miss somewhere.
"""
import json
import os

from .. import slotfile
from ..util import ID_TO_CODE, KitError, info
from .sim import DUMMY_PARAMS, hurt_spheres, simulate


def summary(res, dummies):
    lines = []
    for dn in dummies:
        for air in (False, True):
            rows = [r for r in res if r["dummy"] == dn and r["air"] == air]
            if not rows:
                continue
            full = sum(1 for r in rows if r["max_hits"] == r["n_spans"])
            h = sum(r["max_hits"] for r in rows)
            n = sum(r["n_spans"] for r in rows)
            lines.append("  vs %s %s: %3d strings, all hits connect in %3d, %4d/%-4d hits (%5.1f%%)" % (
                dn, "AIR" if air else "GND", len(rows), full, h, n, 100.0 * h / max(1, n)))
    return lines


def run(game, mod, slot_code=None, dummies="GOK,KLL", verbose=False, json_out=None, baseline=True):
    _, slots = slotfile.read(mod)
    slot = slots[0] if not slot_code else next((s for s in slots if s.code == slot_code.upper()), None)
    if slot is None:
        raise KitError("no such slot in %s" % mod)
    bcode = ID_TO_CODE.get(slot.base)
    m0 = slot.file("m0")
    f = slotfile.mod_file(mod, m0) if m0 else None
    if f:
        moveset, mname = open(f, "rb").read(), m0
    else:
        mname = "PAC/CMN/CH/BC%s/BC%sM0.AMB" % (bcode, bcode)
        moveset = game.read(mname)
        info("The slot has no moveset of its own - simulating %s's" % bcode)
    sk = slot.costume_paths("skeleton").get((0, 0))
    fs = slotfile.mod_file(mod, sk) if sk else None
    if fs:
        skel, sname = open(fs, "rb").read(), sk
    else:
        sname = "PAC/CMN/CH/BC%s/BC%s000.AMB" % (bcode, bcode)
        skel = game.read(sname)
    ds = [d.strip().upper() for d in dummies.split(",") if d.strip()]
    for d in ds:
        if d not in DUMMY_PARAMS:
            raise KitError("dummy %s: only %s" % (d, ", ".join(DUMMY_PARAMS)))
    info("Moveset %s, skeleton %s, dummies %s" % (mname, sname, ", ".join(ds)))
    hurts = {d: hurt_spheres(game, d) for d in ds}

    def progress(k, n):
        if k % 10 == 0 or k == n:
            info("  simulated %d / %d strings" % (k, n))
    res, rig = simulate(moveset, skel, hurts, progress)
    if rig.root_s != 1.0:
        info("Skeleton root scale %.3f%s (sizes and reach scaled)" % (rig.root_s, " (non-uniform)" if rig.nu else ""))
    skipped = sum(r["skipped"] for r in res)
    base_lines = []
    if baseline and f:
        bm = "PAC/CMN/CH/BC%s/BC%sM0.AMB" % (bcode, bcode)
        bres, _ = simulate(game.read(bm), game.read("PAC/CMN/CH/BC%s/BC%s000.AMB" % (bcode, bcode)), hurts)
        base_lines = summary(bres, ds)
    print("=" * 72)
    print("Hit simulation: %s (%s)" % (slot.name, slot.code))
    print("=" * 72)
    for line in summary(res, ds):
        print(line)
    if base_lines:
        print("For comparison, the game's own %s moveset (retail moves miss some hits in this sim too):" % bcode)
        for line in base_lines:
            print(line)
    bad = [r for r in res if r["max_hits"] < r["n_spans"] and (verbose or not r["air"])]
    if bad:
        print("Strings where a hit misses (best start distance D0; per move: separation when it hits, "
              "'miss', or '-' after a blow-away):")
        for r in bad:
            print("  %s %s %-28s %2d/%-2d hits  D0 %4.1f  %s" % (
                r["dummy"], "AIR" if r["air"] else "GND", " ".join(r["path"]), r["max_hits"], r["n_spans"],
                r["best_D0"], r["best"]))
    if verbose:
        print("All strings:")
        for r in res:
            print("  %s %s %-28s %2d/%-2d hits  clean D0 %-12s" % (
                r["dummy"], "AIR" if r["air"] else "GND", " ".join(r["path"]), r["max_hits"], r["n_spans"],
                r["D0_clean"]))
    if skipped:
        print("Note: %d hitbox(es) name a joint this skeleton doesn't have - not simulated" % skipped)
    print("(A simulation of standing dummies with the game's rules - confirm in game: Training, dummy guard off.)")
    if json_out:
        with open(json_out, "w") as fh:
            json.dump(res, fh, indent=0)
        info("wrote " + os.path.abspath(json_out))
