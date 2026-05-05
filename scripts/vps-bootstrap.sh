#!/usr/bin/env bash
set -euo pipefail

# Bootstrap fresh Hetzner VPS (Ubuntu 24.04). Run as root after first SSH.
# Usage: ssh root@<ip> bash < scripts/vps-bootstrap.sh

REPO_URL="${DAVINCI_REPO_URL:-git@github.com:spectrum77/davinci.git}"
DEPLOY_DIR="/opt/davinci"

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get -y upgrade
apt-get install -y ca-certificates curl gnupg ufw fail2ban unattended-upgrades git

# Firewall
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# Docker Engine + Compose v2
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

# Repo
mkdir -p "$DEPLOY_DIR"
if [[ ! -d "$DEPLOY_DIR/.git" ]]; then
  git clone "$REPO_URL" "$DEPLOY_DIR"
fi

# Backups dir + cron
mkdir -p /opt/backups
cat >/etc/cron.d/davinci-pgdump <<'CRON'
0 6 * * * root docker compose -f /opt/davinci/docker-compose.yml -f /opt/davinci/docker-compose.prod.yml exec -T postgres pg_dump -U davinci davinci | gzip > /opt/backups/davinci-$(date +\%F).sql.gz && find /opt/backups -name 'davinci-*.sql.gz' -mtime +14 -delete
CRON

echo "==> next steps:"
echo "    1) cd $DEPLOY_DIR && cp .env.example .env && \$EDITOR .env (set passwords, secrets, domains)"
echo "    2) chmod 600 .env"
echo "    3) docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build"
echo "    4) docker compose exec api alembic upgrade head"
