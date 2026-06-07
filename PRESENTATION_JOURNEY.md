# Treeat — From Local Prototype to Live, Persistent Web App
### The deployment journey, mapped to the Infrared.city course (Day 1 → Day 2)

> **Treeat** is an urban tree-planting thermal-comfort tool for Vienna (Leopoldstadt).
> You draw an area on a map, and it simulates outdoor heat (UTCI) using the
> **Infrared.city SDK**, recommends where to plant trees, and finds the coolest
> walking route — then **remembers** every analysis you run.

---

## 0. The one-slide summary

| The prof's checklist | What we did | Status |
|---|---|---|
| Split frontend / backend | React (Vercel) ⇄ FastAPI (Railway) | ✅ |
| Keep the API key on the backend | `INFRARED_API_KEY` lives only on Railway | ✅ |
| Deploy both halves to the cloud | Vercel + Railway, auto-deploy from GitHub | ✅ |
| Connect them with environment variables | `VITE_API_URL`, `ALLOWED_ORIGINS` | ✅ |
| **Day-2 challenge: data survives a refresh** | Neon Postgres + SQLAlchemy | ✅ |

**Stack the prof recommended:** Vercel + Render + Neon.
**Stack we used:** Vercel + **Railway** + Neon. *(Railway and Render are equivalent — both run the backend container.)*

---

## 1. Where we started (the prototype)

Before deployment, Treeat only ran on one laptop:

- **Backend** — FastAPI (`main.py`): endpoints for `/api/analyze`, `/api/cool-route`,
  tree species, suppliers, and a PDF planting plan. It calls the **Infrared SDK**
  to run UTCI (thermal comfort) simulations.
- **Frontend** — React 18 + Vite + MapLibre GL: an interactive map where you draw a
  polygon, plus accordion panels for wind, tree planting, and the cool route.
- **At this starting point: no internet presence, no database.** Results lived in
  memory and vanished when the server stopped. *(The cloud deployment comes in
  Section 3, and the Neon Postgres database — the "memory" — is added in Section 5.
  That's the journey.)*

> **Presentation framing:** "It worked on my machine. The course was about turning
> *'works on my machine'* into *'works for anyone, anywhere, and remembers what it did.'*"

---

## 2. The core principle the prof taught: split + hide the key

The single most important lesson: **a web app is two programs, not one.**

```
┌────────────────────┐         HTTPS          ┌────────────────────┐
│   FRONTEND (React) │  ───────────────────▶  │  BACKEND (FastAPI) │
│   runs in browser  │   "analyze this area"  │  runs on a server  │
│   PUBLIC           │  ◀───────────────────  │  PRIVATE           │
│   Hosted: Vercel   │      results + map     │  Hosted: Railway   │
└────────────────────┘                        └─────────┬──────────┘
                                                         │ secret API key
                                                         ▼
                                              ┌────────────────────┐
                                              │  Infrared.city SDK │
                                              └────────────────────┘
```

**Why split?** Anything in the browser is visible to the user (View Source).
So the **Infrared API key can never go in the frontend** — it would be stolen
instantly. The key lives only on the backend, which the public never sees.

**What we changed to make this real:**
- Every one of the frontend's 11 API calls was rewritten from a hardcoded
  `localhost` address to `${VITE_API_URL}` — an **environment variable** set in
  Vercel that points to the Railway backend.
- The backend's CORS setting was changed from "allow everyone" (`*`) to an
  `ALLOWED_ORIGINS` environment variable, so only *our* Vercel site can call it.

> **Slide takeaway:** *Frontend = public face. Backend = locked back office.
> They talk through environment variables, never hardcoded secrets.*

---

## 3. Deploying to the cloud

| Piece | Host | What it deploys from |
|---|---|---|
| Frontend | **Vercel** | GitHub repo, auto-builds the React app |
| Backend | **Railway** | GitHub fork `rimchoufani/Treeat`, runs `uvicorn main:app` |

**Config that made it work:**
- A `railway.toml` telling Railway the backend lives in `treeroute/backend` and
  starts with `uvicorn main:app`.
- `requirements.txt` pinned to the exact library versions (so the cloud installs
  the same packages my laptop had).
- **Repo hygiene:** added a `.gitignore` and removed `node_modules` + logs from
  git — the tracked file count dropped from **3,517 → 69**. The real `.env`
  (with the secret key) was never committed.

> **Slide takeaway:** *Push to GitHub → Vercel and Railway rebuild automatically.
> Deployment becomes "git push."*

---

## 4. The problems we hit (and how we solved them)

This is the most honest — and most impressive — part of the story. Real
deployment is debugging.

### 🔴 Problem 1 — "It's deployed but no data comes through"
- **Symptom:** Live site loaded, but analyses failed silently.
- **Cause:** The original Infrared API key had **expired** (`403 SUBSCRIPTION_INACTIVE`).
- **Fix:** The professor issued a fresh key. The key had to be updated in
  **Railway's Variables** (the backend), **not Vercel** — proving the lesson that
  the secret lives on the backend.
- **Gotcha learned:** changing an environment variable doesn't take effect until
  you **redeploy**. We added a small masked "key fingerprint" debug check to
  confirm *which* key the live server was actually holding.

### 🔴 Problem 2 — Cool-route feature: "must have scikit-learn installed"
- **Cause:** The routing library (OSMnx) needs **scikit-learn** to find the nearest
  street to a point. It was on my laptop but not listed for the cloud to install.
- **Fix:** Added `scikit-learn` to `requirements.txt`.

### 🔴 Problem 3 — Cool-route then hung forever at 75%
- **Cause:** A newer version of OSMnx (2.1.0) **changed how you ask for a map
  area** — the old way of passing the bounding box was silently invalid, so it
  retried downloading map data endlessly.
- **Fix:** Switched both call sites to `graph_from_polygon()` — the same reliable
  call the analysis flow already used. Verified live (e.g. a 1,429 m cool route).

> **Slide takeaway:** *"Works on my machine" fails in the cloud because the cloud
> is a clean machine — every dependency must be declared, and library versions
> matter.*

---

## 5. The Day-2 challenge: make it remember (persistence)

The prof's Day-2 slides set one final bar: **your data must survive a page refresh.**
Treeat failed this — results lived in memory and disappeared on refresh or redeploy.
This was the last box to tick.

### The principle: separate *compute* from *storage*
A server can restart at any time (a redeploy, a crash, free-tier sleep). So
anything you want to keep can't live inside the running program — it has to go in
a **database** that outlives the server.

### What we built
- A new **`db.py`** persistence layer using **SQLAlchemy** (a single `analyses`
  table).
- It reads a `DATABASE_URL` environment variable:
  - In production → **Neon Postgres** (a cloud database).
  - On my laptop → falls back to a local SQLite file automatically (zero setup).
- Every completed analysis is **auto-saved**. New endpoints:
  - `GET /api/saved` — list past analyses
  - `GET /api/saved/{id}` — reload a full analysis (redraws the map, re-enables
    the budget slider and PDF)
  - `DELETE /api/saved/{id}` — remove one
- Frontend got a new **"💾 Saved Analyses"** panel that loads your history when the
  page opens and refreshes after each new run.

### "Storage by shape" — a design choice from the slides
The big result blob (the UTCI grid, heatmap images, route geometry) goes in a
flexible **JSON column**, while small summary numbers (tree count, average
temperature) get their own columns so the list view stays fast and light.

### The bug that taught the most
Saves were **failing silently**. The cause: the UTCI temperature grid contains
**NaN** ("not a number") cells for places with no data — and databases reject NaN.
- **Fix:** a `_clean()` function recursively converts every NaN/infinity to `null`
  before saving, plus a finite-number check on the summary fields.

### Proof it works
The live diagnostic returned:
```json
{ "backend": "postgres", "write_ok": true, "error": null }
```
And an analysis saved under one deployment **survived a full redeploy** and still
appeared in the list. ✅ Data now survives refresh *and* redeploy.

> **Slide takeaway:** *The server forgets. The database remembers. Persistence =
> moving the data out of the program and into a place that outlives it.*

---

## 6. The final architecture

```
   ┌──────────────────────────────┐
   │  USER (browser)              │
   └──────────────┬───────────────┘
                  │  VITE_API_URL
                  ▼
   ┌──────────────────────────────┐
   │  FRONTEND — React + MapLibre │   Hosted on VERCEL  (public)
   │  draw area · view heatmap    │
   │  · saved analyses panel      │
   └──────────────┬───────────────┘
                  │  HTTPS  (ALLOWED_ORIGINS allow-list)
                  ▼
   ┌──────────────────────────────┐
   │  BACKEND — FastAPI           │   Hosted on RAILWAY  (private)
   │  /api/analyze · /cool-route  │
   │  INFRARED_API_KEY (secret)   │
   └────────┬─────────────┬───────┘
            │             │  DATABASE_URL
            ▼             ▼
   ┌─────────────┐  ┌──────────────────┐
   │ Infrared    │  │ NEON Postgres    │  ← remembers every analysis
   │ SDK (UTCI)  │  │ (analyses table) │
   └─────────────┘  └──────────────────┘
```

**Live URLs**
- Frontend: `https://treeat.vercel.app`
- Backend: `https://treeat-production.up.railway.app`

---

## 7. Suggested slide order (for your deck)

1. **Title** — Treeat: cooling cities with data, one tree at a time.
2. **The problem** — heat in cities; what UTCI is; the Infrared SDK.
3. **Demo** — draw an area → heatmap → tree plan → cool route → saved history.
4. **"It only worked on my laptop."** — the gap the course closed.
5. **Lesson 1: split + hide the key** — the two-program diagram.
6. **Lesson 2: deploy to the cloud** — Vercel + Railway, "git push to ship."
7. **The real story: debugging** — expired key, missing scikit-learn, OSMnx change.
8. **Lesson 3: make it remember** — Neon Postgres, the NaN bug, proof it persists.
9. **Final architecture** — the full diagram.
10. **What I learned** — a web app is frontend + backend + database, glued by
    environment variables; secrets stay on the server; the cloud is a clean
    machine; the database outlives the server.

---

## 8. Glossary (for questions / backup slides)

- **UTCI** — Universal Thermal Climate Index: how hot it *feels* outdoors.
- **Frontend / backend** — the public browser app vs. the private server.
- **Environment variable** — a setting stored in the host's dashboard, not in the
  code (e.g. the API key, the database address). Keeps secrets out of the repo.
- **CORS / `ALLOWED_ORIGINS`** — the backend's guest list of which sites may call it.
- **SQLAlchemy** — Python library for talking to a database.
- **Neon Postgres** — a cloud-hosted Postgres database (the "memory").
- **SQLite** — a tiny file-based database used as the local fallback.
- **OSMnx** — library that pulls street maps from OpenStreetMap for routing.
- **NaN** — "not a number"; empty grid cells that databases reject until cleaned.
