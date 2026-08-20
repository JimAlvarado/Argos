# Análisis de vehículos a velocidad — 28 de julio de 2026

## Grabación

- Archivo de referencia: `data/video_referencia_velocidad.mp4`
- Resolución de pantalla: 1636 × 914
- Duración: 17.43 segundos
- Frecuencia: 30 FPS
- Cuadros: 523

## Revisión del 29 de julio de 2026

Grabación analizada: `data/video_referencia_20260729_fluidez.mp4`.

- Duración: 14.13 segundos a 30 FPS.
- La interfaz reportó aproximadamente 6.4–6.9 inferencias por segundo sobre CPU.
- `vehicle_kitti_v0_last.pt` detectó en 2 de 8 muestras a 25% de confianza.
- Bajar KITTI hasta 5% sólo mejoró a 3 de 8; el problema es de dominio, no de
  sensibilidad.
- `yolov8n.pt` detectó en las 8 muestras y produjo 16 cajas.
- `yolo11n.pt` detectó en las 8 muestras y produjo 21 cajas, con un costo
  ligeramente mayor en este equipo.

Se restauró `yolov8n.pt` a 25%, 640 píxeles y NMS por clase como perfil operativo
equilibrado. La predicción visual ahora mueve únicamente el centro por un máximo
de 1.8% de la imagen y 0.12 segundos; nunca extrapola las esquinas por separado.
Las cajas perdidas se conservan como máximo 0.55 segundos en la vista fluida y
tres ciclos de inferencia en la vista procesada, sin alterar su tamaño.

## Diagnóstico

En la grabación, la aplicación reporta aproximadamente 8.8–9.5 FPS de
inferencia con `vehicle_kitti_v0_last.pt`. La vista en vivo continúa avanzando,
pero las cajas no aparecen sobre varios camiones, automóviles y autobuses
claramente visibles.

Se tomó una muestra cada 10 cuadros sobre el área de cámara de la grabación
(53 muestras). Esta evaluación está limitada por la recompresión de la captura
de pantalla, pero permite comparar ambos modelos bajo las mismas condiciones:

| Modelo | Confianza | Muestras con detección |
|---|---:|---:|
| `vehicle_kitti_v0_last.pt` | 40% | 0 de 53 |
| `yolov8n.pt` | 40% | 23 de 53 |
| `yolov8n.pt` | 30% | 35 de 53 |

El modelo KITTI tampoco respondió de manera útil al bajar el umbral. Por ello,
el problema principal de esta grabación es incompatibilidad de dominio/modelo,
no únicamente velocidad de procesamiento.

## Cambios aplicados

- Vista en vivo y refresco de interfaz elevados a 30 FPS.
- FPS objetivo configurable: 10, 15, 20, 30 y 60.
- Selección automática de GPU CUDA.
- Precisión FP16 y cuDNN optimizado cuando existe GPU.
- Calentamiento del modelo antes de iniciar.
- Inferencia configurada explícitamente para CPU o GPU.
- Seguimiento con predicción de velocidad.
- Asociación estable entre las etiquetas vehiculares `car`, `truck`, `bus`,
  `van`, `tram`, `motorcycle` y `bicycle`.
- Cruce rápido confirmable desde dos detecciones.
- Confianza operativa ajustada de 40% a 30% en la configuración actual.

## Recomendación para servidor

1. Usar una GPU NVIDIA con una instalación de PyTorch que muestre
   `torch.cuda.is_available() == True`.
2. Comenzar con un modelo nano o small a 640 y objetivo de 30 FPS.
3. Medir sobre el stream RTSP original, no sobre una grabación de pantalla.
4. Entrenar un modelo con imágenes de las cámaras, alturas, clima, compresión,
   desenfoque y velocidades reales de la planta.
5. Para lingotes, restringir la inferencia a la región de la banda y entrenar
   específicamente la clase `lingote`.
6. Exportar el modelo validado a TensorRT cuando se defina la GPU del servidor.

Un servidor potente aumenta los FPS, pero el modelo propio y la calidad del
stream determinan si el objeto puede reconocerse.
