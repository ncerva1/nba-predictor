"""
Compara las predicciones publicadas de una jornada con los resultados reales.

Uso:
    python evaluate.py 2026-10-26

Lee docs/data/<fecha>-*.json (lo que se publicó en la web) y results/<fecha>.json
(marcadores y box scores reales: por jugador [MIN, PTS, REB, AST, 3PM]) y escribe
results/<fecha>_eval.json con:
    - por partido: ganador, margen, total, spread/total vs mercado, error de proyección
    - apuestas marcadas "con valor" (EV >= 3%): acierto y ganancia por 1 unidad
    - jugadores: media proyectada vs real (sesgo y MAE por estadística) y calibración de las líneas justas
    - calibración agregada (Brier, log loss) de win%, cover y over
"""
import glob, json, os, sys, unicodedata

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
STATS = {"pts": 1, "reb": 2, "ast": 3, "fg3m": 4}


def norm(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return s.replace(".", "").replace("'", "").replace(" jr", "").replace(" iii", "").replace(" ii", "").strip()


def actual_line(players, name):
    by = {norm(k): v for k, v in players.items()}
    v = by.get(norm(name))
    if v is None:
        return None
    out = dict(min=v[0], pts=v[1], reb=v[2], ast=v[3], fg3m=v[4])
    if len(v) >= 12:  # box score ampliado (1.3.4): ..., fgm, fga, fg3a, stl, blk, tov, pf
        out.update(zip(("fgm", "fga", "fg3a", "stl", "blk", "tov", "pf"), v[5:12]))
        out.update(fg2m=out["fgm"] - out["fg3m"], fg2a=out["fga"] - out["fg3a"], stocks=out["stl"] + out["blk"])
    if len(v) >= 14:  # 1.3.5: ..., ftm, fta
        out.update(ftm=v[12], fta=v[13])
    out.update(pra=out["pts"] + out["reb"] + out["ast"], pr=out["pts"] + out["reb"], pa=out["pts"] + out["ast"],
               ra=out["reb"] + out["ast"])
    ten = sum(out[k] >= 10 for k in ("pts", "reb", "ast", "stl", "blk") if k in out)
    out.update(dd=int(ten >= 2), td=int(ten >= 3))  # doble-doble / triple-doble (1.3.6)
    return out


def is_over(b):
    """Prop al over (o "sí" en doble-doble / triple-doble, que llevan "side")."""
    return b["side"] == "over" if b.get("side") else " Over " in b["label"]


def settle(b, g, res):
    """1 gana, 0 pierde, None push / sin dato."""
    h, a = g["home"], g["away"]
    m = res["home"] - res["away"]
    tot = res["home"] + res["away"]
    if b["kind"] == "ml":
        return int((m > 0) == (b["team"] == h))
    if b["kind"] == "spread":
        x = (m if b["team"] == h else -m) + b["line"]
        return None if x == 0 else int(x > 0)
    if b["kind"] == "total":
        x = tot - b["line"]
        if x == 0:
            return None
        return int(x > 0) if b["label"].startswith("Over") else int(x < 0)
    if b["kind"] == "prop":
        st = b.get("stat") or next(k for k, lab in (("pts", "Puntos"), ("reb", "Rebotes"), ("ast", "Asistencias"),
                                                    ("fg3m", "Triples"), ("pra", "PTS+REB+AST")) if b["label"].endswith(lab))
        al = actual_line(res["players"], b["player"])
        if al is None or al["min"] == 0 or st not in al:
            return None  # no jugó (o el box score no trae esa estadística): apuesta anulada
        x = al[st] - b["line"]
        return None if x == 0 else int(x > 0) if is_over(b) else int(x < 0)


def brier_ll(ps, ys):
    ps, ys = np.clip(np.array(ps, float), 1e-3, 1 - 1e-3), np.array(ys, float)
    return float(np.mean((ps - ys) ** 2)), float(-np.mean(ys * np.log(ps) + (1 - ys) * np.log(1 - ps)))


def evaluate(date):
    res_all = json.load(open(os.path.join(HERE, "results", f"{date}.json")))["games"]
    games, bets, players = [], [], []
    cal = {"win": ([], []), "cover": ([], []), "over": ([], []), "prop_line": ([], [])}
    for f in sorted(glob.glob(os.path.join(HERE, "docs", "data", f"{date}-*.json"))):
        d = json.load(open(f))
        g, s, mk = d["game"], d["summary"], d["market"]
        h, a = g["home"], g["away"]
        res = res_all.get(f"{a}@{h}")
        if res is None:
            continue
        m, tot = res["home"] - res["away"], res["home"] + res["away"]
        proj_m = s["proj"][h] - s["proj"][a]
        pw = s["win_prob"][h]
        cal["win"][0].append(pw); cal["win"][1].append(int(m > 0))
        cover = m + mk["spread_home"]
        if cover != 0:
            cal["cover"][0].append(s["p_home_cover"]); cal["cover"][1].append(int(cover > 0))
        if tot != mk["total"]:
            cal["over"][0].append(s["p_over"]); cal["over"][1].append(int(tot > mk["total"]))
        games.append(dict(
            id=d["id"], score=f"{a} {res['away']} - {h} {res['home']}" + (" (OT)" if res.get("ot") else ""),
            proj=f"{a} {s['proj'][a]} - {h} {s['proj'][h]}",
            pick_winner=h if pw >= 0.5 else a, p_pick=round(max(pw, 1 - pw), 3), winner=h if m > 0 else a,
            winner_ok=(pw >= 0.5) == (m > 0),
            margin_err=round(m - proj_m, 1), total_err=round(tot - s["proj_total"], 1),
            pts_err={h: round(res["home"] - s["proj"][h], 1), a: round(res["away"] - s["proj"][a], 1)},
            market_spread_home=mk["spread_home"], market_total=mk["total"],
            model_vs_market_margin=round(proj_m + mk["spread_home"], 1),  # >0: modelo más pro-local que el mercado
            market_margin_err=round(m + mk["spread_home"], 1)))
        for b in d["bets"]:
            if not b["value"]:
                continue
            r = settle(b, g, res)
            bets.append(dict(game=d["id"], label=b["label"], kind=b["kind"], dec=b["dec"], p=b["p"], ev=b["ev"],
                             result=r, profit=None if r is None else round(b["dec"] - 1 if r else -1.0, 3)))
        for p in s["players"]:
            al = actual_line(res["players"], p["name"])
            row = dict(game=d["id"], name=p["name"], team=p["team"], pos=p["pos"], proj_min=p["min"],
                       played=al is not None and al["min"] > 0, act=al)
            if al and al["min"] > 0:
                for st in ("pts", "reb", "ast", "fg3m", "pra"):
                    cal["prop_line"][0].append(p[st]["p_over"]); cal["prop_line"][1].append(int(al[st] > p[st]["line"]))
                # 1.3.4: también robos, tapones, tiros... si el box score es el ampliado y el partido los proyectó
                row["err"] = {st: round(al[st] - p[st]["mean"], 1) for st in al if st != "min" and isinstance(p.get(st), dict)}
                row["err"]["min"] = round(al["min"] - p["min"], 1)
            players.append(row)

    played = [p for p in players if p["played"]]
    pstats = {}
    for st in ("min", "pts", "reb", "ast", "fg3m", "pra", "pr", "pa", "ra", "stl", "blk", "stocks", "tov", "pf",
               "fgm", "fga", "fg2m", "fg2a", "fg3a", "ftm", "fta", "dd", "td"):
        e = np.array([p["err"][st] for p in played if st in p["err"]])
        if not len(e):
            continue
        pstats[st] = dict(bias=round(float(e.mean()), 2), mae=round(float(np.abs(e).mean()), 2), n=len(e))
    settled = [b for b in bets if b["result"] is not None]
    calib = {}
    for k, (ps, ys) in cal.items():
        if ps:
            br, ll = brier_ll(ps, ys)
            calib[k] = dict(n=len(ps), brier=round(br, 3), logloss=round(ll, 3),
                            mean_p=round(float(np.mean(ps)), 3), hit_rate=round(float(np.mean(ys)), 3))
    # prop_line: deciles de probabilidad -> frecuencia real
    ps, ys = map(np.array, cal["prop_line"])
    bins = [(0, .35), (.35, .45), (.45, .55), (.55, .65), (.65, 1.01)]
    calib["prop_line_bins"] = [dict(rango=f"{lo:.2f}-{hi:.2f}", n=int(((ps >= lo) & (ps < hi)).sum()),
                                    p_media=round(float(ps[(ps >= lo) & (ps < hi)].mean()), 3) if ((ps >= lo) & (ps < hi)).any() else None,
                                    real=round(float(ys[(ps >= lo) & (ps < hi)].mean()), 3) if ((ps >= lo) & (ps < hi)).any() else None)
                               for lo, hi in bins]
    mae = lambda k: round(float(np.mean([abs(g[k]) for g in games])), 1)
    out = dict(
        date=date,
        games=games,
        summary=dict(
            n_games=len(games), winners_ok=sum(g["winner_ok"] for g in games),
            margin_mae=mae("margin_err"), total_mae=mae("total_err"),
            market_margin_mae=mae("market_margin_err"),
            market_total_mae=round(float(np.mean([abs(int(g["score"].split()[1]) + int(g["score"].split()[4]) - g["market_total"])
                                                   for g in games])), 1),
            total_bias=round(float(np.mean([g["total_err"] for g in games])), 1),
            value_bets=len(bets), value_bets_settled=len(settled), value_bets_won=sum(b["result"] for b in settled),
            value_units=round(sum(b["profit"] for b in settled), 2)),
        calibration=calib, value_bets=bets, player_errors=pstats,
        no_show=[dict(game=p["game"], name=p["name"], proj_min=p["proj_min"]) for p in players
                 if not p["played"] and p["proj_min"] >= 8],
        biggest_misses=sorted([dict(game=p["game"], name=p["name"], proj=round(p["act"]["pts"] - p["err"]["pts"], 1),
                                    real=p["act"]["pts"], err=p["err"]["pts"]) for p in played],
                              key=lambda x: -abs(x["err"]))[:12],
        players=players)
    json.dump(out, open(os.path.join(HERE, "results", f"{date}_eval.json"), "w"), indent=1, ensure_ascii=False)
    return out


if __name__ == "__main__":
    o = evaluate(sys.argv[1])
    for g in o["games"]:
        print(f"{g['id']:<24} real {g['score']:<28} proy {g['proj']:<26} ganador {'OK ' if g['winner_ok'] else 'MAL'} "
              f"(p {g['p_pick']:.2f}) err margen {g['margin_err']:+5.1f} total {g['total_err']:+6.1f} | mercado err {g['market_margin_err']:+5.1f}")
    print(json.dumps(o["summary"], indent=1, ensure_ascii=False))
    print(json.dumps(o["calibration"], indent=1, ensure_ascii=False))
    for b in o["value_bets"]:
        print(f"  {b['game']:<24} {b['label']:<34} dec {b['dec']:<6} p {b['p']:.2f} ev {b['ev']:+.2f} -> {b['result']} {b['profit']}")
    print("Jugadores:", o["player_errors"])
    print("No jugaron:", o["no_show"])
    for x in o["biggest_misses"]:
        print("  ", x)
