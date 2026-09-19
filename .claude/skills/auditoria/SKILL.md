---
name: auditoria
description: Auditoría crítica y QA de Motoradar sin complacencia. Lanza en paralelo los agentes critico-* (dominio, scraping, persistencia, entrega-cli, qa-tests, seguridad, arquitectura), verifica sus hallazgos más graves y consolida un informe priorizado con recomendaciones y propuestas. Usar cuando el usuario pida revisar, criticar, auditar o hacer QA del proyecto o de un área.
argument-hint: "[todo | dominio | scraping | persistencia | entrega-cli | qa-tests | seguridad | arquitectura ...]"
---

# Auditoría crítica de Motoradar

Argumentos: `$ARGUMENTS` (vacío o `todo` = todas las áreas; si no, solo las áreas nombradas).

## 1. Preparar

- Lee `.claude/critica/REGLAS.md`: aplica también a ti al consolidar.
- Anota `git rev-parse --short HEAD` y `git status --short` para registrar qué se auditó.

## 2. Lanzar los críticos en paralelo

En UN solo mensaje, lanza un Agent por área seleccionada con `subagent_type` igual al nombre:
`critico-dominio`, `critico-scraping`, `critico-persistencia`, `critico-entrega-cli`,
`critico-qa-tests`, `critico-seguridad`, `critico-arquitectura`.

Si un tipo de agente no está disponible (sesión iniciada antes de crearlos), usa
`general-purpose` con el prompt: "Lee `.claude/agents/<nombre>.md` e interpreta su cuerpo como
tus instrucciones completas; ignora el frontmatter. Audita el repositorio actual."

Prompt adicional para cada uno: commit auditado, y "Devuelve el informe completo en el formato
de REGLAS.md. No modifiques archivos del repositorio."

## 3. Verificar antes de creer

Los agentes también pueden exagerar. Para cada hallazgo P0 y P1:
- Abre el código en la ubicación citada y confirma que la descripción es correcta.
- Si es `CONFIRMADO`, ejecuta tú la reproducción cuando sea barato hacerlo.
- Descarta o degrada lo que no se sostenga, y dilo en el informe ("descartado: motivo").
- Fusiona duplicados entre áreas (p. ej. seguridad y entrega-cli sobre el token en logs).

## 4. Consolidar

Escribe `docs/auditorias/AAAA-MM-DD.md` (fecha de hoy; si existe, añade sufijo `-2`):

```
# Auditoría <fecha> — commit <sha>

## Veredicto global
<5 líneas máximo, honesto: ¿se puede dejar corriendo sin supervisión hoy? ¿qué es lo peor?>

## Nota por área
| Área | Nota 0-10 | Peor problema | Hallazgos P0/P1/P2/P3 |

## Top 10 a arreglar primero
Ordenado por (impacto en encontrar motos ≤ R$1.000) / (esfuerzo). Cada uno: severidad,
ubicación, arreglo en una frase, prueba de aceptación, esfuerzo estimado.

## Hallazgos por área
<todos los verificados, con el formato de REGLAS.md>

## Documentación que miente o exagera

## Propuestas de mejora y alternativas
Con coste, riesgo y qué NO hacer.

## Descartados en la verificación

## Límites de esta auditoría
```

Las notas se justifican con hallazgos, no con impresiones. Un 10 no existe; un 5 es "funciona
con supervisión".

## 5. Responder al usuario

En el chat, solo: veredicto global, tabla de notas, top 5 y ruta del informe.
No arregles nada salvo que el usuario lo pida. Si lo pide, empieza por el top 10 y añade
la prueba de aceptación antes de cada arreglo.
