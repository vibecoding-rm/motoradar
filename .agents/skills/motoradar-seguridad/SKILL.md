---
name: motoradar-seguridad
description: Audita límites de confianza, datos, exportaciones, secretos y dependencias de Motoradar sin consultar información privada; usar para revisión de seguridad y privacidad del proyecto.
---

# Seguridad y datos de Motoradar

## Contrato de revisión
Esta skill es específica de Motoradar. Trabaja sobre el repositorio indicado o el directorio actual; si no está disponible, pide su ubicación y no inventes una revisión. Lee AGENTS.md, README.md, docs/OBJETIVO.md, docs/ARQUITECTURA.md, docs/ROADMAP.md y CONTRIBUTING.md. La documentación define intención; el código y las pruebas aportan evidencia.

Registra commit y cambios locales. Conserva el trabajo existente. Por defecto audita sin cambiar comportamiento: una solicitud adicional de corrección autoriza los arreglos dentro de su alcance. No consultes configuración personal, grupos, tokens, perfiles ni bases reales. Usa ejemplos, mocks, FX offline y SQLite temporal; no abras navegadores ni hagas peticiones o envíos reales.

Para cada hallazgo aporta archivo:línea actual, entrada o secuencia, resultado esperado y observado, impacto, confianza, solución y prueba de aceptación. Distingue reproducción ejecutada, traza estática, riesgo condicionado e hipótesis. Usa P0 para daño crítico inmediato, P1 para comportamiento incorrecto prioritario, P2 para robustez o deuda con coste demostrado y P3 para mejoras menores; justifica por impacto y alcance. No fuerces hallazgos ni elogios, cuotas, notas 0–10 o porcentajes sin medir.

Preserva motos hasta R$1.000 inclusive, averiadas/proyectos/doadoras y sin papeles; margen/tasación no filtran. Precio incierto se etiqueta por confirmar. Identidad fuente/id y conversión antes de comparación. Reporta archivos revisados, pruebas realmente ejecutadas y límites. Una suite verde no prueba integración real.

## Área
Lee las rutas de entrada en sources/, enrich.py y config.py y sus salidas en notify.py, store.py y cli.py; también .gitignore, requirements.txt y workflow CI. No leas sesiones, entorno completo, configuración privada ni blobs que puedan contener secretos. Para inventario de Git usa nombres/rutas, no valores.

Traza contenido del vendedor hasta HTML Telegram, CSV, consola y JSON SQLite. Comprueba SQL parametrizado, safe_load, escapes y fórmulas CSV con payload ficticio; no ejecutes la fórmula. Delimita el impacto real: escribir texto peligroso no demuestra ejecución en el equipo.

Revisa URL/host/redirecciones/tamaño en enriquecimiento, mensajes de error que contienen respuestas externas y dónde se imprime/persiste cada dato. Usa token ficticio para probar redacción y no expongas valores reales en el informe. Distingue riesgo de payload controlado por vendedor/proveedor de una entrada controlada solo por operador.

En perfiles persistentes identifica qué directorio se guarda, quién lo usa y si el lock de base protege el perfil. No cambies permisos, borres sesiones ni cierres cuentas. Para dependencias evalúa fijación/reproducibilidad y privilegios; si consultas vulnerabilidades, usa fuentes actuales y versiones verificadas. No inventes CVEs ni cumplimiento legal. Cualquier valoración jurídica requiere fuentes y contexto y se separa del bug técnico.

