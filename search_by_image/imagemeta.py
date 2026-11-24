"""Remove personal metadata from an image before it leaves the server.

Phone photos carry EXIF data (GPS position, camera serial, capture time) that
search services have no use for. Only the data needed to show the picture
correctly is kept: JPEG colour profiles and the Adobe colour-transform marker,
and the EXIF orientation flag, rewritten as a minimal EXIF block. Without the
flag a phone photo stored sideways would be searched sideways.

Images are not decoded or re-encoded, so no imaging library is needed and the
pixels are untouched.
"""

from __future__ import annotations

import struct

JPEG_MIME = "image/jpeg"
PNG_MIME = "image/png"
WEBP_MIME = "image/webp"
GIF_MIME = "image/gif"

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Text, EXIF and timestamp chunks; everything needed to draw the image stays.
_PNG_DROP = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}
_WEBP_DROP = {b"EXIF", b"XMP "}
_WEBP_FLAG_EXIF = 0x08
_WEBP_FLAG_XMP = 0x04


class UnsupportedImage(ValueError):
    """The upload is not an image this module understands, or it is damaged."""


def sniff(data: bytes) -> str:
    """Return the MIME type of ``data`` judged by its first bytes."""
    if data[:3] == b"\xff\xd8\xff":
        return JPEG_MIME
    if data[:8] == _PNG_SIGNATURE:
        return PNG_MIME
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return WEBP_MIME
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return GIF_MIME
    raise UnsupportedImage("not a JPEG, PNG, WebP or GIF image")


def clean(data: bytes) -> tuple[bytes, str]:
    """Return ``(cleaned_bytes, mime_type)``.

    GIF has no standard place for location or camera data, so it passes
    through unchanged. Raises :class:`UnsupportedImage` for other formats and
    for files whose structure is damaged: sending a file that could not be
    cleaned would defeat the purpose.
    """
    mime = sniff(data)
    try:
        if mime == JPEG_MIME:
            return _clean_jpeg(data), mime
        if mime == PNG_MIME:
            return _clean_png(data), mime
        if mime == WEBP_MIME:
            return _clean_webp(data), mime
    except (IndexError, struct.error) as exc:
        raise UnsupportedImage("the image file is damaged") from exc
    return data, mime


def _clean_jpeg(data: bytes) -> bytes:
    out = [b"\xff\xd8"]
    orientation = 1
    after_app0 = None  # where the minimal EXIF block goes
    pos = 2
    while True:
        if data[pos] != 0xFF:
            raise UnsupportedImage("the image file is damaged")
        while data[pos] == 0xFF:  # fill bytes may precede a marker
            pos += 1
        marker = data[pos]
        pos += 1
        if marker == 0xD9:  # end of image without scan data
            out.append(b"\xff\xd9")
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:  # markers without a length
            out.append(bytes((0xFF, marker)))
            continue
        (length,) = struct.unpack(">H", data[pos : pos + 2])
        if length < 2 or pos + length > len(data):
            raise UnsupportedImage("the image file is damaged")
        segment = data[pos - 2 : pos + length]
        payload = data[pos + 2 : pos + length]
        pos += length
        if marker == 0xDA:
            # Start of scan: the compressed picture and everything after it
            # stay exactly as they are.
            out.append(segment)
            out.append(data[pos:])
            break
        if marker == 0xE1 and payload.startswith(b"Exif\x00\x00"):
            orientation = _exif_orientation(payload[6:])
            continue
        if marker == 0xE0 or (marker == 0xE2 and payload.startswith(b"ICC_PROFILE\x00")) or marker == 0xEE:
            # JFIF header, colour profile, Adobe colour transform
            out.append(segment)
            if marker == 0xE0:
                after_app0 = len(out)
            continue
        if 0xE0 <= marker <= 0xEF or marker == 0xFE:
            continue  # other APPn blocks (XMP, IPTC, maker data) and comments
        out.append(segment)

    if orientation != 1:
        out.insert(after_app0 or 1, _minimal_exif(orientation))
    return b"".join(out)


def _exif_orientation(tiff: bytes) -> int:
    """Read the orientation tag (0x0112) from IFD0 of an EXIF TIFF block."""
    if tiff[:2] == b"II":
        endian = "<"
    elif tiff[:2] == b"MM":
        endian = ">"
    else:
        return 1
    (ifd_offset,) = struct.unpack(endian + "I", tiff[4:8])
    (count,) = struct.unpack(endian + "H", tiff[ifd_offset : ifd_offset + 2])
    for i in range(count):
        entry = ifd_offset + 2 + 12 * i
        tag, typ = struct.unpack(endian + "HH", tiff[entry : entry + 4])
        if tag == 0x0112 and typ == 3:  # SHORT
            (value,) = struct.unpack(endian + "H", tiff[entry + 8 : entry + 10])
            return value if 1 <= value <= 8 else 1
    return 1


def _minimal_exif(orientation: int) -> bytes:
    """An APP1 EXIF segment holding only the orientation tag."""
    tiff = (
        b"MM\x00\x2a"  # big-endian TIFF header
        + struct.pack(">I", 8)  # IFD0 right after the header
        + struct.pack(">H", 1)  # one entry
        + struct.pack(">HHIHH", 0x0112, 3, 1, orientation, 0)
        + struct.pack(">I", 0)  # no further IFD
    )
    payload = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def _clean_png(data: bytes) -> bytes:
    out = [_PNG_SIGNATURE]
    pos = len(_PNG_SIGNATURE)
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        end = pos + 12 + length
        if end > len(data):
            raise UnsupportedImage("the image file is damaged")
        if kind not in _PNG_DROP:
            out.append(data[pos:end])
        pos = end
        if kind == b"IEND":
            break
    return b"".join(out)


def _clean_webp(data: bytes) -> bytes:
    chunks = []
    pos = 12
    while pos < len(data):
        kind = data[pos : pos + 4]
        (size,) = struct.unpack("<I", data[pos + 4 : pos + 8])
        end = pos + 8 + size + (size & 1)  # chunks are padded to even sizes
        if pos + 8 + size > len(data):
            raise UnsupportedImage("the image file is damaged")
        chunk = data[pos:end]
        if kind == b"VP8X":
            flags = chunk[8] & ~(_WEBP_FLAG_EXIF | _WEBP_FLAG_XMP)
            chunk = chunk[:8] + bytes((flags,)) + chunk[9:]
        if kind not in _WEBP_DROP:
            chunks.append(chunk)
        pos = end
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body
