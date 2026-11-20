"""Evaluación del revisor de Mistral con casos reales (2026-11-13).

Sin red ni API: fijan la parte mecánica de `scripts/mistral_eval.py`
(qué objetivo se deriva de un arreglo, dónde cae un hallazgo, qué se cuenta).
Que el modelo acierte no se puede probar aquí: es lo que la evaluación mide.
"""
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "mistral_eval", os.path.join(_RAIZ, "scripts", "mistral_eval.py"))
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)
mr = ev.mr

SHA = "a" * 40
POSITIVO = {"id": "p", "tipo": "positivo", "base": SHA, "cabeza": SHA,
            "arreglo": SHA, "defecto": "d"}
NEGATIVO = {"id": "n", "tipo": "negativo", "base": SHA, "cabeza": SHA,
            "archivos": ["a.py"], "defecto": "d"}

DIFF = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,3 +1,4 @@
 def f(x):
+    y = x + 1
     return x

"""


class TestCasos(unittest.TestCase):
    def cargar(self, casos):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(casos, fh)
        try:
            return ev.cargar_casos(fh.name)
        finally:
            os.unlink(fh.name)

    def test_acepta_los_dos_tipos(self):
        self.assertEqual(len(self.cargar([POSITIVO, NEGATIVO])), 2)

    def test_rechaza_casos_incompletos_repetidos_o_sin_sha(self):
        malos = ([dict(POSITIVO, tipo="otro")],
                 [{k: v for k, v in POSITIVO.items() if k != "arreglo"}],
                 [{k: v for k, v in NEGATIVO.items() if k != "archivos"}],
                 [dict(POSITIVO, cabeza="HEAD; rm -rf /")],
                 [dict(POSITIVO, cabeza=SHA + "; rm -rf /")],
                 [dict(POSITIVO, cabeza=SHA + "\n")],
                 [dict(POSITIVO, cabeza=SHA[:7])],
                 [dict(NEGATIVO, archivos="scripts/a.py")],
                 [dict(NEGATIVO, archivos=["scripts/a.py", 3])],
                 [dict(POSITIVO, ignorar="scripts/a.py")],
                 [dict(POSITIVO, control="sí")],
                 [dict(NEGATIVO, control=True)],
                 [POSITIVO, dict(POSITIVO)])
        for casos in malos:
            with self.subTest(casos=casos), self.assertRaises(ValueError):
                self.cargar(casos)

    def test_el_ejemplo_de_casos_es_valido_y_fija_commits_completos(self):
        # Los casos reales viven en cada repo evaluado; aquí, la plantilla.
        casos = ev.cargar_casos(os.path.join(_RAIZ, "ejemplos", "eval-casos.json"))
        self.assertEqual({c["tipo"] for c in casos}, {"positivo", "negativo"})
        self.assertEqual([c["id"] for c in casos if c.get("control")], ["c1-control"])
        for c in casos:
            for clave in ("base", "cabeza", "arreglo"):
                if clave in c:
                    self.assertRegex(c[clave], r"^[0-9a-f]{40}$")

    def test_los_casos_por_defecto_son_los_del_repo_evaluado(self):
        self.assertEqual(ev.CASOS, os.path.join(".github", "eval", "casos.json"))


class TestObjetivos(unittest.TestCase):
    def test_el_objetivo_es_la_funcion_que_el_arreglo_toco(self):
        with tempfile.TemporaryDirectory() as d:
            def git(*a):
                return subprocess.run(["git", "-C", d] + list(a), check=True,
                                      capture_output=True, text=True).stdout.strip()

            def escribir(nombre, texto):
                os.makedirs(os.path.dirname(os.path.join(d, nombre)) or d, exist_ok=True)
                with open(os.path.join(d, nombre), "w") as fh:
                    fh.write(texto)

            git("init", "-q")
            git("config", "user.email", "t@t"); git("config", "user.name", "t")
            codigo = ("def sana(x):\n    return x\n\n\n"
                      "def rota(x):\n    y = x\n    return y - 1\n")
            escribir("a.py", codigo)
            escribir("tests/test_a.py", "def test_a():\n    pass\n")
            escribir("otro.py", "V = 1\n")
            git("add", "-A"); git("commit", "-qm", "defecto")
            cabeza = git("rev-parse", "HEAD")
            escribir("a.py", codigo.replace("y - 1", "y + 1"))
            escribir("tests/test_a.py", "def test_a():\n    assert True\n")
            escribir("otro.py", "V = 2\n")
            git("commit", "-qam", "arreglo")
            caso = dict(POSITIVO, cabeza=cabeza, arreglo=git("rev-parse", "HEAD"),
                        ignorar=["otro.py"])
            viejo = os.getcwd()
            try:
                os.chdir(d)
                with mock.patch.dict(os.environ, {"MISTRAL_EXCLUIDOS": ""}):
                    destino = ev.objetivos(caso)
                with mock.patch.dict(os.environ, {"MISTRAL_EXCLUIDOS": "*.py"}):
                    destino_excluido = ev.objetivos(caso)
            finally:
                os.chdir(viejo)
        self.assertEqual(destino, {"a.py": [(5, 7, "rota")]})   # ni tests ni ignorados
        self.assertEqual(destino_excluido, {})

    def test_negativo_apunta_a_sus_archivos(self):
        self.assertEqual(ev.objetivos(NEGATIVO), {"a.py": []})


class TestRangosObjetivo(unittest.TestCase):
    """El objetivo no es el contexto: una vecina sin tocar no cuenta (Codex, email-triage-plugin#203)."""

    FUENTE = (
        "def antes():\n"        # 1
        "    return 1\n"        # 2
        "\n"                    # 3
        "MARCA = 'x'\n"         # 4
        "OTRA = 'y'\n"          # 5
        "\n"                    # 6
        "def despues():\n"      # 7
        "    return 2\n"        # 8
    )

    def test_un_cambio_de_modulo_no_arrastra_las_funciones_vecinas(self):
        rangos = ev._rangos_objetivo(self.FUENTE, {4, 5})
        self.assertEqual(rangos, [(4, 5, "")])
        # El contexto del revisor sí las abarca: por eso no sirve de objetivo.
        contexto = mr._rangos_python(self.FUENTE, {4, 5})
        self.assertTrue(any(i <= 2 <= f for i, f, _ in contexto))

    def test_una_linea_de_modulo_pegada_a_una_funcion_no_se_une_a_ella(self):
        fuente = "X = 1\ndef f():\n    return X\n"
        self.assertEqual(ev._rangos_objetivo(fuente, {1, 3}),
                         [(1, 1, ""), (2, 3, "f")])

    def test_fuente_que_no_parsea_no_da_objetivos(self):
        self.assertEqual(ev._rangos_objetivo("def (:\n", {1}), [])


class TestClasificar(unittest.TestCase):
    def test_una_cita_de_varias_lineas_se_situa_como_tramo_entero(self):
        # Revisión Codex (mistral-review#3): `return None` se repite en la
        # función objetivo; el tramo citado solo existe en `ajena`.
        diff = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,0 +1,6 @@\n"
                "+def objetivo():\n+    return None\n+\n"
                "+def ajena():\n+    return None\n+    pass\n")
        cita = "def ajena():\n    return None"
        self.assertEqual(ev.lineas_citadas(diff, "a.py", cita), {4, 5})
        hallazgo = dict(HALLAZGO, fragmento=cita)
        self.assertEqual(ev.clasificar(hallazgo, diff, {"a.py": [(1, 2, "objetivo")]}),
                         "archivo")
        self.assertEqual(ev.clasificar(hallazgo, diff, {"a.py": [(4, 6, "ajena")]}),
                         "funcion")
        # Una sola línea repetida sigue contando en todas sus apariciones.
        self.assertEqual(ev.lineas_citadas(diff, "a.py", "return None"), {2, 5})

    def test_lineas_citadas_son_las_de_la_cabeza(self):
        self.assertEqual(ev.lineas_citadas(DIFF, "a.py", "y = x + 1"), {2})
        self.assertEqual(ev.lineas_citadas(DIFF, "a.py", "+    y = x + 1\nreturn x"), {2, 3})
        self.assertEqual(ev.lineas_citadas(DIFF, "b.py", "y = x + 1"), set())
        self.assertEqual(ev.lineas_citadas(DIFF, "a.py", "}"), set())

    def test_una_linea_eliminada_cuenta_por_sus_vecinas(self):
        # Quitar una guarda solo se puede citar por la linea borrada (Codex, email-triage-plugin#203).
        diff = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                "@@ -1,4 +1,3 @@\n def f(x):\n-    if x is None: return 0\n"
                "     return x + 1\n \n")
        self.assertEqual(ev.lineas_citadas(diff, "a.py", "if x is None: return 0"), {1, 2})
        hallazgo = {"archivo": "a.py", "fragmento": "-    if x is None: return 0"}
        self.assertTrue(mr.cita_en(hallazgo["fragmento"], mr.indice_diff(diff)["a.py"]))
        self.assertEqual(ev.clasificar(hallazgo, diff, {"a.py": [(1, 2, "f")]}), "funcion")

    def test_la_cita_directa_usa_lineas_completas_y_vecinas_del_borrado(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -4 +3,0 @@\n-    return x\n"
        destino = {"a.py": [(2, 2, "f")]}
        self.assertTrue(ev._cita_directa_visible(diff, destino))
        self.assertFalse(ev._cita_directa_visible(diff[:-1], destino))
        self.assertFalse(ev._cita_directa_visible(diff.replace("return x", ")"), destino))
        self.assertTrue(ev._cita_directa_visible(diff, {"a.py": []}))
        self.assertFalse(ev._cita_directa_visible(diff, {"b.py": []}))

    def test_funcion_archivo_o_fuera(self):
        def h(archivo, fragmento):
            return {"archivo": archivo, "fragmento": fragmento}
        destino = {"a.py": [(1, 2, "f")]}
        self.assertEqual(ev.clasificar(h("a.py", "y = x + 1"), DIFF, destino), "funcion")
        self.assertEqual(ev.clasificar(h("a.py", "return x"), DIFF, destino), "archivo")
        self.assertEqual(ev.clasificar(h("b.py", "y = x + 1"), DIFF, destino), "fuera")
        # Negativo: sin rangos, el archivo entero cuenta como «archivo».
        self.assertEqual(ev.clasificar(h("a.py", "y = x + 1"), DIFF, {"a.py": []}), "archivo")


COMPROBACIONES = [{"archivo": "a.py", "escenario": "e", "conclusion": "c",
                   "estado": "pendiente"}]
HALLAZGO = {"severidad": "media", "archivo": "a.py", "fragmento": "y = x + 1",
            "problema": "p", "escenario": "e", "arreglo": "a"}


class TestRevisar(unittest.TestCase):
    def revisar(self, respuestas, destino=None, diff=DIFF, rutas=None, caso=POSITIVO):
        if rutas is None:
            rutas = {"a.py"}
        if destino is None:
            destino = {"a.py": [(1, 2, "f")]}
        with mock.patch.object(mr, "obtener_diff", return_value=diff), \
                mock.patch.object(mr, "obtener_rutas_diff", return_value=rutas), \
                mock.patch.object(mr, "obtener_contexto", return_value=""), \
                mock.patch.object(mr, "leer_guia", return_value=(None, "")), \
                mock.patch.object(ev, "git", return_value="asunto\n"), \
                mock.patch.object(ev, "objetivos", return_value=destino), \
                mock.patch.object(mr, "llamar_mistral", side_effect=respuestas) as api:
            return ev.revisar(caso, "m", "clave"), api

    def test_revision_valida_clasifica_hallazgos_y_pendientes(self):
        texto = json.dumps({"resumen": "R", "hallazgos": [HALLAZGO],
                            "comprobaciones": COMPROBACIONES})
        registro, api = self.revisar([texto])
        self.assertEqual(registro["estado"], "valida")
        self.assertEqual(registro["solicitudes"], 1)
        self.assertEqual(registro["hallazgos"][0], dict(HALLAZGO, donde="funcion"))
        self.assertEqual(registro["pendientes"][0], {
            "archivo": "a.py", "escenario": "e", "conclusion": "c", "donde": "archivo"})
        # El asunto del commit es el titulo; no hay descripcion que delate el arreglo.
        usuario = api.call_args.args[0][1]["content"]
        self.assertIn("asunto", usuario)
        self.assertIn("(sin descripción)", usuario)

    def test_una_cita_de_una_linea_con_secreto_se_valida_tachada(self):
        # Como en una revisión real: el modelo recibe el diff tachado y copia
        # la línea tachada; validarla contra el diff sin tachar la rechazaba.
        valor = "valor" + "privado"
        diff = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n"
                '-api_key = "viejo"\n+api_key = "%s"\n' % valor)
        citado = dict(HALLAZGO, fragmento=mr.mask_secrets('api_key = "%s"' % valor))
        texto = json.dumps({"resumen": "R", "hallazgos": [citado],
                            "comprobaciones": COMPROBACIONES})
        registro, api = self.revisar([texto], diff=diff)
        self.assertEqual((registro["estado"], api.call_count), ("valida", 1))
        self.assertNotIn(valor, api.call_args.args[0][1]["content"])

    def test_cobertura_del_diff_sin_cambiar_los_mensajes_ni_la_validacion(self):
        borrado = ("diff --git a/b.py b/b.py\n--- a/b.py\n+++ /dev/null\n"
                   "@@ -1 +0,0 @@\n-valor_borrado = True\n")
        binario = ("diff --git a/c.bin b/c.bin\n"
                   "Binary files a/c.bin and b/c.bin differ\n")
        diff = DIFF + borrado + binario
        rutas = {"a.py", "b.py", "c.bin"}
        texto = json.dumps({"resumen": "R", "hallazgos": [HALLAZGO],
                            "comprobaciones": COMPROBACIONES})
        for limite, rutas_visibles, lineas_visibles, recortado in (
                (len(diff), 2, 2, False), (len(DIFF + borrado) - 1, 1, 1, True)):
            with self.subTest(limite=limite), mock.patch.object(mr, "MAX_DIFF", limite):
                mensajes, _ = mr.construir_mensajes("asunto", None, diff, (None, ""), "")
                registro, api = self.revisar([texto], diff=diff, rutas=rutas)
                api.assert_called_once_with(mensajes, "m", "clave", proveedor="mistral", ruta="")
                self.assertEqual(registro["estado"], "valida")
                self.assertEqual(registro["hallazgos"], [dict(HALLAZGO, donde="funcion")])
                self.assertEqual(registro["cobertura"]["diff"], {
                    "rutas_disponibles": 3, "rutas_sin_cambios_textuales": 1,
                    "rutas_con_cambios": 2, "rutas_con_cambios_visibles": rutas_visibles,
                    "lineas_de_cambio": 2, "lineas_de_cambio_visibles": lineas_visibles,
                    "recortado": recortado, "linea_final_incompleta": recortado})

    def test_objetivo_fuera_del_recorte_conserva_la_revision_y_el_hallazgo(self):
        referencia = ("diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n"
                      "@@ -1 +1 @@\n-x = 0\n+x = 1\n")
        diff = DIFF + referencia
        caso = dict(POSITIVO, defecto="marca del defecto conocido", arreglo="b" * 40)
        texto = json.dumps({"resumen": "R", "hallazgos": [HALLAZGO],
                            "comprobaciones": COMPROBACIONES})
        corte = len(DIFF) + referencia.index("\n+x = 1")
        for limite, visible in ((len(diff), True), (corte, False)):
            with self.subTest(visible=visible), mock.patch.object(mr, "MAX_DIFF", limite):
                registro, api = self.revisar([texto], destino={"b.py": [(1, 1, "")]},
                                             diff=diff, rutas={"a.py", "b.py"}, caso=caso)
                api.assert_called_once()
                self.assertEqual(registro["estado"], "valida")
                self.assertEqual(registro["hallazgos"], [dict(HALLAZGO, donde="fuera")])
                self.assertEqual(registro["cita_directa_visible"], visible)
                mensajes = api.call_args.args[0]
                self.assertNotIn(caso["defecto"], json.dumps(mensajes))
                self.assertNotIn(caso["arreglo"], json.dumps(mensajes))

    def test_respuesta_mal_formada_se_pide_dos_veces_y_queda_invalida(self):
        registro, api = self.revisar(["no json", "no json", "no json"])
        self.assertEqual((registro["estado"], api.call_count), ("invalida", 2))
        self.assertEqual(registro["hallazgos"], [])

    def test_rechazo_sin_json_guarda_solo_el_motivo_y_la_longitud(self):
        registro, _ = self.revisar(["no json", "tampoco"])
        self.assertEqual(registro["rechazadas"], [
            {"detalle": "json_invalido", "caracteres": len("no json")},
            {"detalle": "json_invalido", "caracteres": len("tampoco")}])

    def test_la_vista_rechazada_tiene_tope_y_se_conserva_al_recuperar_la_revision(self):
        rechazado = json.dumps({"resumen": "x" * (ev.MAX_RECHAZADA + 50)})
        texto = json.dumps({"resumen": "R", "hallazgos": [HALLAZGO],
                            "comprobaciones": COMPROBACIONES})
        registro, _ = self.revisar([rechazado, texto])
        self.assertEqual(registro["estado"], "valida")
        self.assertEqual(len(registro["rechazadas"]), 1)
        rechazada = registro["rechazadas"][0]
        self.assertEqual(len(rechazada["texto"]), ev.MAX_RECHAZADA)
        self.assertEqual(rechazada["caracteres"], len(rechazado))
        self.assertEqual(rechazada["detalle"], "hallazgos_no_lista")

    def test_json_sin_campos_conocidos_no_tiene_vista_de_rechazo(self):
        for texto in ('["campo libre"]', '{"desconocido": "campo libre"}'):
            with self.subTest(texto=texto):
                registro, _ = self.revisar([texto, texto])
                self.assertTrue(all("texto" not in r for r in registro["rechazadas"]))

    def test_el_registro_conserva_el_hallazgo_entero_para_etiquetarlo(self):
        largo = dict(HALLAZGO, problema="p" * 900, escenario="entrada " + "e" * 900,
                     arreglo="a" * 900)
        texto = json.dumps({"resumen": "R", "hallazgos": [largo],
                            "comprobaciones": COMPROBACIONES})
        registro, _ = self.revisar([texto])
        for campo in ("problema", "escenario", "arreglo", "fragmento"):
            self.assertEqual(registro["hallazgos"][0][campo], largo[campo])

    @staticmethod
    def respuesta_http(motivo, contenido):
        datos = {"choices": [{"finish_reason": motivo, "message": {"content": contenido}}]}
        return io.BytesIO(json.dumps(datos).encode("utf-8"))

    def test_reintenta_length_y_conserva_el_recuento_de_cortes(self):
        valido = json.dumps({"resumen": "R", "hallazgos": [HALLAZGO],
                             "comprobaciones": COMPROBACIONES})
        for segundo_motivo, contenido, estado, detalle, cortes in (
                ("stop", valido, "valida", "", 1),
                ("length", "{", "cortada", "length", 2)):
            with self.subTest(segundo_motivo=segundo_motivo):
                respuestas = [self.respuesta_http("length", "{"),
                              self.respuesta_http(segundo_motivo, contenido)]
                with mock.patch.object(mr.urllib.request, "urlopen", side_effect=respuestas) as abrir:
                    registro, _ = self.revisar(mr.llamar_mistral)
                self.assertEqual((registro["estado"], registro["detalle"]), (estado, detalle))
                self.assertEqual(abrir.call_count, 2)
                self.assertEqual(registro["cortes_salida"], cortes)
                self.assertEqual(registro["rechazadas"], [])
                informe = ev.resumir([POSITIVO], {"p": [registro]}, "m", 1)
                self.assertIn("**Respuestas cortadas por límite de salida:** %d." % cortes, informe)

    def test_el_corte_comparte_el_tope_con_un_rechazo_del_validador(self):
        registro, api = self.revisar(["no json", mr.RespuestaIncompleta("length")])
        self.assertEqual((registro["estado"], registro["detalle"]), ("cortada", "length"))
        self.assertEqual(api.call_count, 2)
        self.assertEqual(registro["cortes_salida"], 1)
        self.assertEqual(registro["rechazadas"], [
            {"detalle": "json_invalido", "caracteres": len("no json")}])

    def test_el_reintento_por_corte_respeta_el_presupuesto_http(self):
        with mock.patch.object(mr.urllib.request, "urlopen",
                               return_value=self.respuesta_http("length", "{")) as abrir, \
                ev.Presupuesto(1) as presupuesto:
            with self.assertRaises(ev.PresupuestoAgotado):
                self.revisar(mr.llamar_mistral)
        self.assertEqual(presupuesto.usadas, 1)
        abrir.assert_called_once()

    def test_respuesta_http_no_json_o_vacia_es_error_de_formato(self):
        for nombre, respuesta in (("no JSON", io.BytesIO(b"cuerpo no JSON")),
                                  ("vacia", self.respuesta_http("stop", " "))):
            with self.subTest(nombre=nombre), \
                    mock.patch.object(mr.urllib.request, "urlopen", return_value=respuesta) as abrir:
                registro, _ = self.revisar(mr.llamar_mistral)
                self.assertEqual((registro["estado"], registro["detalle"]),
                                 ("error", "formato_respuesta"))
                self.assertEqual(registro["cortes_salida"], 0)
                self.assertEqual(registro["rechazadas"], [])
                abrir.assert_called_once()

    def test_error_http_se_registra_sin_reintentar_la_respuesta(self):
        fallo = mr.urllib.error.HTTPError(mr.API, 500, "e", {}, io.BytesIO(b""))
        registro, api = self.revisar([fallo])
        self.assertEqual((registro["estado"], registro["detalle"]), ("error", "HTTP 500"))
        api.assert_called_once()


class TestPresupuesto(unittest.TestCase):
    def test_cuenta_peticiones_http_reales_y_corta_en_el_transporte(self):
        fallo = mr.urllib.error.HTTPError(mr.API, 503, "e", {}, io.BytesIO(b""))
        original = mr.urllib.request.urlopen
        with mock.patch.object(mr.urllib.request, "urlopen", side_effect=fallo) as abrir, \
                mock.patch.object(mr.time, "sleep"):
            with ev.Presupuesto(4) as presupuesto:
                # Una llamada con errores transitorios son tres peticiones, no una.
                with self.assertRaises(mr.urllib.error.HTTPError):
                    mr.llamar_mistral([], "m", "clave")
                self.assertEqual(presupuesto.usadas, 3)
                with self.assertRaises(ev.PresupuestoAgotado):
                    mr.llamar_mistral([], "m", "clave")
                self.assertEqual((presupuesto.usadas, abrir.call_count), (4, 4))
            self.assertIs(mr.urllib.request.urlopen, abrir)   # se restaura al salir
        self.assertIs(mr.urllib.request.urlopen, original)

    def respuesta(self, cuerpo):
        r = mock.MagicMock()
        r.__enter__.return_value = r
        r.read.return_value = json.dumps(cuerpo).encode("utf-8")
        return r

    def test_la_espera_crece_con_el_tope_de_salida(self):
        self.assertEqual(ev.espera_para(16000), 180)      # la del revisor
        self.assertEqual(ev.espera_para(128000), 1280)
        buena = {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                 "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        with mock.patch.object(mr.urllib.request, "urlopen",
                               return_value=self.respuesta(buena)) as abrir:
            with ev.Presupuesto(5, espera=1280):
                mr.llamar_mistral([], "m", "clave")
            self.assertEqual(abrir.call_args.kwargs["timeout"], 1280)
            with ev.Presupuesto(5):                       # sin espera: la del revisor
                mr.llamar_mistral([], "m", "clave")
            self.assertEqual(abrir.call_args.kwargs["timeout"], 180)

    def test_suma_los_tokens_que_declara_la_api(self):
        buena = {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                 "usage": {"prompt_tokens": 30000, "completion_tokens": 9000}}
        sin_uso = {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                   "usage": {"prompt_tokens": "muchos", "completion_tokens": True}}
        with mock.patch.object(mr.urllib.request, "urlopen", side_effect=[
                self.respuesta(buena), self.respuesta(buena), self.respuesta(sin_uso)]):
            with ev.Presupuesto(5) as presupuesto:
                for _ in range(3):
                    self.assertEqual(mr.llamar_mistral([], "m", "clave"), "{}")
        self.assertEqual((presupuesto.entrada, presupuesto.salida), (60000, 18000))
        self.assertEqual((presupuesto.respondidas, presupuesto.sin_esfuerzo), (2, 0))

    def test_avisa_si_el_modelo_no_admite_el_esfuerzo(self):
        rechazo = mr.urllib.error.HTTPError(
            mr.API, 400, "e", {}, io.BytesIO(b"reasoning_effort is not supported"))
        buena = {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                 "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        with mock.patch.object(mr.urllib.request, "urlopen",
                               side_effect=[rechazo, self.respuesta(buena)]):
            with ev.Presupuesto(5) as presupuesto:
                mr.llamar_mistral([], "m", "clave")
        self.assertEqual((presupuesto.usadas, presupuesto.sin_esfuerzo), (2, 1))

    def test_tras_el_rechazo_deja_de_pedir_el_esfuerzo(self):
        rechazo = mr.urllib.error.HTTPError(
            mr.API, 400, "e", {}, io.BytesIO(b"reasoning_effort is not supported"))
        buena = {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                 "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        cuerpos = []

        def abrir(req, *a, **k):
            cuerpos.append(json.loads(req.data))
            if len(cuerpos) == 1:
                raise rechazo
            return self.respuesta(buena)

        with mock.patch.object(mr.urllib.request, "urlopen", side_effect=abrir):
            with ev.Presupuesto(5) as presupuesto:
                for _ in range(3):
                    mr.llamar_mistral([], "m", "clave")
        # Tres revisiones: 4 peticiones, no 6 (una rechazada por revision).
        self.assertEqual((presupuesto.usadas, presupuesto.sin_esfuerzo), (4, 3))
        self.assertIn("reasoning_effort", cuerpos[0])
        self.assertTrue(all("reasoning_effort" not in c for c in cuerpos[1:]))
        self.assertTrue(all(c["model"] == "m" for c in cuerpos))

    def test_agotado_deja_informe_parcial_y_falla(self):
        salida = io.StringIO()
        registro = {"estado": "valida", "detalle": "", "solicitudes": 0,
                    "recortado": False, "hallazgos": [], "pendientes": []}
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"MISTRAL_API_KEY": "clave"}), \
                mock.patch.object(ev, "cargar_casos", return_value=[POSITIVO, NEGATIVO]), \
                mock.patch.object(mr, "obtener_diff", return_value=DIFF), \
                mock.patch.object(ev, "objetivos", return_value={"a.py": [(1, 2, "f")]}), \
                mock.patch.object(ev, "revisar",
                                  side_effect=[registro, ev.PresupuestoAgotado()]), \
                mock.patch.object(mr, "escribir_resumen"), redirect_stdout(salida):
            ruta = os.path.join(d, "r.json")
            codigo = ev.main(["--ejecutar", "--repeticiones", "1", "--salida", ruta])
            with open(ruta, encoding="utf-8") as fh:
                guardado = json.load(fh)
        self.assertEqual(codigo, 1)
        self.assertTrue(guardado["incompleta"])
        self.assertEqual((len(guardado["resultados"]["p"]), guardado["resultados"]["n"]),
                         (1, []))
        self.assertIn("Evaluación incompleta", salida.getvalue())


class TestProcedencia(unittest.TestCase):
    @staticmethod
    def crear_repo(ruta, archivos):
        os.makedirs(ruta)

        def git(*args):
            return subprocess.run(
                ("git", "-C", ruta) + args, check=True, capture_output=True,
                text=True).stdout.strip()

        git("init", "-q")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        for archivo in archivos:
            destino = os.path.join(ruta, archivo)
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            with open(destino, "w", encoding="utf-8") as fh:
                fh.write(archivo + "\n")
        git("add", "--", *archivos)
        git("-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
            "commit", "-qm", "fixture")
        return git("rev-parse", "HEAD")

    def test_distingue_checkouts_y_cambios_versionados(self):
        archivos = ("scripts/mistral_eval.py", "scripts/mistral_review.py")
        with tempfile.TemporaryDirectory() as d:
            evaluado = os.path.join(d, "evaluado")
            revisor = os.path.join(evaluado, ".mistral-review")
            sha_evaluado = self.crear_repo(evaluado, ("README.md",))
            sha_revisor = self.crear_repo(revisor, archivos)
            with mock.patch.object(ev, "_AQUI", os.path.join(revisor, "scripts")), \
                    mock.patch.object(ev.os, "getcwd", return_value=evaluado):
                self.assertEqual(ev._procedencia(), {
                    "revisor": {"commit": sha_revisor, "cambios_locales": False},
                    "evaluado": {"commit": sha_evaluado, "cambios_locales": False}})
                with open(os.path.join(revisor, archivos[1]), "a", encoding="utf-8") as fh:
                    fh.write("cambio local\n")
                self.assertTrue(ev._procedencia()["revisor"]["cambios_locales"])
                self.assertFalse(ev._procedencia()["evaluado"]["cambios_locales"])
                with open(os.path.join(evaluado, "README.md"), "a", encoding="utf-8") as fh:
                    fh.write("cambio local\n")
                self.assertTrue(ev._procedencia()["evaluado"]["cambios_locales"])

    def test_no_atribuye_una_copia_sin_seguimiento_al_repo_que_la_contiene(self):
        with tempfile.TemporaryDirectory() as d:
            evaluado = os.path.join(d, "evaluado")
            sha_evaluado = self.crear_repo(evaluado, ("scripts/otro.py",))
            scripts = os.path.join(evaluado, "scripts")
            for nombre in ("mistral_eval.py", "mistral_review.py"):
                with open(os.path.join(scripts, nombre), "w", encoding="utf-8") as fh:
                    fh.write("# copia sin seguimiento\n")
            with mock.patch.object(ev, "_AQUI", scripts), \
                    mock.patch.object(ev.os, "getcwd", return_value=evaluado):
                self.assertEqual(ev._procedencia(), {
                    "revisor": {"commit": None, "cambios_locales": None},
                    "evaluado": {"commit": sha_evaluado, "cambios_locales": False}})


class TestGuardadoParcial(unittest.TestCase):
    REGISTRO = {"estado": "valida", "detalle": "", "solicitudes": 0,
                "recortado": False, "hallazgos": [], "pendientes": []}

    def ejecutar(self, efectos):
        salida = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "r.json")
            vistos = []

            def revisar(caso, modelo, clave, **_):
                # Lo que hay en disco ANTES de cada revision.
                if os.path.exists(ruta):
                    with open(ruta, encoding="utf-8") as fh:
                        vistos.append(json.load(fh))
                efecto = efectos.pop(0)
                if isinstance(efecto, BaseException):
                    raise efecto
                return dict(efecto)

            with mock.patch.dict(os.environ, {"MISTRAL_API_KEY": "clave"}), \
                    mock.patch.object(ev, "cargar_casos",
                                      return_value=[POSITIVO, NEGATIVO]), \
                    mock.patch.object(mr, "obtener_diff", return_value=DIFF), \
                    mock.patch.object(ev, "objetivos", return_value={"a.py": [(1, 2, "f")]}), \
                    mock.patch.object(ev, "revisar", side_effect=revisar), \
                    mock.patch.object(mr, "escribir_resumen"), redirect_stdout(salida):
                codigo = ev.main(["--ejecutar", "--repeticiones", "1", "--salida", ruta])
            with open(ruta, encoding="utf-8") as fh:
                final = json.load(fh)
            sobrantes = os.listdir(d)
        return codigo, vistos, final, salida.getvalue(), sobrantes

    def test_guarda_procedencia_y_duracion_en_parcial_y_final(self):
        procedencia = {"revisor": {"commit": SHA, "cambios_locales": False},
                       "evaluado": {"commit": "b" * 40, "cambios_locales": True}}
        with mock.patch.object(ev, "_procedencia", return_value=procedencia, create=True), \
                mock.patch.object(ev, "monotonic_ns", create=True,
                                  side_effect=[0, 24_999_999, 30_000_000, 51_000_000]):
            codigo, vistos, final, _, _ = self.ejecutar([self.REGISTRO, self.REGISTRO])
        self.assertEqual(codigo, 0)
        self.assertEqual(vistos[0]["procedencia"], procedencia)
        self.assertEqual(final["procedencia"], procedencia)
        self.assertEqual(vistos[0]["resultados"]["p"][0]["duracion_ms"], 24)
        self.assertEqual(final["resultados"]["p"][0]["duracion_ms"], 24)
        self.assertEqual(final["resultados"]["n"][0]["duracion_ms"], 21)

    def test_el_json_se_guarda_tras_cada_revision(self):
        codigo, vistos, final, _, sobrantes = self.ejecutar(
            [self.REGISTRO, self.REGISTRO])
        self.assertEqual(codigo, 0)
        # Antes de la segunda revision ya estaba en disco la primera.
        self.assertEqual(len(vistos), 1)
        self.assertTrue(vistos[0]["en_curso"])
        self.assertEqual((len(vistos[0]["resultados"]["p"]),
                          vistos[0]["resultados"]["n"]), (1, []))
        self.assertFalse(final["en_curso"])
        self.assertFalse(final["incompleta"])
        self.assertEqual(sobrantes, ["r.json"])   # sin temporal olvidado

    def test_una_cancelacion_deja_lo_revisado_y_falla(self):
        codigo, _, final, salida, _ = self.ejecutar([self.REGISTRO, KeyboardInterrupt()])
        self.assertEqual(codigo, 1)
        self.assertTrue(final["cancelada"])
        self.assertTrue(final["incompleta"])
        self.assertEqual(len(final["resultados"]["p"]), 1)
        self.assertIn("Evaluación cancelada", salida)

    def test_protege_el_json_parcial_el_final_y_el_resumen(self):
        valor = "valor-ficticio-de-prueba"
        desconocido = "campo-ajeno-al-esquema"
        frase = 'El caso contiene password: "%s"' % valor
        tachada = 'El caso contiene password: "***"'
        hallazgo = dict(HALLAZGO, problema=frase, escenario=frase, arreglo=frase)
        pendiente = dict(COMPROBACIONES[0], escenario=frase, conclusion=frase)
        valido = dict(resumen="R", hallazgos=[hallazgo], comprobaciones=[pendiente])
        rechazado = json.dumps(dict(valido, resumen=frase,
                                    hallazgos=[dict(hallazgo, arreglo={"nota": desconocido})],
                                    comprobaciones=[dict(pendiente, estado="dudoso")],
                                    meta=desconocido))
        guardados = []
        guardar = ev.guardar

        def guardar_y_leer(ruta, datos):
            guardar(ruta, datos)
            with open(ruta, encoding="utf-8") as fh:
                guardados.append(fh.read())

        salida = io.StringIO()
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"MISTRAL_API_KEY": "clave"}), \
                mock.patch.object(ev, "cargar_casos", return_value=[POSITIVO]), \
                mock.patch.object(ev, "git", return_value="asunto"), \
                mock.patch.object(ev, "objetivos", return_value={"a.py": [(1, 2, "f")]}), \
                mock.patch.object(ev, "guardar", side_effect=guardar_y_leer), \
                mock.patch.object(mr, "obtener_diff", return_value=DIFF), \
                mock.patch.object(mr, "obtener_rutas_diff", return_value={"a.py"}), \
                mock.patch.object(mr, "obtener_contexto", return_value=""), \
                mock.patch.object(mr, "leer_guia", return_value=(None, "")), \
                mock.patch.object(mr.urllib.request, "urlopen", side_effect=[
                    TestRevisar.respuesta_http("stop", texto)
                    for texto in (frase, rechazado, json.dumps(valido))]) as abrir, \
                mock.patch.object(mr, "escribir_resumen") as resumen, redirect_stdout(salida):
            ruta = os.path.join(d, "r.json")
            codigo = ev.main(["--ejecutar", "--repeticiones", "2", "--salida", ruta])

        self.assertEqual((codigo, abrir.call_count), (0, 3))
        self.assertEqual(len(guardados), 3)
        for texto in guardados + [salida.getvalue(), resumen.call_args.args[0]]:
            self.assertNotIn(valor, texto)
            self.assertNotIn(desconocido, texto)
        estados = [json.loads(texto) for texto in guardados]
        self.assertEqual([datos["en_curso"] for datos in estados], [True, True, False])
        self.assertEqual([datos["peticiones"] for datos in estados], [2, 3, 3])
        for datos in estados:
            for revision in datos["resultados"]["p"]:
                self.assertEqual(revision["cobertura"]["diff"]["lineas_de_cambio_visibles"], 1)
        invalida, valida = estados[-1]["resultados"]["p"]
        self.assertEqual((invalida["estado"], valida["estado"]), ("invalida", "valida"))
        self.assertEqual((invalida["solicitudes"], valida["solicitudes"]), (2, 1))
        primero, segundo = invalida["rechazadas"]
        self.assertEqual(primero, {"detalle": "json_invalido", "caracteres": len(frase)})
        self.assertEqual(segundo["caracteres"], len(rechazado))
        self.assertEqual(segundo["detalle"], "comprobacion_0_estado_invalido")
        vista_hallazgo = json.loads(segundo["texto"])["hallazgos"][0]
        self.assertEqual(vista_hallazgo["problema"], tachada)
        self.assertNotIn("arreglo", vista_hallazgo)
        self.assertEqual(valida["hallazgos"], [dict(HALLAZGO, donde="funcion",
                                                   problema=tachada, escenario=tachada,
                                                   arreglo=tachada)])
        self.assertEqual(valida["pendientes"], [{"archivo": "a.py", "donde": "archivo",
                                                "escenario": tachada, "conclusion": tachada}])
        self.assertIn(tachada, resumen.call_args.args[0])


class TestInforme(unittest.TestCase):
    def test_cuenta_lo_que_senala_la_funcion_y_lista_para_etiquetar(self):
        def r(estado, donde=None):
            return {"estado": estado, "detalle": "length" if estado == "cortada" else "",
                    "solicitudes": 1, "recortado": False, "pendientes": [],
                    "hallazgos": [{"severidad": "alta", "archivo": "a.py",
                                   "problema": "texto del hallazgo", "fragmento": "x",
                                   "escenario": "entrada concreta", "arreglo": "a",
                                   "donde": donde}]
                    if donde else []}
        resultados = {"p": [r("valida", "funcion"), r("cortada")],
                      "n": [r("valida", "archivo"), r("valida")]}
        informe = ev.resumir([POSITIVO, NEGATIVO], resultados, "m", 2)
        self.assertIn("**Revisiones válidas:** 3 de 4 (cortada: 1)", informe)
        self.assertIn("en 1 de 1 casos", informe)
        self.assertIn("en 1 de 1 revisiones válidas", informe)
        self.assertIn("1 hallazgo(s) en 2 revisiones válidas, con 1 en los mismos archivos",
                      informe)
        self.assertIn("| `p` | positivo | 1/2 | 1/1 | 1 | 0 |", informe)
        self.assertIn("| `n` | negativo | 2/2 | — | 1 | 0 |", informe)
        self.assertIn("hallazgo 1 (alta) [funcion] en `a.py`: texto del hallazgo", informe)
        self.assertIn("  - Escenario: entrada concreta", informe)
        self.assertIn("Repetición 2: cortada (length).", informe)
        self.assertIn("Repetición 2: sin hallazgos ni pendientes.", informe)

    def test_el_control_se_cuenta_aparte_de_los_defectos_reales(self):
        def r(donde):
            return {"estado": "valida", "detalle": "", "solicitudes": 1,
                    "recortado": False, "pendientes": [],
                    "hallazgos": [{"severidad": "alta", "archivo": "a.py",
                                   "problema": "p", "fragmento": "x", "escenario": "e",
                                   "arreglo": "a", "donde": donde}]}
        control = dict(POSITIVO, id="c", control=True)
        informe = ev.resumir([POSITIVO, control], {"p": [r("fuera")], "c": [r("funcion")]},
                             "m", 1)
        # La coincidencia del control se cuenta por separado.
        self.assertIn("en 0 de 1 casos", informe)
        self.assertIn("**Localización del control", informe)
        self.assertIn("en 1 de 1 casos y en 1 de 1 revisiones válidas con cita directa visible", informe)
        self.assertIn("| `c` | control | 1/1 | 1/1 | 1 | 0 |", informe)

    def test_localizacion_exige_revision_valida_y_cita_directa_visible(self):
        def r(visible, donde, estado="valida"):
            return {"estado": estado, "detalle": "conexión" if estado == "error" else "",
                    "cita_directa_visible": visible, "pendientes": [],
                    "hallazgos": [dict(HALLAZGO, donde=donde)] if donde else []}

        fuera_del_diff = dict(POSITIVO, id="p-oculto")
        con_error = dict(POSITIVO, id="p-error")
        control = dict(POSITIVO, id="c", control=True)
        control_sin_cita = dict(control, id="c-oculto")
        resultados = {
            "p": [r(True, "fuera"), r(False, "funcion")],
            "p-oculto": [r(False, "funcion")],
            "p-error": [r(True, None, "error")],
            "c": [r(True, "funcion"), r(False, "funcion")],
            "c-oculto": [r(False, None, "error")],
            "n": [r(True, "archivo"), r(False, "archivo")],
        }
        informe = ev.resumir([POSITIVO, fuera_del_diff, con_error, control,
                              control_sin_cita, NEGATIVO], resultados, "m", 2)
        self.assertIn("**Revisiones válidas:** 7 de 9 (error: 2)", informe)
        self.assertIn("**Localización de positivos** · Algún hallazgo cita el objetivo "
                      "en 0 de 1 casos y en 0 de 1 revisiones válidas con cita directa visible.",
                      informe)
        self.assertIn("**Localización del control (defecto sintético)** · Algún hallazgo "
                      "cita el objetivo en 1 de 1 casos y en 1 de 1 revisiones válidas "
                      "con cita directa visible.", informe)
        self.assertIn("2 hallazgo(s) en 2 revisiones válidas, con 1 en los mismos archivos", informe)
        self.assertIn("| `p` | positivo | 2/2 | 0/1 | 2 | 0 |", informe)
        self.assertIn("| `p-oculto` | positivo | 1/1 | — | 1 | 0 |", informe)
        self.assertIn("| `c` | control | 2/2 | 1/1 | 2 | 0 |", informe)
        self.assertIn("Repetición 2: sin cita directa visible.", informe)
        self.assertIn("Repetición 1: error (conexión).", informe)

    def test_numera_los_hallazgos_en_el_orden_del_artefacto(self):
        base = {"estado": "valida", "detalle": "", "pendientes": [],
                "cita_directa_visible": False}
        primero = dict(HALLAZGO, donde="fuera", problema="Primero")
        segundo = dict(HALLAZGO, donde="funcion", problema="Segundo")
        resultados = {"p": [dict(base, hallazgos=[primero, segundo]),
                            dict(base, hallazgos=[segundo])]}
        informe = ev.resumir([POSITIVO], resultados, "m", 2)
        self.assertIn("**Localización de positivos** · Sin revisiones válidas con cita directa visible.",
                      informe)
        self.assertIn("Repetición 1, hallazgo 1 (media) [fuera] en `a.py`: Primero", informe)
        self.assertIn("Repetición 1, hallazgo 2 (media) [funcion] en `a.py`: Segundo", informe)
        self.assertIn("Repetición 2, hallazgo 1 (media) [funcion] en `a.py`: Segundo", informe)


class TestMain(unittest.TestCase):
    def main(self, argv, entorno=None):
        salida = io.StringIO()
        with mock.patch.dict(os.environ, entorno or {"MISTRAL_API_KEY": ""}), \
                mock.patch.object(ev, "cargar_casos", return_value=[POSITIVO, NEGATIVO]), \
                mock.patch.object(ev, "listar", return_value="lista"), \
                mock.patch.object(ev, "revisar") as revisar, \
                mock.patch.object(mr, "escribir_resumen"), redirect_stdout(salida):
            revisar.return_value = {"estado": "valida", "detalle": "", "solicitudes": 1,
                                    "recortado": False, "hallazgos": [], "pendientes": []}
            codigo = ev.main(argv)
        return codigo, revisar, salida.getvalue()

    def test_sin_ejecutar_solo_lista_y_no_llama(self):
        codigo, revisar, salida = self.main([])
        self.assertEqual((codigo, salida.strip()), (0, "lista"))
        revisar.assert_not_called()

    def test_contexto_auxiliar_se_opta_y_no_amplia_las_citas_del_diff(self):
        valor = "valor-ficticio-del-contexto"
        fuente_auxiliar = ('def auxiliar(x):\n    password = "%s"\n'
                           '    return x + len(password)\n' % valor) + "\n" * 15
        inicio = len(fuente_auxiliar.splitlines()) + 1
        fuente = fuente_auxiliar + "def f(x):\n    return auxiliar(x)\n"
        diff = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                "@@ -%d,2 +%d,2 @@\n def f(x):\n-    return x\n+    return auxiliar(x)\n"
                % (inicio, inicio))
        valido = {"resumen": "R", "hallazgos": [dict(HALLAZGO, fragmento="return auxiliar(x)")],
                  "comprobaciones": COMPROBACIONES}
        rechazado = dict(valido, hallazgos=[dict(HALLAZGO, fragmento="return x + len(password)")])
        respuestas = [TestRevisar.respuesta_http("stop", json.dumps(respuesta))
                      for respuesta in (valido, rechazado, valido)]
        artefactos = []

        with mock.patch.dict(os.environ, {"MISTRAL_API_KEY": "clave", "MISTRAL_PROVEEDOR": "mistral"}), \
                mock.patch.object(ev, "cargar_casos", return_value=[POSITIVO]), \
                mock.patch.object(ev, "_procedencia", return_value={}), \
                mock.patch.object(ev, "git", return_value="asunto"), \
                mock.patch.object(ev, "objetivos", return_value={"a.py": [(inicio, inicio + 1, "f")]}), \
                mock.patch.object(mr, "obtener_diff", return_value=diff), \
                mock.patch.object(mr, "obtener_rutas_diff", return_value={"a.py"}), \
                mock.patch.object(mr, "leer_guia", return_value=(None, "")), \
                mock.patch.object(mr.subprocess, "run", return_value=mock.Mock(
                    returncode=0, stdout=fuente.encode("utf-8"))), \
                mock.patch.object(mr.urllib.request, "urlopen", side_effect=respuestas) as abrir, \
                mock.patch.object(ev, "guardar", side_effect=lambda _, datos: artefactos.append(
                    json.loads(json.dumps(datos)))), \
                mock.patch.object(mr, "escribir_resumen"), redirect_stdout(io.StringIO()):
            args = ["--ejecutar", "--repeticiones", "1", "--salida", "r.json"]
            self.assertEqual(ev.main(args), 0)
            self.assertEqual(ev.main(args + ["--contexto", "auxiliar-local"]), 0)

        self.assertEqual(abrir.call_count, 3)
        self.assertEqual([(a["contexto_modo"], a["en_curso"]) for a in artefactos], [
            ("estandar", True), ("estandar", False), ("auxiliar-local", True), ("auxiliar-local", False)])
        self.assertNotIn(valor, json.dumps(artefactos))
        peticion_base, peticion_auxiliar, reintento = [json.loads(c.args[0].data)
                                                      for c in abrir.call_args_list]
        self.assertEqual(peticion_auxiliar, reintento)
        mensajes_base = peticion_base.pop("messages")
        mensajes_auxiliares = peticion_auxiliar.pop("messages")
        self.assertEqual(peticion_base, peticion_auxiliar)
        self.assertEqual(mensajes_base[0], mensajes_auxiliares[0])
        usuario_base, usuario_auxiliar = mensajes_base[1]["content"], mensajes_auxiliares[1]["content"]
        self.assertEqual(usuario_base.split("\n\n## Contexto adicional", 1)[0],
                         usuario_auxiliar.split("\n\n## Contexto adicional", 1)[0])
        self.assertEqual(usuario_base.split("\n\n## Diff", 1)[1], usuario_auxiliar.split("\n\n## Diff", 1)[1])
        self.assertNotIn("def auxiliar(x):", usuario_base)
        self.assertIn("def auxiliar(x):", usuario_auxiliar)
        self.assertIn('password = "***"', usuario_auxiliar)
        self.assertNotIn(valor, usuario_auxiliar)
        base = artefactos[1]["resultados"]["p"][0]
        auxiliar = artefactos[3]["resultados"]["p"][0]
        self.assertNotIn("caracteres_auxiliares", base["cobertura"]["contexto"])
        self.assertGreater(auxiliar["cobertura"]["contexto"]["caracteres_auxiliares"], 0)
        self.assertEqual((base["estado"], auxiliar["estado"]), ("valida", "valida"))
        self.assertEqual((base["solicitudes"], auxiliar["solicitudes"]), (1, 2))
        self.assertEqual(base["hallazgos"], auxiliar["hallazgos"])
        self.assertIn("cita_fuera_del_diff", auxiliar["rechazadas"][0]["detalle"])

    def test_contexto_desconocido_falla_antes_de_leer_casos_o_llamar(self):
        salida = io.StringIO()
        with mock.patch.object(ev, "cargar_casos") as cargar, \
                mock.patch.object(mr.urllib.request, "urlopen") as abrir, redirect_stderr(salida), \
                self.assertRaises(SystemExit) as error:
            ev.main(["--ejecutar", "--contexto", "inventado"])
        self.assertEqual(error.exception.code, 2)
        self.assertIn("auxiliar-local", salida.getvalue())
        cargar.assert_not_called()
        abrir.assert_not_called()

    def test_listado_avisa_si_falta_cita_directa_y_permite_el_caso(self):
        salida = io.StringIO()
        with mock.patch.object(ev, "cargar_casos", return_value=[POSITIVO]), \
                mock.patch.object(ev, "objetivos", return_value={"b.py": [(1, 1, "")]}), \
                mock.patch.object(mr, "obtener_diff", return_value=DIFF), \
                mock.patch.object(mr.urllib.request, "urlopen") as abrir, redirect_stdout(salida):
            codigo = ev.main([])
        self.assertEqual(codigo, 0)
        self.assertIn("sin cita directa visible", salida.getvalue())
        abrir.assert_not_called()

    def test_listado_y_ejecucion_rechazan_todo_el_lote_si_hay_casos_no_evaluables(self):
        vacio = dict(NEGATIVO, id="vacio", cabeza="b" * 40)
        sin_objetivo = dict(POSITIVO, id="sin-objetivo", cabeza="c" * 40)
        casos = [POSITIVO, vacio, sin_objetivo]
        registro = {"estado": "valida", "detalle": "", "solicitudes": 0,
                    "recortado": False, "hallazgos": [], "pendientes": []}
        for argv in ([], ["--ejecutar"]):
            salida = io.StringIO()
            with self.subTest(argv=argv), \
                    mock.patch.dict(os.environ, {"MISTRAL_API_KEY": "clave"}), \
                    mock.patch.object(ev, "cargar_casos", return_value=casos), \
                    mock.patch.object(mr, "obtener_diff",
                                      side_effect=lambda base, cabeza: "" if cabeza == vacio["cabeza"] else DIFF), \
                    mock.patch.object(ev, "objetivos",
                                      side_effect=lambda caso: {} if caso == sin_objetivo else {"a.py": [(1, 2, "f")]}), \
                    mock.patch.object(ev, "revisar", return_value=registro) as revisar, \
                    mock.patch.object(mr, "escribir_resumen"), redirect_stdout(salida):
                codigo = ev.main(argv)
                self.assertEqual(codigo, 2)
                revisar.assert_not_called()
                self.assertIn("Caso vacio: diff vacío tras aplicar exclusiones", salida.getvalue())
                self.assertIn("Caso sin-objetivo: positivo sin objetivo derivable tras aplicar exclusiones",
                              salida.getvalue())

    def test_listado_valido_comprueba_solo_los_casos_seleccionados(self):
        salida = io.StringIO()
        with mock.patch.object(ev, "cargar_casos", return_value=[POSITIVO, NEGATIVO]), \
                mock.patch.object(mr, "obtener_diff", return_value=DIFF) as obtener_diff, \
                mock.patch.object(ev, "objetivos", return_value={"a.py": [(1, 2, "f")]}), \
                redirect_stdout(salida):
            self.assertEqual(ev.main(["--solo", "p"]), 0)
        obtener_diff.assert_called_once_with(POSITIVO["base"], POSITIVO["cabeza"])
        self.assertEqual(salida.getvalue(), "p [positivo] diff=%d caracteres\n  a.py\n    1-2 f\n" % len(DIFF))

    def test_sin_clave_no_evalua_y_falla(self):
        codigo, revisar, salida = self.main(["--ejecutar"])
        self.assertEqual(codigo, 2)
        revisar.assert_not_called()
        self.assertIn("MISTRAL_API_KEY", salida)

    def test_tope_de_solicitudes_y_casos_desconocidos(self):
        con_clave = {"MISTRAL_API_KEY": "clave"}
        codigo, revisar, _ = self.main(["--ejecutar", "--repeticiones", "500"], con_clave)
        self.assertEqual(codigo, 2)
        revisar.assert_not_called()
        codigo, revisar, _ = self.main(["--ejecutar", "--solo", "no-existe"], con_clave)
        self.assertEqual(codigo, 2)
        revisar.assert_not_called()

    def test_ejecuta_cada_caso_las_veces_pedidas_y_guarda_el_json(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "r.json")
            codigo, revisar, salida = self.main(
                ["--ejecutar", "--solo", "p", "--repeticiones", "3", "--salida", ruta],
                {"MISTRAL_API_KEY": "clave", "MISTRAL_MODEL": "modelo-x"})
            with open(ruta, encoding="utf-8") as fh:
                guardado = json.load(fh)
        self.assertEqual((codigo, revisar.call_count), (0, 3))
        self.assertEqual(guardado["modelo"], "modelo-x")
        self.assertEqual(len(guardado["resultados"]["p"]), 3)
        self.assertNotIn("clave", json.dumps(guardado))
        self.assertIn("Evaluación del revisor de Mistral (`modelo-x`", salida)

    def test_modelo_y_esfuerzo_solo_para_la_evaluacion(self):
        vistos = []
        con_clave = {"MISTRAL_API_KEY": "clave", "MISTRAL_MODEL": "modelo-x"}
        previo = mr.ESFUERZO
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "r.json")
            salida = io.StringIO()
            registro = {"estado": "valida", "detalle": "", "solicitudes": 1,
                        "recortado": False, "hallazgos": [], "pendientes": []}
            with mock.patch.dict(os.environ, con_clave), \
                    mock.patch.object(ev, "cargar_casos", return_value=[POSITIVO]), \
                    mock.patch.object(mr, "obtener_diff", return_value=DIFF), \
                    mock.patch.object(ev, "objetivos", return_value={"a.py": [(1, 2, "f")]}), \
                    mock.patch.object(ev, "revisar", side_effect=lambda c, m, k, **_: (
                        vistos.append((m, mr.ESFUERZO)), dict(registro))[1]), \
                    mock.patch.object(mr, "escribir_resumen"), redirect_stdout(salida):
                codigo = ev.main(["--ejecutar", "--repeticiones", "1", "--salida", ruta,
                                  "--modelo", "mistral-large-2512", "--esfuerzo", "high"])
            with open(ruta, encoding="utf-8") as fh:
                guardado = json.load(fh)
        self.assertEqual(codigo, 0)
        self.assertEqual(vistos, [("mistral-large-2512", "high")])
        self.assertEqual(mr.ESFUERZO, previo)          # se restaura al acabar
        self.assertEqual((guardado["modelo"], guardado["esfuerzo"]),
                         ("mistral-large-2512", "high"))
        self.assertEqual(guardado["tokens"]["entrada"], 0)
        self.assertIn("Esfuerzo de razonamiento pedido: `high`", salida.getvalue())

    def test_max_salida_solo_para_la_evaluacion(self):
        vistos = []
        previo = mr.MAX_SALIDA
        registro = {"estado": "valida", "detalle": "", "solicitudes": 1, "recortado": False,
                    "hallazgos": [], "pendientes": [],
                    "rechazadas": [{"detalle": "d", "caracteres": 1, "texto": "t"}]}
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "r.json")
            salida = io.StringIO()
            with mock.patch.dict(os.environ, {"MISTRAL_API_KEY": "clave"}), \
                    mock.patch.object(ev, "cargar_casos", return_value=[POSITIVO]), \
                    mock.patch.object(mr, "obtener_diff", return_value=DIFF), \
                    mock.patch.object(ev, "objetivos", return_value={"a.py": [(1, 2, "f")]}), \
                    mock.patch.object(ev, "revisar", side_effect=lambda c, m, k, **_: (
                        vistos.append(mr.MAX_SALIDA), dict(registro))[1]), \
                    mock.patch.object(mr, "escribir_resumen"), redirect_stdout(salida):
                codigo = ev.main(["--ejecutar", "--repeticiones", "1", "--salida", ruta,
                                  "--max-salida", "32000"])
            with open(ruta, encoding="utf-8") as fh:
                guardado = json.load(fh)
        self.assertEqual((codigo, vistos), (0, [32000]))
        self.assertEqual(mr.MAX_SALIDA, previo)        # se restaura al acabar
        self.assertEqual(guardado["max_salida"], 32000)
        self.assertIn("Tope de salida: 32000 tokens por respuesta, con 320 s de espera",
                      salida.getvalue())
        self.assertIn("1 respuesta(s) rechazadas", salida.getvalue())
        self.assertNotIn("`t`", salida.getvalue())     # el texto no va al resumen

    def test_max_salida_vacio_usa_el_del_revisor_y_rechaza_valores_raros(self):
        con_clave = {"MISTRAL_API_KEY": "clave"}
        codigo, _, salida = self.main(["--ejecutar", "--solo", "p", "--repeticiones", "1",
                                       "--max-salida", ""], con_clave)
        self.assertEqual(codigo, 0)
        self.assertIn("Tope de salida: %d tokens" % mr.MAX_SALIDA, salida)
        for valor in ("3999", "256001", "abc", "-1", "1e4", "16000.0", "²", "١٢٠٠٠",
                      "9" * 20):
            with self.subTest(valor=valor):
                codigo, revisar, _ = self.main(["--ejecutar", "--max-salida", valor],
                                               con_clave)
                self.assertEqual(codigo, 2)
                revisar.assert_not_called()

    def test_vacios_usan_la_variable_y_el_esfuerzo_del_revisor(self):
        codigo, revisar, salida = self.main(
            ["--ejecutar", "--solo", "p", "--repeticiones", "1",
             "--modelo", "", "--esfuerzo", ""],
            {"MISTRAL_API_KEY": "clave", "MISTRAL_MODEL": "modelo-x"})
        self.assertEqual(codigo, 0)
        self.assertEqual(revisar.call_args.args[1], "modelo-x")
        self.assertIn("pedido: `%s`" % mr.ESFUERZO, salida)

    def test_rechaza_modelo_o_esfuerzo_raros_sin_llamar(self):
        con_clave = {"MISTRAL_API_KEY": "clave"}
        for extra in (["--modelo", "m\"; rm -rf /"], ["--modelo", "a b"],
                      ["--modelo", "x" * 81], ["--esfuerzo", "máximo"]):
            with self.subTest(extra=extra):
                codigo, revisar, _ = self.main(["--ejecutar"] + extra, con_clave)
                self.assertEqual(codigo, 2)
                revisar.assert_not_called()


try:
    import yaml
except ImportError:
    yaml = None


@unittest.skipUnless(yaml, "PyYAML no instalado")
class TestWorkflowEval(unittest.TestCase):
    def cargar(self, ruta):
        with open(os.path.join(_RAIZ, ruta), encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    def test_evalua_la_historia_del_repo_que_llama_sin_permiso_de_escribir(self):
        trabajo = self.cargar(".github/workflows/eval.yml")["jobs"]["mistral-eval"]
        self.assertEqual(trabajo["permissions"], {"contents": "read"})
        checkouts = [p for p in trabajo["steps"] if p.get("uses", "").startswith("actions/checkout@")]
        self.assertEqual(checkouts[0]["with"]["fetch-depth"], 0)  # casos = commits antiguos
        self.assertNotIn("repository", checkouts[0]["with"])      # el repo que llama
        self.assertEqual(checkouts[1]["with"]["path"], ".mistral-review")
        ordenes = [p["run"] for p in trabajo["steps"] if "run" in p]
        self.assertTrue(all("python3 .mistral-review/scripts/mistral_eval.py" in o for o in ordenes))
        self.assertEqual(sum("--ejecutar" in o for o in ordenes), 1)
        # Mismas exclusiones que la revisión del repo: los objetivos y el diff
        # de cada caso no deben incluir generados (paso de email-triage-plugin).
        pasos = [p for p in trabajo["steps"] if "run" in p]
        self.assertTrue(all(p["env"]["MISTRAL_EXCLUIDOS"] == "${{ inputs.excluded }}"
                            for p in pasos))

    def test_el_ejemplo_solo_es_manual_y_pasa_entradas_que_existen(self):
        ejemplo = self.cargar("ejemplos/mistral-eval.yml")
        self.assertEqual(list(ejemplo[True]), ["workflow_dispatch"])  # YAML 1.1: `on` es True
        trabajo = ejemplo["jobs"]["mistral-eval"]
        entradas = self.cargar(".github/workflows/eval.yml")[True]["workflow_call"]["inputs"]
        self.assertLessEqual(set(trabajo["with"]), set(entradas))

    def test_el_contexto_auxiliar_es_optativo_y_llega_al_evaluador(self):
        ejemplo = self.cargar("ejemplos/mistral-eval.yml")
        opcion = ejemplo[True]["workflow_dispatch"]["inputs"]["contexto"]
        self.assertEqual(opcion["type"], "choice")
        self.assertEqual(opcion["default"], "estandar")
        self.assertEqual(opcion["options"], ["estandar", "auxiliar-local"])
        self.assertEqual(ejemplo["jobs"]["mistral-eval"]["with"]["contexto"],
                         "${{ inputs.contexto }}")

        reutilizable = self.cargar(".github/workflows/eval.yml")
        entrada = reutilizable[True]["workflow_call"]["inputs"]["contexto"]
        self.assertEqual(entrada["default"], "estandar")
        self.assertEqual(entrada["type"], "string")
        pasos = [p for p in reutilizable["jobs"]["mistral-eval"]["steps"] if "run" in p]
        self.assertEqual(len(pasos), 2)
        for paso in pasos:
            self.assertEqual(paso["env"]["CONTEXTO_EVAL"], "${{ inputs.contexto }}")
            self.assertIn('--contexto "$CONTEXTO_EVAL"', paso["run"])


if __name__ == "__main__":
    unittest.main()
