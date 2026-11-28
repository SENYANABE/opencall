"""Render the editorial cover references using local Unbounded and Manrope font files."""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = (1080, 1350)
INK, BLUE, WHITE, PALE = "#0A1A44", "#1F4BFF", "#FFFFFF", "#EEF3FF"
BLACK, RULE = "#101318", "#B9C8F3"


def type_at(draw, point, text, file, size, color):
    font = ImageFont.truetype(str(file), size)
    draw.text(point, text, font=font, fill=color, anchor="lt")


def frame(background):
    image = Image.new("RGB", SIZE, background)
    return image, ImageDraw.Draw(image)


def render(directory, display, body):
    directory.mkdir(parents=True, exist_ok=True)

    image, draw = frame(BLACK)
    type_at(draw, (81, 86), "ОПЕНКОЛЛ", display, 52, WHITE)
    draw.line((82, 188, 998, 188), fill="#53596A", width=2)
    draw.rectangle((82, 270, 998, 1000), fill=BLUE)
    font = ImageFont.truetype(str(display), 390)
    draw.text((530, 655), "1.1", font=font, fill=WHITE, anchor="mm")
    type_at(draw, (82, 1102), "Обновление редакции", body, 58, WHITE)
    type_at(draw, (83, 1203), "30 ноября 2026", body, 36, "#CBD5FF")
    image.save(directory / "update-1.1.jpg", quality=94, optimize=True)

    image, draw = frame(PALE)
    type_at(draw, (82, 87), "ОПЕНКОЛЛ", display, 44, BLUE)
    draw.line((82, 185, 998, 185), fill=RULE, width=2)
    type_at(draw, (78, 293), "Дизайн-", display, 118, INK)
    type_at(draw, (78, 447), "конкурсы", display, 110, INK)
    type_at(draw, (82, 614), "от российских брендов", body, 63, INK)
    draw.rectangle((82, 845, 998, 1178), fill=BLUE)
    type_at(draw, (119, 891), "02", display, 142, WHITE)
    type_at(draw, (122, 1085), "конкурса", body, 47, WHITE)
    type_at(draw, (82, 1225), "Декабрь 2026", body, 34, INK)
    image.save(directory / "collection-1.1.jpg", quality=94, optimize=True)

    image, draw = frame(WHITE)
    type_at(draw, (82, 87), "ОПЕНКОЛЛ", display, 44, BLUE)
    draw.line((82, 185, 998, 185), fill=RULE, width=2)
    type_at(draw, (80, 288), "Дедлайны", display, 100, INK)
    type_at(draw, (83, 432), "ближайших двух недель", body, 55, INK)
    draw.rectangle((82, 618, 998, 1155), fill=PALE)
    type_at(draw, (116, 765), "04–18", display, 148, BLUE)
    type_at(draw, (124, 975), "декабря", body, 66, INK)
    image.save(directory / "deadlines-example.jpg", quality=94, optimize=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unbounded", type=Path, required=True)
    parser.add_argument("--manrope", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    render(args.output, args.unbounded, args.manrope)
