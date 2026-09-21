"""Mensaje de alerta de Telegram: botones tappables, layout limpio y la linea
de bajada de precio."""
import unittest

from motoradar.models import Listing
from motoradar.notify import _alert_buttons, _alert_text, _place


def L(**kw) -> Listing:
    base = dict(
        source="facebook", external_id="1", title="Honda CG 125 Fan 2015",
        url="https://www.facebook.com/marketplace/item/123", price=900.0,
        currency="BRL", location="Jaguarão, RS",
        raw={"alert_status": "confirmed", "condition": "andando"},
    )
    base.update(kw)
    return Listing(**base)


class Botones(unittest.TestCase):
    def test_dos_botones_abrir_y_maps(self):
        filas = _alert_buttons(L())
        self.assertEqual(len(filas), 1)
        textos = [b["text"] for b in filas[0]]
        self.assertIn("🔗 Abrir aviso", textos)
        self.assertIn("🗺️ Ver zona", textos)
        self.assertEqual(filas[0][0]["url"], "https://www.facebook.com/marketplace/item/123")
        self.assertIn("google.com/maps", filas[0][1]["url"])

    def test_ubicacion_de_grupo_no_pone_boton_de_mapa(self):
        filas = _alert_buttons(L(location="grupo 999"))
        textos = [b["text"] for b in filas[0]]
        self.assertIn("🔗 Abrir aviso", textos)
        self.assertNotIn("🗺️ Ver zona", textos)

    def test_place_limpia_el_sufijo_de_grupo(self):
        self.assertEqual(_place("Melo (grupo 123)"), "Melo")
        self.assertEqual(_place("grupo 999"), "")
        self.assertEqual(_place("Jaguarão, RS"), "Jaguarão, RS")


class Mensaje(unittest.TestCase):
    def test_confirmado_limpio_sin_jerga(self):
        t = _alert_text(L())
        self.assertIn("MOTO EN PRESUPUESTO", t)
        self.assertIn("Honda CG 125 Fan 2015", t)
        self.assertNotIn("via facebook", t)
        self.assertNotIn("SIN CONFIRMAR", t)

    def test_sin_confirmar_lo_dice(self):
        l = L(price=5.0, raw={"alert_status": "unconfirmed", "condition": "por verificar"})
        t = _alert_text(l)
        self.assertIn("PRECIO POR CONFIRMAR", t)
        self.assertIn("SIN CONFIRMAR", t)

    def test_bajada_de_precio_arriba_y_en_grande(self):
        l = L(price=900.0, raw={"alert_status": "confirmed", "condition": "andando",
                                "previous_price": 1200.0})
        t = _alert_text(l)
        self.assertIn("bajó", t)
        self.assertIn("→", t)
        self.assertIn("1.200", t)
        self.assertIn("900", t)

    def test_sin_papeles_se_marca(self):
        l = L(raw={"alert_status": "confirmed", "condition": "andando", "doc_risk": True})
        self.assertIn("sin papeles", _alert_text(l))


if __name__ == "__main__":
    unittest.main()
