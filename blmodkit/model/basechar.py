"""What the importer takes from the base character's own costume files (read from the user's game at run time)."""
import numpy as np

from . import gamefiles, nux_io, skeleton

BODY_FLAGS = 0x16308800


class BaseChar:
    def __init__(self, files, code, colour=0):
        self.code = code.upper()
        self.colour = colour
        nux_name, amb_name = gamefiles.costume_paths(self.code, colour, 0)
        if not files.has(nux_name):
            nux_name, amb_name = gamefiles.costume_paths(self.code, 0, 0)
        self.nux_name, self.amb_name = nux_name, amb_name
        self.nux_bytes = files.read(nux_name)
        self.amb_bytes = files.read(amb_name)
        self.nux = nux_io.parse(self.nux_bytes)
        self.rig = skeleton.Rig(self.amb_bytes)
        self.body = nux_io.body(self.nux)
        pos, bi, bw = [], [], []
        for p in self.body.polygons:
            pos.append(p.positions())
            bi.append(p.bone_idx())
            bw.append(p.weights())
        self.body_arrays = dict(pos=np.concatenate(pos), bidx=np.concatenate(bi), bw=np.concatenate(bw))
        self.template = next(p.materials[0] for p in self.body.polygons
                             if p.materials[0].flags == BODY_FLAGS and len(p.materials[0].textures) == 2)
        self.textures = {t.gidx: t for t in self.nux.textures}
        self._analyse_ramps()

    def _analyse_ramps(self):
        """skin ramp = the one on the first face model's skin; cloth ramp = the single-row ramp that shades the
        most non-white body texels (Goku: gi 0x10010049); per ramp: band values at the alpha-127 columns"""
        face = next((n for i, n in self.nux.models if i != 0xFF), None)
        self.skin_ramp = None
        if face is not None:
            m = face.polygons[0].materials[0]
            if len(m.textures) == 2:
                self.skin_ramp = m.textures[0][0]
        use = {}
        texel = {}
        for p in self.body.polygons:
            m = p.materials[0]
            if len(m.textures) != 2:
                continue
            ramp, col = m.texture_ids
            T = self.textures[col].decode()
            uv = p.uvs()
            h, w = T.shape[:2]
            px = T[(np.mod(uv[:, 1], 1.0) * h).astype(int) % h, (np.mod(uv[:, 0], 1.0) * w).astype(int) % w]
            use.setdefault(ramp, []).append(px)
        self.ramps = {}
        for gid, pxs in use.items():
            px = np.concatenate(pxs)
            img = self.textures[gid].decode()
            r = img.shape[0] // 2
            row = img[r, :, :3].astype(int)
            single = bool((img[..., :3] == img[r:r + 1, :, :3]).all())
            med = np.median(px[:, :3], 0)
            self.ramps[gid] = dict(row=row, single_row=single, vertices=len(px), texel_median=med,
                                   texel_white=bool(med.min() >= 245), shadow=row[100], mid=row[130], lit=row[170],
                                   alpha_median=float(np.median(px[:, 3])))
            texel[gid] = med
        cloth = [(g, r) for g, r in self.ramps.items() if g != self.skin_ramp and r["single_row"] and not r["texel_white"]]
        self.cloth_ramp = max(cloth, key=lambda t: t[1]["vertices"])[0] if cloth else None
        if self.skin_ramp is not None and self.skin_ramp in self.ramps:
            self.skin_texel = self.ramps[self.skin_ramp]["texel_median"]
        else:
            self.skin_texel = np.array([255.0, 255.0, 255.0])

    def ramp_output(self, gid, lit_texel):
        """display colour of a texel under this ramp in the shadow / mid / lit bands (subtractive shader)"""
        r = self.ramps[gid]
        t = np.asarray(lit_texel, float)
        return {k: np.clip(t - r[k], 0, 255) for k in ("shadow", "mid", "lit")}
