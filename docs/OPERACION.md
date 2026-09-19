# Instalación, configuración y operación

## Entorno probado

Python 3.12 en Windows. Hay workflow para Windows y Linux, pero GitHub Actions
y Linux no se ejecutaron en la máquina donde se preparó esta versión.

Desde la raíz del proyecto, en PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m motoradar init
.\.venv\Scripts\python.exe -m motoradar doctor
```

`init` no sobrescribe config.yaml si existe. requirements incluye patchright;
instalar su navegador es un paso separado solo si se va a usar Facebook:

```powershell
.\.venv\Scripts\patchright.exe install chromium
.\.venv\Scripts\python.exe -m motoradar login
```

## Configuración

config.example.yaml es el ejemplo versionado. config.yaml, grupos.json, sesiones
y data/ son locales y están ignorados por Git. Ejecutar desde la raíz: las rutas
relativas de datos se resuelven desde el directorio de trabajo, no desde el YAML.

- `budget: 1000` y `fx.base: BRL`: criterio principal, inclusive.
- `filters.queries` y `cities`: cobertura deseada. No excluir sucata/pecas por ser doadoras.
- `monitoring.enrich_details`: detalle previo a selección final; por defecto true.
- `monitoring.alert_unconfirmed`: aviso separado de precio incierto; por defecto true.
- `monitoring.health_zero_runs`: pasadas seguidas sin observar nada antes de
  avisar por Telegram; por defecto 4.
- `monitoring.session_pause_cycles`: pasadas que `watch` deja pausada una fuente
  cuya sesión cayó, en lugar de reintentar contra el mismo checkpoint; por defecto 8.
- `monitoring.retention_days`: plazo de borrado del histórico (observaciones,
  anuncios sin entrega y corridas); por defecto 90, `0` desactiva el borrado.
  El feed de grupos captura publicaciones de vecinos que no venden nada: sin
  plazo, la base crece como un archivo de datos personales de terceros.
- `sources.<nombre>.enabled`: fuentes para corridas sin --source.
- `economics`: tasación complementaria para deals. No filtra run/watch.
- `fx.rates`: tasas expresadas por USD; `offline` evita la consulta de tasas.
- `sources.facebook.group_mode`: `feed` (por defecto) lee el grupo en orden
  cronológico y clasifica en local; `search` usa el buscador del grupo; `both`
  hace las dos cosas. El buscador ordena por relevancia y solo devuelve lo que
  contiene la palabra buscada, así que por sí solo pierde frescura y avisos que
  no dicen «moto».
- `sources.facebook.marketplace_every_cycles`: en `watch`, Marketplace corre una
  de cada N pasadas. Los grupos siguen corriendo en todas.
- `sources.facebook.group_cities`: mapa id→lista de ciudades para región aproximada.
  Un grupo **sin entrada** queda marcado `region_unknown`: sus avisos pasan igual
  (no se pierden motos baratas) pero el filtro de ciudad no los respalda y tanto
  la consola como la alerta lo declaran.
- `sources.facebook.marketplace_queries` y `group_queries`: consultas separadas
  para no repetir en grupos toda la lista general. `group_queries` solo se usa
  con `group_mode` `search` o `both`.
- `group_items`, `group_budget_s`, `group_settle_s` y `navigation_timeout_ms`:
  límites de cada recorrido; Facebook informa progreso por fase y grupo.
- `detail_limit`, `detail_timeout_ms` y `detail_pause`: techo y ritmo de lectura
  de detalle. Se priorizan precios plausibles antes de tarjetas R$0/R$10; lo
  omitido conserva `detail_status` y no se presenta como detalle leído.

La carga valida tipos antes de consultar fuentes: listas YAML no pueden sustituirse
por strings y `enabled`, `offline` y opciones de monitoreo deben ser booleanos.

El ejemplo usa OLX activo y Facebook/Mercado Livre desactivados hasta configurarlos.
--source fuerza la fuente elegida aunque esté desactivada en YAML.

Telegram toma TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID del entorno con precedencia
sobre YAML. Se pueden completar los campos locales telegram.token/chat_id.
**El token no va en config.yaml.** Se lee de `TELEGRAM_BOT_TOKEN` o de
`~/.motoradar/telegram_token` (una línea, fuera del árbol del proyecto), y un
token escrito en el YAML se rechaza al arrancar: cualquier búsqueda de texto
sobre el proyecto lo expone sin abrir el archivo. `doctor` enmascara los
destinatarios (`...0259`) porque es el comando que uno pega cuando pide ayuda.

El `chat_id` se obtiene enviando cualquier mensaje al bot y leyendo después
`https://api.telegram.org/bot<token>/getUpdates`: el valor está en
`result[].message.chat.id`. Es un número, y para un chat privado es el propio
usuario. Telegram no permite que un bot escriba primero: **cada destinatario
tiene que hablarle al bot al menos una vez** antes de poder recibir alertas.

`chat_id` acepta un id suelto o una lista de ids. Con varios destinatarios cada
uno recibe en su chat privado y mantiene su propia cola: intentos, `retry_at` y
la pausa por HTTP 429 son por destinatario, así que un fallo de uno no retrasa
las alertas del otro. Por entorno, `TELEGRAM_CHAT_ID` admite ids separados por
coma. `doctor` lista los destinatarios y los pendientes de cada uno.
No versionar el token: config.yaml y .env están ignorados por Git.
La plantilla [.env.example](../.env.example) enumera variables: **el programa no
carga .env automáticamente**; el entorno o launcher debe exportarlas.

## Comandos

```powershell
python -m motoradar doctor
python -m motoradar run --source olx --budget 1000 --dry-run
python -m motoradar run --source olx --budget 1000
python -m motoradar watch --source olx --budget 1000 --interval 15
python -m motoradar retry
python -m motoradar explain --limit 40
python -m motoradar deals --source olx --budget 1000 --top 20
python -m motoradar -c config.local.yaml doctor
```

- doctor consulta estado local sin scraping ni envío. Devuelve **código 1**
  cuando hay algo que atender (entregas abandonadas), para poder usarlo en una
  tarea programada; imprime el pendiente más antiguo con sus intentos y su
  último error, y enmascara los destinatarios.
- dry-run consulta fuentes, pero no guarda anuncios, exporta CSV ni envía.
- run busca una vez y procesa eventos/pendientes.
- watch repite run, con pausa en minutos después del trabajo; Ctrl+C detiene.
  El intervalo por defecto es 15 minutos: en los grupos de pueblo una moto barata
  se vende en menos de una hora. Si una fuente pierde la sesión, watch la pausa
  y avisa en vez de reintentar cada intervalo contra el mismo checkpoint.
- explain muestra, leyendo solo la base, qué vio el radar y por qué cada aviso
  llegó o se cayó: origen, precio, clase, veredicto y causa. `--solo-descartados`
  aísla lo que no llegó. Es la herramienta para afinar con datos.
- retry procesa hasta 50 pendientes vencidos del destinatario configurado, sin scraping.
- deals es una vista de consola con referencias opcionales; no exporta ni envía.
- --no-enrich evita lectura de detalle; puede reducir precisión.

Un dry-run con Facebook seleccionado puede abrir un navegador. No ejecutar dos
corridas con el mismo perfil Facebook aunque usen bases diferentes.

## Recuperación

| Síntoma | Acción |
|---|---|
| Telegram sin configurar | Completar entorno/YAML y consultar doctor |
| Entregas pendientes | Revisar conectividad/configuración; retry respeta retry_at |
| HTTP 429 | Dejar vencer el aplazamiento; no borrar estados para forzar envíos |
| Facebook expira | watch pausa la fuente y avisa; renovar con login y verificar status |
| Fuente ciega (DOM) | La corrida informa `error` y `N con DOM no reconocido`: el HTML cambió y cero resultados NO significa que no haya ofertas. Revisar selectores en sources/facebook.py |
| Silencio sostenido | Tras `monitoring.health_zero_runs` pasadas sin observar nada llega un aviso de salud; doctor lista los avisos activos |
| Fuentes fallan | Consultar resultado de corrida; run devuelve error si ninguna disponible |
| Un socio no recibe nada | Si bloqueó el bot o el chat no existe, Telegram responde 403/400: la entrega se marca `dead` y `doctor` la lista como abandonada. Que le escriba al bot y volver a encolar |
| watch no avisa de nada | Devuelve código 2 sin fuentes habilitadas, 1 si ninguna pasada funcionó, y manda un aviso de salud tras 3 pasadas fallidas seguidas |
| Otra corrida posee la base | Detener el proceso duplicado; el lock se libera al cerrar el propietario |
| Sin oportunidades | Distinguir cero candidatos de error/cobertura parcial; revisar zona y queries |

La ausencia de resultados no demuestra ausencia de motos baratas. Fuente caída,
ubicación aproximada, clasificación y ventana de paginación afectan cobertura.
Las corridas guardan por fuente estado `ok`, `partial` o `error`, contadores,
`dom_failures`, `session_expired` y el diagnóstico; `deals` devuelve error si
todas las fuentes fallaron. Una fuente que no reconoce el HTML nunca se informa
como `ok` con cero resultados.

## Backup y restauración

Para respaldo con la base abierta usar la API de backup de SQLite. Ajustar rutas
a la configuración local; el ejemplo crea un respaldo nuevo con nombre fechado:

```python
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

Path("data/backups").mkdir(parents=True, exist_ok=True)
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
with sqlite3.connect("data/listings.sqlite3") as source:
    with sqlite3.connect(f"data/backups/listings-{stamp}.sqlite3") as backup:
        source.backup(backup)
```

Para restaurar, detener todos los procesos, preservar la base actual y sus WAL/SHM
como respaldo, restaurar a una ubicación nueva, comprobar PRAGMA integrity_check
y ejecutar doctor con configuración que apunte a la copia. Ensayar antes de
cambiar la base utilizada por vigilancia. Tokens y perfiles se respaldan aparte.

## Estado comprobado

146 pruebas offline locales aprobadas y lectura OLX acotada que parseó dos anuncios sin
candidatos de la zona probada. Telegram quedó configurado con bot propio y dos
destinatarios, con entrega real confirmada a ambos el 18/09/2026; el reintento
ante un fallo real y el volumen sostenido siguen sin probarse. Facebook tiene lectura real
acotada del 17/09 y feed cronológico desde el 18/09, sin medición de cobertura.
Mercado Livre sigue sin validar. Ver [ROADMAP.md](ROADMAP.md).
