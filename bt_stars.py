"""Backtest 1.6.1/1.7: proyección de minutos y puntos frente a lo real por nivel de anotador (cur pts), jornadas 1-11 nov.
    python bt_stars.py GARBAGE_CUT PTS_GAMMA MIN_BETA [n] [RECENT_W RECENT_MW CTX_K]   (CTX_K 0 = sin contexto)
"""
import glob, json, sys, statistics as st
import numpy as np
import model
from evaluate import norm

model.GARBAGE_CUT, model.PTS_GAMMA, model.MIN_BETA = float(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])
N = int(sys.argv[4]) if len(sys.argv) > 4 else 3000
if len(sys.argv) > 7:
    model.RECENT_W, model.RECENT_MW, model.CTX_K = map(float, sys.argv[5:8])
    if len(sys.argv) > 8: model.CTX_RK = float(sys.argv[8])
    model.USE_CTX = model.CTX_K > 0
rows, props = [], []
for f in sorted(glob.glob("games/2026-11-0[1-9]_*.json")) + sorted(glob.glob("games/2026-11-1[01]_*.json")):
    if f.endswith("_results.json"):
        continue
    date = f[6:16]
    try:
        R = json.load(open(f"results/{date}.json"))
    except FileNotFoundError:
        continue
    G = R["games"]
    act = {norm(n): v for x in (G.values() if isinstance(G, dict) else G) for n, v in x["players"].items()}
    cfg = model.load_game(f)
    s = model.simulate(cfg, N)
    cur = {p["name"]: p for p in cfg["players"]}
    IX = {"pts": 1, "reb": 2, "ast": 3, "fg3m": 4}
    for n, L in (cfg.get("prop_lines") or {}).items():
        a = act.get(norm(n))
        if not a or n not in s["players"]:
            continue
        d = s["players"][n]["draws"]
        for stt, v in L.items():
            parts = model.COMBOS.get(stt, (stt,))
            if not all(x in IX for x in parts) or not isinstance(v, list) or v[0] is None:
                continue
            real = sum(a[IX[x]] for x in parts)
            if real == v[0]:
                continue
            po = float((d[stt] > v[0]).mean())
            props.append((stt, po, int(real > v[0]), cur.get(n, {}).get("cur", {}).get("pts", 0)))
    for n, P in s["players"].items():
        a = act.get(norm(n))
        if not a or n not in cur:
            continue
        d = P["draws"]
        rows.append((cur[n]["cur"]["pts"], float(d["min"].mean()), float(d["pts"].mean()), a[0], a[1],
                     float(d["reb"].mean()), a[2], float(d["ast"].mean()), a[3],
                     float((d["pts"] > a[1]).mean())))
out = []
for lo, hi in ((0, 10), (10, 15), (15, 20), (20, 25), (25, 99)):
    r = [x for x in rows if lo <= x[0] < hi]
    out.append(f"{lo}-{hi} n={len(r)} min {st.mean(x[1] - x[3] for x in r):+.2f} pts {st.mean(x[2] - x[4] for x in r):+.2f} reb {st.mean(x[5] - x[6] for x in r):+.2f} ast {st.mean(x[7] - x[8] for x in r):+.2f}")
mae = lambda i, j: st.mean(abs(x[i] - x[j]) for x in rows)
# PIT: P(sim > real); bien calibrado = media 0.5
print(f"GC={model.GARBAGE_CUT} G={model.PTS_GAMMA} B={model.MIN_BETA} n={len(rows)} MAE min {mae(1,3):.2f} pts {mae(2,4):.3f} reb {mae(5,6):.3f} ast {mae(7,8):.3f} "
      f"bias pts {st.mean(x[2]-x[4] for x in rows):+.2f} PIT {st.mean(x[9] for x in rows):.3f}")
print(" | ".join(out))
import math
ll = lambda r: -st.mean(math.log(max(min(p if y else 1 - p, 1 - 1e-4), 1e-4)) for _, p, y, _ in r)
print(f"props n={len(props)} P(over) media {st.mean(p for _, p, _, _ in props):.3f} real over {st.mean(y for _, _, y, _ in props):.3f} logloss {ll(props):.4f}")
for g in ("pts", "reb", "ast", "fg3m", "pra"):
    r = [x for x in props if x[0] == g]
    if r: print(f"  {g} n={len(r)} P(over) {st.mean(p for _, p, _, _ in r):.3f} real {st.mean(y for _, _, y, _ in r):.3f} ll {ll(r):.4f}")
r = [x for x in props if x[3] >= 20]
print(f"  estrellas 20+ n={len(r)} P(over) {st.mean(p for _, p, _, _ in r):.3f} real {st.mean(y for _, _, y, _ in r):.3f} ll {ll(r):.4f}")
