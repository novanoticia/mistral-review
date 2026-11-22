#!/usr/bin/env python3
"""Evaluación del revisor de Mistral con casos reales del historial.

Responde a una pregunta que los tests no pueden responder: ¿encuentra el
revisor defectos de verdad, y cuántos hallazgos falsos repite? (propuesta 2
de docs/auditoria-mistral-2026-10-29.md en email-triage-plugin).

Cada caso fija dos commits del historial y se revisa con el MISMO código que
un PR (diff, contexto, prompt, llamada y validación de mistral_review.py):

- positivo: `cabeza` es un commit con un defecto conocido y `arreglo` el
  commit que lo corrigió. El objetivo se deriva solo: las funciones de
  `cabeza` que el arreglo tocó. Un hallazgo «señala la función» si cita una
  línea del diff dentro de una de ellas.
- negativo: `cabeza` es un commit sobre el que Mistral publicó hallazgos que
  la auditoría contrastó como falsos, y `archivos` dónde estaban. Se cuentan
  los hallazgos y cuántos caen otra vez en esos archivos.

Lo que mide es mecánico (dónde apunta un hallazgo). Que el hallazgo describa
el defecto real lo decide una persona: el informe lista cada hallazgo para
etiquetarlo. Señalar la función no prueba acertar; no señalarla sí indica que
el defecto no se vio.

Diferencias con una revisión real, a propósito: el título es el asunto del
commit y no hay descripción (la del PR histórico podía nombrar el arreglo), y
la guía es la del árbol actual, no la de entonces.

Se ejecuta desde la raíz del repo evaluado (el workflow `eval.yml` lo hace
así): los commits de los casos son de ese repo.

Solo stdlib. Sin `--ejecutar` no llama a la API: lista casos y objetivos.
"""
import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.error
from time import monotonic_ns

_AQUI = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "mistral_review", os.path.join(_AQUI, "mistral_review.py"))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)

# Relativa al directorio de trabajo: el repo evaluado, cuyos commits son los
# casos (git se ejecuta ahí). El script vive en mistral-review desde el
# 2026-11-16; los casos siguen en cada repo.
CASOS = os.path.join(".github", "eval", "casos.json")
ESFUERZOS = ("low", "medium", "high")
MAX_LLAMADAS = 80  # tope de peticiones HTTP por ejecución: cada una cuesta dinero
# Límites de `--max-salida`: por debajo no cabe una revisión.
# 256.000 a DeepSeek V4.1 Flash, decisión del mantenedor: con 32.000 cortó 2
# de 3 revisiones (ejecución #8). Peor caso: 256.000 × 1,20 $/M ≈ 0,31 $ por
# respuesta. Si el modelo no admite tanto, la API responde 400 sin cobrar.
MAX_SALIDA_MINIMA, MAX_SALIDA_MAXIMA = 4000, 256000
MAX_RECHAZADA = 20000  # caracteres de la vista filtrada de una respuesta rechazada
CAMPOS_HALLAZGO = ("severidad", "archivo", "fragmento", "problema", "escenario", "arreglo")
CAMPOS_PENDIENTE = ("archivo", "escenario", "conclusion")
# Espera por petición en la evaluación: la del revisor (180 s) o más si el tope
# de salida lo pide. La API responde de una vez al terminar; DeepSeek tardó
# ~145 s en 32.000 tokens (ejecución #8), así que con un tope mayor una espera
# fija cortaría la conexión y reintentaría pagando otra vez.
ESPERA_MINIMA = 180
TOKENS_POR_SEGUNDO = 100  # conservador: medido ~220 con DeepSeek


def espera_para(max_salida):
    """Segundos de espera por petición para un tope de salida dado."""
    return max(ESPERA_MINIMA, max_salida // TOKENS_POR_SEGUNDO)


class PresupuestoAgotado(RuntimeError):
    """Se alcanzó MAX_LLAMADAS: la evaluación se detiene con lo que lleva."""


class Presupuesto:
    """Cuenta las peticiones HTTP reales, no las llamadas a `llamar_mistral`.

    Cada llamada puede hacer hasta tres peticiones (errores transitorios o un
    parámetro que el modelo no admite), y cada revisión hasta
    INTENTOS_RESPUESTA llamadas: contar revisiones dejaba pasar varias veces
    el tope anunciado (Codex, email-triage-plugin#203). El tope se aplica en el transporte.
    """

    def __init__(self, maximo, espera=None, con_esfuerzo=True):
        self.maximo, self.usadas, self.espera = maximo, 0, espera
        # Sin esfuerzo pedido (OpenRouter por defecto), que falte en la
        # petición no significa que el modelo lo haya rechazado.
        self.con_esfuerzo = con_esfuerzo
        # Consumo real que declara la API en `usage`: el coste deja de ser una
        # estimación. `sin_esfuerzo` cuenta las respuestas obtenidas tras
        # retirar `reasoning_effort` (el modelo no lo admite): ahí el esfuerzo
        # pedido no se aplicó.
        self.entrada = self.salida = self.respondidas = self.sin_esfuerzo = 0
        # Coste que declara OpenRouter en `usage.cost` (créditos). Mistral no
        # lo envía: entonces se dice que falta, sin estimarlo.
        self.coste, self.con_coste = 0.0, 0
        # Quién sirvió cada respuesta según la API: `provider` (OpenRouter; su
        # doc no lo promete sin streaming) y `model`. Recuentos por nombre.
        self.servido_por, self.modelo_servido = {}, {}
        # Tras el primer rechazo de `reasoning_effort`, las demás peticiones ya
        # salen sin él: cada revisión gastaba una petición rechazada (400) y
        # otra buena (ejecuciones #3 y #4: 26 y 24 de 80).
        self.omitir_esfuerzo = False
        self._abrir = mr.urllib.request.urlopen

    def abrir(self, req, *args, **kwargs):
        if self.usadas >= self.maximo:
            raise PresupuestoAgotado()
        if self.omitir_esfuerzo:
            _quitar_esfuerzo(req)
        if self.espera is not None:
            kwargs["timeout"] = self.espera   # sustituye los 180 s del revisor
        self.usadas += 1
        return _Respuesta(self._abrir(req, *args, **kwargs), self, req)

    def anotar(self, datos, req):
        """Suma los tokens de una respuesta y anota quién la sirvió. Solo
        números y nombres saneados: nada del contenido."""
        try:
            respuesta = json.loads(datos)
            uso = respuesta.get("usage")
            entrada, salida = uso.get("prompt_tokens"), uso.get("completion_tokens")
        except (ValueError, AttributeError):
            return
        for n in (entrada, salida):
            if not isinstance(n, int) or isinstance(n, bool) or n < 0:
                return
        self.entrada += entrada
        self.salida += salida
        self.respondidas += 1
        for campo, recuento in (("provider", self.servido_por),
                                ("model", self.modelo_servido)):
            nombre = _nombre_servido(respuesta.get(campo))
            recuento[nombre] = recuento.get(nombre, 0) + 1
        coste = uso.get("cost")
        if isinstance(coste, (int, float)) and not isinstance(coste, bool) and coste >= 0:
            self.coste += coste
            self.con_coste += 1
        try:
            if self.con_esfuerzo and "reasoning_effort" not in json.loads(req.data):
                self.sin_esfuerzo += 1
                self.omitir_esfuerzo = True
        except (ValueError, AttributeError, TypeError):
            pass

    def __enter__(self):
        mr.urllib.request.urlopen = self.abrir
        return self

    def __exit__(self, *exc):
        mr.urllib.request.urlopen = self._abrir
        return False


def _nombre_servido(valor):
    """Nombre de proveedor o modelo apto para el informe: alfabeto fijo y
    tope de longitud, para que nada raro de la respuesta llegue al resumen.
    Sin valor, «no lo informa»."""
    if not isinstance(valor, str):
        return "no lo informa"
    return re.sub(r"[^A-Za-z0-9 ._:/-]", "", valor).strip()[:60] or "no lo informa"


def _recuentos(recuento):
    return ", ".join("`%s` (%d)" % par for par in sorted(recuento.items()))


def _quitar_esfuerzo(req):
    """Retira `reasoning_effort` del cuerpo JSON de la petición, si lo lleva."""
    try:
        cuerpo = json.loads(req.data)
    except (ValueError, TypeError):
        return
    if isinstance(cuerpo, dict) and "reasoning_effort" in cuerpo:
        del cuerpo["reasoning_effort"]
        req.data = json.dumps(cuerpo).encode("utf-8")


def guardar(ruta, datos):
    """Escribe el JSON entero de forma atómica (temporal + os.replace).

    Se llama tras cada revisión: si el workflow se cancela (ejecución #5, 90
    minutos sin artefacto), lo ya revisado queda en disco, nunca a medias.
    """
    temporal = ruta + ".tmp"
    with open(temporal, "w", encoding="utf-8") as fh:
        json.dump(datos, fh, ensure_ascii=False, indent=2)
    os.replace(temporal, ruta)


class _Respuesta:
    """La respuesta HTTP tal cual, pero dejando ver sus tokens al presupuesto."""

    def __init__(self, respuesta, presupuesto, req):
        self._respuesta, self._presupuesto, self._req = respuesta, presupuesto, req

    def __enter__(self):
        self._respuesta.__enter__()
        return self

    def __exit__(self, *exc):
        return self._respuesta.__exit__(*exc)

    def read(self, *args):
        datos = self._respuesta.read(*args)
        self._presupuesto.anotar(datos, self._req)
        return datos


@contextlib.contextmanager
def con_esfuerzo(valor, atributo="ESFUERZO"):
    """Fija `mr.ESFUERZO` (o `mr.ESFUERZO_OPENROUTER`) mientras dura la
    evaluación y lo restaura al salir."""
    previo = getattr(mr, atributo)
    setattr(mr, atributo, valor)
    try:
        yield
    finally:
        setattr(mr, atributo, previo)


@contextlib.contextmanager
def con_max_salida(valor):
    """Fija `mr.MAX_SALIDA` (max_tokens) solo para la evaluación.

    DeepSeek V4.1 Flash agotó los 16.000 tokens razonando en 12 de 13 casos
    (ejecución #6): con el tope del revisor, sus revisiones no se pueden medir.
    """
    previo, mr.MAX_SALIDA = mr.MAX_SALIDA, valor
    try:
        yield
    finally:
        mr.MAX_SALIDA = previo


def git(*args):
    return subprocess.run(("git",) + args, check=True, capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout


def _procedencia_checkout(directorio, archivos=()):
    sin_procedencia = {"commit": None, "cambios_locales": None}
    try:
        raiz = git("-C", directorio, "rev-parse", "--show-toplevel").removesuffix("\n")
        commit = git("-C", raiz, "rev-parse", "--verify", "HEAD^{commit}").strip()
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
            return sin_procedencia
        if archivos:
            # Una copia suelta puede encontrarse dentro del checkout evaluado.
            if os.path.realpath(raiz) != os.path.realpath(directorio):
                return sin_procedencia
            versionados = git("-C", raiz, "ls-tree", "--name-only", commit, "--", *archivos)
            if set(versionados.splitlines()) != set(archivos):
                return sin_procedencia
        cambios = git("--no-optional-locks", "-C", raiz, "status", "--porcelain=v1",
                      "--untracked-files=no", "--", *archivos)
    except (OSError, subprocess.CalledProcessError):
        return sin_procedencia
    return {"commit": commit, "cambios_locales": bool(cambios)}


def _procedencia():
    scripts = ("scripts/mistral_eval.py", "scripts/mistral_review.py")
    return {"revisor": _procedencia_checkout(os.path.dirname(_AQUI), scripts),
            "evaluado": _procedencia_checkout(os.getcwd())}


def _validar_tramos(caso, campo):
    tramos = caso[campo]
    if not isinstance(tramos, list) or not tramos:
        raise ValueError("caso %s: %s debe ser una lista no vacía" % (caso["id"], campo))
    for tramo in tramos:
        if not isinstance(tramo, dict):
            raise ValueError("caso %s: tramo de %s sin archivo y rango" % (caso["id"], campo))
        archivo, desde, hasta = tramo.get("archivo"), tramo.get("desde"), tramo.get("hasta")
        if (not isinstance(archivo, str) or "\0" in archivo
                or any(p in ("", ".", "..") for p in archivo.split("/"))
                or type(desde) is not int or type(hasta) is not int or not 1 <= desde <= hasta):
            raise ValueError("caso %s: %s requiere ruta relativa y líneas válidas"
                             % (caso["id"], campo))
        if campo == "fragmentos_contexto" and not archivo.isprintable():
            raise ValueError("caso %s: fragmentos_contexto requiere rutas imprimibles" % caso["id"])


def cargar_casos(ruta):
    """Lee y valida el fichero de casos; un caso mal escrito no se evalúa a medias."""
    with open(ruta, encoding="utf-8") as fh:
        casos = json.load(fh)
    vistos = set()
    for c in casos:
        if not isinstance(c, dict) or c.get("tipo") not in ("positivo", "negativo"):
            raise ValueError("caso sin tipo válido: %r" % (c,))
        claves = ["id", "base", "cabeza", "defecto"] + (
            ["arreglo"] if c["tipo"] == "positivo" else ["archivos"])
        for clave in claves:
            if not c.get(clave):
                raise ValueError("caso %s: falta %s" % (c.get("id"), clave))
        if "archivos_objetivo" in c and c["tipo"] != "positivo":
            raise ValueError("caso %s: archivos_objetivo solo admite un positivo" % c["id"])
        for clave in ("archivos", "ignorar", "archivos_objetivo"):
            # Una cadena suelta se recorrería letra a letra sin avisar.
            if clave in c and not (isinstance(c[clave], list) and all(
                    isinstance(x, str) and x for x in c[clave])):
                raise ValueError("caso %s: %s no es una lista de rutas" % (c["id"], clave))
        for clave in ("base", "cabeza", "arreglo"):
            # SHA completo y nada más: `$` admitiría un salto de línea final.
            if clave in c and not re.fullmatch(r"[0-9a-f]{40}", str(c[clave])):
                raise ValueError("caso %s: %s no es un SHA completo" % (c["id"], clave))
        if "control" in c and not (c["control"] is True and c["tipo"] == "positivo"):
            # Un control es un positivo sintético que se cuenta aparte.
            raise ValueError("caso %s: control solo admite true, en un positivo" % c["id"])
        if "evidencia_minima" in c and c["tipo"] != "positivo":
            raise ValueError("caso %s: evidencia_minima solo admite un positivo" % c["id"])
        for campo in ("evidencia_minima", "fragmentos_contexto"):
            if campo in c:
                _validar_tramos(c, campo)
        if c["id"] in vistos:
            raise ValueError("caso repetido: %s" % c["id"])
        vistos.add(c["id"])
    return casos


def _rangos_objetivo(texto, lineas):
    """Rangos de referencia para localizar citas. Devuelve [] si no parsea.

    Las funciones que contienen una línea cambiada y, fuera de toda función,
    solo esa línea. No se usa `mr._rangos_python`: da contexto, y su ventana
    alrededor de un cambio de módulo abarca funciones que el arreglo no tocó
    (Codex, email-triage-plugin#203: un hallazgo en una vecina contaba como detección).
    """
    return _rangos_por_funcion(mr._funciones_python(texto), lineas)


def _rangos_por_funcion(funciones, lineas):
    """La función que contiene cada línea cambiada; fuera de toda función,
    solo esa línea. `funciones` es None si el archivo no se pudo analizar."""
    if funciones is None:
        return []
    rangos = set()
    for n in lineas:
        dentro = [f for f in funciones if f[0] <= n <= f[1]]
        rangos.add(dentro[0] if dentro else (n, n, ""))
    unidos = []
    for inicio, fin, nombre in sorted(rangos):
        # Solo se juntan líneas de módulo seguidas: nunca con una función.
        if unidos and not nombre and not unidos[-1][2] and inicio == unidos[-1][1] + 1:
            unidos[-1] = (unidos[-1][0], fin, "")
        else:
            unidos.append((inicio, fin, nombre))
    return unidos


def _rangos_objetivo_archivo(archivo, texto, lineas):
    """Rangos de un archivo de `archivos_objetivo`, sea del tipo que sea.

    Python: la función (AST). Shell (`.sh`, `.bash` o con shebang de sh/bash):
    la función de shell (`mr._funciones_shell`). Lo demás (YAML, Markdown…):
    las líneas cambiadas, juntando las seguidas.
    """
    if archivo.endswith(".py"):
        return _rangos_objetivo(texto, lineas)
    primera = texto.split("\n", 1)[0]
    if archivo.endswith((".sh", ".bash")) or re.match(r"#!.*\b(ba)?sh\b", primera):
        return _rangos_por_funcion(mr._funciones_shell(texto), lineas)
    return _rangos_por_funcion([], lineas)


def objetivos(caso):
    """{archivo: [(inicio, fin, nombre)]} de referencia para localizar citas.

    Para un positivo se usa el diff de `arreglo` a `cabeza` para encontrar los
    rangos del código Python que se corrigió, excluidos los tests. Si el caso
    trae `archivos_objetivo`, se usan solo esos archivos, de cualquier tipo
    (`_rangos_objetivo_archivo`): así se evalúan repos de shell, YAML o
    Markdown sin cambiar los casos que ya existían. Para un negativo se usan
    los archivos de los hallazgos históricos.
    """
    if caso["tipo"] == "negativo":
        return {archivo: [] for archivo in caso["archivos"]}
    exclusiones = [":(exclude)%s" % ruta for ruta in mr.excluidos()]
    inverso = git("-c", "core.quotePath=true", "diff", "--no-color", "--no-ext-diff",
                  "--no-textconv", "--src-prefix=a/", "--dst-prefix=b/", "-U0",
                  caso["arreglo"], caso["cabeza"], "--", ".", *exclusiones)
    destino = {}
    elegidos = caso.get("archivos_objetivo")
    for archivo, lineas in sorted(mr.lineas_cambiadas(inverso).items()):
        if elegidos is not None:
            if archivo not in elegidos:
                continue
        elif (not archivo.endswith(".py") or mr._es_test(archivo)
                or archivo in caso.get("ignorar", ())):
            continue  # `ignorar`: lo que el arreglo tocó sin ser el defecto
        try:
            texto = git("show", "%s:%s" % (caso["cabeza"], archivo))
        except subprocess.CalledProcessError:
            continue  # el arreglo creó el archivo: en la cabeza no hay dónde apuntar
        rangos = (_rangos_objetivo_archivo(archivo, texto, lineas) if elegidos is not None
                  else _rangos_objetivo(texto, lineas))
        if rangos:
            destino[archivo] = rangos
    return destino


class CasoNoEvaluable(ValueError):
    """El caso requiere preparación antes de consultar al modelo."""


def _datos_caso(caso):
    """Prepara el diff efectivo y la referencia de un caso evaluable."""
    diff = mr.obtener_diff(caso["base"], caso["cabeza"])
    if not diff.strip():
        raise CasoNoEvaluable("diff vacío tras aplicar exclusiones")
    destino = objetivos(caso)
    if caso["tipo"] == "positivo" and not destino:
        raise CasoNoEvaluable("positivo sin objetivo derivable tras aplicar exclusiones")
    return diff, destino


def _numeros_de_linea(linea, numero):
    return {numero, numero - 1} - {0} if linea.startswith("-") else {numero}


def _en_objetivo(numeros, rangos):
    return any(inicio <= n <= fin for n in numeros for inicio, fin, _ in rangos)


def _cita_directa_visible(diff, destino):
    """Indica si una línea citable del prefijo coincide con la referencia."""
    for ruta, linea, numero in mr._lineas_de_hunks(diff):
        rangos = destino.get(ruta)
        if rangos is None or not any(c.isalnum() for c in linea[1:]):
            continue
        if not rangos or _en_objetivo(_numeros_de_linea(linea, numero), rangos):
            return True
    return False


def lineas_citadas(diff, archivo, fragmento):
    """Números de línea (en la cabeza) de las líneas del diff que cita un fragmento.

    Una línea eliminada no existe en la cabeza: cuentan las dos que la rodean,
    como en `mr.lineas_cambiadas`. Quitar una guarda es un defecto que solo se
    puede citar por la línea borrada, y `mr.cita_en` acepta esa evidencia
    (Codex, email-triage-plugin#203).

    Una cita de varias líneas se localiza como tramo seguido de un mismo hunk,
    la misma regla que la acepta (`mr.cita_consecutiva`): emparejar cada línea
    por separado la situaba también donde solo se repite una de ellas, p. ej.
    un `return None` de la función objetivo (revisión Codex, mistral-review#3).
    """
    trozos = [t for t in str(fragmento).splitlines()
              if t.strip() and t.strip() not in ("+", "-")]
    if not any(any(c.isalnum() for c in t) for t in trozos):
        return set()

    # Hunks del archivo, sin líneas en blanco (tampoco cuentan en la cita).
    hunks, esperado = [], None
    for ruta, linea, numero in mr._lineas_de_hunks(diff):
        if ruta != archivo:
            continue
        if not hunks or numero != esperado:
            hunks.append([])
        esperado = numero if linea.startswith("-") else numero + 1
        if linea[1:].strip():
            hunks[-1].append((linea, numero))
    numeros = set()
    for hunk in hunks:
        for inicio in range(len(hunk) - len(trozos) + 1):
            tramo = hunk[inicio:inicio + len(trozos)]
            if all(mr._misma_linea(t, l) for t, (l, _) in zip(trozos, tramo)):
                for linea, numero in tramo:
                    numeros |= _numeros_de_linea(linea, numero)
    return numeros


def clasificar(hallazgo, diff, destino):
    """'funcion' si cita una línea dentro de un objetivo; 'archivo'; o 'fuera'."""
    rangos = destino.get(hallazgo["archivo"])
    if rangos is None:
        return "fuera"
    numeros = lineas_citadas(diff, hallazgo["archivo"], hallazgo["fragmento"])
    if _en_objetivo(numeros, rangos):
        return "funcion"
    return "archivo"


def _campos_filtrados(objeto, campos):
    if not isinstance(objeto, dict):
        return {}
    return {campo: mr.mask_secrets(objeto[campo]) for campo in campos
            if isinstance(objeto.get(campo), str)}


def _vista_rechazada(texto):
    """Extracto de los campos reconocidos para diagnosticar un rechazo."""
    try:
        datos = json.loads(texto)
    except (ValueError, RecursionError):
        return None
    if not isinstance(datos, dict):
        return None
    # El filtro se aplica a las cadenas decodificadas, antes de escapar comillas.
    vista = _campos_filtrados(datos, ("resumen",))
    for grupo, campos in (("hallazgos", CAMPOS_HALLAZGO),
                          ("comprobaciones", CAMPOS_PENDIENTE + ("estado",))):
        filas = datos.get(grupo)
        if isinstance(filas, list):
            vista[grupo] = [_campos_filtrados(fila, campos) for fila in filas]
    if vista:
        return json.dumps(vista)[:MAX_RECHAZADA]
    return None


def _cambios_en_diff(diff):
    """Rutas y número de líneas completas añadidas o eliminadas."""
    rutas = set()
    lineas = 0
    for ruta, linea, _ in mr._lineas_de_hunks(diff):
        if linea.startswith(("+", "-")):
            rutas.add(ruta)
            lineas += 1
    return rutas, lineas


def revisar(caso, modelo, clave, proveedor="mistral", ruta="", *, contexto_modo="estandar"):
    """Evalúa un caso y registra su resultado junto con los intentos de respuesta."""
    # Tachado antes de enviar, validar y clasificar, como en main(): una cita
    # de una línea con secreto llega al modelo tachada (revisión Codex,
    # mistral-review#3).
    diff, destino = _datos_caso(caso)
    rutas_textuales, lineas = _cambios_en_diff(diff)
    diff = mr.mask_secrets(diff)
    prefijo = diff[:mr.MAX_DIFF]
    titulo = git("log", "-1", "--format=%s", caso["cabeza"]).strip()
    cobertura_guia, cobertura_contexto = {}, {}
    guia = mr.leer_guia(cobertura=cobertura_guia)
    nombre_guia, texto_guia = guia
    if nombre_guia is not None:
        if os.path.isabs(nombre_guia):
            nombre_guia = os.path.basename(nombre_guia)
        nombre_guia = mr.mask_secrets(nombre_guia)
    cobertura_guia.update(
        nombre=nombre_guia,
        sha256=hashlib.sha256(mr.mask_secrets(texto_guia).encode("utf-8")).hexdigest()
        if texto_guia else None,
    )
    opciones_contexto = {"cobertura": cobertura_contexto}
    if contexto_modo == "auxiliar-local":
        opciones_contexto["auxiliares_locales"] = True
    contexto = mr.obtener_contexto(prefijo, caso["cabeza"], **opciones_contexto)
    fragmentos, _ = _fragmentos_contexto(caso)
    extra = ()
    if fragmentos:
        extra = (("Fragmentos de la versión revisada (solo lectura)", fragmentos),)
        cobertura_contexto["caracteres_fragmentos"] = len(fragmentos)
    mensajes, recortado = mr.construir_mensajes(titulo, None, diff, guia, contexto, extra=extra)
    rutas = mr.obtener_rutas_diff(caso["base"], caso["cabeza"])
    rutas_visibles, lineas_visibles = _cambios_en_diff(prefijo)
    cobertura_diff = {
        "rutas_disponibles": len(rutas),
        "rutas_sin_cambios_textuales": len(rutas - rutas_textuales),
        "rutas_con_cambios": len(rutas_textuales),
        "rutas_con_cambios_visibles": len(rutas_visibles),
        "lineas_de_cambio": lineas,
        "lineas_de_cambio_visibles": lineas_visibles,
        "recortado": recortado,
        "linea_final_incompleta": recortado and bool(prefijo) and not prefijo.endswith("\n"),
    }
    registro = {"estado": "invalida", "detalle": "", "solicitudes": 0,
                "recortado": recortado, "hallazgos": [], "pendientes": [],
                "rechazadas": [], "cortes_salida": 0,
                "cita_directa_visible": _cita_directa_visible(prefijo, destino),
                "cobertura": {"diff": cobertura_diff, "guia": cobertura_guia,
                              "contexto": cobertura_contexto}}
    comprobaciones = []
    try:
        for solicitud in range(1, mr.INTENTOS_RESPUESTA + 1):
            registro["solicitudes"] += 1
            try:
                texto = mr.llamar_mistral(mensajes, modelo, clave,
                                          proveedor=proveedor, ruta=ruta)
            except mr.RespuestaIncompleta as fallo:
                if fallo.causa == "length":
                    registro["cortes_salida"] += 1
                    if solicitud < mr.INTENTOS_RESPUESTA:
                        continue
                raise
            diagnostico = []
            resultado = mr.interpretar(texto, prefijo,
                exigir_comprobaciones=True, rutas_comprobaciones=rutas,
                diagnostico=diagnostico, comprobaciones_validadas=comprobaciones)
            registro["detalle"] = ", ".join(diagnostico)
            if resultado is not None:
                break
            rechazo = {"detalle": registro["detalle"], "caracteres": len(texto)}
            vista = _vista_rechazada(texto)
            if vista is not None:
                rechazo["texto"] = vista
            registro["rechazadas"].append(rechazo)
    except urllib.error.HTTPError as fallo:
        registro.update(estado="error", detalle="HTTP %d" % fallo.code)
        return registro
    except (urllib.error.URLError, TimeoutError):
        registro.update(estado="error", detalle="conexión")
        return registro
    except mr.RespuestaIncompleta as fallo:
        # Solo `length` es una salida cortada; `error_proveedor` o `error`
        # son fallos del proveedor y no se arreglan subiendo `--max-salida`.
        registro.update(estado="cortada" if fallo.causa == "length" else "error",
                        detalle=fallo.causa or "incompleta")
        return registro
    except ValueError:
        registro.update(estado="error", detalle="formato_respuesta")
        return registro
    if resultado is None:
        return registro
    registro["estado"] = "valida"
    for h in resultado[1]:
        donde = clasificar(h, prefijo, destino)
        registro["hallazgos"].append(dict(_campos_filtrados(h, CAMPOS_HALLAZGO), donde=donde))
    for c in comprobaciones:
        if c["estado"] == "pendiente":
            donde = "archivo" if c["archivo"] in destino else "fuera"
            registro["pendientes"].append(dict(_campos_filtrados(c, CAMPOS_PENDIENTE), donde=donde))
    return registro


def resumir(casos, resultados, modelo, repeticiones, proveedor="mistral", ruta=""):
    """Informe en Markdown. Las cifras son recuentos; el juicio final es humano."""
    if proveedor == "openrouter":
        # Proveedor y modelo a la vista: dos evaluaciones solo se comparan
        # si se sabe qué configuración midió cada una.
        etiqueta = "vía OpenRouter (`%s`%s" % (
            modelo, ", proveedor `%s`" % ruta if ruta else ", proveedor elegido por OpenRouter")
    else:
        etiqueta = "de Mistral (`%s`" % modelo
    partes = ["## Evaluación del revisor %s, %d repetición(es) por caso)"
              % (etiqueta, repeticiones), ""]
    ejecuciones = [r for c in casos for r in resultados[c["id"]]]
    validas = [r for r in ejecuciones if r["estado"] == "valida"]
    partes.append("- **Revisiones válidas:** %d de %d (%s)." % (
        len(validas), len(ejecuciones), ", ".join(
            "%s: %d" % (e, sum(r["estado"] == e for r in ejecuciones))
            for e in ("invalida", "cortada", "error")
            if any(r["estado"] == e for r in ejecuciones)) or "sin fallos finales"))
    cortes = sum(r.get("cortes_salida", 0) for r in ejecuciones)
    if cortes:
        partes.append("- **Respuestas cortadas por límite de salida:** %d." % cortes)
    positivos = [c for c in casos if c["tipo"] == "positivo" and not c.get("control")]
    controles = [c for c in casos if c.get("control")]
    negativos = [c for c in casos if c["tipo"] == "negativo"]
    con_cita = {c["id"]: [r for r in resultados[c["id"]]
                         if r["estado"] == "valida" and r.get("cita_directa_visible", True)]
                for c in casos}

    def senala(r):
        return any(h["donde"] == "funcion" for h in r["hallazgos"])

    for grupo, titulo in ((positivos, "Localización de positivos"),
                          (controles, "Localización del control (defecto sintético)")):
        if not grupo:
            continue
        por_caso = [con_cita[c["id"]] for c in grupo if con_cita[c["id"]]]
        if not por_caso:
            partes.append("- **%s** · Sin revisiones válidas con cita directa visible." % titulo)
            continue
        revisiones = [r for lista in por_caso for r in lista]
        partes.append("- **%s** · Algún hallazgo cita el objetivo en %d de %d casos y en "
                      "%d de %d revisiones válidas con cita directa visible."
                      % (titulo, sum(any(senala(r) for r in lista) for lista in por_caso),
                         len(por_caso), sum(senala(r) for r in revisiones), len(revisiones)))
    if negativos:
        ok = [r for c in negativos for r in resultados[c["id"]] if r["estado"] == "valida"]
        total = sum(len(r["hallazgos"]) for r in ok)
        repite = sum(h["donde"] != "fuera" for c in negativos for r in con_cita[c["id"]]
                     for h in r["hallazgos"])
        partes.append("- **Commits con hallazgos falsos conocidos** · %d hallazgo(s) en %d "
                      "revisiones válidas, con %d en los mismos archivos que entonces."
                      % (total, len(ok), repite))
    partes += ["", "Para etiquetar un hallazgo, contrasta su escenario con el código.",
               "", "| Caso | Tipo | Válidas | Cita el objetivo | Hallazgos | Pendientes |",
               "|---|---|---:|---:|---:|---:|"]
    for c in casos:
        rs = resultados[c["id"]]
        ok = [r for r in rs if r["estado"] == "valida"]
        localizables = con_cita[c["id"]]
        partes.append("| `%s` | %s | %d/%d | %s | %d | %d |" % (
            c["id"], "control" if c.get("control") else c["tipo"], len(ok), len(rs),
            "%d/%d" % (sum(senala(r) for r in localizables), len(localizables))
            if c["tipo"] == "positivo" and localizables else "—",
            sum(len(r["hallazgos"]) for r in ok), sum(len(r["pendientes"]) for r in ok)))
    partes += ["", "### Hallazgos para etiquetar"]
    for c in casos:
        partes += ["", "**`%s`** (%s) — %s" % (c["id"], c["tipo"], mr._linea(c["defecto"], 300))]
        for i, r in enumerate(resultados[c["id"]], 1):
            if not r.get("cita_directa_visible", True):
                partes.append("- Repetición %d: sin cita directa visible." % i)
            if r["estado"] != "valida":
                partes.append("- Repetición %d: %s%s." % (
                    i, r["estado"], " (%s)" % r["detalle"] if r["detalle"] else ""))
                continue
            if not r["hallazgos"] and not r["pendientes"]:
                partes.append("- Repetición %d: sin hallazgos ni pendientes." % i)
            for numero, h in enumerate(r["hallazgos"], 1):
                partes.append("- Repetición %d, hallazgo %d (%s) [%s] en %s: %s" % (
                    i, numero, h["severidad"], h["donde"], mr._codigo(h["archivo"], 120),
                    mr._linea(h["problema"], 400)))
                partes.append("  - Escenario: %s" % mr._linea(h["escenario"], 400))
            for p in r["pendientes"]:
                partes.append("- Repetición %d, pendiente [%s] en %s: %s" % (
                    i, p["donde"], mr._codigo(p["archivo"], 120),
                    mr._linea(p["escenario"], 300)))
    return "\n".join(partes) + "\n"


def _tramos_en_cabeza(caso, campo):
    """Valida rangos y lee sus líneas del commit, nunca del árbol de trabajo."""
    archivos = {}
    son_fragmentos = campo == "fragmentos_contexto"
    for tramo in caso.get(campo, ()):
        archivo, desde, hasta = tramo["archivo"], tramo["desde"], tramo["hasta"]
        ruta_informe = mr.mask_secrets(mr._linea(archivo, limite=None))
        if archivo not in archivos:
            if son_fragmentos and (not archivo.endswith((".py", ".sh")) or mr.excluido(archivo)):
                raise CasoNoEvaluable("fragmentos_contexto: archivo no permitido: %s" % ruta_informe)
            objeto = "%s:%s" % (caso["cabeza"], archivo)
            try:
                contenido = subprocess.run(("git", "cat-file", "blob", objeto),
                                           check=True, capture_output=True).stdout
            except subprocess.CalledProcessError:
                raise CasoNoEvaluable("%s: archivo no disponible en cabeza: %s"
                                     % (campo, ruta_informe)) from None
            texto = contenido.decode("utf-8", "replace")
            if son_fragmentos:
                if "\0" in texto:
                    raise CasoNoEvaluable("fragmentos_contexto: archivo no textual: %s" % ruta_informe)
                # Tachar antes de escoger el rango conserva la detección de PEM
                # aunque su cabecera quede fuera de las líneas solicitadas.
                texto = mr.mask_secrets(texto)
            # Git numera por LF y también cuenta una última línea sin LF.
            archivos[archivo] = texto.split("\n")
            if archivos[archivo][-1] == "":
                archivos[archivo].pop()
        lineas = archivos[archivo]
        if hasta > len(lineas):
            raise CasoNoEvaluable("%s: rango %s:%d-%d fuera de las %d líneas de cabeza"
                                 % (campo, ruta_informe, desde, hasta, len(lineas)))
        yield archivo, desde, hasta, lineas[desde - 1:hasta]


def _fragmentos_contexto(caso):
    bloques, visibles = [], {}
    usados = 0
    for archivo, desde, hasta, lineas in _tramos_en_cabeza(caso, "fragmentos_contexto"):
        bloque = "%s:%d-%d\n%s" % (mr.mask_secrets(archivo), desde, hasta, "\n".join(lineas))
        usados += len(bloque) + (2 if bloques else 0)
        if usados > mr.MAX_AUXILIAR:
            raise CasoNoEvaluable("fragmentos_contexto: supera los %d caracteres tras el tachado"
                                 % mr.MAX_AUXILIAR)
        bloques.append(bloque)
        visibles.setdefault(archivo, set()).update(range(desde, hasta + 1))
    return "\n\n".join(bloques), visibles


def _evidencia_minima_visible(caso, prefijo, contexto_modo, visibles):
    for ruta, linea, numero in mr._lineas_de_hunks(prefijo):
        if linea[0] in "+ ":
            visibles.setdefault(ruta, set()).add(numero)
    mr.obtener_contexto(prefijo, caso["cabeza"], lineas_visibles=visibles,
                        auxiliares_locales=contexto_modo == "auxiliar-local")
    for tramo in caso["evidencia_minima"]:
        archivo, desde, hasta = tramo["archivo"], tramo["desde"], tramo["hasta"]
        total = hasta - desde + 1
        presentes = sum(desde <= numero <= hasta for numero in visibles.get(archivo, ()))
        estado = "completa" if presentes == total else "parcial" if presentes else "ausente"
        yield "    %s:%d-%d %s (%d/%d)" % (
            mr.mask_secrets(mr._linea(archivo, limite=None)), desde, hasta, estado, presentes, total)


def listar(casos, *, contexto_modo=None):
    """Comprueba los casos y prepara su listado sin llamar a la API."""
    lineas, errores = [], []
    for c in casos:
        try:
            diff, destino = _datos_caso(c)
            for _ in _tramos_en_cabeza(c, "evidencia_minima"):
                pass
            fragmentos, visibles = _fragmentos_contexto(c)
        except CasoNoEvaluable as fallo:
            errores.append("::error::Caso %s: %s" % (mr._linea(c["id"]), fallo))
            continue
        lineas.append("%s [%s] diff=%d caracteres" % (c["id"], c["tipo"], len(diff)))
        for archivo, rangos in sorted(destino.items()):
            lineas.append("  %s%s" % (archivo, "".join(
                "\n    %d-%d %s" % r for r in rangos)))
        prefijo = mr.mask_secrets(diff)[:mr.MAX_DIFF]
        if not _cita_directa_visible(prefijo, destino):
            lineas.append("  sin cita directa visible en el diff preparado")
        if contexto_modo is not None and fragmentos:
            lineas.append("  fragmentos de contexto: %d caracteres tras el tachado" % len(fragmentos))
        if contexto_modo is not None and "evidencia_minima" in c:
            lineas.append("  evidencia mínima · %s" % contexto_modo)
            lineas.extend(_evidencia_minima_visible(c, prefijo, contexto_modo, visibles))
    if errores:
        raise CasoNoEvaluable("\n".join(errores))
    return "\n".join(lineas)


def _comprobar_casos(casos, mostrar_listado=False, *, contexto_modo="estandar"):
    try:
        listado = listar(casos, contexto_modo=contexto_modo if mostrar_listado else None)
    except CasoNoEvaluable as fallo:
        print(fallo)
        return False
    if mostrar_listado:
        print(listado)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--casos", default=CASOS)
    ap.add_argument("--solo", default="", help="ids separados por comas")
    ap.add_argument("--repeticiones", type=int, default=2)
    ap.add_argument("--salida", help="ruta del JSON con cada revisión")
    ap.add_argument("--ejecutar", action="store_true", help="llamar a la API del proveedor (Mistral u OpenRouter)")
    ap.add_argument("--modelo", default="",
                    help="modelo solo para esta evaluación (vacío = MISTRAL_MODEL)")
    ap.add_argument("--esfuerzo", default="",
                    help="reasoning_effort solo para esta evaluación: low, medium o high "
                         "(vacío = el del revisor)")
    ap.add_argument("--max-salida", default="",
                    help="max_tokens solo para esta evaluación, de %d a %d "
                         "(vacío = el del revisor)" % (MAX_SALIDA_MINIMA, MAX_SALIDA_MAXIMA))
    ap.add_argument("--contexto", choices=("estandar", "auxiliar-local"), default="estandar",
                    help="modo de contexto solo para esta evaluación (por defecto: estandar)")
    args = ap.parse_args(argv)
    casos = cargar_casos(args.casos)
    if args.solo.strip():
        pedidos = {x.strip() for x in args.solo.split(",") if x.strip()}
        desconocidos = pedidos - {c["id"] for c in casos}
        if desconocidos:
            print("::error::Casos desconocidos: %s" % ", ".join(sorted(desconocidos)))
            return 2
        casos = [c for c in casos if c["id"] in pedidos]
    if not args.ejecutar:
        return 0 if _comprobar_casos(casos, mostrar_listado=True, contexto_modo=args.contexto) else 2
    # Mínimo seguro: una petición por revisión. Los reintentos se descuentan
    # del mismo tope en el transporte y pueden agotar la evaluación antes.
    if args.repeticiones < 1 or len(casos) * args.repeticiones > MAX_LLAMADAS:
        print("::error::Hasta %d peticiones por ejecución: reduce casos o repeticiones."
              % MAX_LLAMADAS)
        return 2
    try:
        # Mismo proveedor que el revisor (MISTRAL_PROVEEDOR); el modelo de
        # `--modelo` manda sobre MISTRAL_MODEL.
        proveedor, modelo, ruta = mr.configuracion(args.modelo.strip())
    except mr.ConfiguracionInvalida as fallo:
        print("::error::Configuración no válida: %s." % fallo)
        return 2
    secreto = mr.PROVEEDORES[proveedor]["secreto"]
    clave = os.environ.get(secreto, "")
    if not clave:
        print("::error::Falta %s: la evaluación no se ha ejecutado." % secreto)
        return 2
    # El id viaja en el cuerpo JSON y en el informe: solo su alfabeto. El de
    # OpenRouter ya lo valida `configuracion` (lleva `/`).
    if proveedor == "mistral" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}", modelo):
        print("::error::Modelo no válido: solo letras, números, punto, guion y dos puntos.")
        return 2
    # Con OpenRouter, sin `--esfuerzo` no se envía (ver mr.ESFUERZO_OPENROUTER).
    esfuerzo = args.esfuerzo.strip() or (mr.ESFUERZO if proveedor == "mistral" else "")
    if esfuerzo and esfuerzo not in ESFUERZOS:
        print("::error::Esfuerzo no válido: usa %s." % ", ".join(ESFUERZOS))
        return 2
    texto_salida = args.max_salida.strip()
    if not texto_salida:
        max_salida = mr.MAX_SALIDA
    # Solo 0-9: `isdigit()` acepta «²», que `int()` no convierte (Mistral, email-triage-plugin#208).
    elif re.fullmatch(r"[0-9]{1,7}", texto_salida) and (
            MAX_SALIDA_MINIMA <= int(texto_salida) <= MAX_SALIDA_MAXIMA):
        max_salida = int(texto_salida)
    else:
        print("::error::max_salida no válido: un entero de %d a %d."
              % (MAX_SALIDA_MINIMA, MAX_SALIDA_MAXIMA))
        return 2
    if not _comprobar_casos(casos):
        return 2
    resultados = {c["id"]: [] for c in casos}
    agotado = cancelada = False
    procedencia = _procedencia()
    exclusiones = [
        mr.mask_secrets(os.path.relpath(patron) if os.path.isabs(patron) else patron)
        for patron in mr.excluidos()
    ]
    opciones_revision = {}
    if args.contexto != "estandar":
        opciones_revision["contexto_modo"] = args.contexto

    def datos(en_curso):
        return {"proveedor": proveedor, "ruta": ruta,
                "modelo": modelo, "repeticiones": args.repeticiones,
                "esfuerzo": esfuerzo, "max_salida": max_salida, "contexto_modo": args.contexto,
                "en_curso": en_curso,
                "peticiones": presupuesto.usadas, "incompleta": agotado or cancelada,
                "cancelada": cancelada, "procedencia": procedencia,
                "exclusiones": exclusiones,
                "tokens": {"entrada": presupuesto.entrada,
                           "salida": presupuesto.salida,
                           "respuestas": presupuesto.respondidas,
                           "sin_esfuerzo": presupuesto.sin_esfuerzo,
                           "coste": presupuesto.coste if presupuesto.con_coste else None,
                           "con_coste": presupuesto.con_coste},
                "servido_por": presupuesto.servido_por,
                "modelo_servido": presupuesto.modelo_servido,
                "casos": casos, "resultados": resultados}

    # Solo cambia en este proceso: el revisor de los PR sigue con el suyo.
    with con_esfuerzo(esfuerzo or mr.ESFUERZO), \
            con_esfuerzo(esfuerzo if proveedor == "openrouter" else "", "ESFUERZO_OPENROUTER"), \
            con_max_salida(max_salida), \
            Presupuesto(MAX_LLAMADAS, espera_para(max_salida), bool(esfuerzo)) as presupuesto:
        try:
            for c in casos:
                for n in range(args.repeticiones):
                    antes = presupuesto.usadas
                    inicio = monotonic_ns()
                    registro = revisar(c, modelo, clave, proveedor=proveedor, ruta=ruta,
                                       **opciones_revision)
                    registro["duracion_ms"] = (monotonic_ns() - inicio) // 1_000_000
                    registro["solicitudes"] = presupuesto.usadas - antes
                    resultados[c["id"]].append(registro)
                    # flush: en el log de Actions se ve el avance caso a caso.
                    print("%s repetición %d: %s, %d hallazgo(s), %d petición(es)" % (
                        c["id"], n + 1, registro["estado"],
                        len(registro["hallazgos"]), registro["solicitudes"]),
                        flush=True)
                    if args.salida:
                        guardar(args.salida, datos(True))
        except PresupuestoAgotado:
            agotado = True
        except KeyboardInterrupt:
            # GitHub cancela con SIGINT: se cierra con lo ya revisado.
            cancelada = True
    informe = resumir(casos, resultados, modelo, args.repeticiones, proveedor, ruta)
    informe += "\nPeticiones HTTP a la API: %d de %d permitidas.\n" % (
        presupuesto.usadas, MAX_LLAMADAS)
    informe += ("\nTokens según la API: %d de entrada y %d de salida, en %d respuesta(s) "
                "con recuento. Esfuerzo de razonamiento pedido: `%s`. Tope de salida: "
                "%d tokens por respuesta, con %d s de espera.\n" % (
                    presupuesto.entrada, presupuesto.salida, presupuesto.respondidas,
                    esfuerzo or "ninguno, el del modelo", max_salida, presupuesto.espera))
    if presupuesto.respondidas:
        informe += ("\nServida por (según la API): %s. Modelo servido: %s.\n" % (
            _recuentos(presupuesto.servido_por), _recuentos(presupuesto.modelo_servido)))
    if presupuesto.con_coste:
        informe += ("\nCoste según la API: %.6f créditos en %d de %d respuesta(s) con "
                    "recuento.\n" % (presupuesto.coste, presupuesto.con_coste,
                                     presupuesto.respondidas))
    else:
        informe += "\nCoste: la API no lo informa; no se estima.\n"
    rechazadas = sum(len(r.get("rechazadas", ())) for rs in resultados.values() for r in rs)
    if rechazadas:
        informe += ("\n%d respuesta(s) rechazadas por el formato. Los motivos y las vistas "
                    "disponibles están en el artefacto `mistral-eval` (campo `rechazadas`).\n"
                    % rechazadas)
    if presupuesto.sin_esfuerzo:
        informe += ("\n**El modelo no admite el esfuerzo de razonamiento:** %d "
                    "respuesta(s) llegaron sin él; en ellas no se aplicó. Tras el "
                    "primer rechazo se dejó de pedir.\n"
                    % presupuesto.sin_esfuerzo)
    if agotado:
        informe += ("\n**Evaluación incompleta:** se alcanzó el tope de peticiones; "
                    "faltan revisiones. Las cifras de arriba son parciales.\n")
    if cancelada:
        informe += ("\n**Evaluación cancelada:** se interrumpió a mitad; faltan "
                    "revisiones. Las cifras de arriba son parciales.\n")
    if args.salida:
        guardar(args.salida, datos(False))   # antes del informe: lo más valioso
    mr.escribir_resumen(informe)
    print(informe, flush=True)
    return 1 if agotado or cancelada else 0


if __name__ == "__main__":
    sys.exit(main())
