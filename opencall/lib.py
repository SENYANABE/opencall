"""Общие функции ОПЕНКОЛЛ: HTML, даты, OpenAI, Телеграм, CSV-лог."""
import csv
import html
import json
import os
import re
import time
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MSK = timezone(timedelta(hours=3))

TYPES = ["конкурс", "грант", "резиденция", "тендер", "стажировка", "премия", "опенколл на выставку", "другое"]
SPECS = ["графдизайн", "иллюстрация", "uxui", "промдизайн", "архитектура_интерьер", "моушн", "3d", "шрифт",
         "упаковка", "фото", "искусство", "мода", "любая"]
SEGMENTS = ["набрать кейсы", "заработать между заказами", "найти заказ для команды", "профинансировать свой проект"]
RF = ["да", "нет", "не указано"]
MONTHS_RU = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
             "ноября", "декабря"]

LOG_FIELDS = ["id", "collected_at", "run_id", "source_name", "source_level", "url", "title", "organizer", "type",
              "deadline", "fee", "prize", "rf_access", "specializations", "status", "reason", "flags", "post_text",
              "moderated_at", "published_at", "message_id", "post_link", "kind", "image", "cover_motif", "scheduled_at", "queue_mode",
              "editor_chat_id", "editor_message_id"]
RUN_FIELDS = ["run_id", "run_at", "sources_total", "sources_failed", "failed_sources", "found", "duplicates",
              "auto_rejected", "to_generation", "cut_by_limit", "sent_to_moderation", "rejected_after_check"]
SUGGESTION_FIELDS = ["received_at", "user_hash", "text", "url", "status"]

UA = "Mozilla/5.0 (compatible; OpencallBot/1.0; +https://github.com/SENYANABE/opencall)"


# ---------- время ----------
def now_msk():
    return datetime.now(MSK).strftime("%Y-%m-%d %H:%M")


def today_msk():
    return datetime.now(MSK).strftime("%Y-%m-%d")


def is_iso(s):
    try:
        datetime.strptime(str(s or ""), "%Y-%m-%d")
        return True
    except ValueError:
        return False


def days_until(iso):
    return (datetime.strptime(iso, "%Y-%m-%d").date() - datetime.now(MSK).date()).days


def fmt_date_ru(iso):
    if not is_iso(iso):
        return str(iso or "")
    d = datetime.strptime(iso, "%Y-%m-%d")
    year = "" if d.year == datetime.now(MSK).year else f" {d.year}"
    return f"{d.day} {MONTHS_RU[d.month - 1]}{year}"


# ---------- текст ----------
def html_to_text(raw, base_url):
    s = str(raw or "")
    s = re.sub(r"(?is)<(script|style|noscript|svg|head|iframe|template)\b.*?</\1>", " ", s)
    s = re.sub(r"(?s)<!--.*?-->", " ", s)

    def link(m):
        href, inner = html.unescape(m.group(1)).strip(), m.group(2)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", inner)).strip()
        if not href or re.match(r"(?i)^(javascript:|#|tel:|data:)", href):
            return f" {text} "
        try:
            url = urljoin(base_url, href)
        except ValueError:
            return f" {text} "
        return f" {text or 'ссылка'} [{url}] "

    s = re.sub(r"(?is)<a\b[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", link, s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|section|article|header|footer|blockquote)>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t ]+", " ", s)
    s = re.sub(r"\s*\n\s*", "\n", s)
    return s.strip()


def norm_url(u):
    u = str(u or "").strip().lower()
    if not u:
        return ""
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = re.sub(r"#.*$", "", u)
    u = re.sub(r"[?&](utm_[a-z]+|fbclid|gclid|yclid|ref)=[^&]*", "", u)
    u = u.replace("?&", "?").rstrip("?")
    return u.rstrip("/")


def norm_title(t):
    t = str(t or "").lower().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]+", " ", t).strip()


def short_id(s):
    return "oc" + format(zlib.crc32(str(s).encode()), "x")


def esc(s):
    return html.escape(str(s or ""), quote=True)


# ---------- сеть ----------
def fetch_page(url, timeout=25):
    """Возвращает (текст_страницы, ошибка)."""
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"})
        if r.status_code >= 400:
            return "", f"HTTP {r.status_code}"
        r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else r.apparent_encoding
        return r.text, ""
    except requests.RequestException as e:
        return "", type(e).__name__


def openai_json(system, user, schema_name, schema, temperature=0.0):
    """Запрос к OpenAI с жёсткой JSON-схемой. Возвращает (dict | None, ошибка)."""
    body = {
        "model": os.environ.get("OPENAI_MODEL") or "gpt-4.1-mini",
        "temperature": temperature,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema", "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
    }
    for attempt in range(3):
        try:
            r = requests.post("https://api.openai.com/v1/chat/completions", json=body, timeout=180,
                              headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]})
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            data = r.json()
            if r.status_code != 200:
                return None, "OpenAI " + str(data.get("error", {}).get("message", r.status_code))[:200]
            return json.loads(data["choices"][0]["message"]["content"]), ""
        except (requests.RequestException, ValueError, KeyError) as e:
            err = type(e).__name__
            time.sleep(3)
    return None, "OpenAI недоступен"


def tg(method, **payload):
    """Вызов Bot API. Возвращает JSON-ответ (ok true/false)."""
    url = f"https://api.telegram.org/bot{os.environ['TG_BOT_TOKEN']}/{method}"
    try:
        r = requests.post(url, json=payload, timeout=75)
        return r.json()
    except (requests.RequestException, ValueError) as e:
        return {"ok": False, "description": type(e).__name__}


def tg_photo(chat_id, path, caption="", parse_mode=None, reply_markup=None):
    """Отправка фото из файла."""
    url = f"https://api.telegram.org/bot{os.environ['TG_BOT_TOKEN']}/sendPhoto"
    data = {"chat_id": chat_id, "caption": caption}
    if parse_mode:
        data["parse_mode"] = parse_mode
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    try:
        with open(path, "rb") as f:
            r = requests.post(url, data=data, files={"photo": f}, timeout=60)
        return r.json()
    except (requests.RequestException, ValueError, OSError) as e:
        return {"ok": False, "description": type(e).__name__}


# ---------- CSV ----------
def read_csv(name):
    p = DATA / name
    if not p.exists():
        return []
    with p.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(name, rows, fields):
    p = DATA / name
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def append_csv(name, rows, fields):
    write_csv(name, read_csv(name) + list(rows), fields)


def read_sources():
    p = ROOT / "sources.csv"
    with p.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows
            if str(r.get("active", "")).strip().lower() == "да" and re.match(r"(?i)^https?://", str(r.get("url", "")).strip())]
