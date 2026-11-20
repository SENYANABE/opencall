"""Тесты логики без сети: python -m pytest tests или python tests/test_pipeline.py"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "opencall"))
import lib  # noqa: E402

lib.DATA = Path(tempfile.mkdtemp())
lib.ROOT = lib.DATA.parent
import covers  # noqa: E402
import drafts  # noqa: E402
import collect  # noqa: E402
import formats  # noqa: E402
import moderate  # noqa: E402
import stats  # noqa: E402

covers.COVERS = lib.DATA / "covers"
drafts.ROOT = lib.ROOT
moderate.DATA = lib.DATA
moderate.STATE = lib.DATA / "state.json"
stats.DATA = lib.DATA
os.environ.update(TG_CHANNEL_ID="@opencall_design", TG_EDITOR_CHAT_ID="1")


def plus(n):
    return (datetime.now(lib.MSK) + timedelta(days=n)).strftime("%Y-%m-%d")


HTML = ('<html><head><script>var a=1</script></head><body><div>OPEN CALL &laquo;Город&raquo;</div>'
        '<p><a href="/post/1">Подробности</a> <a href="https://org.ru/apply?utm_source=tg">форма</a></p>'
        + "<p>текст</p>" * 60 + "</body></html>")


def op(title, url, days, **kw):
    d = dict(title=title, url=url, organizer="", type="конкурс", deadline=plus(days), fee="", prize="",
             rf_access="да", specializations=["графдизайн"], is_design_relevant=True, summary_source="")
    d.update(kw)
    return d


def test_html():
    t = lib.html_to_text(HTML, "https://t.me/s/chan")
    assert "[https://t.me/post/1]" in t and "«Город»" in t and "var a" not in t


def test_collect_select():
    srcs = [{"name": "A", "url": "https://a.ru", "level": "c"}, {"name": "B", "url": "https://b.ru", "level": "B"},
            {"name": "C", "url": "https://down", "level": "A"}]
    fetch = lambda u: ("", "ConnectionError") if "down" in u else (HTML, "")
    items = [op("Конкурс плакатов «Город»", "https://org.ru/apply?utm_source=tg", 30),
             op("Уже в логе", "https://old.ru/call", 40),
             op("Скоро", "https://a.ru/1", 1),
             op("Вакансия", "https://a.ru/2", 20, is_design_relevant=False),
             op("Конкурс плакатов «Город»", "https://org.ru/apply/", 30)]
    extract = lambda src, text: ({"items": items}, "") if src["name"] == "A" else (None, "rate limit")
    found, failed = collect.collect(srcs, extract=extract, fetch=fetch)
    assert len(found) == 5 and len(failed) == 2
    to_gen, rej, dup, cut = collect.select(found, [{"url": "https://www.old.ru/call/", "title": "x"}], "run_1")
    assert dup == 2 and len(to_gen) == 1 and cut == 0
    assert sorted(r["reason"] for r in rej) == ["до дедлайна меньше 3 дней", "не про дизайн"]
    assert to_gen[0]["source_level"] == "C"
    return to_gen[0]


GEN = dict(page_deadline="", page_fee="", page_prize="100 000 ₽", page_rf_access="да", mismatch_notes="",
           title_ru="Конкурс плакатов «Город»", summary_ru="Музей ищет плакаты о городе. Нужно прислать до трёх работ.",
           fee_ru="бесплатно", prize_ru="100 000 ₽", segment="набрать кейсы")


def test_card():
    row = test_collect_select()
    card = collect.make_card(row, fetch=lambda u: (HTML, ""), generate=lambda r, p: dict(GEN, page_deadline=plus(28)))
    assert card["status"] == "на_модерации", card
    assert "дедлайн расходится" in card["flags"]
    assert card["post_text"].startswith("<b>") and "#конкурс" in card["post_text"]
    print(card["post_text"])
    bad = collect.build_card(row, True, dict(GEN, summary_ru="Успейте подать!"))
    assert bad["status"] == "отбраковано_авто" and "стоп-лист" in bad["reason"]
    assert collect.build_card(row, True, None)["reason"] == "ошибка генерации"
    paid = collect.build_card(row, True, dict(GEN, page_fee="100 €"))
    assert "платное участие" in paid["flags"]
    free = collect.build_card(row, True, dict(GEN, page_fee="0"))
    assert "платное" not in free["flags"]
    for b in sum(drafts.editor_markup(card)["inline_keyboard"], []):
        assert len(b["callback_data"].encode()) <= 64
    assert card["kind"] == "card" and card["cover_motif"] == ""
    return card


def test_moderation():
    card = test_card()
    rows = [dict(card)]
    calls = []

    def send(method, **p):
        calls.append(method)
        if method == "sendMessage":
            return {"ok": True, "result": {"message_id": 7, "chat": {"id": -100123, "username": "opencall_design"}}}
        return {"ok": True}

    cq = {"id": "q", "data": "pub:" + card["id"], "from": {"id": 1}, "message": {"message_id": 5, "chat": {"id": 1}}}
    assert moderate.handle_callback(cq, rows, "1", send)
    assert rows[0]["status"] == "опубликовано" and rows[0]["post_link"] == "https://t.me/opencall_design/7"
    assert not moderate.handle_callback(cq, rows, "1", send)  # повторное нажатие
    stranger = dict(cq, **{"from": {"id": 999}})
    rows2 = [dict(card)]
    assert not moderate.handle_callback(stranger, rows2, "1", send) and rows2[0]["status"] == "на_модерации"
    rej = dict(cq, data=f"rej:{card['id']}:org")
    assert moderate.handle_callback(rej, rows2, "1", send) and rows2[0]["reason"] == "сомнительный организатор"
    fail = lambda m, **p: {"ok": False, "description": "Bad Request"} if m == "sendMessage" else {"ok": True}
    rows3 = [dict(card)]
    moderate.handle_callback(cq, rows3, "1", fail)
    assert rows3[0]["status"] == "ошибка_публикации"

    s = moderate.handle_message({"chat": {"id": 9, "type": "private"}, "from": {"id": 9, "username": "ivan"},
                                 "text": "Вот конкурс https://x.ru/c"}, "1", send)
    assert s["url"] == "https://x.ru/c" and "ivan" not in str(s)
    assert moderate.handle_message({"chat": {"id": 9, "type": "private"}, "text": "/start"}, "1", send) is None
    sent = []
    moderate.handle_message({"chat": {"id": 1, "type": "private"}, "text": "/drafts"}, "1",
                            lambda m, **p: sent.append(p.get("text", "")) or {"ok": True}, rows=[dict(card)])
    assert sent and sent[0].startswith("<b>Карточка · черновик")
    t = collect.build_card(dict(card, status=""), True, dict(GEN, title_ru="Резиденция — для художников"))
    assert "—" not in t["post_text"] and "Резиденция: для художников" in t["post_text"]
    assert moderate.handle_message({"chat": {"id": -5, "type": "group"}, "text": "hi"}, "1", send) is None

    lib.write_csv("log.csv", rows + rows2 + rows3, lib.LOG_FIELDS)
    lib.write_csv("runs.csv", [{"run_id": "r", "run_at": "2026-11-17 07:00", "sources_total": 3, "sources_failed": 2,
                                "found": 5, "duplicates": 2, "auto_rejected": 2, "sent_to_moderation": 1}], lib.RUN_FIELDS)
    stats.write_stats()
    md = (lib.DATA / "STATS.md").read_text()
    assert "| Опубликовано | 1 |" in md and "сомнительный организатор" in md


def test_covers_and_formats():
    import io
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (1024, 1536), "blue").save(buf, "PNG")
    out = Image.open(io.BytesIO(covers.crop_4x5(buf.getvalue())))
    assert out.size == (1024, 1280)
    card = test_card()
    # обложка: фейковый генератор кладёт файл
    def fake(row, motif=""):
        covers.COVERS.mkdir(parents=True, exist_ok=True)
        p = covers.COVERS / f"{row['id']}.jpg"; p.write_bytes(covers.crop_4x5(buf.getvalue()))
        return str(p.relative_to(lib.DATA.parent)), ""
    collect.add_covers([card], make=fake)
    assert drafts.image_path(card)
    assert any(b["callback_data"].startswith("pubtxt:") for b in sum(drafts.editor_markup(card)["inline_keyboard"], []))
    bad = collect.add_covers([dict(card, image="")], make=lambda r, m="": ("", "обложка: нужна верификация"))[0]
    assert "верификация" in bad["flags"]
    # публикация с фото и без
    calls = []
    sp = lambda chat, path, **kw: calls.append(("photo", kw.get("caption", ""))) or {"ok": True, "result": {"message_id": 1, "chat": {"username": "c"}}}
    sm = lambda m, **kw: calls.append((m, kw.get("text", ""))) or {"ok": True, "result": {"message_id": 2, "chat": {"username": "c"}}}
    drafts.publish(card, True, sm, sp); drafts.publish(card, False, sm, sp)
    assert calls[0][0] == "photo" and calls[1][0] == "sendMessage"
    long_post = card["post_text"].replace(card["post_text"].split("\n\n")[2], "Очень длинное предложение. " * 60)
    cap = drafts.fit_caption(long_post)
    assert cap and drafts.visible_len(cap) <= 1024 and "Подать заявку" in cap
    # рубрики
    rows = [dict(card, deadline=plus(5)), dict(card, id="x2", deadline=plus(60))]
    d = formats.build_digest(rows)
    assert d and d["kind"] == "digest" and card_title_ok(d) and "x2" not in d["post_text"]
    p = formats.build_poll()
    calls.clear(); drafts.publish(p, True, sm, sp)
    assert calls[0][0] == "sendPoll"
    assert formats.build_digest([dict(card, deadline=plus(40))]) is None


def test_audience():
    import audience
    html = ('<div class="tgme_widget_message_wrap"><div data-post="opencall_design/3"><span class="tgme_widget_message_views">1.2K</span></div></div>'
            '<div class="tgme_widget_message_wrap"><div data-post="opencall_design/4"><span class="tgme_widget_message_views">35</span></div></div>')
    assert audience.parse_channel_page(html) == {3: 1200, 4: 35}
    row = audience.snapshot(fetch=lambda u: (html, ""), send=lambda m, **k: {"ok": True, "result": 100})
    assert row["subscribers"] == 100 and row["avg_views"] == 618 and row["err_percent"] == 618.0
    stats.write_stats()
    assert "## Аудитория" in (lib.DATA / "STATS.md").read_text()


def test_follow_and_ru_priority():
    hub = '<a href="/media/design/konkursy-v-dekabre/">Конкурсы и опен-коллы в декабре</a> <a href="/media/design/kak-risovat/">Как рисовать</a> <a href="https://other.ru/konkurs">Конкурс чужой</a>'
    pages = {"https://skillbox.ru/media/design/": hub,
             "https://skillbox.ru/media/design/konkursy-v-dekabre/": "<p>" + "Подборка конкурсов. " * 20 + "</p>"}
    seen = []
    def fetch(u):
        seen.append(u)
        return pages.get(u, ""), ""
    def extract(src, text):
        return {"items": [{"title": "Конкурс " + src["url"][-12:], "url": src["url"]}]}, ""
    src = {"name": "Skillbox", "url": "https://skillbox.ru/media/design/", "follow": "да"}
    pages[src["url"]] = hub + "<p>" + "текст хаба " * 20 + "</p>"
    found, failed = collect.collect([src], extract=extract, fetch=fetch)
    assert seen == ["https://skillbox.ru/media/design/", "https://skillbox.ru/media/design/konkursy-v-dekabre/"], seen
    assert len(found) == 2 and not failed
    # российские вперёд, даже если дедлайн позже
    assert collect.is_ru({"title": "Конкурс плаката"}) and not collect.is_ru({"title": "Villa Lofoten AIR", "organizer": "Lofoten"})
    S = {"name": "s", "level": "B"}
    ops = [(S, {"title": "Foreign Residency", "url": "https://a.org/1", "deadline": "2026-12-01", "is_design_relevant": True, "rf_access": "не указано"}),
           (S, {"title": "Конкурс постера", "url": "https://b.ru/2", "deadline": "2026-12-20", "is_design_relevant": True, "rf_access": "да"})]
    passing, _, _, _ = collect.select(ops, [], "r")
    assert passing[0]["title"] == "Конкурс постера"


def test_queue():
    from datetime import datetime
    card = dict(test_card(), status="на_модерации")
    other = dict(card, id="oc2")
    rows = [card, other]
    calls = []
    def send(m, **p):
        calls.append((m, p))
        return {"ok": True, "result": {"message_id": 9, "chat": {"username": "c"}}}
    # слоты: в 11:30 ближайший 13:00, следующий 19:00, потом завтра 10:00
    now = datetime(2026, 11, 19, 11, 30)
    assert moderate.next_slot(rows, now) == "2026-11-19 13:00"
    cq = lambda data, mid=5: {"id": "q", "data": data, "from": {"id": 1}, "message": {"message_id": mid, "chat": {"id": 1}}}
    import moderate as M
    orig = M.next_slot
    M.next_slot = lambda rows, now_=None: orig(rows, now)
    try:
        assert moderate.handle_callback(cq("que:" + card["id"]), rows, "1", send)
        assert card["status"] == "в_очереди" and card["scheduled_at"] == "2026-11-19 13:00"
        assert moderate.handle_callback(cq("quetxt:oc2", 6), rows, "1", send)
        assert other["scheduled_at"] == "2026-11-19 19:00" and other["queue_mode"] == "text"
        assert not moderate.handle_callback(cq("que:" + card["id"]), rows, "1", send)  # уже в очереди
    finally:
        M.next_slot = orig
    # до 13:00 ничего не выходит, в 13:00 выходит первая
    assert moderate.publish_due(rows, send=send, send_photo=lambda *a, **k: {"ok": True, "result": {"message_id": 9, "chat": {"username": "c"}}}, now="2026-11-19 12:59") == 0
    n = moderate.publish_due(rows, send=send, send_photo=lambda *a, **k: {"ok": True, "result": {"message_id": 9, "chat": {"username": "c"}}}, now="2026-11-19 13:00")
    assert n == 1 and card["status"] == "опубликовано" and other["status"] == "в_очереди"
    assert any(m == "editMessageReplyMarkup" and p.get("message_id") == 5 for m, p in calls)
    # убрать из очереди
    assert moderate.handle_callback(cq("unq:oc2", 6), rows, "1", send) and other["status"] == "на_модерации"
    assert "Очередь пуста" in moderate.queue_text(rows)
    assert any(b["callback_data"].startswith("que:") for b in sum(drafts.editor_markup(other)["inline_keyboard"], []))


def card_title_ok(d):
    return "Конкурс плакатов" in d["post_text"] and "Дедлайны" in d["post_text"]


if __name__ == "__main__":
    test_follow_and_ru_priority()
    test_queue()
    test_audience()
    test_covers_and_formats()
    test_html()
    test_moderation()
    print("ВСЕ ТЕСТЫ ПРОШЛИ")
