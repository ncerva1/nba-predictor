"""Bajas de última hora (11-nov): lista rápida de jugadores confirmados fuera DESPUÉS de la última simulación.

La web (docs/index.html) lee docs/data/bajas.json al abrir y, en los partidos aún sin jugar, quita al momento a esos
jugadores de los player props, picks, picks sin cuota, escalones X+, parlays (SGP y entre partidos) y del soñador
(su pata se cambia por un reemplazo que cumpla las reglas), y resume las bajas en cada partido con lo que dice "Si no
juega…". Sirve para publicar una baja en un par de minutos (solo cambia este archivo) mientras llega la re-simulación.

Cuando el partido se re-simula con el jugador en "out" del slate, build_site.py llama a sync(): la entrada queda
marcada "sim": true (la web ya no quita nada, solo la sigue mostrando en el resumen como baja confirmada).

Uso:
  python bajas.py add 2026-11-11 "Jayson Tatum" [--duda] [--note "tobillo"] [--source "ESPN 18:40"]
  python bajas.py rm 2026-11-11 "Jayson Tatum"
  python bajas.py list [2026-11-11]
  python bajas.py sync [2026-11-11]     # marca "sim" las ya incluidas en la simulación y borra jornadas viejas
Luego: git add docs/data/bajas.json, commit, PR y merge (no hace falta correr build_site.py).
"""
import glob
import json
import os
import sys
import unicodedata
from datetime import date as Date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "docs", "data")
PATH = os.path.join(OUT, "bajas.json")
KEEP_DAYS = 3  # sync borra las entradas de jornadas de hace más de 3 días


def norm(s):
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return " ".join("".join(c if c.isalpha() or c == " " else " " for c in s).split())


def load():
    if os.path.exists(PATH):
        return json.load(open(PATH, encoding="utf-8"))
    return {"updated": None, "list": []}


def save(d):
    d["updated"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
    d["list"].sort(key=lambda x: (x["date"], x["gid"], x["team"], x["player"]))
    json.dump(d, open(PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def games_of(date):
    for p in sorted(glob.glob(os.path.join(OUT, f"{date}-*.json"))):
        r = json.load(open(p, encoding="utf-8"))
        if isinstance(r, dict) and "summary" in r and "game" in r:
            yield r


def roster(r):
    """{nombre: (equipo, en_simulación)} con los jugadores simulados y los OUT del slate."""
    g = r["game"]
    out = {p["name"]: (p["team"], True) for p in r["summary"]["players"]}
    for t, w in (r.get("without") or {}).items():
        for x in w.get("out_today", []):
            out.setdefault(x["name"], (t, False))
    games = os.path.join(HERE, "games", f"{g['date']}_{g['away']}@{g['home']}.json")
    if os.path.exists(games):  # resto del roster (suplentes que no entraron en la simulación)
        for p in json.load(open(games, encoding="utf-8")).get("players", []):
            if p.get("name") and p.get("team"):
                out.setdefault(p["name"], (p["team"], False))
    return out


def find(date, name):
    """(partido, nombre oficial, equipo, en_simulación). Acepta el nombre completo o un apellido único."""
    q, hits = norm(name), []
    for r in games_of(date):
        for n, (t, insim) in roster(r).items():
            k = norm(n)
            if k == q:
                return r, n, t, insim
            if q and (q in k.split() or k.endswith(" " + q) or q in k):
                hits.append((r, n, t, insim))
    if len(hits) == 1:
        return hits[0]
    if not hits:
        sys.exit(f"No encontré a {name!r} en los partidos publicados del {date}.")
    sys.exit(f"{name!r} es ambiguo: " + ", ".join(f"{n} ({t})" for _, n, t, _ in hits))


def impact(r, player):
    """Qué quita la web de ese partido: apuestas con cuota, picks con valor, picks sin cuota y SGP."""
    k = norm(player)
    mine = lambda b: norm(b.get("player")) == k
    bets = [b for b in r.get("bets", []) if mine(b)]
    return dict(bets=len(bets), value=sum(bool(b.get("value")) for b in bets),
                model_picks=sum(mine(b) for b in r.get("model_picks", [])),
                sgp=sum(any(mine(l) for l in pl["legs"]) for pl in r.get("parlays", [])))


def cross_impact(date, player, gid):
    idx = os.path.join(OUT, "index.json")
    if not os.path.exists(idx):
        return 0, False
    I = json.load(open(idx, encoding="utf-8"))
    k = norm(player)
    hit = lambda l: l.get("gid") == gid and norm(l.get("player")) == k
    cross = sum(any(hit(l) for l in pl["legs"]) for pl in I.get("parlays", {}).get(date, []))
    dream = any(hit(l) for l in (I.get("dream", {}).get(date) or {}).get("legs", []))
    return cross, dream


def add(date, name, status="out", note="", source=""):
    r, n, t, insim = find(date, name)
    if "result" in r:
        sys.exit(f"{r['id']} ya tiene resultado: no hace falta marcar la baja.")
    d = load()
    d["list"] = [x for x in d["list"] if not (x["gid"] == r["id"] and norm(x["player"]) == norm(n))]
    e = dict(date=date, gid=r["id"], player=n, team=t, status=status, note=note, source=source,
             at=datetime.now(timezone.utc).isoformat(timespec="minutes"), sim=not insim and status == "out")
    d["list"].append(e)
    save(d)
    g = r["game"]
    print(f"{'OUT' if status == 'out' else 'EN DUDA'}: {n} ({t}) en {g['away']} @ {g['home']}")
    if e["sim"]:
        print("  Ya estaba fuera de la simulación: la web solo lo muestra en el resumen de bajas.")
    elif status == "out":
        im, (cross, dream) = impact(r, n), cross_impact(date, n, r["id"])
        print(f"  La web quita {im['bets']} apuestas con cuota ({im['value']} picks con valor), "
              f"{im['model_picks']} picks sin cuota, {im['sgp']} SGP, {cross} parlays entre partidos"
              + (" y cambia su pata del soñador" if dream else ""))
        print("  Marcador, hándicap, total y props de sus compañeros siguen con él hasta re-simular el partido.")
    return e


def rm(date, name):
    d = load()
    k = norm(name)
    keep = [x for x in d["list"] if not (x["date"] == date and (norm(x["player"]) == k or k in norm(x["player"]).split()))]
    if len(keep) == len(d["list"]):
        sys.exit(f"No hay baja de {name!r} el {date}.")
    print("Quitadas: " + ", ".join(x["player"] for x in d["list"] if x not in keep))
    d["list"] = keep
    save(d)


def sync(dates=None, quiet=False):
    """Marca "sim" las bajas cuyo jugador ya no está en la simulación publicada y borra jornadas viejas."""
    if not os.path.exists(PATH):
        return
    d = load()
    old = (Date.today() - timedelta(days=KEEP_DAYS)).isoformat()
    lst, changed = [], False
    for x in d["list"]:
        if x["date"] < old:
            changed = True
            continue
        if x["status"] == "out" and not x.get("sim") and (dates is None or x["date"] in dates):
            p = os.path.join(OUT, f"{x['gid']}.json")
            if os.path.exists(p):
                r = json.load(open(p, encoding="utf-8"))
                if not any(norm(q["name"]) == norm(x["player"]) for q in r["summary"]["players"]):
                    x["sim"] = True
                    changed = True
                    if not quiet:
                        print(f"Baja de {x['player']} ya incluida en la simulación de {x['gid']}")
        lst.append(x)
    if changed:
        d["list"] = lst
        save(d)


def show(date=None):
    for x in load()["list"]:
        if date is None or x["date"] == date:
            st = "OUT" if x["status"] == "out" else "duda"
            print(f"{x['date']} {x['gid'][11:]:<12} {x['team']} {x['player']:<28} {st:<4} "
                  f"{'(en la simulación)' if x.get('sim') else '(pendiente de re-simular)' if st == 'OUT' else ''} {x.get('note', '')}")


def main():
    a = sys.argv[1:]
    opt = lambda k: a[a.index(k) + 1] if k in a and a.index(k) + 1 < len(a) else ""
    skip = {i + 1 for i, x in enumerate(a) if x in ("--note", "--source")}
    pos = [x for i, x in enumerate(a) if not x.startswith("--") and i not in skip]
    if not pos:
        sys.exit(__doc__)
    cmd = pos[0]
    if cmd == "add" and len(pos) >= 3:
        for n in pos[2:]:
            add(pos[1], n, "duda" if "--duda" in a else "out", opt("--note"), opt("--source"))
    elif cmd == "rm" and len(pos) >= 3:
        for n in pos[2:]:
            rm(pos[1], n)
    elif cmd == "list":
        show(pos[1] if len(pos) > 1 else None)
    elif cmd == "sync":
        sync({pos[1]} if len(pos) > 1 else None)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
