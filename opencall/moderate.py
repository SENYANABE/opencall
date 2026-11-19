"""Модерация: забираем нажатия редактора и сообщения подписчиков из Телеграма, публикуем, пишем лог."""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

from lib import DATA, LOG_FIELDS, SUGGESTION_FIELDS, append_csv, now_msk, read_csv, tg, tg_photo, write_csv
from drafts import editor_markup, publish, send_draft
from stats import write_stats

REASONS = {"topic": "не по теме", "data": "ошибка в данных", "org": "сомнительный организатор", "dup": "дубль",
           "weak": "слабый повод", "format": "формат не подошёл"}
STATE = DATA / "state.json"
START_TEXT = ("Это бот ОПЕНКОЛЛ. Пришлите ссылку на конкурс, грант, резиденцию или тендер для дизайнеров. "
              "Редактор проверит условия у организатора и добавит в ленту.")
THANKS_TEXT = "Спасибо, получили. Проверим условия у организатора и, если всё сходится, опубликуем в канале."


SLOTS = ["10:00", "13:00", "19:00"]  # время выхода постов из очереди, МСК


def next_slot(rows, now=None):
    """Ближайший свободный слот после текущего момента: один пост на слот."""
    from datetime import datetime, timedelta
    now = now or datetime.strptime(now_msk(), "%Y-%m-%d %H:%M")
    taken = {r.get("scheduled_at") for r in rows if r.get("status") == "в_очереди"}
    day = now.date()
    for _ in range(60):
        for hm in SLOTS:
            slot = datetime.strptime(f"{day} {hm}", "%Y-%m-%d %H:%M")
            key = slot.strftime("%Y-%m-%d %H:%M")
            if slot > now and key not in taken:
                return key
        day += timedelta(days=1)
    return ""


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


def handle_callback(cq, rows, editor_id, send=tg, send_photo=None):
    """Меняет rows на месте. Возвращает True, если лог изменился."""
    parts = str(cq.get("data", "")).split(":")
    msg = cq.get("message") or {}
    chat_id, message_id = (msg.get("chat") or {}).get("id"), msg.get("message_id")
    if str((cq.get("from") or {}).get("id")) != str(editor_id):
        send("answerCallbackQuery", callback_query_id=cq["id"], text="Модерация доступна только редактору")
        return False
    if len(parts) < 2 or parts[0] not in ("pub", "pubtxt", "rej", "que", "quetxt", "unq"):
        send("answerCallbackQuery", callback_query_id=cq["id"])
        return False

    row = next((r for r in rows if r["id"] == parts[1]), None)
    if not row:
        send("answerCallbackQuery", callback_query_id=cq["id"], text="Карточка не найдена в логе")
        return False
    if parts[0] == "unq":
        if row["status"] != "в_очереди":
            send("answerCallbackQuery", callback_query_id=cq["id"], text="Уже не в очереди: " + row["status"])
            return False
        row.update(status="на_модерации", scheduled_at="", queue_mode="")
        send("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id, reply_markup=editor_markup(row))
        send("answerCallbackQuery", callback_query_id=cq["id"], text="Убрано из очереди")
        return True
    if row["status"] in ("опубликовано", "отклонено"):
        send("answerCallbackQuery", callback_query_id=cq["id"], text="Уже обработано: " + row["status"])
        return False

    if row["status"] == "в_очереди" and parts[0] in ("que", "quetxt"):
        send("answerCallbackQuery", callback_query_id=cq["id"], text="Уже в очереди на " + row["scheduled_at"])
        return False
    row["moderated_at"] = now_msk()
    if parts[0] in ("que", "quetxt"):
        slot = next_slot(rows)
        row.update(status="в_очереди", scheduled_at=slot, queue_mode="text" if parts[0] == "quetxt" else "",
                   editor_chat_id=str(chat_id or ""), editor_message_id=str(message_id or ""), reason="")
        ack = "В очереди: выйдет " + slot
        markup = {"inline_keyboard": [[{"text": "В очереди: " + slot, "callback_data": "noop"}],
                                      [{"text": "Убрать из очереди", "callback_data": "unq:" + row["id"]},
                                       {"text": "Опубликовать сейчас", "callback_data": "pub:" + row["id"]}]]}
    elif parts[0] == "rej":
        reason = REASONS.get(parts[2] if len(parts) > 2 else "", "отклонено редактором")
        row.update(status="отклонено", reason=reason)
        ack, markup = "Отклонено: " + reason, {"inline_keyboard": [[{"text": "Отклонено: " + reason, "callback_data": "noop"}]]}
    else:
        kwargs = {"send": send}
        if send_photo:
            kwargs["send_photo"] = send_photo
        ack, markup = do_publish(row, parts[0] == "pub", **kwargs)
    send("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id, reply_markup=markup)
    send("answerCallbackQuery", callback_query_id=cq["id"], text=ack[:190])
    return True


def do_publish(row, with_image, send=tg, send_photo=None):
    """Публикует карточку, обновляет строку лога. Возвращает (текст ответа, кнопки)."""
    kwargs = {"send": send}
    if send_photo:
        kwargs["send_photo"] = send_photo
    res = publish(row, with_image=with_image, **kwargs)
    if res.get("ok"):
        link = post_link(res["result"])
        row.update(status="опубликовано", reason="", published_at=now_msk(),
                   message_id=str(res["result"]["message_id"]), post_link=link)
        return "Опубликовано", {"inline_keyboard": [[{"text": "Опубликовано: открыть пост", "url": link}]]}
    row.update(status="ошибка_публикации", reason="Телеграм: " + str(res.get("description", "нет ответа")))
    return ("Ошибка публикации: " + str(res.get("description", "")),
            {"inline_keyboard": [[{"text": "Повторить публикацию", "callback_data": "pub:" + row["id"]}]]})


def publish_due(rows, send=tg, send_photo=None, now=None):
    """Выпускает из очереди всё, чьё время пришло. Возвращает число выпущенных."""
    now = now or now_msk()
    due = sorted((r for r in rows if r.get("status") == "в_очереди" and r.get("scheduled_at") and r["scheduled_at"] <= now),
                 key=lambda r: r["scheduled_at"])
    for r in due:
        ack, markup = do_publish(r, r.get("queue_mode") != "text", send=send, send_photo=send_photo)
        if r.get("editor_chat_id") and r.get("editor_message_id"):
            send("editMessageReplyMarkup", chat_id=r["editor_chat_id"], message_id=int(r["editor_message_id"]),
                 reply_markup=markup)
        print("из очереди:", r["id"], r["status"])
    return len(due)


def queue_text(rows):
    q = sorted((r for r in rows if r.get("status") == "в_очереди"), key=lambda r: r["scheduled_at"])
    if not q:
        return "Очередь пуста."
    return "Очередь:\n" + "\n".join(f"{r['scheduled_at']} · {r.get('title', '')[:60]}" for r in q)


def resend_drafts(rows, editor_id, send=tg, send_photo=tg_photo):
    pending = [r for r in rows if r["status"] in ("на_модерации", "ошибка_публикации")]
    for r in pending:
        send_draft(r, chat_id=editor_id, send=send, send_photo=send_photo)
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
        elif text.startswith("/queue"):
            send("sendMessage", chat_id=editor_id, text=queue_text(rows or []))
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


def process(updates, editor_id):
    rows = read_csv("log.csv")
    log_changed, suggestions, offset = False, [], load_offset()
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
    print(f"апдейтов: {len(updates)}, лог изменён: {log_changed}, предложений: {len(suggestions)}")


def git_sync(message):
    """Режим сервера: забрать свежий лог перед обработкой и отправить изменения после."""
    root = str(DATA.parent)
    subprocess.run(["git", "-C", root, "add", "data"], check=False)
    if subprocess.run(["git", "-C", root, "diff", "--cached", "--quiet"]).returncode != 0:
        subprocess.run(["git", "-C", root, "commit", "-qm", message], check=False)
        subprocess.run(["git", "-C", root, "pull", "-q", "--rebase"], check=False)
        subprocess.run(["git", "-C", root, "push", "-q"], check=False)


def main():
    editor_id = os.environ["TG_EDITOR_CHAT_ID"]
    tg("deleteWebhook")  # getUpdates не работает, если у бота включён вебхук
    loop = "--loop" in sys.argv
    while True:
        # долгий опрос на сервере: Телеграм держит запрос до 50 секунд и отдаёт нажатие сразу
        res = tg("getUpdates", offset=load_offset(), timeout=50 if loop else 0,
                 allowed_updates=["message", "callback_query"])
        if not res.get("ok"):
            print("getUpdates:", res.get("description"))
            if not loop:
                raise SystemExit(1)
            time.sleep(15)
            continue
        updates = res.get("result", [])
        if updates:
            if loop:
                subprocess.run(["git", "-C", str(DATA.parent), "pull", "-q", "--rebase"], check=False)
            process(updates, editor_id)
            if loop:
                git_sync("Лог: модерация " + now_msk())
        elif not loop:
            print("апдейтов: 0")
        # очередь: выпускаем посты, чьё время пришло
        rows = read_csv("log.csv")
        if any(r.get("status") == "в_очереди" and r.get("scheduled_at", "z") <= now_msk() for r in rows):
            if loop:
                subprocess.run(["git", "-C", str(DATA.parent), "pull", "-q", "--rebase"], check=False)
                rows = read_csv("log.csv")
            if publish_due(rows):
                write_csv("log.csv", rows, LOG_FIELDS)
                write_stats()
                if loop:
                    git_sync("Лог: публикация из очереди " + now_msk())
        if not loop:
            return


if __name__ == "__main__":
    main()
