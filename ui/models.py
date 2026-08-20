"""Seleccion, validacion y carga de modelos YOLO y archivos de video.

Extraido de DetectorApp sin modificar la logica. Es un mixin: conserva el acceso
a los widgets y al estado de la ventana. DetectorApp lo compone.
"""
from __future__ import annotations

import threading
from pathlib import Path
from tkinter import filedialog

from core.paths import MODEL_DIR

from tkinter import messagebox

from core import runtime
from core.config import SUPPORTED_MODEL_TASKS, TRACKABLE_MODEL_TASKS, save_config


class ModelMixin:
    """Seleccion, validacion y carga de modelos YOLO y archivos de video."""

    def _browse_video(self):
        path = filedialog.askopenfilename(
            title="Seleccionar video",
            filetypes=[
                ("Archivos de video", "*.mp4 *.avi *.mkv *.mov *.wmv"),
                ("Todos los archivos", "*.*"),
            ],
        )
        if path:
            self.file_entry.delete(0, "end")
            self.file_entry.insert(0, path)

    def _browse_model(self):
        path = filedialog.askopenfilename(
            title="Seleccionar modelo YOLO",
            filetypes=[("Modelo PyTorch", "*.pt")],
        )
        if path:
            self.load_model(Path(path))

    @staticmethod

    @staticmethod
    def _validate_model_file(path: Path):
        size = path.stat().st_size
        with path.open("rb") as model_file:
            header = model_file.read(256)
        text_header = header.decode("utf-8", errors="ignore").strip().lower()
        if (
            size < 1024
            or text_header.startswith(
                ("404", "not found", "<!doctype html", "<html")
            )
        ):
            preview = header.decode("utf-8", errors="replace").strip()
            raise ValueError(
                "El archivo seleccionado no contiene un modelo. "
                f"Mide sólo {size} bytes"
                + (f" y contiene: {preview!r}." if preview else ".")
                + " Descarga nuevamente el archivo .pt desde su fuente."
            )

    @staticmethod

    @staticmethod
    def _friendly_model_error(error: Exception) -> str:
        text = str(error)
        error_name = type(error).__name__
        if error_name in {"UnpicklingError", "EOFError"}:
            return (
                "El archivo .pt está incompleto, dañado o no es un checkpoint "
                f"de Ultralytics compatible. Detalle: {text}"
            )
        if "No module named" in text:
            return (
                "El modelo requiere una arquitectura o dependencia que no está "
                f"instalada. Detalle: {text}"
            )
        return f"{error_name}: {text}"

    def load_model(self, path: Path, initial: bool = False):
        if self.worker and self.worker.is_alive():
            messagebox.showwarning(
                "Detección activa",
                "Detén la detección antes de cambiar el modelo.",
            )
            return
        if not path.exists():
            self._set_message(f"No existe el modelo:\n{path}", error=True)
            self.model_card.set("SIN MODELO", "#ff6572")
            if initial:
                self.start_button.configure(state="disabled")
            return

        self.load_model_button.configure(state="disabled", text="Cargando…")
        self.select_classes_button.configure(state="disabled")
        self.people_only_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.model_card.set("CARGANDO…", "#f4b942")
        self._set_message("Validando el modelo seleccionado…")

        def loader():
            try:
                self._validate_model_file(path)
                runtime.load_inference_runtime()
                model = runtime.YOLO(str(path))
                task = str(getattr(model, "task", "") or "").lower()
                if task not in SUPPORTED_MODEL_TASKS:
                    raise ValueError(
                        "La tarea del modelo no es compatible. Se aceptan "
                        "detección, segmentación, pose, cajas orientadas (OBB) "
                        f"y clasificación; el archivo reporta {task!r}."
                    )
                names = getattr(model, "names", {})
                parameters = (
                    sum(parameter.numel() for parameter in model.model.parameters())
                    if hasattr(model.model, "parameters")
                    else 0
                )
                cuda_available = runtime.hay_gpu()
                self.model_queue.put(
                    (
                        "loaded", model, path, len(names), task,
                        parameters / 1_000_000, cuda_available,
                    )
                )
            except Exception as exc:
                self.model_queue.put(
                    ("failed", self._friendly_model_error(exc))
                )

        threading.Thread(target=loader, daemon=True, name="model-loader").start()

    def _model_loaded(
        self, model, path: Path, class_count: int, task: str,
        parameter_millions: float, cuda_available: bool
    ):
        # Se recuerda el equipo para que el proximo arranque aplique el
        # perfil correcto antes de construir la ventana.
        equipo = "gpu" if cuda_available else "cpu"
        if self.config_data.get("last_device") != equipo:
            self.config_data["last_device"] = equipo
            save_config(self.config_data)
        self.model = model
        self.model_task = task
        self.model_path = path
        names = getattr(model, "names", {})
        self.available_classes = (
            {int(key): str(value) for key, value in names.items()}
            if isinstance(names, dict)
            else {index: str(value) for index, value in enumerate(names)}
        )
        if self.enabled_class_names is not None:
            valid_names = set(self.available_classes.values())
            filtered = [
                name for name in self.enabled_class_names if name in valid_names
            ]
            # Si el modelo cambió y ninguna clase coincide, volvemos a "todas".
            self.enabled_class_names = filtered or None
        self.config_data["model_path"] = str(path)
        self.config_data["model_task"] = task
        self.config_data["enabled_class_names"] = self.enabled_class_names
        save_config(self.config_data)
        self.model_label.configure(text=path.name)
        self.model_card.set(path.name.upper(), "#bd8cff")
        self.load_model_button.configure(state="normal", text="Cargar modelo .pt")
        self.select_classes_button.configure(state="normal")
        self.people_only_button.configure(state="normal")
        self._update_class_filter_label()
        self.start_button.configure(state="normal")
        self.status_card.set("LISTO", "#43a9ff")
        line_supported = task in TRACKABLE_MODEL_TASKS
        self.line_enabled_check.configure(
            state="normal" if line_supported else "disabled"
        )
        self.draw_line_button.configure(
            state="normal" if line_supported else "disabled"
        )
        self.zone_enabled_check.configure(
            state="normal" if line_supported else "disabled"
        )
        self.high_danger_check.configure(
            state="normal" if line_supported else "disabled"
        )
        self.danger_sound_menu.configure(
            state="normal" if line_supported else "disabled"
        )
        self.load_danger_mp3_button.configure(
            state="normal" if line_supported else "disabled"
        )
        self.draw_zone_button.configure(
            state="normal" if line_supported else "disabled"
        )
        if not line_supported:
            self.line_enabled_var.set(False)
            self.zone_enabled_var.set(False)
            self.high_danger_var.set(False)
            self.config_data["line_enabled"] = False
            self.config_data["zone_enabled"] = False
            self.config_data["high_danger_zone"] = False
            save_config(self.config_data)
        if not cuda_available and parameter_millions >= 20:
            self.image_size_var.set("640")
            self.config_data["image_size"] = 640
            save_config(self.config_data)
            self._set_message(
                f"Modelo pesado ({parameter_millions:.0f} M parámetros) en CPU. "
                "Resolución ajustada a 640; usa n/s para conteo fluido.",
                error=True,
            )
        else:
            hardware = "GPU" if cuda_available else "CPU"
            self._set_message(
                f"Modelo listo · {task} · {class_count} clases · {hardware}"
            )

    def _model_failed(self, error: str):
        self.load_model_button.configure(state="normal", text="Cargar modelo .pt")
        has_previous_model = self.model is not None
        self.select_classes_button.configure(
            state="normal" if has_previous_model else "disabled"
        )
        self.people_only_button.configure(
            state="normal" if has_previous_model else "disabled"
        )
        self.start_button.configure(
            state="normal" if has_previous_model else "disabled"
        )
        self.model_card.set(
            self.model_path.name.upper() if has_previous_model else "ERROR",
            "#bd8cff" if has_previous_model else "#ff6572",
        )
        self.status_card.set(
            "LISTO" if has_previous_model else "ERROR",
            "#43a9ff" if has_previous_model else "#ff6572",
        )
        self._set_message(f"No se pudo cargar el modelo:\n{error}", error=True)
