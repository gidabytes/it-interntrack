# Installing on Windows to `C:\it-interntrack`

Two routes. The scripted one does the whole thing; the manual one is below it
if you would rather see each step, or if the script hits something.

- **Web interface:** http://localhost:5174
- **API docs:** http://localhost:8000/api/docs

---

## What you need first

| | Version | Where |
|---|---|---|
| Python | 3.11 or newer | [python.org/downloads/windows](https://www.python.org/downloads/windows/) — **tick "Add python.exe to PATH"** |
| Node.js | 18 or newer | [nodejs.org](https://nodejs.org/) — take the LTS build |
| Git | any | [git-scm.com](https://git-scm.com/download/win) — optional, only to clone |

After installing either one, **open a new terminal.** PowerShell reads `PATH`
once at startup, so an existing window will not see them.

Check both:

```powershell
py --version
node --version
```

> **If `python` prints nothing and exits, or opens the Microsoft Store:** that
> is Windows' app-execution alias stub, not Python. Use `py` instead, or turn
> the stub off under **Settings → Apps → Advanced app settings → App execution
> aliases**, switching off `python.exe` and `python3.exe`.

---

## Get the code into `C:\it-interntrack`

**From the zip:** extract it so that `C:\it-interntrack\backend` and
`C:\it-interntrack\frontend` exist. Extracting often creates a nested folder —
if you end up with `C:\it-interntrack\it-interntrack\backend`, move the inner
contents up one level.

**With Git:**

```powershell
cd C:\
git clone https://github.com/gidabytes/it-interntrack.git it-interntrack
```

Confirm the layout:

```powershell
dir C:\it-interntrack
```

You should see `backend`, `frontend`, `docs`, `scripts`, `README.md`.

---

## The scripted route

```powershell
cd C:\it-interntrack
.\scripts\windows\setup.ps1
```

That creates the virtual environment, installs both dependency sets, generates
`.env` with fresh secrets, creates the database, makes an `admin` account and
loads demo data. It takes a few minutes, mostly `npm install`.

> **If PowerShell refuses to run it** — *"running scripts is disabled on this
> system"* — allow it for this window only:
>
> ```powershell
> Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
> ```
>
> That lasts until you close the window and changes nothing permanently. The
> scripts themselves never need an activated virtual environment, so this is
> the only place the policy comes up.

Options:

```powershell
.\scripts\windows\setup.ps1 -AdminPassword 'MyStrongPass123!'   # instead of 'admin'
.\scripts\windows\setup.ps1 -NoDemoData                          # skip demo records
.\scripts\windows\setup.ps1 -SkipFrontend                        # backend only
```

Then start it, in **two separate terminals**:

```powershell
# Terminal 1
cd C:\it-interntrack
.\scripts\windows\start-api.ps1

# Terminal 2
cd C:\it-interntrack
.\scripts\windows\start-web.ps1
```

Open **http://localhost:5174** and skip to [Signing in](#signing-in).

---

## The manual route

### 1. Virtual environment

```powershell
cd C:\it-interntrack\backend
py -m venv .venv
```

**Then use `.venv\Scripts\python.exe` directly rather than activating it.**
Activation needs a relaxed execution policy; calling the interpreter does not,
which avoids the problem entirely:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If you prefer an activated prompt:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

The prompt then shows `(.venv)` and plain `python` works. In `cmd.exe` the
equivalent is `.venv\Scripts\activate.bat`, which needs no policy change.

### 2. Configuration and secrets

```powershell
.\.venv\Scripts\python.exe -m app.cli init-env
```

That writes `.env` with a generated `SECRET_KEY`, `FERNET_KEYS` and
`BLIND_INDEX_KEY`.

> **Do this rather than editing the file by hand.** There is no `openssl` on
> Windows, and Notepad silently saves as `.env.txt` — which the application
> then ignores completely, leaving you with a confusing "encryption is not
> configured" failure.

If you must edit it later, use VS Code or Notepad++, or in Notepad set **Save
as type** to **All Files** and put the name in quotes: `".env"`.

> **The application will not start without `FERNET_KEYS`.** That is
> deliberate: intern email addresses, contact numbers and emergency contacts
> are encrypted at rest, and starting without a key would quietly write them
> in plaintext.
>
> **Back up `C:\it-interntrack\backend\.env` somewhere separate from the
> database.** Lose those keys and those columns are permanently unreadable — a
> database backup on its own will not bring them back.

### 3. Database

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.cli bootstrap --password 'admin'
```

That creates 30 tables, 36 computation settings, the 17 standard shift
templates, and an `admin` account. The web interface forces a password change
at first sign-in, so `admin` is fine to start with.

Optional demo data — 12 interns across 5 teams with six weeks of history:

```powershell
.\.venv\Scripts\python.exe -m app.cli seed-demo
```

Check it:

```powershell
.\.venv\Scripts\python.exe -m app.cli status
```

### 4. Start the API

```powershell
cd C:\it-interntrack\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Leave it running. You should see:

```
IT InternTrack 1.0.0 ready | tz=Asia/Manila | db=sqlite | encryption=on
```

Windows may show a **Windows Defender Firewall** prompt. **Cancel** or allow
private networks only — nothing needs to reach this from outside the machine.

Check: http://localhost:8000/api/health

### 5. Start the web interface

A **second** terminal:

```powershell
cd C:\it-interntrack\frontend
npm install
npm run dev
```

Open **http://localhost:5174**

---

## Signing in

Sign in as **`admin`** with the password from the bootstrap step (`admin` by
default). You will be required to choose a new one of at least 10 characters
before anything else is available — that is why the simple starting password
is acceptable.

With demo data loaded, password `InternTrack#2026` for all of these:

| Username | Role | What they see |
|---|---|---|
| `itmanager` | IT Manager | Both dashboards, all interns, approvals |
| `rsantos` | IT Supervisor | Own interns only, can approve and sign |
| `mlim` | IT Supervisor | A second supervisor, with a visible backlog |
| `jvaldez` | IT Mentor | Read-only, plus exposure logging |
| `ana.cruz` | OJT Intern | Time clock and own records only |

---

## Everyday commands

All from `C:\it-interntrack\backend`:

```powershell
# Why can nobody sign in? Lists accounts, lockouts, password and key checks.
.\.venv\Scripts\python.exe -m app.cli diagnose

# Regain access to an account and clear any lockout.
.\.venv\Scripts\python.exe -m app.cli reset-password admin --password admin

# Mark absences for a date. Safe to re-run; never marks anyone absent before
# their shift has ended.
.\.venv\Scripts\python.exe -m app.cli finalize-day 2026-11-25

# Configuration and row counts
.\.venv\Scripts\python.exe -m app.cli status

# Remove demo data
.\.venv\Scripts\python.exe -m app.cli purge-demo
```

### Scheduling the nightly close

There is no cron on Windows. Use Task Scheduler — `SHIFT-17` ends at 08:00, so
09:00 safely covers every shift from the previous day:

```powershell
$action = New-ScheduledTaskAction `
  -Execute 'C:\it-interntrack\backend\.venv\Scripts\python.exe' `
  -Argument '-m app.cli finalize-day' `
  -WorkingDirectory 'C:\it-interntrack\backend'

$trigger = New-ScheduledTaskTrigger -Daily -At 9:00am

Register-ScheduledTask -TaskName 'InternTrack nightly close' `
  -Action $action -Trigger $trigger -RunLevel Highest
```

Run as a service account rather than your own login, so it keeps working when
you are signed out. Without a date argument `finalize-day` requires one, so
either pass yesterday's date from a small wrapper `.ps1`, or run it against
the current date and accept that the last shift of the day closes the
following morning.

---

## If something goes wrong

**`running scripts is disabled on this system`**

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Or avoid activation entirely and call `.venv\Scripts\python.exe` directly, as
above.

**`python` was not found, or the Microsoft Store opens** — the app-execution
alias stub. Use `py`, or turn the stub off under **Settings → Apps → Advanced
app settings → App execution aliases**.

**`[Errno 10048] ... only one usage of each socket address`** — the port is
taken, usually by an earlier run.

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen |
  Select-Object OwningProcess, @{n='Name';e={(Get-Process -Id $_.OwningProcess).ProcessName}}

Stop-Process -Id <the PID> -Force
# or just use another port:
.\scripts\windows\start-api.ps1 -Port 8001
```

**`Could not import module "app.main"`** — you are in the wrong folder. Run
uvicorn from `C:\it-interntrack\backend`. The startup line confirms it: it
should read `Will watch for changes in these directories: ['C:\\it-interntrack\\backend']`.

**`Field-level encryption is not configured`** — `.env` is missing or has no
`FERNET_KEYS`. Check the file is genuinely called `.env` and not `.env.txt`:

```powershell
cmd /c dir /a C:\it-interntrack\backend\.env*
```

Then `.\.venv\Scripts\python.exe -m app.cli init-env`.

**Sign-in fails** — run the diagnostic:

```powershell
.\.venv\Scripts\python.exe -m app.cli diagnose
```

It distinguishes the four causes: the account does not exist, the password is
not what you think, the account is locked after five failed attempts (15
minutes), or `FERNET_KEYS` no longer matches the data.

**`npm install` fails with permission or path errors** — a path-length limit.
Enable long paths once, as Administrator, then restart:

```powershell
New-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' `
  -Name 'LongPathsEnabled' -Value 1 -PropertyType DWORD -Force
```

`C:\it-interntrack` is short enough that this rarely comes up.

**Blank page at localhost:5174** — check the `npm run dev` terminal for errors
and that the API terminal is still running. Press F12 in the browser; failed
`/api` calls in the console mean the backend is down.

**Antivirus slows `npm install` to a crawl** — excluding
`C:\it-interntrack\frontend\node_modules` from real-time scanning helps a lot.
Your endpoint policy may not allow it.

---

## Switching to PostgreSQL or Supabase

Only the connection string changes.

```powershell
.\.venv\Scripts\python.exe -m pip install "psycopg[binary]"
```

Then edit `DATABASE_URL` in `.env`:

```ini
DATABASE_URL=postgresql+psycopg://interntrack:PASSWORD@dbhost:5432/interntrack
```

Re-run `alembic upgrade head` and `bootstrap` against the new database. See
[docs/SUPABASE.md](docs/SUPABASE.md) — in particular, run migrations against
port **5432**, never the 6543 pooler.

Note that the SQLite path in `.env` is **relative** (`sqlite:///./interntrack.db`),
so it resolves against whatever folder you start from. If you want it fixed,
use forward slashes even on Windows:

```ini
DATABASE_URL=sqlite:///C:/it-interntrack/backend/interntrack.db
```

---

## Before real intern data goes in

- [ ] Admin password changed from `admin` (the application forces this)
- [ ] `backend\.env` backed up **separately** from the database
- [ ] `DEBUG=false` and `REQUIRE_ENCRYPTION=true` in `.env`
- [ ] PostgreSQL rather than SQLite if more than one person uses it at once
- [ ] `purge-demo` run to clear the demo records
- [ ] Real teams, supervisors, shift templates and holidays loaded
- [ ] `finalize-day` scheduled in Task Scheduler
- [ ] Backups scheduled **and a restore tested**

Full production setup is in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), written
for Linux; on Windows Server the equivalents are IIS or nginx for Windows as
the reverse proxy, and NSSM or a Windows Service wrapper in place of systemd.
