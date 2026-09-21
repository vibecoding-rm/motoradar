# Project: Motoradar Facebook Scraper Production Readiness

## Architecture
Motoradar is an offline-first, event-driven intelligence radar designed to capture low-cost motorcycles (<= R$ 1.000 BRL or equivalent UYU) in the border region of Jaguarão (RS, Brazil) and Río Branco (Cerro Largo, Uruguay) for acquisition, repair, and resale.

### Module & Package Boundaries
- `motoradar/sources/facebook.py`: Facebook Playwright-based scraper for Marketplace cards and Group feeds/posts. Provides DOM extraction, session management, and candidate card/post streaming.
- `motoradar/filters.py`: Location, price, and keyword filtering logic. Enforces strict geographic bounds.
- `motoradar/money.py`: Multi-currency exchange rate layer (`FX`), symbol detection, and currency conversions (USD, BRL, UYU).
- `motoradar/models.py`: Core data structures (`Listing`, `SearchResult`, `FilterConfig`, `Appraisal`) and text/price parsers.
- `motoradar/appraise.py`: Domain heuristics, motorcycle classification (`kind`), vehicle condition (`parts`, `project`, `runner`), buyer search detection (`wanted`), commercial ad detection (`shop_ad`), and decoy price evaluation (`bait_price`).
- `motoradar/enrich.py`: Detail enrichment and secondary price refinement from detail pages.
- `motoradar/pipeline.py`: Unified pipeline orchestrating FX normalization, filtering, appraisal, enrichment, and categorization into `opportunities`, `unconfirmed`, and `discarded`.
- `motoradar/store.py`: Atomic SQLite persistence, listing history, and multi-destination delivery queues with WAL mode and advisory file locking.
- `motoradar/cli.py`: CLI subcommands (`init`, `login`, `status`, `doctor`, `retry`, `run`, `deals`, `explain`, `watch`, and `collect`).

## Feature Inventory
Every feature and defect identified in the Survey phase is enumerated here with its assigned milestone. No feature may be left unassigned.

| # | Feature / Defect ID | Description | Milestone | Source |
|---|---|---|---|---|
| 1 | BUG-FB-GEO-01 | Eliminate `region_unknown` bypass in `filters.matches()` so unmapped groups undergo strict location checks | M1 | Explorer 1 |
| 2 | BUG-FB-GEO-02 | Prevent mapped groups from unconditionally assuming target cities; extract seller location cues from post text and discard distant cities | M1 | Explorer 1 |
| 3 | BUG-FB-GEO-03 | Disambiguate "rio branco" to avoid matching metropolitan neighborhoods (e.g. Porto Alegre, Novo Hamburgo, Acre) | M1 | Explorer 1 |
| 4 | FEAT-FB-GEO-07 | Implement negative city exclusion filter in `filters.py` for Pelotas, Bagé, Melo, Porto Alegre, Arroio Grande, Rio Grande, Herval | M1 | Explorer 1 |
| 5 | BUG-FB-GEO-04 | Harmonize configuration defaults (`cities: ["jaguarao", "rio branco"]`) across `config.py` and `config.example.yaml` | M1 | Explorer 1 |
| 6 | BUG-FB-GEO-05 | Preserve Marketplace cards with missing location lines for detail enrichment rather than dropping prematurely | M1 | Explorer 1 |
| 7 | H2-FB-ISO | Per-card and per-post `try/except` exception isolation in `facebook.py` (`_marketplace` and `_read_feed` generators) | M1 | Explorer 3 |
| 8 | BUG-R2-01 | Detect numerical sequence decoy prices (`R$ 1.234`, `1234`, `12345`, `1111`) as `bait_price` and tag as `unconfirmed` ("PRECIO POR CONFIRMAR") | M2 | Explorer 2 |
| 9 | BUG-R2-02 | Support single `$` Uruguayan currency notation (`$ 6.000`, `$6000`, `valor $ 6.000`) in price and currency detection | M2 | Explorer 2 |
| 10 | BUG-R2-03 | Currency-normalize comparison against `BAIT_MAX` in `enrich.fix_bait_prices` | M2 | Explorer 2 |
| 11 | FEAT-R2-THRESH | Enforce resale threshold <= R$ 1.000 BRL (or equivalent UYU) with exact conversion and inclusive upper bound | M2 | Explorer 2 |
| 12 | BUG-R3-01 | Spanish motorcycle accessories & spare parts (`casco`, `campera`, `cubierta`, `llanta`, `repuestos`, `freno`, `foco`, `cadena`) discarded from bike classification | M3 | Explorer 2 |
| 13 | BUG-R3-02 | Spanish purchase requests (`alguien tiene`, `necesito`, `ando buscando`, `comprar`, `pago contado`) marked as `wanted=True, buyable=False` | M3 | Explorer 2 |
| 14 | BUG-R3-03 | Spanish repair shops and services (`taller`, `mecanica`, `cadeteria`, `flete`) marked as `shop_ad=True, buyable=False` | M3 | Explorer 2 |
| 15 | BUG-R3-04 | Spanish real estate and vehicle trade-in ads (`propiedad`, `solar`, `local`) marked as `buyable=False` | M3 | Explorer 2 |
| 16 | BUG-R3-05 | Add Uruguayan domestic brands (`Winner`, `Yumbo`, `Baccio`, `Zanella`, `Keeway`, `Mondial`, `Vince`, `Vital`) to `BRAND_WORDS` and models | M3 | Explorer 2 |
| 17 | BUG-R3-06 | Expand `group_corpus.json` and domain tests with cross-border Spanish cases | M3 | Explorer 2 |
| 18 | H1-CLI-COLLECT | Register `collect` subparser in `cli.py` connecting to `cli.collect` with formatting and filtering flags | M4 | Explorer 3 |
| 19 | H3-CLI-DEADCODE | Remove unreachable dead return code in `watch_loop` (`cli.py:677-678`) | M4 | Explorer 3 |
| 20 | H4-DATE-PARSING | Enhance post age parsing in `facebook.py` for absolute and Portuguese/Spanish relative dates | M4 | Explorer 3 |
| 21 | E2E-TEST-SUITE | Build comprehensive opaque-box E2E test suite (Tiers 1-4) derived from user requirements and publish `TEST_READY.md` | E2E-Track | Orchestrator |
| 22 | FINAL-ACCEPTANCE | Pass 100% of E2E test suite and complete Tier 5 Adversarial Coverage Hardening | M5 | Orchestrator |

## Milestones

| # | Name | Scope | Dependencies | Status |
|---|---|---|---|---|
| M1 | Strict Geographic Delimitation & FB Scraper Extraction | Features 1, 2, 3, 4, 5, 6, 7 (`motoradar/filters.py`, `motoradar/sources/facebook.py`, `config*.yaml`, `config.py`) | none | DONE (218 tests passing, gate passed) |
| M2 | Currency Normalization, FX & Decoy Pricing | Features 8, 9, 10, 11 (`motoradar/money.py`, `motoradar/models.py`, `motoradar/enrich.py`, `motoradar/pipeline.py`) | none | DONE (250 unit + 62 E2E tests passing, Gate PASS) |
| M3 | Cross-Border Domain Classification & False Positive Elimination | Features 12, 13, 14, 15, 16, 17 (`motoradar/appraise.py`, `tests/fixtures/group_corpus.json`, `tests/test_clasificacion_grupos.py`, `tests/test_dominio.py`) | none | DONE (262 unit + 62 E2E tests passing, Gate PASS) |
| M4 | Production CLI (`collect`, `run`, `watch`) & Robustness | Features 18, 19, 20 (`motoradar/cli.py`, `motoradar/store.py`, `tests/test_cli.py`) | M1, M2, M3 | DONE (287 unit + 62 E2E tests passing, Gate PASS) |
| M5 | Final E2E Test Suite Pass & Adversarial Hardening | Feature 22: Pass 100% of E2E test suite (Tiers 1-4) + Tier 5 Adversarial Coverage Hardening | M4, E2E-Track | DONE (333 unit/integration + 62 E2E tests passing, Gate PASS) |
| E2E | E2E Testing Track (Opaque-Box Test Suite) | Feature 21: Test infrastructure and Tiers 1-4 opaque-box test cases publishing `TEST_READY.md` | none (Parallel Track) | DONE (62 E2E tests passing, TEST_READY.md published) |

## Interface Contracts

### `filters.py` ↔ `pipeline.py` & `sources/facebook.py`
- Function: `matches(listing: Listing, f: FilterConfig) -> tuple[bool, str]`
- Behavior:
  - Strict geographic matching: only returns `(True, "")` if `listing.location` or verified `search_region` matches Jaguarão (RS, Brasil) or Río Branco (Cerro Largo, Uruguay).
  - Explicit rejection `(False, "ciudad no coincide")` if location mentions Pelotas, Bagé, Melo, Porto Alegre, Arroio Grande, Rio Grande, or other non-target municipalities.
  - No bypass on `region_unknown=True`.

### `money.py` / `models.py` ↔ `pipeline.py`
- Functions:
  - `parse_price_text(text: str, default_currency: str = "BRL") -> tuple[float | None, str | None]`
  - `FX.convert(amount: float, frm: str, to: str) -> float | None`
- Behavior:
  - Recognizes single `$` followed by digits as `UYU` in border contexts.
  - Normalizes UYU to BRL via `FX.convert(amount, "UYU", "BRL")`.
  - Inclusive budget threshold check: `price <= cfg.budget` in base currency.
  - Decoy sequences (`1234`, `1.234`, `1111`, `12345`, `0.0`, `1.0`) must evaluate `bait_price = True` so `pipeline.prepare` places them into `SearchResult.unconfirmed` with label "PRECIO POR CONFIRMAR".

### `appraise.py` ↔ `pipeline.py`
- Function: `appraise(listing: Listing) -> Appraisal`
- Behavior:
  - `kind`: `"moto"`, `"auto"`, `"unknown"`. Must correctly recognize Uruguayan brands (`Winner`, `Yumbo`, `Baccio`, `Zanella`, `Keeway`, etc.) as `"moto"` even without generic word "moto".
  - `is_buyable`: True if and only if `kind == "moto" and not wanted and not shop_ad and not accessory`.
  - Non-motorcycle items (casco, campera, cubierta, llanta, repuestos, freno, foco, cadena) must set `is_buyable = False`.
  - Purchase requests (`alguien tiene moto`, `necesito moto`, `ando buscando`, `comprar moto`) must set `wanted = True` and `is_buyable = False`.
  - Services/workshops (`taller`, `mecanica`, `cadeteria`, `flete`) must set `shop_ad = True` and `is_buyable = False`.
  - Motorbike condition `parts`, `project`, `runner` and `doc_risk` (sin documentos, sin papeles) must remain buyable if price is within threshold.

### `cli.py` ↔ User / Production Runners
- Commands: `motoradar run`, `motoradar watch`, `motoradar collect`
- Subcommand `collect`:
  - Flags: `-c/--config`, `--source`, `--query`, `--format` (`json`, `console`, `csv`), `--no-filter`.
  - Exit code 0 on clean execution, no unhandled tracebacks.

## Code Layout
- Sources: `motoradar/`
  - `motoradar/sources/`: `facebook.py`, `mercadolibre.py`, `olx.py`
  - `motoradar/filters.py`
  - `motoradar/money.py`
  - `motoradar/models.py`
  - `motoradar/appraise.py`
  - `motoradar/enrich.py`
  - `motoradar/pipeline.py`
  - `motoradar/store.py`
  - `motoradar/locking.py`
  - `motoradar/cli.py`
  - `motoradar/config.py`
- Tests: `tests/`
  - `tests/fixtures/`: mock JSON files and HTML fixtures
  - `tests/test_facebook.py`
  - `tests/test_clasificacion_grupos.py`
  - `tests/test_dominio.py`
  - `tests/test_radar.py`
  - `tests/test_cli.py`
  - `tests/test_entrega.py`
  - `tests/test_robustez.py`
  - `tests/e2e/`: E2E opaque-box test suite
