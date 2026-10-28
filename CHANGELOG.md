# Cambios del modelo

## Web — 2026-10-28: parlays recomendados
- `build_site.py` arma parlays de 2, 3 y 4 patas por partido (SGP) y entre partidos de la jornada. Patas: cuota
  publicada, prob. >= 55%, EV >= 0 (entre partidos, solo picks con valor) y sin jugadores en duda (p_play < 0.9).
  Se elige la combinación más probable con cuota combinada >= 2.00 y EV > 0. En el SGP la prob. conjunta se
  cuenta en las simulaciones (recoge la correlación); entre partidos se multiplica. Se guardan en
  `docs/data/<id>.json` (`parlays`) y en `index.json` (`parlays` por fecha) y se liquidan con los resultados.

## 1.2 — 2026-10-27 (calibración con los 9 partidos del 26-oct)
Evaluación completa en `results/2026-10-26_eval.json`. Lo que se cambió y por qué:

- **Minutos más inciertos**: DE de minutos 3.5/4.0 -> 5.5/6.0 (titular/suplente). El error real de minutos fue
  ~7 de DE y los rangos 10-90% de puntos solo contenían al 75% de los jugadores (deberían ser 80%).
- **Salidas tempranas**: 4% de probabilidad por jugador de jugar solo 15-60% de sus minutos (lesión, faltas),
  como Markkanen (6 min) o Jaren Jackson Jr. (10 min).
- **Bajas de última hora (`p_play`)**: cada jugador puede tener una probabilidad de jugar. Si no juega en una
  simulación, sus minutos se reparten entre los demás y sus tiros pasan a los compañeros. Las props se
  calculan condicionadas a que juegue (si no juega, la apuesta se anula). En `build_slate.py`, `gtd` ahora es
  esa probabilidad (antes recortaba minutos).
- **Puntos por cuota del marcador**: los puntos de cada jugador son su parte del marcador simulado del equipo,
  así siempre suman el marcador y una baja reparte el volumen.
- **Menos peso a los primeros partidos**: tasas por minuto con w = G / (G + 6) (antes G / (G + 3)). Evita casos
  como Ty Jerome proyectado a 31 puntos por 2 partidos de 36.

Backtest sobre el 26-oct (log score, menor es mejor): puntos 3.197 -> 3.114, PRA 3.503 -> 3.417, rebotes
2.378 -> 2.347; cobertura del rango 10-90% de puntos 75% -> 78%, de PRA 73% -> 80%.

Sin cambios a nivel equipo (ratings, shrink_games, DE de marcador): con 9 partidos ninguna alternativa mejoró
de forma clara y el error de margen del modelo (10.1) fue igual al del mercado (10.4).
