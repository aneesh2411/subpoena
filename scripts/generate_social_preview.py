"""Render the Pages social card after updating the landing metrics.

Install Pillow==12.2.0, then run python scripts/generate_social_preview.py.
The renderer uses Pillow's bundled Aileron font and adds no time-dependent data.
"""

import re
from html import unescape
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DOCS = Path(__file__).resolve().parents[1] / "docs"
WIDTH, HEIGHT = 1200, 630
PAPER = "#f6f1e4"
INK = "#1d1b16"
DESCRIPTION = "Independent outcome auditing for AI voice agents"


def landing_stats() -> list[tuple[str, str]]:
    html = (DOCS / "index.html").read_text(encoding="utf-8")
    block = re.search(
        r"<!-- metrics:landing-stats:start -->(.*?)<!-- metrics:landing-stats:end -->",
        html,
        re.DOTALL,
    )
    if block is None:
        raise ValueError("Missing landing stats block in docs/index.html")
    rows = re.findall(r'<div class="stat"><b>([^<]+)</b><span>([^<]+)</span></div>', block[1])
    if len(rows) != 6:
        raise ValueError(f"Expected six landing stats in docs/index.html, found {len(rows)}")
    return [(unescape(value), unescape(label)) for value, label in rows]


def main() -> None:
    stats = landing_stats()
    image = Image.new("RGB", (WIDTH, HEIGHT), "#14161a")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((36, 36, 1163, 593), radius=20, fill=PAPER)
    draw.rectangle((72, 75, 130, 82), fill="#b73333")
    draw.text(
        (72, 98),
        "SUBPOENA",
        font=ImageFont.load_default(size=102),
        fill=INK,
        stroke_width=1,
    )
    draw.text((76, 216), DESCRIPTION, font=ImageFont.load_default(size=32), fill=INK)
    draw.line((76, 275, 1124, 275), fill="#c4bdae", width=2)

    value_font = ImageFont.load_default(size=52)
    label_font = ImageFont.load_default(size=18)
    for index, (value, label) in enumerate(stats):
        row, column = divmod(index, 3)
        x = 76 + column * 354
        y = 302 + row * 136
        draw.rounded_rectangle((x, y, x + 340, y + 118), radius=12, fill="#eae4d7")
        draw.text((x + 16, y + 9), value, font=value_font, fill="#2457a4")
        draw.text((x + 16, y + 81), label, font=label_font, fill=INK)

    image.save(DOCS / "social-preview.png", format="PNG", compress_level=9)


if __name__ == "__main__":
    main()
