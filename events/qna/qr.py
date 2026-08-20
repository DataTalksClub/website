"""Dependency-free QR renderers for the canonical Event Q&A share URL."""

from __future__ import annotations

import struct
import zlib
from xml.sax.saxutils import escape as xml_escape


class QRCodeUnavailable(RuntimeError):
    """The share URL cannot fit in the supported QR code format."""


# Each version contains one or two block groups.  A group is (blocks, total
# codewords, data codewords) for the QR error-correction level M.
_ECC_M: tuple[tuple[tuple[int, int, int], ...], ...] = (
    ((1, 26, 16),),
    ((1, 44, 28),),
    ((1, 70, 44),),
    ((2, 50, 32),),
    ((2, 67, 43),),
    ((4, 43, 27),),
    ((4, 49, 31),),
    ((2, 60, 38), (2, 61, 39)),
    ((3, 58, 36), (2, 59, 37)),
    ((4, 69, 43), (1, 70, 44)),
    ((1, 80, 50), (4, 81, 51)),
    ((6, 58, 36), (2, 59, 37)),
    ((8, 59, 37), (1, 60, 38)),
    ((4, 64, 40), (5, 65, 41)),
    ((5, 65, 41), (5, 66, 42)),
    ((7, 73, 45), (3, 74, 46)),
    ((10, 74, 46), (1, 75, 47)),
    ((9, 69, 43), (4, 70, 44)),
    ((3, 70, 44), (11, 71, 45)),
    ((3, 67, 41), (13, 68, 42)),
    ((17, 68, 42),),
    ((17, 74, 46),),
    ((4, 75, 47), (14, 76, 48)),
    ((6, 73, 45), (14, 74, 46)),
    ((8, 75, 47), (13, 76, 48)),
    ((19, 74, 46), (4, 75, 47)),
    ((22, 73, 45), (3, 74, 46)),
    ((3, 73, 45), (23, 74, 46)),
    ((21, 73, 45), (7, 74, 46)),
    ((19, 75, 47), (10, 76, 48)),
    ((2, 74, 46), (29, 75, 47)),
    ((10, 74, 46), (23, 75, 47)),
    ((14, 74, 46), (21, 75, 47)),
    ((14, 74, 46), (23, 75, 47)),
    ((12, 75, 47), (26, 76, 48)),
    ((6, 75, 47), (34, 76, 48)),
    ((29, 74, 46), (14, 75, 47)),
    ((13, 74, 46), (32, 75, 47)),
    ((40, 75, 47), (7, 76, 48)),
    ((18, 75, 47), (31, 76, 48)),
)


def _append_bits(bits: list[int], value: int, length: int) -> None:
    bits.extend((value >> shift) & 1 for shift in range(length - 1, -1, -1))


def _gf_multiply(left: int, right: int) -> int:
    result = 0
    while right:
        if right & 1:
            result ^= left
        right >>= 1
        left <<= 1
        if left & 0x100:
            left ^= 0x11D
    return result


def _reed_solomon(data: list[int], ecc_length: int) -> list[int]:
    generator = [1]
    for exponent in range(ecc_length):
        next_generator = [0] * (len(generator) + 1)
        factor = 1
        for _ in range(exponent):
            factor = _gf_multiply(factor, 2)
        for index, coefficient in enumerate(generator):
            next_generator[index] ^= coefficient
            next_generator[index + 1] ^= _gf_multiply(coefficient, factor)
        generator = next_generator

    remainder = [0] * ecc_length
    for codeword in data:
        factor = codeword ^ remainder[0]
        remainder = remainder[1:] + [0]
        for index in range(ecc_length):
            remainder[index] ^= _gf_multiply(generator[index + 1], factor)
    return remainder


def _codewords(url: str, version: int) -> list[int]:
    payload = url.encode("utf-8")
    groups = _ECC_M[version - 1]
    data_capacity = sum(blocks * data for blocks, _total, data in groups)
    count_length = 8 if version < 10 else 16
    bits: list[int] = []
    _append_bits(bits, 0b0100, 4)
    _append_bits(bits, len(payload), count_length)
    for byte in payload:
        _append_bits(bits, byte, 8)
    capacity_bits = data_capacity * 8
    if len(bits) > capacity_bits:
        raise QRCodeUnavailable("the Event Q&A URL is too long for a QR code")
    _append_bits(bits, 0, min(4, capacity_bits - len(bits)))
    bits.extend([0] * (8 - (len(bits) % 8)))
    data = [
        sum(bits[index + offset] << (7 - offset) for offset in range(8))
        for index in range(0, len(bits), 8)
    ]
    pad = (0xEC, 0x11)
    pad_start = len(data)
    for index in range(len(data), data_capacity):
        data.append(pad[(index - pad_start) % 2])

    blocks: list[tuple[list[int], list[int]]] = []
    offset = 0
    for block_count, total_codewords, data_codewords in groups:
        ecc_length = total_codewords - data_codewords
        for _ in range(block_count):
            block = data[offset : offset + data_codewords]
            offset += data_codewords
            blocks.append((block, _reed_solomon(block, ecc_length)))
    result: list[int] = []
    for index in range(max(len(block[0]) for block in blocks)):
        result.extend(block[0][index] for block in blocks if index < len(block[0]))
    for index in range(max(len(block[1]) for block in blocks)):
        result.extend(block[1][index] for block in blocks if index < len(block[1]))
    return result


def _bch(value: int, polynomial: int, degree: int) -> int:
    original = value
    value <<= degree
    while value.bit_length() >= polynomial.bit_length():
        value ^= polynomial << (value.bit_length() - polynomial.bit_length())
    return (original << degree) | value


def _alignment_positions(version: int, size: int) -> list[int]:
    if version == 1:
        return []
    count = version // 7 + 2
    step = 26 if version == 32 else ((version * 4 + count * 2 + 1) // (count * 2 - 2)) * 2
    return [6, *reversed([size - 7 - step * index for index in range(count - 1)])]


def _matrix(url: str) -> list[list[bool]]:
    for version in range(1, 41):
        try:
            codewords = _codewords(url, version)
        except QRCodeUnavailable:
            continue
        return _make_matrix(codewords, version)
    raise QRCodeUnavailable("the Event Q&A URL is too long for a QR code")


def _make_matrix(codewords: list[int], version: int) -> list[list[bool]]:
    size = version * 4 + 17
    matrix: list[list[bool | None]] = [[None] * size for _ in range(size)]
    function: list[list[bool]] = [[False] * size for _ in range(size)]

    def set_function(x: int, y: int, dark: bool) -> None:
        if 0 <= x < size and 0 <= y < size:
            matrix[y][x] = dark
            function[y][x] = True

    def finder(center_x: int, center_y: int) -> None:
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                distance = max(abs(dx), abs(dy))
                set_function(center_x + dx, center_y + dy, distance not in {2, 4})

    for index in range(size):
        set_function(6, index, index % 2 == 0)
        set_function(index, 6, index % 2 == 0)
    finder(3, 3)
    finder(size - 4, 3)
    finder(3, size - 4)

    positions = _alignment_positions(version, size)
    finder_centers = {(6, 6), (size - 7, 6), (6, size - 7)}
    for center_y in positions:
        for center_x in positions:
            if (center_x, center_y) in finder_centers:
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    set_function(center_x + dx, center_y + dy, max(abs(dx), abs(dy)) != 1)
    set_function(8, size - 8, True)

    if version >= 7:
        version_bits = _bch(version, 0x1F25, 12)
        for index in range(18):
            bit = (version_bits >> index) & 1 != 0
            set_function(index // 3, index % 3 + size - 8 - 3, bit)
            set_function(index % 3 + size - 8 - 3, index // 3, bit)

    mask = 0
    format_bits = _bch(mask, 0x537, 10) ^ 0x5412
    for index in range(8):
        vertical_bit = (format_bits >> index) & 1 != 0
        horizontal_bit = (format_bits >> (14 - index)) & 1 != 0
        vertical_offset = 1 if index == 6 else 0
        horizontal_offset = 1 if index == 6 else 0
        set_function(8, index + vertical_offset, vertical_bit)
        set_function(index + horizontal_offset, 8, horizontal_bit)
        set_function(size - 1 - index, 8, vertical_bit)
        set_function(8, size - 1 - index, horizontal_bit)
    set_function(8, 8, False)
    set_function(8, size - 8, True)

    data_bits = [
        bit
        for codeword in codewords
        for bit in ((codeword >> shift) & 1 for shift in range(7, -1, -1))
    ]
    bit_index = 0
    right = size - 1
    while right >= 1:
        if right == 6:
            right -= 1
        for offset in range(size):
            for x in (right, right - 1):
                upward = ((right & 2) == 0) ^ (x < 6)
                y = size - 1 - offset if upward else offset
                if function[y][x]:
                    continue
                dark = bit_index < len(data_bits) and bool(data_bits[bit_index])
                bit_index += 1
                if (x + y) % 2 == 0:
                    dark = not dark
                matrix[y][x] = dark
        right -= 2
    return [[bool(cell) for cell in row] for row in matrix]


def svg(url: str) -> bytes:
    matrix = _matrix(url)
    size = len(matrix) + 8
    paths: list[str] = []
    for y, row in enumerate(matrix, start=4):
        for x, dark in enumerate(row, start=4):
            if dark:
                paths.append(f"M{x} {y}h1v1h-1z")
    aria_label = xml_escape("Event Q&A share code")
    markup = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'role="img" aria-label="{aria_label}">'
        f'<path fill="currentColor" d="{"".join(paths)}"/></svg>'
    )
    return markup.encode("ascii")


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    checksum = struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return struct.pack(">I", len(data)) + kind + data + checksum


def png(url: str, *, size: int = 512) -> bytes:
    size = max(64, min(2048, int(size)))
    matrix = _matrix(url)
    modules = len(matrix) + 8
    scale = max(1, size // modules)
    pixels = modules * scale
    rows: list[bytearray] = []
    for y in range(modules):
        source_y = y // scale - 4
        row = bytearray([255])
        for x in range(modules):
            source_x = x // scale - 4
            dark = (
                0 <= source_y < len(matrix)
                and 0 <= source_x < len(matrix)
                and matrix[source_y][source_x]
            )
            row.extend((0 if dark else 255,) * 3)
        rows.extend(row for _ in range(scale))
    raw = b"".join(bytes(row) for row in rows)
    return b"\x89PNG\r\n\x1a\n" + b"".join(
        (
            _png_chunk(b"IHDR", struct.pack(">IIBBBBB", pixels, pixels, 8, 2, 0, 0, 0)),
            _png_chunk(b"IDAT", zlib.compress(raw, 9)),
            _png_chunk(b"IEND", b""),
        )
    )
