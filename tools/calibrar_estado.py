"""Calibra la senal de una estacion contra un video real.

Es el trabajo que hay que hacer ANTES de activar una estacion: convertir un
video en los dos numeros que el motor necesita (el estado inactivo y el activo
medidos) y comprobar, sobre ese mismo video, que los intervalos que salen
coinciden con lo que se ve.

Sin este paso no se activa nada: un umbral inventado da datos que parecen buenos
y no lo son, que es exactamente lo que este proyecto no puede permitirse.

Uso:

    python -m tools.calibrar_estado "C:\\ruta\\video.mp4" --senal rosado
    python -m tools.calibrar_estado "video.mp4" --estacion mantenedor
    python -m tools.calibrar_estado "video.mp4" --senal ocupacion ^
        --region 0.26,0.26,0.52,0.16 --hz 2

Deja en `data\\calibracion\\<nombre>\\`:

  senal.csv        la serie completa, para revisarla en Excel
  region.jpg       un cuadro con la region dibujada: PRIMERO verificar esto
  t*_<estado>.jpg  cuadros en cada transicion, para contrastar con la imagen

El orden importa: si `region.jpg` no cae donde debe, todo lo demas sobra.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

from core import paths
from core.camera import abrir_fuente
from core.pipeline.estados import Histeresis, MaquinaDeEstado
from core.pipeline.senales import (
    ESTACIONES,
    SENALES,
    construir_senal,
    recortar,
)
from core.utils import formato_duracion

ANCHO_REGISTRO = 640


def _region_desde_texto(texto: str) -> dict:
    partes = texto.split(",")
    if len(partes) != 4:
        raise argparse.ArgumentTypeError(
            "La region va como x,y,w,h en fracciones de 0 a 1, "
            "por ejemplo 0.46,0.43,0.30,0.12")
    try:
        x, y, w, h = (float(p) for p in partes)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"Valores no numericos en la region: {texto}") from error
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1):
        raise argparse.ArgumentTypeError(
            f"La region {texto} sale del cuadro (todo va entre 0 y 1)")
    return {"x": x, "y": y, "w": w, "h": h}


def _dos_estados(valores: np.ndarray) -> tuple[float, float]:
    """Separa la senal en sus dos estados y devuelve (inactivo, activo).

    Un paso de k-medias con dos grupos, partiendo del punto medio del rango. Se
    usa la MEDIA de cada grupo y no percentiles del total: los percentiles
    dependen de cuanto duro cada estado, y ese sesgo ya causo un umbral malo
    (con la puerta abierta el 84% del video, el umbral de apertura caia dentro
    de la distribucion de "abierta").
    """
    centro = (valores.min() + valores.max()) / 2
    for _ in range(25):
        bajos, altos = valores[valores <= centro], valores[valores > centro]
        if bajos.size == 0 or altos.size == 0:
            break
        nuevo = (bajos.mean() + altos.mean()) / 2
        if abs(nuevo - centro) < 1e-9:
            break
        centro = nuevo
    bajos, altos = valores[valores <= centro], valores[valores > centro]
    inactivo = float(bajos.mean()) if bajos.size else float(valores.min())
    activo = float(altos.mean()) if altos.size else float(valores.max())
    return inactivo, activo


def main(argv=None) -> int:
    analizador = argparse.ArgumentParser(
        description="Calibra la senal de una estacion contra un video real.")
    analizador.add_argument("video", help="Archivo de video a analizar")
    analizador.add_argument(
        "--estacion", choices=sorted(ESTACIONES),
        help="Toma region y senal de una estacion ya definida")
    analizador.add_argument(
        "--senal", choices=sorted(SENALES),
        help="Senal a medir (obligatoria si la estacion no la trae)")
    analizador.add_argument("--region", type=_region_desde_texto,
                            help="x,y,w,h en fracciones del cuadro")
    analizador.add_argument("--hz", type=float, default=2.0,
                            help="Muestras por segundo (por omision 2)")
    analizador.add_argument("--permanencia", type=float, default=3.0,
                            help="Segundos que un estado debe sostenerse")
    argumentos = analizador.parse_args(argv)

    ajustes = ESTACIONES.get(argumentos.estacion or "", {})
    region = argumentos.region or ajustes.get("region")
    if region is None:
        analizador.error("Falta --region o --estacion para deducirla.")
    if argumentos.senal:
        medir = construir_senal(SENALES[argumentos.senal])
        nombre_senal = argumentos.senal
    elif ajustes.get("senal") is not None:
        # Las senales con memoria se declaran como clase; hay que instanciarlas.
        medir = construir_senal(ajustes["senal"])
        nombre_senal = ajustes["origen"].split(":")[-1]
    else:
        analizador.error(
            "Falta --senal. La estacion elegida todavia no tiene una asignada, "
            "que es precisamente lo que hay que averiguar aqui.")

    ruta = Path(argumentos.video)
    if not ruta.is_file():
        print(f"No existe el video: {ruta}", file=sys.stderr)
        return 2

    salida = paths.DATA_DIR / "calibracion" / ruta.stem[:60]
    salida.mkdir(parents=True, exist_ok=True)

    captura, _ = abrir_fuente(str(ruta))
    fps = captura.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(captura.get(cv2.CAP_PROP_FRAME_COUNT))
    salto = max(1, int(round(fps / max(argumentos.hz, 0.1))))

    print(f"video      {ruta.name}")
    print(f"           {int(captura.get(cv2.CAP_PROP_FRAME_WIDTH))}x"
          f"{int(captura.get(cv2.CAP_PROP_FRAME_HEIGHT))}  "
          f"{fps:.2f} fps  {total / fps / 60:.1f} min")
    print(f"senal      {nombre_senal}")
    print(f"region     {region}")
    print(f"muestreo   {fps / salto:.2f} Hz\n")

    tiempos, valores, desplazamientos = [], [], []
    previo = None
    ventana = None
    caja = None
    numero = 0
    while True:
        if not captura.grab():
            break
        if numero % salto == 0:
            ok, cuadro = captura.retrieve()
            if not ok:
                break
            recorte, caja = recortar(cuadro, region)
            tiempos.append(numero / fps)
            valores.append(float(medir(recorte)))

            chico = cv2.resize(cuadro, (ANCHO_REGISTRO, int(
                cuadro.shape[0] * ANCHO_REGISTRO / cuadro.shape[1])))
            gris = cv2.cvtColor(chico, cv2.COLOR_BGR2GRAY).astype(np.float32)
            if ventana is None:
                ventana = cv2.createHanningWindow(
                    (gris.shape[1], gris.shape[0]), cv2.CV_32F)
                marcado = cuadro.copy()
                cv2.rectangle(marcado, caja[:2], caja[2:], (0, 255, 255), 4)
                cv2.imwrite(str(salida / "region.jpg"), marcado,
                            [cv2.IMWRITE_JPEG_QUALITY, 90])
            if previo is not None:
                # Consecutivas y no contra una referencia fija: contra una
                # referencia fija esto mide el cambio de ESCENA, no el de la
                # camara, y da cientos de pixeles con la camara quieta.
                (dx, dy), _ = cv2.phaseCorrelate(previo, gris, ventana)
                desplazamientos.append(float(np.hypot(dx, dy)))
            else:
                desplazamientos.append(0.0)
            previo = gris
        numero += 1
    captura.release()

    if len(valores) < 10:
        print("Muy pocas muestras para calibrar.", file=sys.stderr)
        return 1

    t = np.asarray(tiempos)
    v = np.asarray(valores)
    d = np.asarray(desplazamientos)

    print(f"muestras   {len(v)}")
    print(f"\nsenal: min {v.min():+.2f}  p25 {np.percentile(v, 25):+.2f}  "
          f"p50 {np.median(v):+.2f}  p75 {np.percentile(v, 75):+.2f}  "
          f"max {v.max():+.2f}")
    print(f"camara: desplazamiento p50 {np.median(d):.3f}  max {d.max():.3f} px")
    if d.max() > 2.0:
        print("  AVISO: la camara se movio durante la grabacion. Los tramos "
              "movidos no sirven para calibrar.")

    inactivo, activo = _dos_estados(v)
    separacion = abs(activo - inactivo)
    print(f"\ndos estados separados de la senal:")
    print(f"  inactivo  {inactivo:+.2f}")
    print(f"  activo    {activo:+.2f}")
    print(f"  separacion {separacion:.2f}")
    if separacion < 3 * (v.std() or 1e-9) / 2:
        print("  AVISO: los dos grupos casi se tocan. Puede que en este video "
              "solo haya UN estado, o que la senal elegida no discrimine.")

    histeresis = Histeresis.desde_estados_medidos(inactivo, activo)
    print(f"\nhisteresis derivada:  entra > {histeresis.entra:+.2f}   "
          f"sale < {histeresis.sale:+.2f}")

    maquina = MaquinaDeEstado(
        histeresis, permanencia=argumentos.permanencia,
        nombre_activo=ajustes.get("nombre_activo", "activo"),
        nombre_inactivo=ajustes.get("nombre_inactivo", "inactivo"))
    intervalos = []
    for momento, valor in zip(t, v):
        terminado = maquina.actualizar(float(valor), float(momento))
        if terminado is not None:
            intervalos.append(terminado)
    pendiente = maquina.cerrar(float(t[-1]))
    if pendiente is not None:
        intervalos.append(pendiente)

    print(f"\nintervalos sobre este video:")
    print(f"  {'estado':<12} {'inicio':>9} {'cierre':>9} {'duracion':>10}  notas")
    for i in intervalos:
        notas = ", ".join(
            n for n, activo_ in (("parcial", i.parcial),
                                 ("con hueco", i.con_hueco)) if activo_)
        print(f"  {i.estado:<12} {i.inicio:9.1f} {i.fin:9.1f} "
              f"{formato_duracion(i.duracion):>10}  {notas}")

    nombre_activo = ajustes.get("nombre_activo", "activo")
    activos = [i for i in intervalos if i.estado == nombre_activo]
    if activos:
        suma = sum(i.duracion for i in activos)
        print(f"\n  veces {nombre_activo}: {len(activos)}   "
              f"tiempo total: {formato_duracion(suma)}   "
              f"({suma / t[-1] * 100:.1f}% del video)")

    # Cuadros en cada transicion: el numero no vale nada hasta contrastarlo con
    # la imagen. Se guardan antes y despues de cada cambio.
    captura, _ = abrir_fuente(str(ruta))
    for i in intervalos[1:]:
        for etiqueta, segundos in (("antes", i.inicio - 2), ("despues", i.inicio + 2)):
            if segundos < 0 or segundos > t[-1]:
                continue
            captura.set(cv2.CAP_PROP_POS_FRAMES, int(segundos * fps))
            ok, cuadro = captura.read()
            if not ok:
                continue
            cv2.rectangle(cuadro, caja[:2], caja[2:], (0, 255, 255), 4)
            escala = 1100 / cuadro.shape[1]
            if escala < 1:
                cuadro = cv2.resize(cuadro, None, fx=escala, fy=escala,
                                    interpolation=cv2.INTER_AREA)
            cv2.imwrite(
                str(salida / f"t{int(segundos):05d}_{etiqueta}_{i.estado}.jpg"),
                cuadro, [cv2.IMWRITE_JPEG_QUALITY, 88])
    captura.release()

    np.savetxt(salida / "senal.csv",
               np.column_stack([t, v, d]), delimiter=",",
               header="t_s,senal,desplazamiento_px", comments="", fmt="%.5f")

    print(f"\nrevisa en {salida}")
    print("  1. region.jpg      el recuadro DEBE caer donde se quiere medir")
    print("  2. t*_antes/despues  el estado debe coincidir con la imagen")
    print("  3. senal.csv       la serie completa")
    print(f"\nsi cuadra, en core/pipeline/senales.py deja "
          f"inactivo_medido={inactivo:.2f} y activo_medido={activo:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
