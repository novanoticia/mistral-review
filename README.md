# mistral-review

Revisor de pull requests con la API de Mistral (u, opcionalmente,
[OpenRouter](#usar-openrouter-opcional)), **reutilizable desde otros repos**. Cada repo lo llama con un workflow corto; el código vive solo aquí, así
que una mejora se hace una vez y llega a todos.

Es una **segunda opinión barata**, no un detector fiable: en una evaluación con
defectos reales del historial de `email-triage-plugin` no encontró ninguno de
los 7 ([evaluación](docs/evaluacion.md)), aunque sí ve defectos evidentes (caso de control). Sus hallazgos hay que
comprobarlos antes de actuar.

## Qué hace

- Lee el diff del PR con Git (nunca ejecuta su código), la guía del proyecto
  (`REVIEW.md`, `AGENTS.md` o `CLAUDE.md`) y las funciones completas, Python y
  shell, que contienen los cambios.
- Envía al proveedor el título y ese material de revisión. La revisión
  automática omite la descripción del PR.
- Tacha secretos evidentes (`api_key = …`, `token: …`, `password=…`) antes de
  enviar nada a Mistral y antes de publicar.
- Pide un JSON con hallazgos (archivo, cita literal, escenario, arreglo) y de 1
  a 6 comprobaciones. Cada hallazgo debe citar una línea completa del diff o
  varias líneas seguidas de un mismo bloque de cambios. Una cita inválida de
  un hallazgo hace que se rechace la respuesta.
- Cada comprobación «revisado» cita una línea añadida o eliminada completa de
  su archivo en el diff enviado. Si falta una cita válida, la comprobación
  queda «pendiente» y se conservan los demás resultados válidos.
- Si la respuesta llega mal formada o cortada por `length`, la pide una vez más.
  Si la segunda tampoco vale, el comentario nombra la regla incumplida (solo su
  código, nunca el contenido de la respuesta).
- Publica un comentario por commit revisado, con el modelo y un enlace a la
  ejecución. Editar el PR no repite la revisión; «Re-run jobs» sí.

Historia y detalles del comportamiento (por qué se repite una solicitud, un
informe por commit, modelos que razonan…): [docs/revision.md](docs/revision.md).

## Cómo usarlo en un repo

1. Copia [`ejemplos/mistral-review.yml`](ejemplos/mistral-review.yml) en
   `.github/workflows/mistral-review.yml` del repo.
2. En ese repo, **Settings → Secrets and variables → Actions**:
   - pestaña **Secrets** → **New repository secret**: `MISTRAL_API_KEY` con la
     clave de Mistral;
   - pestaña **Variables** → **New repository variable** (opcional):
     `MISTRAL_MODEL`, por ejemplo `zai-glm-5-3-flash`.
3. Abre un PR: aparecerá el check `mistral-review / mistral-review` y, al
   terminar, un comentario con la revisión.

Para revisar con un modelo de OpenRouter en vez de Mistral, ver
[Usar OpenRouter](#usar-openrouter-opcional): solo variables y un secreto.

### Parámetros (`with:`)

| Parámetro | Para qué | Por defecto |
|---|---|---|
| `model` | Modelo. Con OpenRouter, **obligatorio** (`organización/modelo`); el ejemplo lo toma de `MISTRAL_MODEL` u `OPENROUTER_MODEL` según el proveedor | con Mistral, `zai-glm-5-3-flash` |
| `provider` | `mistral` u `openrouter` | vacío = `mistral` |
| `openrouter_provider` | Solo con OpenRouter: proveedor final fijo (p. ej. `z-ai`) | vacío = lo elige OpenRouter |
| `excluded` | Archivos que no se revisan (generados), uno por línea | ninguno |
| `guides` | Archivos de guía, por orden de preferencia | `REVIEW.md`, `AGENTS.md`, `CLAUDE.md` |
| `ref` | Rama o etiqueta de este repo cuyo script se ejecuta | `main` |

`ref` debe coincidir con lo que pongas tras `@` en `uses:` (por ejemplo `@v1`
y `ref: v1`): así un cambio aquí no llega a todos los repos sin control.

### Secretos (`secrets:`)

| Secreto | Cuándo es obligatorio |
|---|---|
| `MISTRAL_API_KEY` | Con `provider` vacío o `mistral` |
| `OPENROUTER_API_KEY` | Con `provider: openrouter` |

Cada proveedor lee solo su clave: con Mistral no hace falta la de OpenRouter,
ni al revés. Si falta la del proveedor elegido, la revisión se omite sin
fallar (es lo que pasa en los PR de forks, que no reciben secretos); el
resumen de Actions dice qué secreto falta.

## Usar OpenRouter (opcional)

[OpenRouter](https://openrouter.ai) es una **pasarela**: una sola clave y una
sola API para modelos de muchas empresas. Mistral sigue siendo el proveedor
por defecto; OpenRouter sirve para usar o probar modelos que Mistral no tiene.

Si el workflow de tu repo ya tiene las líneas de proveedor (como los de
[`ejemplos/`](ejemplos/)), **no hay que tocar ningún archivo**: el proveedor se
elige con variables del repo. Si no las tiene, mira antes
[Si tu workflow no tiene las líneas de proveedor](#si-tu-workflow-no-tiene-las-líneas-de-proveedor).

### Qué se configura

Todo en el repo que se revisa: **Settings → Secrets and variables → Actions**.

| Nombre | Tipo | Valor de ejemplo | Para qué |
|---|---|---|---|
| `MISTRAL_PROVIDER` | Variable | `openrouter` | El interruptor: `openrouter` = OpenRouter; sin ella o con `mistral` = Mistral |
| `OPENROUTER_MODEL` | Variable | `z-ai/glm-5.3-flash` | Modelo de OpenRouter, con la forma `organización/modelo` |
| `OPENROUTER_PROVIDER` | Variable (opcional, recomendada) | `z-ai` | Empresa que ejecuta el modelo; fijarla hace las revisiones comparables |
| `OPENROUTER_API_KEY` | Secreto | la clave | Clave de OpenRouter |
| `MISTRAL_MODEL` | Variable (ya existía) | `zai-glm-5-3-flash` | Modelo de Mistral; con OpenRouter no se usa |

El prefijo `MISTRAL_` de `MISTRAL_PROVIDER` es histórico: el revisor nació
solo con Mistral. La variable decide el proveedor, sea cual sea.

### Activarlo, paso a paso

1. **Crea la clave** en [openrouter.ai/settings/keys](https://openrouter.ai/settings/keys)
   y ponle un **límite de crédito** pequeño. Recarga crédito en
   **openrouter.ai → Credits**: OpenRouter cobra por adelantado.
2. **Elige el modelo** en [openrouter.ai/models](https://openrouter.ai/models).
   Copia su identificador exacto (el que aparece bajo el nombre, por ejemplo
   `z-ai/glm-5.3-flash`). En esa misma página están las empresas que lo
   sirven, con su precio; elige una y copia su identificador con el botón de
   copiar que hay junto a su nombre (por ejemplo `z-ai`; alguno lleva una
   variante, como `deepinfra/fp8`).
3. En GitHub, en el repo: **Settings → Secrets and variables → Actions**.
   - Pestaña **Secrets → New repository secret**: nombre `OPENROUTER_API_KEY`,
     valor la clave.
   - Pestaña **Variables → New repository variable**, tres veces:
     `OPENROUTER_MODEL` = el modelo del paso 2;
     `OPENROUTER_PROVIDER` = la empresa del paso 2;
     `MISTRAL_PROVIDER` = `openrouter`.
4. **Comprueba** que funciona: abre un PR, o en uno abierto pulsa **Actions →
   la ejecución de «Mistral PR Review» → Re-run jobs**. El comentario debe
   empezar por «Revisión vía OpenRouter (`z-ai/glm-5.3-flash`, proveedor
   `z-ai`)». Puede tardar varios minutos (con Mistral, segundos).

### Cambiar de proveedor

Solo se toca `MISTRAL_PROVIDER` (en **Variables**, el lápiz para editarla):

| Quieres | `MISTRAL_PROVIDER` | Modelo que se usa |
|---|---|---|
| Mistral | `mistral` (o borrar la variable) | `MISTRAL_MODEL` (sin ella, `zai-glm-5-3-flash`) |
| OpenRouter | `openrouter` | `OPENROUTER_MODEL` |

Las demás variables y el secreto pueden quedarse puestos: con Mistral se
ignoran (`OPENROUTER_PROVIDER` con un aviso en el registro).

Afecta a todo lo que el repo lance con los workflows de los ejemplos:
revisión de PR, contraste (`mistral-reply.yml`) y evaluación
(`mistral-eval.yml`). En el
contraste, `MISTRAL_REPLY_MODEL` es un modelo de Mistral y solo se usa con
Mistral; con OpenRouter el contraste usa el modelo de la revisión.

### Si algo falla

El revisor comprueba la configuración **antes de llamar a ninguna API**: un
error de configuración no gasta crédito. El mensaje sale en el registro del
check y en el resumen de la ejecución en Actions.

| Mensaje | Causa | Arreglo |
|---|---|---|
| «con OpenRouter el modelo es obligatorio» | `MISTRAL_PROVIDER=openrouter` sin `OPENROUTER_MODEL` | Crear `OPENROUTER_MODEL` |
| «el modelo de OpenRouter debe tener la forma `organización/modelo`» | Un nombre de Mistral (`zai-glm-5-3-flash`) en `OPENROUTER_MODEL` | Copiar el identificador de openrouter.ai/models |
| «con Mistral el modelo no lleva `/`» | Un modelo de OpenRouter en `MISTRAL_MODEL` | Devolver `MISTRAL_MODEL` a un modelo de Mistral |
| «proveedor no reconocido» | `MISTRAL_PROVIDER` con otro valor (errata) | `mistral` u `openrouter` |
| «los enrutadores de OpenRouter cambian de modelo…» | `OPENROUTER_MODEL=openrouter/auto` o similar | Un modelo concreto |
| La revisión se omite y el resumen dice que falta un secreto | Falta `OPENROUTER_API_KEY` | Crear el secreto (paso 3) |
| Error 503 de OpenRouter | Ningún proveedor del modelo cumple las condiciones de abajo (por ejemplo, el de `OPENROUTER_PROVIDER` no admite el modo JSON) | Otro proveedor de la página del modelo, u otro modelo |
| Error 404 de OpenRouter | El modelo no existe (errata en `OPENROUTER_MODEL`) | Copiar el identificador otra vez |
| Error 402 de OpenRouter | Sin crédito, o la clave llegó a su límite | Recargar crédito o subir el límite de la clave |

### Si tu workflow no tiene las líneas de proveedor

Compara el workflow del repo con
[`ejemplos/mistral-review.yml`](ejemplos/mistral-review.yml). Si le faltan,
edítalo (icono del lápiz) para que `with:` y `secrets:` queden así:

```yaml
    with:
      model: ${{ vars.MISTRAL_PROVIDER == 'openrouter' && vars.OPENROUTER_MODEL || vars.MISTRAL_PROVIDER != 'openrouter' && vars.MISTRAL_MODEL || '' }}
      provider: ${{ vars.MISTRAL_PROVIDER }}
      openrouter_provider: ${{ vars.OPENROUTER_PROVIDER }}
    secrets:
      MISTRAL_API_KEY: ${{ secrets.MISTRAL_API_KEY }}
      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
```

`uses:` y `ref` deben apuntar a `v1.2` o posterior: con una versión anterior,
GitHub rechaza el workflow si recibe `provider` u `OPENROUTER_API_KEY`. Lo
mismo en `mistral-reply.yml` y `mistral-eval.yml` si los usas (sus ejemplos
tienen las líneas completas).

### Cómo se configura la petición

Fijo, no se elige:

- `require_parameters`: solo proveedores que admiten todo lo que se pide.
  OpenRouter ignora en silencio un parámetro no admitido; sin esto el modo
  JSON podría desaparecer sin aviso.
- `allow_fallbacks: false`: si el proveedor elegido falla, la revisión falla;
  no salta a otro.
- `data_collection: "deny"`: solo proveedores que, según OpenRouter, no
  guardan los datos.
- Sin `temperature` ni `reasoning_effort` (con Mistral sí se envían): con
  `require_parameters`, un modelo que no admita alguno se quedaría sin
  proveedor. Los modelos que razonan usan su esfuerzo por defecto; la
  evaluación puede fijar otro con `esfuerzo`.
- No se admiten los enrutadores de OpenRouter (`openrouter/auto` y
  similares): eligen un modelo distinto en cada petición.

### Privacidad, costes y límites

- **Privacidad.** El título, el diff, la guía y el contexto del PR pasan por
  **dos empresas**: OpenRouter y el proveedor final, cada una con sus
  condiciones.
  `data_collection: "deny"` filtra según lo que OpenRouter sabe de cada
  proveedor, que según su propia documentación no es definitivo. Los secretos
  evidentes se tachan antes de enviar, igual que con Mistral.
- **Costes.** El precio depende del proveedor final: el mismo modelo puede
  costar varias veces más en uno que en otro. Sin `OPENROUTER_PROVIDER`,
  OpenRouter elige en cada petición. `z-ai/glm-5.3-flash` servido por `z-ai`
  cuesta lo mismo que en Mistral, del orden de medio céntimo por revisión. La
  evaluación muestra el coste que declara la API (`usage.cost`, en créditos).
- **Sin `OPENROUTER_PROVIDER`**, dos revisiones del mismo PR pueden salir de
  proveedores (y versiones comprimidas) distintos: no son comparables.
- **Más lento:** en las pruebas reales, entre 4 y 10 minutos por revisión,
  frente a segundos con Mistral.
- Un modelo que agota el tope de salida razonando se pide una segunda vez,
  como con Mistral; la segunda solicitud también se cobra.
- Resultados medidos con OpenRouter (GLM 5.3 Flash y Qwen3 Coder Next):
  [docs/evaluacion.md](docs/evaluacion.md).

## Contraste de revisiones de otras IAs (opcional)

`reply.yml` contrasta por lectura los hallazgos que dejan en un PR otras IAs
(Codex, Claude, Copilot, CodeRabbit, Gemini) o comentarios firmados como
asistidos por Claude. Responde con un dictamen por hallazgo —de acuerdo, no se
sostiene, ya corregido o pendiente—, con citas literales del código. Solo
comenta: no aplica parches. Un rechazo de la respuesta del modelo va al
resumen de Actions, no al PR.

Para activarlo, copia [`ejemplos/mistral-reply.yml`](ejemplos/mistral-reply.yml)
en `.github/workflows/` del repo. Usa el mismo secreto y, opcionalmente, la
variable `MISTRAL_REPLY_MODEL` para probar otro modelo de Mistral solo aquí
(con OpenRouter no se usa). También se
puede lanzar a mano (**Actions → Mistral Reply to AI Reviews → Run workflow**)
con el número del PR y el id del comentario.

Procede de `email-triage-plugin`; su historia y sus límites están en el
docstring de [`scripts/mistral_reply.py`](scripts/mistral_reply.py).

## Evaluación con casos reales (opcional)

`eval.yml` mide si el revisor encuentra defectos de verdad, con commits del
historial del repo: positivos (un defecto conocido y su arreglo), negativos
(hallazgos falsos de antes) y un caso de control evidente. Es manual: cada
ejecución cuesta dinero. Para activarlo, copia
[`ejemplos/mistral-eval.yml`](ejemplos/mistral-eval.yml) en `.github/workflows/`
y los casos en `.github/eval/casos.json` (plantilla:
[`ejemplos/eval-casos.json`](ejemplos/eval-casos.json)). Método, límites y
resultados medidos en `email-triage-plugin`: [docs/evaluacion.md](docs/evaluacion.md).

## Coste

Con `zai-glm-5-3-flash` (0,15 / 0,50 USD por millón de tokens de entrada /
salida), del orden de **medio céntimo por revisión**. Con OpenRouter depende
del modelo y del proveedor final ([costes](#usar-openrouter-opcional)). Los PR
de forks no reciben el secreto: la revisión se omite sin fallar.

## Desarrollo

```bash
python3 -m unittest discover -s tests -t .
```

Solo biblioteca estándar de Python, sin red ni API en los tests.

Procedencia: fusiona las cuatro copias que tenían `email-triage-plugin`
(base), `claude-skills-exporter`, `multilang-repo-skill` y `repo-auditor-ia`
(contexto de shell y `mask_secrets`). El contraste de respuestas a otras IAs
(`mistral_reply.py`) y la evaluación (`mistral_eval.py`) se trajeron después,
también de `email-triage-plugin`.

---

*Texto elaborado con asistencia de IA; requiere revisión humana.*
