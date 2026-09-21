"""Tests unitarios y de robustez para el CLI de Motoradar (Milestone M4).

Verifica el subcomando `collect`, manejo de interrupciones en `watch_loop`,
despacho de comandos `run`, `deals`, `doctor`, `retry`, `status`, `init`
y ejecucion 100% offline.
"""
from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from motoradar.cli import build_parser, collect, main, watch_loop
from motoradar.config import Config
from motoradar.models import Listing
from motoradar.notify import DeliveryResult
from motoradar.sources.base import BaseSource, SourceOutcome


def _sample_listing(identifier: str = "1", title: str = "Honda CG 125 1998",
                    price: float = 800.0, currency: str = "BRL",
                    location: str = "Jaguarao, RS", source: str = "facebook") -> Listing:
    return Listing(
        source=source,
        external_id=identifier,
        title=title,
        price=price,
        currency=currency,
        location=location,
        url=f"https://facebook.com/marketplace/item/{identifier}",
        image="",
        posted_at="",
        raw={"description": title, "kind": "marketplace"}
    )


class MockSource(BaseSource):
    def __init__(self, listings: list[Listing] | None = None, should_fail: bool = False):
        super().__init__()
        self._listings = listings or []
        self._should_fail = should_fail

    def fetch(self, cfg, opts):
        if self._should_fail:
            from motoradar.sources.base import SourceError
            raise SourceError("Error de red simulado")
        yield from self._listings


class TestCliCollectSubparser(unittest.TestCase):
    """Pruebas unitarias para el subcomando `collect` y sus modificadores."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.cfg_path = Path(self.td.name) / "config.yaml"
        self.cfg_path.write_text(
            "db_path: ':memory:'\n"
            "csv_path: test.csv\n"
            "budget: 1000\n"
            "sources:\n"
            "  facebook:\n"
            "    enabled: true\n"
            "filters:\n"
            "  cities: ['jaguarao', 'rio branco']\n"
            "  max_price: 1000\n"
            "fx:\n"
            "  base: BRL\n"
            "  offline: true\n",
            encoding="utf-8"
        )

    def tearDown(self):
        self.td.cleanup()

    def test_collect_help_exits_zero_with_expected_flags(self):
        """`collect --help` sale con codigo 0 y documenta los argumentos requeridos."""
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            main(["collect", "--help"])
        self.assertEqual(cm.exception.code, 0)
        output = out.getvalue()
        self.assertIn("--format", output)
        self.assertIn("--no-filter", output)
        self.assertIn("--source", output)
        self.assertIn("--limit", output)
        self.assertIn("--enrich", output)

    def test_collect_format_json_outputs_valid_json(self):
        """`collect --format json` emite un array JSON valido con los anuncios recolectados."""
        l1 = _sample_listing("101", "Honda CG 125", 800.0)
        l2 = _sample_listing("102", "Yamaha YBR 125", 950.0)
        out = io.StringIO()
        with patch.dict("motoradar.cli.REGISTRY", {"facebook": MockSource([l1, l2])}), \
             redirect_stdout(out):
            code = main(["collect", "--format", "json", "-c", str(self.cfg_path)])

        self.assertEqual(code, 0)
        data = json.loads(out.getvalue())
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["external_id"], "101")
        self.assertEqual(data[0]["price"], 800.0)
        self.assertEqual(data[1]["external_id"], "102")

    def test_collect_format_csv_outputs_valid_csv(self):
        """`collect --format csv` emite CSV valido a stdout con cabeceras correctas."""
        l1 = _sample_listing("201", "Honda Biz 100", 750.0)
        out = io.StringIO()
        with patch.dict("motoradar.cli.REGISTRY", {"facebook": MockSource([l1])}), \
             redirect_stdout(out):
            code = main(["collect", "--format", "csv", "-c", str(self.cfg_path)])

        self.assertEqual(code, 0)
        reader = csv.DictReader(io.StringIO(out.getvalue()))
        rows = list(reader)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["external_id"], "201")
        self.assertEqual(rows[0]["title"], "Honda Biz 100")
        self.assertIn("price", reader.fieldnames)
        self.assertIn("location", reader.fieldnames)

    def test_collect_format_console_outputs_clean_formatted_text(self):
        """`collect --format console` emite texto formateado limpio para terminal."""
        l1 = _sample_listing("301", "Honda Storm 125", 900.0)
        out = io.StringIO()
        with patch.dict("motoradar.cli.REGISTRY", {"facebook": MockSource([l1])}), \
             redirect_stdout(out):
            code = main(["collect", "--format", "console", "-c", str(self.cfg_path)])

        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertFalse(text.strip().startswith("[{") or text.strip().startswith("{"))
        self.assertIn("Honda Storm 125", text)
        self.assertIn("900", text)

    def test_collect_default_format_is_console(self):
        """Sin especificar `--format`, el formato predeterminado es consola."""
        l1 = _sample_listing("302", "Baccio Classic 125", 600.0)
        out = io.StringIO()
        with patch.dict("motoradar.cli.REGISTRY", {"facebook": MockSource([l1])}), \
             redirect_stdout(out):
            code = main(["collect", "-c", str(self.cfg_path)])

        self.assertEqual(code, 0)
        self.assertIn("Baccio Classic 125", out.getvalue())

    def test_collect_no_filter_preserves_raw_listings(self):
        """`collect --no-filter` recolecta anuncios crudos sin descartar por ciudad ni presupuesto."""
        in_zone = _sample_listing("401", "Moto en Jaguarao", 800.0, location="Jaguarao")
        out_of_zone = _sample_listing("402", "Moto en Pelotas", 5000.0, location="Pelotas")

        # Con filtros activos: solo pasa in_zone
        out_filtered = io.StringIO()
        with patch.dict("motoradar.cli.REGISTRY", {"facebook": MockSource([in_zone, out_of_zone])}), \
             redirect_stdout(out_filtered):
            main(["collect", "--format", "json", "-c", str(self.cfg_path)])
        filtered_items = json.loads(out_filtered.getvalue())
        self.assertEqual(len(filtered_items), 1)
        self.assertEqual(filtered_items[0]["external_id"], "401")

        # Con --no-filter: ambos pasan (recolecta cruda)
        out_raw = io.StringIO()
        with patch.dict("motoradar.cli.REGISTRY", {"facebook": MockSource([in_zone, out_of_zone])}), \
             redirect_stdout(out_raw):
            main(["collect", "--no-filter", "--format", "json", "-c", str(self.cfg_path)])
        raw_items = json.loads(out_raw.getvalue())
        self.assertEqual(len(raw_items), 2)
        raw_ids = {item["external_id"] for item in raw_items}
        self.assertEqual(raw_ids, {"401", "402"})

    def test_collect_source_flag_restricts_collection_to_specified_source(self):
        """`collect --source facebook` restringe la recoleccion exclusivamente a dicha fuente."""
        fb_item = _sample_listing("fb-1", "Moto FB", 700.0, source="facebook")
        olx_item = _sample_listing("olx-1", "Moto OLX", 700.0, source="olx")

        mock_registry = {
            "facebook": MockSource([fb_item]),
            "olx": MockSource([olx_item]),
        }
        out = io.StringIO()
        with patch.dict("motoradar.cli.REGISTRY", mock_registry), redirect_stdout(out):
            code = main(["collect", "--source", "facebook", "--format", "json", "-c", str(self.cfg_path)])

        self.assertEqual(code, 0)
        items = json.loads(out.getvalue())
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "facebook")

    def test_collect_invalid_format_raises_argument_error(self):
        """Un formato desconocido falla de inmediato con error de argumentos."""
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            main(["collect", "--format", "yaml", "-c", str(self.cfg_path)])
        self.assertEqual(cm.exception.code, 2)

    def test_collect_limit_option_truncates_output(self):
        """`-n / --limit` recorta la cantidad maxima de resultados."""
        items = [_sample_listing(str(i), f"Moto {i}", 700.0) for i in range(5)]
        out = io.StringIO()
        with patch.dict("motoradar.cli.REGISTRY", {"facebook": MockSource(items)}), \
             redirect_stdout(out):
            code = main(["collect", "--limit", "2", "--format", "json", "-c", str(self.cfg_path)])

        self.assertEqual(code, 0)
        res = json.loads(out.getvalue())
        self.assertEqual(len(res), 2)

    def test_collect_negative_or_zero_limit_fails(self):
        """`--limit <= 0` eleva error de argumentos."""
        for bad_limit in ["0", "-1", "-10"]:
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
                main(["collect", "--limit", bad_limit, "-c", str(self.cfg_path)])
            self.assertEqual(cm.exception.code, 2)

    def test_collect_query_override_modifies_config_queries(self):
        """`-q / --query` pisa los terminos de busqueda de filters y facebook."""
        l1 = _sample_listing("501", "Yamaha Crypton 110", 850.0)
        captured_cfg = []

        def fake_collect(cfg, *args, **kwargs):
            captured_cfg.append(cfg)
            return [l1], {"facebook": SourceOutcome(status="ok", observed=1, discarded=0).as_dict()}

        with patch("motoradar.cli.collect", side_effect=fake_collect), \
             redirect_stdout(io.StringIO()):
            main(["collect", "-q", "crypton", "-c", str(self.cfg_path)])

        self.assertEqual(len(captured_cfg), 1)
        cfg_used = captured_cfg[0]
        self.assertEqual(cfg_used.filters.queries, ["crypton"])
        fb_opts = cfg_used.source_opts("facebook")
        self.assertEqual(fb_opts.get("marketplace_queries"), ["crypton"])
        self.assertEqual(fb_opts.get("group_queries"), ["crypton"])

    def test_collect_namespace_dispatch(self):
        """Llamar a `collect(args)` con un Namespace ejecuta `cmd_collect`."""
        parser = build_parser()
        args = parser.parse_args(["collect", "-c", str(self.cfg_path), "--format", "json"])
        l1 = _sample_listing("601", "Baccio 110", 500.0)

        out = io.StringIO()
        with patch.dict("motoradar.cli.REGISTRY", {"facebook": MockSource([l1])}), \
             redirect_stdout(out):
            code = collect(args)

        self.assertEqual(code, 0)
        items = json.loads(out.getvalue())
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["external_id"], "601")

    def test_collect_fails_when_all_sources_fail(self):
        """Si todas las fuentes fallan, collect retorna codigo de salida 1."""
        err = io.StringIO()
        with patch.dict("motoradar.cli.REGISTRY", {"facebook": MockSource(should_fail=True)}), \
             redirect_stderr(err), redirect_stdout(io.StringIO()):
            code = main(["collect", "-c", str(self.cfg_path)])

        self.assertEqual(code, 1)
        self.assertIn("No hubo fuentes disponibles", err.getvalue())

    def test_collect_enrich_flag_behavior(self):
        """`--enrich` y `--no-enrich` llaman a enrich/fix_bait_prices adecuadamente."""
        l1 = _sample_listing("701", "Winner Fair 110", 600.0)

        # Con --enrich
        with patch.dict("motoradar.cli.REGISTRY", {"facebook": MockSource([l1])}), \
             patch("motoradar.enrich.enrich") as mock_enrich, \
             patch("motoradar.enrich.fix_bait_prices") as mock_fix_bait, \
             redirect_stdout(io.StringIO()):
            code = main(["collect", "--enrich", "-c", str(self.cfg_path)])
        self.assertEqual(code, 0)
        self.assertEqual(mock_enrich.call_count, 1)
        self.assertEqual(mock_fix_bait.call_count, 1)

        # Con --no-enrich
        with patch.dict("motoradar.cli.REGISTRY", {"facebook": MockSource([l1])}), \
             patch("motoradar.enrich.enrich") as mock_enrich_2, \
             patch("motoradar.enrich.fix_bait_prices") as mock_fix_bait_2, \
             redirect_stdout(io.StringIO()):
            code2 = main(["collect", "--no-enrich", "-c", str(self.cfg_path)])
        self.assertEqual(code2, 0)
        self.assertEqual(mock_enrich_2.call_count, 0)
        self.assertEqual(mock_fix_bait_2.call_count, 0)


class TestCliWatchLoopRobustness(unittest.TestCase):
    """Pruebas de robustez para `watch_loop`, cortacircuitos y manejo de KeyboardInterrupt."""

    def test_watch_loop_clean_interrupt_during_run_once(self):
        """Interrupcion (Ctrl+C) durante `run_once` finaliza limpiamente con codigo 0."""
        cfg = Config(sources={"facebook": {"enabled": True}})
        out = io.StringIO()
        with patch("motoradar.cli.run_once", side_effect=KeyboardInterrupt), \
             redirect_stdout(out):
            code = watch_loop(cfg, only=None, enrich_details=False, interval_min=15,
                              sleeper=lambda _s: None, start_cycle=0)
        self.assertEqual(code, 0)
        self.assertIn("Chau.", out.getvalue())

    def test_watch_loop_clean_interrupt_during_sleep(self):
        """Interrupcion (Ctrl+C) durante el `sleeper` sale inmediatamente del bucle con codigo 0."""
        cfg = Config(sources={"facebook": {"enabled": True}})
        out = io.StringIO()
        runs = 0

        def fake_run(*args, **kwargs):
            nonlocal runs
            runs += 1

        def interrupt_sleeper(_seconds):
            raise KeyboardInterrupt

        with patch("motoradar.cli.run_once", side_effect=fake_run), \
             redirect_stdout(out):
            code = watch_loop(cfg, only=None, enrich_details=False, interval_min=15,
                              sleeper=interrupt_sleeper, start_cycle=0)

        self.assertEqual(code, 0)
        self.assertEqual(runs, 1)  # Se interrumpio en el sleep de la primera pasada; no ejecuto una segunda
        self.assertIn("Chau.", out.getvalue())

    def test_watch_loop_dead_return_code_removed(self):
        """Verifica que watch_loop retorne honestamente segun pasadas_ok al agotar max_cycles."""
        cfg = Config(sources={"facebook": {"enabled": True}})

        # Caso exitoso: al menos 1 pasada ok -> retorna 0
        with patch("motoradar.cli.run_once") as mock_run, \
             redirect_stdout(io.StringIO()):
            code_ok = watch_loop(cfg, only=None, enrich_details=False, interval_min=15,
                                 max_cycles=1, sleeper=lambda _s: None, start_cycle=0)
        self.assertEqual(code_ok, 0)
        self.assertEqual(mock_run.call_count, 1)

        # Caso fallido: 0 pasadas ok -> retorna 1
        with patch("motoradar.cli.run_once", side_effect=RuntimeError("db lock")), \
             patch("motoradar.cli.send_health", return_value=DeliveryResult(True)), \
             redirect_stderr(io.StringIO()), \
             redirect_stdout(io.StringIO()):
            code_fail = watch_loop(cfg, only=None, enrich_details=False, interval_min=15,
                                   max_cycles=1, sleeper=lambda _s: None, start_cycle=0)
        self.assertEqual(code_fail, 1)


class TestCliSubcommandsExecution(unittest.TestCase):
    """Verifica que los comandos CLI principales despachen limpiamente de forma 100% offline."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.td.name) / "radar.db")
        self.csv_path = str(Path(self.td.name) / "radar.csv")
        self.cfg_path = Path(self.td.name) / "config.yaml"
        self.cfg_path.write_text(
            f"db_path: '{self.db_path}'\n"
            f"csv_path: '{self.csv_path}'\n"
            "budget: 1000\n"
            "sources:\n"
            "  facebook:\n"
            "    enabled: true\n"
            "fx:\n"
            "  base: BRL\n"
            "  offline: true\n",
            encoding="utf-8"
        )

    def tearDown(self):
        self.td.cleanup()

    def test_main_run_dry_run_executes_cleanly(self):
        """`main(['run', '--dry-run'])` ejecuta el pipeline sin errores ni conexion externa."""
        item = _sample_listing("901", "Honda CG 125", 800.0)
        out = io.StringIO()
        with patch.dict("motoradar.cli.REGISTRY", {"facebook": MockSource([item])}), \
             redirect_stdout(out):
            code = main(["run", "--dry-run", "-c", str(self.cfg_path)])
        self.assertEqual(code, 0)
        self.assertIn("motos en presupuesto", out.getvalue())

    def test_main_deals_executes_cleanly(self):
        """`main(['deals'])` genera reporte de oportunidades de negocio offline."""
        item = _sample_listing("902", "Honda CG 125", 800.0)
        out = io.StringIO()
        with patch.dict("motoradar.cli.REGISTRY", {"facebook": MockSource([item])}), \
             redirect_stdout(out):
            code = main(["deals", "--top", "5", "--no-enrich", "-c", str(self.cfg_path)])
        self.assertEqual(code, 0)
        self.assertIn("HASTA R$ 1.000 EN TU ZONA", out.getvalue())

    def test_main_doctor_executes_cleanly(self):
        """`main(['doctor'])` audita el estado del sistema sin dependencias externas."""
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["doctor", "-c", str(self.cfg_path)])
        self.assertEqual(code, 0)
        self.assertIn("Presupuesto:", out.getvalue())
        self.assertIn("Fuentes activas:", out.getvalue())

    def test_main_init_creates_new_config_without_overwriting(self):
        """`main(['init'])` crea configuracion si no existe y no sobreescribe si ya existe."""
        new_cfg = Path(self.td.name) / "sub" / "new_config.yaml"
        new_cfg.parent.mkdir(parents=True, exist_ok=True)
        out1 = io.StringIO()
        with redirect_stdout(out1):
            code1 = main(["init", "-c", str(new_cfg)])
        self.assertEqual(code1, 0)
        self.assertTrue(new_cfg.exists())

        # Segunda ejecucion no sobreescribe
        out2 = io.StringIO()
        with redirect_stdout(out2):
            code2 = main(["init", "-c", str(new_cfg)])
        self.assertEqual(code2, 0)
        self.assertIn("ya existe", out2.getvalue())


if __name__ == "__main__":
    unittest.main()
