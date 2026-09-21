"""El cargador de .env (_load_dotenv) que usan los entry points reales.

Se prueba aislando os.environ (patch.dict clear=True, que restaura al salir) y
el cwd, para no filtrar nada a los demas tests."""
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from motoradar.cli import _load_dotenv


class LoadDotenv(unittest.TestCase):
    def _load_with(self, contenido: str, previo: dict | None = None) -> dict:
        prev_cwd = os.getcwd()
        with TemporaryDirectory() as td:
            (Path(td) / ".env").write_text(contenido, encoding="utf-8")
            os.chdir(td)
            try:
                with patch.dict(os.environ, previo or {}, clear=True):
                    _load_dotenv()
                    return dict(os.environ)
            finally:
                os.chdir(prev_cwd)

    def test_carga_claves_validas(self):
        env = self._load_with("TELEGRAM_BOT_TOKEN=abc\nTELEGRAM_CHAT_ID=123,456\n")
        self.assertEqual(env.get("TELEGRAM_BOT_TOKEN"), "abc")
        self.assertEqual(env.get("TELEGRAM_CHAT_ID"), "123,456")

    def test_no_pisa_una_variable_ya_definida(self):
        # Una variable real del entorno gana sobre el .env.
        env = self._load_with("TELEGRAM_BOT_TOKEN=delarchivo\n",
                               previo={"TELEGRAM_BOT_TOKEN": "delentorno"})
        self.assertEqual(env.get("TELEGRAM_BOT_TOKEN"), "delentorno")

    def test_ignora_comentarios_claves_invalidas_y_comillas(self):
        env = self._load_with(
            '# comentario\n'
            '\n'
            'facebook user= x\n'          # clave con espacio: no es variable valida
            'TELEGRAM_BOT_TOKEN="entre comillas"\n')
        self.assertNotIn("facebook user", env)
        self.assertNotIn("facebook", env)
        self.assertEqual(env.get("TELEGRAM_BOT_TOKEN"), "entre comillas")


if __name__ == "__main__":
    unittest.main()
