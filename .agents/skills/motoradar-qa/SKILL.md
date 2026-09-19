---
name: motoradar-qa
description: Evalúa pruebas y CI de Motoradar contra reglas de producto e invariantes, ejecuta QA offline y propone regresiones concretas; usar para crítica de calidad y brechas de validación.
---

# QA y regresiones de Motoradar

## Contrato de revisión
Esta skill es específica de Motoradar. Trabaja sobre el repositorio indicado o el directorio actual; si no está disponible, pide su ubicación y no inventes una revisión. Lee AGENTS.md, README.md, docs/OBJETIVO.md, docs/ARQUITECTURA.md, docs/ROADMAP.md y CONTRIBUTING.md. La documentación define intención; el código y las pruebas aportan evidencia.

Registra commit y cambios locales. Conserva el trabajo existente. Por defecto audita sin cambiar comportamiento: una solicitud adicional de corrección autoriza los arreglos dentro de su alcance. No consultes configuración personal, grupos, tokens, perfiles ni bases reales. Usa ejemplos, mocks, FX offline y SQLite temporal; no abras navegadores ni hagas peticiones o envíos reales.

Para cada hallazgo aporta archivo:línea actual, entrada o secuencia, resultado esperado y observado, impacto, confianza, solución y prueba de aceptación. Distingue reproducción ejecutada, traza estática, riesgo condicionado e hipótesis. Usa P0 para daño crítico inmediato, P1 para comportamiento incorrecto prioritario, P2 para robustez o deuda con coste demostrado y P3 para mejoras menores; justifica por impacto y alcance. No fuerces hallazgos ni elogios, cuotas, notas 0–10 o porcentajes sin medir.

Preserva motos hasta R$1.000 inclusive, averiadas/proyectos/doadoras y sin papeles; margen/tasación no filtran. Precio incierto se etiqueta por confirmar. Identidad fuente/id y conversión antes de comparación. Reporta archivos revisados, pruebas realmente ejecutadas y límites. Una suite verde no prueba integración real.

## Área
Lee tests completos, .github/workflows/tests.yml y los módulos necesarios para interpretar asserts. Inspecciona efectos externos antes de ejecutar; no instales dependencias ni herramientas si no son necesarias para esta revisión.

Ejecuta python -m unittest discover -s tests -v y python -m compileall -q motoradar tests; informa intérprete, número, fallos y código de salida. Puedes reutilizar una ejecución real de esta revisión si código, tests y entorno no cambiaron; registra esa condición. Si no puedes ejecutarlos, explica el impedimento. Usa coverage solo si disponible; no publiques porcentajes estimados como mediciones.

Construye trazabilidad entre casos de docs/OBJETIVO.md, eventos y los ocho invariantes arquitectónicos y nombres exactos de pruebas. Directa significa que un assert observa el comportamiento; parcial cubre solo una variante, fuente o canal; ausente significa que ningún assert lo verifica. Clasifica por comportamiento, no por semejanza del nombre. Detecta asserts que prueban cantidad sin identidad/estado, mocks de la función que se pretende validar y pruebas que fallan por un motivo accidental.

Si aporta evidencia, haz mutaciones pequeñas en copia temporal aislada y verifica que el original queda intacto. Registra cambio exacto, tests seleccionados y si el mutante sobrevive; supervivencia en un subset no prueba supervivencia en toda la suite.

Propón primero regresiones para fallos reproducidos, con entrada/estado y aserción concreta. Separa fixture fabricado de muestra real sanitizada, tests offline de integración autorizada y CI definido de CI ejecutado. No exijas herramientas nuevas por moda ni conviertas cantidad de tests en calidad.
