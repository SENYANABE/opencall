"""Рубрики помимо ежедневных карточек: дайджест дедлайнов, опрос, навигация, обложки к готовым карточкам.
Всё уходит редактору черновиком и выходит только после его нажатия."""
import json
import os
import re
from datetime import datetime

from covers import generate_cover
from drafts import send_draft
from lib import LOG_FIELDS, MSK, append_csv, openai_json, days_until, esc, fmt_date_ru, is_iso, now_msk, read_csv, short_id, tg, write_csv
from stats import write_stats

DIGEST_DAYS = 14
POLL = {
    "question": "Подавали заявку на что-то из ленты в этом месяце?",
    "options": ["Да, уже есть результат", "Да, жду ответа", "Собираюсь подать", "Пока только читаю"],
}
TYPE_WORD = {"конкурс": "конкурс", "грант": "грант", "резиденция": "резиденция", "тендер": "тендер",
             "стажировка": "стажировка", "премия": "премия", "опенколл на выставку": "выставка"}


def card_title(r):
    m = re.match(r"<b>(.*?)</b>", r.get("post_text") or "")
    return m.group(1) if m else esc(r.get("title"))


def new_row(kind, text, flags=""):
    stamp = now_msk()
    return {"id": short_id(kind + stamp), "collected_at": stamp, "run_id": "format_" + kind, "source_name": "редакция",
            "source_level": "", "kind": kind, "title": kind, "status": "на_модерации", "post_text": text, "flags": flags}


def build_digest(rows):
    items = [r for r in rows
             if (r.get("kind") or "card") == "card" and r["status"] in ("на_модерации", "опубликовано")
             and is_iso(r.get("deadline")) and 0 <= days_until(r["deadline"]) <= DIGEST_DAYS]
    items.sort(key=lambda r: r["deadline"])
    if not items:
        return None
    lines = ["<b>Дедлайны ближайших двух недель</b>", "", "Что закрывается до " + fmt_date_ru(
        datetime.fromordinal(datetime.now(MSK).date().toordinal() + DIGEST_DAYS).strftime("%Y-%m-%d")) + ":", ""]
    day = None
    for r in items[:15]:
        if r["deadline"] != day:
            day = r["deadline"]
            lines.append(f"<b>{fmt_date_ru(day)}</b>")
        link = r.get("post_link") or r["url"]
        kind = TYPE_WORD.get(r.get("type"), "опенколл")
        lines.append(f'· <a href="{esc(link)}">{card_title(r)}</a>, {kind}')
    lines += ["", "Сохраните пост, чтобы не пропустить. #дедлайны"]
    flags = "в дайджесте есть неопубликованные карточки: ссылки ведут на организатора" if any(
        r["status"] != "опубликовано" for r in items[:15]) else ""
    return new_row("digest", "\n".join(lines), flags)


def build_nav():
    me = tg("getMe")
    bot = "@" + me["result"]["username"] if me.get("ok") else "нашему боту"
    text = "\n".join([
        "<b>ОПЕНКОЛЛ: как пользоваться лентой</b>", "",
        "Конкурсы, гранты, резиденции и тендеры для дизайнеров из России и мира. Каждую карточку сверяем со страницей организатора, дедлайн, взнос и приз всегда в первых строках.", "",
        "<b>Рубрики</b>",
        "Карточки: каждый день",
        "Тендеры недели: по понедельникам",
        "Дедлайны ближайших двух недель: по пятницам",
        "Разбор победителей: раз в две недели",
        "Опрос «подавали заявку?»: раз в месяц", "",
        "<b>Теги</b>",
        "Тип: #конкурс #грант #резиденция #тендер #стажировка",
        "Специализация: #графдизайн #иллюстрация #uxui #промдизайн #моушн #3d #шрифт #упаковка",
        "География: #россия #международный", "",
        f"Нашли опенколл, которого нет в ленте? Пришлите ссылку {esc(bot)}, проверим и добавим.",
    ])
    return new_row("nav", text)


def build_poll():
    return new_row("poll", json.dumps(POLL, ensure_ascii=False))


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
    fmt = (os.environ.get("FORMAT") or "").strip()
    if not fmt:  # запуск по расписанию
        now = datetime.now(MSK)
        fmt = "digest" if now.weekday() == 4 else "poll" if now.day == 1 else ""
    if not fmt:
        print("сегодня по расписанию форматов нет")
        return
    rows = read_csv("log.csv")
    print("формат:", fmt)
    if fmt == "card_cover":
        for r in covers_for_pending(rows):
            write_csv("log.csv", rows, LOG_FIELDS)
            res = send_draft(r)
            print(r["id"], "обложка:", r.get("image") or r.get("flags"), "черновик:", res.get("ok"), res.get("description", ""))
        write_csv("log.csv", rows, LOG_FIELDS)
    else:
        builder = {"digest": lambda: build_digest(rows), "poll": build_poll, "nav": build_nav}[fmt]
        row = builder()
        if not row:
            print("нет материалов для формата")
            return
        append_csv("log.csv", [row], LOG_FIELDS)
        res = send_draft(row)
        print(row["id"], "черновик:", res.get("ok"), res.get("description", ""))
    write_stats()


if __name__ == "__main__":
    main()
