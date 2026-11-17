"""Модерация: забираем нажатия редактора и сообщения подписчиков из Телеграма, публикуем, пишем лог."""
import hashlib
import json
import os
import re

from lib import DATA, LOG_FIELDS, SUGGESTION_FIELDS, append_csv, now_msk, read_csv, tg, write_csv
from stats import write_stats

REASONS = {"topic": "не по теме", "data": "ошибка в данных", "org": "сомнительный организатор", "dup": "дубль",
           "weak": "слабый повод"}
STATE = DATA / "state.json"
START_TEXT = ("Это бот ОПЕНКОЛЛ. Пришлите ссылку на конкурс, грант, резиденцию или тендер для дизайнеров. "
              "Редактор проверит условия у организатора и добавит в ленту.")
THANKS_TEXT = "Спасибо, получили. Проверим условия у организатора и, если всё сходится, опубликуем в канале."


def load_offset():
    try:
        return json.loads(STATE.read_text()).get("offset", 0)
    except (OSError, ValueError):
        return 0


def save_offset(offset):
    STATE.write_text(json.dumps({"offset": offset}))


def post_link(result):
    chat = result.get("chat", {})
    if chat.get("username"):
        return f"https://t.me/{chat['username']}/{result['message_id']}"
    return f"https://t.me/c/{str(chat.get('id', '')).replace('-100', '', 1)}/{result['message_id']}"


def handle_callback(cq, rows, editor_id, send=tg):
    """Меняет rows на месте. Возвращает True, если лог изменился."""
    parts = str(cq.get("data", "")).split(":")
    msg = cq.get("message") or {}
    chat_id, message_id = (msg.get("chat") or {}).get("id"), msg.get("message_id")
    if str((cq.get("from") or {}).get("id")) != str(editor_id):
        send("answerCallbackQuery", callback_query_id=cq["id"], text="Модерация доступна только редактору")
        return False
    if len(parts) < 2 or parts[0] not in ("pub", "rej"):
        send("answerCallbackQuery", callback_query_id=cq["id"])
        return False

    row = next((r for r in rows if r["id"] == parts[1]), None)
    if not row:
        send("answerCallbackQuery", callback_query_id=cq["id"], text="Карточка не найдена в логе")
        return False
    if row["status"] in ("опубликовано", "отклонено"):
        send("answerCallbackQuery", callback_query_id=cq["id"], text="Уже обработано: " + row["status"])
        return False

    row["moderated_at"] = now_msk()
    if parts[0] == "rej":
        reason = REASONS.get(parts[2] if len(parts) > 2 else "", "отклонено редактором")
        row.update(status="отклонено", reason=reason)
        ack, markup = "Отклонено: " + reason, {"inline_keyboard": [[{"text": "Отклонено: " + reason, "callback_data": "noop"}]]}
    else:
        res = send("sendMessage", chat_id=os.environ["TG_CHANNEL_ID"], text=row["post_text"], parse_mode="HTML",
                   link_preview_options={"is_disabled": True})
        if res.get("ok"):
            link = post_link(res["result"])
            row.update(status="опубликовано", reason="", published_at=now_msk(),
                       message_id=str(res["result"]["message_id"]), post_link=link)
            ack, markup = "Опубликовано", {"inline_keyboard": [[{"text": "Опубликовано: открыть пост", "url": link}]]}
        else:
            row.update(status="ошибка_публикации", reason="Телеграм: " + str(res.get("description", "нет ответа")))
            ack = "Ошибка публикации: " + str(res.get("description", ""))
            markup = {"inline_keyboard": [[{"text": "Повторить публикацию", "callback_data": "pub:" + row["id"]}]]}
    send("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id, reply_markup=markup)
    send("answerCallbackQuery", callback_query_id=cq["id"], text=ack[:190])
    return True


def resend_drafts(rows, editor_id, send=tg):
    from collect import editor_message
    pending = [r for r in rows if r["status"] in ("на_модерации", "ошибка_публикации")]
    for r in pending:
        text, markup = editor_message(r)
        send("sendMessage", chat_id=editor_id, text=text, parse_mode="HTML", reply_markup=markup,
             link_preview_options={"is_disabled": True})
    if not pending:
        send("sendMessage", chat_id=editor_id, text="Черновиков на модерации нет.")


def handle_message(m, editor_id, send=tg, rows=None):
    """Возвращает строку для suggestions.csv или None."""
    chat = m.get("chat") or {}
    if chat.get("type") != "private":
        return None
    text = str(m.get("text") or m.get("caption") or "").strip()
    if not text:
        return None
    if text.startswith("/start"):
        send("sendMessage", chat_id=chat["id"], text=START_TEXT)
        return None
    if text.startswith("/id"):
        send("sendMessage", chat_id=chat["id"], text=f"Ваш chat_id: {chat['id']}")
        return None
    if str(chat.get("id")) == str(editor_id):
        if text.startswith("/drafts"):
            resend_drafts(rows or [], editor_id, send)
        return None  # свои сообщения редактор боту не предлагает
    user = m.get("from") or {}
    url = re.search(r"https?://\S+", text)
    who = "@" + user["username"] if user.get("username") else (user.get("first_name") or "подписчик")
    send("sendMessage", chat_id=editor_id, text=f"Предложение от {who}:\n\n{text}",
         link_preview_options={"is_disabled": True})
    send("sendMessage", chat_id=chat["id"], text=THANKS_TEXT)
    return {
        "received_at": now_msk(),
        # в публичный репозиторий не пишем id и ник подписчика, только хэш
        "user_hash": hashlib.sha256(str(user.get("id", "")).encode()).hexdigest()[:10],
        "text": text[:1000], "url": url.group(0) if url else "", "status": "новое",
    }


def main():
    editor_id = os.environ["TG_EDITOR_CHAT_ID"]
    tg("deleteWebhook")  # getUpdates не работает, если у бота включён вебхук
    offset = load_offset()
    res = tg("getUpdates", offset=offset, timeout=0, allowed_updates=["message", "callback_query"])
    if not res.get("ok"):
        raise SystemExit("getUpdates: " + str(res.get("description")))
    updates = res.get("result", [])
    print(f"апдейтов: {len(updates)}")
    if not updates:
        return

    rows = read_csv("log.csv")
    log_changed, suggestions = False, []
    for u in updates:
        try:
            if "callback_query" in u:
                log_changed |= handle_callback(u["callback_query"], rows, editor_id)
            elif "message" in u:
                s = handle_message(u["message"], editor_id, rows=rows)
                if s:
                    suggestions.append(s)
        except Exception as e:  # одна битая запись не должна ронять очередь
            print("ошибка обработки", u.get("update_id"), type(e).__name__, e)
        offset = u["update_id"] + 1

    if log_changed:
        write_csv("log.csv", rows, LOG_FIELDS)
    if suggestions:
        append_csv("suggestions.csv", suggestions, SUGGESTION_FIELDS)
    save_offset(offset)
    write_stats()
    print(f"лог изменён: {log_changed}, предложений: {len(suggestions)}")


if __name__ == "__main__":
    main()
