"""Backtest de las props de tiros, pérdidas y faltas (1.6.2, 13-nov): suma simulada vs real por estadística y log loss
de P(over) en la línea .5 alrededor de su media, jornadas 26-oct a 12-nov (las que tienen box score ampliado).
    BT_RANKINGS=<carpeta> python bt_shots.py [n] [SHOT_THIN 0/1] [KFT] [K robos] [K pérdidas]
"""
import glob, json, os, sys, math, collections, tempfile
import numpy as np
import model
from evaluate import norm
from build_site import model_picks

N = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
if len(sys.argv) > 2: model.SHOT_THIN = bool(int(sys.argv[2]))
if len(sys.argv) > 3: model.SHOT_KFT = float(sys.argv[3])
if len(sys.argv) > 5: model.EXTRA_K = {"stl": float(sys.argv[4]), "tov": float(sys.argv[5])}
RK = os.environ.get("BT_RANKINGS")  # carpeta con un snapshot de rankings por fecha
TMP = os.path.join(tempfile.gettempdir(), "bt_shots_game.json")
IX = dict(zip(("min", "pts", "reb", "ast", "fg3m", "fgm", "fga", "fg3a", "stl", "blk", "tov", "pf", "ftm", "fta"), range(14)))
ST = ("pts", "fg3m", "fgm", "fga", "fg2m", "fg2a", "fg3a", "ftm", "fta", "tov", "pf", "stl", "blk")
mp = collections.defaultdict(list)
tot = collections.defaultdict(lambda: [0.0, 0.0]); ll = collections.defaultdict(list); half = {}
for f in sorted(glob.glob("games/2026-1[01]-*_*.json")):
    if f.endswith("_results.json"):
        continue
    date = f[6:16]
    try:
        R = json.load(open(f"results/{date}.json"))
    except FileNotFoundError:
        continue
    G = R["games"]
    act = {norm(n): v for x in (G.values() if isinstance(G, dict) else G) for n, v in x["players"].items()}
    if RK:  # rankings del día (db/build_db.py --snapshot <fecha> --out <RK>/<fecha>.json); sin ellos, los de hoy
        tmp = json.load(open(f)); tmp.setdefault("db", {})["rankings"] = os.path.join(RK, date + ".json")
        json.dump(tmp, open(TMP, "w")); f = TMP
    cfg = model.load_game(f)
    s = model.simulate(cfg, N)
    for b in model_picks(cfg, s):  # picks del modelo sin cuota (build_site.model_picks), liquidados con el box score
        a = act.get(norm(b["player"]))
        if b["stat"] in ST and a and len(a) >= 14 and a[0]:
            v = a[IX[b["stat"]]] if b["stat"] in IX else a[IX["fgm"]] - a[IX["fg3m"]] if b["stat"] == "fg2m" else a[IX["fga"]] - a[IX["fg3a"]]
            mp[b["stat"] + "/" + b["side"]].append((b["p"], (v > b["line"]) == (b["side"] == "over"), b["p_model"]))
    for n, P in s["players"].items():
        a = act.get(norm(n))
        if not a or len(a) < 14 or not a[0] or "fga" not in P["draws"]:
            continue
        a = {k: a[i] for k, i in IX.items()}
        a.update(fg2m=a["fgm"] - a["fg3m"], fg2a=a["fga"] - a["fg3a"])
        d = P["draws"]; on = d["min"] > 0
        for k in ST:
            x = d[k][on]
            if not len(x):
                continue
            m = float(x.mean()); tot[k][0] += a[k]; tot[k][1] += m
            ln = math.floor(m) + 0.5
            po = min(max(float((x > ln).mean()), 1e-3), 1 - 1e-3); y = a[k] > ln
            cdf = np.searchsorted(np.sort(x), np.arange(61), side="right") / len(x)  # P(X <= k)
            rps = float(((cdf - (a[k] <= np.arange(61))) ** 2).sum())  # ranked probability score: no depende de la línea
            ll[k].append((-math.log(po if y else 1 - po), po, y, rps))
print(f"n={N} thin={getattr(model, 'SHOT_THIN', None)} KFT={model.SHOT_KFT} K={model.EXTRA_K}")
for k in ST:
    L = np.array(ll[k])
    print(f"  {k:5} real/sim {tot[k][0] / tot[k][1]:.3f}  RPS {L[:, 3].mean():.4f}  logloss {L[:, 0].mean():.4f}  P(over) sim {L[:, 1].mean():.3f} real {L[:, 2].mean():.3f}  n={len(L)}")
print("  picks del modelo (n, acierto real, prob. publicada, prob. del modelo sin encoger):")
for k in sorted(mp):
    L = np.array(mp[k], float)
    print(f"    {k:10} n={len(L):4} real {L[:, 1].mean():.3f} publicada {L[:, 0].mean():.3f} modelo {L[:, 2].mean():.3f}")
