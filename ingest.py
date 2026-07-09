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


def _channel_field(con, channel_id, field):
    row = con.execute(f"SELECT {field} FROM channels WHERE id=?", (channel_id,)).fetchone()
    return row[field] if row and row[field] else None


def set_meta(con, key, value):
    con.execute("INSERT INTO meta(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


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
# YouTube — Data API v3 (API key; public statistics)
#           + Analytics API (OAuth; real audience retention)
# ---------------------------------------------------------------------------

def _youtube_access_token():
    """Exchange the stored OAuth refresh token for a short-lived access token."""
    cid = os.environ.get("YOUTUBE_OAUTH_CLIENT_ID")
    secret = os.environ.get("YOUTUBE_OAUTH_CLIENT_SECRET")
    refresh = os.environ.get("YOUTUBE_OAUTH_REFRESH_TOKEN")
    if not (cid and secret and refresh):
        return None
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": cid, "client_secret": secret,
        "refresh_token": refresh, "grant_type": "refresh_token"}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("access_token")


def _youtube_retention(access_token):
    """Per-video average-view-percentage from the Analytics API → {videoId: pct}."""
    from datetime import date, timedelta
    start = os.environ.get("YOUTUBE_ANALYTICS_START") or (date.today() - timedelta(days=730)).isoformat()
    r = requests.get("https://youtubeanalytics.googleapis.com/v2/reports", params={
        "ids": "channel==MINE", "startDate": start, "endDate": date.today().isoformat(),
        "metrics": "averageViewPercentage", "dimensions": "video",
        "sort": "-averageViewPercentage", "maxResults": 200},
        headers={"Authorization": f"Bearer {access_token}"}, timeout=TIMEOUT)
    r.raise_for_status()
    return {row[0]: round(float(row[1]), 1) for row in r.json().get("rows", [])}


def _youtube_videos_for(key, yt_channel_id, limit):
    """Fetch recent public videos + stats for ANY channel id. Returns a list of
    normalized post dicts, or None if the channel isn't found. Works for your own
    channel and for competitors alike (public statistics only)."""
    base = "https://www.googleapis.com/youtube/v3"
    r = requests.get(f"{base}/channels", params={
        "part": "contentDetails", "id": yt_channel_id, "key": key}, timeout=TIMEOUT)
    r.raise_for_status()
    items = r.json().get("items", [])
    if not items:
        return None
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

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

    out = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        r = requests.get(f"{base}/videos", params={
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(batch), "key": key}, timeout=TIMEOUT)
        r.raise_for_status()
        for v in r.json().get("items", []):
            sn, st = v.get("snippet", {}), v.get("statistics", {})
            secs = _iso_duration_to_seconds(v.get("contentDetails", {}).get("duration"))
            out.append({
                "id": v["id"],
                "type": "short" if secs and secs <= 60 else "video",
                "title": sn.get("title", "Untitled"),
                "hook": _hook_from(sn.get("description")) or sn.get("title", ""),
                "published_at": sn.get("publishedAt", ""),
                "views": st.get("viewCount", 0), "likes": st.get("likeCount", 0),
                "comments": st.get("commentCount", 0),
                "url": f"https://youtu.be/{v['id']}"})
    return out


def sync_youtube(con, channel_id):
    key = os.environ.get("YOUTUBE_API_KEY")
    yt_channel = os.environ.get("YOUTUBE_CHANNEL_ID") or _channel_field(con, channel_id, "yt_channel_id")
    if not (key and yt_channel):
        return {"platform": "youtube", "status": "skipped",
                "reason": "set YOUTUBE_API_KEY and YOUTUBE_CHANNEL_ID"}
    limit = int(os.environ.get("YOUTUBE_MAX", "30"))

    # real audience retention (optional; needs OAuth Analytics access)
    retention_map, retention_note = {}, "not configured"
    try:
        token = _youtube_access_token()
        if token:
            retention_map = _youtube_retention(token)
            retention_note = "youtube analytics"
    except Exception as e:
        retention_note = f"unavailable ({e})"

    videos = _youtube_videos_for(key, yt_channel, limit)
    if videos is None:
        return {"platform": "youtube", "status": "error", "reason": "channel not found"}
    for vd in videos:
        upsert_post(con, channel_id, "youtube", vd["id"],
                    retention=retention_map.get(vd["id"], 0),
                    **{k: vd[k] for k in vd if k != "id"})
    con.commit()
    return {"platform": "youtube", "status": "ok", "synced": len(videos),
            "retention": retention_note}


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
# Competitors — public data only (no private analytics of other creators)
# ---------------------------------------------------------------------------

def sync_competitor_youtube(con, comp):
    """A competitor's recent public videos via the Data API key + their channel id."""
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key or not comp.get("yt_channel_id"):
        return None
    limit = int(os.environ.get("COMPETITOR_MAX", "20"))
    videos = _youtube_videos_for(key, comp["yt_channel_id"], limit)
    if videos is None:
        return {"platform": "youtube", "status": "error", "reason": "channel not found"}
    for vd in videos:
        upsert_post(con, comp["id"], "youtube", vd["id"],
                    **{k: vd[k] for k in vd if k != "id"})
    return {"platform": "youtube", "status": "ok", "synced": len(videos)}


def sync_competitor_instagram(con, comp):
    """A competitor's public posts via Instagram Business Discovery (uses YOUR token).
    Returns public like/comment counts + follower count; views aren't exposed for
    other accounts, so they stay 0."""
    token = os.environ.get("IG_ACCESS_TOKEN")
    ig_user = os.environ.get("IG_USER_ID")
    if not (token and ig_user and comp.get("ig_username")):
        return None
    limit = int(os.environ.get("COMPETITOR_MAX", "20"))
    ver = os.environ.get("IG_API_VERSION", "v21.0")
    fields = (f"business_discovery.username({comp['ig_username']})"
              f"{{followers_count,media.limit({limit})"
              f"{{id,caption,media_type,media_product_type,permalink,timestamp,"
              f"like_count,comments_count}}}}")
    r = requests.get(f"https://graph.facebook.com/{ver}/{ig_user}",
                     params={"fields": fields, "access_token": token}, timeout=TIMEOUT)
    r.raise_for_status()
    bd = r.json().get("business_discovery", {})
    if bd.get("followers_count"):
        con.execute("UPDATE channels SET followers=? WHERE id=?",
                    (bd["followers_count"], comp["id"]))
    count = 0
    for m in bd.get("media", {}).get("data", []):
        product = (m.get("media_product_type") or m.get("media_type") or "").lower()
        upsert_post(
            con, comp["id"], "instagram", m["id"],
            type="reel" if "reel" in product or product == "video" else "post",
            title=(m.get("caption") or "Instagram post")[:80],
            hook=_hook_from(m.get("caption")),
            published_at=m.get("timestamp", ""),
            views=0, likes=m.get("like_count", 0), comments=m.get("comments_count", 0),
            url=m.get("permalink", ""))
        count += 1
    return {"platform": "instagram", "status": "ok", "synced": count}


COMPETITOR_SYNCERS = [sync_competitor_youtube, sync_competitor_instagram]


def sync_competitors(con):
    comps = [dict(r) for r in con.execute("SELECT * FROM channels WHERE is_own=0").fetchall()]
    results = []
    for comp in comps:
        for fn in COMPETITOR_SYNCERS:
            try:
                res = fn(con, comp)
            except Exception as e:
                res = {"platform": fn.__name__.split("_")[-1], "status": "error", "reason": str(e)}
            if res:
                results.append({"channel": comp["name"], **res})
    con.commit()
    return results


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

SYNCERS = [sync_youtube, sync_instagram, sync_tiktok]


def _default_db():
    return os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "data.db"))


def _sync_own(con):
    cid = _own_channel_id(con)
    if cid is None:
        return [{"platform": "all", "status": "error", "reason": "no own channel in DB"}]
    out = []
    for fn in SYNCERS:
        try:
            out.append(fn(con, cid))
        except Exception as e:
            out.append({"platform": fn.__name__.replace("sync_", ""),
                        "status": "error", "reason": str(e)})
    return out


def sync_all(db_path=None):
    """Sync your OWN channels only. Kept for scripting; sync_everything is the full run."""
    if requests is None:
        return [{"platform": "all", "status": "error",
                 "reason": "the 'requests' package is required for live sync"}]
    con = _connect(db_path or _default_db())
    try:
        return _sync_own(con)
    finally:
        con.close()


def sync_everything(db_path=None):
    """Full run: your own channels + all tracked competitors. Records last_sync."""
    if requests is None:
        return {"error": "the 'requests' package is required for live sync",
                "own": [], "competitors": []}
    con = _connect(db_path or _default_db())
    try:
        own = _sync_own(con)
        competitors = sync_competitors(con)
        set_meta(con, "last_sync", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        con.commit()
        return {"own": own, "competitors": competitors}
    finally:
        con.close()


def _print(section, rows):
    print(section)
    for res in rows:
        who = (res.get("channel") + " / ") if res.get("channel") else ""
        line = f"  {who}{res['platform']:<10} {res['status']}"
        if res.get("synced") is not None:
            line += f"  ({res['synced']} posts)"
        if res.get("reason"):
            line += f"  — {res['reason']}"
        print(line)


if __name__ == "__main__":
    data = sync_everything()
    if data.get("error"):
        print(data["error"])
    else:
        _print("Own channels:", data["own"])
        _print("Competitors:", data["competitors"] or [{"platform": "-", "status": "none configured"}])
