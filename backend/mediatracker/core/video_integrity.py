"""Light container checks without ffmpeg. Used only when filing a download."""

from __future__ import annotations

from pathlib import Path

_HEAD_READ = 64 * 1024
_TAIL_READ = 8 * 1024

_EBML_HEADER_ID = 0x1A45DFA3
_SEGMENT_ID = 0x18538067


def integrity_issue(path: Path) -> str | None:
    """Return a short French reason when the file looks truncated, else None."""
    suffix = path.suffix.casefold()
    if suffix == ".mkv":
        return _mkv_issue(path)
    if suffix == ".mp4":
        return _mp4_issue(path)
    return None


def _read_bounds(path: Path) -> tuple[int, bytes, bytes] | None:
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size <= 0:
        return None
    head_len = min(size, _HEAD_READ)
    tail_len = min(size, _TAIL_READ)
    try:
        with path.open("rb") as handle:
            head = handle.read(head_len)
            if tail_len and tail_len < size:
                handle.seek(size - tail_len)
                tail = handle.read(tail_len)
            else:
                tail = head
    except OSError:
        return None
    if len(head) < head_len:
        return None
    return size, head, tail


def _mkv_issue(path: Path) -> str | None:
    bounds = _read_bounds(path)
    if bounds is None:
        return "Fichier illisible."
    size, head, _tail = bounds
    if len(head) < 4:
        return None
    try:
        pos = 0
        element_id, pos = _read_element_id(head, pos)
        if element_id != _EBML_HEADER_ID:
            return None
        header_size, pos = _read_vint(head, pos)
        if header_size < 0:
            return None
        pos += header_size
        while pos < len(head):
            element_id, pos = _read_element_id(head, pos)
            body_size, pos = _read_vint(head, pos)
            if element_id == _SEGMENT_ID:
                if body_size < 0:
                    return None
                segment_end = pos + body_size
                if segment_end > size:
                    return "Segment MKV tronqué par rapport à la taille déclarée."
                return None
            if body_size < 0:
                return None
            pos += body_size
        return None
    except _ParseError:
        return None


def _mp4_issue(path: Path) -> str | None:
    try:
        size = path.stat().st_size
    except OSError:
        return "Fichier illisible."
    if size < 8:
        return None
    offset = 0
    parsed = False
    try:
        with path.open("rb") as handle:
            while offset < size:
                handle.seek(offset)
                header = handle.read(8)
                if len(header) < 8:
                    return None
                box_size = int.from_bytes(header[:4], "big")
                if box_size == 0:
                    return None
                if box_size == 1:
                    ext = handle.read(8)
                    if len(ext) < 8:
                        return None
                    box_size = int.from_bytes(ext, "big")
                    if box_size < 16:
                        return None
                elif box_size < 8:
                    return None
                parsed = True
                if offset + box_size > size:
                    return "Boîte MP4 tronquée."
                offset += box_size
        if parsed and offset != size:
            return "Structure MP4 incohérente."
        return None
    except OSError:
        return None


class _ParseError(Exception):
    pass


def _vint_length(first: int) -> int:
    if first >= 0x80:
        return 1
    if first >= 0x40:
        return 2
    if first >= 0x20:
        return 3
    if first >= 0x10:
        return 4
    if first >= 0x08:
        return 5
    if first >= 0x04:
        return 6
    if first >= 0x02:
        return 7
    return 8


def _read_element_id(buf: bytes, pos: int) -> tuple[int, int]:
    if pos >= len(buf):
        raise _ParseError
    length = _vint_length(buf[pos])
    end = pos + length
    if end > len(buf):
        raise _ParseError
    value = int.from_bytes(buf[pos:end], "big")
    return value, end


def _read_vint(buf: bytes, pos: int) -> tuple[int, int]:
    if pos >= len(buf):
        raise _ParseError
    first = buf[pos]
    if first == 0:
        raise _ParseError
    length = _vint_length(first)
    max_marker = (1 << (7 * length)) - 1
    mask = 1 << (8 - length)
    value = first & (mask - 1)
    for _ in range(1, length):
        pos += 1
        if pos >= len(buf):
            raise _ParseError
        value = (value << 8) | buf[pos]
    pos += 1
    if value == max_marker:
        return -1, pos
    return value, pos
