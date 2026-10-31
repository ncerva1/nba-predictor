"""
"Sin un jugador" (pedido de Nicolas, 31-oct; idea de app.ludocomparador.com): cómo cambian las estadísticas de
los compañeros si un jugador no juega.

Dos fuentes, que la web muestra juntas:
 1. Modelo: se repite la simulación del partido con ese jugador fuera (p_play = 0) y con la misma semilla y el
    mismo número de simulaciones. Como el jugador sigue en la lista, los números aleatorios son los mismos y la
    diferencia con la simulación publicada es solo el efecto de su baja: sus minutos se reparten entre los que
    juegan (el equipo sigue sumando 240) y sus tiros pasan a los compañeros.
 2. Real: partidos de esta temporada (results/<fecha>.json, antes del partido) de su equipo en que no jugó,
    frente a los que sí jugó, con el promedio de cada compañero en cada grupo. También para los que ya están
    fuera hoy (game.out), que el modelo ya no proyecta.

Uso desde build_site.py:  r["without"] = without_view(cfg, s, summ, n)
"""
import copy, glob, json, os

import numpy as np

from model import name_key, season_of, simulate

HERE = os.path.dirname(os.path.abspath(__file__))
STATS = ("pts", "reb", "ast", "fg3m", "pra", "pr", "pa", "ra", "stl", "blk", "fgm", "fga", "fg3a", "dd")
REAL = ("min", "pts", "reb", "ast", "fg3m", "pra", "pr", "pa", "ra", "stl", "blk", "stocks", "tov",
        "fgm", "fga", "fg3a", "ftm", "fta")
MIN_ABSENT, MIN_GTD, MAX_ABSENT = 20.0, 12.0, 7  # a quién se le simula la baja (minutos proyectados)
MIN_MATE = 6.0  # compañeros que se muestran (minutos proyectados con él)
# El marcador del equipo no cambia solo por quitar a un jugador (en el slate eso va en "adj"), así que aquí se le
# resta al ataque del equipo una parte (LOST_SHARE) de los puntos que aporta por encima de un suplente en sus minutos
# (Tatum, ~27 pts en 35 min frente a ~12 de un suplente: unos -4.5 pts; un rol de 8 pts en 20 min: casi nada).
LOST_SHARE = 0.3


def _fair(x):  # misma línea justa que model.summarize
    mu = np.mean(x)
    return max(float(np.floor(mu) + 0.5) if mu >= 4 else float(np.floor(mu - 0.25) + 0.5), 0.5)


def _market_line(cfg, name, st):
    v = cfg.get("prop_lines", {}).get(name, {}).get(st)
    if not v:
        return None
    if v[1] is None and len(v) > 3:  # solo under, con su propia línea
        return v[3]
    return v[0]


def _rows(s, team, lines=None, cfg=None):
    """{jugador: {"min": m, st: [media, P(over línea)(, línea)]}} de los que juegan, condicionado a que jueguen."""
    out = {}
    for name, d in s["players"].items():
        if d["team"] != team:
            continue
        on = d["draws"]["min"] > 0
        if on.mean() < 0.02 or (lines is None and d["draws"]["min"][on].mean() < MIN_MATE):
            continue
        row = {"min": round(float(d["draws"]["min"][on].mean()), 1)}
        for st in STATS:
            if st not in d["draws"]:
                continue
            x = d["draws"][st][on]
            if lines is None:  # simulación base: fija la línea (la de la casa si hay, si no la justa)
                ln = _market_line(cfg, name, st)
                ln = _fair(x) if ln is None else ln
                row[st] = [round(float(x.mean()), 1), round(float((x > ln).mean()), 2), ln]
            elif name in lines and st in lines[name]:
                row[st] = [round(float(x.mean()), 1), round(float((x > lines[name][st]).mean()), 2)]
        out[name] = row
    return out


def _team_of(names, date):
    """Equipo de los jugadores que no están en el JSON del partido (los OUT de hoy), según raw/<fecha>/cur.csv."""
    import csv
    want, team = {name_key(n): n for n in names}, {}
    for cp in [os.path.join(HERE, "raw", date, "cur.csv")] + sorted(glob.glob(os.path.join(HERE, "raw", "*", "cur.csv")),
                                                                    reverse=True):
        if os.path.exists(cp):
            for r in csv.DictReader(open(cp, encoding="utf-8")):
                k = name_key(r["Player"])
                if k in want and want[k] not in team:
                    team[want[k]] = r["Team"]
    return team


def _season_games(team, date):
    """Box scores de los partidos de `team` esta temporada antes de `date`: [(fecha, rival, {name_key: línea})]."""
    from evaluate import actual_line
    season, out = season_of(date), []
    for rp in sorted(glob.glob(os.path.join(HERE, "results", "20??-??-??.json"))):
        R = json.load(open(rp))
        d = R["date"]
        if d >= date or season_of(d) != season:
            continue
        for key, x in R["games"].items():
            away, home = key.split("@")
            if team not in (away, home):
                continue
            lines = {}
            for n in x["players"]:
                al = actual_line(x["players"], n)
                if al:
                    lines[name_key(n)] = al
            out.append((d, home if team == away else away, lines))
    return out


def _avg(lines):
    if not lines:
        return None
    o = {"n": len(lines)}
    for k in REAL:
        v = [l[k] for l in lines if k in l]
        if len(v) == len(lines):
            o[k] = round(float(np.mean(v)), 1)
    return o


def real_splits(team, absent, roster, date):
    """Promedio real de cada compañero en los partidos de esta temporada en que `absent` no jugó y en los que sí.
    Un partido cuenta para el equipo si algún jugador del roster de hoy aparece en el box score con minutos."""
    games = _season_games(team, date)
    ka, out_g, in_g = name_key(absent), [], []
    for d, opp, lines in games:
        played = {k for k in (name_key(n) for n in roster) if k in lines and lines[k]["min"] > 0}
        if not played - {ka}:
            continue  # el box score no es de este equipo (o vino vacío)
        (in_g if ka in played else out_g).append((d, opp, lines))
    if not out_g:
        return None
    mates = {}
    for n in roster:
        k = name_key(n)
        if k == ka:
            continue
        o = _avg([l[k] for _, _, l in out_g if k in l and l[k]["min"] > 0])
        i = _avg([l[k] for _, _, l in in_g if k in l and l[k]["min"] > 0])
        if o:
            mates[n] = {"out": o, "in": i}
    return {"n_out": len(out_g), "n_in": len(in_g), "games_out": [f"{d} vs {opp}" for d, opp, _ in out_g],
            "mates": mates}


def without_view(cfg, s, n, seed=7):
    """Para cada equipo: líneas base de los compañeros y, por cada jugador importante, la simulación sin él y sus
    partidos reales sin él. `s` es la simulación publicada (mismo cfg, n y semilla)."""
    g = cfg["game"]
    out = {}
    for t in (g["away"], g["home"]):
        tp = [p for p in cfg["players"] if p["team"] == t]
        base = _rows(s, t, cfg=cfg)
        lines = {nm: {st: v[2] for st, v in r.items() if st != "min"} for nm, r in base.items()}
        mins = {nm: r["min"] for nm, r in base.items()}
        cand = [p for p in tp if p["name"] in mins and p.get("p_play", 1.0) > 0 and
                (mins[p["name"]] >= MIN_ABSENT or (p.get("p_play", 1.0) < 1 and mins[p["name"]] >= MIN_GTD))]
        cand = sorted(cand, key=lambda p: -mins[p["name"]])[:MAX_ABSENT]
        roster = [p["name"] for p in tp] + [x["name"] for x in g.get("reserves", {}).get(t, [])]
        bench = [r["pts"][0] / r["min"] for nm, r in base.items()
                 if not next(q for q in tp if q["name"] == nm)["starter"] and r["min"] > 0]
        repl = float(np.median(bench)) if bench else 0.35  # puntos por minuto de un suplente del equipo
        pace = s["pace"]
        absent = []
        for p in cand:
            c = copy.deepcopy(cfg)
            for q in c["players"]:
                if q["name"] == p["name"]:
                    q["p_play"] = 0.0
            r0 = base[p["name"]]
            lost = round(LOST_SHARE * max(r0["pts"][0] - repl * r0["min"], 0) * p.get("p_play", 1.0), 1)
            c["teams"][t]["adj"]["o"] = c["teams"][t]["adj"].get("o", 0) - lost * 100 / pace
            sw = simulate(c, n, seed)
            absent.append(dict(name=p["name"], pos=p["pos"], min=mins[p["name"]], p_play=p.get("p_play", 1.0),
                               team_pts=[round(float(s["sh" if t == g["home"] else "sa"].mean()), 1),
                                         round(float(sw["sh" if t == g["home"] else "sa"].mean()), 1)],
                               model=_rows(sw, t, lines), real=real_splits(t, p["name"], roster, g["date"])))
        out_today = []
        outs = g.get("out", [])
        teams = _team_of(outs, g["date"]) if outs else {}
        for nm in outs:
            if teams.get(nm) == t:
                rs = real_splits(t, nm, roster + [nm], g["date"])
                out_today.append(dict(name=nm, real=rs))
        out[t] = dict(base=base, absent=absent, out_today=out_today)
    return out
