"""Base-character files from the user's OWN game copy (read at run time through the kit's Game; nothing shipped)."""
from ..game import Game  # noqa: F401  (re-exported for the importer)


def open_game(arg=None):
    """--game (or the folder saved with `python -m blmodkit config --game`, or BLMODKIT_GAME)"""
    return Game(arg)


def costume_paths(code, colour=0, model=0):
    c = code.upper()
    return ("PAC/CMN/CH/BC%s/BC%s0%d%d.NUX" % (c, c, colour, model),
            "PAC/CMN/CH/BC%s/BC%s0%d%d.AMB" % (c, c, colour, model))
