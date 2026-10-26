# Web del NBA Predictor

Sitio estático (sin servidor) que muestra, por partido, la proyección del modelo y las apuestas
ordenadas por valor esperado (probabilidad del modelo vs cuota).

## Actualizar
```
python build_site.py              # corre el modelo para todos los games/*.json y escribe docs/data/
python build_site.py 2026-10-27   # solo los partidos de una fecha
```
Luego sube los cambios de `docs/` (git add docs && git commit && git push).

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
