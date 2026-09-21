# Motoradar: referencias y primera mejora implementada

Investigación: 16 de septiembre de 2026. Objetivo: alertar sobre motos de R$1.000 o menos para reparar/revender. Admite motos andando, averiadas, proyectos y doadoras; papeles y margen no determinan elegibilidad.

## Proyectos contrastados

Se consultaron repositorios originales, metadatos de GitHub y código pertinente. Las fechas siguientes son `pushed_at` del repositorio, no garantías de compatibilidad con los proveedores. No se instalaron ni ejecutaron estos proyectos.

| Proyecto | Evidencia de desarrollo | Qué conviene incorporar |
|---|---|---|
| [changedetection.io](https://github.com/dgtlmoon/changedetection.io) | Actualización 2026-09-15; Apache-2.0; pruebas y CI; servicio de notificaciones separado. | Vigilancia por cambios, historial, estado operativo y separación entre detectar y notificar. Es una referencia de plataforma, no un sustituto directo del clasificador de motos. |
| [Apprise](https://github.com/caronc/apprise) | Actualización 2026-09-16; BSD-2-Clause; biblioteca de notificaciones con pruebas. | Interfaz común para canales, imágenes y escape de contenido. Mantener Telegram directo inicialmente evita añadir dependencias sin necesidad. Apprise por sí sola no constituye nuestra bandeja persistente. |
| [GPU Radar](https://github.com/gabrfern99/gpu-radar) | Actualización 2026-08-23; scraper, historial de precios, detalle previo a alerta, dashboard y bloqueo de corrida documentados. GitHub no identifica licencia. | Seguimiento por id, cambios de precio, lectura de detalle y exclusión de ejecuciones superpuestas. Su puntuación no se adopta como filtro para este negocio. Que un anuncio desaparezca no prueba una venta. |
| [fb-car-bot](https://github.com/evanoseen/fb-car-bot) | Actualización 2026-05-31; MIT; proyecto pequeño, con módulos scraper/parser/evaluator/db/telegram. Se revisaron db.ts e index.ts. | Flujo de búsqueda y aviso, servicio supervisado y señal de actividad. Su lógica de alerted/last_price exige revisión; no es evidencia suficiente para llamarlo una plataforma madura. |
| [passivebot scraper](https://github.com/passivebot/facebook-marketplace-scraper) | Archivado; última actualización 2024-04-29; GitHub no identifica licencia. | Referencia de UI y extracción, no base recomendada para reconstruir el radar. |

El código nuevo es implementación propia de los patrones seleccionados. No se copió código de repositorios sin licencia identificada.

Código original inspeccionado:

- [changedetection.io notification_service.py](https://github.com/dgtlmoon/changedetection.io/blob/master/changedetectionio/notification_service.py) y [handler.py](https://github.com/dgtlmoon/changedetection.io/blob/master/changedetectionio/notification/handler.py).
- [fb-car-bot db.ts](https://github.com/evanoseen/fb-car-bot/blob/main/src/db.ts) e [index.ts](https://github.com/evanoseen/fb-car-bot/blob/main/src/index.ts).
- [GPU Radar scraper.py](https://github.com/gabrfern99/gpu-radar/blob/main/scraper.py).

## Implementado en esta iteración

1. `run` y `watch` seleccionan motos según budget, leen descripciones por defecto y clasifican intención/tipo antes de avisar. No requieren referencias de mercado.
2. `deals` utiliza la misma selección; la tasación sigue siendo información complementaria.
3. Presupuesto inclusivo: 1000 entra, 1000,01 queda fuera. Los papeles y el estado mecánico no excluyen.
4. Anuncios sin precio o <= R$100 se separan como PRECIO POR CONFIRMAR. Se puede desactivar su aviso con monitoring.alert_unconfirmed=false. Esta heurística puede marcar una compra real barata como incierta; no se elimina del radar.
5. Corrección de importes truncados, formatos BRL/USD y números no finitos. Precios extraídos del detalle se normalizan otra vez antes de comparar. Monedas sin tasa no se comparan como si fueran BRL.
6. Identidad por fuente/id, actualización del estado y observaciones históricas. No se fusionan anuncios distintos por título y precio.
7. Entregas persistentes por destinatario, confirmación individual, reintentos con espera creciente y respeto del retry_after de Telegram. Se comprueba el campo ok del JSON, además del estado HTTP.
8. Nueva alerta al bajar de precio dentro del presupuesto o entrar en él. Una observación posterior fuera de presupuesto cancela entregas aún pendientes. Los pendientes usan el estado más reciente observado.
9. Bloqueo de corrida por base de datos para impedir que dos procesos locales recojan/envíen simultáneamente.
10. SQLite WAL y migración aditiva de las tablas anteriores; se mantiene first_seen. Registros antiguos marcados notified no se reenvían masivamente al actualizar. JSON antiguo truncado se tolera al leer metadata.
11. OLX no deja de paginar por una página de anuncios repetidos y distingue fallo total de búsqueda vacía. Se informa cobertura parcial.
12. Variables de entorno sustituyen Telegram YAML. Validación de fuente, presupuesto, intervalo y cantidad de resultados.
13. `doctor` muestra diagnóstico local; `retry` envía pendientes sin scraping; `--dry-run` busca sin guardar/exportar/enviar. El navegador de Facebook queda desactivado solo en el ejemplo nuevo; no se alteró la configuración personal.
14. Localidad se filtra por ubicación, no por menciones en título. Para grupos se usa la zona configurada como señal aproximada, con etiqueta de confirmación. Se puede configurar group_cities por id para mejorar esa señal.
15. Referencias deduplicadas y sin solicitudes/publicidad/riesgo documental; min_samples impide calcular margen cuando la muestra no alcanza. Esto no limita alertas de compra sin papeles.

## Uso

```powershell
python -m motoradar doctor
python -m motoradar run --source olx --budget 1000 --dry-run
python -m motoradar watch --budget 1000 --interval 30
python -m motoradar retry
python -m unittest discover -s tests -v
```

`--dry-run` consulta proveedores y puede abrir el navegador si Facebook está seleccionado; no persiste anuncios ni envía mensajes. Para una prueba acotada seleccionar OLX explícitamente.

Telegram requiere TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID o los campos equivalentes en config.yaml. El diagnóstico local de la configuración personal durante esta iteración indicó Telegram sin configurar. No se creó un bot ni se enviaron mensajes de prueba. La vigilancia no se dejó corriendo en segundo plano.

La base antigua se amplía automáticamente en la primera corrida normal. Copiarla mediante la API de backup de SQLite si se necesita un respaldo mientras está abierta; no copiar solo el archivo principal ignorando el WAL.

## Verificación y límites

- 31 pruebas locales aprobadas sobre parser, selección, SQLite temporal, migración, reinicio, envío parcial, HTTP 429, JSON API ok, bloqueo, CLI, configuración y paginación. Todas las entregas de prueba son mocks. También pasó la compilación de módulos.
- Consulta real acotada: una query, una categoría y una página OLX, sin detalles, en dry-run. Se parsearon dos anuncios; ninguno pasó la zona Jaguarão de esa prueba. Esto verifica ese camino de lectura en ese momento, no cobertura completa ni detección real de una compra elegible.
- Se añadió workflow de regresiones para Windows/Linux. No se ejecutó GitHub Actions ni se validó Linux en esta máquina.
- No se verificaron Facebook ni Mercado Livre con cuentas/tokens reales. El flujo OAuth de Mercado Livre sigue pendiente de corregir/verificar con autorización y permisos reales.
- La entrega es recuperable, pero un timeout después de que Telegram acepte un mensaje puede producir duplicados al reintentar. [Contrato de respuestas y retry_after de Telegram](https://core.telegram.org/bots/api).
- El presupuesto remoto solo descubre anuncios que el proveedor devuelve. No se garantiza detectar todos los anuncios mal categorizados, precios ausentes o rebajas fuera de la ventana de búsqueda.
- Los pendientes no prueban disponibilidad actual: se cancelan si se observa un cambio excluyente, pero un anuncio que desaparece entre búsquedas aún puede tener una entrega pendiente.
- Siguen pendientes un corpus de clasificación reservado, importes múltiples/entrada/cuotas, variantes de modelos, política de retención del historial y medición de capacidad. Los importes siguen almacenados como float/REAL; migrar a Decimal/unidades menores con una política explícita en otra etapa.

## Próximas inversiones

1. Verificar integraciones: Facebook con perfil existente, OAuth/paginación de Mercado Livre y fixtures sanitizados de ambas. Evitar declarar una fuente operativa solo porque tiene adaptador.
2. Reducir ruido: corpus etiquetado, varios importes por descripción, negaciones y ubicación real frente a región de grupo. Incorporar una revisión manual de desconocidos.
3. Reducir coste: caché de detalles con TTL y revalidación; métricas de duración/cobertura; consultas equivalentes compartidas y ritmo controlado por proveedor.
4. Operación continua: supervisión en Windows, recuperación de sesión y resumen de salud. Implementarlo una vez que la entrega Telegram esté configurada y comprobada.
5. Interfaz local de historial/pendientes y exportación completa, usando los datos persistidos.
6. Concurrencia acotada y PostgreSQL cuando las mediciones o la necesidad de varios hosts lo justifiquen. Mantener un único propietario por perfil de navegador.

La mejora de esta iteración es la base funcional y de recuperación del radar. La precisión y cobertura de producción requieren las validaciones pendientes anteriores.
