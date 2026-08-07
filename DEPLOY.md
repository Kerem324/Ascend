# 🚀 Deploy JARVIS to Railway + install on iPhone

A copy-paste checklist to get the dashboard online, persistent, and on your phone.

## 1 · Deploy

1. Go to **[railway.app](https://railway.app)** → **Log in with GitHub**.
2. **New Project → Deploy from GitHub repo → `Kerem324/Ascend`**.
3. Choose the branch to deploy (`main` once the PR is merged, or
   `claude/social-media-dashboard-hn35qe`).
4. Railway reads `requirements.txt` + `Procfile` (`gunicorn app:app`) and builds.
   No build config needed.

## 2 · Get a public URL

- Open the service → **Settings → Networking → Generate Domain**.
- You'll get something like `https://ascend-production.up.railway.app`. That's
  the link you open on any device.

## 3 · Make your data survive redeploys (persistent storage)

Railway's disk is wiped on every deploy, so add a **Volume** for the SQLite file:

1. Service → **Variables → New Variable**: `DB_PATH` = `/data/data.db`
2. Service → **Volumes → New Volume**, mount path: `/data`
3. Redeploy. The app auto-creates the DB on the volume and keeps it across
   deploys. (No code change needed — `DB_PATH` is already env-driven.)

> Prefer Postgres? Not necessary at this scale — a volume + SQLite is plenty for
> a personal dashboard. Ask and I'll add a Postgres adapter if you outgrow it.

## 4 · Optional environment variables

Add these under **Variables** only for what you want on:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Turns the Idea Engine from data-driven → Claude-generated |
| `SYNC_INTERVAL_MINUTES` | e.g. `60` → auto-syncs your channels + competitors hourly |
| `YOUTUBE_API_KEY`, `YOUTUBE_CHANNEL_ID` | Live YouTube data (see `.env.example`) |
| `IG_ACCESS_TOKEN`, `IG_USER_ID` | Live Instagram data |
| `TIKTOK_ACCESS_TOKEN` | Live TikTok data |

## 5 · Go live with your real channels (optional)

Copy `channels.example.json` → `channels.json`, fill in your brand + competitors
(names + platform IDs), commit, and push. On next deploy the app runs in **live
mode** (no demo data) and fills everything from sync.

## 6 · Install on your iPhone 📱

1. Open your Railway URL in **Safari**.
2. Tap **Share** → **Add to Home Screen** → **Add**.
3. It installs as **JARVIS** with the diamond app icon, launches full-screen (no
   Safari bars), works offline for the last-loaded data, and respects the notch.

Same steps work on Android (Chrome → *Install app*).
