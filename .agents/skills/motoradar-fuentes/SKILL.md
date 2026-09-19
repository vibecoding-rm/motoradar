---
name: motoradar-fuentes
description: Audita adaptadores OLX, Facebook y Mercado Livre y detalle de Motoradar usando HTML/JSON simulados; usar para crítica de extracción, identidad y cobertura parcial.
---

# Fuentes y extracción de Motoradar

## Contrato de revisión
Esta skill es específica de Motoradar. Trabaja sobre el repositorio indicado o el directorio actual; si no está disponible, pide su ubicación y no inventes una revisión. Lee AGENTS.md, README.md, docs/OBJETIVO.md, docs/ARQUITECTURA.md, docs/ROADMAP.md y CONTRIBUTING.md. La documentación define intención; el código y las pruebas aportan evidencia.

Registra commit y cambios locales. Conserva el trabajo existente. Por defecto audita sin cambiar comportamiento: una solicitud adicional de corrección autoriza los arreglos dentro de su alcance. No consultes configuración personal, grupos, tokens, perfiles ni bases reales. Usa ejemplos, mocks, FX offline y SQLite temporal; no abras navegadores ni hagas peticiones o envíos reales.

Para cada hallazgo aporta archivo:línea actual, entrada o secuencia, resultado esperado y observado, impacto, confianza, solución y prueba de aceptación. Distingue reproducción ejecutada, traza estática, riesgo condicionado e hipótesis. Usa P0 para daño crítico inmediato, P1 para comportamiento incorrecto prioritario, P2 para robustez o deuda con coste demostrado y P3 para mejoras menores; justifica por impacto y alcance. No fuerces hallazgos ni elogios, cuotas, notas 0–10 o porcentajes sin medir.

Preserva motos hasta R$1.000 inclusive, averiadas/proyectos/doadoras y sin papeles; margen/tasación no filtran. Precio incierto se etiqueta por confirmar. Identidad fuente/id y conversión antes de comparación. Reporta archivos revisados, pruebas realmente ejecutadas y límites. Una suite verde no prueba integración real.

## Área
Lee sources/base.py, sources/__init__.py, sources/olx.py, sources/facebook.py, sources/mercadolivre.py, enrich.py y collect en cli.py.

Simula página válida, fin explícito, duplicados, HTML cambiado, HTTP 200 con bloqueo/login, timeout, 429 y fallo parcial. Traza hasta stats y código de salida: cero resultados no demuestra una fuente sana. No afirmes que un selector o parámetro sirve hoy por leer un comentario antiguo.

Comprueba identidad con tracking, URL alternativa, id ausente y edición de texto; separa paginación de deduplicación. Revisa límites locales/remotos y moneda que el proveedor espera. En Facebook usa objetos fake, nunca navegador/perfil real; comprueba cierre ante fallo de lanzamiento/contexto y detección de login/checkpoint en grupos y Marketplace.

La existencia del adaptador no valida acceso, OAuth o inventario. Si una conclusión requiere semántica actual de una API, verifica documentación oficial o déjala como hipótesis; eso no autoriza consultas a cuentas ni fuentes reales. Propón fixtures sanitizados con origen y fecha cuando existan; un HTML fabricado sirve para regresión, no para prometer cobertura. Da prioridad a distinguir página inválida de vacía y a conservar resultados parciales útiles.

