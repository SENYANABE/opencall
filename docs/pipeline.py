"""Схема пайплайна ОПЕНКОЛЛ с точками контроля. Сборка: python docs/pipeline.py (нужны graphviz и rsvg-convert)."""
import html
import subprocess
from pathlib import Path

ACC, INK, MUT, PALE = "#1F4BFF", "#0A1A44", "#4A5A85", "#DCE4FF"
RED, REDBG, GRN, GRNBG, AMB, AMBBG = "#C8322A", "#FDECEB", "#0E8A4F", "#E3F6EC", "#A86400", "#FFF3DC"
FONT = "Inter"


def esc(s):
    return html.escape(s, quote=False)


def box(name, title, body, who="code", check=None):
    fill, fg, sub = {"code": ("#FFFFFF", INK, MUT), "llm": ("#F1F4FF", INK, MUT), "human": (ACC, "#FFFFFF", "#DCE4FF")}[who]
    tag = {"code": "КОД", "llm": "LLM", "human": "РЕДАКТОР"}[who]
    rows = [f'<TR><TD ALIGN="LEFT"><FONT POINT-SIZE="12" COLOR="{sub}"><B>{tag}</B></FONT></TD></TR>',
            f'<TR><TD ALIGN="LEFT"><FONT POINT-SIZE="19" COLOR="{fg}"><B>{esc(title)}</B></FONT></TD></TR>']
    for line in body.split("\n"):
        rows.append(f'<TR><TD ALIGN="LEFT"><FONT POINT-SIZE="14" COLOR="{sub}">{esc(line)}</FONT></TD></TR>')
    if check:
        rows.append(f'<TR><TD ALIGN="LEFT" BGCOLOR="{AMBBG}" CELLPADDING="6"><FONT POINT-SIZE="13" COLOR="{AMB}"><B>Контроль: </B>{esc(check)}</FONT></TD></TR>')
    label = f'<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="3" CELLPADDING="2">{"".join(rows)}</TABLE>>'
    return f'{name} [label={label}, shape=box, style="rounded,filled", fillcolor="{fill}", color="{ACC}", penwidth=2, margin="0.25,0.18"];'


def sink(name, title, body, color, bg):
    label = (f'<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="2"><TR><TD ALIGN="LEFT"><FONT POINT-SIZE="16" COLOR="{color}"><B>{esc(title)}</B></FONT></TD></TR>'
             f'<TR><TD ALIGN="LEFT"><FONT POINT-SIZE="13" COLOR="{color}">{esc(body)}</FONT></TD></TR></TABLE>>')
    return f'{name} [label={label}, shape=box, style="rounded,filled", fillcolor="{bg}", color="{color}", margin="0.2,0.12"];'


nodes = [
    box("trig", "Триггеры", "Расписание GitHub Actions: 07:00 и 15:00 МСК\nПятница 10:00: дедлайны недели · 1-е число: опрос\nПроверка кнопок бота: каждые 10 минут"),
    box("src", "1. Сбор", "Белый список sources.csv: сайты организаторов (A),\nагрегаторы (B), СМИ и ТГ-каналы (C)", "code",
        "новые источники добавляет только редактор"),
    box("ext", "2. Извлечение", "Текст страницы → анонсы по JSON-схеме:\nназвание, ссылка, дедлайн, взнос, приз, доступ из РФ", "llm",
        "факты только из текста, нет данных = пустое поле"),
    box("sel", "3. Отбор", "Дубли по ссылке и названию, сортировка по дедлайну,\nлимит 10 карточек за запуск", "code",
        "нет ссылки · нет или прошёл дедлайн · < 3 дней · не про дизайн · недоступно из РФ"),
    box("ver", "4. Сверка с организатором", "Открываем страницу организатора, сравниваем\nдедлайн, взнос, приз, доступ из РФ", "llm",
        "расхождение → флаг редактору, верим организатору"),
    box("card", "5. Карточка и обложка", "LLM пишет текст по ToV, код собирает пост по шаблону.\nОбложка 4:5 по референсам стиля ДЗ2", "llm",
        "стоп-лист, без «!» и эмодзи, ≤ 600 знаков, флаг платного взноса"),
    box("mod", "6. Модерация", "Черновик с обложкой и флагами приходит в бот:\nОпубликовать · Без обложки · 5 причин отказа", "human",
        "без нажатия редактора пост не выходит"),
    box("pub", "7. Публикация", "Пост в канал, ссылка и статус в лог", "code", "ошибка Телеграма → статус и кнопка «Повторить»"),
    box("an", "8. Статистика", "STATS.md после каждого запуска: собрано, брак с причинами,\nопубликовано · подписчики, просмотры, ERR", "code"),
    box("sug", "Предложения подписчиков", "Ссылка боту → лог (без ников) → пересылка редактору", "code",
        "источник попадает в белый список только после проверки"),
    sink("rej1", "отбраковано_авто", "причина в data/log.csv", RED, REDBG),
    sink("rej2", "отбраковано_авто", "стоп-лист, дедлайн по организатору", RED, REDBG),
    sink("rej3", "отклонено", "причина из кнопки редактора", RED, REDBG),
    sink("ok", "опубликовано", "ссылка на пост в логе", GRN, GRNBG),
]
edges = ["trig -> src", "src -> ext", "ext -> sel", "sel -> ver [label=<<FONT POINT-SIZE=\"12\" COLOR=\"#4A5A85\">прошло</FONT>>]",
         "sel -> rej1 [style=dashed, color=\"#C8322A\"]", "ver -> card", "card -> rej2 [style=dashed, color=\"#C8322A\"]",
         "card -> mod", "mod -> pub", "mod -> rej3 [style=dashed, color=\"#C8322A\"]", "pub -> ok", "ok -> an",
         "sug -> mod [style=dotted]"]

dot = f'''digraph G {{
  graph [rankdir=TB, bgcolor="#EEF3FF", pad="0.6", nodesep="0.5", ranksep="0.45", fontname="{FONT}",
         label=<<FONT POINT-SIZE="30" COLOR="{ACC}"><B>ОПЕНКОЛЛ · пайплайн и точки контроля качества</B></FONT><BR/><FONT POINT-SIZE="15" COLOR="{MUT}">github.com/SENYANABE/opencall · белый блок: код · голубой: LLM · синий: редактор · жёлтая плашка: контроль</FONT>>,
         labelloc=t];
  node [fontname="{FONT}"]; edge [color="{ACC}", penwidth=2, arrowsize=0.9, fontname="{FONT}"];
  {chr(10).join(nodes)}
  {chr(10).join(edges)}
  {{rank=same; sel; rej1}} {{rank=same; card; rej2}} {{rank=same; mod; rej3; sug}}
}}'''

here = Path(__file__).parent
(here / "pipeline.dot").write_text(dot, encoding="utf-8")
subprocess.run(["dot", "-Tsvg", str(here / "pipeline.dot"), "-o", str(here / "pipeline.svg")], check=True)
subprocess.run(["rsvg-convert", "-z", "2", str(here / "pipeline.svg"), "-o", str(here / "pipeline.png")], check=True)
print("ok")
