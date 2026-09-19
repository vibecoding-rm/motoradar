---
name: motoradar-dominio
description: Audita precios, monedas, clasificación y selección de Motoradar con reproducciones offline y propuestas de dominio; usar para crítica o QA de estas reglas.
---

# Dominio y selección de Motoradar

## Contrato de revisión
Esta skill es específica de Motoradar. Trabaja sobre el repositorio indicado o el directorio actual; si no está disponible, pide su ubicación y no inventes una revisión. Lee AGENTS.md, README.md, docs/OBJETIVO.md, docs/ARQUITECTURA.md, docs/ROADMAP.md y CONTRIBUTING.md. La documentación define intención; el código y las pruebas aportan evidencia.

Registra commit y cambios locales. Conserva el trabajo existente. Por defecto audita sin cambiar comportamiento: una solicitud adicional de corrección autoriza los arreglos dentro de su alcance. No consultes configuración personal, grupos, tokens, perfiles ni bases reales. Usa ejemplos, mocks, FX offline y SQLite temporal; no abras navegadores ni hagas peticiones o envíos reales.

Para cada hallazgo aporta archivo:línea actual, entrada o secuencia, resultado esperado y observado, impacto, confianza, solución y prueba de aceptación. Distingue reproducción ejecutada, traza estática, riesgo condicionado e hipótesis. Usa P0 para daño crítico inmediato, P1 para comportamiento incorrecto prioritario, P2 para robustez o deuda con coste demostrado y P3 para mejoras menores; justifica por impacto y alcance. No fuerces hallazgos ni elogios, cuotas, notas 0–10 o porcentajes sin medir.

Preserva motos hasta R$1.000 inclusive, averiadas/proyectos/doadoras y sin papeles; margen/tasación no filtran. Precio incierto se etiqueta por confirmar. Identidad fuente/id y conversión antes de comparación. Reporta archivos revisados, pruebas realmente ejecutadas y límites. Una suite verde no prueba integración real.

## Área
Lee models.py, money.py, appraise.py, filters.py, pipeline.py, enrich.py y market.py junto a las pruebas pertinentes. Traza run/watch/deals hasta la selección final.

Comprueba límite inclusive y valores inválidos; múltiples importes (entrada, cuotas, precio antiguo y total), moneda explícita y ausente, teléfono/año/cc/km. Separa parse_price de campo estructurado y parse_price_text de texto libre; no atribuyas al primero un uso que el adaptador no hace. Tras corregir precio, comprueba renormalización y metadatos originales coherentes; compara señuelo en moneda base.

En clasificación diferencia objeto vendido, condición e intención: motor suelto versus moto con motor fundido, piezas para reparar versus doadora completa, pedido versus venta que acepta troca. No arregles falsos positivos eliminando proyectos válidos. Prueba motos sin modelo reconocido y marcas compartidas con autos. Revisa ciudad/región aproximada y filtros de texto sin metadatos internos.

La tasación es información: audita moneda, muestras y deduplicación de referencias sin convertirlas en filtros. Propón corpus por fuente/clase y conjunto reservado antes de cambiar heurísticas globales. Cada cambio propuesto lleva casos positivos y negativos, incluidos anuncios baratos legítimos.

