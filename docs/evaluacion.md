# Evaluación del revisor de Mistral con casos reales

Los tests del revisor fijan su mecánica (qué se envía, qué se valida, qué se
publica). No dicen si el modelo **encuentra defectos de verdad**. Esta
evaluación sí, con commits del historial del repo evaluado: es la propuesta 2 de la
[auditoría del 29 de octubre de 2026](https://github.com/novanoticia/email-triage-plugin/blob/main/docs/auditoria-mistral-2026-10-29.md).

Desde el 2026-11-16 el script (`scripts/mistral_eval.py`) y el workflow
(`.github/workflows/eval.yml`) viven aquí y sirven a cualquier repo; los
casos son commits de cada repo y viven en él (`.github/eval/casos.json`).
Los casos y resultados de este documento son los de `email-triage-plugin`,
donde nació la evaluación.

## Qué mide

Cada caso de `.github/eval/casos.json` fija dos commits y se revisa con el
mismo código que un PR (diff, contexto, prompt, llamada y validación de
`mistral_review.py`).

| Tipo | Qué es | Qué se cuenta |
|---|---|---|
| **positivo** | `cabeza` tiene un defecto conocido; `arreglo` es el commit que lo corrigió. | Si algún hallazgo cita una línea dentro de una función que el arreglo tocó («señala la función»). |
| **negativo** | Sobre `cabeza`, Mistral publicó hallazgos que la auditoría contrastó como falsos. | Cuántos hallazgos hay y cuántos caen otra vez en los mismos archivos. |

El objetivo de un positivo no se escribe a mano: se deriva del diff entre la
cabeza y el arreglo (funciones Python que no son tests). `ignorar` excluye
archivos que el arreglo tocó sin ser el defecto.

En `email-triage-plugin` hay 7 positivos (#123, #125, #126, #143, #199 y
dos de #200) y 5 negativos (#108, #114, #116, #123, #140), PR de ese repo,
más un **caso de control**
(`c1-guarda-invertida`, con `"control": true`).

El control es un defecto sintético y evidente: `_porcentaje` en
`scripts/distribucion_tiers.py` con la guarda invertida, que se ve entero en
un diff de pocas líneas. Su defecto y su arreglo son dos commits seguidos del
mismo PR, así que en `main` solo queda la versión correcta. Sirve para separar
dos lecturas de un 0 de 7: si un modelo tampoco ve el control, no ve defectos
evidentes; si lo ve y falla los reales, los casos reales piden algo que el
revisor no recibe (contexto de otros archivos, conocer el proyecto). No cuenta
entre los 7: los resultados se comparan aparte.

## Lo que no mide

- **Señalar la función no prueba acertar.** Un hallazgo puede caer en la
  función correcta y describir otra cosa. El informe lista cada hallazgo para
  que una persona lo etiquete; solo esa etiqueta es el resultado.
- **No señalarla sí es informativo:** el defecto no se vio.
- En los negativos, un hallazgo nuevo en el mismo archivo no es
  automáticamente falso: hay que leerlo.
- Doce casos y pocas repeticiones dan una impresión con números, no una
  estadística. No sirven para comparar diferencias pequeñas.
- Los casos proceden de este repositorio y los positivos los encontraron
  otros revisores (Codex, pruebas reales): es una muestra sesgada hacia lo que
  otros sí vieron.

## Diferencias con una revisión real

- El título es el asunto del commit y **no hay descripción**: la del PR
  histórico podía nombrar el arreglo.
- La guía (`CLAUDE.md`) es la del árbol actual, no la de entonces.
- El diff es el del PR hasta ese commit (`base...cabeza`), como en una
  revisión a mitad de PR.

## Cómo ejecutarla

En el repo evaluado, copia [`ejemplos/mistral-eval.yml`](../ejemplos/mistral-eval.yml)
en `.github/workflows/` y los casos en `.github/eval/casos.json` (plantilla:
[`ejemplos/eval-casos.json`](../ejemplos/eval-casos.json)). Después,
**Actions → Mistral Eval → Run workflow.** Cuatro campos:

- `repeticiones`: revisiones por caso (2 por defecto).
- `solo`: ids separados por comas; vacío evalúa todos.
- `modelo`: un modelo solo para esta evaluación (por ejemplo
  `mistral-large-2512` o `codestral-latest`). Vacío usa `MISTRAL_MODEL`. El
  revisor de los PR no cambia: sirve para comparar modelos sin tocarlo. Con
  OpenRouter (`provider: openrouter`), un identificador `organización/modelo`.
- `esfuerzo`: esfuerzo de razonamiento (`low`, `medium`, `high`) solo para
  esta evaluación. Vacío usa el del revisor (con OpenRouter, no se envía y
  el modelo usa el suyo). Si el modelo no lo admite, se
  retira solo y el informe lo dice.
- `max_salida`: tope de tokens de salida por respuesta (`max_tokens`), de
  4.000 a 256.000, solo para esta evaluación. Vacío usa el del revisor
  (16.000). Para modelos que razonan mucho: DeepSeek V4.1 Flash agotó los
  16.000 en 12 de 13 casos (ejecución #6) y los 32.000 en 2 de 3 (#8).
  Subirlo encarece la salida en proporción (peor caso con DeepSeek y
  256.000: ~0,31 $ por respuesta). La espera por petición crece con el tope
  (1 s por cada 100 tokens, mínimo 180 s), porque la API responde de una vez
  al terminar. Si el modelo no admite tanto, la API responde 400 sin cobrar.

El resultado sale abreviado en el resumen de la ejecución. El artefacto
`mistral-eval` conserva el fragmento, el problema, el escenario y el arreglo
de cada hallazgo para poder etiquetarlo. Tras validar y clasificar la
respuesta, se aplica el filtro de secretos a los textos guardados de
hallazgos y pendientes. El resumen y el registro de Actions usan esos mismos
textos filtrados. El filtro reconoce patrones de asignación habituales y
puede dejar pasar secretos en otros formatos.

Cada entrada de `rechazadas` conserva el motivo (`detalle`) y la longitud
original de la respuesta (`caracteres`). Si la respuesta se puede leer como
JSON y contiene campos reconocidos de resumen, hallazgos o comprobaciones,
el campo opcional `texto` guarda una vista de esos campos solo en el
artefacto. Se filtran las cadenas antes de convertir la vista a texto y
limitarla a 20.000 caracteres. Esto permite examinar si el modelo apuntó al
defecto aunque fallara el formato, como en el control de la ejecución #7.
Si el JSON está mal formado, se guardan el motivo y la longitud.

La evaluación usa el secreto `MISTRAL_API_KEY` y la variable
`MISTRAL_MODEL` del repositorio, igual que el revisor. Con OpenRouter usa el secreto
`OPENROUTER_API_KEY` y las variables `MISTRAL_PROVIDER` y, opcional,
`OPENROUTER_PROVIDER` ([README](../README.md#usar-openrouter-opcional)).

Si la API indica `length`, se agotó el margen de salida y se puede hacer un
segundo intento. El campo `cortes_salida` cuenta esos cortes aunque la siguiente
respuesta sea válida. Si el último intento termina por `length`, la revisión
queda `cortada`. Un cuerpo HTTP que no sea JSON o un contenido vacío se registra
como `error` con el detalle `formato_respuesta`. Una respuesta textual que el
validador rechaza queda `invalida` si se agotan los intentos.

**Comparar proveedores y modelos.** Los casos y los criterios son los mismos
para cualquier configuración. El encabezado del informe y el JSON del
artefacto (`proveedor`, `ruta`, `modelo`, `esfuerzo`, `max_salida`) dicen
qué se midió; dos ejecuciones solo se comparan si eso consta. El informe y
el artefacto (`servido_por`, `modelo_servido`) dicen también quién sirvió
cada respuesta según la API, o «no lo informa» si la respuesta no lo trae. Con OpenRouter,
fija `OPENROUTER_PROVIDER`: si no, cada petición puede ir a un proveedor
distinto y la comparación pierde sentido. Que una configuración encuentre más
defectos en una ejecución no prueba que sea mejor: hacen falta varias
repeticiones y etiquetar los hallazgos a mano.

**Coste.** Cada revisión admite hasta dos intentos de respuesta. Se usa el
segundo si la primera se corta por `length` o el validador la rechaza. Con
`mistral-large-latest` y razonamiento, del orden de 10.000
tokens de salida más la entrada (guía, contexto y diff: decenas de miles de
tokens). Doce casos a dos repeticiones son al menos 24 peticiones. Hay un
tope de 80 **peticiones HTTP reales** por ejecución, contadas en el
transporte: incluye los reintentos por errores transitorios. Si se alcanza,
la evaluación se detiene, publica lo que lleva marcado como incompleto y
termina en error. El informe da los tokens de entrada y de salida que
declara la API, para calcular el gasto con la tarifa del modelo, y el coste
cuando la API lo declara (OpenRouter, `usage.cost`, en créditos); si no lo
declara (Mistral), lo dice y no lo estima. Un modelo
que no admite el esfuerzo de razonamiento gasta una petición de más solo la
primera vez: tras el rechazo, el resto de la ejecución ya no lo pide. Para
probar barato: `solo` con un caso y una repetición.

El JSON se guarda tras cada revisión y el paso «Evaluar» tiene su propio
límite (80 minutos, por debajo de los 90 del job). Si la ejecución se cancela
o se agota el tiempo, el artefacto `mistral-eval` conserva lo ya revisado:
`en_curso: true` si el proceso murió a mitad, `cancelada: true` si llegó a
cerrar tras la cancelación. La ejecución #5 (esfuerzo `high`, 12 casos) se
canceló a los 90 minutos sin dejar nada; esto lo evita.

Sin `--ejecutar`, el script solo lista los casos y sus objetivos y no llama
a la API:

```bash
# desde la raíz del repo evaluado, con mistral-review clonado al lado
python3 ../mistral-review/scripts/mistral_eval.py
```

## Cómo añadir un caso

1. Positivo: localiza el commit con el defecto (`cabeza`), el que lo arregló
   (`arreglo`) y la base del PR (`base`). Negativo: el commit revisado, la
   base y los archivos de los hallazgos falsos.
2. Añádelo a `.github/eval/casos.json` con los SHA completos y una frase en
   `defecto` que diga qué debería verse.
3. Ejecuta el listado y comprueba que el objetivo es la función del defecto.

Si se usa la evaluación para ajustar el prompt, hay que separar casos de
ajuste y de evaluación: medir con los mismos casos con los que se ajustó solo
mide el ajuste.

## Resultados (2026-11-15)

Una repetición por caso: dan tendencia, no porcentajes. Cada hallazgo se
etiquetó a mano leyendo su problema y su escenario; «señala la función» no
basta. Costes calculados con los tokens que declara la API y la tarifa de la
página oficial de precios de Mistral (USD por millón, entrada / salida):
Large 4 0,70 / 2,10; GLM 5.3 1,40 / 4,40; GLM 5.3 Flash 0,15 / 0,50;
DeepSeek V4.1 Flash 0,30 / 1,20. Las ejecuciones (de `email-triage-plugin`) #2 a #4 son anteriores al
caso de control (12 casos). Faltan a propósito: #1 (no forma parte de esta
comparación), #5 (Large 4 con esfuerzo `high`, cancelada a los 90 minutos sin
datos) y #7 y #10 (GLM 5.3 Flash antes del email-triage-plugin#210: el control salía inválido
por citar dos líneas; las sustituye #11).

| Ejecución | Modelo | Control | Defectos reales | Ruido en negativos | Coste |
|---|---|---|---|---|---|
| #2 | `mistral-large-latest` (Large 4) | — | 0 de 7 | 1 hallazgo inflado | ~0,75 $ (estimado) |
| #3 | `mistral-large-2512` (Large 3) | — | 0 de 7 | 7 hallazgos especulativos | ~0,22 $ (estimado) |
| #4 | `codestral-latest` | — | 0 de 7 | 3 hallazgos inútiles | ~0,12 $ (estimado) |
| #6, #8, #9 | `deepseek-v4-1-flash-1017` | acierto | 1 de 2 medidos (p125, exacto) | sin datos | 0,35 + 0,13 + 0,32 $ |
| #11 | `zai-glm-5-3-flash` | acierto | 0 de 7 | ninguno | 0,08 $ |
| #12 | `mistral-large-latest` (Large 4) | acierto | — (solo el control) | — | 0,02 $ |
| #13 | `zai-glm-5-3` | acierto | 0 de 7 (p126 cortada) | ninguno | 0,74 $ |

Lo que se aprendió:

- **Todos los modelos fiables ven el defecto evidente y ninguno ve los
  reales.** El control descarta que «0 de 7» signifique que no leen el diff:
  los defectos del historial piden algo que este revisor no tiene (contexto
  de otros archivos, conocer el proyecto). La detección sigue siendo de Codex.
- **DeepSeek V4.1 Flash es el único que acertó un defecto real** (p125, con
  el escenario de `compactar --dias 30` y un segundo hallazgo real), pero no
  termina: agotó la salida razonando en 12 de 13 casos con 16.000 tokens, en
  2 de 3 con 32.000 y en 2 de 2 con 128.000 (siempre hasta el último token).
  El esfuerzo `low` no lo frena. Descartado como revisor.
- **GLM 5.3 Flash** perdía el control por citar dos líneas seguidas (el
  validador solo admitía una); desde el email-triage-plugin#210 se aceptan tramos
  consecutivos de un mismo hunk y sale 13 de 13 válidas, sin ruido, en unos
  30 s por revisión. **GLM 5.3** completo da lo mismo a nueve veces el coste.
- Las tres revisiones de esta jornada que se cortaron por la espera de 180 s
  (email-triage-plugin#206 una vez, email-triage-plugin#210 dos) fueron revisiones de PR con Large 4; ninguna
  revisión de la evaluación la agotó (con topes de salida grandes la espera
  crece sola desde el email-triage-plugin#209). Causa probable, sin verificar: Large 4 tarda
  a veces más de 180 s según el diff.

**Decisión del mantenedor (2026-11-15):** el revisor de los PR pasa a
`zai-glm-5-3-flash` (variable `MISTRAL_MODEL`): el mismo valor que Large 4
como segunda opinión barata, a ~0,006 $ por revisión frente a ~0,04 $, más
rápido y sin ruido medido. Es un identificador fijo, sin `-latest`; los
modelos de terceros rotan rápido en Mistral (GLM 5.2 se retiró un mes después
de quedar obsoleto), así que hay que vigilar su fecha de retirada en la
página de modelos. Gasto medido de las ejecuciones #6 a #13 (incluidas #7,
~0,08 $, y #10, menos de 0,01 $, que no salen en la tabla): ~1,73 $. Las #2 a
#4 son de una sesión anterior y su coste es una estimación (~1,09 $).

## Primera prueba con OpenRouter (2026-11-17)

Prueba de funcionamiento, no de calidad: un caso (`solo:
c1-guarda-invertida`), una repetición, en `email-triage-plugin` con
`proveedor: openrouter`, `modelo: z-ai/glm-5.3-flash` y `proveedor_final:
z-ai` (el mismo GLM 5.3 Flash que usa el revisor con Mistral, servido por
Z.AI). Primera llamada real a OpenRouter.

| Proveedor | Modelo | Control | Tokens (entrada / salida) | Coste |
|---|---|---|---|---|
| OpenRouter (Z.AI) | `z-ai/glm-5.3-flash` | acierto | 14.032 / 6.821 | 0,00546 créditos (declarado) |

- **Funciona de punta a punta:** 1 de 1 revisiones válidas, una sola
  petición HTTP, sin reintentos ni cortes por `length`, y el informe y el
  artefacto dicen qué se midió (`proveedor`, `ruta`, `modelo`).
- **El hallazgo es un acierto** (etiquetado a mano): señala la guarda
  invertida con sus dos fallos (0 % con correos y `ZeroDivisionError` sin
  ellos), con escenario concreto y el arreglo exacto. El único pendiente
  (no hay test que cubra `_porcentaje`) es cierto y no es ruido.
- **Coste:** la tarifa del endpoint de Z.AI en el catálogo de OpenRouter es la
  misma que la de Mistral (0,15 / 0,50 $ por millón); con estos tokens da
  ~0,0055 $, y la API declaró 0,00546 créditos. Indica que un crédito equivale
  a un dólar, sin confirmar (la pequeña diferencia puede ser entrada en
  caché). Por revisión cuesta lo mismo que con Mistral (~0,006 $ en la #11).
- **Calidad frente a Mistral:** ambos aciertan el control; con un caso y una
  repetición no se puede decir que uno sea mejor.
- **Sin confirmar:** qué proveedor sirvió la petición. El script guardaba la
  `ruta` pedida, no lo que responde la API; con `allow_fallbacks: false` solo
  pudo ser Z.AI, pero no consta en el artefacto. Desde entonces se anota el
  `provider` de la respuesta (`servido_por`); la documentación de OpenRouter
  no lo incluye en la respuesta sin streaming, así que puede salir «no lo
  informa».
- Fijar `proveedor_final` importa: el mismo modelo lo sirven en OpenRouter
  más de cuarenta proveedores, varios cuantizados en fp4 y algunos sin
  `response_format`; sin fijarlo, cada petición puede ir a uno distinto.
- En el resumen de la ejecución el escenario se corta a 400 caracteres a
  propósito; el texto completo está en el artefacto `mistral-eval`.

## Qwen3 Coder Next vía OpenRouter (2026-11-17)

Los 13 casos, una repetición, con `proveedor: openrouter`, `modelo:
qwen/qwen3-coder-next` y `proveedor_final: parasail` (único proveedor del
modelo; bf16). Se eligió por ser barato y estar hecho para código. Ejecución
con `v1.2`: el artefacto no dice quién sirvió las respuestas.

| Modelo | Control | Defectos reales | Ruido en negativos | Válidas | Coste |
|---|---|---|---|---|---|
| `qwen/qwen3-coder-next` (Parasail) | acierto | 0 de 6 medidos (p199 inválida) | 2 hallazgos falsos | 12 de 13 | 0,061 créditos (declarado) |
| `zai-glm-5-3-flash` (Mistral, #11) | acierto | 0 de 7 | ninguno | 13 de 13 | 0,08 $ |

Etiquetado a mano:

- **Control:** acierto, con un escenario algo impreciso (dice que `0/0`
  falla «si n no fuera 0»; con n = 0 también falla).
- **Positivos:** ningún hallazgo en p123, p125, p126, p143 ni
  p200-recursión. En p200-última-línea, un hallazgo especulativo que no es
  el defecto. p199 quedó inválida tras dos intentos (citas fuera del diff;
  el primero inventaba un `NameError`).
- **Negativos:** en n140, un hallazgo de severidad **alta** cuyo propio
  escenario acaba en «No hay error aquí»; en n114, que la severidad
  distingue mayúsculas, cuando el prompt las exige en minúscula. n108, n116
  y n123, limpios.
- **Formato:** 2 de 15 respuestas rechazadas (una con `fragment` en vez de
  `fragmento`, corregida al reintento). 15 peticiones en total.
- **Tokens:** 477.033 de entrada y 17.930 de salida, unos 1.200 de salida
  por respuesta frente a los ~6.800 de GLM 5.3 Flash en la prueba del
  control. Hipótesis sin verificar: apenas razona, y eso explicaría el ruido.

**Conclusión:** con una repetición no es concluyente, pero no hay nada a
favor de cambiar: ve lo mismo que GLM 5.3 Flash, con más ruido y peor
formato, a un coste parecido. El revisor sigue con `zai-glm-5-3-flash`.

*Texto elaborado con asistencia de IA; requiere revisión humana.*
