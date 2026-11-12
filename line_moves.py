"""
Rellena la línea y cuota de apertura ("open") de las jornadas publicadas antes de que build_site.py la guardara
(12-nov): la apertura es la primera versión de docs/data/<id>.json en el historial de git (el análisis de la mañana).
No re-simula ni cambia ningún pick; solo añade "open" a las apuestas que no lo tienen, la proyección media ("mean")
de cada prop y refresca index.json.

    python line_moves.py            # todas las jornadas
    python line_moves.py 2026-11-12 # solo esa
"""
import glob, json, os, subprocess, sys

from build_site import OUT, HERE, add_means, bet_key, index_entry


def first_version(path):
    rel = os.path.relpath(path, HERE)
    revs = subprocess.run(["git", "log", "--format=%H", "--", rel], cwd=HERE, capture_output=True, text=True).stdout.split()
    if not revs:
        return None
    return json.loads(subprocess.run(["git", "show", f"{revs[-1]}:{rel}"], cwd=HERE, capture_output=True, text=True).stdout)


def backfill(path):
    r = add_means(json.load(open(path)))
    if all("open" in b for b in r["bets"]):
        json.dump(r, open(path, "w"), ensure_ascii=False, separators=(",", ":"))
        return r, 0
    first = first_version(path) or r
    prev = {bet_key(b): b for b in first["bets"]}
    n = 0
    for b in r["bets"]:
        if "open" in b:
            continue
        o = prev.get(bet_key(b))
        b["open"] = [o.get("line"), o.get("dec")] if o else [b.get("line"), b.get("dec")]
        n += o is not None and (o.get("line"), o.get("dec")) != (b.get("line"), b.get("dec"))
    json.dump(r, open(path, "w"), ensure_ascii=False, separators=(",", ":"))
    return r, n


if __name__ == "__main__":
    pre = sys.argv[1:]
    idx_path = os.path.join(OUT, "index.json")
    index = json.load(open(idx_path))
    by_id = {g["id"]: i for i, g in enumerate(index["games"])}
    last = max(g["date"] for g in index["games"])
    for p in sorted(glob.glob(os.path.join(OUT, "20??-??-??-*.json"))):
        gid = os.path.basename(p)[:-5]
        if pre and not any(gid.startswith(x) for x in pre):
            continue
        r, n = backfill(p)
        if gid in by_id:
            e = index_entry(r)
            if e["date"] != last:
                e.pop("all_bets", None)
            index["games"][by_id[gid]] = e
        print(f"OK {gid}: {n} líneas o cuotas movidas desde la apertura")
    json.dump(index, open(idx_path, "w"), ensure_ascii=False, indent=1)
