# Burst Limit .NUX format (as implemented by blmodkit/nux.py)

All values are big-endian. Everything below was checked in code:
- `serialize(parse(f)) == f` byte for byte on the 4 reference files (BCGOK000/010, BCGHM000, BCGOK002).
- The same holds for all 276 character, effect and ARST NUX files of the game.
- "computed" means `serialize()` derives the field and does not store it.

## 1. Container

| off | size | field |
|---|---|---|
| 0x00 | 4 | `#NUX` |
| 0x04 | 4 | header size = 0x40 (stored) |
| 0x08 | 4 | **absolute offset of `#NUB`** (computed). The old notes called this the "texture block size"; it is really 0x40 + the NTXR block + its padding. |
| 0x0C | 26 | NUT name, e.g. `BCGOK010_B.NUT`, NUL + zero padded. Effect `*_MODEL.NUX` files have 0xFD junk after the NUL; it is stored and kept. |
| 0x26 | 26 | NUB name, e.g. `BCGOK010.NUB` |
| 0x40 | | NTXR block (section 2) |
| | | 0xCD filler × `ntxr_cd`, then zeros up to the next multiple of 0x40, giving `nub` |
| nub | | `#NUB` (section 3), then the NDXR blocks; the file ends 0x40 aligned |

The 0xCD filler count is not derivable: game files have 0, 16, 20 or 24 bytes. The count is stored and is 20 in all 4 reference files.

## 2. NTXR (textures)

**NTXR header**
- `NTXR`, then u16 version 0x0100, then u16 count, then 8 zero bytes. Entries follow back to back.

**Entry header**

| off | field |
|---|---|
| +0x00 | total = header + data (computed) |
| +0x04 | 0 |
| +0x08 | data size (computed) |
| +0x0C | u16 header size (computed): 0x50 for 1 mip, 0x60 for 3 mips |
| +0x0E | 0 |
| +0x10 | u16 mips |
| +0x12 | u16 format: 0 = DXT1, 1 = DXT3, 2 = DXT5 |
| +0x14 | u16 width |
| +0x16 | u16 height |
| +0x18 | 24 zero bytes |
| +0x30 | only when mips > 1: u32 size per mip level, padded to 0x10 with 0xCD. The sizes always equal the DXT level sizes, which `Texture.level_sizes()` computes. |

**After the mip table**
- `eXt\0` 00000020 00000010 00000000
- `GIDX` 00000010 **texture id** 00000000
- Then the data: linear (not tiled) DXT stored as 16-bit big-endian words, all mips back to back.

**Formats found in the game files:** DXT5 1024² or 512² with 3 mips (colour), DXT1 256² with 1 mip (toon ramps), and DXT5 with 1 mip (effects).

### Texture ids (GIDX)

- Character files use `0x1001CCNN`. CC is the character (GOK = 0x00, GHM = 0x02, VGT = 0x03, FRZ = 0x13) and NN is a serial number.
- Across the 138 character files, an id never appears in two characters, and the same id always means the same pixels. The only exceptions are 2 Frieza ids. Recolours reuse the ids of unchanged textures.
  - Example: BCGOK010 shares ids 0001/0003/0004/0006/0008/0009/000a/000b with 000 and has new ids 0021-0024 and 003f for its recoloured textures.
- This suggests ids are global keys, possibly a cross-file texture cache; the engine side has not been checked. The rule for the mod:
  - **every texture whose pixels change gets a NEW id** that no Goku file uses;
  - Goku's game files use 0x10010001-0x10010049;
  - `set_texture(..., new_gidx=)` and `add_texture()` use 0x100100A0 and up, and retarget every material that referenced the old id.
- A material's texture ids must exist in the same NUX.

## 3. #NUB (model table)

| off | field |
|---|---|
| +0x00 | `#NUB` |
| +0x04 | size = file end − nub (computed) |
| +0x08 | count |
| +0x0C | offset of the first NDXR from nub = align(0x20 + 8·count, 0x40) (computed) |
| +0x10 | u16, u16: (2, 2) in character files, (1, 0) in effect MODEL files (stored) |
| +0x14 | `\0NUD` |
| +0x18 | 8 zero bytes |
| +0x20 | count × (u32 offset relative to the first NDXR, u32 index) (computed offsets) |

- Then zero padding, then the NDXR blocks. Each block is padded with zeros to 0x40.
- The index is 0xFF for the body. For the expressions it is 0..7 (Face00-07), plus extras in some characters (8, 10-13, 32, 33).
- Order in the character files: 0xFF first, then the faces.

## 4. NDXR (Smash-4 NUD layout)

**Header (0x30)**

| off | field |
|---|---|
| +0x00 | `NDXR` |
| +0x04 | size (computed) |
| +0x08 | u16 version 0x0200 |
| +0x0A | u16 mesh count (always 1 in the game files) |
| +0x0C | u16 type: 1 skinned, 0 rigid |
| +0x0E | u16 bones: 204 for Goku's 206-bone skeleton, 0 for faces |
| +0x10 | index buffer offset − 0x30 (computed) |
| +0x14 | index buffer size (computed) |
| +0x18 | vertex buffer size (computed) |
| +0x1C | vertex-add buffer size (computed) |
| +0x20 | bounding sphere cx, cy, cz, r (computed) |

**Bounding sphere**
- Centre = float32 running sum of every vertex position (all polygons, in file order) ÷ vertex count. This is bit-exact on all 1195 NDXR of the game.
- r = 3000.0 in every character file.
- In effect MODEL files r is the maximum distance from the centre.

**Mesh header (0x30 each)**

| off | field |
|---|---|
| +0x00 | sphere = header sphere |
| +0x10 | (cx, cy, cz, 0) |
| +0x20 | name offset into the names table (computed) |
| +0x24 | u32 bone flag: 4 for the skinned body, 0 for faces |
| +0x28 | s16 single-bind bone, always −1 |
| +0x2A | u16 polygon count (computed) |
| +0x2C | offset of the mesh's first polygon header (computed) |

**Polygon header (0x30 each, after all mesh headers)**

| off | field |
|---|---|
| +0x00 | index offset (computed) |
| +0x04 | vertex offset (computed) |
| +0x08 | vertex-add offset (computed; 0 when not skinned) |
| +0x0C | u16 vertex count |
| +0x0E | vertex format byte (vsz) |
| +0x0F | UV format byte (uvsz) |
| +0x10 | 4 × u32 material offset; only slot 0 is ever used (computed) |
| +0x20 | u16 index count |
| +0x22 | primitive byte: bit 0x40 = triangle list, otherwise strip. **Always 0 in the game files.** |
| +0x23 | 0x04 for skinned polygons, 0 for faces |
| +0x24 | 12 zero bytes |

**Section order**
1. header, mesh headers, polygon headers;
2. **materials**, in order of first use; a shared Material object is written once, although the game never shares materials;
3. zero pad to 0x10;
4. **index buffer**, polygons in order, no padding between them, zero pad to 0x10;
5. **vertex buffer**, polygons in order, zero pad to 0x10;
6. **vertex-add buffer**, zero pad to 0x10;
7. **names**, NUL terminated, zero pad to 0x10. Names over 15 characters take 32 bytes.

### Vertex formats

- vsz high nibble = bone type, low nibble = normal type.
- uvsz high nibble = number of UVs, low nibble = colour type (2 = RGBA8).
- With a bone type, position, normal and bones go in the vertex-add stream; colour and UVs go in the vertex stream.

| format | stream | layout | bytes |
|---|---|---|---|
| body 0x11/0x12 | vertex-add | pos f32×4 (w = 1.0), normal f32×4 (w = 1.0), bone u32×4, weight f32×4 | 64 |
| body 0x11/0x12 | vertex | RGBA8, uv f16×2 | 8 |
| faces 0x06/0x12 | vertex (only stream) | pos f32×3, normal f16×4 (w = 1.0), RGBA8, uv f16×2 | 28 |

- Weights sum to 1 and are not sorted.
- Unused slots repeat one of the vertex's own bones (for example 3) with weight 0; they do not use 0.
- Bone indices go into the AMB skeleton (0..205).
- Goku's vertex colour is 255,255,255,255 everywhere. Some other characters vary the first byte.
- Other formats (0x07, 0x03, bone types 2 and 4) have dtypes in `vertex_dtypes()` but never occur in the game files.

### Index buffers

- Every polygon in the game uses **triangle strips with 0xFFFF restarts**; 0 of 4165 polygons set the 0x40 list bit.
- Every strip, the last one included, ends with 0xFFFF.
- Triangle k of a strip is (s[k], s[k+1], s[k+2]) when k is even and (s[k+1], s[k], s[k+2]) when k is odd.
- Parity flips at every position, degenerate triangles included (leading duplicate indices are used to flip winding), and resets after 0xFFFF.
- Many strips are a single triangle plus 0xFFFF.
- The smallest game polygon has 4 vertices, 6 indices and 2 triangles. Every mesh has at least one polygon.
- Goku body: 7716 vertices, 9898 triangles, 17112 indices, 11 polygons.

### Material (0x20 + 0x18·n + 0x10)

**Header**

| off | field |
|---|---|
| +0x00 | u32 flags: 0x16308800 for skin/body (some characters use 0x16318800 / 0x16308801); 0x14048000 / 0x14048001 for the extra eye/mouth face polygons |
| +0x08 | u16 src blend (0/1) |
| +0x0A | u16 texture count (computed) |
| +0x0C | u16 dst blend (0/1) |
| +0x12 | u16 cull (2, occasionally 0) |

All other header bytes are 0.

**Texture entry (0x18 each)**

| off | field |
|---|---|
| +0x00 | u32 texture id |
| +0x0C | wrapS, wrapT (02 02 or 00 00) |
| +0x0E | min filter (02) |
| +0x0F | mag filter (01) |
| +0x10 | mip detail (01) |
| +0x11 | 0 or 2 |
| +0x14 | f32 0, −0.25 or −0.5 (LOD bias?) |

- Goku's body materials have 2 textures: **[0] = 256² DXT1 ramp, [1] = DXT5 colour map** (for example 0x10010049 then 0x10010002).

**Properties**
- One empty 0x10 terminator: size 0, then zeros. There are no NU_ parameters in any game file.
- `Material` keeps the header and entries as raw bytes. Only the texture count is computed.

## 5. What the builder computes (`build_polygon`, `build_ndxr`, `serialize`)

**Computed**
- Every offset and size in the NUX, NUB, NDXR, mesh, polygon and texture headers.
- Material placement, all padding, name offsets, the NUB table and the position of `#NUB`.
- The bounding spheres (bit-exact game formula).

**Defaults**
- position w = 1, normal w = 1, colour 255.
- Polygon flag 0x04 when skinned.
- Mesh bone flag 4 when skinned.
- NDXR type 1 when skinned, otherwise 0; bones taken from the caller.

**Index modes**

| mode | encoding | BCGOK000 indices (game 24194) |
|---|---|---|
| `strip` (default) | greedy stripifier, 0xFFFF after each strip | 25394 |
| `restart` | `a b c FFFF` per triangle | 56480 |
| `list` | flag 0x40 | 42360 |
| `indices=` | keep a given strip | — |

**Validation (the original test)**
- BCGOK000 body + 8 faces were decoded to arrays and rebuilt through `build_polygon` / `build_ndxr`.
- Keeping the original strips gives a **byte-identical** 5,772,352-byte file.
- Building from triangles in each mode gives identical positions, normals, bones, weights, colours, UVs, materials and oriented triangle sets (not byte-identical, because the strips differ).

## 6. Port helpers

- **`replace_body(nux, polygons)`**
  - Builds a new 0xFF NDXR from Polygon objects or `build_polygon` kwargs.
  - Keeps the mesh name `XGOK_NW`, 204 bones, r = 3000 and the skinned flags.
- **`empty_faces(nux, mode='degenerate')`** (recommended)
  - Every face NDXR keeps its NUB index, mesh name, sphere, vertex format and first polygon's material.
  - That polygon is cut to 3 coincident copies of vertex 0 with the strip `0 1 2 FFFF`: one zero-area triangle.
  - This stays within what the game files contain (≥ 1 polygon per mesh, strips with restarts). It is still untested in game.
  - `mode='collapse'` keeps every polygon but collapses its vertices.
  - `mode='zero'` gives 0 polygons, which no game file has.
- **`set_texture(nux, i, rgba, new_gidx=None)`**
  - Re-encodes with `nux_texpatch.encode` and box-filtered mips at the same size, format and mip count.
  - Optional new id, with the materials retargeted.
- **`add_texture(nux, rgba, fmt, mips, gidx=None)`**
  - Appends an entry; the default id is the first free id at or above 0x100100A0.
  - Point a material at it with `material.textures[k][0] = gidx`.
  - The NTXR grows and `#NUB` moves, both computed.
- **`rename(nux, 'BCGOK010')`**
  - Sets `BCGOK010_B.NUT` / `BCGOK010.NUB`.
  - A file that replaces BCGOK010 must keep 010's names; the face mesh names stay `BCGOK000_FaceNN` as in the real 010.
- **`dump(nux, prefix)` / `python nux_io.py dump X.NUX prefix`**
  - Writes `.npz` per-polygon arrays plus `.json` (textures, materials as hex).
  - Outputs: `data/BCGOK000_nux.*`, `data/BCGOK010_nux.*`.

## 7. Open points

- **Triangle lists (0x40):** no game file uses them, and the renderer's handling of the bit was not found in the code. Use strips (the default) for game files.
- **Game behaviour:** whether the game's texture cache is really keyed by GIDX across files is inferred from how the ids are used, not from the code.
- **Faces:** a mesh with 0 polygons, or a face model missing from the NUB, was not tested. Keep all 9 entries.
