"""
Genera y simula todos los partidos de una jornada.

Uso:
    python build_slate.py slates/2026-10-26.json [n_sims]

Entradas:
  slates/<fecha>.json        partidos, cuotas, bajas, back-to-back y ajustes manuales de roster
  raw/<fecha>/cur.csv        stats por jugador de la temporada actual (Team,Player,Pos,G,GS,MP,PTS,TRB,AST,3P
                             y opcionales FG,FGA,3PA,STL,BLK,TOV,PF para las props de tiros, robos, tapones...)
  raw/<fecha>/prior.csv      stats por jugador de la temporada anterior (Player,MP,PTS,TRB,AST,3P + las mismas opcionales)
  db/rankings_latest.json    BD de rankings (python db/build_db.py)

Salidas (en games/):
  <fecha>_<AWAY>@<HOME>.json          JSON del partido, mismo formato que el de IND@MIL (editable a mano)
  <fecha>_<AWAY>@<HOME>_results.json  salida de model.summarize()
  (en slates/) <fecha>_results.json   resumen de la jornada y apuestas ordenadas por valor (prob. vs cuota)

Reglas para el JSON de cada partido:
  - prior de equipo = ORtg/DRtg/pace 2025-26 de la BD re-escalados a la media de liga, + ajuste manual de roster
  - adj de contexto = ajuste de roster + back-to-back (b2b_adj) del archivo de la jornada
  - jugadores: los que tienen >= 8 min/pj, sin los OUT; minutos = MP actual re-escalados para que el equipo
    sume 240; "gtd" = probabilidad de que juegue (p_play: el modelo lo deja fuera en esa fracción de
    simulaciones y reparte sus minutos); g_cur = G (el modelo pondera la temporada actual con G / (G + 6));
    sin temporada anterior -> prior = actual
Los partidos que ya tienen JSON hecho a mano en games/ (p. ej. IND@MIL) no se regeneran: solo se simulan.
"""
import csv, json, os, sys
import numpy as np
from model import load_game, simulate, summarize, american_to_prob, prob_to_american

HERE = os.path.dirname(os.path.abspath(__file__))
LG = {"ortg": 115.0, "pace": 100.5, "home_adv_per100": 1.0}
STAT_COL = {"pts": "PTS", "reb": "TRB", "ast": "AST", "fg3m": "3P"}
# columnas opcionales (1.3.4): tiros de campo, triples intentados, robos, tapones, pérdidas y faltas
EXTRA_COL = {"fgm": "FG", "fga": "FGA", "fg3a": "3PA", "stl": "STL", "blk": "BLK", "tov": "TOV", "pf": "PF"}


def extra(r):
    return {s: float(r[c]) for s, c in EXTRA_COL.items() if r and r.get(c) not in (None, "")}


def read_csv(p):
    return list(csv.DictReader(open(p, encoding="utf-8")))


def prior_team(R, t):
    s = R["prior_season"]
    def val(team, m):
        return R["teams"][team]["metrics"][m][s]["value"]
    lg = {m: np.mean([val(x, m) for x in R["teams"]]) for m in ("OFF_RATING", "DEF_RATING", "PACE")}
    return {"ortg": round(val(t, "OFF_RATING") - lg["OFF_RATING"] + LG["ortg"], 2),
            "drtg": round(val(t, "DEF_RATING") - lg["DEF_RATING"] + LG["ortg"], 2),
            "pace": round(val(t, "PACE") - lg["PACE"] + LG["pace"], 2)}


def pos_rates(cur):
    """Tasas por minuto de las columnas opcionales por posición (G / F / C): prior de robos, tapones, pérdidas y
    faltas de quien no tiene temporada anterior con esos datos, para que 4 partidos sin un robo no den 0 robos."""
    tot = {}
    for r in cur:
        x = extra(r)
        if len(x) < len(EXTRA_COL) or float(r["MP"]) <= 0:
            continue
        t = tot.setdefault(pos_group(r.get("Pos") or ""), {k: 0.0 for k in ["min", *EXTRA_COL]})
        t["min"] += float(r["MP"])
        for k, v in x.items():
            t[k] += v
    return {g: {k: t[k] / t["min"] for k in EXTRA_COL} for g, t in tot.items()}


def pos_group(pos):
    return "C" if pos.startswith("C") else "G" if pos.endswith("G") else "F"


def team_players(team, cur, prior, out, gtd, prates=None):
    rows = [r for r in cur if r["Team"] == team and float(r["MP"]) >= 8 and r["Player"] not in out]
    for r in rows:
        r["_min"] = float(r["MP"])
    tot = sum(r["_min"] for r in rows)
    scale = 240 / tot
    rows.sort(key=lambda r: (-int(r["GS"]) / max(int(r["G"]), 1), -r["_min"]))
    starters = {r["Player"] for r in rows[:5]}
    players = []
    for r in sorted(rows, key=lambda r: -r["_min"]):
        g = int(r["G"])
        c = {"min": float(r["MP"]), **{s: float(r[col]) for s, col in STAT_COL.items()}, **extra(r)}
        p = prior.get(r["Player"])
        pr = {"min": float(p["MP"]), **{s: float(p[col]) for s, col in STAT_COL.items()}, **extra(p)} if p else dict(c)
        if prates and len(extra(r)) == len(EXTRA_COL) and len(extra(p)) < len(EXTRA_COL):
            # el perfil de tiro es del jugador (su temporada actual); robos, tapones, pérdidas y faltas, de su posición
            pg = prates.get(pos_group(r["Pos"] or ""), {})
            pr.update({k: round((pg[k] if k in ("stl", "blk", "tov", "pf") else c[k] / max(c["min"], 1)) * pr["min"], 2)
                       for k in EXTRA_COL})
        players.append({"team": team, "name": r["Player"], "pos": r["Pos"] or "F",
                        "starter": r["Player"] in starters, "min": round(min(r["_min"] * scale, 38.0), 1),
                        "g_cur": g, "p_play": gtd.get(r["Player"], 1.0), "cur": c, "prior": pr})
    return players


def build_game(slate, g, R, cur, prior):
    a, h = g["away"], g["home"]
    teams = {}
    for t in (h, a):
        adj_r = g.get("adj", {}).get(t, {})
        adj = {"o": adj_r.get("o", 0.0), "d": adj_r.get("d", 0.0), "pace": 0.0}
        if t in g.get("b2b", []):
            for k, v in slate["b2b_adj"].items():
                adj[k] = round(adj[k] + v, 2)
        rows = [r for r in cur if r["Team"] == t]
        teams[t] = {"cur": {"gp": 0, "ortg": LG["ortg"], "drtg": LG["ortg"], "pace": LG["pace"]},  # la BD lo reemplaza
                    "prior": prior_team(R, t), "adj": adj,
                    "context": {"b2b": t in g.get("b2b", []), "roster_note": adj_r.get("why", "")}}
    players = []
    for t in (h, a):
        players += team_players(t, cur, prior, set(g.get("out", [])), g.get("gtd", {}), pos_rates(cur))
    names = {p["name"] for p in players}
    return {
        "game": {"date": slate["date"], "away": a, "home": h, "venue": g.get("venue", ""), "tip_et": g["tip_et"],
                 "notes": g.get("notes", []), "out": g.get("out", []), "gtd": g.get("gtd", {})},
        "db": {"use": True, "k_team": 0.6, "k_dvp": 0.3},
        "league": LG, "shrink_games": 10, "teams": teams,
        "matchup": {h: {}, a: {}}, "players": players,
        "market": {"source": slate["sources"]["schedule_odds"], **g["market"]},
        "prop_lines": {k: v for k, v in slate.get("prop_lines", {}).items() if k in names},
    }


def bet(game, pick, kind, p, odds, extra=None):
    imp = american_to_prob(odds)
    dec = 1 + (odds / 100 if odds > 0 else 100 / -odds)
    return {"game": game, "pick": pick, "kind": kind, "odds": odds, "model_prob": round(p, 3),
            "implied": round(imp, 3), "edge": round(p - imp, 3), "ev_per_unit": round(p * dec - 1, 3),
            "fair_odds": prob_to_american(p), **(extra or {})}


def value_bets(cfg, s, summ):
    h, a = cfg["game"]["home"], cfg["game"]["away"]
    gid = f"{a}@{h}"
    mk = cfg["market"]
    m = s["sh"] - s["sa"]
    tot = s["sh"] + s["sa"]
    sp = mk["spread_home"]
    out = [
        bet(gid, f"{h} ML", "Moneyline", float((m > 0).mean()), mk["ml_home"]),
        bet(gid, f"{a} ML", "Moneyline", float((m < 0).mean()), mk["ml_away"]),
        bet(gid, f"{h} {sp:+g}", "Spread", float((m + sp > 0).mean()), mk.get("spread_odds_home", -110)),
        bet(gid, f"{a} {-sp:+g}", "Spread", float((m + sp < 0).mean()), mk.get("spread_odds_away", -110)),
        bet(gid, f"Over {mk['total']}", "Total", float((tot > mk["total"]).mean()), mk.get("over_odds", -110)),
        bet(gid, f"Under {mk['total']}", "Total", float((tot < mk["total"]).mean()), mk.get("under_odds", -110)),
    ]
    for name, lines in cfg.get("prop_lines", {}).items():
        x = s["players"][name]["draws"]
        for st, v in lines.items():
            ln, o_over = v[0], v[1]
            if st not in x:
                continue
            po = float((x[st] > ln).mean())
            if o_over is not None:
                out.append(bet(gid, f"{name} Over {ln} {st.upper()}", "Prop", po, o_over, {"player": name}))
            if len(v) > 2 and v[2] is not None:
                lu = v[3] if len(v) > 3 else ln
                out.append(bet(gid, f"{name} Under {lu} {st.upper()}", "Prop", float((x[st] < lu).mean()), v[2], {"player": name}))
    return out


def prop_watch(cfg, summ, top=8):
    """Props sin cuota de mercado: umbrales altos de probabilidad con su cuota justa (precio mínimo a exigir)."""
    gid = f"{cfg['game']['away']}@{cfg['game']['home']}"
    rows = []
    for p in summ["players"]:
        if p["min"] < 24:
            continue
        for st in ("pts", "reb", "ast", "fg3m", "pra"):
            d = p[st]
            rows.append({"game": gid, "player": p["name"], "team": p["team"], "stat": st.upper(),
                         "mean": d["mean"], "line": d["line"], "p_over": d["p_over"],
                         "fair_over": prob_to_american(d["p_over"]), "p10": d["p10"], "p90": d["p90"]})
    # los más estables: menor dispersión relativa entre los que más producen
    rows.sort(key=lambda r: -(r["mean"] / max(r["p90"] - r["p10"], 1)))
    return rows[:top]


def main():
    slate_path = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 20000
    slate = json.load(open(slate_path))
    date = slate["date"]
    raw = os.path.join(HERE, "raw", date)
    cur = read_csv(os.path.join(raw, "cur.csv"))
    prior = {r["Player"]: r for r in read_csv(os.path.join(raw, "prior.csv"))}
    R = json.load(open(os.path.join(HERE, "db", "rankings_latest.json")))
    gdir = os.path.join(HERE, "games")
    paths = []
    for g in slate["games"]:
        p = os.path.join(gdir, f"{date}_{g['away']}@{g['home']}.json")
        json.dump(build_game(slate, g, R, cur, prior), open(p, "w"), indent=1, ensure_ascii=False)
        paths.append(p)
    # partidos con JSON hecho a mano que no están en la lista de la jornada
    known = {os.path.basename(p) for p in paths}
    for f in sorted(os.listdir(gdir)):
        if f.startswith(date) and f.endswith(".json") and "_results" not in f and f not in known:
            paths.append(os.path.join(gdir, f))

    games, bets, watch = [], [], []
    for p in paths:
        cfg = load_game(p)
        s = simulate(cfg, n)
        summ = summarize(cfg, s)
        json.dump(summ, open(p.replace(".json", "_results.json"), "w"), indent=1, ensure_ascii=False)
        h, a = cfg["game"]["home"], cfg["game"]["away"]
        vb = value_bets(cfg, s, summ)
        bets += vb
        watch += prop_watch(cfg, summ)
        best = max(vb, key=lambda b: b["edge"])
        games.append({"id": f"{a}@{h}", "away": a, "home": h, "tip_et": cfg["game"]["tip_et"],
                      "proj_away": summ["proj"][a], "proj_home": summ["proj"][h],
                      "win_away": round(summ["win_prob"][a], 3), "win_home": round(summ["win_prob"][h], 3),
                      "proj_total": summ["proj_total"], "proj_margin_home": round(summ["proj"][h] - summ["proj"][a], 1),
                      "market_spread_home": cfg["market"]["spread_home"], "market_total": cfg["market"]["total"],
                      "p_blowout": round(summ["p_blowout_any"], 3), "p_ot": round(summ["p_ot"], 3),
                      "best_pick": best["pick"], "best_edge": best["edge"],
                      "notes": cfg["game"].get("notes", [])})
        print(f"{a}@{h}: {a} {summ['proj'][a]} - {h} {summ['proj'][h]} | win {h} {summ['win_prob'][h]:.1%} "
              f"| total {summ['proj_total']} vs {cfg['market']['total']} | mejor: {best['pick']} edge {best['edge']:+.1%}")
    games.sort(key=lambda g: g["tip_et"])
    bets.sort(key=lambda b: -b["edge"])
    out = {"date": date, "n_sims": n, "sources": slate["sources"], "games": games, "bets": bets, "prop_watch": watch}
    json.dump(out, open(os.path.join(HERE, "slates", f"{date}_results.json"), "w"), indent=1, ensure_ascii=False)
    print("\nTop valor:")
    for b in bets[:12]:
        print(f"  {b['game']:<8} {b['pick']:<36} {b['odds']:>5}  modelo {b['model_prob']:.1%} vs impl {b['implied']:.1%}  edge {b['edge']:+.1%}")


if __name__ == "__main__":
    main()
