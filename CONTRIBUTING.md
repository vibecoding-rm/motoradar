# Desarrollo y contribuciones

Antes de cambiar reglas leer [objetivo](docs/OBJETIVO.md). Las motos sin papeles,
averiadas o sin margen estimado siguen siendo candidatas dentro del presupuesto.

## Preparar y verificar

Usar Python 3.12, entorno virtual y requirements.txt; ver
[operación](docs/OPERACION.md). No se necesitan cuentas o navegadores para las pruebas.

```powershell
python -m unittest discover -s tests -v
python -m compileall -q motoradar tests
```

Agregar regresiones cuando cambie una regla de selección, identidad, dinero,
entrega o recuperación. Usar fixtures sanitizados y mocks para fallos externos.
No usar secretos ni sesiones reales en CI.

## Flujo Git

main contiene la base compartida. Crear una rama por cambio concreto:

```powershell
git switch -c mejora/nombre-del-cambio
git status --short
git diff
git add motoradar tests docs README.md
git commit -m "fix: describir el cambio de comportamiento"
```

Antes de commit revisar archivos staged y confirmar que no incluyen datos locales.
Usar identidad Git propia para commits humanos. Describir problema, comportamiento
resultante, verificación y limitaciones. No declarar una integración validada sin evidencia.

## Cambios de datos y proveedores

Preservar first_seen/histórico, mantener migraciones compatibles y ensayar con
bases temporales. Eventos y entregas deben poder recuperarse después de reiniciar.
Una fuente nueva necesita búsqueda, parseo, detalle cuando corresponda, errores
clasificados y pruebas. La paginación no debe terminar por deduplicación.

Consultar [arquitectura](docs/ARQUITECTURA.md), [prioridades](docs/ROADMAP.md) y
[registro de cambios](CHANGELOG.md). El repositorio local no implica publicación
en GitHub; configurar un remoto es una operación separada.
