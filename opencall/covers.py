"""Обложки 4:5 в фирменном стиле ОПЕНКОЛЛ (ДЗ2).
Модель получает референсы из style/refs и рисует новый объект в том же стиле."""
import base64
import io
import os

import requests
from PIL import Image

from lib import DATA, ROOT

COVERS = DATA / "covers"
REFS_DIR = ROOT / "style" / "refs"
# Разные по композиции референсы: объект на постаменте, предметы в полёте, рост вверх
REF_FILES = ["5.jpg", "6-1.jpg", "2.jpg"]

STYLE = (
    "Vertical 4:5 glossy 3D render, premium tech advertising aesthetic. "
    "Hero: {motif}. "
    "The hero is made of crystal-clear luminous glass with polished chrome caps and rims "
    "and glowing bands of bright blue pixel mosaic. "
    "Saturated electric-blue sky gradient background fading to a soft lilac-pink haze at the bottom, "
    "a soft beam of light from above. "
    "Iridescent lilac and pink pearl spheres and small clear glass cubes float around, "
    "large out-of-focus pearls in the foreground bokeh, thin glowing white light trails orbit the hero, "
    "tiny sparkles and lens glints. "
    "Bright, airy, high-key studio lighting, strong specular highlights, shallow depth of field. "
    "The hero is large and fills the lower two thirds of the frame, slight dynamic angle, the top quarter stays calm and open. "
    "No text, no letters, no numbers, no logos, no watermarks, no people, no faces, no flags."
)

REF_INSTRUCTION = (
    "The attached images are STYLE REFERENCES ONLY. Create one NEW image with a different subject. "
    "Copy their style exactly: materials, colors, lighting, sparkles, pearls, glass cubes, light trails, depth of field, mood. "
    "Do not copy their objects or compositions. "
)

DEFAULT_MOTIF = {
    "конкурс": "an abstract twisted glass award statuette on a round chrome pedestal",
    "грант": "a tower of chrome coins with a frosted glass seedling sprouting from the top",
    "резиденция": "a small glass house with a warm glowing window on a chrome platform",
    "тендер": "a glass briefcase with chrome corners and a glowing document rising from it",
    "стажировка": "a glass staircase with chrome steps rising upward",
    "премия": "a glass medallion with a chrome rim on a glowing ribbon",
}


def cover_prompt(row, motif=""):
    motif = (motif or DEFAULT_MOTIF.get(row.get("type"), "a glass envelope with a glowing chrome ticket sliding out")).strip()
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


def _refs():
    return [REFS_DIR / f for f in REF_FILES if (REFS_DIR / f).exists()]


def _request(model, prompt, refs):
    headers = {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]}
    quality = os.environ.get("OPENAI_IMAGE_QUALITY") or "medium"
    if refs:
        files = [("image[]", (p.name, p.read_bytes(), "image/jpeg")) for p in refs]
        data = {"model": model, "prompt": REF_INSTRUCTION + prompt, "size": "1024x1536", "quality": quality, "n": "1"}
        return requests.post("https://api.openai.com/v1/images/edits", data=data, files=files, headers=headers, timeout=300)
    body = {"model": model, "prompt": prompt, "size": "1024x1536", "quality": quality, "n": 1}
    return requests.post("https://api.openai.com/v1/images/generations", json=body, headers=headers, timeout=300)


def generate_cover(row, motif=""):
    """Возвращает (относительный путь к файлу | "", ошибка). Сначала по референсам, при сбое без них."""
    model = os.environ.get("OPENAI_IMAGE_MODEL") or "gpt-image-1-mini"
    prompt = cover_prompt(row, motif)
    errors = []
    attempts = [_refs(), []] if _refs() else [[]]
    for refs in attempts:
        try:
            r = _request(model, prompt, refs)
            data = r.json()
            if r.status_code != 200:
                errors.append(str(data.get("error", {}).get("message", r.status_code))[:150])
                continue
            item = data["data"][0]
            raw = base64.b64decode(item["b64_json"]) if item.get("b64_json") else requests.get(item["url"], timeout=60).content
            COVERS.mkdir(parents=True, exist_ok=True)
            path = COVERS / f"{row['id']}.jpg"
            path.write_bytes(crop_4x5(raw))
            print("обложка", row["id"], "по референсам" if refs else "без референсов", "; ".join(errors))
            return str(path.relative_to(DATA.parent)), ""
        except (requests.RequestException, ValueError, KeyError, OSError) as e:
            errors.append(type(e).__name__)
    return "", "обложка: " + "; ".join(errors)
