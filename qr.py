"""
Minimal QR Code generator — byte mode, versions 1-10, EC levels L and M.
Pure Python standard library only. Enough to encode a LAN URL into a PNG.

  matrix = encode("http://192.168.1.5:8000/play")   # list[list[bool]], no border
  png_bytes = png(matrix, scale=10, border=4)

Verified against the reference `qrcode` package for a range of inputs/versions.
"""

import struct
import zlib

# --- Galois field GF(256) for Reed-Solomon --------------------------------------
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gf_mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(deg):
    # product of (x - a^0)(x - a^1)...(x - a^(deg-1)); monic, highest degree first
    poly = [1]
    for i in range(deg):
        new = poly + [0]
        for j in range(len(poly)):
            new[j + 1] ^= _gf_mul(poly[j], _EXP[i])
        poly = new
    return poly


def _rs_encode(data, ec_len):
    gen = _rs_generator(ec_len)
    res = list(data) + [0] * ec_len
    for i in range(len(data)):
        coef = res[i]
        if coef != 0:
            for j in range(len(gen)):
                res[i + j] ^= _gf_mul(gen[j], coef)
    return res[len(data):]


# --- capacity / block tables (versions 1-10) -----------------------------------
# level -> version -> (ec_per_block, g1_blocks, g1_data, g2_blocks, g2_data)
_BLOCKS = {
    "L": {
        1: (7, 1, 19, 0, 0), 2: (10, 1, 34, 0, 0), 3: (15, 1, 55, 0, 0),
        4: (20, 1, 80, 0, 0), 5: (26, 1, 108, 0, 0), 6: (18, 2, 68, 0, 0),
        7: (20, 2, 78, 0, 0), 8: (24, 2, 97, 0, 0), 9: (30, 2, 116, 0, 0),
        10: (18, 2, 68, 2, 69),
    },
    "M": {
        1: (10, 1, 16, 0, 0), 2: (16, 1, 28, 0, 0), 3: (26, 1, 44, 0, 0),
        4: (18, 2, 32, 0, 0), 5: (24, 2, 43, 0, 0), 6: (16, 4, 27, 0, 0),
        7: (18, 4, 31, 0, 0), 8: (22, 2, 38, 2, 39), 9: (22, 3, 36, 2, 37),
        10: (26, 4, 43, 1, 44),
    },
}

_ALIGN = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
    7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}

_EC_FORMAT_BITS = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}


def _total_data_codewords(level, version):
    ec, g1n, g1d, g2n, g2d = _BLOCKS[level][version]
    return g1n * g1d + g2n * g2d


def _char_capacity(level, version):
    total_bits = _total_data_codewords(level, version) * 8
    cc_bits = 8 if version <= 9 else 16
    return (total_bits - 4 - cc_bits) // 8


# --- bit buffer ---------------------------------------------------------------
class _Bits:
    def __init__(self):
        self.bits = []

    def put(self, value, length):
        for i in range(length - 1, -1, -1):
            self.bits.append((value >> i) & 1)

    def __len__(self):
        return len(self.bits)


# --- data encoding -----------------------------------------------------------
def _encode_data(text, level):
    raw = text.encode("utf-8")
    version = None
    for v in range(1, 11):
        if len(raw) <= _char_capacity(level, v):
            version = v
            break
    if version is None:
        raise ValueError("data too long for versions 1-10")

    buf = _Bits()
    buf.put(0b0100, 4)                       # byte mode
    buf.put(len(raw), 8 if version <= 9 else 16)
    for b in raw:
        buf.put(b, 8)

    total_codewords = _total_data_codewords(level, version)
    capacity_bits = total_codewords * 8
    # terminator
    for _ in range(min(4, capacity_bits - len(buf))):
        buf.bits.append(0)
    # byte align
    while len(buf) % 8:
        buf.bits.append(0)
    # pad bytes
    pad = [0xEC, 0x11]
    i = 0
    codewords = [int("".join(map(str, buf.bits[j:j + 8])), 2) for j in range(0, len(buf), 8)]
    while len(codewords) < total_codewords:
        codewords.append(pad[i % 2])
        i += 1

    return version, _interleave(codewords, level, version)


def _interleave(data_codewords, level, version):
    ec, g1n, g1d, g2n, g2d = _BLOCKS[level][version]
    blocks = []
    pos = 0
    for _ in range(g1n):
        blocks.append(data_codewords[pos:pos + g1d]); pos += g1d
    for _ in range(g2n):
        blocks.append(data_codewords[pos:pos + g2d]); pos += g2d
    ec_blocks = [_rs_encode(b, ec) for b in blocks]

    result = []
    maxlen = max(len(b) for b in blocks)
    for i in range(maxlen):
        for b in blocks:
            if i < len(b):
                result.append(b[i])
    for i in range(ec):
        for b in ec_blocks:
            result.append(b[i])

    bits = []
    for cw in result:
        for k in range(7, -1, -1):
            bits.append((cw >> k) & 1)
    return bits


# --- matrix construction ----------------------------------------------------
def _new_matrix(version):
    size = version * 4 + 17
    return [[None] * size for _ in range(size)], size


def _place_finder(m, size, r, c):
    for dr in range(-1, 8):
        for dc in range(-1, 8):
            rr, cc = r + dr, c + dc
            if 0 <= rr < size and 0 <= cc < size:
                on = (0 <= dr <= 6 and dc in (0, 6)) or \
                     (0 <= dc <= 6 and dr in (0, 6)) or \
                     (2 <= dr <= 4 and 2 <= dc <= 4)
                m[rr][cc] = on


def _place_alignment(m, size, version):
    centers = _ALIGN[version]
    for r in centers:
        for c in centers:
            if (r, c) in ((6, 6), (6, centers[-1]), (centers[-1], 6)):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    m[r + dr][c + dc] = max(abs(dr), abs(dc)) != 1


def _place_timing(m, size):
    for i in range(8, size - 8):
        v = (i % 2 == 0)
        if m[6][i] is None:
            m[6][i] = v
        if m[i][6] is None:
            m[i][6] = v


def _reserve_format(m, size):
    for i in range(9):
        if m[8][i] is None:
            m[8][i] = False
        if m[i][8] is None:
            m[i][8] = False
    for i in range(8):
        m[8][size - 1 - i] = False
        m[size - 1 - i][8] = False
    m[size - 8][8] = True   # dark module


def _reserve_version(m, size, version):
    if version < 7:
        return
    for i in range(6):
        for j in range(3):
            m[i][size - 11 + j] = False
            m[size - 11 + j][i] = False


def _data_positions(m, size):
    col = size - 1
    up = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if up else range(size)
        for r in rows:
            for c in (col, col - 1):
                if m[r][c] is None:
                    yield r, c
        up = not up
        col -= 2


_MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _bch_format(fmt):
    g = 0b10100110111
    v = fmt << 10
    for i in range(4, -1, -1):
        if v & (1 << (i + 10)):
            v ^= g << i
    return ((fmt << 10) | v) ^ 0b101010000010010


def _bch_version(ver):
    g = 0b1111100100101
    v = ver << 12
    for i in range(5, -1, -1):
        if v & (1 << (i + 12)):
            v ^= g << i
    return (ver << 12) | v


def _apply_format(m, size, level, mask):
    fmt = (_EC_FORMAT_BITS[level] << 3) | mask
    bits = _bch_format(fmt)
    for i in range(15):
        bit = (bits >> i) & 1
        # around top-left
        if i < 6:
            m[i][8] = bool(bit)
        elif i == 6:
            m[7][8] = bool(bit)
        elif i == 7:
            m[8][8] = bool(bit)
        elif i == 8:
            m[8][7] = bool(bit)
        else:
            m[8][14 - i] = bool(bit)
        # around top-right / bottom-left
        if i < 8:
            m[8][size - 1 - i] = bool(bit)
        else:
            m[size - 15 + i][8] = bool(bit)


def _apply_version(m, size, version):
    if version < 7:
        return
    bits = _bch_version(version)
    for i in range(18):
        bit = bool((bits >> i) & 1)
        r, c = i // 3, i % 3
        m[r][size - 11 + c] = bit
        m[size - 11 + c][r] = bit


def _penalty(m, size):
    score = 0
    # rule 1: runs of 5+
    for line in (m, list(zip(*m))):
        for row in line:
            run = 1
            for i in range(1, size):
                if row[i] == row[i - 1]:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + (run - 5)
                    run = 1
            if run >= 5:
                score += 3 + (run - 5)
    # rule 2: 2x2 blocks
    for r in range(size - 1):
        for c in range(size - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    # rule 3: finder-like patterns
    pat1 = [True, False, True, True, True, False, True, False, False, False, False]
    pat2 = list(reversed(pat1))
    for r in range(size):
        for c in range(size - 10):
            seg = [m[r][c + k] for k in range(11)]
            if seg == pat1 or seg == pat2:
                score += 40
    for c in range(size):
        for r in range(size - 10):
            seg = [m[r + k][c] for k in range(11)]
            if seg == pat1 or seg == pat2:
                score += 40
    # rule 4: deviation of dark-module ratio from 50%
    dark = sum(row.count(True) for row in m)
    ratio = dark * 100.0 / (size * size)
    score += int(abs(ratio - 50) / 5) * 10
    return score


def encode(text, level="M"):
    """Return the QR module matrix (list[list[bool]]) with no quiet zone."""
    version, data_bits = _encode_data(text, level)
    base, size = _new_matrix(version)
    _place_finder(base, size, 0, 0)
    _place_finder(base, size, 0, size - 7)
    _place_finder(base, size, size - 7, 0)
    _place_alignment(base, size, version)
    _place_timing(base, size)
    _reserve_version(base, size, version)
    _reserve_format(base, size)

    positions = list(_data_positions(base, size))

    def lay(mask, real):
        m = [row[:] for row in base]
        fn = _MASKS[mask]
        di = 0
        for (r, c) in positions:
            bit = data_bits[di] if di < len(data_bits) else 0
            di += 1
            m[r][c] = bool(bit) ^ fn(r, c)
        if real:
            _apply_format(m, size, level, mask)
            _apply_version(m, size, version)
        else:
            # match the spec's mask-evaluation state: format / version / dark
            # module all treated as light while scoring penalties
            m[size - 8][8] = False
        return [[bool(x) for x in row] for row in m]

    best_mask, best_score = 0, None
    for mask in range(8):
        p = _penalty(lay(mask, real=False), size)
        if best_score is None or p < best_score:
            best_mask, best_score = mask, p
    return lay(best_mask, real=True)


# --- PNG output --------------------------------------------------------------
def png(matrix, scale=10, border=4, dark=(0, 0, 0), light=(255, 255, 255)):
    n = len(matrix)
    total = n + border * 2
    w = h = total * scale

    def px(r, c):
        br, bc = r // scale, c // scale
        mr, mc = br - border, bc - border
        if 0 <= mr < n and 0 <= mc < n and matrix[mr][mc]:
            return dark
        return light

    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            raw.extend(px(y, x))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
