import sys

# `console()` carga el `.env` y reconfigura stdout a UTF-8 antes de despachar,
# igual que el comando instalado `motoradar`. `main()` (sin carga de .env) queda
# para los tests.
from .cli import console

sys.exit(console())
