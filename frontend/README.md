# IT InternTrack — frontend

React + TypeScript + Vite + Tailwind, talking to the FastAPI backend.

## Running it

The API must be running first (see `../backend`).

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**.

The dev server proxies `/api` to `http://127.0.0.1:8000`, so the browser sees a
single origin and no CORS configuration is needed while developing. Point it
somewhere else with:

```bash
VITE_API_TARGET=http://127.0.0.1:8001 npm run dev
```

## Building for production

```bash
npm run build      # outputs to dist/
npm run preview    # serve the build locally
```

Serve `dist/` from nginx and proxy `/api` to the backend — see
`../docs/DEPLOYMENT.md`, which already includes the nginx block.

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Dev server with hot reload |
| `npm run build` | Typecheck then production build |
| `npm run typecheck` | Types only, no build |
| `npm run preview` | Serve the built output |

## Layout

```
src/
  api/
    client.ts      fetch wrapper: bearer token, error normalising, downloads
    types.ts       response shapes, matching the OpenAPI schema
  components/
    Shell.tsx      sidebar, top bar, role-aware navigation
    ui.tsx         KpiCard, Table, Badge, ProgressBar, Spinner, ErrorNote
  hooks/
    useAuth.tsx    session, permissions, automatic sign-out on 401
    useTheme.tsx   dark and light mode
  lib/
    format.ts      Asia/Manila time rendering, H:MM, percentages
    status.ts      colour mapping for every status vocabulary
  pages/           one file per screen
```

## Things worth knowing before changing it

**Permissions drive navigation.** `Shell.tsx` filters links by permission code,
not by role name, which matches how the API guards its endpoints. An IT Manager
legitimately cannot see Settings — they do not hold `settings.manage`.

**A 401 signs the user out globally.** `client.ts` notifies the auth context on
any 401, so an expired token drops to the sign-in screen rather than leaving a
half-broken UI.

**Business dates are not display dates.** An overnight session belongs to the
date its shift started. At 02:00 the duty board shows yesterday's business date
for the interns on duty, and that is correct. Do not "fix" it by localising the
timestamp.

**Rendered and creditable hours are different columns on purpose.** Attendance
shows both. Do not collapse them: rendered is what the intern worked, creditable
is what counts toward OJT after clipping and any daily ceiling.

**`innerText` reflects CSS.** Labels styled `uppercase` come back uppercased,
which matters if you write assertions against page text.

## Verified

Driven end to end in a real browser against the live API on PostgreSQL: sign-in,
dashboard with charts, duty board across midnight, compliance, intern list and
detail, attendance with a live approval crediting OJT hours, exposure matrix,
all 17 shift templates, report preview, settings, theme toggle, and role
scoping for IT Manager and intern accounts.
