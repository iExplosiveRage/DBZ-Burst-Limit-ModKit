"""Reader for the game's CRI CPK archive (LONG2DATA_US.CPK).

Layout: "CPK " header + @UTF table (TocOffset, ContentOffset, ...), "TOC " + @UTF table with
one row per file (DirName, FileName, FileSize, ExtractSize, FileOffset). @UTF tables may be
XOR-scrambled. Files with ExtractSize > FileSize are CRILAYLA-compressed (in this game the
"CRILAYLA" magic is zeroed). Read only - the kit never writes the archive.
"""
import struct


def _decrypt_utf(data):
    out = bytearray(len(data))
    m, t = 0x0000655F, 0x00004115
    for i, b in enumerate(data):
        out[i] = b ^ (m & 0xFF)
        m = (m * t) & 0xFFFFFFFF
    return bytes(out)


def read_utf(chunk):
    """@UTF table -> list of row dicts."""
    if chunk[:4] != b"@UTF":
        chunk = _decrypt_utf(chunk)
    if chunk[:4] != b"@UTF":
        raise ValueError("not a CRI @UTF table")
    base = 8
    rows_off, strings_off, data_off, _name_off = struct.unpack(">IIII", chunk[8:24])
    ncols, row_len = struct.unpack(">HH", chunk[24:28])
    nrows = struct.unpack(">I", chunk[28:32])[0]
    rows_off += base
    strings_off += base
    data_off += base

    def string_at(off):
        start = strings_off + off
        return chunk[start:chunk.index(b"\0", start)].decode("latin1")

    fmts = {0: ">B", 1: ">b", 2: ">H", 3: ">h", 4: ">I", 5: ">i", 6: ">Q", 7: ">q", 8: ">f"}

    def value(typ, pos):
        if typ in fmts:
            f = fmts[typ]
            n = struct.calcsize(f)
            return struct.unpack(f, chunk[pos:pos + n])[0], n
        if typ == 0xA:
            return string_at(struct.unpack(">I", chunk[pos:pos + 4])[0]), 4
        if typ == 0xB:
            off, size = struct.unpack(">II", chunk[pos:pos + 8])
            return chunk[data_off + off:data_off + off + size], 8
        raise ValueError("unknown @UTF column type %x" % typ)

    columns = []
    pos = 32
    for _ in range(ncols):
        flags = chunk[pos]
        name = string_at(struct.unpack(">I", chunk[pos + 1:pos + 5])[0])
        pos += 5
        storage, typ = flags & 0xF0, flags & 0x0F
        const = None
        if storage == 0x30:
            const, used = value(typ, pos)
            pos += used
        columns.append((name, storage, typ, const))
    rows = []
    for r in range(nrows):
        pos = rows_off + r * row_len
        row = {}
        for name, storage, typ, const in columns:
            if storage == 0x50:
                row[name], used = value(typ, pos)
                pos += used
            elif storage == 0x30:
                row[name] = const
            else:
                row[name] = None
        rows.append(row)
    return rows


def crilayla(data):
    """Decompress one CRILAYLA file (returns the input when it isn't compressed).

    The compressed stream is read backwards; output is written from the end to the start;
    the first 0x100 bytes of the file are stored raw after the stream."""
    if len(data) < 16 + 0x100:
        return data
    usize, hoff = struct.unpack("<II", data[8:16])
    if data[:8] not in (b"CRILAYLA", bytes(8)) or 16 + hoff + 0x100 != len(data):
        return data
    out = bytearray(usize + 0x100)
    out[:0x100] = data[16 + hoff:16 + hoff + 0x100]
    src = data[16:16 + hoff]
    pool = 0
    left = 0
    pos = len(src) - 1

    def bits(n):
        nonlocal pool, left, pos
        v = 0
        while n:
            if left == 0:
                pool = src[pos]
                pos -= 1
                left = 8
            take = left if left < n else n
            v = (v << take) | ((pool >> (left - take)) & ((1 << take) - 1))
            left -= take
            n -= take
        return v

    write = usize + 0x100 - 1
    end = 0x100
    while write >= end:
        if bits(1):
            ref = write + 3 + bits(13)
            length = 3
            for nb in (2, 3, 5):
                v = bits(nb)
                length += v
                if v != (1 << nb) - 1:
                    break
            else:
                while True:
                    v = bits(8)
                    length += v
                    if v != 255:
                        break
            dist = ref - write
            if length <= dist:
                # source and destination don't overlap: one slice copy (backwards = same bytes)
                out[write - length + 1:write + 1] = out[ref - length + 1:ref + 1]
                write -= length
            else:
                for _ in range(length):
                    out[write] = out[ref]
                    write -= 1
                    ref -= 1
        else:
            out[write] = bits(8)
            write -= 1
    return bytes(out)


class Cpk:
    """An open CPK: rows by upper-case path ('PAC/CMN/CH/BCGOK/BCGOKM0.AMB')."""

    def __init__(self, path):
        self.path = path
        self.f = open(path, "rb")
        head = self.f.read(0x10)
        if head[:4] != b"CPK ":
            raise ValueError("%s is not a CPK archive" % path)
        size = struct.unpack("<Q", head[8:16])[0]
        header = read_utf(self.f.read(size))[0]
        toc_offset = header["TocOffset"]
        content_offset = header.get("ContentOffset")
        self.f.seek(toc_offset)
        toc_head = self.f.read(0x10)
        if toc_head[:4] != b"TOC ":
            raise ValueError("%s: no TOC" % path)
        toc_size = struct.unpack("<Q", toc_head[8:16])[0]
        self.rows = read_utf(self.f.read(toc_size))
        self.base = min(toc_offset, content_offset) if content_offset else toc_offset
        self.by_path = {}
        for r in self.rows:
            p = ((r.get("DirName") or "") + "/" + (r.get("FileName") or "")).lstrip("/")
            r["path"] = p.replace("\\", "/").upper()
            self.by_path[r["path"]] = r

    def close(self):
        self.f.close()

    def __contains__(self, path):
        return path.replace("\\", "/").upper() in self.by_path

    def paths(self):
        return [r["path"] for r in self.rows if r.get("FileName")]

    def size(self, path):
        return self.by_path[path.replace("\\", "/").upper()]["ExtractSize"]

    def read(self, path, head=None):
        """The file's bytes (decompressed). head: only the first `head` bytes when the file is
        compressed and head <= 0x100 (CRILAYLA keeps them raw - fast)."""
        r = self.by_path[path.replace("\\", "/").upper()]
        self.f.seek(self.base + r["FileOffset"])
        raw = self.f.read(r["FileSize"])
        if head is not None and head <= 0x100 and r["ExtractSize"] > r["FileSize"] and len(raw) > 0x110:
            usize, hoff = struct.unpack("<II", raw[8:16])
            if 16 + hoff + 0x100 == len(raw):
                return raw[16 + hoff:16 + hoff + head]
        data = crilayla(raw) if r["ExtractSize"] != r["FileSize"] else raw
        return data[:head] if head is not None else data
