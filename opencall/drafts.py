"""Черновики редактору и публикация в канал для всех форматов."""
import json
import os
import re

from lib import ROOT, esc, tg, tg_photo

CAPTION_LIMIT = 1024  # лимит подписи к фото в Телеграме

KIND_LABEL = {"card": "Карточка", "digest": "Дайджест дедлайнов", "poll": "Опрос", "nav": "Навигация (закреп)"}


def visible_len(html_text):
    return len(re.sub(r"<[^>]+>", "", html_text).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))


def fit_caption(post_text):
    """Если пост не влезает в подпись к фото, укорачиваем абзац с описанием."""
    if visible_len(post_text) <= CAPTION_LIMIT:
        return post_text
    blocks = post_text.split("\n\n")
    if len(blocks) >= 3:
        summary = blocks[2]
        over = visible_len(post_text) - CAPTION_LIMIT + 2
        sentences = re.split(r"(?<=[.!?])\s+", summary)
        while sentences and visible_len(" ".join(sentences)) > max(0, visible_len(summary) - over):
            sentences.pop()
        blocks[2] = " ".join(sentences) if sentences else ""
        text = "\n\n".join(b for b in blocks if b)
        if visible_len(text) <= CAPTION_LIMIT:
            return text
    return ""  # не влезло: публикуем без фото


def image_path(row):
    p = (row.get("image") or "").strip()
    return ROOT / p if p and (ROOT / p).exists() else None


def editor_markup(row):
    rid = row["id"]
    kb = [[{"text": "В очередь", "callback_data": f"que:{rid}"},
           {"text": "Опубликовать сейчас", "callback_data": f"pub:{rid}"}]]
    if row.get("kind", "card") == "card":
        if image_path(row):
            kb.append([{"text": "В очередь без обложки", "callback_data": f"quetxt:{rid}"},
                       {"text": "Сейчас без обложки", "callback_data": f"pubtxt:{rid}"}])
        kb += [
            [{"text": "Не по теме", "callback_data": f"rej:{rid}:topic"},
             {"text": "Ошибка в данных", "callback_data": f"rej:{rid}:data"}],
            [{"text": "Сомнительный организатор", "callback_data": f"rej:{rid}:org"},
             {"text": "Дубль", "callback_data": f"rej:{rid}:dup"}],
            [{"text": "Слабый повод", "callback_data": f"rej:{rid}:weak"}],
        ]
    else:
        kb.append([{"text": "Отклонить", "callback_data": f"rej:{rid}:format"}])
    return {"inline_keyboard": kb}


def preview_text(row):
    if row.get("kind") == "poll":
        p = json.loads(row["post_text"])
        return "Вопрос: " + esc(p["question"]) + "\n" + "\n".join("• " + esc(o) for o in p["options"])
    return row["post_text"]


def send_draft(row, chat_id=None, send=tg, send_photo=tg_photo):
    chat_id = chat_id or os.environ["TG_EDITOR_CHAT_ID"]
    kind = row.get("kind") or "card"
    service = [f"<b>{KIND_LABEL.get(kind, kind)} · черновик {esc(row['id'])}</b>"]
    if kind == "card":
        service.append(f"Источник: {esc(row.get('source_name'))} (уровень {esc(row.get('source_level') or '?')})")
    service.append(f"Флаги: {esc(row.get('flags') or 'нет')}")
    img = image_path(row)
    if img:
        send_photo(chat_id, img, caption=f"Обложка к черновику {row['id']}")
    text = "\n".join(service) + "\n\n- - - - - - - - - -\n\n" + preview_text(row)
    return send("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=editor_markup(row),
                link_preview_options={"is_disabled": True})


def publish(row, with_image=True, send=tg, send_photo=tg_photo):
    """Публикует в канал. Возвращает ответ Телеграма."""
    channel = os.environ["TG_CHANNEL_ID"]
    kind = row.get("kind") or "card"
    if kind == "poll":
        p = json.loads(row["post_text"])
        return send("sendPoll", chat_id=channel, question=p["question"], options=[{"text": o} for o in p["options"]],
                    is_anonymous=True)
    img = image_path(row) if with_image else None
    if img:
        caption = fit_caption(row["post_text"])
        if caption:
            return send_photo(channel, img, caption=caption, parse_mode="HTML")
    return send("sendMessage", chat_id=channel, text=row["post_text"], parse_mode="HTML",
                link_preview_options={"is_disabled": True})
