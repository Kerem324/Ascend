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

- **Add a post** button in the sidebar → manual entry form.
- Or `POST /api/posts` with JSON:
  ```json
  {"platform":"youtube","type":"short","title":"…","hook":"…",
   "views":12000,"likes":900,"comments":40,"shares":80,"saves":150,"retention":58.2}
  ```
- To pull live numbers automatically, ingest from the YouTube Data API /
  Instagram Graph API / TikTok API into the same `posts` schema (see `app.py`).

## Data model

`channels` (own + competitors) → `posts` (platform, type, title, hook,
published_at, views, likes, comments, shares, saves, retention). SQLite,
created on first boot in `data.db`.
