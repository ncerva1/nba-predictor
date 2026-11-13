# Cambios del modelo

## Modelo 1.6.2 — 2026-11-13: tiros, libres, pérdidas y faltas (mercados de bucketsbase)
- Pedido de Nicolas tras ver en bucketsbase props de tiros de campo, triples, dobles y libres (anotados e intentados),
  pérdidas y faltas. El modelo ya los simulaba (1.3.4-1.3.6) y la web los proyectaba; ninguna fuente que leemos
  publica sus líneas (scoresandodds, Covers, VegasInsider y RotoWire solo traen puntos, rebotes, asistencias, triples,
  robos, tapones y combinadas), así que siguen como picks del modelo sin cuota.
- Backtest `bt_shots.py` (26-oct a 12-nov, 2.010 jugador-partido): el modelo sobreproyectaba dobles anotados (real /
  simulado 0.896), libres (0.894 / 0.881) y tiros de campo (0.929). Causa: puntos y triples se simulan por separado y
  en las simulaciones con 3 × triples > puntos lo que queda para dobles y libres se cortaba en 0, inflando su media.
  Arreglo: `SHOT_THIN` (lo que queda se adelgaza para que su media sea puntos − 3 × triples) y `SHOT_KFT` 0.95.
  Queda: dobles 0.952, libres 0.999 / 0.983, tiros de campo 0.968; RPS mejora en tiros de campo, intentados y libres.
  Pérdidas y robos un 5-7% altos: `EXTRA_K` 0.95 / 0.93 (0.989 / 0.989). Puntos, rebotes, asistencias y triples no cambian.
- Picks sin cuota: también pérdidas y faltas (`MP_SHOTS`, mínimo 1.5 de promedio). En tiros el modelo decía ~60% y
  acertaban ~53%, así que la probabilidad publicada se acerca a 50% (`MP_SHRINK` 0.3; `p_model` guarda la original);
  pérdidas y faltas no se encogen (faltas Over 62.6% real con 57.8% del modelo, n = 163).
- Web: la ficha del jugador y Rachas cubren faltas, tiros de campo, dobles, triples intentados y libres (player_cards.py
  guarda esas columnas y `mp`, la línea de los picks sin cuota, que Rachas usa como "línea del modelo"); "Pérdidas y
  faltas" muestra sus picks sin cuota en la portada.
- Rige desde la jornada del 14-nov (la del 13-nov ya estaba publicada).

## Web — 2026-11-12: botón "Arma tu combinada" siempre visible
- El botón flotante de Mi combinada se ve siempre (vacío dice "+ Arma tu combinada"); al abrirlo explica que ahí se
  calcula la probabilidad de cualquier combinada (prob. del modelo, cuota justa y EV). Antes solo aparecía al
  agregar el primer pick.

## Web — 2026-11-12: partidos previos tocables y soñador a lo ancho en escritorio
- Tocar una barra de los últimos 10 o un cruce (tarjetas de picks y ficha del jugador) abre ese partido: marcador,
  la línea completa del jugador (min, pts, reb, ast, triples, tiros de campo, libres, robos, tapones, pérdidas,
  faltas) y el box score de los dos equipos. Datos en docs/data/boxes/<fecha>.json (player_cards.py, solo los
  últimos 10 de cada jugador y sus cruces con el rival de hoy; se carga al tocar).
- Escritorio (≥900 px): el parlay soñador ocupa todo el ancho con las patas en filas de tarjetas y el seguimiento en
  columnas; en móvil sigue vertical.

## Contexto de rol (probado, apagado) y "Su rol según quién juega" en la web — 2026-11-12
- context.py: por jugador, forma reciente (últimos 5) y medias en los partidos con la misma situación de compañeros
  clave (≥28 min) que hoy. model.py lo mezcla con las tasas de temporada (USE_CTX, RECENT_W, RECENT_MW, CTX_K, CTX_RK).
- Backtest 1-11 nov (`bt_stars.py ... 0 0 4 12`): MAE de puntos 4.635 → 4.620 y de minutos 4.84 → 4.83, pero log loss
  en las líneas de la casa 0.7016 → 0.7019 (asistencias 0.6928 → 0.6964); la forma reciente sola no aporta. Con ~10
  partidos por equipo las muestras "sin X" son de 2-8 partidos y la casa ya mueve la línea. Queda APAGADO
  (USE_CTX = False, el modelo sigue en 1.6.1) y se vuelve a probar hacia el 25-nov (decisión de Nicolas).
- Web: la ficha del jugador muestra sus medias reales de esta temporada con y sin cada compañero clave (≥24 min en
  ≥2 partidos), quién falta hoy y cómo le fue en los partidos con esa misma situación (player_cards.py, campo `ro`).

## Modelo 1.6.1 — 2026-11-12: más minutos y puntos para las estrellas
- Diagnóstico (Cooper Flagg en 16.1 pts con 25.9 de media): el modelo comprimía hacia la media. Desde el 1-nov, los
  de 25+ pts quedaban −2.6 pts y −1.7 min; los de 20-25, −1.7 pts y −2.9 min; los suplentes de <10 pts, +0.6 pts.
  En las líneas de la casa, P(over) media 0.445 frente a 0.487 real (0.424 frente a 0.502 en los de 20+ pts):
  de ahí el exceso de Unders.
- Cambios en model.py: MIN_BETA = 1.15 (los minutos del equipo se concentran algo más en los que más juegan),
  PTS_GAMMA = 1.1 (el reparto de puntos del equipo se concentra algo más en los anotadores), GARBAGE_CUT 0.86 → 0.93
  (en una paliza los titulares siguen perdiendo minutos, pero menos).
- Backtest 1-11 nov (`bt_stars.py`, 1.599 actuaciones, 2.571 líneas de casa): sesgo de puntos por nivel entre −0.1 y
  +0.4; MAE de puntos 4.685 → 4.635 y de minutos 4.95 → 4.84; P(over) media 0.473 frente a 0.487 real (estrellas
  0.479 frente a 0.502); log loss en las líneas 0.7083 → 0.7016. Pendiente: los triples siguen algo cortos
  (P(over) 0.468 frente a 0.512).
- Jornada del 12-nov re-simulada con 1.6.1 antes del primer partido (pedido de Nicolas).

## Web — 2026-11-12 (5): beneficiados por bajas, cruces favorables, movimientos de línea y buscador
- Portada: "Beneficiados por bajas" (OUT de última hora y dudas con la simulación sin él, index `wo` de
  build_site.wo_index; fuera hoy con los partidos reales sin él), "Cruces favorables" (DvP del rival en el sexto
  extremo para el lado del prop) y "Movimientos de línea" (línea y cuota desde la apertura, campo `open`).
- Buscador de jugadores (lupa arriba): jugadores de la jornada y de las 3 anteriores; abre la ficha.
- player_ids.json guarda el nombre como lo escribe nba.com (la web lo normaliza).

## Web — 2026-11-12 (4): tarjetas de pick con gráficas y resumen del modelo actual
- Picks en tarjetas (vista por defecto; también "Por partido" y "Tabla"): foto, el pick en grande, cuota, gráfica de
  sus últimos 10 partidos contra la línea de hoy (verde = se habría cumplido) y cruces recientes contra el rival.
  En móvil se deslizan a los lados. El Pick del día usa la tarjeta grande.
- Mini gráfica de los últimos 10 en la columna Últ. 10 de todas las tablas de picks.
- "Cómo le ha ido al modelo": las cifras son de la última versión con jornadas liquidadas, con su curva de unidades
  acumuladas; las versiones anteriores quedan en el detalle.
- Sombras suaves, esquinas más redondeadas y entrada animada de las secciones.

## Web — 2026-11-12 (3): limpieza visual y fotos de jugadores
- Portada: picks justo después del Pick del día; "Cómo le ha ido al modelo" al final como 4 cifras (picks, unidades,
  ROI, línea de cierre) con las tablas plegadas. Mayor EV y picks sin cuota plegados de entrada. Actualizaciones,
  bienvenida y leyenda en una línea. Móvil: de ~15.800 px a ~5.900 px de alto.
- Partido en pestañas Picks / Jugadores / Análisis. Apuestas del partido: las de valor y las 10 de mayor EV, con botón
  para ver todas; picks sin cuota plegados. Sin "Líneas escalonadas"; "Crea tu pick" pasa a un botón que abre el
  formulario de Mi combinada. Móvil: de ~47.000 px a ~7.400 px en la pestaña Picks.
- Filas de picks: sin la etiqueta "Player prop", Lo tomo y + en una fila, cuota junto al nombre en móvil, borde verde
  en los picks con valor y cuota más grande. Tablas apiladas sin cortes a la derecha; títulos más pequeños en móvil.
- Fotos oficiales de nba.com junto a cada nombre y en grande en la ficha (`docs/data/player_ids.json`, de
  `league_view.player_ids`; si no hay foto o no carga, iniciales).

## Web — 2026-11-12 (2): pestaña Liga, Pick del día y proyección vs línea (ideas 4-9 de bucketsbase)
- Pestaña Liga (#liga) con cuatro secciones: Equipos (récord total/local/visitante, últimos 10, ratings con su puesto
  y DvP de las 5 posiciones), Lesiones (parte de toda la liga con fecha estimada de regreso, `docs/data/injuries.json`
  de `injuries.py`; lo refrescan las revisiones horarias, paso 1b), Líderes (promedios de esta temporada) y Margen de
  las casas (prob. implícitas de los dos lados − 1 por casa y mercado, con todo lo guardado en odds/).
  `league_view.py` escribe `docs/data/league.json`; build_site.py lo llama al final.
- Pick del día en la portada: el pick con valor de mayor EV de la jornada, con el historial de los anteriores.
- Proyección vs línea: cada prop guarda `mean` (media simulada) y las tablas de picks la muestran bajo la
  probabilidad ("proy. 27.4 (+2.9)", verde si favorece al pick). Jornadas anteriores rellenadas con `line_moves.py`.

## Web — 2026-11-12: ficha de jugador, Rachas y línea de cierre (ideas de bucketsbase)
- Ficha de jugador: tocar cualquier nombre (picks, tablas de props, Rachas) abre su ficha con barras de sus últimos
  partidos frente a la línea (editable), % por encima en Últ. 5/10/20, esta temporada y H2H, filtros de sede, minutos,
  nivel del rival (DvP de esta temporada para su posición y estadística) y temporada, y lo que proyecta el modelo para
  esa línea. Datos en `docs/data/players/<fecha>.json` (`player_cards.py`, lo llama build_site.py al final; no
  re-simula). `player_logs` guarda ahora el local de cada partido.
- Pestaña Rachas (#rachas): jugadores con 3+ partidos seguidos por encima o por debajo de su línea de hoy, con lo que
  dice el modelo de ese lado. Se omiten líneas con cuota < 1.20.
- Línea de cierre: cada apuesta guarda `open` = [línea, cuota] de su primera publicación y build_site.py lo conserva al
  re-simular. La web marca en la cuota si la línea se movió a favor o en contra desde la apertura, y el historial y
  Mis picks cuentan cuántos picks le ganaron al cierre (`clv_beat`/`clv_same`/`clv_lost`). Las jornadas anteriores se
  rellenaron con la primera versión de cada partido en git (`line_moves.py`).

## 1.6 — 2026-11-12: nivel de anotación de esta temporada y puntos más abiertos
Recálculo pedido por Nicolas cuando todos los equipos llegaron a 10 partidos. Backtest: las 127 jornadas-partido del
26-oct al 11-nov re-simuladas con la BD de cada mañana (`db/build_db.py --snapshot <fecha>`) y 1.875 líneas de props
con cuota a los dos lados.

- Totales: salían +5,4 puntos sobre lo real (la casa +2,3). El JSON de cada partido fija el nivel de liga de 2025-26
  (115 x 100,5 = ~115,6 puntos por equipo) y esta temporada se anotan ~113,2. `db_link` calcula `league.pts_scale` =
  media de puntos de la liga esta temporada (mezclada con la pasada por N / (N + 30) partidos de liga) / nivel del
  JSON, y `model.expected_scores` la aplica a los dos equipos (hoy 0,983, unos −4 puntos de total). Sesgo del
  backtest: +5,4 → +2,6; MAE del total 15,2 → 14,8 (casa 14,0).
- Puntos de jugador: dispersión NB 20 → 7. Antes el 17% de las anotaciones reales caía bajo el percentil 10 simulado
  y el 12% sobre el 90; ahora 13% y 9,5%. Log loss del modelo solo en las líneas: 0,711 → 0,708. Equipos, rebotes,
  asistencias y triples ya tenían el ancho correcto y no cambian.
- Probado y descartado: corrección del Over por estadística (no mejora fuera de muestra: los ajustes cambian de signo
  según el periodo) y bajar `MKT_W` a 0,15 (log loss casi igual, 0,6826 vs 0,6830, pero con cuotas al margen de
  Betano deja 0–1 picks por jornada). `MKT_W` sigue en 0,25: con cuotas colombianas el backtest da 89 picks en 10
  jornadas, −1,5 u: el modelo apenas supera el margen de la casa.
- Web: la tabla por versión cuenta 1.5.1 y 1.6 juntas como 1.6 (empiezan la misma jornada).

## Datos — 2026-11-11: líneas de combinadas (P+R, P+A, R+A, PRA) y triples
- Pedido de Nicolas: el modelo ya simulaba las combinadas, pero no había líneas de casa (scoresandodds las carga por
  JS en /nba/props). Ahora salen de la página de cada jugador en scoresandodds (6-8 casas): `odds/combos/<fecha>.json`
  + `python odds/merge_combos.py <fecha>` las añade a prop_lines del slate. Con eso entran en apuestas, props,
  portada, parlays, soñador y Armar apuesta como cualquier otra prop.
- 11-nov: 79 jugadores y 387 líneas, re-simulado a las 15:00 ET. A pedido de Nicolas (15:26 ET) se republicaron los
  15 picks y el soñador de ese día con las combinadas: entraron 10 picks nuevos y 5 patas nuevas en el soñador (cuota 50.86).

## Web — 2026-11-11: Armar apuesta en cada partido
- Pedido de Nicolas (estilo ludoprops): sección **Armar apuesta** arriba de las apuestas de cada partido, con las 5
  mejores líneas del partido o de cada equipo y el porqué de cada una (proyección y rango, prob. modelo vs casa,
  Últ. 10, H2H, DvP del rival, bajas de hoy con sus partidos reales sin ese jugador, minutos, total esperado, paliza,
  back-to-back). Filtros: Partido / equipo, Over / Under / ambos y mercado.
- Solo líneas con cuota en los dos lados y prob. ≥ 50%. Puntaje = EV a cuota tipo Betano + apoyo de Últ. 10 / H2H /
  DvP; máx. 2 por jugador y una por familia. Sin jugadores fuera o en duda (p_play < 0.9) ni Unders de rookies.
- Cuota: la de 1.5.1; en jornadas anteriores al 12-nov se estima en la web con la misma fórmula (CO_OVERROUND 1.08),
  y con esa cuota van a Mi combinada / Mis picks. Todo se calcula en el navegador con el JSON ya publicado.

## Web — 2026-11-11: Mis picks con tu cuota y aviso de valor perdido
- Pedido de Nicolas: en cada pick de Mis picks se puede escribir **Tu cuota** (`dec_taken`, la de su casa) mientras no
  esté liquidado. Las unidades usan esa cuota, y un panel compara el EV de la web con el EV a tu cuota (misma prob.).
- Aviso en pendientes cuando la última revisión movió la línea (mismo jugador, estadística y lado con otra línea),
  dejó sin valor un pick recomendado o bajó la cuota más de 0.04. En la portada aparece "n perdieron valor".
- El soñador de las jornadas desde el 11-nov (`DREAM_CO_FROM`) va siempre a cuota colombiana, aunque la jornada se
  haya publicado antes de 1.5.1, así una revisión horaria no lo devuelve a cuotas de EE. UU.

## Modelo 1.5.1 — 2026-11-12: cuotas de props al margen de Betano Colombia
- Pedido de Nicolas (11-nov): en Betano las cuotas salen más bajas que en la web, así que el EV real es menor y el
  soñador de cuota 50 queda en ~37. Causa: las props que leemos (scoresandodds / Covers) son la MEJOR cuota de cada
  lado entre 6 casas de EE. UU. (margen conjunto ~3.5%); en Colombia el margen es ~7-8% (Kambi 29/30-oct: 7.3%).
- `localize_odds` (build_site.py), desde las jornadas del 12-nov: quita el margen del par y aplica `CO_OVERROUND`
  = 1.08; un lado sin pareja sube su prob. implícita en 1.08 / 1.035. La cuota leída queda en `odds_ref`/`dec_ref`
  (la web la muestra como "≈ Betano · EE. UU. x.xx"). No toca juegos con `"prop_odds_region": "CO"`.
- Soñador: con esas cuotas casi ninguna pata tiene EV ≥ 0, así que cada pata pide EV ≥ −3% (`DREAM_LEG_EV`).
- Prueba con la jornada del 11-nov: 12 picks (EV de +3% a +10% en vez de +8% a +17%) y soñador de 7 patas a ~56.

## Web — 2026-11-11: reemplazos para las patas del soñador
- Pedido de Nicolas: si la casa no ofrece una pata (p. ej. Reed Sheppard no sale en Betano), cada pata del soñador de
  hoy tiene "No está en mi casa" con hasta 3 reemplazos y la cuota/prob. se recalculan. `dream_swaps` (build_site.py)
  guarda por pata hasta 6 candidatas en `swaps` (con `pc`, su correlación con las patas y candidatas de su partido).
  Reglas duras: sin repetir jugador, máx. 2 por partido con corr. ≥ 0.97, máx. mitad Unders. Si no salen 3, entran
  marcadas en `soft` las de prob. 52–55% o cuota total 40–250 fuera de 50–200. La elección se guarda en el navegador;
  el seguimiento del soñador cuenta la publicada.

## Web — 2026-11-09: líneas escalonadas (X+)
- Pedido de Nicolas: las líneas tipo "31+ puntos @1.30". `ladders` da por jugador (≥ 15 min, juega seguro) la prob.
  de llegar a cada umbral de PTS, REB, AST, 3PM, PRA, R+A, robos y tapones (50–97%) con su cuota mínima con valor,
  ajustada en logit con la mezcla de la casa de la línea principal. Sección "Líneas escalonadas (X+)" en cada partido.
- `fetch_co.py` guarda todas las líneas de cada jugador (antes solo la principal) y las "N+" de un solo lado →
  `alt_lines` → `alt_bets` en build_site: entran en SGP y soñador, nunca en los picks con valor.

## Web — 2026-11-09: parlay soñador
- Pedido de Nicolas: una combinada por jornada con varias líneas de buena probabilidad y cuota total entre 50 y 200.
  `dream_parlay` (build_site.py) toma hasta 10 props por partido (prob. mezclada ≥ 55%, EV ≥ 0, cuota a los dos lados,
  sin jugadores en duda/rookies), permite 2 del mismo partido solo si su correlación en `sims/<id>.bin` es ≥ 0.97 y
  elige por programación dinámica la de mayor prob. conjunta con cuota 50–200 (máx. 10 patas). Sección "Parlay
  soñador" en la portada con su seguimiento propio (1 u por jornada; patas anuladas no cuentan en la cuota).

## Web — 2026-11-08: unidades por versión del modelo
- Pedido de Nicolas: "Cómo le ha ido al modelo" separa picks, % de acierto, unidades y ROI por versión (1.1, 1.2,
  1.3, 1.5), además de la columna Modelo en la tabla por jornada. Las versiones y su primera jornada están en
  `MODEL_VERSIONS` (build_site.py): al publicar una versión nueva se añade ahí.

## Web — 2026-11-02: título "Picks recomendados por el sistema"
- La sección de portada "Picks con valor" pasa a llamarse "Picks recomendados por el sistema" (pedido de Nicolas).

## Web — 2026-11-02: historial de 10 partidos con tiros (nba.com)
- `player_logs`: últimos partidos de temporada regular de esta temporada y la anterior (pedido de Nicolas: si van
  menos de 10 en esta, se completan con la pasada; rookies y quien no jugó la pasada solo tienen los de esta), de
  `gamelogs/nba/` (oficial nba.com, con tiros) + `results/` + `h2h/`. Playoffs y play-in fuera; mismo partido con
  fecha ±1 día entre fuentes se cuenta una vez. Dobles = FGA − 3PA.
- Últ. 10 y H2H ahora cubren tiros de campo, dobles, triples intentados y libres; H2H usa todos los cruces de las dos
  temporadas. `--hr-only` recalcula los % de una jornada publicada sin re-simular.

## Web — 2026-11-02: picks con valor primero y % de cumplimiento
- Portada: primero "Picks con valor" (los verdes, los recomendados); luego "Los 20 de mayor EV" como información
  adicional (por categoría) y "Picks sin cuota de casa" (tiros, doble-doble) siempre visibles.
- `add_hit_rates`: cada prop y pick sin cuota lleva `hr` = {l10: [aciertos, partidos] en sus últimos 10 de esta
  temporada (de results/), h2h: lo mismo contra el rival (h2h/ temporada pasada + esta), dvp: puesto del rival en lo
  que concede a su posición (statpick: PTS, REB, AST, 3PM, PRA, STL, BLK, TOV)}. Columnas Últ. 10, H2H y DvP.

## 1.5 — 2026-11-01: probabilidades mezcladas con la casa y menos picks
Diagnóstico de Nicolas (por qué el modelo pierde unidades): en los 955 picks con valor del 26 al 31-oct el modelo
decía 64%, la casa pedía 54% y se acertó 49% (−91 u). Backtest con esas jornadas (`backtest_market.py`): con los
cambios de abajo, 39 picks, 26–13, +12,2 u (muestra pequeña: intervalo del ROI +6% a +56%).

- `market_blend`: p = p_casa sin margen + 0,25 × (p_modelo − p_casa) (`MKT_W`); `p_model` y `p_mkt` se guardan.
  Sin cuota a los dos lados de la misma línea no hay p_casa y la apuesta no se recomienda.
- Solo se recomiendan player props (ganador, hándicap y total: 28–44, −23,6 u) y nunca el Under de un rookie o de
  quien no jugó la temporada pasada (etiquetas `rookie` / `lesion`). `no_value_why` dice por qué no se marca.
- `daily_cap`: por jornada, como mucho 2 picks por jugador (uno por familia: puntos/P+R/P+A/PRA, rebotes/R+A,
  asistencias, robos/tapones) y 15 en total, los de mayor EV. `cand` = pasa el filtro; `value` = entra en el tope.
- SGP: solo props con cuota a los dos lados, sin rookies; los de 2, 3 y 4 patas no comparten patas; la
  probabilidad conjunta aplica la correlación de las simulaciones a las probabilidades mezcladas. Parlays entre
  partidos: solo de 2 patas. Con la mezcla habrá bastantes menos SGP.
- Descartado: no recomendar cuando el modelo se separa más de 15 puntos de la casa (empeoraba el backtest).
- Pendiente: ensanchar la varianza de las simulaciones (no se puede medir sin re-simular; revisar con la calibración
  de las próximas jornadas) y recalcular `MKT_W` cada semana con todas las líneas liquidadas.

## Web — 2026-11-01: "Crea tu pick" (picks propios en la combinada)
Pedido de Nicolas. El mismo formulario está en la página de cada partido (sustituye a "Calcula tu apuesta") y en Mi
combinada ("+ Crear pick", eliges cualquier partido que falte por jugarse; en el inicio, "+ Crea tu propio pick").
Jugador: cualquier estadística proyectada, como antes. Equipo: ganador, hándicap, total del partido y puntos de un
equipo (nuevo tipo de pata `tt`). Probabilidad de equipo: la publicada si la línea coincide con una apuesta de las
casas; si no, contada en data/sims/<id>.bin; sin muestra, normal centrada en la proyección. Las patas de equipo
propias se liquidan con el marcador y entran en el conteo conjunto del mismo partido. No cambia ningún pick.

## Web — 2026-10-31: "Si no juega…" (compañeros sin un jugador)
Pedido de Nicolas (idea de ludocomparador). `without.py` añade a cada partido `without`: por equipo, a cada jugador con
20+ minutos proyectados (o en duda con 12+) se le repite la simulación fuera (p_play = 0, misma semilla y mismas
simulaciones, así la diferencia es solo su baja) y se guarda media y P(over) de cada compañero sobre la misma línea
(la de la casa o la justa con él). Al ataque de su equipo se le resta el 30% de los puntos que aporta por encima de
un suplente (LOST_SHARE). También los partidos reales de esta temporada (results/) sin él frente a con él, y los de
los OUT de hoy (game.out). No cambia ningún pick. Los partidos ya jugados lo reciben al volver a correr
`build_site.py` para su fecha (se re-simulan solo para esta sección).

## 1.3.6 — 2026-10-31: doble-doble, triple-doble y picks de tiros de campo
Pedido de Nicolas: no había picks de doble-doble, triple-doble ni tiros de campo anotados / intentados.

- `model.simulate` dibuja `dd` y `td` (1 si lo logra) contando en cada simulación las categorías con 10 o más
  (puntos, rebotes, asistencias, robos y tapones), así heredan la correlación de minutos y ritmo. En el resumen la
  media es la probabilidad (línea 0.5). Con los partidos del 26 al 29-oct el modelo esperaba 54 doble-dobles y
  hubo 46 (entre 30% y 70% de prob. sale algo alto: 0.38 vs 0.26 y 0.56 vs 0.47); triple-dobles 3.0 vs 3.
- `model.shot_prior`: en `prior.csv` un 24% de las filas trae tiros que no cuadran con los puntos (2 x FGM + 3PM +
  FTM a más de un 10% de PTS). Esas columnas se escalan para que cuadren; antes inflaban o encogían los intentados.
- `build_site.model_picks`: picks sin cuota de casa (las casas que leemos no publican estas líneas). Tiros de campo
  anotados / intentados: línea de referencia en el .5 alrededor de su promedio (temporada actual mezclada con la
  anterior en los minutos de hoy) que menos le conviene al pick, lado con >= 55%, solo jugadores con 20+ minutos,
  3+ anotados / 7+ intentados de promedio y que seguro juegan. Doble-doble "Sí" desde 35%, triple-doble desde 6%.
  Cada pick trae la cuota justa y la cuota mínima que debe pagar la casa para tener valor (EV >= 3%); se liquidan
  con el resultado real como los demás.
- `prop_lines` acepta `dd` / `td` (`[0.5, cuota sí, cuota no]`; las apuestas se llaman "Doble-doble: Sí / No") y
  `odds/fetch_co.py` lee esos mercados de Rushbet / BetPlay (sí / no por jugador, o lista de jugadores solo con sí).
- 31-oct (aclaración de Nicolas): lo que pedía eran las props de tiros. Los picks sin cuota cubren ahora tiros de
  campo, dobles (anotados e intentados), triples intentados y tiros libres (anotados e intentados); los triples
  anotados ya tenían líneas de casa. La tabla se filtra por estadística (en "Todas", las 20 más probables).
- Web: categoría "Doble y triple-doble" en los picks; en "Tiros" y "Doble y triple-doble" (inicio, "Ver todo" y
  cada partido) aparece la tabla "Picks del modelo sin cuota de casa"; la tabla de props trae DD y TD en %.

## Web — 2026-10-31: etiquetas de rookies y jugadores sin temporada previa
Pedido de Nicolas (casos Dybantsa: Under 4.5 rebotes con 1 partido NBA, y Kyrie: Under 26.5 PRA sin 2025-26).

- `players/<temporada>.json`: novatos (lista de basketball-reference) y veteranos que no jugaron la temporada anterior
  (`missed_prior`, con el motivo). `build_site.player_flags` además marca "sin datos previos" a quien no esté en
  `raw/<fecha>/prior.csv` (el modelo solo usa sus partidos de esta temporada).
- La etiqueta (Rookie / Sin 2025-26 / Sin datos previos) sale junto al nombre en las props del partido, en las
  tablas de picks, en las patas de los parlays y en la calculadora, con el aviso de que sus predicciones varían más.
  Pasa el cursor o toca la etiqueta para ver el detalle. Se aplica también a las jornadas ya jugadas sin re-simular.
- Solo informa: el modelo no cambia sus probabilidades por la etiqueta.

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
