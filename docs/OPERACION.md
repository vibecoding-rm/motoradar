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
- `sources.<nombre>.enabled`: fuentes para corridas sin --source.
- `economics`: tasación complementaria para deals. No filtra run/watch.
- `fx.rates`: tasas expresadas por USD; `offline` evita la consulta de tasas.
- `sources.facebook.group_cities`: mapa id→lista de ciudades para región aproximada.

El ejemplo usa OLX activo y Facebook/Mercado Livre desactivados hasta configurarlos.
--source fuerza la fuente elegida aunque esté desactivada en YAML.

Telegram toma TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID del entorno con precedencia
sobre YAML. Se pueden completar los campos locales telegram.token/chat_id.
La plantilla [.env.example](../.env.example) enumera variables: **el programa no
carga .env automáticamente**; el entorno o launcher debe exportarlas.

## Comandos

```powershell
python -m motoradar doctor
python -m motoradar run --source olx --budget 1000 --dry-run
python -m motoradar run --source olx --budget 1000
python -m motoradar watch --source olx --budget 1000 --interval 30
python -m motoradar retry
python -m motoradar deals --source olx --budget 1000 --top 20
python -m motoradar -c config.local.yaml doctor
```

- doctor consulta estado local sin scraping ni envío.
- dry-run consulta fuentes, pero no guarda anuncios, exporta CSV ni envía.
- run busca una vez y procesa eventos/pendientes.
- watch repite run, con pausa en minutos después del trabajo; Ctrl+C detiene.
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
| Facebook expira | Detener vigilancia, renovar con login y verificar status |
| Fuentes fallan | Consultar resultado de corrida; run devuelve error si ninguna disponible |
| Otra corrida posee la base | Detener el proceso duplicado; el lock se libera al cerrar el propietario |
| Sin oportunidades | Distinguir cero candidatos de error/cobertura parcial; revisar zona y queries |

La ausencia de resultados no demuestra ausencia de motos baratas. Fuente caída,
ubicación aproximada, clasificación y ventana de paginación afectan cobertura.

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

31 pruebas locales aprobadas y lectura OLX acotada que parseó dos anuncios sin
candidatos de la zona probada. Telegram estaba sin configurar; no hubo entrega
real ni validación de Facebook/Mercado Livre. Ver [ROADMAP.md](ROADMAP.md).
