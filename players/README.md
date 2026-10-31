# Etiquetas de jugadores con poca historia

`players/<temporada>.json` marca en la web a los jugadores cuyas proyecciones tienen más varianza porque el modelo
casi no tiene datos previos de ellos:

- `rookies`: novatos de la temporada (lista de basketball-reference, `leagues/NBA_<año>_rookies.html`).
- `missed_prior`: veteranos que no jugaron la temporada anterior (lesión u otro motivo), con el motivo.

Además, `build_site.py` marca solo a cualquier jugador que no aparezca en `raw/<fecha>/prior.csv` como
"sin datos 2025-26": el modelo lo proyecta solo con sus partidos de esta temporada.

Cuando debute un novato que no esté en la lista o vuelva alguien tras un año fuera, añadirlo aquí y correr
`python build_site.py` (no hace falta volver a simular: las etiquetas se aplican también a jornadas ya jugadas).
