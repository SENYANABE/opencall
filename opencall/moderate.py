"""Модерация: забираем нажатия редактора и сообщения подписчиков из Телеграма, публикуем, пишем лог."""
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time

from lib import DATA, LOG_FIELDS, SUGGESTION_FIELDS, append_csv, days_until, is_iso, now_msk, read_csv, tg, tg_photo, write_csv
from drafts import editor_markup, publish, send_draft
from editorial import CONFIG
from stats import write_stats

REASONS = {"topic": "не по теме", "data": "ошибка в данных", "org": "сомнительный организатор", "dup": "дубль",
           "weak": "слабый повод", "format": "формат не подошёл"}
STATE = DATA / "state.json"
START_TEXT = ("Это бот ОПЕНКОЛЛ. Пришлите ссылку на конкурс, грант, резиденцию или тендер для дизайнеров. "
              "Редактор проверит условия у организатора и добавит в ленту.")
THANKS_TEXT = "Спасибо, получили. Проверим условия у организатора и, если всё сходится, опубликуем в канале."


SLOTS = ["10:00", "13:00", "19:00"]  # время выхода постов из очереди, МСК


def next_slot(rows, now=None, preferred=""):
    """Ближайший свободный слот после текущего момента: один пост на слот."""
    from datetime import datetime, timedelta
    now = now or datetime.strptime(now_msk(), "%Y-%m-%d %H:%M")
    taken = {r.get("scheduled_at") for r in rows if r.get("status") == "в_очереди"}
    if preferred > now.strftime("%Y-%m-%d %H:%M") and preferred not in taken:
        return preferred
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


def handle_callback(cq, rows, editor_id, send=tg, send_photo=None, checkpoint=None):
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
    if row["status"] in ("опубликовано", "отклонено", "отправляется", "проверить_отправку"):
        send("answerCallbackQuery", callback_query_id=cq["id"], text="Уже обработано: " + row["status"])
        return False

    if row["status"] == "в_очереди" and parts[0] in ("que", "quetxt"):
        send("answerCallbackQuery", callback_query_id=cq["id"], text="Уже в очереди на " + row["scheduled_at"])
        return False
    row["moderated_at"] = now_msk()
    if parts[0] in ("que", "quetxt"):
        slot = next_slot(rows, preferred=row["release_at"]) if row.get("release_at") else next_slot(rows)
        if not slot:
            send("answerCallbackQuery", callback_query_id=cq["id"], text="Нет свободных слотов")
            return False
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
        ack, markup = do_publish(row, parts[0] == "pub", checkpoint=checkpoint, **kwargs)
    send("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id, reply_markup=markup)
    send("answerCallbackQuery", callback_query_id=cq["id"], text=ack[:190])
    return True


def do_publish(row, with_image, send=tg, send_photo=None, checkpoint=None):
    """Записывает попытку до запроса к Telegram, чтобы перезапуск не продублировал пост."""
    kind = row.get("kind") or "card"
    if not CONFIG["formats"].get(kind, {}).get("enabled", True):
        row.update(status="на_модерации", scheduled_at="", reason="рубрика отключена")
        return "Рубрика отключена", editor_markup(row)
    deadline = row.get("deadline")
    expires = row.get("content_expires_on")
    if row.get("cancelled_at") or (is_iso(deadline) and days_until(deadline) < 0) or (is_iso(expires) and days_until(expires) < 0):
        row.update(status="отклонено", reason="срок вышел или возможность отменена")
        return "Материал устарел", {"inline_keyboard": []}
    if os.environ.get("OPENCALL_DRY_RUN") == "1":
        return "Пробный режим: публикация отключена", editor_markup(row)
    kwargs = {"send": send}
    if send_photo:
        kwargs["send_photo"] = send_photo
    prior = row["status"]
    row.update(status="отправляется", reason="проверить исход отправки при перезапуске")
    if checkpoint:
        try:
            checkpoint()
        except OSError:
            row.update(status=prior, reason="не удалось сохранить состояние перед отправкой")
            raise
    res = publish(row, with_image=with_image, **kwargs)
    if res.get("ok"):
        link = post_link(res["result"])
        row.update(status="опубликовано", reason="", published_at=now_msk(),
                   message_id=str(res["result"]["message_id"]), post_link=link)
        if checkpoint:
            checkpoint()
        return "Опубликовано", {"inline_keyboard": [[{"text": "Опубликовано: открыть пост", "url": link}]]}
    description = str(res.get("description", "нет ответа"))
    if res.get("delivery_uncertain"):
        row.update(status="проверить_отправку", reason=description)
        if checkpoint:
            checkpoint()
        return "Проверь канал: Telegram не подтвердил отправку", {"inline_keyboard": []}
    row.update(status="ошибка_публикации", reason="Телеграм: " + description)
    if checkpoint:
        checkpoint()
    return ("Ошибка публикации: " + description,
            {"inline_keyboard": [[{"text": "Повторить публикацию", "callback_data": "pub:" + row["id"]}]]})


def publish_due(rows, send=tg, send_photo=None, now=None, checkpoint=None):
    """Выпускает из очереди всё, чьё время пришло. Возвращает число выпущенных."""
    now = now or now_msk()
    due = sorted((r for r in rows if r.get("status") == "в_очереди" and r.get("scheduled_at") and r["scheduled_at"] <= now),
                 key=lambda r: r["scheduled_at"])
    for r in due:
        ack, markup = do_publish(r, r.get("queue_mode") != "text", send=send, send_photo=send_photo, checkpoint=checkpoint)
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
    def save_log():
        write_csv("log.csv", rows, LOG_FIELDS)

    log_changed, suggestions, offset = False, [], load_offset()
    for u in updates:
        try:
            if "callback_query" in u:
                log_changed |= handle_callback(u["callback_query"], rows, editor_id, checkpoint=save_log)
            elif "message" in u:
                message = u["message"]
                chat = message.get("chat", {})
                command = (message.get("text") or "").split()
                if chat.get("type") == "private" and str(chat.get("id")) == str(editor_id) and command[:1] == ["/cancel"]:
                    card = next((r for r in rows if r["id"] in command[1:2] and (r.get("kind") or "card") == "card"), None)
                    if card:
                        card["cancelled_at"] = now_msk()
                        log_changed = True
                    tg("sendMessage", chat_id=editor_id, text="Отмена отмечена в журнале" if card else "Укажите ID карточки из журнала")
                else:
                    suggestion = handle_message(message, editor_id, rows=rows)
                    if suggestion:
                        suggestions.append(suggestion)
        except Exception as e:  # одна битая запись не должна ронять очередь
            print("ошибка обработки", u.get("update_id"), type(e).__name__, e)
        offset = u["update_id"] + 1
    if log_changed:
        save_log()
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
    if os.environ.get("OPENCALL_DRY_RUN") == "1":
        print("Пробный режим: бот модерации отключён.")
        return
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
        # Серверный сбор, рубрики и модерация записывают один журнал.
        lock_path = os.environ.get("OPENCALL_LOCK_PATH", "/tmp/opencall.lock")
        with open(lock_path, "a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if loop and (updates or any(r.get("status") == "в_очереди" for r in read_csv("log.csv"))):
                subprocess.run(["git", "-C", str(DATA.parent), "pull", "-q", "--rebase"], check=True)
            if updates:
                process(updates, editor_id)
                if loop:
                    git_sync("Лог: модерация " + now_msk())
            rows = read_csv("log.csv")
            if publish_due(rows, checkpoint=lambda: write_csv("log.csv", rows, LOG_FIELDS)):
                write_csv("log.csv", rows, LOG_FIELDS)
                write_stats()
                if loop:
                    git_sync("Лог: публикация из очереди " + now_msk())
        if not loop:
            return


if __name__ == "__main__":
    main()
