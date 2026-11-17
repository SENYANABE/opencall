"""Аудитория: подписчики (Bot API) и просмотры постов (публичная веб-лента канала t.me/s)."""
import os
import re

from lib import append_csv, fetch_page, now_msk, read_csv, tg, today_msk, write_csv

AUDIENCE_FIELDS = ["date", "subscribers", "posts_tracked", "total_views", "avg_views", "err_percent"]
VIEWS_FIELDS = ["post", "post_link", "views", "updated_at"]


def parse_views(text):
    """'1.2K' -> 1200."""
    t = text.strip().upper().replace(",", ".")
    mult = 1000 if t.endswith("K") else 1_000_000 if t.endswith("M") else 1
    try:
        return int(float(t.rstrip("KM")) * mult)
    except ValueError:
        return 0


def parse_channel_page(html):
    """Возвращает {номер_поста: просмотры} из t.me/s/<канал>."""
    out = {}
    for block in re.split(r'(?=<div class="tgme_widget_message_wrap)', html):
        post = re.search(r'data-post="[^"/]+/(\d+)"', block)
        views = re.search(r'tgme_widget_message_views">([^<]+)<', block)
        if post and views:
            out[int(post.group(1))] = parse_views(views.group(1))
    return out


def channel_username():
    ch = os.environ.get("TG_CHANNEL_ID", "")
    return ch[1:] if ch.startswith("@") else ""


def snapshot(fetch=fetch_page, send=tg):
    """Пишет снимок аудитории за сегодня (одна строка в день, последняя перезаписывает)."""
    username = channel_username()
    subs = send("getChatMemberCount", chat_id=os.environ["TG_CHANNEL_ID"])
    subscribers = subs.get("result", 0) if subs.get("ok") else 0
    views = {}
    if username:
        html, err = fetch(f"https://t.me/s/{username}")
        if html:
            views = parse_channel_page(html)
    write_csv("post_views.csv", [
        {"post": n, "post_link": f"https://t.me/{username}/{n}", "views": v, "updated_at": now_msk()}
        for n, v in sorted(views.items())], VIEWS_FIELDS)
    total = sum(views.values())
    avg = round(total / len(views)) if views else 0
    row = {"date": today_msk(), "subscribers": subscribers, "posts_tracked": len(views), "total_views": total,
           "avg_views": avg, "err_percent": round(100 * avg / subscribers, 1) if subscribers else 0}
    rows = [r for r in read_csv("audience.csv") if r["date"] != row["date"]] + [row]
    write_csv("audience.csv", rows, AUDIENCE_FIELDS)
    return row
