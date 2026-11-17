"""Тесты логики без сети: python -m pytest tests или python tests/test_pipeline.py"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "opencall"))
import lib  # noqa: E402

lib.DATA = Path(tempfile.mkdtemp())
import collect  # noqa: E402
import moderate  # noqa: E402
import stats  # noqa: E402

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
    text, markup = collect.editor_message(card)
    for b in sum(markup["inline_keyboard"], []):
        assert len(b["callback_data"].encode()) <= 64
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
    assert moderate.handle_message({"chat": {"id": -5, "type": "group"}, "text": "hi"}, "1", send) is None

    lib.write_csv("log.csv", rows + rows2 + rows3, lib.LOG_FIELDS)
    lib.write_csv("runs.csv", [{"run_id": "r", "run_at": "2026-11-17 07:00", "sources_total": 3, "sources_failed": 2,
                                "found": 5, "duplicates": 2, "auto_rejected": 2, "sent_to_moderation": 1}], lib.RUN_FIELDS)
    stats.write_stats()
    md = (lib.DATA / "STATS.md").read_text()
    assert "| Опубликовано | 1 |" in md and "сомнительный организатор" in md


if __name__ == "__main__":
    test_html()
    test_moderation()
    print("ВСЕ ТЕСТЫ ПРОШЛИ")
