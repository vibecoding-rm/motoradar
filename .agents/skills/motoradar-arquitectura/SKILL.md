---
name: motoradar-arquitectura
description: Critica diseño, contratos, deuda y prioridades de Motoradar comparando código, pruebas y documentación; usar para propuestas de arquitectura proporcionadas a una herramienta personal.
---

# Arquitectura y propuestas de Motoradar

## Contrato de revisión
Esta skill es específica de Motoradar. Trabaja sobre el repositorio indicado o el directorio actual; si no está disponible, pide su ubicación y no inventes una revisión. Lee AGENTS.md, README.md, docs/OBJETIVO.md, docs/ARQUITECTURA.md, docs/ROADMAP.md y CONTRIBUTING.md. La documentación define intención; el código y las pruebas aportan evidencia.

Registra commit y cambios locales. Conserva el trabajo existente. Por defecto audita sin cambiar comportamiento: una solicitud adicional de corrección autoriza los arreglos dentro de su alcance. No consultes configuración personal, grupos, tokens, perfiles ni bases reales. Usa ejemplos, mocks, FX offline y SQLite temporal; no abras navegadores ni hagas peticiones o envíos reales.

Para cada hallazgo aporta archivo:línea actual, entrada o secuencia, resultado esperado y observado, impacto, confianza, solución y prueba de aceptación. Distingue reproducción ejecutada, traza estática, riesgo condicionado e hipótesis. Usa P0 para daño crítico inmediato, P1 para comportamiento incorrecto prioritario, P2 para robustez o deuda con coste demostrado y P3 para mejoras menores; justifica por impacto y alcance. No fuerces hallazgos ni elogios, cuotas, notas 0–10 o porcentajes sin medir.

Preserva motos hasta R$1.000 inclusive, averiadas/proyectos/doadoras y sin papeles; margen/tasación no filtran. Precio incierto se etiqueta por confirmar. Identidad fuente/id y conversión antes de comparación. Reporta archivos revisados, pruebas realmente ejecutadas y límites. Una suite verde no prueba integración real.

## Área
Revisa todos los módulos productivos, pruebas, configuración de ejemplo, CI y documentación vigente; consulta informes históricos solo como pistas que deben revalidarse. Enumera qué leíste y qué quedó fuera.

Traza dependencias y efectos reales de run/watch/deals/retry: selección en pipeline.py, decisión de eventos en store.py, normalización y construcción de mensajes. Señala divergencias observables; repartir una responsabilidad entre módulos no es por sí mismo un bug.

Examina claves de raw y significados por proveedor; propone campos tipados solo para los que participan en decisiones/contracts y conserva datos extra. Revisa acoplamiento del payload persistido a Listing y necesidad de versionarlo. Busca puntos concretos donde separar orquestación, decisiones puras y presentación mejora testabilidad o evita un fallo.

Contrasta afirmaciones documentales con código y pruebas, números de tests y estado de integraciones. No declares falta de cobertura porque falta una herramienta. Estima esfuerzo como rango con supuestos, dependencias, riesgo y prueba de aceptación; evita promesas de calendario o rendimiento.

Compara arreglo mínimo, refactor pequeño y opción de mayor coste cuando exista una decisión real. Prioriza descubrir motos válidas y detectar fuente ciega antes de paneles, catálogo o infraestructura distribuida. Si el diseño actual basta, dilo y justifica no cambiarlo.

