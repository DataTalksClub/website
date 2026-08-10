from __future__ import annotations

import os
import re
import struct
import xml.etree.ElementTree as ElementTree
import zlib
from collections.abc import Iterable, Iterator, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from multiprocessing import current_process, get_all_start_methods, get_context
from pathlib import PurePosixPath
from threading import Lock

_SVG_DECLARATION = re.compile(r"^\s*<\?xml\s+[^?]*\?>", re.IGNORECASE)
_SVG_PROCESSING_INSTRUCTION = re.compile(r"<\?\s*([a-z_:][\w:.-]*)", re.IGNORECASE)
_SVG_DECLARATION_OR_ENTITY = re.compile(r"<!\s*(?:doctype|entity)\b", re.IGNORECASE)
_CSS_URL_TOKEN = re.compile(r"(?<![\w-])url\s*\(", re.IGNORECASE)
_CSS_URL = re.compile(r"url\s*\(\s*([^)]*?)\s*\)", re.IGNORECASE | re.DOTALL)
_SVG_PRESENTATION_ATTRIBUTES = frozenset(
    {
        "alignment-baseline",
        "baseline-shift",
        "clip",
        "clip-path",
        "clip-rule",
        "color",
        "color-interpolation",
        "color-interpolation-filters",
        "color-profile",
        "color-rendering",
        "cursor",
        "direction",
        "display",
        "dominant-baseline",
        "enable-background",
        "fill",
        "fill-opacity",
        "fill-rule",
        "filter",
        "flood-color",
        "flood-opacity",
        "font-family",
        "font-size",
        "font-size-adjust",
        "font-stretch",
        "font-style",
        "font-variant",
        "font-weight",
        "glyph-orientation-horizontal",
        "glyph-orientation-vertical",
        "image-rendering",
        "kerning",
        "letter-spacing",
        "lighting-color",
        "marker",
        "marker-end",
        "marker-mid",
        "marker-start",
        "mask",
        "opacity",
        "overflow",
        "pointer-events",
        "shape-rendering",
        "stop-color",
        "stop-opacity",
        "stroke",
        "stroke-dasharray",
        "stroke-dashoffset",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "stroke-opacity",
        "stroke-width",
        "text-anchor",
        "text-decoration",
        "text-rendering",
        "transform",
        "transform-origin",
        "unicode-bidi",
        "vector-effect",
        "visibility",
        "white-space",
        "word-spacing",
        "writing-mode",
    }
)
_SVG_ACTIVE_TAGS = frozenset(
    {
        "animate",
        "animatemotion",
        "animatetransform",
        "discard",
        "embed",
        "foreignobject",
        "iframe",
        "link",
        "object",
        "script",
        "set",
    }
)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_CRITICAL_CHUNKS = frozenset({b"IHDR", b"PLTE", b"IDAT", b"IEND"})
_MAX_DECODED_RASTER_BYTES = 128 * 1024 * 1024
_MAX_RASTER_DIMENSION = 100_000
_MAX_RASTER_SCANLINES = 1_000_000
_JPEG_FRAME_PRECISIONS = {
    0xC0: frozenset({8}),
    0xC2: frozenset({8}),
}
_PARALLEL_MEDIA_MIN_ITEMS = 64
_PARALLEL_MEDIA_WORKERS = 4
_PARALLEL_MEDIA_PAYLOAD: Mapping[str, bytes] | None = None
_PARALLEL_MEDIA_LOCK = Lock()
_MEDIA_CONTENT_TYPES = frozenset({"image/gif", "image/jpeg", "image/png", "image/svg+xml"})
_MEDIA_VALIDATION_CODES = frozenset(
    {
        "media_extension_content_mismatch",
        "unsafe_svg",
        "unsafe_svg_external_reference",
        "unsupported_media_type",
    }
)
_MEDIA_WORKER_FAILURE = "media_validation_worker_failed"
_MEDIA_BATCH_CODES = _MEDIA_VALIDATION_CODES | {_MEDIA_WORKER_FAILURE}


class MediaValidationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _invalid() -> MediaValidationError:
    return MediaValidationError("media_extension_content_mismatch")


def _jpeg_segment(data: bytes, position: int) -> tuple[bytes, int]:
    if position + 2 > len(data):
        raise _invalid()
    length = int.from_bytes(data[position : position + 2], "big")
    if length < 2 or position + length > len(data):
        raise _invalid()
    return data[position + 2 : position + length], position + length


@dataclass(frozen=True, slots=True)
class _JpegComponent:
    horizontal_sampling: int
    vertical_sampling: int
    quantization_table: int


@dataclass(frozen=True, slots=True)
class _JpegFrame:
    marker: int
    width: int
    height: int
    components: dict[int, _JpegComponent]

    @property
    def maximum_horizontal_sampling(self) -> int:
        return max(component.horizontal_sampling for component in self.components.values())

    @property
    def maximum_vertical_sampling(self) -> int:
        return max(component.vertical_sampling for component in self.components.values())

    @property
    def mcu_columns(self) -> int:
        divisor = 8 * self.maximum_horizontal_sampling
        return (self.width + divisor - 1) // divisor

    @property
    def mcu_rows(self) -> int:
        divisor = 8 * self.maximum_vertical_sampling
        return (self.height + divisor - 1) // divisor

    def component_block_shape(self, component_id: int) -> tuple[int, int]:
        component = self.components[component_id]
        return (
            self.mcu_columns * component.horizontal_sampling,
            self.mcu_rows * component.vertical_sampling,
        )


class _JpegBitReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.byte_position = 0
        self.bit_buffer = 0
        self.buffered_bits = 0

    def read_bit(self) -> int:
        return self.read_bits(1)

    def read_bits(self, count: int) -> int:
        if count < 0:
            raise _invalid()
        while self.buffered_bits < count and self.byte_position < len(self.data):
            self.bit_buffer = (self.bit_buffer << 8) | self.data[self.byte_position]
            self.buffered_bits += 8
            self.byte_position += 1
        if self.buffered_bits < count:
            raise _invalid()
        remaining = self.buffered_bits - count
        value = (self.bit_buffer >> remaining) & ((1 << count) - 1)
        self.bit_buffer &= (1 << remaining) - 1
        self.buffered_bits = remaining
        return value

    def peek_bits(self, count: int) -> int:
        while self.buffered_bits < count and self.byte_position < len(self.data):
            self.bit_buffer = (self.bit_buffer << 8) | self.data[self.byte_position]
            self.buffered_bits += 8
            self.byte_position += 1
        if self.buffered_bits < count:
            raise _invalid()
        return (self.bit_buffer >> (self.buffered_bits - count)) & ((1 << count) - 1)

    def discard_bits(self, count: int) -> None:
        if count > self.buffered_bits:
            raise _invalid()
        self.buffered_bits -= count
        self.bit_buffer &= (1 << self.buffered_bits) - 1

    def read_huffman(self, table: _JpegHuffmanTable | None) -> int:
        if table is None:
            raise _invalid()
        while self.buffered_bits < 8 and self.byte_position < len(self.data):
            self.bit_buffer = (self.bit_buffer << 8) | self.data[self.byte_position]
            self.buffered_bits += 8
            self.byte_position += 1
        if self.buffered_bits >= 8:
            prefix = (self.bit_buffer >> (self.buffered_bits - 8)) & 0xFF
            match = table.eight_bit_prefixes[prefix]
            if match is not None:
                symbol, length = match
                self.buffered_bits -= length
                self.bit_buffer &= (1 << self.buffered_bits) - 1
                return symbol
        code = 0
        for length in range(1, 17):
            code = (code << 1) | self.read_bits(1)
            matched_symbol = table.codes.get((length, code))
            if matched_symbol is not None:
                return matched_symbol
        raise _invalid()

    @property
    def remaining_bits(self) -> int:
        return self.buffered_bits + (len(self.data) - self.byte_position) * 8

    def require_complete_byte_padding(self) -> None:
        remaining = self.remaining_bits
        # Encoders in the accepted corpus use both zero and one fill bits.
        # A completed scan can leave only the partial byte containing those
        # fill bits; any whole unread byte is extraneous entropy payload.
        if remaining > 7:
            raise _invalid()
        self.discard_bits(remaining)


@dataclass(frozen=True, slots=True)
class _JpegHuffmanTable:
    codes: dict[tuple[int, int], int]
    eight_bit_prefixes: tuple[tuple[int, int] | None, ...]


def _jpeg_huffman_table(counts: bytes, symbols: bytes) -> _JpegHuffmanTable:
    table: dict[tuple[int, int], int] = {}
    prefixes: list[tuple[int, int] | None] = [None] * 256
    code = 0
    symbol_position = 0
    for length, count in enumerate(counts, start=1):
        if code + count > 1 << length:
            raise _invalid()
        for _ in range(count):
            symbol = symbols[symbol_position]
            table[(length, code)] = symbol
            if length <= 8:
                prefix_start = code << (8 - length)
                for prefix in range(prefix_start, prefix_start + (1 << (8 - length))):
                    prefixes[prefix] = (symbol, length)
            symbol_position += 1
            code += 1
        code <<= 1
    if symbol_position != len(symbols):
        raise _invalid()
    return _JpegHuffmanTable(table, tuple(prefixes))


def _jpeg_entropy_chunks(data: bytes) -> tuple[tuple[bytes, ...], tuple[int, ...]]:
    chunks: list[bytearray] = [bytearray()]
    restart_markers: list[int] = []
    position = 0
    while position < len(data):
        byte = data[position]
        position += 1
        if byte != 0xFF:
            chunks[-1].append(byte)
            continue
        if position >= len(data):
            raise _invalid()
        marker = data[position]
        position += 1
        if marker == 0x00:
            chunks[-1].append(0xFF)
            continue
        if not 0xD0 <= marker <= 0xD7:
            raise _invalid()
        restart_markers.append(marker)
        chunks.append(bytearray())
    return tuple(bytes(chunk) for chunk in chunks), tuple(restart_markers)


def _jpeg_mcu_count(frame: _JpegFrame, component_ids: tuple[int, ...]) -> int:
    if len(component_ids) > 1:
        return frame.mcu_columns * frame.mcu_rows
    component = frame.components[component_ids[0]]
    columns = (
        frame.width * component.horizontal_sampling + 8 * frame.maximum_horizontal_sampling - 1
    ) // (8 * frame.maximum_horizontal_sampling)
    rows = (
        frame.height * component.vertical_sampling + 8 * frame.maximum_vertical_sampling - 1
    ) // (8 * frame.maximum_vertical_sampling)
    return columns * rows


def _jpeg_mcu_blocks(
    frame: _JpegFrame,
    component_ids: tuple[int, ...],
    mcu_index: int,
) -> tuple[tuple[int, int], ...]:
    if len(component_ids) == 1:
        return ((component_ids[0], mcu_index),)
    mcu_x = mcu_index % frame.mcu_columns
    mcu_y = mcu_index // frame.mcu_columns
    blocks: list[tuple[int, int]] = []
    for component_id in component_ids:
        component = frame.components[component_id]
        block_columns, _ = frame.component_block_shape(component_id)
        for vertical in range(component.vertical_sampling):
            for horizontal in range(component.horizontal_sampling):
                block_x = mcu_x * component.horizontal_sampling + horizontal
                block_y = mcu_y * component.vertical_sampling + vertical
                blocks.append((component_id, block_y * block_columns + block_x))
    return tuple(blocks)


def _jpeg_decode_sequential_block(
    reader: _JpegBitReader,
    dc_table: _JpegHuffmanTable | None,
    ac_table: _JpegHuffmanTable | None,
) -> None:
    if dc_table is None or ac_table is None:
        raise _invalid()
    data = reader.data
    byte_position = reader.byte_position
    bit_buffer = reader.bit_buffer
    buffered_bits = reader.buffered_bits

    while buffered_bits < 8 and byte_position < len(data):
        bit_buffer = (bit_buffer << 8) | data[byte_position]
        buffered_bits += 8
        byte_position += 1
    dc_match = (
        dc_table.eight_bit_prefixes[(bit_buffer >> (buffered_bits - 8)) & 0xFF]
        if buffered_bits >= 8
        else None
    )
    if dc_match is not None:
        dc_size, length = dc_match
        buffered_bits -= length
        bit_buffer &= (1 << buffered_bits) - 1
    else:
        code = 0
        dc_size = -1
        for length in range(1, 17):
            if buffered_bits == 0:
                if byte_position >= len(data):
                    raise _invalid()
                bit_buffer = data[byte_position]
                buffered_bits = 8
                byte_position += 1
            buffered_bits -= 1
            code = (code << 1) | ((bit_buffer >> buffered_bits) & 1)
            bit_buffer &= (1 << buffered_bits) - 1
            symbol = dc_table.codes.get((length, code))
            if symbol is not None:
                dc_size = symbol
                break
        if dc_size < 0:
            raise _invalid()
    if dc_size > 16:
        raise _invalid()
    while buffered_bits < dc_size and byte_position < len(data):
        bit_buffer = (bit_buffer << 8) | data[byte_position]
        buffered_bits += 8
        byte_position += 1
    if buffered_bits < dc_size:
        raise _invalid()
    buffered_bits -= dc_size
    bit_buffer &= (1 << buffered_bits) - 1

    coefficient = 1
    while coefficient < 64:
        while buffered_bits < 8 and byte_position < len(data):
            bit_buffer = (bit_buffer << 8) | data[byte_position]
            buffered_bits += 8
            byte_position += 1
        ac_match = (
            ac_table.eight_bit_prefixes[(bit_buffer >> (buffered_bits - 8)) & 0xFF]
            if buffered_bits >= 8
            else None
        )
        if ac_match is not None:
            symbol, length = ac_match
            buffered_bits -= length
            bit_buffer &= (1 << buffered_bits) - 1
        else:
            code = 0
            symbol = -1
            for length in range(1, 17):
                if buffered_bits == 0:
                    if byte_position >= len(data):
                        raise _invalid()
                    bit_buffer = data[byte_position]
                    buffered_bits = 8
                    byte_position += 1
                buffered_bits -= 1
                code = (code << 1) | ((bit_buffer >> buffered_bits) & 1)
                bit_buffer &= (1 << buffered_bits) - 1
                decoded = ac_table.codes.get((length, code))
                if decoded is not None:
                    symbol = decoded
                    break
            if symbol < 0:
                raise _invalid()
        zero_run, size = symbol >> 4, symbol & 0x0F
        if size == 0:
            if zero_run == 0:
                break
            if zero_run != 15 or coefficient + 16 > 64:
                raise _invalid()
            coefficient += 16
            continue
        coefficient += zero_run
        if coefficient >= 64:
            raise _invalid()
        while buffered_bits < size and byte_position < len(data):
            bit_buffer = (bit_buffer << 8) | data[byte_position]
            buffered_bits += 8
            byte_position += 1
        if buffered_bits < size:
            raise _invalid()
        buffered_bits -= size
        bit_buffer &= (1 << buffered_bits) - 1
        coefficient += 1
    reader.byte_position = byte_position
    reader.bit_buffer = bit_buffer
    reader.buffered_bits = buffered_bits


def _jpeg_decode_progressive_ac_first(
    reader: _JpegBitReader,
    table: _JpegHuffmanTable | None,
    coefficient_mask: int,
    spectral_start: int,
    spectral_end: int,
    eob_run: int,
) -> tuple[int, int]:
    if eob_run:
        return coefficient_mask, eob_run - 1
    coefficient = spectral_start
    while coefficient <= spectral_end:
        symbol = reader.read_huffman(table)
        zero_run, size = symbol >> 4, symbol & 0x0F
        if size == 0:
            if zero_run == 15:
                coefficient += 16
                if coefficient > spectral_end + 1:
                    raise _invalid()
                continue
            eob_run = (1 << zero_run) + reader.read_bits(zero_run)
            return coefficient_mask, eob_run - 1
        coefficient += zero_run
        if coefficient > spectral_end:
            raise _invalid()
        reader.read_bits(size)
        coefficient_mask |= 1 << coefficient
        coefficient += 1
    return coefficient_mask, 0


def _jpeg_decode_progressive_ac_refinement(
    reader: _JpegBitReader,
    table: _JpegHuffmanTable | None,
    coefficient_mask: int,
    spectral_start: int,
    spectral_end: int,
    eob_run: int,
) -> tuple[int, int]:
    coefficient = spectral_start
    if eob_run == 0:
        while coefficient <= spectral_end:
            symbol = reader.read_huffman(table)
            zero_run, size = symbol >> 4, symbol & 0x0F
            if size not in {0, 1}:
                raise _invalid()
            if size == 0 and zero_run != 15:
                eob_run = (1 << zero_run) + reader.read_bits(zero_run)
                break
            new_coefficient = reader.read_bit() if size else None
            remaining_zeros = zero_run
            while coefficient <= spectral_end:
                if coefficient_mask & (1 << coefficient):
                    reader.read_bit()
                elif remaining_zeros == 0:
                    break
                else:
                    remaining_zeros -= 1
                coefficient += 1
            if remaining_zeros or coefficient > spectral_end:
                raise _invalid()
            if new_coefficient is not None:
                coefficient_mask |= 1 << coefficient
            coefficient += 1
    if eob_run:
        while coefficient <= spectral_end:
            if coefficient_mask & (1 << coefficient):
                reader.read_bit()
            coefficient += 1
        eob_run -= 1
    return coefficient_mask, eob_run


def _validate_jpeg_scan_entropy(
    entropy: bytes,
    *,
    frame: _JpegFrame,
    component_ids: tuple[int, ...],
    table_selectors: dict[int, tuple[int, int]],
    huffman_tables: dict[tuple[int, int], _JpegHuffmanTable],
    spectral_start: int,
    spectral_end: int,
    successive_high: int,
    successive_low: int,
    restart_interval: int,
    coefficient_masks: dict[int, list[int]],
) -> None:
    mcu_count = _jpeg_mcu_count(frame, component_ids)
    chunks, restart_markers = _jpeg_entropy_chunks(entropy)
    interval = restart_interval or mcu_count
    expected_chunks = (mcu_count + interval - 1) // interval
    if len(chunks) != expected_chunks or len(restart_markers) != expected_chunks - 1:
        raise _invalid()
    if any(marker != 0xD0 + (index % 8) for index, marker in enumerate(restart_markers)):
        raise _invalid()

    mcu_position = 0
    sequential_block_components = tuple(
        component_id
        for component_id in component_ids
        for _ in range(
            (
                frame.components[component_id].horizontal_sampling
                * frame.components[component_id].vertical_sampling
            )
            if len(component_ids) > 1
            else 1
        )
    )
    for chunk in chunks:
        reader = _JpegBitReader(chunk)
        eob_run = 0
        chunk_mcus = min(interval, mcu_count - mcu_position)
        for mcu_index in range(mcu_position, mcu_position + chunk_mcus):
            blocks: Iterable[tuple[int, int]]
            if frame.marker == 0xC0 or spectral_start == 0:
                blocks = ((component_id, 0) for component_id in sequential_block_components)
            else:
                blocks = ((component_ids[0], mcu_index),)
            for component_id, block_index in blocks:
                dc_selector, ac_selector = table_selectors[component_id]
                if frame.marker == 0xC0:
                    _jpeg_decode_sequential_block(
                        reader,
                        huffman_tables.get((0, dc_selector)),
                        huffman_tables.get((1, ac_selector)),
                    )
                    continue
                if spectral_start == 0:
                    if successive_high == 0:
                        size = reader.read_huffman(huffman_tables.get((0, dc_selector)))
                        if size > 16:
                            raise _invalid()
                        reader.read_bits(size)
                    else:
                        reader.read_bit()
                    continue
                masks = coefficient_masks[component_id]
                if successive_high == 0:
                    masks[block_index], eob_run = _jpeg_decode_progressive_ac_first(
                        reader,
                        huffman_tables.get((1, ac_selector)),
                        masks[block_index],
                        spectral_start,
                        spectral_end,
                        eob_run,
                    )
                else:
                    masks[block_index], eob_run = _jpeg_decode_progressive_ac_refinement(
                        reader,
                        huffman_tables.get((1, ac_selector)),
                        masks[block_index],
                        spectral_start,
                        spectral_end,
                        eob_run,
                    )
        if eob_run:
            raise _invalid()
        reader.require_complete_byte_padding()
        mcu_position += chunk_mcus
    if mcu_position != mcu_count:
        raise _invalid()


def _validate_jpeg(data: bytes) -> None:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise _invalid()
    position = 2
    saw_frame = False
    saw_scan = False
    frame: _JpegFrame | None = None
    frame_components: set[int] = set()
    frame_quantization_tables: set[int] = set()
    quantization_tables: set[int] = set()
    huffman_tables: dict[tuple[int, int], _JpegHuffmanTable] = {}
    restart_interval = 0
    coefficient_masks: dict[int, list[int]] = {}
    successive_levels: dict[tuple[int, int], int] = {}
    sequential_scanned_components: set[int] = set()
    frame_markers = frozenset(
        {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }
    )
    while position < len(data):
        if data[position] != 0xFF:
            raise _invalid()
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            raise _invalid()
        marker = data[position]
        position += 1
        if marker in {0x00, 0xD8}:
            raise _invalid()
        if marker == 0xD9:
            if (
                position != len(data)
                or not saw_frame
                or not saw_scan
                or frame is None
                or (frame.marker == 0xC0 and sequential_scanned_components != set(frame.components))
            ):
                raise _invalid()
            return
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            raise _invalid()
        segment, position = _jpeg_segment(data, position)
        if marker == 0xDB:
            table_position = 0
            while table_position < len(segment):
                table_info = segment[table_position]
                table_position += 1
                precision = table_info >> 4
                table_id = table_info & 0x0F
                table_bytes = 64 * (precision + 1)
                if (
                    precision not in {0, 1}
                    or table_id > 3
                    or table_position + table_bytes > len(segment)
                ):
                    raise _invalid()
                quantization_tables.add(table_id)
                table_position += table_bytes
        elif marker == 0xC4:
            table_position = 0
            while table_position < len(segment):
                if table_position + 17 > len(segment):
                    raise _invalid()
                table_info = segment[table_position]
                table_class = table_info >> 4
                table_id = table_info & 0x0F
                counts = segment[table_position + 1 : table_position + 17]
                symbol_count = sum(counts)
                table_position += 17
                if (
                    table_class not in {0, 1}
                    or table_id > 3
                    or symbol_count == 0
                    or symbol_count > 256
                    or table_position + symbol_count > len(segment)
                ):
                    raise _invalid()
                symbols = segment[table_position : table_position + symbol_count]
                huffman_tables[(table_class, table_id)] = _jpeg_huffman_table(counts, symbols)
                table_position += symbol_count
        elif marker == 0xCC:
            raise _invalid()
        elif marker == 0xDD:
            if len(segment) != 2:
                raise _invalid()
            restart_interval = int.from_bytes(segment, "big")
        if marker in frame_markers:
            if len(segment) < 6 or saw_frame or marker not in {0xC0, 0xC2}:
                raise _invalid()
            precision = segment[0]
            height = int.from_bytes(segment[1:3], "big")
            width = int.from_bytes(segment[3:5], "big")
            components = segment[5]
            if (
                precision not in _JPEG_FRAME_PRECISIONS[marker]
                or width == 0
                or height == 0
                or width > _MAX_RASTER_DIMENSION
                or height > _MAX_RASTER_DIMENSION
                or components == 0
                or components > 4
                or width * height * components > _MAX_DECODED_RASTER_BYTES
                or len(segment) != 6 + (3 * components)
            ):
                raise _invalid()
            component_ids: set[int] = set()
            component_definitions: dict[int, _JpegComponent] = {}
            component_quantization_tables: set[int] = set()
            for offset in range(6, len(segment), 3):
                component_id, sampling, table_id = segment[offset : offset + 3]
                if (
                    component_id in component_ids
                    or sampling >> 4 == 0
                    or sampling & 0x0F == 0
                    or sampling >> 4 > 4
                    or sampling & 0x0F > 4
                    or table_id > 3
                ):
                    raise _invalid()
                component_ids.add(component_id)
                component_definitions[component_id] = _JpegComponent(
                    horizontal_sampling=sampling >> 4,
                    vertical_sampling=sampling & 0x0F,
                    quantization_table=table_id,
                )
                component_quantization_tables.add(table_id)
            saw_frame = True
            frame = _JpegFrame(
                marker=marker,
                width=width,
                height=height,
                components=component_definitions,
            )
            frame_components = component_ids
            frame_quantization_tables = component_quantization_tables
            if marker == 0xC2:
                total_blocks = 0
                for component_id in component_ids:
                    block_columns, block_rows = frame.component_block_shape(component_id)
                    block_count = block_columns * block_rows
                    total_blocks += block_count
                    coefficient_masks[component_id] = [0] * block_count
                if total_blocks * 64 > _MAX_DECODED_RASTER_BYTES:
                    raise _invalid()
        if marker != 0xDA:
            continue
        if len(segment) < 4:
            raise _invalid()
        components = segment[0]
        if (
            components == 0
            or len(segment) != 4 + (2 * components)
            or not saw_frame
            or frame is None
            or not frame_quantization_tables.issubset(quantization_tables)
        ):
            raise _invalid()
        spectral_start, spectral_end, approximation = segment[-3:]
        successive_high = approximation >> 4
        successive_low = approximation & 0x0F
        if (
            spectral_start > spectral_end
            or spectral_end > 63
            or successive_high > 13
            or successive_low > 13
        ):
            raise _invalid()
        scan_components: set[int] = set()
        scan_component_ids: list[int] = []
        table_selectors: dict[int, tuple[int, int]] = {}
        for offset in range(1, 1 + (2 * components), 2):
            component_id = segment[offset]
            table_selector = segment[offset + 1]
            dc_table = table_selector >> 4
            ac_table = table_selector & 0x0F
            if (
                component_id not in frame_components
                or component_id in scan_components
                or dc_table > 3
                or ac_table > 3
            ):
                raise _invalid()
            scan_components.add(component_id)
            scan_component_ids.append(component_id)
            table_selectors[component_id] = (dc_table, ac_table)

        if frame.marker == 0xC0:
            if (
                (spectral_start, spectral_end, successive_high, successive_low) != (0, 63, 0, 0)
                or scan_components & sequential_scanned_components
                or any(
                    (0, table_selectors[component_id][0]) not in huffman_tables
                    or (1, table_selectors[component_id][1]) not in huffman_tables
                    for component_id in scan_components
                )
            ):
                raise _invalid()
        else:
            if (
                (spectral_start == 0 and spectral_end != 0)
                or (spectral_start > 0 and components != 1)
                or (successive_high and successive_low + 1 != successive_high)
            ):
                raise _invalid()
            coefficient_range = range(spectral_start, spectral_end + 1)
            for component_id in scan_components:
                for coefficient in coefficient_range:
                    previous = successive_levels.get((component_id, coefficient))
                    if (successive_high == 0 and previous is not None) or (
                        successive_high > 0 and previous != successive_high
                    ):
                        raise _invalid()
            if spectral_start == 0 and successive_high == 0:
                if any(
                    (0, table_selectors[component_id][0]) not in huffman_tables
                    for component_id in scan_components
                ):
                    raise _invalid()
            elif spectral_start > 0 and any(
                (1, table_selectors[component_id][1]) not in huffman_tables
                for component_id in scan_components
            ):
                raise _invalid()
        saw_scan = True
        entropy_start = position
        while position < len(data):
            if data[position] != 0xFF:
                position += 1
                continue
            entropy_marker = position
            while position < len(data) and data[position] == 0xFF:
                position += 1
            if position >= len(data):
                raise _invalid()
            marker = data[position]
            if marker == 0x00 or 0xD0 <= marker <= 0xD7:
                position += 1
                continue
            position = entropy_marker
            break
        if position >= len(data) or position == entropy_start:
            raise _invalid()
        _validate_jpeg_scan_entropy(
            data[entropy_start:position],
            frame=frame,
            component_ids=tuple(scan_component_ids),
            table_selectors=table_selectors,
            huffman_tables=huffman_tables,
            spectral_start=spectral_start,
            spectral_end=spectral_end,
            successive_high=successive_high,
            successive_low=successive_low,
            restart_interval=restart_interval,
            coefficient_masks=coefficient_masks,
        )
        if frame.marker == 0xC0:
            sequential_scanned_components.update(scan_components)
        else:
            for component_id in scan_components:
                for coefficient in range(spectral_start, spectral_end + 1):
                    successive_levels[(component_id, coefficient)] = successive_low
    raise _invalid()


def _png_row_layout(
    width: int, height: int, bits_per_pixel: int, interlace: int
) -> list[tuple[int, int]]:
    if interlace == 0:
        return [(((width * bits_per_pixel + 7) // 8) + 1, height)]
    rows: list[tuple[int, int]] = []
    for x_start, y_start, x_step, y_step in (
        (0, 0, 8, 8),
        (4, 0, 8, 8),
        (0, 4, 4, 8),
        (2, 0, 4, 4),
        (0, 2, 2, 4),
        (1, 0, 2, 2),
        (0, 1, 1, 2),
    ):
        pass_width = (width - x_start + x_step - 1) // x_step if width > x_start else 0
        pass_height = (height - y_start + y_step - 1) // y_step if height > y_start else 0
        if pass_width:
            rows.append((((pass_width * bits_per_pixel + 7) // 8) + 1, pass_height))
    return rows


def _validate_png(data: bytes) -> None:
    if not data.startswith(_PNG_SIGNATURE):
        raise _invalid()
    position = len(_PNG_SIGNATURE)
    ihdr: tuple[int, int, int, int, int] | None = None
    palette = False
    saw_idat = False
    idat_ended = False
    idat = bytearray()
    while position < len(data):
        if position + 12 > len(data):
            raise _invalid()
        length = int.from_bytes(data[position : position + 4], "big")
        chunk_type = data[position + 4 : position + 8]
        chunk_end = position + 12 + length
        if (
            length > _MAX_DECODED_RASTER_BYTES
            or chunk_end > len(data)
            or len(chunk_type) != 4
            or any(not (65 <= value <= 90 or 97 <= value <= 122) for value in chunk_type)
        ):
            raise _invalid()
        payload = data[position + 8 : position + 8 + length]
        expected_crc = int.from_bytes(data[position + 8 + length : chunk_end], "big")
        if zlib.crc32(chunk_type + payload) & 0xFFFFFFFF != expected_crc:
            raise _invalid()
        position = chunk_end
        if ihdr is None and chunk_type != b"IHDR":
            raise _invalid()
        if chunk_type == b"IHDR":
            if ihdr is not None or length != 13:
                raise _invalid()
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            allowed_depths = {
                0: {1, 2, 4, 8, 16},
                2: {8, 16},
                3: {1, 2, 4, 8},
                4: {8, 16},
                6: {8, 16},
            }
            if (
                width == 0
                or height == 0
                or width > _MAX_RASTER_DIMENSION
                or height > _MAX_RASTER_DIMENSION
                or color_type not in allowed_depths
                or bit_depth not in allowed_depths[color_type]
                or compression != 0
                or filtering != 0
                or interlace not in {0, 1}
            ):
                raise _invalid()
            ihdr = (width, height, bit_depth, color_type, interlace)
        elif chunk_type == b"PLTE":
            if saw_idat or palette or length == 0 or length % 3 or length > 768:
                raise _invalid()
            palette = True
        elif chunk_type == b"IDAT":
            if idat_ended:
                raise _invalid()
            saw_idat = True
            idat.extend(payload)
            if len(idat) > _MAX_DECODED_RASTER_BYTES:
                raise _invalid()
        elif chunk_type == b"IEND":
            if length != 0 or not saw_idat or position != len(data):
                raise _invalid()
            break
        else:
            if chunk_type[0] & 0x20 == 0 or chunk_type in _PNG_CRITICAL_CHUNKS:
                raise _invalid()
            if saw_idat:
                idat_ended = True
    else:
        raise _invalid()
    if ihdr is None:
        raise _invalid()
    width, height, bit_depth, color_type, interlace = ihdr
    if (color_type == 3 and not palette) or (color_type in {0, 4} and palette):
        raise _invalid()
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    row_layout = _png_row_layout(width, height, bit_depth * channels, interlace)
    decoded_size = sum(row_size * row_count for row_size, row_count in row_layout)
    scanline_count = sum(row_count for _, row_count in row_layout)
    if decoded_size > _MAX_DECODED_RASTER_BYTES or scanline_count > _MAX_RASTER_SCANLINES:
        raise _invalid()
    try:
        inflater = zlib.decompressobj()
        decoded = inflater.decompress(bytes(idat), decoded_size + 1)
    except zlib.error as error:
        raise _invalid() from error
    if (
        len(decoded) != decoded_size
        or not inflater.eof
        or inflater.unused_data
        or inflater.unconsumed_tail
    ):
        raise _invalid()
    offset = 0
    for row_size, row_count in row_layout:
        for _ in range(row_count):
            if decoded[offset] > 4:
                raise _invalid()
            offset += row_size


def _gif_sub_blocks(data: bytes, position: int) -> tuple[bytes, int]:
    payload = bytearray()
    while True:
        if position >= len(data):
            raise _invalid()
        size = data[position]
        position += 1
        if size == 0:
            return bytes(payload), position
        if position + size > len(data):
            raise _invalid()
        payload.extend(data[position : position + size])
        if len(payload) > _MAX_DECODED_RASTER_BYTES:
            raise _invalid()
        position += size


def _validate_gif_lzw(payload: bytes, minimum_code_size: int, expected_pixels: int) -> None:
    if minimum_code_size < 2 or minimum_code_size > 8:
        raise _invalid()
    clear_code = 1 << minimum_code_size
    end_code = clear_code + 1

    def reset() -> tuple[dict[int, bytes], int, int]:
        return (
            {value: bytes((value,)) for value in range(clear_code)},
            minimum_code_size + 1,
            end_code + 1,
        )

    dictionary, code_size, next_code = reset()
    bit_position = 0
    previous: bytes | None = None
    output_size = 0
    saw_clear = False
    while bit_position + code_size <= len(payload) * 8:
        byte_position = bit_position // 8
        bit_offset = bit_position % 8
        available = int.from_bytes(payload[byte_position : byte_position + 3], "little")
        code = (available >> bit_offset) & ((1 << code_size) - 1)
        bit_position += code_size
        if code == clear_code:
            dictionary, code_size, next_code = reset()
            previous = None
            saw_clear = True
            continue
        if code == end_code:
            if not saw_clear or output_size != expected_pixels:
                raise _invalid()
            return
        if not saw_clear:
            raise _invalid()
        if code in dictionary:
            entry = dictionary[code]
        elif code == next_code and previous is not None:
            entry = previous + previous[:1]
        else:
            raise _invalid()
        output_size += len(entry)
        if output_size > expected_pixels:
            raise _invalid()
        if previous is not None and next_code < 4096:
            dictionary[next_code] = previous + entry[:1]
            next_code += 1
            if next_code == 1 << code_size and code_size < 12:
                code_size += 1
        previous = entry
    raise _invalid()


def _validate_gif(data: bytes) -> None:
    if len(data) < 14 or data[:6] not in {b"GIF87a", b"GIF89a"}:
        raise _invalid()
    width = int.from_bytes(data[6:8], "little")
    height = int.from_bytes(data[8:10], "little")
    packed = data[10]
    if width == 0 or height == 0:
        raise _invalid()
    position = 13
    global_palette = bool(packed & 0x80)
    if global_palette:
        position += 3 * (1 << ((packed & 0x07) + 1))
    if position > len(data):
        raise _invalid()
    saw_image = False
    while position < len(data):
        introducer = data[position]
        position += 1
        if introducer == 0x3B:
            if position != len(data) or not saw_image:
                raise _invalid()
            return
        if introducer == 0x21:
            if position >= len(data):
                raise _invalid()
            label = data[position]
            position += 1
            if label == 0xF9:
                if position + 6 > len(data) or data[position] != 4 or data[position + 5] != 0:
                    raise _invalid()
                position += 6
            elif label in {0x01, 0xFF}:
                expected_size = 12 if label == 0x01 else 11
                if position >= len(data) or data[position] != expected_size:
                    raise _invalid()
                position += 1 + expected_size
                if position > len(data):
                    raise _invalid()
                _, position = _gif_sub_blocks(data, position)
            elif label == 0xFE:
                _, position = _gif_sub_blocks(data, position)
            else:
                raise _invalid()
            continue
        if introducer != 0x2C or position + 9 > len(data):
            raise _invalid()
        left, top, image_width, image_height, image_packed = struct.unpack(
            "<HHHHB", data[position : position + 9]
        )
        position += 9
        if (
            image_width == 0
            or image_height == 0
            or left + image_width > width
            or top + image_height > height
        ):
            raise _invalid()
        local_palette = bool(image_packed & 0x80)
        if not global_palette and not local_palette:
            raise _invalid()
        if local_palette:
            position += 3 * (1 << ((image_packed & 0x07) + 1))
        if position >= len(data):
            raise _invalid()
        minimum_code_size = data[position]
        position += 1
        payload, position = _gif_sub_blocks(data, position)
        pixels = image_width * image_height
        if pixels > _MAX_DECODED_RASTER_BYTES:
            raise _invalid()
        _validate_gif_lzw(payload, minimum_code_size, pixels)
        saw_image = True
    raise _invalid()


def _normalize_css_value(value: str) -> str:
    without_comments: list[str] = []
    position = 0
    while position < len(value):
        if value.startswith("/*", position):
            end = value.find("*/", position + 2)
            if end < 0:
                raise MediaValidationError("unsafe_svg_external_reference")
            position = end + 2
            continue
        if value.startswith("*/", position):
            raise MediaValidationError("unsafe_svg_external_reference")
        without_comments.append(value[position])
        position += 1

    collapsed = "".join(without_comments)
    normalized: list[str] = []
    position = 0
    while position < len(collapsed):
        character = collapsed[position]
        if character != "\\":
            normalized.append(character)
            position += 1
            continue
        position += 1
        if position >= len(collapsed) or collapsed[position] in "\r\n\f":
            raise MediaValidationError("unsafe_svg_external_reference")
        if collapsed[position] in "0123456789abcdefABCDEF":
            start = position
            while (
                position < len(collapsed)
                and position - start < 6
                and collapsed[position] in "0123456789abcdefABCDEF"
            ):
                position += 1
            codepoint = int(collapsed[start:position], 16)
            if position < len(collapsed) and collapsed[position] in " \t\r\n\f":
                position += 1
            if codepoint == 0 or 0xD800 <= codepoint <= 0xDFFF or codepoint > 0x10FFFF:
                raise MediaValidationError("unsafe_svg_external_reference")
            normalized.append(chr(codepoint))
            continue
        normalized.append(collapsed[position])
        position += 1

    result = "".join(normalized).lower()
    # Do not reinterpret escapes or comments introduced by normalization. CSS
    # tokenization edge cases are rejected rather than guessed at.
    if "\\" in result or "/*" in result or "*/" in result:
        raise MediaValidationError("unsafe_svg_external_reference")
    return result


def _validate_css_value(value: str) -> None:
    lowered = _normalize_css_value(value)
    if "@import" in lowered or "@font-face" in lowered:
        raise MediaValidationError("unsafe_svg_external_reference")
    references = _CSS_URL.findall(lowered)
    if _CSS_URL_TOKEN.search(lowered) and not references:
        raise MediaValidationError("unsafe_svg_external_reference")
    for reference in references:
        normalized = reference.strip(" \t\r\n'\"")
        if not re.fullmatch(r"#[a-z_][a-z0-9_.:-]*", normalized, re.IGNORECASE):
            raise MediaValidationError("unsafe_svg_external_reference")
    if "expression" in lowered or "javascript:" in lowered:
        raise MediaValidationError("unsafe_svg_external_reference")


def _validate_svg(data: bytes) -> None:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise _invalid() from error
    if _SVG_DECLARATION_OR_ENTITY.search(text):
        raise MediaValidationError("unsafe_svg")
    without_declaration = _SVG_DECLARATION.sub("", text, count=1)
    processing_instruction = _SVG_PROCESSING_INSTRUCTION.search(without_declaration)
    if processing_instruction is not None:
        code = (
            "unsafe_svg_external_reference"
            if processing_instruction.group(1).lower() == "xml-stylesheet"
            else "unsafe_svg"
        )
        raise MediaValidationError(code)
    try:
        root = ElementTree.fromstring(text)
    except (ElementTree.ParseError, ValueError) as error:
        raise _invalid() from error
    if root.tag.rsplit("}", 1)[-1].lower() != "svg":
        raise _invalid()
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag == "style":
            raise MediaValidationError("unsafe_svg_external_reference")
        if tag in _SVG_ACTIVE_TAGS:
            raise MediaValidationError("unsafe_svg")
        for raw_name, value in element.attrib.items():
            name = raw_name.rsplit("}", 1)[-1].lower()
            normalized = value.strip()
            lowered = normalized.lower()
            if name.startswith("on"):
                raise MediaValidationError("unsafe_svg")
            if name == "base" and normalized:
                raise MediaValidationError("unsafe_svg_external_reference")
            if name in {"href", "src"} and normalized and not normalized.startswith("#"):
                raise MediaValidationError("unsafe_svg_external_reference")
            if (
                name == "style"
                or name in _SVG_PRESENTATION_ATTRIBUTES
                or _CSS_URL_TOKEN.search(lowered)
                or "@import" in lowered
            ):
                _validate_css_value(value)


def media_type(data: bytes, *, path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        _validate_jpeg(data)
        return "image/jpeg"
    if suffix == ".png":
        _validate_png(data)
        return "image/png"
    if suffix == ".gif":
        _validate_gif(data)
        return "image/gif"
    if suffix == ".svg":
        _validate_svg(data)
        return "image/svg+xml"
    raise MediaValidationError("unsupported_media_type")


def _media_validation_result(path: str, data: bytes) -> tuple[str, str, str]:
    try:
        content_type = media_type(data, path=path)
        if content_type not in _MEDIA_CONTENT_TYPES:
            return path, "", _MEDIA_WORKER_FAILURE
        return path, content_type, ""
    except MediaValidationError as error:
        code = error.code if error.code in _MEDIA_VALIDATION_CODES else _MEDIA_WORKER_FAILURE
        return path, "", code
    except Exception:
        # Validator defects must not expose arbitrary exception text through the
        # adapter or management command. The parent accepts only the fixed code.
        return path, "", _MEDIA_WORKER_FAILURE


def _validate_inherited_media(path: str) -> tuple[str, str, str]:
    payload = _PARALLEL_MEDIA_PAYLOAD
    if payload is None:  # pragma: no cover - a worker cannot start without its fork snapshot
        return path, "", _MEDIA_WORKER_FAILURE
    try:
        return _media_validation_result(path, payload[path])
    except BaseException:
        # A child-side SystemExit/KeyboardInterrupt or other non-Exception must
        # become a content-free result. Parent-process interrupts are not caught.
        return path, "", _MEDIA_WORKER_FAILURE


def _valid_media_worker_result(result: object, *, expected_path: str) -> bool:
    if not isinstance(result, tuple) or len(result) != 3:
        return False
    path, content_type, error_code = result
    if not all(isinstance(item, str) for item in result) or path != expected_path:
        return False
    if error_code:
        return content_type == "" and error_code in _MEDIA_BATCH_CODES
    return content_type in _MEDIA_CONTENT_TYPES


class _MediaWorkerBoundaryError(RuntimeError):
    """Internal content-free sentinel; it never crosses the batch boundary."""


def validate_media_batch(
    items: Sequence[tuple[str, bytes]],
) -> tuple[tuple[str, str, str], ...]:
    """Deep-validate media with deterministic ordering and bounded parallelism.

    Accepted snapshots contain hundreds of JPEGs whose entropy streams require
    CPU-bound validation. ``fork`` workers inherit the already-bounded, immutable
    source byte mapping, so validation can use several cores without
    serializing roughly 145 MiB through worker pipes or reading the checkout a
    second time. Small fixtures and daemon workers remain sequential.
    """

    ordered = tuple(sorted(items, key=lambda item: item[0]))
    available_cpus = os.process_cpu_count() or 1
    if (
        len(ordered) < _PARALLEL_MEDIA_MIN_ITEMS
        or available_cpus < 2
        or current_process().daemon
        or "fork" not in get_all_start_methods()
    ):
        return tuple(_media_validation_result(path, data) for path, data in ordered)

    paths = tuple(path for path, _data in ordered)
    payload = dict(ordered)
    del items, ordered
    workers = min(_PARALLEL_MEDIA_WORKERS, available_cpus, len(paths))
    global _PARALLEL_MEDIA_PAYLOAD
    with _PARALLEL_MEDIA_LOCK:
        _PARALLEL_MEDIA_PAYLOAD = payload
        executor: ProcessPoolExecutor | None = None
        failure_path: str | None = None
        current_path = paths[0]
        results: list[tuple[str, str, str]] = []
        completed_results: tuple[tuple[str, str, str], ...] = ()
        iterator: Iterator[tuple[str, str, str]] | None = None
        try:
            executor = ProcessPoolExecutor(
                max_workers=workers,
                mp_context=get_context("fork"),
            )
            iterator = iter(executor.map(_validate_inherited_media, paths, chunksize=1))
            for current_path in paths:
                try:
                    result = next(iterator)
                except StopIteration as error:
                    raise _MediaWorkerBoundaryError from error
                if not _valid_media_worker_result(result, expected_path=current_path):
                    raise _MediaWorkerBoundaryError
                results.append(result)

            current_path = paths[0]
            try:
                next(iterator)
            except StopIteration:
                pass
            else:
                raise _MediaWorkerBoundaryError
            completed_results = tuple(results)
        except Exception:
            failure_path = current_path
        finally:
            try:
                if executor is not None:
                    executor.shutdown(wait=True, cancel_futures=True)
            except Exception:
                failure_path = failure_path or paths[0]
            finally:
                # Parent interrupts must propagate, but never with the 145 MiB
                # invocation payload or executor/future references still live.
                _PARALLEL_MEDIA_PAYLOAD = None
                payload.clear()
                iterator = None
                executor = None
                results.clear()
        if failure_path is not None:
            return ((failure_path, "", _MEDIA_WORKER_FAILURE),)
        return completed_results
