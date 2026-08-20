"""Genera un reporte de diagnostico completo para analizar el sistema.

Uso:

    python -m tools.diagnostico

Escribe el reporte en `data/diagnostico/` y muestra la ruta al terminar. Ese
archivo es autosuficiente: contiene entorno, configuracion efectiva, estadisticas
de la base, analisis de estabilidad del seguimiento, uso de disco por evidencias
y las fallas recientes.

No incluye contrasenas ni imagenes: es texto plano y se puede compartir.
"""
from __future__ import annotations

import json
import os
import platform
import sqlite3
import sys
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from core import paths  # noqa: E402
from core.config import load_config  # noqa: E402

CLAVES_SECRETAS = {"password", "username", "danger_mp3_path"}
ANCHO = 78


class Reporte:
    def __init__(self):
        self.lineas: list[str] = []
        self.banderas: list[str] = []

    def titulo(self, texto: str) -> None:
        self.lineas += ["", "=" * ANCHO, texto.upper(), "=" * ANCHO]

    def seccion(self, texto: str) -> None:
        self.lineas += ["", texto, "-" * len(texto)]

    def linea(self, texto: str = "") -> None:
        self.lineas.append(texto)

    def dato(self, etiqueta: str, valor) -> None:
        self.lineas.append(f"  {etiqueta:<38} {valor}")

    def bandera(self, texto: str) -> None:
        """Marca un hallazgo que merece atencion."""
        self.banderas.append(texto)

    def __str__(self) -> str:
        return "\n".join(self.lineas) + "\n"


def _entorno(r: Reporte) -> None:
    r.titulo("entorno")
    r.dato("Fecha del reporte", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    r.dato("Sistema", f"{platform.system()} {platform.release()}")
    r.dato("Python", sys.version.split()[0])
    r.dato("Procesadores logicos", os.cpu_count())
    r.dato("Carpeta del proyecto", RAIZ)

    r.seccion("Bibliotecas")
    for modulo in ("numpy", "cv2", "PIL", "torch", "ultralytics", "customtkinter"):
        try:
            importado = __import__(modulo)
            version = getattr(importado, "__version__", "sin version")
        except Exception as error:
            version = f"NO DISPONIBLE ({type(error).__name__})"
        r.dato(modulo, version)

    r.seccion("Aceleracion")
    try:
        import torch

        disponible = torch.cuda.is_available()
        r.dato("CUDA disponible", disponible)
        if disponible:
            r.dato("GPU", torch.cuda.get_device_name(0))
        else:
            r.bandera(
                "Sin GPU: la inferencia corre en CPU. Es el techo principal de FPS."
            )
    except Exception as error:
        r.dato("CUDA disponible", f"no se pudo comprobar ({type(error).__name__})")


def _configuracion(r: Reporte) -> None:
    r.titulo("configuracion efectiva")
    try:
        config = load_config()
    except Exception as error:
        r.linea(f"  No se pudo leer la configuracion: {error}")
        return

    for clave in sorted(config):
        if clave in CLAVES_SECRETAS:
            r.dato(clave, "(oculto)")
            continue
        r.dato(clave, config[clave])

    confianza = float(config.get("confidence", 0))
    tamano = int(config.get("image_size", 0))
    fps = int(config.get("target_fps", 0))
    r.seccion("Lectura de los ajustes clave")
    r.dato("Resolucion de inferencia", f"{tamano} px")
    r.dato("Confianza", f"{confianza:.0%}")
    r.dato("NMS (iou)", config.get("iou"))
    r.dato("FPS objetivo", fps)
    if tamano >= 960 and not _hay_gpu():
        r.bandera(
            f"Resolucion {tamano} px en CPU: cuesta "
            f"{(tamano / 640) ** 2:.2f} veces mas que 640 px."
        )

    r.seccion("Configuracion del rastreador")
    tracker = RAIZ / "config" / "bytetrack_arzyz.yaml"
    umbrales = {}
    if tracker.is_file():
        for renglon in tracker.read_text(encoding="utf-8").splitlines():
            if ":" in renglon and not renglon.strip().startswith("#"):
                clave, _, valor = renglon.partition(":")
                umbrales[clave.strip()] = valor.strip()
                r.dato(clave.strip(), valor.strip())
    else:
        r.linea("  No se encontro el archivo del rastreador.")

    # Incoherencia clasica: si los umbrales del rastreador quedan por debajo del
    # filtro de confianza, nunca actuan y cada caja suelta crea identidad nueva.
    try:
        nuevo = float(umbrales.get("new_track_thresh", 0))
        if nuevo and nuevo <= confianza:
            r.bandera(
                f"new_track_thresh ({nuevo}) <= confianza ({confianza}): el "
                "rastreador crea una identidad nueva con cualquier deteccion."
            )
    except ValueError:
        pass


def _hay_gpu() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _conectar():
    if not paths.DB_PATH.is_file():
        return None
    # Se intenta en solo lectura para no interferir con el detector en marcha.
    # Una base en modo WAL puede rechazarlo si necesita recuperar el diario;
    # en ese caso se abre normal, que es igual de seguro para leer.
    try:
        conexion = sqlite3.connect(f"file:{paths.DB_PATH}?mode=ro", uri=True)
        conexion.execute("SELECT 1 FROM sqlite_master LIMIT 1")
    except sqlite3.Error:
        conexion = sqlite3.connect(str(paths.DB_PATH))
    conexion.row_factory = sqlite3.Row
    return conexion


def _base_de_datos(r: Reporte, datos: dict) -> None:
    r.titulo("base de datos")
    conexion = _conectar()
    if conexion is None:
        r.linea(f"  No existe la base en {paths.DB_PATH}")
        return

    # closing() es obligatorio: en Python 3.14 una conexion sin close() retiene
    # el archivo hasta que pasa el recolector, y `with conexion` no la cierra.
    with closing(conexion), conexion:
        tamano_mb = paths.DB_PATH.stat().st_size / 1_048_576
        r.dato("Archivo", paths.DB_PATH)
        r.dato("Tamano", f"{tamano_mb:.2f} MB")

        for tabla in ("detections", "crossings", "zone_alerts"):
            try:
                total = conexion.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
                r.dato(f"Registros en {tabla}", total)
                datos[f"total_{tabla}"] = int(total)
            except sqlite3.Error as error:
                r.dato(f"Registros en {tabla}", f"error: {error}")

        r.seccion("Eventos por dia (ultimos 10 dias con actividad)")
        for fila in conexion.execute(
            "SELECT substr(detected_at,1,10) AS dia, COUNT(*) AS eventos, "
            "SUM(total) AS detecciones FROM detections "
            "GROUP BY dia ORDER BY dia DESC LIMIT 10"
        ):
            r.dato(
                fila["dia"],
                f"{fila['eventos']:>5} eventos  "
                f"{fila['detecciones']:>6} detecciones acumuladas",
            )

        r.seccion("Detecciones por evento (revela cajas duplicadas)")
        histograma = Counter(
            int(fila[0])
            for fila in conexion.execute("SELECT total FROM detections")
        )
        for cuantas in sorted(histograma):
            barra = "#" * min(int(histograma[cuantas] / max(1, max(histograma.values()) / 40)), 40)
            r.dato(f"{cuantas} detecciones", f"{histograma[cuantas]:>6}  {barra}")
        datos["histograma_detecciones"] = dict(histograma)
        if histograma:
            promedio = sum(k * v for k, v in histograma.items()) / sum(histograma.values())
            r.dato("Promedio por evento", f"{promedio:.2f}")
            datos["promedio_detecciones"] = round(promedio, 2)

        _por_fuente(r, conexion, datos)
        _estabilidad_de_seguimiento(r, conexion, datos)

        r.seccion("Clases detectadas (ultimos 1000 eventos)")
        clases = Counter()
        for (crudo,) in conexion.execute(
            "SELECT classes_json FROM detections ORDER BY id DESC LIMIT 1000"
        ):
            try:
                for nombre, cuenta in json.loads(crudo).items():
                    clases[nombre] += int(cuenta)
            except (TypeError, ValueError):
                continue
        for nombre, cuenta in clases.most_common(12):
            r.dato(nombre, cuenta)
        datos["clases"] = dict(clases.most_common(12))


def _almacenamiento(r: Reporte, datos: dict) -> None:
    """Espacio, ritmo de crecimiento y carpetas sincronizadas.

    Una carpeta sincronizada (OneDrive, Dropbox, Google Drive) es una causa
    real de congelamiento: el servicio bloquea archivos mientras los sube, y el
    latido del modulo se escribe cada segundo en esa misma carpeta.
    """
    import shutil

    r.titulo("almacenamiento")
    ruta = str(RAIZ)
    r.dato("Carpeta del proyecto", ruta)

    servicios = [n for n in ("OneDrive", "Dropbox", "Google Drive", "iCloud",
                             "Nextcloud", "Sync") if n.lower() in ruta.lower()]
    if servicios:
        r.dato("Servicio de sincronizacion", ", ".join(servicios))
        r.bandera(
            f"El proyecto vive dentro de {servicios[0]}. Ese servicio bloquea "
            "archivos mientras los sincroniza y puede congelar el modulo: el "
            "latido se escribe cada segundo en esa misma carpeta. Mueve el "
            "proyecto a una ruta local (por ejemplo C:\\Arzyz\\Vision)."
        )
    else:
        r.dato("Servicio de sincronizacion", "ninguno detectado")

    try:
        uso = shutil.disk_usage(RAIZ)
        libre_gb = uso.free / 1_073_741_824
        r.dato("Espacio libre en disco", f"{libre_gb:.1f} GB")
        if libre_gb < 10:
            r.bandera(f"Solo quedan {libre_gb:.1f} GB libres en disco.")
    except Exception:
        libre_gb = None

    # Proyeccion de crecimiento con el ritmo real medido
    evidencias = datos.get("evidencias", 0)
    megas = datos.get("evidencias_mb", 0.0)
    if evidencias and megas:
        kb = megas * 1024 / evidencias
        r.dato("Tamano medio por evidencia", f"{kb:.0f} KB")
        for etiqueta, piezas in (("por hora a 950 piezas/h", 950),
                                 ("por turno de 8 h", 950 * 8)):
            r.dato(f"Crecimiento estimado {etiqueta}",
                   f"{piezas * kb / 1024:,.0f} MB")
        por_turno_gb = 950 * 8 * kb / 1_048_576
        if por_turno_gb > 2:
            r.bandera(
                f"Las evidencias creceran ~{por_turno_gb:.1f} GB por turno "
                f"({kb:.0f} KB cada una). Reduce el tamano guardado o limita "
                "la retencion."
            )
        if libre_gb and por_turno_gb > 0:
            r.dato("Turnos antes de llenar el disco",
                   f"{libre_gb / por_turno_gb:.0f}")


def _salud_de_modulos(r: Reporte, datos: dict) -> None:
    """Latidos y reinicios: distingue un cierre del operador de una caida."""
    r.titulo("salud de los modulos")
    try:
        from core import heartbeat
    except Exception as error:
        r.linea(f"  No se pudo leer el latido: {error}")
        return

    carpeta = heartbeat.HEARTBEAT_DIR
    if not carpeta.is_dir():
        r.linea("  Ningun modulo ha latido todavia.")
    else:
        r.seccion("Ultimo latido por modulo")
        for archivo in sorted(carpeta.glob("*.json")):
            modulo = archivo.stem
            edad = heartbeat.age(modulo)
            estado = "sin dato" if edad is None else f"hace {edad:.0f} s"
            r.dato(modulo, estado)
            if edad is not None and edad > 60:
                r.bandera(f"{modulo} no late desde hace {edad:.0f} s.")

    # Reinicios y congelamientos, leidos del registro de fallas
    try:
        from core import failures

        bloques = failures.leer_recientes(300)
    except Exception:
        bloques = []
    reinicios: dict[str, int] = {}
    congelados: dict[str, int] = {}
    for bloque in bloques:
        primera = bloque.splitlines()[0] if bloque else ""
        if "| supervisor |" not in primera:
            continue
        for palabra in primera.split():
            pass
        texto = primera.split("| supervisor |")[-1].strip()
        nombre = texto.split()[0] if texto else "?"
        if "reiniciado" in texto:
            reinicios[nombre] = reinicios.get(nombre, 0) + 1
        if "congelado" in texto:
            congelados[nombre] = congelados.get(nombre, 0) + 1

    r.seccion("Intervenciones del supervisor")
    if not reinicios and not congelados:
        r.linea("  Ninguna. Los modulos no han necesitado reinicio.")
    else:
        for modulo in sorted(set(reinicios) | set(congelados)):
            r.dato(modulo,
                   f"{congelados.get(modulo, 0)} congelamiento(s), "
                   f"{reinicios.get(modulo, 0)} reinicio(s)")
        datos["reinicios"] = reinicios
        datos["congelamientos"] = congelados
        total = sum(congelados.values())
        if total:
            r.bandera(
                f"El supervisor tuvo que reiniciar modulos {total} vez/veces "
                "por congelamiento. Revisa almacenamiento y carga de CPU."
            )


def _por_fuente(r: Reporte, conexion, datos: dict) -> None:
    """Separa las estadisticas por camara y por modelo.

    Sin esta separacion, los eventos del detector de personas y los del modulo
    de objetos se mezclan y la rotacion de identidad se atribuye a quien no
    corresponde.
    """
    r.titulo("actividad por fuente y modelo")
    try:
        filas = list(conexion.execute(
            "SELECT source, model_name, COUNT(*) AS eventos, "
            "SUM(total) AS detecciones, MIN(detected_at) AS desde, "
            "MAX(detected_at) AS hasta "
            "FROM detections GROUP BY source, model_name ORDER BY eventos DESC"
        ))
    except sqlite3.Error as error:
        r.linea(f"  No se pudo consultar: {error}")
        return
    if not filas:
        r.linea("  Sin actividad registrada.")
        return
    for fila in filas:
        r.linea()
        r.dato("Fuente", fila["source"])
        r.dato("  Modelo", fila["model_name"])
        r.dato("  Eventos", fila["eventos"])
        r.dato("  Detecciones acumuladas", fila["detecciones"])
        r.dato("  Periodo", f"{fila['desde']} -> {fila['hasta']}")
    datos["fuentes"] = [dict(f) for f in filas]


def _estabilidad_de_seguimiento(r: Reporte, conexion, datos: dict) -> None:
    """El analisis mas importante: cuanto dura cada identidad de objeto.

    Si el rastreador pierde a una persona y le asigna un identificador nuevo,
    el conteo de objetos se infla aunque la escena no cambie. Se mide con la
    vida de cada identidad: cuantos eventos y cuantos segundos sobrevive.
    """
    r.seccion("Estabilidad del seguimiento (analisis clave)")
    apariciones: dict[str, list[str]] = defaultdict(list)
    sin_identidades = 0
    total_filas = 0

    # Solo se analizan fuentes que guardan identidades. El modulo de objetos
    # cuenta por cruce de linea y no las registra: incluirlo aqui atribuiria al
    # detector de personas una rotacion que no le corresponde.
    fuentes = set()
    for fila in conexion.execute(
        "SELECT detected_at, track_ids, source FROM detections "
        "WHERE track_ids IS NOT NULL AND track_ids NOT IN ('', '[]') ORDER BY id"
    ):
        fuentes.add(fila["source"])
        total_filas += 1
        try:
            marcas = json.loads(fila["track_ids"])
        except (TypeError, ValueError):
            continue
        if not marcas:
            sin_identidades += 1
            continue
        for marca in marcas:
            apariciones[str(marca)].append(fila["detected_at"])

    if not apariciones:
        r.linea("  Sin identidades guardadas todavia.")
        r.linea("  El modulo de objetos cuenta por cruce de linea y no las usa;")
        r.linea("  este analisis aplica al detector de personas.")
        return
    r.dato("Fuentes analizadas", ", ".join(sorted(fuentes)) or "-")

    vidas = [len(momentos) for momentos in apariciones.values()]
    efimeras = sum(1 for v in vidas if v == 1)
    porcentaje = efimeras * 100 / len(vidas)

    # Objetos presentes a la vez: la mediana de identidades por evento es el
    # mejor estimador de "cuantas personas hay realmente en la toma".
    por_evento = sorted(
        len(json.loads(fila["track_ids"]))
        for fila in conexion.execute(
            "SELECT track_ids FROM detections "
            "WHERE track_ids IS NOT NULL AND track_ids != ''"
        )
        if fila["track_ids"] not in (None, "", "[]")
    )
    concurrentes = por_evento[len(por_evento) // 2] if por_evento else 0
    rotacion = len(apariciones) / concurrentes if concurrentes else 0

    r.dato("Identidades distintas registradas", len(apariciones))
    r.dato("Eventos analizados", total_filas)
    r.dato("Objetos simultaneos (mediana)", concurrentes)
    r.dato("Rotacion de identidad", f"{rotacion:.1f}x")
    r.dato("Apariciones por identidad (promedio)", f"{sum(vidas)/len(vidas):.2f}")
    r.dato("Identidades de un solo evento", f"{efimeras} ({porcentaje:.0f}%)")

    datos["identidades"] = len(apariciones)
    datos["objetos_concurrentes"] = concurrentes
    datos["rotacion_identidad"] = round(rotacion, 1)
    datos["identidades_efimeras_pct"] = round(porcentaje, 1)

    # Con la escena estable, la rotacion deberia acercarse a 1: cada objeto
    # conserva su identidad. Valores altos significan que el rastreador pierde
    # a la misma persona y la vuelve a dar de alta como si fuera nueva.
    if rotacion >= 3:
        r.bandera(
            f"Rotacion de identidad {rotacion:.1f}x en {', '.join(sorted(fuentes))}: "
            f"hay {concurrentes} objetos simultaneos pero se registraron "
            f"{len(apariciones)} identidades. "
            "Cada objeto real cambio de identificador varias veces; por eso el "
            "conteo de objetos se infla."
        )
    if porcentaje >= 50:
        r.bandera(
            f"{porcentaje:.0f}% de las identidades duran un solo evento: el "
            "rastreador esta perdiendo objetos y reasignando identificadores."
        )

    r.linea()
    r.linea("  Identidades mas persistentes:")
    for marca, momentos in sorted(
        apariciones.items(), key=lambda par: len(par[1]), reverse=True
    )[:10]:
        r.dato(f"    {marca}", f"{len(momentos):>4} eventos  ({momentos[0]} -> {momentos[-1]})")

    r.linea()
    r.linea("  Identidades por sesion de arranque:")
    por_sesion = Counter(marca.split(":")[0] for marca in apariciones)
    for sesion, cuantas in por_sesion.most_common(10):
        r.dato(f"    sesion {sesion}", f"{cuantas} identidades")


def _evidencias(r: Reporte, datos: dict) -> None:
    r.titulo("evidencias en disco")
    raiz = paths.EVIDENCE_DIR
    if not raiz.is_dir():
        r.linea(f"  No existe {raiz}")
        return
    archivos = list(raiz.rglob("*.jpg")) + list(raiz.rglob("*.jpeg"))
    total_bytes = sum(a.stat().st_size for a in archivos if a.is_file())
    r.dato("Carpeta", raiz)
    r.dato("Imagenes guardadas", len(archivos))
    r.dato("Espacio ocupado", f"{total_bytes / 1_048_576:.1f} MB")
    datos["evidencias"] = len(archivos)
    datos["evidencias_mb"] = round(total_bytes / 1_048_576, 1)

    por_dia = Counter(a.parent.parent.name for a in archivos if a.is_file())
    if por_dia:
        r.seccion("Imagenes por dia")
        for dia, cuantas in sorted(por_dia.items(), reverse=True)[:10]:
            r.dato(dia, cuantas)
        pico = max(por_dia.values())
        if pico > 500:
            r.bandera(
                f"{pico} imagenes en un solo dia: revisa evidence_dedup y "
                "evidence_refresh_seconds."
            )


def _fallas(r: Reporte) -> None:
    r.titulo("fallas recientes")
    try:
        from core import failures

        bloques = failures.leer_recientes(25)
    except Exception as error:
        r.linea(f"  No se pudo leer el registro: {error}")
        return
    if not bloques:
        r.linea("  Sin fallas registradas. Buena senal.")
        return
    r.bandera(f"Hay {len(bloques)} fallas registradas; revisa el detalle.")
    for bloque in bloques:
        r.linea()
        for renglon in bloque.splitlines():
            r.linea(f"  {renglon}")


def generar() -> tuple[Path, dict]:
    r = Reporte()
    datos: dict = {}
    r.linea("=" * ANCHO)
    r.linea("ARZYZ VISION - REPORTE DE DIAGNOSTICO".center(ANCHO))
    r.linea("=" * ANCHO)

    for paso, funcion in (
        ("entorno", lambda: _entorno(r)),
        ("configuracion", lambda: _configuracion(r)),
        ("base de datos", lambda: _base_de_datos(r, datos)),
        ("evidencias", lambda: _evidencias(r, datos)),
        ("almacenamiento", lambda: _almacenamiento(r, datos)),
        ("salud de modulos", lambda: _salud_de_modulos(r, datos)),
        ("fallas", lambda: _fallas(r)),
    ):
        try:
            funcion()
        except Exception as error:
            r.titulo(f"error al analizar {paso}")
            r.linea(f"  {type(error).__name__}: {error}")

    r.titulo("resumen")
    if r.banderas:
        r.linea("  Hallazgos que merecen atencion:")
        for indice, bandera in enumerate(r.banderas, start=1):
            r.linea(f"    {indice}. {bandera}")
    else:
        r.linea("  Sin hallazgos automaticos.")
    datos["banderas"] = r.banderas

    destino = paths.DATA_DIR / "diagnostico"
    destino.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d_%H%M")
    archivo = destino / f"diagnostico_{marca}.txt"
    archivo.write_text(str(r), encoding="utf-8")
    (destino / f"diagnostico_{marca}.json").write_text(
        json.dumps(datos, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return archivo, datos


def main() -> int:
    print("=" * ANCHO)
    print("ARZYZ VISION - DIAGNOSTICO".center(ANCHO))
    print("=" * ANCHO)
    if not paths.DB_PATH.is_file():
        print()
        print("  AVISO: no existe la base de datos todavia.")
        print(f"  Esperada en: {paths.DB_PATH}")
        print("  Ejecuta el detector con la camara un rato y vuelve a intentarlo.")
        print()

    archivo, datos = generar()

    print()
    print("-" * ANCHO)
    print("RESUMEN")
    print("-" * ANCHO)
    for etiqueta, clave in (
        ("Objetos simultaneos (mediana)", "objetos_concurrentes"),
        ("Identidades registradas", "identidades"),
        ("Rotacion de identidad", "rotacion_identidad"),
        ("Detecciones por evento (promedio)", "promedio_detecciones"),
        ("Evidencias en disco", "evidencias"),
        ("Espacio de evidencias (MB)", "evidencias_mb"),
    ):
        if clave in datos:
            print(f"  {etiqueta:<38} {datos[clave]}")

    banderas = datos.get("banderas", [])
    print()
    if banderas:
        print(f"  {len(banderas)} hallazgo(s):")
        for indice, bandera in enumerate(banderas, start=1):
            print(f"    {indice}. {bandera}")
    else:
        print("  Sin hallazgos automaticos.")

    print()
    print("=" * ANCHO)
    print("REPORTE GUARDADO EN:")
    print(f"  {archivo}")
    print("=" * ANCHO)
    print("Comparte ese archivo .txt para analizarlo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
