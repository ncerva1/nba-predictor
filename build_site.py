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


def bet(kind, label, p, odds, line=None, player=None, team=None, assumed=False):
    imp = american_to_prob(odds)
    dec = 1 + (odds / 100 if odds > 0 else 100 / -odds)
    ev = p * (dec - 1) - (1 - p)
    return dict(kind=kind, label=label, player=player, team=team, line=line, odds=odds,
                odds_assumed=assumed, p=round(float(p), 3), implied=round(imp, 3),
                fair=prob_to_american(p), edge=round(float(p - imp), 3), ev=round(float(ev), 3),
                value=bool(ev >= VALUE_EV))


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
    summ = summarize(cfg, s)
    bets = team_bets(cfg, s) + prop_bets(cfg, s)
    bets.sort(key=lambda b: b["ev"], reverse=True)
    g = cfg["game"]
    return dict(id=game_id(path), game=g, market=cfg.get("market", {}), used_db=use_db, sims=n,
                summary=summ, bets=bets,
                records={t: cfg["teams"][t].get("record") for t in (g["away"], g["home"])},
                context={t: cfg["teams"][t].get("context", {}) for t in (g["away"], g["home"])})


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
        try:
            r = run_game(p, n)
        except Exception as e:  # un JSON roto no debe tumbar toda la jornada
            print(f"ERROR {os.path.basename(p)}: {e}")
            continue
        json.dump(r, open(os.path.join(OUT, f"{r['id']}.json"), "w"), ensure_ascii=False, separators=(",", ":"))
        g, sm = r["game"], r["summary"]
        keep[r["id"]] = dict(id=r["id"], date=g["date"], away=g["away"], home=g["home"], tip_et=g.get("tip_et"),
                             venue=g.get("venue"), records=r["records"], proj=sm["proj"], win_prob=sm["win_prob"],
                             proj_total=sm["proj_total"], used_db=r["used_db"],
                             n_value=sum(b["value"] for b in r["bets"]), top_bets=r["bets"][:3],
                             value_bets=[b for b in r["bets"] if b["value"]])
        print(f"OK {r['id']}: {g['away']} {sm['proj'][g['away']]} - {g['home']} {sm['proj'][g['home']]}, "
              f"{keep[r['id']]['n_value']} apuestas con valor")
    games = sorted(keep.values(), key=lambda g: (g["date"], g.get("tip_et") or "", g["id"]), reverse=False)
    json.dump(dict(generated=datetime.now(timezone.utc).isoformat(timespec="minutes"), value_ev=VALUE_EV, games=games),
              open(idx_path, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
