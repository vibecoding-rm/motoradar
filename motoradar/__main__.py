import sys

# La consola de Windows usa cp1252 y rompe los acentos portugueses al
# imprimir (los datos estan bien en UTF-8, es solo la salida).
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from .cli import main

sys.exit(main())
