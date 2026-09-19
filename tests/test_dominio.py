"""Pruebas unitarias de dominio y clasificacion fronteriza (Hito M3).

Verifica exhaustivamente la clasificacion de marcas y modelos uruguayos,
evitacion de falsos positivos (vital, bicicletas winner), accesorios e indumentaria
en espanol, solicitudes de compra (wanted), servicios comerciales (shop_ad),
inmuebles, condicion del vehiculo (parts, project, runner) y riesgo documental.
"""
from __future__ import annotations

import unittest

from motoradar.appraise import appraise, extract_model, is_bait_price, is_decoy_sequence
from motoradar.models import Listing


def _make_listing(title: str, text: str = "", price: float | None = 500.0,
                  currency: str = "BRL", is_post: bool = True) -> Listing:
    raw = {
        "kind": "group" if is_post else "marketplace",
        "text_shape": "post" if is_post else "title",
        "description": text,
    }
    return Listing(
        source="facebook",
        external_id="t1",
        title=title,
        url="https://facebook.test/post1",
        price=price,
        currency=currency,
        location="Rio Branco",
        raw=raw,
    )


class TestUruguayanBrandsAndModels(unittest.TestCase):
    def test_marcas_uruguayas_reconocidas_sin_palabra_moto(self):
        casos = [
            ("Vendo Winner Fair 110 al dia", "winner-fair"),
            ("Winner Fair 110cc", "winner-fair"),
            ("Yumbo GS 125 impecable", "yumbo-gs-125"),
            ("Yumbo Dakar 125 impecable", "yumbo-dakar"),
            ("Baccio PX 110 andando bien", "baccio-px-110"),
            ("Baccio Classic", "baccio-classic"),
            ("Zanella Due 50 funcionando", "zanella-due"),
            ("Zanella 110 al dia", "zanella"),
            ("Keeway Target 125 impecable", "keeway-target"),
            ("Mondial TD 125 modelo nuevo", "mondial-td"),
            ("Vince Lifan 110 andando", "vince"),
            ("Vital 110cc al dia", "vital"),
            ("Vendo Vital VX 110", "vital-vx"),
        ]
        for c, expected_model in casos:
            with self.subTest(caso=c):
                l = _make_listing(c)
                a = appraise(l)
                self.assertEqual(a.kind, "moto")
                self.assertTrue(a.is_buyable)
                if expected_model:
                    self.assertEqual(extract_model(c), expected_model)

    def test_evitar_colisiones_con_palabras_genericas(self):
        falsos = [
            "Parte vital de la maquina",
            "Vendo mesa de luz de vital importancia",
            "Vendo repuesto para auto pieza vital",
            "Vendo motor de heladera parte vital",
        ]
        for f in falsos:
            with self.subTest(caso=f):
                a = appraise(_make_listing(f))
                self.assertNotEqual(a.kind, "moto")
                self.assertFalse(a.is_buyable)

    def test_bicicletas_winner_descartadas(self):
        bicis = [
            "Vendo bicicleta Winner rodado 26",
            "Bicicleta Winner impecable",
            "Winner rodado 26",
            "Chiva Winner 18 cambios",
        ]
        for b in bicis:
            with self.subTest(caso=b):
                a = appraise(_make_listing(b))
                self.assertNotEqual(a.kind, "moto")
                self.assertFalse(a.is_buyable)


class TestSpanishAccessoriesAndParts(unittest.TestCase):
    def test_accesorios_e_indumentaria_en_espanol(self):
        casos = [
            "Vendo casco LS2 talle L impecable",
            "Campera con protecciones para moto",
            "Cubiertas 17 de moto nuevas el par",
            "Llanta delantera con freno de disco",
            "Repuestos varios de moto 110 foco cadena y plasticos",
            "Escape deportivo para moto 125",
            "Freno de disco delantero de moto",
            "Carburador 125 de moto nuevo",
        ]
        for c in casos:
            with self.subTest(caso=c):
                a = appraise(_make_listing(c))
                self.assertIn(a.kind, ("accessory", "unknown"))
                self.assertFalse(a.is_buyable)


class TestSpanishBuyerSearch(unittest.TestCase):
    def test_solicitudes_de_compra_en_espanol(self):
        casos = [
            "Busco moto 110 pago contado en mano",
            "Alguien tiene moto barata para la venta?",
            "Necesito moto urgente para trabajar",
            "Ando buscando moto para comprar",
            "Compro moto para repuesto pago contado",
            "Quien vende moto 125 barata?",
            "Se busca moto con o sin papeles",
        ]
        for c in casos:
            with self.subTest(caso=c):
                a = appraise(_make_listing(c))
                self.assertTrue(a.wanted)
                self.assertFalse(a.is_buyable)


class TestSpanishCommercialServices(unittest.TestCase):
    def test_talleres_y_servicios_comerciales_en_espanol(self):
        casos = [
            "Taller mecanico de motos reparaciones electricas",
            "Mecanica ligera de motos y afinaciones",
            "Hago fletes de motos y traslados",
            "Servicio de cadeteria en moto y encomiendas",
            "Reparaciones de motos presupuestos",
        ]
        for c in casos:
            with self.subTest(caso=c):
                a = appraise(_make_listing(c))
                self.assertTrue(a.shop_ad)
                self.assertFalse(a.is_buyable)


class TestSpanishRealEstate(unittest.TestCase):
    def test_propiedades_e_inmuebles_en_espanol(self):
        casos = [
            "Vendo solar en Rio Branco 300m2 acepto moto",
            "Alquilo local comercial tomo moto en permuta",
            "Vendo propiedad con galpon permuto por moto",
            "Terreno en Rio Branco acepto moto",
        ]
        for c in casos:
            with self.subTest(caso=c):
                a = appraise(_make_listing(c))
                self.assertIn(a.kind, ("other", "accessory", "unknown"))
                self.assertFalse(a.is_buyable)


class TestVehicleConditionSpanish(unittest.TestCase):
    def test_condiciones_desarme_y_proyecto(self):
        self.assertEqual(appraise(_make_listing("Vendo moto para desarme")).condition, "parts")
        self.assertEqual(appraise(_make_listing("Moto para repuestos con faltantes")).condition, "parts")
        self.assertEqual(appraise(_make_listing("Vendo moto desarmada")).condition, "parts")
        self.assertEqual(appraise(_make_listing("Vendo moto averiada no arranca")).condition, "project")
        self.assertEqual(appraise(_make_listing("Moto para reparar o proyecto")).condition, "project")
        self.assertEqual(appraise(_make_listing("Winner 110 con motor desarmado para proyecto o armar")).condition, "project")
        self.assertEqual(appraise(_make_listing("Vendo Winner 110 andando bien")).condition, "runner")


class TestDocumentationRiskSpanish(unittest.TestCase):
    def test_riesgo_documental_en_frontera(self):
        riesgos = [
            "Vendo Winner Fair 110 sin libreta",
            "Vendo moto 125 solo libreta municipal",
            "Yumbo 125 debe patente",
            "Baccio 110 sin papeles",
            "Zanella 110 no transfiere solo para campo",
        ]
        for r in riesgos:
            with self.subTest(caso=r):
                a = appraise(_make_listing(r))
                self.assertTrue(a.doc_risk)
                self.assertTrue(a.is_buyable)

    def test_documentacion_al_dia_sin_riesgo(self):
        sin_riesgo = [
            "Winner Fair 110 papeles al dia",
            "Baccio PX empadronada en Cerro Largo",
            "Yumbo 125 con patente al dia",
        ]
        for s in sin_riesgo:
            with self.subTest(caso=s):
                a = appraise(_make_listing(s))
                self.assertFalse(a.doc_risk)
                self.assertTrue(a.is_buyable)


class TestBaitAndDecoyPrices(unittest.TestCase):
    def test_precios_senuelo_y_secuencias(self):
        self.assertTrue(is_decoy_sequence(1234))
        self.assertTrue(is_decoy_sequence("12345"))
        self.assertTrue(is_decoy_sequence(1111))
        self.assertTrue(is_decoy_sequence("2222"))
        self.assertFalse(is_decoy_sequence(800))
        self.assertFalse(is_decoy_sequence("6000"))

        self.assertTrue(is_bait_price(0))
        self.assertTrue(is_bait_price(1))
        self.assertTrue(is_bait_price(100))
        self.assertTrue(is_bait_price(1234))
        self.assertFalse(is_bait_price(500))
        self.assertFalse(is_bait_price(1000))


if __name__ == "__main__":
    unittest.main()
