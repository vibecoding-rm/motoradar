# Protocolo de auditoría

## Cobertura por área

| Skill | Responsabilidad primaria | Frontera compartida |
|---|---|---|
| motoradar-dominio | models, money, appraise, filters, pipeline, market | Corrección de precio en enrich y selección de CLI |
| motoradar-fuentes | sources y extracción de enrich | collect, identidad, moneda y stats |
| motoradar-persistencia | store, locking, esquema, eventos | pipeline, CLI y flush_pending |
| motoradar-operacion | CLI, config, notify, __main__ | Estado persistente, fallos y recuperación |
| motoradar-qa | tests y workflow CI | Trazabilidad de todos los contratos |
| motoradar-seguridad | Datos externos, secretos, CSV, sesiones y dependencias | Todos los puntos de entrada/salida |
| motoradar-arquitectura | Diseño transversal y documentación | Coherencia de responsabilidades y prioridades |

No asumas cobertura de un archivo por asignarlo a alguien: recoge inventario de lecturas y límites. Un área puede reportar cero hallazgos.

## Confianza y gravedad

- **Reproducido**: se ejecutó una entrada/secuencia offline y se registró salida. Da pasos suficientes para repetirla y versiones relevantes.
- **Traza estática**: el camino de código prueba la conclusión, pero no se ejecutó. Identifica ramas y efectos concretos.
- **Riesgo condicionado**: depende de un estado posible pero no observado; explícita condición y frecuencia desconocida.
- **Hipótesis**: falta evidencia externa o muestra representativa. Indica cómo verificarla sin presentarla como fallo actual.

P0 exige daño crítico inmediato con alcance acreditado; un falso positivo aislado normalmente es P1. P1 afecta selección, precio, alertas o continuidad en un caso realista. P2 implica robustez/deuda con efecto concreto. P3 es mejora menor. No conviertas automáticamente todo incumplimiento en P0 ni toda carencia de herramienta en deuda prioritaria.

## QA y límites

Inspecciona tests antes de ejecutarlos. Comandos requeridos: unittest discover y compileall según AGENTS.md. Para scripts ad hoc usa carpeta temporal, red bloqueada o mocks explícitos y FX offline; recuerda que dry-run sigue consultando fuentes. Para pruebas de persistencia registra consulta SQL y estado tras reabrir. Un fixture sintético permite probar parser y no representa el HTML vigente. No uses cuentas reales ni integración como parte del QA automatizado.

Reporta qué pruebas invocan transporte, parser o adaptador y cuáles parchean esa función. Si no hay cobertura medida, da nombres de tests y brechas sin porcentajes. Los informes antiguos son históricos; si un fallo está corregido, registrarlo como cerrado puede aportar contexto, pero no cuenta como hallazgo abierto.

## Ficha y priorización

Por hallazgo: ID, severidad justificada, confianza, archivo:línea actual, condición/entrada, esperado, observado, impacto, solución mínima, alternativa cuando aporte valor y prueba de aceptación. Incluye evidencia de ejecución o traza. No exijas un número fijo de hallazgos.

Prioriza por pérdida/ruido de compras candidatas y continuidad de entrega; después por frecuencia acreditada, alcance, confianza, esfuerzo y dependencias. Estima esfuerzos como rangos condicionados, no compromisos. Cada propuesta define problema resuelto, coste, riesgo, compensación y criterio para elegirla frente a mantener el diseño actual.

## Informe final

1. Commit, cambios locales, fecha, intérprete y alcance de lectura.
2. Veredicto apoyado en evidencia; sin nota numérica arbitraria.
3. Hallazgos ordenados por impacto, con fichas verificables.
4. Riesgos e hipótesis separados; pruebas que faltan por contrato.
5. Propuestas concretas y orden de trabajo.
6. Resultado real de QA, descartados/cerrados y limitaciones.

Si guardas scripts de reproducción, mantenlos offline y fuera de tests; no introduzcas suites que fallen deliberadamente como parte de CI. La revisión no requiere corregir código salvo solicitud de implementación.

