"""art: Burst Limit-style select / HUD pictures from ONE image of your character.

    python -m blmodkit art --src picture.png --mod mods\\My_Slot [--face X,Y] [--face-size PX]
                           [--subject full|bust|head] [--zoom 1.0] [--style cel|flat|none] ...

Makes (and installs into the mod):
    art/<CODE>_CARD.png        512x120  character-select card (tight close-up, face turned right)
    art/<CODE>_PORTRAIT.png    368x720  big picture (left = screen edge for player 1)
    BUFAC_<CODE>.NUT           236x268  battle HUD face (one per form, same picture), DXT5
    art/<CODE>_art_preview.png all three on the game's backgrounds colours, to judge the framing

Framing works from two numbers about your picture: where the FACE is (the point between the
eyes) and the FACE SIZE (eyes-to-chin distance), both in source pixels. Without --face /
--face-size they are guessed from the figure's outline and --subject (full body, bust or head
picture); check the preview and pass them when the guess is off.

Look ("--style"):
    cel  (default) flat cel shading like the game's art: the picture's own colours reduced to a
         small palette (k-means, --colours), dark line art kept as black ink, solid black outer
         contour (3 px on the card / big picture, 2 px on the HUD face), no outline along crop
         edges. Works with any character - nothing here is specific to one.
    flat only the outer contour and the crops (keeps your picture's shading).
    none just scaled and cropped.
A picture without transparency gets its background removed from the corners (--bg-tolerance).
Needs only numpy and Pillow.
"""
import os
import re

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import slotfile
from .ntxr import Nut, encode_rgba, make_entry, decode_dxt
from .util import KitError, info, warn

SS = 2  # work at 2x the output size, then area-downsample (anti-aliasing)

# Output layouts. face_dst = where the face (the point between the eyes) lands; face_px = the
# eye-to-chin height of the face in output pixels (measured on the game's own pictures: Teen
# Gohan's HUD face has the eyes at (132, 186) and the chin at y 222).
LAYOUTS = {
    "card": dict(size=(512, 120), face_dst=(240.0, 50.0), face_px=85.0, rot=0.0, outline=3.0,
                 hard=("left", "top", "bottom")),
    "portrait": dict(size=(368, 720), face_dst=(110.0, 200.0), face_px=58.0, rot=0.0, outline=3.0,
                     hard=("bottom", "left")),
    "hud": dict(size=(236, 268), face_dst=(124.0, 186.0), face_px=36.0, rot=-5.0, outline=2.0, hard=()),
}
# --subject: where the eyes are and the eye-to-chin size, as fractions of the figure's height
# (full = whole body standing, bust = head and chest, head = a head shot)
SUBJECTS = {"full": (0.09, 0.05), "bust": (0.28, 0.13), "head": (0.50, 0.25)}


# ----------------------------------------------------------------------------- input picture

def load_source(path, tolerance=40, crop=None, flip=False):
    im = Image.open(path)
    im = im.convert("RGBA")
    if crop:
        im = im.crop(crop)
    if flip:
        im = im.transpose(Image.FLIP_LEFT_RIGHT)
    a = np.array(im)
    if a[..., 3].min() == 255:
        a = remove_background(im, tolerance)
    if (a[..., 3] > 8).sum() < 100:
        raise KitError("%s: nothing left after removing the background - try a PNG with transparency "
                       "or a lower --bg-tolerance" % path)
    return a


def remove_background(im, tolerance):
    """Flood-fill from the four corners (colours within `tolerance`) -> transparent."""
    rgb = im.convert("RGB")
    w, h = rgb.size
    mark = (255, 0, 255)
    work = rgb.copy()
    for xy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if work.getpixel(xy) != mark:
            ImageDraw.floodfill(work, xy, mark, thresh=tolerance)
    m = np.array(work)
    bg = (m[..., 0] == 255) & (m[..., 1] == 0) & (m[..., 2] == 255)
    a = np.array(im)
    a[..., 3] = np.where(bg, 0, 255)
    # soften the cut edge by one pixel
    al = Image.fromarray(a[..., 3]).filter(ImageFilter.GaussianBlur(0.7))
    a[..., 3] = np.minimum(a[..., 3], np.array(al))
    info("  no transparency in the picture: background removed from the corners (tolerance %d)" % tolerance)
    return a


def guess_face(a, subject):
    """(face x, face y, face size) from the figure's outline.

    full / bust: the neck is the narrowest row below the head - the chin sits just above it, and
    the eyes about a third of the head's height above the chin (anime heads with big hair).
    When no clear neck is found: fixed fractions of the figure's height (SUBJECTS)."""
    op = a[..., 3] > 128
    ys, xs = np.nonzero(op)
    y0, y1 = int(ys.min()), int(ys.max())
    hgt = y1 - y0 + 1
    fy_frac, fs_frac = SUBJECTS[subject]
    fy, fsz = y0 + fy_frac * hgt, fs_frac * hgt
    if subject in ("full", "bust"):
        lo, hi = (0.06, 0.32) if subject == "full" else (0.25, 0.75)
        rows = np.arange(y0 + int(lo * hgt), y0 + int(hi * hgt))
        width = np.array([op[r].sum() for r in rows], np.float32)
        if len(width) > 4:
            k = int(width.argmin())
            head_w = width[:max(1, k)].max()
            if width[k] < 0.7 * head_w and k > 0:
                neck = rows[k]
                # chin: going up from the neck, the first row clearly wider than the neck
                chin = neck
                for r in range(neck, y0, -1):
                    if op[r].sum() > 1.6 * width[k]:
                        chin = r
                        break
                fsz = 0.32 * (chin - y0)
                fy = chin - fsz
    xr = np.nonzero(op[int(np.clip(fy, y0, y1))])[0]
    fx = float((xr.min() + xr.max()) / 2) if len(xr) else float(np.median(xs))
    return fx, float(fy), float(fsz)


# ----------------------------------------------------------------------------- framing

def warp(src, face, face_size, layout, zoom, shift):
    """The source mapped into a canvas of SS x the output size: face -> face_dst, face_size ->
    face_px * zoom. Returns float RGB (straight) and alpha 0..1."""
    w, h = layout["size"]
    W, H = w * SS, h * SS
    s = layout["face_px"] * zoom / face_size * SS          # canvas px per source px
    img = Image.fromarray(src, "RGBA").convert("RGBa")       # premultiplied for clean edges
    pre = 1.0
    if s < 0.5:                                              # shrink first (area filter): less aliasing
        pre = max(s * 2.0, 1e-3)
        img = img.resize((max(1, int(img.width * pre)), max(1, int(img.height * pre))), Image.BOX)
    th = np.deg2rad(layout["rot"])
    c, si = np.cos(th), np.sin(th)
    dx = (layout["face_dst"][0] + shift[0]) * SS
    dy = (layout["face_dst"][1] + shift[1]) * SS
    fx, fy = face[0] * pre, face[1] * pre
    k = s / pre                                              # canvas px per (pre-scaled) source px
    # source = F + R(-rot) (dst - D) / k
    a_, b_ = c / k, si / k
    d_, e_ = -si / k, c / k
    cc = fx - a_ * dx - b_ * dy
    ff = fy - d_ * dx - e_ * dy
    out = img.transform((W, H), Image.AFFINE, (a_, b_, cc, d_, e_, ff), resample=Image.BICUBIC)
    arr = np.array(out.convert("RGBA")).astype(np.float32)
    return arr[..., :3], arr[..., 3] / 255.0


# ----------------------------------------------------------------------------- look

def kmeans_palette(pixels, k, iters=12, seed=1):
    """k representative colours of the opaque pixels (k-means in RGB, numpy only)."""
    rng = np.random.default_rng(seed)
    px = pixels.astype(np.float32)
    if len(px) > 60000:
        px = px[rng.choice(len(px), 60000, replace=False)]
    k = max(2, min(k, len(np.unique(px.astype(np.uint8), axis=0))))
    # k-means++ start
    cent = [px[rng.integers(len(px))]]
    for _ in range(1, k):
        d = np.min(((px[:, None, :] - np.array(cent)[None]) ** 2).sum(-1), 1)
        cent.append(px[rng.choice(len(px), p=d / max(d.sum(), 1e-9))])
    cent = np.array(cent)
    for _ in range(iters):
        lab = ((px[:, None, :] - cent[None]) ** 2).sum(-1).argmin(1)
        for j in range(k):
            sel = px[lab == j]
            if len(sel):
                cent[j] = sel.mean(0)
    return cent


def lum(rgb):
    return rgb @ np.array([0.299, 0.587, 0.114], np.float32)


def stylize(rgb, alpha, style, colours, ink_level):
    """cel look: palette from the picture, line art kept, flat colours."""
    if style != "cel":
        return rgb, alpha
    inside = alpha > 0.5
    L = lum(rgb)
    # line art: dark pixels, plus thin dark lines found with a grey closing (max then min filter)
    Li = Image.fromarray(np.clip(L, 0, 255).astype(np.uint8))
    closed = np.array(Li.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))).astype(np.float32)
    tophat = closed - L
    ink = np.clip((ink_level + 12.0 - L) / 24.0, 0, 1)
    ink = np.maximum(ink, np.clip((tophat - 28.0) / 30.0, 0, 1) * np.clip((150.0 - L) / 60.0, 0, 1))
    ink = np.where(inside, ink, 0.0)
    # flat colours: smooth a little, then the nearest palette colour; labels cleaned of specks
    sm = np.array(Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).filter(ImageFilter.MedianFilter(5))
                  ).astype(np.float32)
    sel = inside & (ink < 0.5)
    if sel.sum() < 16:
        return rgb, alpha
    pal = kmeans_palette(sm[sel], colours)
    flat = sm.reshape(-1, 3)
    labels = np.zeros(len(flat), np.int32)
    for i in range(0, len(flat), 200000):
        labels[i:i + 200000] = ((flat[i:i + 200000, None, :] - pal[None]) ** 2).sum(-1).argmin(1)
    lab_img = Image.fromarray(labels.reshape(L.shape).astype(np.uint8))
    lab_img = lab_img.filter(ImageFilter.ModeFilter(5))
    labels = np.array(lab_img)
    col = pal[labels]
    ink_s = np.array(Image.fromarray((ink * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6))) / 255.0
    col = col * (1.0 - ink_s[..., None])
    return col, alpha


def contour(rgb, alpha, width, hard):
    """Solid black outer contour `width` output px wide, not along the given crop edges."""
    if width <= 0:
        return rgb
    solid = (alpha > 0.5).astype(np.uint8) * 255
    pad = int(width * SS) + 2
    m = np.pad(solid, pad, mode="constant")
    if "left" in hard:
        m[:, :pad] = m[:, pad:pad + 1]
    if "right" in hard:
        m[:, -pad:] = m[:, -pad - 1:-pad]
    if "top" in hard:
        m[:pad, :] = m[pad:pad + 1, :]
    if "bottom" in hard:
        m[-pad:, :] = m[-pad - 1:-pad, :]
    size = int(round(width * SS)) * 2 + 1
    er = np.array(Image.fromarray(m).filter(ImageFilter.MinFilter(size)))[pad:-pad, pad:-pad]
    ring = (solid > 0) & (er == 0)
    ring_s = np.array(Image.fromarray(ring.astype(np.uint8) * 255).filter(ImageFilter.GaussianBlur(0.5))) / 255.0
    return rgb * (1.0 - ring_s[..., None])


def hud_cut(size):
    """The bust wedge every retail HUD face is cut with (left and bottom cut lines)."""
    w, h = size
    yy, xx = np.mgrid[0:h * SS, 0:w * SS].astype(np.float32)
    x, y = (xx + 0.5) / SS, (yy + 0.5) / SS
    left = x - (64.0 + 0.288 * (y - 200.0))
    bottom = (84.0 + 2.05 * (268.0 - y)) - x
    k = np.clip(left * SS + 0.5, 0, 1)
    k = np.where(y < 192.0, 1.0, k)
    return (k * np.clip(bottom * SS + 0.5, 0, 1)).astype(np.float32)


def downsample(rgb, alpha):
    pre = np.dstack([rgb * alpha[..., None], alpha * 255.0])
    h, w = alpha.shape
    im = Image.fromarray(np.clip(pre + 0.5, 0, 255).astype(np.uint8), "RGBa")
    small = im.resize((w // SS, h // SS), Image.BOX).convert("RGBA")
    out = np.array(small)
    out[out[..., 3] == 0, :3] = 0
    return out


def render(src, face, face_size, name, opts):
    L = LAYOUTS[name]
    zoom = opts["zoom"] * opts.get(name + "_zoom", 1.0)
    rgb, a = warp(src, face, face_size, L, zoom, opts.get(name + "_shift", (0.0, 0.0)))
    rgb, a = stylize(rgb, a, opts["style"], opts["colours"], opts["ink"])
    if opts["style"] != "none":
        # the contour follows the figure (made before the HUD wedge cut: no line along the cut)
        rgb = contour(rgb, a, L["outline"], L["hard"])
    if name == "hud":
        a = a * hud_cut(L["size"])
    return downsample(rgb, a)


# ----------------------------------------------------------------------------- install

def bufac_nut(face_rgba, existing, count, code):
    """BUFAC_<code>.NUT: `count` copies of the face (one per form), DXT5, the mod's own handles."""
    data = encode_rgba(face_rgba, 2, 1)
    if existing is not None:
        nut = Nut(existing)
        handles = [e.gidx for e in nut.entries]
    else:
        from .textures import handle_block
        first, end = handle_block(code)
        handles = [end - 0x10 + i for i in range(count)]
        nut = Nut(b"NTXR" + bytes([1, 0, 0, 0]) + bytes(8))
    nut.entries = [make_entry(2, 236, 268, g, data, 1) for g in handles[:max(count, 1)]]
    return nut.to_bytes()


def preview(card, portrait, hud, path):
    W, H = 20 + 368 + 20 + 512 + 20, 20 + 720 + 20
    sheet = Image.new("RGBA", (W, H), (44, 36, 60, 255))
    d = ImageDraw.Draw(sheet)
    # big picture as player 1 (screen edge on the left)
    sheet.alpha_composite(Image.fromarray(portrait, "RGBA"), (20, 20))
    x = 20 + 368 + 20
    for k, plate in enumerate(((232, 226, 214), (70, 60, 58))):
        bg = Image.new("RGBA", (512, 120), plate + (255,))
        bg.alpha_composite(Image.fromarray(card, "RGBA"))
        sheet.alpha_composite(bg, (x, 40 + k * 140))
    d.text((x, 20), "card on the selected (light) and list (dark) plate", fill=(255, 255, 255, 255))
    hb = Image.new("RGBA", (236, 268), (24, 20, 30, 255))
    hb.alpha_composite(Image.fromarray(hud, "RGBA"))
    sheet.alpha_composite(hb, (x, 340))
    hb2 = Image.new("RGBA", (236, 268), (24, 20, 30, 255))
    hb2.alpha_composite(Image.fromarray(hud[:, ::-1].copy(), "RGBA"))
    sheet.alpha_composite(hb2, (x + 256, 340))
    d.text((x, 620), "HUD face: player 1 / player 2 (mirrored, as the game draws it)", fill=(255, 255, 255, 255))
    sheet.convert("RGB").save(path)


def _set_slot_line(slot_toml, key, value, section):
    """Make sure `key = "value"` is in slot.toml (top of [[slot]] or [slot.files])."""
    text = open(slot_toml, "r", encoding="utf-8").read()
    pat = re.compile(r'^(\s*#?\s*)%s\s*=.*$' % re.escape(key), re.M)
    line = '%s = "%s"' % (key, value)
    if re.search(r'^\s*%s\s*=' % re.escape(key), text, re.M):
        return False
    m = pat.search(text)
    if m:
        text = text[:m.start()] + line + text[m.end():]
    else:
        anchor = "[slot.files]" if section == "files" else "[[slot]]"
        i = text.find(anchor)
        if i < 0:
            raise KitError("%s has no %s section" % (slot_toml, anchor))
        j = text.find("\n", i) + 1
        text = text[:j] + line + "\n" + text[j:]
    with open(slot_toml, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return True


def parse_pair(s, what):
    try:
        x, y = [float(v) for v in s.split(",")]
        return x, y
    except ValueError:
        raise KitError("%s must be two numbers X,Y - got %r" % (what, s))


def run(src_path, mod, slot_code=None, face=None, face_size=None, subject="full", zoom=1.0, style="cel",
        colours=12, ink=55.0, crop=None, flip=False, bg_tolerance=40, card_zoom=1.0, portrait_zoom=1.0,
        hud_zoom=1.0, card_shift=(0, 0), portrait_shift=(0, 0), hud_shift=(0, 0), only=None):
    _, slots = slotfile.read(mod)
    if not slots:
        raise KitError("%s/slot.toml has no [[slot]]" % mod)
    slot = slots[0] if not slot_code else next((s for s in slots if s.code == slot_code.upper()), None)
    if slot is None:
        raise KitError("no slot %s in %s/slot.toml" % (slot_code, mod))
    code = slot.code
    if subject not in SUBJECTS:
        raise KitError("--subject must be full, bust or head")
    src = load_source(src_path, bg_tolerance, crop, flip)
    gx, gy, gs = guess_face(src, subject)
    fx, fy = face if face else (gx, gy)
    fsz = face_size if face_size else gs
    info("Source %s: %dx%d, face at (%.0f, %.0f), face size %.0f px%s" % (
        os.path.basename(src_path), src.shape[1], src.shape[0], fx, fy, fsz,
        "" if face and face_size else " (guessed - see the preview; --face X,Y / --face-size PX to fix)"))
    opts = dict(zoom=zoom, style=style, colours=colours, ink=ink, card_zoom=card_zoom, portrait_zoom=portrait_zoom,
                hud_zoom=hud_zoom, card_shift=card_shift, portrait_shift=portrait_shift, hud_shift=hud_shift)
    out = {n: render(src, (fx, fy), fsz, n, opts) for n in ("card", "portrait", "hud")}
    art_dir = os.path.join(mod, "art")
    os.makedirs(art_dir, exist_ok=True)
    slot_toml = os.path.join(mod, "slot.toml")
    written = []
    if only in (None, "card"):
        rel = slot.t.get("card") or "art/%s_CARD.png" % code
        Image.fromarray(out["card"], "RGBA").save(os.path.join(mod, *rel.replace("\\", "/").split("/")))
        _set_slot_line(slot_toml, "card", rel, "slot")
        written.append(rel)
    if only in (None, "portrait"):
        rel = slot.t.get("portrait") or "art/%s_PORTRAIT.png" % code
        Image.fromarray(out["portrait"], "RGBA").save(os.path.join(mod, *rel.replace("\\", "/").split("/")))
        _set_slot_line(slot_toml, "portrait", rel, "slot")
        written.append(rel)
    if only in (None, "hud"):
        rel = slot.file("bufac") or "PAC/CMN/FIX/BUFAC/BUFAC_%s.NUT" % code
        p = slotfile.mod_file(mod, rel) or os.path.join(mod, *rel.split("/"))
        existing = open(p, "rb").read() if os.path.isfile(p) else None
        count = len(Nut(existing).entries) if existing else max(1, int(slot.max_form) + 1)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(bufac_nut(out["hud"], existing, count, code))
        _set_slot_line(slot_toml, "bufac", rel, "files")
        Image.fromarray(out["hud"], "RGBA").save(os.path.join(art_dir, "%s_HUD.png" % code))
        written += [rel + " (%d face(s))" % count, "art/%s_HUD.png" % code]
    pv = os.path.join(art_dir, "%s_art_preview.png" % code)
    preview(out["card"], out["portrait"], out["hud"], pv)
    for w in written:
        info("  wrote " + w)
    info("  preview: " + pv)
    return out
