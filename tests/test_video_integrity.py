from pathlib import Path

from mediatracker.core.video_integrity import integrity_issue

_EBML_HEADER_ID = 0x1A45DFA3
_SEGMENT_ID = 0x18538067


def _ebml_element(element_id: int, body: bytes) -> bytes:
    if element_id == _EBML_HEADER_ID:
        id_bytes = bytes([0x1A, 0x45, 0xDF, 0xA3])
    elif element_id == _SEGMENT_ID:
        id_bytes = bytes([0x18, 0x53, 0x80, 0x67])
    else:
        raise ValueError(element_id)
    size = len(body)
    if size < 127:
        size_bytes = bytes([0x80 | size])
    else:
        size_bytes = bytes([0x40 | (size >> 8), size & 0xFF])
    return id_bytes + size_bytes + body


def _minimal_mkv() -> bytes:
    header_body = bytes(
        [
            0x42,
            0x86,
            0x81,
            0x01,
            0x42,
            0xF7,
            0x81,
            0x01,
            0x42,
            0xF2,
            0x81,
            0x04,
            0x42,
            0xF3,
            0x81,
            0x08,
            0x42,
            0x82,
            0x88,
            0x6D,
            0x61,
            0x74,
            0x72,
            0x6F,
            0x73,
            0x6B,
            0x61,
        ]
    )
    segment_body = b"\x00"
    return _ebml_element(_EBML_HEADER_ID, header_body) + _ebml_element(_SEGMENT_ID, segment_body)


def _minimal_mp4(payload: bytes = b"\x00") -> bytes:
    ftyp_body = b"isom" + b"\x00" * 4
    ftyp = len(ftyp_body) + 8
    mdat = len(payload) + 8
    return (
        ftyp.to_bytes(4, "big")
        + b"ftyp"
        + ftyp_body
        + mdat.to_bytes(4, "big")
        + b"mdat"
        + payload
    )


def test_mkv_complete_passes(tmp_path: Path) -> None:
    path = tmp_path / "Fable.Complete.mkv"
    path.write_bytes(_minimal_mkv())
    assert integrity_issue(path) is None


def test_mkv_padding_after_segment_is_not_suspect(tmp_path: Path) -> None:
    path = tmp_path / "Fable.Pad.mkv"
    path.write_bytes(_minimal_mkv() + b"PAD!")
    assert integrity_issue(path) is None


def test_mkv_truncated_is_suspect(tmp_path: Path) -> None:
    data = _minimal_mkv()
    path = tmp_path / "Fable.Trunc.mkv"
    path.write_bytes(data[:-1])
    assert integrity_issue(path) is not None


def test_mp4_complete_passes(tmp_path: Path) -> None:
    path = tmp_path / "Harbor.Complete.mp4"
    path.write_bytes(_minimal_mp4(b"frame"))
    assert integrity_issue(path) is None


def test_mp4_truncated_is_suspect(tmp_path: Path) -> None:
    data = _minimal_mp4(b"frame" * 20)
    path = tmp_path / "Harbor.Trunc.mp4"
    path.write_bytes(data[:-6])
    assert integrity_issue(path) is not None


def test_other_extension_ignored(tmp_path: Path) -> None:
    path = tmp_path / "Clip.avi"
    path.write_bytes(b"not-a-video")
    assert integrity_issue(path) is None
