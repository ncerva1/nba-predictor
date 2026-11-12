"""
Parte de lesiones de toda la liga para la pestaña Liga (pedido de Nicolas 12-nov, idea 5 de bucketsbase).

    python injuries.py <archivo.txt> [fuente]

<archivo.txt>: una línea por jugador "EQUIPO|Jugador|Pos|Regreso estimado|Estado|Nota" (p. ej. lo que devuelve
WebFetch de https://www.espn.com/nba/injuries pidiendo ese formato). Escribe docs/data/injuries.json con la hora de
la actualización; la web lo muestra tal cual y marca a quien vuelve hoy o mañana. La rutina de la mañana lo
refresca (las bajas del partido siguen saliendo de slates/ y bajas.py; esto es solo informativo).
"""
import json, os, sys
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
MON = {m: i + 1 for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"))}


def ret_iso(txt, today):
    try:
        m, d = txt.split()[:2]
        x = date(today.year, MON[m[:3]], int(d))
        if (today - x).days > 14:  # "Oct 1" en noviembre = la próxima temporada
            x = x.replace(year=today.year + 1)
        return x.isoformat()
    except (KeyError, ValueError):
        return None


def main(path, source="ESPN (espn.com/nba/injuries)"):
    today = datetime.now(timezone.utc).date()
    rows = []
    for ln in open(path, encoding="utf-8"):
        p = [x.strip() for x in ln.strip().split("|")]
        if len(p) < 5 or len(p[0]) != 3:
            continue
        team, player, pos, ret, status, *note = p
        rows.append(dict(team=team, player=player, pos=pos, ret=ret, ret_iso=ret_iso(ret, today), status=status,
                         note=note[0] if note else ""))
    out = dict(updated=datetime.now(timezone.utc).isoformat(timespec="minutes"), source=source, list=rows)
    fp = os.path.join(HERE, "docs", "data", "injuries.json")
    json.dump(out, open(fp, "w"), ensure_ascii=False, indent=1)
    print(f"OK {len(rows)} jugadores -> {fp}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
