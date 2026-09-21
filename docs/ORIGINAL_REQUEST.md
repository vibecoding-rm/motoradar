# Original User Request

## Initial Request — 2026-09-18T22:09:19Z

Analizar, depurar y dejar listo para producción el scraper de Facebook de Motoradar, garantizando que recolecte anuncios de motos en venta únicamente en la zona fronteriza de Jaguarão (Brasil) y Río Branco (Uruguay), con un precio máximo de R$ 1.000 o su equivalente en pesos uruguayos (UYU), orientadas al negocio de compra, reparación y reventa.

Working directory: D:/Dev/Labs/home_lab/Dayron
Integrity mode: development

## Requirements

### R1. Extracción y delimitación geográfica estricta
El extractor de Facebook (Marketplace y Grupos) debe capturar publicaciones de motos relevantes limitadas exclusivamente a Jaguarão y Río Branco (incluyendo grupos comunitarios y clasificados locales de dicha frontera), descartando anuncios de ciudades más lejanas (ej. Pelotas, Bagé, Melo, Porto Alegre) que no pertenezcan al ámbito local inmediato.

### R2. Conversión monetaria y umbral de reventa
Los precios detectados en pesos uruguayos (UYU) o reales brasileños (BRL) deben normalizarse con la tasa de cambio vigente del sistema (`FX`), asegurando que solo califiquen como oportunidades aquellas motos cuyo precio final sea menor o igual a R$ 1.000 BRL (o su contravalor equivalente en UYU). Las publicaciones con precios señuelo (R$ 0, R$ 1, R$ 1.234) o no especificados deben identificarse correctamente como "precio por confirmar" y no descartarse prematuramente si corresponden a motos viables.

### R3. Clasificación de dominio y corrección de falsos positivos
Depurar la heurística de clasificación y extracción para diferenciar con precisión motos completas (andando, averiadas, proyectos, motos para desarme/doadoras o sin documentación) de publicaciones de repuestos sueltos, accesorios, indumentaria, publicaciones de búsqueda/compra ("busco moto"), y anuncios inmobiliarios o comerciales ajenos.

### R4. Calidad, robustez y preparación para producción
Identificar errores en el flujo de ejecución, manejo de tiempos de espera, parsing del DOM y persistencia, garantizando un funcionamiento estable en modo producción (`run`, `watch` y `collect`). El repositorio debe cumplir rigurosamente con los contratos de seguridad e invariantes del proyecto: no persistir credenciales privadas, no alterar la suite offline y mantener compatibilidad en las transacciones de almacenamiento.

## Verification Resources

- Suite de pruebas del proyecto: `python -m unittest discover -s tests -v`
- Verificación de sintaxis y compilación: `python -m compileall -q motoradar tests`
- Fixtures existentes de Facebook: `tests/fixtures/facebook_marketplace_cards.json` y `tests/fixtures/facebook_group_posts.json`
- Pruebas específicas de Facebook: `tests/test_facebook.py` y `tests/test_clasificacion_grupos.py`

## Acceptance Criteria

### Filtrado geográfico y de divisas
- [ ] Los anuncios procedentes de Marketplace o grupos con ubicación fuera de Jaguarão o Río Branco son filtrados y no se clasifican como oportunidades locales.
- [ ] Los precios en UYU se convierten a la moneda base (BRL) usando la capa `FX` sin comparar montos nominales incompatibles.
- [ ] Toda publicación de moto con precio normalizado <= R$ 1.000 BRL (o equivalente en UYU) se clasifica como oportunidad en presupuesto.
- [ ] Toda publicación de moto con precio nulo, señuelo o ambiguo se clasifica como precio por confirmar, sin marcarse como oportunidad confirmada ni descartarse.

### Clasificación y descarte
- [ ] Publicaciones de repuestos sueltos, accesorios, solicitudes de compra ("busco moto", "procuro moto") y publicidad no se clasifican como motos.
- [ ] Motos averiadas, desarmadas, doadoras o sin papeles con precio en rango son aceptadas como oportunidades de reparación/reventa.

### Calidad y producción
- [ ] `python -m unittest discover -s tests -v` se ejecuta con 100% de pruebas pasando satisfactoriamente.
- [ ] `python -m compileall -q motoradar tests` finaliza con código de salida 0 sin errores de sintaxis.
- [ ] Ninguna prueba automatizada realiza conexiones externas reales a Facebook, no abre navegadores ni requiere credenciales reales.
- [ ] Los comandos CLI (`motoradar run`, `motoradar watch`, `motoradar collect`) se ejecutan sin excepciones no controladas en las configuraciones válidas.
