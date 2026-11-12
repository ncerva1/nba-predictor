"""
Contexto del jugador para el modelo 1.7 (pedido de Nicolas, 12-nov): el modelo no debe mirar solo promedios de
temporada, sino la lectura completa de cada jugador hoy. Añade a cada jugador del partido, desde sus partidos de esta
temporada antes de la fecha (build_site.player_logs):

  recent: promedios por partido de sus últimos RECENT_N partidos (forma reciente: minutos, puntos, rebotes...)
  ctx:    promedios en los partidos de esta temporada con la misma situación de compañeros clave que hoy
          (los que juegan hoy jugaron y los que faltan hoy faltaron). Así se recoge un cambio de rol: Franz Wagner
          sin Banchero ni Suggs reparte 8.5 asistencias en 38 min; con los dos, 5 en 31.

Compañero clave = de su equipo, con >= KEY_MIN min por partido esta temporada en >= 3 partidos. Hoy "falta" si no
está en la lista de jugadores del partido o su p_play < 0.5.
model.simulate mezcla estas tasas con las de la temporada (pesos RECENT_W y n / (n + CTX_K)).
"""
from collections import defaultdict

RECENT_N, KEY_MIN, MODE = 5, 28.0, "all"
STATS = ("min", "pts", "reb", "ast", "fg3m")


def _avg(gs):
    return {s: round(sum(g["line"].get(s, 0) for g in gs) / len(gs), 2) for s in STATS}


def add_context(cfg, date):
    from build_site import player_logs
    from evaluate import norm
    logs = player_logs(date)
    # equipo de cada partido (results/ no siempre lo trae: se hereda el último conocido del jugador)
    games = {}
    for n, gs in logs.items():
        t, rows = None, []
        for g in sorted(gs.values(), key=lambda g: g["date"]):
            t = g.get("team") or t
            if g["cur"] and t and (g["line"].get("min") or 0) > 0:
                rows.append(dict(g, _t=t))
        games[n] = rows
    pres = defaultdict(set)
    for n, rows in games.items():
        for g in rows:
            pres[(g["_t"], g["date"])].add(n)
    active = {norm(p["name"]) for p in cfg["players"] if p.get("p_play", 1.0) >= 0.5}
    for t in {p["team"] for p in cfg["players"]}:
        team_dates = sorted({d for (tt, d) in pres if tt == t})
        mins = defaultdict(list)
        for d in team_dates:
            for n in pres[(t, d)]:
                g = next(x for x in games[n] if x["date"] == d)
                mins[n].append(g["line"].get("min") or 0)
        key = {n for n, m in mins.items() if len(m) >= 3 and sum(m) / len(m) >= KEY_MIN}
        for p in cfg["players"]:
            if p["team"] != t:
                continue
            n = norm(p["name"])
            rows = [g for g in games.get(n, []) if g["_t"] == t]
            if not rows:
                continue
            rows.sort(key=lambda g: g["date"], reverse=True)
            if len(rows) >= 3:
                p["recent"] = dict(g=min(len(rows), RECENT_N), **_avg(rows[:RECENT_N]))
            mates = key - {n}
            today_in = {m for m in mates if m in active}
            out = mates - today_in
            if MODE == "out" and out:  # faltan compañeros clave: partidos en que también faltaron (el resto libre)
                same = [g for g in rows if not any(m in pres[(t, g["date"])] for m in out)]
            else:
                same = [g for g in rows if all((m in pres[(t, g["date"])]) == (m in today_in) for m in mates)]
            if same and len(same) < len(rows):  # solo si el contexto de hoy difiere de alguno de sus partidos
                p["ctx"] = dict(g=len(same), out=sorted(m for m in mates if m not in today_in), **_avg(same))
    return cfg
