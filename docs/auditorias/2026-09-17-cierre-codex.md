# Cierre offline de hallazgos — 17 de septiembre de 2026

## Alcance y estado

Se implementaron y verificaron los criterios de aceptación de los 15 hallazgos
de `2026-09-16-codex.md` y los siete fallos/riesgos accionables añadidos por
`2026-09-16-codex-2.md`. El trabajo parte de `9541a90` y conserva las
modificaciones locales previas. Entorno: Python 3.12.10 en Windows.

No se consultaron cuentas, fuentes, perfiles, tokens, grupos ni bases reales. No
se abrió navegador ni se enviaron mensajes. «Cerrado» significa corregido por
contrato y reproducción offline; no valida cobertura o integración productiva.

## Resultado por bloque

| IDs | Cierre implementado | Evidencia permanente |
|---|---|---|
| D01, D07 | Entrada/cuota no se toma como total; señuelo se decide en moneda base y el detalle se renormaliza desde la moneda del vendedor | `Prices.test_down_payments_and_installments_are_not_total_prices`, `Selection.test_total_price_wins_over_down_payment`, `Selection.test_uyu_bait_is_corrected_then_normalized_in_base_currency` |
| D02–D05, D08 | Objeto, condición e intención se interpretan con título/contexto; piezas, autos, pedidos y teléfonos quedan fuera sin excluir motos proyecto ni teléfonos ofrecidos como pago | `Selection.test_object_condition_and_intent_are_classified_in_context` |
| D06, API-REUSE | Caché FX validada semánticamente; una `Listing` reutilizada se recalcula desde precio/moneda del vendedor | `Money.test_invalid_cache_shapes_and_rates_fall_back_safely`, `Selection.test_reusing_listing_with_new_fx_matches_fresh_evaluation` |
| P01 | Cooldown persistente por destinatario; bajadas actualizan el pendiente sin borrar intentos o plazo | `Persistence.test_destination_cooldown_survives_new_events_and_restart` |
| P02 | `collect(..., apply_filters=False)` conserva moneda incompatible para que pipeline/store registren exclusión y cancelen pendiente | `EndToEnd.test_fx_incompatible_observation_cancels_previous_delivery` |
| F01, F02 | OLX distingue vacío explícito, bloqueo y markup desconocido; Facebook valida login/checkpoint en Marketplace y grupos | `EndToEnd.test_olx_distinguishes_empty_blocked_and_unknown_pages`, `EndToEnd.test_facebook_navigation_identity_and_cleanup_are_explicit` |
| F03 | Se extrae id estable desde DOM/permalink cuando existe; el fallback por texto queda marcado explícitamente como identidad inestable | `EndToEnd.test_facebook_navigation_identity_and_cleanup_are_explicit` |
| O01, R02 | Telegram valida la forma JSON por fila; un payload persistido incompatible se aplaza y no bloquea las entregas válidas | `Telegram.test_malformed_json_is_a_row_failure_not_a_batch_abort`, `Persistence.test_incompatible_delivery_is_isolated_without_blocking_next` |
| O02 | Salud de fuente estructurada `ok/partial/error`; resultados parciales se conservan y `deals` devuelve error si todas fallaron | `EndToEnd.test_partial_source_keeps_results_and_structured_diagnostic`, `EndToEnd.test_deals_fails_when_every_source_failed` |
| O03 | Tipos YAML, límites numéricos, listas, booleanos y secciones se validan antes de efectos externos; entorno vacío no borra YAML | `EndToEnd.test_mistyped_yaml_is_rejected_before_use`, `EndToEnd.test_empty_environment_does_not_erase_yaml_secret` |
| S01 | Campos externos con prefijos `=`, `+`, `-`, `@` o whitespace se neutralizan al exportar CSV | `EndToEnd.test_csv_neutralizes_spreadsheet_formulas` |
| R01 | Creación y cierre de runtime/contexto/página quedan protegidos; se intenta `stop` aun si `close` falla | `EndToEnd.test_facebook_navigation_identity_and_cleanup_are_explicit` y reproducción R01 del segundo informe |
| M01, V01 | `min_samples` gobierna margen, warning y confianza; los centavos significativos se muestran | `Selection.test_price_presentation_and_reference_sufficiency_are_consistent` |

La base migra aditivamente a `user_version=2` para `destination_state`.
La suite ahora construye un esquema v0 real, preserva sus filas y rechaza una
versión futura sin modificar el archivo.

## QA ejecutado

```text
python -m unittest discover -s tests -v  -> 59/59 OK
python -m compileall -q motoradar tests  -> codigo 0
git diff --check                         -> sin errores
```

El workflow CI ejecuta desde ahora unittest y compileall en Windows/Linux. Esto
es definición de CI, no evidencia de una ejecución remota.

## Límites que siguen abiertos fuera de estos hallazgos

- Falta validar entregas Telegram y proveedores con cuentas/datos reales.
- Si Facebook no expone ningún id o permalink, una edición seguirá cambiando el
  hash; ahora esa identidad se declara inestable en vez de presentarse como fiable.
- Las heurísticas de clasificación necesitan corpus etiquetado para medir
  precisión/recall; las regresiones prueban casos, no una tasa de cobertura.
- Mercado Livre continúa sin paginación completa ni OAuth verificado.
- Persisten los pendientes de operación productiva del ROADMAP: fixtures reales
  sanitizados, jornada sostenida, backup/restauración y supervisión.

