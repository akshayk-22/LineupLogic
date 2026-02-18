import os
from espn_api.basketball import League as BasketballLeague

def get_nba_league():
    league_id = int(os.getenv("ESPN_LEAGUE_ID"))
    year = int(os.getenv("ESPN_YEAR"))
    swid = os.getenv("ESPN_SWID")
    s2 = os.getenv("ESPN_S2")

    if not all([league_id, year, swid, s2]):
        raise ValueError("Missing ESPN env vars. Check backend/.env")

    return BasketballLeague(
        league_id=league_id,
        year=year,
        swid=swid,
        espn_s2=s2
    )
