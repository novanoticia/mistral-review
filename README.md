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
- Tacha secretos evidentes (`api_key = …`, `token: …`, `password=…`) antes de
  enviar nada a Mistral y antes de publicar.
- Pide un JSON con hallazgos (archivo, cita literal, escenario, arreglo) y de 1
  a 6 comprobaciones. Una cita vale si es una línea completa del diff o varias
  líneas seguidas de un mismo hunk; si no, la revisión se rechaza.
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

### Parámetros (`with:`)

| Parámetro | Para qué | Por defecto |
|---|---|---|
| `model` | Modelo. Con OpenRouter, **obligatorio** (`organización/modelo`) | con Mistral, `zai-glm-5-3-flash` |
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

[OpenRouter](https://openrouter.ai) es una **pasarela**: una sola API y una
sola clave para modelos de muchas empresas. No es un modelo: el modelo se
elige aparte (`model`), y detrás de cada modelo puede haber varios
**proveedores finales** (las empresas que lo ejecutan), con precios,
versiones comprimidas (cuantización) y políticas de datos distintas. Mistral
sigue siendo el proveedor por defecto; OpenRouter sirve sobre todo para
probar modelos que Mistral no ofrece.

**Requisito:** una versión de mistral-review que incluya OpenRouter (la
primera etiqueta publicada después de `v1.1`). Con una versión anterior,
GitHub rechaza el workflow si le pasas `provider` u `OPENROUTER_API_KEY`.

Pasos, desde la web de GitHub en el repo que se revisa:

1. Crea una clave en [openrouter.ai/settings/keys](https://openrouter.ai/settings/keys).
   Conviene ponerle un **límite de gasto** al crearla.
2. **Settings → Secrets and variables → Actions → Secrets → New repository
   secret**: nombre `OPENROUTER_API_KEY`, valor la clave.
3. Pestaña **Variables → New repository variable**:
   - `MISTRAL_PROVIDER` = `openrouter`;
   - `MISTRAL_MODEL` = el identificador exacto del modelo en OpenRouter, con
     la forma `organización/modelo` (por ejemplo `z-ai/glm-5.3-flash`); se
     copia de la página del modelo en [openrouter.ai/models](https://openrouter.ai/models);
   - opcional, `OPENROUTER_PROVIDER` = el proveedor final, para que todas las
     revisiones salgan del mismo (por ejemplo `z-ai`); la página del modelo
     en OpenRouter lista sus proveedores.
4. En `.github/workflows/mistral-review.yml`, edita el archivo (icono del
   lápiz) y añade, como en [`ejemplos/mistral-review.yml`](ejemplos/mistral-review.yml):
   ```yaml
       with:
         provider: ${{ vars.MISTRAL_PROVIDER }}
         openrouter_provider: ${{ vars.OPENROUTER_PROVIDER }}
       secrets:
         OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
   ```
   (junto a lo que ya hay en `with:` y `secrets:`; `uses:` y `ref` deben
   apuntar a la versión con OpenRouter). Lo mismo en `mistral-reply.yml` y
   `mistral-eval.yml` si los usas.

El comentario del PR dirá «Revisión vía OpenRouter (`modelo`)». Si el modelo
falta o tiene forma de identificador de Mistral (`zai-glm-5-3-flash`), el
check falla con un mensaje claro **sin llamar a ninguna API** ni cambiar de
modelo por su cuenta.

**Volver a Mistral:** en **Variables**, borra `MISTRAL_PROVIDER` (o déjala en
`mistral`) y devuelve `MISTRAL_MODEL` a un modelo de Mistral (o bórrala para
usar `zai-glm-5-3-flash`). **Las dos cosas**: un identificador de OpenRouter
enviado a Mistral da error HTTP 400. `OPENROUTER_PROVIDER` se ignora con un
aviso y el secreto puede quedarse.

**Cómo se configura la petición** (fijo, no se elige):

- `require_parameters`: solo proveedores que admiten todo lo que se pide.
  OpenRouter ignora en silencio un parámetro no admitido; sin esto el modo
  JSON podría desaparecer sin aviso.
- `allow_fallbacks: false`: si el proveedor elegido falla, la revisión falla;
  no salta a otro.
- `data_collection: "deny"`: solo proveedores que, según OpenRouter, no
  guardan los datos.

**Privacidad.** Con OpenRouter el diff, la guía y el contexto del PR pasan
por **dos empresas**: OpenRouter y el proveedor final, cada una con sus
condiciones. `data_collection: "deny"` filtra según lo que OpenRouter sabe de
cada proveedor, que según su propia documentación no es una fuente definitiva.
Los secretos evidentes se tachan antes de enviar, igual que con Mistral.

**Costes.** OpenRouter cobra por adelantado (créditos) y el precio depende
del proveedor final: el mismo modelo puede costar varias veces más en uno que
en otro. Sin `openrouter_provider`, OpenRouter elige en cada petición y el
coste varía. La evaluación muestra el coste que declara la API (`usage.cost`,
en créditos); con Mistral lo dice como no disponible y no lo estima.

**Limitaciones conocidas:**

- No se ha ejecutado aún ninguna revisión real con OpenRouter.
- No hay modelo por defecto: cada repo elige el suyo.
- Sin `openrouter_provider`, dos revisiones del mismo PR pueden salir de
  proveedores (y cuantizaciones) distintos: no son comparables entre sí.
- Si ningún proveedor del modelo cumple las condiciones de arriba (por
  ejemplo, admitir el modo JSON), OpenRouter responde con error y la revisión
  falla de forma visible: hay que elegir otro modelo o proveedor.
- Un modelo que agota el tope de salida razonando se pide una segunda vez,
  como con Mistral; OpenRouter avisa de que en ese caso repetir no suele
  ayudar, y la segunda solicitud también se cobra.

## Contraste de revisiones de otras IAs (opcional)

`reply.yml` contrasta por lectura los hallazgos que dejan en un PR otras IAs
(Codex, Claude, Copilot, CodeRabbit, Gemini) o comentarios firmados como
asistidos por Claude. Responde con un dictamen por hallazgo —de acuerdo, no se
sostiene, ya corregido o pendiente—, con citas literales del código. Solo
comenta: no aplica parches. Un rechazo de la respuesta del modelo va al
resumen de Actions, no al PR.

Para activarlo, copia [`ejemplos/mistral-reply.yml`](ejemplos/mistral-reply.yml)
en `.github/workflows/` del repo. Usa el mismo secreto y, opcionalmente, la
variable `MISTRAL_REPLY_MODEL` para probar otro modelo solo aquí. También se
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
