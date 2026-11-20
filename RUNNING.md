# How to run IT InternTrack

From a clean machine to a working application. Every command here has been run
end to end and verified.

- **Web interface:** http://localhost:5174
- **API docs:** http://localhost:8000/api/docs

---

## 1. What you need

| | Version | Check with |
|---|---|---|
| Python | 3.11 or newer | `python3 --version` |
| Node.js | 18 or newer | `node --version` |
| npm | 9 or newer | `npm --version` |

A database is optional to start with: SQLite is built into Python and needs no
setup. PostgreSQL or Supabase is for later, and is one connection string away.

On Ubuntu or Lubuntu, if anything is missing:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nodejs npm
```

---

## 2. Get the code

From the zip:

```bash
unzip it-interntrack.zip
cd it-interntrack
```

Or from GitHub:

```bash
git clone https://github.com/gidabytes/it-interntrack.git
cd it-interntrack
```

---

## 3. Set up the backend

```bash
cd backend

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

The prompt should now start with `(.venv)`. **Everything below assumes the
virtual environment is active.** If you open a new terminal, run
`source .venv/bin/activate` again.

---

## 4. Create the configuration

```bash
cp .env.example .env
```

Generate three secrets:

```bash
openssl rand -hex 32                          # -> SECRET_KEY
python -m app.cli generate-key                # -> FERNET_KEYS
python -m app.cli generate-blind-index-key    # -> BLIND_INDEX_KEY
```

Open `.env` and paste each value against the matching name:

```ini
SECRET_KEY=<first command's output>
FERNET_KEYS=<second command's output>
BLIND_INDEX_KEY=<third command's output>
```

> **The application will not start without `FERNET_KEYS`.** That is deliberate:
> intern email addresses, contact numbers and emergency contacts are encrypted
> at rest, and starting without a key would silently write them in plaintext.
>
> **Back these keys up somewhere separate from the database.** Lose them and
> those columns are permanently unreadable — a database backup alone will not
> restore them.

---

## 5. Create the database

```bash
alembic upgrade head
python -m app.cli bootstrap --password 'ChangeThisOnFirstSignIn!'
```

That creates 30 tables, 36 computation settings, the 17 standard shift
templates, and an `admin` account with the password you chose.

Optionally load realistic demo data — 12 interns across 5 teams with six weeks
of attendance history, exposure logs and leave requests:

```bash
python -m app.cli seed-demo
```

Every demo row is marked `DEMO DATA` and can be removed later with
`python -m app.cli purge-demo`. **Do not run `seed-demo` on a real database.**

Confirm it worked:

```bash
python -m app.cli status
```

---

## 6. Start the API

```bash
uvicorn app.main:app --reload
```

Leave this terminal running. You should see:

```
IT InternTrack 1.0.0 ready | tz=Asia/Manila | db=sqlite | encryption=on
```

Check it: http://localhost:8000/api/health

---

## 7. Start the web interface

Open a **second terminal**:

```bash
cd it-interntrack/frontend
npm install        # first time only, takes a minute
npm run dev
```

Open **http://localhost:5174**

The dev server proxies `/api` to the backend on port 8000, so the browser sees
one origin and there is no CORS setup to do.

---

## 8. Sign in

With demo data loaded, password `InternTrack#2026` for all of these:

| Username | Role | What you see |
|---|---|---|
| `itmanager` | IT Manager | Both dashboards, all interns, approvals |
| `rsantos` | IT Supervisor | Only their own interns, can approve and sign |
| `mlim` | IT Supervisor | A second supervisor, with a visible backlog |
| `jvaldez` | IT Mentor | Read-only, plus exposure logging |
| `ana.cruz` | OJT Intern | Time clock and their own records only |

Plus `admin`, with the password you set in step 5 — the only account that sees
**Settings**.

Sign in as different roles to see the access control work: an intern has no
Settings or Duty Board in the navigation at all, and an IT Manager correctly
cannot see Settings either, because that needs `settings.manage`.

**Worth looking at first:** Dashboard, then Duty Board (the live operations
view), then Compliance (who is behind, who will miss their deadline), then
Reports → any report → **XLSX** to download a real Excel file.

---

## Switching to PostgreSQL or Supabase

Only the connection string changes. Nothing in the code.

```bash
pip install "psycopg[binary]"
```

Then edit `DATABASE_URL` in `.env`:

```ini
# Plain PostgreSQL
DATABASE_URL=postgresql+psycopg://interntrack:PASSWORD@dbhost:5432/interntrack

# Supabase Cloud (use port 5432 for migrations)
DATABASE_URL=postgresql+psycopg://postgres:PASSWORD@db.PROJECT.supabase.co:5432/postgres?sslmode=require

# Self-hosted Supabase
DATABASE_URL=postgresql+psycopg://postgres:PASSWORD@localhost:5432/postgres
```

Then re-run step 5 (`alembic upgrade head`, `bootstrap`) against the new
database. See [docs/SUPABASE.md](docs/SUPABASE.md) — in particular, run
migrations against port **5432**, never the 6543 pooler.

---

## Running the tests

```bash
cd backend
PYTHONPATH=. pytest -q                     # all 162
PYTHONPATH=. pytest -m mandatory -v        # the 20 specification scenarios
PYTHONPATH=. pytest -m shift_matrix -v     # all 17 shift templates
PYTHONPATH=. pytest -m security -v         # auth, RBAC, encryption
```

Against PostgreSQL instead of SQLite:

```bash
TEST_DATABASE_URL="postgresql+psycopg://user:pass@localhost:5432/testdb" \
  PYTHONPATH=. pytest -q
```

Frontend:

```bash
cd frontend
npm run typecheck
npm run build
```

---

## Everyday commands

```bash
# Mark absences and flag incomplete records for a date.
# Safe to re-run; never marks anyone absent before their shift has ended.
python -m app.cli finalize-day 2026-11-20

# Configuration and row counts
python -m app.cli status

# Remove demo data
python -m app.cli purge-demo

# Rotate the encryption keys (see docs/DEPLOYMENT.md for the full sequence)
python -m app.cli rotate-encryption
```

Schedule `finalize-day` nightly once you go live. `SHIFT-17` ends at 08:00, so
09:00 safely covers every shift from the previous day:

```cron
0 9 * * * cd /path/to/backend && /path/to/.venv/bin/python -m app.cli finalize-day $(date -d yesterday +\%F)
```

---

## If something goes wrong

**`Address already in use`** — something is already on that port.

```bash
ss -ltnp | grep -E ':8000|:5174'
pkill -f "uvicorn app.main"
# or just use another port:
uvicorn app.main:app --reload --port 8001
```

**`Could not import module "app.main"`** — you are in the wrong directory. Run
uvicorn from `backend/`, not from your home folder. The startup line tells you:
it should read `Will watch for changes in these directories: ['…/backend']`.

**`Field-level encryption is not configured`** — `FERNET_KEYS` is empty in
`.env`. Go back to step 4.

**`Failed to decrypt a stored value`** — the keys in `.env` are not the ones the
data was written with. Restore the original keys. If they are genuinely lost,
the encrypted columns cannot be recovered; for a demo database, delete
`backend/interntrack.db` and redo steps 5–6.

**Sign-in says "Incorrect username or password"** and you are sure it is right
— five failed attempts locks an account for 15 minutes. Wait, or reset it:
`python -m app.cli bootstrap` recreates nothing, so use the admin account and
`POST /api/admin/users/{id}/reset-password`.

**Blank page at localhost:5174** — check the `npm run dev` terminal for errors,
and that the API terminal is still running. The browser console (F12) will show
failed `/api` calls if the backend is down.

**`command not found: alembic` or `uvicorn`** — the virtual environment is not
active. Run `source .venv/bin/activate`.

---

## Before real intern data goes in

- [ ] Change the `admin` password from the bootstrap value
- [ ] `FERNET_KEYS` and `BLIND_INDEX_KEY` backed up **separately** from the database
- [ ] `DEBUG=false` and `REQUIRE_ENCRYPTION=true` in `.env`
- [ ] PostgreSQL rather than SQLite, if more than one person will use it at once
- [ ] `python -m app.cli purge-demo` to clear the demo records
- [ ] Real teams, supervisors, shift templates and holidays loaded
- [ ] `finalize-day` scheduled
- [ ] Backups scheduled **and a restore tested**

Full production setup — nginx, systemd, TLS, backups — is in
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
