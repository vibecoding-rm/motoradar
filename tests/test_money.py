"""Unit tests for Milestone M2: Currency Normalization, FX & Decoy Pricing.

Verifies:
- BUG-R2-01: Decoy numerical sequence detection in appraise.py and pipeline.py.
- BUG-R2-02: Single '$' Uruguayan currency notation in models.py and money.py.
- BUG-R2-03: Currency-normalized bait comparison in enrich.py (fix_bait_prices).
- FEAT-R2-THRESH: Strict and inclusive resale threshold (<= 1000 BRL equivalent).
"""
import unittest

from motoradar.appraise import appraise, is_bait_price, is_decoy_sequence
from motoradar.config import Config
from motoradar.enrich import fix_bait_prices
from motoradar.models import Listing, parse_price_text
from motoradar.money import FX, detect_currency
from motoradar.pipeline import prepare


class TestBaitAndDecoyPrices(unittest.TestCase):
    def test_bait_price_threshold_boundaries(self):
        """Boundary values for <= 100.0 BAIT_PRICE."""
        self.assertFalse(is_bait_price(None))
        self.assertTrue(is_bait_price(0))
        self.assertTrue(is_bait_price(0.0))
        self.assertTrue(is_bait_price(1.0))
        self.assertTrue(is_bait_price(10))
        self.assertTrue(is_bait_price(50.0))
        self.assertTrue(is_bait_price(100.0))

        # Values above 100 that are not decoy sequences
        self.assertFalse(is_bait_price(100.01))
        self.assertFalse(is_bait_price(101.0))
        self.assertFalse(is_bait_price(500.0))
        self.assertFalse(is_bait_price(800.0))
        self.assertFalse(is_bait_price(1000.0))
        self.assertFalse(is_bait_price(6500.0))

    def test_decoy_numerical_sequences(self):
        """Decoy patterns such as 1234, 12345, 1111, 2222, etc."""
        decoys = [
            1234, 1234.0, 12345, 12345.0, 123456,
            1111, 1111.0, 2222, 3333, 4444, 5555, 9999,
            11111, 22222,
        ]
        for val in decoys:
            with self.subTest(val=val):
                self.assertTrue(is_bait_price(val), f"{val} should be detected as bait/decoy")

    def test_decoy_from_formatted_string(self):
        """String inputs representing decoy prices."""
        self.assertTrue(is_bait_price("R$ 1.234"))
        self.assertTrue(is_bait_price("1234"))
        self.assertTrue(is_bait_price("$ 1.234"))
        self.assertTrue(is_bait_price("1111"))
        self.assertFalse(is_bait_price("R$ 1.000"))
        self.assertFalse(is_bait_price("800"))

    def test_appraise_detects_bait_and_decoy_prices(self):
        """Listing appraisal flags bait_price for both low prices and decoy sequences."""
        l_seq = Listing("facebook", "1", "Honda CG 125", "", price=1234.0)
        a_seq = appraise(l_seq)
        self.assertTrue(a_seq.bait_price)

        l_low = Listing("facebook", "2", "Honda CG 125", "", price=50.0)
        a_low = appraise(l_low)
        self.assertTrue(a_low.bait_price)

        l_real = Listing("facebook", "3", "Honda CG 125", "", price=800.0)
        a_real = appraise(l_real)
        self.assertFalse(a_real.bait_price)

    def test_is_decoy_sequence_patterns(self):
        """is_decoy_sequence catches sequences without enforcing BAIT_PRICE threshold."""
        self.assertTrue(is_decoy_sequence(1234))
        self.assertTrue(is_decoy_sequence(1234.0))
        self.assertTrue(is_decoy_sequence(1111))
        self.assertTrue(is_decoy_sequence("1234"))
        self.assertTrue(is_decoy_sequence("$ 1.234"))
        # Non-sequences must return False even if <= 100
        self.assertFalse(is_decoy_sequence(50.0))
        self.assertFalse(is_decoy_sequence(10.0))
        self.assertFalse(is_decoy_sequence(100.0))
        self.assertFalse(is_decoy_sequence(800.0))
        self.assertFalse(is_decoy_sequence(None))

    def test_decoy_sequence_boundary_does_not_match_legitimate_prices(self):
        """DECOY_PRICE_RE must not greedily match legitimate prices starting with 1234 (e.g. 12340, 12349, 123400)."""
        non_decoys = [12340, 12340.0, "12340", 12349, 12349.0, 123400, 123400.0, 123450]
        for val in non_decoys:
            with self.subTest(val=val):
                self.assertFalse(is_decoy_sequence(val), f"{val} should NOT be a decoy sequence")


class TestUruguayanCurrencyParsing(unittest.TestCase):
    def test_single_dollar_notation_in_parse_price_text(self):
        """Single '$' with or without label resolves to UYU."""
        cases = [
            ("Vendo moto Winner 110 valor $ 6.000", (6000.0, "UYU")),
            ("Vendo moto Winner 110 $ 6.000", (6000.0, "UYU")),
            ("Vendo moto Winner 110 $6000", (6000.0, "UYU")),
            ("Vendo moto Winner 110 valor $6000", (6000.0, "UYU")),
            ("Vendo moto Winner 110 $ 800", (800.0, "UYU")),
            ("Vendo moto Winner 110 por $ 800", (800.0, "UYU")),
            ("Vendo moto Winner 110 $U 6.000", (6000.0, "UYU")),
            ("Vendo moto Winner 110 6000 pesos", (6000.0, "UYU")),
            ("Vendo moto Winner 110 valor 6000 pesos", (6000.0, "UYU")),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_price_text(text), expected)

    def test_currency_symbol_precedence(self):
        """USD (US$, U$S) and BRL (R$) take precedence over single '$'."""
        cases = [
            ("Vendo moto US$ 200", (200.0, "USD")),
            ("Vendo moto U$S 200", (200.0, "USD")),
            ("Vendo moto R$ 800", (800.0, "BRL")),
            ("Vendo moto 800 reais", (800.0, "BRL")),
            ("Vendo moto valor R$ 900", (900.0, "BRL")),
            ("Vendo moto valor US$ 150", (150.0, "USD")),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_price_text(text), expected)

    def test_single_dollar_down_payments_stripped(self):
        """Down payments with '$' are stripped so total price is preserved."""
        cases = [
            ("Vendo moto valor $ 6.000, entrada de $ 1.000", (6000.0, "UYU")),
            ("Vendo moto valor $ 6.000, $ 1.000 de entrada", (6000.0, "UYU")),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_price_text(text), expected)

    def test_detect_currency(self):
        """detect_currency identifies single '$' as UYU and preserves USD/BRL."""
        self.assertEqual(detect_currency("$ 6.000"), "UYU")
        self.assertEqual(detect_currency("$6000"), "UYU")
        self.assertEqual(detect_currency("valor $ 6.000"), "UYU")
        self.assertEqual(detect_currency("$U 6000"), "UYU")
        self.assertEqual(detect_currency("6000 pesos"), "UYU")
        self.assertEqual(detect_currency("US$ 200"), "USD")
        self.assertEqual(detect_currency("U$S 200"), "USD")
        self.assertEqual(detect_currency("R$ 800"), "BRL")
        self.assertEqual(detect_currency("800 reais"), "BRL")

    def test_uyu_code_notation_in_parse_price_text(self):
        """UYU code notation variants in parse_price_text."""
        cases = [
            ("UYU 6000", (6000.0, "UYU")),
            ("6000 UYU", (6000.0, "UYU")),
            ("valor UYU 6000", (6000.0, "UYU")),
            ("precio UYU 5000", (5000.0, "UYU")),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_price_text(text), expected)

    def test_spanish_down_payments_stripped_or_not_confused_with_total(self):
        """Spanish down payment mentions (entrega, cuotas, seña) should not become total price."""
        # Total price with down payment
        text_combo = "Vendo moto Winner valor $ 25.000, entrega de $ 800"
        price, curr = parse_price_text(text_combo)
        self.assertEqual(price, 25000.0)
        self.assertEqual(curr, "UYU")

        # Reverse order: down payment first, then total price
        text_rev = "Vendo moto Winner entrega de $ 800, valor $ 25.000"
        price, curr = parse_price_text(text_rev)
        self.assertEqual(price, 25000.0)
        self.assertEqual(curr, "UYU")

        # Solo down payment must not be parsed as total price
        text_solo = "Vendo moto Winner entrega $ 800 y cuotas"
        price, curr = parse_price_text(text_solo)
        self.assertIsNone(price)

        # Multi-installment
        text_cuotas = "Vendo moto Winner 12 cuotas de $ 800"
        price, curr = parse_price_text(text_cuotas)
        self.assertIsNone(price)

    def test_displacement_not_stripped_as_down_payment(self):
        """Motorcycle displacement preceding down payment must not be stripped by _NON_TOTAL_PRICE."""
        cases = [
            ("Vendo moto CG 160 entrada de 900 reais, valor 8000 reais", (8000.0, "BRL")),
            ("Vendo Biz 110 entrada de 500 reais, valor 4000 reais", (4000.0, "BRL")),
            ("Vendo Titan 150 entrada 800 reais, valor 7000 reais", (7000.0, "BRL")),
            ("Vendo Biz 125 entrega de $ 800, valor $ 25.000", (25000.0, "UYU")),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_price_text(text), expected)

    def test_down_payment_punctuation_delimiters_stripped(self):
        """Down payments with punctuation delimiters (: or -) must be stripped by _NON_TOTAL_PRICE."""
        standalone = [
            "Vendo Winner entrega: $ 800 y cuotas",
            "Vendo Winner seña: $ 500 y saldo",
            "Vendo CG entrada: R$ 800 e parcelas",
            "Vendo Winner cuotas: $ 800",
            "Vendo Winner entrega - $ 800 y cuotas",
            "Vendo Winner 12 cuotas: $ 800",
            "Vendo CG 12 parcelas - de 150 reais",
        ]
        for text in standalone:
            with self.subTest(text=text):
                price, curr = parse_price_text(text)
                self.assertIsNone(price, f"Standalone down payment {text!r} was falsely parsed as total price: {price}")

        combos = [
            ("Vendo CG valor 8000 reais, entrada: R$ 800", (8000.0, "BRL")),
            ("Vendo Winner valor $ 25.000, entrega: $ 800", (25000.0, "UYU")),
            ("Vendo Winner entrega: $ 800, valor $ 25.000", (25000.0, "UYU")),
        ]
        for text, expected in combos:
            with self.subTest(text=text):
                self.assertEqual(parse_price_text(text), expected)


class TestBaitPriceEnrichmentWithFX(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(budget=1000.0, fx={"offline": True})
        self.fx = FX(offline=True)

    def test_usd_legitimate_low_price_not_discarded_as_bait(self):
        """50 USD ~ 257.50 BRL is above BAIT_MAX (100 BRL) and must be accepted."""
        listing = Listing(
            "facebook", "usd_50", "Honda CG 125", "",
            price=5.0, currency="BRL",
            raw={"description": "vendo valor US$ 50"}
        )
        corregidos = fix_bait_prices([listing], self.fx, "BRL")
        self.assertEqual(corregidos, 1)
        self.assertEqual(listing.price, 50.0)
        self.assertEqual(listing.currency, "USD")
        self.assertEqual(listing.raw["card_price"], 5.0)

    def test_usd_actual_bait_price_is_discarded(self):
        """10 USD ~ 51.50 BRL is <= BAIT_MAX (100 BRL) and must remain bait."""
        listing = Listing(
            "facebook", "usd_10", "Honda CG 125", "",
            price=5.0, currency="BRL",
            raw={"description": "vendo valor US$ 10"}
        )
        corregidos = fix_bait_prices([listing], self.fx, "BRL")
        self.assertEqual(corregidos, 0)
        self.assertEqual(listing.price, 5.0)

    def test_decoy_sequence_card_price_fixed_by_real_description_price(self):
        """Card price 1234 (decoy) is fixed by real price 800 from description."""
        listing = Listing(
            "facebook", "decoy_fixed", "Honda CG 125", "",
            price=1234.0, currency="BRL",
            raw={"description": "vendo por 800 reais"}
        )
        corregidos = fix_bait_prices([listing], self.fx, "BRL")
        self.assertEqual(corregidos, 1)
        self.assertEqual(listing.price, 800.0)
        self.assertEqual(listing.raw["card_price"], 1234.0)

    def test_decoy_sequence_card_price_in_uyu_fixed_by_real_description_price(self):
        """Card price 1234 UYU (decoy) normalized to 158.09 BRL is fixed by real price in description."""
        listing = Listing(
            "facebook", "uyu_decoy_fixed", "Moto Winner 110", "",
            price=1234.0, currency="UYU", location="Rio Branco",
            raw={"description": "vendo por $ 5.000"}
        )
        res = prepare([listing], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)
        self.assertEqual(len(res.unconfirmed), 0)
        self.assertEqual(res.opportunities[0].price, 640.55)
        self.assertEqual(res.opportunities[0].currency, "BRL")
        self.assertEqual(res.opportunities[0].raw.get("original_price"), 5000.0)
        self.assertEqual(res.opportunities[0].raw.get("original_currency"), "UYU")
        self.assertEqual(res.opportunities[0].raw.get("card_price"), 1234.0)
        self.assertEqual(res.opportunities[0].raw.get("card_currency"), "UYU")

    def test_decoy_sequence_in_description_is_rejected_as_bait(self):
        """Detail text containing decoy sequence like 1234 pesos is rejected and does not overwrite price."""
        listing = Listing(
            "facebook", "bait_desc_decoy", "Honda CG 125", "",
            price=5.0, currency="BRL", location="Jaguarao",
            raw={"description": "vendo por 1234 pesos"}
        )
        corregidos = fix_bait_prices([listing], self.fx, "BRL")
        self.assertEqual(corregidos, 0)
        self.assertEqual(listing.price, 5.0)


class TestPipelineThresholdAndDecoy(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(budget=1000.0, fx={"offline": True})
        self.fx = FX(offline=True)

    def test_decoy_sequence_1234_classified_as_unconfirmed(self):
        """Listing with decoy price 1234 BRL is placed into unconfirmed (BUG-R2-01)."""
        l = Listing("facebook", "decoy_1234", "Honda CG 125", "",
                    price=1234.0, currency="BRL", location="Jaguarao")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(len(res.unconfirmed), 1)
        self.assertEqual(res.unconfirmed[0].external_id, "decoy_1234")
        self.assertEqual(res.unconfirmed[0].raw.get("alert_status"), "unconfirmed")
        self.assertNotIn("fuera de presupuesto", res.discarded)

    def test_decoy_sequence_1111_classified_as_unconfirmed(self):
        """Listing with repeated digit decoy price 1111 is placed into unconfirmed."""
        l = Listing("facebook", "decoy_1111", "Honda CG 125", "",
                    price=1111.0, currency="BRL", location="Jaguarao")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(len(res.unconfirmed), 1)
        self.assertEqual(res.unconfirmed[0].external_id, "decoy_1111")
        self.assertEqual(res.unconfirmed[0].raw.get("alert_status"), "unconfirmed")

    def test_decoy_sequence_in_uyu_classified_as_unconfirmed(self):
        """Listing with decoy price in UYU (e.g. 1234 UYU) is unconfirmed."""
        l = Listing("facebook", "decoy_uyu", "Moto Winner 110", "",
                    price=1234.0, currency="UYU", location="Rio Branco")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(len(res.unconfirmed), 1)
        self.assertEqual(res.unconfirmed[0].external_id, "decoy_uyu")
        self.assertEqual(res.unconfirmed[0].raw.get("alert_status"), "unconfirmed")

    def test_resale_threshold_inclusive_boundary(self):
        """Exact 1000.0 BRL is confirmed opportunity; 1000.01 is discarded (FEAT-R2-THRESH)."""
        l_exact = Listing("facebook", "t_1000", "Honda CG 125", "",
                          price=1000.0, currency="BRL", location="Jaguarao")
        l_over = Listing("facebook", "t_1001", "Honda CG 125", "",
                         price=1000.01, currency="BRL", location="Jaguarao")
        res = prepare([l_exact, l_over], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)
        self.assertEqual(res.opportunities[0].external_id, "t_1000")
        self.assertEqual(res.opportunities[0].raw.get("alert_status"), "confirmed")
        self.assertEqual(res.discarded.get("fuera de presupuesto"), 1)

    def test_uyu_single_dollar_within_and_over_budget(self):
        """Single '$' UYU price normalized via FX and threshold-tested."""
        # 6000 UYU / 40.2 * 5.15 = 768.66 BRL <= 1000.0 -> opportunity
        l_within = Listing("facebook", "uyu_in", "Moto Winner 110", "",
                           price=6000.0, currency="UYU", location="Rio Branco")
        # 10000 UYU / 40.2 * 5.15 = 1281.09 BRL > 1000.0 -> discarded
        l_over = Listing("facebook", "uyu_out", "Moto Winner 110", "",
                         price=10000.0, currency="UYU", location="Rio Branco")
        res = prepare([l_within, l_over], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)
        self.assertEqual(res.opportunities[0].external_id, "uyu_in")
        self.assertEqual(res.opportunities[0].price, 768.66)
        self.assertEqual(res.discarded.get("fuera de presupuesto"), 1)

    def test_usd_legitimate_low_price_pipeline_opportunity(self):
        """50 USD (~257.50 BRL) processed via pipeline.prepare qualifies as confirmed opportunity."""
        l = Listing("facebook", "usd_50", "Honda CG 125", "",
                    price=50.0, currency="USD", location="Jaguarao")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)
        self.assertEqual(len(res.unconfirmed), 0)
        self.assertEqual(res.opportunities[0].raw.get("alert_status"), "confirmed")
        self.assertEqual(res.opportunities[0].price, 257.50)

    def test_usd_actual_bait_price_pipeline_unconfirmed(self):
        """10 USD (~51.50 BRL <= 100 BRL) is classified as unconfirmed bait in pipeline."""
        l = Listing("facebook", "usd_10", "Honda CG 125", "",
                    price=10.0, currency="USD", location="Jaguarao")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(len(res.unconfirmed), 1)
        self.assertEqual(res.unconfirmed[0].raw.get("alert_status"), "unconfirmed")

    def test_foreign_decoy_sequence_pipeline_unconfirmed(self):
        """1234 UYU (~158.09 BRL > 100 BRL) is recognized as decoy sequence and classified as unconfirmed."""
        l = Listing("facebook", "uyu_1234", "Moto Winner 110", "",
                    price=1234.0, currency="UYU", location="Rio Branco")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(len(res.unconfirmed), 1)
        self.assertEqual(res.unconfirmed[0].raw.get("alert_status"), "unconfirmed")

    def test_over_budget_price_starting_with_1234_discarded_not_unconfirmed(self):
        """12340 BRL is over budget and NOT a decoy sequence, so it must be discarded as 'fuera de presupuesto'."""
        l = Listing("facebook", "legit_12340", "Honda CB 500", "",
                    price=12340.0, currency="BRL", location="Jaguarao")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(len(res.unconfirmed), 0)
        self.assertEqual(res.discarded.get("fuera de presupuesto"), 1)


class TestListingPriceFormatting(unittest.TestCase):
    def test_pretty_price_with_string_original_price(self):
        """pretty_price handles string original_price without raising TypeError."""
        l = Listing("facebook", "1", "Moto", "", price=100.0, currency="BRL",
                    raw={"original_price": "1000", "original_currency": "UYU"})
        self.assertEqual(l.pretty_price(), "R$ 100 ($U 1.000)")

    def test_pretty_price_with_formatted_string_original_price(self):
        """pretty_price preserves thousands separator when original_price is formatted string."""
        l = Listing("facebook", "2", "Moto", "", price=100.0, currency="BRL",
                    raw={"original_price": "1.000", "original_currency": "UYU"})
        self.assertEqual(l.pretty_price(), "R$ 100 ($U 1.000)")

    def test_pretty_price_with_none_or_missing_price(self):
        """pretty_price with None price returns 'sin precio'."""
        l = Listing("facebook", "3", "Moto", "", price=None)
        self.assertEqual(l.pretty_price(), "sin precio")

    def test_pretty_price_with_float_values(self):
        """pretty_price formats floats with two decimal places when non-integer."""
        l = Listing("facebook", "4", "Moto", "", price=257.50, currency="BRL",
                    raw={"original_price": 50.0, "original_currency": "USD"})
        self.assertEqual(l.pretty_price(), "R$ 257,50 (US$ 50)")


if __name__ == "__main__":
    unittest.main()
