"""
Compara el modelo con y sin el peso H2H (1.3) en una jornada ya jugada, solo en los jugadores que tienen
partidos H2H de la temporada pasada (y jugaron >= 10 min).

Uso:
    python backtest_h2h.py 2026-10-27 [K ...]

Lee games/<fecha>_*.json y results/<fecha>.json. Imprime log score (menor es mejor; prob. de caer a +-1 del
valor real) de PTS, REB, AST y PRA, y el error medio de minutos, para "sin H2H" y para cada H2H_K pedido.
"""
import copy, json, os, sys

import numpy as np

import model
from evaluate import actual_line

HERE = os.path.dirname(os.path.abspath(__file__))


def run(cfgs, res, use=True, n=10000):
    out = {k: [] for k in ("pts", "reb", "ast", "pra", "min")}
    for gid, cfg in cfgs.items():
        c = copy.deepcopy(cfg)
        if not use:
            for p in c["players"]:
                p.pop("h2h", None)
        s = model.simulate(c, n)
        for p in cfg["players"]:
            al = actual_line(res[gid]["players"], p["name"])
            if "h2h" not in p or not al or al["min"] < 10:
                continue
            d = s["players"][p["name"]]["draws"]
            on = d["min"] > 0
            for st in ("pts", "reb", "ast", "pra"):
                x = d[st][on]
                out[st].append(-np.log((np.sum(np.abs(x - al[st]) <= 1) + 1) / (len(x) + 3) / 3))
            out["min"].append(abs(d["min"][on].mean() - al["min"]))
    return len(out["min"]), {k: round(float(np.mean(v)), 3) if v else None for k, v in out.items()}


def main():
    date = sys.argv[1]
    ks = [float(k) for k in sys.argv[2:]] or [model.H2H_K]
    res = json.load(open(os.path.join(HERE, "results", f"{date}.json")))["games"]
    cfgs = {}
    for gid in res:
        a, h = gid.split("@")
        p = os.path.join(HERE, "games", f"{date}_{a}@{h}.json")
        if os.path.exists(p):
            raw = json.load(open(p))
            use_db = raw.get("db", {}).get("use", False) and os.path.exists(os.path.join(HERE, "db", "rankings_latest.json"))
            cfgs[gid] = model.load_game(p, use_db, True)
    n, base = run(cfgs, res, use=False)
    print(f"{date}: {n} jugadores con H2H")
    print(f"  sin H2H      {base}")
    for k in ks:
        model.H2H_K = k
        print(f"  H2H_K = {k:<4g} {run(cfgs, res)[1]}")


if __name__ == "__main__":
    main()
