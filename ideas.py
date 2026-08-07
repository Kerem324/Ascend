"""
Idea engine — generates new video ideas for the personal brand.

Two layers, both optional-key-free at the base:

1. Data-driven (always on): mines your best-performing hooks and the posts that
   blew up on competitor channels, then remixes proven hooks with hot topics into
   fresh idea cards with a data-grounded rationale and a predicted-reach score.
2. Claude-enhanced (optional): if an Anthropic API key is configured, the same
   data context is handed to Claude for sharper, more original ideas. Falls back
   to the data-driven layer automatically if the key is absent or the call fails.
"""

import os
import json
import random
import sqlite3
from datetime import datetime, timedelta

PLATFORM_LABEL = {"youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok"}

FORMATS = {
    "youtube": ["Short", "Long-form breakdown", "Tutorial"],
    "instagram": ["Reel", "Carousel"],
    "tiktok": ["Talking-head", "POV skit", "Green-screen reaction"],
}

# Fallback banks so the engine always returns something, even with sparse data.
FALLBACK_HOOKS = [
    "Stop scrolling if you want to grow fast",
    "3 things nobody tells you about going viral",
    "I tried this for 30 days — here's what happened",
    "Do this in the first 3 seconds",
    "Watch this before you post again",
]
FALLBACK_TOPICS = [
    "my exact posting schedule", "the edit that doubled my views",
    "how I batch a week of content", "the hook I steal from big creators",
    "what I'd do to restart at 0",
]


def _connect(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def _rows(con, days):
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    return con.execute(
        "SELECT p.*, c.name AS channel_name, c.is_own AS is_own "
        "FROM posts p JOIN channels c ON c.id=p.channel_id "
        "WHERE p.published_at >= ?", (since,)).fetchall()


def _gather_context(db_path, days=90):
    """Pull the raw material an idea is built from: proven hooks + hot topics."""
    con = _connect(db_path)
    try:
        rows = _rows(con, days)
    finally:
        con.close()

    own = [r for r in rows if r["is_own"]]
    comp = [r for r in rows if not r["is_own"]]

    # Proven hooks — by average views across everything we can see.
    hook_stats = {}
    for r in rows:
        h = hook_stats.setdefault(r["hook"], {"views": 0, "n": 0})
        h["views"] += r["views"]
        h["n"] += 1
    proven = sorted(
        ({"hook": k, "avgViews": round(v["views"] / v["n"])} for k, v in hook_stats.items() if k),
        key=lambda x: x["avgViews"], reverse=True)[:8]
    if not proven:
        proven = [{"hook": h, "avgViews": 0} for h in FALLBACK_HOOKS]

    # Hot topics — titles of posts that blew up (per-channel viral) + own top posts.
    hot = []
    by_channel = {}
    for r in comp:
        by_channel.setdefault(r["channel_name"], []).append(r)
    for name, posts in by_channel.items():
        views = sorted(p["views"] for p in posts)
        median = views[len(views) // 2] if views else 0
        for p in sorted(posts, key=lambda p: p["views"], reverse=True):
            if median and p["views"] >= median * 2.5:
                hot.append({"topic": p["title"], "source": name,
                            "platform": p["platform"], "views": p["views"]})
    for p in sorted(own, key=lambda p: p["views"], reverse=True)[:5]:
        hot.append({"topic": p["title"], "source": "your channel",
                    "platform": p["platform"], "views": p["views"]})
    if not hot:
        hot = [{"topic": t, "source": "the niche", "platform": "youtube", "views": 0}
               for t in FALLBACK_TOPICS]

    # Which platform performs best for the brand (weights idea platform choice).
    plat_perf = {}
    for r in own:
        b = plat_perf.setdefault(r["platform"], {"views": 0, "n": 0})
        b["views"] += r["views"]
        b["n"] += 1
    best_platforms = sorted(
        plat_perf, key=lambda k: plat_perf[k]["views"] / plat_perf[k]["n"], reverse=True) \
        or ["youtube", "instagram", "tiktok"]

    return {"provenHooks": proven, "hotTopics": hot[:12], "bestPlatforms": best_platforms}


def _score(hook_avg, topic_views, best_span):
    """Blend hook strength (0–70) and topic heat (0–30) into a 0–100 predictor."""
    hook_component = min(70, hook_avg / best_span * 70) if best_span else 35
    topic_component = 30 if topic_views else 12
    return int(min(99, hook_component + topic_component))


def generate_ideas(db_path, days=90, count=9, seed=None):
    ctx = _gather_context(db_path, days)
    rng = random.Random(seed)
    hooks, topics, platforms = ctx["provenHooks"], ctx["hotTopics"], ctx["bestPlatforms"]
    best_span = max((h["avgViews"] for h in hooks), default=0) or 1

    ideas = []
    used = set()
    attempts = 0
    while len(ideas) < count and attempts < count * 6:
        attempts += 1
        hook = rng.choice(hooks)
        topic = rng.choice(topics)
        # Prefer the brand's strongest platforms, but keep variety.
        platform = rng.choice(platforms[:2] + platforms) if platforms else "youtube"
        key = (hook["hook"], topic["topic"])
        if key in used:
            continue
        used.add(key)
        fmt = rng.choice(FORMATS.get(platform, ["Short"]))
        title = topic["topic"] if topic["topic"][0].isupper() else topic["topic"].capitalize()
        rationale = (f"Your hook “{hook['hook']}” averages {hook['avgViews']:,} views. "
                     f"Pair it with “{topic['topic']}” — "
                     + (f"which blew up on {topic['source']} ({topic['views']:,} views)."
                        if topic["views"] else f"a proven angle from {topic['source']}."))
        ideas.append({
            "title": title,
            "hook": hook["hook"],
            "platform": platform,
            "platformLabel": PLATFORM_LABEL.get(platform, platform),
            "format": fmt,
            "rationale": rationale,
            "score": _score(hook["avgViews"], topic["views"], best_span),
            "source": "data",
        })
    ideas.sort(key=lambda i: i["score"], reverse=True)
    return {"ideas": ideas, "provenHooks": hooks, "hotTopics": topics, "engine": "data"}


# ---------------------------------------------------------------------------
# Optional Claude enhancement
# ---------------------------------------------------------------------------

def _has_anthropic_creds():
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def enhance_with_claude(ctx, count=9):
    """Ask Claude for sharper ideas grounded in the same data. Returns a list of
    idea dicts, or None on any failure (caller falls back to the data engine)."""
    try:
        import anthropic
    except ImportError:
        return None
    if not _has_anthropic_creds():
        return None

    model = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")
    hooks = "\n".join(f"- \"{h['hook']}\" (~{h['avgViews']:,} avg views)" for h in ctx["provenHooks"])
    topics = "\n".join(f"- \"{t['topic']}\" ({t['source']}, {t['views']:,} views)" for t in ctx["hotTopics"])
    schema = {
        "type": "object",
        "properties": {
            "ideas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "hook": {"type": "string"},
                        "platform": {"type": "string", "enum": ["youtube", "instagram", "tiktok"]},
                        "format": {"type": "string"},
                        "rationale": {"type": "string"},
                        "score": {"type": "integer"},
                    },
                    "required": ["title", "hook", "platform", "format", "rationale", "score"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["ideas"],
        "additionalProperties": False,
    }
    prompt = (
        "You are a viral short-form content strategist for a personal brand.\n"
        f"Generate {count} fresh, specific video ideas.\n\n"
        f"Hooks that perform for this brand:\n{hooks}\n\n"
        f"Topics/formats that recently blew up in the niche:\n{topics}\n\n"
        "For each idea: a punchy title, a scroll-stopping opening hook line, the best "
        "platform (youtube/instagram/tiktok), a format, a one-sentence rationale tied to "
        "the data above, and a 0–100 predicted-performance score. Be original — remix, "
        "don't copy. Return them ordered best-first."
    )
    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            output_config={"format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}],
        )
        if resp.stop_reason == "refusal":
            return None
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if not text:
            return None
        data = json.loads(text)
    except Exception:
        return None

    out = []
    for i in data.get("ideas", []):
        p = i.get("platform", "youtube")
        out.append({
            "title": i.get("title", "Untitled"),
            "hook": i.get("hook", ""),
            "platform": p,
            "platformLabel": PLATFORM_LABEL.get(p, p),
            "format": i.get("format", ""),
            "rationale": i.get("rationale", ""),
            "score": int(i.get("score", 0)),
            "source": "claude",
        })
    return out or None


def get_ideas(db_path, days=90, count=9, seed=None, use_ai=True):
    ctx = _gather_context(db_path, days)
    if use_ai:
        enhanced = enhance_with_claude(ctx, count)
        if enhanced:
            enhanced.sort(key=lambda i: i["score"], reverse=True)
            return {"ideas": enhanced, "provenHooks": ctx["provenHooks"],
                    "hotTopics": ctx["hotTopics"], "engine": "claude"}
    return generate_ideas(db_path, days, count, seed)
