"""Обложки 4:5 в фирменном стиле ОПЕНКОЛЛ: глянцевый 3D, хром и стекло, градиент синий-лавандовый."""
import base64
import io
import os

import requests
from PIL import Image

from lib import DATA

COVERS = DATA / "covers"

# Стиль из ДЗ2: глянцевый 3D для премиальной техно-рекламы
STYLE = (
    "Glossy 3D render, premium tech advertising aesthetic, vertical 4:5. "
    "Hero: {motif}, made of polished chrome, frosted translucent glass and accents of glowing blue pixel mosaic. "
    "Iridescent lilac pearl spheres and small clear glass cubes and prisms float around the hero, "
    "thin glowing light trails orbit it. "
    "Soft sky-blue to cobalt gradient background with lavender-pink haze at the bottom. "
    "Bright studio lighting, specular highlights, lens glints, shallow depth of field, bokeh in foreground and background. "
    "Hero in the lower half of the frame, clean empty upper half. "
    "No text, no letters, no numbers, no logos, no watermarks, no people, no faces, no flags."
)

DEFAULT_MOTIF = {
    "конкурс": "an abstract translucent glass award statuette on a round chrome pedestal",
    "грант": "a tower of chrome coins with a thick embossed abstract ring on each face and a frosted glass seedling on top",
    "резиденция": "a small frosted glass house with a warm glowing window standing on a chrome disc",
    "тендер": "a tower of chrome coins next to a frosted glass folder",
    "стажировка": "a frosted glass staircase with chrome steps rising upward",
    "премия": "a chrome medallion on a frosted glass ribbon",
}


def cover_prompt(row, motif=""):
    motif = (motif or DEFAULT_MOTIF.get(row.get("type"), "a frosted glass envelope with a chrome ticket sliding out of it")).strip()
    return STYLE.format(motif=motif)


def crop_4x5(raw_bytes):
    im = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    w, h = im.size
    target_h = int(w * 5 / 4)
    if h > target_h:
        top = (h - target_h) // 2
        im = im.crop((0, top, w, top + target_h))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=86, optimize=True)
    return out.getvalue()


def generate_cover(row, motif=""):
    """Возвращает (относительный путь к файлу | "", ошибка)."""
    model = os.environ.get("OPENAI_IMAGE_MODEL") or "gpt-image-1-mini"
    body = {"model": model, "prompt": cover_prompt(row, motif), "size": "1024x1536", "n": 1}
    if model.startswith("gpt-image"):
        body["quality"] = os.environ.get("OPENAI_IMAGE_QUALITY") or "medium"
    try:
        r = requests.post("https://api.openai.com/v1/images/generations", json=body, timeout=240,
                          headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]})
        data = r.json()
        if r.status_code != 200:
            return "", "обложка: " + str(data.get("error", {}).get("message", r.status_code))[:200]
        item = data["data"][0]
        raw = base64.b64decode(item["b64_json"]) if item.get("b64_json") else requests.get(item["url"], timeout=60).content
        COVERS.mkdir(parents=True, exist_ok=True)
        path = COVERS / f"{row['id']}.jpg"
        path.write_bytes(crop_4x5(raw))
        return str(path.relative_to(DATA.parent)), ""
    except (requests.RequestException, ValueError, KeyError, OSError) as e:
        return "", "обложка: " + type(e).__name__
