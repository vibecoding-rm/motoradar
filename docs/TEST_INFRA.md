# Motoradar E2E Test Infrastructure & Methodology

## 1. Overview & Test Philosophy

Motoradar is an intelligence radar designed to capture low-cost motorcycles ($\le \text{R\$}~1.000$ BRL or equivalent UYU) in the border region of Jaguarão (RS, Brazil) and Río Branco (Cerro Largo, Uruguay) for acquisition, repair, and resale.

The End-to-End (E2E) Test Suite establishes an authoritative, opaque-box verification harness ensuring production readiness for the Facebook Scraper track.

### 1.1 Core Principles
- **Opaque-Box Testing**: Tests interact with the system strictly through public entrypoints (`motoradar.pipeline.prepare`, `motoradar.cli.run_once`, `motoradar.cli.collect`, `motoradar.sources.facebook.card_to_listing`, `post_to_listing`, `motoradar.filters.matches`, `motoradar.money.FX`). No private internal states or private functions are probed or altered.
- **Requirement-Driven**: Every test case directly derives its expected output from user requirements (`ORIGINAL_REQUEST.md`), product rules (`AGENTS.md`), and the architecture specification (`PROJECT.md`).
- **Progressive Testability**: Tests are self-contained and independently verifiable using features and invariants established for the system.
- **Zero Flakiness & Determinism**: All test runs are hermetic, with isolated temporary directories, ephemeral in-memory or temporary SQLite databases, and clean fixtures.

### 1.2 System Invariants
- **100% Offline**: No network calls, socket connections, or external API invocations. Any real HTTP request is strictly forbidden.
- **Zero Browser Launches**: No actual browser processes (Chromium, Firefox, WebKit) are spawned; Playwright DOM extraction is tested via sanitized HTML/DOM structures and parser fixtures.
- **Privacy & Security**: Zero persistent credentials, tokens, or real user identifiers in tests or output files.
- **Transactional Consistency**: Atomic persistence in SQLite and delivery queues without partial state corruption.

---

## 2. 4-Tier Test Architecture

The E2E test suite is organized into four hierarchical verification tiers:

```
+-------------------------------------------------------------------------+
|                  Tier 4: Real-World Application Scenarios               |
|      (Realistic multi-listing Marketplace & Group scraping runs)        |
+-------------------------------------------------------------------------+
                                    |
+-------------------------------------------------------------------------+
|             Tier 3: Pairwise Cross-Feature Combinations                 |
|      (Border Location x Currency x Condition x Budget Combinations)     |
+-------------------------------------------------------------------------+
                                    |
+-------------------------------------------------------------------------+
|                  Tier 2: Boundary & Corner Cases                        |
|   (Exact budget limits, decoy prices, accents/casing, empty feeds)      |
+-------------------------------------------------------------------------+
                                    |
+-------------------------------------------------------------------------+
|                    Tier 1: Feature Coverage                             |
|          (R1: Geography, R2: FX, R3: Domain, R4: Robustness/CLI)        |
+-------------------------------------------------------------------------+
```

### 2.1 Tier 1: Feature Coverage
Validates the baseline acceptance criteria for each primary requirement ($\ge 5$ tests per requirement):
- **Requirement R1 (Strict Geographic Delimitation)**:
  1. Acceptance of listings with explicit location "Jaguarão, RS".
  2. Acceptance of listings with explicit location "Río Branco, Uruguay".
  3. Rejection of distant Brazilian city "Pelotas, RS" (`ciudad no coincide`).
  4. Rejection of distant Brazilian city "Bagé, RS" (`ciudad no coincide`).
  5. Rejection of distant Uruguayan city "Melo, Uruguay" (`ciudad no coincide`).
  6. Rejection of distant metropolitan center "Porto Alegre, RS" (`ciudad no coincide`).
- **Requirement R2 (Currency, FX Conversion & Budget Threshold)**:
  1. Inclusion of BRL listing within budget ($\text{R\$}~800 \le \text{R\$}~1.000$).
  2. Inclusion of UYU listing converted to BRL ($6.000~\text{UYU} \approx \text{R\$}~768.66 \le \text{R\$}~1.000$).
  3. Exclusion of BRL listing exceeding budget ($\text{R\$}~1.200 > \text{R\$}~1.000$).
  4. Exclusion of UYU listing exceeding budget ($12.000~\text{UYU} \approx \text{R\$}~1.537.31 > \text{R\$}~1.000$).
  5. Classification of decoy price ($\text{R\$}~50.0 \le \text{BAIT\_PRICE}$) into `unconfirmed` ("PRECIO POR CONFIRMAR").
  6. Classification of missing price (`price = None`) into `unconfirmed` without dropping.
- **Requirement R3 (Domain Classification & False Positive Elimination)**:
  1. Inclusion of running motorcycle ("Honda CG 125 funcionando").
  2. Inclusion of project motorcycle ("Moto projeto com motor fundido").
  3. Inclusion of donor/parts motorcycle ("Moto para retirada de pecas").
  4. Exclusion of motorcycle apparel/accessory ("Capacete peels novo fechado").
  5. Exclusion of standalone spare part ("Tanque de combustivel Titan 150").
  6. Exclusion of buyer search request ("Procuro moto ate 1000 reais").
  7. Exclusion of commercial rental/ad ("Alugo moto por dia para entrega").
- **Requirement R4 (Production Robustness & CLI Pipeline)**:
  1. Execution of `pipeline.prepare` producing structured `SearchResult` with mutually exclusive lists.
  2. Execution of `cli.collect` with enabled sources yielding formatted stats dictionary.
  3. Execution of `cli.run_once` in `dry_run=True` mode without unhandled exceptions.
  4. Atomic persistence and deduplication of listings in temporary SQLite database.
  5. Correct ordering of confirmed opportunities strictly by price ascending.

### 2.2 Tier 2: Boundary & Corner Cases
Stress-tests boundary parameters and domain edge conditions ($\ge 5$ tests per requirement):
- **Requirement R1 Boundaries**:
  1. Case-insensitive location matching (e.g. `"JAGUARÃO"`, `"rio branco"`).
  2. Accent-insensitive location matching (`"Jaguarao"`, `"Rio Branco"`).
  3. Punctuation and whitespace padding (`" Jaguarão - RS "`, `"Río Branco / Cerro Largo"`).
  4. Multi-city location fields containing both target and non-target tokens.
  5. Marketplace card missing location line handled cleanly.
- **Requirement R2 Boundaries**:
  1. Exact budget upper boundary: $\text{R\$}~1.000,00$ is confirmed (inclusive threshold).
  2. Exact budget upper breach: $\text{R\$}~1.000,01$ is discarded as out-of-budget.
  3. Zero price (`0.0`) classified as bait/unconfirmed rather than a free motorcycle.
  4. Minimum bait threshold boundary (`BAIT_PRICE = 100.0` vs `100.01`).
  5. Unsupported currency handling (`fx_error` set, listing marked excluded with conversion error reason).
- **Requirement R3 Boundaries**:
  1. Motorcycle with documentation risk ("sem documentos", "so pra rodar") accepted if price qualifies.
  2. Post beginning with conversational greeting ("Bom dia grupo! Vendo essa 125 andando, 800").
  3. Phone ad false positive: "Vendo celular Moto G 22" rejected as accessory/non-vehicle.
  4. Automobile false positive: "Vendo Gol 1.0 aceito troca" rejected as car.
  5. Real estate trade-in: "Vendo casa aceito moto na troca" rejected as property.
  6. Power equipment: "Vendo rocadeira CG 520" rejected as machinery.
- **Requirement R4 Boundaries**:
  1. Empty input feed: 0 listings processed cleanly returning empty lists and 0 exit code.
  2. Duplicate listings: identical UID in batch processed idempotently without doubling counts.
  3. Corrupted attributes: raw listings with empty strings or special characters processed without unhandled exception.
  4. CSV export robustness with UTF-8 BOM encoding for accented text.
  5. Advisory file locking releasing immediately upon block exit or error.

### 2.3 Tier 3: Pairwise Cross-Feature Combinations
Tests multi-variable interactions across orthogonal domain dimensions:
- **Dimension A**: Geographic Location (`Jaguarão`, `Río Branco`, `Pelotas`)
- **Dimension B**: Currency & Rate (`BRL`, `UYU`, `Unsupported/EUR`)
- **Dimension C**: Vehicle Condition (`Runner`, `Project`, `Parts/Doadora`, `Accessory`)
- **Dimension D**: Price Point (`Under Budget`, `Exact Budget 1.000`, `Over Budget`, `Decoy/None`)

Selected combination matrix tests verify that:
- Incompatible geography immediately discards regardless of price or bike condition.
- Incompatible currency immediately discards without comparing mismatched nominal values.
- Non-bike condition immediately discards even when within geographic zone and budget.
- Decoy price places valid bikes into `unconfirmed` regardless of geographic origin.

### 2.4 Tier 4: Real-World Application Scenarios
Simulates production scraping runs:
- **Scenario 1: Full Marketplace Batch Run (Jaguarão)**: 10 heterogeneous candidate cards simulating a border scraping cycle, verifying exact categorization into `opportunities`, `unconfirmed`, and `discarded` with clean logging.
- **Scenario 2: Cross-Border Uruguayan Group Feed (Río Branco)**: Multi-post feed written in colloquial Uruguayan Spanish with UYU prices, verifying currency normalization and classification.
- **Scenario 3: Complete CLI Pipeline with Storage & Export**: End-to-end execution of `run_once` inserting into temporary SQLite DB, verifying idempotent repeat run and clean output generation.

---

## 3. Public Entrypoints Under Test

| Entrypoint | Module | Role in E2E Testing |
|---|---|---|
| `prepare(listings, cfg, fx)` | `motoradar.pipeline` | Main business logic: FX, filter, appraisal, categorization |
| `run_once(cfg, only, dry_run)` | `motoradar.cli` | High-level CLI batch runner orchestrating end-to-end flow |
| `collect(cfg, only, apply_filters)` | `motoradar.cli` | Collector entrypoint gathering candidates from sources |
| `matches(listing, f)` | `motoradar.filters` | Geographic and keyword filter contract |
| `card_to_listing(card)` | `motoradar.sources.facebook` | Facebook Marketplace card parser |
| `post_to_listing(group_id, post, ...)` | `motoradar.sources.facebook` | Facebook Group post parser |
| `FX(rates, offline)` | `motoradar.money` | Currency conversion and FX layer |

---

## 4. Verification Protocol

To run the complete test suite:
```powershell
python -m unittest discover -s tests -v
```

To run the E2E test suite exclusively:
```powershell
python -m unittest tests/e2e/test_e2e_facebook.py -v
```

To verify syntax and byte-compilation:
```powershell
python -m compileall -q motoradar tests
```
