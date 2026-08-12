#!/usr/bin/env bash
# Деплой на сервер: backend синхронизируется как есть, фронтенд собирается
# локально (на сервере 1 ГБ RAM — сборка там невозможна) и уезжает статикой.
set -euo pipefail

SERVER="${SC_SERVER:-root@83.166.244.157}"
APP_DIR=/opt/shorts-cutter

cd "$(dirname "$0")"

echo "==> backend"
rsync -az --delete \
  --exclude '__pycache__' --exclude '*.pyc' \
  server/app/ "$SERVER:$APP_DIR/app/"

if [ -d web/dist ]; then
  echo "==> frontend"
  rsync -az --delete web/dist/ "$SERVER:$APP_DIR/web/"
fi

echo "==> systemd"
rsync -az deploy/systemd/ "$SERVER:/etc/systemd/system/"
ssh "$SERVER" 'systemctl daemon-reload && systemctl restart shorts-cutter-api shorts-cutter-worker && sleep 2 && systemctl is-active shorts-cutter-api shorts-cutter-worker'

echo "==> health"
curl -fsS --max-time 15 http://${SERVER#*@}/api/health && echo
echo "OK"
