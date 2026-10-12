# blmodkit - make your own extra characters for DBZ Burst Limit (PC)

blmodkit is a modder kit for the PC recompilation of *Dragon Ball Z: Burst Limit*. It makes
**extra-character slots**: new fighters that show up on their own page of the Versus / Training
character select (press **R2**), with their own moveset, model, voice, effects, card, big picture
and HUD face.

**The kit contains no game files.** Each command reads the files it needs from **your own copy
of the game** (`LONG2DATA_US.CPK` and `default.xex`) while it runs. The mods you make are built
from your copy as well. When you share a mod, share only the files you made yourself (see
[Sharing a mod](#7-sharing-a-mod-without-game-files)).

- Python 3.9 or newer on Windows. Packages: `numpy` and `Pillow` (`pip install -r requirements.txt`)
- The game: the US (NTSC-U) version, installed for the PC recompilation (folder `game_data_root`,
  which has `default.xex` and `LONG2DATA\`)
- [DBZ Burst Limit Recompiled](https://github.com/iExplosiveRage/DBZ-Burst-Limit-Recompiled/releases) **v0.5.0-alpha or newer**
  (the first version with extra-character slots, the **R2** page)

```
python -m blmodkit --help
python -m blmodkit <command> --help
```

| command | what it does |
|---|---|
| `gui` | opens the graphical interface (see [GUI](#gui)) |
| `config --game <folder>` | saves where your game is, so you don't need `--game` on every command |
| `extract --out <dir> [--char VGT]` | copies a character's files out of your archive, to look at or edit |
| `new-slot --base VGT --code XYZ --name "..." --out <mod>` | makes a new extra character from one of the game's characters, ready to load |
| `art --src picture.png --mod <mod>` | makes the select card, the big picture and the HUD face from one picture |
| `import-model ...` | puts your own rigged model on the slot (see [section 3](#3-give-it-another-model)) |
| `recolor-fx --mod <mod> --hue <deg>` | gives the slot's effects (ki, beams, aura) one colour |
| `hits --mod <mod>` | simulates the slot's rush / smash strings against Goku and Krillin |
| `check <mod>` | checks a mod: paths, sizes, texture clashes, names, art, voice |
| `fix-textures --mod <mod>` | gives a slot you made by hand its own texture handles and texture-set names |

### GUI

Prefer buttons? Double-click **`BLModKit.bat`** (or `blmodkit_gui.pyw`) in the kit folder, or run
`python -m blmodkit gui`. It needs nothing beyond Python, numpy and Pillow (the window uses
tkinter, which comes with Python from python.org). The tabs follow the steps below:

1. **Setup**: your game folder, the mods folder, Blender (found automatically) and, optionally,
   `burstlimit.exe`. Saved like `config`.
2. **New character**: base character, 3-letter code, name, mod folder, switch on (`new-slot`).
3. **Art**: pick a picture, see the card / big picture / HUD face live, click the face, adjust
   zoom and framing, then *Apply to mod* (`art`).
4. **Model**: a `.blend` / `.fbx` / `.glb`, size, lip sync and palette options (`import-model`),
   with the preview picture.
5. **Effects**: hue slider with a colour swatch, saturation, aura size (`recolor-fx`).
6. **Check & test**: `check` results as a list, the `hits` report, open the mods folder, launch
   the game.

The mod the tabs work on is picked at the top (*Current mod*). Every job prints into the log at
the bottom, the same text as the command line.

---

## 1. Setup (once)

1. Install Python from python.org (tick "Add python.exe to PATH" in the installer).
2. Open a terminal in the kit folder (the folder that holds this README) and run:
   ```
   pip install -r requirements.txt
   python -m blmodkit config --game "D:\Games\BurstLimit\game_data_root"
   ```
   Use the folder that has `default.xex` and `LONG2DATA`. The folder of `burstlimit.exe` also
   works when `game_data_root` is next to it or is named in its `burstlimit.toml`.

The first `check` makes a list of every texture in your game, which takes about two minutes.
The list is kept in `%LOCALAPPDATA%\blmodkit\cache\` (it stays on your PC) and later checks
reuse it.

## 2. Make a new character from an existing one

```
python -m blmodkit new-slot --base VGT --code TST --name "Test Vegeta" --out "D:\Games\BurstLimit\mods\Test_Vegeta" --enable
```

- `--base`: the character you start from. The new fighter **plays as** this character for all
  game logic (move list layout, CPU, save unlocks). Codes: GOK Goku, GHS Kid Gohan, GHM Teen
  Gohan, VGT Vegeta, KLL Krillin, TRX Trunks, PIC Piccolo, TSH Tien, YMC Yamcha, RAD Raditz,
  NAP Nappa, GNY Ginyu, RCM Recoome, FRZ Frieza, 16G/17G/18G Androids, CEL Cell, SBM Saibamen,
  BRL Broly, BDK Bardock.
- `--code`: 3 letters or digits for your character's files (`BCTST...`). Use a code that no
  other mod uses.
- `--name`: the name shown on the select screen and the battle HUD. Plain ASCII only.
- `--out`: the new mod folder. Put it in the game's `mods` folder, next to `burstlimit.exe`.
- `--enable`: adds the mod to `mods\mods.toml`. You can also switch it on in the game: Main
  menu > Mods.
- `--colours N`: how many costumes to copy (by default, all of the base character's).
  `--max-form N`: lets the slot transform (experimental). The default is 0, no transformations.

What you get:

```
Test_Vegeta\
  slot.toml          what the game reads: code, base, name, colours, pictures, Ultimates, files
  mod.toml           name / author / description shown in the Mods menu
  art\TST_CARD.png, art\TST_PORTRAIT.png     placeholder pictures (make real ones with `art`)
  PAC\CMN\CH\BCTST\  moveset (BCTSTM0.AMB), hand / lip poses, costume models + skeletons,
                     effect scripts / textures / models, aura, voice lip-sync keys
  PAC\CMN\FIX\BUFAC\BUFAC_TST.NUT            battle HUD face
  SOUND\CSB\CMN|US|JP\BATTLE_TST_*.CSB        sound effects and voice (US / JP)
```

The files are byte-for-byte copies of the base character's files, with two changes:

- **Texture handles.** Each texture in the `.NUX` models and `.NUT` texture banks gets a new
  handle (GIDX) from the slot's own range, which is `0x30000000 + code * 0x800`. The materials
  that use a texture are updated to the new handle. The game loads each handle only once, so a
  slot that kept Vegeta's handles could show Vegeta's textures, or Vegeta could show the slot's.
- **Texture-set names.** The name inside each `.NUX` gets the new code
  (`BCVGT000_B.NUT` becomes `BCTST000_B.NUT`). The game keeps one texture set per name, and a
  second file with the same name gets the first file's textures, which can crash the game.

`slot.toml` is commented line by line. The base character's Ultimates are listed there, so they
can always be selected, whatever your save has unlocked. Rename them or point them at another of
the base character's Ultimate slots:

```toml
ultimates = [[88, 1], [110, 0]]          # [PROPERNOUN text id or "Name", base Ultimate slot 0-2]
ultimates = [["Galick Blast", 1]]        # your own name
```

The extra page holds up to 9 characters. Mod folders are listed in alphabetical order.

## 3. Give it another model

`import-model` replaces a slot's costume model with your own rigged model (`.blend` or `.fbx`;
it runs Blender in the background and never saves your file):

```
python -m blmodkit import-model --help
```

Example (a Blender model on a slot made from Goku, sized automatically, mouth moving with the voice):

```
python -m blmodkit import-model --blend "My Character.blend" --base GOK --mod mods\My_Slot --scale auto --lips
```

It maps the bones (Xenoverse 2, FighterZ, Mixamo, Auto-Rig Pro, Rigify and plain names are
recognised; edit the written `bone_map.json` and pass it back with `--bone-map` if a bone is
wrong), fits the mesh onto the base character's skeleton, converts the materials to the game's
toon shading (`--palette base` / `--skin-tone goku` to match the game's colours), and writes a
preview PNG. The full guide (rig requirements, bone mapping, sizes, materials, faces, lip sync,
error messages) is in [docs\MODEL_IMPORT.md](docs/MODEL_IMPORT.md).

Whichever way you replace a model or a texture, run `check` afterwards. Every changed texture
needs a handle that no other file uses. If you edited files by hand,
`python -m blmodkit fix-textures --mod <mod>` moves the textures into the slot's own handle
range and renames the texture sets. It saves a backup first.

Limits measured in game:

- Costume model (`.NUX`): up to about **8.9 MB**.
- Moveset (`M0`): tested up to **4.7-4.8 MB**. 5.24 MB crashed while loading the battle.
- Effect models (`fx_model`): at most **16** models.

## 4. Make the art

One picture of your character makes all three pictures in the game's style:

```
python -m blmodkit art --src mychar.png --mod "D:\Games\BurstLimit\mods\Test_Vegeta"
```

| picture | size | notes |
|---|---|---|
| card | 512x120 | tight close-up on the face; left / top / bottom are hard crops |
| big picture | 368x720 | the left edge is the screen edge for player 1 (player 2 sees it mirrored) |
| HUD face | 236x268 | head and neck, cut by the same wedge as the game's faces; one per form |

- A PNG with a transparent background works best. On a picture without transparency, the
  background is removed from the corners (`--bg-tolerance`).
- **Framing.** The face position is found from the outline of the figure. Open
  `art\<CODE>_art_preview.png` to check it. If the framing is off, give the point between the
  eyes and the eyes-to-chin distance in pixels of your picture: `--face 400,205 --face-size 85`.
  Other options: `--subject bust|head` for pictures that aren't full-body, `--zoom` (all three),
  `--card-zoom` / `--portrait-zoom` / `--hud-zoom`, `--card-shift DX,DY`, `--crop X0,Y0,X1,Y1`,
  and `--flip` (the game wants the face turned to the right).
- **Look.** `--style cel` (the default) reduces your picture's own colours to a small palette
  (`--colours 12`), keeps dark line art as black ink (`--ink`), and adds the game's solid black
  outline (3 px, or 2 px on the HUD face), with no outline along crop edges. `--style flat`
  keeps your shading and only adds the outline. `--style none` only scales and crops.
- `--only card|portrait|hud` remakes just one of the three.

## 5. Effects colour (optional)

```
python -m blmodkit recolor-fx --mod "...\Test_Vegeta" --hue 120     # green
```

Changes the slot's ki blasts, beams, sparks and aura: the particle colours in `BSP` / `BAR`, the
effect textures (`ESP` / `EAS`, endpoint colours of each block, so shapes stay the same) and the
effect models (vertex colours).

- Whites, greys and blacks stay as they are. So do browns of dust and rock (turn that off with
  `--no-keep-earthy`).
- It recolours the base character's **original** effects each time, so you can run it again with
  another hue. `--source mod` recolours your mod's current files in place instead.
- `--saturation 0.8` mutes the colours. `--aura-scale 0.8` makes the aura smaller, for a
  smaller character.
- Some hues: 0 red, 30 orange, 60 yellow, 120 green, 190 cyan, 230 blue, 280 purple, 326 pink.

## 6. Test

1. **Check the mod:**
   ```
   python -m blmodkit check "...\Test_Vegeta"
   ```
   The report lists errors (the game would ignore or misuse something) and warnings. It covers:
   - `slot.toml` keys (typos are reported) and values
   - that every file is there, path lengths (56 characters at most), and no file taking over a
     game file
   - sizes against the limits above
   - texture handles against the game and your other switched-on mods
   - texture-set names, art sizes, the HUD face count, and the voice banks (US / JP + lip sync)
2. **Simulate the hits** (only matters after you change the moveset):
   ```
   python -m blmodkit hits --mod "...\Test_Vegeta"
   ```
   Every rush / smash string is played against a standing Goku and Krillin, using the game's
   hit rules. The hurt-sphere sizes come from your `default.xex`. You get:
   - the share of hits that land, next to the base character's own moveset for comparison
   - the strings with a miss, and the move where it misses

   This is a simulation; confirm in game.
3. **In game:** start the game, go to Versus or Training, and press **R2** on the character
   select. Pick your character, then **Y** for its colours. R2 again or L2 goes back to the
   roster. Offline only.
   - The game reads `slot.toml` and the new file names at start, so **restart the game** after
     adding a slot or changing file names.
   - The game log (`burstlimit.log`) has a `Slots:` line for every file the slot loads, and a
     warning for anything it falls back on.

## 7. Sharing a mod without game files

A slot made with `new-slot` starts as copies of the base character's files, which are game files.
**Don't upload a mod folder as it is.**

1. Share only what you made: your own model, textures, moveset changes, voice clips, pictures,
   and `slot.toml` / `mod.toml`.
2. Let other players rebuild the rest from their own game: the same `new-slot` command line
   (base, code, name), then your files copied over it, and `recolor-fx` with your hue.
3. A short `HOW_TO_INSTALL.txt` with those command lines is enough.
4. Never share `extract` output, the cache folder (`%LOCALAPPDATA%\blmodkit`), or files from
   other games.

## 8. Reference

- `slot.toml`: the header of each generated file explains every key. The game reads `code`,
  `base`, `name`, `colours`, `max_form`, `card`, `portrait`, `tint`, `ultimates`, `dramas`, and
  `[slot.files]` (`m0 m1 l000 se_csb vox_csb vox_amk bsp esp eas fx_model arst bar bufac model
  skeleton`). `model` / `skeleton` are patterns: `{c}` = colour digit, `{m}` = model index.
- Mods folder rules:
  - Only the paths that `slot.toml` names are added to the archive as new files.
  - Paths must be at most 56 characters, printable ASCII, and under `PAC/` or `SOUND/`.
  - A file in a mod that has the **name** of a game file replaces that game file everywhere.
    `check` warns about it.
- `docs\NUX_FORMAT.md` describes the `.NUX` model format (`blmodkit\nux.py` reads and writes it
  byte for byte). The other formats are described at the top of `blmodkit\ntxr.py`,
  `blmodkit\cpk.py`, `blmodkit\fx.py` and `blmodkit\hits\formats.py`.
- Online play: extra characters are offline only. Online rooms only match players who have
  the same mods switched on.
