"""Command line: python -m blmodkit <command> ...   (python -m blmodkit <command> --help for details)"""
import argparse
import os
import sys

from . import __version__
from .util import KitError, info, load_config, save_config


def _pair(s):
    from .art import parse_pair
    return parse_pair(s, "value")


def _box(s):
    try:
        v = [int(x) for x in s.split(",")]
        assert len(v) == 4
        return tuple(v)
    except (ValueError, AssertionError):
        raise argparse.ArgumentTypeError("expected X0,Y0,X1,Y1")


def cmd_config(a):
    cfg = load_config()
    if a.game:
        from .game import find_cpk
        cpk, root = find_cpk(a.game)
        if not cpk:
            raise KitError("no LONG2DATA_US.CPK found in or under %s" % a.game)
        cfg["game"] = os.path.abspath(a.game)
        save_config(cfg)
        info("Saved: game = %s (archive %s)" % (cfg["game"], cpk))
    else:
        info("game = %s" % cfg.get("game", "(not set)"))


def cmd_extract(a):
    from .extract import run
    from .game import Game
    run(Game(a.game), a.out, a.char, a.filter, a.list)


def cmd_new_slot(a):
    from .game import Game
    from .newslot import run
    run(Game(a.game), a.base, a.code, a.name, a.out, a.colours, a.max_form, a.author, a.force, a.enable)


def cmd_art(a):
    from .art import run
    run(a.src, a.mod, a.slot, a.face, a.face_size, a.subject, a.zoom, a.style, a.colours, a.ink, a.crop, a.flip,
        a.bg_tolerance, a.card_zoom, a.portrait_zoom, a.hud_zoom, a.card_shift or (0, 0),
        a.portrait_shift or (0, 0), a.hud_shift or (0, 0), a.only)


def cmd_check(a):
    from .check import run
    from .game import Game
    game = None
    try:
        game = Game(a.game)
    except KitError as e:
        info("NOTE: %s\n      (checks that compare with the game are skipped)" % e)
    ok = run(a.mod, game, a.mods_dir)
    sys.exit(0 if ok else 1)


def cmd_recolor(a):
    from .fx import run
    from .game import Game
    run(Game(a.game), a.mod, a.hue, a.slot, a.saturation, a.source, a.keep_earthy, a.aura_scale, a.dry_run)


def cmd_fix_textures(a):
    from .fixtex import run
    from .game import Game
    game = None
    try:
        game = Game(a.game)
    except KitError as e:
        info("NOTE: %s" % e)
    run(a.mod, game, a.dry_run)


def cmd_hits(a):
    from .game import Game
    from .hits.report import run
    run(Game(a.game), a.mod, a.slot, a.dummies, a.verbose, a.json, a.baseline)


def cmd_import_model(a):
    from .model.cli import run
    run(a)


def cmd_gui(a):
    try:
        from .gui import main as gui_main
    except ImportError as e:
        raise KitError("the GUI needs tkinter (part of the standard Python installer from python.org): %s" % e)
    gui_main()


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="python -m blmodkit",
        description="blmodkit %s - extra-character slot mods for DBZ Burst Limit (PC recompilation). "
                    "Every game file comes from YOUR copy of the game." % __version__)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    game_help = ("your game: the game_data_root folder (default.xex + LONG2DATA), the LONG2DATA_US.CPK, or "
                 "the burstlimit.exe folder; default: the one saved with `config --game`")

    s = sub.add_parser("gui", help="open the graphical interface (same functions as the commands)")
    s.set_defaults(fn=cmd_gui)

    s = sub.add_parser("config", help="remember where your game is")
    s.add_argument("--game", help=game_help)
    s.set_defaults(fn=cmd_config)

    s = sub.add_parser("extract", help="copy a character's files out of your game's archive")
    s.add_argument("--game", help=game_help)
    s.add_argument("--out", required=True, help="folder to write into (archive paths are kept)")
    s.add_argument("--char", action="append", help="character code (GOK, VGT, ...); repeat for more. "
                   "Without --char/--filter: everything")
    s.add_argument("--filter", help="only paths containing this text (e.g. BUFAC_)")
    s.add_argument("--list", action="store_true", help="only list the files")
    s.set_defaults(fn=cmd_extract)

    s = sub.add_parser("new-slot", help="make a new extra-character mod from a game character")
    s.add_argument("--game", help=game_help)
    s.add_argument("--base", required=True, help="the character it starts from and plays as: code (VGT) or id")
    s.add_argument("--code", required=True, help="the new 3-letter file code (A-Z, 0-9), e.g. TST")
    s.add_argument("--name", help="name on the character select (ASCII)")
    s.add_argument("--out", required=True, help="the new mod folder, e.g. <game>\\mods\\Test_Vegeta")
    s.add_argument("--colours", "--colors", type=int, help="number of colours (default: all the base has)")
    s.add_argument("--max-form", type=int, default=0, help="highest form (0 = no transformations; default 0)")
    s.add_argument("--author", default="", help="for mod.toml")
    s.add_argument("--enable", action="store_true", help="also switch it on in the mods folder's mods.toml")
    s.add_argument("--force", action="store_true", help="write into an existing folder")
    s.set_defaults(fn=cmd_new_slot)

    s = sub.add_parser("art", help="card / big picture / HUD face from one picture, installed into the mod")
    s.add_argument("--src", required=True, help="your picture (PNG with transparency is best)")
    s.add_argument("--mod", required=True, help="the slot mod folder")
    s.add_argument("--slot", help="slot code when slot.toml has several")
    s.add_argument("--face", type=_pair, help="X,Y of the point between the eyes in --src (pixels)")
    s.add_argument("--face-size", type=float, help="eyes-to-chin distance in --src (pixels)")
    s.add_argument("--subject", default="full", choices=("full", "bust", "head"),
                   help="what the picture shows, for the face guess (default full body)")
    s.add_argument("--zoom", type=float, default=1.0, help="zoom all three (1.0 = the game's framing)")
    s.add_argument("--card-zoom", type=float, default=1.0)
    s.add_argument("--portrait-zoom", type=float, default=1.0)
    s.add_argument("--hud-zoom", type=float, default=1.0)
    s.add_argument("--card-shift", type=_pair, help="move the face on the card by DX,DY output pixels")
    s.add_argument("--portrait-shift", type=_pair)
    s.add_argument("--hud-shift", type=_pair)
    s.add_argument("--style", default="cel", choices=("cel", "flat", "none"))
    s.add_argument("--colours", "--colors", type=int, default=12, help="palette size for --style cel")
    s.add_argument("--ink", type=float, default=55.0, help="darkness (0-255) below which pixels become line art")
    s.add_argument("--crop", type=_box, help="X0,Y0,X1,Y1: use only this part of --src")
    s.add_argument("--flip", action="store_true", help="mirror the picture (the game wants the face turned right)")
    s.add_argument("--bg-tolerance", type=int, default=40, help="background removal for pictures without alpha")
    s.add_argument("--only", choices=("card", "portrait", "hud"), help="make only one of the three")
    s.set_defaults(fn=cmd_art)

    s = sub.add_parser("check", help="validate a slot mod (paths, sizes, textures, names, art, voice)")
    s.add_argument("mod", help="the slot mod folder")
    s.add_argument("--game", help=game_help)
    s.add_argument("--mods-dir", help="the mods folder whose enabled mods are compared (default: the mod's parent)")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("recolor-fx", aliases=["recolour-fx"], help="recolour the slot's effects to one hue")
    s.add_argument("--mod", required=True)
    s.add_argument("--hue", type=float, required=True, help="target hue in degrees (0 red, 60 yellow, 120 green, "
                   "200 cyan, 240 blue, 280 purple, 326 pink)")
    s.add_argument("--game", help=game_help)
    s.add_argument("--slot", help="slot code when slot.toml has several")
    s.add_argument("--saturation", type=float, default=1.0, help="saturation multiplier (default 1)")
    s.add_argument("--source", choices=("base", "mod"), default="base",
                   help="recolour the base character's original effects (default; repeatable) or the mod's "
                        "current files in place")
    s.add_argument("--keep-earthy", action=argparse.BooleanOptionalAction, default=True,
                   help="leave dust / rock browns alone (default on)")
    s.add_argument("--aura-scale", type=float, default=1.0, help="scale the aura effects (BAR) for a smaller / "
                   "bigger character (default 1)")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_recolor)

    s = sub.add_parser("fix-textures", help="move a hand-made slot's texture handles / set names to its own")
    s.add_argument("--mod", required=True)
    s.add_argument("--game", help=game_help)
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_fix_textures)

    s = sub.add_parser("hits", help="simulate the slot's rush / smash strings vs Goku and Krillin")
    s.add_argument("--mod", required=True)
    s.add_argument("--game", help=game_help)
    s.add_argument("--slot", help="slot code when slot.toml has several")
    s.add_argument("--dummies", default="GOK,KLL", help="standing dummies (GOK, KLL)")
    s.add_argument("--verbose", "-v", action="store_true", help="every string, not just the summary")
    s.add_argument("--json", help="also write the full results here")
    s.add_argument("--baseline", action=argparse.BooleanOptionalAction, default=True,
                   help="also simulate the base character's own moveset for comparison (default on)")
    s.set_defaults(fn=cmd_hits)

    from .model.cli import add_arguments as _import_model_args
    s = sub.add_parser("import-model", help="put a rigged model (.blend / .fbx) on the slot as its costume model",
                       description="Exports the model with Blender (background, the file is never saved), maps its "
                                   "bones, fits it onto the base character's skeleton, converts the materials to the "
                                   "game's toon shading and writes the costume .NUX + .AMB into the slot mod. "
                                   "See docs/MODEL_IMPORT.md.")
    _import_model_args(s)
    s.set_defaults(fn=cmd_import_model)

    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except KitError as e:
        print("ERROR: %s" % e)
        sys.exit(2)


if __name__ == "__main__":
    main()
