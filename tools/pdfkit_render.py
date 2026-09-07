"""Render one PDF page the way Apple's Preview draws it — through PDFKit.

**Why this exists.** PDF viewers disagree about annotations. Preview draws a
`/Text` sticky note with its own icon and ignores the note's appearance
stream; every viewer draws a `/Stamp` from its appearance. The only honest
way to know what the operator will see in Preview is to ask PDFKit, the
engine Preview is built on, and this asks it: the page is drawn with its
annotations into a bitmap exactly as Preview would draw it, and saved as PNG.
pdfplumber and pypdfium2 cannot stand in for this — pdfium has its own
annotation renderer, and the difference between the two is the whole reason
to look.

macOS only, and deliberately not a dependency of anything: PyObjC is a large
binary package for one verification. Run it from a scratch environment:

    uv venv /tmp/pdfkit-venv
    uv pip install --python /tmp/pdfkit-venv/bin/python3 pyobjc-framework-Quartz
    /tmp/pdfkit-venv/bin/python3 tools/pdfkit_render.py copy.pdf 9 page10.png 2

usage: pdfkit_render.py <pdf> <page-index> <out.png> [scale]
"""

from __future__ import annotations

import sys

from Foundation import NSURL
from Quartz import (
    CGBitmapContextCreate,
    CGBitmapContextCreateImage,
    CGColorSpaceCreateDeviceRGB,
    CGContextFillRect,
    CGContextScaleCTM,
    CGContextSetRGBFillColor,
    CGImageDestinationAddImage,
    CGImageDestinationCreateWithURL,
    CGImageDestinationFinalize,
    CGRectMake,
    PDFDocument,
    kCGImageAlphaPremultipliedLast,
    kPDFDisplayBoxMediaBox,
)


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2
    path, index, out = argv[1], int(argv[2]), argv[3]
    scale = float(argv[4]) if len(argv) > 4 else 2.0

    document = PDFDocument.alloc().initWithURL_(NSURL.fileURLWithPath_(path))
    if document is None:
        print(f"could not open {path}")
        return 1
    page = document.pageAtIndex_(index)
    box = page.boundsForBox_(kPDFDisplayBoxMediaBox)
    width, height = int(box.size.width * scale), int(box.size.height * scale)

    context = CGBitmapContextCreate(
        None, width, height, 8, width * 4, CGColorSpaceCreateDeviceRGB(),
        kCGImageAlphaPremultipliedLast,
    )
    CGContextSetRGBFillColor(context, 1, 1, 1, 1)
    CGContextFillRect(context, CGRectMake(0, 0, width, height))
    CGContextScaleCTM(context, scale, scale)
    page.drawWithBox_toContext_(kPDFDisplayBoxMediaBox, context)

    destination = CGImageDestinationCreateWithURL(
        NSURL.fileURLWithPath_(out), "public.png", 1, None
    )
    CGImageDestinationAddImage(destination, CGBitmapContextCreateImage(context), None)
    CGImageDestinationFinalize(destination)
    print(f"rendered page {index + 1} via PDFKit -> {out} ({width}x{height})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
