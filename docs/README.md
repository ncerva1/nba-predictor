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

Para solo marcar una etapa sin re-simular nada: `python build_site.py ninguno --stage pregame --for HOY` (un filtro
que no coincide con ningún partido reconstruye solo `index.json`). `index.json` guarda `updates` = `{jornada: {etapa:
hora UTC}}` (últimas 14 jornadas) y `last_stage`.

**Commit de cada etapa: siempre `git add -A docs/`** (no solo `docs/data/*.json`). Desde "Mi combinada" (PR #40),
`build_site.py` escribe `docs/data/sims/<id>.bin` para cada partido sin jugar y lo borra al cargar su resultado: la
mañana y la revisión de bajas tienen que subir los `.bin` nuevos, y la de resultados tiene que subir los borrados.
Si una jornada ya publicada quedó sin esos archivos, `python build_site.py <fecha> --samples-only` los regenera sin
cambiar los picks.

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
`build_site.py` usa la línea más repetida entre las casas, promedia sus cuotas y muestra la mejor casa para cada apuesta. Si un partido no aparece en ese archivo, usa el `market` del JSON del partido (y -110 si falta la cuota).

### Casas de Colombia (Rushbet, BetPlay)
`python odds/fetch_co.py <fecha>` descarga sus cuotas de la API de Kambi (solo responde desde una conexión normal,
no desde la nube: correrlo en el PC), guarda `odds/co/<fecha>.json` y las mezcla en `odds/<fecha>.json` con
`"region": "CO"`. La web las marca con la etiqueta CO y muestra la mejor opción en Colombia de cada apuesta.
Después, `python build_site.py <fecha>`.
