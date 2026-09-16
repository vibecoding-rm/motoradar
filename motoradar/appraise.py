"""Clasifica un aviso desde la optica de quien compra para arreglar y revender.

Todo el vocabulario salio de leer 1000 avisos reales de OLX/RS, no de suponer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Listing, strip_accents

# --- modelos de moto en Brasil -----------------------------------------------
# clave canonica -> patron. El orden importa: lo especifico primero.
MODELS: list[tuple[str, str]] = [
    ("honda-cg-160",   r"\bcg[\s-]*160\b|\btitan\s*160\b"),
    ("honda-cg-150",   r"\bcg[\s-]*150\b|\b(titan|fan)\s*150\b"),
    ("honda-cg-125",   r"\bcg[\s-]*125\b|\b(titan|fan)\s*125\b|\bcg\s*(ks|es|esd)\b"),
    ("honda-cg",       r"\bcg\b|\btitan\b|\bfan\b"),
    ("honda-biz",      r"\bbiz\b|\bbis\s*125\b"),
    ("honda-pop",      r"\bpop\s*(100|110)?\b"),
    ("honda-bros",     r"\bbros\b|\bnxr\b"),
    ("honda-xre",      r"\bxre\b"),
    ("honda-twister",  r"\btwister\b|\bcbx\s*250\b"),
    ("honda-cb300",    r"\bcb\s*300\w?\b"),          # CB 300R / 300F
    ("honda-cb500",    r"\bcb\s*500\w?\b"),          # CB 500X / 500F
    ("honda-cbr",      r"\bcbr\b"),
    ("honda-falcon",   r"\bfalcon\b|\bnx[\s-]*4\b"),
    ("honda-pcx",      r"\bpcx\b"),
    ("honda-sh",       r"\bsh\s*150\b"),
    ("yamaha-factor",  r"\bfactor\b|\bybr\b"),
    ("yamaha-fazer",   r"\bfazer\b|\bys\s*250\b"),
    ("yamaha-crosser", r"\bcrosser\b"),
    ("yamaha-lander",  r"\blander\b"),
    ("yamaha-ttr",     r"\bttr\s*2?[23]0\b|\bttr\b"),
    ("yamaha-tenere",  r"\btenere\b|\bxtz\s*250\b"),   # 250: otra liga de precio
    ("yamaha-xtz-125", r"\bxtz\s*1(25|50)\w?\b"),  # XTZ 125K / 150
    ("yamaha-xtz",     r"\bxtz\b"),
    ("yamaha-mt",      r"\bmt[\s-]*0?[3579]\b"),
    ("yamaha-r3",      r"\br3\b|\byzf\b"),
    ("suzuki-intruder", r"\bintruder\b"),
    ("suzuki-gsx",     r"\bgsx\b|\bgsr\b"),
    ("suzuki-vstrom",  r"\bv[\s-]*strom\b"),
    ("kawasaki-ninja", r"\bninja\b|\bzx[\s-]*\d+\b"),
    ("shineray",       r"\bshineray\b"),
    ("dafra",          r"\bdafra\b|\bkansas\b|\bhorizon\b"),
    ("traxx",          r"\btraxx\b"),
    ("sundown",        r"\bsundown\b|\bweb\s*100\b"),
    ("haojue",         r"\bhaojue\b|\bdk\s*150\b"),
    ("honda-xl",       r"\bxl\s*125\b"),
]

# Autos que ensucian la categoria de piezas: "Sucata Fusca Pra Retirada".
# Deliberadamente SIN "strada" (existe la Honda CBX 200 Strada), "ka" y
# "master" (hay una "Moto master ride 150"): daban falsos positivos.
CAR_WORDS = (
    "fusca gol golf palio uno celta corsa onix hb20 civic corolla focus fiesta "
    "ecosport vectra astra linea siena saveiro fiorino kombi hilux "
    "ranger s10 amarok duster sandero logan clio 206 207 307 308 407 504 "
    "peugeot citroen renault fiat chevrolet volkswagen ford toyota hyundai "
    "iveco daily sprinter ducato caminhao automovel"
).split()

# Accesorios y repuestos sueltos: NO son una moto.
# La busqueda por 'moto' en Marketplace trae telefonos Motorola Moto G.
PHONE_WORDS = "celular smartphone iphone samsung xiaomi redmi tablet notebook".split()

# En Marketplace los titulos son informales y muchas veces no nombran marca:
# "Moto trilha", "Vendo moto Winner 200cc uruguaia", "Moto". Sin esto quedaban
# como desconocidas y se perdian, que es justo donde esta lo barato.
MOTO_WORD_RE = re.compile(r"\bmotos?\b|\bmotocicleta|\bscooter|\bciclomotor|\bmobilete|\bmotoneta")

ACCESSORY_WORDS = (
    "capacete viseira bau bauleto baleto colete joelheira luva jaqueta bota "
    "retrovisor espelho pneu camara relacao coroa pinhao corrente escapamento "
    "ponteira cavalete tanque banco guidao manete farol lanterna pisca bateria "
    "vela filtro oleo carburador radiador protetor slider suporte alarme capa "
    "rabeta paralama carenagem adesivo chave macaco bomba calibrador oculos "
    "mochila alforje rack antena carregador "
    # lo que se colo como si fuera moto entera en la primera corrida:
    "lente escape manual kit par jogo comando biela caixa virabrequim cilindro "
    "pistao embreagem disco pastilha amortecedor garfo roda aro raio tambor "
    "cabo velocimetro painel chicote rele bobina cdi magneto estator buzina "
    "pedaleira coifa retentor rolamento junta anel valvula cabecote bengala acabamento bolha bacalhau tbi carter escapamento cesto cestos bagageiro"
).split()

# Estado del vehiculo.
PARTS_RE = re.compile(
    r"\bsucata\b|\bem\s+pecas?\b|retirada\s+de\s+peca|"
    r"\bpecas?\s+d[ae]\b|\bdoador\b|\bdesmanche\b|\bdesmonte\b|so\s+as\s+pecas"
)
PROJECT_RE = re.compile(
    r"\bnao\s+funciona\b|\bnao\s+pega\b|\bnao\s+anda\b|motor\s+fundido\b|"
    r"\bfundid[ao]\b|\bparad[ao]\s+(ha|faz)\b|\bprojeto\b|\bpra\s+restaurar\b|"
    r"\brestaurar\b|\brestauracao\b|\bpra\s+reforma\b|\breformar\b|"
    r"\bp/?\s*conserto\b|\bpra\s+consertar\b|\bincompleta\b|\bsem\s+motor\b"
)
# Problemas de papeles: en Brasil definen si la podes revender legalmente.
# Problemas de papeles: en Brasil definen si la podes revender legalmente.
# Lo de 'carteirinha' y 'so pra rodar' salio de leer descripciones reales de
# Jaguarao: es como se dice ahi que la moto no tiene documento.
DOC_RISK_RE = re.compile(
    r"\bsem\s+document|\bsem\s+doc\b|\bsem\s+carteirinha\b|"
    r"\bsem\s+papel|\bdocumento\s+atrasad|\bdivida\b|\bipva\s+atrasad|"
    r"\bso\s+pra\s+rodar\b|\bnao\s+transfer|\bsinistrad|\bbatid[ao]\b|"
    r"\brecuperad[ao]\b|\bleilao\b|\bsem\s+placa\b|\bchassi\s+remarcad|"
    r"\bmulta[s]?\s+atrasad|\bsem\s+nota\b|"
    r"\bnao\s+tem\s+(carteirinha|document|papel|placa|nota)"
)
# Avisos que BUSCAN comprar, no que venden. Los dos casos reales de Jaguarao
# usan "a pedido", asi que la palabra sola no alcanza:
#   "A pedido alguma biz a venda"          -> busca comprar
#   "Vendo moto pra retirada de pecas"     -> vende
# Un "vendo" explicito le gana siempre a cualquier marca de busqueda.
# Publicidad de comercios, no ventas de particular. En los grupos de Jaguarao
# abunda: "Rio Motos Liquidacao de Capacetes -15% ou parcele em 4x",
# "Aqui na Jaguarao Auto Moto Pecas encontras Militec 1", "Chegou reposicao".
SHOP_RE = re.compile(
    r"\bliquidac|\bpromoc|\d+%|\bparcele\b|\bsem\s+juros\b|"
    r"\b\d+x\s+sem\b|\bchegou\s+|\breposic|\batacado\b|"
    r"\baqui\s+n[ao]\s+|\bconsulte\b|\bentrega\s+grati|"
    r"\bloja\b|\bfrete\s+grati|\bcatalogo\b|\bencomenda\b|\bmototaxi\b|\bmoto\s+taxi\b|\bcorrida\s+rapida\b|\bentrega\s+com\s+confianca\b|\bfazemos\s+entrega\b"
)

# "Perdi a placa da moto, se alguem achar me chamem" no es una venta.
LOST_RE = re.compile(r"\bperdi\b|\bperdeu\b|\bachei\b|\bencontrei\b|\bse\s+alguem\s+achar\b|\bfoi\s+furtad")

WANTED_RE = re.compile(r"\bprocuro\b|\ba\s+pedido\b|\bbusco\b|\bcompro\b|\bpreciso\s+de\b|\balguem\s+tem\b")
SELLING_RE = re.compile(r"\bvendo\b|\bvende-?se\b|\bestou\s+vendendo\b|\btroco\b|\brepasso\b")

STOLEN_RE = re.compile(r"\broubad|\bfurtad|\bsem\s+procedencia\b")

YEAR_RE = re.compile(r"\b(19[89]\d|20[0-3]\d)\b")

# Si el titulo arranca con una marca de moto, es una moto aunque el modelo
# no este en el registro ("Suzuki Chopper Road ... bau personal").
BRAND_WORDS = (
    "honda yamaha suzuki kawasaki bmw harley triumph ducati dafra shineray "
    "traxx sundown kasinski haojue mottu royal bajaj ktm husqvarna aprilia "
    "vespa piaggio kymco lifan jonny"
).split()

# Un desmanche que publica a R$1/R$10 no esta vendiendo a ese precio: es
# carnada para que lo llames. Comprobado: el mismo vendedor lista la misma
# sucata a R$10 y a R$10.000.
BAIT_PRICE = 100.0


@dataclass
class Appraisal:
    kind: str             # moto | accessory | car | unknown
    condition: str        # runner | project | parts
    model: str | None
    year: int | None
    bait_price: bool
    doc_risk: bool
    stolen_flag: bool
    wanted: bool = False
    shop_ad: bool = False

    @property
    def is_buyable(self) -> bool:
        """Una moto entera que sirva como compra (andando, proyecto o doador)."""
        return self.kind == "moto" and not self.wanted and not self.shop_ad


def _has(words: list[str], text: str) -> bool:
    # con s? opcional: "piscas", "pecas", "lentes" son el mismo termino
    return any(re.search(rf"\b{w}s?\b", text) for w in words)

def _leads_with(words: list[str], text: str, window: int = 4) -> bool:
    """La pieza suelta va al principio del titulo: "Tanque factor 125 Yamaha",
    "Carburador da CBX 200". La moto entera arranca por marca/modelo:
    "Honda Biz C100 ES com partida eletrica, incluido capacete". Esa posicion
    resulto el mejor discriminador sobre los avisos reales."""
    head = " ".join(text.split()[:window])
    return _has(words, head)


def extract_model(title: str) -> str | None:
    text = strip_accents(title)
    for key, pattern in MODELS:
        if re.search(pattern, text):
            return key
    return None


def extract_year(title: str) -> int | None:
    years = [int(y) for y in YEAR_RE.findall(title)]
    # "Sucata Honda SH 150I 2017 2017" -> repetido; "CG 125 2008" -> uno solo.
    return max(years) if years else None


def appraise(listing: Listing) -> Appraisal:
    text = strip_accents(f"{listing.title} {listing.raw.get('description', '')}")
    model = extract_model(listing.title)

    # Guarda estructural, mas confiable que perseguir vocabulario: la
    # categoria de repuestos solo contiene motos enteras cuando el aviso dice
    # "sucata"/"em pecas". Si no lo dice, es una pieza aunque nombre el modelo
    # ("Bolha Kawasaki Ninja 300", "Bacalhau do piloto", "TBI XRE 190").
    in_parts_category = "pecas-e-acessorios" in str(listing.raw.get("category", ""))

    is_parts = bool(PARTS_RE.search(text))
    is_project = bool(PROJECT_RE.search(text))

    # El orden importa. Una pieza suelta se titula empezando por la pieza
    # ("Tanque factor 125"); una moto entera empieza por marca o modelo
    # ("Suzuki Chopper Road ... bau personal"), aunque mencione accesorios.
    if re.search(r"\bmoto\s*g\s*\d", text) or (not model and _has(PHONE_WORDS, text)):
        kind = "accessory"          # "Moto G 22" es un telefono, no una moto
    elif not model and _leads_with(CAR_WORDS, text):
        kind = "car"
    elif not (is_parts or is_project) and _leads_with(ACCESSORY_WORDS, text):
        # is_project tambien manda: "Projeto kit trio" son tres motos proyecto
        # al precio de una, no un kit de repuestos.
        kind = "accessory"
    elif in_parts_category and not (is_parts or is_project):
        kind = "accessory"
    elif model or _leads_with(BRAND_WORDS, text, 2):
        kind = "moto"
    elif MOTO_WORD_RE.search(text):
        kind = "moto"
    elif _has(ACCESSORY_WORDS, text):
        kind = "accessory"
    else:
        kind = "unknown"

    condition = "parts" if is_parts else "project" if is_project else "runner"

    return Appraisal(
        kind=kind,
        condition=condition,
        model=model,
        year=extract_year(listing.title),
        bait_price=listing.price is not None and listing.price <= BAIT_PRICE,
        doc_risk=bool(DOC_RISK_RE.search(text)),
        stolen_flag=bool(STOLEN_RE.search(text)),
        wanted=bool(WANTED_RE.search(text)) and not SELLING_RE.search(text),
        shop_ad=bool(SHOP_RE.search(text) or LOST_RE.search(text)),
    )
