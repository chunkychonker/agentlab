"""Decodes an 8-bit RGB, non-interlaced PNG into raw pixels using only the stdlib.

Exists so the walkability check can read the floor plans without adding an
imaging dependency to the verify gate.
"""

import struct
import zlib

_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_BPP = 3


def decode(data: bytes) -> tuple[int, int, bytes]:
    """Returns (width, height, pixels) with pixels as row-major RGB bytes.

    Raises ValueError for anything but an 8-bit RGB (colour type 2),
    non-interlaced PNG, or for a corrupt stream.
    """
    if data[:8] != _SIGNATURE:
        raise ValueError("not a PNG")
    pos, idat, header = 8, bytearray(), None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    if header is None:
        raise ValueError("PNG has no IHDR")
    width, height, depth, colour, _, _, interlace = header
    if (depth, colour, interlace) != (8, 2, 0):
        raise ValueError(f"unsupported PNG (depth={depth}, colour type={colour}, interlace={interlace}); need 8-bit RGB")
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise ValueError(f"corrupt PNG data: {exc}") from exc
    stride = width * _BPP
    if len(raw) != height * (stride + 1):
        raise ValueError("PNG data length does not match its header")
    out, prev = bytearray(height * stride), bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        line = _unfilter(raw[start], bytearray(raw[start + 1:start + 1 + stride]), prev)
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return width, height, bytes(out)


def _unfilter(kind: int, line: bytearray, prev: bytearray) -> bytearray:
    if kind == 0:
        return line
    if kind == 1:
        for x in range(_BPP, len(line)):
            line[x] = (line[x] + line[x - _BPP]) & 255
    elif kind == 2:
        for x in range(len(line)):
            line[x] = (line[x] + prev[x]) & 255
    elif kind == 3:
        for x in range(len(line)):
            left = line[x - _BPP] if x >= _BPP else 0
            line[x] = (line[x] + ((left + prev[x]) >> 1)) & 255
    elif kind == 4:
        for x in range(len(line)):
            a = line[x - _BPP] if x >= _BPP else 0
            b, c = prev[x], (prev[x - _BPP] if x >= _BPP else 0)
            pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
            line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
    else:
        raise ValueError(f"unknown PNG filter type {kind}")
    return line
