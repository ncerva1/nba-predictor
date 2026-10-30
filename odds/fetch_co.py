"""Cuotas de casas colombianas (Rushbet y BetPlay, ambas sobre la plataforma Kambi).

La API de Kambi solo responde desde una conexión normal (no desde la nube), así que este script
se corre en el PC de Nicolas. Solo usa la librería estándar de Python.

    python odds/fetch_co.py 2026-10-29            # descarga, guarda odds/co/<fecha>.json y lo mezcla en odds/<fecha>.json
    python odds/fetch_co.py 2026-10-29 --no-merge # solo descarga
    python odds/fetch_co.py 2026-10-29 --parse    # re-procesa odds/co_raw/<fecha>/ sin descargar

Cada casa entra en odds/<fecha>.json con "region": "CO" y las cuotas en formato americano (con decimales,
para que la cuota decimal original se recupere exacta). build_site.py las usa como una casa más y además
muestra la mejor casa colombiana de cada apuesta.
"""
import json, os, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BOOKS = {"Rushbet": ["rsico"], "BetPlay": ["betplay"]}  # código de operador en Kambi (se prueban en orden)
# Los operadores colombianos solo responden en el host us1 (eu-offering-api da 429 "No access")
API = "https://us1.offering-api.kambicdn.com/offering/v2018/{op}/{path}?lang=en_GB&market=CO&client_id=2&channel_id=1&includeParticipants=true"
BOGOTA = timezone(timedelta(hours=-5))

TEAMS = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN", "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE", "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET", "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC", "LA Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR", "Utah Jazz": "UTA", "Washington Wizards": "WAS",
}
# mercado de jugador -> estadística del modelo, por palabras clave en orden (las combinadas antes que sus partes)
PROP_STATS = [(("points", "rebounds", "assists"), "pra"), (("points", "rebounds"), "pr"), (("points", "assists"), "pa"),
              (("rebounds", "assists"), "ra"), (("steals", "blocks"), "stocks"),
              (("3-point", "attempt"), "fg3a"), (("three", "attempt"), "fg3a"), (("3-point",), "fg3m"), (("three",), "fg3m"),
              (("2-point", "attempt"), "fg2a"), (("2-point",), "fg2m"),
              (("free throw", "attempt"), "fta"), (("free throw",), "ftm"),
              (("field goal", "attempt"), "fga"), (("field goal",), "fgm"),
              (("points",), "pts"), (("rebounds",), "reb"), (("assists",), "ast"), (("steals",), "stl"),
              (("blocks",), "blk"), (("turnovers",), "tov"), (("fouls",), "pf")]
PROP_SKIP = ("quarter", "half", "1st", "2nd", "3rd", "4th", "first", "double", "triple")


def prop_stat(crit):
    if any(x in crit for x in PROP_SKIP):
        return None
    return next((k for words, k in PROP_STATS if all(w in crit for w in words)), None)
SKIP = ("quarter", "half", "player", "by ", "race", "margin", "odd/even", "3-way", "winning")


def team(name):
    if name in TEAMS:
        return TEAMS[name]
    for full, ab in TEAMS.items():  # "Hawks", "Atlanta" ...
        if name and (full.endswith(" " + name) or full.startswith(name + " ")):
            return ab
    return None


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def american(dec):
    return round((dec - 1) * 100, 2) if dec >= 2 else round(-100 / (dec - 1), 2)


def download(date, raw_dir):
    os.makedirs(raw_dir, exist_ok=True)
    for book, ops in BOOKS.items():
        for op in ops:
            try:
                lv = get(API.format(op=op, path="listView/basketball/nba/all/all/matches.json"))
            except Exception as e:
                print(f"{book} ({op}): no responde: {e}")
                continue
            evs = [e["event"] for e in lv.get("events", []) if local_date(e["event"]["start"]) == date]
            print(f"{book} ({op}): {len(lv.get('events', []))} partidos NBA, {len(evs)} del {date}")
            full = {"op": op, "events": []}
            for ev in evs:
                try:
                    full["events"].append(get(API.format(op=op, path=f"betoffer/event/{ev['id']}.json")))
                except Exception as e:
                    print(f"  {ev.get('englishName', ev['id'])}: {e}")
                time.sleep(0.3)
            json.dump(full, open(os.path.join(raw_dir, f"{book}.json"), "w"))
            break


def local_date(start):
    return datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(BOGOTA).strftime("%Y-%m-%d")


def side(o, ev):
    """'home' / 'away' de un outcome de moneyline o hándicap."""
    for n in (o.get("participant"), o.get("englishLabel"), o.get("label")):
        if n and n == ev.get("homeName"): return "home"
        if n and n == ev.get("awayName"): return "away"
    return {"OT_ONE": "home", "OT_TWO": "away"}.get(o.get("type"))


def even(outs):  # qué tan cerca de cuotas parejas está una línea (para quedarse con la principal)
    return abs(outs[0]["odds"] - outs[1]["odds"])


UNMAPPED = set()  # mercados de jugador sin estadística conocida (se listan al final para añadirlos)


def player_name(n):
    """Kambi escribe "Tatum, Jayson"; las stats, "Jayson Tatum"."""
    return " ".join(reversed([x.strip() for x in n.split(",", 1)])) if "," in n else n


def to_prop_lines(co):
    """Props de las casas CO en el formato de slates/<fecha>.json: {jugador: {stat: [línea, over, under]}},
    con la línea más pareja entre las dos casas (y si coinciden, la mejor cuota de cada lado)."""
    out = {}
    for g in co.values():
        for b in g["books"]:
            for p in b.get("props", []):
                cur = out.setdefault(player_name(p["player"]), {}).get(p["stat"])
                new = [p["line"], p["over"], p["under"]]
                if cur and cur[0] == new[0]:
                    new = [new[0], max(cur[1], new[1], key=lambda o: o if o > 0 else 10000 / -o),
                           max(cur[2], new[2], key=lambda o: o if o > 0 else 10000 / -o)]
                out[player_name(p["player"])][p["stat"]] = new
    return out


def parse_event(data):
    ev = data["events"][0]
    home, away = team(ev.get("homeName")), team(ev.get("awayName"))
    if not home or not away:
        return None, None
    rec, cand, props = {}, {"spread": [], "total": []}, {}
    for bo in data.get("betOffers", []):
        crit = (bo.get("criterion", {}).get("englishLabel") or bo.get("criterion", {}).get("label") or "").lower()
        typ = (bo.get("betOfferType", {}).get("englishName") or "").lower()
        outs = [o for o in bo.get("outcomes", []) if o.get("status", "OPEN") == "OPEN" and o.get("odds")]
        if "by the player" in crit or ("player" in crit and typ == "over/under"):
            stat = prop_stat(crit)
            if not stat:
                UNMAPPED.add(crit)
            ov = next((o for o in outs if o.get("type") == "OT_OVER"), None)
            un = next((o for o in outs if o.get("type") == "OT_UNDER"), None)
            if stat and ov and un and ov.get("line") is not None:
                key = (ov.get("participant") or bo.get("participant") or "", stat)
                cur = props.get(key)
                if not cur or even([ov, un]) < cur[0]:
                    props[key] = (even([ov, un]), dict(player=key[0], stat=stat, line=ov["line"] / 1000,
                                                       over=american(ov["odds"] / 1000), under=american(un["odds"] / 1000)))
            continue
        if any(s in crit for s in SKIP) or len(outs) != 2:
            continue
        if crit.startswith("moneyline"):  # "First to 25 Points", "Quarter 1"... también son tipo Match
            for o in outs:
                s = side(o, ev)
                if s: rec[f"ml_{s}"] = american(o["odds"] / 1000)
        elif typ == "handicap" and crit.startswith("point spread") and all(o.get("line") is not None for o in outs):
            cand["spread"].append(outs)
        elif typ == "over/under" and crit.startswith("total points"):  # no "Total Match Assists"
            cand["total"].append(outs)
    if cand["spread"]:
        for o in min(cand["spread"], key=even):
            s = side(o, ev)
            if s: rec[f"spread_{s}"] = [o["line"] / 1000, american(o["odds"] / 1000)]
    if cand["total"]:
        outs = min(cand["total"], key=even)
        ov = next((o for o in outs if o.get("type") == "OT_OVER"), outs[0])
        un = next((o for o in outs if o.get("type") == "OT_UNDER"), outs[1])
        rec["total"] = [ov["line"] / 1000, american(ov["odds"] / 1000), american(un["odds"] / 1000)]
    if props:
        rec["props"] = [v[1] for v in props.values()]
    return f"{away}@{home}", rec


def parse(raw_dir):
    out = {}
    for fn in sorted(os.listdir(raw_dir)):
        book = fn[:-5]
        for data in json.load(open(os.path.join(raw_dir, fn)))["events"]:
            key, rec = parse_event(data)
            if key and rec:
                out.setdefault(key, {"books": []})["books"].append(dict(book=book, region="CO", **rec))
    return out


def merge(date, co):
    path = os.path.join(HERE, f"{date}.json")
    od = json.load(open(path)) if os.path.exists(path) else {"date": date, "source": "", "games": {}}
    names = {b["book"] for g in co.values() for b in g["books"]}
    for key, g in co.items():
        books = [b for b in od["games"].get(key, {}).get("books", []) if b["book"] not in names]
        od["games"].setdefault(key, {})["books"] = books + g["books"]
    note = f"Casas de Colombia ({', '.join(sorted(names))}) vía Kambi, {datetime.now(BOGOTA):%Y-%m-%d %H:%M} Bogotá"
    od["source"] = " | ".join([s for s in od.get("source", "").split(" | ") if not s.startswith("Casas de Colombia")] + [note]).strip(" |")
    json.dump(od, open(path, "w"), indent=1, ensure_ascii=False)
    print(f"mezclado en {path}")


def merge_props(date, co):
    """Añade a slates/<fecha>.json las props CO de estadísticas que no tenían línea (combinadas, robos, tapones...);
    las que ya tenían línea de varias casas de EE. UU. se quedan como estaban."""
    path = os.path.join(HERE, "..", "slates", f"{date}.json")
    sl = json.load(open(path))
    pl, n = sl.setdefault("prop_lines", {}), 0
    for name, stats in to_prop_lines(co).items():
        for st, v in stats.items():
            if st not in pl.get(name, {}):
                pl.setdefault(name, {})[st] = v
                n += 1
    src = sl.setdefault("sources", {})
    src["props_co"] = f"Rushbet / BetPlay (Kambi): {n} líneas añadidas"
    json.dump(sl, open(path, "w"), indent=1, ensure_ascii=False)
    print(f"{n} props CO añadidas a {path}")


if __name__ == "__main__":
    date = sys.argv[1]
    raw_dir = os.path.join(HERE, "co_raw", date)
    if "--parse" not in sys.argv:
        download(date, raw_dir)
    co = parse(raw_dir)
    os.makedirs(os.path.join(HERE, "co"), exist_ok=True)
    json.dump({"date": date, "games": co}, open(os.path.join(HERE, "co", f"{date}.json"), "w"), indent=1, ensure_ascii=False)
    for key, g in co.items():
        for b in g["books"]:
            print(key, b["book"], {k: v for k, v in b.items() if k not in ("book", "region", "props")}, f"{len(b.get('props', []))} props")
    pl = to_prop_lines(co)
    from collections import Counter
    print("props por estadística:", dict(Counter(st for p in pl.values() for st in p)))
    if UNMAPPED:
        print("mercados de jugador sin mapear:", sorted(UNMAPPED))
    if "--no-merge" not in sys.argv and co:
        merge(date, co)
