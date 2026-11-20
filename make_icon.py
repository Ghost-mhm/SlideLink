# SlideLink · EN: Draws the SlideLink icons (PNG and ICO) without extra libraries.
# SlideLink · DE: Zeichnet die SlideLink-Symbole (PNG und ICO) ohne Zusatzbibliotheken.
"""Draws the SlideLink icon (no image libraries needed) and writes
static/icon.ico (16-256 px) plus static/icon-192.png and static/icon-512.png."""
import os
import struct
import zlib

ROOT = os.path.dirname(os.path.abspath(__file__))
BG, ACCENT, DARK = (20, 21, 28), (124, 140, 255), (20, 21, 28)


# EN: True if a point lies inside a rounded rectangle.
# DE: True, wenn ein Punkt in einem abgerundeten Rechteck liegt.
def rrect(x, y, x0, y0, x1, y1, r):
    """Is point (x, y) inside the rounded rectangle?"""
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


# EN: Mixes two colors.
# DE: Mischt zwei Farben.
def shade(u, v):
    """Colour of the icon at (u, v) in a 512x512 design space, or None for transparent."""
    if not rrect(u, v, 0, 0, 512, 512, 110):
        return None
    if rrect(u, v, 86, 126, 426, 336, 26):  # the slide
        if rrect(u, v, 126, 170, 296, 196, 13):
            return DARK
        if rrect(u, v, 126, 220, 366, 238, 9) or rrect(u, v, 126, 260, 326, 278, 9):
            return tuple(int(a * .4 + b * .6) for a, b in zip(ACCENT, DARK))
        return ACCENT
    if rrect(u, v, 245, 336, 267, 396, 11) or rrect(u, v, 185, 385, 327, 407, 11):  # the stand
        return ACCENT
    return BG


# EN: Renders the icon at a given size as RGBA pixels.
# DE: Rendert das Symbol in einer Größe als RGBA-Pixel.
def render(size, ss=4):
    rows = []
    step = 512 / (size * ss)
    for y in range(size):
        row = bytearray()
        for x in range(size):
            r = g = b = a = 0
            for sy in range(ss):
                for sx in range(ss):
                    c = shade((x * ss + sx + .5) * step, (y * ss + sy + .5) * step)
                    if c:
                        r += c[0]; g += c[1]; b += c[2]; a += 255
            n = ss * ss
            if a:
                cov = a / 255
                row += bytes((int(r / cov), int(g / cov), int(b / cov), a // n))
            else:
                row += b"\0\0\0\0"
        rows.append(bytes(row))
    return rows


# EN: Encodes RGBA pixels as a PNG file.
# DE: Kodiert RGBA-Pixel als PNG-Datei.
def png(rows, size):
    raw = b"".join(b"\0" + r for r in rows)

    # EN: One PNG chunk with CRC.
    # DE: Ein PNG-Chunk mit CRC.
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


# EN: Writes icon.svg, the PNG sizes and icon.ico.
# DE: Schreibt icon.svg, die PNG-Größen und icon.ico.
def main():
    static = os.path.join(ROOT, "static")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = {s: png(render(s, 4 if s <= 64 else 2), s) for s in sizes}
    # ICO container with PNG-compressed entries (supported since Windows Vista)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, blobs = b"", b""
    for s in sizes:
        data = images[s]
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    with open(os.path.join(static, "icon.ico"), "wb") as f:
        f.write(header + entries + blobs)
    for s in (192, 512):
        with open(os.path.join(static, f"icon-{s}.png"), "wb") as f:
            f.write(png(render(s, 1 if s == 512 else 2), s))
    print("icons written to", static)


if __name__ == "__main__":
    main()
