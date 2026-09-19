from __future__ import annotations

import argparse
import shutil
import sys
import time
import math
import sqlite3
import json
import csv
import contextlib
from contextlib import nullcontext as _no_lock
from pathlib import Path

from dataclasses import replace

from .config import Config
from .filters import matches
from .models import Listing, now_iso
from .money import FX
from .notify import to_console, to_csv, flush_pending, send_health
from .pipeline import normalize, prepare
from .locking import exclusive
from .sources import REGISTRY
from .sources.base import (SessionExpired, SourceError, SourceOutcome,
                           format_outcome, outcome_status)


def _to_base(listing: Listing, fx: FX, base: str) -> None:
    """Pasa todo a una sola moneda antes de filtrar o comparar. Guarda el
    precio original para poder mostrarlo: el vendedor de Rio Branco pide en
    pesos y conviene ver su numero, no solo la conversion."""
    normalize(listing, fx, base)


def collect(cfg_or_args: Config | argparse.Namespace,
            only: list[str] | None = None,
            apply_filters: bool = True, fx: FX | None = None,
            skip: set[str] | None = None) -> tuple[list[Listing], dict] | int:
    """apply_filters=False devuelve TODO. Lo necesita `deals`: los precios de
    referencia salen de la muestra completa de la region, no de lo que pasa
    tu filtro de ciudad y presupuesto.

    `skip` deja fuera una fuente pausada por el vigilante (sesion caida):
    seguir golpeandola cada pasada no la arregla y arriesga la cuenta."""
    if isinstance(cfg_or_args, argparse.Namespace):
        return cmd_collect(cfg_or_args)
    cfg = cfg_or_args
    found: list[Listing] = []
    stats: dict[str, dict] = {}
    fx = fx or FX(cfg.fx.get("rates"), offline=bool(cfg.fx.get("offline")))
    base = cfg.fx.get("base", "BRL")
    for name, source in REGISTRY.items():
        if only and name not in only:
            continue
        if not only and not cfg.source_enabled(name):
            continue
        if skip and name in skip:
            print(f"-> {name}: pausada en esta pasada", flush=True)
            continue
        print(f"-> {name} ...", flush=True)
        kept = dropped = 0
        # Una superficie declarada que observa 0 tiene que APARECER con 0: una
        # clave ausente no se ve, y ahi se esconde media fuente ciega.
        origenes: dict[str, int] = {origen: 0 for origen in source.expected_origins}
        errors: list[str] = []
        status = "ok"
        session_expired = False
        try:
            for listing in source.fetch(cfg, cfg.source_opts(name)):
                _to_base(listing, fx, base)
                ok, _reason = matches(listing, cfg.filters) if apply_filters else (True, "")
                # Sin filtros se conserva toda observacion, incluso una moneda
                # incompatible: pipeline/store deben registrar la exclusion y
                # cancelar cualquier entrega anterior de esa identidad.
                if not apply_filters or (ok and not listing.raw.get("fx_error")):
                    found.append(listing)
                    kept += 1
                    etiqueta = surface_label(listing)
                    origenes[etiqueta] = origenes.get(etiqueta, 0) + 1
                else:
                    dropped += 1
            if source.failed_pages:
                status = "partial"
        except SessionExpired as exc:
            errors.append(str(exc))
            session_expired = True
            status = "partial" if kept else "error"
        except SourceError as exc:
            errors.append(str(exc))
            status = "partial" if kept else "error"
        except Exception as exc:  # una fuente rota no tumba la corrida
            errors.append(f"inesperado {type(exc).__name__}: {exc}")
            status = "partial" if kept else "error"
        # Una fuente que no reconoce el HTML no esta "ok con cero resultados":
        # esta ciega. Sin esta distincion el radar se apaga y sigue informando
        # pasadas normales y vacias.
        dom_failures = source.dom_failures
        for message in source.errors:
            if message not in errors:
                errors.append(message)
        if dom_failures:
            status = "partial" if kept else "error"
        stats[name] = SourceOutcome(
            status=status,
            observed=kept,
            discarded=dropped,
            failed_pages=source.failed_pages,
            dom_failures=dom_failures,
            session_expired=session_expired,
            observed_by_origin=origenes,
            errors=errors,
        ).as_dict()
        print(f"   {format_outcome(stats[name])}")
    return found, stats


ZERO_RUNS_ALERT = 4      # pasadas seguidas sin observar nada antes de avisar


def surface_label(listing: Listing) -> str:
    """Clave de SUPERFICIE para medir salud: `facebook/grupo-<id>`.

    Distinta de `observation_origins`, que agrupa por via de lectura
    (feed/buscador) para la consola. Aqui interesa el grupo concreto, porque un
    grupo es lo que se queda ciego por su cuenta.
    """
    raw = listing.raw
    if raw.get("kind") == "group":
        return f"{listing.source}/grupo-{raw.get('group', '?')}"
    if raw.get("kind"):
        return f"{listing.source}/{raw['kind']}"
    return listing.source


def mask_destination(destination: str) -> str:
    """`doctor` es el comando que uno pega en un chat cuando algo no funciona.

    Un chat_id no da acceso por si solo (Telegram exige que la persona le
    escriba al bot primero), pero es la identidad de otra persona y no hace
    falta entera para distinguir un destinatario de otro.
    """
    texto = str(destination or "")
    return f"...{texto[-4:]}" if len(texto) > 4 else "..."


def cycle_config(cfg: Config, cycle: int) -> Config:
    """Marketplace no necesita la misma cadencia que los grupos.

    En los grupos las motos baratas se venden en menos de una hora, asi que
    conviene mirarlos seguido; Marketplace es mas lento y cada consulta suya
    es una carga de pagina mas contra la cuenta. `marketplace_every_cycles`
    lo deja correr cada N pasadas sin frenar los grupos.
    """
    fb = cfg.source_opts("facebook")
    try:
        every = int(fb.get("marketplace_every_cycles", 1) or 1)
    except (TypeError, ValueError):
        every = 1
    if every <= 1 or not fb.get("marketplace", True) or cycle % every == 0:
        return cfg
    sources = {name: (dict(opts) if isinstance(opts, dict) else opts)
               for name, opts in cfg.sources.items()}
    sources["facebook"] = {**sources.get("facebook", {}), "marketplace": False}
    print(f"  (Marketplace se salta esta pasada; corre cada {every})")
    return replace(cfg, sources=sources)


def check_health(store, cfg: Config, stats: dict) -> list[str]:
    """Avisos de que el radar dejo de ver, no de que no haya ofertas.

    Devuelve los textos avisados. Cada motivo tiene su propio cooldown en la
    base para no convertir el bot en ruido y que termines silenciandolo.
    """
    try:
        limit = int(cfg.monitoring.get("health_zero_runs", ZERO_RUNS_ALERT))
    except (TypeError, ValueError):
        limit = ZERO_RUNS_ALERT
    token = str(cfg.telegram.get("token", ""))
    destinos = cfg.telegram_destinations()
    avisos: list[str] = []
    for name, stat in stats.items():
        if not isinstance(stat, dict):
            continue
        motivos: list[tuple[str, str]] = []
        if stat.get("session_expired"):
            motivos.append((f"sesion:{name}",
                            f"{name}: la sesion expiro o pide verificacion. "
                            f"Corre: python -m motoradar login"))
        if stat.get("dom_failures"):
            motivos.append((f"dom:{name}",
                            f"{name}: {stat['dom_failures']} paginas sin DOM "
                            f"reconocible. El adaptador quedo ciego: cero "
                            f"resultados NO significa que no haya ofertas."))
        if not stat.get("observed"):
            streak = store.source_zero_streak(name, limit=max(limit * 3, 20))
            if streak >= limit:
                motivos.append((f"cero:{name}",
                                f"{name}: {streak} pasadas seguidas sin "
                                f"observar un solo aviso. Revisar sesion, "
                                f"filtros y selectores."))
        # Y por superficie: una mitad ciega no baja el total de la fuente.
        origenes = stat.get("observed_by_origin")
        for superficie, vistos in (origenes or {}).items() if isinstance(origenes, dict) else ():
            if vistos:
                continue
            streak = store.source_zero_streak(superficie, limit=max(limit * 3, 20))
            if streak >= limit:
                motivos.append((f"cero:{superficie}",
                                f"{superficie}: {streak} pasadas seguidas sin "
                                f"un solo aviso, mientras el resto de la fuente "
                                f"si observa. Revisar esa superficie."))
        # Partial success is not recovery from an active DOM/session fault.
        # Clearing its key here would resend the same notice on every pass.
        active_keys = {key for key, _ in motivos}
        claves = [f"{prefix}:{name}" for prefix in ("cero", "dom", "sesion")]
        claves += [f"cero:{superficie}" for superficie in (origenes or {})
                   if isinstance(origenes, dict)]
        for key in claves:
            if key not in active_keys:
                store.clear_health_notice(key)
        for key, texto in motivos:
            print(f"  !! SALUD {texto}")
            if not store.health_notice_due(key):
                continue
            avisos.append(texto)
            entregado = not (token and destinos)   # sin Telegram basta la consola
            for destino in (destinos if token else []):
                result = send_health(token, destino, texto)
                if result.ok:
                    entregado = True
                else:
                    print(f"  ! aviso de salud no entregado a "
                          f"{mask_destination(destino)}: {result.error}")
            # El cooldown se marca recien cuando alguien lo recibio: un corte de
            # red de 20 s no puede comprar 6 horas de silencio sobre un radar
            # ciego, que es el peor escenario del producto.
            if entregado:
                store.mark_health_notice(key)
    return avisos


def observation_origins(listings: list[Listing]) -> dict[str, int]:
    """Observaciones por origen real, no por fuente.

    "facebook: 151 observados" no dice si el feed cronologico de los grupos
    trajo algo o si todo vino de Marketplace, que es justo lo que hay que
    saber para decidir si los grupos valen el tiempo y el riesgo de cuenta.
    """
    conteo: dict[str, int] = {}
    for listing in listings:
        kind = listing.raw.get("kind")
        if kind == "group":
            etiqueta = f"{listing.source}/grupo-{listing.raw.get('group_origin', '?')}"
        elif kind:
            etiqueta = f"{listing.source}/{kind}"
        else:
            etiqueta = listing.source
        conteo[etiqueta] = conteo.get(etiqueta, 0) + 1
    return conteo


def run_explain(cfg: Config, limit: int = 40, solo_descartados: bool = False) -> int:
    """Que vio el radar y por que cada aviso llego o se cayo.

    Sin esto, pasar de 149 observaciones a 13 alertas era una caja negra: los
    dos fallos peores de la auditoria (el clasificador tirando motos de grupo y
    la tarjeta sin precio perdiendo el titulo) se habrian visto en 30 segundos
    mirando esta tabla. Es local: lee la base, no consulta a nadie.
    """
    from .appraise import appraise
    from .store import Store
    ruta = Path(cfg.db_path)
    if not ruta.exists():
        print("Sin historial: todavia no hubo corridas guardadas")
        return 1
    store = Store(cfg.db_path)
    try:
        filas = store.conn.execute(
            "SELECT raw, title, price, url, location, source, posted_at"
            " FROM listings ORDER BY last_seen DESC, first_seen DESC LIMIT ?",
            (max(1, limit) * 4,)).fetchall()
    finally:
        store.close()

    print(f"{'origen':22} {'precio':>10}  {'clase':10} {'veredicto':14} titulo")
    mostradas = 0
    por_veredicto: dict[str, int] = {}
    for fila in filas:
        try:
            raw = json.loads(fila["raw"] or "{}")
        except json.JSONDecodeError:
            raw = {}
        listing = Listing(source=fila["source"], external_id="", title=fila["title"] or "",
                          url=fila["url"] or "", price=fila["price"],
                          currency=raw.get("currency", "BRL"),
                          location=fila["location"] or "", posted_at=fila["posted_at"] or "",
                          raw=raw)
        a = appraise(listing)
        estado = raw.get("alert_status", "excluded")
        veredicto = {"confirmed": "alerta", "unconfirmed": "por confirmar"}.get(
            estado, "descartado")
        por_veredicto[veredicto] = por_veredicto.get(veredicto, 0) + 1
        if solo_descartados and veredicto != "descartado":
            continue
        if mostradas >= limit:
            continue
        mostradas += 1
        causa = ""
        if veredicto == "descartado":
            causa = raw.get("excluded_reason") or a.kind
            if a.wanted:
                causa = "pedido de compra"
            elif a.shop_ad:
                causa = "publicidad/rifa/alquiler"
            elif raw.get("card_parse"):
                causa = raw["card_parse"]
        print(f"{surface_label(listing)[:22]:22} {listing.pretty_price():>10}  "
              f"{a.kind:10} {veredicto:14} {(listing.title or '')[:46]}"
              + (f"  [{causa}]" if causa else ""))
    print()
    print(", ".join(f"{k}: {v}" for k, v in sorted(por_veredicto.items())))
    return 0


def run_once(cfg: Config, only: list[str] | None = None,
             dry_run: bool = False, enrich_details: bool = True,
             cycle: int = 0, skip: set[str] | None = None,
             stats_out: dict | None = None) -> int:
    from .store import Store
    cfg.validate()
    with exclusive(cfg.db_path) if not dry_run else _no_lock():
        started = now_iso()
        fx = FX(cfg.fx.get("rates"), offline=bool(cfg.fx.get("offline")))
        # The alert budget is authoritative; convert remote limits to BRL.
        remote_budget = fx.convert(cfg.budget, cfg.fx.get("base", "BRL"), "BRL")
        search = replace(cycle_config(cfg, cycle),
                         filters=replace(cfg.filters, max_price=remote_budget,
                                         min_price=0, allow_no_price=True))
        listings, stats = collect(search, only, apply_filters=False, fx=fx, skip=skip)
        if stats_out is not None:
            stats_out.clear()
            stats_out.update(stats)
        result = prepare(listings, cfg, fx, enrich_details)
        print(f"\n{len(result.opportunities)} motos en presupuesto; "
              f"{len(result.unconfirmed)} con precio por confirmar")
        # De 151 observaciones a 13 seleccionadas sin explicacion no se puede
        # afinar nada: hay que ver de donde vino cada cosa y por que se cayo.
        origenes = observation_origins(result.observed)
        if origenes:
            print("  origen: " + ", ".join(
                f"{nombre} {total}" for nombre, total in
                sorted(origenes.items(), key=lambda kv: (-kv[1], kv[0]))))
        if result.discarded:
            print("  descartados: " + ", ".join(
                f"{motivo} x{total}" for motivo, total in
                sorted(result.discarded.items(), key=lambda kv: (-kv[1], kv[0]))))
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
                destinos = cfg.telegram_destinations()
                # Sin Telegram configurado se persiste igual, con destinatario
                # vacio: el historico no depende de tener bot.
                objetivos = destinos or [""]
                eligible = {l.uid for l in result.opportunities}
                if cfg.monitoring.get("alert_unconfirmed", True):
                    eligible.update(l.uid for l in result.unconfirmed)
                fresh = []
                for listing in result.observed:
                    is_eligible = listing.uid in eligible
                    novedad = store.upsert_many(listing, objetivos, is_eligible)
                    if is_eligible and novedad:
                        fresh.append(listing)
                store.record_run(started, stats, len(result.opportunities), len(result.unconfirmed))
                check_health(store, cfg, stats)
                # Deliver first: a CSV open in Excel or a console encoding error
                # must not hold back Telegram alerts.
                token = str(cfg.telegram.get("token", ""))
                sent = sum(flush_pending(store, token, destino) for destino in destinos)
                to_console(fresh)
                try:
                    to_csv(fresh, cfg.csv_path)
                except OSError as exc:
                    print(f"  ! no se pudo escribir {cfg.csv_path}: {exc}")
                print(f"  -> {sent} alertas enviadas; historico: {store.count()}")
                try:
                    dias = int(cfg.monitoring.get("retention_days", 90))
                except (TypeError, ValueError):
                    dias = 90
                borrado = store.prune(dias)
                if any(borrado.values()):
                    print(f"  retencion {dias} dias: -{borrado['observations']} "
                          f"observaciones, -{borrado['listings']} anuncios, "
                          f"-{borrado['runs']} corridas")
            finally:
                store.close()
        if not stats or all(outcome_status(s) == "error" for s in stats.values()):
            raise SourceError("No hubo fuentes disponibles; revisar configuracion/estado")
        return len(fresh)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="motoradar",
                                     description="Radar de motos baratas en Jaguarao")
    parser.add_argument("-c", "--config", default="config.yaml")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="crea config.yaml desde el ejemplo")
    p_login = sub.add_parser("login", help="abre el navegador para iniciar sesion en Facebook")
    p_status = sub.add_parser("status", help="chequea si la sesion de Facebook sigue viva")
    p_doctor = sub.add_parser("doctor", help="estado local del radar y sus entregas, sin consultar proveedores")
    p_retry = sub.add_parser("retry", help="reintenta entregas pendientes, sin volver a buscar")

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

    p_explain = sub.add_parser(
        "explain", help="que vio el radar y por que cada aviso llego o se cayo")
    p_explain.add_argument("--limit", type=int, default=40)
    p_explain.add_argument("--solo-descartados", action="store_true",
                           dest="solo_descartados")

    p_watch = sub.add_parser("watch", help="corre en bucle cada N minutos")
    p_watch.add_argument("--interval", type=int, default=15, help="minutos (def: 15)")
    p_watch.add_argument("--source", action="append", dest="only")
    p_watch.add_argument("--budget", type=float)
    p_watch.add_argument("--no-enrich", action="store_true")

    for p in [p_init, p_login, p_status, p_doctor, p_retry, p_run, p_deals, p_explain, p_watch]:
        p.add_argument("-c", "--config", default=argparse.SUPPRESS, help="ruta al archivo config.yaml")

    p_collect = sub.add_parser("collect", help="recolecta anuncios sin guardar en la base ni enviar alertas")
    p_collect.add_argument("-c", "--config", default=argparse.SUPPRESS, help="ruta al archivo config.yaml")
    p_collect.add_argument("--source", action="append", dest="only",
                           help="forzar una fuente concreta (repetible)")
    p_collect.add_argument("-q", "--query", type=str, default=None,
                           help="termino de busqueda (anula filters.queries)")
    p_collect.add_argument("--format", choices=["console", "json", "csv"], default="console",
                           help="formato de salida (def: console)")
    p_collect.add_argument("--no-filter", action="store_true", dest="no_filter",
                           help="desactivar filtros para recoleccion cruda")
    p_collect.add_argument("-n", "--limit", type=int, default=None,
                           help="limite maximo de resultados a mostrar")
    p_collect.add_argument("--enrich", action=argparse.BooleanOptionalAction, default=None,
                           help="activar o desactivar lectura de detalles")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "only", None) and set(args.only) - REGISTRY.keys():
        parser.error("--source debe ser: " + ", ".join(REGISTRY))
    if hasattr(args, "interval") and args.interval <= 0:
        parser.error("--interval debe ser > 0")
    if hasattr(args, "top") and args.top <= 0:
        parser.error("--top debe ser > 0")
    if getattr(args, "limit", None) is not None and args.limit <= 0:
        parser.error("--limit debe ser > 0")
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
        # doctor devolvia 0 siempre, asi que no servia para una tarea programada.
        problemas: list[str] = []
        print(f"Presupuesto: {Listing.SYMBOL[cfg.fx.get('base', 'BRL')]} {cfg.budget:,.0f}")
        print("Fuentes activas:", ", ".join(n for n in REGISTRY if cfg.source_enabled(n)) or "ninguna")
        destinos = cfg.telegram_destinations()
        print("Telegram:", f"configurado, {len(destinos)} destinatario(s): "
              + ", ".join(mask_destination(d) for d in destinos)
              if cfg.telegram.get("token") and destinos else "sin configurar")
        print("Papeles y margen: no filtran; estado mecanico: admite proyectos y doadoras")
        if Path(cfg.db_path).exists():
            conn = sqlite3.connect(Path(cfg.db_path).resolve().as_uri() + "?mode=ro", uri=True)
            try:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "deliveries" in tables:
                    print("Entregas pendientes:", conn.execute("SELECT count(*) FROM deliveries WHERE sent_at IS NULL").fetchone()[0])
                    for fila in conn.execute(
                            """SELECT destination,count(*) FROM deliveries
                            WHERE sent_at IS NULL GROUP BY destination
                            ORDER BY destination"""):
                        print(f"  {mask_destination(fila[0]) if fila[0] else '(sin destinatario)'}: {fila[1]}")
                if "runs" in tables:
                    last = conn.execute("SELECT finished_at,stats FROM runs ORDER BY id DESC LIMIT 1").fetchone()
                    if last:
                        print("Ultima corrida:", last[0])
                        for name, stat in json.loads(last[1]).items():
                            print(f"  {name}: {format_outcome(stat)}")
                if "deliveries" in tables:
                    columnas = {r[1] for r in conn.execute("PRAGMA table_info(deliveries)")}
                    if "state" in columnas:
                        for fila in conn.execute(
                                """SELECT destination,count(*),max(last_error)
                                FROM deliveries WHERE state='dead' AND sent_at IS NULL
                                GROUP BY destination ORDER BY destination"""):
                            problemas.append(
                                f"{mask_destination(fila[0])}: {fila[1]} entregas "
                                f"abandonadas ({fila[2] or 'sin motivo'})")
                    vieja = conn.execute(
                        """SELECT created_at,attempts,last_error FROM deliveries
                        WHERE sent_at IS NULL ORDER BY created_at LIMIT 1""").fetchone()
                    if vieja:
                        print(f"Pendiente mas antiguo: {vieja[0]} "
                              f"({vieja[1]} intentos, {vieja[2] or 'sin error'})")
                if "health_notices" in tables:
                    avisos = conn.execute(
                        "SELECT key,detail FROM health_notices ORDER BY key").fetchall()
                    print("Avisos de salud activos:",
                          "ninguno" if not avisos else "")
                    for key, detail in avisos:
                        print(f"  {key} (desde {detail})")
            finally:
                conn.close()
        else:
            print("Sin historial: todavia no hubo corridas guardadas")
        if problemas:
            print("PROBLEMAS:")
            for problema in problemas:
                print(f"  {problema}")
        return 1 if problemas else 0

    if args.cmd == "retry":
        from .store import Store
        with exclusive(cfg.db_path):
            store = Store(cfg.db_path)
            try:
                token = str(cfg.telegram.get("token", ""))
                print("Alertas enviadas:", sum(
                    flush_pending(store, token, destino)
                    for destino in cfg.telegram_destinations()))
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

    if args.cmd == "explain":
        return run_explain(cfg, args.limit, args.solo_descartados)

    if args.cmd == "deals":
        return run_deals(cfg, args.only, args.top, enrich_details=enrichment)

    if args.cmd == "watch":
        return watch_loop(cfg, args.only, enrichment, args.interval)

    if args.cmd == "collect":
        return run_collect(cfg, args)

    return 0


def run_collect(cfg: Config, args: argparse.Namespace) -> int:
    """Ejecuta recoleccion de anuncios con opciones de filtrado, enriquecimiento y formato."""
    if getattr(args, "query", None):
        q = [args.query]
        filters = replace(cfg.filters, queries=q)
        fb_opts = dict(cfg.source_opts("facebook"))
        fb_opts["marketplace_queries"] = q
        fb_opts["group_queries"] = q
        sources = dict(cfg.sources)
        sources["facebook"] = fb_opts
        cfg = replace(cfg, filters=filters, sources=sources)

    apply_filters = not getattr(args, "no_filter", False)

    if getattr(args, "enrich", None) is not None:
        enrich_details = bool(args.enrich)
    else:
        enrich_details = cfg.monitoring.get("enrich_details", True) and not getattr(args, "no_enrich", False)

    fx = FX(cfg.fx.get("rates"), offline=bool(cfg.fx.get("offline")))
    fmt = getattr(args, "format", "console")
    only = getattr(args, "only", None)

    redirect_ctx = contextlib.redirect_stdout(sys.stderr) if fmt in ("json", "csv") else contextlib.nullcontext()

    with redirect_ctx:
        try:
            listings, stats = collect(cfg, only=only, apply_filters=apply_filters, fx=fx)
        except SourceError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        if not stats or all(outcome_status(s) == "error" for s in stats.values()):
            print("No hubo fuentes disponibles; revisar configuracion/estado", file=sys.stderr)
            return 1

        if enrich_details and listings:
            try:
                from .enrich import enrich, fix_bait_prices
                enrich(listings, cfg.source_opts("facebook"))
                fix_bait_prices(listings, fx, cfg.fx.get("base", "BRL"))
            except Exception as exc:
                print(f"  ! error en enrich: {exc}", file=sys.stderr)

    limit = getattr(args, "limit", None)
    if limit is not None:
        listings = listings[:limit]

    if fmt == "json":
        rows = [l.to_row() for l in listings]
        sys.stdout.write(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
    elif fmt == "csv":
        fieldnames = ["source", "external_id", "title", "url", "price", "currency",
                      "location", "image", "posted_at", "uid", "alert_status"]
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        from .notify import _csv_safe
        for l in listings:
            writer.writerow({k: _csv_safe(v) for k, v in l.to_row().items()})
    else:
        to_console(listings)

    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    config_path = getattr(args, "config", "config.yaml")
    cfg = Config.load(config_path)
    return run_collect(cfg, args)


SESSION_PAUSE_CYCLES = 8   # pasadas que una fuente queda pausada sin sesion


def _avisar_ciclo_fallido(cfg: Config, seguidas: int, exc: Exception) -> None:
    """Un fallo ANTES de `check_health` no generaba ningun aviso.

    Base bloqueada por otro proceso, config rota, disco lleno: el radar podia
    estar un dia entero sin mirar y el unico rastro era la terminal.
    """
    token = str(cfg.telegram.get("token", ""))
    texto = (f"el vigilante lleva {seguidas} pasadas seguidas fallando antes de "
             f"poder buscar ({type(exc).__name__}). El radar no esta mirando.")
    print(f"  !! SALUD {texto}", file=sys.stderr)
    for destino in (cfg.telegram_destinations() if token else []):
        send_health(token, destino, texto)


def ciclo_inicial(cfg: Config) -> int:
    """Cuantas pasadas lleva esta base, para no reiniciar la cadencia.

    `cycle` arrancaba en 0 en cada arranque de `watch`, y como Marketplace corre
    cuando `cycle % N == 0`, reiniciar el vigilante a mano varias veces al dia
    anulaba `marketplace_every_cycles` y su proteccion de la cuenta.
    """
    ruta = Path(cfg.db_path)
    if not ruta.exists():
        return 0
    try:
        conn = sqlite3.connect(ruta.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            return int(conn.execute("SELECT count(*) FROM runs").fetchone()[0])
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def watch_loop(cfg: Config, only: list[str] | None, enrich_details: bool,
               interval_min: int, max_cycles: int | None = None,
               sleeper=time.sleep, start_cycle: int | None = None) -> int:
    """Vigilancia con cortacircuito por sesion caida.

    Sin esto, un checkpoint de Facebook dejaba el bucle reintentando cada
    intervalo contra la misma pared: no arreglaba nada, no avisaba nada y
    cada reintento sumaba riesgo sobre la cuenta.
    """
    print(f"Vigilando cada {interval_min} min. Ctrl+C para cortar.")
    try:
        pause_cycles = int(cfg.monitoring.get("session_pause_cycles", SESSION_PAUSE_CYCLES))
    except (TypeError, ValueError):
        pause_cycles = SESSION_PAUSE_CYCLES
    pause_cycles = max(1, pause_cycles)
    enabled = {name for name in REGISTRY
               if (name in only if only else cfg.source_enabled(name))}
    if not enabled:
        # Con cero fuentes habilitadas (una errata en `sources`, por ejemplo) el
        # bucle imprimia "0 motos en presupuesto" y seguia para siempre, con
        # pinta de pasada sana. Mejor fallar en el arranque y decirlo.
        print("Ninguna fuente habilitada: revisar `sources` en config.yaml, "
              "o usar --source", file=sys.stderr)
        return 2
    paused: dict[str, int] = {}
    fallos_seguidos = 0
    pasadas_ok = 0
    cycle = ciclo_inicial(cfg) if start_cycle is None else start_cycle
    hechas = 0
    interrupted = False
    while max_cycles is None or hechas < max_cycles:
        for name, resume_at in list(paused.items()):
            if cycle >= resume_at:
                del paused[name]
                print(f"  {name}: se reintenta en esta pasada")
        skip = set(paused)
        stats: dict = {}
        try:
            if enabled and skip >= enabled:
                print("  Todas las fuentes estan pausadas por sesion caida; "
                      "corre: python -m motoradar login")
            else:
                run_once(cfg, only, enrich_details=enrich_details, cycle=cycle,
                         skip=skip, stats_out=stats)
                pasadas_ok += 1
                fallos_seguidos = 0
        except KeyboardInterrupt:
            print("\nChau.")
            interrupted = True
            break
        except Exception as exc:
            fallos_seguidos += 1
            print(f"Pasada fallida ({fallos_seguidos} seguidas): {exc}",
                  file=sys.stderr)
            if fallos_seguidos == 3:
                _avisar_ciclo_fallido(cfg, fallos_seguidos, exc)
        for name, stat in stats.items():
            if isinstance(stat, dict) and stat.get("session_expired"):
                paused[name] = cycle + pause_cycles
                print(f"  !! {name}: sesion caida; pausada {pause_cycles} "
                      f"pasadas. Corre: python -m motoradar login", file=sys.stderr)
        cycle += 1
        hechas += 1
        if max_cycles is not None and hechas >= max_cycles:
            break
        try:
            sleeper(interval_min * 60)
        except KeyboardInterrupt:
            print("\nChau.")
            interrupted = True
            break
    # solo tienen esto para saber si el vigilante sirvio de algo. Antes devolvia
    # 0 aunque no hubiera completado ni una pasada.
    if interrupted:
        return 0
    return 0 if pasadas_ok else 1


def run_deals(cfg: Config, only: list[str] | None = None, top: int = 15,
              enrich_details: bool = True) -> int:
    """La vista del que compra para arreglar y revender."""
    from .market import Economics, assembly_clusters, build_references, evaluate

    cfg.validate()
    sym = Listing.SYMBOL[cfg.fx.get("base", "BRL")]
    econ = Economics.from_config(cfg.economics)

    # La muestra tiene que ser ANCHA aunque tu presupuesto sea chico: el precio
    # de referencia sale de las motos andando, que valen muy por encima de lo
    # que vas a pagar. Con el tope del presupuesto no habria con que comparar.
    wide = replace(cfg, filters=replace(cfg.filters, max_price=econ.survey_max_price,
                                        min_price=0.0))
    everything, stats = collect(wide, only, apply_filters=False)
    if not stats or all(outcome_status(s) == "error" for s in stats.values()):
        print("No hubo fuentes disponibles; no se puede distinguir de una muestra vacia.", file=sys.stderr)
        for name, stat in stats.items():
            print(f"  {name}: {format_outcome(stat)}", file=sys.stderr)
        return 1
    partial = {name: stat for name, stat in stats.items()
               if outcome_status(stat) in ("partial", "error")}
    if partial:
        print("  ! muestra parcial; algunas fuentes o paginas fallaron")
        for name, stat in partial.items():
            print(f"    {name}: {format_outcome(stat)}")
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
    return 0
