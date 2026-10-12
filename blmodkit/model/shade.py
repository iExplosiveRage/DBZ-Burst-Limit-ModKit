r"""Burst Limit character toon shading, decoded from the game's own pixel shader (CF0D1D9262A69C96 in XBOX360.NSH).

Per pixel (characters, normal mode, directional light lightpos.w = 1):
    u      = 0.67 * texel.a + 0.165 * 255 * (1 + N.L)      ramp column (alpha 127 -> 85..170)
    colour = texel.rgb - ramp[u].rgb                       subtractive: ramps store what the shadow takes away
The game multiplies by its ambient / light colours and fades afterwards; tone() is a display approximation fitted
on Goku's wristbands in a training fight.
"""
import numpy as np

NL_K = 0.165 * 255.0


def column(alpha, nl, lw=1.0):
    return 0.67 * np.asarray(alpha, float) + NL_K * lw * (1.0 + np.clip(nl, -1.0, 1.0))


def ramp_take(ramp_img, col, row=None):
    """what the ramp subtracts (stored value) at float column(s), linear between texel centres"""
    r = ramp_img.shape[0] // 2 if row is None else row
    line = ramp_img[r, :, :3].astype(np.float64)
    x = np.clip(np.asarray(col, float) - 0.5, 0, line.shape[0] - 1)
    i0 = np.floor(x).astype(int)
    i1 = np.minimum(i0 + 1, line.shape[0] - 1)
    f = (x - i0)[..., None]
    return line[i0] * (1 - f) + line[i1] * f


def shade(texel_rgba, nl, ramp_img):
    t = np.asarray(texel_rgba, float)
    return np.clip(t[..., :3] - ramp_take(ramp_img, column(t[..., 3], nl)), 0, 255)


def tone(c):
    c = np.clip(np.asarray(c, float), 0, 255)
    return np.clip(1.05 * 255.0 * (c / 255.0) ** 1.27, 0, 255)
