# Guía para trabajar en Motoradar

Leer README.md y docs/OBJETIVO.md antes de cambiar el comportamiento del radar.
Consultar docs/ROADMAP.md para prioridades y docs/ARQUITECTURA.md para contratos.

## Reglas de producto

- El objetivo es detectar motos de hasta R$1.000 inclusive, para reparar/revender.
- Admite motos andando, averiadas, proyectos y doadoras. Papeles y margen no son filtros.
- Precio desconocido o señuelo debe identificarse como precio por confirmar.
- No convertir una tasación, una puntuación o falta de documentos en requisito de alerta.
- No confundir un repuesto suelto, una solicitud de compra o publicidad con una moto.
- No afirmar cobertura completa ni entrega exactamente una vez.

## Desarrollo

- Mantener selección compartida en pipeline.py; evitar reglas divergentes entre run/watch/deals.
- Identidad por fuente/id. Persistir actualización y entrega en la misma transacción.
- Normalizar moneda después de cambiar un precio; no comparar monedas incompatibles.
- Probar cambios de comportamiento con casos de dominio y fallos pertinentes.
- Verificación: `python -m unittest discover -s tests -v` y `python -m compileall -q motoradar tests`.
- Las pruebas automatizadas no deben usar cuentas reales, abrir navegadores ni enviar mensajes.
- No versionar configuración personal, grupos, tokens, cookies, perfiles o bases de datos.
- El token de Telegram no va en config.yaml: entorno o `~/.motoradar/telegram_token`.
- Un informe de auditoría en `docs/auditorias/` se versiona solo si está sanitizado:
  sin tokens, sin chat_id completos, sin ids de grupos privados y sin texto de
  vendedores. La evidencia cruda (`evidencia_*.json`, `repro_*.py`) está ignorada.
- Documentar limitaciones y cambios de comandos. Consultar CONTRIBUTING.md.

## Crítica y QA por áreas

Cuando se solicite una auditoría o propuestas de mejora, consultar
`docs/REVISION_CRITICA.md` y las skills de `.agents/skills/`. El coordinador es
`motoradar-auditoria`; exige evidencia y distingue fallos de hipótesis. Una
revisión no autoriza cambiar comportamiento ni realizar integraciones reales.
