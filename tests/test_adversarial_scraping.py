"""Adversarial Test Harness for Milestone M5 (Tier 5 Adversarial Coverage Hardening).

Covers:
1. Scraping exception isolation & DOM variations (missing tags, whitespace, multiline seller notes, missing price lines).
2. Border location edge cases (bilingual border expressions, tricky deceptive locations, Rio Branco disambiguation).
3. Multi-locale date parsing in parse_post_age (relative ES/PT, abbreviations, punctuation, invalid/malformed inputs).
4. Detail enrichment and pipeline categorizations (timeout handling, partial error isolation, partition invariants).
5. Empirical defect isolation: Down payment regex greediness in models.py and multiline card note truncation in facebook.py.

100% offline, zero network, zero live browsers, zero credential leaks.
"""
from __future__ import annotations

import io
from pathlib import Path
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest.mock import Mock, patch

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from motoradar.appraise import appraise
from motoradar.config import Config, FilterConfig
from motoradar.enrich import (
    _is_facebook_item,
    detail_price_text,
    enrich_facebook,
    extract_description,
    fix_bait_prices,
)
from motoradar.filters import detect_location_cue, is_non_target_location, matches
from motoradar.models import Listing, parse_price_text, _NON_TOTAL_PRICE, strip_accents, _NOT_PRICE
from motoradar.money import FX
from motoradar.pipeline import SearchResult, normalize, prepare
from motoradar.sources.base import SessionExpired, SourceError
from motoradar.sources.facebook import (
    FacebookSource,
    _looks_like_location,
    _parse_card,
    card_to_listing,
    feed_dom_broken,
    market_dom_broken,
    parse_post_age,
    post_to_listing,
)


class TestAdversarialDOMAndScrapingIsolation(unittest.TestCase):
    """Adversarial stress testing of DOM parsing and exception isolation in scraping."""

    def test_card_to_listing_with_empty_and_corrupted_dicts(self):
        """Card parser handles empty, missing, or non-string fields safely."""
        # Completely empty dict
        l_empty = card_to_listing({})
        self.assertEqual(l_empty.source, "facebook")
        self.assertTrue(l_empty.external_id.startswith("card-"))
        self.assertEqual(l_empty.title, "")
        self.assertIsNone(l_empty.price)
        self.assertTrue(l_empty.raw.get("location_missing"))

        # Dict with None values
        l_nones = card_to_listing({"text": None, "href": None, "img": None})
        self.assertEqual(l_nones.title, "")
        self.assertIsNone(l_nones.price)
        self.assertTrue(l_nones.external_id.startswith("card-"))

        # Dict with int/unexpected types in text and href
        l_types = card_to_listing({"text": 12345, "href": 67890})
        self.assertEqual(l_types.title, "12345")
        self.assertEqual(l_types.external_id, "67890")

    def test_card_to_listing_with_whitespace_and_newlines(self):
        """Card parser normalizes whitespace, tabs, and multiple blank lines."""
        raw_text = "  \n\t  R$ 850  \n\n\t  Honda CG 125 Fan  \t \n\n  Jaguarão, RS  \n\n"
        listing = card_to_listing({
            "href": "https://www.facebook.com/marketplace/item/1001/",
            "text": raw_text,
        })
        self.assertEqual(listing.external_id, "1001")
        self.assertEqual(listing.price, 850.0)
        self.assertEqual(listing.currency, "BRL")
        self.assertEqual(listing.title, "Honda CG 125 Fan")
        self.assertEqual(listing.location, "Jaguarão, RS")

    def test_card_to_listing_without_price_classified_safely(self):
        """Card with no price line sets price=None without confusing title or location."""
        raw_text = "Honda CG 125 para desarme\nJaguarão, RS"
        listing = card_to_listing({
            "href": "https://www.facebook.com/marketplace/item/1002/",
            "text": raw_text,
        })
        self.assertIsNone(listing.price)
        self.assertEqual(listing.title, "Honda CG 125 para desarme")
        self.assertEqual(listing.location, "Jaguarão, RS")

    def test_card_to_listing_free_and_zero_price_not_zero_dollars(self):
        """'Gratis', 'Free', 'De graça', and 'R$ 0' must NOT become 0.0 numeric price."""
        free_variants = [
            "Gratis\nHonda CG 125 quebrada\nJaguarão, RS",
            "Free\nHonda CG 125 quebrada\nJaguarão, RS",
            "De graça\nHonda CG 125 quebrada\nJaguarão, RS",
            "De graca\nHonda CG 125 quebrada\nJaguarão, RS",
            "R$ 0\nHonda CG 125 quebrada\nJaguarão, RS",
            "R$0,00\nHonda CG 125 quebrada\nJaguarão, RS",
        ]
        for text in free_variants:
            with self.subTest(text=text.splitlines()[0]):
                l = card_to_listing({
                    "href": "https://www.facebook.com/marketplace/item/1003/",
                    "text": text,
                })
                self.assertIsNone(l.price, f"Expected None price for '{text.splitlines()[0]}', got {l.price}")
                self.assertIn("el aviso no publica precio", l.raw.get("card_price_evidence", ""))

    def test_card_to_listing_missing_location_flagged_and_preserved(self):
        """Card with price and title but no location line sets location_missing=True."""
        raw_text = "R$ 900\nHonda CG 125"
        l = card_to_listing({
            "href": "https://www.facebook.com/marketplace/item/1004/",
            "text": raw_text,
        })
        self.assertEqual(l.title, "Honda CG 125")
        self.assertEqual(l.location, "")
        self.assertTrue(l.raw.get("location_missing"))
        self.assertEqual(l.price, 900.0)

    def test_card_to_listing_missing_title_flagged_without_inventing_city(self):
        """Card with price and location but no title does NOT copy location into title."""
        raw_text = "R$ 900\nJaguarão, RS"
        l = card_to_listing({
            "href": "https://www.facebook.com/marketplace/item/1005/",
            "text": raw_text,
        })
        self.assertEqual(l.title, "")
        self.assertEqual(l.location, "Jaguarão, RS")
        self.assertEqual(l.raw.get("card_parse"), "la tarjeta no traia titulo")

    def test_card_to_listing_missing_href_generates_synthetic_card_id(self):
        """Card missing href generates a deterministic SHA-256 card- prefixed ID."""
        l1 = card_to_listing({"text": "R$ 800\nHonda CG 125\nJaguarão, RS"})
        l2 = card_to_listing({"text": "R$ 800\nYamaha YBR 125\nJaguarão, RS"})
        self.assertTrue(l1.external_id.startswith("card-"))
        self.assertTrue(l2.external_id.startswith("card-"))
        self.assertNotEqual(l1.external_id, l2.external_id)
        self.assertIn("sin enlace", l1.raw.get("url_confidence", ""))

    def test_card_to_listing_strikethrough_rebaja_extracts_lowest_price(self):
        """Discounted card (strikethrough + new price) extracts the current first price."""
        raw_text = "R$ 800\nR$ 1.200\n2014 Yamaha Factor\nJaguarão, RS"
        l = card_to_listing({
            "href": "https://www.facebook.com/marketplace/item/1006/",
            "text": raw_text,
        })
        self.assertEqual(l.price, 800.0)
        self.assertEqual(l.title, "2014 Yamaha Factor")
        self.assertEqual(l.location, "Jaguarão, RS")

    def test_post_to_listing_empty_and_corrupted_post_dict(self):
        """Group post parser handles empty or malformed dictionaries without exceptions."""
        l_empty = post_to_listing("999", {}, "", True)
        self.assertEqual(l_empty.source, "facebook")
        self.assertTrue(l_empty.external_id.startswith("g999-"))
        self.assertIsNone(l_empty.price)
        self.assertEqual(l_empty.location, "grupo 999")
        self.assertEqual(l_empty.currency, "BRL")

        l_none = post_to_listing("999", {"text": None, "id": None, "blocks": None}, "jaguarao", False)
        self.assertEqual(l_none.title, "")
        self.assertIsNone(l_none.price)

    def test_post_to_listing_extracts_price_from_secondary_blocks(self):
        """Group post with price separated in secondary blocks extracts the price."""
        post = {
            "id": "post_sec_1",
            "text": "Vendo moto Honda CG 125 em perfeito estado",
            "blocks": [
                "Vendo moto Honda CG 125 em perfeito estado",
                "Valor 950 reais",
                "Tratar no direct",
            ],
            "href": "https://www.facebook.com/groups/999/posts/post_sec_1/",
        }
        l = post_to_listing("999", post, "jaguarao", False)
        self.assertEqual(l.price, 950.0)
        self.assertEqual(l.currency, "BRL")

    def test_post_to_listing_scrubs_uruguayan_and_brazilian_phone_numbers(self):
        """Seller personal contact data (phones, emails, urls) is redacted from title & description."""
        post = {
            "id": "scrub_1",
            "text": "Vendo CG 125 por 900 reais chamar no whats 53991234567 o al cel 099123456 email juan@test.uy o ver https://motos.test/1",
        }
        l = post_to_listing("999", post, "jaguarao", False)
        self.assertNotIn("53991234567", l.title)
        self.assertNotIn("099123456", l.title)
        self.assertNotIn("juan@test.uy", l.title)
        self.assertNotIn("https://motos.test/1", l.title)
        self.assertIn("[tel]", l.title)
        self.assertIn("[email]", l.title)
        self.assertIn("[enlace]", l.title)

    def test_post_to_listing_punctuated_installments_and_downpayments(self):
        """Down payment ('entrada') or installment separated by comma does not set the listing price."""
        post_down = {
            "id": "down_1",
            "text": "Vendo Honda Falcon valor 12000 reais, entrada de 800 reais",
        }
        l_down = post_to_listing("999", post_down, "jaguarao", False)
        self.assertEqual(l_down.price, 12000.0)

        post_inst = {
            "id": "inst_1",
            "text": "Vendo moto em 10x de 100 reais sem entrada",
        }
        l_inst = post_to_listing("999", post_inst, "jaguarao", False)
        self.assertIsNone(l_inst.price)

    def test_source_marketplace_generator_exception_isolation(self):
        """Corrupted card objects inside evaluate(ITEM_JS) do not abort the marketplace loop."""
        source = FacebookSource()
        cfg = Config(filters=FilterConfig(cities=["jaguarao"]))
        page = Mock()
        page.goto.return_value = None
        # Return 1 valid card, 1 corrupted card (None causing AttributeError on card.get), 1 valid card
        cards = [
            {"href": "https://www.facebook.com/marketplace/item/101/", "text": "R$ 800\nHonda CG 125\nJaguarão, RS"},
            None,
            {"href": "https://www.facebook.com/marketplace/item/102/", "text": "R$ 900\nYamaha YBR 125\nJaguarão, RS"},
        ]
        page.evaluate.return_value = cards
        opts = {"marketplace_queries": ["moto"], "scroll_rounds": 0, "scroll_pause": 0}

        with patch("motoradar.sources.facebook._validate_navigation"), \
             patch("motoradar.sources.facebook._scroll"), \
             redirect_stdout(io.StringIO()):
            yielded = list(source._marketplace(page, cfg, opts, rounds=0, pause=0))

        self.assertEqual(len(yielded), 2)
        self.assertEqual(yielded[0].external_id, "101")
        self.assertEqual(yielded[1].external_id, "102")
        self.assertEqual(len(source.errors), 1)
        self.assertIn("tarjeta malformada omitida", source.errors[0])

    def test_source_group_feed_generator_exception_isolation(self):
        """Corrupted posts in group feed do not abort subsequent posts."""
        source = FacebookSource()
        page = Mock()
        posts = [
            {"id": "p1", "text": "Vendo moto 800 reais"},
            None,
            {"id": "p2", "text": "Vendo moto 900 reais"},
        ]
        with patch.object(source, "_read_feed", return_value=posts), \
             patch("motoradar.sources.facebook._validate_navigation"), \
             redirect_stdout(io.StringIO()):
            yielded = list(source._group_feed(page, "999", "jaguarao", False, {}))

        self.assertEqual(len(yielded), 2)
        self.assertEqual(yielded[0].external_id, "g999-p1")
        self.assertEqual(yielded[1].external_id, "g999-p2")
        self.assertEqual(len(source.errors), 1)
        self.assertIn("post malformado omitido", source.errors[0])

    def test_dom_health_detects_blind_vs_empty_feed(self):
        """DOM health correctly distinguishes zero results (healthy empty) from broken DOM."""
        # Healthy empty feed: explicit empty marker found
        self.assertFalse(feed_dom_broken({"feed": True, "children": 0, "empty": True}))
        # Broken DOM: 0 children and NO empty marker
        self.assertTrue(feed_dom_broken({"feed": True, "children": 0, "empty": False}))
        # Broken DOM: feed container missing entirely without empty marker
        self.assertTrue(feed_dom_broken({"feed": False, "children": 0, "empty": False, "main": True}))
        # Healthy Marketplace empty:
        self.assertFalse(market_dom_broken({"anchors": 0, "empty": True, "main": True}))
        # Broken Marketplace DOM:
        self.assertTrue(market_dom_broken({"anchors": 0, "empty": False, "main": True}))


class TestAdversarialBorderLocationFiltering(unittest.TestCase):
    """Adversarial stress testing of cross-border location matching and deceptive exclusions."""

    def setUp(self):
        self.f = FilterConfig(cities=["jaguarao", "rio branco"])

    def _make_l(self, loc: str, title: str = "Honda CG 125", kind: str = "marketplace") -> Listing:
        return Listing(
            source="facebook",
            external_id="geo_test",
            title=title,
            url="https://www.facebook.com/marketplace/item/geo_test/",
            price=800.0,
            currency="BRL",
            location=loc,
            raw={"kind": kind},
        )

    def test_positive_border_expressions_jaguarao_and_rio_branco(self):
        """Bilingual border expressions within target zone pass geographic filters."""
        positives = [
            "frontera Jaguarao/Rio Branco",
            "Frontera Jaguarão - Río Branco",
            "Jaguarao centro",
            "Jaguarão, RS",
            "Jaguarão - RS",
            "Rio Branco barrio comercial",
            "Río Branco centro",
            "Rio Branco, Cerro Largo, Uruguay",
            "Rio Branco, Cerro Largo",
            "Rio Branco / Jaguarao",
            "Jaguarao",
            "Rio Branco",
        ]
        for loc in positives:
            with self.subTest(loc=loc):
                l = self._make_l(loc)
                ok, reason = matches(l, self.f)
                self.assertTrue(ok, f"Expected '{loc}' to pass, got reason: '{reason}'")

    def test_deceptive_border_expressions_rejected_by_negative_filter(self):
        """Deceptive ads mentioning distant cities with delivery/freight to border are discarded."""
        deceptives = [
            ("en Pelotas pero envio a Jaguarao", "pelotas"),
            ("Melo con flete a Rio Branco", "melo"),
            ("Bage pero llevo a Jaguarao", "bage"),
            ("Porto Alegre con envio gratis a Rio Branco", "porto alegre"),
            ("Arroio Grande pero puedo arrimar a Jaguarao", "arroio grande"),
            ("Herval pero entrego en Jaguarao", "herval"),
            ("Vendo moto en Treinta y Tres hago envio a Rio Branco", "treinta y tres"),
            ("Santa Vitoria do Palmar con entrega en Jaguarao", "santa vitoria do palmar"),
            ("Chui con flete a Rio Branco", "chui"),
            ("Cangucu entrego en Jaguarao", "cangucu"),
            ("Piratini voy a Jaguarao", "piratini"),
            ("Acegua envio a Rio Branco", "acegua"),
        ]
        for text, matched_city in deceptives:
            with self.subTest(text=text):
                l = Listing(
                    source="facebook",
                    external_id="deceptive_1",
                    title=f"Honda CG 125 - {text}",
                    url="https://facebook.com/item/1",
                    price=800.0,
                    location="Jaguarão, RS",
                    raw={"kind": "marketplace", "description": text},
                )
                ok, reason = matches(l, self.f)
                self.assertFalse(ok, f"Expected '{text}' to be rejected, but it passed.")
                self.assertEqual(reason, "ciudad no coincide")
                self.assertTrue(is_non_target_location(text))

    def test_rio_grande_do_sul_state_accepted_but_rio_grande_city_rejected(self):
        """'Rio Grande do Sul' (state) passes, while 'Rio Grande' (city 140km away) is rejected."""
        # Target city with state spelled out
        l_state = self._make_l("Jaguarão, Rio Grande do Sul")
        ok_state, reason_state = matches(l_state, self.f)
        self.assertTrue(ok_state, f"Expected 'Jaguarão, Rio Grande do Sul' to pass, got: '{reason_state}'")

        # Distant city of Rio Grande
        l_city = self._make_l("Rio Grande, RS")
        ok_city, reason_city = matches(l_city, self.f)
        self.assertFalse(ok_city, "Expected 'Rio Grande, RS' to be rejected")
        self.assertEqual(reason_city, "ciudad no coincide")

    def test_rio_branco_cerro_largo_accepted_but_melo_cerro_largo_rejected(self):
        """Río Branco (Cerro Largo) passes, while Melo (Cerro Largo capital) is rejected."""
        l_rb = self._make_l("Rio Branco, Cerro Largo")
        ok_rb, _ = matches(l_rb, self.f)
        self.assertTrue(ok_rb)

        l_melo = self._make_l("Melo, Cerro Largo")
        ok_melo, reason_melo = matches(l_melo, self.f)
        self.assertFalse(ok_melo)
        self.assertEqual(reason_melo, "ciudad no coincide")

    def test_rio_branco_disambiguation_metro_districts_rejected(self):
        """Metropolitan neighborhoods named 'Rio Branco' (Porto Alegre, Novo Hamburgo, Acre) are rejected."""
        metro_variants = [
            "Bairro Rio Branco, Porto Alegre",
            "Rio Branco, Porto Alegre",
            "Rio Branco, Novo Hamburgo",
            "Rio Branco, Canoas",
            "Rio Branco, RS",  # Neighborhood in RS metro
            "Rio Branco, Acre",
            "Rio Branco, AC",
            "Rio Branco / Brasil",
        ]
        for loc in metro_variants:
            with self.subTest(loc=loc):
                l = self._make_l(loc)
                ok, reason = matches(l, self.f)
                self.assertFalse(ok, f"Expected '{loc}' to be rejected as metropolitan Rio Branco")
                self.assertEqual(reason, "ciudad no coincide")

    def test_unmapped_group_rejects_by_default_unless_explicit_target_cue(self):
        """An unmapped group post is rejected by default, but passes if text explicitly states target city."""
        # Unmapped group with generic text
        post_generic = {"id": "g1", "text": "Vendo Honda CG 125 valor 800 reais"}
        l_generic = post_to_listing("999", post_generic, "", True)
        ok_gen, reason_gen = matches(l_generic, self.f)
        self.assertFalse(ok_gen)
        self.assertEqual(reason_gen, "ciudad no coincide")

        # Unmapped group with explicit Jaguarão cue
        post_jag = {"id": "g2", "text": "Vendo Honda CG 125 valor 800 reais en Jaguarao"}
        l_jag = post_to_listing("999", post_jag, "", True)
        ok_jag, _ = matches(l_jag, self.f)
        self.assertTrue(ok_jag)

        # Unmapped group with explicit Río Branco cue
        post_rb = {"id": "g3", "text": "Vendo Yumbo 125 valor 800 reais en Rio Branco"}
        l_rb = post_to_listing("999", post_rb, "", True)
        ok_rb, _ = matches(l_rb, self.f)
        self.assertTrue(ok_rb)

    def test_unmapped_group_with_distant_city_cue_rejected(self):
        """An unmapped group post mentioning a distant city is explicitly identified and rejected."""
        post_pelotas = {"id": "g4", "text": "Vendo Honda CG 125 valor 800 reais en Pelotas"}
        l_pel = post_to_listing("999", post_pelotas, "", True)
        self.assertIn("pelotas", l_pel.location.lower())
        ok_pel, reason_pel = matches(l_pel, self.f)
        self.assertFalse(ok_pel)
        self.assertEqual(reason_pel, "ciudad no coincide")

    def test_filter_matches_marketplace_card_missing_location_preserved(self):
        """BUG-FB-GEO-05: Marketplace cards with missing location pass filters to allow enrichment."""
        l_no_loc = Listing(
            source="facebook",
            external_id="no_loc_card",
            title="Honda CG 125",
            url="https://facebook.com/item/no_loc",
            price=800.0,
            location="",
            raw={"kind": "marketplace", "location_missing": True},
        )
        ok, reason = matches(l_no_loc, self.f)
        self.assertTrue(ok, f"Expected marketplace card without location to be preserved, got: '{reason}'")

    def test_case_and_accent_insensitivity_in_border_locations(self):
        """Matching is fully insensitive to case and diacritics (jaguarão, JAGUARAO, Río Branco, RIO BRANCO)."""
        variants = ["JAGUARÃO", "jaguarao", "Jaguarao", "RÍO BRANCO", "rio branco", "Río Branco"]
        for v in variants:
            with self.subTest(v=v):
                l = self._make_l(v)
                ok, _ = matches(l, self.f)
                self.assertTrue(ok, f"Failed on case/accent variant '{v}'")


class TestAdversarialDateParsingAndLocale(unittest.TestCase):
    """Adversarial stress testing of parse_post_age across Spanish and Portuguese expressions."""

    def setUp(self):
        self.ref_now = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)

    def test_spanish_relative_date_variations_and_abbreviations(self):
        """All Spanish relative formats (hours, minutes, days, weeks, months, years) parse accurately."""
        cases = [
            ("hace 2 horas", 7200.0),
            ("hace 2 h", 7200.0),
            ("hace 2 h.", 7200.0),
            ("hace 2 hs", 7200.0),
            ("hace 2 hrs.", 7200.0),
            ("2 h", 7200.0),
            ("2h", 7200.0),
            ("hace 1 hora", 3600.0),
            ("hace 15 minutos", 900.0),
            ("15 min", 900.0),
            ("15 min.", 900.0),
            ("15 mins", 900.0),
            ("15m", 900.0),
            ("hace 3 dias", 259200.0),
            ("hace 3 días", 259200.0),
            ("3 d", 259200.0),
            ("3 d.", 259200.0),
            ("3d", 259200.0),
            ("hace 1 semana", 604800.0),
            ("1 sem", 604800.0),
            ("1 sem.", 604800.0),
            ("hace 2 semanas", 1209600.0),
            ("hace 1 mes", 2592000.0),
            ("hace 2 meses", 5184000.0),
            ("hace 1 año", 31536000.0),
            ("hace 1 ano", 31536000.0),
            ("1 a", 31536000.0),
            ("10 s", 10.0),
            ("30 seg", 30.0),
            ("45 segs", 45.0),
            ("15 segundos", 15.0),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                got = parse_post_age(text, now=self.ref_now)
                self.assertEqual(got, expected, f"Failed on '{text}': got {got}, expected {expected}")

    def test_portuguese_relative_date_variations_and_abbreviations(self):
        """All Portuguese relative formats (há, faz, atrás) parse accurately."""
        cases = [
            ("há 2 horas", 7200.0),
            ("ha 2 horas", 7200.0),
            ("faz 2 horas", 7200.0),
            ("2 horas atrás", 7200.0),
            ("2 horas atras", 7200.0),
            ("há 15 minutos", 900.0),
            ("15 min atrás", 900.0),
            ("faz 10 minutos", 600.0),
            ("há 3 dias", 259200.0),
            ("3 dias atrás", 259200.0),
            ("faz 3 dias", 259200.0),
            ("há 1 semana", 604800.0),
            ("1 semana atrás", 604800.0),
            ("faz 1 semana", 604800.0),
            ("há 2 meses", 5184000.0),
            ("faz 2 meses", 5184000.0),
            ("há 1 ano", 31536000.0),
            ("1 ano atrás", 31536000.0),
            ("faz 1 ano", 31536000.0),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                got = parse_post_age(text, now=self.ref_now)
                self.assertEqual(got, expected, f"Failed on '{text}': got {got}, expected {expected}")

    def test_immediate_and_day_offset_markers(self):
        """Immediate markers (hoy/hoje/ahora/agora) and offset markers (ayer/ontem/anteayer)."""
        immediates = [
            "hoy", "hoje", "ahora", "agora", "hace un momento", "hace poco",
            "agora mesmo", "hoy a las 14:30", "hoje às 14:30"
        ]
        for imm in immediates:
            with self.subTest(imm=imm):
                self.assertEqual(parse_post_age(imm, now=self.ref_now), 0.0)

        # Yesterday / ontem -> 86400.0
        self.assertEqual(parse_post_age("ayer", now=self.ref_now), 86400.0)
        self.assertEqual(parse_post_age("ontem", now=self.ref_now), 86400.0)
        self.assertEqual(parse_post_age("ayer a las 20:00", now=self.ref_now), 86400.0)
        self.assertEqual(parse_post_age("ontem às 18:00", now=self.ref_now), 86400.0)

        # Day before yesterday -> 172800.0
        self.assertEqual(parse_post_age("anteayer", now=self.ref_now), 172800.0)
        self.assertEqual(parse_post_age("anteontem", now=self.ref_now), 172800.0)
        self.assertEqual(parse_post_age("anteayer a las 10:00", now=self.ref_now), 172800.0)

    def test_absolute_dates_with_all_twelve_months_and_abbreviations(self):
        """Absolute date formats across all 12 months in Spanish and Portuguese."""
        months_es = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                     "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        months_pt = ["janeiro", "fevereiro", "março", "abril", "maio", "junho",
                     "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]

        for m_num, (es, pt) in enumerate(zip(months_es, months_pt, strict=True), 1):
            if m_num <= 9:  # Past months in 2026 (ref is Sept 19, 2026)
                day = 10
                expected = (self.ref_now - datetime(2026, m_num, day, tzinfo=timezone.utc)).total_seconds()
                self.assertEqual(parse_post_age(f"{day} de {es}", now=self.ref_now), expected)
                self.assertEqual(parse_post_age(f"{day} de {pt}", now=self.ref_now), expected)

        # Short abbreviations
        self.assertIsNotNone(parse_post_age("15 de ene de 2024", now=self.ref_now))
        self.assertIsNotNone(parse_post_age("15 de jan de 2024", now=self.ref_now))
        self.assertIsNotNone(parse_post_age("20 de feb", now=self.ref_now))
        self.assertIsNotNone(parse_post_age("20 de fev", now=self.ref_now))
        self.assertIsNotNone(parse_post_age("10 de mar", now=self.ref_now))
        self.assertIsNotNone(parse_post_age("12 de abr", now=self.ref_now))
        self.assertIsNotNone(parse_post_age("1 de mai", now=self.ref_now))
        self.assertIsNotNone(parse_post_age("1 de may", now=self.ref_now))
        self.assertIsNotNone(parse_post_age("15 de jun", now=self.ref_now))
        self.assertIsNotNone(parse_post_age("20 de jul", now=self.ref_now))
        self.assertIsNotNone(parse_post_age("15 de ago", now=self.ref_now))
        self.assertIsNotNone(parse_post_age("12 de sep", now=self.ref_now))
        self.assertIsNotNone(parse_post_age("12 de set", now=self.ref_now))
        self.assertIsNotNone(parse_post_age("14 de oct", now=self.ref_now))
        self.assertIsNotNone(parse_post_age("14 de out", now=self.ref_now))
        self.assertIsNotNone(parse_post_age("5 de nov", now=self.ref_now))
        self.assertIsNotNone(parse_post_age("24 de dic", now=self.ref_now))
        self.assertIsNotNone(parse_post_age("24 de dez", now=self.ref_now))

    def test_absolute_dates_with_future_wrap_around(self):
        """Date without year later in the calendar wraps around to previous year."""
        # Today is Sept 19, 2026. "25 de diciembre" (no year) -> Dec 25, 2025.
        got = parse_post_age("25 de diciembre", now=self.ref_now)
        expected = (self.ref_now - datetime(2025, 12, 25, tzinfo=timezone.utc)).total_seconds()
        self.assertEqual(got, expected)

    def test_date_parsing_with_noise_punctuation_and_whitespace(self):
        """Punctuation, dots, middle dots (Facebook style), and prefixes do not fail."""
        cases = [
            ("2 h · Editado", 7200.0),
            ("hace 2 h · ", 7200.0),
            ("Publicado hace 3 d.", 259200.0),
            ("Publicada hace 15 min", 900.0),
            ("12 de septiembre a las 14:30", (self.ref_now - datetime(2026, 9, 12, 14, 30, tzinfo=timezone.utc)).total_seconds()),
            ("12 de setembro as 14:30", (self.ref_now - datetime(2026, 9, 12, 14, 30, tzinfo=timezone.utc)).total_seconds()),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_post_age(text, now=self.ref_now), expected)

    def test_adversarial_malformed_and_unexpected_inputs_never_raise(self):
        """Malformed, non-string, garbage, and invalid dates return None safely without raising."""
        adversarial_inputs = [
            None,
            "",
            "   ",
            "\t\n",
            12345,
            12.34,
            True,
            False,
            [],
            {},
            "precio negociable",
            "vendo moto 125",
            "1234",
            "undefined",
            "null",
            "N/A",
            "31 de febrero",       # invalid calendar day
            "31 de abril",         # April has 30 days
            "99 de enero de 2024",  # invalid day
            "a" * 10000,           # buffer stress
            "\x00\x01\x02\n\t",    # control characters
        ]
        for raw in adversarial_inputs:
            with self.subTest(raw=raw):
                try:
                    res = parse_post_age(raw, now=self.ref_now)
                    self.assertIsNone(res, f"Expected None for input '{raw}', got {res}")
                except Exception as exc:
                    self.fail(f"parse_post_age raised an unhandled exception on '{raw}': {exc}")


class TestAdversarialEnrichmentAndPipeline(unittest.TestCase):
    """Adversarial testing of detail enrichment, error isolation, and pipeline invariants."""

    def setUp(self):
        self.cfg = Config(
            budget=1000.0,
            fx={"offline": True, "base": "BRL"},
            filters=FilterConfig(cities=["jaguarao", "rio branco"]),
        )
        self.fx = FX(offline=True)

    def test_extract_description_multilingual_headers(self):
        """extract_description correctly handles Spanish, Portuguese, and English sections."""
        bodies = [
            ("Detalles\nDescripción\nVendo moto por 900 reales en buen estado\nInformación del vendedor", "Vendo moto por 900 reales en buen estado"),
            ("Detalhes\nDescrição\nVendo moto por 900 reais em bom estado\nInformações do vendedor", "Vendo moto por 900 reais em bom estado"),
            ("Details\nDescription\nSelling motorcycle 900 brl good condition\nSeller information", "Selling motorcycle 900 brl good condition"),
        ]
        for body, expected in bodies:
            with self.subTest(header=body.splitlines()[0]):
                desc = extract_description(body)
                self.assertIn(expected, desc)

    def test_extract_description_stop_markers(self):
        """extract_description stops cleanly at seller info, approximate location, or related searches."""
        body = (
            "Detalles del vehículo\n"
            "Descripción\n"
            "Moto Honda CG 125 impecable valor 800\n"
            "La ubicación es aproximada\n"
            "Información del vendedor\n"
            "Teléfono 099123456"
        )
        desc = extract_description(body)
        self.assertIn("Moto Honda CG 125 impecable valor 800", desc)
        self.assertNotIn("La ubicación es aproximada", desc)
        self.assertNotIn("Información del vendedor", desc)

    def test_detail_price_text_rescues_missing_or_bait_card_price(self):
        """detail_price_text accurately extracts the real price line from the detail page body."""
        body = (
            "Marketplace\n"
            "R$ 15.500\n"
            "2025 Honda cg160\n"
            "Jaguarão, RS\n"
            "Publicado hace 3 horas"
        )
        self.assertEqual(detail_price_text(body), "R$ 15.500")

        # Uruguayan peso notation in detail
        body_uyu = (
            "Marketplace\n"
            "$U 28.000\n"
            "Yumbo GS 125\n"
            "Río Branco, Cerro Largo\n"
        )
        self.assertEqual(detail_price_text(body_uyu), "$U 28.000")

    def test_is_facebook_item_security_checks(self):
        """_is_facebook_item strictly accepts valid Facebook marketplace item URLs only."""
        val = "https://www.facebook.com/marketplace/item/123456789/"
        self.assertTrue(_is_facebook_item(val))

        invalids = [
            "http://www.facebook.com/marketplace/item/123/",          # non-https
            "https://evil.example/marketplace/item/123/",             # wrong domain
            "https://www.facebook.com.evil.com/marketplace/item/123/", # subdomain spoof
            "https://www.facebook.com/groups/999/posts/123/",          # group post, not item
            "https://www.facebook.com/marketplace/",                   # not an item
            "",
            None,
        ]
        for url in invalids:
            with self.subTest(url=url):
                self.assertFalse(_is_facebook_item(url))

    def test_enrich_facebook_per_item_timeout_and_error_isolation(self):
        """When an individual item encounters an exception in Playwright, subsequent items still proceed."""
        profile = Mock()
        profile.exists.return_value = True
        pw, ctx = Mock(), Mock()
        page = Mock()
        ctx.pages = [page]

        l1 = Listing("facebook", "item_fail", "Moto 1", "https://www.facebook.com/marketplace/item/1/", price=5.0)
        l2 = Listing("facebook", "item_ok", "Moto 2", "https://www.facebook.com/marketplace/item/2/", price=5.0)

        call_count = 0
        def mock_goto(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "item/1/" in url:
                raise TimeoutError("Navigation timeout")
            return None

        page.goto.side_effect = mock_goto
        page.evaluate.return_value = "Detalles\nDescrição\nVendo moto por 900 reais\nInformações do vendedor"

        with patch("motoradar.sources.facebook.PROFILE_DIR", profile), \
             patch("motoradar.sources.facebook._launch", return_value=(pw, ctx)), \
             patch("motoradar.sources.facebook._settle"), \
             patch("motoradar.enrich.time.sleep"), \
             redirect_stdout(io.StringIO()):
            done = enrich_facebook([l1, l2], pause=0)

        self.assertEqual(done, 1)
        self.assertIn("fallo: TimeoutError", l1.raw.get("detail_status", ""))
        self.assertEqual(l2.raw.get("detail_status"), "leido")
        self.assertEqual(l2.raw.get("description"), "Vendo moto por 900 reais")
        ctx.close.assert_called_once()
        pw.stop.assert_called_once()

    def test_enrich_facebook_detail_limit_capping(self):
        """Listings beyond detail_limit are marked 'omitido por limite operativo'."""
        profile = Mock()
        profile.exists.return_value = True
        pw, ctx = Mock(), Mock()
        page = Mock()
        ctx.pages = [page]
        page.evaluate.return_value = "Detalles\nDescrição\nVendo moto\nInformación"

        listings = [
            Listing("facebook", f"item_{i}", f"Moto {i}", f"https://www.facebook.com/marketplace/item/{i}/", price=5.0)
            for i in range(5)
        ]

        with patch("motoradar.sources.facebook.PROFILE_DIR", profile), \
             patch("motoradar.sources.facebook._launch", return_value=(pw, ctx)), \
             patch("motoradar.sources.facebook._settle"), \
             patch("motoradar.enrich.time.sleep"), \
             redirect_stdout(io.StringIO()):
            done = enrich_facebook(listings, max_items=2, pause=0)

        self.assertEqual(done, 2)
        self.assertEqual(listings[0].raw.get("detail_status"), "leido")
        self.assertEqual(listings[1].raw.get("detail_status"), "leido")
        self.assertEqual(listings[2].raw.get("detail_status"), "omitido por limite operativo")
        self.assertEqual(listings[3].raw.get("detail_status"), "omitido por limite operativo")
        self.assertEqual(listings[4].raw.get("detail_status"), "omitido por limite operativo")

    def test_pipeline_prepare_enrich_exception_handled_gracefully(self):
        """If enrich fails in pipeline.prepare, the pipeline catches it and continues."""
        l = Listing("facebook", "err_1", "Honda CG 125", "https://facebook.com/marketplace/item/err_1/",
                    price=800.0, location="Jaguarão, RS")

        with patch("motoradar.pipeline.enrich", side_effect=RuntimeError("Browser launch crash")), \
             redirect_stdout(io.StringIO()):
            res = prepare([l], self.cfg, self.fx, enrich_details=True)

        self.assertEqual(len(res.opportunities), 1)
        self.assertEqual(res.opportunities[0].external_id, "err_1")

    def test_pipeline_prepare_partition_invariant(self):
        """prepare partitions all observed listings into mutually exclusive categories."""
        listings = [
            # 1. Valid opportunity within budget
            Listing("facebook", "c1", "Honda CG 125", "https://fb.com/1", price=800.0, location="Jaguarão, RS"),
            # 2. Missing price (unconfirmed)
            Listing("facebook", "c2", "Honda CG 125", "https://fb.com/2", price=None, location="Jaguarão, RS"),
            # 3. Decoy price (unconfirmed)
            Listing("facebook", "c3", "Honda CG 125", "https://fb.com/3", price=1234.0, location="Jaguarão, RS"),
            # 4. Over budget (discarded)
            Listing("facebook", "c4", "Honda CG 125", "https://fb.com/4", price=1500.0, location="Jaguarão, RS"),
            # 5. Non-target location (discarded)
            Listing("facebook", "c5", "Honda CG 125", "https://fb.com/5", price=800.0, location="Pelotas, RS"),
            # 6. Non-buyable accessory (discarded)
            Listing("facebook", "c6", "Capacete fechado novo", "https://fb.com/6", price=200.0, location="Jaguarão, RS"),
            # 7. Wanted search request (discarded)
            Listing("facebook", "c7", "Procuro moto urgente", "https://fb.com/7", price=800.0, location="Jaguarão, RS"),
        ]

        res = prepare(listings, self.cfg, self.fx, enrich_details=False)

        total_categorized = len(res.opportunities) + len(res.unconfirmed) + sum(res.discarded.values())
        self.assertEqual(total_categorized, len(res.observed))
        self.assertEqual(len(res.opportunities), 1)
        self.assertEqual(res.opportunities[0].external_id, "c1")
        self.assertEqual(len(res.unconfirmed), 2)
        unconf_ids = {u.external_id for u in res.unconfirmed}
        self.assertEqual(unconf_ids, {"c2", "c3"})
        self.assertEqual(sum(res.discarded.values()), 4)

    def test_pipeline_prepare_decoy_and_missing_prices_unconfirmed(self):
        """All decoy sequences and missing prices land in SearchResult.unconfirmed with 'unconfirmed' status."""
        decoys = [0.0, 1.0, 50.0, 1111.0, 1234.0, 12345.0, None]
        for d in decoys:
            with self.subTest(decoy=d):
                l = Listing("facebook", f"dec_{d}", "Honda CG 125", "https://fb.com/dec",
                            price=d, location="Jaguarão, RS")
                res = prepare([l], self.cfg, self.fx, enrich_details=False)
                self.assertEqual(len(res.opportunities), 0)
                self.assertEqual(len(res.unconfirmed), 1)
                self.assertEqual(res.unconfirmed[0].raw.get("alert_status"), "unconfirmed")

    def test_pipeline_prepare_cross_border_currency_normalization(self):
        """UYU prices are normalized against base currency without comparing nominal amounts."""
        # 6000 UYU converted to BRL @ offline rates: 6000 / 40.2 * 5.15 = 768.66 BRL <= 1000.0 -> confirmed
        l_uyu_ok = Listing("facebook", "uyu_ok", "Yumbo GS 125", "https://fb.com/u1",
                           price=6000.0, currency="UYU", location="Río Branco, Cerro Largo")
        res_ok = prepare([l_uyu_ok], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res_ok.opportunities), 1)
        opp = res_ok.opportunities[0]
        self.assertEqual(opp.currency, "BRL")
        self.assertAlmostEqual(opp.price, 768.66, places=2)
        self.assertEqual(opp.raw.get("original_currency"), "UYU")
        self.assertEqual(opp.raw.get("original_price"), 6000.0)

        # 10000 UYU converted: 10000 / 40.2 * 5.15 = 1281.09 BRL > 1000.0 -> discarded
        l_uyu_over = Listing("facebook", "uyu_over", "Yumbo GS 125", "https://fb.com/u2",
                             price=10000.0, currency="UYU", location="Río Branco, Cerro Largo")
        res_over = prepare([l_uyu_over], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res_over.opportunities), 0)
        self.assertEqual(res_over.discarded.get("fuera de presupuesto"), 1)

    def test_pipeline_prepare_domain_exclusion_categories(self):
        """Domain classification correctly labels condition and excludes non-buyable entries."""
        # Runner
        l_run = Listing("facebook", "d_run", "Honda CG 125 impecable andando bien", "https://fb.com/r",
                        price=800.0, location="Jaguarão, RS")
        res_run = prepare([l_run], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res_run.opportunities), 1)
        self.assertEqual(res_run.opportunities[0].raw.get("condition"), "runner")

        # Project / broken engine
        l_proj = Listing("facebook", "d_proj", "Honda CG 125 motor fundido para reformar", "https://fb.com/p",
                         price=600.0, location="Jaguarão, RS")
        res_proj = prepare([l_proj], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res_proj.opportunities), 1)
        self.assertEqual(res_proj.opportunities[0].raw.get("condition"), "project")

        # Donor / parts bike
        l_parts = Listing("facebook", "d_part", "Moto para retirada de pecas sucata", "https://fb.com/s",
                          price=450.0, location="Jaguarão, RS")
        res_parts = prepare([l_parts], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res_parts.opportunities), 1)
        self.assertEqual(res_parts.opportunities[0].raw.get("condition"), "parts")

        # Spanish accessories / non-motorcycles
        for title in ["Casco integral nuevo", "Campera de moto talle L", "Cubierta trasera 90/90-18",
                      "Llanta delantera Titan", "Repuestos de moto varios", "Cadena reforzada"]:
            with self.subTest(title=title):
                l_acc = Listing("facebook", "acc", title, "https://fb.com/acc",
                                price=150.0, location="Jaguarão, RS")
                res_acc = prepare([l_acc], self.cfg, self.fx, enrich_details=False)
                self.assertEqual(len(res_acc.opportunities), 0)
                self.assertEqual(res_acc.discarded.get("pedido/publicidad/repuesto/desconocido"), 1)


class TestExposedDefectsAndEdgeCases(unittest.TestCase):
    """Empirical demonstrations of genuine defects and edge-case failure modes discovered and resolved."""

    def test_reproduce_defect_1_unpunctuated_downpayment_strips_total_price(self):
        """DEFECT 1 (P1): When a post states total price and down payment without punctuation,

        _NON_TOTAL_PRICE must not consume the total price.

        Input: 'Vendo Honda Falcon valor 12000 reais entrada de 800 reais'
        Expected: Price is 12000.0 BRL, downpayment 800 is stripped, discarded as over budget.
        """
        post = {
            "id": "p_down_bug",
            "text": "Vendo Honda Falcon valor 12000 reais entrada de 800 reais",
        }
        listing = post_to_listing("999", post, "jaguarao", False)

        amount, currency = parse_price_text(post["text"])
        self.assertEqual(amount, 12000.0)
        self.assertEqual(currency, "BRL")

        # In pipeline.prepare, the 12000 BRL bike is discarded as outside budget (budget=1000.0)
        cfg = Config(budget=1000.0, fx={"offline": True})
        fx = FX(offline=True)
        res = prepare([listing], cfg, fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("fuera de presupuesto"), 1)

    def test_reproduce_defect_2_multiline_marketplace_card_notes_truncate_title(self):
        """DEFECT 2 (P2): When a Marketplace card body has 3 lines (title + seller note + location),

        _parse_card preserves the first line (title) and extracts location from the last line.

        Input: 'R$ 800\nHonda CG 125\nDetalles al privado\nJaguarão, RS'
        Expected: Title contains 'Honda CG 125', location is 'Jaguarão, RS'.
        """
        card_text = "R$ 800\nHonda CG 125\nDetalles al privado\nJaguarão, RS"
        title, location = _parse_card(card_text)

        self.assertIn("Honda CG 125", title)
        self.assertEqual(location, "Jaguarão, RS")

        # In pipeline.prepare, this viable R$ 800 motorcycle in Jaguarão is confirmed!
        listing = card_to_listing({
            "href": "https://www.facebook.com/marketplace/item/note_bug/",
            "text": card_text,
        })
        cfg = Config(budget=1000.0, fx={"offline": True})
        fx = FX(offline=True)
        res = prepare([listing], cfg, fx, enrich_details=False)

        self.assertEqual(len(res.opportunities), 1)
        self.assertEqual(res.opportunities[0].price, 800.0)
        self.assertIn("Honda", res.opportunities[0].title)

    def test_downpayment_only_resolves_to_none_and_unconfirmed(self):
        """Ads with only down payment mentions resolve to price=None and unconfirmed status."""
        down_only_cases = [
            "Honda Falcon entrada de 800 reais",
            "Honda Falcon entrada: 800 reais",
            "Honda Falcon 800 reais de entrada",
            "Honda Falcon 800 reais entrada",
            "Honda Falcon seña de 500 pesos",
            "Honda Falcon entrega de $ 800",
        ]
        cfg = Config(budget=1000.0, fx={"offline": True})
        fx = FX(offline=True)
        for text in down_only_cases:
            with self.subTest(text=text):
                price, curr = parse_price_text(text)
                self.assertIsNone(price, f"Expected None price for '{text}', got {price}")
                listing = Listing("facebook", "down_only", text, "https://fb.com/down",
                                  price=price, location="Jaguarão, RS")
                res = prepare([listing], cfg, fx, enrich_details=False)
                self.assertEqual(len(res.opportunities), 0)
                self.assertEqual(len(res.unconfirmed), 1)

    def test_unpunctuated_total_price_with_various_downpayment_formats(self):
        """Unpunctuated total prices followed by down payments preserve the total price."""
        combos = [
            ("Vendo Honda Falcon valor 12000 reais entrada 800 reais", (12000.0, "BRL")),
            ("Vendo Honda Falcon valor 12000 reais entrada: 800", (12000.0, "BRL")),
            ("Vendo Honda Falcon 12000 reais, 800 reais de entrada", (12000.0, "BRL")),
            ("Vendo Honda Falcon valor 12000 reais, 800 reais de entrada", (12000.0, "BRL")),
            ("Vendo Honda CG 125 valor 900 reais entrada de 200 reais", (900.0, "BRL")),
        ]
        for text, expected in combos:
            with self.subTest(text=text):
                self.assertEqual(parse_price_text(text), expected)

        # The 900 BRL bike with 200 BRL downpayment qualifies as confirmed opportunity
        l_in_budget = Listing("facebook", "in_budget_down",
                              "Vendo Honda CG 125 valor 900 reais entrada de 200 reais",
                              "https://fb.com/in_budget", price=900.0, location="Jaguarão, RS")
        cfg = Config(budget=1000.0, fx={"offline": True})
        fx = FX(offline=True)
        res = prepare([l_in_budget], cfg, fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)
        self.assertEqual(res.opportunities[0].price, 900.0)


if __name__ == "__main__":
    unittest.main()
