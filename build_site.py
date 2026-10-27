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
"""
import glob, json, os, subprocess, sys
from datetime import datetime, timezone

import numpy as np

from model import american_to_prob, load_game, prob_to_american, simulate, summarize

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "docs", "data")
STAT_LABEL = {"pts": "Puntos", "reb": "Rebotes", "ast": "Asistencias", "fg3m": "Triples", "pra": "PTS+REB+AST"}
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
                               ev=round(p * (d - 1) - (1 - p), 3)))
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
            x = d["draws"]["pra"] if st == "pra" else d["draws"].get(st)
            if x is None:
                continue
            x = x[d["draws"]["min"] > 0]  # la prop se anula si no juega
            ln, o_over = v[0], v[1]
            o_under = v[2] if len(v) > 2 else None
            po, push = (x > ln).mean(), (x == ln).mean()
            lab = STAT_LABEL.get(st, st)
            out.append(bet("prop", f"{name} Over {ln:g} {lab}", po / (1 - push), o_over, ln, name, d["team"]))
            if o_under is not None:
                out.append(bet("prop", f"{name} Under {ln:g} {lab}", (1 - po - push) / (1 - push), o_under, ln, name, d["team"]))
    return out


def game_id(path):
    return os.path.basename(path)[:-5].replace("@", "-at-").replace("_", "-")


def run_game(path, n):
    cfg_raw = json.load(open(path))
    use_db = cfg_raw.get("db", {}).get("use", False) and os.path.exists(os.path.join(HERE, "db", "rankings_latest.json"))
    cfg = load_game(path, use_db)
    s = simulate(cfg, n)
    g = cfg["game"]
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
    return dict(id=game_id(path), game=g, market=cfg.get("market", {}), used_db=use_db, sims=n,
                summary=summ, bets=bets,
                records={t: cfg["teams"][t].get("record") for t in (g["away"], g["home"])},
                context={t: cfg["teams"][t].get("context", {}) for t in (g["away"], g["home"])})


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
    return r


def index_entry(r):
    g, sm = r["game"], r["summary"]
    vb = [b for b in r["bets"] if b["value"]]
    e = dict(id=r["id"], date=g["date"], away=g["away"], home=g["home"], tip_et=g.get("tip_et"),
             venue=g.get("venue"), records=r["records"], proj=sm["proj"], win_prob=sm["win_prob"],
             proj_total=sm["proj_total"], used_db=r["used_db"],
             n_value=len(vb), top_bets=r["bets"][:3], value_bets=vb)
    if "result" in r:
        e["result"] = dict(r["result"], picks_won=sum(b["result"] == "won" for b in vb),
                           picks_lost=sum(b["result"] == "lost" for b in vb),
                           units=round(sum(b["dec"] - 1 if b["result"] == "won" else -1 if b["result"] == "lost" else 0
                                           for b in vb), 2))
    return e


def main():
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    n = int(sys.argv[sys.argv.index("--sims") + 1]) if "--sims" in sys.argv else 20000
    args = [x for x in args if x != str(n)]
    if not os.path.exists(os.path.join(HERE, "db", "rankings_latest.json")):
        subprocess.run([sys.executable, os.path.join(HERE, "db", "build_db.py")], check=False)
    paths = sorted(p for p in glob.glob(os.path.join(HERE, "games", "*.json")) if not p.endswith("_results.json"))
    if args:
        paths = [p for p in paths if any(os.path.basename(p).startswith(a) for a in args)]
    os.makedirs(OUT, exist_ok=True)
    idx_path = os.path.join(OUT, "index.json")
    index = json.load(open(idx_path)) if os.path.exists(idx_path) else {"games": []}
    keep = {g["id"]: g for g in index["games"]}
    for p in paths:
        gid, date = game_id(p), os.path.basename(p)[:10]
        res = load_results(date).get(os.path.basename(p)[11:-5])
        out_path = os.path.join(OUT, f"{gid}.json")
        try:
            if res and os.path.exists(out_path):
                # partido ya jugado: no se vuelve a simular (la web muestra lo que se publicó antes del partido)
                r = attach_result(json.load(open(out_path)), res)
            else:
                r = run_game(p, n)
                if res:
                    r = attach_result(r, res)
        except Exception as e:  # un JSON roto no debe tumbar toda la jornada
            print(f"ERROR {os.path.basename(p)}: {e}")
            continue
        json.dump(r, open(out_path, "w"), ensure_ascii=False, separators=(",", ":"))
        keep[r["id"]] = index_entry(r)
        g, sm = r["game"], r["summary"]
        print(f"OK {r['id']}: {g['away']} {sm['proj'][g['away']]} - {g['home']} {sm['proj'][g['home']]}, "
              f"{keep[r['id']]['n_value']} apuestas con valor" + (" (resultado real cargado)" if res else ""))
    games = sorted(keep.values(), key=lambda g: (g["date"], g.get("tip_et") or "", g["id"]), reverse=False)
    history = []  # récord de los picks con valor por jornada (solo jornadas con resultados)
    for date in sorted({g["date"] for g in games if "result" in g}):
        rs = [g["result"] for g in games if g["date"] == date and "result" in g]
        history.append(dict(date=date, games=len(rs), winners_ok=sum(x["winner_ok"] for x in rs),
                            picks_won=sum(x["picks_won"] for x in rs), picks_lost=sum(x["picks_lost"] for x in rs),
                            units=round(sum(x["units"] for x in rs), 2)))
    json.dump(dict(generated=datetime.now(timezone.utc).isoformat(timespec="minutes"), value_ev=VALUE_EV,
                   history=history, games=games), open(idx_path, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
