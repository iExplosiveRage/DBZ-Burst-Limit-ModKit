"""extract: copy files out of YOUR game's archive (for study / editing in other tools).

    python -m blmodkit extract --out extracted --char VGT        # Vegeta's battle files + sound banks
    python -m blmodkit extract --out extracted --filter BUFAC_    # every HUD face bank
    python -m blmodkit extract --out extracted --char GOK --list  # just list

Files keep their archive paths (extracted\\PAC\\CMN\\CH\\BCVGT\\...). A character's set: the
PAC/CMN/CH/BC<code>/ folder (moveset, models, skeletons, effects), the HUD face bank, the
select-screen pictures, and the SOUND/CSB banks (sound effects + US / JP voice).
Extracted files are the game's - keep them for yourself, never share them.
"""
import os

from .util import KitError, human_size, info, parse_base


def run(game, out, chars=None, flt=None, list_only=False):
    paths = []
    if chars:
        for c in chars:
            _, code = parse_base(c)
            paths += game.all_character_paths(code)
    if flt:
        f = flt.upper().replace("\\", "/")
        pool = paths if chars else game.cpk.paths()
        paths = [p for p in pool if f in p]
    if not chars and not flt:
        paths = game.cpk.paths()
    paths = sorted(set(paths))
    if not paths:
        raise KitError("nothing matched")
    total = 0
    for p in paths:
        size = game.cpk.size(p)
        total += size
        if list_only:
            info("%-56s %10s" % (p, human_size(size)))
            continue
        dest = os.path.join(out, *p.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(game.read(p))
        info("  %-56s %10s" % (p, human_size(size)))
    info("%s %d file(s), %s%s" % ("Listed" if list_only else "Extracted", len(paths), human_size(total),
                                  "" if list_only else " -> " + os.path.abspath(out)))
