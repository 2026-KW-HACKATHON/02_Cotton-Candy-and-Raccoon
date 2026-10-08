"""Helpers shared from test_hwp_text.py."""

import struct
import zlib

from pipeline.attachments.download import DownloadedAttachment

__all__ = [
    "_hwp",
    "_record",
    "_text",
]


_FREE = 0xFFFFFFFF


_END = 0xFFFFFFFE


def _compound(streams: dict[str, bytes]) -> bytes:
    """Build a small CFB v3 fixture with FAT, directory, MiniFAT and data sectors.

    This fixture builder implements the container layout, not HWP text parsing.
    Thus tests go through the real olefile parser instead of mocking it away.
    """
    entries = [("Root Entry", 5, b"")]
    identifiers: dict[str, int] = {"": 0}
    children: dict[int, list[int]] = {}
    for path, data in streams.items():
        segments = path.split("/")
        for length in range(1, len(segments) + 1):
            prefix = "/".join(segments[:length])
            if prefix in identifiers:
                continue
            parent = identifiers["/".join(segments[: length - 1])]
            identifier = len(entries)
            identifiers[prefix] = identifier
            is_stream = length == len(segments)
            entries.append(
                (segments[length - 1], 2 if is_stream else 1, data if is_stream else b"")
            )
            children.setdefault(parent, []).append(identifier)

    mini_data = bytearray()
    mini_fat: list[int] = []
    starts: dict[int, int] = {}
    for identifier, (_, kind, data) in enumerate(entries):
        if kind == 2 and len(data) < 4096:
            starts[identifier] = len(mini_fat) if data else _END
            count = (len(data) + 63) // 64
            for index in range(count):
                mini_fat.append(len(mini_fat) + 1 if index < count - 1 else _END)
            mini_data.extend(data.ljust(count * 64, b"\0"))

    sectors = [bytes(512)]  # sector 0 is the FAT
    chains: list[list[int]] = []

    def allocate(data: bytes) -> int:
        if not data:
            return _END
        start = len(sectors)
        padded = data.ljust(((len(data) + 511) // 512) * 512, b"\0")
        sectors.extend(padded[index : index + 512] for index in range(0, len(padded), 512))
        chains.append(list(range(start, len(sectors))))
        return start

    directory_start = allocate(bytes(((len(entries) + 3) // 4) * 512))
    mini_fat_count = (len(mini_fat) + 127) // 128
    mini_fat_start = allocate(
        struct.pack(
            "<" + "I" * (mini_fat_count * 128),
            *(mini_fat + [_FREE] * (mini_fat_count * 128 - len(mini_fat))),
        )
    )
    root_start = allocate(bytes(mini_data))
    for identifier, (_, kind, data) in enumerate(entries):
        if kind == 2 and len(data) >= 4096:
            starts[identifier] = allocate(data)

    left: dict[int, int] = {}
    right: dict[int, int] = {}

    def tree(nodes: list[int]) -> int:
        if not nodes:
            return _FREE
        middle = len(nodes) // 2
        identifier = nodes[middle]
        left[identifier] = tree(nodes[:middle])
        right[identifier] = tree(nodes[middle + 1 :])
        return identifier

    child_roots = {
        parent: tree(sorted(nodes, key=lambda i: (len(entries[i][0]), entries[i][0].upper())))
        for parent, nodes in children.items()
    }
    directory = bytearray()
    for identifier, (name, kind, data) in enumerate(entries):
        entry = bytearray(128)
        encoded = (name + "\0").encode("utf-16le")
        entry[: len(encoded)] = encoded
        struct.pack_into(
            "<HBBIII",
            entry,
            64,
            len(encoded),
            kind,
            1,
            left.get(identifier, _FREE),
            right.get(identifier, _FREE),
            child_roots.get(identifier, _FREE),
        )
        start = root_start if identifier == 0 else starts.get(identifier, _END)
        size = len(mini_data) if identifier == 0 else len(data)
        struct.pack_into("<IQ", entry, 116, start, size)
        directory.extend(entry)
    directory = directory.ljust(((len(entries) + 3) // 4) * 512, b"\0")
    for index in range(len(directory) // 512):
        sectors[directory_start + index] = bytes(directory[index * 512 : (index + 1) * 512])
    assert len(sectors) <= 128, "fixture is limited to one FAT sector"
    fat = [_FREE] * 128
    fat[0] = 0xFFFFFFFD
    for chain in chains:
        for index, identifier in enumerate(chain):
            fat[identifier] = chain[index + 1] if index + 1 < len(chain) else _END
    sectors[0] = struct.pack("<128I", *fat)

    header = bytearray(512)
    header[:8] = bytes.fromhex("D0 CF 11 E0 A1 B1 1A E1")
    struct.pack_into("<HHHHH", header, 24, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into(
        "<IIIIIIIII",
        header,
        40,
        0,
        1,
        directory_start,
        0,
        4096,
        mini_fat_start,
        mini_fat_count,
        _END,
        0,
    )
    struct.pack_into("<109I", header, 76, 0, *([_FREE] * 108))
    return bytes(header) + b"".join(sectors)


def _record(tag: int, data: bytes, *, level: int = 0) -> bytes:
    size = len(data)
    return (
        struct.pack("<I", tag | (level << 10) | (min(size, 0xFFF) << 20))
        + (struct.pack("<I", size) if size >= 0xFFF else b"")
        + data
    )


def _text(value: str, *, level: int = 1) -> bytes:
    return _record(67, value.encode("utf-16le"), level=level)


def _deflate(data: bytes, *, trailer: bool = False) -> bytes:
    encoder = zlib.compressobj(wbits=-15)
    compressed = encoder.compress(data) + encoder.flush()
    return compressed + (struct.pack("<II", zlib.crc32(data), len(data)) if trailer else b"")


def _hwp(
    sections: list[bytes],
    *,
    flags: int = 1,
    version: int = 0x05010001,
    declared_count: int | None = None,
    trailer: bool = False,
    overrides: dict[str, bytes | None] | None = None,
) -> DownloadedAttachment:
    header = bytearray(256)
    header[:17] = b"HWP Document File"
    struct.pack_into("<II", header, 32, version, flags)
    count = len(sections) if declared_count is None else declared_count
    doc_info = _record(16, struct.pack("<H", count) + bytes(28))
    encode = (lambda data: _deflate(data, trailer=trailer)) if flags & 1 else bytes
    streams = {"FileHeader": bytes(header), "DocInfo": encode(doc_info)}
    streams.update(
        {f"BodyText/Section{index}": encode(data) for index, data in enumerate(sections)}
    )
    for path, data in (overrides or {}).items():
        if data is None:
            streams.pop(path, None)
        else:
            streams[path] = data
    return DownloadedAttachment("안내.hwp", "application/x-hwp", _compound(streams))
