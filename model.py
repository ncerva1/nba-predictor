"""
NBA-Predictor 1.4 — simulador Monte Carlo de partido + player props, conectado a la BD de rankings.

Uso:
    python model.py games/2026-10-26_IND@MIL.json [n_sims]          # usa la BD si el JSON trae "db": {"use": true}
    python model.py games/2026-10-26_IND@MIL.json 20000 --db         # fuerza la BD
    python model.py games/2026-10-26_IND@MIL.json 20000 --no-db      # solo valores manuales del JSON
    python model.py games/2026-10-28_GSW@MIN.json 20000 --no-h2h     # sin el peso de los H2H de la temporada pasada

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
 5. Jugadores: minutos aleatorios (DE 5.5-6, 4% de salidas tempranas, bajas de última hora con
    "p_play"; los minutos libres se reparten para que el equipo sume 240; recorte si hay blowout),
    tasas por minuto mezcladas cur/prior con w = g / (g + PLAYER_K); los puntos son la parte del
    jugador en el marcador simulado de su equipo; rebotes, asistencias y triples se escalan por los multiplicadores
    de enfrentamiento (rebote, asistencias, triples concedidos) y por la defensa vs posición del rival,
    y dibujadas con Poisson-gamma (sobre-dispersión).
 6. H2H (1.4): si el jugador enfrentó al rival de hoy la temporada pasada (con su equipo actual o con otro),
    se mide cuánto le subió ese rival respecto a su temporada (lift) y se mezcla con la DvP actual del rival:
    peso n / (n + H2H_K) con n en partidos completos según sus minutos, x0.75 si fue con otro equipo, y menor si
    la defensa del rival cambió mucho entre temporadas. Los minutos se ajustan igual (un tercio si cambió de rol
    o de equipo).
"""
import glob, json, os, re, sys, unicodedata
import numpy as np

STAT_R = {"pts": 20.0, "reb": 12.0, "ast": 14.0, "fg3m": 8.0}  # dispersión NB
TEAM_SD, RHO, MARGIN_SD = 12.0, 0.35, 13.7
# Calibración 1.2 (resultados del 26-oct, ver results/2026-10-26_eval.json):
MIN_SD = {True: 5.5, False: 6.0}  # DE de minutos titular / suplente (antes 3.5 / 4.0; el error real fue ~7)
EXIT_P, EXIT_FRAC = 0.04, (0.15, 0.6)  # prob. de salir pronto (lesión, faltas) y fracción de minutos que juega
PLAYER_K = 6  # partidos de encogimiento de las tasas por minuto hacia la temporada anterior (antes 3)
# 1.3 (pedido de Nicolas, 29-oct): más peso a los enfrentamientos H2H de la temporada pasada contra el mismo
# rival. Peso de las tasas H2H = n / (n + H2H_K) (1 partido 0.25, 2 -> 0.40, 4 -> 0.57). Solo cuentan
# partidos en que jugó >= H2H_MIN_MIN con su equipo actual. Minutos (1.3.1): mismo peso si su rol no cambió
# (titular entonces y ahora, o suplente entonces y ahora).
# 1.3.2: cada H2H cuenta según los minutos que jugó frente a los que se le proyectan hoy (15 de 34 min = 0.44
# partidos), así un H2H corto (lesión, faltas, paliza) pesa poco. Para los minutos solo cuentan los H2H en que jugó
# >= H2H_FULL de sus minutos de hoy, y si cambió de rol pesan un tercio (H2H_ROLE_W) en vez de nada.
H2H_K, H2H_MIN_MIN, H2H_FULL, H2H_ROLE_W = 3, 10, 0.5, 1 / 3
# 1.4 (30-oct, acordado con Nicolas): el H2H entra como "cuánto le sube ese rival" (lift = su tasa por minuto en
# los H2H / su tasa de toda esa temporada), que se aplica a su nivel de hoy y se mezcla con la DvP del rival.
# Así sirven también los H2H con su equipo anterior (pesan H2H_TEAM_W), y el peso baja si el rival cambió mucho
# de una temporada a otra (H2H_CHG: desviaciones típicas entre equipos de su métrica concedida, ya encogida).
# Los minutos H2H pesan un tercio de lo que pesaban en 1.3.2 (H2H_MIN_SCALE): en el backtest del 27 y 28-oct
# siempre empeoraron el error de minutos.
H2H_TEAM_W, LIFT_CAP, H2H_MIN_SCALE = 0.75, (0.5, 2.0), 1 / 3
H2H_CHG = {"pts": ("DEF_RATING", "OPP_PTS"), "reb": ("DEF_RATING", "OPP_REB"),
           "ast": ("DEF_RATING", "OPP_AST"), "fg3m": ("DEF_RATING", "OPP_FG3M")}
MAX_MIN = 44
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


def player_weight(p):
    """Peso de la temporada actual en las tasas por minuto: g / (g + PLAYER_K).
    g = partidos jugados esta temporada ("g_cur"); los JSON viejos solo traen w_cur = g / (g + 3)."""
    if "g_cur" in p:
        g = p["g_cur"]
    else:
        w = p.get("w_cur", 1.0)
        if w >= 1:
            return 1.0  # sin temporada anterior
        g = 3 * w / (1 - w)
    return g / (g + PLAYER_K)


def _norm(name):
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    n = re.sub(r"[^a-z ]", "", n.replace("-", " "))
    return " ".join(w for w in n.split() if w not in ("jr", "sr", "ii", "iii", "iv"))


def name_key(name):  # apellido + 3 primeras letras del nombre: "Ron Holland" = "Ronald Holland II"
    w = _norm(name).split()
    return f"{w[-1]} {w[0][:3]}" if len(w) > 1 else " ".join(w)


def prior_season(date):
    y = int(date[:4]) if int(date[5:7]) >= 8 else int(date[:4]) - 1  # la temporada y/y+1 empieza en octubre
    return f"{y - 1}-{str(y)[2:]}"


def h2h_same_role(p):
    """True si su rol en los H2H (titular en la mayoría o no) es el mismo que hoy."""
    hh = p["h2h"]
    return (hh.get("gs", 0) * 2 >= hh["g"]) == bool(p["starter"])


def h2h_weights(p):
    """(peso base de los H2H en las tasas, peso de los minutos H2H, minutos medios de los H2H completos)."""
    hh, today = p["h2h"], max(p["min"], 1)
    n_eff = sum(min(m / today, 1.0) for m in hh["mins"])
    full = [m for m in hh["mins"] if m >= H2H_FULL * today]
    team_w = 1.0 if hh.get("same_team", True) else H2H_TEAM_W
    role_w = 1.0 if h2h_same_role(p) and hh.get("same_team", True) else H2H_ROLE_W
    wm = len(full) / (len(full) + H2H_K) * role_w * H2H_MIN_SCALE
    return n_eff / (n_eff + H2H_K) * team_w, wm, (np.mean(full) if full else 0.0)


def opp_change(R, opp):
    """Cuánto cambió la defensa del rival de una temporada a otra, por estadística: factor de 0 a 1 que multiplica
    el peso del H2H (1 = igual que la temporada pasada). Usa la mezcla actual/anterior de la BD (encogida por
    partidos jugados) frente a la temporada anterior, en desviaciones típicas entre los 30 equipos."""
    out = {}
    for st, ms in H2H_CHG.items():
        zs = []
        for m in ms:
            vals = [R["teams"][t]["metrics"][m][R["prior_season"]]["value"] for t in R["teams"]]
            met = R["teams"][opp]["metrics"][m]
            zs.append(abs(met["blend"]["value"] - met[R["prior_season"]]["value"]) / max(np.std(vals), 1e-6))
        out[st] = round(1 / (1 + float(np.mean(zs)) ** 2), 3)
    return out


def apply_h2h(cfg, R=None):
    """Añade a cada jugador sus partidos de la temporada pasada contra el rival de hoy (h2h/<temporada>.json),
    con su equipo actual o con otro: p["h2h"] = {g, min, pts, reb, ast, fg3m, gs} (sumas), same_team y chg
    (cuánto pesa según el cambio del rival, con la BD R). Lo usa simulate()."""
    g = cfg["game"]
    path = os.path.join(HERE, "h2h", f"{prior_season(g['date'])}.json")
    if cfg.get("h2h", {}).get("use", True) is False or not os.path.exists(path):
        return cfg
    H = json.load(open(path))["games"]
    rep = {"season": prior_season(g["date"]), "players": {}, "opp_change": {}}
    for t, o in ((g["home"], g["away"]), (g["away"], g["home"])):
        chg = opp_change(R, o) if R and o in R["teams"] else {st: 1.0 for st in H2H_CHG}
        rep["opp_change"][o] = chg
        lines = {}
        for x in H:  # partidos del rival de hoy contra cualquier equipo; nunca los que el jugador jugó CON el rival
            if o not in (x["home"], x["away"]):
                continue
            other = x["away"] if x["home"] == o else x["home"]
            for i, (n, mi, pts, reb, ast, f3) in enumerate(x["players"].get(other, [])):
                if mi >= H2H_MIN_MIN:  # los box scores de ESPN listan primero a los 5 titulares
                    lines.setdefault(name_key(n), []).append((other, (mi, pts, reb, ast, f3, int(i < 5))))
        for p in cfg["players"]:
            if p["team"] != t or name_key(p["name"]) not in lines:
                continue
            got = lines[name_key(p["name"])]
            same = [r for tm, r in got if tm == t]
            rows = same or [r for tm, r in got]  # con su equipo actual si los hay; si no, con el anterior
            tot = np.sum(rows, axis=0)
            p["h2h"] = dict(g=len(rows), mins=[r[0] for r in rows], same_team=bool(same), chg=chg,
                            team=t if same else got[0][0],
                            **{k: float(v) for k, v in zip(("min", "pts", "reb", "ast", "fg3m", "gs"), tot)})
            rep["players"][p["name"]] = dict(g=len(rows), team=p["h2h"]["team"], w=round(h2h_weights(p)[0], 2),
                                             min=round(tot[0] / len(rows), 1), pts=round(tot[1] / len(rows), 1),
                                             same_role=h2h_same_role(p), lift=h2h_lift(p))
    cfg["_h2h_report"] = rep
    return cfg


def h2h_lift(p):
    """Cuánto le subió (o bajó) ese rival en los H2H respecto a su temporada: tasa por minuto H2H / tasa de la
    temporada anterior, por estadística (acotado a LIFT_CAP), y lo mismo para los minutos."""
    hh, pr = p["h2h"], p["prior"]
    out = {}
    for s in STAT_R:
        season = pr[s] / max(pr["min"], 1)
        out[s] = 1.0 if season < 0.02 else round(float(np.clip(hh[s] / hh["min"] / season, *LIFT_CAP)), 3)
    return out


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
    base_min = {}
    for p in players:
        w = player_weight(p)
        base = {s: w * p["cur"][s] / max(p["cur"]["min"], 1) +
                (1 - w) * p["prior"][s] / max(p["prior"]["min"], 1) for s in STAT_R}
        # efecto del rival = mezcla de su DvP actual y de lo que ese rival le subió en los H2H (lift), según
        # cuántos H2H completos hay, con qué equipo los jugó y cuánto cambió el rival
        p["rate"] = {s: base[s] * dvp(p, s) for s in STAT_R}
        base_min[p["name"]] = p["min"]
        hh = p.get("h2h")
        if hh and hh["g"] > 0 and hh["min"] > 0:
            wh, wm, mh2h = h2h_weights(p)
            lift = h2h_lift(p)
            for s in STAT_R:
                ws = wh * hh.get("chg", {}).get(s, 1.0)
                p["rate"][s] = base[s] * ((1 - ws) * dvp(p, s) + ws * lift[s])
            if wm > 0:  # minutos: lo que jugó en los H2H frente a su media de esa temporada
                ml = float(np.clip(mh2h / max(p["prior"]["min"], 1), 0.7, 1.3))
                base_min[p["name"]] = p["min"] * ((1 - wm) + wm * ml)
    for t in (h, a):  # los minutos H2H cambian el reparto, no el total del equipo
        tp = [p for p in players if p["team"] == t]
        k = sum(p["min"] for p in tp) / max(sum(base_min[p["name"]] for p in tp), 1e-6)
        for p in tp:
            base_min[p["name"]] = min(base_min[p["name"]] * k, MAX_MIN)

    # minutos: ruido + salidas tempranas + bajas de última hora (p_play); los minutos que faltan
    # se reparten entre los que juegan para que el equipo siga sumando sus 240
    mins = {}
    for t in (h, a):
        tp = [p for p in players if p["team"] == t]
        base = np.array([base_min[p["name"]] for p in tp])[:, None]
        m_ = np.clip(base + rng.normal(0, 1, (len(tp), n)) * np.array([[MIN_SD[p["starter"]]] for p in tp]), 0, MAX_MIN)
        early = rng.random((len(tp), n)) < EXIT_P
        m_ = np.where(early, m_ * rng.uniform(*EXIT_FRAC, (len(tp), n)), m_)
        plays = rng.random((len(tp), n)) < np.array([[p.get("p_play", 1.0)] for p in tp])
        m_ = np.where(plays, m_, 0.0)
        flex = plays & ~early
        fixed = np.where(flex, 0.0, m_).sum(axis=0)
        scale = np.maximum(base.sum() - fixed, 0) / np.maximum(np.where(flex, m_, 0.0).sum(axis=0), 1)
        m_ = np.where(flex, np.minimum(m_ * scale, MAX_MIN), m_)
        for p, x in zip(tp, m_):
            x = np.where(garbage, x * (0.86 if p["starter"] else 1.18), x)
            mins[p["name"]] = x + ot * (4.0 if p["starter"] else 1.0) * (x > 0)

    # puntos: cada jugador se lleva su parte (tasa x minutos x DvP) del marcador simulado de su equipo,
    # así una baja o una salida temprana pasa sus tiros a los compañeros
    pts_w = {p["name"]: p["rate"]["pts"] * mins[p["name"]] * cfg["matchup"][p["team"]].get("pts_mult", 1)
             for p in players}
    pts_sum = {t: sum(pts_w[p["name"]] for p in players if p["team"] == t) for t in (h, a)}

    res = {}
    for p in players:
        t = p["team"]
        mn = mins[p["name"]]
        tf = team_sim[t] / team_mean[t]
        of = team_mean[opp[t]] / np.maximum(team_sim[opp[t]], 60)
        mm = cfg["matchup"][t]
        means = {
            "pts": team_sim[t] * pts_w[p["name"]] / np.maximum(pts_sum[t], 1e-6),
            "reb": p["rate"]["reb"] * mn * mm.get("reb_mult", 1) * of ** 0.5,
            "ast": p["rate"]["ast"] * mn * mm.get("ast_mult", 1) * tf ** 0.8,
            "fg3m": p["rate"]["fg3m"] * mn * tf ** 0.5 * mm.get("fg3_mult", 1),
        }
        draws = {s: nb_draw(rng, means[s], STAT_R[s]) for s in STAT_R}
        draws["pra"] = draws["pts"] + draws["reb"] + draws["ast"]
        draws["min"] = mn
        res[p["name"]] = dict(team=t, pos=p["pos"], draws=draws, p_play=p.get("p_play", 1.0))

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
        "h2h_report": cfg.get("_h2h_report"),
        "players": [],
    }
    lines = cfg.get("prop_lines", {})
    for name, d in s["players"].items():
        on = d["draws"]["min"] > 0  # las props se anulan si no juega: todo condicionado a que juegue
        row = dict(name=name, team=d["team"], pos=d["pos"], min=round(float(d["draws"]["min"][on].mean()), 1),
                   p_play=round(float(on.mean()), 3))
        for st in ("pts", "reb", "ast", "fg3m", "pra"):
            x = d["draws"][st][on]
            fl = float(np.floor(np.mean(x)) + 0.5) if st != "fg3m" else float(np.floor(np.mean(x) - 0.25) + 0.5)
            fl = max(fl, 0.5)
            mu = float(x.mean())
            ks = range(max(1, int(mu * 0.6)), int(np.ceil(mu * 1.45)) + 2)
            row[st] = dict(mean=round(mu, 1), p10=float(np.percentile(x, 10)),
                           p90=float(np.percentile(x, 90)), line=fl,
                           p_over=round(float((x > fl).mean()), 3),
                           ladder={int(k): round(float((x >= k).mean()), 3) for k in ks})
            if name in lines and st in lines[name]:
                ln, odds = lines[name][st][:2]  # [línea, cuota over] o [línea, over, under]
                po = float((x > ln).mean())
                row[st]["market"] = dict(line=ln, odds=odds, p_over=round(po, 3),
                                         implied=round(american_to_prob(odds), 3),
                                         edge=round(po - american_to_prob(odds), 3))
        out["players"].append(row)
    return out


def load_game(path, use_db=None, use_h2h=None):
    """Carga el JSON del partido, lo completa con la BD de rankings si corresponde y le añade los H2H de
    la temporada pasada (desactivar con "h2h": {"use": false} en el JSON o use_h2h=False)."""
    cfg = json.load(open(path))
    if use_h2h is not None:
        cfg.setdefault("h2h", {})["use"] = use_h2h
    want = cfg.get("db", {}).get("use", False) if use_db is None else use_db
    if want:
        from db_link import apply_db
        rk = cfg.get("db", {}).get("rankings") or os.path.join(HERE, "db", "rankings_latest.json")
        cfg = apply_db(cfg, rk)
        return apply_h2h(cfg, json.load(open(rk)))
    return apply_h2h(cfg)


if __name__ == "__main__":
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    flag = True if "--db" in sys.argv else (False if "--no-db" in sys.argv else None)
    path = args[0]
    n = int(args[1]) if len(args) > 1 else 20000
    cfg = load_game(path, flag, False if "--no-h2h" in sys.argv else None)
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
