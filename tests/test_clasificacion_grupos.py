"""Clasificacion de CUERPOS DE POST, la forma de texto que trae el feed.

El feed cronologico entrega el post completo, no un titulo de anuncio, y
`appraise` estaba calibrado sobre titulos de Marketplace: decidia por las
primeras palabras. Con un saludo delante ("Bom dia grupo!") un motor suelto
pasaba como MOTO EN PRESUPUESTO y "Vendo essa 125 andando, 800" —el ejemplo
que justifica el feed— se descartaba como desconocido.

Esta prueba mide el veredicto de NEGOCIO de punta a punta (llega o no llega),
no campos intermedios: fue exactamente lo que dejo pasar el fallo. El corpus
vive en `fixtures/group_corpus.json` para poder crecer con cada falso positivo
que aparezca en una corrida real.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from motoradar.config import Config, FilterConfig
from motoradar.money import FX
from motoradar.pipeline import prepare
from motoradar.sources.facebook import post_to_listing

CORPUS = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "group_corpus.json")
    .read_text(encoding="utf-8"))


def _listing(texto: str, identifier: str):
    return post_to_listing("999", {"id": identifier, "text": texto,
                                   "blocks": [texto], "href": "", "img": ""},
                           "jaguarao", False)


class CuerposDePost(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(budget=1000.0, fx={"offline": True},
                          filters=FilterConfig(cities=["jaguarao", "rio branco"]))
        self.fx = FX(None, offline=True)

    def _veredicto(self, texto: str, identifier: str = "1") -> bool:
        """True si el aviso llega (oportunidad o precio por confirmar)."""
        listing = _listing(texto, identifier)
        result = prepare([listing], self.cfg, self.fx, enrich_details=False)
        llega = bool(result.opportunities or result.unconfirmed)
        # Coherencia: lo que no llega tiene que estar contado como descartado.
        self.assertEqual(llega, not result.discarded, texto)
        return llega

    def test_corpus_completo(self):
        for numero, caso in enumerate(CORPUS["casos"], 1):
            with self.subTest(texto=caso["texto"][:60], clase=caso["clase"]):
                self.assertEqual(self._veredicto(caso["texto"], str(numero)),
                                 caso["alertar"],
                                 f"{caso['clase']}: {caso.get('_porque', '')}")

    def test_precision_y_recall_del_corpus(self):
        """Fija la linea base medida: 23/23. Si baja, algo se rompio."""
        aciertos = sum(
            self._veredicto(caso["texto"], str(numero)) == caso["alertar"]
            for numero, caso in enumerate(CORPUS["casos"], 1))
        total = len(CORPUS["casos"])
        self.assertEqual(aciertos, total, f"{aciertos}/{total} del corpus")

    def test_clasificaciones_detalladas_del_corpus(self):
        """Verifica que appraise() infiera con precision cada atributo cuando este declarado."""
        from motoradar.appraise import appraise
        for numero, caso in enumerate(CORPUS["casos"], 1):
            listing = _listing(caso["texto"], str(numero))
            a = appraise(listing)
            with self.subTest(texto=caso["texto"][:50], clase=caso["clase"]):
                if "kind" in caso:
                    self.assertEqual(a.kind, caso["kind"], f"kind: {caso['texto']}")
                if "condition" in caso:
                    self.assertEqual(a.condition, caso["condition"], f"condition: {caso['texto']}")
                if "doc_risk" in caso:
                    self.assertEqual(a.doc_risk, caso["doc_risk"], f"doc_risk: {caso['texto']}")
                if "wanted" in caso:
                    self.assertEqual(a.wanted, caso["wanted"], f"wanted: {caso['texto']}")
                if "shop_ad" in caso:
                    self.assertEqual(a.shop_ad, caso["shop_ad"], f"shop_ad: {caso['texto']}")
                if "is_buyable" in caso:
                    self.assertEqual(a.is_buyable, caso["is_buyable"], f"is_buyable: {caso['texto']}")


    def test_un_titulo_de_marketplace_sigue_clasificandose_por_posicion(self):
        """El cambio no puede degradar la via que ya funcionaba."""
        from motoradar.appraise import appraise
        from motoradar.models import Listing
        casos = {
            "Honda Biz C100 ES com partida eletrica": "moto",
            "Tanque factor 125 Yamaha": "accessory",
            "Carburador da CBX 200": "accessory",
            "Sucata Honda SH 150I 2017": "moto",
        }
        for titulo, esperado in casos.items():
            with self.subTest(titulo=titulo):
                listing = Listing("olx", "1", titulo, "https://olx.test/1", price=800)
                self.assertEqual(appraise(listing).kind, esperado)


if __name__ == "__main__":
    unittest.main()
