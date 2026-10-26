"""
Puente entre la BD de rankings (db/rankings_latest.json) y el modelo (model.py).

apply_db(cfg, rankings_path) devuelve una copia del JSON del partido con:

 1. teams[T].cur  <- gp, ORtg, DRtg y pace de la temporada actual (NBA.com), re-escalados a la media de liga
    del JSON (league.ortg / league.pace) para no mezclar escalas con el prior ajustado por roster.
    El prior y los ajustes de contexto (adj: lesiones, b2b...) siguen siendo manuales.
 2. matchup[T]    <- multiplicadores calculados con los valores mezclados (blend) del rival:
      reb_mult  = rebotes totales que concede el rival / media de liga
      ast_mult  = asistencias que concede el rival / media de liga
      fg3_mult  = triples que concede el rival / media de liga
      style_per100 = interacción de estilos (play types): sum_p (frec_T(p) - frec_liga(p)) *
                     (PPP_concedido_rival(p) - PPP_liga(p)) * 100. Positivo = lo que T hace más de lo normal
                     es justo lo que el rival defiende peor. Se suma a los puntos por 100 posesiones.
      pts_mult = 1 y ft_bonus_pts = 0 (el nivel de puntos ya lo dan ORtg/DRtg; evitar doble conteo)
    Todos con encogimiento K hacia 1 (cfg["db"]["k_team"], por defecto 0.6) y, si el JSON trae
    "matchup_manual", se multiplican/suman encima (ajuste humano explícito).
 3. players[i].dvp <- defensa vs posición del rival (statpick, por 48 min) para pts/reb/ast/fg3m:
      mult = 1 + K_dvp * (valor_rival_pos / media_liga_pos - 1), K_dvp por defecto 0.3,
    normalizado dentro del equipo (promedio ponderado por minutos = 1) para que solo REDISTRIBUYA la
    producción entre posiciones; el nivel del equipo lo fijan los pasos 1-2.
    Posiciones G / F / G-F / F-C se promedian entre las posiciones que cubren.

Todo lo que cambia queda en cfg["_db_report"] (se imprime y se guarda en *_results.json).
"""
import copy, json
import numpy as np

PT = ['Isolation', 'Transition', 'PRBallHandler', 'PRRollman', 'Postup', 'Spotup', 'Handoff', 'Cut', 'OffScreen', 'OffRebound']
POS_MAP = {"G": ["PG", "SG"], "F": ["SF", "PF"], "W": ["SG", "SF"]}
DVP_STAT = {"pts": "PTS", "reb": "REB", "ast": "AST", "fg3m": "3PM"}


def _metric(R, t, m, which="blend"):
    d = R["teams"][t]["metrics"].get(m, {})
    v = d.get(which) or d.get(R["season"])
    return None if v is None else v["value"]


def _league_mean(R, m, which="blend"):
    vals = [_metric(R, t, m, which) for t in R["teams"]]
    vals = [v for v in vals if v is not None]
    return float(np.mean(vals)) if vals else None


def _pt(R, t, key, which="blend"):
    d = R["teams"][t]["play_types"].get(key, {})
    return d.get(which) or d.get(R["season"])


def _positions(pos):
    out = []
    for part in pos.replace("/", "-").split("-"):
        out += POS_MAP.get(part, [part])
    return [p for p in out if p in ("PG", "SG", "SF", "PF", "C")] or ["SF"]


def apply_db(cfg, rankings_path):
    cfg = copy.deepcopy(cfg)
    R = json.load(open(rankings_path))
    dbc = cfg.get("db", {})
    k_team, k_dvp = dbc.get("k_team", 0.6), dbc.get("k_dvp", 0.3)
    h, a = cfg["game"]["home"], cfg["game"]["away"]
    opp = {h: a, a: h}
    rep = {"snapshot": R["snapshot_date"], "season": R["season"], "teams": {}, "players": {}}

    # ---- 1. temporada actual -> cur (re-escalada a la media de liga del JSON)
    cur = R["season"]
    lg_o_cur, lg_d_cur = _league_mean(R, "OFF_RATING", cur), _league_mean(R, "DEF_RATING", cur)
    lg_p_cur = _league_mean(R, "PACE", cur)
    L = cfg["league"]
    for t in (h, a):
        old = dict(cfg["teams"][t]["cur"])
        new = {
            "gp": R["teams"][t]["gp"],
            "ortg": round(_metric(R, t, "OFF_RATING", cur) - lg_o_cur + L["ortg"], 2),
            "drtg": round(_metric(R, t, "DEF_RATING", cur) - lg_d_cur + L["ortg"], 2),
            "pace": round(_metric(R, t, "PACE", cur) - lg_p_cur + L["pace"], 2),
        }
        cfg["teams"][t]["cur"] = new
        rep["teams"][t] = {"cur_json": old, "cur_db": new}

    # ---- 2. multiplicadores de enfrentamiento
    manual = cfg.get("matchup_manual", {})
    lg = {m: _league_mean(R, m) for m in ("OPP_REB", "OPP_AST", "OPP_FG3M")}
    lg_freq = {p: np.mean([_pt(R, t, f"{p}|off")["freq"] for t in R["teams"]]) for p in PT}
    lg_dppp = {p: np.mean([_pt(R, t, f"{p}|def")["ppp"] for t in R["teams"]]) for p in PT}
    old_matchup = cfg.get("matchup", {})
    cfg["matchup"] = {}
    for t in (h, a):
        o = opp[t]
        shrink = lambda ratio: round(1 + k_team * (ratio - 1), 4)
        mm = {
            "reb_mult": shrink(_metric(R, o, "OPP_REB") / lg["OPP_REB"]),
            "ast_mult": shrink(_metric(R, o, "OPP_AST") / lg["OPP_AST"]),
            "fg3_mult": shrink(_metric(R, o, "OPP_FG3M") / lg["OPP_FG3M"]),
            "pts_mult": 1.0,
            "ft_bonus_pts": 0.0,
        }
        style, parts = 0.0, {}
        for p in PT:
            fo, dd = _pt(R, t, f"{p}|off"), _pt(R, o, f"{p}|def")
            c = (fo["freq"] - lg_freq[p]) * (dd["ppp"] - lg_dppp[p]) * 100
            style += c
            parts[p] = round(float(c), 3)
        mm["style_per100"] = round(k_team * style, 3)
        for key, v in manual.get(t, {}).items():  # ajuste humano encima
            if key.endswith("_mult"):
                mm[key] = round(mm.get(key, 1.0) * v, 4)
            else:
                mm[key] = round(mm.get(key, 0.0) + v, 3)
        cfg["matchup"][t] = mm
        top = sorted(parts.items(), key=lambda kv: -abs(kv[1]))[:3]
        rep["teams"][t].update(matchup_json=old_matchup.get(t), matchup_db=mm, style_top=top,
                               opp_ranks={m: R["teams"][o]["metrics"][m]["blend"]["rank"]
                                          for m in ("DEF_RATING", "OPP_REB", "OPP_AST", "OPP_FG3M", "OPP_PTS_PAINT")
                                          if "blend" in R["teams"][o]["metrics"].get(m, {})})

    # ---- 3. defensa vs posición por jugador
    dvp_lg = {}
    for pos in ("PG", "SG", "SF", "PF", "C"):
        for s, col in DVP_STAT.items():
            dvp_lg[(pos, s)] = np.mean([R["teams"][t]["dvp"][pos][col]["value"] for t in R["teams"] if R["teams"][t]["dvp"]])
    for t in (h, a):
        o = opp[t]
        ps = [p for p in cfg["players"] if p["team"] == t]
        if not R["teams"][o]["dvp"]:
            continue
        raw = {}
        for p in ps:
            poss = _positions(p["pos"])
            raw[p["name"]] = {s: 1 + k_dvp * (np.mean([R["teams"][o]["dvp"][q][col]["value"] / dvp_lg[(q, s)] for q in poss]) - 1)
                              for s, col in DVP_STAT.items()}
        tot_min = sum(p["min"] for p in ps)
        for s in DVP_STAT:
            avg = sum(raw[p["name"]][s] * p["min"] for p in ps) / tot_min
            for p in ps:
                p.setdefault("dvp", {})[s] = round(raw[p["name"]][s] / avg, 4)
        for p in ps:
            rep["players"][p["name"]] = {"pos": p["pos"], "vs": o, **p["dvp"]}
    cfg["_db_report"] = rep
    return cfg


def print_report(rep):
    print(f"[BD] snapshot {rep['snapshot']} ({rep['season']})")
    for t, d in rep["teams"].items():
        print(f"  {t}: cur JSON {d['cur_json']} -> BD {d['cur_db']}")
        m = d["matchup_db"]
        print(f"      matchup BD: reb x{m['reb_mult']} ast x{m['ast_mult']} 3PM x{m['fg3_mult']} "
              f"estilo {m['style_per100']:+.2f} pts/100 (top: {d['style_top']})  | antes JSON: {d['matchup_json']}")
        print(f"      ranks del rival (blend): {d['opp_ranks']}")
    movers = sorted(rep["players"].items(), key=lambda kv: -max(abs(kv[1][s] - 1) for s in DVP_STAT))[:6]
    print("  DvP (mayores ajustes): " + "; ".join(
        f"{n} ({d['pos']}) " + " ".join(f"{s} x{d[s]:.2f}" for s in DVP_STAT if abs(d[s] - 1) >= 0.03) for n, d in movers))
