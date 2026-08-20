"""Gestion de evidencias JPEG y bitacora CSV.

Extraido de detector_empresarial.py sin modificar la logica.
"""
from __future__ import annotations

import csv
import re
import threading
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

import cv2

from core import paths


class EvidenceManager:
    CSV_DELIMITER = ","
    CSV_EXCEL_SEPARATOR = "sep=,"
    # Las cuatro ultimas columnas son de la fase 2 (estados con duracion) y van
    # AL FINAL a proposito: agregar en medio correria de lugar las columnas que
    # operacion ya usa en sus hojas de Excel.
    #
    # `duracion` es legible y `duracion_s` numerica: la primera para leer, la
    # segunda para poder sumar el tiempo abierto en una tabla dinamica, que es
    # justo lo que se pidio ("temporizar y guardar el dato").
    CSV_FIELDS = [
        "id", "fecha_hora", "tipo_evento", "fuente", "total_objetos",
        "clases", "confianza_maxima", "direccion", "track_id",
        "modelo", "archivo_evidencia",
        "duracion", "duracion_s", "fin", "observaciones",
    ]

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    @staticmethod
    def slug(value: str, fallback: str = "general") -> str:
        normalized = unicodedata.normalize("NFKD", str(value))
        ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
        clean = re.sub(r"[^a-zA-Z0-9_-]+", "_", ascii_value).strip("_").lower()
        return clean[:60] or fallback

    @staticmethod
    def parse_timestamp(timestamp: str) -> datetime:
        try:
            return datetime.fromisoformat(timestamp)
        except ValueError:
            return datetime.now()

    def day_directory(self, timestamp: str) -> Path:
        moment = self.parse_timestamp(timestamp)
        directory = (
            self.root / f"{moment:%Y}" / f"{moment:%m}" / f"{moment:%d}"
        )
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def category_directory(
        self, timestamp: str, source: str, category: str
    ) -> Path:
        directory = (
            self.day_directory(timestamp)
            / self.slug(source, "fuente")
            / self.slug(category, "evidencias")
        )
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def image_path(
        self, timestamp: str, source: str, category: str,
        classes: dict[str, int] | None = None,
        confidence: float = 0.0,
    ) -> Path:
        moment = datetime.now()
        class_text = "-".join(
            f"{self.slug(name, 'objeto')}-{count}"
            for name, count in sorted((classes or {}).items())[:3]
        ) or "escena"
        class_text = class_text[:72].rstrip("_-")
        filename = (
            f"{self.slug(category)}_{moment:%H-%M-%S-%f}_"
            f"{class_text}_{confidence:.0%}_{uuid.uuid4().hex[:6]}.jpg"
        )
        return self.category_directory(
            timestamp, source, category
        ) / filename

    def save_image(
        self, frame, timestamp: str, source: str, category: str,
        classes: dict[str, int] | None = None,
        confidence: float = 0.0,
    ) -> str:
        output = self.image_path(
            timestamp, source, category, classes, confidence
        )
        temporary = output.with_suffix(".tmp")
        try:
            # imencode + Path.write_bytes funciona también cuando la ruta de
            # Windows contiene acentos y evita dejar un JPEG incompleto.
            encoded, buffer = cv2.imencode(
                ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90]
            )
            if not encoded:
                raise OSError("OpenCV no pudo codificar la imagen JPEG")
            with self.lock:
                temporary.write_bytes(buffer.tobytes())
                temporary.replace(output)
            try:
                return str(output.resolve().relative_to(paths.BASE_DIR.resolve()))
            except ValueError:
                return str(output.resolve())
        except (OSError, ValueError, cv2.error) as exc:
            try:
                if temporary.exists():
                    temporary.unlink()
                with paths.ERROR_LOG_PATH.open("a", encoding="utf-8") as log:
                    log.write(
                        f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] "
                        f"EVIDENCIA {category}: {exc}\n"
                    )
            except OSError:
                pass
            return ""

    def csv_path(self, timestamp: str) -> Path:
        moment = self.parse_timestamp(timestamp)
        return (
            self.day_directory(timestamp)
            / f"registro_eventos_{moment:%Y-%m-%d}.csv"
        )

    def _encabezado_de(self, path: Path) -> list[str] | None:
        """Columnas que ya tiene el archivo, o None si no se pudo leer."""
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as archivo:
                lineas = archivo.read().splitlines()
        except OSError:
            return None
        # Excel necesita la linea "sep=," ANTES del encabezado real.
        indice = 1 if lineas and lineas[0].startswith("sep=") else 0
        if indice >= len(lineas):
            return None
        return next(
            csv.reader([lineas[indice]], delimiter=self.CSV_DELIMITER)
        )

    def _escribir(self, path: Path, records: list[dict]) -> None:
        """Escribe el CSV completo. Unico lugar que arma el archivo.

        Lo comparten la reconstruccion diaria y la migracion de columnas: si
        cada una escribiera por su cuenta, una podria quedarse con el
        encabezado viejo.
        """
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as csv_file:
            csv_file.write(f"{self.CSV_EXCEL_SEPARATOR}\r\n")
            writer = csv.DictWriter(
                csv_file,
                fieldnames=self.CSV_FIELDS,
                delimiter=self.CSV_DELIMITER,
                extrasaction="ignore",
            )
            writer.writeheader()
            for record in records:
                writer.writerow(
                    {field: record.get(field, "") for field in self.CSV_FIELDS}
                )
        temporary.replace(path)

    def _migrar_columnas(self, path: Path) -> None:
        """Actualiza un CSV cuyo encabezado quedo de una version anterior.

        Al agregar columnas, una fila nueva escrita sobre un archivo con
        encabezado viejo lleva mas valores que columnas y desalinea todo el
        archivo. Ocurre solo el dia en que se despliega la version nueva, y es
        precisamente el tipo de costura donde este proyecto ya se ha lastimado:
        el error no salta, el dato simplemente sale mal. Se corrige una vez por
        archivo, conservando las filas que ya estaban.
        """
        if not path.exists() or path.stat().st_size == 0:
            return
        existente = self._encabezado_de(path)
        if existente is None or existente == self.CSV_FIELDS:
            return
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as archivo:
                lineas = archivo.read().splitlines()
        except OSError:
            return
        indice = 1 if lineas and lineas[0].startswith("sep=") else 0
        previas = list(
            csv.DictReader(lineas[indice:], delimiter=self.CSV_DELIMITER)
        )
        self._escribir(path, previas)

    def append_csv(self, record: dict):
        path = self.csv_path(record["fecha_hora"])
        try:
            with self.lock:
                self._migrar_columnas(path)
                new_file = not path.exists() or path.stat().st_size == 0
                with path.open(
                    "a", encoding="utf-8-sig", newline=""
                ) as csv_file:
                    if new_file:
                        csv_file.write(f"{self.CSV_EXCEL_SEPARATOR}\r\n")
                    writer = csv.DictWriter(
                        csv_file,
                        fieldnames=self.CSV_FIELDS,
                        delimiter=self.CSV_DELIMITER,
                        extrasaction="ignore",
                    )
                    if new_file:
                        writer.writeheader()
                    writer.writerow(
                        {
                            field: record.get(field, "")
                            for field in self.CSV_FIELDS
                        }
                    )
        except (OSError, csv.Error) as exc:
            try:
                with paths.ERROR_LOG_PATH.open("a", encoding="utf-8") as log:
                    log.write(
                        f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] "
                        f"CSV: {exc}\n"
                    )
            except OSError:
                pass

    def write_daily_csv(self, timestamp: str, records: list[dict]):
        with self.lock:
            self._escribir(self.csv_path(timestamp), records)
