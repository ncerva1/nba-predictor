# Cambios del modelo

## Web — 2026-10-31: tres actualizaciones diarias
Pedido de Nicolas: resultados de madrugada, análisis con cuotas por la mañana y revisión de bajas antes del primer
partido. `build_site.py --stage results|morning|pregame [--for fecha]` registra cada actualización en `index.json`
(`updates`, `last_stage`) y la web la muestra en "Actualizaciones de la jornada" (hecha/pendiente, con la hora de la
revisión de bajas calculada desde el primer partido). El historial del modelo suma el récord de SGP y de parlays entre
partidos, y cada jornada anterior muestra sus parlays con Ganada/Perdida por pata.

## 1.3.5 — 2026-10-30: tiros libres, quinteto por puesto y rotación
Pedido de Nicolas tras ver la 1.3.4.

- Tiros libres anotados e intentados: columnas opcionales FT y FTA (`build_slate.FT_COL`, aparte de las demás para
  que un CSV sin ellas siga teniendo el resto). Los libres metidos se dibujan primero sobre los puntos que no son
  triples (proporción del jugador, sin sesgo por paridad) y el resto son dobles; los fallados van en proporción a los
  metidos. `evaluate.py` acepta `..., ftm, fta` al final del box score ampliado.
- Quinteto probable por puesto (`build_slate.assign_slots`): los 5 titulares se reparten PG/SG/SF/PF/C con la
  asignación más cercana a su posición listada (el que más asiste va de base, el que más rebotea de pívot). La DvP
  del titular (`db_link`) mezcla mitad el puesto que cubre y mitad su posición: Derrick White como base de BOS
  recibe la defensa de CHI contra bases.
- Web: "Top 20" (los 20 picks de mayor EV entre todas las categorías) es la vista por defecto; todas las tablas de
  apuestas se ordenan por columna (clic en el encabezado o selector "Ordenar") y cada sección se puede plegar; la
  tabla Resumen de props trae todas las estadísticas; las categorías sin líneas publicadas (combinadas, tiros,
  faltas) se muestran con una explicación; "Calcula tu apuesta" en cada partido da la probabilidad y el EV de
  cualquier prop con la línea y la cuota de tu casa; "Quinteto probable y rotación" muestra puesto, papel (titular,
  rotación, rol, reserva de paliza), minutos proyectados y en qué tramos de cada cuarto suele estar en cancha
  (patrón típico de la NBA, no cambios reales).

## 1.3.4 — 2026-10-30: más mercados de player props y picks por categoría
Pedido de Nicolas: además de puntos, rebotes, asistencias, triples, combinadas (PTS+REB, PTS+AST, REB+AST, PRA),
robos, tapones, faltas y tiros de campo / dobles / triples anotados e intentados.

- `model.simulate` dibuja PTS+REB, PTS+AST, REB+AST y robos+tapones a partir de las mismas simulaciones (así
  mantienen la correlación). Si el JSON del jugador trae FG, FGA, 3PA, STL, BLK, TOV y PF (columnas opcionales de
  `raw/<fecha>/cur.csv` y `prior.csv`, ver `build_slate.EXTRA_COL`), también dobles y triples anotados e intentados,
  tiros de campo, robos, tapones, pérdidas y faltas: los tiros salen de los puntos simulados (lo que no son triples
  se reparte entre dobles y libres con la proporción del jugador; los fallos se dibujan aparte), robos y pérdidas
  van por minutos y ritmo, tapones y faltas por minutos (faltas con tope 6). Sin temporada anterior con esos datos,
  robos, tapones, pérdidas y faltas se encogen hacia la media de su posición (G / F / C) y el perfil de tiro sale
  de su temporada actual.
- `prop_lines` acepta cualquier estadística de `build_site.STAT_LABEL` y líneas de un solo lado
  (`[línea, null, cuota under]`). `evaluate.py` liquida por `stat` y acepta box scores ampliados
  (`[min, pts, reb, ast, 3pm, fgm, fga, 3pa, stl, blk, tov, pf]`).
- Web: los picks de la portada se ven por categoría (Con valor, Equipo, Puntos, Rebotes, Asistencias, Triples,
  Combinadas, Robos y tapones, Tiros, Pérdidas y faltas) con el top 20 por EV de cada una en verde / azul (dorado
  en Copa) / rojo, y la página "Ver todo" (`#todos`, `#todos-cup`) lista todas las apuestas de la jornada por EV
  con buscador. `index.json` lleva `all_bets` solo de la última jornada. En cada partido la tabla de proyecciones
  tiene vista Resumen (todas las estadísticas) y se ordena por minutos, puntos o cualquier columna.

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
