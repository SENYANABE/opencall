"""Статистика выпуска: data/STATS.md пересобирается после каждого запуска."""
from collections import Counter

from lib import DATA, now_msk, read_csv


def write_stats():
    log, runs, sugg = read_csv("log.csv"), read_csv("runs.csv"), read_csv("suggestions.csv")
    st = Counter(r["status"] for r in log)
    total = len(log)
    auto, pub, rej = st["отбраковано_авто"], st["опубликовано"], st["отклонено"]

    def n(field):
        return sum(int(r.get(field) or 0) for r in runs)

    def pct(a, b):
        return f"{round(100 * a / b)}%" if b else "нет данных"

    reasons = Counter(r["reason"] for r in log if r["status"] in ("отбраковано_авто", "отклонено") and r["reason"])
    sources = Counter(r["source_name"] for r in log)
    published_by_source = Counter(r["source_name"] for r in log if r["status"] == "опубликовано")

    lines = [
        "# ОПЕНКОЛЛ · статистика выпуска", "",
        f"Обновлено: {now_msk()} МСК. Файл пересобирается автоматически после каждого запуска пайплайна.", "",
        "## Производство", "",
        "| Показатель | Значение |", "|---|---|",
        f"| Запусков сбора | {len(runs)} |",
        f"| Сбоев источников (сумма по запускам) | {n('sources_failed')} |",
        f"| Найдено анонсов | {n('found')} |",
        f"| Отсеяно как дубли | {n('duplicates')} |",
        f"| Новых материалов в логе | {total} |",
        f"| Отбраковано автоматикой | {auto} |",
        f"| Отправлено на модерацию | {total - auto} |",
        f"| Ждут решения редактора | {st['на_модерации']} |",
        f"| Опубликовано | {pub} |",
        f"| Отклонено редактором | {rej} |",
        f"| Ошибки публикации | {st['ошибка_публикации']} |",
        f"| Доля отбраковки (автоматика + редактор) | {pct(auto + rej, total)} |",
        f"| Доля одобренных редактором | {pct(pub, pub + rej)} |",
        f"| Предложений от подписчиков | {len(sugg)} |",
        "", "## Причины отбраковки", "",
        "| Причина | Кол-во |", "|---|---|",
        *[f"| {k} | {v} |" for k, v in reasons.most_common()],
        "", "## Источники", "",
        "| Источник | Материалов | Опубликовано |", "|---|---|---|",
        *[f"| {k} | {v} | {published_by_source[k]} |" for k, v in sources.most_common()],
        "", "## Последние запуски", "",
        "| Запуск | Найдено | Дубли | Брак | На модерацию | Сбои |", "|---|---|---|---|---|---|",
        *[f"| {r['run_at']} | {r['found']} | {r['duplicates']} | {int(r['auto_rejected'] or 0) + int(r.get('rejected_after_check') or 0)} "
          f"| {r.get('sent_to_moderation', '')} | {r['sources_failed']} |" for r in runs[-14:][::-1]],
        "",
    ]
    (DATA / "STATS.md").write_text("\n".join(lines), encoding="utf-8")
