"""Script de revisión con Mistral (.github/scripts/mistral_review.py).

Sin red ni API: se prueban el diff, el prompt, el parseo del JSON, el
comentario y la publicación (con `subprocess` simulado).
"""
import ast
from datetime import datetime, timezone
import io
import urllib.error
import importlib.util
import json
import os
import signal
import subprocess
import tempfile
import unittest
from unittest import mock

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "mistral_review", os.path.join(_RAIZ, "scripts", "mistral_review.py"))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)

HALLAZGO = {"severidad": "media", "archivo": "a.py", "fragmento": "x = `1`",
            "problema": "El ancla quita la marca", "escenario": "texto truncado -> eco_distinto",
            "arreglo": "conservar la marca"}
COMPROBACION = {"archivo": "a.py", "escenario": "entrada vacía",
               "conclusion": "la guarda devuelve error antes de escribir",
               "estado": "revisado", "fragmento": "x = `1`"}


class TestDiff(unittest.TestCase):
    def test_excluye_generados_y_lleva_contexto(self):
        with tempfile.TemporaryDirectory() as d:
            def git(*a):
                subprocess.run(["git", "-C", d] + list(a), check=True,
                               capture_output=True)
            git("init", "-q")
            git("config", "user.email", "t@t"); git("config", "user.name", "t")
            os.makedirs(os.path.join(d, "tests", "i18n"))
            with open(os.path.join(d, "a.py"), "w") as fh:
                fh.write("".join("l%d\n" % i for i in range(30)))
            with open(os.path.join(d, "tests", "i18n", "linea_base.json"), "w") as fh:
                fh.write("{}\n")
            git("add", "-A"); git("commit", "-qm", "base")
            base = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"],
                                  capture_output=True, text=True).stdout.strip()
            with open(os.path.join(d, "a.py"), "a") as fh:
                fh.write("nuevo\n")
            with open(os.path.join(d, "tests", "i18n", "linea_base.json"), "w") as fh:
                fh.write('{"x": 1}\n')
            git("commit", "-qam", "cambio")
            viejo = os.getcwd()
            try:
                os.chdir(d)
                # Exclusión por parámetro del repo que llama (MISTRAL_EXCLUIDOS).
                with mock.patch.dict(os.environ, {"MISTRAL_EXCLUIDOS": "tests/i18n/linea_base.json"}):
                    diff = mr.obtener_diff(base, "HEAD")
                sin = mr.obtener_diff(base, "HEAD")
            finally:
                os.chdir(viejo)
        self.assertIn("+nuevo", diff)
        self.assertIn(" l20", diff)            # -U10: contexto amplio
        self.assertNotIn("linea_base.json", diff)
        self.assertIn("linea_base.json", sin)   # sin parámetro, nada se excluye


class TestPrompt(unittest.TestCase):
    def test_lleva_guia_titulo_y_diff_entero(self):
        msgs, recortado = mr.construir_mensajes("T", "D", "+x\n" * 10,
                                                ("CLAUDE.md", "regla 1"))
        u = msgs[1]["content"]
        self.assertFalse(recortado)
        for trozo in ("Guía del proyecto (CLAUDE.md)", "regla 1", "## Título\nT", "+x"):
            self.assertIn(trozo, u)
        self.assertIn("DATOS del PR", msgs[0]["content"])

    def test_recorta_y_lo_dice(self):
        diff = "+" + "x" * (mr.MAX_DIFF + 10)
        msgs, recortado = mr.construir_mensajes("T", None, diff, (None, ""))
        self.assertTrue(recortado)
        self.assertIn("recortado a %d" % mr.MAX_DIFF, msgs[1]["content"])
        self.assertNotIn("Guía del proyecto", msgs[1]["content"])


class TestRespuesta(unittest.TestCase):
    def test_interpreta_el_esquema(self):
        r = mr.interpretar(json.dumps({"resumen": "R", "hallazgos": [HALLAZGO]}))
        self.assertEqual(r, ("R", [HALLAZGO]))
        for malo in ("no json", "[]", json.dumps({"resumen": "R"}), None):
            self.assertIsNone(mr.interpretar(malo))

    def test_rechaza_hallazgos_incompletos_sin_convertirlos_en_exito(self):
        for campo in HALLAZGO:
            for valor in (None, "", "  ", [], 123):
                with self.subTest(campo=campo, valor=valor):
                    malo = dict(HALLAZGO, **{campo: valor})
                    self.assertIsNone(mr.interpretar(json.dumps(
                        {"resumen": "R", "hallazgos": [HALLAZGO, malo]})))
        for malo in ({}, "x", dict(HALLAZGO, severidad="crítica")):
            self.assertIsNone(mr.interpretar(json.dumps(
                {"resumen": "R", "hallazgos": [malo]})))

    def test_resumen_obligatorio_y_revision_parcial(self):
        for resumen in (None, [], 123, "", "  "):
            self.assertIsNone(mr.interpretar(json.dumps(
                {"resumen": resumen, "hallazgos": []})))
        md = mr.renderizar(("R", []), "m", mr.MAX_DIFF + 1, True)
        self.assertIn("Sin hallazgos en la parte revisada", md)
        self.assertNotIn("**Sin hallazgos.**", md)

    def test_renderiza_hallazgos_ordenados(self):
        bajo = dict(HALLAZGO, severidad="baja", problema="menor")
        alto = dict(HALLAZGO, severidad="alta", problema="grave")
        md = mr.renderizar(("R", [bajo, alto]), "codestral-latest", 100, False)
        self.assertTrue(md.startswith(mr.MARCA))
        self.assertIn("[Mistral AI (@mistralai)](https://github.com/mistralai)", md)
        self.assertLess(md.index("grave"), md.index("menor"))
        self.assertIn("🔴 **alta** · `a.py`", md)
        self.assertIn("Escenario: `texto truncado -> eco_distinto`", md)
        self.assertIn("`` x = `1` ``", md)

    def test_publica_como_texto_literal_la_respuesta_y_conserva_enlaces_propios(self):
        texto = ("@equipo [nota](https://example.invalid/nota) "
                 "![figura](https://example.invalid/figura.png) <!-- nota --> `f()`")
        hallazgo = dict(HALLAZGO, archivo="a`b.py", problema=texto, escenario=texto, arreglo=texto)
        comprobacion = dict(COMPROBACION, archivo="a`b.py", escenario=texto, conclusion=texto)
        url = "https://github.com/o/r/actions/runs/1"
        md = mr.renderizar((texto, [hallazgo]), "m", 10, False,
                           comprobaciones=[comprobacion], ejecucion_url=url)
        literal = texto.replace("@equipo", "@\u200bequipo").replace("<!--", "<\u200b!--")
        self.assertEqual(md.count("`` %s ``" % literal), 6)
        self.assertEqual(md.count("`` a`b.py ``"), 2)
        self.assertIn("[Ejecución de Actions](%s)" % url, md)

    def test_se_puede_copiar_una_cita_con_decorador(self):
        hallazgo = dict(HALLAZGO, fragmento="@staticmethod", problema="Avisa a @equipo")
        md = mr.renderizar(("R", [hallazgo]), "m", 10, False)
        prefijo = "   - Fragmento: `"
        linea = next(l for l in md.splitlines() if l.startswith(prefijo))
        self.assertTrue(linea.endswith("`"))
        copiado = linea[len(prefijo):-1]
        self.assertEqual(copiado, "@staticmethod")
        ast.parse(copiado + "\ndef ejemplo():\n    pass\n")
        self.assertIn("`Avisa a @\u200bequipo`", md)

    def test_tachar_un_valor_al_final_conserva_el_delimitador(self):
        for valor in ("valor-ficticio", "***"):
            with self.subTest(valor=valor):
                md = mr.renderizar(("password=" + valor, []), "m", 10, False)
                self.assertIn("\n` password=*** `\n", md)

    def test_sin_hallazgos_y_formato_raro(self):
        self.assertIn("**Sin hallazgos.**",
                      mr.renderizar(("R", []), "m", 1, False))
        md = mr.renderizar(None, "m", 200000, True)
        self.assertIn("Revisión no válida", md)
        self.assertNotIn("texto libre", md)
        self.assertIn("recortado de 200000", md)


class TestPublicar(unittest.TestCase):
    def test_dos_resultados_crean_comentarios_sin_editar_ni_leer_el_historial(self):
        anterior = {"body": mr.MARCA + "\nRevisión anterior"}
        comentarios = [dict(anterior)]

        def crear(comando, **kwargs):
            self.assertEqual(comando, ["gh", "api", "-X", "POST",
                "repos/o/r/issues/7/comments", "--input", "-"])
            comentarios.append(json.loads(kwargs["input"]))
            return mock.Mock(stdout="{}")

        cuerpos = ["cuerpo con \"comillas\"\ny saltos",
                   mr.renderizar(None, "m", 1, False, error="fallo de API")]
        with mock.patch.object(mr.subprocess, "run", side_effect=crear) as run:
            for cuerpo in cuerpos:
                mr.publicar("o/r", 7, cuerpo)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(comentarios, [anterior] + [{"body": c} for c in cuerpos])

    def test_error_al_crear_se_propaga_sin_intentar_editar_otros_comentarios(self):
        with mock.patch.object(mr.subprocess, "run", side_effect=
                subprocess.CalledProcessError(1, "gh")) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                mr.publicar("o/r", 7, "cuerpo")
        run.assert_called_once()


class TestAPI(unittest.TestCase):
    def respuesta(self, contenido="{}", fin="stop"):
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = json.dumps({
            "choices": [{"message": {"content": contenido}, "finish_reason": fin}]
        }).encode()
        return resp

    def error(self, codigo, detalle="", headers=None):
        return urllib.error.HTTPError(mr.API, codigo, "error", headers or {},
                                      io.BytesIO(detalle.encode()))

    def test_reintenta_transitorios(self):
        for error in (self.error(429), self.error(503),
                      urllib.error.URLError("red"), TimeoutError()):
            with self.subTest(error=error), mock.patch.object(
                    mr.urllib.request, "urlopen", side_effect=[error, self.respuesta()]) as abrir, \
                    mock.patch.object(mr.time, "sleep"):
                self.assertEqual(mr.llamar_mistral([], "m", "clave"), "{}")
                self.assertEqual(abrir.call_count, 2)

    def test_limite_reintentos_y_errores_permanentes(self):
        for codigo, llamadas in ((503, 3), (401, 1), (400, 1)):
            with self.subTest(codigo=codigo), mock.patch.object(
                    mr.urllib.request, "urlopen", side_effect=self.error(codigo)) as abrir, \
                    mock.patch.object(mr.time, "sleep"):
                with self.assertRaises(urllib.error.HTTPError):
                    mr.llamar_mistral([], "m", "clave")
                self.assertEqual(abrir.call_count, llamadas)

    def test_fallback_solo_por_formato_no_soportado(self):
        with mock.patch.object(mr.urllib.request, "urlopen", side_effect=[
                self.error(400, "response_format json_object is not supported"),
                self.respuesta()]) as abrir:
            mr.llamar_mistral([], "m", "clave")
        cuerpos = [json.loads(c.args[0].data) for c in abrir.call_args_list]
        self.assertIn("response_format", cuerpos[0])
        self.assertNotIn("response_format", cuerpos[1])

    def test_rechaza_salida_truncada_o_vacia(self):
        for contenido, fin in (("{}", "length"), (None, "stop"), ("", "stop"),
                               ("{}", "model_length"), ("{}", "error")):
            with self.subTest(contenido=contenido, fin=fin), mock.patch.object(
                    mr.urllib.request, "urlopen", return_value=self.respuesta(contenido, fin)):
                with self.assertRaises(ValueError):
                    mr.llamar_mistral([], "m", "clave")


    def test_retry_after_segundos_fecha_y_valor_invalido(self):
        ahora = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
        for valor, espera in (("30", 30), ("Sun, 25 Oct 2026 12:00:40 GMT", 40),
                              ("no válido", 1)):
            with self.subTest(valor=valor), mock.patch.object(
                    mr.urllib.request, "urlopen", side_effect=[
                        self.error(429, headers={"Retry-After": valor}), self.respuesta()]), \
                    mock.patch.object(mr.time, "sleep") as dormir, \
                    mock.patch.object(mr, "datetime") as reloj:
                reloj.now.return_value = ahora
                mr.llamar_mistral([], "m", "clave")
                dormir.assert_called_once_with(espera)

    def test_no_reintenta_antes_de_un_retry_after_excesivo(self):
        with mock.patch.object(mr.urllib.request, "urlopen", side_effect=self.error(
                429, headers={"Retry-After": "3600"})) as abrir, \
                mock.patch.object(mr.time, "sleep") as dormir:
            with self.assertRaises(urllib.error.HTTPError):
                mr.llamar_mistral([], "m", "clave")
            self.assertEqual(abrir.call_count, 1)
            dormir.assert_not_called()

    def test_retry_after_que_no_cabe_en_el_plazo_no_espera_ni_reintenta(self):
        error = self.error(429, headers={"Retry-After": "15"})
        with mock.patch.object(mr.time, "monotonic", return_value=100), \
                mock.patch.object(mr.urllib.request, "urlopen", side_effect=error) as abrir, \
                mock.patch.object(mr.time, "sleep") as dormir:
            with self.assertRaisesRegex(mr.PlazoRevisionAgotado, "la espera del reintento"):
                mr.llamar_mistral([], "m", "clave", plazo=110)
        abrir.assert_called_once()
        self.assertEqual(abrir.call_args.kwargs["timeout"], 10)
        dormir.assert_not_called()

    def test_reintento_http_usa_el_tiempo_que_queda_del_mismo_plazo(self):
        respuestas = [self.error(429, headers={"Retry-After": "3"}), self.respuesta()]
        with mock.patch.object(mr.time, "monotonic", side_effect=[100, 105, 108, 109]), \
                mock.patch.object(mr.urllib.request, "urlopen", side_effect=respuestas) as abrir, \
                mock.patch.object(mr.time, "sleep") as dormir:
            self.assertEqual(mr.llamar_mistral([], "m", "clave", plazo=110), "{}")
        dormir.assert_called_once_with(3)
        self.assertEqual([llamada.kwargs["timeout"] for llamada in abrir.call_args_list], [10, 2])

    @unittest.skipUnless(hasattr(signal, "setitimer"), "requiere temporizador POSIX")
    def test_el_plazo_corta_una_lectura_bloqueada_sin_reintentar(self):
        respuesta = self.respuesta()
        respuesta.__enter__.return_value.read.side_effect = lambda: signal.raise_signal(signal.SIGALRM)
        plazo = mr.time.monotonic() + 60
        manejador = signal.getsignal(signal.SIGALRM)
        with mock.patch.object(mr.urllib.request, "urlopen", return_value=respuesta) as abrir, \
                self.assertRaises(mr.PlazoRevisionAgotado), mr._limitar_revision(plazo):
            mr.llamar_mistral([], "m", "clave", plazo=plazo)
        abrir.assert_called_once()
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        self.assertEqual(signal.getsignal(signal.SIGALRM), manejador)

    def test_fallback_comparte_el_limite_de_tres_solicitudes(self):
        with mock.patch.object(mr.urllib.request, "urlopen", side_effect=[
                self.error(503), self.error(400, "json_object is unsupported"),
                self.error(503)]) as abrir, mock.patch.object(mr.time, "sleep"):
            with self.assertRaises(urllib.error.HTTPError):
                mr.llamar_mistral([], "m", "clave")
            self.assertEqual(abrir.call_count, 3)
            self.assertNotIn("response_format", json.loads(abrir.call_args.args[0].data))

    def test_estructura_de_api_invalida_da_error_controlado(self):
        for cuerpo in ({}, {"choices": []}, {"choices": [None]}, {"choices": [{}]}, None):
            resp = mock.MagicMock()
            resp.__enter__.return_value.read.return_value = json.dumps(cuerpo).encode()
            with self.subTest(cuerpo=cuerpo), mock.patch.object(
                    mr.urllib.request, "urlopen", return_value=resp):
                with self.assertRaises(ValueError):
                    mr.llamar_mistral([], "m", "clave")


class TestMain(unittest.TestCase):
    DIFF = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 0\n+x = `1`\n"

    def ejecutar(self, respuesta=None, fallo=None, entorno=None):
        if respuesta and respuesta.startswith("{"):
            datos = json.loads(respuesta)
            datos.setdefault("comprobaciones", [COMPROBACION])
            respuesta = json.dumps(datos)
        evento = json.dumps({"pull_request": {
            "number": 7, "base": {"sha": "base"}, "head": {"sha": "abc123"}}})
        # GH_TOKEN vacío: sin él no se consulta el historial del PR (sin red).
        variables = {"MISTRAL_API_KEY": "clave", "GITHUB_EVENT_PATH": "evento.json",
                     "GH_TOKEN": "",
                     "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": ""}
        variables.update(entorno or {})
        with mock.patch.dict(os.environ, variables), \
                mock.patch("builtins.open", mock.mock_open(read_data=evento)), \
                mock.patch.object(mr, "obtener_diff", return_value=self.DIFF), \
                mock.patch.object(mr, "obtener_rutas_diff", return_value={"a.py"}), \
                mock.patch.object(mr, "leer_guia", return_value=(None, "")), \
                mock.patch.object(mr, "obtener_contexto", return_value="contexto"), \
                mock.patch.object(mr, "llamar_mistral", return_value=respuesta, side_effect=fallo), \
                mock.patch.object(mr, "publicar") as publicar, mock.patch("builtins.print"):
            codigo = mr.main()
        publicar.assert_called_once()
        return codigo, publicar.call_args.args[2]

    def test_la_peticion_solo_incluye_descripcion_si_se_habilita(self):
        titulo = "Título de prueba"
        descripcion = "Descripción ficticia del PR"
        final = "fin-de-la-descripción"
        evento = json.dumps({"pull_request": {
            "number": 7, "base": {"sha": "base"}, "head": {"sha": "abc123"},
            "title": titulo, "body": f"{descripcion}\npassword=valor-ficticio\n"
                                     + "." * 4000 + final}})
        resultado = json.dumps({"resumen": "R", "hallazgos": [],
                                "comprobaciones": [COMPROBACION]})
        respuesta = json.dumps({"choices": [{"message": {"content": resultado},
                                             "finish_reason": "stop"}]}).encode()
        for proveedor, modelo in (("mistral", mr.MODELO), ("openrouter", "pruebas/modelo")):
            for opcion in (None, "false", "1", "true"):
                variables = {"GITHUB_EVENT_PATH": "evento.json", "GITHUB_REPOSITORY": "o/r",
                             "MISTRAL_PROVEEDOR": proveedor, "MISTRAL_MODEL": modelo,
                             mr.PROVEEDORES[proveedor]["secreto"]: "clave-ficticia"}
                if opcion is not None:
                    variables["MISTRAL_INCLUIR_DESCRIPCION"] = opcion
                with self.subTest(proveedor=proveedor, opcion=opcion), \
                        mock.patch.dict(os.environ, variables, clear=True), \
                        mock.patch("builtins.open", mock.mock_open(read_data=evento)), \
                        mock.patch.object(mr, "obtener_diff", return_value=self.DIFF), \
                        mock.patch.object(mr, "obtener_rutas_diff", return_value={"a.py"}), \
                        mock.patch.object(mr, "leer_guia", return_value=("REVIEW.md", "Regla de prueba")), \
                        mock.patch.object(mr, "obtener_contexto", return_value="Contexto de prueba"), \
                        mock.patch.object(mr.urllib.request, "urlopen",
                                          return_value=io.BytesIO(respuesta)) as abrir, \
                        mock.patch.object(mr, "publicar"), mock.patch("builtins.print"):
                    self.assertEqual(mr.main(), 0)
                    abrir.assert_called_once()
                    peticion = abrir.call_args.args[0]
                    self.assertEqual(peticion.full_url, mr.PROVEEDORES[proveedor]["api"])
                    mensaje = json.loads(peticion.data)["messages"][1]["content"]
                    for texto in (titulo, "Regla de prueba", "Contexto de prueba", "+x = `1`"):
                        self.assertIn(texto, mensaje)
                    self.assertNotIn("valor-ficticio", mensaje)
                    self.assertNotIn(final, mensaje)
                    if opcion == "true":
                        self.assertIn(descripcion, mensaje)
                        self.assertIn("password=***", mensaje)
                    else:
                        self.assertNotIn(descripcion, mensaje)
                        self.assertNotIn("password=", mensaje)

    def test_respuesta_invalida_publica_aviso_y_falla_el_check(self):
        for texto, codigo in (("no json", 1),
                (json.dumps({"resumen": "R", "hallazgos": []}), 0),
                (json.dumps({"resumen": "R", "hallazgos": [HALLAZGO]}), 0),
                (json.dumps({"resumen": "R", "hallazgos": [dict(
                    HALLAZGO, archivo="inventado.py")]}), 1)):
            with self.subTest(texto=texto):
                resultado, cuerpo = self.ejecutar(texto)
                self.assertEqual(resultado, codigo)
                self.assertIn("Commit revisado: `abc123`", cuerpo)
                if codigo:
                    self.assertIn("Revisión no válida", cuerpo)
                    self.assertNotIn("**Sin hallazgos.**", cuerpo)

    def test_fallo_de_api_publica_informe_sin_filtrar_detalles(self):
        fallos = (TimeoutError("dato privado"), urllib.error.URLError("dato privado"),
                  ValueError("dato privado"),
                  urllib.error.HTTPError(mr.API, 401, "dato privado", {}, None))
        for fallo in fallos:
            with self.subTest(fallo=fallo):
                codigo, cuerpo = self.ejecutar(fallo=fallo)
                self.assertEqual(codigo, 1)
                self.assertIn("Revisión no completada", cuerpo)
                self.assertIn("Commit revisado: `abc123`", cuerpo)
                self.assertNotIn("dato privado", cuerpo)
                self.assertNotIn("Sin hallazgos", cuerpo)
                self.assertNotIn("sustituye", cuerpo)

    def test_reintento_por_corte_comparte_plazo_y_deja_resumen_al_agotarse(self):
        plazos = []

        def responder(*args, plazo, **kwargs):
            plazos.append(plazo)
            if len(plazos) == 1:
                raise mr.RespuestaIncompleta("length")
            raise mr.PlazoRevisionAgotado()

        with mock.patch.object(mr.time, "monotonic", return_value=100), \
                mock.patch.object(mr, "escribir_resumen") as resumen:
            codigo, cuerpo = self.ejecutar(fallo=responder)
        self.assertEqual(plazos, [100 + mr.TIEMPO_MAX_REVISION] * 2)
        self.assertEqual(codigo, 1)
        self.assertIn("se agotó el plazo total de la revisión", cuerpo)
        resumen.assert_called_once_with(cuerpo)

    def test_cada_intento_del_mismo_commit_identifica_su_ejecucion_en_todos_los_estados(self):
        for intento in ("1", "2"):
            for respuesta, fallo in ((json.dumps({"resumen": "R", "hallazgos": []}), None),
                                     ("no json", None), (None, TimeoutError())):
                with self.subTest(intento=intento, respuesta=respuesta, fallo=fallo):
                    _, cuerpo = self.ejecutar(respuesta, fallo, entorno={
                        "GITHUB_SERVER_URL": "https://github.example.test/",
                        "GITHUB_RUN_ID": "123456", "GITHUB_RUN_ATTEMPT": intento})
                    self.assertIn("Commit revisado: `abc123`", cuerpo)
                    self.assertIn("https://github.example.test/o/r/actions/runs/123456/attempts/"
                                  + intento, cuerpo)
                    self.assertIn("intento %s" % intento, cuerpo)

    def test_fuera_de_actions_no_inventa_un_enlace(self):
        _, cuerpo = self.ejecutar(json.dumps({"resumen": "R", "hallazgos": []}))
        self.assertNotIn("/actions/runs/", cuerpo)

    def test_evidencia_fuera_de_la_parte_enviada_no_cuenta(self):
        with mock.patch.object(mr, "MAX_DIFF", self.DIFF.index("+x = `1`")):
            codigo, cuerpo = self.ejecutar(json.dumps(
                {"resumen": "R", "hallazgos": [HALLAZGO]}))
        self.assertEqual(codigo, 1)
        self.assertIn("Revisión no válida", cuerpo)

    def test_una_cita_recortada_deja_la_comprobacion_pendiente(self):
        diff = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                "@@ -1 +1,3 @@\n contexto = 0\n+visible = 1\n+recortado = 2\n")
        respuesta = {"resumen": "R", "hallazgos": [], "comprobaciones": [
            dict(COMPROBACION, fragmento="recortado = 2")]}
        with mock.patch.object(self, "DIFF", diff), mock.patch.object(mr, "MAX_DIFF", len(diff) - 1):
            codigo, cuerpo = self.ejecutar(json.dumps(respuesta))
        self.assertEqual(codigo, 0)
        self.assertIn("Comprobaciones por lectura pendientes (1)", cuerpo)
        self.assertNotIn("recortado = 2", cuerpo)

    def test_el_informe_solo_muestra_citas_de_cambios_del_archivo_indicado(self):
        diff = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                "@@ -1,2 +1,3 @@\n contexto = True\n-x = 0\n+x = `1`\n+password=dato-ficticio\n"
                "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n"
                "@@ -0,0 +1 @@\n+otro_archivo = True\n")
        casos = (("+x = `1`", True), ("x = 0", True), (None, False), ([], False),
                 ("x = `", False), ("contexto = True", False), ("otro_archivo = True", False),
                 ("password=***", True), ("password=dato-ficticio", False),
                 ("x = 0\nx = `1`", False))
        for fragmento, valida in casos:
            comprobacion = dict(COMPROBACION)
            if fragmento is None:
                del comprobacion["fragmento"]
            else:
                comprobacion["fragmento"] = fragmento
            respuesta = {"resumen": "R", "hallazgos": [], "comprobaciones": [comprobacion]}
            with self.subTest(fragmento=fragmento), mock.patch.object(self, "DIFF", diff):
                codigo, cuerpo = self.ejecutar(json.dumps(respuesta))
                self.assertEqual(codigo, 0)
                self.assertNotIn("dato-ficticio", cuerpo)
                if valida:
                    self.assertIn("**revisado**", cuerpo)
                    self.assertIn("Fragmento: " + mr._codigo(fragmento), cuerpo)
                    self.assertIn(COMPROBACION["conclusion"], cuerpo)
                else:
                    self.assertIn("**pendiente**", cuerpo)
                    self.assertIn("Falta una cita verificable", cuerpo)
                    self.assertNotIn("Fragmento", cuerpo)
                    self.assertNotIn(COMPROBACION["conclusion"], cuerpo)

    def test_diff_vacio_no_consulta_api_ni_exige_casos_inventados(self):
        with mock.patch.object(self, "DIFF", ""):
            codigo, cuerpo = self.ejecutar(fallo=AssertionError("no llamar API"))
        self.assertEqual(codigo, 0)
        self.assertIn("No hay diff revisable", cuerpo)


class TestSinClave(unittest.TestCase):
    def test_sin_clave_no_falla(self):
        with mock.patch.dict(os.environ, {"MISTRAL_API_KEY": ""}):
            self.assertEqual(mr.main(), 0)


class TestComprobaciones(unittest.TestCase):
    def test_diagnostico_distingue_rechazos_sin_exponer_contenido(self):
        base = {"resumen": "R", "hallazgos": [], "comprobaciones": [COMPROBACION]}
        casos = [
            ("dato privado", "json_invalido"),
            (dict(base, hallazgos=None), "hallazgos_no_lista"),
            (dict(base, resumen=""), "resumen_invalido"),
            (dict(base, comprobaciones=[]), "comprobaciones_cantidad_o_tipo"),
            (dict(base, comprobaciones=[{}]), "comprobacion_0_campos_invalidos"),
            (dict(base, comprobaciones=[dict(COMPROBACION, estado="dato privado")]),
             "comprobacion_0_estado_invalido"),
            (dict(base, comprobaciones=[dict(COMPROBACION, archivo="dato privado")]),
             "comprobacion_0_archivo_fuera_del_pr"),
            (dict(base, hallazgos=[{}]), "hallazgo_0_campos_invalidos"),
            (dict(base, hallazgos=[dict(HALLAZGO, severidad="dato privado")]),
             "hallazgo_0_severidad_invalida"),
            (dict(base, hallazgos=[dict(HALLAZGO, fragmento="dato privado")]),
             "hallazgo_0_cita_fuera_del_diff(sin_coincidencia)"),
        ]
        for datos, esperado in casos:
            with self.subTest(codigo=esperado):
                diagnostico = []
                texto = datos if isinstance(datos, str) else json.dumps(datos)
                self.assertIsNone(mr.interpretar(texto, TestMain.DIFF, True,
                                                diagnostico=diagnostico))
                self.assertEqual(diagnostico, [esperado])
                self.assertNotIn('dato privado', str(diagnostico))
        diagnostico = []
        self.assertIsNotNone(mr.interpretar(json.dumps(base), TestMain.DIFF, True,
                                           diagnostico=diagnostico))
        self.assertEqual(diagnostico, [])

    def test_causa_de_cita_rechazada_sin_contenido_ni_relajar_la_aceptacion(self):
        base = {"resumen": "R", "comprobaciones": [COMPROBACION]}
        casos = (("x = `", "a.py", "parcial"),
                 ("x = `1`\ny = 2", "a.py", "multilinea"),
                 ("x = `1`", "otro.py", "archivo_sin_cambios"),
                 ("dato privado", "a.py", "sin_coincidencia"))
        for fragmento, archivo, causa in casos:
            with self.subTest(causa=causa):
                h = dict(HALLAZGO, fragmento=fragmento, archivo=archivo)
                diagnostico, sin_verificar = [], []
                self.assertIsNone(mr.interpretar(json.dumps(dict(base, hallazgos=[h])),
                    TestMain.DIFF, True, diagnostico=diagnostico, sin_verificar=sin_verificar))
                self.assertEqual(diagnostico, ["hallazgo_0_cita_fuera_del_diff(%s)" % causa])
                self.assertNotIn("privado", str(diagnostico))
                self.assertEqual(sin_verificar, [(h, causa)])

    def test_cita_con_otra_sangria_cuenta_pero_no_el_texto_distinto(self):
        base = {"resumen": "R", "comprobaciones": [COMPROBACION]}
        for fragmento, valida in (("    x = `1`  ", True), ("x = `1`", True),
                                  ("+x = `1`", True), ("+   x = `1`", True),
                                  ("x  = `1`", False), ("x = `1`;", False),
                                  ("   ", False)):
            with self.subTest(fragmento=fragmento):
                texto = json.dumps(dict(base, hallazgos=[dict(HALLAZGO, fragmento=fragmento)]))
                r = mr.interpretar(texto, TestMain.DIFF, True)
                self.assertEqual(r is not None, valida)

    def test_cita_sin_letras_ni_numeros_no_prueba_nada(self):
        diff = TestMain.DIFF + "@@ -3 +3 @@\n+    }\n+\n"
        for fragmento in ("}", "+}", "+", " ) "):
            with self.subTest(fragmento=fragmento):
                diagnostico, sin_verificar = [], []
                texto = json.dumps({"resumen": "R", "comprobaciones": [COMPROBACION],
                                    "hallazgos": [dict(HALLAZGO, fragmento=fragmento)]})
                self.assertIsNone(mr.interpretar(texto, diff, True, diagnostico=diagnostico,
                                                 sin_verificar=sin_verificar))
                self.assertEqual(diagnostico, ["hallazgo_0_cita_fuera_del_diff(sin_contenido)"])

    def test_hallazgos_no_aceptados_se_publican_inertes(self):
        malo = dict(HALLAZGO, archivo="x` @octocat [clic](https://evil.invalid) <img src=x> `",
                    fragmento="no existe", problema="ver @octocat ![p](https://evil.invalid/p.png)",
                    escenario="<details>", arreglo="[aquí](https://evil.invalid)")
        texto = json.dumps({"resumen": "R", "hallazgos": [malo], "comprobaciones": [COMPROBACION]})
        _, cuerpo = TestMain().ejecutar(texto)
        seccion = cuerpo.split("sin aceptar", 1)[1]
        self.assertIn("hallazgo_0", seccion)
        self.assertIn("`` x` @\u200boctocat [clic](https://evil.invalid) <img src=x> ` ``", seccion)
        self.assertIn("`ver @\u200boctocat ![p](https://evil.invalid/p.png)`", seccion)
        self.assertIn("`<details>`", seccion)
        self.assertIn("`[aquí](https://evil.invalid)`", seccion)

    def test_cita_mala_no_borra_las_demas_ni_aprueba_el_check(self):
        malo = dict(HALLAZGO, fragmento="línea que no existe", problema="Defecto B")
        texto = json.dumps({"resumen": "R", "hallazgos": [HALLAZGO, malo],
                            "comprobaciones": [COMPROBACION]})
        sin_verificar = []
        self.assertIsNone(mr.interpretar(texto, TestMain.DIFF, True,
                                         sin_verificar=sin_verificar))
        self.assertEqual([c for _, c in sin_verificar], [None, "sin_coincidencia"])
        codigo, cuerpo = TestMain().ejecutar(texto)
        self.assertEqual(codigo, 1)
        self.assertIn("Revisión no válida", cuerpo)
        self.assertIn("Hallazgos devueltos sin aceptar (2)", cuerpo)
        self.assertIn("El ancla quita la marca", cuerpo)
        self.assertIn("Defecto B", cuerpo)
        self.assertIn("verificada en el diff", cuerpo)
        self.assertIn("no verificada (`sin_coincidencia`)", cuerpo)
        self.assertNotIn("Sin hallazgos", cuerpo)

    def test_otro_rechazo_no_muestra_hallazgos_sin_verificar(self):
        # Si falla el esquema, no hay nada fiable que mostrar.
        malo = dict(HALLAZGO, fragmento="línea que no existe")
        sin_verificar = []
        texto = json.dumps({"resumen": "R", "hallazgos": [malo, {}],
                            "comprobaciones": [COMPROBACION]})
        self.assertIsNone(mr.interpretar(texto, TestMain.DIFF, True,
                                         sin_verificar=sin_verificar))
        self.assertEqual(sin_verificar, [])
        _, cuerpo = TestMain().ejecutar(texto)
        self.assertNotIn("sin aceptar", cuerpo)

    def test_exige_escenarios_y_rutas_del_diff_sin_aceptar_exito_vacio(self):
        base = {"resumen": "R", "hallazgos": [], "comprobaciones": [COMPROBACION]}
        self.assertIsNotNone(mr.interpretar(json.dumps(base), TestMain.DIFF, True))
        for valor in (None, [], [{}], [dict(COMPROBACION, estado="ejecutado")],
                      [dict(COMPROBACION, escenario="")],
                      [dict(COMPROBACION, archivo="inventado.py")]):
            with self.subTest(valor=valor):
                self.assertIsNone(mr.interpretar(json.dumps(dict(
                    base, comprobaciones=valor)), TestMain.DIFF, True))
        del base["comprobaciones"]
        self.assertIsNone(mr.interpretar(json.dumps(base), TestMain.DIFF, True))

    def test_renderiza_casos_pendientes_como_lectura_no_como_tests_ejecutados(self):
        md = mr.renderizar(("R", []), "m", 10, False,
            comprobaciones=[dict(COMPROBACION, estado="pendiente")])
        self.assertIn("sin ejecutar tests", md)
        self.assertIn("**pendiente**", md)
        self.assertIn(COMPROBACION["escenario"], md)

    def test_contexto_del_commit_acotado_y_enviado_como_datos(self):
        with mock.patch.object(mr.subprocess, "run", return_value=mock.Mock(
                returncode=0, stdout=b"def funcion():\n    pass\n")) as run, \
                mock.patch.object(mr, "MAX_ARCHIVO", 12):
            contexto = mr.obtener_contexto(TestMain.DIFF, "abc123")
        self.assertEqual(run.call_args.args[0], ["git", "show", "abc123:a.py"])
        # Ni una línea cabe en 12 caracteres: se dice, sin partir líneas.
        self.assertIn("omitido por presupuesto: funcion", contexto)
        self.assertNotIn("pass", contexto)
        mensajes, _ = mr.construir_mensajes("T", "D", TestMain.DIFF,
                                           (None, ""), contexto)
        self.assertIn(contexto, mensajes[1]["content"])

    def test_resumen_sobrevive_a_fallo_de_publicacion(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "resumen.md")
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ruta}), \
                    mock.patch.object(mr, "publicar", side_effect=OSError("privado")):
                # El helper anterior verifica publicación correcta; aquí main se
                # ejecuta directamente para comprobar el fallo de permisos.
                evento = json.dumps({"pull_request": {"number": 7,
                    "head": {"sha": "abc123"}, "base": {"sha": "base"}}})
                with mock.patch.dict(os.environ, {"MISTRAL_API_KEY": "clave", "GH_TOKEN": "",
                        "GITHUB_EVENT_PATH": "evento", "GITHUB_REPOSITORY": "o/r"}), \
                        mock.patch.object(mr, "obtener_diff", return_value=TestMain.DIFF), \
                        mock.patch.object(mr, "obtener_rutas_diff", return_value={"a.py"}), \
                        mock.patch.object(mr, "obtener_contexto", return_value=""), \
                        mock.patch.object(mr, "leer_guia", return_value=(None, "")), \
                        mock.patch.object(mr, "llamar_mistral", return_value=json.dumps({
                            "resumen": "R", "hallazgos": [],
                            "comprobaciones": [COMPROBACION]})), \
                        mock.patch("builtins.open", mock.mock_open(read_data=evento)) as abrir, \
                        mock.patch("builtins.print"):
                    self.assertEqual(mr.main(), 1)
                    abrir.assert_any_call(ruta, "a", encoding="utf-8")
                    escrito = abrir().write.call_args.args[0]
                    self.assertIn("Comprobaciones por lectura", escrito)

    def test_sin_clave_deja_motivo_en_resumen(self):
        with mock.patch.dict(os.environ, {"MISTRAL_API_KEY": ""}), \
                mock.patch.object(mr, "escribir_resumen") as resumen, \
                mock.patch("builtins.print"):
            self.assertEqual(mr.main(), 0)
        self.assertIn("omitida", resumen.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
