---
name: motoradar-auditoria
description: Coordina una auditoría crítica completa o por áreas de Motoradar, aplica QA offline, verifica hallazgos de especialistas y entrega prioridades y alternativas con evidencia; usar cuando se pida crítica integral del proyecto.
---

# Auditoría crítica de Motoradar

## Contrato de revisión
Esta skill es específica de Motoradar. Trabaja sobre el repositorio indicado o el directorio actual; si no está disponible, pide su ubicación y no inventes una revisión. Lee AGENTS.md, README.md, docs/OBJETIVO.md, docs/ARQUITECTURA.md, docs/ROADMAP.md y CONTRIBUTING.md. La documentación define intención; el código y las pruebas aportan evidencia.

Registra commit y cambios locales. Conserva el trabajo existente. Por defecto audita sin cambiar comportamiento: una solicitud adicional de corrección autoriza los arreglos dentro de su alcance. No consultes configuración personal, grupos, tokens, perfiles ni bases reales. Usa ejemplos, mocks, FX offline y SQLite temporal; no abras navegadores ni hagas peticiones o envíos reales.

Para cada hallazgo aporta archivo:línea actual, entrada o secuencia, resultado esperado y observado, impacto, confianza, solución y prueba de aceptación. Distingue reproducción ejecutada, traza estática, riesgo condicionado e hipótesis. Usa P0 para daño crítico inmediato, P1 para comportamiento incorrecto prioritario, P2 para robustez o deuda con coste demostrado y P3 para mejoras menores; justifica por impacto y alcance. No fuerces hallazgos ni elogios, cuotas, notas 0–10 o porcentajes sin medir.

Preserva motos hasta R$1.000 inclusive, averiadas/proyectos/doadoras y sin papeles; margen/tasación no filtran. Precio incierto se etiqueta por confirmar. Identidad fuente/id y conversión antes de comparación. Reporta archivos revisados, pruebas realmente ejecutadas y límites. Una suite verde no prueba integración real.


## Preparación y especialistas
Lee [references/protocolo.md](references/protocolo.md) para la matriz de áreas, confianza, QA y formato del informe. Para una revisión integral cubre todos los módulos productivos, tests, CI y ejemplos, incluyendo los cambios locales; no presupongas que HEAD refleja el código auditado.

Las áreas están implementadas como skills hermanas: motoradar-dominio, motoradar-fuentes, motoradar-persistencia, motoradar-operacion, motoradar-qa, motoradar-seguridad y motoradar-arquitectura. Lee la SKILL.md de cada área aplicable. Son instrucciones reutilizables; sus agents/openai.yaml son metadatos de UI, no procesos permanentes.

Si el usuario solicita agentes/delegación o las instrucciones aplicables lo autorizan, divide tareas independientes de solo lectura y respeta los slots disponibles. Pasa contratos, alcance, prohibición de efectos externos y formato de evidencia. Si delegación no está autorizada o disponible, recorre las mismas áreas tú mismo. No necesitas siete procesos simultáneos ni debes omitir un área porque faltan slots.

## Consolidación
Comprueba ubicaciones actuales y vuelve a ejecutar las reproducciones prioritarias cuando sea barato. Las conclusiones de otro agente no son evidencia suficiente por sí solas. Fusiona duplicados, corrige severidad y descarta lo que no se sostenga. Verifica que una propuesta no excluye motos baratas reales ni contradice contratos.

Entrega un informe en docs/auditorias con fecha y sufijo único si ya existe: alcance, estado auditado, veredicto, hallazgos comprobados, riesgos separados, recomendaciones y pruebas de aceptación, QA y límites. No reemplaces informes previos. Auditar no autoriza implementar recomendaciones; si el usuario también pide corregir, ejecuta los cambios autorizados y revalida.

