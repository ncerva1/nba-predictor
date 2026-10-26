# BD de rankings de equipos — NBA-Predictor

Base de datos con los rankings 1-30 de los 30 equipos en ataque, defensa, estilo de juego,
tipos de jugada, zonas de tiro y defensa por posición. Cubre el punto 3 (a y b) de las instrucciones del proyecto.

## Estructura

```
db/
  raw/<YYYY-MM-DD>/          snapshot de cada actualización (fuente de verdad, texto)
    nbacom_team_<temporada>.csv      ~80 métricas por equipo (NBA.com: Base, Advanced, Misc, Opponent, Scoring)
    nbacom_playtypes_<temporada>.csv 10 tipos de jugada x ataque/defensa: frecuencia y PPP (Synergy)
    nbacom_shots_<temporada>.csv     5 zonas de tiro x propio/rival: FGA y FG%
    dvp_statpick.csv                 defensa vs posición PG/SG/SF/PF/C (PTS, REB, AST, 3PM, PRA, STL, BLK, TOV)
    team_pergame.csv, opp_pergame.csv  Basketball-Reference (respaldo)
    ptw_*.csv, shw_*.csv, checksums.json, page_dump.txt   formato de transporte + sumas de control
    meta.json                        temporada, corte, fuentes
  build_db.py        reconstruye nba_rankings.sqlite y rankings_latest.json desde raw/
  ingest_dump.py     convierte el volcado de NBA.com en CSV, verifica sumas y reconstruye
  fetch_nbacom.js    script que se ejecuta en Chrome (www.nba.com/stats) para descargar todo
```

`nba_rankings.sqlite` y `rankings_latest.json` no se guardan en el proyecto: se regeneran con `python build_db.py`.

## Tablas

| Tabla | Contenido |
|---|---|
| `team_metrics` | valor y ranking de cada métrica por snapshot y temporada; `side` = off / def / net / style |
| `blended_metrics` | mezcla temporada actual + anterior, `w = gp / (gp + 10)` (mismo shrinkage que model.py) |
| `play_types` / `blended_play_types` | frecuencia (`poss_pct`), PPP, `freq_rank`, `ppp_rank` por tipo de jugada y lado |
| `shot_zones` | FGA y FG% por zona (RA, PaintNonRA, MidRange, Corner3, ATB3), propio y concedido |
| `dvp` | defensa vs posición (statpick, por 48 min, ajustado por calendario) |
| `snapshots`, `teams` | metadatos |

**Ranking:** rank 1 = mejor para el equipo (más puntos anotados, menos concedidos, menor PPP concedido...).
En métricas de estilo (`PCT_*`, `PACE`, frecuencias) rank 1 = el que más lo hace / más lo enfrenta.
En `dvp`, rank 1 = concede menos y 30 = concede más.

## Uso

```bash
python build_db.py --team IND --vs MIL     # informe de enfrentamiento: ataque vs defensa, play types, DvP
```

## Actualización (cada 1-2 días)

1. En Chrome, abrir `https://www.nba.com/stats/teams/traditional` y ejecutar `fetch_nbacom.js` (54 peticiones
   secuenciales, ~2 min). **Nunca en paralelo**: stats.nba.com bloquea la IP unos 15 min ante ráfagas.
2. Leer la página (sección `### ...`) y guardarla en `raw/<fecha>/page_dump.txt`.
3. Opcional: actualizar `dvp_statpick.csv` desde statpick.ai (si no, se hereda el del snapshot anterior).
4. `python ingest_dump.py <fecha>` — verifica sumas de control y reconstruye la BD.
5. Subir los CSV del nuevo snapshot al proyecto.

## Limitaciones conocidas

- Con 2-4 partidos jugados en 2026-27 los rankings de la temporada actual son ruido; usar las vistas `blended`.
- Los play types de Synergy con frecuencia muy baja (≈0) tienen PPP poco fiable.
- DvP depende de statpick.ai (asignación de posiciones propia del sitio).
- Faltan todavía: últimos 10 partidos por equipo y jugador, H2H y blowouts (puntos 2, 4 y 6 de las instrucciones).

## Conexión con el modelo

`model.py` lee `db/rankings_latest.json` a través de `db_link.py` cuando el JSON del partido trae
`"db": {"use": true}` (o con `--db`; `--no-db` lo desactiva). La BD aporta:

- **Temporada actual** de cada equipo (gp, ORtg, DRtg, pace), re-escalada a la media de liga del JSON.
  El prior ajustado por roster y los ajustes de contexto (`adj`) siguen siendo manuales.
- **Multiplicadores de enfrentamiento**: rebote, asistencias y triples que concede el rival, y el ajuste de
  estilo por tipos de jugada (pts/100). Encogidos hacia 1 con `k_team` (0.6). Lo que no está en la BD
  (p. ej. una baja de último minuto) va en `"matchup_manual"` y se aplica encima.
- **Defensa vs posición** por jugador (`k_dvp`, 0.3): solo redistribuye la producción entre posiciones.

El bloque `"matchup"` del JSON se ignora cuando la BD está activa; el informe `[BD]` muestra lo que cambió.
