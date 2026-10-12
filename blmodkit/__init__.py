"""blmodkit - modder kit for the Dragon Ball Z: Burst Limit PC recompilation.

Makes extra-character slot mods (the R2 page of the character select) from the player's
own copy of the game.  The kit never contains game files: every game file it needs is
read at run time from the user's LONG2DATA_US.CPK (and default.xex for a few numbers).

Run `python -m blmodkit --help` for the commands; README.md has the step-by-step guide.
"""

__version__ = "1.0.0"
