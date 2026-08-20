"""Capturador de dataset: extrae cuadros utiles y propone las cajas.

Problema que resuelve: un video de una hora a 25 FPS son 90,000 cuadros, casi
todos identicos y con la banda vacia. Etiquetar eso a mano es inviable.

Este capturador hace tres cosas:

1. **Filtra.** Solo guarda cuadros donde hay material en el corredor. Los cientos
   de cuadros con la banda vacia se descartan.
2. **Evita repetidos.** Si el objeto casi no se movio desde el ultimo cuadro
   guardado, no vuelve a guardarlo. Un dataset con 300 fotos del mismo instante
   no ensena nada.
3. **Propone las cajas.** Detecta el material por brillo, movimiento y forma
   alargada, y escribe las etiquetas en formato YOLO. El operador **corrige**
   en vez de dibujar desde cero, que es entre cinco y diez veces mas rapido.

Uso:

    python -m tools.capturador video.mp4
    python -m tools.capturador video.mp4 --clase lingote --max 200

Salida en `data\\dataset\\`:

    images/   recortes de la region de interes, listos para entrenar
    labels/   etiquetas YOLO (una linea por objeto: clase cx cy w h)
    preview/  las mismas imagenes con las cajas dibujadas, para revisar
    dataset.yaml
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from core import paths  # noqa: E402
from core.pipeline.classic import Ajustes, detectar as _detectar  # noqa: E402

# Region de interes sobre el cuadro 4K, medida sobre la camara Lingotera.
# La camara puede reposicionarse desde su configuracion: si eso pasa, hay que
# recalibrar estos valores y el capturador lo avisa (ver comprobar_encuadre).
ROI_POR_DEFECTO = {"x": 1600, "y": 1140, "w": 560, "h": 1020}

# Los umbrales viven en core.pipeline.classic: el capturador y el modulo de
# deteccion comparten la MISMA implementacion. Duplicarla haria que el dataset
# dejara de corresponder con lo que ve produccion.
AJUSTES_DATASET = Ajustes(area_minima=1400, margen_superior=200, margen_inferior=140)


def _dimensiones(video: Path) -> tuple[int, int, float]:
    salida = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate",
         "-of", "json", str(video)],
        capture_output=True, text=True, check=True,
    )
    s = json.loads(salida.stdout)["streams"][0]
    num, _, den = s["r_frame_rate"].partition("/")
    return int(s["width"]), int(s["height"]), int(num) / int(den or 1)


def _leer_recortes(video: Path, roi: dict):
    """Devuelve los cuadros ya recortados a la region de interes.

    Se recorta en ffmpeg y no en Python: mover 4K completo por memoria para
    quedarse con el 7% del cuadro desperdicia tiempo y RAM.
    """
    w, h = roi["w"], roi["h"]
    orden = [
        "ffmpeg", "-v", "error", "-i", str(video),
        "-vf", f"crop={w}:{h}:{roi['x']}:{roi['y']}",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-",
    ]
    proceso = subprocess.Popen(orden, stdout=subprocess.PIPE, bufsize=10**8)
    tam = w * h * 3
    while True:
        crudo = proceso.stdout.read(tam)
        if len(crudo) < tam:
            break
        yield np.frombuffer(crudo, np.uint8).reshape(h, w, 3)
    proceso.stdout.close()
    proceso.wait()


def detectar(gris: np.ndarray, fondo: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Propone cajas de material usando el detector compartido.

    A resolucion completa del recorte (560x1020), por eso los ajustes son el
    doble que los del modulo, que procesa a media escala.
    """
    return _detectar(gris, fondo, AJUSTES_DATASET)


def comprobar_encuadre(gris: np.ndarray) -> str | None:
    """Avisa si la vista no se parece a la esperada.

    La camara puede reposicionarse desde su configuracion. Si eso ocurre, la
    region de interes deja de caer sobre la banda y el capturador guardaria
    basura sin que nadie lo note.
    """
    # Umbrales calibrados con material real: la banda muestra una desviacion
    # de 41 de dia y 61 de noche. Por debajo de 15 no hay estructura de banda.
    if gris.mean() < 12:
        return "La region esta casi negra: revisa que la camara siga encuadrada."
    if gris.std() < 15:
        return "La region no tiene estructura de banda: revisa el encuadre."
    return None


def capturar(video: Path, roi: dict, destino: Path, clase: str,
             maximo: int, minimo_desplazamiento: int) -> dict:
    ancho, alto, fps = _dimensiones(video)
    if roi["x"] + roi["w"] > ancho or roi["y"] + roi["h"] > alto:
        raise SystemExit(
            f"La region {roi} no cabe en un video de {ancho}x{alto}. "
            "Si moviste la camara, recalibra con --roi."
        )

    for sub in ("images", "labels", "preview"):
        (destino / sub).mkdir(parents=True, exist_ok=True)

    # Primera pasada: fondo por mediana. Con la banda vacia la mayor parte del
    # tiempo, la mediana es justamente la banda sin material.
    muestras = []
    for i, cuadro in enumerate(_leer_recortes(video, roi)):
        if i % 25 == 0:
            muestras.append(cv2.cvtColor(cuadro, cv2.COLOR_BGR2GRAY))
        if len(muestras) >= 80:
            break
    if not muestras:
        raise SystemExit("No se pudo leer el video.")
    fondo = np.median(np.stack(muestras), axis=0).astype(np.uint8)

    aviso = comprobar_encuadre(fondo)
    if aviso:
        print(f"  AVISO: {aviso}")

    guardadas = 0
    revisados = 0
    con_material = 0
    ultimo_centro = None
    marca = datetime.now().strftime("%Y%m%d%H%M")
    base = f"{video.stem[:28]}_{marca}"

    for indice, cuadro in enumerate(_leer_recortes(video, roi)):
        revisados += 1
        gris = cv2.cvtColor(cuadro, cv2.COLOR_BGR2GRAY)
        cajas = detectar(gris, fondo)
        if not cajas:
            continue
        con_material += 1

        # Evitar repetidos: solo se guarda si la escena cambio lo suficiente.
        centro = np.mean([[x + w / 2, y + h / 2] for x, y, w, h in cajas], axis=0)
        if ultimo_centro is not None:
            if np.hypot(*(centro - ultimo_centro)) < minimo_desplazamiento:
                continue
        ultimo_centro = centro

        nombre = f"{base}_{indice:06d}"
        cv2.imwrite(str(destino / "images" / f"{nombre}.jpg"), cuadro,
                    [cv2.IMWRITE_JPEG_QUALITY, 92])

        h_roi, w_roi = gris.shape
        lineas = []
        vista = cuadro.copy()
        for x, y, w, h in cajas:
            lineas.append(
                f"0 {(x + w / 2) / w_roi:.6f} {(y + h / 2) / h_roi:.6f} "
                f"{w / w_roi:.6f} {h / h_roi:.6f}"
            )
            cv2.rectangle(vista, (x, y), (x + w, y + h), (0, 255, 0), 3)
            cv2.putText(vista, clase, (x, max(y - 8, 14)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        (destino / "labels" / f"{nombre}.txt").write_text(
            "\n".join(lineas) + "\n", encoding="utf-8")
        cv2.putText(vista, f"t={indice / fps:.1f}s  {len(cajas)} objeto(s)",
                    (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 215, 255), 2)
        cv2.imwrite(str(destino / "preview" / f"{nombre}.jpg"), vista,
                    [cv2.IMWRITE_JPEG_QUALITY, 80])

        guardadas += 1
        if guardadas >= maximo:
            break

    (destino / "dataset.yaml").write_text(
        f"# Dataset generado por tools/capturador.py\n"
        f"path: {destino}\ntrain: images\nval: images\n\n"
        f"names:\n  0: {clase}\n", encoding="utf-8")

    return {
        "cuadros_revisados": revisados,
        "cuadros_con_material": con_material,
        "imagenes_guardadas": guardadas,
        "descartados_por_repetidos": con_material - guardadas,
        "region": roi,
        "fps": fps,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Genera dataset etiquetado a partir de un video.")
    p.add_argument("video", type=Path)
    p.add_argument("--clase", default="lingote")
    p.add_argument("--max", type=int, default=400, dest="maximo")
    p.add_argument("--separacion", type=int, default=25,
                   help="px minimos de desplazamiento entre imagenes guardadas")
    p.add_argument("--roi", default=None,
                   help="x,y,w,h si la camara se reposiciono")
    p.add_argument("--salida", type=Path, default=None)
    a = p.parse_args()

    if not a.video.is_file():
        raise SystemExit(f"No existe el video: {a.video}")

    roi = dict(ROI_POR_DEFECTO)
    if a.roi:
        x, y, w, h = (int(v) for v in a.roi.split(","))
        roi = {"x": x, "y": y, "w": w, "h": h}

    destino = a.salida or (paths.DATA_DIR / "dataset")
    print(f"\n  Video  : {a.video.name}")
    print(f"  Region : {roi['w']}x{roi['h']} en ({roi['x']},{roi['y']})")
    print(f"  Destino: {destino}\n")

    r = capturar(a.video, roi, destino, a.clase, a.maximo, a.separacion)

    print(f"  Cuadros revisados        : {r['cuadros_revisados']}")
    print(f"  Con material en la banda : {r['cuadros_con_material']}")
    print(f"  Descartados por repetidos: {r['descartados_por_repetidos']}")
    print(f"  IMAGENES GUARDADAS       : {r['imagenes_guardadas']}")
    print(f"\n  Revisa las cajas propuestas en: {destino / 'preview'}")
    print("  Corrige lo que haga falta y entrena con dataset.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
