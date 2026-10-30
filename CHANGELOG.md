# Cambios del modelo

## 1.3.3 — 2026-10-30: H2H de esta temporada también, y Copa marcada
Pedido de Nicolas para la noche de Copa: usar como referencia los cruces entre ambos equipos de esta temporada y
la pasada, de liga y de Copa.

- `model.current_h2h_games` saca los cruces de esta temporada de `results/<fecha>.json` (los que carga la rutina
  diaria; el equipo de cada jugador sale de `games/` o `raw/*/cur.csv`). `apply_h2h` los suma a los de
  `h2h/<temporada anterior>.json` con el mismo peso (n / (n + H2H_K) por minutos), así que la rutina no tiene que
  hacer nada nuevo. Esos partidos ya están en el promedio de la temporada: el H2H les da peso extra a propósito.
- La web muestra los dos años juntos (más reciente primero), cada partido marcado Liga o Copa (fase de grupos de
  2025-26 por `cup/history.json`, esta temporada por `cup/<temporada>.json`), y la columna "Vs rival" de los
  props marca con ᶜ los partidos de Copa. `results/` acepta `largest_lead` opcional por partido.

## 1.3 — 2026-10-29: más peso a los H2H de la temporada pasada
Pedido de Nicolas tras el 28-oct (Curry: el modelo daba Under 25.5 con 80%; en los 2 H2H de 2025-26 contra MIN
hizo 39 y 26, y anotó 42). Al empezar la temporada hay pocos partidos y cada rival defiende distinto.

- `model.load_game` añade a cada jugador sus partidos contra el rival de hoy en `h2h/<temporada anterior>.json`,
  solo los que jugó con su equipo actual y con >= 10 min (`apply_h2h`, informe en `h2h_report`).
- Tasas por minuto = (1 - wh) x tasa normal + wh x tasa H2H, con wh = n / (n + 3): 1 partido 25%, 2 -> 40%,
  3 -> 50%, 4 -> 57%. La defensa vs posición (DvP) ya no se aplica a la parte H2H (ese partido ya trae al rival).
- Minutos: se mezclan con los minutos H2H y se re-escalan para que el equipo sume lo mismo.
- 1.3.1 (mismo día, pedido de Nicolas): los minutos H2H pesan igual que las tasas (antes la mitad), pero solo si
  el rol del jugador es el mismo que hoy: titular en la mayoría de esos H2H y titular hoy, o suplente y suplente
  (los box scores de ESPN listan primero a los 5 titulares). Si cambió de rol (p. ej. un suplente que ahora es
  titular) sus minutos H2H no cuentan; las tasas por minuto sí. Los H2H con otro equipo nunca cuentan (caso
  Naz Reid: sus H2H con MIN no se usan para CHA). Backtest 27-oct: error de minutos 4.75 -> 5.06, PTS 3.138 -> 3.213.
- 1.3.2 (mismo día, pedido de Nicolas: ponderar según el contexto del jugador): cada H2H cuenta según los minutos
  que jugó frente a los que se le proyectan hoy (15 de 34 min = 0.44 partidos), así un H2H corto por lesión,
  faltas o paliza pesa poco y manda la forma reciente + la defensa del rival (DvP). Para los minutos solo cuentan
  los H2H en que jugó al menos la mitad de sus minutos de hoy, y si cambió de rol pesan un tercio (antes nada).
  Backtest 27-oct: minutos 4.75 -> 4.85, PTS 3.138 -> 3.201.
- Se desactiva con `"h2h": {"use": false}` en el JSON del partido o `python model.py <partido> --no-h2h`.
- `backtest_h2h.py <fecha>` compara con y sin H2H en una jornada jugada. En el 27-oct (4 partidos, 48 jugadores
  con H2H) el H2H empeoró un poco el log score (PTS 3.138 -> 3.181) y mejoró el error de minutos (4.75 -> 4.64):
  muestra muy chica; revisar cada jornada y ajustar `H2H_K`.

Curry 28-oct con 1.3.1: 21.2 pts proyectados (antes 18.9), P(over 25.5) 29% (antes 20%). Sus minutos casi no
suben (23.6): sus compañeros también jugaron más en esos H2H (GSW rota hoy a 13 jugadores) y el re-escalado a 240
se lo come. El problema de fondo es que sus minutos salen solo de sus 3 primeros partidos.

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
