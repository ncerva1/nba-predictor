"""
NBA-Predictor — BD de rankings de equipos (v2).

Fuente de verdad: CSV crudos por fecha en db/raw/<YYYY-MM-DD>/ (texto, versionable en el proyecto).
Este script reconstruye db/nba_rankings.sqlite con TODOS los snapshots, calcula el ranking 1-30
de cada métrica, mezcla temporada actual con la anterior (shrinkage w = gp / (gp + SHRINK)) y
exporta db/rankings_latest.json para model.py.

Uso:
    python build_db.py                        # reconstruye todo
    python build_db.py --team MIL --vs IND    # informe de enfrentamiento (último snapshot)

Archivos por snapshot (los que falten se omiten):
    nbacom_team_<season>.csv       NBA.com leaguedashteamstats (Base, Advanced, Misc, Opponent, Scoring) — ancho
    nbacom_shots_<season>.csv      NBA.com shot locations por zona, propio (off) y rival (def)
    nbacom_playtypes_<season>.csv  NBA.com Synergy play types por equipo, ofensivo y defensivo
    dvp_statpick.csv               Defensa vs posición (statpick.ai, por 48 min, ajustado)
    team_pergame.csv / opp_pergame.csv   Basketball-Reference (respaldo si falta NBA.com)
    meta.json                      temporada actual/anterior, corte, fuentes

Convención de ranking: rank 1 = mejor para el equipo (más puntos, menos puntos concedidos...).
Métricas de "estilo" (frecuencias, % de tiros por zona, % de puntos) no son buenas ni malas:
rank 1 = el que MÁS lo hace / el que MÁS lo concede.
"""
import argparse, glob, json, os, sqlite3
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
DB = os.path.join(HERE, "nba_rankings.sqlite")
OUT_JSON = os.path.join(HERE, "rankings_latest.json")
SHRINK = 10  # partidos de la temporada actual que "pesan" igual que el prior

ABBR = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN", "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE", "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET", "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM", "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP", "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS", "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA", "Washington Wizards": "WAS",
}

# ---------------- clasificación de métricas NBA.com ----------------
SKIP = {"GP", "W", "L", "MIN", "W_PCT", "TEAM_ID", "TEAM_NAME", "TEAM_ABBREVIATION", "CFID", "CFPARAMS",
        "E_PACE", "PACE_PER40", "POSS", "PIE"}
LOWER_IS_BETTER = {"DEF_RATING", "E_DEF_RATING", "TOV", "PF", "TM_TOV_PCT", "BLKA", "OPP_PTS_OFF_TOV",
                   "OPP_PTS_2ND_CHANCE", "OPP_PTS_FB", "OPP_PTS_PAINT"}
OPP_HIGHER_IS_BETTER = {"OPP_TOV", "OPP_PF", "OPP_BLKA", "OPP_PFD"}  # bueno para la defensa que el rival los haga
STYLE = ("PCT_", "AST_RATIO", "PACE")  # métricas de estilo: rank 1 = el que más lo hace


def classify(col):
    """devuelve (lado, higher_is_better, es_estilo)"""
    if col.startswith("PCT_") or col.startswith("PACE") or col == "AST_RATIO":
        return "style", True, True
    if col in OPP_HIGHER_IS_BETTER:
        return "def", True, False
    if col.startswith("OPP_") or col in ("DEF_RATING", "E_DEF_RATING", "DREB_PCT", "STL", "BLK"):
        if col in ("DREB_PCT", "STL", "BLK"):
            return "def", True, False
        return "def", False, False
    if col in ("NET_RATING", "E_NET_RATING", "PLUS_MINUS"):
        return "net", True, False
    return "off", col not in LOWER_IS_BETTER, False


def rank_series(s, higher_is_better):
    return s.rank(ascending=not higher_is_better, method="min").astype(int)


# ---------------- carga de snapshots ----------------
def load_nbacom_team(path):
    d = pd.read_csv(path).set_index("TEAM_ABBREVIATION")
    return d[[c for c in d.columns if c not in SKIP and pd.api.types.is_numeric_dtype(d[c])]], d["GP"]


def bref_fallback(snap):
    """Si no hay NBA.com, deriva métricas básicas de Basketball-Reference (formato v1)."""
    tp, op = os.path.join(snap, "team_pergame.csv"), os.path.join(snap, "opp_pergame.csv")
    if not (os.path.exists(tp) and os.path.exists(op)):
        return None, None
    t = pd.read_csv(tp); t = t[t.Team.isin(ABBR)].set_index(t.Team.map(ABBR))
    o = pd.read_csv(op); o = o[o.Team.isin(ABBR)].set_index(o.Team.map(ABBR)).loc[t.index]
    poss = ((t.FGA - t.ORB + t.TOV + .44 * t.FTA) + (o.FGA - o.ORB + o.TOV + .44 * o.FTA)) / 2
    m = pd.DataFrame(index=t.index)
    m["PTS"], m["OPP_PTS"] = t.PTS, o.PTS
    m["OFF_RATING"], m["DEF_RATING"] = 100 * t.PTS / poss, 100 * o.PTS / poss
    m["NET_RATING"] = m.OFF_RATING - m.DEF_RATING
    m["PACE"] = poss * 48 / (t.MP / 5)
    for pre, d in (("", t), ("OPP_", o)):
        m[pre + "FG3M"], m[pre + "FG3A"], m[pre + "FG3_PCT"] = d["3P"], d["3PA"], d["3P%"]
        m[pre + "FTM"], m[pre + "FTA"], m[pre + "FT_PCT"] = d.FT, d.FTA, d["FT%"]
        m[pre + "AST"], m[pre + "TOV"], m[pre + "OREB"], m[pre + "REB"] = d.AST, d.TOV, d.ORB, d.TRB
        m[pre + "EFG_PCT"] = (d.FG + .5 * d["3P"]) / d.FGA
    return m, t.G


def season_files(snap, kind):
    out = {}
    for p in glob.glob(os.path.join(snap, f"nbacom_{kind}_*.csv")):
        out[os.path.basename(p).split("_")[-1][:-4]] = p
    return out


def build():
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    con.executescript("""
    CREATE TABLE teams(abbr TEXT PRIMARY KEY, name TEXT);
    CREATE TABLE snapshots(snapshot_date TEXT PRIMARY KEY, season TEXT, prior_season TEXT, cutoff TEXT,
                           sources TEXT, notes TEXT);
    CREATE TABLE team_metrics(snapshot_date TEXT, season TEXT, team TEXT, metric TEXT, side TEXT,
                              higher_is_better INT, is_style INT, value REAL, rank INT, gp INT,
                              PRIMARY KEY(snapshot_date, season, team, metric));
    CREATE TABLE shot_zones(snapshot_date TEXT, season TEXT, team TEXT, side TEXT, zone TEXT,
                            fgm REAL, fga REAL, fg_pct REAL, fga_rank INT, fg_pct_rank INT,
                            PRIMARY KEY(snapshot_date, season, team, side, zone));
    CREATE TABLE play_types(snapshot_date TEXT, season TEXT, team TEXT, play_type TEXT, side TEXT,
                            poss_pct REAL, ppp REAL, percentile REAL, poss REAL, pts REAL, efg REAL,
                            tov_pct REAL, score_pct REAL, freq_rank INT, ppp_rank INT,
                            PRIMARY KEY(snapshot_date, season, team, play_type, side));
    CREATE TABLE dvp(snapshot_date TEXT, team TEXT, position TEXT, stat TEXT, value REAL, rank INT, source TEXT,
                     PRIMARY KEY(snapshot_date, team, position, stat, source));
    CREATE TABLE blended_metrics(snapshot_date TEXT, team TEXT, metric TEXT, w_cur REAL, value REAL, rank INT,
                                 PRIMARY KEY(snapshot_date, team, metric));
    CREATE TABLE blended_play_types(snapshot_date TEXT, team TEXT, play_type TEXT, side TEXT, w_cur REAL,
                                    poss_pct REAL, ppp REAL, freq_rank INT, ppp_rank INT,
                                    PRIMARY KEY(snapshot_date, team, play_type, side));
    """)
    con.executemany("INSERT INTO teams VALUES(?,?)", [(a, n) for n, a in ABBR.items()])

    for snap in sorted(glob.glob(os.path.join(RAW, "*"))):
        date = os.path.basename(snap)
        mp = os.path.join(snap, "meta.json")
        meta = json.load(open(mp)) if os.path.exists(mp) else {}
        cur, prior = meta.get("season"), meta.get("prior_season")
        con.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?)", (date, cur, prior, meta.get("cutoff"),
                    json.dumps(meta.get("sources", {})), meta.get("notes")))

        # ---- métricas de equipo
        team_files = season_files(snap, "team")
        frames = {s: load_nbacom_team(p) for s, p in team_files.items()}
        if cur and cur not in frames:
            m, gp = bref_fallback(snap)
            if m is not None:
                frames[cur] = (m, gp)
        for season, (m, gp) in frames.items():
            rows = []
            for col in m.columns:
                side, hib, style = classify(col)
                r = rank_series(m[col], hib)
                rows += [(date, season, t, col, side, int(hib), int(style), float(m.at[t, col]), int(r[t]), int(gp[t]))
                         for t in m.index if pd.notna(m.at[t, col])]
            con.executemany("INSERT INTO team_metrics VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
        if cur in frames and prior in frames:
            (mc, gpc), (mpr, _) = frames[cur], frames[prior]
            common = [c for c in mc.columns if c in mpr.columns]
            w = (gpc / (gpc + SHRINK)).reindex(mc.index)
            rows = []
            for col in common:
                _, hib, _ = classify(col)
                b = w * mc[col] + (1 - w) * mpr[col].reindex(mc.index)
                r = rank_series(b, hib)
                rows += [(date, t, col, float(w[t]), float(b[t]), int(r[t])) for t in mc.index if pd.notna(b[t])]
            con.executemany("INSERT INTO blended_metrics VALUES(?,?,?,?,?,?)", rows)

        # ---- tiros por zona
        for season, p in season_files(snap, "shots").items():
            d = pd.read_csv(p)
            for (side, zone), g in d.groupby(["side", "zone"]):
                g = g.set_index("team")
                fr = rank_series(g.fga, True)
                pr = rank_series(g.fg_pct, side == "off")  # off: más % es mejor; def: menos % concedido es mejor
                con.executemany("INSERT INTO shot_zones VALUES(?,?,?,?,?,?,?,?,?,?)",
                                [(date, season, t, side, zone, r.fgm, r.fga, r.fg_pct, int(fr[t]), int(pr[t]))
                                 for t, r in g.iterrows()])

        # ---- play types
        pts = {}
        for season, p in season_files(snap, "playtypes").items():
            d = pd.read_csv(p)
            pts[season] = d
            for (pt, side), g in d.groupby(["play_type", "side"]):
                g = g.set_index("team")
                fr = rank_series(g.poss_pct, True)
                pr = rank_series(g.ppp, side == "off")  # def: rank 1 = concede menos PPP
                con.executemany("INSERT INTO play_types VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                [(date, season, t, pt, side, r.poss_pct, r.ppp, r.percentile, r.poss, r.pts,
                                  r.efg, r.tov_pct, r.score_pct, int(fr[t]), int(pr[t])) for t, r in g.iterrows()])
        if cur in pts and prior in pts and cur in frames:
            gpc = frames[cur][1]
            a = pts[cur].set_index(["team", "play_type", "side"])
            b = pts[prior].set_index(["team", "play_type", "side"]).reindex(a.index)
            w = a.index.get_level_values("team").map(lambda t: gpc[t] / (gpc[t] + SHRINK)).values
            bl = pd.DataFrame({"w": w,
                               "poss_pct": w * a.poss_pct + (1 - w) * b.poss_pct.fillna(a.poss_pct),
                               "ppp": w * a.ppp + (1 - w) * b.ppp.fillna(a.ppp)}, index=a.index).reset_index()
            for (pt, side), g in bl.groupby(["play_type", "side"]):
                g = g.set_index("team")
                fr, pr = rank_series(g.poss_pct, True), rank_series(g.ppp, side == "off")
                con.executemany("INSERT INTO blended_play_types VALUES(?,?,?,?,?,?,?,?,?)",
                                [(date, t, pt, side, r.w, r.poss_pct, r.ppp, int(fr[t]), int(pr[t]))
                                 for t, r in g.iterrows()])

        # ---- defensa vs posición
        dv = os.path.join(snap, "dvp_statpick.csv")
        if os.path.exists(dv):
            d = pd.read_csv(dv)
            d["abbr"] = d.Team.map(ABBR)
            con.executemany("INSERT INTO dvp VALUES(?,?,?,?,?,?,?)",
                            [(date, r.abbr, r.Position, s, float(r[s]), int(r[f"{s}_Rank"]), "statpick")
                             for _, r in d.iterrows() for s in ["PTS", "REB", "AST", "3PM", "PRA", "STL", "BLK", "TOV"]])
    con.commit()
    export_json(con)
    return con


def latest(con):
    return con.execute("SELECT MAX(snapshot_date), (SELECT season FROM snapshots ORDER BY snapshot_date DESC LIMIT 1),"
                       " (SELECT prior_season FROM snapshots ORDER BY snapshot_date DESC LIMIT 1) FROM snapshots").fetchone()


def export_json(con):
    date, cur, prior = latest(con)
    q = lambda sql, *a: pd.read_sql(sql, con, params=(date, *a))
    out = {"snapshot_date": date, "season": cur, "prior_season": prior, "shrink_games": SHRINK, "teams": {}}
    tm = q("SELECT * FROM team_metrics WHERE snapshot_date=?")
    bm = q("SELECT * FROM blended_metrics WHERE snapshot_date=?")
    pt = q("SELECT * FROM play_types WHERE snapshot_date=?")
    bp = q("SELECT * FROM blended_play_types WHERE snapshot_date=?")
    sz = q("SELECT * FROM shot_zones WHERE snapshot_date=?")
    dv = q("SELECT * FROM dvp WHERE snapshot_date=?")
    for t in ABBR.values():
        T = out["teams"][t] = {"gp": None, "metrics": {}, "play_types": {}, "shot_zones": {}, "dvp": {}}
        for _, r in tm[tm.team == t].iterrows():
            T["metrics"].setdefault(r.metric, {"side": r.side, "style": bool(r.is_style)})[r.season] = \
                {"value": round(r.value, 4), "rank": int(r["rank"])}
            if r.season == cur:
                T["gp"] = int(r.gp)
        for _, r in bm[bm.team == t].iterrows():
            T["metrics"].setdefault(r.metric, {})["blend"] = {"value": round(r.value, 4), "rank": int(r["rank"]), "w_cur": round(r.w_cur, 3)}
        for _, r in pt[pt.team == t].iterrows():
            T["play_types"].setdefault(f"{r.play_type}|{r.side}", {})[r.season] = \
                {"freq": r.poss_pct, "ppp": r.ppp, "pctile": r.percentile, "freq_rank": int(r.freq_rank), "ppp_rank": int(r.ppp_rank)}
        for _, r in bp[bp.team == t].iterrows():
            T["play_types"].setdefault(f"{r.play_type}|{r.side}", {})["blend"] = \
                {"freq": round(r.poss_pct, 4), "ppp": round(r.ppp, 4), "freq_rank": int(r.freq_rank), "ppp_rank": int(r.ppp_rank)}
        for _, r in sz[sz.team == t].iterrows():
            T["shot_zones"].setdefault(f"{r.zone}|{r.side}", {})[r.season] = \
                {"fga": r.fga, "fg_pct": r.fg_pct, "fga_rank": int(r.fga_rank), "fg_pct_rank": int(r.fg_pct_rank)}
        for _, r in dv[dv.team == t].iterrows():
            T["dvp"].setdefault(r.position, {})[r.stat] = {"value": r.value, "rank": int(r["rank"])}
    json.dump(out, open(OUT_JSON, "w"), indent=1)


# ---------------- informe de enfrentamiento ----------------
PAIRS = [("PTS", "OPP_PTS"), ("OFF_RATING", "DEF_RATING"), ("EFG_PCT", "OPP_EFG_PCT"), ("FG3A", "OPP_FG3A"),
         ("FG3_PCT", "OPP_FG3_PCT"), ("PTS_PAINT", "OPP_PTS_PAINT"), ("FTA", "OPP_FTA"), ("AST", "OPP_AST"),
         ("TM_TOV_PCT", "OPP_TOV_PCT"), ("PTS_FB", "OPP_PTS_FB"), ("PTS_2ND_CHANCE", "OPP_PTS_2ND_CHANCE"),
         ("OREB_PCT", "DREB_PCT")]


def matchup(con, team, vs):
    date, cur, prior = latest(con)
    bm = pd.read_sql("SELECT * FROM blended_metrics WHERE snapshot_date=?", con, params=(date,))
    if bm.empty:  # sin prior: usar temporada actual
        bm = pd.read_sql("SELECT * FROM team_metrics WHERE snapshot_date=? AND season=?", con, params=(date, cur))
    have = set(bm.metric)
    def g(t, m):
        r = bm[(bm.team == t) & (bm.metric == m)]
        return (r.value.iloc[0], int(r["rank"].iloc[0])) if len(r) else (float("nan"), 0)
    print(f"Snapshot {date} — valores mezclados {cur}/{prior} (w = gp/(gp+{SHRINK})); rank 1 = mejor")
    for a, b in ((team, vs), (vs, team)):
        print(f"\n== Ataque {a} vs Defensa {b} ==")
        for om, dm in PAIRS:
            if om in have and dm in have:
                (ov, orr), (dv, drr) = g(a, om), g(b, dm)
                flag = "  <-- ventaja ataque" if orr <= 10 and drr >= 21 else ("  <-- ventaja defensa" if orr >= 21 and drr <= 10 else "")
                print(f"  {om:<16}{a} {ov:8.3f} (#{orr:>2}) | {dm:<20}{b} {dv:8.3f} (#{drr:>2}){flag}")
    bp = pd.read_sql("SELECT * FROM blended_play_types WHERE snapshot_date=?", con, params=(date,))
    if not bp.empty:
        for a, b in ((team, vs), (vs, team)):
            o = bp[(bp.team == a) & (bp.side == "off")].set_index("play_type")
            d = bp[(bp.team == b) & (bp.side == "def")].set_index("play_type")
            j = o.join(d, lsuffix="_of", rsuffix="_def").sort_values("poss_pct_of", ascending=False)
            print(f"\n== Play types: ataque {a} vs defensa {b} (freq %, PPP, rank PPP; def rank 1 = concede menos) ==")
            for p, r in j.iterrows():
                print(f"  {p:<14} {a} {r.poss_pct_of*100:5.1f}%  PPP {r.ppp_of:.3f} (#{r.ppp_rank_of:>2}) | "
                      f"{b} concede PPP {r.ppp_def:.3f} (#{r.ppp_rank_def:>2}), lo enfrenta {r.poss_pct_def*100:4.1f}%")
    dv = pd.read_sql("SELECT * FROM dvp WHERE snapshot_date=? AND stat IN ('PTS','REB','AST','3PM')", con, params=(date,))
    for t in (team, vs):
        p = dv[dv.team == t].pivot(index="position", columns="stat", values="rank").reindex(["PG", "SG", "SF", "PF", "C"])
        if not p.empty:
            print(f"\nDefensa vs posición {t} (rank 1 = concede menos, 30 = concede más):\n{p.to_string()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--team")
    ap.add_argument("--vs")
    a = ap.parse_args()
    con = build()
    n = con.execute("SELECT COUNT(*) FROM team_metrics").fetchone()[0]
    print(f"BD reconstruida: {DB} ({n} filas de métricas)")
    if a.team and a.vs:
        matchup(con, a.team, a.vs)
