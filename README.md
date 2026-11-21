# mistral-review

Revisor automático de pull requests con la API de Mistral (u, opcionalmente,
OpenRouter), **reutilizable desde cualquier repo**. Cada repo lo llama con un
workflow corto; el código vive solo aquí, así que una mejora se hace una vez y
llega a todos.

Cuando abres o actualizas un PR, deja un comentario con los defectos que ve,
cada uno con una cita literal del diff, y con las comprobaciones que intentó.

> **Qué esperar.** Es una **segunda opinión barata** (del orden de medio
> céntimo por revisión), no un detector fiable. En una evaluación con 7
> defectos reales del historial de `email-triage-plugin` no encontró ninguno,
> aunque sí ve defectos evidentes ([evaluación](docs/evaluacion.md)).
> Comprueba sus hallazgos antes de actuar.

**Índice:** [Empezar](#empezar-en-5-minutos) ·
[Qué verás en el PR](#qué-verás-en-el-pr) ·
[Configuración](#configuración) ·
[Los tres workflows](#los-tres-workflows) ·
[Qué datos salen del repo](#qué-datos-salen-del-repo) ·
[Si algo falla](#si-algo-falla) ·
[Versiones](#versiones) ·
[Usar OpenRouter](#usar-openrouter-opcional) ·
[Desarrollo](#desarrollo)

## Empezar en 5 minutos

Todo desde la web de GitHub, en el repo que quieres revisar:

1. **Copia el workflow.** Crea el archivo `.github/workflows/mistral-review.yml`
   (**Add file → Create new file**) y pega el contenido de
   [`ejemplos/mistral-review.yml`](ejemplos/mistral-review.yml).
2. **Fija la versión (recomendado).** En ese archivo cambia `@main` por la
   última [versión publicada](https://github.com/novanoticia/mistral-review/releases)
   (por ejemplo `@v1.5`) y `ref: main` por la misma etiqueta (`ref: v1.5`).
   Las dos siempre iguales. Pulsa **Commit changes…**.
3. **Añade la clave.** **Settings → Secrets and variables → Actions →
   pestaña Secrets → New repository secret**: nombre `MISTRAL_API_KEY`,
   valor tu clave de Mistral.
4. **Pruébalo.** Abre un PR. Aparecerá el check
   `mistral-review / mistral-review` y, en segundos, un comentario con la
   revisión.

No hace falta nada más: sin variables, el modelo es `zai-glm-5-3-flash`.

## Qué verás en el PR

Un comentario por commit revisado, titulado «Revisión de Mistral AI», con el
modelo, el commit y un enlace a la ejecución. Debajo:

- **Resultado**: cuántos hallazgos, cuántas comprobaciones pendientes y
  cuántas sin defecto aparente.
- **Hallazgos**: severidad, archivo, problema, un escenario concreto
  (entrada → resultado incorrecto), la línea citada del diff y el arreglo
  mínimo. Una cita que no está en el diff invalida la respuesta.
- **Comprobaciones**: de 1 a 6 intentos de romper el cambio. «Revisado»
  exige citar una línea del diff; sin cita, queda «pendiente» y dice qué
  falta para decidir.
- **«Revisión inconclusa»** si no hay hallazgos y todas las comprobaciones
  quedaron pendientes: no se pudo verificar nada, revísalo a mano.

Editar el título o la descripción no repite la revisión de un commit ya
revisado; **Actions → la ejecución → Re-run jobs** sí. Cómo decide cada cosa y
por qué: [docs/revision.md](docs/revision.md).

## Configuración

### Variables y secretos

Todo en el repo revisado: **Settings → Secrets and variables → Actions**.

| Nombre | Tipo | ¿Hace falta? | Para qué |
|---|---|---|---|
| `MISTRAL_API_KEY` | Secreto | Sí, con Mistral | Clave de la API de Mistral |
| `MISTRAL_MODEL` | Variable | No | Modelo de Mistral; vacío = `zai-glm-5-3-flash` |
| `MISTRAL_REPLY_MODEL` | Variable | No | Otro modelo de Mistral solo para el [contraste](#contrastar-las-revisiones-de-otras-ias); vacío = `MISTRAL_MODEL` |
| `MISTRAL_PROVIDER` | Variable | No | `openrouter` para usar [OpenRouter](#usar-openrouter-opcional); vacío o `mistral` = Mistral |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `OPENROUTER_PROVIDER` | Secreto y variables | Solo con OpenRouter | Ver [OpenRouter](#usar-openrouter-opcional) |

Cada proveedor lee solo su clave. Si falta la del proveedor elegido, la
revisión se omite sin fallar y el resumen de Actions dice qué secreto falta.
Es lo que pasa siempre en los PR que vienen de un fork, porque GitHub no les
entrega secretos.

Sobre `MISTRAL_REPLY_MODEL`: en una prueba real, `codestral-latest` devolvió
más dictámenes que hallazgos y el contraste se rechazó; con
`zai-glm-5-3-flash` funcionó. Si lo cambias, pruébalo antes con un caso
conocido ([cómo](#contrastar-las-revisiones-de-otras-ias)).

### Parámetros del workflow (`with:`)

Los ejemplos ya traen lo necesario. Solo tocarás `excluded` (y `guides`, si
tu guía tiene otro nombre).

| Parámetro | Para qué | Por defecto |
|---|---|---|
| `excluded` | Rutas que no se revisan (archivos generados, fixtures…), una por línea. `*` también cruza `/` | ninguna, además de las [sensibles](#qué-datos-salen-del-repo) |
| `guides` | Guía del proyecto que se envía al modelo, por orden de preferencia | `REVIEW.md`, `AGENTS.md`, `CLAUDE.md` |
| `ref` | Versión de este repo cuyo script se ejecuta; igual que tras `@` en `uses:` | `main` |
| `model`, `provider`, `openrouter_provider` | Modelo y proveedor; los ejemplos los toman de las variables de arriba | Mistral con `zai-glm-5-3-flash` |

Ejemplo de exclusiones:

```yaml
    with:
      excluded: |
        tests/golden/**
        dist/**
```

La guía (`REVIEW.md`, `AGENTS.md` o `CLAUDE.md`) le da al modelo las reglas
del proyecto, y comprueba si el cambio las incumple. Se envía la primera que
exista y no esté excluida.

## Los tres workflows

La revisión es el principal. Los otros dos son opcionales y se activan igual:
copiando su ejemplo en `.github/workflows/` del repo. Los tres usan los mismos
secretos y variables.

| Workflow | Qué hace | Cuándo se ejecuta | Coste |
|---|---|---|---|
| [`mistral-review.yml`](ejemplos/mistral-review.yml) | Revisa el PR | Solo, en cada PR | ~0,5 céntimos por commit |
| [`mistral-reply.yml`](ejemplos/mistral-reply.yml) | Contrasta lo que comentan otras IAs | Solo, cuando otra IA comenta | ~0,5 céntimos por respuesta |
| [`mistral-eval.yml`](ejemplos/mistral-eval.yml) | Mide si el revisor encuentra defectos reales | Solo a mano | céntimos por ejecución |

Si fijas una versión, hazlo en **todos** los archivos que llamen al revisor.

### Contrastar las revisiones de otras IAs

Cuando Codex, Claude, Copilot, CodeRabbit o Gemini dejan hallazgos en un PR,
`mistral-reply.yml` los contrasta leyendo el código y publica un comentario
«Contraste de Mistral AI» con un dictamen por hallazgo: **de acuerdo**, **no se
sostiene**, **ya corregido** o **pendiente**, con la línea citada. Solo
comenta: no aplica parches. Si la respuesta del modelo no vale, no se publica
nada en el PR y el motivo va al resumen de Actions.

Responde a comentarios publicados **después** de añadir el workflow a la rama
principal. Para contrastar uno anterior (o probar un modelo con un caso que ya
conoces): **Actions → Mistral Reply to AI Reviews → Run workflow**, con el
número del PR y el id del comentario, que es el número que va tras
`discussion_r` en el enlace del comentario. El PR puede estar cerrado.

Su historia y sus límites están en el docstring de
[`scripts/mistral_reply.py`](scripts/mistral_reply.py).

### Evaluar el revisor con casos reales

`mistral-eval.yml` mide si el revisor encuentra defectos de verdad, con
commits del historial del propio repo:

- **positivos**: un commit con un defecto conocido y el que lo arregló;
- **negativos**: hallazgos que una revisión anterior dio y resultaron falsos;
- **control**: un defecto evidente que cualquier revisor debería ver.

Necesita, además del workflow, los casos en `.github/eval/casos.json`
(plantilla: [`ejemplos/eval-casos.json`](ejemplos/eval-casos.json)). Por
defecto, el objetivo con el que se comparan las citas sale del **código
Python** que tocó el arreglo. En repos de shell, YAML o Markdown, añade al
caso `archivos_objetivo` con los archivos del defecto; sin él, el listado lo
rechaza («positivo sin objetivo derivable»). Se lanza
desde **Actions → Mistral Eval → Run workflow**, donde eliges repeticiones,
casos, modelo y esfuerzo. Método, cómo elegir los casos y resultados medidos:
[docs/evaluacion.md](docs/evaluacion.md).

## Qué datos salen del repo

Se envían al proveedor (Mistral, u OpenRouter y la empresa final):

- el **título** del PR, el **diff** y la **guía** del proyecto;
- las **funciones completas** (Python y shell) que contienen los cambios;
- en el contraste, además, el **texto de los comentarios** contrastados y los
  fragmentos de código que citan.

Ni la revisión ni el contraste envían la **descripción** del PR. Antes de
enviar nada, y otra vez antes de publicar:

- **Se tachan secretos**: asignaciones como `api_key = …`, `token: …` o
  `password=…`; tokens con formato conocido (GitHub, OpenAI, Anthropic,
  Stripe, AWS, Slack, Google, JWT); el cuerpo de una clave privada PEM; y la
  contraseña de una URL. Es una lista de bloqueo: un formato raro puede
  escapar.
- **Se excluyen siempre** `.env*`, `*.pem`, `*.key` e `id_rsa*`, en cualquier
  carpeta, aunque el workflow no los excluya.
- **Los datos van delimitados** con un código que el PR no puede imitar, y el
  modelo recibe la orden de tratar todo lo que va dentro como datos, nunca
  como instrucciones. Reduce el riesgo de que un PR manipule la revisión, pero
  no lo elimina: por eso los hallazgos se verifican con citas y la conclusión
  sigue requiriendo revisión humana.

El revisor nunca ejecuta el código del PR: lo lee con Git. Más detalle en
[docs/revision.md](docs/revision.md). Para avisar de una vulnerabilidad,
[SECURITY.md](SECURITY.md).

## Si algo falla

El mensaje sale en el registro del check y en el resumen de la ejecución
(**Actions → la ejecución**). Un error de configuración se detecta antes de
llamar a la API y no gasta crédito.

| Ves | Causa probable | Qué hacer |
|---|---|---|
| No aparece el check | El archivo no está en `.github/workflows/` de la rama principal, o tiene un error de sintaxis | Revisa la pestaña **Actions**: GitHub muestra ahí el error del workflow |
| «Revisión omitida» y el resumen nombra un secreto | Falta la clave del proveedor, o el PR viene de un fork | Crea el secreto ([configuración](#variables-y-secretos)); en forks es lo esperado |
| El comentario nombra una regla incumplida en vez de hallazgos | El modelo respondió mal dos veces | **Re-run jobs**; si se repite, prueba otro modelo |
| «Revisión inconclusa» | Sin hallazgos y sin ninguna comprobación verificable | Revisa el PR a mano; no significa «sin defectos» |
| El contraste no publica nada y el resumen dice «Contraste de Mistral rechazado: …» | La respuesta del modelo no cumplía el formato (por ejemplo, `respuestas_4_de_1`: más dictámenes que hallazgos) | Usa `zai-glm-5-3-flash` en `MISTRAL_REPLY_MODEL` o bórrala |
| GitHub rechaza el workflow al recibir `provider` u `OPENROUTER_API_KEY` | `uses:` apunta a una versión anterior a `v1.2` | Fija una versión reciente ([versiones](#versiones)) |
| Errores de OpenRouter (402, 404, 503…) | Crédito, modelo o proveedor | Tabla de [docs/openrouter.md](docs/openrouter.md#si-algo-falla) |

La revisión entera, incluidos los reintentos, tiene un tope de 12 minutos.

## Versiones

`uses: …@X` decide qué versión del workflow se descarga y `ref: X` qué
versión del script se ejecuta. **Deben ser iguales**; si no, se mezclan dos
versiones.

- Con `@main` el repo recibe cada cambio de aquí en su próxima ejecución.
- Con una etiqueta (`@v1.5`), solo cuando tú la cambies. Es lo recomendable.

### Comprobar o fijar la versión del revisor

Desde la web de GitHub, en el repo que usa el revisor:

1. Abre `.github/workflows/` y entra en cada archivo que llame al revisor
   (`mistral-review.yml`, y también `mistral-reply.yml` o `mistral-eval.yml`
   si los tienes).
2. Busca la línea `uses: novanoticia/mistral-review/.github/workflows/….yml@…`
   y mira lo que va detrás de `@`.
3. Dentro del bloque `with:` que sigue, busca `ref:`. Si no está, vale `main`.
4. Para fijar una versión, pulsa el lápiz (**Edit this file**), pon la misma
   etiqueta tras `@` y en `ref:` (añade `ref:` dentro del `with:` existente si
   faltaba) y pulsa **Commit changes…**.

Las versiones publicadas y sus cambios están en
[Releases](https://github.com/novanoticia/mistral-review/releases).

## Usar OpenRouter (opcional)

[OpenRouter](https://openrouter.ai) da acceso, con una sola clave, a modelos
de muchas empresas. Sirve para usar o probar modelos que Mistral no tiene.
Con los workflows de los ejemplos **no hay que tocar ningún archivo**:

1. Crea una clave en [openrouter.ai/settings/keys](https://openrouter.ai/settings/keys),
   con un límite de crédito pequeño, y recarga crédito.
2. En el repo, **Settings → Secrets and variables → Actions**:
   - secreto `OPENROUTER_API_KEY` con la clave;
   - variable `OPENROUTER_MODEL` con el modelo, en la forma
     `organización/modelo` (por ejemplo `z-ai/glm-5.3-flash`);
   - variable `OPENROUTER_PROVIDER` con la empresa que lo ejecuta (por
     ejemplo `z-ai`): hace las revisiones comparables;
   - variable `MISTRAL_PROVIDER` = `openrouter`.
3. Abre un PR o usa **Re-run jobs**. El comentario debe empezar por
   «Revisión vía OpenRouter». Tarda más: de 4 a 10 minutos en las pruebas.

Para volver a Mistral, borra `MISTRAL_PROVIDER` o ponla en `mistral`; lo demás
puede quedarse. Guía completa (cómo elegir modelo y proveedor, errores,
privacidad con dos empresas, costes): [docs/openrouter.md](docs/openrouter.md).

## Coste

Con `zai-glm-5-3-flash` (0,15 / 0,50 USD por millón de tokens de entrada /
salida), del orden de **medio céntimo por revisión**; el contraste cuesta algo
parecido por respuesta. Un modelo que agota su tope de salida razonando se
pide una segunda vez, y esa también se cobra. Con OpenRouter depende del
modelo y del proveedor final ([costes](docs/openrouter.md#privacidad-costes-y-límites)).

## Desarrollo

```bash
python3 -m unittest discover -s tests -t .
```

Solo biblioteca estándar de Python, sin red ni API en los tests. El revisor
exige Python 3.12 o posterior; con 3.11 se salta la prueba que lo necesita.

Este repo revisa y contrasta sus propios PR con
[`.github/workflows/mistral-review.yml`](.github/workflows/mistral-review.yml) y
[`.github/workflows/mistral-reply.yml`](.github/workflows/mistral-reply.yml),
fijados en la última versión publicada y no en la rama del PR: el script que
se ejecuta con la clave es el de una etiqueta publicada. La excepción es un PR
que edite esos dos archivos, porque en `pull_request` GitHub toma el workflow
de la rama del PR; ese cambio se revisa a mano.

### Al publicar una versión

Los repos que fijan una etiqueta no reciben la versión nueva hasta que se
cambie a mano. Tras publicar la *release* (por ejemplo `v1.6`), en cada archivo
de esta lista cambia lo que va tras `@` en `uses:` y el valor de `ref:` a la
nueva etiqueta, las dos a la vez:

| Repo | Archivos en `.github/workflows/` |
|---|---|
| `mistral-review` (este) | `mistral-review.yml`, `mistral-reply.yml` |
| `email-triage-plugin` | `mistral-review.yml`, `mistral-reply.yml`, `mistral-eval.yml` |
| `claude-skills-exporter` | `mistral-review.yml`, `mistral-reply.yml` |
| `multilang-repo-skill` | `mistral-review.yml`, `mistral-reply.yml` |
| `repo-auditor-ia` | `mistral-review.yml`, `mistral-reply.yml` |

Para comprobar que no queda ninguna referencia antigua en un repo, busca la
etiqueta anterior (por ejemplo `v1.5`) en su carpeta `.github/`. En este repo,
`test_los_workflows_propios_fijan_la_misma_version` falla si `uses:` y `ref:`
no coinciden o si los dos workflows apuntan a versiones distintas. Si añades
el revisor a un repo nuevo, añádelo también a esta tabla.

### Procedencia

Fusiona las cuatro copias que tenían `email-triage-plugin` (base),
`claude-skills-exporter`, `multilang-repo-skill` y `repo-auditor-ia` (contexto
de shell y `mask_secrets`). El contraste (`mistral_reply.py`) y la evaluación
(`mistral_eval.py`) se trajeron después, también de `email-triage-plugin`.

---

*Texto elaborado con asistencia de IA; requiere revisión humana.*
