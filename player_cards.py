"""
Ficha de jugador y Rachas de la web (pedido de Nicolas 12-nov, ideas de bucketsbase).

    python player_cards.py 2026-11-12          # una jornada
    python player_cards.py --all               # todas las jornadas publicadas en docs/data/

Escribe docs/data/players/<fecha>.json con, por cada jugador de los partidos de esa jornada:
    t: equipo, pos: posición del modelo, dv: posición para el DvP (PG/SG/SF/PF/C), gid: partido, opp: rival, h: 1 si es local
    m: {stat: [línea, cuota over, cuota under, prob. over, prob. under, lado con valor]} de las líneas de la casa
    g: últimos partidos de temporada regular (esta y la pasada, más reciente primero):
       [fecha, rival, local 1/0, min, pts, reb, ast, fg3m, stl, blk, tov] (null si la fuente no lo trae)
    ro: rol según quién juega (context.role_splits): hoy = medias en partidos con la misma situación de compañeros
        clave que hoy, out = clave que faltan hoy, m = [compañero, falta hoy, con él, sin él]
        ([partidos, min, pts, reb, ast, fg3m]); solo esta temporada
    g lleva al final `k`: índice del partido en boxes/<fecha>.json (solo últimos 10 y cruces con el rival de hoy)
docs/data/boxes/<fecha>.json (la web lo carga al tocar un partido de las gráficas): games = [[fecha, visitante,
local, pts visitante, pts local, {equipo: [[jugador, *BOX], ...]}], ...] con el box score de los dos equipos.
y dvp: {equipo: {posición: {PTS/REB/AST/3PM/PRA/STL/BLK/TOV: puesto}}} (1 = el que menos concede) para el filtro
"nivel del rival". build_site.py lo llama solo al final de cada pasada; no re-simula ni cambia ningún pick.
"""
import glob, json, os, sys

from build_site import OUT, load_rankings, player_logs
from evaluate import is_over
from db_link import _positions
from evaluate import norm

N_GAMES = 40
COLS = ("min", "pts", "reb", "ast", "fg3m", "stl", "blk", "tov")
BOX = ("min", "pts", "reb", "ast", "fg3m", "fg3a", "fgm", "fga", "ftm", "fta", "stl", "blk", "tov", "pf")
BOX_RECENT = 10  # partidos recientes de cada jugador con box score (las barras de las tarjetas son los últimos 10)


def boxes(logs, want):
    """Box score de cada partido de `want` = {(fecha, frozenset(equipos))} desde los game logs de todos los jugadores.
    results/ no trae el equipo: se toma el del partido más cercano del jugador que sea de ese cruce."""
    out = {}
    for n, gs in logs.items():
        known = sorted((d, g["team"]) for d, g in gs.items() if g.get("team"))
        for d, g in gs.items():
            k = (d, frozenset(g["pair"]))
            if k not in want:
                continue
            t = g.get("team")
            if not t:
                near = sorted(known, key=lambda x: abs(int(x[0].replace("-", "")) - int(d.replace("-", ""))))
                t = next((tt for _, tt in near if tt in g["pair"]), None)
            if not t:
                continue
            out.setdefault(k, {"home": g.get("home"), "t": {}})["t"].setdefault(t, []).append(
                [g.get("name") or n, *[g["line"].get(c) for c in BOX]])
            if g.get("home") and not out[k]["home"]:
                out[k]["home"] = g["home"]
    return out


def market_lines(r):
    m = {}
    for b in r["bets"]:
        if b.get("kind") != "prop" or b.get("alt") or not b.get("stat") or b.get("line") is None:
            continue
        x = m.setdefault(b["player"], {}).setdefault(b["stat"], [b["line"], None, None, None, None, None])
        if x[0] != b["line"]:  # under con otra línea: la ficha usa la del over
            continue
        side = b.get("side") or ("over" if is_over(b) else "under")
        i = 1 if side == "over" else 2
        x[i], x[i + 2] = b.get("dec"), b.get("p")
        if b.get("value"):
            x[5] = side
    return m


def build(date):
    paths = sorted(p for p in glob.glob(os.path.join(OUT, f"{date}-*.json")))
    if not paths:
        return None
    logs = player_logs(date)
    R = load_rankings() or {}
    out, on, want = {}, {}, {}
    for p in paths:
        r = json.load(open(p))
        g = r["game"]
        lines = market_lines(r)
        for pl in r["summary"]["players"]:
            if "pts" not in pl:
                continue
            team = pl["team"]
            on[pl["name"]] = pl.get("p_play", 1) >= 0.5
            opp = g["home"] if team == g["away"] else g["away"]
            rows = sorted(logs.get(norm(pl["name"]), {}).values(), key=lambda x: x["date"], reverse=True)[:N_GAMES]
            gl = []
            for i, x in enumerate(rows):
                t = x["team"] or (team if team in x["pair"] else None)
                o = next(iter(x["pair"] - {t}), None) if t else None
                k = None
                if i < BOX_RECENT or o == opp:
                    k = want.setdefault((x["date"], frozenset(x["pair"])), len(want))
                gl.append([x["date"], o, int(x.get("home") == t) if t and x.get("home") else None,
                           *[x["line"].get(k) for k in COLS], k])
            out[pl["name"]] = dict(t=team, pos=pl.get("pos"), dv=(_positions(pl.get("pos") or "") or [None])[0],
                                   gid=r["id"], opp=opp, h=int(team == g["home"]), m=lines.get(pl["name"], {}),
                                   **({"flag": pl["flag"]} if pl.get("flag") else {}), g=gl)
    try:
        from context import role_splits
        ro = role_splits([(n, x["t"], on[n]) for n, x in out.items()], date)
    except Exception as e:  # sin logs suficientes la ficha sigue sin este bloque
        print("rol según quién juega no disponible:", e, file=sys.stderr)
        ro = {}
    for n, x in ro.items():
        out[n]["ro"] = x
    dvp = {t: {pos: {c: v["rank"] for c, v in cols.items()} for pos, cols in (T.get("dvp") or {}).items()}
           for t, T in (R.get("teams") or {}).items()}
    os.makedirs(os.path.join(OUT, "players"), exist_ok=True)
    fp = os.path.join(OUT, "players", f"{date}.json")
    bx, games = boxes(logs, want), []
    for (d, pair), i in sorted(want.items(), key=lambda x: x[1]):
        b = bx.get((d, pair)) or {"home": None, "t": {}}
        h = b["home"] if b["home"] in pair else None
        a = next(iter(pair - {h}), None) if h else None
        if not h:
            a, h = sorted(pair)
        sc = {t: sum(r[2] or 0 for r in b["t"].get(t, [])) for t in (a, h)}
        games.append([d, a, h, sc[a], sc[h], {t: sorted(ps, key=lambda r: -(r[1] or 0)) for t, ps in b["t"].items()}])
    os.makedirs(os.path.join(OUT, "boxes"), exist_ok=True)
    json.dump(dict(date=date, cols=["name", *BOX], games=games), open(os.path.join(OUT, "boxes", f"{date}.json"), "w"),
              ensure_ascii=False, separators=(",", ":"))
    json.dump(dict(date=date, cols=["date", "opp", "home", *COLS, "k"], dvp=dvp, players=out), open(fp, "w"),
              ensure_ascii=False, separators=(",", ":"))
    return fp


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--all" in args:
        args = sorted({os.path.basename(p)[:10] for p in glob.glob(os.path.join(OUT, "20??-??-??-*.json"))})
    for d in args:
        print("OK", build(d))
