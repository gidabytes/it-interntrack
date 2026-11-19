# Deployment — internal IT network

Targets an on-premises Linux or Windows Server with no cloud dependency.
PostgreSQL is recommended; SQLite is acceptable for a single instance.

---

## 1. Prerequisites

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip \
                    postgresql postgresql-contrib nginx
```

Windows Server: install Python 3.11+, PostgreSQL and nginx for Windows, then
follow the same steps using `.venv\Scripts\activate` and a Windows service
wrapper (NSSM) in place of systemd.

---

## 2. Application user and location

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin interntrack
sudo mkdir -p /opt/interntrack
sudo chown interntrack:interntrack /opt/interntrack

sudo -u interntrack git clone https://github.com/gidabytes/it-interntrack.git /opt/interntrack/app
cd /opt/interntrack/app/backend

sudo -u interntrack python3.11 -m venv /opt/interntrack/venv
sudo -u interntrack /opt/interntrack/venv/bin/pip install -r requirements.txt
```

---

## 3. PostgreSQL

```bash
sudo -u postgres psql <<'SQL'
CREATE USER interntrack WITH PASSWORD 'use-a-long-random-password';
CREATE DATABASE interntrack OWNER interntrack ENCODING 'UTF8';
GRANT ALL PRIVILEGES ON DATABASE interntrack TO interntrack;
SQL
```

Restrict access to the application host in `pg_hba.conf`:

```
host    interntrack    interntrack    10.0.0.0/8    scram-sha-256
```

---

## 4. Configuration and secrets

```bash
cd /opt/interntrack/app/backend
sudo -u interntrack cp .env.example .env
sudo chmod 600 .env
sudo chown interntrack:interntrack .env

# Generate the three secrets
openssl rand -hex 32
sudo -u interntrack /opt/interntrack/venv/bin/python -m app.cli generate-key
sudo -u interntrack /opt/interntrack/venv/bin/python -m app.cli generate-blind-index-key
```

Edit `.env`:

```ini
DEBUG=false
DATABASE_URL=postgresql+psycopg://interntrack:PASSWORD@localhost:5432/interntrack
SECRET_KEY=<openssl output>
FERNET_KEYS=<generate-key output>
BLIND_INDEX_KEY=<generate-blind-index-key output>
REQUIRE_ENCRYPTION=true
BUSINESS_TIMEZONE=Asia/Manila
COMPANY_NAME=Your Company
ORGANIZATION_NAME=Information Technology Department
CORS_ORIGINS=["https://interntrack.yourdomain.local"]
```

> ### Back up the encryption keys separately
>
> `FERNET_KEYS` and `BLIND_INDEX_KEY` are the only way to read the encrypted
> columns. Store them in your password manager or key vault — **not** in the
> same place as the database backups. A backup encrypted with keys that were
> stored alongside it protects nobody; a backup whose keys are lost is
> unrecoverable.

---

## 5. Schema and bootstrap

```bash
cd /opt/interntrack/app/backend
sudo -u interntrack /opt/interntrack/venv/bin/alembic upgrade head
sudo -u interntrack /opt/interntrack/venv/bin/python -m app.cli bootstrap \
    --username admin --email it-admin@yourdomain.local \
    --password 'SetAStrongPasswordHere'
sudo -u interntrack /opt/interntrack/venv/bin/python -m app.cli status
```

Sign in once and change the administrator password immediately.

**Do not run `seed-demo` on a production database.**

---

## 6. systemd service

`/etc/systemd/system/interntrack.service`:

```ini
[Unit]
Description=IT InternTrack API
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=interntrack
Group=interntrack
WorkingDirectory=/opt/interntrack/app/backend
EnvironmentFile=/opt/interntrack/app/backend/.env
ExecStart=/opt/interntrack/venv/bin/uvicorn app.main:app \
          --host 127.0.0.1 --port 8000 --workers 4 \
          --proxy-headers --forwarded-allow-ips='127.0.0.1'
Restart=always
RestartSec=5

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/interntrack/app/backend/uploads /opt/interntrack/backups

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now interntrack
sudo systemctl status interntrack
```

Bind to `127.0.0.1` only. nginx is the sole public entry point.

---

## 7. nginx reverse proxy

`/etc/nginx/sites-available/interntrack`:

```nginx
server {
    listen 80;
    server_name interntrack.yourdomain.local;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name interntrack.yourdomain.local;

    ssl_certificate     /etc/ssl/certs/interntrack.crt;
    ssl_certificate_key /etc/ssl/private/interntrack.key;
    ssl_protocols       TLSv1.2 TLSv1.3;

    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;

    client_max_body_size 8m;

    # Throttle sign-in attempts: account lockout stops password guessing on one
    # account, this stops a broad sweep across many.
    limit_req_zone $binary_remote_addr zone=login:10m rate=10r/m;

    location /api/auth/login {
        limit_req zone=login burst=5 nodelay;
        proxy_pass http://127.0.0.1:8000;
        include /etc/nginx/proxy_params;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }

    # Frontend build output, once the UI is built.
    location / {
        root /opt/interntrack/app/frontend/dist;
        try_files $uri $uri/ /index.html;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/interntrack /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

`X-Forwarded-For` is honoured for exactly one proxy hop, so audit-log IP
addresses record the real client rather than nginx.

---

## 8. Scheduled jobs

Finalise each business date after the latest shift has ended. `SHIFT-17` ends at
08:00, so 09:00 safely covers every shift from the previous day:

```bash
sudo crontab -u interntrack -e
```

```cron
# Finalise yesterday's attendance (mark absences, flag incomplete records)
0 9 * * * cd /opt/interntrack/app/backend && \
  /opt/interntrack/venv/bin/python -m app.cli finalize-day $(date -d yesterday +\%F) \
  >> /var/log/interntrack/finalize.log 2>&1
```

Finalisation is idempotent and never marks an intern absent before the shift
has ended and the grace period has elapsed, so running it more than once is
safe.

---

## 9. Backups

`/opt/interntrack/bin/backup.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
BACKUP_DIR=/opt/interntrack/backups
STAMP=$(date +%F_%H%M)
mkdir -p "$BACKUP_DIR"

PGPASSWORD='PASSWORD' pg_dump -h localhost -U interntrack -Fc interntrack \
    > "$BACKUP_DIR/interntrack_$STAMP.dump"

# Keep 30 days
find "$BACKUP_DIR" -name 'interntrack_*.dump' -mtime +30 -delete

# Verify the dump is readable, not merely present.
pg_restore --list "$BACKUP_DIR/interntrack_$STAMP.dump" > /dev/null
echo "$(date -Is) backup ok: interntrack_$STAMP.dump"
```

```cron
30 1 * * * /opt/interntrack/bin/backup.sh >> /var/log/interntrack/backup.log 2>&1
```

**A database backup alone is not a restorable backup.** The encrypted columns
need `FERNET_KEYS`. Record the keys in your key vault and note which key era
each backup belongs to, especially after a rotation.

Test a restore on a non-production host before you need one.

---

## 10. Key rotation

```bash
# 1. Generate a new key and PREPEND it, keeping the old one
/opt/interntrack/venv/bin/python -m app.cli generate-key
#    FERNET_KEYS=<new>,<old>
sudo systemctl restart interntrack

# 2. Re-encrypt everything under the new primary key
/opt/interntrack/venv/bin/python -m app.cli rotate-encryption

# 3. Only now remove <old> from FERNET_KEYS
sudo systemctl restart interntrack
```

Removing the old key before step 2 completes makes data written under it
unreadable. The application raises loudly rather than returning corrupt values,
but the data is gone.

---

## 11. Pre-production checklist

```bash
curl -H "Authorization: Bearer <admin-token>" \
     https://interntrack.yourdomain.local/api/admin/system-status
```

- [ ] `SECRET_KEY` is not the shipped placeholder
- [ ] `FERNET_KEYS` and `BLIND_INDEX_KEY` set, and backed up **separately** from the database
- [ ] `DEBUG=false` and `REQUIRE_ENCRYPTION=true`
- [ ] PostgreSQL, not SQLite
- [ ] `.env` is `chmod 600`, owned by the service account
- [ ] TLS terminating at nginx; the app bound to `127.0.0.1` only
- [ ] Administrator password changed from the bootstrap value
- [ ] All 17 shift templates present
- [ ] No DEMO DATA (`python -m app.cli purge-demo` if any)
- [ ] `finalize-day` scheduled
- [ ] Backups scheduled **and a restore tested**
- [ ] `pg_hba.conf` restricted to the application host
- [ ] Log rotation configured for `/var/log/interntrack/`
- [ ] Real shift templates, teams, supervisors and holidays loaded
- [ ] One team piloted for a full OJT cycle, and the signed timesheets checked against what the schools accept

---

## 12. Upgrading

```bash
cd /opt/interntrack/app
sudo -u interntrack git pull
sudo -u interntrack /opt/interntrack/venv/bin/pip install -r backend/requirements.txt
cd backend
sudo -u interntrack /opt/interntrack/venv/bin/alembic upgrade head
sudo systemctl restart interntrack
```

Back up first. Migrations run forward cleanly; `alembic downgrade -1` reverses
the last one if needed.
