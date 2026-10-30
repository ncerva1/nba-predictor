"""
NBA Cup: ¿los partidos de Copa tienen más puntos que los de liga regular? (pedido de Nicolas, 30-oct-2026)

Lee cup/raw/*.csv y escribe cup/history.json, que usa build_site.py para la pestaña NBA Cup.

Fuentes (WebFetch): marcadores y la marca "NBA Cup" de basketball-reference (leagues/NBA_<año>_games-<mes>.html);
línea de cierre del total de covers.com/sports/nba/matchups?selectedDate=<fecha>.
- cup_<temporada>.csv: los 60 partidos de fase de grupos (2023-24 sin línea en 3 de las 7 noches).
- reg_<temporada>.csv: partidos de liga regular desde el inicio hasta el final de la fase de grupos (B-Ref se corta a
  mitad de mes: 2024-25 = 22-oct a 21-nov + 1/5-dic; 2025-26 = 21-oct a 22-nov).
- reg_lines.csv: muestra de 12 noches sin Copa con línea de cierre (6 por temporada), para comparar overs.
- cup_knockouts.csv: cuartos, semis y final 2025-26.

Uso: python cup/cup_stats.py
"""
import csv, json, os, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
SEASONS = ("2024-25", "2025-26")  # las que se comparan; 2023-24 solo para los cruces previos


def rows(name):
    p = os.path.join(RAW, name)
    return list(csv.DictReader(open(p))) if os.path.exists(p) else []


def tot(r):
    return int(r["away_pts"]) + int(r["home_pts"])


def line(r):
    return float(r["close_total"]) if r.get("close_total") else None


def ou(rs):
    d = [tot(r) - line(r) for r in rs if line(r) is not None]
    return dict(n=len(d), over=sum(x > 0 for x in d), under=sum(x < 0 for x in d),
                vs_line=round(st.mean(d), 1) if d else None)


def season_summary(cup, reg):
    op = min(r["date"] for r in cup)
    on = [r for r in cup if r["date"] == op]
    before = [r for r in reg if r["date"] < op]
    reg_ot = [tot(r) for r in reg if not r["ot"]]
    cup_ot = [tot(r) for r in cup if not r["ot"]]
    return dict(
        opening=dict(date=op, games=len(on), avg_total=round(st.mean(map(tot, on)), 1),
                     reg_before=dict(games=len(before), avg_total=round(st.mean(map(tot, before)), 1)), **ou(on)),
        group=dict(games=len(cup), avg_total=round(st.mean(map(tot, cup)), 1), avg_regulation=round(st.mean(cup_ot), 1),
                   ot=sum(bool(r["ot"]) for r in cup), **ou(cup)),
        regular=dict(games=len(reg), avg_total=round(st.mean(map(tot, reg)), 1), avg_regulation=round(st.mean(reg_ot), 1),
                     ot=sum(bool(r["ot"]) for r in reg)),
    )


def team_split(cup, reg, t):
    """Puntos a favor / en contra del equipo en Copa frente a sus partidos de liga regular del mismo tramo."""
    def side(rs):
        pf = [int(r["away_pts"]) if r["away"] == t else int(r["home_pts"]) for r in rs if t in (r["away"], r["home"])]
        pa = [int(r["home_pts"]) if r["away"] == t else int(r["away_pts"]) for r in rs if t in (r["away"], r["home"])]
        return pf, pa
    cpf, cpa = side(cup)
    rpf, rpa = side(reg)
    if not cpf:
        return None
    mine = [r for r in cup if t in (r["away"], r["home"])]
    return dict(g=len(cpf), w=sum(f > a for f, a in zip(cpf, cpa)), l=sum(f < a for f, a in zip(cpf, cpa)),
                pf=round(st.mean(cpf), 1), pa=round(st.mean(cpa), 1),
                reg_g=len(rpf), reg_pf=round(st.mean(rpf), 1) if rpf else None, reg_pa=round(st.mean(rpa), 1) if rpa else None,
                **{k: v for k, v in ou(mine).items()})


def game_row(r, season, rnd="Fase de grupos"):
    t, l = tot(r), line(r)
    return dict(season=season, date=r["date"], away=r["away"], home=r["home"],
                score={r["away"]: int(r["away_pts"]), r["home"]: int(r["home_pts"])}, ot=r["ot"] or "",
                total=t, line=l, ou=None if l is None else ("over" if t > l else "under" if t < l else "push"),
                round=r.get("round") or rnd)


def main():
    out = dict(source="basketball-reference (marcadores, marca NBA Cup) + covers.com (línea de cierre del total)",
               seasons={}, teams={}, games=[])
    all_cup, all_reg = [], []
    for s in ("2023-24",) + SEASONS:
        cup = rows(f"cup_{s}.csv")
        out["games"] += [game_row(r, s) for r in cup]
        if s not in SEASONS:
            continue
        reg = rows(f"reg_{s}.csv")
        out["seasons"][s] = season_summary(cup, reg)
        for t in sorted({r["away"] for r in cup} | {r["home"] for r in cup}):
            out["teams"].setdefault(t, {})[s] = team_split(cup, reg, t)
        all_cup += cup
        all_reg += reg
    out["games"] += [game_row(r, "2025-26", r["round"]) for r in rows("cup_knockouts.csv")]
    base = rows("reg_lines.csv")
    cup_l = [r for r in all_cup if line(r) is not None]
    out["pooled"] = dict(
        cup=dict(games=len(all_cup), avg_total=round(st.mean(map(tot, all_cup)), 1), **ou(cup_l)),
        regular=dict(games=len(all_reg), avg_total=round(st.mean(map(tot, all_reg)), 1)),
        regular_lines=ou(base),
        opening=dict(games=sum(out["seasons"][s]["opening"]["games"] for s in SEASONS),
                     over=sum(out["seasons"][s]["opening"]["over"] for s in SEASONS),
                     under=sum(out["seasons"][s]["opening"]["under"] for s in SEASONS)),
    )
    # Conclusión para el modelo: diferencia Copa - liga en puntos por partido (media de las dos temporadas).
    diff = st.mean(out["seasons"][s]["group"]["avg_total"] - out["seasons"][s]["regular"]["avg_total"] for s in SEASONS)
    out["pooled"]["cup_minus_regular"] = round(diff, 1)
    out["model_total_adj"] = 0.0  # la diferencia (~-1 pt) está muy por debajo del ruido (~±2.5 pts con 120 partidos)
    json.dump(out, open(os.path.join(HERE, "history.json"), "w"), ensure_ascii=False, indent=1)
    for s in SEASONS:
        x = out["seasons"][s]
        print(s, "1ª jornada", x["opening"], "\n   grupos", x["group"], "\n   liga", x["regular"])
    print("pooled", out["pooled"])


if __name__ == "__main__":
    main()
