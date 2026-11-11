"""Líneas de combinadas (P+R, P+A, R+A, PRA) y triples de scoresandodds, por jugador.

scoresandodds.com/nba/props/<stat> solo trae puntos, rebotes, asistencias y triples: las combinadas las carga por JS.
La página de cada jugador (scoresandodds.com/prop-bets/<id>/<slug>, los enlaces salen de /nba/props/points?date=)
sí trae las combinadas de 6-8 casas. Se bajan con WebFetch a odds/combos/<fecha>.json:
    {"source": "...", "players": {"Jugador": {"pr": [línea, over, under], "pa": ..., "ra": ..., "pra": ..., "fg3m": ...}}}
(línea con más casas reales, mejor cuota por lado; sin PrizePicks / Underdog / Sleeper / Kalshi). Ojo: algunas
páginas siguen mostrando el partido anterior hasta que las casas cuelgan el del día; esas se descartan.

Uso: python odds/merge_combos.py <fecha>   -> añade a slates/<fecha>.json las líneas de estadísticas que no tenían.
"""
import json, os, re, sys, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
STATS = ("pr", "pa", "ra", "pra", "fg3m")


def norm(x):
    x = unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode().lower()
    x = re.sub(r"[^a-z ]", "", x.replace("-", " "))
    return " ".join(w for w in x.split() if w not in ("jr", "sr", "ii", "iii", "iv"))


def merge(date):
    src = json.load(open(os.path.join(HERE, "combos", f"{date}.json"), encoding="utf-8"))
    path = os.path.join(HERE, "..", "slates", f"{date}.json")
    sl = json.load(open(path, encoding="utf-8"))
    pl = sl.setdefault("prop_lines", {})
    known = {norm(n): n for n in pl}  # nombres del slate (los de las props de puntos / rebotes / asistencias)
    n, miss = 0, []
    for name, stats in src["players"].items():
        k = known.get(norm(name))
        if not k:
            miss.append(name)
            continue
        for st, v in stats.items():
            if st in STATS and st not in pl[k] and v and v[0] is not None and (v[1] is not None or v[2] is not None):
                pl[k][st] = v
                n += 1
    sl.setdefault("sources", {})["props_combos"] = f"{src.get('source', 'scoresandodds por jugador')}: {n} líneas"
    json.dump(sl, open(path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"{n} líneas añadidas a {path}; sin jugador en el slate: {', '.join(miss) or 'ninguno'}")


if __name__ == "__main__":
    merge(sys.argv[1])
