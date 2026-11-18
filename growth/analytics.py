"""Phase 8: performance of every Short / Reel on your channels, and which ones are winners.

Reads ALL uploads (also the ones you posted by hand) with the official APIs, read-only:
  YouTube  Data API v3: channel uploads → views, likes, comments, length   (existing YouTube login)
  Instagram Graph API: your Reels → likes, comments; views/shares/saves from /insights
           (needs the permission instagram_manage_insights on the Page token, see POSTING_SETUP.md)

A winner = views ≥ winner_views_youtube (1000) on YouTube, ≥ winner_views_instagram (100) on Instagram.
Results: database tables stats + stats_history, the Sheet tab "Performance", Views/Likes/Comments in
Posted Reels, and growth/learn.py turns the winners into rules for the AI.
"""

import re
from datetime import datetime

from core.db import get_db
from core.logger import get_logger

log = get_logger("analytics")
_ig_insights_ok = None          # None = not tried, False = permission missing


def _iso_duration(d: str) -> float:
    m = re.fullmatch(r"P(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0.0
    h, mi, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + s


def fetch_youtube(limit=200) -> list:
    from posting.youtube_shorts import _client
    yt = _client()
    items = yt.channels().list(part="contentDetails", mine=True).execute().get("items") or []
    if not items:
        return []
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, tok = [], None
    while len(ids) < limit:
        r = yt.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=50,
                                    pageToken=tok).execute()
        ids += [i["contentDetails"]["videoId"] for i in r.get("items", [])]
        tok = r.get("nextPageToken")
        if not tok:
            break
    now = datetime.now().isoformat(timespec="seconds")
    out = []
    for i in range(0, len(ids), 50):
        r = yt.videos().list(part="snippet,statistics,contentDetails", id=",".join(ids[i:i + 50])).execute()
        for v in r.get("items", []):
            st, sn = v.get("statistics", {}), v["snippet"]
            out.append({"platform": "youtube", "external_id": v["id"], "title": sn.get("title", ""),
                        "url": f"https://youtube.com/shorts/{v['id']}", "published_at": sn.get("publishedAt", "")[:16],
                        "duration_s": _iso_duration(v["contentDetails"].get("duration")),
                        "views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                        "comments": int(st.get("commentCount", 0)), "checked_at": now,
                        "_text": sn.get("description", "")[:300]})
    return out


def fetch_instagram(limit=100, session=None) -> list:
    global _ig_insights_ok
    import requests
    from posting.instagram import _cfg
    s, c = session or requests.Session(), _cfg()
    if not c["ig"] or not c["token"]:
        return []
    base = f"https://{c['host']}/{c['ver']}"
    r = s.get(f"{base}/{c['ig']}/media", timeout=30, params={
        "fields": "id,caption,media_product_type,timestamp,like_count,comments_count,permalink",
        "limit": min(limit, 100), "access_token": c["token"]}).json()
    if "error" in r:
        raise RuntimeError(f"Instagram: {r['error'].get('message')}")
    now = datetime.now().isoformat(timespec="seconds")
    out = []
    for m in r.get("data", []):
        if m.get("media_product_type") != "REELS":
            continue
        row = {"platform": "instagram", "external_id": m["id"], "title": (m.get("caption") or "").split("\n")[0][:120],
               "url": m.get("permalink", ""), "published_at": (m.get("timestamp") or "")[:16],
               "likes": m.get("like_count"), "comments": m.get("comments_count"), "checked_at": now,
               "_text": (m.get("caption") or "")[:300]}
        if _ig_insights_ok is not False:
            ins = s.get(f"{base}/{m['id']}/insights", timeout=30,
                        params={"metric": "views,shares,saved", "access_token": c["token"]}).json()
            if "data" in ins:
                _ig_insights_ok = True
                vals = {d["name"]: d["values"][0]["value"] for d in ins["data"]}
                row.update(views=vals.get("views"), shares=vals.get("shares"), saves=vals.get("saved"))
            elif (ins.get("error") or {}).get("code") == 10:
                _ig_insights_ok = False
                log.warning("Instagram views need the permission instagram_manage_insights "
                            "(make a new Page token, docs/POSTING_SETUP.md step 4)")
        out.append(row)
    return out


def instagram_views_missing() -> bool:
    return _ig_insights_ok is False


def refresh(settings) -> dict:
    """Read both platforms, save, link to farm clips. Returns {"youtube": n, "instagram": n, "errors": [...]}"""
    db = get_db()
    by_ext = {(p["platform"], p.get("external_id")): p["clip_id"] for p in db.posts("posted") if p.get("external_id")}
    by_url = {p.get("url"): p["clip_id"] for p in db.posts("posted") if p.get("url")}
    rep = {"youtube": 0, "instagram": 0, "errors": []}
    for name, fn in (("youtube", fetch_youtube), ("instagram", fetch_instagram)):
        try:
            rows = fn()
        except Exception as exc:  # noqa: BLE001 - one platform failing never stops the other
            rep["errors"].append(f"{name}: {str(exc)[:200]}")
            log.warning(f"stats {name}: {str(exc)[:200]}")
            continue
        for row in rows:
            row["clip_id"] = by_ext.get((name, row["external_id"])) or by_url.get(row["url"])
            db.save_stat({k: v for k, v in row.items() if not k.startswith("_")})
        rep[name] = len(rows)
        rep.setdefault("removed", []).extend(_removed(db, name, rows))
    if rep.get("removed"):
        rep["errors"].append("posts no longer on the platform (removed?): " + ", ".join(rep["removed"]))
    if instagram_views_missing():
        rep["errors"].append("instagram views: permission instagram_manage_insights missing")
    log.info(f"stats refreshed: {rep}")
    return rep


def _removed(db, platform, rows) -> list:
    """Posts the farm published that the platform no longer lists (taken down for copyright / guidelines,
    or deleted by hand). Logged once as an error → Sheet tab Failed Tasks. Only judged when the platform
    returned a full list (fewer rows than the page size), so paging never looks like a removal."""
    if not rows or len(rows) >= (100 if platform == "instagram" else 200):
        return []
    have = {r["external_id"] for r in rows}
    seen = {e["item"] for e in db.events("error", 500) if (e.get("stage") or "") == f"{platform}-removed"}
    out = []
    for p in db.posts("posted"):
        ext = p.get("external_id") or ""
        if p["platform"] != platform or not ext or ext == "manual" or ext in have:
            continue
        out.append(f"{p['clip_id']} ({platform})")
        if p["clip_id"] not in seen:
            db.event("error", p["clip_id"], f"{platform}-removed",
                     f"{platform} no longer shows this post ({p.get('url')}). If you did not delete it, it was "
                     "taken down: check Instagram → Settings → Account Status / YouTube Studio → Content → Restrictions")
            log.warning(f"{p['clip_id']}: no longer on {platform}")
    return out


def threshold(settings, platform) -> int:
    return int(settings.get(f"winner_views_{platform}", 1000 if platform == "youtube" else 100))


def is_winner(settings, row) -> bool:
    return row.get("views") is not None and row["views"] >= threshold(settings, row["platform"])


def winners(settings) -> list:
    return [r for r in get_db().stats() if is_winner(settings, r)]
