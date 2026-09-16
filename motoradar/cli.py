from __future__ import annotations

import argparse
import shutil
import sys
import time
import math
import sqlite3
import json
from contextlib import nullcontext as _no_lock
from pathlib import Path

from dataclasses import replace

from .config import Config
from .filters import matches
from .models import Listing, now_iso
from .money import FX
from .notify import to_console, to_csv, flush_pending
from .pipeline import normalize, prepare
from .locking import exclusive
from .sources import REGISTRY
from .sources.base import SourceError


def _to_base(listing: Listing, fx: FX, base: str) -> None:
    """Pasa todo a una sola moneda antes de filtrar o comparar. Guarda el
    precio original para poder mostrarlo: el vendedor de Rio Branco pide en
    pesos y conviene ver su numero, no solo la conversion."""
    normalize(listing, fx, base)


def collect(cfg: Config, only: list[str] | None = None,
            apply_filters: bool = True, fx: FX | None = None) -> tuple[list[Listing], dict]:
    """apply_filters=False devuelve TODO. Lo necesita `deals`: los precios de
    referencia salen de la muestra completa de la region, no de lo que pasa
    tu filtro de ciudad y presupuesto."""
    found: list[Listing] = []
    stats: dict[str, str] = {}
    fx = fx or FX(cfg.fx.get("rates"), offline=bool(cfg.fx.get("offline")))
    base = cfg.fx.get("base", "BRL")
    for name, source in REGISTRY.items():
        if only and name not in only:
            continue
        if not only and not cfg.source_enabled(name):
            continue
        print(f"-> {name} ...", flush=True)
        kept = dropped = 0
        try:
            for listing in source.fetch(cfg, cfg.source_opts(name)):
                _to_base(listing, fx, base)
                ok, _reason = matches(listing, cfg.filters) if apply_filters else (True, "")
                if ok and not listing.raw.get("fx_error"):
                    found.append(listing)
                    kept += 1
                else:
                    dropped += 1
            stats[name] = f"{kept} pasan / {dropped} descartados"
            if getattr(source, "failed_pages", 0):
                stats[name] += f" / PARCIAL: {source.failed_pages} paginas fallidas"
        except SourceError as exc:
            stats[name] = f"ERROR: {exc}"
        except Exception as exc:  # una fuente rota no tumba la corrida
            stats[name] = f"ERROR inesperado: {type(exc).__name__}: {exc}"
        print(f"   {stats[name]}")
    return found, stats


def run_once(cfg: Config, only: list[str] | None = None,
             dry_run: bool = False, enrich_details: bool = True) -> int:
    from .store import Store
    cfg.validate()
    with exclusive(cfg.db_path) if not dry_run else _no_lock():
        started = now_iso()
        fx = FX(cfg.fx.get("rates"), offline=bool(cfg.fx.get("offline")))
        # The alert budget is authoritative; convert remote limits to BRL.
        remote_budget = fx.convert(cfg.budget, cfg.fx.get("base", "BRL"), "BRL")
        search = replace(cfg, filters=replace(cfg.filters, max_price=remote_budget,
                                              min_price=0, allow_no_price=True))
        listings, stats = collect(search, only, apply_filters=False, fx=fx)
        result = prepare(listings, cfg, fx, enrich_details)
        print(f"\n{len(result.opportunities)} motos en presupuesto; "
              f"{len(result.unconfirmed)} con precio por confirmar")
        if fx.source == "fallback":
            print("  ! conversion con cotizacion de respaldo")
        if dry_run:
            to_console(result.opportunities)
            print("PRECIO POR CONFIRMAR:")
            to_console(result.unconfirmed)
            fresh = result.opportunities
        else:
            store = Store(cfg.db_path)
            try:
                destination = str(cfg.telegram.get("chat_id", ""))
                eligible = {l.uid for l in result.opportunities}
                if cfg.monitoring.get("alert_unconfirmed", True):
                    eligible.update(l.uid for l in result.unconfirmed)
                fresh = []
                for listing in result.observed:
                    is_eligible = listing.uid in eligible
                    event = store.upsert(listing, destination, is_eligible)
                    if is_eligible and event:
                        fresh.append(listing)
                store.record_run(started, stats, len(result.opportunities), len(result.unconfirmed))
                to_console(fresh)
                to_csv(fresh, cfg.csv_path)
                sent = flush_pending(store, cfg.telegram.get("token", ""), destination)
                print(f"  -> {sent} alertas enviadas; historico: {store.count()}")
            finally:
                store.close()
        if not stats or all(str(s).startswith("ERROR") for s in stats.values()):
            raise SourceError("No hubo fuentes disponibles; revisar configuracion/estado")
        return len(fresh)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="motoradar",
                                     description="Radar de motos baratas en Jaguarao")
    parser.add_argument("-c", "--config", default="config.yaml")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="crea config.yaml desde el ejemplo")
    sub.add_parser("login", help="abre el navegador para iniciar sesion en Facebook")
    sub.add_parser("status", help="chequea si la sesion de Facebook sigue viva")
    sub.add_parser("doctor", help="estado local del radar y sus entregas, sin consultar proveedores")
    sub.add_parser("retry", help="reintenta entregas pendientes, sin volver a buscar")

    p_run = sub.add_parser("run", help="una pasada por todas las fuentes activas")
    p_run.add_argument("--source", action="append", dest="only",
                       help="forzar una fuente concreta (repetible)")
    p_run.add_argument("--budget", type=float)
    p_run.add_argument("--dry-run", action="store_true", help="busca sin guardar ni enviar")
    p_run.add_argument("--no-enrich", action="store_true")

    p_deals = sub.add_parser("deals", help="oportunidades de compra-arreglo-reventa")
    p_deals.add_argument("--top", type=int, default=15)
    p_deals.add_argument("--budget", type=float, help="pisa el budget de la config")
    p_deals.add_argument("--source", action="append", dest="only")
    p_deals.add_argument("--no-enrich", action="store_true",
                         help="no leer las descripciones (mas rapido)")

    p_watch = sub.add_parser("watch", help="corre en bucle cada N minutos")
    p_watch.add_argument("--interval", type=int, default=30, help="minutos (def: 30)")
    p_watch.add_argument("--source", action="append", dest="only")
    p_watch.add_argument("--budget", type=float)
    p_watch.add_argument("--no-enrich", action="store_true")

    args = parser.parse_args(argv)
    if getattr(args, "only", None) and set(args.only) - REGISTRY.keys():
        parser.error("--source debe ser: " + ", ".join(REGISTRY))
    if hasattr(args, "interval") and args.interval <= 0:
        parser.error("--interval debe ser > 0")
    if hasattr(args, "top") and args.top <= 0:
        parser.error("--top debe ser > 0")
    if getattr(args, "budget", None) is not None and (not math.isfinite(args.budget) or args.budget < 0):
        parser.error("--budget debe ser finito y >= 0")

    if args.cmd == "init":
        dest = Path(args.config)
        if dest.exists():
            print(f"{dest} ya existe, no lo toco.")
            return 0
        shutil.copy(Path(__file__).resolve().parent.parent / "config.example.yaml", dest)
        print(f"Creado {dest}. Editalo y despues: python -m motoradar run")
        return 0

    if args.cmd == "login":
        from .sources.facebook import login
        login()
        return 0

    if args.cmd == "status":
        from .sources.facebook import session_ready
        ok = session_ready()
        print("Facebook:", "sesion activa" if ok
              else "sin sesion -> corré: python -m motoradar login")
        return 0 if ok else 1

    cfg = Config.load(args.config)
    if getattr(args, "budget", None) is not None:
        cfg.budget = args.budget
    enrichment = cfg.monitoring.get("enrich_details", True) and not getattr(args, "no_enrich", False)

    if args.cmd == "doctor":
        print(f"Presupuesto: {Listing.SYMBOL[cfg.fx.get('base', 'BRL')]} {cfg.budget:,.0f}")
        print("Fuentes activas:", ", ".join(n for n in REGISTRY if cfg.source_enabled(n)) or "ninguna")
        print("Telegram:", "configurado" if cfg.telegram.get("token") and cfg.telegram.get("chat_id") else "sin configurar")
        print("Papeles y margen: no filtran; estado mecanico: admite proyectos y doadoras")
        if Path(cfg.db_path).exists():
            conn = sqlite3.connect(Path(cfg.db_path).resolve().as_uri() + "?mode=ro", uri=True)
            try:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "deliveries" in tables:
                    print("Entregas pendientes:", conn.execute("SELECT count(*) FROM deliveries WHERE sent_at IS NULL").fetchone()[0])
                if "runs" in tables:
                    last = conn.execute("SELECT finished_at,stats FROM runs ORDER BY id DESC LIMIT 1").fetchone()
                    if last:
                        print("Ultima corrida:", last[0])
                        for name, stat in json.loads(last[1]).items():
                            print(f"  {name}: {stat}")
            finally:
                conn.close()
        else:
            print("Sin historial: todavia no hubo corridas guardadas")
        return 0

    if args.cmd == "retry":
        from .store import Store
        with exclusive(cfg.db_path):
            store = Store(cfg.db_path)
            try:
                print("Alertas enviadas:", flush_pending(store, cfg.telegram.get("token", ""), str(cfg.telegram.get("chat_id", ""))))
            finally:
                store.close()
        return 0

    if args.cmd == "run":
        try:
            run_once(cfg, args.only, dry_run=args.dry_run, enrich_details=enrichment)
        except SourceError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0

    if args.cmd == "deals":
        run_deals(cfg, args.only, args.top, enrich_details=enrichment)
        return 0

    if args.cmd == "watch":
        print(f"Vigilando cada {args.interval} min. Ctrl+C para cortar.")
        while True:
            try:
                run_once(cfg, args.only, enrich_details=enrichment)
                time.sleep(args.interval * 60)
            except KeyboardInterrupt:
                print("\nChau.")
                return 0
            except Exception as exc:
                print(f"Pasada fallida: {exc}", file=sys.stderr)
                try:
                    time.sleep(args.interval * 60)
                except KeyboardInterrupt:
                    return 0

    return 0


def run_deals(cfg: Config, only: list[str] | None = None, top: int = 15,
              enrich_details: bool = True) -> None:
    """La vista del que compra para arreglar y revender."""
    from .market import Economics, assembly_clusters, build_references, evaluate

    sym = Listing.SYMBOL[cfg.fx.get("base", "BRL")]
    econ = Economics.from_config(cfg.economics)

    # La muestra tiene que ser ANCHA aunque tu presupuesto sea chico: el precio
    # de referencia sale de las motos andando, que valen muy por encima de lo
    # que vas a pagar. Con el tope del presupuesto no habria con que comparar.
    wide = replace(cfg, filters=replace(cfg.filters, max_price=econ.survey_max_price,
                                        min_price=0.0))
    everything, _ = collect(wide, only, apply_filters=False)
    refs = build_references(everything, floor=econ.min_real_price)
    print(f"\nMuestra: {len(everything)} avisos | referencias de precio "
          f"para {len(refs)} modelos")

    fx = FX(cfg.fx.get("rates"), offline=bool(cfg.fx.get("offline")))
    result = prepare(everything, cfg, fx, enrich_details)
    candidatas = result.opportunities
    if result.unconfirmed:
        print("PRECIO POR CONFIRMAR (no se asume dentro del presupuesto):")
        to_console(result.unconfirmed)

    # 3) recien ahora tasar, con el texto completo a la vista
    deals, pedidos = [], []
    for listing in candidatas:
        deal = evaluate(listing, refs, econ)
        if deal.appraisal.wanted or deal.appraisal.shop_ad:
            pedidos.append(deal)      # busca comprar, o es publicidad de local
            continue
        deals.append(deal)

    if pedidos:
        buscan = sum(1 for d in pedidos if d.appraisal.wanted)
        print(f"({len(pedidos)} descartadas: {buscan} buscan comprar, "
              f"{len(pedidos) - buscan} publicidad de comercio)")

    # Una sola lista, ordenada por precio. El criterio es el presupuesto y
    # nada mas: todo lo que entre se muestra. El margen va como dato extra
    # cuando se puede calcular, nunca como filtro — hubo una version que
    # escondia compras validas por "no llegar al margen" y eso estaba mal.
    def orden(d):
        precio = d.listing.price
        return (precio is None, precio if precio is not None else 0)

    # Solo motos enteras. Al sacar el filtro de margen se colaron repuestos
    # (un farol, una biela, un capacete) y hasta maquinaria de panaderia de un
    # grupo: el margen los tapaba de casualidad, no por diseno.
    lista = sorted([d for d in deals if d.appraisal.kind == "moto"], key=orden)
    otros = [d for d in deals if d.appraisal.kind == "unknown"]

    print(f"\nHASTA {sym} {cfg.budget:,.0f} EN TU ZONA: {len(lista)}\n"
          .replace(",", "."))
    if not lista:
        print("  Nada en esta pasada. Proba subir `budget` o ampliar `cities`.\n")

    for d in lista[:top]:
        l, a = d.listing, d.appraisal
        etiquetas = [x for x in [
            a.condition,
            a.model or None,
            str(a.year) if a.year else None,
            "SIN PAPELES" if a.doc_risk else None,
            "OJO: ROBADA" if a.stolen_flag else None,
        ] if x]
        print(f"  {l.pretty_price():>14}  {l.title[:58]}")
        print(f"                  {' · '.join(etiquetas)}")
        if d.margin is not None:
            ref = f"{sym} {d.reference.median:,.0f}".replace(",", ".")
            print(f"                  margen estimado ~{sym} {d.margin:,.0f}"
                  f" (ref {ref}, arreglo {sym} {d.repair:,.0f})".replace(",", "."))
        desc = l.raw.get("description", "")
        if desc:
            print(f"                  \"{desc[:130]}\"")
        print(f"                  {l.location[:26]}  {l.url}\n")

    if otros:
        print(f"NO PUDE CLASIFICARLAS ({len(otros)}) — miralas a ojo:")
        for d in otros[:8]:
            l = d.listing
            print(f"  {l.pretty_price():>12}  {l.title[:56]}")
            print(f"                {l.location[:24]}  {l.url}")
        print()

    if len(lista) > top:
        print(f"  ... y {len(lista) - top} mas. Subi --top para verlas.\n")

    clusters = assembly_clusters(deals)
    if clusters:
        print("PARA ARMAR (varios doadores del mismo modelo):")
        for model, ds in clusters.items():
            total = sum(x.listing.price or 0 for x in ds)
            print(f"  {model}: {len(ds)} unidades, total {sym} {total:,.0f}".replace(",", "."))
            for x in ds:
                print(f"     {x.listing.pretty_price():>10}  {x.listing.title[:54]}")
        print()
