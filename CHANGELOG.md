# Cambios del modelo

## 1.4 — 2026-10-30: el H2H como "cuánto le sube ese rival"
Acordado con Nicolas el 29-oct (casos LaMelo CHA -> MIN contra MIA, y MIA cambiada con Giannis).

- **Lift relativo**: en vez de usar las stats H2H tal cual, se mide cuánto le subió ese rival respecto a su
  temporada (tasa por minuto en los H2H / tasa de toda la temporada anterior, acotado a 0.5-2.0). Ese porcentaje
  se aplica a su nivel de hoy y se mezcla con la DvP actual del rival: efecto rival = (1 - w) x DvP + w x lift.
- **H2H con otro equipo**: como el lift no depende de su equipo, ahora cuentan los H2H que jugó contra ese rival
  con su equipo anterior (pesan x0.75), si el partido está en `h2h/<temporada>.json`. Si tiene H2H con su
  equipo actual, se usan solo esos.
- **Cambio del rival**: el peso del H2H se multiplica por 1 / (1 + z^2), con z = cuánto cambió la defensa del
  rival (DEF_RATING y la stat concedida: OPP_PTS, OPP_REB, OPP_AST, OPP_FG3M) entre la temporada pasada y la
  mezcla actual de la BD (ya encogida por partidos jugados), en desviaciones típicas entre los 30 equipos. Va en
  `h2h_report.opp_change` (p. ej. el 28-oct MIN 0.6-0.7, TOR y PHI ~0.5, MIA ~1).
- **Minutos**: lo que jugó en los H2H frente a su media de esa temporada (acotado 0.7-1.3), con un tercio del peso
  de 1.3.2: en el 27 y 28-oct los minutos H2H empeoraron siempre el error de minutos.
- Forma reciente: con 4-5 partidos jugados la media de la temporada ya es la de los últimos partidos; queda para
  cuando haya registros partido a partido (raw/ solo trae medias).

Backtest (log score, menor es mejor; jugadores con H2H), sin H2H / 1.3.2 / 1.4:
27-oct PTS 3.118 / 3.198 / 3.159, PRA 3.385 / 3.473 / 3.450, minutos 4.82 / 4.82 / 4.86;
28-oct PTS 3.108 / 3.123 / 3.114, PRA 3.362 / 3.388 / 3.367, minutos 4.45 / 4.59 / 4.51.

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
