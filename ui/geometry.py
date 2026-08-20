"""Linea de conteo y zona sobre el lienzo: dibujo, arrastre y reescalado.

Extraido de DetectorApp sin modificar la logica. Es un mixin: conserva el acceso
a los widgets y al estado de la ventana. DetectorApp lo compone.
"""
from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

import os

import cv2
import numpy as np
from tkinter import messagebox

from core.config import save_config
from core.pipeline.validation import validated_line_points, validated_zone_points


class GeometryMixin:
    """Linea de conteo y zona sobre el lienzo: dibujo, arrastre y reescalado."""

    def _rule_tool_changed(self, selected: str):
        if selected == "Zona de alerta":
            self.line_tool_frame.pack_forget()
            self.zone_tool_frame.pack(fill="x")
        else:
            self.zone_tool_frame.pack_forget()
            self.line_tool_frame.pack(fill="x")

    def _line_enabled_changed(self):
        enabled = bool(self.line_enabled_var.get())
        if enabled and not self.line_defined:
            self.line_enabled_var.set(False)
            self.config_data["line_enabled"] = False
            save_config(self.config_data)
            self._refresh_line_overlay()
            self._set_message(
                "Primero pulsa Trazar y marca los dos puntos de la línea.",
                error=True,
            )
            return
        self.config_data["line_enabled"] = enabled
        save_config(self.config_data)
        worker = self.worker
        if worker and worker.is_alive():
            worker.update_line(self.line_points, enabled)
        self._refresh_line_overlay()
        self._set_message(
            "Conteo por línea activado" if enabled else "Conteo por línea desactivado"
        )

    def _zone_enabled_changed(self):
        enabled = bool(self.zone_enabled_var.get())
        self.config_data["zone_enabled"] = enabled
        self.config_data["zone_points"] = self.zone_points
        save_config(self.config_data)
        worker = self.worker
        if worker and worker.is_alive():
            worker.update_zone(
                self.zone_points,
                enabled,
                bool(self.high_danger_var.get()),
            )
        if not enabled:
            self._set_danger_alarm(False)
        self._refresh_zone_overlay()
        self._set_message(
            "Vigilancia de zona activada"
            if enabled
            else "Vigilancia de zona desactivada"
        )

    def _high_danger_changed(self):
        high_danger = bool(self.high_danger_var.get())
        self.config_data["high_danger_zone"] = high_danger
        save_config(self.config_data)
        worker = self.worker
        if worker and worker.is_alive():
            worker.update_zone(
                self.zone_points,
                bool(self.zone_enabled_var.get()),
                high_danger,
            )
        if not high_danger:
            self._set_danger_alarm(False)
        self._refresh_zone_overlay()
        self._set_message(
            "Modo de alto peligro activado"
            if high_danger
            else "Modo de alto peligro desactivado"
        )

    def begin_line_drawing(self):
        if self.last_raw_frame is None:
            messagebox.showinfo(
                "Sin imagen",
                "Inicia una fuente de video para dibujar la línea sobre la escena.",
            )
            return
        self._drawing_zone_points = None
        self.finish_zone_button.configure(state="disabled")
        self._drag_target = None
        self._drag_original_points = None
        self._drawing_line_points = []
        self.video_canvas.configure(cursor="crosshair")
        self._refresh_zone_overlay()
        self._set_message(
            "Dibujo de línea: haz clic en el primer punto sobre el video."
        )

    def clear_line(self):
        self._drawing_line_points = None
        self._drag_target = None
        self._drag_original_points = None
        self.line_defined = False
        self.line_enabled_var.set(False)
        self.config_data["line_defined"] = False
        self.config_data["line_enabled"] = False
        save_config(self.config_data)
        if self.worker and self.worker.is_alive():
            self.worker.update_line(self.line_points, False)
            self.worker.reset_crossing_counts()
        self.video_canvas.configure(cursor="arrow")
        self._refresh_line_overlay()
        self.cross_total_label.configure(text="0")
        self.cross_ab_label.configure(text="0")
        self.cross_ba_label.configure(text="0")
        self.cross_classes_label.configure(text="Por clase: —")
        self.last_crossing_label.configure(text="Último: sin cruces")
        self._set_message("Trazo de cruce de línea borrado.")

    def begin_zone_drawing(self):
        if self.last_raw_frame is None:
            messagebox.showinfo(
                "Sin imagen",
                "Inicia una fuente de video para dibujar la zona.",
            )
            return
        self._drawing_line_points = None
        self._drag_target = None
        self._drag_original_points = None
        self._drawing_zone_points = []
        self.finish_zone_button.configure(state="normal")
        self.video_canvas.configure(cursor="crosshair")
        self._set_message(
            "Marca al menos 3 vértices y pulsa Finalizar."
        )
        self._refresh_line_overlay()
        self._refresh_zone_overlay()

    def finish_zone_drawing(self):
        points = self._drawing_zone_points or []
        if len(points) < 3:
            self._set_message(
                "La zona necesita al menos 3 vértices.", error=True
            )
            return
        validated = validated_zone_points(points)
        if validated != points:
            self._set_message(
                "El polígono es demasiado pequeño o inválido.", error=True
            )
            return
        self.zone_points = [point[:] for point in points]
        self._drawing_zone_points = None
        self.finish_zone_button.configure(state="disabled")
        self.video_canvas.configure(cursor="arrow")
        self.zone_enabled_var.set(True)
        self.config_data["zone_enabled"] = True
        self.config_data["zone_points"] = self.zone_points
        save_config(self.config_data)
        if self.worker and self.worker.is_alive():
            self.worker.update_zone(
                self.zone_points, True, bool(self.high_danger_var.get())
            )
        self._refresh_zone_overlay()
        self._set_message(
            "Zona de alerta guardada y activada."
        )

    def clear_zone(self):
        self._drawing_zone_points = None
        self._drag_target = None
        self._drag_original_points = None
        self.finish_zone_button.configure(state="disabled")
        self.video_canvas.configure(cursor="arrow")
        self.zone_enabled_var.set(False)
        self.config_data["zone_enabled"] = False
        save_config(self.config_data)
        if self.worker and self.worker.is_alive():
            self.worker.update_zone(
                self.zone_points, False, bool(self.high_danger_var.get())
            )
        self._set_danger_alarm(False)
        self._refresh_zone_overlay()
        self._set_message("Zona de alerta desactivada.")

    def _canvas_normalized_point(
        self, event, clamp: bool = False, report_error: bool = True
    ):
        x0, y0, width, height = self.video_display_rect
        if width <= 1 or height <= 1:
            return None
        inside = (
            x0 <= event.x <= x0 + width
            and y0 <= event.y <= y0 + height
        )
        if not inside and not clamp:
            if report_error:
                self._set_message(
                    "Haz clic dentro de la imagen de video.", error=True
                )
            return None
        return [
            min(max((event.x - x0) / width, 0.0), 1.0),
            min(max((event.y - y0) / height, 0.0), 1.0),
        ]

    def _find_drag_target(self, event):
        x0, y0, width, height = self.video_display_rect
        if width <= 1 or height <= 1:
            return None
        selected_tool = self.rule_tool_var.get()
        candidates = []
        if self.line_defined and bool(self.line_enabled_var.get()):
            for index, point in enumerate(self.line_points):
                x = x0 + point[0] * width
                y = y0 + point[1] * height
                distance = (event.x - x) ** 2 + (event.y - y) ** 2
                priority = 0 if selected_tool == "Cruce de línea" else 1
                candidates.append((distance, priority, "line", index))
        if bool(self.zone_enabled_var.get()):
            for index, point in enumerate(self.zone_points):
                x = x0 + point[0] * width
                y = y0 + point[1] * height
                distance = (event.x - x) ** 2 + (event.y - y) ** 2
                priority = 0 if selected_tool == "Zona de alerta" else 1
                candidates.append((distance, priority, "zone", index))
        if not candidates:
            return None
        distance, _, kind, index = min(
            candidates, key=lambda item: (item[0], item[1])
        )
        return (kind, index) if distance <= 15 ** 2 else None

    def _on_video_click(self, event):
        if (
            self._drawing_zone_points is None
            and self._drawing_line_points is None
        ):
            target = self._find_drag_target(event)
            if target is not None:
                self._drag_target = target
                source = (
                    self.line_points
                    if target[0] == "line"
                    else self.zone_points
                )
                self._drag_original_points = [
                    point[:] for point in source
                ]
                self.video_canvas.configure(cursor="fleur")
            return
        point = self._canvas_normalized_point(event)
        if point is None:
            return
        if self._drawing_zone_points is not None:
            if len(self._drawing_zone_points) >= 12:
                self._set_message(
                    "Máximo 12 vértices. Pulsa Finalizar.", error=True
                )
                return
            self._drawing_zone_points.append(point)
            self._refresh_zone_overlay()
            self._set_message(
                f"Vértice {len(self._drawing_zone_points)} agregado · "
                "pulsa Finalizar al completar la zona."
            )
            return
        if self._drawing_line_points is None:
            return
        self._drawing_line_points.append(point)
        if len(self._drawing_line_points) == 1:
            self._refresh_line_overlay()
            self._set_message("Ahora haz clic en el segundo punto de la línea.")
            return

        first, second = self._drawing_line_points[:2]
        if (
            (first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2
            < 0.0025
        ):
            self._drawing_line_points = []
            self._set_message(
                "La línea es demasiado corta. Selecciona dos puntos separados.",
                error=True,
            )
            return
        self.line_points = [first, second]
        self.line_defined = True
        self._drawing_line_points = None
        self.video_canvas.configure(cursor="arrow")
        self.line_enabled_var.set(True)
        self.config_data["line_enabled"] = True
        self.config_data["line_defined"] = True
        self.config_data["line_points"] = self.line_points
        save_config(self.config_data)
        if self.worker and self.worker.is_alive():
            self.worker.update_line(self.line_points, True)
        self._refresh_line_overlay()
        self._set_message("Línea de conteo guardada y activada.")

    def _on_video_drag(self, event):
        if self._drag_target is None:
            return
        point = self._canvas_normalized_point(
            event, clamp=True, report_error=False
        )
        if point is None:
            return
        kind, index = self._drag_target
        if kind == "line":
            self.line_points[index] = point
            self._refresh_line_overlay()
        else:
            self.zone_points[index] = point
            self._refresh_zone_overlay()

    def _on_video_release(self, _event):
        if self._drag_target is None:
            return
        kind, _ = self._drag_target
        original = self._drag_original_points
        self._drag_target = None
        self._drag_original_points = None
        self.video_canvas.configure(cursor="arrow")

        if kind == "line":
            first, second = self.line_points
            valid = (
                (first[0] - second[0]) ** 2
                + (first[1] - second[1]) ** 2
            ) >= 0.0025
            if not valid and original:
                self.line_points = original
                self._refresh_line_overlay()
                self._set_message(
                    "La línea es demasiado corta; se restauró el trazo anterior.",
                    error=True,
                )
                return
            self.line_points = validated_line_points(
                self.line_points
            )
            self.config_data["line_points"] = self.line_points
            save_config(self.config_data)
            if self.worker and self.worker.is_alive():
                self.worker.update_line(
                    self.line_points, bool(self.line_enabled_var.get())
                )
            self._refresh_line_overlay()
            self._set_message("Punto de la línea actualizado.")
            return

        contour = np.asarray(self.zone_points, dtype=np.float32)
        if abs(cv2.contourArea(contour)) < 0.0025 and original:
            self.zone_points = original
            self._refresh_zone_overlay()
            self._set_message(
                "La zona quedó demasiado pequeña; se restauró la anterior.",
                error=True,
            )
            return
        self.config_data["zone_points"] = self.zone_points
        save_config(self.config_data)
        if self.worker and self.worker.is_alive():
            self.worker.update_zone(
                self.zone_points,
                bool(self.zone_enabled_var.get()),
                bool(self.high_danger_var.get()),
            )
        self._refresh_zone_overlay()
        self._set_message("Vértice de la zona actualizado.")

    def _on_video_motion(self, event):
        if (
            self._drawing_zone_points is not None
            or self._drawing_line_points is not None
        ):
            self.video_canvas.configure(cursor="crosshair")
            return
        cursor = "fleur" if self._find_drag_target(event) else "arrow"
        if self.video_canvas.cget("cursor") != cursor:
            self.video_canvas.configure(cursor=cursor)

    def _on_video_canvas_configure(self, event):
        self.video_canvas.coords(
            self.video_placeholder_item, event.width / 2, event.height / 2
        )
        size = (max(int(event.width), 2), max(int(event.height), 2))
        if (
            size == self._stable_video_canvas_size
            and not self._video_resize_pending
        ):
            return
        self._video_resize_pending = True
        overlay_items = [
            self.zone_overlay_item,
            *self.zone_vertex_items,
            self.line_shadow_item,
            self.line_overlay_item,
            *self.line_endpoint_items,
            *[
                item
                for badge in self.line_badge_items
                for item in badge
            ],
            self.line_first_point_item,
        ]
        for item in overlay_items:
            self.video_canvas.itemconfigure(item, state="hidden")
        if self._video_resize_after_id is not None:
            try:
                self.after_cancel(self._video_resize_after_id)
            except tk.TclError:
                pass
        self._video_resize_after_id = self.after(
            140, self._finish_video_resize
        )

    def _finish_video_resize(self):
        self._video_resize_after_id = None
        if self._closing or self.state() == "iconic":
            return
        self._stable_video_canvas_size = (
            max(self.video_canvas.winfo_width(), 2),
            max(self.video_canvas.winfo_height(), 2),
        )
        self._video_resize_pending = False
        packet = self._deferred_frame_packet
        preview = self._deferred_preview_packet
        self._deferred_frame_packet = None
        self._deferred_preview_packet = None
        if packet is not None:
            self._display_packet(
                packet, render_main=preview is None
            )
        if preview is not None:
            self._display_preview(preview)
        elif packet is None and self.last_raw_frame is not None:
            self.live_image = self._render_canvas_frame(
                self.video_canvas,
                self.video_image_item,
                self.video_placeholder_item,
                self.last_raw_frame,
                self.live_image,
                main_video=True,
            )

    def _refresh_line_overlay(self):
        if not hasattr(self, "line_overlay_item"):
            return
        x0, y0, width, height = self.video_display_rect
        points = self.line_points
        drawing = self._drawing_line_points
        if drawing is not None and len(drawing) == 1:
            points = [self._drawing_line_points[0], self._drawing_line_points[0]]
        visible = (
            self.line_defined and bool(self.line_enabled_var.get())
        ) or (
            drawing is not None and len(drawing) > 0
        )
        line_items = [
            self.line_shadow_item,
            self.line_overlay_item,
            self.line_first_point_item,
            *self.line_endpoint_items,
            *[
                item
                for badge in self.line_badge_items
                for item in badge
            ],
        ]
        if (
            not visible
            or len(points) != 2
            or width <= 1
            or height <= 1
        ):
            for item in line_items:
                self.video_canvas.itemconfigure(item, state="hidden")
            return
        coordinates = [
            x0 + points[0][0] * width,
            y0 + points[0][1] * height,
            x0 + points[1][0] * width,
            y0 + points[1][1] * height,
        ]
        self.video_canvas.itemconfigure(
            self.line_shadow_item, state="hidden"
        )
        self.video_canvas.coords(self.line_overlay_item, *coordinates)
        self.video_canvas.itemconfigure(
            self.line_overlay_item, state="normal"
        )
        endpoints = (
            (coordinates[0], coordinates[1]),
            (coordinates[2], coordinates[3]),
        )
        for index, (x, y) in enumerate(endpoints):
            endpoint = self.line_endpoint_items[index]
            self.video_canvas.coords(
                endpoint, x - 5, y - 5, x + 5, y + 5
            )
            self.video_canvas.itemconfigure(endpoint, state="normal")
        if drawing is not None:
            for badge in self.line_badge_items:
                for item in badge:
                    self.video_canvas.itemconfigure(item, state="hidden")
            x, y = coordinates[0], coordinates[1]
            self.video_canvas.coords(
                self.line_first_point_item, x - 5, y - 5, x + 5, y + 5
            )
            self.video_canvas.itemconfigure(
                self.line_first_point_item, state="normal"
            )
        else:
            self.video_canvas.itemconfigure(
                self.line_first_point_item, state="hidden"
            )
            delta_x = coordinates[2] - coordinates[0]
            delta_y = coordinates[3] - coordinates[1]
            line_length = max(
                (delta_x ** 2 + delta_y ** 2) ** 0.5, 1.0
            )
            positive_normal = (
                -delta_y / line_length,
                delta_x / line_length,
            )
            # Coincide con la lógica de conteo: en líneas verticales A queda
            # del lado positivo; en horizontales, del lado negativo.
            a_sign = 1 if abs(delta_y) >= abs(delta_x) else -1
            middle = (
                (coordinates[0] + coordinates[2]) / 2,
                (coordinates[1] + coordinates[3]) / 2,
            )
            for index, sign in enumerate((a_sign, -a_sign)):
                badge_x = middle[0] + positive_normal[0] * 32 * sign
                badge_y = middle[1] + positive_normal[1] * 32 * sign
                badge_x = min(max(badge_x, x0 + 13), x0 + width - 13)
                badge_y = min(max(badge_y, y0 + 13), y0 + height - 13)
                badge_oval, badge_text = self.line_badge_items[index]
                self.video_canvas.coords(
                    badge_oval,
                    badge_x - 11, badge_y - 11,
                    badge_x + 11, badge_y + 11,
                )
                self.video_canvas.coords(badge_text, badge_x, badge_y)
                self.video_canvas.itemconfigure(badge_oval, state="hidden")
                self.video_canvas.itemconfigure(badge_text, state="normal")

    def _refresh_zone_overlay(self):
        if not hasattr(self, "zone_overlay_item"):
            return
        x0, y0, width, height = self.video_display_rect
        drawing = self._drawing_zone_points
        points = drawing if drawing is not None else self.zone_points
        high_danger = bool(self.high_danger_var.get())
        visible = bool(self.zone_enabled_var.get()) or bool(drawing)
        if not visible or not points or width <= 1 or height <= 1:
            self.video_canvas.itemconfigure(
                self.zone_overlay_item, state="hidden"
            )
            for item in self.zone_vertex_items:
                self.video_canvas.itemconfigure(item, state="hidden")
            return
        canvas_points = [
            (x0 + point[0] * width, y0 + point[1] * height)
            for point in points
        ]
        polygon_points = list(canvas_points)
        while len(polygon_points) < 3:
            polygon_points.append(polygon_points[-1])
        coordinates = [
            coordinate
            for point in polygon_points
            for coordinate in point
        ]
        self.video_canvas.coords(self.zone_overlay_item, *coordinates)
        self.video_canvas.itemconfigure(
            self.zone_overlay_item,
            state="normal",
            fill=(
                "#c72f3b" if high_danger else "#d27b22"
            ) if len(points) >= 3 else "",
            outline="#ff6672" if high_danger else "#ffb45b",
        )
        for index, item in enumerate(self.zone_vertex_items):
            if index < len(canvas_points):
                x, y = canvas_points[index]
                self.video_canvas.coords(
                    item, x - 6, y - 6, x + 6, y + 6
                )
                self.video_canvas.itemconfigure(item, state="normal")
                self.video_canvas.itemconfigure(
                    item,
                    outline="#ff6672" if high_danger else "#ffb45b",
                )
            else:
                self.video_canvas.itemconfigure(item, state="hidden")
