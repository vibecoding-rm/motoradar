# Especialistas de crítica y QA de Motoradar

Ocho skills reutilizables, versionadas en `.agents/skills/`: siete especialistas
y un coordinador. Cada carpeta contiene `SKILL.md` y metadatos de presentación
en `agents/openai.yaml`. Esos metadatos no crean procesos permanentes.

Las skills también se copian al directorio personal de skills de Codex para su
uso local. Las copias personales son una instalación de esta versión; no se
sincronizan automáticamente con futuros cambios del repositorio. Si una sesión
no las ofrece por nombre, indicar la ruta de su `SKILL.md` permite leer sus
instrucciones expresamente.

## Áreas

| Skill | Responsabilidad |
|---|---|
| `motoradar-auditoria` | Auditoría integral, coordinación, verificación y prioridades |
| `motoradar-dominio` | Precio, moneda, clasificación, filtros, selección y tasación |
| `motoradar-fuentes` | OLX, Facebook, Mercado Livre, detalle, identidad y cobertura |
| `motoradar-persistencia` | SQLite, migraciones, eventos, transacciones y locking |
| `motoradar-operacion` | CLI, YAML, Telegram, CSV, vigilancia y diagnóstico |
| `motoradar-qa` | Pruebas, trazabilidad, regresiones y CI |
| `motoradar-seguridad` | Datos externos, exportaciones, secretos, sesiones y dependencias |
| `motoradar-arquitectura` | Contratos entre módulos, deuda y propuestas proporcionadas |

## Uso

Para revisión completa:

```text
Usa $motoradar-auditoria para criticar todo Motoradar, aplicar QA y proponer
mejoras. Crea agentes para áreas independientes y verifica sus hallazgos.
```

Para una revisión concreta:

```text
Usa $motoradar-dominio para revisar parsing, monedas y clasificación.
Usa $motoradar-qa para contrastar las pruebas con el objetivo y los invariantes.
Usa $motoradar-arquitectura para comparar opciones de diseño y su coste.
```

También se puede pedir: «Lee `.agents/skills/motoradar-auditoria/SKILL.md` y
aplícalo al estado actual del proyecto». Si no se autoriza delegación o no hay
subagentes disponibles, el coordinador recorre las mismas áreas por sí mismo.

## Qué exige una crítica útil

Cada hallazgo identifica ubicación actual, entrada o secuencia, resultado
esperado y observado, impacto, confianza, solución y prueba de aceptación.
Se distingue reproducción ejecutada de traza estática, riesgo condicionado e
hipótesis. No se imponen notas numéricas, cuotas de hallazgos ni elogios de relleno.
La severidad se justifica por daño y alcance; un falso positivo aislado no se
convierte automáticamente en emergencia P0.

Las propuestas comparan arreglo mínimo y alternativas cuando existe una decisión
real, con esfuerzo estimado, riesgos y aceptación. Deben preservar las motos
baratas averiadas, proyectos y doadoras; papeles, margen y tasación no son filtros.

El QA usa mocks, datos ficticios, FX offline y SQLite temporal. No consulta
configuración privada, perfiles o bases reales ni abre navegadores, consulta
fuentes o envía mensajes. Un `dry-run` del radar sí puede consultar fuentes y
no sustituye esa restricción.

El coordinador conserva los informes anteriores y escribe otro archivo fechado
en `docs/auditorias/`. Auditar no implica corregir el código; si se pide además
implementar, se hacen los cambios autorizados con regresiones pertinentes.

## Primera aplicación y límites

La primera revisión de esta suite está en
[2026-09-16-codex.md](auditorias/2026-09-16-codex.md), con un script offline de
reproducciones. La skill de QA fue aplicada por un revisor independiente y se
ajustó para definir trazabilidad y evitar repetir verificaciones sin cambios.
La validación de formato de una skill no demuestra calidad de todas sus futuras
revisiones; la crítica sigue necesitando comprobar cada evidencia.

Los perfiles existentes de `.claude/` se conservan como trabajo previo. Esta
suite de Codex tiene su propio protocolo y no cambia esos archivos.
