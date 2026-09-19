---
name: motoradar-persistencia
description: Audita SQLite, migraciones, eventos, locking y recuperación de Motoradar con bases temporales; usar para revisión de estado, concurrencia y entregas persistidas.
---

# Persistencia y recuperación de Motoradar

## Contrato de revisión
Esta skill es específica de Motoradar. Trabaja sobre el repositorio indicado o el directorio actual; si no está disponible, pide su ubicación y no inventes una revisión. Lee AGENTS.md, README.md, docs/OBJETIVO.md, docs/ARQUITECTURA.md, docs/ROADMAP.md y CONTRIBUTING.md. La documentación define intención; el código y las pruebas aportan evidencia.

Registra commit y cambios locales. Conserva el trabajo existente. Por defecto audita sin cambiar comportamiento: una solicitud adicional de corrección autoriza los arreglos dentro de su alcance. No consultes configuración personal, grupos, tokens, perfiles ni bases reales. Usa ejemplos, mocks, FX offline y SQLite temporal; no abras navegadores ni hagas peticiones o envíos reales.

Para cada hallazgo aporta archivo:línea actual, entrada o secuencia, resultado esperado y observado, impacto, confianza, solución y prueba de aceptación. Distingue reproducción ejecutada, traza estática, riesgo condicionado e hipótesis. Usa P0 para daño crítico inmediato, P1 para comportamiento incorrecto prioritario, P2 para robustez o deuda con coste demostrado y P3 para mejoras menores; justifica por impacto y alcance. No fuerces hallazgos ni elogios, cuotas, notas 0–10 o porcentajes sin medir.

Preserva motos hasta R$1.000 inclusive, averiadas/proyectos/doadoras y sin papeles; margen/tasación no filtran. Precio incierto se etiqueta por confirmar. Identidad fuente/id y conversión antes de comparación. Reporta archivos revisados, pruebas realmente ejecutadas y límites. Una suite verde no prueba integración real.

## Área
Lee store.py, locking.py y los contratos entre pipeline.py, cli.py y notify.py. Prueba usando una base temporal que puedas cerrar/reabrir, no la base del usuario.

Traza nuevo elegible, repetido, excluido→elegible, incierto→confirmado, bajada, subida fuera de presupuesto y cancelación. Examina oscilaciones por observación incompleta y cambio de moneda; no declares duplicado indebido sin distinguir un evento nuevo legítimo.

Inyecta fallo entre actualización y encolado; comprueba rollback de listings/observations/deliveries y preservación de first_seen. La migración necesita esquema antiguo construido explícitamente, no solo user_version=0 en una base moderna. Revisa versión futura, históricos y payload incompatible.

Prueba fallo de envío/reinicio, actualización de snapshot, intentos y retry_at tanto sin evento como con evento nuevo. Un aplazamiento de destinatario debe incluir anuncios añadidos después. Revisa alcance de cancelación por destino, entradas malformadas, retención e índices mediante consultas concretas.

Distingue lock por base de propiedad del perfil Facebook; no declares comprobado Linux tras ejecutar Windows. Mantén SQLite si basta: propone WAL/índices/migración/retención antes de workers o PostgreSQL y exige una necesidad medida para escalarlos.

