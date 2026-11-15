# mistral-review

Revisor de pull requests con la API de Mistral, **reutilizable desde otros
repos**. Cada repo lo llama con un workflow corto; el código vive solo aquí, así
que una mejora se hace una vez y llega a todos.

Es una **segunda opinión barata**, no un detector fiable: en una evaluación con
defectos reales del historial de `email-triage-plugin` no encontró ninguno de
los 7, aunque sí ve defectos evidentes (caso de control). Sus hallazgos hay que
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
- Publica un comentario por commit revisado, con el modelo y un enlace a la
  ejecución. Editar el PR no repite la revisión; «Re-run jobs» sí.

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
| `model` | Modelo de Mistral | `zai-glm-5-3-flash` |
| `excluded` | Archivos que no se revisan (generados), uno por línea | ninguno |
| `guides` | Archivos de guía, por orden de preferencia | `REVIEW.md`, `AGENTS.md`, `CLAUDE.md` |
| `ref` | Rama o etiqueta de este repo cuyo script se ejecuta | `main` |

`ref` debe coincidir con lo que pongas tras `@` en `uses:`. Hoy todos usan
`main`; cuando haya una etiqueta (`v1`), se puede fijar en ambos sitios para que
un cambio aquí no llegue a todos los repos sin control.

## Coste

Con `zai-glm-5-3-flash` (0,15 / 0,50 USD por millón de tokens de entrada /
salida), del orden de **medio céntimo por revisión**. Los PR de forks no
reciben el secreto: la revisión se omite sin fallar.

## Desarrollo

```bash
python3 -m unittest discover -s tests -t .
```

Solo biblioteca estándar de Python, sin red ni API en los tests.

Procedencia: fusiona las cuatro copias que tenían `email-triage-plugin`
(base), `claude-skills-exporter`, `multilang-repo-skill` y `repo-auditor-ia`
(contexto de shell y `mask_secrets`). El contraste de respuestas a otras IAs
(`mistral_reply.py`) y la evaluación (`mistral_eval.py`) siguen, por ahora, en
`email-triage-plugin`.

---

*Texto elaborado con asistencia de IA; requiere revisión humana.*
