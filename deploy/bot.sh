#!/usr/bin/env bash
# Бот модерации: долгий опрос Телеграма, нажатия обрабатываются сразу.
cd /opt/opencall
set -a; . ./.env; set +a
exec venv/bin/python opencall/moderate.py --loop
