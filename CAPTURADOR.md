# Capturador de dataset

Convierte un video de la camara en imagenes etiquetadas listas para entrenar.

## Uso

```bat
python -m tools.capturador "C:\ruta\Lingotera_dia.mp4"
python -m tools.capturador "C:\ruta\Lingotera_noche.mp4" --roi 1544,1140,560,1020
```

Salida en `data\dataset\`:

| Carpeta | Contenido |
|---|---|
| `images/` | Recortes de la region de interes, listos para entrenar |
| `labels/` | Etiquetas YOLO (`clase cx cy w h`, normalizadas) |
| `preview/` | Las mismas imagenes con las cajas dibujadas, **para revisar** |
| `dataset.yaml` | Configuracion para el entrenamiento |

## Que hace y por que

Un video de una hora a 25 FPS son 90,000 cuadros, casi todos identicos.
Etiquetar eso a mano es inviable. El capturador:

1. **Filtra.** Solo conserva cuadros con material en la banda.
2. **Evita repetidos.** Si el objeto casi no se movio, no guarda otra copia.
3. **Propone las cajas.** El operador **corrige** en vez de dibujar desde cero,
   entre cinco y diez veces mas rapido.

Detecta por brillo, movimiento y **forma alargada**. Esa tercera condicion es la
que descarta el vapor, que es brillante y se mueve pero es difuso.

## Resultado medido sobre 60 s reales

| | Dia (11-ago) | Noche (6-ago) |
|---|---|---|
| Cuadros revisados | 1,500 | 1,501 |
| Con material | 1,275 | 1,408 |
| Descartados por repetidos | 1,076 | 1,159 |
| **Imagenes guardadas** | **199** | **249** |
| Objetos por imagen | 1.97 | 1.60 |

448 imagenes etiquetadas de dos minutos de video.

## Si se mueve la camara

La camara puede reposicionarse desde su configuracion. Cuando eso pasa, la
region de interes deja de caer sobre la banda. El capturador lo comprueba y
avisa; para corregir se pasa la region nueva con `--roi x,y,w,h`.

Entre el 6 y el 11 de agosto la camara se movio 56 px en horizontal, por eso el
video de noche se procesa con `--roi 1544,1140,560,1020`.

## Antes de etiquetar: la regla acordada

**Solo cuenta la pieza completa.** Un lingote cortado por el borde del cuadro no
se cuenta. Al revisar en `preview/`, borra las cajas de piezas incompletas.

## Siguiente paso

Revisar `preview/`, corregir en CVAT o Label Studio (locales, las imagenes no
salen de la planta) y entrenar:

```bat
yolo detect train data=data\dataset\dataset.yaml model=yolov8n.pt epochs=100 imgsz=640
```
