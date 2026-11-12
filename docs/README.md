# Web del NBA Predictor

Sitio estático (sin servidor) que muestra, por partido, la proyección del modelo y las apuestas
ordenadas por valor esperado (probabilidad del modelo vs cuota).

## Actualizar
```
python build_site.py              # corre el modelo para todos los games/*.json y escribe docs/data/
python build_site.py 2026-10-27   # solo los partidos de una fecha
```
Luego sube los cambios de `docs/` (git add docs && git commit && git push).

## Las tres actualizaciones diarias
Cada jornada se publica tres veces (tareas programadas en la nube, hora de Colombia). Cada una pasa `--stage` a
`build_site.py` para que la web muestre en "Actualizaciones de la jornada" qué se hizo y cuándo:

| Etapa | Cuándo | Qué hace | Comando |
|---|---|---|---|
| `results` | madrugada (~3:10) | box scores reales de AYER en `results/AYER.json`, `evaluate.py`; picks, SGP y parlays entre partidos quedan liquidados | `python build_site.py AYER --stage results --for AYER` |
| `morning` | mañana (~11:20) | partidos de HOY con BD, bajas, H2H y cuotas; programa la revisión de bajas | `python build_site.py HOY --sims 40000 --stage morning --for HOY` |
| `pregame` | 30-60 min antes del primer partido | bajas de última hora (ESPN injuries); re-simula solo si algo cambió | `python build_site.py HOY --sims 40000 --stage pregame --for HOY` |
| `pregame` (horaria) | cada hora después, mientras queden partidos por empezar | bajas y cuotas de los partidos aún sin empezar; re-simula y publica solo si algo cambió; se detiene sola | `python build_site.py HOY_<AWAY>@<HOME> ... --sims 40000 --stage pregame --for HOY` |

**Revisiones horarias (10-nov):** la tarea de la mañana programa la primera revisión 45 min antes del primer partido con el prompt de `tareas/revision_bajas_cuotas.txt` (cambia FECHA y DETALLE). Cada revisión mira bajas y cuotas solo de los partidos que aún no empiezan y, al terminar, se reprograma una vez (dentro de 60 min, o 30 min antes del siguiente partido si es antes) mientras quede algún partido que empiece más de 40 min después; si no queda ninguno, no programa nada y la cadena se acaba. Para cambiar lo que hacen las revisiones basta editar ese archivo.

**Bajas de última hora (11-nov):** `docs/data/bajas.json` es una lista rápida de jugadores confirmados fuera después de
la última simulación. La web la lee al abrir y, en los partidos sin jugar, quita al momento a esos jugadores de los
player props, picks, picks sin cuota, escalones X+, parlays (SGP y entre partidos) y del soñador (su pata se cambia
sola por el mejor reemplazo que cumpla las reglas, o queda anulada), muestra "Bajas de última hora" en la portada y
"Bajas del partido" en cada partido (nuevas, ya simuladas y dudas, con lo que dice "Si no juega…"), y marca con ⚠ las
apuestas de equipo y de compañeros que aún cuentan con él. Comandos: `python bajas.py add FECHA "Jugador" [--duda]
[--note ...] [--source ...]`, `python bajas.py rm FECHA "Jugador"`, `python bajas.py list`. Se publica con un commit
solo de ese archivo (sin build_site.py). Al re-simular, `build_site.py` llama a `bajas.sync()`: la baja que ya no
está en la simulación queda `"sim": true` (la web solo la muestra en el resumen); las jornadas de hace más de 3 días
se borran solas.

Para solo marcar una etapa sin re-simular nada: `python build_site.py ninguno --stage pregame --for HOY` (un filtro
que no coincide con ningún partido reconstruye solo `index.json`). `index.json` guarda `updates` = `{jornada: {etapa:
hora UTC}}` (últimas 14 jornadas) y `last_stage`.

**Commit de cada etapa: siempre `git add -A docs/`** (no solo `docs/data/*.json`). Desde "Mi combinada" (PR #40),
`build_site.py` escribe `docs/data/sims/<id>.bin` para cada partido sin jugar y lo borra al cargar su resultado: la
mañana y la revisión de bajas tienen que subir los `.bin` nuevos, y la de resultados tiene que subir los borrados.
Si una jornada ya publicada quedó sin esos archivos, `python build_site.py <fecha> --samples-only` los regenera sin
cambiar los picks.

**Parlay soñador (9-nov):** una combinada larga por jornada con cuota total entre 50 y 200 (`index.json` →
`dream` = `{jornada: parlay}`), armada por `build_site.py` con las patas más probables (player props con cuota a los
dos lados, prob. ≥ 55%, EV ≥ 0, sin jugadores en duda ni rookies): como mucho 2 por partido y solo si en la muestra
`sims/<id>.bin` no se estorban; como mucho la mitad de las patas al Under; se elige la de mayor prob. conjunta. Sale sola en la etapa `morning` (y se rehace en
`pregame` si se re-simulan todos los partidos aún sin jugar); `results` la liquida con 1 unidad por jornada (si una
pata se anula, cuenta la cuota de las demás). Para rehacerla sin re-simular nada:
`python build_site.py ninguno --dream HOY`.

**Líneas escalonadas "X+" (9-nov):** cada partido trae `ladders` = `{jugador: {team, stat: [[k, prob, cuota mínima,
(cuota casa, EV)], ...]}}` (k+ = Over k−0.5; prob. corrida en logit igual que la mezcla con la casa movió la línea
principal) y la web las muestra en "Líneas escalonadas (X+)". Sale sola en `morning`/`pregame`; para una jornada ya
publicada sin cambiar picks: `python build_site.py <fecha> --ladder-only`. Las cuotas reales de esas líneas solo
llegan de Rushbet/BetPlay (`odds/fetch_co.py` en el PC): `merge_props` escribe `alt_lines` en el slate
(`{jugador: {stat: [[línea, over, under|null], ...]}}`), `build_slate.py` las pasa al partido y `build_site.py` las
convierte en `alt_bets` (las de un solo lado se quitan el margen con el de la línea principal). Nunca son pick con
valor: solo entran en los SGP y en el soñador.

**Historial de jugadores (% Últ. 10 / H2H):** `gamelogs/nba/leaguegamelog_P_<temporada>.csv` son los game logs
oficiales de nba.com (todas las filas de jugador de temporada regular, con FGM/FGA, 3PM/3PA, FTM/FTA). Desde la nube
stats.nba.com no responde: se bajan desde el PC de Nicolas con Claude in Chrome (fetch a `stats.nba.com/stats/leaguegamelog`
desde una página de nba.com/stats) y se suben a GitHub. Los días posteriores al CSV salen de `results/`, así que no hace
falta bajarlos cada día. Para refrescar los % de una jornada ya publicada sin re-simular ni cambiar picks:
`python build_site.py <fecha> --hr-only`.

**Insumos de la jornada desde los game logs (2-nov):** si el CSV de la temporada actual llega hasta AYER,
`python gamelogs/build_raw.py HOY --h2h AWAY@HOME ...` escribe `raw/HOY/cur.csv`, `raw/HOY/prior.csv`,
`db/raw/HOY/team_pergame.csv` y `opp_pergame.csv` (formato B-Ref) y añade a `h2h/<temporada anterior>.json` los
cruces que falten, sin WebFetch a basketball-reference. Si el CSV va atrasado, avisa: entonces se sigue con B-Ref.

## Publicar gratis en GitHub Pages
1. Crear un repositorio en GitHub (por ejemplo `nba-predictor`) y subir esta carpeta `nba_predictor/` como raíz.
2. En el repo: Settings → Pages → Source: "Deploy from a branch" → Branch `main`, carpeta `/docs`.
3. La web queda en `https://<usuario>.github.io/nba-predictor/` y cada partido en `#<id>` (p. ej. `#2026-10-26-IND-at-MIL`).

## Ver en local
```
cd docs && python -m http.server 8000   # abrir http://localhost:8000
```

## Cuotas de varias casas
Guarda las cuotas del día en `odds/<fecha>.json` (formato: `{"games": {"AWAY@HOME": {"books": [{"book": "FanDuel", "spread_away": [4.5, -110], "spread_home": [-4.5, -110], "total": [233.5, -108, -112], "ml_away": 154, "ml_home": -180}]}}}`).
Desde el 12-nov (modelo 1.5.1) las cuotas de props se pasan al margen de una casa colombiana tipo Betano (`localize_odds`, `CO_OVERROUND` = 1.08): no hace falta hacer nada a mano. Si algún día un partido trae las props de una casa colombiana real, poner `"prop_odds_region": "CO"` en su games/*.json para que no se ajusten.
`build_site.py` usa la línea más repetida entre las casas, promedia sus cuotas y muestra la mejor casa para cada apuesta. Si un partido no aparece en ese archivo, usa el `market` del JSON del partido (y -110 si falta la cuota).

### Casas de Colombia (Rushbet, BetPlay)
`python odds/fetch_co.py <fecha>` descarga sus cuotas de la API de Kambi (solo responde desde una conexión normal,
no desde la nube: correrlo en el PC), guarda `odds/co/<fecha>.json` y las mezcla en `odds/<fecha>.json` con
`"region": "CO"`. La web las marca con la etiqueta CO y muestra la mejor opción en Colombia de cada apuesta.
Después, `python build_site.py <fecha>`.
