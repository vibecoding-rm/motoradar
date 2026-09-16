# Mejoras y escalamiento

Estado: 16 de septiembre de 2026. Los plazos son estimaciones para una persona
familiarizada con el código; dependen de acceso a fuentes y datos de evaluación.
Ninguna etapa debe convertir papeles o margen en filtros de compra.

## Base implementada

- [x] Selección compartida por presupuesto, clasificación y lectura de detalle.
- [x] Alertas de motos averiadas/doadoras y sin papeles.
- [x] Precios inciertos identificados por separado.
- [x] Corrección de truncamiento y renormalización monetaria tras detalle.
- [x] Actualización por identidad y observaciones históricas.
- [x] Entregas persistentes, reintentos individuales y control HTTP 429.
- [x] Bloqueo de corrida, diagnóstico local y modo sin persistencia/envíos.
- [x] Regresiones locales y workflow CI preparado.

La base está verificada localmente. Esto no equivale a validar fuentes completas,
precisión de clasificación o funcionamiento continuo en producción.

## P0 — Cerrar el circuito real de alertas, 2–4 días

- [ ] Configurar Telegram y comprobar una entrega real al destinatario elegido.
- [ ] Verificar Facebook con perfil propio: sesión expirada, grupos, Marketplace y detalles.
- [ ] Revisar OAuth y permisos de Mercado Livre; corregir autenticación/paginación.
- [ ] Capturar fixtures sanitizados representativos de cada fuente.
- [ ] Ensayar recuperación tras caída/reinicio usando una base de prueba.

Aceptación: evidencia de anuncios reales parseados por cada fuente declarada
operativa y de una entrega real, con estado de fallo distinguible de cero resultados.
No anunciar una fuente como lista por tener adaptador.

## P1 — Menos ruido y menos compras omitidas, 1–2 semanas

- [ ] Parser de múltiples importes: entrada, cuotas, precio anterior, lote y precio total.
- [ ] Importes en unidades menores o Decimal, redondeo explícito y migración compatible.
- [ ] Corpus etiquetado con conjunto reservado y evaluación por fuente/clase.
- [ ] Separar evidencia de vehículo, condición e intención; manejar negaciones y desconocidos.
- [ ] Ubicación estructurada: país, ciudad, región de grupo y confianza.
- [ ] Catálogo por cilindrada/variante/año para tasaciones opcionales.
- [ ] Mostrar desconocidos en revisión manual sin tratarlos automáticamente como motos.

Aceptación: publicar tamaño de muestra, precisión, recall y causas de error.
Medir también pérdida de motos baratas reales al reducir falsos positivos.
No afirmar umbrales de precisión alcanzados antes de evaluar.

## P1 — Operación continua, 1 semana

- [ ] Métricas y logs estructurados con run_id, fuente, cobertura y duración.
- [ ] Caché de detalles con TTL y revalidación por cambios.
- [ ] Retención de observaciones/payloads y backups con restauración ensayada.
- [ ] Bloqueo por perfil Facebook además del lock por base.
- [ ] Supervisión en Windows: arranque, reinicio y parada observables.
- [ ] Resumen de salud configurable y visibilidad del pendiente más antiguo.
- [ ] Empaquetado e instalación reproducible, dependencias fijadas y política de actualización.

Aceptación: jornada representativa sin crecimiento sostenido del backlog,
restauración demostrada y diagnóstico de fuente caída/sesión expirada.

## P2 — Mejor experiencia, 1–2 semanas

- [ ] Historial consultable de anuncios, cambios de precio y entregas.
- [ ] Panel local con oportunidades, precios por confirmar y filtros de zona.
- [ ] Exportación completa e identificación de vistos/contactados/comprados.
- [ ] Registro opcional de reparación, venta y margen real para aprendizaje.

Aceptación: facilitar revisar y contactar compras candidatas. El panel no debe
ocultar anuncios por falta de margen ni exigir documentación.

## Escalar según evidencia

| Situación medida | Próximo paso | Evidencia requerida |
|---|---|---|
| Detalle domina duración | Caché y revalidación selectiva | Duración por etapa y proporción de caché reutilizable |
| Corridas exceden cadencia deseada | Consultas compartidas y concurrencia HTTP limitada por dominio | p95 de corrida y errores/bloqueos del proveedor |
| Contención de escritura local | Transacciones por lote e índices medidos | Esperas de lock y latencia de transacción |
| Varios escritores/hosts necesarios | Evaluar PostgreSQL y migración reversible | Necesidad operativa real, ensayo y validación de datos |
| Backlog exige procesos separados | Worker de envíos con cola persistente | Antigüedad de pendientes y tiempos de recuperación |
| Varios usuarios requieren búsquedas similares | Recolección compartida, suscripciones y autorización por usuario | Modelo de acceso y pruebas de aislamiento |

Mantener un único propietario de cada perfil de navegador. Aumentar procesos
sin controlar perfiles y ritmo de fuentes puede empeorar cobertura y estabilidad.
No introducir Kubernetes o microservicios por crecimiento hipotético.

## Línea base de métricas

Registrar éxito/cobertura por fuente, duración p50/p95, candidatos por zona,
precios corregidos/desconocidos, falsos positivos confirmados, comparables únicos,
tiempo observación→entrega, pendientes/antigüedad, tamaño de base y lock waits.
Usar compras y ventas reales para aprender costes, sin filtrar el radar por rentabilidad.

La revisión inicial completa está en [REVISION_SENIOR.md](../REVISION_SENIOR.md);
la investigación y primera implementación en
[INVESTIGACION_Y_MEJORAS.md](../INVESTIGACION_Y_MEJORAS.md).
