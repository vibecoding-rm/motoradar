# -*- coding: utf-8 -*-
"""Regresiones de tres bugs del scraper de Facebook, encontrados en una
revision senior. Cada clase fija UN bug con su caso reproductor y un par de
controles para que el arreglo no rompa lo que ya funcionaba.

BUG 1  parse_price unia numeros separados por espacio ("800 2015" -> 8002015),
       asi que una moto de R$800 quedaba fuera de presupuesto y se perdia.
BUG 2  detect_currency ignoraba las PALABRAS de moneda: "200 dolares" -> BRL.
BUG 3  _parse_card leia un titulo corto ("Titan preta") como ubicacion y
       dejaba el titulo vacio, sin nada que clasificar.
"""
import unittest

from motoradar.models import parse_price
from motoradar.money import detect_currency
from motoradar.sources.facebook import _first_price, _parse_card, card_to_listing


class TestBug1PrecioConNumeroPegado(unittest.TestCase):
    def test_parse_price_no_une_anio_de_4_digitos(self):
        # "2015"/"2019" (4 digitos) no es un grupo de millar valido, asi que ya
        # no se pega al precio: "800 2015" -> 800, no 8.002.015.
        self.assertEqual(parse_price("800 2015"), 800.0)
        self.assertEqual(parse_price("1.000 2019"), 1000.0)

    def test_first_price_ignora_el_anio_pegado(self):
        self.assertEqual(_first_price("R$ 800 2015")[0], 800.0)
        self.assertEqual(_first_price("R$ 1.000 2019")[0], 1000.0)
        # El caso comun (texto tras el precio) tampoco arrastra nada.
        self.assertEqual(_first_price("R$ 900 otimo estado")[0], 900.0)

    def test_card_de_800_no_se_va_de_presupuesto(self):
        card = {"text": "R$ 800 2015\nHonda CG Titan\nJaguarao, RS",
                "href": "https://www.facebook.com/marketplace/item/123456"}
        listing = card_to_listing(card)
        self.assertEqual(listing.price, 800.0)
        self.assertLessEqual(listing.price, 1000.0)

    def test_no_rompe_precios_validos(self):
        # Controles: separadores de millar y decimal deben seguir bien.
        self.assertEqual(parse_price("1.800"), 1800.0)
        self.assertEqual(parse_price("1.800.000"), 1800000.0)
        self.assertEqual(parse_price("1.250,00"), 1250.0)
        self.assertEqual(parse_price("1.250,50"), 1250.5)
        self.assertEqual(parse_price("850,00"), 850.0)
        self.assertEqual(parse_price("1 250"), 1250.0)   # millar con espacio (soportado)
        self.assertEqual(_first_price("R$ 900")[0], 900.0)

    def test_limite_conocido_precio_mas_cc_de_3_digitos(self):
        # LIMITACION documentada: "900 160" (precio + cilindrada de 3 digitos)
        # es indistinguible de un millar "900.160" y el parser lo lee como tal.
        # No se puede separar sin romper "1 250" -> 1250 (feature testeada en
        # test_radar). En la practica el precio de la tarjeta viene en su propio
        # renglon, asi que este choque casi no ocurre. Se fija para que quede
        # explicito si algun dia se decide cambiar el criterio.
        self.assertEqual(parse_price("900 160"), 900160.0)


class TestBug2MonedaEnPalabras(unittest.TestCase):
    def test_dolar_en_palabra_es_usd(self):
        self.assertEqual(detect_currency("vendo por 200 dolares"), "USD")
        self.assertEqual(detect_currency("moto barata, 300 dolar"), "USD")

    def test_reais_en_palabra_es_brl(self):
        self.assertEqual(detect_currency("valor 3 mil reais"), "BRL")

    def test_no_rompe_los_simbolos_ni_el_default(self):
        self.assertEqual(detect_currency("US$ 200"), "USD")
        self.assertEqual(detect_currency("U$S 200"), "USD")
        self.assertEqual(detect_currency("R$ 900"), "BRL")
        self.assertEqual(detect_currency("$U 15.000"), "UYU")
        self.assertEqual(detect_currency("15000 pesos"), "UYU")
        self.assertEqual(detect_currency("moto linda"), "BRL")  # default


class TestBug3TituloCortoNoEsUbicacion(unittest.TestCase):
    def test_titulo_de_dos_palabras_se_conserva(self):
        for text in ("R$ 900\nBike nova", "R$ 800\nTitan preta",
                     "R$ 500\nPop nova", "R$ 700\nBros branca"):
            title, loc = _parse_card(text)
            self.assertTrue(title, f"titulo vacio para {text!r}")
            self.assertEqual(loc, "", f"ubicacion inventada para {text!r}")

    def test_ubicacion_real_sigue_detectandose(self):
        # Con dos lineas, la ciudad con UF sigue siendo la ubicacion.
        title, loc = _parse_card("R$1.800\nMoto trilha\nArroio Grande, RS")
        self.assertEqual(title, "Moto trilha")
        self.assertEqual(loc, "Arroio Grande, RS")
        # Una sola linea que SI es ciudad conocida se toma como ubicacion.
        title, loc = _parse_card("R$ 900\nMelo")
        self.assertEqual(title, "")
        self.assertEqual(loc, "Melo")


if __name__ == "__main__":
    unittest.main()
