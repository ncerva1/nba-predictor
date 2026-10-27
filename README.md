# NBA Predictor

Simulador Monte Carlo de partidos NBA y player props, con una web estática que muestra
por partido las apuestas con mayor valor (probabilidad del modelo vs cuota).

- `model.py`: modelo y simulación (`python model.py games/<partido>.json`)
- `db/`: BD de rankings de los 30 equipos (`python db/build_db.py`)
- `games/`: un JSON de entrada por partido
- `build_slate.py`: arma los JSON de una jornada (`slates/<fecha>.json` + `raw/<fecha>/`) y los simula
- `build_site.py`: corre el modelo para todos los partidos y escribe `docs/data/`
- `h2h/<temporada>.json`: enfrentamientos de la temporada anterior entre los equipos de cada jornada (box scores de ESPN
  por jugador y máxima ventaja de cada equipo); `build_site.py` los muestra en la web junto a los player props.
  Añadir los cruces de cada jornada nueva antes de correr `build_site.py`.
- `results/`: resultados reales por jornada (`results/<fecha>.json`, box scores) y su evaluación
- `evaluate.py`: compara lo publicado con lo real (`python evaluate.py <fecha>` -> `results/<fecha>_eval.json`)
- `docs/`: la web (GitHub Pages: rama `main`, carpeta `/docs`)

Ver `docs/README.md` para actualizar y publicar.

## Después de cada jornada
1. Guardar marcadores y box scores en `results/<fecha>.json` (formato en `evaluate.py`).
2. `python evaluate.py <fecha>`: aciertos, calibración, picks ganados/perdidos, errores por jugador.
3. `python build_site.py <fecha>`: los partidos con resultado no se vuelven a simular; la web muestra
   el resultado real, cada pick liquidado y el historial del modelo.

Cambios del modelo: ver `CHANGELOG.md`.
