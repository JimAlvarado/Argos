# Arzyz Vision

Detector de objetos de escritorio basado en YOLO, preparado como primera versión
operativa para monitoreo continuo.

## Centro de Control

La plataforma incluye un menú principal local en HTML con cuatro módulos:
Detección Facial, Detección de Placas, Detección de Vehículos y Detección de
Personas. El módulo de Personas abre el detector actual en una ventana
maximizada y proceso independiente. Los otros módulos están preparados
visualmente para incorporarse progresivamente sin concentrar todas las
inferencias en una misma ventana.

Para abrir el menú principal, usa:

```text
iniciar_centro_control.bat
```

El Centro de Control abre `http://127.0.0.1:8765` solamente en el equipo local.
El dashboard consulta la base SQLite existente y presenta eventos del día,
objetos, cruces, alertas, actividad semanal, clases y registros recientes. La
interfaz no realiza inferencia y su consumo de recursos es mínimo.

El dashboard mantiene una conexión local en vivo y refleja cambios de SQLite en
menos de un segundo, sin recargar la página. Las tarjetas **Objetos**,
**Cruces** y **Alertas de zona** abren una galería local con sus últimas
capturas. Cada cruce y alerta nuevos guardan automáticamente una evidencia para
que estas galerías siempre tengan respaldo visual.

PyTorch y Ultralytics se cargan en segundo plano después de presentar la ventana
del detector. El calentamiento se ejecuta al pulsar **Iniciar**, antes de abrir
la cámara, para que el módulo quede interactivo cuanto antes sin perder cuadros.

## Funciones incluidas

- Cámara local, cámara IP/RTSP y archivos de video.
- Reconexión automática cuando una cámara IP pierde señal.
- Carga y validación de modelos YOLO `.pt` sin modificar el código.
- Selector de clases con acciones para todas, ninguna y sólo personas.
- Tracking persistente ByteTrack con identificadores por objeto.
- Persistencia visual breve para reducir el parpadeo de cajas intermitentes.
- Asociación geométrica de respaldo para que un parpadeo no rompa el conteo.
- Línea configurable con dos clics y conteo bidireccional A → B / B → A.
- Conteo total y por clase, con registro de cruces en SQLite.
- Umbral de confianza ajustable.
- Resolución de inferencia seleccionable: 640, 960 o 1280 píxeles.
- Video anotado, contador total, desglose por clase, FPS y latencia.
- Mosaico con hasta cuatro detecciones simultáneas.
- Registro persistente en SQLite.
- Evidencias JPEG automáticas y capturas manuales.
- La contraseña RTSP nunca se guarda en el archivo de configuración.
- Interfaz e inferencia separadas para evitar congelamientos.

## Inicio rápido

Para instalar en una computadora que sólo tiene Windows 11 consulta
[`INSTALAR_WINDOWS_11.md`](INSTALAR_WINDOWS_11.md). Incluye enlaces oficiales,
comandos CMD, versiones exactas, validación y las variantes CPU/NVIDIA.

1. Instala Python 3.14 de 64 bits.
2. Abre una terminal en esta carpeta.
3. Crea un entorno, instala primero PyTorch y después las dependencias:

   ```bat
   py -3.14 -m venv .venv
   call .venv\Scripts\activate.bat
   python -m pip install torch==2.13.0 torchvision==0.28.0 --index-url https://download.pytorch.org/whl/cpu
   python -m pip install -r requirements.txt
   ```

4. Coloca al menos un modelo `.pt` en `modelos`. Esta entrega incluye
   `modelos/yolov8n.pt`.
5. Ejecuta:

   ```bat
   iniciar_centro_control.bat
   ```

El acceso directo usa automáticamente `.venv` cuando existe.

## Uso

1. Selecciona la fuente de video.
2. Para RTSP, selecciona Axis, Provision ISR o Hikvision y captura IP, usuario y
   contraseña. El puerto 554 y la ruta se completan automáticamente.
   En Provision ISR la aplicación prueba automáticamente `profile1`, `profile2`
   y `profile3`; el RTSP debe estar habilitado en la configuración de red de la
   cámara y el usuario debe tener permiso de visualización.
3. Ajusta la confianza. Para la cámara Full HD de prueba se recomienda 25–30%.
4. Pulsa **Iniciar detección**.
5. Para usar un modelo entrenado, detén la detección y pulsa
   **Cargar modelo .pt**.
6. Para limitar lo detectado, pulsa **Elegir clases**. También puedes usar
   **Solo personas** para desactivar todas las clases excepto `person`.
7. En **Reglas y alertas**, selecciona **Cruce de línea**, actívalo, pulsa
   **Trazar** y selecciona dos puntos. La aplicación mostrará A y B.
8. Para vigilar un área, selecciona **Zona de alerta**, pulsa
   **Dibujar polígono**, marca al menos tres vértices y pulsa **Finalizar**.
   Marca **Zona de alto peligro** cuando el área requiera una alarma repetitiva.
9. Para ajustar una regla ya guardada, arrastra directamente sus puntos sobre
   el video. El cambio se guarda al soltar el botón del mouse.

El botón **Borrar** de cruce de línea elimina el trazo, desactiva la regla y
reinicia los conteos de la sesión sin borrar el historial almacenado.

La opción **Guardar evidencias** se puede activar o desactivar mientras la
detección está funcionando; el cambio se aplica inmediatamente.
Los interruptores **Activar conteo direccional**, **Activar vigilancia de zona**
y **Zona de alto peligro** también permanecen operativos durante la detección y
actualizan el procesamiento en vivo.

Los controles **Iniciar detección**, **Detener** y **Captura manual** están
debajo del video para reservar el panel izquierdo a la configuración. Los
relojes visibles usan formato de 12 horas con indicador AM/PM.

El filtro de clases se aplica dentro de la predicción YOLO: las clases desactivadas
no generan cuadros, conteos ni eventos. La selección se conserva entre ejecuciones
y se valida nuevamente cuando cambia el modelo.

La resolución 960 es el ajuste equilibrado recomendado para cámaras Full HD. Usa
640 si el CPU no mantiene los FPS necesarios y 1280 cuando los objetos estén muy
lejos o sean pequeños y exista capacidad de procesamiento suficiente.

## Conteo por línea

ByteTrack asigna un ID temporal a cada objeto. Un cruce sólo se registra cuando el
centro cambia de lado dentro del segmento dibujado, demuestra desplazamiento real
y supera una banda muerta contra vibración. Los objetos fuera de los extremos del
segmento no se cuentan. La pantalla muestra:

- Total de cruces de la sesión.
- Dirección A → B.
- Dirección B → A.
- Conteo por clase y último cruce.

Los cruces también se guardan en la tabla `crossings` de
`data/detecciones.db`. Para una banda transportadora coloca la línea perpendicular
al movimiento, usa una cámara fija y selecciona únicamente la clase que se desea
contar.

En una línea vertical, A es el lado izquierdo y B el derecho. En una línea
horizontal, A es la parte superior y B la inferior.

## Zona de alerta

La zona es un polígono semitransparente de hasta 12 vértices. La aplicación
evalúa cada objeto con seguimiento y genera una alerta cuando el ID entra desde
fuera; permanecer dentro no repite la alerta cuadro por cuadro.

Al activar **Zona de alto peligro**, el mismo polígono cambia a rojo
semitransparente. Se puede elegir entre cuatro patrones continuos: **Doble
pitido**, **Triple urgente**, **Sirena alternada** y **Pulso rápido**. También se
puede cargar un archivo **MP3 personalizado**, reservado exclusivamente para
esta zona y reproducido en bucle mediante el motor multimedia de Windows.

El sonido se detiene automáticamente cuando todos los objetos salen, cuando se
desactiva la zona o al detener la detección. Los pitidos se ejecutan fuera del
hilo de la interfaz y el MP3 se reproduce de forma asíncrona para no reducir la
fluidez del video. La ruta del MP3 se guarda en la configuración; si el archivo
se mueve o deja de existir, la aplicación utiliza **Doble pitido** como respaldo.

Cada entrada genera:

- aviso emergente rojo, compacto, semitransparente y siempre visible;
- pitido breve reproducido de forma asíncrona;
- imagen dentro de `alertas_zona`;
- registro SQLite en `zone_alerts`;
- fila `ALERTA_ZONA` en el CSV diario;
- entrada visible en el registro de eventos.

El aviso sólo muestra `Objeto detectado` y el tipo de objeto. Permanece en
pantalla hasta pulsar **Cerrar** o la tecla **Esc**.

La línea de conteo se dibuja como una guía delgada. Las letras **A** y **B**, sin
círculos, aparecen a cada lado del segmento —no en sus extremos— y coinciden con
la lógica usada para registrar las direcciones A → B y B → A. Sus dos extremos,
al igual que todos los vértices del polígono, se pueden reposicionar mediante
arrastre incluso mientras la detección está funcionando.

El modelo incluido confundía la banqueta de la cámara de prueba con `surfboard`
alrededor de 30–45% de confianza. La aplicación exige 65% únicamente para esa
clase, conservando la sensibilidad general necesaria para vehículos pequeños.

Para lingotes será necesario entrenar un modelo `best.pt` con imágenes reales de
la colada continua. El modelo COCO incluido no conoce la clase industrial
`lingote`.

## Modelos `.pt` compatibles

El aplicativo acepta pesos válidos de Ultralytics para:

- **detección** (`detect`);
- **segmentación** (`segment`);
- **pose** (`pose`);
- **cajas orientadas** (`obb`);
- **clasificación** (`classify`).

Los modelos de detección de rostros, placas, vehículos u objetos industriales
funcionan cuando el archivo contiene un checkpoint real de Ultralytics. Cambiar
la extensión de otro archivo a `.pt` no lo convierte en un modelo compatible.
La aplicación detecta descargas fallidas pequeñas, páginas `404`, archivos
dañados y dependencias faltantes, y conserva el último modelo funcional si el
nuevo archivo no puede cargarse.

La clasificación analiza la escena completa y por ello no admite conteo por
cruce de línea. Las tareas de detección, segmentación, pose y OBB sí permiten
seguimiento y conteo. No cargues archivos `.pt` de procedencia desconocida: los
checkpoints de PyTorch deben tratarse como archivos ejecutables y obtenerse sólo
de fuentes confiables.

Fuentes recomendadas:

- Modelos oficiales: https://docs.ultralytics.com/tasks/detect/
- Familias y tareas YOLO11: https://docs.ultralytics.com/models/yolo11/
- Releases oficiales: https://github.com/ultralytics/assets/releases
- Modelos propios o públicos de Ultralytics Platform:
  https://docs.ultralytics.com/platform/explore/

Para CPU comienza con variantes `n` (nano). Las variantes `s`, `m`, `l` y `x`
mejoran progresivamente la capacidad, pero consumen más memoria y reducen los FPS.
Un `best.pt` entrenado con imágenes reales de la planta será normalmente más útil
que un modelo genérico más grande.

Para uso interno empresarial o software privado, revisa antes del despliegue las
condiciones vigentes de Ultralytics:
https://www.ultralytics.com/license

Los datos se almacenan dentro de `data/`:

- `data/detecciones.db`: historial SQLite.
- `data/evidencias/AÑO/MES/DÍA/FUENTE/`: evidencia por fecha y cámara.
- `data/config.json`: preferencias sin contraseña.

Dentro de cada fuente se crean `detecciones/`, `capturas_manuales/` y
`alertas_zona/`. Cada día también contiene
`registro_eventos_AAAA-MM-DD.csv`, codificado como UTF-8 con BOM y separado por
comas. La primera línea `sep=,` indica expresamente a Excel cómo distribuir las
11 columnas, incluso si la configuración regional usa otro separador. El CSV
unifica detecciones, cruces, alertas de zona y capturas manuales.

El botón **CSV** abre el archivo del día y **Evidencias** abre la carpeta raíz.
Al iniciar esta versión, las carpetas antiguas `AAAA-MM-DD` se migran en segundo
plano, se actualizan sus rutas en SQLite y se reconstruyen los CSV históricos
sin duplicar registros.

## Consideraciones para 24/7

### Video en vivo y modelos pesados

La captura de cámara está separada de la inferencia: conserva únicamente el
cuadro más reciente y actualiza la vista hasta 30 veces por segundo. Así no se
acumula un búfer ni se reproduce la cámara en cámara lenta cuando un modelo tarda.
Las cajas se conservan brevemente entre inferencias para evitar parpadeos.

Al minimizar la aplicación se siguen capturando y analizando cuadros, pero se
omite el costoso renderizado de la interfaz. Durante maximizar o restaurar, el
reescalado del video se aplaza hasta que Windows termina de cambiar el tamaño;
se conserva sólo el cuadro más reciente y se dibuja una vez en las dimensiones
finales. Las proporciones de los paneles permanecen fijas para evitar saltos de
geometría.

La tarjeta **Inferencia** muestra los FPS del modelo, no los FPS de la cámara.
El control **FPS objetivo de análisis** permite 10, 15, 20, 30 o 60 FPS. Para
cámaras normales usa 30; 60 sólo aporta valor cuando la fuente realmente entrega
60 FPS y el hardware puede sostenerlos.
En un equipo sin GPU, `yolov8x.pt` puede tardar más de un segundo por cuadro:
la imagen seguirá en vivo, pero las cajas y el contador sólo podrán actualizarse
cuando termine cada inferencia. Para conteo en tiempo real sobre CPU se recomienda
`yolov8n.pt` o `yolov8s.pt`; reserva `x` para GPU NVIDIA, análisis de archivos o
escenarios donde la precisión importe más que la latencia. La aplicación reduce
automáticamente a 640 la resolución de inferencia de modelos pesados en CPU.

Cuando PyTorch detecta una GPU NVIDIA/CUDA, la aplicación la selecciona
automáticamente, activa precisión FP16 y optimizaciones de cuDNN. El modelo se
calienta antes de habilitar el inicio para que el primer vehículo rápido no se
pierda por la latencia de arranque. En CPU se conserva FP32.

Para objetos rápidos, el seguimiento auxiliar predice la siguiente posición y
mantiene el mismo ID aunque un vehículo cambie temporalmente entre las etiquetas
`car`, `truck`, `bus` o `van`. El cruce puede confirmarse con dos detecciones
separadas y conserva las protecciones contra vibración y dobles conteos.

La fluidez no corrige un modelo fuera de dominio. Si el modelo no reconoce el
objeto en cuadros individuales, aumentar los FPS no creará la detección. Valida
cada modelo con video real de la cámara y mide recall, falsos positivos y
detecciones consecutivas antes de usarlo para conteo.

La captura en vivo es propiedad exclusiva del hilo lector. Esto evita que
OpenCV/FFmpeg libere una cámara mientras todavía ejecuta una lectura, condición
que puede cerrar `python.exe` sin generar un traceback. Las conexiones RTSP
también tienen tiempos límite de apertura y lectura para poder detenerse o
reconectarse de manera segura.

- Usa el substream RTSP para visualización y el stream principal sólo cuando la
  precisión lo requiera.
- En Windows configura el equipo para no suspenderse y ejecuta la aplicación con
  el Programador de tareas al iniciar sesión.
- Vigila el crecimiento de `data/evidencias`. La siguiente fase debe incorporar
  retención automática por días o por espacio disponible.
- Un CPU funciona para pruebas, pero varias cámaras requieren una GPU NVIDIA y una
  versión de PyTorch compatible con CUDA.

## Camino a calidad empresarial

1. **Datos propios:** definir clases, condiciones de aceptación y un proceso de
   etiquetado/revisión. Separar entrenamiento, validación y prueba por cámara y
   fecha para evitar métricas engañosas.
2. **Métricas:** medir precisión, recall, mAP, falsos positivos por hora y tiempo
   de detección. Establecer metas por clase y por escenario.
3. **Seguimiento:** complementar ByteTrack, cruce y zona existentes con
   permanencia, velocidad y validación específica por proceso.
4. **Arquitectura:** separar captura, inferencia, eventos y panel; contenerizar el
   motor de inferencia y administrar múltiples cámaras desde un servicio central.
5. **Operación:** servicio de Windows, watchdog externo, health checks, logs
   rotativos, telemetría, alertas y retención configurable.
6. **Seguridad:** secretos en Windows Credential Manager/Vault, usuarios y roles,
   TLS, auditoría, segmentación de la red de cámaras y actualizaciones firmadas.
7. **Despliegue:** modelo versionado, pruebas de regresión, canary deployment y
   rollback. Exportar a ONNX/TensorRT cuando el hardware esté definido.
8. **Gobierno:** política de privacidad, enmascaramiento cuando aplique, tiempos de
   conservación y trazabilidad de cada modelo/dataset.

## Alcance actual

Esta versión es funcional como estación de una cámara. Para llamarla plataforma
empresarial aún deben agregarse tracking, reglas de negocio, retención, monitoreo
externo, seguridad centralizada y soporte multi-cámara.
