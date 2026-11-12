"""
Pestaña Liga de la web (pedido de Nicolas 12-nov, ideas 4, 8 y 9 de bucketsbase): docs/data/league.json con
    teams:   por equipo, récord y últimos 10 de esta temporada, ratings con su puesto y DvP por posición
             (lo que concede a PG/SG/SF/PF/C en PTS/REB/AST/3PM/PRA/STL/BLK/TOV, 1 = el que menos concede)
    leaders: líderes de esta temporada por partido (mínimo de partidos jugados)
    books:   margen medio de cada casa por mercado (suma de prob. implícitas de los dos lados − 1) en odds/
No re-simula nada. build_site.py lo llama al final de cada pasada; también: python league_view.py [fecha]
"""
import csv, glob, json, os, sys
from collections import defaultdict
from datetime import date as _d, timedelta

from build_site import HERE, OUT, load_rankings, player_logs
from model import american_to_prob, season_of

METRICS = ("OFF_RATING", "DEF_RATING", "NET_RATING", "PACE", "PTS", "OPP_PTS", "FG3M", "OPP_FG3M", "REB", "AST", "TOV",
           "PTS_PAINT", "OPP_PTS_PAINT", "FG3_PCT", "OPP_FG3_PCT")
LEAD = ("pts", "reb", "ast", "fg3m", "stl", "blk", "pra", "min")
LEAD_N = 15


def team_games(date):
    """{equipo: [(fecha, rival, local 1/0, puntos, puntos rival)]} de esta temporada antes de `date`."""
    season = season_of(date)
    games = defaultdict(dict)  # equipo -> {fecha: fila}
    for fp in sorted(glob.glob(os.path.join(HERE, "gamelogs", "nba", "leaguegamelog_P_*.csv"))):
        agg = {}
        for r in csv.DictReader(open(fp, encoding="utf-8")):
            d = r["GAME_DATE"][:10]
            if d >= date or season_of(d) != season:
                continue
            k = (r["GAME_ID"], r["TEAM_ABBREVIATION"])
            if k not in agg:
                opp = r["MATCHUP"].replace(" vs. ", " @ ").split(" @ ")[-1].strip()
                agg[k] = [d, opp, int(" vs. " in r["MATCHUP"]), 0]
            agg[k][3] += int(float(r["PTS"] or 0))
        by_gid = defaultdict(dict)
        for (gid, t), x in agg.items():
            by_gid[gid][t] = x
        for gid, ts in by_gid.items():
            if len(ts) != 2:
                continue
            (a, xa), (b, xb) = ts.items()
            games[a][xa[0]] = (xa[0], xa[1], xa[2], xa[3], xb[3])
            games[b][xb[0]] = (xb[0], xb[1], xb[2], xb[3], xa[3])
    for rp in sorted(glob.glob(os.path.join(HERE, "results", "20??-??-??.json"))):
        R = json.load(open(rp))
        d = R["date"]
        if d >= date or season_of(d) != season:
            continue
        near = {(_d.fromisoformat(d) + timedelta(days=k)).isoformat() for k in (-1, 0, 1)}
        for key, x in R["games"].items():
            a, h = key.split("@")
            for t, o, home, p, q in ((a, h, 0, x["away"], x["home"]), (h, a, 1, x["home"], x["away"])):
                if any(dd in games[t] and games[t][dd][1] == o for dd in near):
                    continue
                games[t][d] = (d, o, home, p, q)
    return {t: sorted(v.values(), reverse=True) for t, v in games.items()}


def display_names():
    """nombre normalizado -> nombre como lo escribe nba.com (o el box score)."""
    from evaluate import norm
    names = {}
    for rp in sorted(glob.glob(os.path.join(HERE, "results", "20??-??-??.json"))):
        for x in json.load(open(rp))["games"].values():
            for n in x["players"]:
                names[norm(n)] = n
    for fp in sorted(glob.glob(os.path.join(HERE, "gamelogs", "nba", "leaguegamelog_P_*.csv"))):
        for r in csv.DictReader(open(fp, encoding="utf-8")):
            names[norm(r["PLAYER_NAME"])] = r["PLAYER_NAME"]
    return names


def player_ids():
    """docs/data/player_ids.json: nombre normalizado -> PLAYER_ID de nba.com, para las fotos oficiales
    (cdn.nba.com/headshots/nba/latest/260x190/<id>.png) que muestra la web."""
    from evaluate import norm
    ids = {}
    for fp in sorted(glob.glob(os.path.join(HERE, "gamelogs", "nba", "leaguegamelog_P_*.csv"))):
        for r in csv.DictReader(open(fp, encoding="utf-8")):
            ids[norm(r["PLAYER_NAME"])] = int(r["PLAYER_ID"])
    fp = os.path.join(OUT, "player_ids.json")
    json.dump(ids, open(fp, "w"), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return fp


def leaders(date):
    logs = player_logs((_d.fromisoformat(date) + timedelta(days=1)).isoformat())
    names = display_names()
    rows, team_gp = [], defaultdict(int)
    for tg in team_games((_d.fromisoformat(date) + timedelta(days=1)).isoformat()).items():
        team_gp[tg[0]] = len(tg[1])
    for n, gs in logs.items():
        cur = sorted((g for g in gs.values() if g["cur"]), key=lambda g: g["date"], reverse=True)
        if not cur:
            continue
        team = next((g["team"] for g in cur if g["team"]), None)
        need = max(5, int(0.6 * team_gp.get(team, 0)))
        if len(cur) < need:
            continue
        av = {k: round(sum(g["line"].get(k, 0) for g in cur) / len(cur), 1) for k in LEAD}
        rows.append(dict(name=names.get(n, n.title()), team=team, gp=len(cur), **av))
    return {k: sorted(rows, key=lambda r: -r[k])[:LEAD_N] for k in LEAD}


def book_margins():
    acc = defaultdict(lambda: defaultdict(list))
    files = sorted(glob.glob(os.path.join(HERE, "odds", "20??-??-??.json"))) + sorted(glob.glob(os.path.join(HERE, "odds", "co", "*.json")))
    p = lambda o: american_to_prob(o) if o is not None else None
    for fp in files:
        for x in json.load(open(fp))["games"].values():
            for b in x.get("books", []):
                name = b.get("book") or ""
                if name.startswith(("Covers casa", "scoresandodds")):
                    continue  # casas sin nombre o consenso
                pairs = [("ml", b.get("ml_home"), b.get("ml_away"))]
                if b.get("spread_home") and b.get("spread_away"):
                    pairs.append(("spread", b["spread_home"][1], b["spread_away"][1]))
                if b.get("total") and len(b["total"]) >= 3:
                    pairs.append(("total", b["total"][1], b["total"][2]))
                for pr in b.get("props", []):
                    pairs.append(("props", pr.get("over"), pr.get("under")))
                for mk, o1, o2 in pairs:
                    if o1 is None or o2 is None:
                        continue
                    acc[name][mk].append(p(o1) + p(o2) - 1)
                acc[name]["_region"] = b.get("region") or "US"
    out = []
    for name, d in acc.items():
        row = dict(book=name, region=d.pop("_region"))
        for mk, v in d.items():
            row[mk] = [round(sum(v) / len(v), 4), len(v)]
        out.append(row)
    return sorted(out, key=lambda r: (r["region"] != "CO", (r.get("ml") or [9])[0]))


def build(date=None):
    idx = json.load(open(os.path.join(OUT, "index.json")))
    date = date or max(g["date"] for g in idx["games"])
    R = load_rankings() or {}
    tg = team_games((_d.fromisoformat(date) + timedelta(days=1)).isoformat())
    teams = {}
    for t, T in (R.get("teams") or {}).items():
        gs = tg.get(t, [])
        w = sum(g[3] > g[4] for g in gs)
        teams[t] = dict(
            rec=[w, len(gs) - w], home=[sum(g[3] > g[4] for g in gs if g[2]), sum(g[3] < g[4] for g in gs if g[2])],
            away=[sum(g[3] > g[4] for g in gs if not g[2]), sum(g[3] < g[4] for g in gs if not g[2])],
            last=[list(g) for g in gs[:10]],
            m={k: [round(v["2026-27"]["value"], 3), v["2026-27"]["rank"]] for k, v in T.get("metrics", {}).items()
               if k in METRICS and v.get("2026-27")},
            dvp={pos: {c: [v["value"], v["rank"]] for c, v in cols.items()} for pos, cols in (T.get("dvp") or {}).items()})
    out = dict(date=date, snapshot=R.get("snapshot_date"), teams=teams, leaders=leaders(date), books=book_margins())
    fp = os.path.join(OUT, "league.json")
    json.dump(out, open(fp, "w"), ensure_ascii=False, separators=(",", ":"))
    player_ids()
    return fp


if __name__ == "__main__":
    print("OK", build(sys.argv[1] if len(sys.argv) > 1 else None))
