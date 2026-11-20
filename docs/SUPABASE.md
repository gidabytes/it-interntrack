# Using Supabase (or any PostgreSQL)

IT InternTrack talks to PostgreSQL through SQLAlchemy, so Supabase is a
**connection-string change** — no code changes, no vendor SDK, nothing to
rewrite. The same build runs on Supabase Cloud, self-hosted Supabase, plain
PostgreSQL, or SQLite.

The full test suite (162 tests) is verified against real PostgreSQL 16 as well
as SQLite, so the models are genuinely dialect-neutral rather than assumed to be.

---

## What this integration is, and is not

**Is:** Supabase as the database.

**Is not:** Supabase Auth, Row Level Security, Realtime or Storage. Those were
considered and deliberately left out, for reasons worth knowing before anyone
revisits the decision:

- **Supabase Auth would replace the JWT/bcrypt login** and, with it, the
  permission layer. The guards that keep the hour ledger trustworthy — an
  intern cannot approve their own attendance, sign their own timesheet, or
  verify their own exposure log — are enforced in the service layer *and* the
  API layer. Re-expressing them as RLS policies is possible but is a security
  rewrite, not an integration.
- **RLS cannot filter or search Fernet-encrypted columns.** Intern email and
  contact numbers are ciphertext in the database; a policy cannot compare them,
  and PostgREST cannot decrypt them. Adopting RLS over this data means giving
  up field-level encryption.
- **Realtime** would be a genuine improvement for the duty board, which
  currently polls. It is additive and can be added later without touching the
  attendance engine.

---

## Choosing an endpoint

Supabase gives a project two connection endpoints, and the difference matters:

| | Port | Pooling | Use for |
|---|---|---|---|
| **Direct / session pooler** | `5432` | Session mode | Migrations, the CLI, normal API serving |
| **Transaction pooler** | `6543` | pgbouncer, transaction mode | Many short-lived connections, serverless |

**Port 6543 breaks server-side prepared statements.** Transaction mode hands a
different backend connection to each statement, so a statement prepared on one
backend fails when executed on another (`prepared statement does not exist`).

The application detects a transaction-mode pooler automatically — by port
`6543` or a `pooler.supabase.co` host — and when it sees one it disables
prepared statements and switches to `NullPool`, because the pooler is already
doing the pooling and a second pool would exhaust the project's connection
limit. You do not have to configure this.

**Run `alembic upgrade head` against port 5432, not 6543.** Migrations use
transactional DDL and advisory locks, which need a session-mode connection.

---

## Supabase Cloud

1. Create the project. Choose the region closest to the DGT site —
   `ap-southeast-1` (Singapore) is usually the lowest latency from the
   Philippines.
2. **Project Settings → Database → Connection string → URI.**
3. Put it in `.env`, changing the scheme to `postgresql+psycopg://`:

```ini
# Direct connection (use this for migrations and normal serving)
DATABASE_URL=postgresql+psycopg://postgres:YOUR_DB_PASSWORD@db.YOURPROJECT.supabase.co:5432/postgres?sslmode=require
```

If you need the transaction pooler for the API instead:

```ini
DATABASE_URL=postgresql+psycopg://postgres.YOURPROJECT:YOUR_DB_PASSWORD@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres?sslmode=require
```

Then:

```bash
cd backend
pip install "psycopg[binary]"
alembic upgrade head
python -m app.cli bootstrap --password 'SetAStrongPasswordHere'
python -m app.cli status          # should report: postgresql (supabase)
```

`sslmode=require` is not optional on Supabase Cloud — the connection crosses
the public internet.

---

## Self-hosted Supabase (on-premises)

Keeps intern PII inside the network, which is what the original specification
asked for.

```bash
git clone --depth 1 https://github.com/supabase/supabase
cd supabase/docker
cp .env.example .env
# Set POSTGRES_PASSWORD, JWT_SECRET, ANON_KEY, SERVICE_ROLE_KEY
docker compose up -d
```

Then point the application at it:

```ini
DATABASE_URL=postgresql+psycopg://postgres:YOUR_POSTGRES_PASSWORD@localhost:5432/postgres
```

No `sslmode` needed on a trusted local network, though it does no harm.

---

## Plain PostgreSQL

If the only thing you wanted from Supabase was managed Postgres, plain
PostgreSQL is one less moving part:

```ini
DATABASE_URL=postgresql+psycopg://interntrack:PASSWORD@dbhost:5432/interntrack
```

See [DEPLOYMENT.md](DEPLOYMENT.md) for the full on-premises setup.

---

## Which to choose

| | Supabase Cloud | Self-hosted Supabase | Plain PostgreSQL |
|---|---|---|---|
| Setup effort | Lowest | Highest | Low |
| Intern PII stays on your network | **No** | Yes | Yes |
| Managed backups | Yes | You run them | You run them |
| Meets "on-premises" as specified | No | Yes | Yes |
| Web SQL editor, dashboard | Yes | Yes | No |

### The compliance point, stated plainly

Intern records hold names, personal email addresses, contact numbers and
emergency contacts — that is personal data about named individuals, many of
them students.

Supabase Cloud places it on infrastructure outside your network and, depending
on region, outside the Philippines. That is a data-residency decision rather
than a technical one, and in a PCI-DSS environment it is the kind of thing that
surfaces in an access review. The original specification asked for
on-premises deployment and said cloud hosting must not be mandatory.

It is a legitimate choice to make deliberately, with the school and whoever
owns data protection. It is not a good choice to make by accident because a
connection string was convenient.

**A reasonable middle path:** evaluate on Supabase Cloud with `seed-demo` data
only — which is synthetic and marked `DEMO DATA` — and decide on self-hosting
before any real intern record is entered.

---

## Encryption keys still matter

Supabase encrypts its storage at rest, but that protects against someone
stealing the disk, not against anyone who can read the database.

`FERNET_KEYS` is what keeps intern email and contact numbers unreadable to
anyone with database access — including, on Supabase Cloud, through the web SQL
editor. **Keep using it.** Without it those columns sit in plaintext in a
console that is one password away.

Equally: a Supabase backup is not restorable without the keys. Back them up in
your key vault, separately from the database.

---

## Migrating existing SQLite data

```bash
# 1. Back up
cp interntrack.db interntrack.db.bak

# 2. Create the schema in PostgreSQL
DATABASE_URL="postgresql+psycopg://..." alembic upgrade head
```

**Do not `pg_dump` a SQLite file, and do not copy rows with a generic tool.**
The encrypted columns hold Fernet tokens that must be moved with the same
`FERNET_KEYS` in place, and `UTCDateTime` columns need timezone-aware values on
the PostgreSQL side. Write a short script that reads through the ORM with the
keys configured and writes through the ORM to the new database, so both are
handled by the same code that produced them.

For a pilot that only contains `DEMO DATA`, it is simpler to migrate nothing:
point at the new database, run `alembic upgrade head`, `bootstrap`, then
`seed-demo` again.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `prepared statement "_pg3_0" does not exist` | Port 6543 without the pooler handling. The app detects this automatically; check your URL actually contains `6543` or `pooler.supabase.co`. |
| `SSL connection has been closed unexpectedly` | Missing `?sslmode=require` on Supabase Cloud. |
| `too many clients already` | A pool on top of the transaction pooler, or several instances sharing a small project. Use port 6543, which switches to `NullPool`. |
| Migrations hang | Running `alembic` against 6543. Use 5432. |
| `password authentication failed` | Pooler usernames are `postgres.PROJECTREF`, not plain `postgres`. |
| `operator does not exist: boolean = integer` | A SQLite-only CHECK constraint. Fixed in the initial migration; if you add one, write bare booleans. |
