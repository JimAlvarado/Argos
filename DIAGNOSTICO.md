# Diagnostico — como generarlo y que mide

## Como generarlo

```bat
cd /d "C:\Users\jim.alvarado\OneDrive - Arzyz Metals\Documentos\Deteccion\Nueva estructuracion"
call .venv\Scripts\activate.bat
python -m tools.diagnostico
```

Deja el reporte en **`data\diagnostico\`** con dos archivos:

- `diagnostico_AAAAMMDD_HHMM.txt` — legible, es el que hay que compartir.
- `diagnostico_AAAAMMDD_HHMM.json` — los mismos datos para procesarlos.

Genera el reporte **despues** de dejar el detector corriendo un rato con la
camara real. Sobre una base vacia no hay nada que analizar.

No contiene contrasenas ni imagenes: es texto plano y se puede compartir.

## Que analiza

| Seccion | Para que sirve |
|---|---|
| Entorno | Version de Python, OpenCV, PyTorch, Ultralytics y si hay CUDA |
| Configuracion efectiva | Los valores reales con los que corrio, no los supuestos |
| Configuracion del rastreador | Los umbrales de ByteTrack y si son coherentes |
| Eventos por dia | Volumen real de actividad |
| Detecciones por evento | Histograma que revela cajas duplicadas |
| **Estabilidad del seguimiento** | El analisis clave (ver abajo) |
| Clases detectadas | Que esta reconociendo el modelo |
| Evidencias en disco | Cuantas imagenes y cuanto espacio ocupan |
| **Actividad por fuente y modelo** | Separa lo de cada camara y cada modulo |
| **Almacenamiento** | Espacio libre, crecimiento de evidencias, carpetas sincronizadas |
| **Salud de los modulos** | Ultimo latido y reinicios del supervisor |
| Fallas recientes | Las ultimas 25 entradas del registro de errores |
| Resumen | Hallazgos detectados automaticamente |

## La metrica que mas importa: rotacion de identidad

```
Objetos simultaneos (mediana)          3
Identidades distintas registradas     21
Rotacion de identidad                7.0x
```

Se lee asi: hay **3 personas** en la toma, pero el sistema registro **21
identidades**. Cada persona real cambio de identificador unas 7 veces.

- **Cerca de 1.0x** — el seguimiento es estable, cada objeto conserva su
  identidad. Los conteos son confiables.
- **3.0x o mas** — el rastreador pierde objetos y los vuelve a dar de alta como
  nuevos. Todo conteo acumulado queda inflado.

Es la diferencia entre "hubo 3 personas" y "hubo 21 personas", que para un
sistema cuyo fin es la recopilacion de datos reales lo cambia todo.

## Que revisar en el reporte

1. **Resumen** — los hallazgos automaticos estan ahi.
2. **Rotacion de identidad** — si supera 3x, los datos analiticos no son fiables
   todavia.
3. **Detecciones por evento** — si aparecen valores por encima del numero real
   de personas, hay cajas duplicadas y hay que bajar `iou`.
4. **Fallas recientes** — cualquier entrada merece atencion.

## Refuerzo tras un congelamiento en planta

El 12 de agosto el modulo de objetos conto unas 15 piezas y se cerro solo. El
reporte registro el sintoma pero no la causa. Estas secciones se agregaron para
que la proxima vez el diagnostico apunte directo al problema.

### Carpetas sincronizadas

El proyecto vivia dentro de **OneDrive**. Ese servicio bloquea archivos mientras
los sube, y el latido del modulo se escribe **cada segundo** en esa misma
carpeta. Cuando la sincronizacion se satura, el latido se retrasa, el supervisor
lo da por congelado y reinicia el modulo. En el registro quedo asi:

```
supervisor | objetos congelado (latido hace 11s); se termina
supervisor | objetos reiniciado automaticamente (intento 1)
```

El reporte ahora detecta OneDrive, Dropbox, Google Drive, iCloud y Nextcloud, y
recomienda mover el proyecto a una ruta local como `C:\Arzyz\Vision`.

### Crecimiento de evidencias

Se medieron 68 imagenes ocupando 43.7 MB: **658 KB cada una**. Al ritmo real de
950 piezas por hora eso son **611 MB por hora** y **4.8 GB por turno**.

El reporte ahora proyecta ese crecimiento y calcula cuantos turnos caben en el
disco libre. Ademas el modulo guarda las evidencias a media escala, cuatro veces
mas ligeras y igual de legibles para verificar.

### Actividad separada por fuente

Antes, los eventos del detector de personas y los del modulo de objetos se
mezclaban, y la rotacion de identidad se atribuia a ambos. El modulo de objetos
cuenta por cruce de linea y **no usa identidades**, asi que ese analisis solo
aplica al detector de personas. Ahora el reporte lo dice con nombre y apellido:

```
Rotacion de identidad 15.0x en RTSP 172.22.5.15
```

### Salud de los modulos

Ultimo latido de cada modulo y cuantas veces el supervisor tuvo que intervenir,
separando congelamientos de reinicios. Un modulo que se reinicia solo puede
pasar desapercibido; ahora queda contado.
