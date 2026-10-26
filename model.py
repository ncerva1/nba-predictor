"""
NBA-Predictor 1.1 — simulador Monte Carlo de partido + player props, conectado a la BD de rankings.

Uso:
    python model.py games/2026-10-26_IND@MIL.json [n_sims]          # usa la BD si el JSON trae "db": {"use": true}
    python model.py games/2026-10-26_IND@MIL.json 20000 --db         # fuerza la BD
    python model.py games/2026-10-26_IND@MIL.json 20000 --no-db      # solo valores manuales del JSON

Flujo:
 1. Rating por equipo = mezcla (shrinkage) de la temporada actual con un prior
    ajustado por roster:  w = gp / (gp + shrink_games).
    Con BD: la temporada actual (gp, ORtg, DRtg, pace) y la media de liga salen de db/rankings_latest.json;
    el prior ajustado por roster y los ajustes de contexto siguen siendo del JSON del partido.
 2. Ajustes de contexto (lesiones, back-to-back, localía) en puntos por 100 pos.
    Con BD se suma el ajuste de estilo (play types de un equipo vs defensa del otro), ver db_link.py.
 3. Puntos esperados = (lg + (ORtg_A - lg) + (DRtg_B - lg) + estilo) * posesiones / 100.
 4. Simulación: puntos con componente común (pace/ritmo arbitral) + independiente,
    calibrado a DE ~12 por equipo y ~13.7 de margen. Trayectoria del marcador con
    puente browniano (48 min) para medir ventaja máxima -> blowouts.
 5. Jugadores: minutos aleatorios (recorte si hay blowout), tasas por minuto
    mezcladas cur/prior, escaladas por el marcador simulado del equipo, por los multiplicadores
    de enfrentamiento (rebote, asistencias, triples concedidos) y por la defensa vs posición del rival,
    y dibujadas con Poisson-gamma (sobre-dispersión).
"""
import json, os, sys
import numpy as np

STAT_R = {"pts": 20.0, "reb": 12.0, "ast": 14.0, "fg3m": 8.0}  # dispersión NB
TEAM_SD, RHO, MARGIN_SD = 12.0, 0.35, 13.7
HERE = os.path.dirname(os.path.abspath(__file__))


def american_to_prob(odds):
    return 100 / (odds + 100) if odds > 0 else -odds / (-odds + 100)


def prob_to_american(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def team_ratings(cfg):
    lg, k = cfg["league"], cfg["shrink_games"]
    out = {}
    for t, d in cfg["teams"].items():
        w = d["cur"]["gp"] / (d["cur"]["gp"] + k)
        o = w * d["cur"]["ortg"] + (1 - w) * d["prior"]["ortg"] + d["adj"]["o"]
        de = w * d["cur"]["drtg"] + (1 - w) * d["prior"]["drtg"] + d["adj"]["d"]
        pace = w * d["cur"]["pace"] + (1 - w) * d["prior"]["pace"] + d["adj"].get("pace", 0)
        out[t] = dict(o=o, d=de, pace=pace, w=w)
    return out


def expected_scores(cfg, r):
    lg = cfg["league"]
    h, a = cfg["game"]["home"], cfg["game"]["away"]
    ha = lg["home_adv_per100"]
    pace = (r[h]["pace"] + r[a]["pace"]) / 2  # ritmo combinado
    st_h = cfg["matchup"][h].get("style_per100", 0)
    st_a = cfg["matchup"][a].get("style_per100", 0)
    ppp_h = lg["ortg"] + (r[h]["o"] - lg["ortg"]) + (r[a]["d"] - lg["ortg"]) + ha + st_h
    ppp_a = lg["ortg"] + (r[a]["o"] - lg["ortg"]) + (r[h]["d"] - lg["ortg"]) - ha + st_a
    mh = ppp_h * pace / 100 + cfg["matchup"][h].get("ft_bonus_pts", 0)
    ma = ppp_a * pace / 100 + cfg["matchup"][a].get("ft_bonus_pts", 0)
    return mh, ma, pace, ppp_h, ppp_a


def nb_draw(rng, mean, r):
    lam = rng.gamma(shape=r, scale=np.maximum(mean, 1e-6) / r)
    return rng.poisson(lam)


def simulate(cfg, n=20000, seed=7):
    rng = np.random.default_rng(seed)
    r = team_ratings(cfg)
    mh, ma, pace, ppp_h, ppp_a = expected_scores(cfg, r)
    h, a = cfg["game"]["home"], cfg["game"]["away"]

    common = rng.normal(0, TEAM_SD * np.sqrt(RHO), n)
    sh = mh + common + rng.normal(0, TEAM_SD * np.sqrt(1 - RHO), n)
    sa = ma + common + rng.normal(0, TEAM_SD * np.sqrt(1 - RHO), n)
    sh, sa = np.round(sh), np.round(sa)
    ot = np.zeros(n, dtype=int)
    tie = sh == sa
    while tie.any():  # prórrogas de 5 min
        ot[tie] += 1
        k = tie.sum()
        sh[tie] += np.round(rng.normal(10.5, 3.5, k))
        sa[tie] += np.round(rng.normal(10.5, 3.5, k))
        tie = sh == sa

    # trayectoria del margen (local - visitante): puente browniano, 48 pasos
    steps = 48
    final_m = sh - sa
    incr = rng.normal(0, MARGIN_SD / np.sqrt(steps), (n, steps))
    walk = np.cumsum(incr, axis=1)
    tgrid = np.arange(1, steps + 1) / steps
    bridge = walk - tgrid * walk[:, -1:] + tgrid * final_m[:, None]
    max_lead_h = np.maximum(bridge.max(axis=1), 0)
    max_lead_a = np.maximum(-bridge.min(axis=1), 0)
    blowout_any = (max_lead_h >= 20) | (max_lead_a >= 20)
    garbage = np.abs(final_m) >= 15  # minutos recortados a titulares

    # ---------- jugadores ----------
    players = cfg["players"]
    team_mean = {h: mh, a: ma}
    team_sim = {h: sh, a: sa}
    opp = {h: a, a: h}
    dvp = lambda p, s: p.get("dvp", {}).get(s, 1.0)  # multiplicador defensa vs posición (1 si no hay BD)
    # tasas por minuto mezcladas
    for p in players:
        w = p["w_cur"]
        p["rate"] = {s: w * p["cur"][s] / max(p["cur"]["min"], 1) +
                     (1 - w) * p["prior"][s] / max(p["prior"]["min"], 1) for s in STAT_R}
    # normalizar puntos para que la suma de jugadores = puntos esperados del equipo
    # (la DvP solo redistribuye los puntos entre jugadores; el nivel lo fijan los ratings)
    norm = {}
    for t in (h, a):
        exp_pts = sum(p["rate"]["pts"] * p["min"] * dvp(p, "pts") for p in players if p["team"] == t)
        norm[t] = team_mean[t] / exp_pts

    res = {}
    for p in players:
        t = p["team"]
        sd = 3.5 if p["starter"] else 4.0
        mins = np.clip(rng.normal(p["min"], sd, n), 0, 44)
        mins = np.where(garbage, mins * (0.86 if p["starter"] else 1.18), mins)
        mins = mins + ot * (4.0 if p["starter"] else 1.0)
        tf = team_sim[t] / team_mean[t]
        of = team_mean[opp[t]] / np.maximum(team_sim[opp[t]], 60)
        mm = cfg["matchup"][t]
        means = {
            "pts": p["rate"]["pts"] * mins * norm[t] * tf * mm.get("pts_mult", 1) * dvp(p, "pts"),
            "reb": p["rate"]["reb"] * mins * mm.get("reb_mult", 1) * of ** 0.5 * dvp(p, "reb"),
            "ast": p["rate"]["ast"] * mins * mm.get("ast_mult", 1) * tf ** 0.8 * dvp(p, "ast"),
            "fg3m": p["rate"]["fg3m"] * mins * tf ** 0.5 * mm.get("fg3_mult", 1) * dvp(p, "fg3m"),
        }
        draws = {s: nb_draw(rng, means[s], STAT_R[s]) for s in STAT_R}
        draws["pra"] = draws["pts"] + draws["reb"] + draws["ast"]
        draws["min"] = mins
        res[p["name"]] = dict(team=t, pos=p["pos"], draws=draws)

    return dict(ratings=r, mh=mh, ma=ma, pace=pace, ppp_h=ppp_h, ppp_a=ppp_a,
                sh=sh, sa=sa, ot=ot, max_lead_h=max_lead_h, max_lead_a=max_lead_a,
                blowout_any=blowout_any, players=res, n=n)


def fair_line(x):
    """línea .5 más cercana a la mediana."""
    return np.floor(np.median(x)) + 0.5 if np.median(x) % 1 == 0 else np.round(np.median(x) - 0.5) + 0.5


def summarize(cfg, s):
    h, a = cfg["game"]["home"], cfg["game"]["away"]
    sh, sa = s["sh"], s["sa"]
    m = sh - sa
    tot = sh + sa
    mk = cfg["market"]
    out = {
        "teams": {t: {k: round(v, 1) if isinstance(v, float) else v for k, v in s["ratings"][t].items()} for t in (h, a)},
        "pace": round(s["pace"], 1),
        "proj": {h: round(float(sh.mean()), 1), a: round(float(sa.mean()), 1)},
        "median": {h: float(np.median(sh)), a: float(np.median(sa))},
        "win_prob": {h: float((m > 0).mean()), a: float((m < 0).mean())},
        "fair_ml": {h: prob_to_american((m > 0).mean()), a: prob_to_american((m < 0).mean())},
        "proj_margin_away": round(float(-m.mean()), 1),
        "proj_total": round(float(tot.mean()), 1),
        "p_home_cover": float((m + mk["spread_home"] > 0).mean()),
        "p_over": float((tot > mk["total"]).mean()),
        "p_ot": float((s["ot"] > 0).mean()),
        "p_blowout_any": float(s["blowout_any"].mean()),
        "p_blowout_by": {h: float((s["max_lead_h"] >= 20).mean()), a: float((s["max_lead_a"] >= 20).mean())},
        "market_implied": {a: american_to_prob(mk["ml_away"]), h: american_to_prob(mk["ml_home"])},
        "scenarios": {
            f"{a} por 10+": float((m <= -10).mean()),
            f"{a} por 1-9": float(((m < 0) & (m > -10)).mean()),
            f"{h} por 1-9": float(((m > 0) & (m < 10)).mean()),
            f"{h} por 10+": float((m >= 10).mean()),
        },
        "margin_hist": np.histogram(-m, bins=np.arange(-40, 42, 4))[0].tolist(),
        "total_pcts": {q: float(np.percentile(tot, q)) for q in (10, 25, 50, 75, 90)},
        "db_report": cfg.get("_db_report"),
        "players": [],
    }
    lines = cfg.get("prop_lines", {})
    for name, d in s["players"].items():
        row = dict(name=name, team=d["team"], pos=d["pos"], min=round(float(d["draws"]["min"].mean()), 1))
        for st in ("pts", "reb", "ast", "fg3m", "pra"):
            x = d["draws"][st]
            fl = float(np.floor(np.mean(x)) + 0.5) if st != "fg3m" else float(np.floor(np.mean(x) - 0.25) + 0.5)
            fl = max(fl, 0.5)
            mu = float(x.mean())
            ks = range(max(1, int(mu * 0.6)), int(np.ceil(mu * 1.45)) + 2)
            row[st] = dict(mean=round(mu, 1), p10=float(np.percentile(x, 10)),
                           p90=float(np.percentile(x, 90)), line=fl,
                           p_over=round(float((x > fl).mean()), 3),
                           ladder={int(k): round(float((x >= k).mean()), 3) for k in ks})
            if name in lines and st in lines[name]:
                ln, odds = lines[name][st]
                po = float((x > ln).mean())
                row[st]["market"] = dict(line=ln, odds=odds, p_over=round(po, 3),
                                         implied=round(american_to_prob(odds), 3),
                                         edge=round(po - american_to_prob(odds), 3))
        out["players"].append(row)
    return out


def load_game(path, use_db=None):
    """Carga el JSON del partido y, si corresponde, lo completa con la BD de rankings."""
    cfg = json.load(open(path))
    want = cfg.get("db", {}).get("use", False) if use_db is None else use_db
    if want:
        from db_link import apply_db
        rk = cfg.get("db", {}).get("rankings") or os.path.join(HERE, "db", "rankings_latest.json")
        cfg = apply_db(cfg, rk)
    return cfg


if __name__ == "__main__":
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    flag = True if "--db" in sys.argv else (False if "--no-db" in sys.argv else None)
    path = args[0]
    n = int(args[1]) if len(args) > 1 else 20000
    cfg = load_game(path, flag)
    s = simulate(cfg, n)
    summ = summarize(cfg, s)
    outp = path.replace(".json", "_results.json")
    json.dump(summ, open(outp, "w"), indent=1, ensure_ascii=False)
    h, a = cfg["game"]["home"], cfg["game"]["away"]
    if cfg.get("_db_report"):
        from db_link import print_report
        print_report(cfg["_db_report"])
    print(f"Ratings: {summ['teams']}  pace {summ['pace']}")
    print(f"Proyección: {a} {summ['proj'][a]} - {h} {summ['proj'][h]} | total {summ['proj_total']}")
    print(f"Win%: {a} {summ['win_prob'][a]:.1%} (justo {summ['fair_ml'][a]}) / {h} {summ['win_prob'][h]:.1%}")
    print(f"Mercado implícito: {summ['market_implied']}")
    print(f"{h} +{cfg['market']['spread_home']} cubre: {summ['p_home_cover']:.1%} | Over {cfg['market']['total']}: {summ['p_over']:.1%}")
    print(f"OT {summ['p_ot']:.1%} | Blowout (20+ en algún momento): {summ['p_blowout_any']:.1%} {summ['p_blowout_by']}")
    print("Escenarios:", {k: f"{v:.1%}" for k, v in summ["scenarios"].items()})
    for p in summ["players"]:
        print(f"{p['team']} {p['name']:<22} min {p['min']:>4} | PTS {p['pts']['mean']:>5} ({p['pts']['p10']:.0f}-{p['pts']['p90']:.0f}) "
              f"REB {p['reb']['mean']:>4} AST {p['ast']['mean']:>4} 3PM {p['fg3m']['mean']:>3} PRA {p['pra']['mean']:>5}"
              + "".join(f" | MKT {st} {p[st]['market']}" for st in ('pts','reb','ast','fg3m') if 'market' in p[st]))
