"""Read a few numbers from the user's own default.xex (US retail).

The hit simulator needs the hurt-sphere radii of Goku and Krillin, which live in the game's
executable data, not in the archive. default.xex is an XEX2 file: AES-128 encrypted (the
file key is decrypted with the console's well-known retail key, as every Xbox 360 emulator
does) and "basic"-compressed (data blocks + runs of zeros). The image base is 0x82000000.

AES comes from Windows' own CNG (bcrypt.dll) through ctypes - no extra Python packages.
The decrypted image is cached in the kit's cache folder on the user's PC (never shipped).
"""
import ctypes
import hashlib
import os
import struct

from .util import KitError, cache_dir

IMAGE_BASE = 0x82000000
# The US (NTSC-U) retail default.xex this game build supports.
US_XEX_SHA1 = "aec598f88cf51181fc377b148e0b1ad30db4485c"
_RETAIL_KEY = bytes([0x20, 0xB1, 0x85, 0xA5, 0x9D, 0x28, 0xFD, 0xC3,
                     0x40, 0x58, 0x3F, 0xBB, 0x08, 0x96, 0xBF, 0x91])


def _aes(key, data, mode):
    """AES-128 decrypt (ECB, or CBC with a zero IV) with Windows CNG."""
    bc = ctypes.WinDLL("bcrypt.dll")
    alg = ctypes.c_void_p()
    if bc.BCryptOpenAlgorithmProvider(ctypes.byref(alg), ctypes.c_wchar_p("AES"), None, 0):
        raise KitError("Windows AES (bcrypt) is not available")
    try:
        chain = ctypes.create_unicode_buffer("ChainingModeECB" if mode == "ecb" else "ChainingModeCBC")
        bc.BCryptSetProperty(alg, ctypes.c_wchar_p("ChainingMode"), chain, ctypes.sizeof(chain), 0)
        hkey = ctypes.c_void_p()
        kb = ctypes.create_string_buffer(key, len(key))
        if bc.BCryptGenerateSymmetricKey(alg, ctypes.byref(hkey), None, 0, kb, len(key), 0):
            raise KitError("AES key setup failed")
        try:
            inp = ctypes.create_string_buffer(bytes(data), len(data))
            out = ctypes.create_string_buffer(len(data))
            got = ctypes.c_ulong()
            iv = ctypes.create_string_buffer(16) if mode == "cbc" else None
            st = bc.BCryptDecrypt(hkey, inp, len(data), None, iv, 16 if iv is not None else 0,
                                  out, len(data), ctypes.byref(got), 0)
            if st:
                raise KitError("AES decrypt failed (0x%08X)" % (st & 0xFFFFFFFF))
            return out.raw[:got.value]
        finally:
            bc.BCryptDestroyKey(hkey)
    finally:
        bc.BCryptCloseAlgorithmProvider(alg, 0)


def _be32(d, o):
    return struct.unpack_from(">I", d, o)[0]


def decrypt_image(xex_path):
    """default.xex -> the loaded PE image (bytes; address A is at offset A - 0x82000000)."""
    d = open(xex_path, "rb").read()
    if d[:4] != b"XEX2":
        raise KitError("%s is not an XEX2 file" % xex_path)
    pe_off, sec_off, nopt = _be32(d, 8), _be32(d, 16), _be32(d, 20)
    ffi = 0
    for i in range(nopt):
        if _be32(d, 24 + 8 * i) == 0x3FF:
            ffi = _be32(d, 28 + 8 * i)
    if not ffi:
        raise KitError("default.xex: no file format header")
    enc, comp = struct.unpack_from(">HH", d, ffi + 4)
    if comp != 1:
        raise KitError("default.xex uses compression %d - only the retail US file (basic) is supported" % comp)
    payload = d[pe_off:]
    payload = payload[:len(payload) - len(payload) % 16]
    if enc == 1:
        session = _aes(_RETAIL_KEY, d[sec_off + 0x150:sec_off + 0x160], "ecb")
        plain = _aes(session, payload, "cbc")
    else:
        plain = payload
    out = bytearray()
    pos = 0
    ffi_size = _be32(d, ffi)
    for b in range(8, ffi_size, 8):
        data_size, zero_size = _be32(d, ffi + b), _be32(d, ffi + b + 4)
        out += plain[pos:pos + data_size]
        pos += data_size
        out += bytes(zero_size)
    if out[:2] != b"MZ":
        raise KitError("default.xex did not decrypt to an executable image (not the US retail file?)")
    return bytes(out)


class Image:
    """The decrypted image of the user's default.xex (cached by the file's SHA-1)."""

    def __init__(self, xex_path):
        sha = hashlib.sha1(open(xex_path, "rb").read()).hexdigest()
        self.sha1 = sha
        cached = os.path.join(cache_dir("xex"), sha + ".img")
        if os.path.exists(cached):
            self.data = open(cached, "rb").read()
        else:
            self.data = decrypt_image(xex_path)
            with open(cached, "wb") as fh:
                fh.write(self.data)

    def floats(self, address, count):
        return struct.unpack_from(">%df" % count, self.data, address - IMAGE_BASE)

    def u16(self, address):
        return struct.unpack_from(">H", self.data, address - IMAGE_BASE)[0]

    def u8(self, address):
        return self.data[address - IMAGE_BASE]
