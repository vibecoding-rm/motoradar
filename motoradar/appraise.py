"""Clasifica un aviso desde la optica de quien compra para arreglar y revender.

Todo el vocabulario salio de leer 1000 avisos reales de OLX/RS, no de suponer.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .models import Listing, parse_price, strip_accents

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
    # Modelos uruguayos y de frontera (Cerro Largo / Rio Branco)
    ("winner-fair",        r"\bwinner\s+fair\b|\bfair\s*110\b"),
    ("winner-street",      r"\bwinner\s+street\b"),
    ("winner-orion",       r"\bwinner\s+orion\b"),
    ("winner-explorer",    r"\bwinner\s+explorer\b"),
    ("winner-strong",      r"\bwinner\s+strong\b"),
    ("winner-bis",         r"\bwinner\s+bi[sz]\b"),
    ("winner-force",       r"\bwinner\s+force\b"),
    ("winner-exclusive",   r"\bwinner\s+exclusive\b"),
    ("winner-dakar",       r"\bwinner\s+dakar\b"),
    ("winner",             r"\bwinner\s+(?:50|70|90|100|110|125|150|200|250)(?:\s*cc)?\b|\bmoto\s+winner\b"),
    ("yumbo-gs-200",       r"\byumbo\s+gs\s*200\b|\bgs\s*200\b"),
    ("yumbo-gs-125",       r"\byumbo\s+gs\s*125\b|\bgs\s*125\b"),
    ("yumbo-gs",           r"\byumbo\s+gs\b|\bgs\s*(?:ii|2)\b"),
    ("yumbo-dakar",        r"\byumbo\s+dakar\b|\bdakar\s*(?:125|200)\b"),
    ("yumbo-speed",        r"\byumbo\s+speed\b"),
    ("yumbo-max",          r"\byumbo\s+max\b"),
    ("yumbo-city",         r"\byumbo\s+city\b"),
    ("yumbo-milestone",    r"\byumbo\s+milestone\b"),
    ("yumbo-shark",        r"\byumbo\s+shark\b"),
    ("baccio-classic",     r"\bbaccio\s+classic\b"),
    ("baccio-px-110",      r"\bbaccio\s+px\s*110\b|\bpx\s*110\b"),
    ("baccio-px",          r"\bbaccio\s+px\b|\bpx\s*125\b"),
    ("baccio-x3m",         r"\bbaccio\s+x\s*3\s*m\b"),
    ("baccio-cruiser",     r"\bbaccio\s+cruiser\b"),
    ("baccio-rider",       r"\bbaccio\s+rider\b"),
    ("zanella-due",        r"\bzanella\s+due\b|\bdue\s*(?:50|110)\b"),
    ("zanella-zb",         r"\bzanella\s+zb\b|\bzb\s*(?:110|125)\b"),
    ("zanella-rx",         r"\bzanella\s+rx\b|\brx\s*(?:125|150)\b"),
    ("zanella-sapucai",    r"\bzanella\s+sapucai\b|\bsapucai\b"),
    ("zanella-ceccato",    r"\bzanella\s+ceccato\b|\bceccato\b"),
    ("zanella-hot",        r"\bzanella\s+hot\b"),
    ("zanella-sol",        r"\bzanella\s+sol\b"),
    ("zanella",            r"\bzanella\s+(?:50|70|90|100|110|125|150|200)?(?:\s*cc)?\b|\bmoto\s+zanella\b"),
    ("keeway-target",      r"\bkeeway\s+target\b|\btarget\s*125\b"),
    ("keeway-superlight",  r"\bkeeway\s+superlight\b"),
    ("keeway-rks",         r"\bkeeway\s+rks\b"),
    ("keeway-rkf",         r"\bkeeway\s+rkf\b"),
    ("keeway",             r"\bkeeway\b"),
    ("mondial-td",         r"\bmondial\s+td\b|\btd\s*(?:150|200)\b"),
    ("mondial-hd",         r"\bmondial\s+hd\b"),
    ("mondial-ld",         r"\bmondial\s+ld\b|\bld\s*110\b"),
    ("mondial-rd",         r"\bmondial\s+rd\b"),
    ("mondial-dax",        r"\bmondial\s+dax\b"),
    ("mondial",            r"\bmondial\b"),
    ("vince",              r"\bvince\s*(?:110|125|hot|sport)?(?:\s*cc)?\b|\bmoto\s+vince\b"),
    # Marca Vital: model patterns para evitar colisión con adjetivo 'vital'
    ("vital-vx",           r"\bvital\s+vx\b|\bvx\s*(?:110|125)\b"),
    ("vital",              r"\bvital\s+(?:50|70|90|100|110|125|150|200)(?:\s*cc)?\b|\bmoto\s+vital\b"),
]

# Autos que ensucian la categoria de piezas: "Sucata Fusca Pra Retirada".
# Deliberadamente SIN "strada" (existe la Honda CBX 200 Strada), "ka" y
# "master" (hay una "Moto master ride 150"): daban falsos positivos.
CAR_WORDS = (
    "fusca gol golf palio uno celta corsa onix hb20 civic corolla focus fiesta "
    "fit 320i 318i "
    "ecosport vectra astra linea siena saveiro fiorino kombi hilux "
    "ranger s10 amarok duster sandero logan clio 206 207 307 308 407 504 "
    "peugeot citroen renault fiat chevrolet volkswagen ford toyota hyundai "
    "iveco daily sprinter ducato caminhao automovel"
).split()

# Accesorios y repuestos sueltos: NO son una moto.
# La busqueda por 'moto' en Marketplace trae telefonos Motorola Moto G.
PHONE_WORDS = "celular smartphone iphone samsung xiaomi redmi tablet notebook".split()

# En Marketplace los titulos son informales y muchas veces no nombran marca.
# La palabra tiene que describir el objeto al comienzo: una casa que "aceita moto
# na troca" o "pecas de moto" no se convierten por eso en una motocicleta.
VEHICLE_LEAD_RE = re.compile(
    r"^(?:(?:19[89]\d|20[0-3]\d)\s+)?(?:uma?\s+)?(?:pro(?:yect|jet)o\s+de\s+)?"
    r"(?:motos?\b|motocicleta|scooter|ciclomotor|mobilete|motoneta)"
)

# --- cuerpos de post de grupo -------------------------------------------------
# Un titulo de Marketplace es corto y arranca por marca o modelo. Un post de
# grupo arranca por saludo, vocativo o emoji casi siempre, y por eso toda
# heuristica de posicion fallaba: "Bom dia grupo! Vendo motor de CG 150" salia
# como MOTO EN PRESUPUESTO, y "Vendo essa 125 andando, 800" salia como
# desconocido y se descartaba. Medido sobre posts reales: precision 0,40.
GREETING_RE = re.compile(
    r"^(?:[^\w\s]+\s*)*"
    r"(?:(?:bom\s+dia|boa\s+tarde|boa\s+noite|buenas|buen\s+dia|"
    r"galera|pessoal|gente|amigos|amigas|atencao|atencion|oi|ola|hola|alo|"
    r"grupo|grupos|povo|turma|colegas|vizinhos|"
    r"oportunidade|oportunidad|urgente|novidade|aproveite)\b[\s,!:.\-]*)+")
# Verbos de venta y de oferta, mas determinantes, en portugues y en español de
# la frontera. "tenho" incluido: "tenho um tanque da Biz 125" es una pieza, y
# sin pelarlo el modelo de la moto convertia el aviso en una moto entera.
SELL_VERB_RE = re.compile(
    r"^(?:vendo|vende-?se|vendese|se\s+vende|troco|repasso|passo|dou|entrego|"
    r"desapego|liquido|tenho|tem|ofereco|ofrezco)\s+"
    r"(?:(?:um|uma|uns|umas|o|a|os|as|esse|essa|este|esta|meu|minha|mi|mia|"
    r"un|una|el|la)\s+)*")
# Palabra que describe un vehiculo, en CUALQUIER posicion del cuerpo.
VEHICLE_WORD_RE = re.compile(
    r"\b(?:motos?|motocicleta|motoneta|motinh[oa]s?|motoca|motito|scooter|"
    r"ciclomotor|mobilete|quadriciclo)\b")
# "vendo essa 125", "moto 110cc". La cilindrada SOLA no alcanza: si valiera,
# "vendo geladeira 200 reais" seria una moto.
DISPLACEMENT_RE = re.compile(
    r"\b(?:ess[ae]|est[ae]|um[ao]|minha|meu|mi|la|el)\s+"
    r"(?:50|100|110|115|125|150|160|190|200|250|300|400)\b"
    r"|\b\d{2,4}\s*cc\b")
# Una rifa no es una venta: "Sorteio: concorra a uma CG 160 0km, cartela 20".
RAFFLE_RE = re.compile(r"\bsorteio\b|\brifa\b|\bcartela\b|\bnumero\s+premiad")
# Alquilar tampoco es vender. "Alugo moto por dia, 50 reais" entraba como una
# moto de R$50 dentro del presupuesto.
RENT_RE = re.compile(r"\balug(?:o|a|am|ar|uel)\b|\balquil|\barriendo\b|\bdiaria\b")
# Vivienda y terrenos que aceptan una moto en parte de pago: el objeto que se
# vende no es la moto. "Vendo casa que aceita moto na troca".
PROPERTY_WORDS = (
    "casa casas terreno terrenos apartamento apto kitnet chacara sitio "
    "galpao barracao lote quadra rancho campo "
    "propiedad propiedades solar solares local locales chacra inmueble inmuebles"
).split()
PIECE_OFFER_RE = re.compile(
    r"^(?:pecas?|partes?|piezas?|repuestos?|plasticos?|accesorios?|pe)\s+(?:d[aeo]s?\s+|para\s+)?motos?\b"
)
MACHINERY_RE = re.compile(
    r"\b(?:rocadeira|motosserra|gerador|aparador|compressor|furadeira|parafusadeira)\b"
)

BICYCLE_WORDS = "bicicleta bicicletas bici bicis chiva chivas bike bikes".split()
BICYCLE_RE = re.compile(
    r"\b(?:bicicleta|bici|chiva|mountain\s+bike|bmx)\b|"
    r"\brodado\s*(?:1[26]|2[046789])\b"
)

ACCESSORY_WORDS = (
    "capacete viseira bau bauleto baleto colete joelheira luva jaqueta bota "
    "retrovisor espelho pneu camara relacao coroa pinhao corrente escapamento "
    "ponteira cavalete tanque banco guidao manete farol lanterna pisca bateria "
    "vela filtro oleo carburador radiador protetor slider suporte alarme capa "
    "rabeta paralama carenagem adesivo chave macaco bomba calibrador oculos "
    "mochila alforje rack antena carregador peca plastico volante "
    # lo que se colo como si fuera moto entera en la primera corrida:
    "lente escape motor manual kit par jogo comando biela caixa virabrequim cilindro "
    "pistao embreagem disco pastilha amortecedor garfo roda aro raio tambor "
    "cabo velocimetro painel chicote rele bobina cdi magneto estator buzina "
    "pedaleira coifa retentor rolamento junta anel valvula cabecote bengala acabamento bolha bacalhau tbi carter escapamento cesto cestos bagageiro "
    # Repuestos y accesorios en espanol:
    "casco campera cubierta cubiertas llanta llantas repuesto repuestos "
    "freno frenos foco focos cadena cadenas juego espejos espejo "
    "asiento guantes baul baulera amortiguador amortiguadores horquilla "
    "guardabarro guardabarros carenado careta embrague piston bujia bujias "
    "bocina tablero cano"
).split()

# Estado del vehiculo.
PARTS_RE = re.compile(
    r"\bsucata\b|\bem\s+pecas?\b|retirada\s+de\s+peca|"
    r"\bpecas?\s+d[ae]\b|\bdoador\b|\bdesmanche\b|\bdesmonte\b|so\s+as\s+pecas|"
    r"\bdesarme\b|\bpara\s+desarm|\bpara\s+repuestos?\b|\bpara\s+piezas?\b|\ben\s+repuestos?\b|(?<!motor\s)\bdesarmad[ao]\b"
)
PROJECT_RE = re.compile(
    r"\bnao\s+funciona\b|\bnao\s+pega\b|\bnao\s+anda\b|motor\s+fundido\b|"
    r"\bfundid[ao]\b|\bparad[ao]\s+(ha|faz)\b|\bpro(?:yect|jet)o\b|\bpra\s+restaurar\b|"
    r"\brestaurar\b|\brestauracao\b|\bpra\s+reforma\b|\breformar\b|"
    r"\bp/?\s*conserto\b|\bpra\s+consertar\b|\bincompleta\b|\bsem\s+motor\b|"
    r"\baveriad[ao]s?\b|\bno\s+funciona\b|\bno\s+anda\b|\bno\s+arranca\b|"
    r"\bpara\s+reparar\b|\ba\s+reparar\b|\brot[ao]s?\b|\bsin\s+motor\b|\bmotor\s+roto\b"
)
# Problemas de papeles: en Brasil definen si la podes revender legalmente.
# Lo de 'carteirinha' y 'so pra rodar' salio de leer descripciones reales de
# Jaguarao: es como se dice ahi que la moto no tiene documento.
DOC_RISK_RE = re.compile(
    r"\bsem\s+document|\bsem\s+doc\b|\bsem\s+carteirinha\b|"
    r"\bsem\s+papel|\bdocumento\s+atrasad|\bdivida\b|\bipva\s+atrasad|"
    r"\bso\s+pra\s+rodar\b|\bnao\s+transfer|\bsinistrad|\bbatid[ao]\b|"
    r"\brecuperad[ao]\b|\bleilao\b|\bsem\s+placa\b|\bchassi\s+remarcad|"
    r"\bmulta[s]?\s+atrasad|\bsem\s+nota\b|"
    r"\bnao\s+tem\s+(?:carteirinha|document|papel|placa|nota)|"
    # Documentacion en frontera / Uruguay (Rio Branco):
    r"\bsin\s+(?:papeles?|documentos?|doc|libreta|matricula|chapa|titulo[s]?)\b|"
    r"\bno\s+tiene\s+(?:papeles?|documentos?|doc|libreta|matricula|chapa|titulo[s]?)\b|"
    r"\bsolo\s+(?:con\s+)?libreta\b|"
    r"\bdebe\s+patente[s]?\b|\bdeuda\s+de\s+patente[s]?\b|\bpatente[s]?\s+atrasad[ao]s?\b|"
    r"\bno\s+transf[ie]ere\b|\bsolo\s+para\s+campo\b|\bsolo\s+campo\b|"
    r"\bembargad[ao]\b|\bprendad[ao]\b"
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
    r"\b\d+x\s+sem\b|\bchegou\s+(?:reposic|na\s+loja|novidad|estoque)|\breposic|\batacado\b|"
    r"\baqui\s+n[ao]\s+|\bconsulte\b|\bentrega\s+grati|"
    r"\bloja\b|\bfrete\s+grati|\bcatalogo\b|\bencomenda\b|\bmototaxi\b|\bmoto\s+taxi\b|\bcorrida\s+rapida\b|"
    r"\bentrega\s+com\s+confianca\b|\bfazemos\s+entrega\b|"
    r"\btaller(?:\s+mecanico)?\b|\bmecanica\b|\bfletes?\b|\bcadeteria\b|\breparacion(?:es)?\b"
)

# "Perdi a placa da moto, se alguem achar me chamem" no es una venta.
LOST_RE = re.compile(r"\bperdi\b|\bperdeu\b|\bachei\b|\bencontrei\b|\bse\s+alguem\s+achar\b|\bfoi\s+furtad")

WANTED_RE = re.compile(
    r"\bprocuro\b|\ba\s+pedido\b|\bbusco\b|\bcompro\b|\bcomprar\b|\bpreciso\s+de\b|"
    r"\balguem\s+tem\b|\balguien\s+tiene\b|\bnecesito\b|\bando\s+buscando\b|"
    r"\bpago\s+contado\b|\bquien\s+vende\b|\bse\s+busca\b"
)
SELLING_RE = re.compile(
    r"\bvendo\b|\bvende-?se\b|\bse\s+vende\b|\bestou\s+vendendo\b|\btroco\b|\brepasso\b|\bpermuto\b"
)

STOLEN_RE = re.compile(r"\broubad|\bfurtad|\bsem\s+procedencia\b")

YEAR_RE = re.compile(r"\b(19[89]\d|20[0-3]\d)\b")

# Si el titulo arranca con una marca de moto, es una moto aunque el modelo
# no este en el registro ("Suzuki Chopper Road ... bau personal").
# Marcas adicionales frecuentes en Uruguay y frontera (Rio Branco / Cerro Largo).
# OJO: 'vital' NO se incluye aqui como palabra suelta para evitar falsos positivos
# con frases cotidianas ("parte vital", "de vital importancia"). Se clasifica
# exclusivamente por modelo en MODELS ('vital-vx', 'vital 110', etc.).
MOTORCYCLE_BRANDS_EXTRA = (
    "yumbo baccio zanella keeway mondial vince winner"
).split()

BRAND_WORDS = (
    "honda yamaha suzuki kawasaki bmw harley triumph ducati dafra shineray "
    "traxx sundown kasinski haojue mottu royal bajaj ktm husqvarna aprilia "
    "vespa piaggio kymco lifan jonny "
    "yumbo baccio zanella keeway mondial vince winner"
).split()

# Un desmanche que publica a R$1/R$10 no esta vendiendo a ese precio: es
# carnada para que lo llames. Comprobado: el mismo vendedor lista la misma
# sucata a R$10 y a R$10.000.
BAIT_PRICE = 100.0
DECOY_PRICE_RE = re.compile(r"^(?:1234(?:5(?:6(?:7(?:8(?:9)?)?)?)?)?|(\d)\1{3,})$")


def is_decoy_sequence(price: float | int | str | None) -> bool:
    """Detecta secuencias numericas señuelo como 1234, 12345, 1111, 2222 independientemente de la moneda."""
    if price is None:
        return False
    if isinstance(price, str):
        parsed = parse_price(price)
        if parsed is not None:
            price = parsed
    try:
        val = float(price)
    except (ValueError, TypeError):
        return False
    if not math.isfinite(val):
        return False
    s = str(int(val))
    return bool(DECOY_PRICE_RE.match(s))


def is_bait_price(price: float | int | str | None) -> bool:
    """Detecta precios señuelo o carnada: <= BAIT_PRICE (100.0) o secuencias como 1234, 1111."""
    if price is None:
        return False
    if isinstance(price, str):
        parsed = parse_price(price)
        if parsed is not None:
            price = parsed
    try:
        val = float(price)
    except (ValueError, TypeError):
        return False
    if not math.isfinite(val):
        return False
    if val <= BAIT_PRICE:
        return True
    return is_decoy_sequence(val)


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


def _vehicle_evidence(shape: str, model: str | None, object_title: str,
                      cuerpo: str, text: str) -> bool:
    """¿Hay evidencia de que el objeto sea una moto entera?

    En un titulo de Marketplace la posicion es el mejor discriminador y se
    mantiene. En un cuerpo de post no sirve: la evidencia puede estar en
    cualquier parte de la frase. Las guardas de exclusion (pieza, auto,
    telefono, maquinaria) se evaluan ANTES y siguen ganando.
    """
    if model:
        return True
    if shape == "post":
        return bool(VEHICLE_WORD_RE.search(object_title)
                    or _has(BRAND_WORDS, object_title)
                    or DISPLACEMENT_RE.search(cuerpo))
    return bool(_leads_with(BRAND_WORDS, text, 2)
                or VEHICLE_LEAD_RE.search(object_title))


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
    title = strip_accents(listing.title)
    # Un cuerpo de post se limpia distinto que un titulo de anuncio.
    shape = listing.raw.get("text_shape") or (
        "post" if listing.raw.get("kind") == "group" else "title")
    cuerpo = GREETING_RE.sub("", title) if shape == "post" else title
    object_title = re.sub(r"^\d+\s+", "", SELL_VERB_RE.sub("", cuerpo))
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
    if re.search(r"\bmoto\s*g\s*\d", object_title) or (not model and _leads_with(PHONE_WORDS, object_title)):
        kind = "accessory"          # "Moto G 22" es un telefono, no una moto
    elif MACHINERY_RE.search(title):
        kind = "accessory"
    elif not model and _leads_with(CAR_WORDS, object_title):
        kind = "car"
    elif _leads_with(PROPERTY_WORDS, object_title, 2):
        # Lo que se vende es la vivienda; la moto es lo que aceptan en parte de
        # pago. Sin este guarda, "Vendo casa que aceita moto na troca" entraba
        # como una moto dentro del presupuesto. No exige `not model`: un aviso
        # de casa que nombra una CG 125 sigue siendo un aviso de casa.
        kind = "other"
    elif not model and (_leads_with(BICYCLE_WORDS, object_title, 2) or BICYCLE_RE.search(object_title)):
        kind = "other"
    elif PIECE_OFFER_RE.search(object_title) or _leads_with(ACCESSORY_WORDS, object_title, 1):
        # La condicion no cambia el objeto: "Motor Biz fundido" sigue siendo
        # un motor suelto; "Moto Biz con motor fundido" sí es una moto proyecto.
        kind = "accessory"
    elif in_parts_category and not (is_parts or is_project):
        kind = "accessory"
    elif _vehicle_evidence(shape, model, object_title, cuerpo, text):
        kind = "moto"
    elif _has(ACCESSORY_WORDS, text):
        kind = "accessory"
    else:
        kind = "unknown"

    condition = "parts" if is_parts else "project" if is_project else "runner"

    title_wanted = bool(WANTED_RE.search(title))
    title_selling = bool(SELLING_RE.search(title))
    if title_wanted and not title_selling:
        wanted = True
    elif title_selling:
        wanted = False
    else:
        wanted = bool(WANTED_RE.search(text)) and not SELLING_RE.search(text)

    return Appraisal(
        kind=kind,
        condition=condition,
        model=model,
        year=extract_year(listing.title),
        bait_price=is_bait_price(listing.price) or is_decoy_sequence(listing.raw.get("original_price")),
        doc_risk=bool(DOC_RISK_RE.search(text)),
        stolen_flag=bool(STOLEN_RE.search(text)),
        wanted=wanted,
        # No son ventas de un particular: publicidad de comercio, objeto
        # perdido, rifa y alquiler. "Alugo moto por dia, 50 reais" entraba
        # como una moto de R$50 dentro del presupuesto.
        shop_ad=bool(SHOP_RE.search(text) or LOST_RE.search(text)
                     or RAFFLE_RE.search(text) or RENT_RE.search(text)),
    )
