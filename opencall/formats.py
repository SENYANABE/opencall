"""Рубрики помимо ежедневных карточек: дайджест дедлайнов, опрос, навигация, обложки к готовым карточкам.
Всё уходит редактору черновиком и выходит только после его нажатия."""
import argparse
import json
import os
import re
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from collect import evidence_in_page
from covers import generate_cover
from drafts import preview_text, send_draft, visible_len
from editorial import CONFIG, CONFIRMED
from lib import LOG_FIELDS, MSK, ROOT, openai_json, esc, fetch_page, fmt_date_ru, html_to_text, is_iso, now_msk, read_csv, short_id, tg, write_csv
from stats import write_stats

FORMATS = CONFIG["formats"]
POLL = {
    "question": "Подавали заявку на что-то из ленты в этом месяце?",
    "options": ["Да, уже есть результат", "Да, жду ответа", "Собираюсь подать", "Пока только читаю"],
}
TYPE_WORD = {"конкурс": "конкурс", "грант": "грант", "резиденция": "резиденция", "тендер": "тендер",
             "стажировка": "стажировка", "премия": "премия", "опенколл на выставку": "выставка"}


def card_title(r):
    m = re.match(r"<b>(.*?)</b>", r.get("post_text") or "")
    return m.group(1) if m else esc(r.get("title"))


def new_row(kind, text, flags="", *, title="", image=""):
    return {"collected_at": now_msk(), "run_id": "format_" + kind, "source_name": "редакция",
            "source_level": "", "kind": kind, "title": title or kind, "status": "на_модерации",
            "post_text": text, "flags": flags, "image": image}


def published_cards(rows, today):
    return [r for r in rows
            if (r.get("kind") or "card") == "card" and r.get("status") == "опубликовано"
            and not r.get("cancelled_at") and r.get("reason") != "отменён"
            and r.get("access_status") == CONFIRMED and r.get("official_quote")
            and is_iso(r.get("deadline")) and date.fromisoformat(r["deadline"]) >= today
            and r.get("post_link")]


def build_digest(rows, today=None, check=None):
    today = today or datetime.now(MSK).date()
    end = today + timedelta(days=FORMATS["digest"]["days"])
    candidates = sorted((r for r in published_cards(rows, today) if date.fromisoformat(r["deadline"]) <= end),
                        key=lambda r: (r["deadline"], r["id"]))
    items = [r for r in candidates[:15] if check is None or check(r)]
    if not items:
        return None
    title = "Дедлайны ближайших двух недель" if FORMATS["digest"]["days"] == 14 else "Ближайшие дедлайны"
    lines = [f"<b>{title}</b>", "", f"Приём заявок до {fmt_date_ru(end.isoformat())} включительно.", ""]
    day = None
    for row in items:
        if row["deadline"] != day:
            day = row["deadline"]
            lines.append(f"<b>{fmt_date_ru(day)}</b>")
        kind = TYPE_WORD.get(row.get("type"), "опенколл")
        lines.append(f'· <a href="{esc(row["post_link"])}">{card_title(row)}</a>, {kind}')
    lines.extend(["", "#дедлайны"])
    row = new_row("digest", "\n".join(lines), "Перед выпуском проверь, что организаторы не меняли сроки и не отменяли приём.",
                  title=title)
    return dict(row, content_expires_on=items[0]["deadline"])


THEMES = {
    "free": ("Бесплатные конкурсы для дизайнеров", lambda r: r.get("type") == "конкурс"
             and bool(re.search(r"(?i)бесплат|без взнос|^\s*(0|нет|free|no fee)\s*$", r.get("fee", "")))),
    "students": ("Конкурсы для студентов-дизайнеров", lambda r: r.get("type") == "конкурс"
                 and bool(re.search("студент", r.get("eligibility", ""), re.I))),
    "logo": ("Конкурсы на разработку логотипа", lambda r: bool(re.search("логотип", r.get("opportunity_subtype", ""), re.I))),
    "uxui": ("Возможности для UX/UI-дизайнеров", lambda r: "uxui" in r.get("specializations", "")),
    "ru": ("Дизайн-конкурсы в России", lambda r: r.get("geography") == "Россия" and r.get("type") == "конкурс"),
}
MONTHS = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь")


def build_collection(rows, theme=None, today=None, check=None):
    today = today or datetime.now(MSK).date()
    title, matches = THEMES[theme or FORMATS["collection"]["theme"]]
    candidates = sorted((r for r in published_cards(rows, today) if matches(r)),
                        key=lambda r: (r["deadline"], r["id"]))
    items = [r for r in candidates[:6] if check is None or check(r)]
    if len(items) < FORMATS["collection"]["minimum_items"]:
        return None
    title = f"{title}: {MONTHS[today.month - 1]} {today.year}"
    lines = [f"<b>{title}</b>", "", f"Данные на {fmt_date_ru(today.isoformat())} {today.year} года.", ""]
    for row in items:
        lines.append(f'· <a href="{esc(row["post_link"])}">{card_title(row)}</a>, до {fmt_date_ru(row["deadline"])}')
    lines.extend(["", "#подборка"])
    row = new_row("collection", "\n".join(lines), "Сверь сроки и условия у организаторов перед выпуском.", title=title)
    return dict(row, content_expires_on=items[0]["deadline"])



@lru_cache(maxsize=30)
def current_source_text(url):
    raw, error, final_url = fetch_page(url, timeout=8, include_url=True)
    return html_to_text(raw, final_url) if raw and not error else ""


def still_open(row):
    quote, url = row.get("deadline_quote"), row.get("url")
    if not quote or not url:
        return False
    text = current_source_text(url)
    markers = r"(?im)^(?:конкурс отмен[её]н|при[её]м заявок (?:закрыт|заверш[её]н)|applications are closed)[!. ]*$"
    confirmed = evidence_in_page(quote, text) and not re.search(markers, text)
    if not confirmed:
        print("исключено из рубрики: не удалось подтвердить актуальность", row["id"])
    return confirmed

def build_nav(bot="нашему боту", rows=()):
    labels = {"collection": "Подборки конкурсов", "digest": "Дедлайны", "case": "Разборы победителей",
              "guide": "Практические материалы", "poll": "Опросы"}
    latest = {r["kind"]: r["post_link"] for r in rows
              if r.get("kind") in labels and r.get("status") == "опубликовано" and r.get("post_link")}
    tags = {"collection": "#подборка", "digest": "#дедлайны", "case": "#разбор", "guide": "#практика", "poll": ""}
    lines = [
        "<b>ОПЕНКОЛЛ: конкурсы для дизайнеров</b>", "",
        "В ленте есть конкурсы, премии, гранты, резиденции и тендеры. В первых строках карточки ищите дедлайн, взнос и приз. Условия и подача заявки — по ссылке на организатора.", "",
        "<b>Рубрики</b>",
    ]
    for kind, label in labels.items():
        if FORMATS[kind]["enabled"]:
            lines.append(f'<a href="{esc(latest[kind])}">{label}</a>' if kind in latest else f"{label} {tags[kind]}".strip())
    lines += [
        "", "<b>Поиск по тегам</b>",
        "Тип: #конкурс #грант #премия #резиденция #тендер",
        "Направление: #графдизайн #иллюстрация #uxui #архитектура_интерьер #упаковка",
        "География: #россия #международный", "",
        f"Нашли конкурс? Пришлите ссылку {esc(bot)}. Редакция проверит условия.",
    ]
    return new_row("nav", "\n".join(lines), title="ОПЕНКОЛЛ: конкурсы для дизайнеров")


def build_poll():
    return new_row("poll", json.dumps(POLL, ensure_ascii=False), title=POLL["question"])


MANUAL = {"case", "stats", "guide", "update", "tenders", "subscriber"}


def manual_draft(fmt, file):
    payload = json.loads(Path(file).read_text(encoding="utf-8"))
    if fmt == "poll":
        question, options = payload.get("question", ""), payload.get("options", [])
        if not payload.get("key") or not 1 <= len(question) <= 300 or not 2 <= len(options) <= 10 or any(
                not isinstance(option, str) or not 1 <= len(option) <= 100 for option in options):
            raise ValueError("укажите key, вопрос и от 2 до 10 вариантов")
        return new_row("poll", json.dumps({"question": question, "options": options}, ensure_ascii=False),
                       title=question), payload["key"]
    if not payload.get("key") or not payload.get("title") or not payload.get("text"):
        raise ValueError("укажите key, title и text в файле выпуска")
    if fmt == "case" and not all(payload.get(field) for field in ("source", "credit", "rights")):
        raise ValueError("для разбора нужны первоисточник, авторство и основание для публикации")
    if fmt == "stats" and not payload.get("source"):
        raise ValueError("для статистики укажите источник данных")
    text = payload["text"]
    if payload.get("credit"):
        text += "\n\nАвторство: " + esc(payload["credit"])
    if payload.get("source"):
        source = payload["source"]
        if not re.fullmatch(r"https?://[^\s<>]+", source):
            raise ValueError("укажите публичную ссылку на источник")
        text += f'\n<a href="{esc(source)}">Источник</a>'
    image = payload.get("image", "")
    if image and not ((ROOT / image).is_file() and (ROOT / image).resolve().is_relative_to(ROOT)):
        raise ValueError("обложка должна находиться в репозитории")
    if visible_len(text) > (1024 if image else 3700):
        raise ValueError("текст превышает допустимую длину")
    row = new_row(fmt, text, payload.get("rights", ""), title=payload["title"], image=image)
    row.update(content_expires_on=payload.get("content_expires_on", ""))
    return row, payload["key"]


def scheduled_formats(now):
    for fmt, config in FORMATS.items():
        if not config.get("enabled") or not config.get("time") or now.strftime("%H:%M") < config["time"]:
            continue
        if now.weekday() == config.get("weekday") or now.day == config.get("month_day"):
            yield fmt


def period_key(fmt, today, theme):
    if fmt == "poll":
        return today.strftime("%Y-%m")
    if fmt in ("digest", "collection"):
        iso = today.isocalendar()
        return f"{iso.year}-W{iso.week:02d}" + (f":{theme or FORMATS['collection']['theme']}" if fmt == "collection" else "")
    return ""


def known_issue(rows, fmt, key):
    for row in rows:
        if row.get("kind") != fmt:
            continue
        old = row.get("collected_at", "")[:10]
        legacy_key = period_key(fmt, date.fromisoformat(old), None) if is_iso(old) and fmt in ("digest", "poll") else ""
        if (row.get("format_key") or legacy_key) == key:
            return row
    return None


MOTIF_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["cover_motif"],
                "properties": {"cover_motif": {"type": "string"}}}


def motif_for(row):
    """Мотив обложки по названию, если карточка создана до появления мотивов."""
    data, _ = openai_json(
        "Придумай по-английски, 8-15 слов, один главный предмет-метафору для обложки этой возможности для дизайнеров. "
        "Предмет должен быть сделан из прозрачного стекла и хрома. Только предмет, без фона и освещения, без текста, людей и логотипов. "
        "Избегай домиков, если это не главное в анонсе.",
        f"Название: {row.get('title')}\nТип: {row.get('type')}\nОрганизатор: {row.get('organizer')}",
        "motif", MOTIF_SCHEMA, temperature=0.8)
    return (data or {}).get("cover_motif", "")


def covers_for_pending(rows, limit=4):
    """Перерисовать обложки у карточек на модерации (после смены стиля или для старых черновиков)."""
    done = []
    for r in reversed(rows):  # свежие карточки первыми
        if len(done) >= limit:
            break
        if (r.get("kind") or "card") == "card" and r["status"] == "на_модерации":
            if not r.get("cover_motif"):
                r["cover_motif"] = motif_for(r)
            path, err = generate_cover(r, r.get("cover_motif", ""))
            if path:
                r["image"] = path
            else:
                r["flags"] = "; ".join(f for f in [r.get("flags"), err] if f)
            r["kind"] = "card"
            done.append(r)
    return done


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", dest="fmt", choices=[*FORMATS, "card_cover"])
    parser.add_argument("--theme", choices=THEMES)
    parser.add_argument("--input")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    selected = args.fmt or os.environ.get("FORMAT", "").strip()
    now = datetime.now(MSK)
    rows = read_csv("log.csv")
    args.dry_run = args.dry_run or os.environ.get("OPENCALL_DRY_RUN") == "1"
    if selected == "card_cover":
        if args.dry_run:
            print("Пробный запуск: генерация и отправка обложек отключены.")
            return
        for row in covers_for_pending(rows):
            write_csv("log.csv", rows, LOG_FIELDS)
            result = send_draft(row)
            print(row["id"], "обложка:", row.get("image") or row.get("flags"), "черновик:", result.get("ok"))
        write_stats()
        return

    def nav():
        bot = os.environ.get("TG_BOT_USERNAME")
        if not bot and not args.dry_run:
            me = tg("getMe")
            bot = me.get("result", {}).get("username")
        return build_nav("@" + bot.lstrip("@") if bot else "нашему боту", rows)

    check = None if args.dry_run else still_open
    builders = {"digest": lambda: build_digest(rows, now.date(), check=check),
                "collection": lambda: build_collection(rows, args.theme, now.date(), check=check),
                "poll": build_poll, "nav": nav}
    if selected and selected not in builders and selected not in MANUAL:
        parser.error("неизвестная рубрика")
    for fmt in ([selected] if selected else scheduled_formats(now)):
        if not FORMATS[fmt]["enabled"]:
            print(fmt, "выключено")
            continue
        manual = fmt in MANUAL or (fmt in ("collection", "poll") and args.input)
        row = None
        if manual:
            if not args.input:
                parser.error("для ручного выпуска указанной рубрики нужен --input")
            row, key = manual_draft(fmt, args.input)
        else:
            key = period_key(fmt, now.date(), args.theme)
        previous = known_issue(rows, fmt, key) if key and not args.dry_run else None
        if not row and not previous:
            row = builders[fmt]()
        if not row and not previous:
            print(fmt, "нет подходящих материалов")
            continue
        key = key or short_id(row["post_text"])
        if not previous and not args.dry_run:
            previous = known_issue(rows, fmt, key)
        if args.dry_run:
            print(fmt, key, row.get("image") or "", "\n" + preview_text(row))
            continue
        if previous and (not previous.get("format_key") or previous.get("editor_message_id")
                         or previous.get("status") != "на_модерации"
                         or previous.get("draft_delivery") in {"sending", "uncertain"}):
            print(fmt, key, "выпуск уже есть", previous["id"], previous.get("draft_delivery", ""))
            continue
        if previous:
            row = previous
        else:
            if FORMATS[fmt].get("publish_time"):
                row["release_at"] = f"{now.date()} {FORMATS[fmt]['publish_time']}"
            row.update(id=short_id(f"{fmt}:{key}"), format_key=key)
            rows.append(row)
        row["draft_delivery"] = "sending"
        write_csv("log.csv", rows, LOG_FIELDS)
        result = send_draft(row)
        message = result.get("result", {})
        if result.get("ok"):
            row.update(editor_message_id=str(message["message_id"]), editor_chat_id=str(message["chat"]["id"]),
                       draft_delivery="sent")
        else:
            row["draft_delivery"] = "uncertain" if result.get("delivery_uncertain") else "failed"
        write_csv("log.csv", rows, LOG_FIELDS)
        print(fmt, row["id"], "черновик:", result.get("ok"), result.get("description", ""))
        write_stats()


if __name__ == "__main__":
    main()
