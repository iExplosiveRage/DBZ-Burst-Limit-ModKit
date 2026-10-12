"""Source bone -> role mapping (the editable bone map), with presets for common rigs.

Roles (what the fit and the weight transfer understand):
  pelvis, spine (resolved by height to the base's STOMACH / CHEST), chest, neck, head, jaw,
  <side>_clavicle, <side>_upperarm, <side>_forearm, <side>_hand, <side>_thigh, <side>_shin, <side>_foot, <side>_toe,
  <side>_thumb1..3, _index1..3, _middle1..3, _ring1..3, _pinky1..3              (side = L or R)
  helpers: <side>_elbow, <side>_knee, <side>_hiphelper (-> HIP), <side>_shoulderhelper (-> ARMDIR)
  lips (only used with --lips): lip_upper_C/L/R, lip_lower_C/L/R, mouth_corner_L/R, cheek_lower_L/R, jaw_half,
           tongue1..3, teeth_upper, teeth_lower
  'follow:<role>'   = weights go with that role (twist / zoom / helper bones)
  'bone:<NAME>'     = straight onto that base-skeleton bone (e.g. bone:GOK_HEAD)
  'auto'            = decided from the hierarchy (the nearest mapped ancestor; head descendants -> head)
  'ignore'          = treated like 'auto' for weights, never used as a joint
The map is written as JSON next to the import (bone_map.json) and can be edited and passed back with --bone-map.
"""
import json
import re

import numpy as np

SIDES = ("L", "R")
FINGERS = ("thumb", "index", "middle", "ring", "pinky")
JOINT_ROLES = ["pelvis", "neck", "head", "chest"] + ["%s_%s" % (s, r) for s in SIDES for r in (
    "clavicle", "upperarm", "forearm", "hand", "thigh", "shin", "foot", "toe")]
REQUIRED = ["pelvis", "head"] + ["%s_%s" % (s, r) for s in SIDES for r in (
    "upperarm", "forearm", "hand", "thigh", "shin", "foot")]
LIP_ROLES = ["jaw", "jaw_half", "lip_upper_C", "lip_upper_L", "lip_upper_R", "lip_lower_C", "lip_lower_L",
             "lip_lower_R", "mouth_corner_L", "mouth_corner_R", "cheek_lower_L", "cheek_lower_R",
             "tongue1", "tongue2", "tongue3", "teeth_upper", "teeth_lower"]


# ------------------------------------------------------------------------------------------------ presets
def _xv(name):
    """Xenoverse 1/2 (Dimps): b_C_Pelvis, b_L_Arm1, h_L_Index1, f_C_Jaw, x_L_ArmZoom3 ..."""
    m = re.match(r"^([bhfgx])_([CLR])_([A-Za-z]+?)(\d*)$", name)
    if not m:
        return None
    kind, side, part, num = m.groups()
    num = int(num) if num else 0
    if kind == "g" or name.startswith("g_x"):
        return "auto"
    if kind == "b":
        t = {("C", "Base"): "auto", ("C", "Pelvis"): "pelvis", ("C", "Spine"): "spine", ("C", "Chest"): "chest",
             ("C", "Neck"): "neck", ("C", "Head"): "head"}
        if (side, part) in t:
            return t[(side, part)]
        t = {"Shoulder": "clavicle", "Arm": {1: "upperarm", 2: "forearm"}, "Hand": "hand",
             "Leg": {1: "thigh", 2: "shin"}, "Foot": "foot", "Toe": "toe", "Elbow": "elbow", "Knee": "knee",
             "LegHelper": "hiphelper", "ArmHelper": "shoulderhelper", "ArmRoll": "follow:forearm",
             "ArmorParts": "follow:upperarm"}
        r = t.get(part)
        if isinstance(r, dict):
            r = r.get(num)
        if r is None:
            return None
        if r.startswith("follow:"):
            return "follow:%s_%s" % (side, r[7:])
        return "%s_%s" % (side, r)
    if kind == "h":
        f = part.lower()
        if f in FINGERS and 1 <= num <= 3:
            return "%s_%s%d" % (side, f, num)
        if f in FINGERS:
            return "%s_%s3" % (side, f)
        return None
    if kind == "x" and part == "ArmZoom":
        return "follow:%s_%s" % (side, "upperarm" if num <= 5 else "forearm")
    if kind == "f":
        t = {("C", "Jaw"): "jaw", ("C", "JawHalf"): "jaw_half", ("C", "MouthTop"): "lip_upper_C",
             ("C", "MouthBottom"): "lip_lower_C", ("L", "MouthTop"): "lip_upper_L", ("R", "MouthTop"): "lip_upper_R",
             ("L", "MouthBottom"): "lip_lower_L", ("R", "MouthBottom"): "lip_lower_R",
             ("L", "MouthCorners"): "mouth_corner_L", ("R", "MouthCorners"): "mouth_corner_R",
             ("L", "CheekBottom"): "cheek_lower_L", ("R", "CheekBottom"): "cheek_lower_R",
             ("C", "ToothTop"): "teeth_upper", ("C", "ToothBottom"): "teeth_lower"}
        if (side, part) in t:
            return t[(side, part)]
        if part == "Tongue":
            return "tongue%d" % min(max(num, 1), 3)
        return "follow:head"
    return None


def _fz(name):
    """Dragon Ball FighterZ (Arc System Works): G_pelvis, G_uparm_L, GD_hand_L, GD_fng_b1_L, GD_ago ..."""
    if not re.match(r"^(G|GD|GP|GDU)_", name):
        return None
    if name.startswith("GDU_BAKE") or name.startswith("GP_"):
        return "ignore"
    fm = re.match(r"^GD_fng_([a-e])(\d)_([LR])$", name)
    if fm:
        f = FINGERS["abcde".index(fm.group(1))]
        return "%s_%s%d" % (fm.group(3), f, min(int(fm.group(2)), 3))
    side = name[-1] if re.search(r"_[LR]$", name) else None
    base = re.sub(r"_[LR]$", "", name)
    t = {"G_pelvis": "pelvis", "G_waist": "spine", "G_stomach": "spine", "G_chest": "chest", "G_neck": "neck",
         "G_head": "head", "GD_ago": "jaw", "GD_lip_up_center": "lip_upper_C", "GD_lip_low_center": "lip_lower_C",
         "GD_teeth_up": "teeth_upper", "GD_teeth_low": "teeth_lower", "G_root": "auto"}
    if base in t:
        return t[base]
    if side:
        t = {"GD_clavicle": "clavicle", "G_uparm": "upperarm", "G_lowarm": "forearm", "GD_hand": "hand",
             "G_thigh": "thigh", "G_calf": "shin", "GD_foot": "foot", "GD_toe": "toe", "GD_elbow": "elbow",
             "GD_knee": "knee", "G_hip": "hiphelper", "GD_shldrHalf": "shoulderhelper",
             "GD_lip_up_out": "lip_upper", "GD_lip_low_out": "lip_lower", "GD_lip_up_end": "mouth_corner",
             "GD_lip_low_end": "mouth_corner"}
        if base in t:
            r = t[base]
            if r in ("lip_upper", "lip_lower", "mouth_corner"):
                return "%s_%s" % (r, side)
            return "%s_%s" % (side, r)
        for pre, seg in (("GD_uparm_", "upperarm"), ("GD_lowarm_", "forearm"), ("GD_thigh_", "thigh"),
                         ("GD_calf_", "shin")):
            if base.startswith(pre):
                return "follow:%s_%s" % (side, seg)
    return None


_MIXAMO_PREFIX = re.compile(r"^(c_|mixamorig\d*[:_]|Bip0?1[ _]|bip0?1[ _]|ValveBiped\.Bip01_|DEF[-_]|ORG[-_]|"
                            r"J_Bip_|Armature[|:]|CC_Base_|rig[:_])", re.I)


def _tokens(name):
    n = _MIXAMO_PREFIX.sub("", name)
    n = re.sub(r"([a-z])([A-Z])", r"\1 \2", n)
    n = re.sub(r"([A-Za-z])(\d)", r"\1 \2", n)
    return [t for t in re.split(r"[\s_.\-:|]+", n.lower()) if t]


def _side(name, toks):
    if re.search(r"(\.|_|-|\b)(l|left)$|^(l|left)(\.|_|-)|^left|left[A-Z_]|_L_|\bL\b", name):
        return "L"
    if re.search(r"(\.|_|-|\b)(r|right)$|^(r|right)(\.|_|-)|^right|right[A-Z_]|_R_|\bR\b", name):
        return "R"
    if "left" in toks or "l" in toks:
        return "L"
    if "right" in toks or "r" in toks:
        return "R"
    return None


FACE_WORDS = r"\b(eye|eyes|eyeball|brow|eyebrow|lid|eyelid|cheek|nose|ear|glasses|face|forehead|hair|bang|bangs)\b"


def _generic(name):
    """Mixamo (mixamorig:LeftForeArm), Auto-Rig Pro (forearm_stretch.l), Rigify (DEF-upper_arm.L), Unity / VRM
    (J_Bip_L_UpperArm, LeftUpperArm), Valve / 3ds Max Biped (Bip01 L Forearm), descriptive names
    ('arm left elbow', 'leg right ankle': a bone named after the joint at its head) and plain names."""
    toks = _tokens(name)
    j = " ".join(toks)
    side = _side(name, toks)
    nums = [int(t) for t in toks if t.isdigit()]
    num = nums[0] if nums else None
    if re.search(r"\b(ik|fk|pole|target|ctrl|ctl|mch|ref|track|nostr|pre|master|traj|pos|offset|org|rot|tip|end|"
                 r"nub|top|fix|look|unused|attach|weapon|prop)\b", j):
        return "ignore"
    twist = bool(re.search(r"\b(twist|roll|zoom|bend|helper|share|correct|corr|tweak|twk|stretch|half|mid|up|low|"
                           r"proxy|phys|jiggle|adj|sleeve|jacket|cloth|coat|cuff|pad|armor|armour)\b", j))
    # face / mouth (before 'head', which often prefixes them: 'head lip upper left')
    if re.search(r"\b(jaw|chin|ago)\b", j):
        return "jaw"
    if re.search(r"\btongue\b", j):
        return "tongue%d" % min(max(num or 1, 1), 3)
    if re.search(r"\bteeth|tooth\b", j):
        return "teeth_lower" if re.search(r"\b(lower|bottom|low)\b", j) else "teeth_upper"
    if re.search(r"\b(lip|lips|mouth)\b", j):
        if re.search(r"\bcorner\b", j) and side:
            return "mouth_corner_%s" % side
        part = "lower" if re.search(r"\b(lower|bottom|low|down)\b", j) else "upper" if re.search(
            r"\b(upper|top|up)\b", j) else None
        if part:
            return "lip_%s_%s" % (part, side or "C")
        return "follow:head"
    if re.search(FACE_WORDS, j):
        return "follow:head"
    # fingers: thumb / index ... or 'finger 1a' (1 = thumb .. 5 = pinky, a/b/c = segment)
    fm = re.search(r"\bfinger (\d)\s*([abc])\b", j) or re.search(r"\bfinger (\d) ([abc])\b", j)
    if fm and side and 1 <= int(fm.group(1)) <= 5:
        return "%s_%s%d" % (side, FINGERS[int(fm.group(1)) - 1], "abc".index(fm.group(2)) + 1)
    fing = None
    for f in FINGERS:
        if re.search(r"\b%s\b" % f, j) or (f == "pinky" and re.search(r"\b(little|pinkie)\b", j)) or \
                (f == "index" and re.search(r"\bpointer\b", j)):
            fing = f
    if fing and side:
        n = num if num is not None else 1
        return "%s_%s%d" % (side, fing, min(max(n, 1), 3))
    if side:
        if re.search(r"\b(hand|wrist|palm)\b", j):
            return "%s_hand" % side
        if re.search(r"\b(toe|toes|ball)\b", j):
            return "%s_toe" % side
        if re.search(r"\b(foot|ankle)\b", j):
            return "%s_foot" % side
        if re.search(r"\b(clavicle|collar|collarbone)\b", j) or \
                (re.search(r"\bshoulder\b", j) and not re.search(r"\b(upper|arm)\b", j)) or \
                (re.search(r"\bshoulder\b", j) and num in (None, 0, 1) and re.search(r"\barm\b", j) and
                 "shoulder 02" not in j):
            return ("follow:%s_clavicle" if twist else "%s_clavicle") % side
        if re.search(r"\bshoulder\b", j) and num == 2:
            return "%s_upperarm" % side
        if re.search(r"\b(fore ?arm|lower ?arm|low ?arm|elbow|forearm stretch)\b", j) or \
                (re.search(r"\barm\b", j) and num == 2 and not twist):
            return ("follow:%s_forearm" if twist else "%s_forearm") % side
        if re.search(r"\b(upper ?arm|up ?arm|arm)\b", j):
            return ("follow:%s_upperarm" if twist else "%s_upperarm") % side
        if re.search(r"\b(calf|shin|knee|lower ?leg|leg stretch|low ?leg)\b", j) or \
                (re.search(r"\bleg\b", j) and num == 2 and not twist):
            return ("follow:%s_shin" if twist else "%s_shin") % side
        if re.search(r"\b(thigh|up ?leg|upper ?leg|leg)\b", j):
            return ("follow:%s_thigh" if twist else "%s_thigh") % side
        if re.search(r"\bhip\b", j):
            return "%s_hiphelper" % side
        return None
    if re.search(r"\bneck\b", j):
        return "follow:neck" if (nums and nums[0] > 1) or re.search(r"\b(middle|upper|mid)\b", j) else "neck"
    if re.search(r"\b(head)\b", j):
        return "head"
    if re.search(r"\b(pelvis|hips|hip|bassin)\b", j):
        return "pelvis"
    if re.search(r"\b(upper ?chest|chest|breast|ribcage)\b", j):
        return "chest"
    if re.search(r"\b(spine|waist|abdomen|stomach|belly|torso)\b", j):
        return "spine"
    if re.search(r"\b(root|master|world|armature|origin|ground)\b", j):
        return "auto"
    return None


PRESETS = {"xv2": _xv, "fighterz": _fz, "generic": _generic}


def detect_rig(names):
    s = set(names)
    if sum(1 for n in names if re.match(r"^[bhfx]_[CLR]_", n)) > 20 and "b_C_Head" in s:
        return "xv2"
    if sum(1 for n in names if n.startswith(("G_", "GD_"))) > 20 and "G_head" in s:
        return "fighterz"
    if any(n.lower().startswith("mixamorig") for n in names):
        return "mixamo"
    if any(n.endswith((".l", ".x")) and n.startswith(("c_", "arm_", "forearm_", "thigh_", "leg_", "spine_"))
           for n in names):
        return "autorigpro"
    return "generic"


# ------------------------------------------------------------------------------------------------ build
def _preset_for(name):
    if re.match(r"^[bhfgx]_[CLR]_", name):
        return _xv
    if re.match(r"^(G|GD|GP|GDU)_", name):
        return _fz
    return _generic


def _plainness(name):
    """fewer name tokens = more likely the joint bone itself (ARP arm.l vs arm_stretch.l)"""
    return len(_tokens(name))


def auto_map(names, parents, heads, deform=None, weighted=None):
    """-> (rig family, {bone: role}) for every bone of the source armature (each name goes through the preset
    of its naming style, so mixed rigs - FighterZ bones inside an Auto-Rig Pro rig - work too)"""
    rig = detect_rig(names)
    roles = {}
    for n in names:
        fn = _preset_for(n)
        r = fn(n)
        if r is None and fn is not _generic:
            r = _generic(n)
        roles[n] = r or "auto"
    # one bone per joint role: keep the best (plain name, weighted, deforming, highest in the hierarchy)
    by = {}
    for i, n in enumerate(names):
        r = roles[n]
        if r in ("auto", "ignore") or r.startswith(("follow:", "bone:")):
            continue
        by.setdefault(r, []).append(i)
    depth = []
    for i in range(len(names)):
        d, j = 0, parents[i]
        while j >= 0:
            d += 1
            j = parents[j]
        depth.append(d)
    for r, idx in by.items():
        if len(idx) < 2 or r == "spine":
            continue

        def key(i):
            return (0 if names[i].startswith(("G_", "b_", "h_")) else 1,
                    0 if re.search(r"finger\s?_?\d[abc]\b", names[i], re.I) else 1, _plainness(names[i]),
                    0 if (weighted is None or weighted[i]) else 1, 0 if (deform is None or deform[i]) else 1, depth[i])
        idx.sort(key=key)
        for i in idx[1:]:
            roles[names[i]] = "follow:" + r
    # two-joint chains named alike (Mixamo UpLeg/Leg, Arm/ForeArm variants): parent -> first, child -> second
    def is_anc(a, b):
        j = parents[b]
        while j >= 0:
            if j == a:
                return True
            j = parents[j]
        return False
    for s in SIDES:
        for first, second in (("thigh", "shin"), ("upperarm", "forearm")):
            r1, r2 = "%s_%s" % (s, first), "%s_%s" % (s, second)
            c1 = [i for i, n in enumerate(names) if roles[n] in (r1, "follow:" + r1)]
            if any(roles[n] == r2 for n in names):
                continue
            for a in c1:
                for b in c1:
                    if a != b and roles[names[a]] == r1 and is_anc(a, b) and roles[names[b]] in (r1, "follow:" + r1):
                        roles[names[b]] = r2
                        for c in c1:
                            if c not in (a, b) and is_anc(b, c):
                                roles[names[c]] = "follow:" + r2
                        break
                else:
                    continue
                break
    # twist / helper bones follow the limb segment they hang under
    limb = ("upperarm", "forearm", "thigh", "shin", "hand", "foot")
    for i, n in enumerate(names):
        r = roles[n]
        if r.startswith("follow:") and parents[i] >= 0:
            pr = roles[names[parents[i]]]
            if any(pr == "%s_%s" % (s, l) for s in SIDES for l in limb) and pr[0] == r[7]:
                roles[n] = "follow:" + pr
    # no pelvis: the lowest common ancestor of both thighs (Auto-Rig Pro's root.x, plain 'root' rigs)
    if "pelvis" not in roles.values():
        th = [i for i, n in enumerate(names) if roles[n] in ("L_thigh", "R_thigh")]
        if len(th) == 2:
            anc = []
            j = th[0]
            while j >= 0:
                anc.append(j)
                j = parents[j]
            j = th[1]
            while j >= 0 and j not in anc:
                j = parents[j]
            if j >= 0:
                roles[names[j]] = "pelvis"
    # 'finger 2a' style rigs: the numbered 'index 01..04' bones are the palm's metacarpals -> they follow the hand
    if any(re.search(r"finger\s?_?\d[abc]\b", n, re.I) for n in names):
        for n in names:
            r = roles[n]
            if re.search(r"\bindex\s?_?0\d\b", " ".join(_tokens(n))) and re.match(r"^(follow:)?[LR]_index\d$", r):
                roles[n] = "follow:%s_hand" % r.replace("follow:", "")[0]
    # no head bone: the common ancestor of the face / hair / mouth bones (descriptive rigs: 'head neck upper')
    if "head" not in roles.values():
        face = [i for i, n in enumerate(names) if roles[n] in ("follow:head", "jaw") or roles[n] in LIP_ROLES]

        def chain(i):
            out = []
            while i >= 0:
                out.append(i)
                i = parents[i]
            return out
        if face:
            common = set(chain(parents[face[0]]))
            for i in face[1:]:
                common &= set(chain(parents[i]))
            if common:
                lca = max(common, key=lambda i: depth[i])
                if roles[names[lca]] in ("auto", "ignore", "follow:neck", "follow:head", "spine"):
                    roles[names[lca]] = "head"
    # fingers: renumber each finger chain by depth (1..3, deeper ones -> 3)
    for s in SIDES:
        for f in FINGERS:
            chain = sorted((i for i, n in enumerate(names) if re.fullmatch(r"%s_%s\d" % (s, f), roles[names[i]] or "")),
                           key=lambda i: depth[i])
            for k, i in enumerate(chain):
                roles[names[i]] = "%s_%s%d" % (s, f, min(k + 1, 3))
    return rig, roles


def resolve(names, parents, roles, heads=None, centroid=None):
    """'auto' / 'ignore' -> 'follow:<role>': head descendants follow the head; other weighted bones follow the
    nearest limb / torso segment of the mapped skeleton (by where their vertices are: centroid[i], NaN = none);
    the rest the nearest mapped ancestor. Returns {bone: role}."""
    out = dict(roles)
    BI = {n: i for i, n in enumerate(names)}
    seg = []
    if heads is not None and centroid is not None:
        jb = {}
        for n, r in roles.items():
            if r and not r.startswith(("follow:", "bone:")) and r not in ("auto", "ignore", "spine"):
                jb.setdefault(r, np.asarray(heads[BI[n]], float))

        def add(role, a, b):
            if a in jb and b in jb:
                seg.append((role, jb[a], jb[b]))
        for s in SIDES:
            add(s + "_clavicle", s + "_clavicle", s + "_upperarm")
            add(s + "_upperarm", s + "_upperarm", s + "_forearm")
            add(s + "_forearm", s + "_forearm", s + "_hand")
            if s + "_hand" in jb and s + "_forearm" in jb:
                seg.append((s + "_hand", jb[s + "_hand"], jb[s + "_hand"] + 0.45 * (jb[s + "_hand"] - jb[s + "_forearm"])))
            add(s + "_thigh", s + "_thigh", s + "_shin")
            add(s + "_shin", s + "_shin", s + "_foot")
            add(s + "_foot", s + "_foot", s + "_toe")
        if "pelvis" in jb and "neck" in jb:
            m = jb["pelvis"] * 0.75 + jb["neck"] * 0.25
            seg.append(("pelvis", jb["pelvis"], m))
            seg.append(("spine", m, jb["neck"]))
        if "neck" in jb and "head" in jb:
            seg.append(("neck", jb["neck"], jb["head"]))
            seg.append(("head", jb["head"], jb["head"] + 1.5 * (jb["head"] - jb["neck"])))

    def nearest(p):
        best, bd = None, 1e30
        for role, a, b in seg:
            ab = b - a
            t = np.clip(((p - a) @ ab) / max(ab @ ab, 1e-12), 0, 1)
            d = np.linalg.norm(p - (a + t * ab))
            if d < bd:
                best, bd = role, d
        return best

    for i, n in enumerate(names):
        if out[n] not in ("auto", "ignore"):
            continue
        j = parents[i]
        r = None
        while j >= 0:
            rr = roles[names[j]]
            if rr not in ("auto", "ignore"):
                r = rr[7:] if rr.startswith("follow:") else rr
                if r.startswith("bone:"):
                    r = rr
                break
            j = parents[j]
        if r is not None and r.startswith("bone:"):
            out[n] = r
            continue
        headlike = r is not None and (r == "head" or r in LIP_ROLES)
        if not headlike and seg and centroid is not None and np.isfinite(centroid[i]).all():
            g = nearest(centroid[i])
            if g is not None:
                out[n] = "follow:" + g
                continue
        out[n] = "follow:" + (r or "pelvis")
    return out


def missing_required(roles):
    have = {r for r in roles.values()}
    return [r for r in REQUIRED if r not in have]


def save(path, rig, roles, base_code, notes=None):
    doc = {"about": "Bone map for blmodkit import-model. Values: a role (see docs/MODEL_IMPORT.md), "
                    "'follow:<role>', 'bone:<BASE BONE NAME>', 'auto' (nearest mapped parent) or 'ignore'.",
           "rig": rig, "base": base_code, "bones": roles}
    if notes:
        doc["notes"] = notes
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)


def load(path):
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    return d.get("rig", "custom"), d["bones"], d.get("materials", {}), d


def merge(auto_roles, user_roles):
    out = dict(auto_roles)
    for k, v in user_roles.items():
        if k in out:
            out[k] = v
    return out
