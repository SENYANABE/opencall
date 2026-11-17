"""Сбор и подготовка: источники -> извлечение -> отбор -> сверка -> карточка -> черновик редактору."""
import json
import os
import re

from lib import (LOG_FIELDS, RF, RUN_FIELDS, SEGMENTS, SPECS, TYPES, append_csv, days_until, esc, fetch_page,
                 fmt_date_ru, html_to_text, is_iso, norm_title, norm_url, now_msk, openai_json, read_csv,
                 read_sources, short_id, tg, today_msk)
from covers import generate_cover
from drafts import send_draft
from stats import write_stats

MAX_TO_GENERATION = 10  # лимит карточек за запуск: контроль расходов и нагрузки на редактора
MIN_DAYS = 3            # коллы с дедлайном ближе 3 дней не берём
SOURCE_CHARS = 14000
PAGE_CHARS = 12000

STOP_LIST = ["успей", "уникальн", "не упусти", "шанс всей жизни", "лучший", "лучшая", "гарантир", "жми", "срочно"]
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
TYPE_TAG = {"конкурс": "конкурс", "грант": "грант", "резиденция": "резиденция", "тендер": "тендер",
            "стажировка": "стажировка", "премия": "премия", "опенколл на выставку": "выставка", "другое": "опенколл"}
SEGMENT_TEXT = {
    "набрать кейсы": "студентам и джунам, чтобы набрать кейсы",
    "заработать между заказами": "фрилансерам, чтобы заработать между заказами",
    "найти заказ для команды": "студиям и командам",
    "профинансировать свой проект": "тем, кто ищет деньги на свой проект",
}

EXTRACT_SYSTEM = "\n".join([
    "Ты сборщик данных медиа ОПЕНКОЛЛ: лента возможностей для дизайнеров (конкурсы, гранты, резиденции, тендеры, стажировки, опенколлы).",
    "Тебе дают текст страницы источника. Ссылки в тексте записаны в квадратных скобках после текста ссылки.",
    "Найди на странице отдельные анонсы возможностей с открытым приёмом заявок и верни их списком.",
    "Правила:",
    "1. Бери факты только из текста. Если поля нет, пиши пустую строку. Ничего не додумывай.",
    "2. url: ссылка на страницу этого анонса или организатора, строго одна из ссылок в квадратных скобках. Если подходящей ссылки нет, пустая строка.",
    "3. deadline: дата окончания приёма заявок в формате YYYY-MM-DD. Если год не указан, выбери ближайший будущий относительно сегодняшней даты. Если дедлайнов несколько, бери последний для подачи заявки.",
    "4. Не пересчитывай валюты и не округляй суммы. fee и prize пиши так, как в тексте.",
    "5. specializations: только те, что прямо названы или очевидно следуют из текста, не больше трёх. Если подходит всем, только 'любая'.",
    "6. is_design_relevant: true, если возможность подходит дизайнерам, иллюстраторам, архитекторам, художникам или студиям. Вакансии, курсы и распродажи: false.",
    "7. Пропускай анонсы, у которых дедлайн уже прошёл, и итоги конкурсов.",
    "8. Не больше 15 анонсов. Пустой список, если подходящего нет.",
])

EXTRACT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["title", "url", "organizer", "type", "deadline", "fee", "prize", "rf_access", "specializations",
                     "is_design_relevant", "summary_source"],
        "properties": {
            "title": {"type": "string"},
            "url": {"type": "string"},
            "organizer": {"type": "string"},
            "type": {"type": "string", "enum": TYPES},
            "deadline": {"type": "string"},
            "fee": {"type": "string", "description": "Взнос как в тексте, 'бесплатно' если явно указано, иначе пусто"},
            "prize": {"type": "string"},
            "rf_access": {"type": "string", "enum": RF, "description": "Могут ли участвовать жители России"},
            "specializations": {"type": "array", "items": {"type": "string", "enum": SPECS}},
            "is_design_relevant": {"type": "boolean"},
            "summary_source": {"type": "string", "description": "Суть анонса своими словами из источника, до 400 знаков"},
        }}}},
}

CARD_SYSTEM = "\n".join([
    "Ты редактор медиа ОПЕНКОЛЛ: лента возможностей для дизайнеров в Телеграме.",
    "Тебе дают поля анонса из агрегатора и текст страницы организатора (может быть пустым, если страница не открылась).",
    "Задача 1. Сверка. Найди на странице организатора дедлайн приёма заявок (YYYY-MM-DD), взнос, призы и доступность для участников из России. Пиши только то, что есть на странице, иначе пустая строка или 'не указано'. В mismatch_notes коротко перечисли расхождения с полями агрегатора, иначе пусто.",
    "Задача 2. Текст карточки на русском.",
    "title_ru: название возможности по-русски, до 90 знаков. Имена собственные и названия премий можно оставить в оригинале.",
    "summary_ru: 2-3 предложения, до 350 знаков: что это, что нужно подать, что получит участник.",
    "fee_ru и prize_ru: коротко, до 60 знаков, валюту и суммы не пересчитывай и не округляй. Если данных нет, пустая строка.",
    "segment: какой задаче дизайнера возможность подходит больше всего.",
    "specializations в агрегаторе могли быть заполнены широко: это не твоя задача, не обращай внимания.",
    "Тон: коротко, на равных, без длинных тире, восклицательных знаков, эмодзи, капслока и слов «успейте», «уникальный шанс», «не упустите», «лучший».",
    "Не обещай победу и не придумывай условия, которых нет в данных.",
    "cover_motif: по-английски, 8-15 слов: один предмет-метафора для обложки этой возможности (например, a chrome trophy with a glass star). Без текста, людей, логотипов и флагов.",
])

CARD_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["page_deadline", "page_fee", "page_prize", "page_rf_access", "mismatch_notes", "title_ru",
                 "summary_ru", "fee_ru", "prize_ru", "segment", "cover_motif"],
    "properties": {
        "page_deadline": {"type": "string"}, "page_fee": {"type": "string"}, "page_prize": {"type": "string"},
        "page_rf_access": {"type": "string", "enum": RF}, "mismatch_notes": {"type": "string"},
        "title_ru": {"type": "string"}, "summary_ru": {"type": "string"}, "fee_ru": {"type": "string"},
        "prize_ru": {"type": "string"}, "segment": {"type": "string", "enum": SEGMENTS},
        "cover_motif": {"type": "string"},
    },
}


# ---------- 1. сбор и извлечение ----------
def collect(sources, extract=None, fetch=None):
    extract = extract or (lambda src, text: openai_json(
        EXTRACT_SYSTEM, f"Сегодня: {today_msk()}\nИсточник: {src['name']} ({src['url']})\n\nТекст страницы:\n{text}",
        "opencalls", EXTRACT_SCHEMA))
    fetch = fetch or fetch_page
    found, failed = [], []
    for src in sources:
        raw, err = fetch(src["url"].strip())
        text = html_to_text(raw, src["url"])[:SOURCE_CHARS] if raw else ""
        if err or len(text) < 100:
            failed.append(f"{src['name']}: страница не загрузилась ({err or 'пусто'})")
            continue
        data, err = extract(src, text)
        if not data:
            failed.append(f"{src['name']}: извлечение не удалось ({err})")
            continue
        for op in data.get("items", []):
            found.append((src, op))
    return found, failed


# ---------- 2. отбор ----------
def select(found, log_rows, run_id):
    seen_url = {norm_url(r.get("url")) for r in log_rows} - {""}
    seen_title = {norm_title(r.get("title")) for r in log_rows} - {""}
    duplicates, rejected, passing = 0, [], []
    for src, op in found:
        title, url = str(op.get("title", "")).strip(), str(op.get("url", "")).strip()
        k_url, k_title = norm_url(url), norm_title(title)
        if (k_url and k_url in seen_url) or (k_title and k_title in seen_title):
            duplicates += 1
            continue
        seen_url.add(k_url)
        seen_title.add(k_title)
        row = {
            "id": short_id(k_url or k_title), "collected_at": now_msk(), "run_id": run_id,
            "source_name": src["name"], "source_level": str(src.get("level", "")).strip().upper(),
            "url": url, "title": title, "organizer": str(op.get("organizer", "")).strip(),
            "type": op.get("type", ""), "deadline": str(op.get("deadline", "")).strip(),
            "fee": str(op.get("fee", "")).strip(), "prize": str(op.get("prize", "")).strip(),
            "rf_access": op.get("rf_access") or "не указано",
            "specializations": ", ".join(op.get("specializations") or []),
            "summary_source": str(op.get("summary_source", "")).strip(),
            "status": "", "reason": "", "flags": "", "post_text": "",
        }
        reason = ""
        if not title:
            reason = "нет названия"
        elif op.get("is_design_relevant") is False:
            reason = "не про дизайн"
        elif not re.match(r"(?i)^https?://", url):
            reason = "нет ссылки на первоисточник"
        elif not row["deadline"]:
            reason = "нет дедлайна"
        elif not is_iso(row["deadline"]):
            reason = "дедлайн не распознан"
        elif days_until(row["deadline"]) < 0:
            reason = "дедлайн прошёл"
        elif days_until(row["deadline"]) < MIN_DAYS:
            reason = "до дедлайна меньше 3 дней"
        elif row["rf_access"] == "нет":
            reason = "недоступно из России"
        if reason:
            row.update(status="отбраковано_авто", reason=reason)
            rejected.append(row)
        else:
            passing.append(row)
    passing.sort(key=lambda r: r["deadline"])  # ближайшие дедлайны первыми, остальное ждёт следующего запуска
    return passing[:MAX_TO_GENERATION], rejected, duplicates, max(0, len(passing) - MAX_TO_GENERATION)


# ---------- 3. сверка и карточка ----------
def build_card(row, page_ok, g):
    row = dict(row)
    flags = []

    def reject(reason):
        row.update(status="отбраковано_авто", reason=reason, flags="; ".join(flags))
        return row

    if not g:
        return reject("ошибка генерации")
    if not page_ok:
        flags.append("страница организатора не открылась, факты только из агрегатора")
    if is_iso(g.get("page_deadline")):
        if g["page_deadline"] != row["deadline"]:
            flags.append(f"дедлайн расходится: агрегатор {row['deadline']}, организатор {g['page_deadline']}")
        row["deadline"] = g["page_deadline"]
    if g.get("page_rf_access") and g["page_rf_access"] != "не указано":
        row["rf_access"] = g["page_rf_access"]
    if g.get("page_fee"):
        row["fee"] = g["page_fee"]
    if g.get("page_prize"):
        row["prize"] = g["page_prize"]
    if g.get("mismatch_notes"):
        flags.append("расхождения: " + g["mismatch_notes"])
    if row.get("source_level") == "C" and not page_ok:
        flags.append("источник уровня C без сверки")

    if row["rf_access"] == "нет":
        return reject("недоступно из России")
    if days_until(row["deadline"]) < MIN_DAYS:
        return reject("до дедлайна меньше 3 дней")

    fee_text = g.get("fee_ru") or row["fee"] or "не указан"
    prize_text = g.get("prize_ru") or row["prize"] or "не указан"
    if row["fee"] and not re.search(r"(?i)бесплат|free|no fee|без взнос|^\s*(нет|0)\s*$", row["fee"]):
        flags.append("платное участие: проверь, не конкурс ли ради взноса")

    # длинное тире в ToV не используем: заменяем на двоеточие или дефис
    title = re.sub(r"\s*[—–]\s*", ": ", str(g.get("title_ru", "")).strip(), count=1).replace("—", "-")
    summary = str(g.get("summary_ru", "")).strip().replace(" — ", ", ").replace("—", "-")
    prose = (title + " " + summary).lower()
    hit = next((w for w in STOP_LIST if w in prose), None)
    if hit:
        return reject(f"стоп-лист: «{hit}»")
    if "!" in title + summary:
        return reject("стоп-лист: восклицательный знак")
    if EMOJI.search(title + summary):
        return reject("стоп-лист: эмодзи")
    if not title or not summary:
        return reject("пустой текст карточки")
    if len(summary) > 600:
        return reject("текст длиннее 600 знаков")

    tags = ["#" + TYPE_TAG.get(row["type"], "опенколл")]
    tags += ["#" + s.strip() for s in row["specializations"].split(",") if s.strip() and s.strip() != "любая"][:3]
    is_ru = re.search(r"(?i)рф|росси|russia", row["organizer"] + " " + row["url"]) or re.search(r"(?i)\.ru(/|$)|\.рф", row["url"])
    tags.append("#россия" if is_ru else "#международный")

    lines = [
        f"<b>{esc(title)}</b>", "",
        f"Дедлайн: {esc(fmt_date_ru(row['deadline']))}",
        f"Взнос: {esc(fee_text)}",
        f"Приз: {esc(prize_text)}", "",
        esc(summary), "",
        f"Кому: {esc(SEGMENT_TEXT.get(g.get('segment'), g.get('segment', '')))}",
        "Из России: " + ("можно участвовать" if row["rf_access"] == "да" else "условия не указаны, уточняй у организатора"),
    ]
    if row["organizer"]:
        lines.append(f"Организатор: {esc(row['organizer'])}")
    lines += ["", f'<a href="{esc(row["url"])}">Подать заявку</a>', "", " ".join(tags)]
    row.update(post_text="\n".join(lines), status="на_модерации", reason="", flags="; ".join(flags), kind="card",
               cover_motif=str(g.get("cover_motif", "")))
    return row


def make_card(row, fetch=None, generate=None):
    fetch = fetch or fetch_page
    raw, err = fetch(row["url"])
    page_text = html_to_text(raw, row["url"])[:PAGE_CHARS] if raw else ""
    page_ok = not err and len(page_text) > 200
    facts = {k: row.get(k, "") for k in ("title", "organizer", "type", "deadline", "fee", "prize", "rf_access", "url", "summary_source")}
    user = (f"Сегодня: {today_msk()}\n\nПоля из агрегатора:\n{json.dumps(facts, ensure_ascii=False, indent=1)}\n\nТекст страницы организатора:\n"
            + (page_text or "(страница не открылась)"))
    if generate:
        g = generate(row, page_text)
    else:
        g, _ = openai_json(CARD_SYSTEM, user, "card", CARD_SCHEMA, temperature=0.3)
    return build_card(row, page_ok, g)


# ---------- 4. обложки ----------
def add_covers(cards, make=None):
    """Генерирует обложки для карточек на модерации. Сбой обложки не блокирует карточку."""
    make = make or generate_cover
    for c in cards:
        path, err = make(c, c.get("cover_motif", ""))
        if path:
            c["image"] = path
        else:
            c["flags"] = "; ".join(f for f in [c.get("flags"), err] if f)
    return cards


def main():
    run_id = "run_" + now_msk().replace("-", "").replace(":", "").replace(" ", "")
    sources = read_sources()
    print(f"[{run_id}] активных источников: {len(sources)}")

    found, failed = collect(sources)
    print(f"найдено анонсов: {len(found)}, сбоев источников: {len(failed)}")
    log_rows = read_csv("log.csv")
    to_gen, rejected, duplicates, cut = select(found, log_rows, run_id)
    print(f"дубли: {duplicates}, брак на входе: {len(rejected)}, в генерацию: {len(to_gen)}, отложено лимитом: {cut}")

    cards = [make_card(r) for r in to_gen]
    to_mod = [c for c in cards if c["status"] == "на_модерации"]
    late_rejects = [c for c in cards if c["status"] != "на_модерации"]

    if os.environ.get("COVERS", "1") != "0":
        add_covers(to_mod)

    # сначала пишем лог, потом шлём черновики: так статус не потеряется при сбое Телеграма
    append_csv("log.csv", rejected + cards, LOG_FIELDS)
    for c in to_mod:
        res = send_draft(c)
        if not res.get("ok"):
            print("не отправлен черновик", c["id"], res.get("description"))

    append_csv("runs.csv", [{
        "run_id": run_id, "run_at": now_msk(), "sources_total": len(sources), "sources_failed": len(failed),
        "failed_sources": "; ".join(failed), "found": len(found), "duplicates": duplicates,
        "auto_rejected": len(rejected), "to_generation": len(to_gen), "cut_by_limit": cut,
        "sent_to_moderation": len(to_mod), "rejected_after_check": len(late_rejects),
    }], RUN_FIELDS)
    write_stats()
    print(f"на модерацию: {len(to_mod)}, отбраковано после сверки: {len(late_rejects)}")


if __name__ == "__main__":
    main()
