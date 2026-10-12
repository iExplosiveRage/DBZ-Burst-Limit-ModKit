# import-model: put your own rigged model on a character slot

`import-model` takes a rigged humanoid model (a Blender `.blend`, or `.fbx` / `.glb` / `.gltf` / `.dae`) and turns it
into a costume of an extra-character slot: the costume model (`BC<code>0<colour>0.NUX`) and its skeleton
(`BC<code>0<colour>0.AMB`). The fighter keeps the base character's skeleton, moveset, hand poses and lip poses, so
the new model plays with the base character's animations.

The kit contains no game files and no models. The base character's files are read from **your** game at run time.
The model is yours to supply. Only share models you have the right to share.

```
python -m blmodkit new-slot --base GOK --code KBK --name "Kid Buu" --out "<game>\mods\Kid_Buu"
python -m blmodkit import-model --blend "Kid Buu.blend" --base GOK --mod "<game>\mods\Kid_Buu" --scale auto
python -m blmodkit check "<game>\mods\Kid_Buu"
```

Start the game, go to Versus or Training, and press **R2** on the character select. The new slot is on that page.

---

## 1. What you need

- Python 3.9+ with `numpy` and `Pillow` (the kit's `requirements.txt`).
- **Blender 4.x or 5.x.** The importer finds it under `C:\Program Files\Blender Foundation\`. If it is somewhere
  else, pass `--blender <path\blender.exe>` or set the `BLENDER` variable. Blender runs in the background with
  `--disable-autoexec` (scripts inside the file never run) and `--factory-startup`. The importer never saves your
  file.
- Your game (`--game`, or once with `python -m blmodkit config --game <folder>`).
- The model must be **rigged** (an armature with vertex weights) and **humanoid**: two arms, two legs, a head. The
  rest pose can be a T-pose or an A-pose. The fit straightens it onto the game's T-pose.

## 2. The command

```
python -m blmodkit import-model (--blend FILE | --fbx FILE) --mod <slot mod> [options]
```

| option | meaning |
|---|---|
| `--base GOK` | base character (code or id). Must be the character the slot plays as (`base =` in slot.toml). Default `GOK`. |
| `--mod <folder>` | the slot mod. It should be made with `new-slot` first. Without a `slot.toml`, pass `--code XYZ --name "Name"` and a minimal one is written. |
| `--colour N` | which costume (0-3, Y on the select) gets the model. Default 0. `colours` in slot.toml is raised if needed. |
| `--scale 1 \| auto \| <f>` | size. `1` (default) = the base character's size. `auto` = the model's own size when its units are known (see 5). A number such as `0.8` sets it directly. |
| `--bone-map map.json` | your edited bone map (see 4). |
| `--skin-tone model \| goku` | `goku` (or `base`) gives the skin the base character's skin colour and keeps the model's line art. |
| `--palette own \| base` | `own` (default): ramps made from the model's own shading. `base`: skin uses the base character's skin ramp, and cloth / hair / everything else uses its cloth ramp (Goku's gi ramp). The model then takes the game's palette. |
| `--soften 0.5` | blends the model's shadow ratio toward the game's 0.6 (0 = the model's own shadow, 1 = the game's). |
| `--max-texture 1024` | largest texture side. Use 512 for a smaller file. |
| `--lips` | weights the mouth to the base character's lip bones so the mouth moves with the voice (needs jaw / lip bones in the model, see 7). |
| `--face-from FILE.NUX` | copies the 8 face models (expression slots) from another costume NUX. Without it they stay empty (see 7). |
| `--armature NAME` | which armature to use when the file has several (default: the one that deforms the most vertices). |
| `--include REGEX` / `--exclude REGEX` | keep / drop mesh objects by name. `--include-hidden` also takes meshes hidden in the viewport. Meshes disabled for rendering, and outline (inverted-hull) meshes, are always skipped. |
| `--use-pose` | uses the armature's current pose as the rest pose, for files whose rest pose is not the pose the mesh was bound in. |
| `--drop-stray` | drops triangles whose vertices lie far from every bone they are weighted to (see 9). |
| `--no-crotch` | turns off the crotch-zone weight folding (see 6). |
| `--work DIR` | work folder. Default `<game>\modkit_work\<mod>\<code>_colour<N>`. It is outside the mod, because files inside a mod folder can be matched to game files. |
| `--dry-run` | builds everything into the work folder and leaves the mod as it is. |
| `--no-preview` | skips the preview picture. |

**Written into the mod:** `PAC/CMN/CH/BC<code>/BC<code>0<c>0.NUX` and `.AMB`, plus the `model` / `skeleton` lines of
`slot.toml` if they do not already point there. A `new-slot` mod already has `BC<code>0{c}{m}`, so its lines don't
change.

**Written into the work folder:**

| file | contents |
|---|---|
| `export/` | Blender's export: geometry, weights, bones, and the images as `.npy`. It is cached until the model file changes. |
| `bone_map.json` | the bone map used for this import. Edit it and pass it back with `--bone-map`. |
| `preview_<name>.png` | the preview (see 8). |
| `import_report.json` | every number from the run: fit, joints, soles, transforms, materials, ramps, texture handles, sizes. |

A run takes 20-35 s; Blender takes about 5 s of that.

## 3. What happens, step by step

1. **Blender export** (`blmodkit/model/blender_export.py`, runs inside Blender).
   - The armature is set to REST. Shape keys are kept at the basis. Outline solidify modifiers are switched off.
   - Every mesh the armature deforms is evaluated and triangulated.
   - Vertices are split per UV / normal corner.
   - Weights are read from the vertex groups. A mesh parented to a bone gets 100 % on that bone.
   - Bones are exported in world space.
   - For each material, the exporter records where its colour comes from (see 6).
2. **Bone map.** Source bones get roles (see 4).
3. **Fit** onto the base skeleton (see 5).
4. **Weights.** Roles are converted to base bones. At most 4 influences per vertex.
5. **Textures and ramps** (see 6).
6. **Assemble.**
   - The base character's costume NUX is the template. It provides the body material, the bone count, the mesh name
     and the 8 face models.
   - One polygon is written per (colour texture, ramp) pair. Exact duplicate vertices are welded.
   - The triangle winding is matched to the base character's model.
   - Every texture gets a handle from the slot's own block, `0x30000000 + base36(code) * 0x800`: the same rule
     `new-slot` and `check` use. Handles already used by the mod's other files are skipped.
   - The texture-set name inside the file is `BC<code>0<c>0_B.NUT`, so it can't share textures with any other file.
   - The skeleton is the base character's AMB. With `--scale` it also gets a root rest scale (see 5).
7. **Preview** (see 8).

## 4. The bone map

Every bone of the source armature gets one value:

| value | meaning |
|---|---|
| `pelvis`, `spine`, `chest`, `neck`, `head` | torso and head joints. Each `spine` bone becomes the base STOMACH or CHEST, whichever its height is closer to. |
| `L_clavicle`, `L_upperarm`, `L_forearm`, `L_hand`, `L_thigh`, `L_shin`, `L_foot`, `L_toe` (and `R_...`) | limb joints. The role sits on the bone **whose head is that joint**. |
| `L_thumb1..3`, `L_index1..3`, `L_middle1..3`, `L_ring1..3`, `L_pinky1..3` | finger segments. |
| `L_elbow`, `L_knee`, `L_hiphelper`, `L_shoulderhelper` | helper bones. They go to the base ELBOW / KNEE / HIP / ARMDIR. |
| `jaw`, `jaw_half`, `lip_upper_C/L/R`, `lip_lower_C/L/R`, `mouth_corner_L/R`, `cheek_lower_L/R`, `teeth_upper`, `teeth_lower`, `tongue1..3` | mouth bones. They are only used with `--lips`; otherwise they act like `head`. |
| `follow:<role>` | twist / roll / zoom / cloth helper. The weights go with that role, spread over the base twist bones. |
| `bone:<NAME>` | straight onto a base bone, for example `bone:GOK_HEAD` or `bone:XGOK_M_JAW`. |
| `auto` | decided by the importer. Descendants of the head follow the head. Other weighted bones follow the limb or torso segment closest to where their vertices are. Unweighted bones follow their nearest mapped parent. |
| `ignore` | treated as `auto`, but never used as a joint. |

**Required:** `pelvis`, `head`, and both sides of `upperarm`, `forearm`, `hand`, `thigh`, `shin`, `foot`.

**Automatic detection.** Each bone name goes through the preset of its naming style, so mixed rigs work too:

| rig | example names |
|---|---|
| Xenoverse 1 / 2 (Dimps) | `b_C_Pelvis`, `b_L_Arm1`, `h_L_Index1`, `x_L_ArmZoom3`, `f_C_Jaw`, `f_L_MouthBottom` |
| FighterZ (Arc System Works) | `G_pelvis`, `G_uparm_L`, `GD_hand_L`, `GD_fng_b1_L` (a = thumb ... e = pinky), `GD_ago` (jaw), `GD_lip_*` |
| Mixamo | `mixamorig:LeftArm`, `LeftForeArm`, `LeftUpLeg` / `LeftLeg` |
| Auto-Rig Pro | `arm.l`, `forearm.l`, `thigh.l`, `leg.l`, `c_index2.l`. The `_stretch` / `_twist` bones follow their segment. IK / FK / pole / ref controllers are ignored. |
| Rigify, VRM / Unity, 3ds Max Biped, descriptive names | `DEF-upper_arm.L`, `J_Bip_L_UpperArm`, `Bip01 L Forearm`, `arm left elbow` / `leg right ankle`, `finger 2a` |

When two bones claim the same joint, the importer keeps the plainest name: `arm.l` over `arm_stretch.l`. After
that it prefers a bone with weights, then the one highest in the hierarchy. If no bone is called head, the head is
the common parent of the face, hair and mouth bones.

**Editing.** Every run writes `bone_map.json` into the work folder:

```json
{ "rig": "xv2", "base": "GOK",
  "bones": { "b_L_Arm1": "L_upperarm", "x_L_ArmZoom3": "follow:L_upperarm", "my_cape_bone": "bone:GOK_CHEST" },
  "materials": { "skin": { "class": "skin", "lit": [255,139,165], "shadow": [107,31,49], "ramp": "own" } } }
```

Change what you need and run again with `--bone-map <that file>`. Bones you leave out keep their automatic role.

The `materials` section lists what each material became:

- `class` is `skin`, `eye` or `other`.
- `lit` / `shadow` are 0-255 RGB.
- `ramp` is `own`, `base_skin` or `base_cloth`.

Your edits there override the automatic values.

**If the importer stops with "the bone map gives a broken skeleton".** It checks the mapped skeleton before it
fits anything:

- every limb segment must be 0.25-4 times the base character's;
- the legs must stand in order (thigh above shin above foot);
- the hands must be outside the shoulders.

The message names the bones it used. Set the right bones in `bone_map.json`.

## 5. Fit and size

**Orientation and similarity, from the skeleton:**
- up = hips → head;
- left = right upper arm → left upper arm;
- forward = left × up. If the toes point backwards, you get a warning (left and right names swapped?).

One scale factor makes the model's hip-to-shoulder height equal the base character's, and the hips land on its
hips. Units don't matter (metres, centimetres, anything).

**Per-role rest-to-rest maps,** blended with the model's own weights:

| part | how it is fitted |
|---|---|
| limbs: clavicle, upper arm, forearm, thigh, shin, finger segments | rotated and stretched along the bone onto the base joints. The cross-section is kept, so the arms keep their thickness. |
| palm | uniform scale; wrist → wrist, middle knuckle → middle knuckle |
| torso | stays as it is |
| neck | stretched so the head joint lands on the base HEAD. The stretch stops at the head, so hair or a hood weighted to the neck doesn't fly off. |
| head and face | translated |
| feet | ankle → ankle, scaled vertically so the soles are on the base character's soles |

A bone with no child (fingertips, FBX bones) keeps its own tail only if the tail looks like a continuation of the
chain. Otherwise the previous segment is continued.

**Weights onto the base bones:**

- **Limb segments** are spread over the base character's twist bones, using the base character's own weight
  profile along that segment. The profile is measured from its body mesh: ARMDIR / AROLL1 / AROLL2 / ARM1 on the
  upper arm, ARM2 / AROLL3 / AROLL4 / HANDXR on the forearm, and the same on the legs.
- **The palm** copies the base hand's palm bones from the nearest base hand vertices, so the knuckles follow the
  game's fist poses.
- **Fingers** go by name.
- **Crotch zone** (on by default): in the crotch of trousers, the thigh twist bones are folded into LEGDIR, and HIP
  is split between WAIST and LEGDIR. This was measured on Kid Buu's baggy trousers: idle worst stretch went from
  9.1x to 6.5x.
- **More than 4 influences:** a dropped bone's weight goes to the kept bone closest to it in the base bone tree.

**Size (`--scale`).** By default the model is fitted to the base character's size. A different size uses a uniform
**root rest scale**: the root bone `X<code>_BODY` gets the scale, the mesh is written already scaled (the game puts
the rest scale into the inverse bind too), and the skeleton's low-poly `#BND` body is scaled the same way. Hit and
hurt spheres scale with the model.

`--scale auto` uses the model's shoulder height when the units are known:

- Xenoverse / FighterZ rigs are in metres;
- otherwise a shoulder height of 0.4-2.6 counts as metres and 40-260 as centimetres.

The reference is 1.30 m. That gives Kid Buu (Xenoverse 2) 0.79, the size the Kid Buu slot settled on. Auto is
limited to 0.6-1.3, and within 4 % of 1 it stays 1.

> **Warning: root travel.** With a scale other than 1, the base moveset's root travel (steps, dashes, knock-backs)
> is drawn scaled. When each move ends, the game moves the fighter the full distance, so moves end with a small
> jump of (1 - scale) × travel: at 0.79, up to about 11 units on a back step. Only a moveset re-authored for that
> size removes it. The Kid Buu slot's moveset used non-uniform root scales plus re-authored motions for this. That
> is moveset work and not part of `import-model`.

## 6. Textures and the game's toon shading

**The game's character shader** (decoded from the game's pixel shader):

```
column = 0.67 * texel.alpha + 42.075 * (1 + N.L)      ramp column; alpha 127 -> columns 85..170
colour = texel.rgb - ramp[column].rgb                  SUBTRACTIVE: a ramp stores what the shadow takes away
```

**Ramps** are 256×256 DXT1 textures with identical rows. Each material uses one ramp:

| columns | band |
|---|---|
| 0-113 | shadow |
| 114-150 | mid |
| 151-255 | lit |

**Texture alpha is not transparency.** It is the shading offset. 127 is neutral: most of Goku's texels are 127. A
higher alpha means always lit, a lower one means shadow-prone.

The body material is opaque. Materials that use transparency in the source get a warning, and their cut-out parts
show as solid.

**Where each material's colour comes from:**

| source | lit colour | shadow colour |
|---|---|---|
| **Xenoverse 2 Blender importer** (an `XV2 Toon Shade`-style group with `Band`, a DYT ramp and a mask) | median of the DYT band row over x 0.775-0.92 | median over x 0.55-0.69 |
| **any base-colour image** (followed back from the shader's Base Color / Color input) | the image itself | default ratio, or the `sss` image's tint |
| **no image** | the material colour, shown flat | default ratio |

Notes:

- **Xenoverse 2:** ink lines come from the mask's alpha. The eye iris comes from the mask's blue channel. A
  mouth or tongue that the importer pointed at its unused light-blue band is rebuilt from the skin row's dark reds.
- **FighterZ:** a material with `ilm` and `sss` images is colour = base × ilm.alpha (the inner lines), and shadow
  = base × sss.
- **FBX:** if the FBX has no texture references, its materials come out flat. Assign the images in Blender, save a
  `.blend`, and import that.

**Textures.**

- One colour texture is made per source image (mask, for Xenoverse), with the size of the source up to
  `--max-texture`. Islands in other UV tiles are moved into the [0,1] tile.
- Meshes whose UVs span several tiles (Xenoverse eyes) are re-baked into their own box.
- Materials without images share a small swatch texture.
- Colour textures are DXT5 with 3 mips.

**Palettes.**

- **`own` (default):** for every distinct shading, a ramp with `delta = lit × (1 - r')`, where `r'` is the model's
  shadow / lit ratio softened toward the game's 0.6 (`--soften`). The lit band shows the model's colour and the
  shadow band its own (softened) shadow.
- **`base`:** skin uses the base character's skin ramp and everything else (except eyes / teeth) its cloth ramp.
  The ramps are copied and get the slot's own handles. Texels are raised by the ramp's lit band, so the lit colour
  stays the model's. This is the lesson from hybrid v3 / v4: with the game's own ramps, the model matches the
  palette of the other fighters.
- **`--skin-tone goku`:** skin texels become the base character's skin texel (Goku: white; the ramp gives the
  colour), and the line art is kept as darkening. The skin then has Goku's colour.

**Skin** is detected by the material name (skin / face / body / hand / head / neck / ...). Set `class` in the bone
map's `materials` section if a material is guessed wrong.

## 7. Face and mouth

**Default: the face is part of the body, rigid on HEAD.** Every face, eye, brow and hair bone goes to the base
HEAD. The base character's 8 face models (the expression slots: blink, joy, pain, shout ...) are left empty: each
keeps one zero-area triangle, which is the structure the game expects. The model's face therefore has no blinking
and no expressions.

**`--face-from FILE.NUX`** copies the 8 face models from another costume NUX, for example one you made yourself for
this model. Their textures get handles in the slot's block.

**`--lips`.** The game moves the mouth for every voice line with the lip bones in the body (`X<code>_M_*` and
`X<code>_JAWPARENT`, posed from `BC<code>L000.AMB`). With `--lips`, the model's mouth bones go onto them:

| model bone | base bone(s) |
|---|---|
| jaw | M_JAW |
| jaw_half / lower cheeks | half JAW, half HEAD |
| lower lips L / C / R | M_LLLIP / M_LCLIP / M_LRLIP |
| upper lips L / C / R | M_ULLIP / M_UCLIP / M_URLIP |
| mouth corners | half M_xCHEEK, the rest on the lips |
| upper teeth | HEAD |
| lower teeth | JAW |
| tongue | M_TONGUE1-3 |

This needs mouth bones in the model. Xenoverse, FighterZ and most game rigs have them; Mixamo rigs don't. The
preview then adds a close-up with the widest-open lip pose.

The base character's lip pivots stay where they are. On a mouth that is far from the base character's, the motion
is plausible but not exact.

## 8. Preview

`preview_<name>.png` is drawn with the game's shading (`shade.py`):

- the rest pose from the front and from the side;
- idle (base moveset motion 0) and a punch (motion 30), each next to the base character at the same scale;
- with `--lips`, the open-mouth close-up.

Only the body is drawn: the base character's face models (eyes) are not in the preview. You can also run it on its
own:

```
python -m blmodkit.model.preview <model.NUX> <skeleton.AMB> out.png --game <game> --base GOK
```

## 9. Limits and troubleshooting

- **Size.** The game's own costume models go up to 7.29 MB. The importer warns above 7.3 MB and refuses above
  8 MB. Use `--max-texture 512` or fewer source images to get under. Kid Buu imports at 5.0 MB.
- **Polygons.** A polygon holds at most 65535 vertices. Bigger groups are split automatically.
- **"N vertices lie far from every bone they are weighted to".** The model's weights or its rest pose are broken.
  Pose an arm in Blender: if far-away parts move with it, the source is broken. Above 2 % of the vertices the
  importer stops. `--drop-stray` removes those triangles. `--use-pose` helps when the file was bound in a pose that
  isn't the armature's rest.
- **Several armatures** (for example an Auto-Rig Pro control rig next to the game skeleton): the one that deforms
  the most vertices through the mesh's Armature modifier is used. Choose another with `--armature`.
- **The model shows the base character's model in game.** The `model` / `skeleton` lines of slot.toml don't point
  at the new files, or the mod is switched off. Run `check`.
- **Everything is a single flat colour.** The materials had no images Blender could load (missing external files).
  The export report lists them.
- **Not done by the importer:** expressions and blinking (use `--face-from`), hair and cloth physics (secondary
  bones follow the nearest body part), a moveset re-authored for a different size.

## 10. Tested

| model | source | result |
|---|---|---|
| Kid Buu | Xenoverse 2 `.blend` (Kid Buu.blend), slot `KBK`, base GOK, `--scale auto` (0.786), `--lips` | in game it loads, animates (idle, rush, knock-down and get-up, ki blast, Kamehameha with its cinematic close-up) and fights, as 1P, as 2P, and against itself |
| Super Buu | Xenoverse 2 `.blend`, slot `SBK`, `--scale auto` (1.3) | the same, in a fight against the Kid Buu slot. Both imported slots work together. |
| Ultimate Gohan | FighterZ `.blend` (an Auto-Rig Pro rig mixing FighterZ deform bones) | bone map, fit and preview are correct (dry run) |

**Accuracy against the hand-made port:** the Kid Buu fit through the kit is identical to the hand-made port that was
approved in game: vertex positions agree within 2e-5, and the weights differ by at most 0.09 (L1).

**Broken sources:** two FBX game rips had scrambled weights (in Blender itself, rotating a finger moves the feet).
The importer detects this and stops with the message above.
