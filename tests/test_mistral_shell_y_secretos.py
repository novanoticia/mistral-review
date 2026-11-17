"""Lo que trae repo-auditor-ia: contexto de shell y secretos tachados.

Portado de repo-auditor-ia (tests/test_mistral_utilidad.py y
tests/test_mistral_review.py), con `_rangos_shell` en lugar de `_rangos`.
Sin red ni API.
"""
import json
import os
import unittest
from unittest import mock

from tests.test_mistral_review import mr
from tests.test_mistral_utilidad import diff_de

GUION = "#!/usr/bin/env bash\nset -u\n" + "".join(
    "RELLENO_%d=%d\n" % (i, i) for i in range(60)) + '''
mask_secrets() {
  local entrada="$1"
  if [ -z "$entrada" ]; then
    return 0
  fi
  printf '%s' "$entrada"
}

function otra {
  echo "no enviada"
}

SUELTA=1
'''
LINEAS_SH = GUION.split("\n")
N_PRINTF = LINEAS_SH.index("  printf '%s' \"$entrada\"") + 1
# Valores partidos para que ningún escáner los tome por un secreto real.
VALOR = "valor" + "sintetico" + "deprueba"


class TestShell(unittest.TestCase):
    def test_reconoce_las_dos_formas_de_declarar_una_funcion(self):
        nombres = [f[2] for f in mr._funciones_shell(GUION)]
        self.assertEqual(nombres, ["mask_secrets", "otra"])
        self.assertEqual(mr._funciones_shell("if x; then\n  y\nfi\n"), [])

    def test_funciones_sin_sangrar_dentro_de_otra_no_la_cierran_antes_de_tiempo(self):
        # Como recon.sh: main() contiene funciones definidas en la columna 0.
        guion = ("main() {\nREPO=x\ninterna() {\n  echo a\n}\n" +
                 "relleno=1\n" * 300 + "echo fin\n}\n")
        funciones = dict((f[2], f[:2]) for f in mr._funciones_shell(guion))
        self.assertEqual(funciones["interna"], (3, 5))
        self.assertEqual(funciones["main"], (1, 307))
        # Dentro de la interna: la interna, no el main que envuelve todo.
        self.assertEqual(mr._rangos_shell(guion, {4}), [(3, 5, "interna")])
        # En el cuerpo de un main enorme: ventana, no 300 líneas de relleno.
        (inicio, fin, nombre), = mr._rangos_shell(guion, {150})
        self.assertEqual((nombre, fin - inicio), ("", 2 * mr.VENTANA))

    def test_envia_la_funcion_de_shell_cambiada_con_su_guarda(self):
        def git_show(orden, **_):
            return mock.Mock(returncode=0, stdout=GUION.encode("utf-8"))
        with mock.patch.object(mr.subprocess, "run", side_effect=git_show):
            texto = mr.obtener_contexto(diff_de("scripts/recon.sh", N_PRINTF, "x"), "abc123")
        self.assertIn("```bash", texto)
        self.assertIn("· mask_secrets", texto)
        self.assertIn('if [ -z "$entrada" ]; then', texto)
        self.assertNotIn("no enviada", texto)
        self.assertNotIn("RELLENO_0=", texto)

    def test_python_sigue_con_su_bloque_python(self):
        fuente = "def f(x):\n    if x:\n        return 1\n    return 0\n"
        def git_show(orden, **_):
            return mock.Mock(returncode=0, stdout=fuente.encode("utf-8"))
        with mock.patch.object(mr.subprocess, "run", side_effect=git_show):
            texto = mr.obtener_contexto(diff_de("a.py", 3, "x"), "abc123")
        self.assertIn("```python", texto)
        self.assertNotIn("```bash", texto)


class TestSecretos(unittest.TestCase):
    def test_tacha_el_valor_y_conserva_la_clave(self):
        for entrada in ('API_KEY="%s"' % VALOR, "token: %s" % VALOR,
                        "password=%s" % VALOR, "'private_key': '%s'" % VALOR):
            with self.subTest(entrada=entrada):
                tachado = mr.mask_secrets(entrada)
                self.assertNotIn(VALOR, tachado)
                self.assertIn("***", tachado)
        self.assertEqual(mr.mask_secrets("x = 1\nnombre = 'ana'"), "x = 1\nnombre = 'ana'")

    def test_no_tacha_nombres_citados_en_prosa(self):
        # `` `secrets:` el bloque `` salía como `` `secrets:*** el bloque ``:
        # la comilla invertida que cierra el nombre no es un valor.
        for linea in ("el bloque `secrets:` de `workflow_call`",
                      "la variable `MISTRAL_API_KEY:` y luego",
                      "con ``token=`` vacío"):
            with self.subTest(linea=linea):
                self.assertEqual(mr.mask_secrets(linea), linea)
        # Un secreto entre comillas invertidas se sigue tachando.
        self.assertEqual(mr.mask_secrets("api_key: `%s`" % VALOR), "api_key: ***")
        self.assertEqual(mr.mask_secrets("api_key:`%s`" % VALOR), "api_key:***")

    def test_no_tacha_expresiones_de_github_actions(self):
        # Una expresión `${{ … }}` es una referencia, no un valor: tacharla
        # deja `secrets:` como `***` y confunde al modelo (claude-skills-exporter#23).
        for linea in ("MISTRAL_API_KEY: ${{ secrets.MISTRAL_API_KEY }}",
                      "OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}",
                      "token: '${{ github.token }}'",
                      'GH_TOKEN: "${{ github.token }}"'):
            with self.subTest(linea=linea):
                self.assertEqual(mr.mask_secrets(linea), linea)
        # Un valor literal en la misma forma YAML se sigue tachando.
        self.assertEqual(mr.mask_secrets("api_key = \"valor\""), 'api_key = "***"')
        self.assertEqual(mr.mask_secrets("MISTRAL_API_KEY: %s" % VALOR),
                         "MISTRAL_API_KEY: ***")
        # Solo la expresión completa `${{`: un `$` suelto no la imita.
        self.assertEqual(mr.mask_secrets("token: $%s" % VALOR), "token: ***")

    def test_enmascara_asignaciones_antes_de_enviar_a_mistral(self):
        entrada = 'API_KEY="' + VALOR + '"'
        mensajes, _ = mr.construir_mensajes(entrada, entrada, entrada,
                                           ("AGENTS.md", entrada), entrada)
        self.assertNotIn(VALOR, mensajes[1]["content"])
        self.assertIn("***", mensajes[1]["content"])
        self.assertEqual(mr.mask_secrets(mensajes[1]["content"]), mensajes[1]["content"])

    def test_el_contexto_pasa_por_el_filtro_de_secretos(self):
        clave = "api" + "_key"
        contexto = "## a.sh\n```bash\n%s=valor-privado\n```" % clave
        mensajes, _ = mr.construir_mensajes("T", "D", "+x\n", (None, ""), contexto)
        self.assertNotIn("valor-privado", mensajes[1]["content"])

    def test_enmascara_texto_generado_en_el_informe(self):
        texto = mr.renderizar(('token="' + VALOR + '"', []), "m", 1, False)
        self.assertNotIn(VALOR, texto)

    def test_una_cita_de_una_linea_tachada_se_valida_contra_el_diff_tachado(self):
        # main() tacha el diff antes de validar: la cita que el modelo copia
        # del mensaje (ya tachado) debe seguir siendo una línea del diff.
        diff = mr.mask_secrets(
            "diff --git a/c.py b/c.py\n--- a/c.py\n+++ b/c.py\n@@ -1 +1 @@\n"
            "-TOKEN = 'viejo'\n+TOKEN = '%s'\n" % VALOR)
        hallazgo = {"severidad": "alta", "archivo": "c.py", "fragmento": "TOKEN = '***'",
                    "problema": "P", "escenario": "E", "arreglo": "A"}
        self.assertIsNotNone(mr.interpretar(
            json.dumps({"resumen": "R", "hallazgos": [hallazgo]}), diff))


class TestParametros(unittest.TestCase):
    def test_listas_por_saltos_de_linea_o_comas(self):
        with mock.patch.dict(os.environ, {"MISTRAL_EXCLUIDOS": " a/b.json\n\nc/*.yaml , d "}):
            self.assertEqual(mr.excluidos(), ("a/b.json", "c/*.yaml", "d"))
        with mock.patch.dict(os.environ, {"MISTRAL_EXCLUIDOS": ""}):
            self.assertEqual(mr.excluidos(), ())

    def test_excluido_sigue_la_misma_regla_que_git(self):
        # `excluido` decide lo que el contraste no lee; `obtener_diff` usa
        # `:(exclude)` de Git. Las dos reglas tienen que coincidir.
        import subprocess, tempfile
        rutas = ["tests/golden/a.json", "tests/golden/sub/b.json", "tests/goldenx.py",
                 "gen/x.py", "src/gen/x.py", "a.min.js", "lib/a.min.js", "README.md"]
        patrones = "tests/golden/**\ngen\n*.min.js"
        with tempfile.TemporaryDirectory() as d:
            for ruta in rutas:
                os.makedirs(os.path.join(d, os.path.dirname(ruta)), exist_ok=True)
                open(os.path.join(d, ruta), "w").close()
            subprocess.run(["git", "init", "-q", d], check=True)
            subprocess.run(["git", "-C", d, "add", "."], check=True)
            excl = [":(exclude)%s" % p for p in patrones.split("\n")]
            incluidas = set(subprocess.run(
                ["git", "-C", d, "ls-files", "--", "."] + excl, check=True,
                capture_output=True, text=True).stdout.split())
        with mock.patch.dict(os.environ, {"MISTRAL_EXCLUIDOS": patrones}):
            for ruta in rutas:
                with self.subTest(ruta=ruta):
                    self.assertEqual(mr.excluido(ruta), ruta not in incluidas)

    def test_modelo_por_defecto_es_el_decidido(self):
        self.assertEqual(mr.MODELO, "zai-glm-5-3-flash")


if __name__ == "__main__":
    unittest.main()
