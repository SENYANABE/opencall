#!/usr/bin/env bash
# Установка ОПЕНКОЛЛ на сервер (Ubuntu/Debian). Запуск от root:
#   curl -fsSL https://raw.githubusercontent.com/SENYANABE/opencall/main/deploy/install.sh | bash
set -euo pipefail
DIR=/opt/opencall
echo "== 1/6 Пакеты"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git python3-venv cron >/dev/null
timedatectl set-timezone Europe/Moscow || true

echo "== 2/6 Код"
[ -d "$DIR/.git" ] || git clone -q https://github.com/SENYANABE/opencall.git "$DIR"
cd "$DIR"
python3 -m venv venv
venv/bin/pip install -q -r requirements.txt
git config user.name "opencall-bot"
git config user.email "opencall-bot@users.noreply.github.com"
git config pull.rebase true

echo "== 3/6 Ключи (ввод не отображается)"
if [ ! -f .env ]; then
  read -rsp "OPENAI_API_KEY: " OPENAI_API_KEY </dev/tty; echo
  read -rsp "TG_BOT_TOKEN: " TG_BOT_TOKEN </dev/tty; echo
  read -rp  "TG_EDITOR_CHAT_ID (число): " TG_EDITOR_CHAT_ID </dev/tty
  read -rp  "TG_CHANNEL_ID (например @opencall_design): " TG_CHANNEL_ID </dev/tty
  umask 077
  cat > .env <<ENV
OPENAI_API_KEY=$OPENAI_API_KEY
TG_BOT_TOKEN=$TG_BOT_TOKEN
TG_EDITOR_CHAT_ID=$TG_EDITOR_CHAT_ID
TG_CHANNEL_ID=$TG_CHANNEL_ID
ENV
fi

echo "== 4/6 Доступ к GitHub на запись"
KEY=/root/.ssh/opencall_deploy
mkdir -p /root/.ssh
[ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N "" -f "$KEY" -C "opencall-server"
ssh-keyscan -q github.com >> /root/.ssh/known_hosts 2>/dev/null
git config core.sshCommand "ssh -i $KEY -o IdentitiesOnly=yes"
git remote set-url origin git@github.com:SENYANABE/opencall.git
echo
echo "Добавь этот ключ в GitHub: репозиторий opencall -> Settings -> Deploy keys -> Add deploy key,"
echo "Title: server, вставь строку ниже и поставь галочку Allow write access:"
echo
cat "$KEY.pub"
echo
read -rp "Когда добавишь, нажми Enter... " _ </dev/tty
git push --dry-run -q && echo "Запись в GitHub работает" || { echo "Нет доступа на запись: проверь ключ и галочку Allow write access, потом запусти установку ещё раз"; exit 1; }

echo "== 5/6 Расписание"
chmod +x deploy/*.sh
cat > /etc/cron.d/opencall <<CRON
SHELL=/bin/bash
# сбор 07:00 и 15:00, дедлайны по пятницам 10:00, опрос 1-го числа 11:00 (время сервера = Москва)
0 7,15 * * * root $DIR/deploy/run.sh collect >> /var/log/opencall.log 2>&1
0 10 * * 5 root FORMAT=digest $DIR/deploy/run.sh formats >> /var/log/opencall.log 2>&1
0 11 1 * * root FORMAT=poll $DIR/deploy/run.sh formats >> /var/log/opencall.log 2>&1
CRON
systemctl restart cron

echo "== 6/6 Бот"
cat > /etc/systemd/system/opencall-bot.service <<UNIT
[Unit]
Description=OPENCALL moderation bot
After=network-online.target

[Service]
ExecStart=$DIR/deploy/bot.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now opencall-bot
sleep 3
systemctl is-active opencall-bot && echo "Бот запущен"
echo
echo "Готово. Логи бота: journalctl -u opencall-bot -f   Логи сбора: tail -f /var/log/opencall.log"
echo "Ручной сбор сейчас: $DIR/deploy/run.sh collect"
