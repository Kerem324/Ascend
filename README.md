# 📈 Creator Dashboard

A visual analytics cockpit for a personal brand's social content across
**YouTube, Instagram and TikTok**. See what you posted, how it performed,
which hooks land, and what your competitors are doing — all in one place.

## What's inside

| View | What it shows |
|------|---------------|
| **Overview** | KPI tiles (views, followers, avg engagement, top post), a views-over-time trend, a views-by-platform breakdown, and your top-performing posts. |
| **My Content** | Every short / reel / video you posted in the window, with views, engagement rate and retention. Filter by platform. |
| **Hook Lab** | The "hook aim" tool — compares average performance **across platforms** and ranks your **opening hooks** by avg views, engagement and retention. |
| **Competitors** | Your *concurrent channels*: their recent posts, what **blew up** (≥ 2.5× that channel's median views), and the hooks behind their breakout content. |

Pick a **7 / 30 / 90-day** window in the top-right; toggle **light/dark** in the sidebar.

## Run it

```bash
pip install -r requirements.txt
python app.py            # http://localhost:5000
# or, like on Railway:
gunicorn app:app
```

The app ships with realistic **demo data** (deterministic seed) so it works
immediately. Delete `data.db` to reseed.

## Use your own numbers

**1 · Manually**
- **Add a post** button in the sidebar → entry form.
- Or `POST /api/posts` with JSON:
  ```json
  {"platform":"youtube","type":"short","title":"…","hook":"…",
   "views":12000,"likes":900,"comments":40,"shares":80,"saves":150,"retention":58.2}
  ```

**2 · Live API sync** (`ingest.py`)

Pull your real posts + stats straight from the platforms into the same table.
Each platform is optional and switches on only when its env vars are set.

```bash
cp .env.example .env          # fill in the platforms you want
set -a; source .env; set +a
python ingest.py              # one-off sync (great for cron)
```

…or click **Sync now** in the sidebar (`POST /api/sync`) to sync on demand.
Re-syncing updates existing posts in place (deduped by the platform's post id).

| Platform | Credentials needed | Metrics it returns |
|----------|--------------------|--------------------|
| **YouTube** | `YOUTUBE_API_KEY`, `YOUTUBE_CHANNEL_ID` (Data API v3 key) | views, likes, comments |
| **Instagram** | `IG_ACCESS_TOKEN`, `IG_USER_ID` (Graph API, Business/Creator acct) | plays/reach, likes, comments, saves, shares |
| **TikTok** | `TIKTOK_ACCESS_TOKEN` (Display API OAuth token) | views, likes, comments, shares |

See `.env.example` for where to get each credential.

**3 · Go fully live** (your real channels + competitors)

Copy `channels.example.json` → `channels.json` and list your brand + the
competitors you track, with their platform ids:

```json
{
  "own":         {"name":"Your Brand","yt_channel_id":"UC…","ig_username":"you"},
  "competitors": [{"name":"Rival","yt_channel_id":"UC…","ig_username":"rival"}]
}
```

When `channels.json` exists the app runs in **live mode**: no demo data — every
number is filled by sync. (Delete `data.db` after editing so it re-seeds.)
Competitor data uses **public** APIs only:

- **YouTube** — any public channel's views/likes/comments (just the API key).
- **Instagram** — public posts via **Business Discovery** (uses *your* token):
  followers, likes, comments (views aren't exposed for other accounts → 0).
- **TikTok** — no public competitor API, so TikTok competitors stay manual.

## Automation — set it and forget it

Sync on a schedule instead of clicking the button:

```bash
SYNC_INTERVAL_MINUTES=60   # full sync (you + competitors) every hour; 0 = off
```

A background scheduler then refreshes everything on that cadence; the sidebar
shows **"Auto-sync every N min · last …"**. Run a single web worker
(`gunicorn app:app` defaults to 1) so only one scheduler is active.

**Real retention (YouTube).** Average-view-% comes from the YouTube *Analytics*
API, which needs an OAuth token (not just the API key). Set it up once:

```bash
# OAuth client type "TVs and Limited Input devices" in Google Cloud Console
export YOUTUBE_OAUTH_CLIENT_ID=... YOUTUBE_OAUTH_CLIENT_SECRET=...
python auth_youtube.py          # opens a device-flow URL + code
# paste the printed YOUTUBE_OAUTH_REFRESH_TOKEN into .env
```

After that, every sync fills in true retention per video (the status shows
`retention ✓`). It's fully optional — leave the OAuth vars blank and retention
stays 0. Instagram exposes only average watch *time* (not a %) and TikTok's
public Display API exposes no retention at all, so those stay 0 and can be
edited in per post.

## Data model

`channels` (own + competitors) → `posts` (platform, type, title, hook,
published_at, views, likes, comments, shares, saves, retention). SQLite,
created on first boot in `data.db`.
