# Motoradar

Radar personal de **motos de hasta R$1.000 inclusive** en Jaguarão y alrededores,
para comprar, reparar o armar y luego revender.

Interesan motos andando, averiadas, proyectos y doadoras. **Papeles y margen
estimado no filtran las alertas.** Precio desconocido o señuelo se presenta como
**PRECIO POR CONFIRMAR**, sin asumir que sea una compra dentro del presupuesto.
Repuestos sueltos, autos, teléfonos, publicidad y pedidos de compra se descartan.

## Empezar

Entorno probado: Python 3.12 en Windows. Desde la raíz del repositorio:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m motoradar init
.\.venv\Scripts\python.exe -m motoradar doctor
.\.venv\Scripts\python.exe -m motoradar run --source olx --budget 1000 --dry-run
```

Editar config.yaml con zona, búsquedas y Telegram. init no sobrescribe una
configuración existente. El ejemplo es genérico: solo OLX activo.

Para vigilar con el entorno activado y Telegram configurado:

```powershell
python -m motoradar watch --source olx --budget 1000 --interval 30
```

watch busca, clasifica, lee detalles y entrega eventos nuevos o bajadas de precio.
Las entregas fallidas quedan persistidas para reintentar. Ctrl+C detiene el bucle.
La pausa de 30 minutos se suma a la duración de cada búsqueda.

## Comandos

| Comando | Propósito |
|---|---|
| doctor | Diagnóstico local de configuración, corridas y pendientes |
| run | Una búsqueda con persistencia, CSV y Telegram configurado |
| run --dry-run | Consulta sin guardar anuncios, exportar ni enviar |
| watch | Vigilancia repetida con el mismo criterio de selección |
| retry | Entregas pendientes vencidas, sin otra búsqueda |
| deals | Vista de consola con tasación complementaria |
| login / status | Iniciar sesión propia de Facebook o comprobarla |
| init | Crear config.yaml a partir del ejemplo |

--source fuerza una fuente; --budget ajusta presupuesto; --no-enrich omite
detalles. -c/--config va antes del subcomando. dry-run puede abrir navegador
si se selecciona Facebook: para prueba acotada usar --source olx.

## Estado actual

| Componente | Evidencia |
|---|---|
| Dominio y persistencia | 31 pruebas locales aprobadas |
| OLX | Lectura real acotada: dos anuncios parseados; ninguno elegible en la zona probada |
| Telegram | Entrega/fallos probados con mocks; configuración y envío real pendientes |
| Facebook | Adaptador existente; validación de sesión y cobertura pendientes |
| Mercado Livre | Adaptador existente; OAuth/acceso y paginación pendientes |
| CI | Workflow Windows/Linux preparado; sin ejecución remota comprobada |

El radar no garantiza precio confirmado por el vendedor, disponibilidad,
condición ni cobertura completa. No se inició vigilancia permanente al preparar
esta base. Las tasas de respaldo se identifican al ejecutar.

## Documentación

- [Objetivo real y reglas de selección](docs/OBJETIVO.md).
- [Instalación, configuración, operación y recuperación](docs/OPERACION.md).
- [Arquitectura, datos e invariantes](docs/ARQUITECTURA.md).
- [Mejoras priorizadas y criterios de escalamiento](docs/ROADMAP.md).
- [Guía de contribución](CONTRIBUTING.md) y [registro de cambios](CHANGELOG.md).
- [Investigación de proyectos y primera mejora](INVESTIGACION_Y_MEJORAS.md).
- [Auditoría inicial del código](REVISION_SENIOR.md).
- [Notas históricas, no contratos vigentes](docs/NOTAS_HISTORICAS.md).

## Verificación

```powershell
python -m unittest discover -s tests -v
python -m compileall -q motoradar tests
```

Las pruebas no requieren cuentas reales ni envían mensajes. config.yaml,
grupos.json, .env, data/, bases y perfiles de sesión están ignorados por Git.
El proyecto no carga .env automáticamente; [.env.example](.env.example) es una
referencia para configurar el entorno.
