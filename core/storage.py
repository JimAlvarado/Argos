"""EventStore: unico propietario de la base SQLite.

Extraido de detector_empresarial.py sin modificar la logica.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from core.evidence import EvidenceManager
from core.utils import formato_duracion


class EventStore:
    def __init__(
        self, db_path: Path, evidence_manager: EvidenceManager | None = None
    ):
        self.db_path = db_path
        self.evidence_manager = evidence_manager
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self):
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS detections (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        detected_at TEXT NOT NULL,
                        source TEXT NOT NULL,
                        total INTEGER NOT NULL,
                        classes_json TEXT NOT NULL,
                        max_confidence REAL NOT NULL,
                        evidence_path TEXT,
                        model_name TEXT NOT NULL
                    )
                    """
                )
                detection_columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(detections)")
                }
                if "track_ids" not in detection_columns:
                    # Identidad de los objetos del evento. Permite contar
                    # objetos distintos en vez de sumar detecciones repetidas.
                    connection.execute(
                        "ALTER TABLE detections ADD COLUMN track_ids TEXT"
                    )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_detections_time "
                    "ON detections(detected_at DESC)"
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS crossings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        crossed_at TEXT NOT NULL,
                        source TEXT NOT NULL,
                        track_id INTEGER NOT NULL,
                        class_name TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        confidence REAL NOT NULL DEFAULT 0,
                        evidence_path TEXT,
                        model_name TEXT NOT NULL
                    )
                    """
                )
                crossing_columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(crossings)")
                }
                if "confidence" not in crossing_columns:
                    connection.execute(
                        "ALTER TABLE crossings "
                        "ADD COLUMN confidence REAL NOT NULL DEFAULT 0"
                    )
                if "evidence_path" not in crossing_columns:
                    connection.execute(
                        "ALTER TABLE crossings ADD COLUMN evidence_path TEXT"
                    )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_crossings_time "
                    "ON crossings(crossed_at DESC)"
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS zone_alerts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        alerted_at TEXT NOT NULL,
                        source TEXT NOT NULL,
                        track_id INTEGER NOT NULL,
                        class_name TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        evidence_path TEXT,
                        model_name TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_zone_alerts_time "
                    "ON zone_alerts(alerted_at DESC)"
                )
                # Estados con duracion (fase 2: tolva, horno, mantenedor).
                # Las tres tablas anteriores guardan hechos INSTANTANEOS; aqui
                # lo que importa es cuanto duro algo, asi que el registro trae
                # inicio, fin y duracion en vez de un solo momento.
                #
                # - `estacion` es el modulo logico (mantenedor, tolva, horno) y
                #   `source` la camara concreta: un mismo script sirve a las
                #   tres estaciones, asi que agrupar por camara no alcanza.
                # - `origen` es el equivalente de `model_name` en las otras
                #   tablas: deja auditado COMO se midio. Hoy vale "camara:..."
                #   y el dia que una senal venga del PLC valdra "plc:...", sin
                #   migrar nada ni perder la trazabilidad de lo ya guardado.
                # - `parcial` marca que la duracion es una cota inferior porque
                #   no se observo el inicio o el fin. Sin este dato, un promedio
                #   de duraciones mezclaria intervalos truncados con reales.
                # - `con_hueco` marca que hubo muestras sin dato confiable
                #   durante el intervalo (la PTZ del mantenedor se reposiciona).
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS estados (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        estacion TEXT NOT NULL,
                        estado TEXT NOT NULL,
                        inicio TEXT NOT NULL,
                        fin TEXT NOT NULL,
                        duracion_s REAL NOT NULL,
                        source TEXT NOT NULL,
                        origen TEXT NOT NULL,
                        parcial INTEGER NOT NULL DEFAULT 0,
                        con_hueco INTEGER NOT NULL DEFAULT 0,
                        valor_medio REAL,
                        evidence_path TEXT
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_estados_time "
                    "ON estados(inicio DESC)"
                )
                # Las consultas de resumen filtran por estacion y estado antes
                # de sumar duraciones; sin este indice recorrerian la tabla
                # completa en cada refresco del tablero.
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_estados_estacion "
                    "ON estados(estacion, estado, inicio DESC)"
                )

    def insert(self, event: dict) -> int:
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    """
                    INSERT INTO detections (
                        detected_at, source, total, classes_json,
                        max_confidence, evidence_path, model_name, track_ids
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["detected_at"],
                        event["source"],
                        event["total"],
                        json.dumps(event["classes"], ensure_ascii=False),
                        event["max_confidence"],
                        event.get("evidence_path", ""),
                        event["model_name"],
                        json.dumps(
                            list(event.get("track_ids", [])), ensure_ascii=False
                        ),
                    ),
                )
                event_id = int(cursor.lastrowid)
        if self.evidence_manager:
            self.evidence_manager.append_csv(
                {
                    "id": f"D-{event_id}",
                    "fecha_hora": event["detected_at"],
                    "tipo_evento": "DETECCION",
                    "fuente": event["source"],
                    "total_objetos": event["total"],
                    "clases": ", ".join(
                        f"{name} x{count}"
                        for name, count in event["classes"].items()
                    ),
                    "confianza_maxima": f"{event['max_confidence']:.2%}",
                    "modelo": event["model_name"],
                    "archivo_evidencia": event.get("evidence_path", ""),
                }
            )
        return event_id

    def recent(self, limit: int = 100) -> list[dict]:
        with closing(self._connect()) as connection:
            detection_rows = connection.execute(
                    """
                    SELECT id, detected_at, source, total, classes_json,
                           max_confidence, evidence_path, model_name
                    FROM detections ORDER BY id DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            crossing_rows = connection.execute(
                """
                SELECT id, crossed_at, source, track_id, class_name,
                       direction, confidence, evidence_path, model_name
                FROM crossings ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            zone_rows = connection.execute(
                """
                SELECT id, alerted_at, source, track_id, class_name,
                       confidence, evidence_path, model_name
                FROM zone_alerts ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            estado_rows = connection.execute(
                """
                SELECT id, inicio, source, estacion, estado, duracion_s,
                       parcial, con_hueco, evidence_path, origen
                FROM estados ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        events = [
            {
                "id": f"D-{row[0]}",
                "detected_at": row[1],
                "source": row[2],
                "total": row[3],
                "classes": json.loads(row[4]),
                "max_confidence": row[5],
                "evidence_path": row[6],
                "model_name": row[7],
            }
            for row in detection_rows
        ]
        events.extend(
            {
                "id": f"C-{row[0]}",
                "detected_at": row[1],
                "source": row[2],
                "total": 1,
                "classes": {
                    f"↔ CRUCE · {row[4]} · {row[5]}": 1
                },
                "max_confidence": row[6] or 0.0,
                "evidence_path": row[7] or "",
                "model_name": row[8],
            }
            for row in crossing_rows
        )
        events.extend(
            {
                "id": f"Z-{row[0]}",
                "detected_at": row[1],
                "source": row[2],
                "total": 1,
                "classes": {f"⚠ ZONA · {row[4]}": 1},
                "max_confidence": row[5],
                "evidence_path": row[6] or "",
                "model_name": row[7],
            }
            for row in zone_rows
        )
        # El estado se ubica en la linea de tiempo por su INICIO, que es lo que
        # el operador busca ("a que hora se abrio"), no por su fin. La duracion
        # va en la etiqueta porque es el dato de valor del evento.
        events.extend(
            {
                "id": f"E-{row[0]}",
                "detected_at": row[1],
                "source": row[2],
                "total": 1,
                "classes": {
                    f"⏱ {row[3].upper()} · {row[4]} "
                    f"{formato_duracion(row[5])}"
                    + (" (parcial)" if row[6] else "")
                    + (" (con hueco)" if row[7] else ""): 1
                },
                # Un estado no tiene confianza: la columna existe por el
                # contrato del registro, y el dato auditable va en model_name.
                "max_confidence": 0.0,
                "evidence_path": row[8] or "",
                "model_name": row[9],
            }
            for row in estado_rows
        )
        events.sort(
            key=lambda event: (event["detected_at"], event["id"]),
            reverse=True,
        )
        return events[:limit]

    def insert_crossing(self, event: dict) -> int:
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    """
                    INSERT INTO crossings (
                        crossed_at, source, track_id, class_name,
                        direction, confidence, evidence_path, model_name
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["crossed_at"],
                        event["source"],
                        event["track_id"],
                        event["class_name"],
                        event["direction"],
                        event.get("confidence", 0.0),
                        event.get("evidence_path", ""),
                        event["model_name"],
                    ),
                )
                crossing_id = int(cursor.lastrowid)
        if self.evidence_manager:
            self.evidence_manager.append_csv(
                {
                    "id": f"C-{crossing_id}",
                    "fecha_hora": event["crossed_at"],
                    "tipo_evento": "CRUCE_LINEA",
                    "fuente": event["source"],
                    "total_objetos": 1,
                    "clases": event["class_name"],
                    "direccion": event["direction"],
                    "track_id": event["track_id"],
                    "confianza_maxima": f"{event.get('confidence', 0.0):.2%}",
                    "modelo": event["model_name"],
                    "archivo_evidencia": event.get("evidence_path", ""),
                }
            )
        return crossing_id

    def insert_zone_alert(self, event: dict) -> int:
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    """
                    INSERT INTO zone_alerts (
                        alerted_at, source, track_id, class_name,
                        confidence, evidence_path, model_name
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["alerted_at"],
                        event["source"],
                        event["track_id"],
                        event["class_name"],
                        event["confidence"],
                        event.get("evidence_path", ""),
                        event["model_name"],
                    ),
                )
                alert_id = int(cursor.lastrowid)
        if self.evidence_manager:
            self.evidence_manager.append_csv(
                {
                    "id": f"Z-{alert_id}",
                    "fecha_hora": event["alerted_at"],
                    "tipo_evento": "ALERTA_ZONA",
                    "fuente": event["source"],
                    "total_objetos": 1,
                    "clases": event["class_name"],
                    "confianza_maxima": f"{event['confidence']:.2%}",
                    "track_id": event["track_id"],
                    "modelo": event["model_name"],
                    "archivo_evidencia": event.get("evidence_path", ""),
                }
            )
        return alert_id

    def insert_estado(self, event: dict) -> int:
        """Registra un intervalo que ya termino, con su duracion."""
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    """
                    INSERT INTO estados (
                        estacion, estado, inicio, fin, duracion_s, source,
                        origen, parcial, con_hueco, valor_medio, evidence_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["estacion"],
                        event["estado"],
                        event["inicio"],
                        event["fin"],
                        float(event["duracion_s"]),
                        event["source"],
                        event["origen"],
                        int(bool(event.get("parcial", False))),
                        int(bool(event.get("con_hueco", False))),
                        event.get("valor_medio"),
                        event.get("evidence_path", ""),
                    ),
                )
                estado_id = int(cursor.lastrowid)
        if self.evidence_manager:
            self.evidence_manager.append_csv(
                self._csv_de_estado(estado_id, event)
            )
        return estado_id

    @staticmethod
    def _csv_de_estado(estado_id: int, event: dict) -> dict:
        """Fila del CSV diario. Compartida con la reconstruccion de CSV.

        Si cada camino armara la fila por su cuenta, un CSV reconstruido no
        coincidiria con el que se escribio en vivo.
        """
        avisos = []
        if event.get("parcial"):
            avisos.append("duracion parcial")
        if event.get("con_hueco"):
            avisos.append("con hueco de vision")
        return {
            "id": f"E-{estado_id}",
            "fecha_hora": event["inicio"],
            "tipo_evento": "ESTADO",
            "fuente": event["source"],
            "total_objetos": "",
            "clases": f"{event['estacion']}: {event['estado']}",
            "duracion": formato_duracion(float(event["duracion_s"])),
            "duracion_s": f"{float(event['duracion_s']):.1f}",
            "fin": event["fin"],
            "observaciones": "; ".join(avisos),
            "modelo": event["origen"],
            "archivo_evidencia": event.get("evidence_path", ""),
        }

    def resumen_de_estados(
        self, estacion: str, estado: str, desde: str | None = None
    ) -> dict:
        """Cuantas veces y cuanto tiempo estuvo la estacion en ese estado.

        Es el dato que pidio operacion para el mantenedor: temporizar las
        aperturas y guardarlas. Los intervalos parciales se cuentan aparte
        porque su duracion es una cota inferior y no debe promediarse con las
        completas sin decirlo.
        """
        consulta = (
            "SELECT COUNT(*), COALESCE(SUM(duracion_s), 0), "
            "COALESCE(MAX(duracion_s), 0), "
            "COALESCE(SUM(parcial), 0), COALESCE(SUM(con_hueco), 0) "
            "FROM estados WHERE estacion = ? AND estado = ?"
        )
        parametros: list = [estacion, estado]
        if desde:
            consulta += " AND inicio >= ?"
            parametros.append(desde)
        with closing(self._connect()) as connection:
            veces, total, maxima, parciales, huecos = connection.execute(
                consulta, parametros
            ).fetchone()
        veces = int(veces)
        return {
            "veces": veces,
            "duracion_total": float(total),
            "duracion_maxima": float(maxima),
            "duracion_promedio": float(total) / veces if veces else 0.0,
            "parciales": int(parciales),
            "con_hueco": int(huecos),
        }

    def record_manual_capture(self, event: dict):
        if not self.evidence_manager:
            return
        self.evidence_manager.append_csv(
            {
                "id": f"M-{uuid.uuid4().hex[:8]}",
                "fecha_hora": event["captured_at"],
                "tipo_evento": "CAPTURA_MANUAL",
                "fuente": event["source"],
                "total_objetos": "",
                "clases": "",
                "modelo": event["model_name"],
                "archivo_evidencia": event["evidence_path"],
            }
        )

    def maintain_evidence(self) -> dict:
        """Migra carpetas antiguas y reconstruye CSV diarios sin duplicados."""
        manager = self.evidence_manager
        if not manager:
            return {"moved": 0, "csv_files": 0}
        moved = 0
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT id, detected_at, source, classes_json,
                       max_confidence, evidence_path
                FROM detections WHERE evidence_path <> ''
                """
            ).fetchall()
            by_path = {
                str(Path(row[5]).resolve()): row for row in rows if row[5]
            }
            updates = []
            legacy_directories = [
                directory
                for directory in manager.root.iterdir()
                if directory.is_dir()
                and re.fullmatch(r"\d{4}-\d{2}-\d{2}", directory.name)
            ]
            for directory in legacy_directories:
                timestamp_fallback = f"{directory.name} 00:00:00"
                for old_path in directory.glob("*.jpg"):
                    row = by_path.get(str(old_path.resolve()))
                    timestamp = row[1] if row else timestamp_fallback
                    source = row[2] if row else "sin_fuente"
                    category = (
                        "capturas_manuales"
                        if old_path.name.startswith("manual_")
                        else "detecciones"
                    )
                    destination_dir = manager.category_directory(
                        timestamp, source, category
                    )
                    destination = destination_dir / old_path.name
                    if destination.exists():
                        destination = destination.with_name(
                            f"{destination.stem}_{uuid.uuid4().hex[:6]}.jpg"
                        )
                    old_path.replace(destination)
                    moved += 1
                    if row:
                        updates.append((str(destination), int(row[0])))
                try:
                    directory.rmdir()
                except OSError:
                    pass
            if updates:
                with connection:
                    connection.executemany(
                        "UPDATE detections SET evidence_path = ? WHERE id = ?",
                        updates,
                    )

        records_by_day: dict[str, list[dict]] = {}
        with closing(self._connect()) as connection:
            detections = connection.execute(
                """
                SELECT id, detected_at, source, total, classes_json,
                       max_confidence, evidence_path, model_name
                FROM detections ORDER BY detected_at, id
                """
            ).fetchall()
            crossings = connection.execute(
                """
                SELECT id, crossed_at, source, track_id, class_name,
                       direction, model_name
                FROM crossings ORDER BY crossed_at, id
                """
            ).fetchall()
            zone_alerts = connection.execute(
                """
                SELECT id, alerted_at, source, track_id, class_name,
                       confidence, evidence_path, model_name
                FROM zone_alerts ORDER BY alerted_at, id
                """
            ).fetchall()
            estados = connection.execute(
                """
                SELECT id, inicio, fin, source, estacion, estado, duracion_s,
                       origen, parcial, con_hueco, evidence_path
                FROM estados ORDER BY inicio, id
                """
            ).fetchall()
        for row in detections:
            classes = json.loads(row[4])
            record = {
                "id": f"D-{row[0]}", "fecha_hora": row[1],
                "tipo_evento": "DETECCION", "fuente": row[2],
                "total_objetos": row[3],
                "clases": ", ".join(
                    f"{name} x{count}" for name, count in classes.items()
                ),
                "confianza_maxima": f"{row[5]:.2%}",
                "modelo": row[7], "archivo_evidencia": row[6] or "",
            }
            records_by_day.setdefault(row[1][:10], []).append(record)
        for row in crossings:
            record = {
                "id": f"C-{row[0]}", "fecha_hora": row[1],
                "tipo_evento": "CRUCE_LINEA", "fuente": row[2],
                "total_objetos": 1, "clases": row[4],
                "direccion": row[5], "track_id": row[3],
                "modelo": row[6],
            }
            records_by_day.setdefault(row[1][:10], []).append(record)
        for row in zone_alerts:
            record = {
                "id": f"Z-{row[0]}", "fecha_hora": row[1],
                "tipo_evento": "ALERTA_ZONA", "fuente": row[2],
                "total_objetos": 1, "clases": row[4],
                "confianza_maxima": f"{row[5]:.2%}",
                "track_id": row[3], "modelo": row[7],
                "archivo_evidencia": row[6] or "",
            }
            records_by_day.setdefault(row[1][:10], []).append(record)
        for row in estados:
            # Se reusa el mismo armador que la escritura en vivo: si cada
            # camino construyera la fila por su cuenta, un CSV reconstruido no
            # coincidiria con el original y nadie sabria cual creer.
            record = self._csv_de_estado(
                int(row[0]),
                {
                    "inicio": row[1], "fin": row[2], "source": row[3],
                    "estacion": row[4], "estado": row[5], "duracion_s": row[6],
                    "origen": row[7], "parcial": bool(row[8]),
                    "con_hueco": bool(row[9]), "evidence_path": row[10] or "",
                },
            )
            records_by_day.setdefault(row[1][:10], []).append(record)
        for day, records in records_by_day.items():
            records.sort(key=lambda record: record["fecha_hora"])
            manager.write_daily_csv(f"{day} 00:00:00", records)
        return {"moved": moved, "csv_files": len(records_by_day)}
