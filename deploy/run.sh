#!/usr/bin/env bash
# Запуск шага пайплайна на сервере: свежий код и лог из GitHub -> шаг -> изменения обратно в GitHub.
# Пример: deploy/run.sh collect · FORMAT=digest deploy/run.sh formats
set -uo pipefail
cd /opt/opencall
exec 9>"${OPENCALL_LOCK_PATH:-/tmp/opencall.lock}"
flock 9
git pull -q --rebase || exit $?
set -a; . ./.env; set +a
step=$1
shift
venv/bin/python "opencall/$step.py" "$@"
result=$?
git add data
if ! git diff --cached --quiet; then
  git commit -qm "Лог: $step $(date '+%Y-%m-%d %H:%M')"
  git pull -q --rebase && git push -q
fi
exit "$result"
