"""Comprehensive 4-Tier E2E Opaque-Box Test Suite for Motoradar Facebook Scraper.

Invariants:
- 100% offline, zero network connections, zero browser launches.
- Tests public entrypoints: pipeline.prepare, cli.run_once, cli.collect,
  filters.matches, models.Listing, money.FX.
- Verifies R1 (Geography), R2 (Currency/FX/Budget), R3 (Domain Classification),
  and R4 (Robustness/CLI/Persistence).
"""
from __future__ import annotations

import copy
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from motoradar.cli import collect, run_once
from motoradar.config import Config, FilterConfig
from motoradar.filters import matches
from motoradar.locking import exclusive
from motoradar.models import Listing
from motoradar.money import FX
from motoradar.pipeline import prepare
from motoradar.sources.base import BaseSource
from motoradar.sources.facebook import card_to_listing, post_to_listing
from motoradar.store import Store


def _make_listing(
    uid: str,
    title: str,
    price: float | None = 800.0,
    currency: str = "BRL",
    location: str = "Jaguarão, RS",
    description: str = "",
    category: str = "veiculos/motos",
    url: str = "",
    search_region: str = "",
    region_unknown: bool = False,
) -> Listing:
    """Helper to create a standard Listing for opaque-box testing."""
    raw = {
        "description": description,
        "category": category,
        "search_region": search_region,
        "region_unknown": region_unknown,
    }
    return Listing(
        source="facebook",
        external_id=uid,
        title=title,
        url=url or f"https://www.facebook.com/marketplace/item/{uid}/",
        price=price,
        currency=currency,
        location=location,
        raw=raw,
    )


# =============================================================================
# TIER 1: FEATURE COVERAGE (R1, R2, R3, R4)
# =============================================================================
class TestTier1FeatureCoverage(unittest.TestCase):
    """Tier 1: Feature Coverage (>=5 tests per requirement R1, R2, R3, R4)."""

    def setUp(self):
        self.cfg = Config(
            budget=1000.0,
            fx={"offline": True, "base": "BRL"},
            filters=FilterConfig(cities=["jaguarao", "rio branco"]),
        )
        self.fx = FX(offline=True)

    # --- R1: Strict Geographic Delimitation ---
    def test_r1_accepts_jaguarao_location(self):
        """R1: Listings explicitly located in Jaguarão are accepted."""
        l = _make_listing("geo_jag", "Honda CG 125", price=800.0, location="Jaguarão, RS")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)
        self.assertEqual(res.opportunities[0].external_id, "geo_jag")

    def test_r1_accepts_rio_branco_location(self):
        """R1: Listings explicitly located in Río Branco are accepted."""
        l = _make_listing("geo_rb", "Honda CG 125", price=800.0, location="Río Branco, Cerro Largo, Uruguay")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)
        self.assertEqual(res.opportunities[0].external_id, "geo_rb")

    def test_r1_rejects_pelotas_location(self):
        """R1: Listings located in distant Pelotas are discarded."""
        l = _make_listing("geo_pel", "Honda CG 125", price=800.0, location="Pelotas, RS")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("ciudad no coincide"), 1)
        self.assertEqual(l.raw.get("alert_status"), "excluded")

    def test_r1_rejects_bage_location(self):
        """R1: Listings located in distant Bagé are discarded."""
        l = _make_listing("geo_bage", "Honda CG 125", price=800.0, location="Bagé, RS")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("ciudad no coincide"), 1)

    def test_r1_rejects_melo_location(self):
        """R1: Listings located in distant Melo are discarded."""
        l = _make_listing("geo_melo", "Honda CG 125", price=800.0, location="Melo, Cerro Largo")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("ciudad no coincide"), 1)

    def test_r1_rejects_porto_alegre_location(self):
        """R1: Listings located in metropolitan Porto Alegre are discarded."""
        l = _make_listing("geo_poa", "Honda CG 125", price=800.0, location="Porto Alegre, RS")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("ciudad no coincide"), 1)

    # --- R2: Currency Normalization, FX & Resale Threshold ---
    def test_r2_brl_within_budget_accepted(self):
        """R2: BRL listings within budget (<= R$ 1.000) are confirmed."""
        l = _make_listing("fx_brl_ok", "Yamaha Factor 125", price=850.0, currency="BRL")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)
        self.assertEqual(res.opportunities[0].price, 850.0)
        self.assertEqual(res.opportunities[0].currency, "BRL")

    def test_r2_uyu_within_budget_converted_and_accepted(self):
        """R2: UYU listings converted below budget threshold are confirmed."""
        # 6000 UYU / 40.2 * 5.15 = 768.66 BRL <= 1000.0
        l = _make_listing("fx_uyu_ok", "Honda CG 125", price=6000.0, currency="UYU", location="Río Branco")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)
        opp = res.opportunities[0]
        self.assertEqual(opp.currency, "BRL")
        self.assertAlmostEqual(opp.price, 768.66, places=2)
        self.assertEqual(opp.raw.get("original_currency"), "UYU")
        self.assertEqual(opp.raw.get("original_price"), 6000.0)

    def test_r2_brl_over_budget_discarded(self):
        """R2: BRL listings over budget threshold are discarded."""
        l = _make_listing("fx_brl_over", "Honda CG 125", price=1200.0, currency="BRL")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("fuera de presupuesto"), 1)

    def test_r2_uyu_over_budget_converted_and_discarded(self):
        """R2: UYU listings converted above budget threshold are discarded."""
        # 12000 UYU / 40.2 * 5.15 = 1537.31 BRL > 1000.0
        l = _make_listing("fx_uyu_over", "Honda CG 125", price=12000.0, currency="UYU", location="Río Branco")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("fuera de presupuesto"), 1)

    def test_r2_decoy_bait_price_classified_as_unconfirmed(self):
        """R2: Decoy bait price (<= BAIT_PRICE 100) classified as unconfirmed."""
        l = _make_listing("fx_bait", "Honda CG 125", price=50.0, currency="BRL")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(len(res.unconfirmed), 1)
        self.assertEqual(res.unconfirmed[0].raw.get("alert_status"), "unconfirmed")

    def test_r2_missing_price_classified_as_unconfirmed(self):
        """R2: Missing price (None) classified as unconfirmed and not discarded."""
        l = _make_listing("fx_none", "Honda CG 125", price=None, currency="BRL")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(len(res.unconfirmed), 1)
        self.assertEqual(res.unconfirmed[0].raw.get("alert_status"), "unconfirmed")

    # --- R3: Domain Classification & False Positive Elimination ---
    def test_r3_running_bike_accepted_as_runner(self):
        """R3: Running motorcycle is classified as moto in runner condition."""
        l = _make_listing("dom_run", "Honda CG 125 funcionando em otimo estado", price=900.0)
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)
        self.assertEqual(res.opportunities[0].raw.get("condition"), "runner")

    def test_r3_project_bike_accepted_as_project(self):
        """R3: Project motorcycle (broken engine) is accepted as project."""
        l = _make_listing("dom_proj", "Moto projeto para restaurar motor fundido", price=700.0)
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)
        self.assertEqual(res.opportunities[0].raw.get("condition"), "project")

    def test_r3_donor_bike_accepted_as_parts(self):
        """R3: Donor motorcycle for parts disassembly is accepted."""
        l = _make_listing("dom_part", "Moto sucata para retirada de pecas", price=500.0)
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)
        self.assertEqual(res.opportunities[0].raw.get("condition"), "parts")

    def test_r3_accessory_apparel_discarded(self):
        """R3: Helmet / apparel accessory is discarded from motorcycle alerts."""
        l = _make_listing("dom_helm", "Capacete peels novo fechado com viseira", price=250.0)
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("pedido/publicidad/repuesto/desconocido"), 1)

    def test_r3_standalone_spare_part_discarded(self):
        """R3: Standalone spare part (tanque) is discarded."""
        l = _make_listing("dom_tank", "Tanque de combustivel Titan 150", price=200.0)
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("pedido/publicidad/repuesto/desconocido"), 1)

    def test_r3_purchase_request_wanted_discarded(self):
        """R3: Buyer search request ('Procuro moto') is discarded."""
        l = _make_listing("dom_proc", "Procuro moto ate 1000 reais urgente", price=1000.0)
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("pedido/publicidad/repuesto/desconocido"), 1)

    def test_r3_commercial_rental_ad_discarded(self):
        """R3: Commercial rental ad ('Alugo moto') is discarded."""
        l = _make_listing("dom_rent", "Alugo moto por dia para entregas", price=50.0)
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("pedido/publicidad/repuesto/desconocido"), 1)

    # --- R4: Production Robustness & CLI Pipeline ---
    def test_r4_prepare_partitions_listings_consistently(self):
        """R4: prepare partitions listings into mutually exclusive categories."""
        listings = [
            _make_listing("p1", "Honda CG 125", price=800.0, location="Jaguarão, RS"),
            _make_listing("p2", "Honda CG 125", price=None, location="Jaguarão, RS"),
            _make_listing("p3", "Honda CG 125", price=800.0, location="Pelotas, RS"),
            _make_listing("p4", "Capacete novo", price=200.0, location="Jaguarão, RS"),
        ]
        res = prepare(listings, self.cfg, self.fx, enrich_details=False)
        total_categorized = len(res.opportunities) + len(res.unconfirmed) + sum(res.discarded.values())
        self.assertEqual(total_categorized, len(res.observed))
        self.assertEqual(len(res.opportunities), 1)
        self.assertEqual(len(res.unconfirmed), 1)
        self.assertEqual(sum(res.discarded.values()), 2)

    def test_r4_opportunities_sorted_by_price_ascending(self):
        """R4: Returned opportunities are sorted strictly by price ascending."""
        listings = [
            _make_listing("s1", "Honda CG 125", price=900.0),
            _make_listing("s2", "Yamaha Factor", price=500.0),
            _make_listing("s3", "Suzuki Intruder", price=750.0),
            _make_listing("s4", "Honda Biz", price=600.0),
        ]
        res = prepare(listings, self.cfg, self.fx, enrich_details=False)
        prices = [opp.price for opp in res.opportunities]
        self.assertEqual(prices, [500.0, 600.0, 750.0, 900.0])

    def test_r4_cli_collect_with_registered_mock_source(self):
        """R4: cli.collect retrieves candidates and reports correct stats."""
        class MockSource(BaseSource):
            def fetch(self, cfg, opts):
                yield _make_listing("col_1", "Honda CG 125", price=800.0, location="Jaguarão, RS")
                yield _make_listing("col_2", "Honda CG 125", price=800.0, location="Pelotas, RS")

        with patch.dict("motoradar.cli.REGISTRY", {"mock_src": MockSource()}):
            with redirect_stdout(io.StringIO()):
                found, stats = collect(self.cfg, only=["mock_src"], apply_filters=True, fx=self.fx)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].external_id, "col_1")
            self.assertIn("mock_src", stats)
            self.assertEqual(stats["mock_src"]["status"], "ok")
            self.assertEqual(stats["mock_src"]["observed"], 1)
            self.assertEqual(stats["mock_src"]["discarded"], 1)

    def test_r4_cli_run_once_dry_run_executes_without_exception(self):
        """R4: cli.run_once executes in dry_run mode cleanly without unhandled errors."""
        class MockSource(BaseSource):
            def fetch(self, cfg, opts):
                yield _make_listing("ro_1", "Honda CG 125", price=800.0, location="Jaguarão, RS")

        with tempfile.TemporaryDirectory() as td:
            cfg = Config(
                db_path=str(Path(td) / "radar.db"),
                csv_path=str(Path(td) / "hits.csv"),
                budget=1000.0,
                fx={"offline": True},
                filters=FilterConfig(cities=["jaguarao"]),
            )
            with patch.dict("motoradar.cli.REGISTRY", {"mock_src": MockSource()}):
                with redirect_stdout(io.StringIO()):
                    count = run_once(cfg, only=["mock_src"], dry_run=True, enrich_details=False)
                # run_once returns number of fresh opportunities found (1)
                self.assertEqual(count, 1)

    def test_r4_store_persistence_atomic_and_idempotent(self):
        """R4: Store persistence handles repeated observations idempotently."""
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "radar.db")
            store = Store(db_path)
            try:
                l = _make_listing("store_1", "Honda CG 125", price=800.0, location="Jaguarão, RS")
                l.raw["alert_status"] = "confirmed"
                l.raw["condition"] = "runner"
                l.raw["doc_risk"] = False

                # First observation of fresh opportunity
                is_new = store.upsert_many(l, ["dest_1"], alert=True)
                self.assertTrue(is_new)

                # Second observation of same opportunity without price change
                is_repeat = store.upsert_many(l, ["dest_1"], alert=True)
                self.assertFalse(is_repeat)
            finally:
                store.close()


# =============================================================================
# TIER 2: BOUNDARY & CORNER CASES
# =============================================================================
class TestTier2BoundaryAndCornerCases(unittest.TestCase):
    """Tier 2: Boundary & Corner Cases (>=5 tests per requirement R1, R2, R3, R4)."""

    def setUp(self):
        self.cfg = Config(
            budget=1000.0,
            fx={"offline": True, "base": "BRL"},
            filters=FilterConfig(cities=["jaguarao", "rio branco"]),
        )
        self.fx = FX(offline=True)

    # --- R1 Boundaries ---
    def test_r1_case_insensitive_matching(self):
        """R1 Boundary: City matching is case-insensitive."""
        l1 = _make_listing("c_up1", "Honda CG 125", location="JAGUARÃO, RS")
        l2 = _make_listing("c_up2", "Honda CG 125", location="RIO BRANCO, CERRO LARGO")
        res = prepare([l1, l2], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 2)

    def test_r1_accent_normalization(self):
        """R1 Boundary: City matching normalizes diacritics/accents."""
        l1 = _make_listing("c_acc1", "Honda CG 125", location="Jaguarao")  # no tilde
        l2 = _make_listing("c_acc2", "Honda CG 125", location="Rio Branco")  # no acute accent
        res = prepare([l1, l2], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 2)

    def test_r1_whitespace_and_punctuation_padding(self):
        """R1 Boundary: City matching handles extra spaces and dashes."""
        l = _make_listing("c_ws", "Honda CG 125", location="  Jaguarão  -  RS  ")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)

    def test_r1_compound_location_string(self):
        """R1 Boundary: Compound location containing target city matches."""
        l = _make_listing("c_comp", "Honda CG 125", location="Jaguarão centro perto da ponte")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)

    def test_r1_non_target_city_rejected(self):
        """R1 Boundary: Non-target city location is discarded with 'ciudad no coincide'."""
        l = _make_listing("c_other", "Honda CG 125", location="Passo Fundo, RS")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("ciudad no coincide"), 1)

    def test_r1_card_missing_location_preserved_for_enrichment(self):
        """R1 Boundary (BUG-FB-GEO-05): Marketplace cards with missing location preserved for detail enrichment."""
        card_raw = {
            "href": "/marketplace/item/999123/",
            "text": "R$ 800\nHonda CG 125",
            "img": "",
        }
        l = card_to_listing(card_raw)
        self.assertEqual(l.location, "")
        ok, reason = matches(l, self.cfg.filters)
        # Preserved for enrichment instead of dropped prematurely
        self.assertTrue(ok, f"Card without location should pass for enrichment, got {reason}")

    # --- R2 Boundaries ---
    def test_r2_exact_budget_upper_bound_inclusive(self):
        """R2 Boundary: Exactly R$ 1.000,00 qualifies as confirmed opportunity."""
        l = _make_listing("b_exact", "Honda CG 125", price=1000.0)
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)
        self.assertEqual(res.opportunities[0].price, 1000.0)

    def test_r2_boundary_plus_one_cent_discarded(self):
        """R2 Boundary: R$ 1.000,01 exceeds budget and is discarded."""
        l = _make_listing("b_exceed", "Honda CG 125", price=1000.01)
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("fuera de presupuesto"), 1)

    def test_r2_card_zero_price_mapped_to_none_and_unconfirmed(self):
        """R2 Boundary: Marketplace card reading R$ 0 mapped to price=None and unconfirmed."""
        card_raw = {
            "href": "/marketplace/item/111/",
            "text": "R$ 0\nHonda CG 125\nJaguarão, RS",
            "img": "",
        }
        l = card_to_listing(card_raw)
        self.assertIsNone(l.price)
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.unconfirmed), 1)

    def test_r2_bait_price_threshold_boundary(self):
        """R2 Boundary: Boundary check for BAIT_PRICE (<= 100 is bait, 101 is not)."""
        l_bait = _make_listing("b_100", "Honda CG 125", price=100.0)
        l_real = _make_listing("b_101", "Honda CG 125", price=101.0)
        res = prepare([l_bait, l_real], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.unconfirmed), 1)
        self.assertEqual(res.unconfirmed[0].external_id, "b_100")
        self.assertEqual(len(res.opportunities), 1)
        self.assertEqual(res.opportunities[0].external_id, "b_101")

    def test_r2_unsupported_currency_conversion_handling(self):
        """R2 Boundary: Unsupported currency produces conversion error and exclusion."""
        l = _make_listing("b_eur", "Honda CG 125", price=150.0, currency="EUR")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("moneda sin conversion"), 1)
        self.assertIn("fx_error", l.raw)

    # --- R3 Boundaries ---
    def test_r3_broken_bike_without_papers_accepted(self):
        """R3 Boundary: Bike without papers and with issues accepted if within budget."""
        l = _make_listing(
            "b_nopapers",
            "Honda CG 125 sem documentos so pra rodar motor batendo",
            price=600.0,
        )
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)
        self.assertTrue(res.opportunities[0].raw.get("doc_risk"))

    def test_r3_post_greeting_prefix_preserves_bike_classification(self):
        """R3 Boundary: Post beginning with conversational greeting classifies bike correctly."""
        post = {
            "id": "b_greet",
            "text": "Bom dia grupo! Vendo essa 125 andando, 800",
            "blocks": ["Bom dia grupo! Vendo essa 125 andando, 800"],
            "href": "",
            "img": "",
        }
        l = post_to_listing("999", post, "jaguarao", False)
        l.price = 800.0
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)

    def test_r3_motorola_phone_false_positive_rejected(self):
        """R3 Boundary: Motorola smartphone 'Moto G 22' rejected as non-vehicle."""
        l = _make_listing("b_phone", "Vendo celular Moto G 22 azul 128gb", price=450.0)
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("pedido/publicidad/repuesto/desconocido"), 1)

    def test_r3_car_false_positive_rejected(self):
        """R3 Boundary: Automobile advertisement ('Gol 1.0') rejected."""
        l = _make_listing("b_car", "Vendo Gol 1.0 motor bom aceito troca", price=950.0)
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("pedido/publicidad/repuesto/desconocido"), 1)

    def test_r3_property_tradein_rejected(self):
        """R3 Boundary: Real estate offering bike as trade-in is rejected."""
        l = _make_listing("b_house", "Vendo casa no centro aceito moto na troca", price=950.0)
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("pedido/publicidad/repuesto/desconocido"), 1)

    def test_r3_power_equipment_machinery_rejected(self):
        """R3 Boundary: Lawn and power machinery ('rocadeira') rejected."""
        l = _make_listing("b_mach", "Vendo rocadeira CG 520 a gasolina revisada", price=600.0)
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("pedido/publicidad/repuesto/desconocido"), 1)

    # --- R4 Boundaries ---
    def test_r4_empty_feed_zero_listings(self):
        """R4 Boundary: Processing empty list of listings returns clean empty SearchResult."""
        res = prepare([], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(res.observed, [])
        self.assertEqual(res.opportunities, [])
        self.assertEqual(res.unconfirmed, [])
        self.assertEqual(res.discarded, {})

    def test_r4_duplicate_listings_deduplicated_by_uid(self):
        """R4 Boundary: Duplicate listings with same UID are deduplicated."""
        l1 = _make_listing("dup1", "Honda CG 125", price=800.0)
        l2 = copy.deepcopy(l1)
        res = prepare([l1, l2], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.observed), 1)
        self.assertEqual(len(res.opportunities), 1)

    def test_r4_corrupted_raw_attributes_handled_safely(self):
        """R4 Boundary: Missing raw metadata dictionaries handled without exception."""
        l = Listing("facebook", "corrupt_1", "Honda CG 125", "", price=800.0, location="Jaguarão, RS")
        l.raw = {}  # completely empty raw dict
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)

    def test_r4_special_characters_and_emojis_in_title(self):
        """R4 Boundary: Emojis and special unicode symbols do not cause crash."""
        l = _make_listing("b_emoji", "🏍️ Honda CG 125 🔥 oportunidade R$ 800 💥", price=800.0)
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)

    def test_r4_exclusive_locking_releases_on_exit(self):
        """R4 Boundary: Advisory file lock releases immediately when context exits."""
        with tempfile.TemporaryDirectory() as td:
            lock_path = str(Path(td) / "test.lock")
            with exclusive(lock_path):
                pass
            # Must be re-acquirable immediately
            with exclusive(lock_path):
                pass


# =============================================================================
# TIER 3: PAIRWISE CROSS-FEATURE COMBINATIONS
# =============================================================================
class TestTier3PairwiseCrossFeature(unittest.TestCase):
    """Tier 3: Pairwise Cross-Feature Combinations.

    Systematically crosses:
    - Geography: Jaguarão vs Río Branco vs Pelotas
    - Currency: BRL vs UYU vs Unsupported (EUR)
    - Condition: Runner vs Project vs Parts vs Accessory
    - Budget: Within budget vs Over budget vs Decoy
    """

    def setUp(self):
        self.cfg = Config(
            budget=1000.0,
            fx={"offline": True, "base": "BRL"},
            filters=FilterConfig(cities=["jaguarao", "rio branco"]),
        )
        self.fx = FX(offline=True)

    def test_combo_jaguarao_brl_runner_within_budget(self):
        """Combo 1: In-zone (Jaguarão) + BRL + Runner + Within budget -> Confirmed Opportunity."""
        l = _make_listing("c1", "Honda CG 125 funcionando", price=800.0, currency="BRL", location="Jaguarão, RS")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)
        self.assertEqual(res.opportunities[0].raw.get("alert_status"), "confirmed")

    def test_combo_rio_branco_uyu_runner_within_budget(self):
        """Combo 2: In-zone (Río Branco) + UYU + Runner + Within budget -> Confirmed Opportunity."""
        # 6000 UYU = 768.66 BRL <= 1000.0
        l = _make_listing("c2", "Honda CG 125 en marcha", price=6000.0, currency="UYU", location="Río Branco")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)
        self.assertEqual(res.opportunities[0].raw.get("alert_status"), "confirmed")

    def test_combo_jaguarao_brl_project_within_budget(self):
        """Combo 3: In-zone (Jaguarão) + BRL + Project + Within budget -> Confirmed Opportunity."""
        l = _make_listing("c3", "Moto projeto motor fundido", price=700.0, currency="BRL", location="Jaguarão, RS")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)
        self.assertEqual(res.opportunities[0].raw.get("condition"), "project")

    def test_combo_rio_branco_uyu_parts_within_budget(self):
        """Combo 4: In-zone (Río Branco) + UYU + Parts + Within budget -> Confirmed Opportunity."""
        # 3500 UYU = 448.38 BRL <= 1000.0
        l = _make_listing("c4", "Moto para desarme y repuestos sucata", price=3500.0, currency="UYU", location="Río Branco")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 1)
        self.assertEqual(res.opportunities[0].raw.get("condition"), "parts")

    def test_combo_pelotas_brl_runner_within_budget(self):
        """Combo 5: Out-of-zone (Pelotas) + BRL + Runner + Within budget -> Discarded by city."""
        l = _make_listing("c5", "Honda CG 125 funcionando", price=500.0, currency="BRL", location="Pelotas, RS")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("ciudad no coincide"), 1)

    def test_combo_pelotas_uyu_runner_within_budget(self):
        """Combo 6: Out-of-zone (Pelotas) + UYU + Runner + Within budget -> Discarded by city."""
        l = _make_listing("c6", "Honda CG 125 funcionando", price=4000.0, currency="UYU", location="Pelotas, RS")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("ciudad no coincide"), 1)

    def test_combo_jaguarao_brl_runner_over_budget(self):
        """Combo 7: In-zone (Jaguarão) + BRL + Runner + Over budget -> Discarded by budget."""
        l = _make_listing("c7", "Honda CG 125 funcionando", price=1500.0, currency="BRL", location="Jaguarão, RS")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("fuera de presupuesto"), 1)

    def test_combo_rio_branco_uyu_runner_over_budget(self):
        """Combo 8: In-zone (Río Branco) + UYU + Runner + Over budget -> Discarded by budget."""
        # 15000 UYU = 1921.64 BRL > 1000.0
        l = _make_listing("c8", "Honda CG 125 funcionando", price=15000.0, currency="UYU", location="Río Branco")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("fuera de presupuesto"), 1)

    def test_combo_jaguarao_brl_accessory_within_budget(self):
        """Combo 9: In-zone (Jaguarão) + BRL + Accessory + Within budget -> Discarded by domain."""
        l = _make_listing("c9", "Capacete fechado novo", price=200.0, currency="BRL", location="Jaguarão, RS")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("pedido/publicidad/repuesto/desconocido"), 1)

    def test_combo_rio_branco_uyu_accessory_within_budget(self):
        """Combo 10: In-zone (Río Branco) + UYU + Accessory + Within budget -> Discarded by domain."""
        l = _make_listing("c10", "Tanque de moto Biz", price=1000.0, currency="UYU", location="Río Branco")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("pedido/publicidad/repuesto/desconocido"), 1)

    def test_combo_jaguarao_eur_runner_within_budget(self):
        """Combo 11: In-zone (Jaguarão) + Unsupported currency + Runner -> Discarded by FX error."""
        l = _make_listing("c11", "Honda CG 125", price=100.0, currency="EUR", location="Jaguarão, RS")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(res.discarded.get("moneda sin conversion"), 1)

    def test_combo_jaguarao_brl_runner_decoy_price(self):
        """Combo 12: In-zone (Jaguarão) + BRL + Runner + Decoy price -> Classified as Unconfirmed."""
        l = _make_listing("c12", "Honda CG 125", price=50.0, currency="BRL", location="Jaguarão, RS")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(len(res.unconfirmed), 1)

    def test_combo_rio_branco_uyu_runner_missing_price(self):
        """Combo 13: In-zone (Río Branco) + Runner + Missing price -> Classified as Unconfirmed."""
        l = _make_listing("c13", "Honda CG 125", price=None, currency="UYU", location="Río Branco")
        res = prepare([l], self.cfg, self.fx, enrich_details=False)
        self.assertEqual(len(res.opportunities), 0)
        self.assertEqual(len(res.unconfirmed), 1)


# =============================================================================
# TIER 4: REAL-WORLD APPLICATION SCENARIOS
# =============================================================================
class TestTier4RealWorldScenarios(unittest.TestCase):
    """Tier 4: Realistic End-to-End Multi-Listing Scraping Runs."""

    def setUp(self):
        self.cfg = Config(
            budget=1000.0,
            fx={"offline": True, "base": "BRL"},
            filters=FilterConfig(cities=["jaguarao", "rio branco"]),
        )
        self.fx = FX(offline=True)

    def test_scenario_1_full_marketplace_batch_run_jaguarao(self):
        """Scenario 1: Full batch of 10 heterogeneous Marketplace cards from Jaguarão."""
        cards = [
            _make_listing("mp1", "Honda CG 125 Titan 2008 andando", price=850.0, location="Jaguarão, RS"),
            _make_listing("mp2", "Yamaha Factor 125 2011", price=950.0, location="Jaguarão, RS"),
            _make_listing("mp3", "Honda CG 150 Titan 2015 impecavel", price=4500.0, location="Jaguarão, RS"),
            _make_listing("mp4", "Capacete Norisk semi novo", price=200.0, location="Jaguarão, RS"),
            _make_listing("mp5", "Sucata de Biz 100 pra retirar pecas", price=400.0, location="Jaguarão, RS"),
            _make_listing("mp6", "Honda Titan 125 pra negocio", price=None, location="Jaguarão, RS"),
            _make_listing("mp7", "Honda CG 125 Fan", price=800.0, location="Pelotas, RS"),
            _make_listing("mp8", "Yamaha Factor 125", price=750.0, location="Bagé, RS"),
            _make_listing("mp9", "Celular Motorola Moto G 52", price=600.0, location="Jaguarão, RS"),
            _make_listing("mp10", "Fiat Uno 2002 andando", price=900.0, location="Jaguarão, RS"),
        ]
        res = prepare(cards, self.cfg, self.fx, enrich_details=False)

        # Exact expected business outcomes:
        # Confirmed: mp5 (400), mp1 (850), mp2 (950)
        self.assertEqual(len(res.opportunities), 3)
        self.assertEqual([o.external_id for o in res.opportunities], ["mp5", "mp1", "mp2"])
        self.assertEqual([o.price for o in res.opportunities], [400.0, 850.0, 950.0])

        # Unconfirmed: mp6 (None)
        self.assertEqual(len(res.unconfirmed), 1)
        self.assertEqual(res.unconfirmed[0].external_id, "mp6")

        # Discarded: mp3 (budget), mp4 (accessory), mp7 (city), mp8 (city), mp9 (phone), mp10 (car)
        self.assertEqual(sum(res.discarded.values()), 6)
        self.assertEqual(res.discarded.get("fuera de presupuesto"), 1)
        self.assertEqual(res.discarded.get("ciudad no coincide"), 2)
        self.assertEqual(res.discarded.get("pedido/publicidad/repuesto/desconocido"), 3)

    def test_scenario_2_cross_border_group_feed_rio_branco(self):
        """Scenario 2: Cross-border group feed simulation with Uruguayan posts."""
        feed = [
            _make_listing("grp1", "Se vende moto 110cc en Rio Branco, 6.000 pesos", price=6000.0, currency="UYU", location="Río Branco"),
            _make_listing("grp2", "Vendo moto en Rio Branco, 12.000 pesos", price=12000.0, currency="UYU", location="Río Branco"),
            _make_listing("grp3", "Passo moto projeto, motor fundido, valor 700 reais", price=700.0, currency="BRL", location="Jaguarão, RS"),
            _make_listing("grp4", "Ando buscando moto para comprar pago contado", price=800.0, currency="BRL", location="Jaguarão, RS"),
            _make_listing("grp5", "Taller mecanico de motos hacemos service", price=50.0, currency="BRL", location="Jaguarão, RS"),
            _make_listing("grp6", "Vendo moto Winner 110 papeles al dia", price=None, currency="UYU", location="Río Branco"),
        ]
        res = prepare(feed, self.cfg, self.fx, enrich_details=False)

        # Confirmed opportunities: grp3 (700 BRL) and grp1 (6000 UYU = 768.66 BRL)
        self.assertEqual(len(res.opportunities), 2)
        opp_ids = [o.external_id for o in res.opportunities]
        self.assertEqual(opp_ids, ["grp3", "grp1"])

        # Unconfirmed: grp6 (no price)
        self.assertEqual(len(res.unconfirmed), 1)
        self.assertEqual(res.unconfirmed[0].external_id, "grp6")

        # Discarded: grp2 (out of budget), grp4 (wanted request), grp5 (shop ad)
        self.assertEqual(sum(res.discarded.values()), 3)
        self.assertEqual(res.discarded.get("fuera de presupuesto"), 1)
        self.assertEqual(res.discarded.get("pedido/publicidad/repuesto/desconocido"), 2)

    def test_scenario_3_end_to_end_cli_pipeline_with_sqlite_and_csv(self):
        """Scenario 3: End-to-end execution of cli.run_once with temporary SQLite DB and CSV."""
        class MockFB(BaseSource):
            def fetch(self, cfg, opts):
                yield _make_listing("cli_1", "Honda CG 125 funcionando", price=800.0, location="Jaguarão, RS")
                yield _make_listing("cli_2", "Honda CG 125 en marcha", price=6000.0, currency="UYU", location="Río Branco")
                yield _make_listing("cli_3", "Honda CG 125", price=1500.0, location="Jaguarão, RS")
                yield _make_listing("cli_4", "Honda CG 125", price=800.0, location="Pelotas, RS")

        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "radar.db")
            csv_path = str(Path(td) / "hits.csv")
            cfg = Config(
                db_path=db_path,
                csv_path=csv_path,
                budget=1000.0,
                fx={"offline": True},
                filters=FilterConfig(cities=["jaguarao", "rio branco"]),
                telegram={"token": "test_token", "chat_id": "12345"},
            )

            with patch.dict("motoradar.cli.REGISTRY", {"facebook": MockFB()}):
                with redirect_stdout(io.StringIO()):
                    count = run_once(cfg, only=["facebook"], dry_run=False, enrich_details=False)
                # Exactly 2 confirmed opportunities queued and returned
                self.assertEqual(count, 2)

            # Verify SQLite contents
            store = Store(db_path)
            try:
                listings_count = store.conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
                self.assertEqual(listings_count, 4)

                # Exactly 2 confirmed opportunities queued for delivery
                deliveries = store.conn.execute(
                    "SELECT uid, state FROM deliveries WHERE destination='12345'"
                ).fetchall()
                self.assertEqual(len(deliveries), 2)
                delivered_uids = {row[0] for row in deliveries}
                self.assertIn("facebook:cli_1", delivered_uids)
                self.assertIn("facebook:cli_2", delivered_uids)
            finally:
                store.close()

            # Verify CSV generation
            csv_file = Path(csv_path)
            self.assertTrue(csv_file.exists())
            content = csv_file.read_bytes()
            self.assertTrue(content.startswith(b"\xef\xbb\xbf"))  # UTF-8 BOM
