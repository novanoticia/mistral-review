# Usar OpenRouter

Guía completa para revisar con un modelo de [OpenRouter](https://openrouter.ai)
en vez de Mistral. El resumen está en el [README](../README.md#usar-openrouter-opcional).

[OpenRouter](https://openrouter.ai) es una **pasarela**: una sola clave y una
sola API para modelos de muchas empresas. Mistral sigue siendo el proveedor
por defecto; OpenRouter sirve para usar o probar modelos que Mistral no tiene.

Si el workflow de tu repo ya tiene las líneas de proveedor (como los de
[`ejemplos/`](../ejemplos/)), **no hay que tocar ningún archivo**: el proveedor se
elige con variables del repo. Si no las tiene, mira antes
[Si tu workflow no tiene las líneas de proveedor](#si-tu-workflow-no-tiene-las-líneas-de-proveedor).

## Qué se configura

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

## Activarlo, paso a paso

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

## Cambiar de proveedor

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

## Si algo falla

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

## Si tu workflow no tiene las líneas de proveedor

Compara el workflow del repo con
[`ejemplos/mistral-review.yml`](../ejemplos/mistral-review.yml). Si le faltan,
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

## Cómo se configura la petición

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

## Privacidad, costes y límites

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
  [evaluacion.md](evaluacion.md).

---

*Texto elaborado con asistencia de IA; requiere revisión humana.*
