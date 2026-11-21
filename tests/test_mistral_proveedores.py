"""Proveedores: Mistral (por defecto) y OpenRouter.

Sin red ni API: `urlopen` y `main()` simulados, como en test_mistral_review.
"""
import io
import json
import os
import urllib.error
import unittest
from unittest import mock

from tests.test_mistral_review import mr, COMPROBACION, HALLAZGO

# Valor partido para que ningún escáner lo tome por una clave real.
CLAVE_OR = "sk-or-" + "valor-sintetico-de-prueba"
CLAVE_MI = "mistral-" + "valor-sintetico-de-prueba"
SIN_PROVEEDOR = {"MISTRAL_PROVEEDOR": "", "MISTRAL_MODEL": "",
                 "MISTRAL_OPENROUTER_PROVEEDOR": ""}


def entorno(**extra):
    variables = dict(SIN_PROVEEDOR)
    variables.update(extra)
    return mock.patch.dict(os.environ, variables)


def respuesta_http(cuerpo):
    datos = cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo).encode()
    resp = mock.MagicMock()
    # Con `with` (el revisor) y sin él (el presupuesto de la evaluación lo envuelve).
    resp.__enter__.return_value.read.return_value = datos
    resp.read.return_value = datos
    return resp


def eleccion(contenido="{}", fin="stop"):
    return {"choices": [{"message": {"content": contenido}, "finish_reason": fin,
                         "native_finish_reason": fin}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "cost": 0.0001}}


def error_http(codigo, cabeceras=None):
    return urllib.error.HTTPError(mr.API_OPENROUTER, codigo, "error", cabeceras or {},
                                  io.BytesIO(b""))


class TestConfiguracion(unittest.TestCase):
    def test_la_configuracion_antigua_sigue_en_mistral(self):
        with entorno():
            self.assertEqual(mr.configuracion(), ("mistral", mr.MODELO, ""))
        with entorno(MISTRAL_MODEL="mistral-large-2512"):
            self.assertEqual(mr.configuracion(), ("mistral", "mistral-large-2512", ""))
        # Sin la variable en absoluto, no solo vacía.
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(mr.configuracion(), ("mistral", mr.MODELO, ""))

    def test_selecciona_openrouter_con_su_modelo(self):
        for valor in ("openrouter", " OpenRouter "):
            with self.subTest(valor=valor), entorno(
                    MISTRAL_PROVEEDOR=valor, MISTRAL_MODEL="z-ai/glm-5.3-flash"):
                self.assertEqual(mr.configuracion(),
                                 ("openrouter", "z-ai/glm-5.3-flash", ""))
        with entorno(MISTRAL_PROVEEDOR="openrouter", MISTRAL_MODEL="otro/x",
                     MISTRAL_OPENROUTER_PROVEEDOR="google-vertex/us-east5"):
            self.assertEqual(mr.configuracion("deepseek/deepseek-v4.1-flash-1017:batch"),
                             ("openrouter", "deepseek/deepseek-v4.1-flash-1017:batch",
                              "google-vertex/us-east5"))

    def test_proveedor_no_reconocido(self):
        for valor in ("anthropic", "mistral,openrouter", "https://ejemplo.test"):
            with self.subTest(valor=valor), entorno(MISTRAL_PROVEEDOR=valor):
                with self.assertRaises(mr.ConfiguracionInvalida) as ctx:
                    mr.configuracion()
                self.assertNotIn(valor, str(ctx.exception))

    def test_openrouter_exige_modelo_sin_cambiar_a_otro(self):
        with entorno(MISTRAL_PROVEEDOR="openrouter"):
            with self.assertRaisesRegex(mr.ConfiguracionInvalida, "obligatorio"):
                mr.configuracion()
        # El modelo de Mistral que sigue en MISTRAL_MODEL no se envía a OpenRouter.
        for modelo in (mr.MODELO, "z-ai/", "/glm", "z ai/glm", "z-ai/glm\nx"):
            with self.subTest(modelo=modelo), entorno(
                    MISTRAL_PROVEEDOR="openrouter", MISTRAL_MODEL=modelo):
                with self.assertRaisesRegex(mr.ConfiguracionInvalida, "organización/modelo"):
                    mr.configuracion()

    def test_mistral_rechaza_un_modelo_de_openrouter_olvidado(self):
        with entorno(MISTRAL_MODEL="z-ai/glm-5.3-flash"):
            with self.assertRaisesRegex(mr.ConfiguracionInvalida, "parece un modelo de OpenRouter"):
                mr.configuracion()
        with entorno(MISTRAL_PROVEEDOR="mistral"):
            with self.assertRaises(mr.ConfiguracionInvalida):
                mr.configuracion("deepseek/deepseek-v4.1-flash")

    def test_rechaza_los_enrutadores_que_cambian_de_modelo(self):
        for modelo in ("openrouter/auto", "openrouter/free"):
            with self.subTest(modelo=modelo), entorno(
                    MISTRAL_PROVEEDOR="openrouter", MISTRAL_MODEL=modelo):
                with self.assertRaisesRegex(mr.ConfiguracionInvalida, "modelo concreto"):
                    mr.configuracion()

    def test_ruta_de_openrouter_no_valida(self):
        for ruta in ("Z-AI", "z-ai;rm", "a/b/c/d/e", "https://x"):
            with self.subTest(ruta=ruta), entorno(
                    MISTRAL_PROVEEDOR="openrouter", MISTRAL_MODEL="z-ai/glm-5.3-flash",
                    MISTRAL_OPENROUTER_PROVEEDOR=ruta):
                with self.assertRaises(mr.ConfiguracionInvalida):
                    mr.configuracion()

    def test_volver_a_mistral_ignora_la_ruta_con_aviso(self):
        with entorno(MISTRAL_OPENROUTER_PROVEEDOR="z-ai"), \
                mock.patch("builtins.print") as imprimir:
            self.assertEqual(mr.configuracion(), ("mistral", mr.MODELO, ""))
        self.assertIn("::warning::", imprimir.call_args.args[0])


class TestPeticion(unittest.TestCase):
    def enviar(self, **kwargs):
        with mock.patch.object(mr.urllib.request, "urlopen",
                               return_value=respuesta_http(eleccion())) as abrir:
            self.assertEqual(mr.llamar_mistral([{"role": "user", "content": "x"}],
                                               "m/x", "clave", **kwargs), "{}")
        req = abrir.call_args.args[0]
        return req, json.loads(req.data)

    def test_peticion_de_mistral_sin_cambios(self):
        req, cuerpo = self.enviar()
        self.assertEqual(req.full_url, mr.API)
        self.assertEqual(req.get_header("Authorization"), "Bearer clave")
        self.assertEqual(set(cuerpo), {"model", "messages", "temperature", "max_tokens",
                                       "reasoning_effort", "response_format"})
        self.assertEqual(cuerpo["max_tokens"], mr.MAX_SALIDA)

    def test_peticion_de_openrouter(self):
        req, cuerpo = self.enviar(proveedor="openrouter")
        self.assertEqual(req.full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(req.get_header("Authorization"), "Bearer clave")
        # Sin cabeceras de atribución: no se anuncia el repo en OpenRouter.
        self.assertIsNone(req.get_header("Http-referer"))
        self.assertEqual(cuerpo["response_format"], {"type": "json_object"})
        # Con require_parameters, un modelo que no los admita no tendría proveedor.
        self.assertNotIn("reasoning_effort", cuerpo)
        self.assertNotIn("temperature", cuerpo)
        self.assertEqual(cuerpo["max_tokens"], mr.MAX_SALIDA)
        self.assertEqual(cuerpo["provider"], {"require_parameters": True,
                                              "allow_fallbacks": False,
                                              "data_collection": "deny"})
        _, cuerpo = self.enviar(proveedor="openrouter", ruta="z-ai")
        self.assertEqual(cuerpo["provider"]["order"], ["z-ai"])
        self.assertFalse(cuerpo["provider"]["allow_fallbacks"])
        # Solo con un esfuerzo explícito (lo fija la evaluación con --esfuerzo).
        with mock.patch.object(mr, "ESFUERZO_OPENROUTER", "high"):
            _, cuerpo = self.enviar(proveedor="openrouter")
        self.assertEqual(cuerpo["reasoning_effort"], "high")
        self.assertNotIn("temperature", cuerpo)

    def test_openrouter_no_retira_el_modo_json_tras_un_400(self):
        error = urllib.error.HTTPError(mr.API_OPENROUTER, 400, "e", {},
                                       io.BytesIO(b"response_format json_object is not supported"))
        with mock.patch.object(mr.urllib.request, "urlopen", side_effect=[
                error, respuesta_http(eleccion())]) as abrir:
            with self.assertRaises(urllib.error.HTTPError):
                mr.llamar_mistral([], "m/x", "clave", proveedor="openrouter")
        self.assertEqual(abrir.call_count, 1)


class TestRespuestas(unittest.TestCase):
    def llamar(self, efectos, proveedor="openrouter"):
        with mock.patch.object(mr.urllib.request, "urlopen", side_effect=efectos) as abrir, \
                mock.patch.object(mr.time, "sleep"):
            try:
                return mr.llamar_mistral([], "m/x", CLAVE_OR, proveedor=proveedor), abrir
            except Exception as fallo:  # se inspecciona en cada test
                return fallo, abrir

    def test_respuesta_valida_de_ambos_proveedores(self):
        for proveedor in ("mistral", "openrouter"):
            with self.subTest(proveedor=proveedor):
                texto, _ = self.llamar([respuesta_http(eleccion('{"resumen": "R"}'))],
                                       proveedor)
                self.assertEqual(texto, '{"resumen": "R"}')

    def test_json_invalido_vacio_y_truncado(self):
        casos = ((b"no es json", ValueError), (eleccion(""), ValueError),
                 (eleccion(None), ValueError), (eleccion("{}", "length"), mr.RespuestaIncompleta),
                 (eleccion("{}", "error"), mr.RespuestaIncompleta),
                 ({"choices": []}, ValueError))
        for cuerpo, tipo in casos:
            with self.subTest(cuerpo=cuerpo):
                fallo, abrir = self.llamar([respuesta_http(cuerpo)])
                self.assertIsInstance(fallo, tipo)
                self.assertEqual(abrir.call_count, 1)

    def test_error_con_http_200_no_pasa_por_respuesta_valida(self):
        fallo, _ = self.llamar([respuesta_http(
            {"id": "gen-1", "error": {"code": 502, "message": "dato del prompt"}})])
        self.assertIsInstance(fallo, mr.RespuestaIncompleta)
        self.assertEqual(fallo.causa, "error_proveedor")
        self.assertNotIn("dato del prompt", str(fallo))

    def test_autenticacion_saldo_y_limites(self):
        # 401 y 402 (sin saldo) no se reintentan: repetir no arregla nada y
        # no debe gastar. 429 y 503 sí, dentro del tope de tres peticiones.
        for codigo, llamadas in ((401, 1), (402, 1), (403, 1), (429, 3), (503, 3)):
            with self.subTest(codigo=codigo):
                fallo, abrir = self.llamar(error_http(codigo))
                self.assertIsInstance(fallo, urllib.error.HTTPError)
                self.assertEqual(abrir.call_count, llamadas)

    def test_fallo_temporal_se_recupera(self):
        texto, abrir = self.llamar([error_http(429, {"Retry-After": "1"}),
                                    urllib.error.URLError("red"),
                                    respuesta_http(eleccion())])
        self.assertEqual(texto, "{}")
        self.assertEqual(abrir.call_count, 3)


class TestMainConProveedor(unittest.TestCase):
    DIFF = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 0\n+x = `1`\n"
    VALIDA = json.dumps({"resumen": "R", "hallazgos": [HALLAZGO],
                         "comprobaciones": [COMPROBACION]})

    def ejecutar(self, variables, respuesta=None, fallo=None):
        evento = json.dumps({"pull_request": {
            "number": 7, "base": {"sha": "base"}, "head": {"sha": "abc123"}}})
        base = dict(SIN_PROVEEDOR, MISTRAL_API_KEY="", OPENROUTER_API_KEY="",
                    GITHUB_EVENT_PATH="evento.json", GH_TOKEN="",
                    GITHUB_REPOSITORY="o/r", GITHUB_RUN_ID="", GITHUB_STEP_SUMMARY="")
        base.update(variables)
        with mock.patch.dict(os.environ, base), \
                mock.patch("builtins.open", mock.mock_open(read_data=evento)), \
                mock.patch.object(mr, "obtener_diff", return_value=self.DIFF), \
                mock.patch.object(mr, "obtener_rutas_diff", return_value={"a.py"}), \
                mock.patch.object(mr, "leer_guia", return_value=(None, "")), \
                mock.patch.object(mr, "obtener_contexto", return_value=""), \
                mock.patch.object(mr, "llamar_mistral", return_value=respuesta,
                                  side_effect=fallo) as llamar, \
                mock.patch.object(mr, "publicar") as publicar, \
                mock.patch("builtins.print") as imprimir:
            codigo = mr.main()
        salida = "\n".join(str(a) for c in imprimir.call_args_list for a in c.args)
        cuerpo = publicar.call_args.args[2] if publicar.called else ""
        return codigo, llamar, cuerpo, salida

    def test_openrouter_revisa_con_su_clave_y_su_modelo(self):
        codigo, llamar, cuerpo, _ = self.ejecutar(
            {"MISTRAL_PROVEEDOR": "openrouter", "MISTRAL_MODEL": "z-ai/glm-5.3-flash",
             "MISTRAL_OPENROUTER_PROVEEDOR": "z-ai", "OPENROUTER_API_KEY": CLAVE_OR,
             "MISTRAL_API_KEY": CLAVE_MI}, respuesta=self.VALIDA)
        self.assertEqual(codigo, 0)
        args, kwargs = llamar.call_args
        self.assertEqual(args[1:], ("z-ai/glm-5.3-flash", CLAVE_OR))
        self.assertEqual(kwargs, {"proveedor": "openrouter", "ruta": "z-ai", "plazo": mock.ANY})
        self.assertIn("## Revisión vía OpenRouter (`z-ai/glm-5.3-flash`, proveedor `z-ai`)",
                      cuerpo)
        self.assertNotIn("mistralai", cuerpo)

    def test_mistral_por_defecto_no_cambia(self):
        codigo, llamar, cuerpo, _ = self.ejecutar(
            {"MISTRAL_API_KEY": CLAVE_MI}, respuesta=self.VALIDA)
        self.assertEqual(codigo, 0)
        self.assertEqual(llamar.call_args.args[1:], (mr.MODELO, CLAVE_MI))
        self.assertEqual(llamar.call_args.kwargs, {"proveedor": "mistral", "ruta": "", "plazo": mock.ANY})
        self.assertIn("[Mistral AI (@mistralai)](https://github.com/mistralai)", cuerpo)

    def test_falta_la_clave_del_proveedor_elegido(self):
        # Cada proveedor solo mira su secreto: la otra clave no lo sustituye.
        for variables, secreto in (
                ({"MISTRAL_PROVEEDOR": "openrouter", "MISTRAL_MODEL": "z-ai/glm-5.3-flash",
                  "MISTRAL_API_KEY": CLAVE_MI}, "OPENROUTER_API_KEY"),
                ({"OPENROUTER_API_KEY": CLAVE_OR}, "MISTRAL_API_KEY")):
            with self.subTest(secreto=secreto):
                codigo, llamar, cuerpo, salida = self.ejecutar(variables)
                self.assertEqual(codigo, 0)  # como un fork: se omite sin fallar
                llamar.assert_not_called()
                self.assertIn("no hay %s disponible" % secreto, salida)
                self.assertEqual(cuerpo, "")

    def test_configuracion_invalida_falla_sin_llamar_a_la_api(self):
        for variables in ({"MISTRAL_PROVEEDOR": "otro"},
                          {"MISTRAL_PROVEEDOR": "openrouter"},
                          {"MISTRAL_PROVEEDOR": "openrouter", "MISTRAL_MODEL": mr.MODELO}):
            with self.subTest(variables=variables):
                variables = dict(variables, OPENROUTER_API_KEY=CLAVE_OR,
                                 MISTRAL_API_KEY=CLAVE_MI)
                codigo, llamar, cuerpo, salida = self.ejecutar(variables)
                self.assertEqual(codigo, 1)
                llamar.assert_not_called()
                self.assertIn("::error::**Revisión no ejecutada por configuración", salida)
                self.assertNotIn(CLAVE_OR, salida)

    def test_errores_no_filtran_la_clave_ni_dicen_sin_hallazgos(self):
        variables = {"MISTRAL_PROVEEDOR": "openrouter", "MISTRAL_MODEL": "z-ai/glm-5.3-flash",
                     "OPENROUTER_API_KEY": CLAVE_OR}
        fallos = (urllib.error.HTTPError(mr.API_OPENROUTER, 401, "Bearer " + CLAVE_OR,
                                         {}, None),
                  urllib.error.HTTPError(mr.API_OPENROUTER, 402, "sin saldo", {}, None),
                  urllib.error.URLError(CLAVE_OR), TimeoutError(CLAVE_OR),
                  ValueError(CLAVE_OR), mr.RespuestaIncompleta("error_proveedor"))
        for fallo in fallos:
            with self.subTest(fallo=type(fallo).__name__):
                codigo, _, cuerpo, salida = self.ejecutar(variables, fallo=fallo)
                self.assertEqual(codigo, 1)
                self.assertIn("Revisión no completada", cuerpo)
                self.assertNotIn("Sin hallazgos", cuerpo)
                self.assertNotIn(CLAVE_OR, cuerpo)
                self.assertNotIn(CLAVE_OR, salida)
        _, _, cuerpo, _ = self.ejecutar(
            variables, fallo=mr.RespuestaIncompleta("error_proveedor"))
        self.assertIn("OpenRouter devolvió una respuesta incompleta", cuerpo)
        self.assertIn("`error_proveedor`", cuerpo)


class TestContrasteConProveedor(unittest.TestCase):
    """El contraste (mistral_reply.py) usa el proveedor de la revisión."""

    def ejecutar(self, variables, respuesta=None):
        from tests.test_mistral_reply import reply, SHA, FUENTE, EVIDENCIAS, RESPUESTA
        pr = {"state": "open", "head": {"sha": SHA, "repo": {"full_name": "o/r"}}}
        base = dict(SIN_PROVEEDOR, MISTRAL_API_KEY="", OPENROUTER_API_KEY="",
                    MISTRAL_REPLY_MODEL="", GITHUB_EVENT_PATH="evento.json",
                    GITHUB_REPOSITORY="o/r", GITHUB_EVENT_NAME="pull_request_review",
                    GITHUB_RUN_ID="88")
        base.update(variables)
        with mock.patch.dict(os.environ, base), \
                mock.patch("builtins.open", mock.mock_open(read_data="{}")), \
                mock.patch.object(reply, "seleccionar", return_value=(7, [FUENTE])), \
                mock.patch.object(reply, "api", side_effect=[pr, [], pr, []]), \
                mock.patch.object(reply.subprocess, "run"), \
                mock.patch.object(reply, "construir_mensajes", return_value=([], EVIDENCIAS)), \
                mock.patch.object(reply.mr, "llamar_mistral", return_value=respuesta or
                                  json.dumps({"respuestas": [RESPUESTA]})) as llamar, \
                mock.patch.object(reply.mr, "publicar") as publicar, \
                mock.patch.object(reply.mr, "escribir_resumen") as resumen, \
                mock.patch("builtins.print") as imprimir:
            codigo = reply.main()
        textos = [str(c.args[0]) for c in resumen.call_args_list]
        textos += [" ".join(map(str, c.args)) for c in imprimir.call_args_list]
        return codigo, llamar, publicar, "\n".join(textos)

    def test_usa_openrouter_con_el_modelo_del_contraste(self):
        codigo, llamar, publicar, _ = self.ejecutar({
            "MISTRAL_PROVEEDOR": "openrouter", "MISTRAL_MODEL": "z-ai/glm-5.3-flash",
            "MISTRAL_REPLY_MODEL": "deepseek/deepseek-v4.1-flash",
            "MISTRAL_OPENROUTER_PROVEEDOR": "deepinfra", "OPENROUTER_API_KEY": CLAVE_OR})
        self.assertEqual(codigo, 0)
        self.assertEqual(llamar.call_args.args[1:], ("deepseek/deepseek-v4.1-flash", CLAVE_OR))
        self.assertEqual(llamar.call_args.kwargs, {"proveedor": "openrouter",
                                                   "ruta": "deepinfra"})
        cuerpo = publicar.call_args.args[2]
        self.assertIn("## Contraste vía OpenRouter (`deepseek/deepseek-v4.1-flash`, "
                      "proveedor `deepinfra`)", cuerpo)
        self.assertNotIn(CLAVE_OR, cuerpo)

    def test_mistral_por_defecto_no_cambia(self):
        codigo, llamar, publicar, _ = self.ejecutar({"MISTRAL_API_KEY": CLAVE_MI})
        self.assertEqual(codigo, 0)
        self.assertEqual(llamar.call_args.args[1:], (mr.MODELO, CLAVE_MI))
        self.assertIn("Contraste de [Mistral AI (@mistralai)]", publicar.call_args.args[2])

    def test_falta_la_clave_del_proveedor_elegido(self):
        codigo, llamar, publicar, texto = self.ejecutar({
            "MISTRAL_PROVEEDOR": "openrouter", "MISTRAL_MODEL": "z-ai/glm-5.3-flash",
            "MISTRAL_API_KEY": CLAVE_MI})
        self.assertEqual(codigo, 0)
        llamar.assert_not_called()
        publicar.assert_not_called()
        self.assertIn("OPENROUTER_API_KEY no disponible", texto)

    def test_configuracion_invalida_falla_sin_llamar(self):
        codigo, llamar, publicar, texto = self.ejecutar({
            "MISTRAL_PROVEEDOR": "openrouter", "OPENROUTER_API_KEY": CLAVE_OR})
        self.assertEqual(codigo, 1)
        llamar.assert_not_called()
        publicar.assert_not_called()
        self.assertIn("Contraste no ejecutado por configuración", texto)


class TestEvaluacionConProveedor(unittest.TestCase):
    """La evaluación (mistral_eval.py) mide la configuración elegida y la registra."""

    def setUp(self):
        from tests.test_mistral_eval import ev, POSITIVO
        self.ev, self.positivo = ev, POSITIVO

    def main(self, argv, variables, efecto=None):
        import tempfile
        from contextlib import redirect_stdout
        from tests.test_mistral_eval import DIFF
        ev = self.ev
        base = dict(SIN_PROVEEDOR, MISTRAL_API_KEY="", OPENROUTER_API_KEY="")
        base.update(variables)
        registro = {"estado": "valida", "detalle": "", "solicitudes": 1,
                    "recortado": False, "hallazgos": [], "pendientes": []}
        salida = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "r.json")
            with mock.patch.dict(os.environ, base), \
                    mock.patch.object(ev, "cargar_casos", return_value=[self.positivo]), \
                    mock.patch.object(ev.mr, "obtener_diff", return_value=DIFF), \
                    mock.patch.object(ev, "objetivos", return_value={"a.py": [(1, 2, "f")]}), \
                    mock.patch.object(ev, "revisar", return_value=registro,
                                      side_effect=efecto) as revisar, \
                    mock.patch.object(ev.mr, "escribir_resumen"), redirect_stdout(salida):
                codigo = ev.main(["--ejecutar", "--repeticiones", "1", "--salida", ruta]
                                 + argv)
            guardado = None
            if os.path.exists(ruta):
                with open(ruta, encoding="utf-8") as fh:
                    guardado = json.load(fh)
        return codigo, revisar, salida.getvalue(), guardado

    def test_evalua_con_openrouter_y_lo_registra(self):
        codigo, revisar, salida, guardado = self.main(
            ["--modelo", "deepseek/deepseek-v4.1-flash"],
            {"MISTRAL_PROVEEDOR": "openrouter", "MISTRAL_OPENROUTER_PROVEEDOR": "deepinfra",
             "OPENROUTER_API_KEY": CLAVE_OR})
        self.assertEqual(codigo, 0)
        self.assertEqual(revisar.call_args.args[1:], ("deepseek/deepseek-v4.1-flash", CLAVE_OR))
        self.assertEqual(revisar.call_args.kwargs, {"proveedor": "openrouter",
                                                    "ruta": "deepinfra"})
        self.assertEqual((guardado["proveedor"], guardado["ruta"], guardado["modelo"]),
                         ("openrouter", "deepinfra", "deepseek/deepseek-v4.1-flash"))
        self.assertIn("Evaluación del revisor vía OpenRouter "
                      "(`deepseek/deepseek-v4.1-flash`, proveedor `deepinfra`", salida)
        self.assertNotIn(CLAVE_OR, salida + json.dumps(guardado))

    def test_mistral_por_defecto_lo_registra(self):
        codigo, revisar, salida, guardado = self.main([], {"MISTRAL_API_KEY": CLAVE_MI})
        self.assertEqual(codigo, 0)
        self.assertEqual((guardado["proveedor"], guardado["modelo"]), ("mistral", mr.MODELO))
        self.assertIn("Evaluación del revisor de Mistral (`%s`" % mr.MODELO, salida)
        # Mistral no declara el coste: se dice, sin estimarlo.
        self.assertIn("Coste: la API no lo informa", salida)
        self.assertIsNone(guardado["tokens"]["coste"])

    def test_sin_clave_o_sin_modelo_no_evalua(self):
        for argv, variables, texto in (
                ([], {"MISTRAL_PROVEEDOR": "openrouter", "MISTRAL_MODEL": "z-ai/glm-5.3-flash",
                      "MISTRAL_API_KEY": CLAVE_MI}, "Falta OPENROUTER_API_KEY"),
                ([], {"MISTRAL_PROVEEDOR": "openrouter", "OPENROUTER_API_KEY": CLAVE_OR},
                 "obligatorio"),
                (["--modelo", mr.MODELO], {"MISTRAL_PROVEEDOR": "openrouter",
                                           "OPENROUTER_API_KEY": CLAVE_OR},
                 "organización/modelo")):
            with self.subTest(texto=texto):
                codigo, revisar, salida, _ = self.main(argv, variables)
                self.assertEqual(codigo, 2)
                revisar.assert_not_called()
                self.assertIn(texto, salida)

    def test_revisar_pasa_el_proveedor_a_la_llamada(self):
        from tests.test_mistral_eval import DIFF, COMPROBACIONES
        ev = self.ev
        texto = json.dumps({"resumen": "R", "hallazgos": [],
                            "comprobaciones": COMPROBACIONES})
        with mock.patch.object(ev.mr, "obtener_diff", return_value=DIFF), \
                mock.patch.object(ev.mr, "obtener_rutas_diff", return_value={"a.py"}), \
                mock.patch.object(ev.mr, "obtener_contexto", return_value=""), \
                mock.patch.object(ev.mr, "leer_guia", return_value=(None, "")), \
                mock.patch.object(ev, "git", return_value="asunto\n"), \
                mock.patch.object(ev, "objetivos", return_value={"a.py": [(1, 2, "f")]}), \
                mock.patch.object(ev.mr, "llamar_mistral", return_value=texto) as api:
            registro = ev.revisar(self.positivo, "z-ai/glm-5.3-flash", "k",
                                  proveedor="openrouter", ruta="z-ai")
        self.assertEqual(registro["estado"], "valida")
        self.assertEqual(api.call_args.kwargs, {"proveedor": "openrouter", "ruta": "z-ai"})

    def test_error_del_proveedor_no_cuenta_como_cortada(self):
        from tests.test_mistral_eval import DIFF
        ev = self.ev
        for causa, estado in (("error_proveedor", "error"), ("error", "error"),
                              ("length", "cortada")):
            with self.subTest(causa=causa), \
                    mock.patch.object(ev.mr, "obtener_diff", return_value=DIFF), \
                    mock.patch.object(ev.mr, "obtener_rutas_diff", return_value={"a.py"}), \
                    mock.patch.object(ev.mr, "obtener_contexto", return_value=""), \
                    mock.patch.object(ev.mr, "leer_guia", return_value=(None, "")), \
                    mock.patch.object(ev, "git", return_value="asunto\n"), \
                    mock.patch.object(ev, "objetivos", return_value={"a.py": [(1, 2, "f")]}), \
                    mock.patch.object(ev.mr, "llamar_mistral",
                                      side_effect=ev.mr.RespuestaIncompleta(causa)):
                registro = ev.revisar(self.positivo, "z-ai/x", "k", proveedor="openrouter")
            self.assertEqual((registro["estado"], registro["detalle"]), (estado, causa))

    def test_esfuerzo_con_openrouter_solo_si_se_pide(self):
        vistos = []
        registro = {"estado": "valida", "detalle": "", "solicitudes": 1,
                    "recortado": False, "hallazgos": [], "pendientes": []}

        def revisar(*_, **__):
            vistos.append(self.ev.mr.ESFUERZO_OPENROUTER)
            return dict(registro)

        variables = {"MISTRAL_PROVEEDOR": "openrouter", "MISTRAL_MODEL": "z-ai/glm-5.3-flash",
                     "OPENROUTER_API_KEY": CLAVE_OR}
        for argv, esperado in (([], ""), (["--esfuerzo", "medium"], "medium")):
            with self.subTest(argv=argv):
                codigo, _, salida, guardado = self.main(argv, variables, efecto=revisar)
            self.assertEqual((codigo, vistos[-1], guardado["esfuerzo"]), (0, esperado, esperado))
            if not esperado:
                self.assertIn("pedido: `ninguno, el del modelo`", salida)
                # No pedirlo no es que el modelo lo rechace.
                self.assertNotIn("no admite el esfuerzo", salida)
        self.assertEqual(self.ev.mr.ESFUERZO_OPENROUTER, "")   # se restaura

    def test_suma_el_coste_que_declara_la_api(self):
        ev = self.ev
        con = eleccion()
        sin = dict(eleccion(), usage={"prompt_tokens": 1, "completion_tokens": 1})
        with mock.patch.object(ev.mr.urllib.request, "urlopen", side_effect=[
                respuesta_http(con), respuesta_http(con), respuesta_http(sin)]):
            with ev.Presupuesto(5) as presupuesto:
                for _ in range(3):
                    ev.mr.llamar_mistral([], "z-ai/x", "k", proveedor="openrouter")
        self.assertEqual(presupuesto.con_coste, 2)
        self.assertAlmostEqual(presupuesto.coste, 0.0002)
        self.assertEqual(presupuesto.respondidas, 3)

    def test_anota_quien_sirvio_cada_respuesta(self):
        ev = self.ev
        servida = dict(eleccion(), provider="Z.AI", model="z-ai/glm-5.3-flash")
        rara = dict(eleccion(), provider="Z.AI\n<script>`x`" + "a" * 100, model=7)
        with mock.patch.object(ev.mr.urllib.request, "urlopen", side_effect=[
                respuesta_http(servida), respuesta_http(servida),
                respuesta_http(eleccion()), respuesta_http(rara)]):
            with ev.Presupuesto(5) as presupuesto:
                for _ in range(4):
                    ev.mr.llamar_mistral([], "z-ai/x", "k", proveedor="openrouter")
        rara_saneada = ("Z.AIscriptx" + "a" * 100)[:60]
        self.assertEqual(presupuesto.servido_por,
                         {"Z.AI": 2, "no lo informa": 1, rara_saneada: 1})
        self.assertEqual(presupuesto.modelo_servido,
                         {"z-ai/glm-5.3-flash": 2, "no lo informa": 2})

    def test_el_informe_y_el_artefacto_dicen_quien_sirvio(self):
        ev = self.ev
        servida = dict(eleccion(), provider="Z.AI", model="z-ai/glm-5.3-flash")

        def revisar(*_, **__):
            ev.mr.llamar_mistral([], "z-ai/glm-5.3-flash", "k", proveedor="openrouter")
            return {"estado": "valida", "detalle": "", "solicitudes": 1,
                    "recortado": False, "hallazgos": [], "pendientes": []}

        variables = {"MISTRAL_PROVEEDOR": "openrouter", "MISTRAL_MODEL": "z-ai/glm-5.3-flash",
                     "OPENROUTER_API_KEY": CLAVE_OR}
        with mock.patch.object(ev.mr.urllib.request, "urlopen",
                               return_value=respuesta_http(servida)):
            codigo, _, salida, guardado = self.main([], variables, efecto=revisar)
        self.assertEqual(codigo, 0)
        self.assertIn("Servida por (según la API): `Z.AI` (1). "
                      "Modelo servido: `z-ai/glm-5.3-flash` (1).", salida)
        self.assertEqual(guardado["servido_por"], {"Z.AI": 1})
        self.assertEqual(guardado["modelo_servido"], {"z-ai/glm-5.3-flash": 1})


try:
    import yaml
except ImportError:
    yaml = None


@unittest.skipUnless(yaml, "PyYAML no instalado")
class TestWorkflowsConProveedor(unittest.TestCase):
    """Los tres reutilizables aceptan OpenRouter sin obligar a nada a quien no lo usa."""
    RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    REUTILIZABLES = {"review.yml": ("mistral-review", "mistral_review.py"),
                     "reply.yml": ("responder", "mistral_reply.py"),
                     "eval.yml": ("mistral-eval", "--ejecutar")}

    def cargar(self, ruta):
        with open(os.path.join(self.RAIZ, ruta), encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    def test_entradas_y_secreto_opcionales_que_llegan_al_script(self):
        for nombre, (trabajo, marca) in self.REUTILIZABLES.items():
            with self.subTest(workflow=nombre):
                flujo = self.cargar(".github/workflows/" + nombre)
                llamada = flujo[True]["workflow_call"]   # YAML 1.1: `on` es True
                for entrada in ("provider", "openrouter_provider"):
                    # Vacío = Mistral: los repos que ya lo llaman no cambian.
                    self.assertEqual(llamada["inputs"][entrada]["default"], "")
                    self.assertEqual(llamada["inputs"][entrada]["type"], "string")
                for secreto in ("MISTRAL_API_KEY", "OPENROUTER_API_KEY"):
                    self.assertFalse(llamada["secrets"][secreto]["required"])
                paso = next(p for p in flujo["jobs"][trabajo]["steps"]
                            if marca in p.get("run", ""))
                self.assertEqual(paso["env"]["MISTRAL_PROVEEDOR"], "${{ inputs.provider }}")
                self.assertEqual(paso["env"]["MISTRAL_OPENROUTER_PROVEEDOR"],
                                 "${{ inputs.openrouter_provider }}")
                self.assertEqual(paso["env"]["OPENROUTER_API_KEY"],
                                 "${{ secrets.OPENROUTER_API_KEY }}")
                self.assertEqual(paso["env"]["MISTRAL_API_KEY"],
                                 "${{ secrets.MISTRAL_API_KEY }}")
                # La clave solo llega al paso que llama a la API.
                otros = [p for p in flujo["jobs"][trabajo]["steps"] if p is not paso]
                self.assertFalse(any("OPENROUTER_API_KEY" in json.dumps(p) for p in otros))

    def test_revision_solo_acepta_incluir_descripcion_con_una_entrada_opcional(self):
        flujo = self.cargar(".github/workflows/review.yml")
        entrada = flujo[True]["workflow_call"]["inputs"]["include_description"]
        self.assertEqual(entrada["type"], "boolean")
        self.assertIs(entrada["default"], False)
        paso = next(p for p in flujo["jobs"]["mistral-review"]["steps"]
                    if "mistral_review.py" in p.get("run", ""))
        self.assertEqual(paso["env"]["MISTRAL_INCLUIR_DESCRIPCION"],
                         "${{ inputs.include_description }}")

    def test_los_ejemplos_pasan_entradas_y_secretos_que_existen(self):
        for ejemplo, nombre in (("mistral-review.yml", "review.yml"),
                                ("mistral-reply.yml", "reply.yml"),
                                ("mistral-eval.yml", "eval.yml")):
            with self.subTest(ejemplo=ejemplo):
                trabajo = next(iter(self.cargar("ejemplos/" + ejemplo)["jobs"].values()))
                llamada = self.cargar(".github/workflows/" + nombre)[True]["workflow_call"]
                self.assertLessEqual(set(trabajo["with"]), set(llamada["inputs"]))
                self.assertLessEqual(set(trabajo["secrets"]), set(llamada["secrets"]))
                self.assertEqual(trabajo["with"]["provider"], "${{ vars.MISTRAL_PROVIDER }}")
                # Cada proveedor, su variable de modelo: OpenRouter nunca toma
                # MISTRAL_MODEL (sin OPENROUTER_MODEL, el revisor falla y lo dice).
                self.assertEqual(trabajo["with"]["model"], "${{ vars.MISTRAL_PROVIDER == \'openrouter\' && vars.OPENROUTER_MODEL || vars.MISTRAL_PROVIDER != \'openrouter\' && vars.MISTRAL_MODEL || \'\' }}")


if __name__ == "__main__":
    unittest.main()
