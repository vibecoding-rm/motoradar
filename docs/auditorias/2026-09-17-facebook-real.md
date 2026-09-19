# Validación acotada de Facebook — 17 de septiembre de 2026

## Alcance autorizado

Se ejecutaron consultas `dry-run` con la sesión local propia para responder si
existían anuncios actuales y ajustar Facebook. No se guardaron anuncios, no se
exportó CSV, no se envió Telegram y no se contactó a vendedores. Este documento
no incluye ids de grupos, cookies, tokens ni configuración privada.

## Evidencia inicial

- Sesión Facebook activa.
- La configuración anterior recorría 8 grupos × 2 consultas × hasta 35 segundos,
  además de seis consultas Marketplace, sin progreso visible.
- Una muestra reducida observó 275 publicaciones y produjo seis oportunidades y
  34 precios por confirmar.
- Revisión de las seis oportunidades: dos ventas plausibles, una solicitud de
  compra de Biz y tres falsos positivos (repuestos, vivienda y roçadeira CG-520N).
- La pasada completa anterior superó diez minutos; la fase de detalle también
  podía quedar varios minutos sin salida.

## Cambios aplicados

- Configuración local: solo Facebook activo; OLX y Mercado Livre desactivados.
- Marketplace usa tres consultas dedicadas; grupos, una consulta cada uno.
- Menos scroll y límites explícitos por grupo/navegación.
- Progreso visible para Marketplace, grupos y lectura de detalle.
- Detalle limitado a 12 anuncios, priorizando precios mayores al umbral de
  señuelo; timeout individual de 15 segundos.
- Clasificación contextual: piezas/plásticos/cestos, vivienda que acepta moto y
  maquinaria con modelo `CG-*` ya no se afirman como motos. Se conservan motos
  de modelo desconocido, proyectos y doadoras explícitas.

## Validación posterior

La pasada real final completó el recorrido y el detalle:

```text
Facebook: OK
155 publicaciones observadas
12 detalles intentados con progreso visible
2 motos en presupuesto
23 precios por confirmar
```

Las dos oportunidades finales fueron una scooter uruguaya anunciada a R$400 y
una publicación «Vendo moto» a R$800, ambas en Jaguarão. Los cuatro falsos
positivos revisados en la muestra anterior no reaparecieron como oportunidades.
El precio, disponibilidad y condición siguen requiriendo confirmación del vendedor.

QA posterior: 61/61 pruebas offline, `compileall` código 0 y `git diff --check`
sin errores. La consulta real prueba esta sesión y muestra acotada, no cobertura
completa ni estabilidad futura de Facebook.

