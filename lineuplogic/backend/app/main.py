from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from app.espn_client import get_nba_league

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import time

import ssl
import certifi
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

load_dotenv()

app = FastAPI(title="LineupLogic API")

# ============================================================
# SSL context (macOS cert fix)
# ============================================================
SSL_CTX = ssl.create_default_context(cafile=certifi.where())

# ============================================================
# Canonical NBA team abbreviations
# ============================================================
NBA_TEAM_ABBREVS = {
    "ATL","BOS","BKN","CHA","CHI","CLE","DAL","DEN","DET","GSW","HOU","IND",
    "LAC","LAL","MEM","MIA","MIL","MIN","NOP","NYK","OKC","ORL","PHI","PHX",
    "POR","SAC","SAS","TOR","UTA","WAS"
}

TEAM_ABBREV_NORMALIZE = {
    "GS": "GSW",
    "NO": "NOP",
    "SA": "SAS",
    "PHO": "PHX",
    "BK": "BKN",
    "NY": "NYK",
    "WSH": "WAS",
}

def normalize_team_abbrev(abbrev: str | None) -> str | None:
    if not abbrev:
        return None
    a = str(abbrev).strip().upper()
    a = TEAM_ABBREV_NORMALIZE.get(a, a)
    if a in NBA_TEAM_ABBREVS:
        return a
    return None

# ============================================================
# Helpers: availability
# ============================================================
def is_unavailable(player) -> bool:
    status = (getattr(player, "injuryStatus", None) or "").strip().upper()
    blocked = {"OUT", "SUSPENSION"}
    return status in blocked

# ============================================================
# Helpers: ids + scoring
# ============================================================
def get_player_id(player):
    for key in ["playerId", "player_id", "id", "espn_id"]:
        val = getattr(player, key, None)
        if val is not None:
            return val
    try:
        d = vars(player)
        for key in ["playerId", "player_id", "id", "espn_id"]:
            if key in d and d[key] is not None:
                return d[key]
    except Exception:
        pass
    return None

def get_points_per_game(player) -> float:
    proj = getattr(player, "projected_avg_points", None)
    if proj is not None:
        try:
            return float(proj)
        except Exception:
            pass
    avg = getattr(player, "avg_points", 0) or 0
    return float(avg)

# ============================================================
# Player schedule parsing (if available)
# ============================================================
def _parse_game_datetime(g):
    if isinstance(g, dict):
        dt_str = (
            g.get("date")
            or g.get("startDate")
            or g.get("startTime")
            or g.get("gameDate")
            or g.get("gameTime")
        )
    else:
        dt_str = (
            getattr(g, "date", None)
            or getattr(g, "startDate", None)
            or getattr(g, "startTime", None)
            or getattr(g, "gameDate", None)
            or getattr(g, "gameTime", None)
        )

    if not dt_str:
        return None

    try:
        s = str(dt_str).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def schedule_has_parsable_dates(player) -> bool:
    sched = getattr(player, "schedule", None)
    if not sched or not isinstance(sched, list):
        return False
    for g in sched:
        if _parse_game_datetime(g) is not None:
            return True
    return False

def games_next_n_days_from_player_schedule(player, days: int = 7) -> int:
    sched = getattr(player, "schedule", None)
    if not sched or not isinstance(sched, list):
        return 0

    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)

    count = 0
    for g in sched:
        dt = _parse_game_datetime(g)
        if dt is None:
            continue
        if now <= dt <= end:
            count += 1
    return count

# ============================================================
# ESPN scoreboard schedule (team-based; works for free agents)
# ============================================================
_TEAM_SCHEDULE_CACHE = {
    "ts": 0.0,
    "days": None,
    "counts": {},
    "last_error": None,
    "last_status": None,
}

def _fetch_espn_nba_scoreboard_for_date(date_yyyymmdd: str) -> dict:
    url = (
        "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
        f"?dates={date_yyyymmdd}"
    )
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (LineupLogic)"})
    with urlopen(req, timeout=15, context=SSL_CTX) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8"))

def _compute_team_games_next_n_days(days: int = 7) -> dict:
    now_date = datetime.now(timezone.utc).date()
    counts = Counter()

    for i in range(days + 1):
        d = now_date + timedelta(days=i)
        yyyymmdd = d.strftime("%Y%m%d")

        data = _fetch_espn_nba_scoreboard_for_date(yyyymmdd)
        events = data.get("events", []) or []

        for ev in events:
            comps = ev.get("competitions", []) or []
            if not comps:
                continue
            competitors = comps[0].get("competitors", []) or []
            for c in competitors:
                team = c.get("team", {}) or {}
                abbrev = normalize_team_abbrev(team.get("abbreviation"))
                if abbrev:
                    counts[abbrev] += 1

    return {k: int(v) for k, v in counts.items() if k in NBA_TEAM_ABBREVS}

def get_team_games_cache(days: int = 7, ttl_seconds: int = 900) -> dict:
    now_ts = time.time()
    cache_ok = (
        _TEAM_SCHEDULE_CACHE["days"] == days
        and (now_ts - _TEAM_SCHEDULE_CACHE["ts"]) < ttl_seconds
        and isinstance(_TEAM_SCHEDULE_CACHE["counts"], dict)
        and len(_TEAM_SCHEDULE_CACHE["counts"]) > 0
    )
    if cache_ok:
        return _TEAM_SCHEDULE_CACHE["counts"]

    try:
        counts = _compute_team_games_next_n_days(days=days)
        _TEAM_SCHEDULE_CACHE["counts"] = counts
        _TEAM_SCHEDULE_CACHE["ts"] = now_ts
        _TEAM_SCHEDULE_CACHE["days"] = days
        _TEAM_SCHEDULE_CACHE["last_error"] = None
        _TEAM_SCHEDULE_CACHE["last_status"] = 200
        return counts
    except HTTPError as e:
        _TEAM_SCHEDULE_CACHE.update({
            "counts": {},
            "ts": now_ts,
            "days": days,
            "last_error": f"HTTPError: {e}",
            "last_status": getattr(e, "code", None),
        })
        return {}
    except URLError as e:
        _TEAM_SCHEDULE_CACHE.update({
            "counts": {},
            "ts": now_ts,
            "days": days,
            "last_error": f"URLError: {e}",
            "last_status": None,
        })
        return {}
    except Exception as e:
        _TEAM_SCHEDULE_CACHE.update({
            "counts": {},
            "ts": now_ts,
            "days": days,
            "last_error": f"Exception: {e}",
            "last_status": None,
        })
        return {}

def games_next_n_days(player, days: int = 7) -> int:
    # 1) If player.schedule is parsable, trust it
    if schedule_has_parsable_dates(player):
        return games_next_n_days_from_player_schedule(player, days=days)

    # 2) Otherwise use team cache
    team_abbrev = normalize_team_abbrev(getattr(player, "proTeam", None))
    if not team_abbrev:
        return 0

    counts = get_team_games_cache(days=days)

    # If team exists in cache, return it
    if team_abbrev in counts:
        return int(counts.get(team_abbrev, 0))

    # 3) If team missing from cache, ESTIMATE using league average of present teams
    present = list(counts.values())
    if present:
        league_avg = sum(present) / len(present)
        return int(round(league_avg))

    # 4) If cache empty (request failed), fallback to 0
    return 0

def projected_points_next_n_days(player, days: int = 7) -> float:
    ppg = get_points_per_game(player)
    g = games_next_n_days(player, days=days)

    if g == 0 and schedule_has_parsable_dates(player):
        return 0.0

    # avoid nuking players when schedule/cache is missing
    if g <= 0:
        return ppg * 1

    return ppg * g

def pack_player(p, days: int = 7, include_debug: bool = False) -> dict:
    ppg_used = get_points_per_game(p)
    raw_team = getattr(p, "proTeam", None)
    norm_team = normalize_team_abbrev(raw_team)

    g = games_next_n_days(p, days=days)
    pts = projected_points_next_n_days(p, days=days)

    out = {
        "playerId": get_player_id(p),
        "name": getattr(p, "name", None),
        "position": getattr(p, "position", None),
        "proTeam": raw_team,
        "injuryStatus": getattr(p, "injuryStatus", None),
        "avg_points": getattr(p, "avg_points", None),
        "projected_avg_points": getattr(p, "projected_avg_points", None),
        "fantasy_ppg_used": round(ppg_used, 2),
        "days_window": days,
        "games_next_n_days": int(g),
        "projected_points_next_n_days": round(float(pts), 2),
    }

    if include_debug:
        cache_counts = get_team_games_cache(days=days)
        out["debug"] = {
            "proTeam_normalized": norm_team,
            "schedule_parsable": schedule_has_parsable_dates(p),
            "team_in_cache": (norm_team in cache_counts) if norm_team else False,
            "cache_team_count": len(cache_counts),
            "cache_last_error": _TEAM_SCHEDULE_CACHE.get("last_error"),
        }

    return out

# ============================================================
# API routes
# ============================================================
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/league/nba/teams")
def nba_teams(days: int = 7):
    try:
        league = get_nba_league()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="ESPN auth failed. Check SWID/ESPN_S2 cookies and league access."
        )

    teams_out = []
    for t in league.teams:
        teams_out.append({
            "team_id": t.team_id,
            "team_name": t.team_name,
            "wins": getattr(t, "wins", None),
            "losses": getattr(t, "losses", None),
        })

    return {"team_count": len(teams_out), "days_window": days, "teams": teams_out}

@app.get("/league/nba/roster")
def nba_roster(team_id: int, days: int = 21):
    league = get_nba_league()
    team = next((t for t in league.teams if t.team_id == team_id), None)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    roster = [p for p in team.roster if not is_unavailable(p)]
    roster_sorted = sorted(roster, key=lambda p: projected_points_next_n_days(p, days=days))

    return {
        "team": team.team_name,
        "days_window": days,
        "roster": [pack_player(p, days=days, include_debug=False) for p in roster_sorted],
    }

@app.get("/league/nba/recommendations/waivers")
def waiver_recommendations(
    team_id: int,
    limit: int = 10,
    pool_size: int = 300,
    days: int = 21,
    drop_player_id: int | None = None
):
    try:
        league = get_nba_league()
    except Exception:
        raise HTTPException(status_code=400, detail="ESPN auth failed.")

    team = next((t for t in league.teams if t.team_id == team_id), None)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    roster = [p for p in team.roster if not is_unavailable(p)]
    if not roster:
        return {"team": team.team_name, "days_window": days, "recommendations": []}

    forced_drop = None
    if drop_player_id is not None:
        forced_drop = next((p for p in roster if get_player_id(p) == drop_player_id), None)
        if not forced_drop:
            raise HTTPException(status_code=404, detail="drop_player_id not found on roster")

    drop_candidates = sorted(roster, key=lambda p: projected_points_next_n_days(p, days=days))[:6]
    if forced_drop:
        drop_candidates = [forced_drop]

    free_agents = [p for p in league.free_agents(size=pool_size) if not is_unavailable(p)]
    free_agents_sorted = sorted(
        free_agents,
        key=lambda p: projected_points_next_n_days(p, days=days),
        reverse=True
    )

    recommendations = []
    used_add_ids = set()

    for fa in free_agents_sorted:
        fa_id = get_player_id(fa)
        if fa_id in used_add_ids:
            continue

        fa_pts = projected_points_next_n_days(fa, days=days)

        for dp in drop_candidates:
            dp_pts = projected_points_next_n_days(dp, days=days)
            delta = fa_pts - dp_pts
            if delta > 0:
                recommendations.append({
                    "add": pack_player(fa, days=days, include_debug=True),
                    "drop": pack_player(dp, days=days, include_debug=True),
                    "expected_gain_next_n_days": round(delta, 2),
                })
                used_add_ids.add(fa_id)
                break

        if len(recommendations) >= limit:
            break

    return {
        "team": team.team_name,
        "days_window": days,
        "drop_player_id": drop_player_id,
        "drop_candidates_used": [pack_player(p, days=days, include_debug=False) for p in drop_candidates],
        "recommendations": recommendations,
    }

# ============================================================
# Static UI serving (safe: does NOT interfere with API routes)
# backend/app/main.py -> parents[1] = backend/
# UI files live at backend/web/
# ============================================================
WEB_DIR = Path(__file__).resolve().parents[1] / "web"
if WEB_DIR.exists():
    # Serve JS/CSS from /static
    app.mount("/static", StaticFiles(directory=str(WEB_DIR), html=False), name="static")

    # Serve index.html at /
    @app.get("/", include_in_schema=False)
    def root():
        return FileResponse(str(WEB_DIR / "index.html"))