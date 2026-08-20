"""Centro de Control local para los módulos de Arzyz Vision.

La interfaz se sirve únicamente en 127.0.0.1 y cada detector se ejecuta en un
proceso independiente para aislar la interfaz del trabajo de inferencia.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import mimetypes
import os
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from collections import Counter
from datetime import date, datetime, timedelta
from functools import lru_cache
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image

from core import failures
from kernel import Supervisor


BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
DB_PATH = BASE_DIR / "data" / "detecciones.db"
HOST = "127.0.0.1"
DEFAULT_PORT = 8765
APP_VERSION = "21"

# El orden de este registro es el ORDEN DEL PROCESO, no el historico de
# implementacion, y es el mismo que el de las tarjetas en web/index.html: la
# tolva carga el horno, el horno vuelca al mantenedor y del mantenedor sale el
# metal a la lingotera, que es la que cuenta piezas. Las cuatro son la misma
# linea y por eso van juntas; personas, placas y vehiculos son otro dominio.
#
# `status()` recorre este diccionario en orden, asi que el tablero y cualquier
# consumidor de /api/status ven la linea en su secuencia real.
MODULES = {
    # --- Proceso de fundicion: las cuatro camaras de la linea ---------------
    # Fase 2: estados con duracion. Tolva, horno y mantenedor comparten UN
    # script y se distinguen por su identificador, que es el que se inyecta en
    # ARZYZ_MODULE_ID y con el que cada proceso late. Un script y tres
    # procesos: una sola base de codigo sin perder el aislamiento de fallos.
    "tolva": {
        "title": "Tolva",
        "subtitle": "Movimiento en la artesa: carga de chatarra",
        # Senal de MOVIMIENTO calibrada el 20-ago-2026 contra el video del
        # 20-jul y verificada 4/4 contra imagen (quieta 0.11, cargando 9.41:
        # 83x de separacion). Detalle en core/pipeline/senales.py.
        "available": True,
        "script": BASE_DIR / "detector_estados.py",
    },
    "horno": {
        "title": "Horno Rotatorio",
        "subtitle": "Encendido, carga recibida y giro",
        # "Encendido/apagado" NO es medible con esta camara: medido el
        # 20-ago-2026, la senal optica depende de la posicion del tambor y no
        # del horno. El detalle esta en core/pipeline/senales.py.
        "available": False,
        "script": BASE_DIR / "detector_estados.py",
    },
    "mantenedor": {
        "title": "Mantenedor",
        "subtitle": "Aperturas de puerta y tiempo abierto",
        "available": True,
        "script": BASE_DIR / "detector_estados.py",
    },
    "objetos": {
        "title": "Detección de Objetos",
        "subtitle": "Conteo de piezas en banda transportadora",
        "available": True,
        "script": BASE_DIR / "detector_objetos.py",
    },
    # --- Seguridad y vigilancia: otro dominio ------------------------------
    "personas": {
        "title": "Detección de Personas",
        "subtitle": "Monitoreo, reglas y zonas de seguridad",
        "available": True,
        "script": BASE_DIR / "detector_empresarial.py",
    },
    "placas": {
        "title": "Detección de Placas",
        "subtitle": "Lectura OCR y registro vehicular",
        "available": False,
    },
    "vehiculos": {
        "title": "Detección de Vehículos",
        "subtitle": "Clasificación, conteo y seguimiento",
        "available": False,
    },
}

# Los identificadores de la linea de fundicion, en orden de proceso. Se declara
# una sola vez aqui para que el tablero, las pruebas y cualquier vista futura
# usen la misma fuente y no una lista repetida que se desincronice.
MODULOS_FUNDICION = ("tolva", "horno", "mantenedor", "objetos")


class ModuleManager:
    def __init__(self) -> None:
        self._processes: dict[str, subprocess.Popen] = {}
        self._started_at: dict[str, float] = {}
        self._lock = threading.Lock()
        # El supervisor vigila los procesos lanzados: reinicia caidas y
        # congelamientos, y respeta los cierres intencionales (codigo 0).
        self.supervisor = Supervisor()
        self.supervisor.start()

    def _running(self, module_id: str) -> bool:
        process = self._processes.get(module_id)
        return bool(process and process.poll() is None)

    def status(self) -> list[dict]:
        with self._lock:
            result = []
            for module_id, definition in MODULES.items():
                running = self._running(module_id)
                result.append(
                    {
                        "id": module_id,
                        "title": definition["title"],
                        "subtitle": definition["subtitle"],
                        "available": definition["available"],
                        "running": running,
                        "pid": (
                            self._processes[module_id].pid if running else None
                        ),
                        "started_at": (
                            self._started_at.get(module_id) if running else None
                        ),
                        "supervision": self.supervisor.estado_de(module_id),
                    }
                )
            return result

    def start(self, module_id: str) -> tuple[bool, str]:
        definition = MODULES.get(module_id)
        if not definition:
            return False, "Módulo desconocido."
        if not definition["available"]:
            return False, "Este módulo está preparado para una siguiente etapa."

        with self._lock:
            if self._running(module_id):
                self._focus(self._processes[module_id].pid)
                return True, "El módulo ya estaba activo; se llevó al frente."

            script = Path(definition["script"])
            if not script.is_file():
                return False, f"No se encontró el ejecutable de {definition['title']}."

            executable = Path(sys.executable)
            pythonw = executable.with_name("pythonw.exe")
            if os.name == "nt" and pythonw.is_file():
                executable = pythonw

            environment = os.environ.copy()
            environment["ARZYZ_LAUNCHED_FROM_HUB"] = "1"
            # El modulo debe latir con el MISMO nombre con el que se registra
            # aqui; si no coinciden, el supervisor no lo escucha nunca.
            environment["ARZYZ_MODULE_ID"] = module_id
            creation_flags = (
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            )
            def lanzar() -> subprocess.Popen:
                return subprocess.Popen(
                    [str(executable), "-B", str(script)],
                    cwd=str(BASE_DIR),
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creation_flags,
                )

            process = lanzar()
            self._processes[module_id] = process
            self._started_at[module_id] = time.time()
            # El supervisor conserva el relanzador: si el proceso cae, lo
            # reinicia con la misma configuracion sin intervencion del operador.
            self.supervisor.register(module_id, process, lanzar)
            return True, f"{definition['title']} se está iniciando."

    @staticmethod
    def _focus(pid: int) -> None:
        if os.name != "nt":
            return

        user32 = ctypes.windll.user32
        found: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def callback(hwnd, _lparam):
            process_id = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
            if process_id.value == pid and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
                return False
            return True

        user32.EnumWindows(callback, 0)
        if found:
            user32.ShowWindow(found[0], 9)
            user32.SetForegroundWindow(found[0])


MANAGER = ModuleManager()
SERVER_STARTED_AT = time.time()


def _distinct_objects(connection, day_text: str):
    """Cuenta objetos distintos del dia a partir de su identidad de seguimiento.

    Devuelve None si ningun registro del dia tiene identidades guardadas, para
    que el llamador use la suma de detecciones como respaldo.
    """
    identidades: set[str] = set()
    con_datos = False
    for (crudo,) in connection.execute(
        "SELECT track_ids FROM detections WHERE substr(detected_at, 1, 10) = ?",
        (day_text,),
    ):
        if not crudo:
            continue
        try:
            marcas = json.loads(crudo)
        except (TypeError, ValueError):
            continue
        if marcas:
            con_datos = True
            identidades.update(str(marca) for marca in marcas)
    return len(identidades) if con_datos else None


def _dashboard_data() -> dict:
    today = date.today()
    days = [today - timedelta(days=offset) for offset in range(6, -1, -1)]
    series = {day.isoformat(): 0 for day in days}
    class_totals: Counter[str] = Counter()
    stats = {
        "events_today": 0,
        "objects_today": 0,
        "crossings_today": 0,
        "alerts_today": 0,
        "events_total": 0,
    }
    recent: list[dict] = []

    if not DB_PATH.is_file():
        return {
            "stats": stats,
            "series": [{"date": key, "value": value} for key, value in series.items()],
            "classes": [],
            "recent": [],
        }

    try:
        connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1)
        connection.row_factory = sqlite3.Row
        today_text = today.isoformat()
        start_text = days[0].isoformat()

        row = connection.execute(
            """
            SELECT COUNT(*) AS events, COALESCE(SUM(total), 0) AS detections
            FROM detections WHERE substr(detected_at, 1, 10) = ?
            """,
            (today_text,),
        ).fetchone()
        stats["events_today"] = int(row["events"])
        stats["detections_today"] = int(row["detections"])
        # "Objetos" cuenta identidades distintas, no detecciones acumuladas: las
        # mismas dos personas en ocho eventos son dos objetos, no dieciseis.
        stats["objects_today"] = _distinct_objects(connection, today_text)
        if stats["objects_today"] is None:
            # Registros anteriores a la columna track_ids: se informa la suma.
            stats["objects_today"] = stats["detections_today"]
        stats["events_total"] = int(
            connection.execute("SELECT COUNT(*) FROM detections").fetchone()[0]
        )
        stats["crossings_today"] = int(
            connection.execute(
                "SELECT COUNT(*) FROM crossings WHERE substr(crossed_at, 1, 10) = ?",
                (today_text,),
            ).fetchone()[0]
        )
        stats["alerts_today"] = int(
            connection.execute(
                "SELECT COUNT(*) FROM zone_alerts WHERE substr(alerted_at, 1, 10) = ?",
                (today_text,),
            ).fetchone()[0]
        )

        for row in connection.execute(
            """
            SELECT substr(detected_at, 1, 10) AS day, COUNT(*) AS total
            FROM detections
            WHERE substr(detected_at, 1, 10) >= ?
            GROUP BY day
            """,
            (start_text,),
        ):
            if row["day"] in series:
                series[row["day"]] = int(row["total"])

        for row in connection.execute(
            "SELECT classes_json FROM detections ORDER BY id DESC LIMIT 1000"
        ):
            try:
                for name, amount in json.loads(row["classes_json"]).items():
                    class_totals[str(name)] += int(amount)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue

        for row in connection.execute(
            """
            SELECT event_time, source, event_type, detail, confidence
            FROM (
                SELECT detected_at AS event_time, source,
                       'DETECCIÓN' AS event_type, classes_json AS detail,
                       max_confidence AS confidence
                FROM detections
                UNION ALL
                SELECT crossed_at, source, 'CRUCE',
                       class_name || ' · ' || direction, confidence
                FROM crossings
                UNION ALL
                SELECT alerted_at, source, 'ALERTA',
                       class_name, confidence
                FROM zone_alerts
            )
            ORDER BY event_time DESC LIMIT 8
            """
        ):
            if row["event_type"] == "DETECCIÓN":
                try:
                    classes = json.loads(row["detail"])
                    summary = ", ".join(
                        f"{name} ×{amount}" for name, amount in classes.items()
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    summary = "Detección"
            else:
                summary = f"{row['event_type']} · {row['detail']}"
            recent.append(
                {
                    "time": row["event_time"],
                    "source": row["source"],
                    "summary": summary,
                    "total": 1,
                    "confidence": round(float(row["confidence"] or 0) * 100),
                }
            )
        connection.close()
    except sqlite3.Error:
        pass

    return {
        "stats": stats,
        "series": [{"date": key, "value": value} for key, value in series.items()],
        "classes": [
            {"name": name, "value": value}
            for name, value in class_totals.most_common(6)
        ],
        "recent": recent,
        "server_uptime": int(time.time() - SERVER_STARTED_AT),
    }


EVIDENCE_QUERIES = {
    "objects": {
        "table": "detections",
        "sql": """
            SELECT id, detected_at, source, classes_json, max_confidence,
                   evidence_path
            FROM detections
            WHERE evidence_path IS NOT NULL AND evidence_path <> ''
            ORDER BY id DESC LIMIT ?
        """,
    },
    "crossings": {
        "table": "crossings",
        "sql": """
            SELECT id, crossed_at, source,
                   class_name || ' · ' || direction,
                   confidence, evidence_path
            FROM crossings
            WHERE evidence_path IS NOT NULL AND evidence_path <> ''
            ORDER BY id DESC LIMIT ?
        """,
    },
    "alerts": {
        "table": "zone_alerts",
        "sql": """
            SELECT id, alerted_at, source, class_name, confidence,
                   evidence_path
            FROM zone_alerts
            WHERE evidence_path IS NOT NULL AND evidence_path <> ''
            ORDER BY id DESC LIMIT ?
        """,
    },
}


def _safe_evidence_path(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = BASE_DIR / candidate
    try:
        candidate = candidate.resolve()
        candidate.relative_to((BASE_DIR / "data" / "evidencias").resolve())
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_file() else None


def _evidence_data(category: str, limit: int = 36) -> list[dict]:
    definition = EVIDENCE_QUERIES.get(category)
    if not definition or not DB_PATH.is_file():
        return []
    items = []
    try:
        connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1)
        columns = {
            row[1]
            for row in connection.execute(
                f"PRAGMA table_info({definition['table']})"
            )
        }
        if category == "crossings" and "evidence_path" not in columns:
            connection.close()
            return []
        for row in connection.execute(definition["sql"], (min(limit, 100),)):
            evidence_path = _safe_evidence_path(row[5])
            if not evidence_path:
                continue
            if category == "objects":
                try:
                    classes = json.loads(row[3])
                    label = ", ".join(
                        f"{name} ×{amount}" for name, amount in classes.items()
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    label = "Detección"
            else:
                label = str(row[3])
            items.append(
                {
                    "id": int(row[0]),
                    "time": row[1],
                    "source": row[2],
                    "label": label,
                    "confidence": round(float(row[4] or 0) * 100),
                    "image_url": (
                        f"/api/evidence/image?type={category}&id={int(row[0])}"
                    ),
                    "thumbnail_url": (
                        f"/api/evidence/image?type={category}&id={int(row[0])}"
                        "&thumbnail=1"
                    ),
                }
            )
        connection.close()
    except sqlite3.Error:
        return []
    return items


def _evidence_counts(category: str) -> tuple[int, int]:
    definition = EVIDENCE_QUERIES.get(category)
    if not definition or not DB_PATH.is_file():
        return 0, 0
    table = definition["table"]
    try:
        connection = sqlite3.connect(
            f"file:{DB_PATH}?mode=ro", uri=True, timeout=1
        )
        columns = {
            row[1] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        total = int(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        )
        captured = (
            int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table} "
                    "WHERE evidence_path IS NOT NULL AND evidence_path <> ''"
                ).fetchone()[0]
            )
            if "evidence_path" in columns
            else 0
        )
        connection.close()
        return total, captured
    except sqlite3.Error:
        return 0, 0


def _evidence_path_by_id(category: str, evidence_id: int) -> Path | None:
    definition = EVIDENCE_QUERIES.get(category)
    if not definition or not DB_PATH.is_file():
        return None
    table = definition["table"]
    try:
        connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1)
        columns = {
            row[1] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if "evidence_path" not in columns:
            connection.close()
            return None
        row = connection.execute(
            f"SELECT evidence_path FROM {table} WHERE id = ?", (evidence_id,)
        ).fetchone()
        connection.close()
    except sqlite3.Error:
        return None
    return _safe_evidence_path(row[0] if row else None)


@lru_cache(maxsize=128)
def _thumbnail_bytes(path_text: str, modified_ns: int) -> bytes:
    del modified_ns  # Forma parte de la clave y renueva la caché al cambiar.
    with Image.open(path_text) as image:
        image.thumbnail((640, 360), Image.Resampling.LANCZOS)
        if image.mode != "RGB":
            image = image.convert("RGB")
        output = BytesIO()
        image.save(output, "JPEG", quality=82, optimize=True)
        return output.getvalue()


class ControlHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, _format: str, *_args) -> None:
        return

    def end_headers(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/menu", "/dashboard", "/index.html"} or path.endswith(
            (".js", ".css")
        ):
            self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def _json(
        self,
        payload: dict | list,
        status: int = HTTPStatus.OK,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _serve_evidence_image(self, query: dict[str, list[str]]) -> None:
        try:
            category = query.get("type", [""])[0]
            evidence_id = int(query.get("id", ["0"])[0])
        except (TypeError, ValueError):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        path = _evidence_path_by_id(category, evidence_id)
        if not path:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        thumbnail = query.get("thumbnail", ["0"])[0] == "1"
        try:
            body = (
                _thumbnail_bytes(str(path), path.stat().st_mtime_ns)
                if thumbnail
                else path.read_bytes()
            )
        except (OSError, ValueError):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=60")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _serve_live_events(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        last_signature = None
        last_heartbeat = 0.0
        try:
            while True:
                database_signature = tuple(
                    (
                        path.stat().st_mtime_ns,
                        path.stat().st_size,
                    )
                    if path.is_file()
                    else (0, 0)
                    for path in (DB_PATH, Path(f"{DB_PATH}-wal"))
                )
                module_status = MANAGER.status()
                module_signature = tuple(
                    (item["id"], item["running"], item["pid"])
                    for item in module_status
                )
                signature = (database_signature, module_signature)
                if signature != last_signature:
                    payload = {
                        "dashboard": _dashboard_data(),
                        "modules": module_status,
                        "now": datetime.now().isoformat(),
                    }
                    message = (
                        "event: update\n"
                        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    ).encode("utf-8")
                    self.wfile.write(message)
                    self.wfile.flush()
                    last_signature = signature
                    last_heartbeat = time.time()
                elif time.time() - last_heartbeat >= 10:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    last_heartbeat = time.time()
                time.sleep(0.6)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/status":
            self._json({"modules": MANAGER.status(), "now": datetime.now().isoformat()})
            return
        if path == "/api/version":
            self._json({"version": APP_VERSION})
            return
        if path == "/api/dashboard":
            self._json(_dashboard_data())
            return
        if path == "/api/live":
            self._serve_live_events()
            return
        if path == "/api/diagnostico/archivo":
            self._descargar_diagnostico(parse_qs(parsed.query))
            return
        if path == "/api/evidence":
            query = parse_qs(parsed.query)
            category = query.get("type", [""])[0]
            items = _evidence_data(category)
            total_events, captured_events = _evidence_counts(category)
            self._json(
                {
                    "category": category,
                    "items": items,
                    "total_events": total_events,
                    "captured_events": captured_events,
                    "missing_captures": max(total_events - captured_events, 0),
                }
            )
            return
        if path == "/api/evidence/image":
            self._serve_evidence_image(parse_qs(parsed.query))
            return
        if path.startswith("/api/"):
            self._json(
                {"ok": False, "message": "Ruta de servicio no encontrada."},
                HTTPStatus.NOT_FOUND,
            )
            return
        if path in {"/", "/menu", "/dashboard"}:
            self.path = "/index.html"
        super().do_GET()

    def _descargar_diagnostico(self, query: dict[str, list[str]]) -> None:
        nombre = query.get("name", [""])[0]
        carpeta = (BASE_DIR / "data" / "diagnostico").resolve()
        destino = (carpeta / nombre).resolve()
        # Solo se sirven archivos de esa carpeta: evita salir con rutas ".."
        if destino.parent != carpeta or not destino.is_file():
            self._json({"ok": False, "message": "Reporte no encontrado."},
                       HTTPStatus.NOT_FOUND)
            return
        contenido = destino.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        # "inline": el reporte se muestra en el dashboard y no se descarga a
        # ninguna carpeta del sistema. El archivo solo vive en data/diagnostico.
        self.send_header("Content-Disposition", f'inline; filename="{destino.name}"')
        self.send_header("Content-Length", str(len(contenido)))
        self.end_headers()
        self.wfile.write(contenido)

    def do_POST(self) -> None:
        raw_path = urlparse(self.path).path
        path = raw_path.strip("/").split("/")
        if len(path) == 4 and path[:2] == ["api", "modules"] and path[3] == "start":
            module_id = path[2]
            ok, message = MANAGER.start(module_id)
            self._json(
                {"ok": ok, "message": message, "modules": MANAGER.status()},
                HTTPStatus.OK if ok else HTTPStatus.CONFLICT,
            )
            return
        if raw_path == "/api/diagnostico":
            self._generar_diagnostico()
            return
        self._json({"ok": False, "message": "Ruta no encontrada."}, HTTPStatus.NOT_FOUND)

    def _generar_diagnostico(self) -> None:
        """Genera el reporte y devuelve su ubicacion y sus hallazgos."""
        try:
            from tools import diagnostico

            archivo, datos = diagnostico.generar()
        except Exception as exc:
            failures.record("dashboard", "no se pudo generar el diagnostico", exc=exc)
            self._json(
                {"ok": False, "message": f"No se pudo generar: {exc}"},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return
        self._json(
            {
                "ok": True,
                "archivo": archivo.name,
                # Ruta relativa: el reporte vive dentro del propio proyecto.
                "ruta": str(archivo.relative_to(BASE_DIR)),
                "banderas": datos.get("banderas", []),
                "resumen": {
                    clave: datos[clave]
                    for clave in (
                        "objetos_concurrentes",
                        "identidades",
                        "rotacion_identidad",
                        "promedio_detecciones",
                        "evidencias",
                    )
                    if clave in datos
                },
            }
        )


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:
        failures.record(
            "dashboard", "error atendiendo una peticion", exc=sys.exc_info()[1]
        )
        # El navegador cancela solicitudes de imágenes al cerrar el modal o
        # navegar. En Windows se reporta como 10053/10054 y no es una falla.
        error = sys.exc_info()[1]
        if isinstance(
            error,
            (BrokenPipeError, ConnectionAbortedError, ConnectionResetError),
        ):
            return
        super().handle_error(request, client_address)


def main() -> None:
    failures.configure("centro_control")
    parser = argparse.ArgumentParser(description="Centro de Control Arzyz Vision")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if not WEB_DIR.is_dir():
        raise SystemExit(f"No se encontró la interfaz: {WEB_DIR}")

    url = f"http://{HOST}:{args.port}"
    try:
        server = ControlServer((HOST, args.port), ControlHandler)
    except OSError:
        # Si el operador vuelve a usar el acceso directo, se reutiliza el
        # Centro de Control que ya está activo en lugar de duplicarlo.
        if not args.no_browser:
            webbrowser.open(url)
        return
    if not args.no_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    print(f"Centro de Control disponible en {url}")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
