import sys

# La reconfiguracion de stdout a UTF-8 (para los acentos en la consola de
# Windows) vive en cli.main(), asi el comando instalado `motoradar` y este
# `python -m motoradar` se comportan igual.
from .cli import main

sys.exit(main())
