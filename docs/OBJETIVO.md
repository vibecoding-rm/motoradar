# Objetivo real y criterios de producto

## Negocio

El usuario compra motos económicas para arreglarlas o armarlas y luego revenderlas.
El radar debe reducir el tiempo entre la publicación de una compra candidata y su
descubrimiento. La decisión final se toma al contactar al vendedor e inspeccionar.

El criterio solicitado es **moto con precio anunciado de R$1.000 o menos**,
en Jaguarão y la zona configurada. El presupuesto puede ajustarse, pero el valor
de referencia del producto es R$1.000 en BRL. No exigir documentación ni margen
estimado positivo. La ausencia de papeles no debe excluir ni penalizar la alerta.

## Qué interesa

| Caso | Comportamiento esperado |
|---|---|
| Moto andando, R$1.000 | Alertar; límite inclusive |
| Moto averiada, R$800 | Alertar |
| Proyecto o moto doadora, R$700 | Alertar |
| Moto sin documentos, R$500 | Alertar; papeles solo como información |
| Moto sin modelo reconocido, pero identificada como moto | Alertar si entra en presupuesto |
| Moto sin tasación o con margen estimado negativo | Alertar si entra en presupuesto |
| Moto a R$1.000,01 | No alertar como compra dentro del presupuesto |
| Moto anunciada en USD/UYU | Convertir a BRL antes de comparar |
| Moneda sin conversión disponible | No comparar ni presentarla como compra confirmada |
| Precio desconocido o señuelo | Mostrar como precio por confirmar; aviso incierto configurable |
| Repuesto suelto, auto, teléfono, publicidad o pedido de compra | Excluir de alertas de motos |
| Tipo no identificado | No afirmarlo como moto; revisión manual futura |

“En presupuesto” significa que el importe leído pasa las reglas del radar; no
significa que el vendedor haya confirmado precio, disponibilidad o condición.
La heurística actual de señuelo usa <=100 en moneda base. Conservar el caso como
incierto evita perder una compra real extremadamente barata.

## Eventos de alerta

1. Nuevo anuncio elegible.
2. Anuncio observado anteriormente que pasa a ser elegible.
3. Bajada de precio dentro del presupuesto o que entra en él.
4. Precio incierto que pasa a tener un importe elegible.

Repetir un anuncio sin cambio relevante no debe generar otra entrega. Una alerta
fallida queda pendiente y se reintenta; no se descarta solo por haber visto el uid.
Si una nueva observación lo excluye, cancelar las entregas aún pendientes.

## Qué no es el producto

- Un tasador que decide si el usuario debe comprar.
- Un filtro de motos documentadas, funcionando o con margen positivo.
- Un sistema que compra o contacta vendedores automáticamente.
- Una garantía de precio real, disponibilidad, estado o cobertura total del mercado.

## Cómo medir utilidad

Medir candidatos encontrados, oportunidades reales confirmadas por el usuario,
falsos positivos por causa, compras omitidas en una muestra revisada y tiempo
desde observación hasta entrega. La rentabilidad realizada sirve para aprender
costes; no debe impedir descubrir motos baratas.

Los objetivos numéricos de precisión, cobertura y frecuencia se definirán tras
recoger una línea base. No hay aún evidencia para prometer capacidad o precisión
de producción.
