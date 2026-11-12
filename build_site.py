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
import base64, glob, json, os, re, subprocess, sys, unicodedata
from datetime import datetime, timezone

import numpy as np

from db_link import PT, _positions
from evaluate import is_over
from model import (BINARY, american_to_prob, current_h2h_games, load_game, prior_season, prob_to_american, season_of,
                   simulate, summarize)
from without import without_view

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "docs", "data")
STAT_LABEL = {"pts": "Puntos", "reb": "Rebotes", "ast": "Asistencias", "fg3m": "Triples", "pra": "PTS+REB+AST",
              "pr": "PTS+REB", "pa": "PTS+AST", "ra": "REB+AST", "stl": "Robos", "blk": "Tapones",
              "stocks": "Robos+Tapones", "tov": "Pérdidas", "pf": "Faltas", "fgm": "Tiros de campo anotados",
              "fga": "Tiros de campo intentados", "fg3a": "Triples intentados", "fg2m": "Dobles anotados",
              "fg2a": "Dobles intentados", "ftm": "Tiros libres anotados", "fta": "Tiros libres intentados",
              "dd": "Doble-doble", "td": "Triple-doble"}
DEFAULT_ODDS = -110
VALUE_EV = 0.03  # EV mínimo para marcar una apuesta como "con valor"

# Modelo 1.5 (1-nov, diagnóstico + backtest 26-31 oct aprobado por Nicolas): el modelo se creía con más ventaja de
# la que tenía (decía 64% en sus picks, la casa pedía 54%, se acertó 49%). Ahora:
# - la probabilidad publicada mezcla la de la casa sin margen con la del modelo: p = p_casa + MKT_W * (p_modelo -
#   p_casa) (p_model guarda la del modelo). Solo hay p_casa si la casa da los dos lados de la misma línea;
#   sin ella la apuesta se muestra pero no se recomienda.
# - solo se recomiendan props (ganador, hándicap y total se muestran sin marca de valor: el mercado proyecta mejor
#   el margen) y nunca el Under de un rookie o de quien no jugó la temporada pasada (el modelo los subestima).
# - por jornada, como mucho MAX_PER_PLAYER picks por jugador (y uno por familia: puntos/P+R/P+A/PRA, rebotes/R+A,
#   asistencias, robos/tapones...) y MAX_DAILY picks en total, los de mayor EV. "cand" = pasa el filtro por apuesta;
#   "value" = además entra en el tope de la jornada.
MKT_W = 0.25
MAX_PER_PLAYER = 2
MAX_DAILY = 15
# Versiones del modelo por jornada (pedido de Nicolas, 8-nov): el historial separa picks y unidades por versión para
# ver qué cambió con cada reforma. Cada versión rige desde su fecha (la primera jornada simulada con ella) hasta la
# siguiente. Al publicar una versión nueva, añadirla aquí con la fecha de su primera jornada.
MODEL_VERSIONS = [
    ("1.1", "2026-10-26", "primera versión"),
    ("1.2", "2026-10-27", "minutos más abiertos, calibrada con el 26-oct"),
    ("1.3", "2026-10-29", "H2H con peso y más mercados de props"),
    ("1.5", "2026-11-01", "mezcla con la casa, solo props y tope de 15 picks"),
    # 1.5.1 (cuotas al margen de Betano) y 1.6 (nivel de anotación, puntos más abiertos) empiezan la misma jornada
    ("1.6", "2026-11-12", "cuotas de Betano, anotación de esta temporada y puntos más abiertos"),
]
MODEL = MODEL_VERSIONS[-1][0]


def model_for(date):
    return [v for v, d, _ in MODEL_VERSIONS if d <= date][-1] if date >= MODEL_VERSIONS[0][1] else MODEL_VERSIONS[0][0]
PICK_FAMILY = {"pts": "p", "pr": "p", "pa": "p", "pra": "p", "reb": "r", "ra": "r", "ast": "a",
               "stl": "d", "blk": "d", "stocks": "d"}
NO_UNDER_FLAGS = ("rookie", "lesion")


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


def pair_key(b):
    """Selecciones opuestas de una misma línea (las dos caras de la apuesta)."""
    if b["kind"] == "prop":  # las líneas alternativas solo se emparejan entre ellas (no cambian las de la línea principal)
        return ("alt" if b.get("alt") else "prop", b["player"], b.get("stat"), b["line"])
    if b["kind"] == "total":
        return ("total", b["line"])
    if b["kind"] == "spread":
        return ("spread", abs(b["line"]))
    return (b["kind"],)


# 1.5.1 (pedido de Nicolas, 11-nov): las cuotas de props que leemos (scoresandodds / Covers) son la MEJOR de cada
# lado entre 6 casas de EE. UU., con un margen conjunto de ~3.5-4% (11-nov: 1.035), mientras que en Colombia la casa
# cobra ~7-8% (Kambi 29/30-oct: 1.073; Betano algo más). Así el EV, los parlays y el soñador salían inflados (soñador
# de cuota 50 en la web = ~37 en Betano). Desde CO_PRICE_FROM cada prop se lleva a la cuota que pondría una casa
# colombiana: se quita el margen del par (prob. justa) y se aplica CO_OVERROUND; si la cuota leída ya es más baja,
# se queda. Un lado sin pareja sube su prob. implícita en CO_OVERROUND / US_OVERROUND. La cuota leída queda en
# odds_ref / dec_ref. No toca cuotas que ya son de una casa colombiana (games/*.json con "prop_odds_region": "CO").
CO_OVERROUND = 1.08
US_OVERROUND = 1.035
CO_PRICE_FROM = "2026-11-12"


def localize_odds(bets, over=CO_OVERROUND):
    groups = {}
    for b in bets:
        if b["kind"] == "prop" and not b.get("alt") and not b.get("odds_assumed"):
            groups.setdefault(pair_key(b), []).append(b)
    for g in groups.values():
        imps = [1 / b["dec"] for b in g]
        tot = sum(imps)
        for b, imp in zip(g, imps):
            new = imp / tot * over if len(g) == 2 else imp * over / US_OVERROUND
            dec = min(b["dec"], 1 / min(new, 0.995))
            if dec < b["dec"] - 1e-9:
                b.update(odds_ref=b["odds"], dec_ref=b["dec"], odds=prob_to_american(1 / dec), dec=round(dec, 3),
                         implied=round(1 / dec, 3))
    return bets


def market_blend(bets, flags=None, w=MKT_W):
    """1.5: mezcla cada probabilidad con la de la casa sin margen y marca las candidatas a pick con valor."""
    flags = flags or {}
    groups = {}
    for b in bets:
        groups.setdefault(pair_key(b), []).append(b)
    ovr = {}  # margen de la casa en la línea principal de cada jugador y estadística (para líneas "X+" de un solo lado)
    for k, g in groups.items():
        if k[0] == "prop" and len(g) == 2 and not any(x.get("alt") or x.get("odds_assumed") for x in g):
            ovr[(g[0]["player"], g[0].get("stat"))] = 1 / g[0]["dec"] + 1 / g[1]["dec"]
    for b in bets:
        pm = b.get("p_model", b["p"])
        g = groups[pair_key(b)]
        mkt = None
        if len(g) == 2 and not any(x.get("odds_assumed") for x in g):
            i0, i1 = 1 / g[0]["dec"], 1 / g[1]["dec"]
            mkt = (1 / b["dec"]) / (i0 + i1)
        elif len(g) == 1 and b.get("alt") and (b["player"], b.get("stat")) in ovr:
            mkt = min((1 / b["dec"]) / ovr[(b["player"], b.get("stat"))], 0.99)
        p = pm if mkt is None else mkt + w * (pm - mkt)
        ev = p * b["dec"] - 1
        shift = p - pm
        b.update(p_model=round(float(pm), 3), p_mkt=None if mkt is None else round(float(mkt), 3), p=round(float(p), 3),
                 fair=prob_to_american(p), edge=round(float(p - 1 / b["dec"]), 3), ev=round(float(ev), 3))
        for o in b.get("books", []):  # otras casas / líneas: mismo ajuste que la línea de consenso
            o["p"] = round(min(max(o["p"] + shift, 0.0), 1.0), 3)
            o["ev"] = round(o["p"] * o["dec"] - 1, 3)
        if "best" in b and b.get("books"):
            b["best"] = max(b["books"], key=lambda o: o["ev"])
        why = None
        if mkt is None:
            why = "sin cuota de la casa para el otro lado"
        elif b["kind"] != "prop":
            why = "picks de equipo en pausa"
        elif b.get("alt"):
            why = "línea alternativa: solo para parlays y el soñador"
        elif b.get("side") == "under" and (flags.get(b["player"]) or {}).get("t") in NO_UNDER_FLAGS:
            why = "sin Unders de rookies ni de quien no jugó la temporada pasada"
        b["cand"] = bool(ev >= VALUE_EV and why is None)
        b["value"] = b["cand"]
        if why and ev >= VALUE_EV:
            b["no_value_why"] = why
        else:
            b.pop("no_value_why", None)
    bets.sort(key=lambda b: b["ev"], reverse=True)
    return bets


def daily_cap(rs, max_player=MAX_PER_PLAYER, max_daily=MAX_DAILY):
    """1.5: entre los partidos sin jugar de una jornada, deja con valor solo los mejores candidatos por EV."""
    cands = sorted(((b["ev"], i, b) for i, r in enumerate(rs) for b in r["bets"]
                    if b.get("cand", b.get("value"))), key=lambda x: -x[0])
    for r in rs:
        for b in r["bets"]:
            b["value"] = False
    n, per, fam = 0, {}, set()
    for _, i, b in cands:
        if n >= max_daily:
            break
        k = (rs[i]["id"], b.get("player") or b.get("team"))
        if b["kind"] == "prop":
            f = (k, PICK_FAMILY.get(b.get("stat"), b.get("stat")))
            if per.get(k, 0) >= max_player or f in fam:
                continue
            fam.add(f)
        per[k] = per.get(k, 0) + 1
        b["value"] = True
        n += 1
    return rs


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
            yn = st in BINARY  # doble-doble / triple-doble: sí (over 0.5) / no (under 0.5)
            if o_over is not None:  # algunas fuentes solo dan una selección (over o under)
                po, push = (x > ln).mean(), (x == ln).mean()
                out.append(dict(bet("prop", f"{name} {lab}: Sí" if yn else f"{name} Over {ln:g} {lab}", po / (1 - push),
                                    o_over, ln, name, d["team"]), stat=st, side="over"))
            if o_under is not None:
                pu, push = (x < lu).mean(), (x == lu).mean()
                out.append(dict(bet("prop", f"{name} {lab}: No" if yn else f"{name} Under {lu:g} {lab}", pu / (1 - push),
                                    o_under, lu, name, d["team"]), stat=st, side="under"))
    for name, stats in cfg.get("alt_lines", {}).items():  # líneas alternativas / escalonadas (Rushbet, BetPlay)
        if name not in s["players"]:
            continue
        d = s["players"][name]
        for st, lines in stats.items():
            x = d["draws"].get(st)
            main = (cfg.get("prop_lines", {}).get(name) or {}).get(st)
            if x is None or st in BINARY:
                continue
            x = x[d["draws"]["min"] > 0]
            lab = STAT_LABEL.get(st, st)
            for ln, o_over, o_under in lines:
                if main and ln == main[0]:
                    continue
                if o_over is not None:
                    po, push = (x > ln).mean(), (x == ln).mean()
                    txt = f"{name} Over {ln:g} {lab}" if o_under is not None else f"{name} {ln + 0.5:g}+ {lab}"
                    out.append(dict(bet("prop", txt, po / (1 - push), o_over, ln, name, d["team"]), stat=st, side="over", alt=True))
                if o_under is not None:
                    pu, push = (x < ln).mean(), (x == ln).mean()
                    out.append(dict(bet("prop", f"{name} Under {ln:g} {lab}", pu / (1 - push), o_under, ln, name, d["team"]),
                                    stat=st, side="under", alt=True))
    return out


# Picks del modelo sin cuota (1.3.6, pedido de Nicolas 31-oct): tiros de campo anotados / intentados, doble-doble y
# triple-doble. Las casas que leemos casi nunca publican esas líneas, así que en vez de EV se da la probabilidad del
# modelo y la cuota mínima que hace falta para que la apuesta tenga valor (EV >= VALUE_EV); el usuario la compara
# con su casa. Tiros: la línea de referencia es su promedio por partido (temporada actual mezclada con la anterior
# como en el modelo, en los minutos de hoy), que es de donde parten las casas; el pick es el lado que el modelo ve
# con >= MP_P contra el .5 que menos le conviene (las cuentas de tiros son asimétricas: con la línea justo encima
# del promedio el under gana más de la mitad de las veces sin que el modelo vea nada). Doble-doble y triple-doble: "Sí" desde MP_DD / MP_TD. Solo jugadores que seguro juegan.
MP_SHOTS = ("fgm", "fga", "fg2m", "fg2a", "fg3a", "ftm", "fta")  # los triples anotados ya tienen líneas de casa
SHOT_SRC = {"fg2m": ("fgm", "fg3m"), "fg2a": ("fga", "fg3a")}
MP_P, MP_DD, MP_TD = 0.55, 0.35, 0.06
MP_MIN, MP_P_PLAY = 20, 0.9   # minutos proyectados mínimos y prob. de jugar
MP_MIN_AVG = {"fgm": 3.0, "fga": 7.0, "fg2m": 2.0, "fg2a": 4.0, "fg3a": 3.0, "ftm": 2.0, "fta": 2.5}  # con menos volumen las casas no publican línea


def model_picks(cfg, s):
    from model import player_weight, shot_prior
    lines = cfg.get("prop_lines", {})
    out = []
    for p in cfg["players"]:
        d = s["players"].get(p["name"])
        if not d or p["min"] < MP_MIN or d["p_play"] < MP_P_PLAY:
            continue
        on = d["draws"]["min"] > 0
        mk = lines.get(p["name"], {})

        def add(st, side, line, prob, ref=None):
            out.append(dict(kind="prop", model_only=True, player=p["name"], team=p["team"], stat=st, side=side,
                            line=line, p=round(float(prob), 3), fair=round(1 / max(prob, 1e-3), 2),
                            min_dec=round((1 + VALUE_EV) / max(prob, 1e-3), 2), ref=ref,
                            label=f"{p['name']} {STAT_LABEL[st]}: Sí" if st in BINARY else
                            f"{p['name']} {'Over' if side == 'over' else 'Under'} {line:g} {STAT_LABEL[st]}"))
        for st in MP_SHOTS:
            x = d["draws"].get(st)
            src = SHOT_SRC.get(st, (st, None))  # dobles = tiros de campo - triples
            if x is None or st in mk or any(c and c not in p["cur"] for c in src):
                continue
            x, w = x[on], player_weight(p)
            pr = shot_prior(p) if all(c is None or p["prior"].get(c) is not None for c in src) and p["prior"].get("min", 0) > 0 else p["cur"]
            per_g = lambda row: row[src[0]] - (row[src[1]] if src[1] else 0)
            per_min = w * per_g(p["cur"]) / max(p["cur"]["min"], 1) + (1 - w) * per_g(pr) / max(pr["min"], 1)
            avg = per_min * p["min"]
            if avg < MP_MIN_AVG[st]:
                continue
            # la casa pone la línea en uno de los dos .5 alrededor del promedio: se prueba cada lado contra el que
            # le es menos favorable (over contra el .5 de arriba, under contra el de abajo)
            hi, lo = float(np.floor(avg) + 0.5), float(np.floor(avg) - 0.5)
            po, pu = float((x > hi).mean()), float((x < lo).mean()) if lo > 0 else 0.0
            if max(po, pu) >= MP_P:
                add(st, "over" if po >= pu else "under", hi if po >= pu else lo, max(po, pu), round(avg, 1))
        for st, thr in (("dd", MP_DD), ("td", MP_TD)):
            x = d["draws"].get(st)
            if x is not None and st not in mk and x[on].mean() >= thr:
                add(st, "over", 0.5, x[on].mean())
    return sorted(out, key=lambda b: -b["p"])


# % de cumplimiento (pedido de Nicolas 2-nov, estilo ludocomparador/Linemate): para cada prop, en cuántos de sus
# últimos partidos de esta temporada (hasta HR_N, de results/) y de sus H2H contra el rival de hoy (temporada pasada
# en h2h/ + esta temporada) habría ganado el pick con la misma línea. Solo partidos que jugó (min > 0) y que traen
# esa estadística (los H2H de la temporada pasada solo tienen MIN, PTS, REB, AST y triples). hr = {l10: [aciertos,
# partidos], h2h: [aciertos, partidos]}.
HR_N = 10
_LOGS = {}


PLAYOFFS = ("04-14", "10-15")  # entre estas fechas (mes-día) solo hay play-in / playoffs: no cuentan


def _derive(line):
    if "fga" in line and "fg3a" in line:
        line.update(fg2m=line["fgm"] - line["fg3m"], fg2a=line["fga"] - line["fg3a"])
    if "stl" in line and "blk" in line:
        line["stocks"] = line["stl"] + line["blk"]
    line.update(pra=line["pts"] + line["reb"] + line["ast"], pr=line["pts"] + line["reb"], pa=line["pts"] + line["ast"],
                ra=line["reb"] + line["ast"])
    ten = sum(line.get(k, 0) >= 10 for k in ("pts", "reb", "ast", "stl", "blk"))
    line.update(dd=int(ten >= 2), td=int(ten >= 3))
    return line


def player_logs(date):
    """{nombre normalizado: {fecha: partido}} antes de `date`, temporada regular, juntando (por orden de preferencia):
    gamelogs/nba/ (game logs oficiales de nba.com, con tiros), results/ (box scores que cargamos cada madrugada:
    cubren los días posteriores al último CSV) y h2h/<temporada anterior>.json (cruces, sin tiros).
    Cada partido: dict(date, pair={equipos}, team, cur=temporada actual, line={estadísticas})."""
    if date in _LOGS:
        return _LOGS[date]
    from evaluate import actual_line, norm
    logs = {}

    def put(n, d, row):
        if d >= date or PLAYOFFS[0] <= d[5:] <= PLAYOFFS[1] or season_of(d) not in (season_of(date), prior_season(date)):
            return  # solo temporada regular de esta temporada y la anterior
        have = logs.setdefault(norm(n), {})
        from datetime import date as _d, timedelta
        for k in (1, -1):  # las fuentes pueden fechar distinto el mismo partido (hora de EE. UU. vs Europa)
            dd = (_d.fromisoformat(d) + timedelta(days=k)).isoformat()
            if dd in have and have[dd]["pair"] == row["pair"] and d not in have:
                d = dd
        if d in have:  # mismo partido de otra fuente: solo completa lo que falta (p. ej. tiros)
            for k, v in row["line"].items():
                have[d]["line"].setdefault(k, v)
            have[d]["team"] = have[d]["team"] or row["team"]
            have[d]["home"] = have[d].get("home") or row.get("home")
            return
        row.update(date=d, cur=season_of(d) == season_of(date))
        have[d] = row
    import csv  # gamelogs/nba/: game logs oficiales de nba.com (leaguegamelog), bajados desde el PC de Nicolas
    for fp in sorted(glob.glob(os.path.join(HERE, "gamelogs", "nba", "leaguegamelog_P_*.csv"))):
        for r in csv.DictReader(open(fp, encoding="utf-8")):
            try:
                mi = float(r["MIN"] or 0)
            except ValueError:
                mi = 0
            if mi <= 0:
                continue
            opp = re.split(r" @ | vs\. ", r["MATCHUP"])[-1].strip()
            home = opp if " @ " in r["MATCHUP"] else r["TEAM_ABBREVIATION"]
            line = dict(min=round(mi), **{k: int(float(r[c] or 0)) for k, c in (
                ("pts", "PTS"), ("reb", "REB"), ("ast", "AST"), ("stl", "STL"), ("blk", "BLK"), ("fgm", "FGM"),
                ("fga", "FGA"), ("fg3m", "FG3M"), ("fg3a", "FG3A"), ("ftm", "FTM"), ("fta", "FTA"), ("tov", "TOV"),
                ("pf", "PF"))})
            put(r["PLAYER_NAME"], r["GAME_DATE"][:10], dict(pair={r["TEAM_ABBREVIATION"], opp},
                                                            team=r["TEAM_ABBREVIATION"], home=home, line=_derive(line)))
    for rp in sorted(glob.glob(os.path.join(HERE, "results", "20??-??-??.json"))):
        R = json.load(open(rp))
        for key, x in R["games"].items():
            a, h = key.split("@")
            for n in x["players"]:
                al = actual_line(x["players"], n)
                if al and al["min"] > 0:
                    put(n, R["date"], dict(pair={a, h}, team=None, home=h, line=al))
    hp = os.path.join(HERE, "h2h", f"{prior_season(date)}.json")
    if os.path.exists(hp):
        for x in json.load(open(hp))["games"]:
            for team, ps in x["players"].items():
                for n, mi, pts, reb, ast, f3, *_ in ps:
                    if mi > 0:
                        put(n, x["date"], dict(pair={x["away"], x["home"]}, team=team, home=x["home"],
                                               line=_derive(dict(min=mi, pts=pts, reb=reb, ast=ast, fg3m=f3))))
    _LOGS[date] = logs
    return logs


DVP_OF = {"pts": "PTS", "reb": "REB", "ast": "AST", "fg3m": "3PM", "pra": "PRA", "stl": "STL", "blk": "BLK", "tov": "TOV"}


def add_hit_rates(cfg, bets):
    """También el DvP (pedido de Nicolas 2-nov): puesto del rival en lo que concede de esa estadística a la posición
    del jugador (1 = el que menos concede, 30 = el que más), con su valor y la media de la liga."""
    from evaluate import norm
    g = cfg["game"]
    team_of = {p["name"]: p["team"] for p in cfg["players"]}
    pos_of = {p["name"]: (_positions(p.get("slot") or p["pos"]) or [None])[0] for p in cfg["players"]}
    R = load_rankings()
    lg = {}
    logs = player_logs(g["date"])
    for b in bets:
        if b.get("kind") != "prop" or not b.get("stat") or b.get("line") is None:
            continue
        st, L, over = b["stat"], b["line"], is_over(b)
        team = team_of.get(b["player"], b.get("team"))
        rival = g["home"] if team == g["away"] else g["away"]
        rows = sorted(logs.get(norm(b["player"]), {}).values(), key=lambda r: r["date"], reverse=True)
        hit = lambda r: (r["line"][st] > L) if over else (r["line"][st] < L)
        # últimos 10 (regla de Nicolas, 2-nov): los de esta temporada y, si no llegan a 10, los últimos de la pasada
        # (rookies o quien no jugó la pasada solo tienen los de esta)
        cur = [r for r in rows if st in r["line"]][:HR_N]
        hh = [r for r in rows if st in r["line"] and rival in r["pair"] and (r["team"] or team) != rival]
        b["hr"] = dict(l10=[sum(map(hit, cur)), len(cur)], h2h=[sum(map(hit, hh)), len(hh)])
        pos, col = pos_of.get(b["player"]), DVP_OF.get(st)
        row = (((R or {}).get("teams", {}).get(rival) or {}).get("dvp") or {}).get(pos, {}).get(col) if pos and col else None
        if row:
            if (pos, col) not in lg:
                vals = [T["dvp"][pos][col]["value"] for T in R["teams"].values() if (T.get("dvp") or {}).get(pos, {}).get(col)]
                lg[(pos, col)] = round(float(np.mean(vals)), 1)
            b["hr"]["dvp"] = dict(pos=pos, rank=row["rank"], value=row["value"], lg=lg[(pos, col)], n=len(R["teams"]))


# Parlays recomendados (estilo SGP de Linemate). Mismo partido: la prob. conjunta sale de contar en
# cuántas simulaciones aciertan TODAS las patas a la vez, así entra la correlación (si un equipo anota
# mucho, suben el over y los puntos de sus jugadores). Entre partidos: los partidos son independientes y
# las probabilidades se multiplican. Las props se calculan suponiendo que juegan todos sus jugadores (si
# uno no juega la casa anula esa pata) y no entran jugadores en duda.
PARLAY_LEG_P = 0.55     # prob. mínima de cada pata
PARLAY_MIN_DEC = 2.0    # cuota combinada mínima para que valga la pena como parlay
PARLAY_POOL = 14        # patas candidatas por partido (las más probables)
ALT_LEG_MIN_DEC = 1.2   # líneas alternativas / escalonadas con cuota menor no entran en parlays (casi no suman y la casa cobra mucho margen)
PARLAY_P_PLAY = 0.9     # jugadores en duda (prob. de jugar menor) no entran en parlays
LEG_KEYS = ("kind", "label", "player", "team", "line", "stat", "side", "p", "dec", "ev")


# Líneas escalonadas "X+" (pedido de Nicolas, 9-nov): por jugador y estadística, la probabilidad de llegar a cada
# umbral entero (25+ = Over 24.5) con la cuota mínima para que tenga valor. Si la casa tiene la línea principal con
# cuota a los dos lados, toda la escalera se corre en escala logit lo mismo que la mezcla con la casa movió esa línea
# (así hereda el ajuste del 1.5). Si Rushbet / BetPlay publican ese umbral (alt_lines), va su cuota y su EV.
LADDER_STATS = ("pts", "reb", "ast", "fg3m", "stl", "blk", "pra", "ra")
LADDER_P = (0.5, 0.97)
LADDER_MIN = 15  # minutos proyectados mínimos


def ladders(cfg, s, bets):
    lg = lambda p: np.log(p / (1 - p))
    off, alt = {}, {}
    for b in bets:
        if b["kind"] != "prop" or b.get("side") != "over":
            continue
        if b.get("alt"):
            alt[(b["player"], b["stat"], b["line"])] = b
        elif b.get("p_mkt") is not None and 0 < b["p_model"] < 1 and 0 < b["p"] < 1:
            off[(b["player"], b["stat"])] = lg(b["p"]) - lg(b["p_model"])
    out = {}
    for p in cfg["players"]:
        d = s["players"].get(p["name"])
        if not d or p["min"] < LADDER_MIN or d["p_play"] < PARLAY_P_PLAY:
            continue
        on = d["draws"]["min"] > 0
        for st in LADDER_STATS:
            x = d["draws"].get(st)
            if x is None:
                continue
            x, o, rungs = np.asarray(x)[on], off.get((p["name"], st), 0.0), []
            for k in range(1, int(x.max()) + 1):
                pm = float((x >= k).mean())
                if not 0 < pm < 1:
                    continue
                q = float(1 / (1 + np.exp(-(lg(pm) + o))))
                if q < LADDER_P[0]:
                    break
                if q > LADDER_P[1]:
                    continue
                rung = [k, round(q, 3), round((1 + VALUE_EV) / q, 2)]
                ab = alt.get((p["name"], st, k - 0.5))
                if ab:
                    rung += [ab["dec"], ab["ev"]]
                rungs.append(rung)
            if rungs:
                out.setdefault(p["name"], {"team": d["team"]})[st] = rungs
    return out


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
    return (x >= b["line"]) if is_over(b) else (x <= b["line"]), d["min"] > 0


def leg_group(b):  # patas que no se combinan entre sí: ganador/hándicap, total, y cada jugador
    return "side" if b["kind"] in ("ml", "spread") else "total" if b["kind"] == "total" else b["player"]


def parlay(legs, p, n_ind=None):
    dec = float(np.prod([l["dec"] for l in legs]))
    ind = float(np.prod([l["p"] for l in legs])) if n_ind is None else n_ind
    return dict(legs=legs, n=len(legs), p=round(p, 3), p_indep=round(ind, 3), corr=round(p / ind, 2) if ind else None,
                dec=round(dec, 2), fair=round(1 / p, 2) if p else None, ev=round(p * dec - 1, 3))


def best_parlays(cands, joint, distinct, exclusive=False, sizes=(2, 3, 4)):
    """Para 2, 3 y 4 patas: la combinación con mayor prob. conjunta, con cuota >= PARLAY_MIN_DEC y EV > 0.
    exclusive (1.5): cada parlay usa patas que no están en los anteriores, así una pata fallada no tumba a todos."""
    from itertools import combinations
    out, used = [], set()
    for k in sizes:
        best = None
        for c in combinations([i for i in range(len(cands)) if i not in used], k):
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
            if exclusive:
                used |= set(best[2])
    return out


def sgp(r, g, s):
    """Parlays del mismo partido. Devuelve (parlays, patas candidatas para los parlays entre partidos)."""
    flags = player_flags(g["date"], os.path.join(HERE, "games", f"{g['date']}_{g['away']}@{g['home']}.json"))
    pool = [b for b in r["bets"] + r.get("alt_bets", []) if (not b.get("alt") or b["dec"] >= ALT_LEG_MIN_DEC) and b["p"] >= PARLAY_LEG_P and b["ev"] >= 0 and b.get("p_mkt") is not None and
            b["kind"] == "prop" and b.get("stat") and s["players"][b["player"]]["p_play"] >= PARLAY_P_PLAY
            and (flags.get(b["player"]) or {}).get("t") not in NO_UNDER_FLAGS]  # 1.5: solo props, sin rookies
    pool = sorted(pool, key=lambda b: -b["p"])[:PARLAY_POOL]
    hits = [leg_hits(b, g, s) for b in pool]
    legs = [dict({k: b.get(k) for k in LEG_KEYS}, gid=r["id"], game=f"{g['away']} @ {g['home']}") for b in pool]
    distinct = lambda ls: len({leg_group(l) for l in ls}) == len(ls)

    def joint(c):
        # correlación de las simulaciones (conjunta / producto de las marginales del modelo) aplicada a las
        # probabilidades publicadas (mezcladas con la casa, 1.5)
        win = np.logical_and.reduce([hits[i][0] for i in c])
        ok = np.logical_and.reduce([hits[i][1] for i in c])
        if not ok.any():
            return 0.0
        pj = float(win[ok].mean())
        marg = float(np.prod([hits[i][0][ok].mean() for i in c]))
        q = [legs[i]["p"] for i in c]
        return min(pj / marg * float(np.prod(q)), min(q)) if marg else 0.0
    res = best_parlays(legs, joint, distinct, exclusive=True)
    return [dict(parlay([legs[i] for i in c], p), type="sgp") for p, _, c in res], legs


def cross_parlays(legs_by_game):
    """Parlays entre partidos con las 2 patas más probables y con valor (EV >= VALUE_EV) de cada partido."""
    cands = []
    for legs in legs_by_game:
        cands += sorted([l for l in legs if l["ev"] >= VALUE_EV and l["kind"] == "prop"], key=lambda l: -l["p"])[:2]
    distinct = lambda ls: len({l["gid"] for l in ls}) == len(ls)
    joint = lambda c: float(np.prod([cands[i]["p"] for i in c]))
    return [dict(parlay([cands[i] for i in c], p), type="cross")
            for p, _, c in best_parlays(cands, joint, distinct, exclusive=True, sizes=(2,))]  # 1.5: solo de 2 patas


# Parlay soñador (pedido de Nicolas, 9-nov): una combinada larga por jornada con cuota total entre DREAM_MIN y
# DREAM_MAX, armada con las patas más probables del modelo. Entre partidos las patas son independientes; dentro de un
# mismo partido van como mucho DREAM_PER_GAME patas y solo si no se estorban (correlación contada en la muestra de
# simulaciones docs/data/sims/<id>.bin >= DREAM_MIN_CORR). Se elige la combinación con la mayor prob. conjunta.
# Unidades: 1 por jornada; si una pata se anula (jugador que no juega) cuenta la cuota de las demás, como en la casa.
DREAM_MIN, DREAM_MAX = 50.0, 200.0
DREAM_LEG_P = 0.55       # prob. mínima de cada pata (mezclada con la casa)
DREAM_POOL = 10          # patas candidatas por partido (las más probables)
DREAM_PER_GAME = 2
DREAM_MAX_LEGS = 10
DREAM_MAX_UNDER = 0.5    # como mucho la mitad de las patas al Under (pedido de Nicolas, 9-nov: que no salga todo Under)
DREAM_LEG_EV = -0.03     # 1.5.1: EV mínimo de cada pata a cuota colombiana (con el margen de Betano casi ninguna
                         # pata llega a EV >= 0, y el soñador es una combinada de probabilidad, no de valor)
DREAM_CO_FROM = "2026-11-11"  # el soñador del 11-nov (aún sin jugar) se rehizo a cuota colombiana (pedido de Nicolas)
DREAM_MIN_CORR = 0.97    # pares del mismo partido: conjunta / producto >= esto (no se estorban)
DREAM_STATS = {"pra": ("pts", "reb", "ast"), "pr": ("pts", "reb"), "pa": ("pts", "ast"), "ra": ("reb", "ast"),
               "stocks": ("stl", "blk")}


def read_sample(gid):
    """docs/data/sims/<id>.bin → {columna: array} (ver write_sample)."""
    path = sample_path(gid)
    if not os.path.exists(path):
        return None
    raw = open(path, "rb").read()
    hl = int.from_bytes(raw[:4], "little")
    head = json.loads(raw[4:4 + hl])
    n, body = head["n"], np.frombuffer(raw[4 + hl:], np.uint8)
    return {c: body[i * n:(i + 1) * n].astype(float) for i, c in enumerate(head["cols"])}


def sample_hits(b, smp):
    """(acierta, juega) de una prop en la muestra, o None si la muestra no trae esa estadística."""
    on = smp.get(f"{b['player']}|on")
    if on is None:
        return None
    parts = DREAM_STATS.get(b["stat"], (b["stat"],))
    if any(f"{b['player']}|{p}" not in smp for p in parts):
        return None
    x = sum(smp[f"{b['player']}|{p}"] for p in parts)
    return ((x >= b["line"]) if is_over(b) else (x <= b["line"])), on > 0


def dream_parlay(rs):
    """rs = partidos publicados (sin jugar) de una jornada. Devuelve el parlay soñador o None."""
    from itertools import combinations
    import math
    opts = []  # por partido: [(log p, log cuota, patas)]
    wide = {}  # por partido: (patas candidatas, muestra de simulaciones), para los reemplazos
    for r in rs:
        g = r["game"]
        if g["date"] >= DREAM_CO_FROM:  # jornadas publicadas antes de 1.5.1: el soñador ya va a cuota colombiana
            old = [b for b in r["bets"] + r.get("alt_bets", []) if "dec_ref" not in b]
            for b in localize_odds(old):
                if "dec_ref" in b:
                    b["ev"] = round(b["p"] * b["dec"] - 1, 3)
        pp = {p["name"]: p.get("p_play", 1) for p in r["summary"]["players"]}
        pool = [b for b in r["bets"] + r.get("alt_bets", []) if (not b.get("alt") or b["dec"] >= ALT_LEG_MIN_DEC) and b["kind"] == "prop" and b.get("stat") and b.get("p_mkt") is not None
                and b["p"] >= DREAM_SWAP_MIN_P and b["ev"] >= (DREAM_LEG_EV if g["date"] >= DREAM_CO_FROM else 0) and pp.get(b["player"], 0) >= PARLAY_P_PLAY
                and (b.get("flag") or {}).get("t") not in NO_UNDER_FLAGS]
        pool = sorted(pool, key=lambda b: -b["p"])
        if not pool:
            continue
        mk = lambda b: dict({k: b.get(k) for k in LEG_KEYS}, gid=r["id"], game=f"{g['away']} @ {g['home']}",
                            **({"flag": b["flag"]} if b.get("flag") else {}))
        smp = read_sample(r["id"])
        wide[r["id"]] = ([mk(b) for b in pool[:DREAM_SWAP_POOL]], smp)  # candidatas a reemplazo
        legs = [mk(b) for b in pool if b["p"] >= DREAM_LEG_P][:DREAM_POOL]
        if not legs:
            continue
        o = [(math.log(l["p"]), math.log(l["dec"]), (l,)) for l in legs]
        if smp and DREAM_PER_GAME >= 2:
            hits = [sample_hits(l, smp) for l in legs]
            for i, j in combinations(range(len(legs)), 2):
                if legs[i]["player"] == legs[j]["player"] or not hits[i] or not hits[j]:
                    continue
                ok = hits[i][1] & hits[j][1]
                if ok.sum() < 100:
                    continue
                pa, pb = hits[i][0][ok].mean(), hits[j][0][ok].mean()
                c = (hits[i][0] & hits[j][0])[ok].mean() / (pa * pb) if pa * pb else 0
                if c < DREAM_MIN_CORR:
                    continue
                p = min(c * legs[i]["p"] * legs[j]["p"], legs[i]["p"], legs[j]["p"])
                o.append((math.log(p), math.log(legs[i]["dec"] * legs[j]["dec"]),
                          (dict(legs[i], corr=round(c, 2)), dict(legs[j], corr=round(c, 2)))))
        opts.append(o)
    # programación dinámica: estado (n.º de patas, cuota en pasos de 1% en log) → mejor (log p, patas)
    top = math.log(DREAM_MAX)
    best = {(0, 0, 0): (0.0, ())}  # (patas, Unders, cuota) → mejor (log p, patas)
    for o in opts:
        nxt = dict(best)
        for (n, u, d), (lp, ls) in best.items():
            for olp, old, ol in o:
                n2, u2 = n + len(ol), u + sum(l.get("side") == "under" for l in ol)
                ld = d / 100 + old
                if n2 > DREAM_MAX_LEGS or ld > top + 1e-9:
                    continue
                k = (n2, u2, round(ld * 100))
                if k not in nxt or nxt[k][0] < lp + olp:
                    nxt[k] = (lp + olp, ls + ol)
        best = nxt
    ok = [(lp, ls) for (n, u, d), (lp, ls) in best.items() if ls and u <= DREAM_MAX_UNDER * n
          and DREAM_MIN <= float(np.prod([l["dec"] for l in ls])) <= DREAM_MAX]
    if not ok:
        return None
    lp, legs = max(ok, key=lambda x: x[0])
    legs = sorted(legs, key=lambda l: (l["gid"], -l["p"]))
    pl = parlay(list(legs), math.exp(lp))
    pl["type"] = "dream"
    pl["games"] = len({l["gid"] for l in legs})
    dream_swaps(pl, wide)
    return pl


# Reemplazos (pedido de Nicolas, 11-nov): si la casa no ofrece una pata (p. ej. el jugador no sale en Betano), la web
# deja cambiarla por otra muy probable que cumpla las mismas reglas: p >= DREAM_LEG_P, EV >= 0, sin repetir jugador,
# máx. DREAM_PER_GAME patas por partido y, si comparte partido con otra pata, correlación >= DREAM_MIN_CORR en las
# simulaciones; máx. DREAM_MAX_UNDER de Unders y cuota total dentro de DREAM_MIN–DREAM_MAX. Cada pata lleva en
# `swaps` hasta DREAM_SWAPS candidatas (las más probables), y cada candidata en `pc` su correlación con las demás
# patas y candidatas de su partido, para que la web pueda combinar varios reemplazos y recalcular cuota y prob.
# Si con todas las reglas no salen 3, entran después (marcadas en `soft`) las que solo fallan en reglas blandas: prob.
# entre DREAM_SWAP_MIN_P y DREAM_LEG_P ("p") o cuota total fuera de 50–200 pero dentro de DREAM_SWAP_DEC ("dec").
# El tope de Unders, el de patas por partido, el jugador repetido y la correlación no se relajan nunca.
DREAM_SWAP_POOL = 25     # candidatas por partido que se miran
DREAM_SWAPS = 6          # candidatas guardadas por pata (la web muestra las 3 mejores que encajan)
DREAM_SWAP_MIN_P = 0.52
DREAM_SWAP_DEC = (40.0, 250.0)


def leg_key(l):
    return f"{l['player']}|{l['stat']}|{l['side']}|{l['line']}"


def dream_swaps(pl, wide):
    legs = pl["legs"]
    n = len(legs)
    keys = {leg_key(l) for l in legs}
    players = {l["player"] for l in legs}
    hits = {}

    def h(l):
        k = (l["gid"], leg_key(l))
        if k not in hits:
            smp = wide.get(l["gid"], (None, None))[1]
            hits[k] = sample_hits(l, smp) if smp else None
        return hits[k]

    def corr(a, b):
        ha, hb = h(a), h(b)
        if not ha or not hb:
            return None
        ok = ha[1] & hb[1]
        if ok.sum() < 100:
            return None
        pa, pb = ha[0][ok].mean(), hb[0][ok].mean()
        return round(float((ha[0] & hb[0])[ok].mean() / (pa * pb)), 2) if pa * pb else None
    dec = float(np.prod([l["dec"] for l in legs]))
    under = sum(l.get("side") == "under" for l in legs)
    chosen = {}  # candidatas elegidas por partido (para la tabla pc)
    for i, l in enumerate(legs):
        rest = [x for j, x in enumerate(legs) if j != i]
        out = []
        for gid, (cands, _) in wide.items():
            mates = [x for x in rest if x["gid"] == gid]
            if len(mates) >= DREAM_PER_GAME:
                continue
            for c in cands:
                if c["player"] in players or leg_key(c) in keys:
                    continue
                if under - (l.get("side") == "under") + (c.get("side") == "under") > DREAM_MAX_UNDER * n:
                    continue
                d = dec / l["dec"] * c["dec"]
                if not DREAM_SWAP_DEC[0] <= d <= DREAM_SWAP_DEC[1]:
                    continue
                soft = (["p"] if c["p"] < DREAM_LEG_P else []) + ([] if DREAM_MIN <= d <= DREAM_MAX else ["dec"])
                cc = None
                if mates:
                    cc = corr(c, mates[0])
                    if cc is None or cc < DREAM_MIN_CORR:
                        continue
                out.append((len(soft), -c["p"], dict(c, **({"soft": soft} if soft else {}))))
        out.sort(key=lambda x: x[:2])
        l["swaps"] = [c for *_, c in out[:DREAM_SWAPS]]
        for c in l["swaps"]:
            chosen.setdefault(c["gid"], {})[leg_key(c)] = c
    # correlación de cada candidata con las patas y demás candidatas de su partido (None = no se puede comprobar)
    for gid, cs in chosen.items():
        group = [x for x in legs if x["gid"] == gid] + list(cs.values())
        for c in cs.values():
            pc = {}
            for x in group:
                if x is c or x["player"] == c["player"]:
                    continue
                pc[leg_key(x)] = corr(c, x)
            for l in legs:
                for s in l["swaps"]:
                    if s["gid"] == gid and leg_key(s) == leg_key(c):
                        s["pc"] = pc
    for l in legs:
        l["key"] = leg_key(l)
        for s in l["swaps"]:
            s["key"] = leg_key(s)


def dream_units(pl):
    """Ganancia apostando 1 unidad: las patas anuladas no cuentan en la cuota."""
    if pl.get("result") == "won":
        return round(float(np.prod([l["dec"] for l in pl["legs"] if l.get("result") != "push"])) - 1, 2)
    return -1.0 if pl.get("result") == "lost" else 0.0 if pl.get("result") == "push" else None


def settle_parlay(pl, results):
    """won / lost / push (todas anuladas) / None si falta algún partido."""
    from evaluate import actual_line, settle
    rs = []
    for l in pl["legs"]:
        res = results.get(l["gid"])
        if res is None:
            return None
        x = settle(l, res["game"], res["res"])
        l["result"] = "push" if x is None else ("won" if x else "lost")
        if l["kind"] == "prop" and l.get("stat"):  # lo que hizo de verdad (la web lo muestra en jornadas pasadas)
            al = actual_line(res["res"]["players"], l["player"])
            l["actual"] = al.get(l["stat"]) if al and al["min"] > 0 else None
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
    for b in r["bets"] + r.get("alt_bets", []) + [l for pl in r.get("parlays", []) for l in pl["legs"]]:
        b.pop("flag", None)
        if b.get("player") in flags:
            b["flag"] = flags[b["player"]]
    return r


def game_id(path):
    return os.path.basename(path)[:-5].replace("@", "-at-").replace("_", "-")


# Muestra de simulaciones para "Mi combinada" de la web (31-oct): con ella el navegador cuenta en cuántas simulaciones
# aciertan a la vez las patas de un mismo partido que el usuario elija (incluidas líneas propias de la calculadora).
# docs/data/sims/<id>.bin: 4 bytes (largo de la cabecera, little-endian) + cabecera JSON {n, cols} + n bytes (0-255)
# por columna: marcador visitante/local y, por jugador, si juega y sus estadísticas base. Tiros de campo (dobles +
# triples), combinadas (PRA, R+T...) y doble-doble se rehacen en la web. La web usa la muestra solo para la
# correlación (conjunta / producto de las marginales en la muestra) y la aplica a las probabilidades publicadas.
# Solo de partidos sin jugar: al cargar el resultado se borra (la combinada ya se liquida con el box score).
SIMS_OUT = os.path.join(OUT, "sims")
SAMPLE_N = 1500
SAMPLE_STATS = ("pts", "reb", "ast", "fg3m", "stl", "blk", "tov", "pf", "fg2m", "fg2a", "fg3a", "ftm", "fta")


def sample_path(gid):
    return os.path.join(SIMS_OUT, f"{gid}.bin")


def write_sample(gid, s):
    os.makedirs(SIMS_OUT, exist_ok=True)
    k = min(SAMPLE_N, len(s["sh"]))
    u8 = lambda a: np.clip(np.round(np.asarray(a[:k], float)), 0, 255).astype(np.uint8).tobytes()
    cols, data = ["sa", "sh"], [u8(s["sa"]), u8(s["sh"])]
    for name, d in s["players"].items():
        dr = d["draws"]
        on = np.asarray(dr["min"][:k]) > 0
        if not on.any():
            continue
        cols.append(f"{name}|on"); data.append(u8(on))
        for st in SAMPLE_STATS:
            if st in dr:
                cols.append(f"{name}|{st}"); data.append(u8(dr[st]))
    head = json.dumps(dict(n=k, cols=cols), ensure_ascii=False).encode()
    with open(sample_path(gid), "wb") as f:
        f.write(len(head).to_bytes(4, "little") + head + b"".join(data))


def drop_sample(gid):
    if os.path.exists(sample_path(gid)):
        os.remove(sample_path(gid))


def run_game(path, n):
    cfg_raw = json.load(open(path))
    use_db = cfg_raw.get("db", {}).get("use", False) and os.path.exists(os.path.join(HERE, "db", "rankings_latest.json"))
    cfg = load_game(path, use_db)
    s = simulate(cfg, n)
    write_sample(game_id(path), s)
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
    pb = prop_bets(cfg, s)
    if g["date"] >= CO_PRICE_FROM and cfg_raw.get("prop_odds_region") != "CO":
        localize_odds(pb)
    bets = market_blend(tb + pb, player_flags(g["date"], path))
    R = load_rankings()
    mv = matchup_view(R, g, summ["players"], summ.get("db_report")) if R else None
    r = dict(id=game_id(path), game=g, market=cfg.get("market", {}), used_db=use_db, sims=n,
                summary=summ, bets=bets, matchup_view=mv, h2h=h2h_view(g, summ["players"]), cup=cup_view(g),
                records={t: cfg["teams"][t].get("record") for t in (g["away"], g["home"])},
                context={t: cfg["teams"][t].get("context", {}) for t in (g["away"], g["home"])})
    r["model_picks"] = model_picks(cfg, s)
    add_hit_rates(cfg, r["bets"] + r["model_picks"])
    r["rotation"] = rotation_view(cfg, summ)
    r["without"] = without_view(cfg, s, n)  # "Sin un jugador": compañeros con / sin cada titular
    r["alt_bets"] = [b for b in r["bets"] if b.get("alt")]
    r["bets"] = [b for b in r["bets"] if not b.get("alt")]
    r["ladders"] = ladders(cfg, s, r["bets"] + r["alt_bets"])
    r["parlays"], r["_legs"] = sgp(r, g, s)
    return r


# Movimiento de línea y línea de cierre (pedido de Nicolas 12-nov, idea de bucketsbase). Cada apuesta guarda en
# "open" = [línea, cuota] la primera vez que se publicó (análisis de la mañana); al re-simular en las revisiones
# horarias se conserva y line / dec pasan a ser las de la última revisión, que al empezar el partido es la de cierre.
# clv(): +1 si el pick le ganó a la línea de cierre (la línea se movió hacia su lado o su cuota bajó), -1 si se movió
# en contra, 0 si no cambió. Es la señal de valor más estable: no depende de la suerte del resultado.
CLV_DEC = 0.01  # cambio mínimo de cuota (sin cambio de línea) para contarlo como movimiento


def bet_key(b):
    side = b.get("side") or (b["label"].split()[0] if b["kind"] == "total" else None)
    return "|".join(str(x) for x in (b["kind"], b.get("player") or "", b.get("team") or "", b.get("stat") or "", side or "",
                                      b["line"] if b.get("alt") else ""))


def add_means(r):
    """Proyección media del modelo en cada prop (para "Proy. vs línea" en la web; idea 7 de bucketsbase)."""
    sp = {p["name"]: p for p in r["summary"]["players"]}
    for b in r["bets"] + r.get("model_picks", []):
        st = (sp.get(b.get("player")) or {}).get(b.get("stat") or "")
        if b.get("kind") == "prop" and isinstance(st, dict) and "mean" in st and b.get("stat") not in ("dd", "td"):
            b["mean"] = st["mean"]
    return r


def carry_open(r, old):
    """Pasa a r (re-simulado) la línea y cuota de apertura de la versión publicada antes (old)."""
    prev = {bet_key(b): b for b in (old or {}).get("bets", [])}
    for b in r["bets"]:
        o = prev.get(bet_key(b))
        b["open"] = (o.get("open") or [o.get("line"), o.get("dec")]) if o else [b.get("line"), b.get("dec")]
    return r


def clv(b):
    o = b.get("open")
    if not o or o[1] is None or b.get("dec") is None:
        return None
    ol, od = o
    if ol is not None and b.get("line") is not None and ol != b["line"]:
        up = b["line"] > ol
        if b["kind"] == "spread":
            return 1 if not up else -1   # hándicap de su equipo más negativo = el mercado lo quiere más
        over = (b.get("side") or b["label"].split()[0].lower()) == "over"
        return 1 if up == over else -1
    if abs(b["dec"] - od) < CLV_DEC:
        return 0
    return 1 if b["dec"] < od else -1


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
    for b in r.get("model_picks", []):
        x = settle(b, g, res)
        b["result"] = "push" if x is None else ("won" if x else "lost")
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
             model_picks=r.get("model_picks", []),
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
    skip = {i + 1 for i, x in enumerate(argv) if x in ("--sims", "--stage", "--for", "--dream")}
    args = [x for i, x in enumerate(argv) if not x.startswith("--") and i not in skip]
    if not os.path.exists(os.path.join(HERE, "db", "rankings_latest.json")):
        subprocess.run([sys.executable, os.path.join(HERE, "db", "build_db.py")], check=False)
    paths = sorted(p for p in glob.glob(os.path.join(HERE, "games", "*.json")) if not p.endswith("_results.json"))
    if args:
        paths = [p for p in paths if any(os.path.basename(p).startswith(a) for a in args)]
    os.makedirs(OUT, exist_ok=True)
    if "--samples-only" in argv:
        # solo la muestra de simulaciones de los partidos ya publicados que aún no se juegan (misma semilla y mismo
        # número de simulaciones que lo publicado: no cambia ningún pick)
        for p in paths:
            gid, out_path = game_id(p), os.path.join(OUT, f"{game_id(p)}.json")
            if not os.path.exists(out_path) or load_results(os.path.basename(p)[:10]).get(os.path.basename(p)[11:-5]):
                continue
            r = json.load(open(out_path))
            cfg = load_game(p, r["used_db"])
            write_sample(gid, simulate(cfg, r["sims"]))
            print(f"OK muestra {gid}")
        return
    idx_path = os.path.join(OUT, "index.json")
    index = json.load(open(idx_path)) if os.path.exists(idx_path) else {"games": []}
    keep = {g["id"]: g for g in index["games"]}
    cross = index.get("parlays", {})  # parlays entre partidos por jornada
    legs_by_date = {}
    sim_dates = set()  # jornadas con partidos sin jugar en esta pasada (tope diario de picks, 1.5)
    all_flags = {}  # etiquetas de los jugadores de esta pasada (para las patas de los parlays entre partidos)
    for p in paths:
        gid, date = game_id(p), os.path.basename(p)[:10]
        res = load_results(date).get(os.path.basename(p)[11:-5])
        out_path = os.path.join(OUT, f"{gid}.json")
        try:
            if res and os.path.exists(out_path):
                # partido ya jugado: no se vuelve a simular (la web muestra lo que se publicó antes del partido)
                r = attach_result(json.load(open(out_path)), res)
                drop_sample(gid)
                if not r.get("matchup_view") and load_rankings():  # partidos publicados antes de esta sección
                    r["matchup_view"] = matchup_view(load_rankings(), r["game"], r["summary"]["players"],
                                                     r["summary"].get("db_report"))
                if not (r.get("h2h") or {}).get("games"):
                    r["h2h"] = h2h_view(r["game"], r["summary"]["players"])
                if "cup" not in r:
                    r["cup"] = cup_view(tag_cup(r["game"]))
                if "without" not in r:  # partidos publicados antes de "Sin un jugador": misma simulación, sin cada uno
                    cfg = load_game(p, r["used_db"])
                    r["without"] = without_view(cfg, simulate(cfg, r["sims"]), r["sims"])
            elif "--ladder-only" in argv and os.path.exists(out_path):
                # solo la escalera X+ de un partido ya publicado sin jugar (misma semilla y simulaciones: no cambia picks)
                r = json.load(open(out_path))
                cfg = load_game(p, r["used_db"])
                r["ladders"] = ladders(cfg, simulate(cfg, r["sims"]), r["bets"] + r.get("alt_bets", []))
            elif "--hr-only" in argv and os.path.exists(out_path):
                # solo recalcula los % de cumplimiento (últ. 10, H2H, DvP) de un partido ya publicado sin jugar,
                # p. ej. tras bajar historiales nuevos a gamelogs/: no re-simula ni cambia ningún pick
                r = json.load(open(out_path))
                add_hit_rates(load_game(p, r["used_db"]), r["bets"] + r.get("model_picks", []))
            else:
                old = json.load(open(out_path)) if os.path.exists(out_path) else None
                r = carry_open(run_game(p, n), old)
                if res:
                    r = attach_result(r, res)
                    drop_sample(gid)
        except Exception as e:  # un JSON roto no debe tumbar toda la jornada
            print(f"ERROR {os.path.basename(p)}: {e}")
            continue
        add_means(r)
        flags = player_flags(date, p)
        apply_flags(r, flags)
        all_flags.update(flags)
        if "_legs" in r:
            legs_by_date.setdefault(date, []).append(r.pop("_legs"))
        json.dump(r, open(out_path, "w"), ensure_ascii=False, separators=(",", ":"))
        keep[r["id"]] = index_entry(r)
        if "result" not in r and "--hr-only" not in argv and "--ladder-only" not in argv:
            sim_dates.add(date)
        g, sm = r["game"], r["summary"]
        print(f"OK {r['id']}: {g['away']} {sm['proj'][g['away']]} - {g['home']} {sm['proj'][g['home']]}, "
              f"{keep[r['id']]['n_value']} apuestas con valor" + (" (resultado real cargado)" if res else ""))
    for date in sorted(sim_dates):  # 1.5: tope de picks por jugador y por jornada, entre todos sus partidos sin jugar
        ps = [os.path.join(OUT, f"{gid}.json") for gid, e in keep.items() if e["date"] == date and "result" not in e]
        rs = daily_cap([json.load(open(x)) for x in ps if os.path.exists(x)])
        for r in rs:
            json.dump(r, open(os.path.join(OUT, f"{r['id']}.json"), "w"), ensure_ascii=False, separators=(",", ":"))
            keep[r["id"]] = index_entry(r)
        print(f"Jornada {date}: {sum(b['value'] for r in rs for b in r['bets'])} picks con valor "
              f"(de {sum(b.get('cand', False) for r in rs for b in r['bets'])} candidatos)")
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
    dream = index.get("dream", {})  # parlay soñador por jornada
    redo = {d for d, lbg in legs_by_date.items() if d in cross and len(lbg) == sum(g["date"] == d and "result" not in g for g in games)}
    if opt("--dream"):  # --dream <fecha>: rehace el soñador con lo ya publicado (usar con un filtro "ninguno")
        redo.add(opt("--dream"))
    for date in sorted(redo):
        ps = [os.path.join(OUT, f"{g['id']}.json") for g in games if g["date"] == date and "result" not in g]
        if not ps:
            continue
        pl = dream_parlay([json.load(open(x)) for x in ps if os.path.exists(x)])
        if pl:
            dream[date] = pl
            print(f"Soñador {date}: {pl['n']} patas, cuota {pl['dec']}, prob. {pl['p']:.1%}")
        else:
            dream.pop(date, None)
            print(f"Soñador {date}: ninguna combinación llega a cuota {DREAM_MIN:g}-{DREAM_MAX:g}")
    for date, pl in dream.items():
        res = load_results(date)
        rs = {g["id"]: dict(game=dict(home=g["home"], away=g["away"]), res=res[f"{g['away']}@{g['home']}"])
              for g in games if g["date"] == date and f"{g['away']}@{g['home']}" in res}
        pl["result"] = settle_parlay(pl, rs)
        pl["units"] = dream_units(pl)
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
        cl = [clv(b) for g in games if g["date"] == date and "result" in g for b in g.get("value_bets", [])]
        cl = [c for c in cl if c is not None]
        history.append(dict(date=date, model=model_for(date), games=len(rs), winners_ok=sum(x["winner_ok"] for x in rs),
                            clv_beat=cl.count(1), clv_same=cl.count(0), clv_lost=cl.count(-1),
                            picks_won=sum(x["picks_won"] for x in rs), picks_lost=sum(x["picks_lost"] for x in rs),
                            units=round(sum(x["units"] for x in rs), 2),
                            sgp_won=sg.count("won"), sgp_lost=sg.count("lost"),
                            cross_won=cr.count("won"), cross_lost=cr.count("lost")))
    versions = []  # récord por versión del modelo (mismas cuentas que el historial por jornada)
    for v, start, desc in MODEL_VERSIONS:
        hs = [x for x in history if x["model"] == v]
        if hs:
            w, l = sum(x["picks_won"] for x in hs), sum(x["picks_lost"] for x in hs)
            u = round(sum(x["units"] for x in hs), 2)
            versions.append(dict(model=v, desc=desc, first=hs[0]["date"], last=hs[-1]["date"], days=len(hs),
                                 games=sum(x["games"] for x in hs), winners_ok=sum(x["winners_ok"] for x in hs),
                                 picks_won=w, picks_lost=l, units=u, roi=round(u / (w + l), 4) if w + l else None,
                                 sgp_won=sum(x["sgp_won"] for x in hs), sgp_lost=sum(x["sgp_lost"] for x in hs),
                                 cross_won=sum(x["cross_won"] for x in hs), cross_lost=sum(x["cross_lost"] for x in hs),
                                 **{k: sum(x.get(k, 0) for x in hs) for k in ("clv_beat", "clv_same", "clv_lost")}))
    now = datetime.now(timezone.utc).isoformat(timespec="minutes")
    updates = index.get("updates", {})  # las tres actualizaciones diarias: {jornada: {etapa: hora UTC}}
    if stage:
        day = stage_for or max((os.path.basename(p)[:10] for p in paths), default=last)
        updates.setdefault(day, {})[stage] = now
    updates = {d: updates[d] for d in sorted(updates)[-14:]}
    json.dump(dict(generated=now, value_ev=VALUE_EV, model=MODEL, mkt_w=MKT_W, max_daily=MAX_DAILY, last_stage=stage or index.get("last_stage"), updates=updates,
                   history=history, versions=versions, parlays=cross, dream=dream, cup=cup_summary(), games=games), open(idx_path, "w"), ensure_ascii=False, indent=1)
    try:  # pestaña Liga: equipos, líderes y margen de las casas (docs/data/league.json)
        import league_view
        league_view.build(last)
    except Exception as e:
        print(f"ERROR league_view: {e}")
    import player_cards  # ficha de jugador y rachas: docs/data/players/<fecha>.json de las jornadas tocadas
    for date in sorted({os.path.basename(p)[:10] for p in paths} | sim_dates):
        if any(g["date"] == date and "result" not in g for g in games) or not os.path.exists(os.path.join(OUT, "players", f"{date}.json")):
            player_cards.build(date)
    if sim_dates:  # bajas de última hora (bajas.py): las que ya entraron en la simulación quedan marcadas "sim"
        import bajas
        bajas.sync(sim_dates)


if __name__ == "__main__":
    main()
