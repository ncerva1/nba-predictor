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
model.simulate mezcla estas tasas con las de la temporada (pesos RECENT_W y n / (n + CTX_K)); está apagado
(model.USE_CTX) hasta que haya más partidos: con 10 PJ por equipo el backtest 1-11 nov salió casi neutro.
role_splits() da lo mismo para la ficha del jugador en la web (player_cards.py): con / sin cada compañero clave.
"""
from collections import defaultdict

RECENT_N, KEY_MIN, MODE = 5, 28.0, "all"
STATS = ("min", "pts", "reb", "ast", "fg3m")


def _avg(gs):
    return {s: round(sum(g["line"].get(s, 0) for g in gs) / len(gs), 2) for s in STATS}


def season_games(date):
    """(games, pres): partidos jugados esta temporada antes de `date` por jugador (con equipo `_t`) y
    pres[(equipo, fecha)] = jugadores que jugaron ese día."""
    from build_site import player_logs
    games = {}
    for n, gs in player_logs(date).items():
        t, rows = None, []  # results/ no siempre trae el equipo: se hereda el último conocido del jugador
        for g in sorted(gs.values(), key=lambda g: g["date"]):
            t = g.get("team") or t
            if g["cur"] and t and (g["line"].get("min") or 0) > 0:
                rows.append(dict(g, _t=t))
        games[n] = rows
    pres = defaultdict(set)
    for n, rows in games.items():
        for g in rows:
            pres[(g["_t"], g["date"])].add(n)
    return games, pres


def key_mates(t, games, pres, kmin=KEY_MIN, mg=3):
    """Jugadores del equipo con >= kmin minutos por partido en >= mg partidos: {nombre: (partidos, min)}."""
    mins = defaultdict(list)
    for (tt, d), ns in pres.items():
        if tt == t:
            for n in ns:
                mins[n].append(next(x for x in games[n] if x["date"] == d)["line"].get("min") or 0)
    return {n: (len(m), sum(m) / len(m)) for n, m in mins.items() if len(m) >= mg and sum(m) / len(m) >= kmin}


def _player_rows(n, t, games):
    return sorted((g for g in games.get(n, []) if g["_t"] == t), key=lambda g: g["date"], reverse=True)


def add_context(cfg, date):
    from evaluate import norm
    games, pres = season_games(date)
    active = {norm(p["name"]) for p in cfg["players"] if p.get("p_play", 1.0) >= 0.5}
    for t in {p["team"] for p in cfg["players"]}:
        key = set(key_mates(t, games, pres))
        for p in cfg["players"]:
            if p["team"] != t:
                continue
            n = norm(p["name"])
            rows = _player_rows(n, t, games)
            if not rows:
                continue
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
                p["ctx"] = dict(g=len(same), out=sorted(out), **_avg(same))
    return cfg


def role_splits(players, date, kmin=24.0):
    """Para la web. players: [(nombre, equipo, juega_hoy)] de los partidos de la jornada. Devuelve
    {nombre: dict(hoy=[g, min, pts, reb, ast, fg3m] en partidos con la misma situación que hoy (o None),
                  out=[compañeros clave que faltan hoy],
                  m=[[compañero, falta_hoy 0/1, [g, min, pts, reb, ast, fg3m] con él, [...] sin él], ...])}
    solo con compañeros clave que se han perdido algún partido suyo (si no, no hay "sin él")."""
    from evaluate import norm
    games, pres = season_games(date)
    disp = {norm(n): n for n, _, _ in players}
    active = {norm(n) for n, _, on in players if on}
    row = lambda gs: [len(gs), *(round(sum(g["line"].get(s, 0) for g in gs) / len(gs), 1) for s in STATS)] if gs else None
    keys, out = {}, {}
    for name, t, _ in players:
        if t not in keys:
            keys[t] = key_mates(t, games, pres, kmin, 2)  # en la web basta con 2 partidos (Banchero)
        n = norm(name)
        rows = _player_rows(n, t, games)
        if len(rows) < 3:
            continue
        mates = sorted(set(keys[t]) - {n}, key=lambda m: -keys[t][m][1])
        today_in = {m for m in mates if m in active}
        ms = []
        for m in mates:
            w = [g for g in rows if m in pres[(t, g["date"])]]
            wo = [g for g in rows if m not in pres[(t, g["date"])]]
            if w and wo:
                ms.append([disp.get(m) or m.title(), int(m not in today_in), row(w), row(wo)])
        if not ms:
            continue
        same = [g for g in rows if all((m in pres[(t, g["date"])]) == (m in today_in) for m in mates)]
        out[name] = dict(hoy=row(same) if len(same) < len(rows) else None,
                         out=[disp.get(m) or m.title() for m in mates if m not in today_in], m=ms)
    return out
