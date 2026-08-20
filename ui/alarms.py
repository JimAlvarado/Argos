"""Alarma sonora de zona de alto peligro: pitidos y MP3 personalizado.

Extraido de DetectorApp sin modificar la logica. Es un mixin: conserva el acceso
a los widgets y al estado de la ventana. DetectorApp lo compone.
"""
from __future__ import annotations

import ctypes
import os
import threading
from pathlib import Path
from tkinter import filedialog

try:
    import winsound
except ImportError:  # Windows unicamente
    winsound = None

from core.config import DANGER_SOUND_PATTERNS

from tkinter import messagebox

from core.config import DANGER_SOUND_OPTIONS, DANGER_SOUND_PATTERNS, save_config


class AlarmsMixin:
    """Alarma sonora de zona de alto peligro: pitidos y MP3 personalizado."""

    def _danger_sound_changed(self, selected: str):
        selected = (
            selected
            if selected in DANGER_SOUND_OPTIONS
            else "Doble pitido"
        )
        if selected == "MP3 personalizado":
            path = Path(str(self.config_data.get("danger_mp3_path", "")))
            if not path.is_file():
                self._browse_danger_mp3()
                path = Path(
                    str(self.config_data.get("danger_mp3_path", ""))
                )
                if not path.is_file():
                    selected = "Doble pitido"
                    self.danger_sound_var.set(selected)
        self.config_data["danger_sound_mode"] = selected
        save_config(self.config_data)
        if self._danger_alarm_active:
            self._restart_danger_alarm()
        self._set_message(f"Alarma crítica seleccionada: {selected}.")

    def _browse_danger_mp3(self):
        selected = filedialog.askopenfilename(
            title="Seleccionar alarma MP3 para alto peligro",
            filetypes=[
                ("Audio MP3", "*.mp3"),
                ("Todos los archivos", "*.*"),
            ],
        )
        if not selected:
            return
        path = Path(selected)
        if path.suffix.lower() != ".mp3" or not path.is_file():
            messagebox.showerror(
                "Archivo no válido",
                "Selecciona un archivo de audio con extensión .mp3.",
            )
            return
        self.config_data["danger_mp3_path"] = str(path)
        self.config_data["danger_sound_mode"] = "MP3 personalizado"
        self.danger_sound_var.set("MP3 personalizado")
        self.danger_mp3_label.configure(text=path.name)
        save_config(self.config_data)
        if self._danger_alarm_active:
            self._restart_danger_alarm()
        self._set_message(f"MP3 de alto peligro cargado: {path.name}")

    def _restart_danger_alarm(self):
        if not self._danger_alarm_active:
            return
        self._danger_alarm_active = False
        self._danger_alarm_generation += 1
        self._stop_danger_mp3()
        self._danger_alarm_active = True
        self._danger_alarm_generation += 1
        generation = self._danger_alarm_generation
        mode = self.danger_sound_var.get()
        if mode == "MP3 personalizado" and self._start_danger_mp3():
            return
        self._danger_alarm_step(generation, 0)

    @staticmethod
    def _play_beep_async(frequency: int = 1350, duration_ms: int = 240):
        if not winsound:
            return

        def play():
            try:
                winsound.Beep(frequency, duration_ms)
            except (OSError, RuntimeError):
                pass

        threading.Thread(
            target=play,
            daemon=True,
            name="zone-alert-beep",
        ).start()

    def _set_danger_alarm(self, active: bool):
        active = bool(active)
        if active == self._danger_alarm_active:
            return
        self._danger_alarm_active = active
        self._danger_alarm_generation += 1
        generation = self._danger_alarm_generation
        if active:
            mode = self.danger_sound_var.get()
            if mode == "MP3 personalizado" and self._start_danger_mp3():
                return
            self._danger_alarm_step(generation, 0)
        else:
            self._stop_danger_mp3()

    @staticmethod

    @staticmethod
    def _mci(command: str) -> int:
        if os.name != "nt":
            return 1
        try:
            return int(
                ctypes.windll.winmm.mciSendStringW(
                    command, None, 0, None
                )
            )
        except (AttributeError, OSError):
            return 1

    def _start_danger_mp3(self) -> bool:
        path = Path(str(self.config_data.get("danger_mp3_path", "")))
        if path.suffix.lower() != ".mp3" or not path.is_file():
            self._set_message(
                "El MP3 de alto peligro no está disponible; "
                "se usará Doble pitido.",
                error=True,
            )
            return False
        self._stop_danger_mp3()
        alias = self._danger_mp3_alias
        if self._mci(f'open "{path}" type mpegvideo alias {alias}') != 0:
            self._set_message(
                "Windows no pudo abrir el MP3; se usará Doble pitido.",
                error=True,
            )
            return False
        if self._mci(f"play {alias} repeat") != 0:
            self._mci(f"close {alias}")
            self._set_message(
                "Windows no pudo reproducir el MP3; "
                "se usará Doble pitido.",
                error=True,
            )
            return False
        self._danger_mp3_playing = True
        return True

    def _stop_danger_mp3(self):
        if not self._danger_mp3_playing:
            return
        alias = self._danger_mp3_alias
        self._mci(f"stop {alias}")
        self._mci(f"close {alias}")
        self._danger_mp3_playing = False
