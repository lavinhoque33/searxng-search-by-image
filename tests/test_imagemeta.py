"""Tests for search_by_image.imagemeta. Run: python3 -m unittest discover tests"""

import struct
import unittest
import zlib

from search_by_image import imagemeta


def segment(marker: int, payload: bytes) -> bytes:
    return bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2) + payload


def exif_payload(orientation: int, endian: str = "<") -> bytes:
    """EXIF block with orientation, a camera model and a GPS latitude."""
    order = b"II" if endian == "<" else b"MM"
    p = lambda fmt, *v: struct.pack(endian + fmt, *v)  # noqa: E731
    # Layout: header (8) | IFD0 (42) | model at 50 (10) | GPS IFD at 60 (18) | latitude at 78 (24)
    ifd0 = p("H", 3)
    ifd0 += p("HHI", 0x0112, 3, 1) + p("HH", orientation, 0)
    ifd0 += p("HHII", 0x0110, 2, 10, 50)
    ifd0 += p("HHII", 0x8825, 4, 1, 60)
    ifd0 += p("I", 0)
    gps = p("H", 1) + p("HHII", 0x0002, 5, 3, 78) + p("I", 0)
    latitude = p("IIIIII", 48, 1, 51, 1, 30, 1)
    body = order + p("H", 42) + p("I", 8) + ifd0 + b"SecretCam\x00" + gps + latitude
    assert len(body) == 102
    return b"Exif\x00\x00" + body


def jpeg_segments(data: bytes) -> list[tuple[int, bytes]]:
    """(marker, payload) pairs up to and including start of scan."""
    out, pos = [], 2
    while True:
        marker = data[pos + 1]
        (length,) = struct.unpack(">H", data[pos + 2 : pos + 4])
        out.append((marker, data[pos + 4 : pos + 2 + length]))
        pos += 2 + length
        if marker == 0xDA:
            return out


SCAN = segment(0xDA, b"\x01\x01\x00\x00\x3f\x00") + b"\x12\xff\x00\x34\xff\xd0\x56" + b"\xff\xd9"


def sample_jpeg(orientation: int, endian: str = "<") -> bytes:
    return (
        b"\xff\xd8"
        + segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
        + segment(0xE1, exif_payload(orientation, endian))
        + segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta>secret</x:xmpmeta>")
        + segment(0xE2, b"ICC_PROFILE\x00\x01\x01profile-bytes")
        + segment(0xED, b"Photoshop 3.0\x00secret IPTC")
        + segment(0xFE, b"secret comment")
        + segment(0xDB, b"\x00" + bytes(64))
        + SCAN
    )


class JpegTest(unittest.TestCase):
    def test_personal_metadata_removed_orientation_kept(self):
        cleaned, mime = imagemeta.clean(sample_jpeg(orientation=6))
        self.assertEqual(mime, "image/jpeg")
        self.assertNotIn(b"Secret", cleaned)
        self.assertNotIn(b"secret", cleaned)
        markers = [m for m, _ in jpeg_segments(cleaned)]
        self.assertEqual(markers, [0xE0, 0xE1, 0xE2, 0xDB, 0xDA])
        exif = dict(jpeg_segments(cleaned))[0xE1]
        self.assertEqual(imagemeta._exif_orientation(exif[6:]), 6)
        self.assertNotIn(struct.pack(">H", 0x8825), exif)  # no GPS pointer

    def test_upright_photo_gets_no_exif_block(self):
        cleaned, _ = imagemeta.clean(sample_jpeg(orientation=1, endian=">"))
        self.assertNotIn(0xE1, [m for m, _ in jpeg_segments(cleaned)])

    def test_compressed_data_untouched(self):
        cleaned, _ = imagemeta.clean(sample_jpeg(orientation=8))
        self.assertTrue(cleaned.endswith(SCAN))

    def test_truncated_file_rejected(self):
        data = sample_jpeg(orientation=6)
        with self.assertRaises(imagemeta.UnsupportedImage):
            imagemeta.clean(data[:40])


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


class PngTest(unittest.TestCase):
    def test_text_chunks_removed(self):
        ihdr = png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
        idat = png_chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        data = (
            b"\x89PNG\r\n\x1a\n"
            + ihdr
            + png_chunk(b"tEXt", b"Comment\x00secret")
            + png_chunk(b"eXIf", b"MM\x00\x2asecret")
            + idat
            + png_chunk(b"IEND", b"")
        )
        cleaned, mime = imagemeta.clean(data)
        self.assertEqual(mime, "image/png")
        self.assertEqual(cleaned, b"\x89PNG\r\n\x1a\n" + ihdr + idat + png_chunk(b"IEND", b""))


def riff_chunk(kind: bytes, data: bytes) -> bytes:
    return kind + struct.pack("<I", len(data)) + data + (b"\x00" if len(data) & 1 else b"")


class WebpTest(unittest.TestCase):
    def test_exif_and_xmp_removed_and_flags_cleared(self):
        vp8x = bytes((0x08 | 0x04 | 0x10,)) + bytes(9)  # EXIF, XMP and alpha flags
        image = riff_chunk(b"VP8L", b"\x2f\x00\x00\x00\x00")
        body = b"WEBP" + riff_chunk(b"VP8X", vp8x) + image + riff_chunk(b"EXIF", b"secret") + riff_chunk(b"XMP ", b"secret")
        data = b"RIFF" + struct.pack("<I", len(body)) + body
        cleaned, mime = imagemeta.clean(data)
        self.assertEqual(mime, "image/webp")
        self.assertNotIn(b"secret", cleaned)
        self.assertEqual(struct.unpack("<I", cleaned[4:8])[0], len(cleaned) - 8)
        self.assertEqual(cleaned[20], 0x10)  # only the alpha flag is left
        self.assertTrue(cleaned.endswith(image))


class FormatTest(unittest.TestCase):
    def test_heic_rejected(self):
        with self.assertRaises(imagemeta.UnsupportedImage):
            imagemeta.clean(b"\x00\x00\x00\x18ftypheic" + bytes(32))

    def test_gif_passes_through(self):
        data = b"GIF89a" + bytes(20)
        self.assertEqual(imagemeta.clean(data), (data, "image/gif"))


if __name__ == "__main__":
    unittest.main()
