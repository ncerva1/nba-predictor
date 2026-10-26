# NBA Predictor

Simulador Monte Carlo de partidos NBA y player props, con una web estática que muestra
por partido las apuestas con mayor valor (probabilidad del modelo vs cuota).

- `model.py`: modelo y simulación (`python model.py games/<partido>.json`)
- `db/`: BD de rankings de los 30 equipos (`python db/build_db.py`)
- `games/`: un JSON de entrada por partido
- `build_site.py`: corre el modelo para todos los partidos y escribe `docs/data/`
- `docs/`: la web (GitHub Pages: rama `main`, carpeta `/docs`)

Ver `docs/README.md` para actualizar y publicar.
