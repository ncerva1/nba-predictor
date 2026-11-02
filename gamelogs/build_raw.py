"""
Deriva de los game logs oficiales de nba.com (gamelogs/nba/leaguegamelog_P_<temporada>.csv) los insumos de una
jornada que antes se armaban con WebFetch a basketball-reference (2-nov):

  raw/<fecha>/cur.csv                 medias por jugador de la temporada actual (equipo = el último en que jugó)
  raw/<fecha>/prior.csv               medias por jugador de la temporada anterior
  db/raw/<fecha>/team_pergame.csv     medias por equipo (formato B-Ref, para el fallback de db/build_db.py)
  db/raw/<fecha>/opp_pergame.csv      lo que concede cada equipo
  h2h/<temporada anterior>.json       añade los cruces de la jornada que falten (--h2h AWAY@HOME ...)

Uso:
    python gamelogs/build_raw.py 2026-11-02 [--h2h OKC@CLE CHI@PHI ...]

Solo cuenta partidos ANTERIORES a <fecha>. Los nombres son los de nba.com (los mismos que usa la web para el % Últ. 10).
Pos sale del último raw/*/cur.csv que tenga al jugador (B-Ref); si no hay, F. GS: el de ese mismo cur.csv para los
partidos hasta su fecha y, para los posteriores, titular = top-5 en minutos de su equipo en ese partido (nba.com no da
GS en el leaguegamelog). Si el CSV de la temporada actual no llega al día anterior a <fecha>, avisa.
"""
import csv, glob, json, os, re, sys
from collections import defaultdict
from datetime import date as D, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "db"))
from build_db import ABBR  # noqa: E402

NAME = {v: k for k, v in ABBR.items()}
COLS = {"PTS": "PTS", "TRB": "REB", "AST": "AST", "3P": "FG3M", "FG": "FGM", "FGA": "FGA", "3PA": "FG3A",
        "STL": "STL", "BLK": "BLK", "TOV": "TOV", "PF": "PF", "FT": "FTM", "FTA": "FTA"}


def season_of(d):
    y = int(d[:4]) if int(d[5:7]) >= 8 else int(d[:4]) - 1
    return f"{y}-{str(y + 1)[2:]}"


def rows(season, before):
    p = os.path.join(HERE, "gamelogs", "nba", f"leaguegamelog_P_{season}.csv")
    out = []
    for r in csv.DictReader(open(p, encoding="utf-8")):
        if r["GAME_DATE"][:10] < before:
            r["_min"] = float(r["MIN"] or 0)
            out.append(r)
    return out


def num(x):
    try:
        return float(x or 0)
    except ValueError:
        return 0.0


def last_cur_info(before):
    """{nombre: (fecha, Pos, G, GS)} del raw/*/cur.csv más reciente que tenga al jugador."""
    info = {}
    for p in sorted(glob.glob(os.path.join(HERE, "raw", "20??-??-??", "cur.csv"))):
        d = p.split(os.sep)[-2]
        if d >= before:
            continue
        for r in csv.DictReader(open(p, encoding="utf-8")):
            info[r["Player"]] = (d, r.get("Pos") or "", int(float(r["G"])), int(float(r.get("GS") or 0)))
    return info


def norm(n):
    import unicodedata
    s = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode().lower()
    return s.replace(".", "").replace("'", "").replace(" jr", "").replace(" iii", "").replace(" ii", "").strip()


def player_table(R, with_team, info=None):
    by = defaultdict(list)
    for r in R:
        if r["_min"] > 0:
            by[r["PLAYER_NAME"]].append(r)
    starters = set()
    if with_team:
        g = defaultdict(list)
        for r in R:
            g[(r["GAME_ID"], r["TEAM_ABBREVIATION"])].append(r)
        for v in g.values():
            for r in sorted(v, key=lambda r: -r["_min"])[:5]:
                starters.add((r["GAME_ID"], r["PLAYER_NAME"]))
    ninfo = {norm(k): v for k, v in (info or {}).items()}
    out = []
    for n, gs in by.items():
        gs.sort(key=lambda r: r["GAME_DATE"])
        G = len(gs)
        row = {"Player": n, "MP": round(sum(r["_min"] for r in gs) / G, 1)}
        for c, k in COLS.items():
            row[c] = round(sum(num(r[k]) for r in gs) / G, 2)
        if with_team:
            i = info.get(n) or ninfo.get(norm(n))
            if i:
                d, pos, g0, gs0 = i
                later = [r for r in gs if r["GAME_DATE"][:10] >= d]
                GS = min(gs0, G - len(later)) + sum((r["GAME_ID"], n) in starters for r in later)
            else:
                pos, GS = "", sum((r["GAME_ID"], n) in starters for r in gs)
            row = {"Team": gs[-1]["TEAM_ABBREVIATION"], "Player": n, "Pos": pos or "F", "G": G, "GS": GS, **row}
        else:
            row["G"] = G
        out.append(row)
    return out


def team_tables(R):
    g = defaultdict(lambda: defaultdict(float))
    for r in R:
        t = g[(r["GAME_ID"], r["TEAM_ABBREVIATION"])]
        t["MP"] += r["_min"]
        for k in ("FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "OREB", "DREB", "REB", "AST", "STL", "BLK", "TOV", "PF", "PTS"):
            t[k] += num(r[k])
    games = defaultdict(list)
    for (gid, team), t in g.items():
        games[gid].append((team, t))
    own, opp = defaultdict(list), defaultdict(list)
    for gid, v in games.items():
        if len(v) != 2:
            continue
        (a, ta), (b, tb) = v
        own[a].append(ta); opp[a].append(tb)
        own[b].append(tb); opp[b].append(ta)

    def table(src):
        out = []
        for team, L in sorted(src.items()):
            n = len(L)
            m = {k: sum(x[k] for x in L) / n for k in L[0]}
            pct = lambda a, b: f"{m[a] / m[b]:.3f}".lstrip("0") if m[b] else ""
            out.append({"Team": NAME[team], "G": n, "MP": round(m["MP"], 1), "FG": round(m["FGM"], 1),
                        "FGA": round(m["FGA"], 1), "FG%": pct("FGM", "FGA"), "3P": round(m["FG3M"], 1),
                        "3PA": round(m["FG3A"], 1), "3P%": pct("FG3M", "FG3A"),
                        "2P": round(m["FGM"] - m["FG3M"], 1), "2PA": round(m["FGA"] - m["FG3A"], 1),
                        "2P%": f"{(m['FGM'] - m['FG3M']) / (m['FGA'] - m['FG3A']):.3f}".lstrip("0"),
                        "FT": round(m["FTM"], 1), "FTA": round(m["FTA"], 1), "FT%": pct("FTM", "FTA"),
                        "ORB": round(m["OREB"], 1), "DRB": round(m["DREB"], 1), "TRB": round(m["REB"], 1),
                        "AST": round(m["AST"], 1), "STL": round(m["STL"], 1), "BLK": round(m["BLK"], 1),
                        "TOV": round(m["TOV"], 1), "PF": round(m["PF"], 1), "PTS": round(m["PTS"], 1)})
        return out
    return table(own), table(opp)


def h2h_games(R, pairs):
    g = defaultdict(list)
    for r in R:
        g[r["GAME_ID"]].append(r)
    out = []
    for gid, v in sorted(g.items(), key=lambda kv: kv[1][0]["GAME_DATE"]):
        r0 = v[0]
        m = re.match(r"(\w+) (@|vs\.) (\w+)", r0["MATCHUP"])
        a, h = (m.group(1), m.group(3)) if m.group(2) == "@" else (m.group(3), m.group(1))
        if frozenset((a, h)) not in pairs:
            continue
        players = {a: [], h: []}
        score = {a: 0, h: 0}
        for r in v:
            t = r["TEAM_ABBREVIATION"]
            score[t] += int(num(r["PTS"]))
            players[t].append([r["PLAYER_NAME"], round(r["_min"]), int(num(r["PTS"])), int(num(r["REB"])),
                               int(num(r["AST"])), int(num(r["FG3M"]))])
        for t in players:
            players[t].sort(key=lambda p: -p[1])
        mins = sum(r["_min"] for r in v)
        out.append({"date": r0["GAME_DATE"][:10], "nba_id": gid, "away": a, "home": h, "score": score,
                    "ot": max(0, round((mins / 10 - 48) / 5)), "players": players})
    return out


def write_csv(p, rows):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main():
    date = sys.argv[1]
    cur_s = season_of(date)
    y = int(cur_s[:4]) - 1
    prior_s = f"{y}-{str(y + 1)[2:]}"
    cur = rows(cur_s, date)
    last = max(r["GAME_DATE"][:10] for r in cur)
    if last < (D.fromisoformat(date) - timedelta(days=1)).isoformat():
        print(f"AVISO: el game log de {cur_s} llega hasta {last}; faltan partidos antes de {date}")
    prior = rows(prior_s, date)
    info = last_cur_info(date)
    write_csv(os.path.join(HERE, "raw", date, "cur.csv"),
              sorted(player_table(cur, True, info), key=lambda r: (r["Team"], -r["MP"])))
    write_csv(os.path.join(HERE, "raw", date, "prior.csv"), sorted(player_table(prior, False), key=lambda r: -r["PTS"]))
    tp, op = team_tables(cur)
    write_csv(os.path.join(HERE, "db", "raw", date, "team_pergame.csv"), sorted(tp, key=lambda r: -r["PTS"]))
    write_csv(os.path.join(HERE, "db", "raw", date, "opp_pergame.csv"), sorted(op, key=lambda r: r["PTS"]))
    if "--h2h" in sys.argv:
        pairs = {frozenset(x.split("@")) for x in sys.argv[sys.argv.index("--h2h") + 1:]}
        hp = os.path.join(HERE, "h2h", f"{prior_s}.json")
        H = json.load(open(hp))
        have = {frozenset((x["away"], x["home"])) for x in H["games"]}
        new = h2h_games(prior, pairs - have)
        H["games"] = sorted(H["games"] + new, key=lambda x: x["date"])
        json.dump(H, open(hp, "w"), ensure_ascii=False, indent=1)
        print(f"h2h: {len(new)} partidos nuevos ({len(pairs - have)} cruces); ya estaban {len(pairs & have)} cruces")
    print(f"cur {last}: {len(cur)} filas; prior {len(prior)} filas")


if __name__ == "__main__":
    main()
