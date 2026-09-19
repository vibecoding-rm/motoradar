---
name: motoradar-operacion
description: Audita CLI, configuración, Telegram, CSV y vigilancia de Motoradar con mocks; usar para QA de comandos, fallos, reintentos y experiencia operativa.
---

# CLI, entrega y operación de Motoradar

## Contrato de revisión
Esta skill es específica de Motoradar. Trabaja sobre el repositorio indicado o el directorio actual; si no está disponible, pide su ubicación y no inventes una revisión. Lee AGENTS.md, README.md, docs/OBJETIVO.md, docs/ARQUITECTURA.md, docs/ROADMAP.md y CONTRIBUTING.md. La documentación define intención; el código y las pruebas aportan evidencia.

Registra commit y cambios locales. Conserva el trabajo existente. Por defecto audita sin cambiar comportamiento: una solicitud adicional de corrección autoriza los arreglos dentro de su alcance. No consultes configuración personal, grupos, tokens, perfiles ni bases reales. Usa ejemplos, mocks, FX offline y SQLite temporal; no abras navegadores ni hagas peticiones o envíos reales.

Para cada hallazgo aporta archivo:línea actual, entrada o secuencia, resultado esperado y observado, impacto, confianza, solución y prueba de aceptación. Distingue reproducción ejecutada, traza estática, riesgo condicionado e hipótesis. Usa P0 para daño crítico inmediato, P1 para comportamiento incorrecto prioritario, P2 para robustez o deuda con coste demostrado y P3 para mejoras menores; justifica por impacto y alcance. No fuerces hallazgos ni elogios, cuotas, notas 0–10 o porcentajes sin medir.

Preserva motos hasta R$1.000 inclusive, averiadas/proyectos/doadoras y sin papeles; margen/tasación no filtran. Precio incierto se etiqueta por confirmar. Identidad fuente/id y conversión antes de comparación. Reporta archivos revisados, pruebas realmente ejecutadas y límites. Una suite verde no prueba integración real.

## Área
Lee cli.py, config.py, notify.py, __main__.py, config.example.yaml, .env.example y docs/OPERACION.md. No ejecutes run/watch/retry/status/login contra configuración real.

Con mocks comprueba petición Telegram, HTML escapado, ok=true, 429/retry_after, respuestas JSON de forma inesperada, timeout tras aceptación y rechazo permanente. No confundas tests que parchean send_telegram con validación del propio transporte. Revisa aislamiento de una entrega corrupta y redacción de errores sin token.

Comprueba tipos YAML y CLI, listas frente a strings, booleanos, infinito/NaN, moneda base, claves desconocidas, precedencia del entorno vacío y rutas. Valida opciones antes de configuración/red; prueba init existente y directorio faltante. Identifica si los errores devuelven un código útil.

Traza orden persistencia→entrega→consola/CSV: un fallo de salida no debería bloquear lo que ya puede enviarse. Comprueba etiqueta incierta en las tres salidas, precisión del importe y columnas antiguas. Para Excel, reproduce fórmula como contenido literal sin abrirlo.

En watch revisa cadencia real, Ctrl+C, duración de lote y fallo continuado. Para doctor distingue configuración presente, salud local y sesión validada; propone edad de pendientes/última corrida y logs útiles antes de infraestructura nueva.

