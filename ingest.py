"""
Live data ingestion for the Creator Dashboard.

Pulls your own posts + their stats from the platform APIs into the same SQLite
`posts` table the dashboard reads. Each platform is optional and activated purely
by the presence of its environment variables — nothing configured means that
platform is skipped, so you can start with just YouTube and add the rest later.

Run it standalone (cron / manual) ...
    python ingest.py
... or trigger it from the dashboard's "Sync now" button (POST /api/sync).

Metric coverage differs per API (documented in .env.example / README):
  - YouTube Data API v3  : views, likes, comments        (public, API key only)
  - Instagram Graph API  : views/reach, likes, comments, shares, saves
  - TikTok Display API   : views, likes, comments, shares
Retention (avg view %) needs each platform's *analytics* API with OAuth; where it
isn't available from the configured endpoint it is left at 0 and can be edited in.
"""

import os
import sqlite3
from datetime import datetime, timezone

try:
    import requests
except ImportError:  # requests is only needed when a platform is actually configured
    requests = None

TIMEOUT = 20


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _connect(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def _own_channel_id(con):
    row = con.execute("SELECT id FROM channels WHERE is_own=1 ORDER BY id LIMIT 1").fetchone()
    return row["id"] if row else None


def upsert_post(con, channel_id, platform, external_id, **f):
    """Insert or update one post, keyed on (channel_id, platform, external_id)."""
    con.execute(
        """INSERT INTO posts (channel_id, platform, external_id, type, title, hook,
                              published_at, views, likes, comments, shares, saves, retention, url)
           VALUES (:cid,:platform,:ext,:type,:title,:hook,:published_at,
                   :views,:likes,:comments,:shares,:saves,:retention,:url)
           ON CONFLICT(channel_id, platform, external_id) DO UPDATE SET
               type=excluded.type, title=excluded.title, hook=excluded.hook,
               published_at=excluded.published_at, views=excluded.views,
               likes=excluded.likes, comments=excluded.comments, shares=excluded.shares,
               saves=excluded.saves, retention=excluded.retention, url=excluded.url""",
        {"cid": channel_id, "platform": platform, "ext": external_id,
         "type": f.get("type", "video"), "title": f.get("title", "Untitled"),
         "hook": f.get("hook", ""), "published_at": f.get("published_at", ""),
         "views": int(f.get("views", 0)), "likes": int(f.get("likes", 0)),
         "comments": int(f.get("comments", 0)), "shares": int(f.get("shares", 0)),
         "saves": int(f.get("saves", 0)), "retention": float(f.get("retention", 0)),
         "url": f.get("url", "")},
    )


def _hook_from(text):
    """A best-effort 'hook' = the first line/sentence of the caption or title."""
    if not text:
        return ""
    first = text.strip().splitlines()[0]
    return (first[:117] + "…") if len(first) > 118 else first


def _iso_duration_to_seconds(dur):
    """Parse an ISO-8601 duration like PT1M5S -> 65 (YouTube contentDetails)."""
    import re
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", dur or "")
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


# ---------------------------------------------------------------------------
# YouTube — Data API v3 (API key only; public statistics)
# ---------------------------------------------------------------------------

def sync_youtube(con, channel_id):
    key = os.environ.get("YOUTUBE_API_KEY")
    yt_channel = os.environ.get("YOUTUBE_CHANNEL_ID")
    if not (key and yt_channel):
        return {"platform": "youtube", "status": "skipped",
                "reason": "set YOUTUBE_API_KEY and YOUTUBE_CHANNEL_ID"}
    limit = int(os.environ.get("YOUTUBE_MAX", "30"))
    base = "https://www.googleapis.com/youtube/v3"

    # 1) uploads playlist for the channel
    r = requests.get(f"{base}/channels", params={
        "part": "contentDetails", "id": yt_channel, "key": key}, timeout=TIMEOUT)
    r.raise_for_status()
    items = r.json().get("items", [])
    if not items:
        return {"platform": "youtube", "status": "error", "reason": "channel not found"}
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    # 2) recent video ids from that playlist
    video_ids, page = [], None
    while len(video_ids) < limit:
        r = requests.get(f"{base}/playlistItems", params={
            "part": "contentDetails", "playlistId": uploads,
            "maxResults": min(50, limit - len(video_ids)),
            "pageToken": page, "key": key}, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        video_ids += [it["contentDetails"]["videoId"] for it in data.get("items", [])]
        page = data.get("nextPageToken")
        if not page:
            break

    # 3) statistics + snippet + duration, in batches of 50
    count = 0
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        r = requests.get(f"{base}/videos", params={
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(batch), "key": key}, timeout=TIMEOUT)
        r.raise_for_status()
        for v in r.json().get("items", []):
            sn, st = v.get("snippet", {}), v.get("statistics", {})
            secs = _iso_duration_to_seconds(v.get("contentDetails", {}).get("duration"))
            upsert_post(
                con, channel_id, "youtube", v["id"],
                type="short" if secs and secs <= 60 else "video",
                title=sn.get("title", "Untitled"),
                hook=_hook_from(sn.get("description")) or sn.get("title", ""),
                published_at=sn.get("publishedAt", ""),
                views=st.get("viewCount", 0), likes=st.get("likeCount", 0),
                comments=st.get("commentCount", 0),
                url=f"https://youtu.be/{v['id']}")
            count += 1
    con.commit()
    return {"platform": "youtube", "status": "ok", "synced": count}


# ---------------------------------------------------------------------------
# Instagram — Graph API (Business/Creator account + long-lived token)
# ---------------------------------------------------------------------------

def sync_instagram(con, channel_id):
    token = os.environ.get("IG_ACCESS_TOKEN")
    ig_user = os.environ.get("IG_USER_ID")
    if not (token and ig_user):
        return {"platform": "instagram", "status": "skipped",
                "reason": "set IG_ACCESS_TOKEN and IG_USER_ID"}
    limit = int(os.environ.get("IG_MAX", "30"))
    ver = os.environ.get("IG_API_VERSION", "v21.0")
    base = f"https://graph.facebook.com/{ver}"

    r = requests.get(f"{base}/{ig_user}/media", params={
        "fields": "id,caption,media_type,media_product_type,permalink,timestamp,"
                  "like_count,comments_count",
        "limit": limit, "access_token": token}, timeout=TIMEOUT)
    r.raise_for_status()
    count = 0
    for m in r.json().get("data", []):
        views = saves = shares = 0
        # Per-media insights (reels report plays/reach + saves/shares).
        try:
            ins = requests.get(f"{base}/{m['id']}/insights", params={
                "metric": "plays,reach,saved,shares", "access_token": token},
                timeout=TIMEOUT).json().get("data", [])
            vals = {d["name"]: d.get("values", [{}])[0].get("value", 0) for d in ins}
            views = vals.get("plays") or vals.get("reach") or 0
            saves = vals.get("saved", 0)
            shares = vals.get("shares", 0)
        except Exception:
            pass
        product = (m.get("media_product_type") or m.get("media_type") or "").lower()
        upsert_post(
            con, channel_id, "instagram", m["id"],
            type="reel" if "reel" in product or product == "video" else "post",
            title=(m.get("caption") or "Instagram post")[:80],
            hook=_hook_from(m.get("caption")),
            published_at=m.get("timestamp", ""),
            views=views, likes=m.get("like_count", 0),
            comments=m.get("comments_count", 0), shares=shares, saves=saves,
            url=m.get("permalink", ""))
        count += 1
    con.commit()
    return {"platform": "instagram", "status": "ok", "synced": count}


# ---------------------------------------------------------------------------
# TikTok — Display API (OAuth user access token)
# ---------------------------------------------------------------------------

def sync_tiktok(con, channel_id):
    token = os.environ.get("TIKTOK_ACCESS_TOKEN")
    if not token:
        return {"platform": "tiktok", "status": "skipped",
                "reason": "set TIKTOK_ACCESS_TOKEN"}
    limit = int(os.environ.get("TIKTOK_MAX", "30"))
    fields = ("id,title,video_description,create_time,share_url,"
              "view_count,like_count,comment_count,share_count")
    r = requests.post(
        f"https://open.tiktokapis.com/v2/video/list/?fields={fields}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"max_count": min(20, limit)}, timeout=TIMEOUT)
    r.raise_for_status()
    videos = r.json().get("data", {}).get("videos", [])
    count = 0
    for v in videos:
        ts = v.get("create_time")
        published = (datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                     if ts else "")
        desc = v.get("video_description") or v.get("title") or ""
        upsert_post(
            con, channel_id, "tiktok", str(v.get("id")),
            type="video",
            title=(v.get("title") or desc or "TikTok")[:80],
            hook=_hook_from(desc),
            published_at=published,
            views=v.get("view_count", 0), likes=v.get("like_count", 0),
            comments=v.get("comment_count", 0), shares=v.get("share_count", 0),
            url=v.get("share_url", ""))
        count += 1
    con.commit()
    return {"platform": "tiktok", "status": "ok", "synced": count}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

SYNCERS = [sync_youtube, sync_instagram, sync_tiktok]


def sync_all(db_path=None):
    db_path = db_path or os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "data.db"))
    if requests is None:
        return [{"platform": "all", "status": "error",
                 "reason": "the 'requests' package is required for live sync"}]
    con = _connect(db_path)
    try:
        channel_id = _own_channel_id(con)
        if channel_id is None:
            return [{"platform": "all", "status": "error", "reason": "no own channel in DB"}]
        results = []
        for fn in SYNCERS:
            try:
                results.append(fn(con, channel_id))
            except Exception as e:
                results.append({"platform": fn.__name__.replace("sync_", ""),
                                "status": "error", "reason": str(e)})
        return results
    finally:
        con.close()


if __name__ == "__main__":
    for res in sync_all():
        line = f"  {res['platform']:<10} {res['status']}"
        if res.get("synced") is not None:
            line += f"  ({res['synced']} posts)"
        if res.get("reason"):
            line += f"  — {res['reason']}"
        print(line)
