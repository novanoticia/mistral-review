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
    resp = mock.MagicMock()
    resp.__enter__.return_value.read.return_value = (
        cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo).encode())
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
        self.assertEqual(cuerpo["reasoning_effort"], mr.ESFUERZO)
        self.assertEqual(cuerpo["max_tokens"], mr.MAX_SALIDA)
        self.assertEqual(cuerpo["provider"], {"require_parameters": True,
                                              "allow_fallbacks": False,
                                              "data_collection": "deny"})
        _, cuerpo = self.enviar(proveedor="openrouter", ruta="z-ai")
        self.assertEqual(cuerpo["provider"]["order"], ["z-ai"])
        self.assertFalse(cuerpo["provider"]["allow_fallbacks"])


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
        self.assertEqual(kwargs, {"proveedor": "openrouter", "ruta": "z-ai"})
        self.assertIn("## Revisión vía OpenRouter (`z-ai/glm-5.3-flash`, proveedor `z-ai`)",
                      cuerpo)
        self.assertNotIn("mistralai", cuerpo)

    def test_mistral_por_defecto_no_cambia(self):
        codigo, llamar, cuerpo, _ = self.ejecutar(
            {"MISTRAL_API_KEY": CLAVE_MI}, respuesta=self.VALIDA)
        self.assertEqual(codigo, 0)
        self.assertEqual(llamar.call_args.args[1:], (mr.MODELO, CLAVE_MI))
        self.assertEqual(llamar.call_args.kwargs, {"proveedor": "mistral", "ruta": ""})
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


if __name__ == "__main__":
    unittest.main()
