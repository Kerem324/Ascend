"""
Creator Dashboard — a social-media analytics cockpit for a personal brand.

Tracks your own content across YouTube / Instagram / TikTok, compares hook
performance ("Hook Lab"), and watches competitor ("concurrent") channels to see
what blew up in the last 30 days and which hooks they used.

The app ships with realistic demo data so it works the moment it boots. Swap the
seed for your own numbers via the "Add post" form, the JSON API, or by wiring the
platform APIs into `ingest.py` later — the schema is the same either way.
"""

import os
import json
import math
import sqlite3
import random
from datetime import datetime, timedelta, date

from flask import Flask, jsonify, render_template, request, g

app = Flask(__name__)

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "data.db"))

PLATFORMS = {
    "youtube":   {"label": "YouTube",   "color": "#e34948", "types": ["short", "video"]},
    "instagram": {"label": "Instagram", "color": "#4a3aa7", "types": ["reel", "post"]},
    "tiktok":    {"label": "TikTok",    "color": "#1baf7a", "types": ["video"]},
}

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    # Allow DB_PATH to live on a mounted volume (e.g. Railway /data) — create it.
    parent = os.path.dirname(os.path.abspath(DB_PATH))
    os.makedirs(parent, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS channels (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            handle          TEXT,
            is_own          INTEGER NOT NULL DEFAULT 0,
            followers       INTEGER NOT NULL DEFAULT 0,
            niche           TEXT,
            yt_channel_id   TEXT,
            ig_username     TEXT,
            tiktok_username TEXT
        );

        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS posts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id   INTEGER NOT NULL REFERENCES channels(id),
            platform     TEXT NOT NULL,
            type         TEXT NOT NULL,
            title        TEXT NOT NULL,
            hook         TEXT NOT NULL,
            published_at TEXT NOT NULL,
            views        INTEGER NOT NULL DEFAULT 0,
            likes        INTEGER NOT NULL DEFAULT 0,
            comments     INTEGER NOT NULL DEFAULT 0,
            shares       INTEGER NOT NULL DEFAULT 0,
            saves        INTEGER NOT NULL DEFAULT 0,
            retention    REAL NOT NULL DEFAULT 0,
            url          TEXT,
            external_id  TEXT
        );
        """
    )
    # Migration: add newer columns to pre-existing databases.
    pcols = [r[1] for r in con.execute("PRAGMA table_info(posts)").fetchall()]
    if "external_id" not in pcols:
        con.execute("ALTER TABLE posts ADD COLUMN external_id TEXT")
    ccols = [r[1] for r in con.execute("PRAGMA table_info(channels)").fetchall()]
    for col in ("yt_channel_id", "ig_username", "tiktok_username"):
        if col not in ccols:
            con.execute(f"ALTER TABLE channels ADD COLUMN {col} TEXT")
    # Dedupe key for live-synced posts. SQLite treats NULLs as distinct, so the
    # demo/manual rows (external_id NULL) are never collapsed by this index.
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_posts_ext "
                "ON posts(channel_id, platform, external_id)")
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# Demo data — deterministic so the dashboard looks the same on every boot
# ---------------------------------------------------------------------------

HOOK_BANK = [
    "You've been posting wrong this whole time",
    "Stop scrolling if you want to grow fast",
    "3 things nobody tells you about going viral",
    "I tried this for 30 days — here's what happened",
    "POV: you finally cracked the algorithm",
    "The one edit that doubled my views",
    "Watch this before you post again",
    "Nobody is talking about this hook",
    "This took me 2 years to figure out",
    "Why your reels get 200 views",
    "Copy this exact posting schedule",
    "The hook I steal from big creators",
    "Do this in the first 3 seconds",
    "I analyzed 100 viral videos so you don't have to",
    "This trend is about to blow up",
]

TITLE_BANK = [
    "How I edit my shorts in 10 min",
    "My exact content workflow",
    "The retention trick that works",
    "Batch filming a week in a day",
    "Reading my worst comments",
    "Growth update: month {n}",
    "Trying a viral format",
    "Behind the scenes of a post",
    "What I'd do to restart at 0",
    "Hook breakdown of my top video",
    "3 apps every creator needs",
    "Answering your DMs",
    "My camera setup on a budget",
    "Posting every day for a week",
    "Why I quit my 9-5 for content",
]

OWN_BRAND = {"name": "Your Brand", "handle": "@yourbrand", "followers": 84200, "niche": "content creation"}

COMPETITORS = [
    {"name": "Mia Makes",     "handle": "@miamakes",     "followers": 212000, "niche": "content creation"},
    {"name": "GrowthGuy",     "handle": "@growthguy",    "followers": 156000, "niche": "content creation"},
    {"name": "Studio Nova",   "handle": "@studionova",   "followers": 98000,  "niche": "content creation"},
    {"name": "The Reel Lab",  "handle": "@thereellab",   "followers": 47000,  "niche": "content creation"},
    {"name": "Creator Codex", "handle": "@creatorcodex", "followers": 301000, "niche": "content creation"},
]


def _make_posts(rng, channel_platforms, base_reach, count_range, days=40):
    """Generate a plausible set of posts for one channel."""
    posts = []
    n = rng.randint(*count_range)
    for _ in range(n):
        platform = rng.choice(channel_platforms)
        ptype = rng.choice(PLATFORMS[platform]["types"])
        days_ago = rng.randint(0, days)
        published = (datetime.utcnow() - timedelta(days=days_ago,
                                                   hours=rng.randint(0, 23))).replace(microsecond=0)
        # Long-form video and "post" reach less than short-form; add a viral tail.
        form_factor = 0.35 if ptype in ("video", "post") else 1.0
        viral = rng.random() < 0.16
        multiplier = rng.uniform(3.0, 9.0) if viral else rng.uniform(0.4, 1.8)
        views = int(base_reach * form_factor * multiplier)
        eng_rate = rng.uniform(0.04, 0.11)
        likes = int(views * eng_rate)
        comments = int(likes * rng.uniform(0.02, 0.08))
        shares = int(likes * rng.uniform(0.05, 0.2))
        saves = int(likes * rng.uniform(0.08, 0.3))
        retention = round(rng.uniform(28, 74), 1)
        title = rng.choice(TITLE_BANK).replace("{n}", str(rng.randint(2, 11)))
        posts.append({
            "platform": platform, "type": ptype, "title": title,
            "hook": rng.choice(HOOK_BANK),
            "published_at": published.isoformat(),
            "views": views, "likes": likes, "comments": comments,
            "shares": shares, "saves": saves, "retention": retention,
            "url": "#",
        })
    return posts


CONFIG_PATH = os.environ.get("CHANNELS_CONFIG", os.path.join(os.path.dirname(__file__), "channels.json"))


def _insert_channel(con, info, is_own):
    cur = con.execute(
        """INSERT INTO channels (name, handle, is_own, followers, niche,
                                 yt_channel_id, ig_username, tiktok_username)
           VALUES (?,?,?,?,?,?,?,?)""",
        (info["name"], info.get("handle", ""), is_own, int(info.get("followers", 0)),
         info.get("niche", ""), info.get("yt_channel_id", ""),
         info.get("ig_username", ""), info.get("tiktok_username", "")),
    )
    return cur.lastrowid


def seed_from_config(con, cfg):
    """Real mode: create channels from channels.json. No fake posts — data
    arrives via live sync."""
    if cfg.get("own"):
        _insert_channel(con, cfg["own"], 1)
    for comp in cfg.get("competitors", []):
        _insert_channel(con, comp, 0)


def seed_demo(con):
    """Demo mode: channels + realistic generated posts, deterministic seed."""
    rng = random.Random(42)

    def add_channel(info, is_own, platforms, base_reach, count_range):
        cid = _insert_channel(con, info, is_own)
        for p in _make_posts(rng, platforms, base_reach, count_range):
            con.execute(
                """INSERT INTO posts (channel_id, platform, type, title, hook, published_at,
                                      views, likes, comments, shares, saves, retention, url)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, p["platform"], p["type"], p["title"], p["hook"], p["published_at"],
                 p["views"], p["likes"], p["comments"], p["shares"], p["saves"],
                 p["retention"], p["url"]),
            )

    add_channel(OWN_BRAND, 1, ["youtube", "instagram", "tiktok"], base_reach=22000, count_range=(22, 30))
    for comp in COMPETITORS:
        reach = int(comp["followers"] * rng.uniform(0.12, 0.28))
        add_channel(comp, 0, ["youtube", "instagram", "tiktok"], base_reach=reach, count_range=(12, 18))


def seed_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    if con.execute("SELECT COUNT(*) FROM channels").fetchone()[0] > 0:
        con.close()
        return

    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as fh:
            seed_from_config(con, json.load(fh))
    else:
        seed_demo(con)

    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def _eng_rate(p):
    return ((p["likes"] + p["comments"] + p["shares"] + p["saves"]) / p["views"] * 100) if p["views"] else 0


def _post_dict(row):
    p = dict(row)
    p["engagement"] = round(_eng_rate(p), 2)
    return p


def _own_channels(db):
    return [dict(r) for r in db.execute("SELECT * FROM channels WHERE is_own=1").fetchall()]


def _posts_within(db, channel_ids, days):
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    q = ("SELECT p.*, c.name AS channel_name, c.handle AS channel_handle "
         "FROM posts p JOIN channels c ON c.id=p.channel_id "
         "WHERE p.channel_id IN (%s) AND p.published_at >= ? "
         "ORDER BY p.published_at DESC" % ",".join("?" * len(channel_ids)))
    return [_post_dict(r) for r in db.execute(q, (*channel_ids, since)).fetchall()]


# ---------------------------------------------------------------------------
# Routes — pages
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    return render_template("dashboard.html", platforms=PLATFORMS)


@app.route("/sw.js")
def service_worker():
    # Served from the root so the worker can control the whole app scope ("/").
    resp = app.send_static_file("sw.js")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# ---------------------------------------------------------------------------
# Routes — API
# ---------------------------------------------------------------------------

@app.route("/api/overview")
def api_overview():
    days = int(request.args.get("days", 30))
    db = get_db()
    own = _own_channels(db)
    own_ids = [c["id"] for c in own]
    if not own_ids:
        return jsonify({"kpis": {}, "trend": [], "platforms": [], "topPosts": []})

    posts = _posts_within(db, own_ids, days)
    total_views = sum(p["views"] for p in posts)
    total_eng = sum(p["likes"] + p["comments"] + p["shares"] + p["saves"] for p in posts)
    avg_eng = round(total_eng / total_views * 100, 2) if total_views else 0
    followers = sum(c["followers"] for c in own)
    best = max(posts, key=lambda p: p["views"]) if posts else None

    # Daily views by publish date
    trend = {}
    for i in range(days - 1, -1, -1):
        d = (datetime.utcnow() - timedelta(days=i)).date().isoformat()
        trend[d] = 0
    for p in posts:
        d = p["published_at"][:10]
        if d in trend:
            trend[d] += p["views"]
    trend_series = [{"date": k, "views": v} for k, v in trend.items()]

    # Views by platform
    plat = {}
    for p in posts:
        plat.setdefault(p["platform"], 0)
        plat[p["platform"]] += p["views"]
    plat_series = [{"platform": k, "label": PLATFORMS[k]["label"],
                    "color": PLATFORMS[k]["color"], "views": v}
                   for k, v in sorted(plat.items(), key=lambda kv: -kv[1])]

    top_posts = sorted(posts, key=lambda p: p["views"], reverse=True)[:6]

    return jsonify({
        "kpis": {
            "views": total_views, "followers": followers,
            "avgEngagement": avg_eng, "posts": len(posts),
            "bestPost": {"title": best["title"], "views": best["views"],
                         "platform": best["platform"]} if best else None,
        },
        "trend": trend_series,
        "platforms": plat_series,
        "topPosts": top_posts,
    })


@app.route("/api/content")
def api_content():
    days = int(request.args.get("days", 30))
    platform = request.args.get("platform", "all")
    db = get_db()
    own_ids = [c["id"] for c in _own_channels(db)]
    if not own_ids:
        return jsonify([])
    posts = _posts_within(db, own_ids, days)
    if platform != "all":
        posts = [p for p in posts if p["platform"] == platform]
    return jsonify(posts)


@app.route("/api/hooks")
def api_hooks():
    """Hook Lab: how each opening hook performs, overall and per platform."""
    days = int(request.args.get("days", 90))
    db = get_db()
    own_ids = [c["id"] for c in _own_channels(db)]
    if not own_ids:
        return jsonify({"hooks": [], "byPlatform": []})
    posts = _posts_within(db, own_ids, days)

    groups = {}
    for p in posts:
        g_ = groups.setdefault(p["hook"], {"hook": p["hook"], "count": 0, "views": 0,
                                           "eng": 0.0, "ret": 0.0})
        g_["count"] += 1
        g_["views"] += p["views"]
        g_["eng"] += p["engagement"]
        g_["ret"] += p["retention"]
    hooks = []
    for g_ in groups.values():
        hooks.append({
            "hook": g_["hook"], "count": g_["count"],
            "avgViews": round(g_["views"] / g_["count"]),
            "avgEngagement": round(g_["eng"] / g_["count"], 2),
            "avgRetention": round(g_["ret"] / g_["count"], 1),
        })
    hooks.sort(key=lambda h: h["avgViews"], reverse=True)

    # Avg views per platform (for the cross-platform comparison bars)
    by_plat = {}
    for p in posts:
        b = by_plat.setdefault(p["platform"], {"views": 0, "eng": 0.0, "count": 0})
        b["views"] += p["views"]
        b["eng"] += p["engagement"]
        b["count"] += 1
    by_platform = [{
        "platform": k, "label": PLATFORMS[k]["label"], "color": PLATFORMS[k]["color"],
        "avgViews": round(v["views"] / v["count"]) if v["count"] else 0,
        "avgEngagement": round(v["eng"] / v["count"], 2) if v["count"] else 0,
        "count": v["count"],
    } for k, v in by_plat.items()]
    by_platform.sort(key=lambda x: x["avgViews"], reverse=True)

    return jsonify({"hooks": hooks, "byPlatform": by_platform})


@app.route("/api/competitors")
def api_competitors():
    """Concurrent channels: recent posts, what blew up, and the hooks behind it."""
    days = int(request.args.get("days", 30))
    db = get_db()
    comps = [dict(r) for r in db.execute("SELECT * FROM channels WHERE is_own=0 "
                                         "ORDER BY followers DESC").fetchall()]
    result = []
    for c in comps:
        posts = _posts_within(db, [c["id"]], days)
        if not posts:
            result.append({**c, "posts": [], "totalViews": 0, "median": 0,
                           "viral": [], "topHooks": []})
            continue
        views_list = sorted(p["views"] for p in posts)
        median = views_list[len(views_list) // 2]
        # "Blew up" = views >= 2.5x this channel's median
        for p in posts:
            p["viral"] = p["views"] >= median * 2.5
        viral = sorted([p for p in posts if p["viral"]],
                       key=lambda p: p["views"], reverse=True)
        hook_perf = {}
        for p in posts:
            hp = hook_perf.setdefault(p["hook"], {"hook": p["hook"], "views": 0, "count": 0})
            hp["views"] += p["views"]
            hp["count"] += 1
        top_hooks = sorted(hook_perf.values(),
                           key=lambda h: h["views"] / h["count"], reverse=True)[:3]
        for h in top_hooks:
            h["avgViews"] = round(h["views"] / h["count"])
        result.append({
            **c,
            "totalViews": sum(p["views"] for p in posts),
            "median": median,
            "posts": sorted(posts, key=lambda p: p["published_at"], reverse=True),
            "viral": viral[:5],
            "topHooks": top_hooks,
        })
    return jsonify(result)


@app.route("/api/posts", methods=["POST"])
def api_add_post():
    """Add one of your own posts (manual entry / API ingest)."""
    data = request.get_json(force=True)
    db = get_db()
    own = _own_channels(db)
    if not own:
        return jsonify({"error": "no own channel"}), 400
    channel_id = own[0]["id"]
    try:
        db.execute(
            """INSERT INTO posts (channel_id, platform, type, title, hook, published_at,
                                  views, likes, comments, shares, saves, retention, url)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (channel_id, data["platform"], data.get("type", "short"),
             data.get("title", "Untitled"), data.get("hook", ""),
             data.get("published_at", datetime.utcnow().isoformat()),
             int(data.get("views", 0)), int(data.get("likes", 0)),
             int(data.get("comments", 0)), int(data.get("shares", 0)),
             int(data.get("saves", 0)), float(data.get("retention", 0)),
             data.get("url", "#")),
        )
        db.commit()
    except (KeyError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.route("/api/ideas")
def api_ideas():
    """New video ideas mined from your best hooks + what blew up on competitors."""
    import ideas as ideas_mod
    days = int(request.args.get("days", 90))
    use_ai = request.args.get("ai", "1") != "0"
    seed = request.args.get("seed")
    try:
        result = ideas_mod.get_ideas(DB_PATH, days=max(days, 60),
                                     seed=seed, use_ai=use_ai)
    except Exception as e:
        return jsonify({"ideas": [], "error": str(e), "engine": "none"}), 200
    return jsonify(result)


@app.route("/api/sync", methods=["POST"])
def api_sync():
    """Pull live numbers for your own channels AND tracked competitors, from
    any platform whose credentials are configured."""
    import ingest
    try:
        results = ingest.sync_everything(DB_PATH)
    except Exception as e:  # never 500 the UI over a flaky third-party API
        return jsonify({"error": str(e), "own": [], "competitors": []}), 200
    return jsonify(results)


@app.route("/api/meta")
def api_meta():
    db = get_db()
    row = db.execute("SELECT value FROM meta WHERE key='last_sync'").fetchone()
    return jsonify({
        "lastSync": row["value"] if row else None,
        "autoSyncMinutes": int(os.environ.get("SYNC_INTERVAL_MINUTES", "0") or 0),
    })


def start_autosync():
    """Background scheduler — runs a full sync every SYNC_INTERVAL_MINUTES."""
    minutes = int(os.environ.get("SYNC_INTERVAL_MINUTES", "0") or 0)
    if minutes <= 0:
        return
    import threading
    import time
    import ingest

    def loop():
        while True:
            time.sleep(minutes * 60)
            try:
                ingest.sync_everything(DB_PATH)
                app.logger.info("auto-sync complete")
            except Exception as e:
                app.logger.warning("auto-sync failed: %s", e)

    threading.Thread(target=loop, daemon=True, name="autosync").start()
    app.logger.info("auto-sync enabled: every %d min", minutes)


init_db()
seed_db()
start_autosync()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
