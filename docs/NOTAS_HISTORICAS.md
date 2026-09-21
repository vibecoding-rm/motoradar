# Notas históricas previas a la organización del repositorio

Estas notas conservan la documentación original como contexto. Sus cifras, afirmaciones sobre proveedores y observaciones de mercado no son garantías vigentes. Para el objetivo y el estado actual consultar README.md, OBJETIVO.md y ROADMAP.md.

# motoradar

Herramienta para **comprar motos baratas, arreglarlas o armarlas, y revenderlas**
en **Jaguarão (RS)** y alrededores.

No es solo un buscador: clasifica cada aviso (moto entera / proyecto / doador /
repuesto suelto), calcula el **precio de referencia del modelo** con lo que se
pide hoy en la región, y estima el **margen** que te queda después de arreglarla.


## Radar de compras de hasta R$1.000

`run` y `watch` ahora clasifican motos y leen las descripciones antes de avisar.
Motos averiadas, proyectos y doadoras interesan; **papeles y margen no filtran**.
El límite es `budget`, inclusive. Los avisos sin precio o con precio señuelo
se identifican por separado como **PRECIO POR CONFIRMAR**.

```powershell
python -m motoradar doctor
python -m motoradar run --source olx --budget 1000 --dry-run
python -m motoradar watch --budget 1000 --interval 30
python -m motoradar retry
python -m unittest discover -s tests -v
```

`--dry-run` consulta la fuente pero no guarda anuncios, exporta CSV ni envía
mensajes. `retry` procesa entregas pendientes sin volver a buscar. Las alertas
fallidas quedan guardadas para próximas corridas; las bajadas de precio pueden
producir una nueva alerta. No se garantiza disponibilidad al momento de leerla.

`monitoring.enrich_details` controla la lectura de detalle y
`monitoring.alert_unconfirmed` permite desactivar las alertas de precio incierto.
Telegram requiere token y chat_id, en YAML o variables de entorno; `doctor`
indica si están configurados sin revelar sus valores. El intervalo de `watch`
es una pausa entre corridas, además del tiempo que tarda cada búsqueda.

La configuración personal existente se conserva. Facebook queda desactivado
solo en el ejemplo para instalaciones nuevas. La ubicación de un grupo es una
señal aproximada: `sources.facebook.group_cities` puede mapear id → lista de
ciudades; confirmar la ubicación concreta con el vendedor.

`deals` comparte la selección y añade tasación opcional. Es una vista de consola;
la persistencia, CSV y Telegram corresponden a `run`/`watch`.

---

## Lo primero: Facebook no tiene API para esto

No existe endpoint oficial, ni gratis ni pago:

- La **Graph API nunca expuso Marketplace**. No hay `/marketplace/*`.
- El acceso de apps al **feed de grupos se cerró en 2020** (`/group/feed`).

Lo único que funciona es manejar **tu propia sesión** con un navegador real.
Eso va contra los Términos de Meta: el riesgo real es un checkpoint o bloqueo
de tu cuenta. El proyecto lo soporta, va lento a propósito, y viene
**desactivado por defecto**. Vos decidís.

## Lo segundo: dónde está realmente la mercadería barata

Midiendo sobre ~1000 avisos reales de OLX/RS:

- **La categoría `motos` no tiene nada barato.** El aviso más barato del corpus
  fue R$3.000. Las motos completas y baratas no viven ahí.
- **Las sucatas están en `pecas-e-acessorios`.** Ahí sí: *"Sucata Honda CB 300R
  2014 (em peças)"*, *"Sucata Biz 125 ES 2007"*, *"Sucata Twister carburada"*.
  El scraper busca en **las dos categorías**.
- **"Sucata" NO significa barato.** Las reales van de R$10.000 a R$16.500.
- **Cuidado con el precio-señuelo.** Un desmanche de Estrela lista la misma
  sucata a R$10 y a R$10.000: el R$10 es carnada para que llames. El tool
  marca todo aviso ≤ R$100 como `precio señuelo`.

**No excluyas "sucata" ni "peças" de tus filtros: esa es tu mercadería.** El
clasificador ya separa moto entera de repuesto suelto.

---

## Las dos puntas del puente

Jaguarão y Río Branco (UY) están a 3 km. El mismo presupuesto se dice de tres
maneras, y sin convertir no se puede comparar nada:

> **US$ 200  ≈  R$ 1.029  ≈  $U 8.039**

El tool convierte todo a una moneda base (`fx.base`, por defecto BRL) antes de
filtrar o comparar, pero **muestra igual el número original del vendedor**:

```
R$ 1.024 ($U 8.000)
```

La cotización sale de `open.er-api.com` (gratis, sin API key), se cachea 12 h y
cae a valores fijos si no hay red. Podés fijarla a mano con `fx.rates`.

### Qué hay del lado uruguayo

| Fuente | Estado |
|---|---|
| **Mercado Libre Uruguay** (site `MLU`) | ✅ API oficial, mismas credenciales que Brasil |
| **Grupos de Facebook** de Río Branco / Cerro Largo | ✅ Vía la fuente `facebook` |
| **Gallito.com.uy** | ❌ No conectado, ver abajo |

**Gallito:** su sección de motos no es alcanzable adivinando URLs — la
navegación la construye JavaScript y `/autos/motos` redirige a automóviles.
Las páginas que sí responden (`/inmuebles`, `/autos/automoviles`) vienen
renderizadas del servidor, así que **es scrapeable en cuanto se sepa la URL
correcta**. Si la sacás de tu navegador, conectarla es un rato.

### Antes de cruzar mercadería

Traer una moto entera de Uruguay a Brasil para revenderla implica
**nacionalizarla**, y ese trámite suele costar más que una moto de R$1000. Los
**repuestos** cruzan con muchísima menos fricción que un vehículo con papeles.
Para tu negocio eso empuja a una conclusión concreta: del lado uruguayo
conviene mirar **doadores y repuestos**, no motos documentadas.

## Fuentes

| Fuente | Cómo entra | Estado |
|---|---|---|
| **OLX** | Sin API. Parsea el markup de las tarjetas | ✅ Funcionando, probado |
| **Mercado Livre** | API oficial documentada | ⚠️ OAuth y acceso pendientes de verificar |
| **Facebook** | Navegador automatizado, sesión propia | ⚠️ Opcional, off por defecto |

## Cómo tasa

```
aviso -> clasificar -> precio de referencia -> margen
```

1. **Clasificar** (`appraise.py`). Decide si es `moto` / `accessory` / `car` y
   en qué estado: `runner` (anda), `project` (no anda), `parts` (doador).
   Saca modelo y año del título.
2. **Referencia** (`market.py`). Mediana de los precios publicados recogidos por ese
   modelo andando en la región. No usa tabla FIPE: la FIPE no cubre sucata ni
   el precio real de la calle. Se autocalibra en cada corrida.
3. **Margen**:
   ```
   margen = referencia x resale_factor - lo_que_pagás - arreglo - riesgo_papeles
   ```

Tus números van en `economics` (config):

```yaml
budget: 1000
economics:
  repair_runner: 300      # anda: revisión, fluidos
  repair_project: 1200    # no anda: motor / eléctrica
  repair_parts: 2500      # armarla entera desde un doador
  resale_factor: 0.85     # vendés abajo del aviso promedio
  no_docs_resale_factor: 1.0   # papeles: no penaliza, solo avisa
  min_margin: 0           # no filtra nada
```

### Lee las descripciones

El título miente por omisión. Dos avisos reales de Jaguarão lo prueban:

| Título | Precio | Descripción | Qué era en realidad |
|---|---|---|---|
| `A+pedido+` | R$500 | *"Vendo moto pra retirada de peças ou pra alguém que queira arrumar, a moto anda, não tem carteirinha, sem piscas"* | **Anda**, sin documento. Compra real. |
| `Biz+Uruguai+` | R$450 | *"A pedido alguma biz a venda"* | **No vende: está buscando comprar.** |

Por eso `deals` abre la página de cada candidata. Solo de las candidatas —las
que ya pasaron precio y zona—, así son pocas requests y puede ir despacio.

- **OLX**: la descripción está en el `ld+json` de la página de detalle.
- **Facebook**: se lee del cuerpo, después del marcador `Detalles` / `Detalhes`
  (la página cambia de idioma según la cuenta).

Con eso el clasificador ve el texto completo y detecta tres cosas que el título
nunca dice:

1. **Avisos que buscan comprar** (`procuro`, `a pedido`, `busco`). Ojo: los dos
   casos de arriba usan *"a pedido"*, así que la palabra sola no alcanza — un
   `vendo` explícito le gana siempre.
2. **El estado real**: *"retirada de peças"* convirtió a `A+pedido+` de
   `runner` a `parts`.
3. **Estado de los papeles**: `não tem carteirinha`, `só pra rodar`,
   `sinistrada`, `sem placa`, `IPVA atrasado` — y también en positivo:
   `com carteirinha`, `em dia pago`, `sin deudas`, `a mi nombre`. Ese
   vocabulario salió de leer avisos reales de Jaguarão, no de suponer.

   **Los papeles no descuentan el margen**: la marca aparece como aviso y la
   decisión es tuya. Si querés que sí descuente, bajá `no_docs_resale_factor`
   (ej. `0.7`) — no como castigo por riesgo, sino porque la referencia se
   calcula con motos documentadas y una sin papeles no se vende a ese precio.

Se salta con `--no-enrich` si querés una pasada rápida.

### Lo que NO hace

- **No inventa precios.** Si no hay suficientes motos del mismo modelo andando
  en la muestra, dice `SIN TASAR` y te la muestra para que la mires a ojo.
  Antes comparaba una XTZ 125 contra una Ténéré 250 y daba un margen fantasma
  de R$6.700; separar cilindradas lo arregló.
- **No verifica nada de lo que dice el vendedor.** Que la descripción no
  mencione problemas de papeles no significa que no los tenga.

### Una guarda que hubo que acotar

Para frenar repuestos que nombran el modelo (*"Escape Inox CBR 600F"*) había una
regla: si una moto *andando* cuesta menos del 25% de su referencia, es un
repuesto. Suena razonable y estaba **mal para este negocio**: una Biz a R$450
contra una referencia de R$12.500 quedaba descartada, cuando es exactamente la
compra que se busca. Comprar al 5% del valor de mercado no es una anomalía acá,
es el objetivo.

Ahora la guarda corre **solo en la categoría de repuestos de OLX**, que es de
donde salían los falsos positivos.

### Clusters para armar

Dos o más doadores del mismo modelo = podés armar una entera. Suele ser el caso
de más margen: dos motos muertas iguales cuestan menos que una viva.

```
PARA ARMAR:
  kawasaki-ninja: 6 unidades, total R$ 101.000
  yamaha-mt: 2 unidades, total R$ 28.500
```

### OLX — lo que descubrí probando contra el sitio real

Cuatro cosas que no están en ninguna doc y que cuestan horas si las buscás solo:

1. **`requests` recibe 403 siempre.** OLX filtra por *fingerprint TLS/HTTP2*,
   no por User-Agent. Se resuelve con `curl_cffi` (`impersonate="chrome"`).
2. **El truco viejo del `__NEXT_DATA__` murió.** OLX ya no es Next.js. Ahora se
   parsea `<section class="olx-adcard">`, que es estable y semántico.
3. **El slug de municipio se ignora en silencio.** `.../estado-rs/jaguarao`
   devuelve el estado entero, con Porto Alegre copando todo. El de **región sí
   funciona**: `estado-rs/regioes-de-pelotas-rio-grande-e-bage` → 150 avisos del
   sur, 4 de Jaguarão en 3 páginas. Sin región, Jaguarão salía 1 vez cada 400.
4. **Hay que paginar** con `&o=N` (50 avisos por página).

### Mercado Livre

Desde 2024 los endpoints de búsqueda piden Bearer token. Creá una app gratis en
[developers.mercadolivre.com.br](https://developers.mercadolivre.com.br) y poné
`client_id` / `client_secret` en la config. Sin eso la fuente avisa y sigue
con las demás en vez de romper la corrida.

---

## Instalación

```bash
pip install -r requirements.txt
patchright install chromium      # solo si vas a usar Facebook
python -m motoradar init         # crea config.yaml
```

## Uso

```bash
python -m motoradar deals                  # ** la vista del flipper **
python -m motoradar deals --budget 3000    # pisa el presupuesto
python -m motoradar run                    # búsqueda simple, sin tasar
python -m motoradar watch --interval 30    # cada 30 minutos
python -m motoradar login                  # sesión de Facebook (una vez)
```

`deals` muestra **una sola lista, ordenada por precio**, con todo lo que entra
en tu presupuesto. El margen aparece como dato extra cuando se puede calcular,
**nunca como filtro**.

Hubo una versión que partía la salida en "dan margen" / "no llegan al margen" /
"sin tasar", y estaba mal: una moto de R$400 que el tool no supiera tasar
quedaba sepultada en la tercera sección. El criterio de compra es el
presupuesto, no el margen.

En `run` y `watch`, los eventos nuevos van a consola, a `data/hits.csv` y a Telegram si lo configuraste. `deals` muestra resultados y tasaciones en consola.

## Configuración

Todo vive en `config.yaml` (ver `config.example.yaml`). Lo que más vas a tocar:

```yaml
filters:
  queries: ["moto", "biz", "titan", "cg 125"]
  max_price: 1000          # subilo a 5000-8000 para ver motos usables
  cities: ["jaguarao", "rio branco", "arroio grande"]
  exclude_keywords: ["capacete", "roubada", "furtada"]
```

Ciudades y keywords se comparan **sin acentos**: `jaguarao` matchea `Jaguarão`.

### Telegram

Hablá con `@BotFather`, `/newbot`, guardá el token. Para el `chat_id`:
mandale un mensaje a tu bot y abrí
`https://api.telegram.org/bot<TOKEN>/getUpdates`.

Poné token y chat_id en la config, o como variables de entorno
`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` (las variables ganan).

### Facebook

```bash
python -m motoradar login    # abre el navegador, iniciás sesión a mano
python -m motoradar status   # ¿la sesión sigue viva?
```

**El proyecto nunca pide, usa ni guarda tu contraseña.** `login` abre un
Chromium visible y vos la escribís en la página de Facebook, como siempre. Lo
único que queda en disco es la cookie de sesión, dentro del perfil de Chromium.
La terminal te avisa sola cuando detecta la cookie `c_user` (la que Facebook
pone recién cuando hay sesión real); no tenés que confirmar nada.

Aprovechá y **fijá la ubicación de Marketplace en Jaguarão** con el radio que
quieras: el perfil queda guardado en `~/.motoradar/fb-profile` y se recuerda.
Después poné `enabled: true` en la config.

Usa **patchright** (fork stealth de Playwright) en vez de Playwright normal,
que Facebook detecta por el fingerprint de CDP y te manda a checkpoint.

En un pueblo de 30.000 habitantes **los grupos de compra/venta rinden mucho
más que Marketplace**. Poné sus ids en `groups` — es la parte que más resultados
te va a dar.

Para sacar el id: abrí el grupo, mirá la URL. Si es
`facebook.com/groups/1234567890/` el id es ese número; si es un nombre
(`facebook.com/groups/compraevendajaguarao/`), ese texto también sirve.

**Usá tu cuenta real para esto.** Los grupos de pueblo tienen admins que
aprueban a mano y conocen a los vecinos: un perfil nuevo sin historial ni
amigos en común no entra, y si no entra no hay nada que leer.

---

## Cómo está armado

```
motoradar/
  cli.py          run / deals / watch / login / init
  appraise.py     clasificador: moto vs repuesto, estado, modelo, año, riesgos
  market.py       precios de referencia, margen, clusters para armar
  config.py       carga config.yaml, variables de entorno pisan secretos
  models.py       Listing, parseo de precios ("R$ 1.250,00" -> 1250.0)
  filters.py      precio, ciudad, keywords — sin acentos
  store.py        SQLite. Identidad por fuente/id, estado actualizado,
                  observaciones y entregas persistentes
  notify.py       consola, CSV, Telegram
  sources/
    base.py           protocolo Source + SourceError
    olx.py            parser de tarjetas + curl_cffi + paginado
    mercadolivre.py   API oficial; OAuth actual pendiente de verificar
    facebook.py       patchright, perfil persistente, marketplace + grupos
```

Una fuente que falla **no tumba la corrida**: se loguea el error y siguen las
otras. Por eso Mercado Livre sin credenciales simplemente avisa.

### Agregar una fuente

Implementá `fetch(cfg, opts) -> Iterable[Listing]` y registrala en
`sources/__init__.py`. Candidatas obvias: Webmotors, iCarros, Mercado Livre de
Uruguay (Río Branco está cruzando el puente).

---

## Proyectos parecidos

Los miré antes de escribir esto y dos me corrigieron decisiones:

- [`evanoseen/fb-car-bot`](https://github.com/evanoseen/fb-car-bot) — Marketplace
  → Telegram, misma arquitectura. **De acá salió lo de patchright.**
- [`devAlphaSystem/OLX-Search-CLI`](https://github.com/devAlphaSystem/OLX-Search-CLI)
  — OLX en Node. Usa impersonación de fingerprint TLS: **confirmó por qué
  `requests` daba 403.**
- [`passivebot/facebook-marketplace-scraper`](https://github.com/passivebot/facebook-marketplace-scraper)
  — el más popular (~400 estrellas), Playwright + Streamlit. Sin actualizar
  desde 2024.
- [`gabrfern99/gpu-radar`](https://github.com/gabrfern99/gpu-radar) — radar de
  precios en OLX que aprende el precio de mercado y puntúa cada aviso. Buena
  idea para robar más adelante.
