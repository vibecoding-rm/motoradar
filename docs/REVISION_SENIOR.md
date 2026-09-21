# Revisión técnica de Motoradar

Fecha: 16 de septiembre de 2026.

> Auditoría del estado anterior a las mejoras. Parte de los hallazgos ya se corrigió. El criterio de producto confirmado después es alertar por presupuesto, sin filtros de papeles ni margen. Consultar [objetivo vigente](docs/OBJETIVO.md), [roadmap](docs/ROADMAP.md) y [cambios implementados](INVESTIGACION_Y_MEJORAS.md).

## Dictamen

El proyecto tiene una estructura adecuada para una herramienta personal: módulos pequeños, adaptadores de fuentes, modelos compartidos y lógica de clasificación separada de la persistencia. La decisión de recoger una muestra amplia y enriquecer únicamente candidatas limita el coste de navegación.

Sin embargo, todavía no es fiable para operar sin supervisión. Los principales riesgos son precios incorrectos, pérdida de alertas, referencias contaminadas y deduplicación que elimina anuncios distintos. Aumentar volumen en este estado multiplicaría esos errores.

Recomendación: mantener un monolito modular y SQLite durante la primera etapa. Primero asegurar la corrección y la recuperación. Adoptar infraestructura distribuida cuando las mediciones y el número de procesos lo justifiquen.

## Alcance y evidencia

Se revisaron todos los módulos Python, README, requirements, configuración de ejemplo y .gitignore. No se encontró AGENTS.md, una suite de pruebas, CI ni manifiesto de empaquetado en este directorio. Tampoco hay metadatos Git disponibles aquí; esto no permite concluir cómo se gestiona el proyecto en otras ubicaciones.

Se ejecutaron reproducciones locales con Python 3.12.10, SQLite temporal y mocks. No se ejecutaron scrapers contra proveedores, no se accedió a la sesión de Facebook ni se enviaron mensajes. No se leyó el contenido de config.yaml ni de grupos.json. La capacidad real y el estado operativo de las integraciones no se midieron.

## Hallazgos priorizados

### P0 — El parser recorta importes

Ubicación: motoradar/models.py:17–38 y parse_price_text.

La primera alternativa de la expresión regular admite uno a tres dígitos y puede coincidir con un prefijo de un importe mayor. Reproducciones:

| Entrada | Resultado actual | Esperado |
|---|---:|---:|
| `1250` | 125 | 1250 |
| `6500` | 650 | 6500 |
| `R$ 1.250,00` | 1250 | 1250 |
| `quero 6500 reais` | 650 BRL | 6500 BRL |

También `USD 1,250.00` devuelve 1,25. El formato debe interpretarse con contexto de moneda/localidad; no basta con reordenar una alternativa si se pretende soportar formatos internacionales.

Impacto: una moto fuera del presupuesto entra como oportunidad y el margen queda inflado.

Solución: separar extracción de candidatos numéricos, interpretación de separadores y detección de moneda; rechazar formatos ambiguos o etiquetarlos para revisión. Usar importes decimales o unidades menores enteras, con una política explícita de redondeo. Conservar texto y evidencia de extracción.

Aceptación: corpus de regresión con importes sin separadores, miles, centavos, USD/UYU/BRL, teléfonos, kilómetros, años, precios anteriores, cuotas y anuncios con varios importes. Ningún importe conocido puede quedar truncado.

### P0 — Las alertas fallidas se pierden

Ubicación: motoradar/cli.py:75–84 y motoradar/notify.py:36–63.

Los anuncios se guardan antes de enviar. Luego se marca toda la lista como notificada, aunque Telegram haya enviado cero mensajes o solo una parte. La siguiente pasada solo selecciona anuncios nuevos: ni siquiera consultar `notified=0` resolvería por sí solo el problema actual.

Reproducción con envío simulado de cero mensajes: primera pasada devuelve un anuncio nuevo, segunda devuelve cero y la fila tiene notified=1. Una excepción de red después de guardar también deja el anuncio fuera de futuras listas nuevas.

Solución: crear una bandeja persistente de entregas en la misma transacción que guarda el anuncio/evento. Registrar destinatario, canal, estado, intentos, próximo intento y error. Confirmar cada entrega por separado y reintentar las pendientes independientemente de la recolección. Aplicar espera progresiva y límites de intentos.

Aceptación: pruebas de fallo total, fallo parcial, timeout, reinicio y límite HTTP 429. Recuperar todos los eventos pendientes. Documentar que un timeout posterior a la aceptación del mensaje puede producir duplicados: no prometer entrega exactamente una vez sin soporte del proveedor.

### P1 — Upsert no actualiza el anuncio

Ubicación: motoradar/store.py:37–60.

Para un uid existente solo cambia last_seen. Precio, título, URL y descripción quedan antiguos. Una bajada de 900 a 500 siguió guardada como 900 en la reproducción.

Solución: separar identidad estable, estado actual y observaciones históricas. Actualizar los campos del mismo uid y registrar los cambios relevantes. Decidir explícitamente cuándo una bajada de precio genera un evento de alerta.

Aceptación: precio actualizado y observación preservada; reingestar el mismo estado no genera eventos adicionales; first_seen permanece estable.

### P1 — Deduplicación demasiado agresiva

Ubicación: motoradar/models.py:126–129 y motoradar/store.py:40–48.

Título y precio entero no identifican un vehículo. Dos Honda Biz de vendedores o ciudades distintas al mismo precio se fusionan. Se omiten moneda, ubicación e identidad del vendedor; además se pierden centavos. Precio cero y precio desconocido generan el mismo componente de precio.

Reproducción: un segundo anuncio con otro id y otra ciudad fue rechazado como ya visto.

Solución: usar fuente + id externo como identidad primaria. Tratar similitud como relación entre anuncios, con puntuación y evidencia; no eliminar automáticamente por título y precio. Normalizar URL y usar hashes deterministas para publicaciones sin id. Facebook usa hash(text), cuyo valor no debe persistirse como identidad estable entre procesos.

Aceptación: anuncios distintos sobreviven, el mismo id es idempotente y las republicaciones se pueden relacionar sin perder observaciones.

### P1 — El enriquecimiento rompe la normalización monetaria

Ubicación: motoradar/cli.py:20–32 y 201–212; motoradar/enrich.py:148–159.

Se convierte a moneda base al recoger, pero fix_bait_prices puede sustituir importe y moneda después. No se vuelve a convertir antes de comparar con budget y calcular margen. Reproducción: una candidata de 6 BRL se corrigió a 200 USD y quedó sin normalizar.

Además, si FX.convert falla, _to_base continúa dejando moneda original: después se compara el número contra un presupuesto de otra moneda. Las etiquetas de margen y presupuesto están fijadas a R$ aunque fx.base sea configurable. Los filtros remotos de MLB también toman números de cfg sin reconvertirlos a BRL.

Solución: representar precio original y normalizado por separado, incluir tasa, fecha y procedencia de FX y normalizar otra vez tras cada corrección. Ante moneda desconocida, separar el anuncio de las comparaciones. Convertir los límites enviados a cada fuente a su moneda o realizar un filtrado local seguro.

Aceptación: casos BRL/UYU/USD con moneda base variable, descripción que cambia moneda, tasa inválida, proveedor caído y precio desconocido. Mostrar cuándo se usa una tasa de respaldo.

### P1 — Referencias contaminadas y confianza excesiva

Ubicación: motoradar/market.py:73–97, 52–54 y 141–146; motoradar/cli.py:179.

build_references acepta anuncios que buscan comprar, publicidad y vehículos con riesgo documental. Se clasifica la muestra principalmente por título antes de enriquecerla. La ausencia de una expresión de avería convierte por defecto el estado en runner; eso expresa falta de información, no evidencia de que ande.

Las referencias no deduplican la muestra. Facebook y Mercado Livre pueden devolver el mismo anuncio en varias consultas, aumentando artificialmente n y alterando la mediana. Hay familias que mezclan vehículos diferentes: MT-03/MT-09, XTZ 125/150 y Ninja de distintas cilindradas. El año extraído no participa en la referencia.

Economics.min_samples no controla el cálculo: Reference.solid fija tres y evaluate calcula margen incluso con una sola muestra. La reproducción calculó margen con n=2 y min_samples=10.

Solución: definir elegibilidad de comparables, deduplicar por identidad y separar marca, modelo, cilindrada, variante, año, región y estado documental. Introducir estado desconocido. Construir referencias históricas con antigüedad, dispersión y evidencia. Cuando falten comparables, mostrar SIN TASAR o un rango exploratorio explícito.

Aceptación: solicitudes de compra y muestras incompatibles no afectan la mediana; min_samples tiene efecto real; duplicados no aumentan n; una familia amplia no produce tasación específica sin advertencia.

Los precios publicados son precios pedidos. La precisión de reventa debe validarse con operaciones reales; una mediana de anuncios no demuestra un precio de cierre.

### P1 — OLX corta páginas con resultados repetidos

Ubicación: motoradar/sources/olx.py:85–94 y 125–134.

fetch interpreta una lista vacía después de deduplicar como fin de resultados. Una página completa de anuncios ya recogidos por otra consulta puede preceder a una página con anuncios nuevos.

Reproducción con dos consultas: la segunda tenía un anuncio repetido en página 1 y un anuncio nuevo en página 2. Esa segunda página nunca se consultó.

Solución: separar cantidad de tarjetas recibidas, cantidad parseada y cantidad nueva. Terminar por ausencia real de resultados o evidencia de fin de paginación, no por cantidad nueva igual a cero.

Aceptación: fixture con página duplicada intermedia seguido de anuncios nuevos; todos los nuevos se recogen dentro del límite de páginas.

### P1 — Entorno no sustituye secretos del YAML

Ubicación: motoradar/config.py:53–55; motoradar/sources/mercadolivre.py:26–27.

setdefault no sustituye una clave presente aunque tenga cadena vacía. La configuración de ejemplo incluye esas claves, por lo que TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID no ganan como promete el comentario. En Mercado Livre un valor YAML no vacío tiene precedencia sobre el entorno.

Solución: definir y aplicar una única política de precedencia, validar valores y mantener secretos fuera del ejemplo y de logs.

Aceptación: variables presentes sustituyen valores vacíos y no vacíos según la política documentada; nunca imprimir credenciales en errores.

### P2 — Persistencia y operación frágiles

- store.py:57 corta el texto JSON serializado a 20.000 caracteres. La reproducción dejó JSON inválido. Limitar campos antes de serializar o guardar el payload completo bajo una política de retención.
- store.py hace commit por anuncio. Agrupar transacciones y medir rendimiento antes de ampliar volumen. El índice de fingerprint no evita carreras del SELECT seguido de INSERT entre procesos.
- collect puede imprimir error de todas las fuentes y main devolver cero. Separar estados de corrida completa, parcial y fallida; persistir el resultado y establecer códigos de salida útiles.
- enrich captura errores sin contexto. Algunos continue omiten la pausa, por lo que el ritmo más rápido se produce precisamente en páginas fallidas. Centralizar políticas de pausa, errores y reintentos.
- watch duerme fuera del bloque que captura KeyboardInterrupt y calcula el periodo como duración de trabajo + intervalo. Validar intervalos positivos, manejar señales y elegir explícitamente cadencia por inicio o por final.
- --budget 0 se ignora por `if args.budget`; --top negativo provoca cortes de lista inesperados; --source desconocida puede ejecutar sin fuentes. Validar todos los argumentos antes de tocar red o disco.
- El filtro de ciudad busca en título, ubicación y raw completo. Un anuncio situado en Porto Alegre que menciona Jaguarao pasó el filtro local. Ubicación debe ser un dato estructurado con confianza y procedencia. Para grupos, usar región configurada del grupo como señal aproximada, no como ubicación verificada del vehículo.
- appraise puede interpretar cualquier texto con `projeto` como moto si no detecta previamente un auto; se reprodujo con `Vendo projeto de panificadora`. Separar evidencia de vehículo de evidencia de condición y gestionar negaciones.
- Los warnings de evaluate no se imprimen todos en deals. Mostrar precio señuelo, muestras insuficientes y procedencia dudosa de forma consistente, sin ocultar su incertidumbre.
- run/deals/watch tienen comportamientos distintos: deals no persiste ni exporta ni avisa por Telegram, watch llama a run y no aplica el análisis de deals. Compartir un servicio de búsqueda con modos explícitos y adaptar la documentación.
- Sesiones HTTP sin cierre explícito y dependencia de helpers privados de Facebook en enrich dificultan mantenimiento. Inyectar clientes y ofrecer una interfaz de detalle en el adaptador.

### P2 — Integraciones y documentación necesitan contratos verificables

Mercado Livre implementa client_credentials. La documentación oficial consultada describe autorización mediante authorization_code y renovación con refresh_token. Esto exige revisar el flujo elegido y los permisos de búsqueda; no se probó el endpoint con credenciales y no se afirma aquí su respuesta actual.

Fuente: https://developers.mercadolivre.com.br/en_us/search-products-seller/authentication-and-authorization

No hay paginación de Mercado Livre y stop_time se almacena como posted_at, mezclando fin de publicación con fecha de publicación. Facebook Marketplace solo busca líneas R$ al extraer precio. Revisar estos contratos con respuestas de ejemplo sanitizadas y pruebas opcionales de integración.

El README afirma Facebook desactivado por defecto, pero config.example.yaml tiene enabled: true. También muestra exclude_keywords con sucata y procuro aunque el texto recomienda conservar sucata y el clasificador puede resolver solicitudes tras leer la descripción. Alinear documentación y comportamiento, y fechar afirmaciones sobre proveedores.

## Arquitectura objetivo

Mantener un paquete desplegable con estas responsabilidades:

1. Adaptadores: buscar, obtener detalle, devolver errores clasificados y metadatos de cobertura.
2. Normalización: identidad, ubicación, importes y procedencia.
3. Persistencia: estado actual, observaciones e ingestiones idempotentes.
4. Clasificación: señales explícitas, estado desconocido y versión de reglas.
5. Referencias: comparables históricos y controles de calidad.
6. Búsqueda: preferencias y presupuesto, con razones de selección o descarte.
7. Entregas: bandeja persistente, suscripciones y reintentos.
8. Interfaces: CLI inicialmente; API/UI cuando haya necesidad de uso remoto.

Flujo: recoger → normalizar → persistir observación → seleccionar candidatas → enriquecer → volver a normalizar → clasificar → evaluar → persistir evento y entrega → enviar.

La calibración de referencias debe usar su propio conjunto elegible. Enriquecer una muestra acotada de comparables cuando el título no ofrece evidencia suficiente, sin abrir todas las páginas en cada corrida.

Tablas sugeridas: listings, listing_observations, collection_runs, source_runs, valuations, notification_deliveries y schema_migrations. Añadir subscriptions y tenant_id únicamente si se ofrece a varios usuarios; acordar retención de payloads y separar secretos/perfiles del contenido comercial.

## Plan de implementación

Estimaciones orientativas para una persona que conoce el proyecto; no son compromisos. Dependen de acceso a integraciones y del corpus disponible.

### Etapa 1 — Corrección esencial, 3–5 días

Orden de trabajo:

1. Capturar las reproducciones como pruebas de regresión.
2. Corregir parser y contratos de dinero; volver a normalizar tras detalle y rechazar comparaciones incompatibles.
3. Corregir precedencia de configuración y validación de CLI.
4. Actualizar por uid, conservar JSON válido y evitar fusión destructiva por fingerprint.
5. Implementar entregas persistentes y confirmación individual.
6. Corregir terminación de paginación y propagar errores de corrida.

Entregable: primera versión que no pierde alertas por fallos conocidos y no inventa importes por truncamiento.

Puerta de salida: todos los casos reproducidos pasan; un reinicio con mensajes pendientes los recupera; dos vendedores distintos no se fusionan. Las pruebas no necesitan cuentas reales.

### Etapa 2 — Mantenibilidad y operación, 1 semana

1. Extraer el servicio de búsqueda de cli.py y unificar run/deals/watch alrededor de resultados estructurados.
2. Tipar configuración de fuentes, FX y economía; detectar opciones desconocidas y tipos inválidos.
3. Introducir migraciones versionadas, transacciones por lote, índices medidos, cierre de recursos y backups recuperables.
4. Añadir logs estructurados con run_id, fuente, duración, cantidades y razones de descarte; redactar secretos.
5. Crear pyproject.toml, versión mínima de Python, dependencias reproducibles y extras opcionales de navegador.
6. CI con lint, comprobación de tipos gradual y regresiones de dominio/fixtures.
7. Alinear README, config de ejemplo y salida monetaria. Añadir guía para recuperar base y sesión.

Puerta de salida: instalación reproducible en entorno limpio; restauración de backup demostrada; errores de todas las fuentes generan estado fallido distinguible de cero anuncios.

### Etapa 3 — Calidad de clasificación y tasación, 1–2 semanas

1. Construir un corpus sanitizado y etiquetado: moto, pieza, auto, desconocido, solicitud, publicidad, condición y moneda.
2. Reservar un conjunto de evaluación que no se use para ajustar reglas. No convertir anécdotas del README en supuesta validación general.
3. Separar vehículo/condición/intención, manejar negaciones y exigir evidencia para runner.
4. Dividir variantes relevantes y utilizar año/región/documentación en selección de comparables.
5. Deduplicar referencias, aplicar min_samples y mostrar n, antigüedad y dispersión junto al margen.
6. Ofrecer escenarios conservador/base/optimista de costes, incluyendo transporte, mano de obra, comisiones y capital inmovilizado si corresponden al negocio.
7. Registrar compras, reparaciones y ventas reales para calibrar costes y precio de cierre.

Puerta de salida: objetivos de precisión y recall acordados y medidos por clase/fuente. Objetivo inicial sugerido: al menos 95% de precisión al presentar motos como enteras en el conjunto reservado, acompañado de recall y tamaño de muestra. No afirmar alcanzarlo sin evaluación.

### Etapa 4 — Escalado en una máquina, 1 semana

1. Medir una línea base de duración, consumo, solicitudes, bloqueos y tamaño de base en una jornada representativa.
2. Cachear detalles por identidad, versión y antigüedad; actualizar solo cuando haga falta.
3. Ejecutar trabajos HTTP independientes con concurrencia acotada, empezando conservadoramente y ajustando por dominio. Evitar consultas remotas duplicadas entre búsquedas equivalentes.
4. Mantener un único dueño por perfil de Facebook. Serializar acceso y limitar tiempo/volumen por corrida.
5. Aplicar espera progresiva con variación aleatoria en fallos transitorios; ante checkpoint o bloqueo, suspender esa fuente y comunicar su estado.
6. SQLite con transacciones cortas, busy_timeout y WAL si el patrón de acceso lo permite. Mantener la base local y medir contención.
7. Ejecutar bajo un supervisor o programador de tareas con reinicio controlado y evitar corridas superpuestas.

SQLite WAL facilita concurrencia entre lectura y escritura, pero sigue permitiendo un solo escritor a la vez y exige procesos en el mismo host. Fuente oficial: https://www.sqlite.org/wal.html

Puerta de salida: la duración p95 deja margen respecto a la cadencia deseada, los fallos de fuente son visibles y el backlog de entregas se recupera sin crecimiento sostenido. No fijar capacidad en anuncios/día antes de medir.

### Etapa 5 — Varios usuarios o máquinas, 2–4 semanas adicionales

Activar esta etapa si hay necesidad real de escritores simultáneos, recolección en varios hosts, búsquedas compartidas o acceso remoto de varios usuarios.

1. Migrar a PostgreSQL con ensayo, validación de datos y plan de reversión; mantener contratos de repositorio.
2. Separar proceso de recolección, procesamiento y envío con una cola persistente cuando el backlog y los reinicios lo requieran. Toda tarea debe poder repetirse sin multiplicar eventos.
3. Añadir suscripciones: recolectar una vez por región/fuente y distribuir por preferencias de usuarios, evitando un navegador por usuario para el mismo corpus.
4. Añadir API autenticada, autorización por usuario, cuotas, auditoría y UI de búsqueda/histórico.
5. Aislar perfiles de navegador por cuenta y asegurar un único worker propietario de cada perfil.
6. Probar migraciones, aislamiento de usuarios, fallos de workers y capacidad con datos sintéticos y fixtures; validar integraciones reales de forma controlada.

Puerta de salida: objetivos de servicio medidos, permisos entre usuarios verificados y recuperación operacional demostrada. No añadir microservicios o Kubernetes solamente por crecimiento esperado.

## Métricas para decidir inversiones

| Métrica | Qué permite decidir |
|---|---|
| Éxito y cobertura por fuente, incluyendo resultados parciales | Distinguir caída de proveedor de falta de oportunidades |
| Duración p50/p95 por etapa y corrida | Concurrencia, caché y frecuencia |
| Fracción de precios/monedas desconocidos o corregidos | Prioridad del parser y enriquecimiento |
| Precisión/recall por clase en corpus reservado | Cambios del clasificador y confianza de presentación |
| Comparables únicos, dispersión y antigüedad | Si una referencia merece tasación |
| Tiempo evento → entrega y antigüedad del pendiente más viejo | Salud de alertas y necesidad de workers |
| Cambios de precio y falsos duplicados revisados | Calidad de identidad/histórico |
| Lock waits, tamaño de DB y coste de transacción | Optimización de SQLite o migración |
| Margen estimado frente a margen real y días hasta venta | Calibración económica |

Los objetivos deben partir de una línea base. Para el uso personal, corrección monetaria, entregas recuperables y visibilidad de fuentes tienen más valor inmediato que una API o una interfaz nueva.
