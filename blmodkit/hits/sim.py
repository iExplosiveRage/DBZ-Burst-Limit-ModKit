"""Chain hit simulator: does every hit of the slot's rush / smash strings connect?

Plays every string of the moveset's command tree (root -> leaf, mashing: the next move starts at
the first cancel window after the current move's last hitbox comes on) against a standing dummy
(Goku's or Krillin's idle hurt spheres), frame by frame, with the rules found in the game code:
  * keys and the motion advance by the move's speed keys each tick; a hitbox is tested on the ticks
    f0 <= int(frame) < f1, swept (midpoint of the previous tick and the current one); each key hits once
  * hitbox centre = bone world matrix * (offset * 0.1), radius = size * 0.1 (size 0 -> 2.0)
  * the attacker moves with its motion's WAIST z (root motion), continuous across moves
  * hit type 0 (standing victim): pushed v * sin^2(pi/2 (1 - t/T)) per frame (v = f8 or 1.0, T = 5 or -f12)
  * type 1: launch (vz = f8 or 0.3, vy = f12 or 1.5, gravity 0.08 / frame^2, vy >= -2.5)
  * type 2: blow-away: the string ends there
  * body push: a grounded defender can't be closer than 6.6 to the attacker's WAIST unless the
    attacker jumps (WAIST y >= 16)
The start distance D0 is swept from 4 to 30; a string's result is its best start distance.
Air strings (slot + 0x200) use the same model (approximate). A sim, not the game: use it to find
moves whose hitboxes miss (wrong bone / offset / size after editing), then test in game.
"""
import struct

import numpy as np

from .formats import (BSK, bcm_root, chunk, hitkeys, key_fields, load_bank, local_pose, skeleton,
                      world_pose)

GRAV, VYMIN, BODY = 0.08, -2.5, 6.6
# logical joints the hitboxes name (the game builds the table per fighter by bone NAME)
JOINT_SUFFIXES = ["WAIST", "STOMACH", "NECK", "HEAD", "LLEG2", "RLEG2", "LFOOT", "RFOOT", "LARM2", "RARM2",
                  "LHANDROT", "RHANDROT", "X:NLF", "X:NRF", "X:NLA", "X:NRA", "LARM1", "RARM1", "CHEST"]
# hurt-sphere joints (the 21 radii of the character's battle parameters follow this order)
HURT_JOINTS = ["WAIST", "STOMACH", "NECK", "HEAD", "LLEG2", "RLEG2", "LFOOT", "RFOOT", "LARM2", "RARM2",
               "LHANDROT", "RHANDROT", "NLF", "NRF", "NLA", "NRA", "LARM1", "RARM1", "CHEST", "NW", "NH"]
# battle parameter blocks in default.xex (+28: 21 hurt radii)
DUMMY_PARAMS = {"GOK": 0x826BA2D8, "KLL": 0x826BA498}


def prefix_of(sk):
    for b in sk:
        if b["name"].endswith("_WAIST"):
            return b["name"][:-len("_WAIST")]
    raise ValueError("skeleton without a *_WAIST bone")


class Rig:
    """A skeleton + how the game scales it (rest scale of the root: smaller / bigger fighters)."""

    def __init__(self, sk):
        self.sk = sk
        self.p = prefix_of(sk)
        self.bi = {b["name"]: i for i, b in enumerate(sk)}
        self.waist = self.bi[self.p + "_WAIST"]
        self.jb = []
        for s in JOINT_SUFFIXES:
            n = ("X" + self.p + "_" + s[2:]) if s.startswith("X:") else (self.p + "_" + s)
            self.jb.append(self.bi.get(n))
        body = sk[0]["s"]
        wst = sk[self.waist]["s"]
        self.root_s, self.nu = 1.0, False
        if abs(body[0] - 1) < 1e-4 and abs(body[2] - 1) < 1e-4 and abs(body[1] - 1) > 1e-4 and \
                abs(wst[0] - body[1]) < 1e-3 and abs(wst[2] - body[1]) < 1e-3:
            self.root_s, self.nu = float(body[1]), True       # (1,S,1) body + (S,1,S) waist
        elif np.allclose(body, body[0]) and abs(body[0] - 1) > 1e-4:
            self.root_s = float(body[0])

    def pose(self, A, m, f):
        nm = A.named_motion(m)
        f = min(f, nm["frames"] - 1)
        Rl, Tl = local_pose(self.sk, nm, f)
        WR, WT = world_pose(self.sk, Rl, Tl)
        S = self.root_s
        if S != 1.0 and self.nu:
            b, w = WT[0], WT[1]
            wv = b + np.array([w[0] - b[0], S * (w[1] - b[1]), w[2] - b[2]])
            WT = [WT[0], wv] + [wv + S * (t - w) for t in WT[2:]]
        elif S != 1.0:
            b = WT[0]
            WT = [WT[0]] + [b + S * (t - b) for t in WT[1:]]
        return WR, WT


def is_attack(cls):
    c = cls
    if not (c & 0x8000) and (c & 0xF1):
        c |= 1
    return bool(c & 1)


def active_spans(keys):
    out = []
    for i, k in enumerate(keys):
        if k["kind"] == 0:
            continue
        off = next((k2["fr"] for k2 in keys[i + 1:] if k2["kind"] == 0 and k2["seq"] == k["seq"]), None)
        out.append((k, k["fr"], off if off is not None else k["fr"] + 1))
    return out


def hurt_spheres(game, code):
    """[(joint, centre, radius)] of a standing dummy (its idle pose), facing the attacker."""
    R = game.image().floats(DUMMY_PARAMS[code] + 28, 21)
    sk = skeleton(game.read("PAC/CMN/CH/BC%s/BC%s000.AMB" % (code, code)))
    d = game.read("PAC/CMN/CH/BC%s/BC%sM0.AMB" % (code, code))
    A = load_bank(d)
    B = BSK(chunk(d, 5))
    nm = A.named_motion(B.recs[0][0][0])
    Rl, Tl = local_pose(sk, nm, 0)
    _WR, WT = world_pose(sk, Rl, Tl)
    names = {b["name"]: i for i, b in enumerate(sk)}
    out = []
    for j, n in enumerate(HURT_JOINTS):
        nn = [x for x in names if x.endswith("_" + n)]
        if nn and R[j] > 0:
            p = WT[names[nn[0]]]
            out.append((n, np.array([-p[0], p[1], -p[2]]), R[j]))
    return out


class Move:
    def __init__(self, rig, A, B, s):
        r, ch = B.recs[s]
        self.slot = s
        self.F = F = A.motion_entry(r[0])[0]
        poses = [rig.pose(A, r[0], f) for f in range(F)]
        self.wz = np.array([p[1][rig.waist][2] for p in poses])
        self.wy = np.array([p[1][rig.waist][1] for p in poses]) / rig.root_s
        win = sorted(key_fields(0, raw)[:3] for t, ks in ch if t == 0 for raw in ks)
        self.windows = []
        on = None
        for fr, _seq, kind in win:
            if kind:
                on = fr
            elif on is not None:
                self.windows.append((on, fr))
                on = None
        sp = sorted((key_fields(4, raw)[0], key_fields(4, raw)[3][0]) for t, ks in ch if t == 4 for raw in ks)
        ticks, f, spd, ki = [], 0.0, 1.0, 0
        while f < F:
            while ki < len(sp) and sp[ki][0] <= int(f):
                spd = sp[ki][1]
                ki += 1
            ticks.append(f)
            f += spd if spd > 1e-3 else 1.0
        self.ticks = ticks
        self.spans = []
        self.skipped = 0
        for k, f0, f1 in active_spans(hitkeys(B, s)):
            if not is_attack(k["cls"]):
                continue
            if k["bone"] >= len(rig.jb) or rig.jb[k["bone"]] is None:
                self.skipped += 1
                continue
            b = rig.jb[k["bone"]]
            off = np.array([k["x"], k["y"], k["z"]], float) * 0.1 * rig.root_s
            rad = (k["size"] * 0.1) if k["size"] else 2.0
            cen = np.array([poses[f][1][b] + poses[f][0][b] @ off for f in range(F)])
            ent = [struct.unpack_from(">HHHHff", B.hits[k["hit"]], 20 * i) for i in range(8)] \
                if k["hit"] < len(B.hits) else [(0, 0, 0, 0, 0.0, 0.0)] * 8
            self.spans.append(dict(f0=f0, f1=max(f0 + 1, f1), rad=rad, cen=cen, ent=ent, hit=k["hit"]))
        self.last_on = max([x["f0"] for x in self.spans], default=0)

    def cancel(self):
        for a, b in self.windows:
            if a >= self.last_on:
                return a
        return None

    @staticmethod
    def at(arr, t):
        a = int(np.floor(t))
        u = t - a
        if a + 1 >= len(arr) or u < 1e-6:
            return arr[min(a, len(arr) - 1)]
        return arr[a] * (1 - u) + arr[a + 1] * u


def play(moves, H, D0):
    O = 0.0
    Z, Y = D0, 0.0
    vz = vy = 0.0
    push = None
    launched = ended = False
    out = []
    c_t = None
    for mi, m in enumerate(moves):
        if mi:
            prev = moves[mi - 1]
            O = O + prev.at(prev.wz, c_t) - m.wz[0]
        res = [None] * len(m.spans)
        used = [False] * len(m.spans)
        last = [None] * len(m.spans)
        c = m.cancel() if mi < len(moves) - 1 else None
        if mi < len(moves) - 1 and c is None:
            c = m.F
        stop = c if c is not None else m.F
        c_t = m.ticks[-1] if m.ticks else 0.0
        for t in m.ticks:
            if int(t) >= stop:
                c_t = t
                break
            if push is not None:
                v, T, k = push
                if k < T:
                    Z += v * np.sin(np.pi / 2 * (1 - k / T)) ** 2
                    push = (v, T, k + 1)
                else:
                    push = None
            if launched:
                Z += vz
                Y += vy
                vy = max(VYMIN, vy - GRAV)
                if Y <= 0:
                    Y, launched, vz, vy = 0.0, False, 0.0, 0.0
            wa = O + m.at(m.wz, t)
            if Y < 1.0 and m.at(m.wy, t) < 16.0 and Z - wa < BODY:
                Z = wa + BODY
            for si, sp in enumerate(m.spans):
                if not (sp["f0"] <= int(t) < sp["f1"]):
                    last[si] = None
                    continue
                cur = m.at(sp["cen"], t) + np.array([0, 0, O])
                pts = [cur] if last[si] is None else [0.5 * (last[si] + cur), cur]
                last[si] = cur
                if used[si]:
                    continue
                if any(np.sum((p - (h + np.array([0, Y, Z]))) ** 2) <= (r + sp["rad"]) ** 2 for p in pts for _n, h, r in H):
                    used[si] = True
                    if ended:
                        res[si] = "late"
                        continue
                    res[si] = round(float(Z - wa), 1)
                    e = sp["ent"][2 if Y > 1.0 else 0]
                    typ, f8, f12 = e[2], e[4], e[5]
                    if typ == 0:
                        T = -f12 if (-1e6 < f12 < 0) else 5.0
                        push = ((f8 if abs(f8) > 1e-5 else 1.0), T, 0)
                    elif typ == 1:
                        launched = True
                        vz = f8 if abs(f8) > 1e-5 else 0.3
                        vy = f12 if abs(f12) > 1e-5 else 1.5
                        Y = max(Y, 0.01)
                    elif typ == 2:
                        ended = True
        out.append([(r if r is not None else ("miss" if not ended else "-")) for r in res])
    return out


def is_string_start(s):
    return 0x400 <= s < 0x448 or 0x47E <= s < 0x488


def strings(d):
    """Rush / smash strings: paths through the command tree from the root's string starts."""
    root = bcm_root(chunk(d, 2))
    paths = []
    seen_starts = set()

    def walk(r, path, depth):
        s = r.slots[0]
        path = path + [s]
        kids = [x for x in r.links if x.slots[0] not in path and x.slots[0] != 0xFFFF]
        if not kids or depth > 10:
            paths.append(path)
        for x in kids:
            walk(x, path, depth + 1)
    for link in root.links:
        s = link.slots[0]
        if is_string_start(s) and s not in seen_starts:
            seen_starts.add(s)
            walk(link, [], 0)
    return paths


def simulate(moveset, skel_data, hurts, progress=None):
    """-> list of result dicts (one per string x air/ground x dummy)."""
    rig = Rig(skeleton(skel_data))
    A = load_bank(moveset)
    B = BSK(chunk(moveset, 5))
    cache = {}

    def M(s):
        if s not in cache:
            cache[s] = Move(rig, A, B, s)
        return cache[s]
    res = []
    paths = strings(moveset)
    for k, path in enumerate(paths):
        for air in (False, True):
            ps = [s + (0x200 if air else 0) for s in path]
            if any(s not in B.recs for s in ps):
                continue
            ms = [M(s) for s in ps]
            for dn, H in hurts.items():
                rows = []
                for D0 in np.arange(4.0, 30.0, 0.5):
                    r = play(ms, H, D0)
                    flat = [x for mv in r for x in mv]
                    rows.append((float(D0), sum(1 for x in flat if isinstance(x, float)),
                                 sum(1 for x in flat if x == "miss"), r))
                tot = max(x[1] for x in rows)
                full = [x[0] for x in rows if x[1] == tot and x[2] == 0]
                best = max(rows, key=lambda x: (x[1], -x[2], -abs(x[0] - 10)))
                res.append(dict(path=["%x" % s for s in ps], air=air, dummy=dn, max_hits=tot,
                                n_spans=sum(len(m.spans) for m in ms), skipped=sum(m.skipped for m in ms),
                                D0_clean=[min(full), max(full)] if full else None, best=best[3], best_D0=best[0]))
        if progress:
            progress(k + 1, len(paths))
    return res, rig
