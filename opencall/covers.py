"""Обложки 4:5 в фирменном стиле ОПЕНКОЛЛ: глянцевый 3D, хром и стекло, градиент синий-лавандовый."""
import base64
import io
import os

import requests
from PIL import Image

from lib import DATA

COVERS = DATA / "covers"

STYLE = (
    "Glossy 3D render of a single central object: {motif}. "
    "Materials: polished chrome and frosted glass with soft iridescent highlights. "
    "Background: smooth vertical gradient from cobalt blue (#1F4BFF) to pale lavender (#EEF3FF), "
    "a few thin glowing light trails curving behind the object. "
    "Soft studio lighting, gentle reflections, shallow depth of field, minimal premium composition, "
    "object centered slightly below the middle, calm empty space above. "
    "Absolutely no text, letters, numbers, logos, flags, people or faces."
)

DEFAULT_MOTIF = {
    "конкурс": "a chrome trophy cup with a glass star floating above it",
    "грант": "a stack of chrome coins with a glass seedling growing from the top coin",
    "резиденция": "a small glass house with a glowing window on a chrome platform",
    "тендер": "a chrome briefcase slightly open with a glass document hovering above",
    "стажировка": "a glass ladder with chrome rungs leading upward",
    "премия": "a chrome medal on a glass ribbon",
}


def cover_prompt(row, motif=""):
    motif = (motif or DEFAULT_MOTIF.get(row.get("type"), "a chrome letter-free envelope with a glass ticket coming out of it")).strip()
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
