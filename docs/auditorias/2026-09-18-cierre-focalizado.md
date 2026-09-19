# Cierre focalizado — 18/09/2026

Base: commit 9541a90 más cambios locales existentes, conservados. Alcance:
encontrar motos hasta R$1.000 sin perder proyectos/doadoras ni exigir papeles
o margen. Sin cambios de configuración personal, consultas, perfiles, bases
reales ni envíos; SQLite temporal y navegador/Telegram simulados.

## Correcciones y evidencia

- **Entregas múltiples:** antes, 800→500 daba un pendiente a `yo` y cero a
  `socio`. `Store.upsert_many` calcula el evento una vez y persiste observación
  y entregas en la misma transacción. Pruebas de `run_once` con ambos órdenes:
  desconocido→800→500→500→1500→700. Las novedades llegan a ambos; repetición
  y exclusión no envían. Fallo inyectado al segundo encolado revierte anuncio,
  observación y primera entrega. Reinicio conserva intentos/retry_at de ambos.
  Se mantiene la protección contra reenvío histórico tras migración.
- **Grupos ciegos:** antes `feed=True, children=0, empty=False` pasaba como sano.
  Ahora es fallo; también se registra fallo cuando hay hijos pero cero posts
  extraíbles sin cartel de vacío. Vacío explícito continúa siendo válido.
- **Detalle:** antes `Detalhes do veículo`/`Descrição` devolvía vacío. Ahora se
  reconoce y prioriza el encabezado de descripción, incluso tras muchos
  atributos. Texto irreconocible deja `detail_status` explícito. Regresión de
  tarjeta R$6→detalle R$6500: excluida, cero envíos; R$6→R$800: entrega a ambos.
- **Cooldown parcial:** observaciones de Marketplace no borran el aviso de DOM
  activo. Cuatro pasadas parciales producen un aviso; recuperación seguida de
  nuevo fallo produce otro.
- **Fixture antigua:** tarjeta sin precio conserva título y ciudad; se verifica
  que llega a `unconfirmed` y no se descarta.

## Límites y siguientes pasos

- Esto cierra reproducciones offline, no demuestra que los selectores lean el
  DOM vivo ni que el orden cronológico se respete.
- Falta diagnóstico persistido por grupo/superficie y medida de frescura;
  la salud sigue agregada por fuente.
- Falta corpus etiquetado para medir omisiones además de falsos positivos.
- Sin caché de detalles: puede repetirse lectura y oscilar la elegibilidad si
  una pasada omite un detalle anteriormente leído.
- Los avisos de salud aún marcan cooldown antes de enviar y no tienen cola
  persistente. La comparación de bajadas entre monedas originales distintas
  sigue pendiente. No se declara apta vigilancia desatendida.
- La rotación del token señalada en la auditoría anterior requiere acción del
  propietario; no se consultó ni cambió el token.

Verificación: `python -m unittest discover -s tests -v` (102 pruebas),
`python -m compileall -q motoradar tests` y `git diff --check`.
