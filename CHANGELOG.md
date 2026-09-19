# Registro de cambios

## Sin publicar

Correcciones de la [auditoría del 18/09](docs/auditorias/2026-09-18.md), que
encontró que el radar perdía motos reales en silencio por tres vías distintas.

Lo que perdía compras:

- `parse_price` devolvía **0.0 para cualquier texto que dijera «grátis»**, sin
  mirar el importe: `"R$ 900 · Entrega grátis"` valía 0. Explicaba los once
  `R$ 0` de la corrida real.
- Una tarjeta de Marketplace **sin precio** se quedaba con la ciudad como título
  y sin ubicación, y después se descartaba por «ciudad no coincide»: una moto
  perdida con un motivo que mentía. `_parse_card` ya decide por contenido.
- `appraise` estaba calibrado sobre títulos de Marketplace y decidía por las
  primeras palabras. Con el feed cronológico recibe el **cuerpo del post**, que
  empieza con saludo o emoji: «Vendo essa 125 andando, 800» —el ejemplo que
  justifica el feed— se descartaba como desconocido, y «Bom dia grupo! Vendo
  motor de CG 150» pasaba como moto en presupuesto. Nuevo `text_shape`, verbos y
  determinantes de las dos orillas, y guardas para rifa, alquiler y vivienda.
  Corpus de regresión de 24 cuerpos de post en `tests/fixtures/group_corpus.json`.
- Una seña se confirmaba como precio total (`"aceito 200 de sinal"` → R$200) y
  en `"comprei por 4 mil, vendo por 900"` ganaba lo que **pagó** el vendedor.
- `_first_price` tomaba el primer número de la línea: `"CG 125 2008 R$ 900"`
  daba R$1.252.008. Ahora toma el importe pegado al símbolo.
- Dos tarjetas sin `href` compartían el uid `facebook:` y una moto desaparecía
  en el dedupe.
- **El segundo destinatario no recibía ninguna re-alerta.** El evento se
  calculaba releyendo la fila ya sobrescrita por el primero, así que el socio
  que iba segundo en `chat_id` comparaba el aviso contra sí mismo. Ahora
  `upsert_many` decide el evento una vez y encola a todos en la misma transacción.

Lo que ocultaba que el radar estaba ciego:

- `feed_dom_broken` ignoraba `children`: un grupo que no materializaba ni un
  post se informaba como tarde tranquila.
- La salud se medía por fuente, no por superficie: con Marketplace trayendo 150
  avisos, los ocho grupos podían estar ciegos semanas sin que la racha de
  silencio arrancara. `observed_by_origin` declara cada superficie, incluidas
  las que observan cero.
- `source_zero_streak` se reseteaba con cualquier corrida donde la fuente no
  participara (un `run --source olx` bastaba) y con un `stats` ilegible.
- `extract_description` exigía una línea exactamente igual a «detalhes» y
  devolvía `""` en la página real de un vehículo, sin dejar rastro. Ahora
  reconoce prefijos y descripciones, **lee el precio del detalle** como campo
  aparte, y el cupo de `detail_limit` va primero a las carnadas, que son las
  únicas cuyo precio se puede corregir.

Privacidad, secretos y configuración:

- El token **no puede estar en config.yaml**: se lee de `TELEGRAM_BOT_TOKEN` o de
  `~/.motoradar/telegram_token`, fuera del árbol del proyecto. Un `grep` sobre
  `*.yaml` lo expone sin abrir el archivo, y eso ocurrió de verdad en la auditoría.
- El texto de terceros se redacta al entrar (`scrub_personal`) y el histórico
  tiene plazo de borrado (`monitoring.retention_days`, 90 días). El feed captura
  publicaciones de vecinos que no venden nada.
- `doctor` enmascara los chat_id; la vista previa de Telegram se limita al enlace
  del aviso, para que una URL escrita por un desconocido no genere una tarjeta
  rica dentro de una alerta que parece del radar.
- Una errata en el nombre de una clave de config ya no pasa en silencio, y sugiere
  la correcta. Encontró un caso real en este repositorio: `sources.olx.region_path`
  no existe (es `state_path`), así que OLX corría con la región por defecto.
Robustez y operacion (misma tanda):

- Cobertura de los grupos: `_render_feed` se rendia tras un unico chequeo de
  crecimiento de 1,5 s y devolvia 3-8 posts de los 20 pedidos. Ahora reintenta
  mientras quede presupuesto. Y `_settle` ya no espera 8 s por `networkidle` en
  un feed infinito donde nunca llega: espera al primer post.
- `group_scroll_pause`: el ritmo de los grupos usaba el valor por defecto porque
  `scroll_pause` solo lo leia Marketplace. Dos diales, cada uno conectado.
- Un grupo que revienta ya no se lleva los otros siete (pasaba con "Execution
  context was destroyed", comun en un feed que navega solo). El error se anota
  por indice, no por id, para no filtrar el grupo privado a `runs.stats`.
- Un bloqueo que Facebook renderiza **sin cambiar la URL** (muro de
  verificacion, pedido de contraseña) se detecta por contenido y es
  `SessionExpired`, no "DOM roto": antes `watch` seguia entrando a una cuenta
  ya marcada 11 veces por pasada.
- Entregas: se guarda la `description` de Telegram (la unica explicacion util) y
  un fallo permanente (403 bot bloqueado, chat inexistente, token invalido) marca
  la fila `dead` en vez de reintentarla cada hora indefinidamente. Un 429 sin
  `retry_after` tambien aplaza al destinatario. Esquema v4 con migracion por
  version, porque `SCHEMA` solo crea tablas que falten.
- Un aviso de salud marca su cooldown recien cuando alguien lo recibio.
- `watch` devuelve codigo 2 sin fuentes habilitadas, 1 si ninguna pasada
  funciono, y avisa tras 3 pasadas fallidas seguidas: un fallo anterior a
  `check_health` (base bloqueada, config rota) no generaba ningun aviso.
- La cadencia de Marketplace se retoma donde quedo la base: `cycle` arrancaba en
  0 en cada arranque y reiniciar a mano anulaba `marketplace_every_cycles`.
- `fix_bait_prices` ya no reintroduce un precio fantasma cuando el detalle
  tambien dice "R$ 0", y el umbral de señuelo es una sola constante.
- `enrich_olx` con techo de items y de tiempo, y con `detail_status` en los
  fallos; OLX reintenta un 429 con backoff y jitter en vez de al instante.
- CSV con BOM: Excel en Windows abria "Jaguarão" como "JaguarÃ£o".
Cierre de los tres pendientes que quedaban del ROADMAP:

- **Contrato de fuente obligatorio**: `BaseSource` (ABC) con `failed_pages`,
  `dom_failures`, `errors`, `expected_origins`, `note_blind()` y
  `require_empty_evidence()`. `collect` dejo de leerlos con `getattr` y valores
  por defecto, que era la causa raiz de la salud falsa: Mercado Livre devolvia
  `{"status": "ok", "observed": 0}` —"hoy no hay motos baratas"— ante cualquier
  cambio de API. Ahora exige evidencia de vacio (`paging.total == 0`), no cancela
  las demas consultas ante un no-200, usa `start_time` en vez de `stop_time`
  (que es el VENCIMIENTO del aviso) y guarda su `condition` aparte, porque usaba
  la misma clave que `appraise` con otro vocabulario.
- **`posted_at` en los posts de grupo**, leyendo la fecha del permalink y
  resolviendo lo relativo ("2 h", "hace 3 horas", "ontem"). Con eso la frescura
  que justifica el feed cronologico es medible, y hay un guarda nuevo: si la
  mediana de antiguedad de los posts leidos supera `group_freshness_days`,
  Facebook no esta aplicando el orden cronologico y se avisa.
- **`motoradar explain`**: que vio el radar y por que cada aviso llego o se cayo
  (origen, precio, clase, veredicto, causa), leyendo la base sin consultar a
  nadie. `prepare` ahora guarda el motivo del descarte por aviso, no solo
  agregado. Aplicado a los datos reales encontro en 30 segundos una tarjeta cuyo
  unico texto era la ciudad: `_parse_card` la tomaba como titulo y appraise
  clasificaba "Jaguarão, RS" como desconocido. Ahora se declara
  `card_parse: la tarjeta no traia titulo` y la lectura de detalle la resuelve.
- Señal de extraccion baja: si se materializan N hijos del feed y se extraen
  menos de N/4 posts, se avisa. La corrida real del 18/09 mostro 20
  materializados y 2-5 posts extraidos por grupo: `_render_feed` ya trae lo que
  se le pide, pero `POST_JS` pierde el 85%. No se declara DOM roto (hay posts) y
  no se adivina un arreglo: cerrarlo necesita una captura del DOM real.
- 146 pruebas offline.

Cierre focalizado del 18/09/2026, verificado offline:

- Bajadas e incierto→confirmado generan entregas para ambos destinatarios,
  independientemente del orden. Observación y encolado múltiple son atómicos;
  los pendientes conservan intentos y aplazamiento al actualizarse.
- Feed sin hijos ni cartel de vacío, o hijos sin posts extraíbles, registra
  fallo de DOM. El éxito parcial no borra el cooldown de un fallo aún activo.
- Detalle reconoce encabezados de vehículo y descripción en portugués,
  español e inglés; prioriza descripción sobre atributos y registra cuando
  no reconoce texto. Regresión completa de tarjeta señuelo a exclusión/alerta.
- Fixture de tarjeta sin precio corregida y comprobada hasta selección final.
  Suite local: 102 pruebas; sin consultas ni envíos reales en esta tanda.

Facebook: cobertura, frescura y fallo visible (revisión del 18/09/2026).

- Los grupos se leen por **feed cronológico** (`group_mode: feed`) y se clasifican
  en local. El buscador de Facebook ordena por relevancia y solo devuelve lo que
  contiene la palabra: «vendo essa 125 andando, 800» no dice «moto» y se perdía.
  `search` y `both` siguen disponibles.
- **Una fuente ciega ya no se informa como pasada vacía.** Si no hay tarjetas ni
  feed ni cartel de vacío, se cuenta `dom_failures`, la corrida queda en `error`
  y llega un aviso de salud por Telegram. Antes un cambio de HTML apagaba el
  radar y seguía informando «OK: 0 observados».
- Avisos de salud por silencio sostenido (`monitoring.health_zero_runs`), DOM no
  reconocido y sesión caída, con cooldown por motivo en la base y limpieza
  automática al volver a observar. `doctor` lista los avisos activos.
- `SessionExpired` separa la sesión caída del resto de fallos: `watch` pausa esa
  fuente `monitoring.session_pause_cycles` pasadas en vez de reintentar contra el
  mismo checkpoint, con el riesgo de cuenta que eso implica.
- **Ninguna alerta queda sin enlace.** Sin permalink renderizado se reconstruye
  desde el id del post y, en último caso, se enlaza el grupo; `url_confidence`
  lo declara en la alerta.
- Un grupo sin `group_cities` ya no hereda las ciudades buscadas como si fueran
  su ubicación: se marca `region_unknown`, sus avisos siguen pasando (no se
  pierden motos baratas) y tanto la consola como la alerta lo dicen.
- En los grupos el precio sin moneda explícita ya no se da por reales: se detecta
  la moneda del texto, así un «$U 15.000» no se convierte en un precio brasileño.
- `marketplace_every_cycles` deja Marketplace corriendo una de cada N pasadas
  mientras los grupos se miran en todas; el intervalo por defecto de `watch` baja
  a 15 minutos.
- Fixtures sanitizadas de tarjetas de Marketplace y posts de grupo
  (`tests/fixtures/`) fijan la traducción a `Listing`, que es donde estaban los
  errores caros: precio truncado, ciudad tomada por título, alerta sin enlace.
- `telegram.chat_id` acepta una lista de destinatarios: el radar lo usan dos
  socios y cada uno recibe en su chat privado, con su propia cola de entregas,
  intentos y pausa por 429. El anuncio se persiste una sola vez. Por entorno,
  `TELEGRAM_CHAT_ID` admite ids separados por coma; `doctor` los lista con sus
  pendientes.
- Esquema v3 con `health_notices`; migración aditiva. 89 pruebas offline.
- Telegram configurado con bot propio y primera entrega real confirmada
  (18/09/2026, chat privado). El formato de alerta ahora declara también la
  confianza del enlace cuando no es el permalink del aviso.

Correcciones de las auditorías [inicial](docs/auditorias/2026-09-16-codex.md),
[segunda pasada](docs/auditorias/2026-09-16-codex-2.md) y su
[cierre offline](docs/auditorias/2026-09-17-cierre-codex.md):

- Las guardas de teléfono y año del parser tenían retrocesos (`\x08`) en vez de `\b`;
  "chamar por 53991234567 valor 900" se leía como R$53.991.234.567.
- `exclude_keywords`/`include_keywords` ya no buscan en metadatos internos
  (categoría, query): `exclude: ["pecas"]` descartaba toda la categoría de motos.
- Un pendiente se cancela cuando el anuncio deja de ser elegible; antes podía
  enviarse "MOTO EN PRESUPUESTO" con un precio que ya no existía.
- Consola y CSV marcan "POR CONFIRMAR"; el CSV incluye `alert_status`. Un CSV con
  columnas antiguas se renombra en vez de desalinear filas.
- Telegram se envía antes de escribir el CSV: un hits.csv abierto en Excel ya no
  bloquea alertas.
- La base anterior quedó cubierta por 38 regresiones locales.
- Parser contextual para no confundir entrada/cuota con precio total; corrección
  de señuelos UYU/USD después de comparar el umbral en moneda base.
- Clasificación contextual de objeto e intención: motor suelto, autos, pedidos,
  teléfonos usados como pago y frases como «chegou da oficina».
- Pausa Telegram persistente por destinatario, sin reiniciar intentos ante bajadas;
  payloads incompatibles quedan aislados sin bloquear el resto del lote.
- Observaciones con moneda incompatible llegan a persistencia y cancelan pendientes.
- Salud de fuentes estructurada (`ok`/`partial`/`error`), bloqueo/vacío OLX y
  login/checkpoint de Facebook diferenciados; `deals` falla si no hubo fuentes.
- Validación estricta de YAML, caché FX, respuestas Telegram y fórmulas CSV.
- Migración aditiva a esquema v2, presentación de centavos y política coherente de
  `min_samples`.
- Facebook separa consultas de Marketplace/grupos, muestra progreso, limita y
  prioriza detalles, y excluye piezas, maquinaria y objetos que solo mencionan
  motos. La suite crece a 61 regresiones y CI ejecuta `compileall`.

## 0.1.0 — Base inicial, 2026-09-16

Primera instantánea versionada del proyecto existente y de sus mejoras locales.

- Radar de motos de hasta R$1.000 inclusive, sin filtros de papeles o margen.
- Selección compartida, lectura de detalle y aviso separado de precio incierto.
- Corrección de precios truncados y renormalización monetaria.
- Estado actualizado por identidad, observaciones y entregas persistentes.
- Reintentos individuales, control de rate limit y bloqueo de corrida.
- Comandos doctor, retry y dry-run; paginación OLX corregida.
- 31 regresiones locales y workflow CI para Windows/Linux.
- Documentación de objetivo, arquitectura, operación, contribución y escalamiento.

Límites: Telegram real pendiente de configuración; Facebook y Mercado Livre
pendientes de validación. CI preparado pero no ejecutado remotamente.
