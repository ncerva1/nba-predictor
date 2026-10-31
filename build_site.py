"""
Genera los datos de la web (docs/) a partir de los JSON de partidos en games/.

Uso:
    python build_site.py                 # todos los partidos de games/
    python build_site.py 2026-10-26      # solo los de esa fecha
    python build_site.py --sims 40000

Para cada games/<fecha>_<AWAY>@<HOME>.json corre el modelo (con la BD si el JSON lo pide
y db/rankings_latest.json existe; si no existe intenta reconstruirla) y escribe:
    docs/data/index.json             lista de partidos con resumen y mejores apuestas
    docs/data/<id>.json              detalle completo de cada partido
La web (docs/index.html) solo lee esos archivos, así que se puede servir gratis en GitHub Pages.

Partidos ya jugados: si existe results/<fecha>.json con el partido, NO se vuelve a simular; se toma la
predicción publicada (docs/data/<id>.json), se le añade el resultado real y se liquida cada apuesta
(won / lost / push). index.json lleva además "history": récord de los picks con valor por jornada.

Valor de una apuesta:
    prob. implícita = cuota americana -> probabilidad (sin quitar el vig)
    edge = prob. modelo - prob. implícita
    EV por 1 unidad = p * (cuota decimal - 1) - (1 - p)
Mercado opcional en el JSON del partido (si falta la cuota de spread/total se asume -110):
    "market": {"spread_home": 4.5, "spread_odds_home": -110, "spread_odds_away": -110,
               "total": 239.5, "over_odds": -110, "under_odds": -110, "ml_away": -170, "ml_home": 142}
    "prop_lines": {"Jugador": {"pts": [24.5, 117]}}            # cuota del over
    "prop_lines": {"Jugador": {"pts": [24.5, 117, -145]}}       # over y under
    "prop_lines": {"Jugador": {"reb": [7.5, null, -120]}}       # solo under (cualquier estadística de STAT_LABEL)
    "prop_lines": {"Jugador": {"reb": [6.5, -135, -162, 7.5]}}  # under con otra línea (4.º valor)
"""
import glob, json, os, re, subprocess, sys, unicodedata
from datetime import datetime, timezone

import numpy as np

from db_link import PT, _positions
from model import (american_to_prob, current_h2h_games, load_game, prior_season, prob_to_american, season_of,
                   simulate, summarize)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "docs", "data")
STAT_LABEL = {"pts": "Puntos", "reb": "Rebotes", "ast": "Asistencias", "fg3m": "Triples", "pra": "PTS+REB+AST",
              "pr": "PTS+REB", "pa": "PTS+AST", "ra": "REB+AST", "stl": "Robos", "blk": "Tapones",
              "stocks": "Robos+Tapones", "tov": "Pérdidas", "pf": "Faltas", "fgm": "Tiros de campo anotados",
              "fga": "Tiros de campo intentados", "fg3a": "Triples intentados", "fg2m": "Dobles anotados",
              "fg2a": "Dobles intentados", "ftm": "Tiros libres anotados", "fta": "Tiros libres intentados"}
DEFAULT_ODDS = -110
VALUE_EV = 0.03  # EV mínimo para marcar una apuesta como "con valor"


def to_dec(odds):
    return 1 + (odds / 100 if odds > 0 else 100 / -odds)


def bet(kind, label, p, odds, line=None, player=None, team=None, assumed=False, dec=None):
    dec = dec or to_dec(odds)
    imp = 1 / dec
    ev = p * (dec - 1) - (1 - p)
    return dict(kind=kind, label=label, player=player, team=team, line=line, odds=odds, dec=round(dec, 3),
                odds_assumed=assumed, p=round(float(p), 3), implied=round(imp, 3),
                fair=prob_to_american(p), edge=round(float(p - imp), 3), ev=round(float(ev), 3),
                value=bool(ev >= VALUE_EV))


def load_odds(date):
    """odds/<fecha>.json: cuotas de varias casas por partido ("AWAY@HOME": {"books": [...]})."""
    path = os.path.join(HERE, "odds", f"{date}.json")
    return json.load(open(path)) if os.path.exists(path) else None


def clean_books(books):
    """Descarta datos incoherentes: hándicaps que no son simétricos y totales lejos de la mediana."""
    tl = [b["total"][0] for b in books if b.get("total")]
    med = float(np.median(tl)) if tl else None
    out = []
    for b in books:
        b = dict(b)
        if b.get("spread_home") and b.get("spread_away") and b["spread_home"][0] != -b["spread_away"][0]:
            b["spread_home"] = b["spread_away"] = None
        if b.get("total") and (b["total"][0] is None or abs(b["total"][0] - med) > 10):
            b["total"] = None
        out.append(b)
    return out


def multi_book_bets(cfg, s, books):
    """Una apuesta por selección: línea de consenso (la más repetida), cuota promedio de las casas
    con esa línea, y la mejor oferta (máximo EV) entre todas las casas con su propia línea."""
    h, a = cfg["game"]["home"], cfg["game"]["away"]
    m, tot = s["sh"] - s["sa"], s["sh"] + s["sa"]

    def p_cover(x, line):  # prob. de ganar la apuesta excluyendo push
        win, push = (x + line > 0).mean(), (x + line == 0).mean()
        return win / (1 - push) if push < 1 else 0.0

    sels = [  # (kind, team, nombre, extractor (línea, cuota), prob(línea), etiqueta(línea))
        ("ml", h, "ml_home", lambda b: (None, b.get("ml_home")), lambda l: (m > 0).mean(), lambda l: f"{h} gana"),
        ("ml", a, "ml_away", lambda b: (None, b.get("ml_away")), lambda l: (m < 0).mean(), lambda l: f"{a} gana"),
        ("spread", h, "sp_home", lambda b: tuple(b["spread_home"]) if b.get("spread_home") else (None, None),
         lambda l: p_cover(m, l), lambda l: f"{h} {l:+g}"),
        ("spread", a, "sp_away", lambda b: tuple(b["spread_away"]) if b.get("spread_away") else (None, None),
         lambda l: p_cover(-m, l), lambda l: f"{a} {l:+g}"),
        ("total", None, "over", lambda b: (b["total"][0], b["total"][1]) if b.get("total") else (None, None),
         lambda l: p_cover(tot, -l), lambda l: f"Over {l:g}"),
        ("total", None, "under", lambda b: (b["total"][0], b["total"][2]) if b.get("total") else (None, None),
         lambda l: p_cover(-tot, l), lambda l: f"Under {l:g}"),
    ]
    out, consensus = [], {}
    for kind, team, key, ext, prob, lab in sels:
        offers = []
        for b in books:
            line, odds = ext(b)
            if odds is None or (kind != "ml" and line is None):
                continue
            p = float(prob(line))
            d = to_dec(odds)
            offers.append(dict(book=b["book"], line=line, odds=odds, dec=round(d, 3), p=round(p, 3),
                               ev=round(p * (d - 1) - (1 - p), 3), **({"region": b["region"]} if b.get("region") else {})))
        if not offers:
            continue
        lines = [o["line"] for o in offers]
        cl = max(set(lines), key=lambda l: (lines.count(l), -abs((l or 0) - np.median([x or 0 for x in lines]))))
        at = [o for o in offers if o["line"] == cl]
        dec = float(np.mean([o["dec"] for o in at]))
        p = float(prob(cl))
        r = bet(kind, lab(cl), p, prob_to_american(1 / dec), cl, team=team, dec=dec)
        offers.sort(key=lambda o: o["ev"], reverse=True)
        r.update(n_books=len(at), best=offers[0], books=offers)
        co = [o for o in offers if o.get("region") == "CO"]  # casas colombianas (odds/fetch_co.py)
        if co:
            r["best_co"] = co[0]
        consensus[key] = (cl, prob_to_american(1 / dec))
        out.append(r)
    return out, consensus


def team_bets(cfg, s):
    h, a = cfg["game"]["home"], cfg["game"]["away"]
    mk = cfg.get("market", {})
    m, tot = s["sh"] - s["sa"], s["sh"] + s["sa"]
    out = []
    if "ml_home" in mk:
        out.append(bet("ml", f"{h} gana", (m > 0).mean(), mk["ml_home"], team=h))
    if "ml_away" in mk:
        out.append(bet("ml", f"{a} gana", (m < 0).mean(), mk["ml_away"], team=a))
    if "spread_home" in mk:
        sp = mk["spread_home"]
        win_h, push = (m + sp > 0).mean(), (m + sp == 0).mean()
        oh, oa = mk.get("spread_odds_home"), mk.get("spread_odds_away")
        # con push la apuesta se devuelve: p condicionada a que no haya push
        out.append(bet("spread", f"{h} {sp:+g}", win_h / (1 - push), oh or DEFAULT_ODDS, sp, team=h, assumed=oh is None))
        out.append(bet("spread", f"{a} {-sp:+g}", (1 - win_h - push) / (1 - push), oa or DEFAULT_ODDS, -sp, team=a, assumed=oa is None))
    if "total" in mk:
        t = mk["total"]
        po, push = (tot > t).mean(), (tot == t).mean()
        oo, ou = mk.get("over_odds"), mk.get("under_odds")
        out.append(bet("total", f"Over {t:g}", po / (1 - push), oo or DEFAULT_ODDS, t, assumed=oo is None))
        out.append(bet("total", f"Under {t:g}", (1 - po - push) / (1 - push), ou or DEFAULT_ODDS, t, assumed=ou is None))
    return out


def prop_bets(cfg, s):
    out = []
    for name, stats in cfg.get("prop_lines", {}).items():
        if name not in s["players"]:
            continue
        d = s["players"][name]
        for st, v in stats.items():
            x = d["draws"].get(st)
            if x is None:
                continue
            x = x[d["draws"]["min"] > 0]  # la prop se anula si no juega
            ln, o_over = v[0], v[1]
            o_under = v[2] if len(v) > 2 else None
            lu = v[3] if len(v) > 3 else ln  # la mejor cuota del under puede ser con otra línea
            lab = STAT_LABEL.get(st, st)
            if o_over is not None:  # algunas fuentes solo dan una selección (over o under)
                po, push = (x > ln).mean(), (x == ln).mean()
                out.append(dict(bet("prop", f"{name} Over {ln:g} {lab}", po / (1 - push), o_over, ln, name, d["team"]), stat=st))
            if o_under is not None:
                pu, push = (x < lu).mean(), (x == lu).mean()
                out.append(dict(bet("prop", f"{name} Under {lu:g} {lab}", pu / (1 - push), o_under, lu, name,
                                    d["team"]), stat=st))
    return out


# Parlays recomendados (estilo SGP de Linemate). Mismo partido: la prob. conjunta sale de contar en
# cuántas simulaciones aciertan TODAS las patas a la vez, así entra la correlación (si un equipo anota
# mucho, suben el over y los puntos de sus jugadores). Entre partidos: los partidos son independientes y
# las probabilidades se multiplican. Las props se calculan suponiendo que juegan todos sus jugadores (si
# uno no juega la casa anula esa pata) y no entran jugadores en duda.
PARLAY_LEG_P = 0.55     # prob. mínima de cada pata
PARLAY_MIN_DEC = 2.0    # cuota combinada mínima para que valga la pena como parlay
PARLAY_POOL = 14        # patas candidatas por partido (las más probables)
PARLAY_P_PLAY = 0.9     # jugadores en duda (prob. de jugar menor) no entran en parlays
LEG_KEYS = ("kind", "label", "player", "team", "line", "stat", "p", "dec", "ev")


def leg_hits(b, g, s):
    """(acierta, válida) por simulación. Push cuenta como acierto (no hace perder); una prop solo es
    válida en las simulaciones en que el jugador juega, igual que la prob. de la apuesta suelta."""
    h = g["home"]
    m, tot = s["sh"] - s["sa"], s["sh"] + s["sa"]
    ok = np.ones(len(m), bool)
    if b["kind"] == "ml":
        return (m > 0) == (b["team"] == h), ok
    if b["kind"] == "spread":
        return (m if b["team"] == h else -m) + b["line"] >= 0, ok
    if b["kind"] == "total":
        return (tot >= b["line"] if b["label"].startswith("Over") else tot <= b["line"]), ok
    d = s["players"][b["player"]]["draws"]
    x = d[b["stat"]]
    return (x >= b["line"]) if " Over " in b["label"] else (x <= b["line"]), d["min"] > 0


def leg_group(b):  # patas que no se combinan entre sí: ganador/hándicap, total, y cada jugador
    return "side" if b["kind"] in ("ml", "spread") else "total" if b["kind"] == "total" else b["player"]


def parlay(legs, p, n_ind=None):
    dec = float(np.prod([l["dec"] for l in legs]))
    ind = float(np.prod([l["p"] for l in legs])) if n_ind is None else n_ind
    return dict(legs=legs, n=len(legs), p=round(p, 3), p_indep=round(ind, 3), corr=round(p / ind, 2) if ind else None,
                dec=round(dec, 2), fair=round(1 / p, 2) if p else None, ev=round(p * dec - 1, 3))


def best_parlays(cands, joint, distinct):
    """Para 2, 3 y 4 patas: la combinación con mayor prob. conjunta, con cuota >= PARLAY_MIN_DEC y EV > 0."""
    from itertools import combinations
    out = []
    for k in (2, 3, 4):
        best = None
        for c in combinations(range(len(cands)), k):
            if not distinct([cands[i] for i in c]):
                continue
            dec = float(np.prod([cands[i]["dec"] for i in c]))
            if dec < PARLAY_MIN_DEC:
                continue
            p = joint(c)
            if p * dec - 1 <= 0:
                continue
            if best is None or (p, p * dec) > (best[0], best[0] * best[1]):
                best = (p, dec, c)
        if best:
            out.append(best)
    return out


def sgp(r, g, s):
    """Parlays del mismo partido. Devuelve (parlays, patas candidatas para los parlays entre partidos)."""
    pool = [b for b in r["bets"] if b["p"] >= PARLAY_LEG_P and b["ev"] >= 0 and
            (b["kind"] != "prop" or (b.get("stat") and s["players"][b["player"]]["p_play"] >= PARLAY_P_PLAY))]
    pool = sorted(pool, key=lambda b: -b["p"])[:PARLAY_POOL]
    hits = [leg_hits(b, g, s) for b in pool]
    legs = [dict({k: b.get(k) for k in LEG_KEYS}, gid=r["id"], game=f"{g['away']} @ {g['home']}") for b in pool]
    distinct = lambda ls: len({leg_group(l) for l in ls}) == len(ls)

    def joint(c):
        win = np.logical_and.reduce([hits[i][0] for i in c])
        ok = np.logical_and.reduce([hits[i][1] for i in c])
        return float(win[ok].mean()) if ok.any() else 0.0
    res = best_parlays(legs, joint, distinct)
    return [dict(parlay([legs[i] for i in c], p), type="sgp") for p, _, c in res], legs


def cross_parlays(legs_by_game):
    """Parlays entre partidos con las 2 patas más probables y con valor (EV >= VALUE_EV) de cada partido."""
    cands = []
    for legs in legs_by_game:
        cands += sorted([l for l in legs if l["ev"] >= VALUE_EV], key=lambda l: -l["p"])[:2]
    distinct = lambda ls: len({l["gid"] for l in ls}) == len(ls)
    joint = lambda c: float(np.prod([cands[i]["p"] for i in c]))
    return [dict(parlay([cands[i] for i in c], p), type="cross") for p, _, c in best_parlays(cands, joint, distinct)]


def settle_parlay(pl, results):
    """won / lost / push (todas anuladas) / None si falta algún partido."""
    from evaluate import settle
    rs = []
    for l in pl["legs"]:
        res = results.get(l["gid"])
        if res is None:
            return None
        x = settle(l, res["game"], res["res"])
        l["result"] = "push" if x is None else ("won" if x else "lost")
        rs.append(l["result"])
    return "lost" if "lost" in rs else "push" if all(x == "push" for x in rs) else "won"


# Ataque de un equipo vs lo que concede el rival (rank 1 = mejor en ambos lados, como en db/build_db.py)
MATCHUP_KEYS = [("PTS", "OPP_PTS", "Puntos"), ("OFF_RATING", "DEF_RATING", "Rating (pts/100 pos.)"),
                ("PTS_PAINT", "OPP_PTS_PAINT", "Puntos en la pintura"), ("FG3A", "OPP_FG3A", "Triples intentados"),
                ("FG3_PCT", "OPP_FG3_PCT", "% de triples"), ("FTA", "OPP_FTA", "Tiros libres intentados"),
                ("AST", "OPP_AST", "Asistencias"), ("PTS_FB", "OPP_PTS_FB", "Puntos en transición"),
                ("PTS_2ND_CHANCE", "OPP_PTS_2ND_CHANCE", "Puntos de segunda oportunidad"),
                ("OREB_PCT", "DREB_PCT", "Rebote ofensivo vs defensivo")]
DVP_COLS = ["PTS", "REB", "AST", "3PM"]


def load_rankings():
    path = os.path.join(HERE, "db", "rankings_latest.json")
    return json.load(open(path)) if os.path.exists(path) else None


def matchup_view(R, g, players, db_report=None):
    """Lo que la web muestra en "Enfrentamiento según los rankings": para cada equipo que ataca, sus
    métricas frente a lo que concede el rival, sus tipos de jugada frente a la defensa rival de esos
    tipos y la defensa del rival por posición (con los jugadores del equipo en cada posición)."""
    h, a = g["home"], g["away"]
    pick = lambda d: (d or {}).get("blend") or (d or {}).get(R["season"])
    lg_ppp = {f"{p}|{s}": float(np.mean([pick(R["teams"][t]["play_types"].get(f"{p}|{s}"))["ppp"] for t in R["teams"]]))
              for p in PT for s in ("off", "def")}
    dvp_lg = {}
    for pos in ("PG", "SG", "SF", "PF", "C"):
        for c in DVP_COLS:
            vals = [R["teams"][t]["dvp"][pos][c]["value"] for t in R["teams"] if R["teams"][t].get("dvp")]
            dvp_lg[f"{pos}|{c}"] = round(float(np.mean(vals)), 1) if vals else None
    rep_players = (db_report or {}).get("players", {})
    out = dict(snapshot=R["snapshot_date"], season=R["season"], prior_season=R.get("prior_season"),
               w_cur={t: round(R["teams"][t]["gp"] / (R["teams"][t]["gp"] + R.get("shrink_games", 10)), 2) for t in (a, h)},
               gp={t: R["teams"][t]["gp"] for t in (a, h)}, attack={})
    for t, o in ((a, h), (h, a)):
        T, O = R["teams"][t], R["teams"][o]
        keys = []
        for om, dm, lab in MATCHUP_KEYS:
            x, y = pick(T["metrics"].get(om)), pick(O["metrics"].get(dm))
            if x and y:
                keys.append(dict(label=lab, off=round(x["value"], 3), off_rank=x["rank"], deff=round(y["value"], 3), def_rank=y["rank"]))
        styles = []
        for p in PT:
            fo, dd = pick(T["play_types"].get(f"{p}|off")), pick(O["play_types"].get(f"{p}|def"))
            if not fo or not dd:
                continue
            # PPP esperado aprox.: lo que el ataque saca de más + lo que la defensa concede de más, sobre la media
            exp = lg_ppp[f"{p}|off"] + (fo["ppp"] - lg_ppp[f"{p}|off"]) + (dd["ppp"] - lg_ppp[f"{p}|def"])
            styles.append(dict(play=p, freq=round(fo["freq"], 3), freq_rank=fo["freq_rank"], ppp=round(fo["ppp"], 3),
                               ppp_rank=fo["ppp_rank"], def_freq=round(dd["freq"], 3), def_ppp=round(dd["ppp"], 3),
                               def_rank=dd["ppp_rank"], lg_ppp=round(lg_ppp[f"{p}|off"], 3), exp_ppp=round(exp, 3)))
        styles.sort(key=lambda r: -r["freq"])
        dvp = {}
        for pos in ("PG", "SG", "SF", "PF", "C"):
            row = (O.get("dvp") or {}).get(pos)
            if not row:
                continue
            who = [dict(name=p["name"], pos=p["pos"], min=p["min"],
                        mult={k: v for k, v in rep_players.get(p["name"], {}).items() if k in ("pts", "reb", "ast", "fg3m")})
                   for p in players if p["team"] == t and p["min"] >= 10 and pos in _positions(p["pos"])]
            dvp[pos] = dict(stats={c: dict(value=row[c]["value"], rank=row[c]["rank"], lg=dvp_lg[f"{pos}|{c}"])
                                   for c in DVP_COLS if c in row}, players=sorted(who, key=lambda x: -x["min"]))
        out["attack"][t] = dict(vs=o, keys=keys, styles=styles, dvp=dvp)
    return out


def _norm(name):
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    n = re.sub(r"[^a-z ]", "", n.replace("-", " "))
    return " ".join(w for w in n.split() if w not in ("jr", "sr", "ii", "iii", "iv"))


def _key(name):  # apellido + 3 primeras letras del nombre: "Ron Holland" = "Ronald Holland II"
    w = _norm(name).split()
    return f"{w[-1]} {w[0][:3]}" if len(w) > 1 else " ".join(w)


def h2h_view(g, players):
    """Enfrentamientos entre los dos equipos esta temporada (results/, vía model.current_h2h_games) y la anterior
    (h2h/<temporada>.json, box scores de ESPN), de liga y de Copa, y la línea de cada jugador de hoy en esos
    partidos (aunque entonces jugara en otro equipo)."""
    files = sorted(glob.glob(os.path.join(HERE, "h2h", "*.json")))
    pair = {g["home"], g["away"]}
    games = []
    if files:
        H = json.load(open(files[-1]))
        games = [dict(x, season=H["season"]) for x in H["games"]
                 if {x["home"], x["away"]} == pair and x["date"] < g["date"]]
    games += current_h2h_games(g["date"], pair)
    if not games:  # todos los equipos se enfrentan 2-4 veces: vacío = ese cruce aún no está cargado
        return None
    games.sort(key=lambda x: x["date"], reverse=True)
    out_games, lines = [], {}
    for x in games:
        lead = x.get("largest_lead", {})
        comp = cup_round(x)
        out_games.append(dict(date=x["date"], season=x["season"], away=x["away"], home=x["home"], score=x["score"],
                              ot=x.get("ot", 0), largest_lead=lead, blowout=[t for t, v in lead.items() if v >= 20],
                              comp="cup" if comp else "league", cup_round=comp))
        for team, ps in x["players"].items():
            for n, mi, pts, reb, ast, f3 in ps:
                lines.setdefault(_key(n), []).append(dict(date=x["date"], season=x["season"], team=team, min=mi,
                                                           pts=pts, reb=reb, ast=ast, fg3m=f3, pra=pts + reb + ast,
                                                           cup=bool(comp)))
    pl = {p["name"]: lines[_key(p["name"])] for p in players if _key(p["name"]) in lines}
    seasons = sorted({x["season"] for x in games}, reverse=True)
    return dict(season=" y ".join(seasons), seasons=seasons, games=out_games, players=pl)


def cup_round(x):
    """Ronda de Copa de un partido ya jugado (None si fue de liga): cup/history.json para temporadas pasadas (las
    fechas del H2H pueden ir un día corridas) y cup/<temporada>.json para la actual."""
    pair = {x["home"], x["away"]}
    sched = cup_schedule()
    k = (x["date"], frozenset(pair))
    if k in sched:
        return sched[k] or "Copa"
    C = cup_history() or {}
    d = datetime.fromisoformat(x["date"])
    for c in C.get("games", []):
        if {c["home"], c["away"]} == pair and abs((datetime.fromisoformat(c["date"]) - d).days) <= 1:
            return c.get("round") or "Copa"
    return None


_CUP = None


def _cup_files():
    global _CUP
    if _CUP is None:
        _CUP = [json.load(open(p)) for p in sorted(glob.glob(os.path.join(HERE, "cup", "20??-??.json")))]
    return _CUP


def cup_schedule():
    """cup/<temporada>.json: {(fecha, {equipo, equipo}): ronda} de los partidos de Copa conocidos (por pareja de
    equipos, así no importa si una fuente invierte local y visitante)."""
    out = {}
    for d in _cup_files():
        for x in d.get("games", []):
            out[(x["date"], frozenset((x["away"], x["home"])))] = x.get("round")
    return out


def cup_dates():
    """Fechas cuyo calendario de Copa está completo en cup/<temporada>.json (fase de grupos): en ellas, un partido
    que no esté en la lista es de liga aunque su JSON diga otra cosa."""
    return {f for d in _cup_files() for f in d.get("group_stage_dates", [])}


def tag_cup(g):
    """Separa liga y Copa según cup/<temporada>.json (la rutina diaria no tiene que saberlo)."""
    k = (g["date"], frozenset((g["away"], g["home"])))
    sched = cup_schedule()
    if k in sched:
        g["competition"] = "cup"
        g["cup_round"] = g.get("cup_round") or sched[k] or "Copa"
    elif g["date"] in cup_dates() and g.get("competition") == "cup":
        g["competition"] = "regular"
        g.pop("cup_round", None)
    return g


def cup_history():
    p = os.path.join(HERE, "cup", "history.json")
    return json.load(open(p)) if os.path.exists(p) else None


def cup_view(g):
    """Para partidos de Copa: cruces previos de Copa entre ambos equipos (con la línea de cierre del total) y cómo
    anotó cada equipo en la Copa frente a sus partidos de liga del mismo tramo (cup/history.json)."""
    C = cup_history()
    if g.get("competition") != "cup" or not C:
        return None
    pair = {g["home"], g["away"]}
    meetings = [x for x in C["games"] if {x["home"], x["away"]} == pair and x["date"] < g["date"]]
    return dict(meetings=meetings, teams={t: C["teams"].get(t, {}) for t in (g["away"], g["home"])},
                model_total_adj=C.get("model_total_adj", 0.0))


def rotation_view(cfg, summ):
    """Quinteto probable (puesto que cubre cada titular) y rotación estimada: minutos proyectados por el modelo y
    papel de cada jugador. Los tramos de cada cuarto los dibuja la web con el patrón típico de la NBA."""
    mins = {p["name"]: p["min"] for p in summ["players"]}
    g = cfg["game"]
    out = {}
    for t in (g["away"], g["home"]):
        rows = []
        for p in cfg["players"]:
            if p["team"] != t:
                continue
            m = mins.get(p["name"], p["min"])
            role = "titular" if p["starter"] else "rotacion" if m >= 18 else "rol" if m >= 8 else "reserva"
            rows.append(dict(name=p["name"], pos=p["pos"], slot=p.get("slot"), min=round(m, 1), role=role,
                             p_play=p.get("p_play", 1.0), mpg=p["cur"]["min"]))
        rows += [dict(name=x["name"], pos=x["pos"], slot=None, min=0.0, role="reserva", mpg=x["min"])
                 for x in g.get("reserves", {}).get(t, [])]
        order = {"PG": 0, "SG": 1, "SF": 2, "PF": 3, "C": 4}
        rows.sort(key=lambda r: (r["role"] != "titular", order.get(r["slot"], 9), -r["min"], -r["mpg"]))
        out[t] = rows
    return out


def cup_summary():
    """Resumen de liga para el inicio de la pestaña NBA Cup."""
    C = cup_history()
    return C and dict(seasons=C["seasons"], pooled=C["pooled"], model_total_adj=C.get("model_total_adj", 0.0))


def player_flags(date, cfg_path):
    """Etiquetas de jugadores con poca historia NBA (más varianza): {nombre: {t, note}}.
    t = "rookie" (players/<temporada>.json), "lesion" (no jugó la temporada pasada, misma lista) o
    "sin_datos" (no está en raw/<fecha>/prior.csv: el modelo solo usa sus partidos de esta temporada)."""
    meta_p = os.path.join(HERE, "players", f"{season_of(date)}.json")
    meta = json.load(open(meta_p)) if os.path.exists(meta_p) else {}
    rook = {_key(n) for n in meta.get("rookies", [])}
    miss = {_key(n): why for n, why in meta.get("missed_prior", {}).items()}
    prior_p = os.path.join(HERE, "raw", date, "prior.csv")
    prior = None
    if os.path.exists(prior_p):
        import csv
        prior = {_key(r["Player"]) for r in csv.DictReader(open(prior_p, encoding="utf-8"))}
    cfg = json.load(open(cfg_path)) if os.path.exists(cfg_path) else {}
    ps = season_of(date)
    out = {}
    for p in cfg.get("players", []):
        k, g = _key(p["name"]), p.get("g_cur", 0)
        games = f"{g} partido{'s' if g != 1 else ''} esta temporada"
        if k in rook:
            out[p["name"]] = dict(t="rookie", note=f"Rookie {ps}: sin temporada NBA previa, {games}")
        elif k in miss:
            out[p["name"]] = dict(t="lesion", note=f"{miss[k][0].upper() + miss[k][1:]}; {games}")
        elif prior is not None and k not in prior:
            out[p["name"]] = dict(t="sin_datos", note=f"Sin datos {prior_season(date)} en el modelo; {games}")
    return out


def apply_flags(r, flags):
    """Pone la etiqueta en los jugadores del resumen, sus props y las patas de los parlays."""
    for p in r["summary"]["players"]:
        p.pop("flag", None)
        if p["name"] in flags:
            p["flag"] = flags[p["name"]]
    for b in r["bets"] + [l for pl in r.get("parlays", []) for l in pl["legs"]]:
        b.pop("flag", None)
        if b.get("player") in flags:
            b["flag"] = flags[b["player"]]
    return r


def game_id(path):
    return os.path.basename(path)[:-5].replace("@", "-at-").replace("_", "-")


def run_game(path, n):
    cfg_raw = json.load(open(path))
    use_db = cfg_raw.get("db", {}).get("use", False) and os.path.exists(os.path.join(HERE, "db", "rankings_latest.json"))
    cfg = load_game(path, use_db)
    s = simulate(cfg, n)
    g = tag_cup(cfg["game"])
    od = load_odds(g["date"])
    books = (od or {}).get("games", {}).get(f"{g['away']}@{g['home']}", {}).get("books")
    if books:
        tb, cons = multi_book_bets(cfg, s, clean_books(books))
        mk = cfg.setdefault("market", {})  # el resumen usa el consenso de las casas
        if "sp_home" in cons: mk["spread_home"] = cons["sp_home"][0]
        if "over" in cons: mk["total"] = cons["over"][0]
        if "ml_home" in cons: mk["ml_home"] = cons["ml_home"][1]
        if "ml_away" in cons: mk["ml_away"] = cons["ml_away"][1]
        mk["source"] = f"promedio de {len(books)} casas ({od.get('source', '')})"
    else:
        tb = team_bets(cfg, s)
    summ = summarize(cfg, s)
    bets = tb + prop_bets(cfg, s)
    bets.sort(key=lambda b: b["ev"], reverse=True)
    R = load_rankings()
    mv = matchup_view(R, g, summ["players"], summ.get("db_report")) if R else None
    r = dict(id=game_id(path), game=g, market=cfg.get("market", {}), used_db=use_db, sims=n,
                summary=summ, bets=bets, matchup_view=mv, h2h=h2h_view(g, summ["players"]), cup=cup_view(g),
                records={t: cfg["teams"][t].get("record") for t in (g["away"], g["home"])},
                context={t: cfg["teams"][t].get("context", {}) for t in (g["away"], g["home"])})
    r["rotation"] = rotation_view(cfg, summ)
    r["parlays"], r["_legs"] = sgp(r, g, s)
    return r


def load_results(date):
    """results/<fecha>.json: marcadores y box scores reales (ver evaluate.py)."""
    path = os.path.join(HERE, "results", f"{date}.json")
    return json.load(open(path))["games"] if os.path.exists(path) else {}


def attach_result(r, res):
    """Congela un partido ya jugado: guarda el resultado real y liquida cada apuesta publicada."""
    from evaluate import actual_line, settle
    g = r["game"]
    h, a = g["home"], g["away"]
    r["result"] = dict(score={a: res["away"], h: res["home"]}, ot=res.get("ot", 0),
                       winner=h if res["home"] > res["away"] else a,
                       winner_ok=(r["summary"]["win_prob"][h] >= 0.5) == (res["home"] > res["away"]))
    for b in r["bets"]:
        x = settle(b, g, res)
        b["result"] = "push" if x is None else ("won" if x else "lost")
    for p in r["summary"]["players"]:
        al = actual_line(res["players"], p["name"])
        p["actual"] = al if al and al["min"] > 0 else None
    for pl in r.get("parlays", []):
        pl["result"] = settle_parlay(pl, {r["id"]: dict(game=g, res=res)})
    return r


def index_entry(r):
    g, sm = r["game"], r["summary"]
    vb = [b for b in r["bets"] if b["value"]]
    e = dict(id=r["id"], date=g["date"], away=g["away"], home=g["home"], tip_et=g.get("tip_et"),
             venue=g.get("venue"), competition=g.get("competition", "regular"), cup_round=g.get("cup_round"),
             records=r["records"], proj=sm["proj"], win_prob=sm["win_prob"],
             proj_total=sm["proj_total"], used_db=r["used_db"],
             n_value=len(vb), top_bets=r["bets"][:3], value_bets=vb, parlays=r.get("parlays", []),
             all_bets=[{k: v for k, v in b.items() if k not in ("books", "fair")} for b in r["bets"]])
    if "result" in r:
        e["result"] = dict(r["result"], picks_won=sum(b["result"] == "won" for b in vb),
                           picks_lost=sum(b["result"] == "lost" for b in vb),
                           units=round(sum(b["dec"] - 1 if b["result"] == "won" else -1 if b["result"] == "lost" else 0
                                           for b in vb), 2))
    return e


# Las tres actualizaciones diarias (--stage): "results" = madrugada, resultados reales de la jornada (partidos,
# picks, SGP y parlays liquidados); "morning" = mañana, análisis y picks de la jornada con sus cuotas; "pregame" =
# 30-60 min antes del primer partido, re-simulación con bajas de última hora. --for <fecha> = jornada a la que
# corresponde (por defecto la fecha más reciente de los partidos procesados).
STAGES = ("results", "morning", "pregame")


def main():
    argv = sys.argv[1:]
    opt = lambda k: argv[argv.index(k) + 1] if k in argv and argv.index(k) + 1 < len(argv) else None
    n = int(opt("--sims") or 20000)
    stage, stage_for = opt("--stage"), opt("--for")
    if stage and stage not in STAGES:
        sys.exit(f"--stage debe ser uno de {', '.join(STAGES)}")
    skip = {i + 1 for i, x in enumerate(argv) if x in ("--sims", "--stage", "--for")}
    args = [x for i, x in enumerate(argv) if not x.startswith("--") and i not in skip]
    if not os.path.exists(os.path.join(HERE, "db", "rankings_latest.json")):
        subprocess.run([sys.executable, os.path.join(HERE, "db", "build_db.py")], check=False)
    paths = sorted(p for p in glob.glob(os.path.join(HERE, "games", "*.json")) if not p.endswith("_results.json"))
    if args:
        paths = [p for p in paths if any(os.path.basename(p).startswith(a) for a in args)]
    os.makedirs(OUT, exist_ok=True)
    idx_path = os.path.join(OUT, "index.json")
    index = json.load(open(idx_path)) if os.path.exists(idx_path) else {"games": []}
    keep = {g["id"]: g for g in index["games"]}
    cross = index.get("parlays", {})  # parlays entre partidos por jornada
    legs_by_date = {}
    all_flags = {}  # etiquetas de los jugadores de esta pasada (para las patas de los parlays entre partidos)
    for p in paths:
        gid, date = game_id(p), os.path.basename(p)[:10]
        res = load_results(date).get(os.path.basename(p)[11:-5])
        out_path = os.path.join(OUT, f"{gid}.json")
        try:
            if res and os.path.exists(out_path):
                # partido ya jugado: no se vuelve a simular (la web muestra lo que se publicó antes del partido)
                r = attach_result(json.load(open(out_path)), res)
                if not r.get("matchup_view") and load_rankings():  # partidos publicados antes de esta sección
                    r["matchup_view"] = matchup_view(load_rankings(), r["game"], r["summary"]["players"],
                                                     r["summary"].get("db_report"))
                if not (r.get("h2h") or {}).get("games"):
                    r["h2h"] = h2h_view(r["game"], r["summary"]["players"])
                if "cup" not in r:
                    r["cup"] = cup_view(tag_cup(r["game"]))
            else:
                r = run_game(p, n)
                if res:
                    r = attach_result(r, res)
        except Exception as e:  # un JSON roto no debe tumbar toda la jornada
            print(f"ERROR {os.path.basename(p)}: {e}")
            continue
        flags = player_flags(date, p)
        apply_flags(r, flags)
        all_flags.update(flags)
        if "_legs" in r:
            legs_by_date.setdefault(date, []).append(r.pop("_legs"))
        json.dump(r, open(out_path, "w"), ensure_ascii=False, separators=(",", ":"))
        keep[r["id"]] = index_entry(r)
        g, sm = r["game"], r["summary"]
        print(f"OK {r['id']}: {g['away']} {sm['proj'][g['away']]} - {g['home']} {sm['proj'][g['home']]}, "
              f"{keep[r['id']]['n_value']} apuestas con valor" + (" (resultado real cargado)" if res else ""))
    games = sorted(keep.values(), key=lambda g: (g["date"], g.get("tip_et") or "", g["id"]), reverse=False)
    last = max((g["date"] for g in games), default=None)
    for g in games:  # la lista completa de apuestas ("Ver todo" y top por categoría) solo de la última jornada
        if g["date"] != last:
            g.pop("all_bets", None)
    for date, lbg in legs_by_date.items():
        # solo se rehacen si se simularon todos los partidos aún sin jugar de esa jornada
        if len(lbg) == sum(g["date"] == date and "result" not in g for g in games):
            cross[date] = cross_parlays(lbg)
            for pl in cross[date]:
                for l in pl["legs"]:
                    if l.get("player") in all_flags:
                        l["flag"] = all_flags[l["player"]]
    for date, pls in cross.items():
        res = load_results(date)
        rs = {g["id"]: dict(game=dict(home=g["home"], away=g["away"]), res=res[f"{g['away']}@{g['home']}"])
              for g in games if g["date"] == date and f"{g['away']}@{g['home']}" in res}
        for pl in pls:
            pl["result"] = settle_parlay(pl, rs)
    history = []  # récord de los picks con valor por jornada (solo jornadas con resultados)
    for date in sorted({g["date"] for g in games if "result" in g}):
        rs = [g["result"] for g in games if g["date"] == date and "result" in g]
        sg = [pl.get("result") for g in games if g["date"] == date for pl in g.get("parlays", [])]
        cr = [pl.get("result") for pl in cross.get(date, [])]
        history.append(dict(date=date, games=len(rs), winners_ok=sum(x["winner_ok"] for x in rs),
                            picks_won=sum(x["picks_won"] for x in rs), picks_lost=sum(x["picks_lost"] for x in rs),
                            units=round(sum(x["units"] for x in rs), 2),
                            sgp_won=sg.count("won"), sgp_lost=sg.count("lost"),
                            cross_won=cr.count("won"), cross_lost=cr.count("lost")))
    now = datetime.now(timezone.utc).isoformat(timespec="minutes")
    updates = index.get("updates", {})  # las tres actualizaciones diarias: {jornada: {etapa: hora UTC}}
    if stage:
        day = stage_for or max((os.path.basename(p)[:10] for p in paths), default=last)
        updates.setdefault(day, {})[stage] = now
    updates = {d: updates[d] for d in sorted(updates)[-14:]}
    json.dump(dict(generated=now, value_ev=VALUE_EV, last_stage=stage or index.get("last_stage"), updates=updates,
                   history=history, parlays=cross, cup=cup_summary(), games=games), open(idx_path, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
