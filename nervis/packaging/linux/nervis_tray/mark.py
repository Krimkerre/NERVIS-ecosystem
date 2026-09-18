"""The NERVIS mark for the tray: a tilted square with a smaller one for its pupil.

The same proportions as the Mac app's `Mark` and `generate_icon.py`: the ring is 0.40 of the
icon's width from the centre, and the pupil's half-diagonal 0.32 of the ring's. **The pupil is
solid while the whole stack answers and faint otherwise**, and it goes out for the blink.

Written as SVG files named `…-symbolic`, in a folder handed to the indicator as its icon theme
path, which is how a panel recolours an icon to match itself: GNOME paints symbolic icons in its
panel's text colour, and KDE and XFCE draw the grey the file carries, which reads on light and
dark panels alike — the part a template image does on the Mac.
"""

from __future__ import annotations

from pathlib import Path

GREY = "#bebebe"  # the colour symbolic icons are drawn in, which panels replace with their own


def svg(whole: bool, pupil: bool = True, size: int = 22) -> str:
    centre = size / 2
    ring = size * 0.40
    inner = ring * 0.32

    def diamond(radius: float) -> str:
        return (f"M {centre:.2f} {centre - radius:.2f} L {centre + radius:.2f} {centre:.2f} "
                f"L {centre:.2f} {centre + radius:.2f} L {centre - radius:.2f} {centre:.2f} Z")

    parts = [f'<path d="{diamond(ring)}" fill="none" stroke="{GREY}" '
             f'stroke-width="{size * 0.09:.2f}" stroke-linejoin="round"/>']
    if pupil:
        opacity = "1" if whole else "0.35"
        parts.append(f'<path d="{diamond(inner)}" fill="{GREY}" fill-opacity="{opacity}"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
            f'viewBox="0 0 {size} {size}">{"".join(parts)}</svg>\n')


def write_icons(folder: Path) -> Path:
    """The three states, written where the indicator will look for them."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, whole, pupil in (("whole", True, True), ("partial", False, True),
                               ("blink", True, False)):
        (folder / f"nervis-tray-{name}-symbolic.svg").write_text(svg(whole, pupil))
        # The plain name too: some panels look for exactly the name they were given.
        (folder / f"nervis-tray-{name}.svg").write_text(svg(whole, pupil))
    return folder
