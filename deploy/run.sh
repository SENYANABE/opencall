#!/usr/bin/env bash
# Запуск шага пайплайна на сервере: свежий код и лог из GitHub -> шаг -> изменения обратно в GitHub.
# Пример: deploy/run.sh collect · FORMAT=digest deploy/run.sh formats
set -uo pipefail
cd /opt/opencall
exec 9>/tmp/opencall.lock
flock 9
git pull -q --rebase
set -a; . ./.env; set +a
venv/bin/python "opencall/$1.py"
git add data
if ! git diff --cached --quiet; then
  git commit -qm "Лог: $1 $(date '+%Y-%m-%d %H:%M')"
  git pull -q --rebase && git push -q
fi
