# Instalación en una PC nueva con Windows 11

Guía validada para **Windows 11 de 64 bits**, Python 3.14.6 y Arzyz Vision
versión 20. La instalación base usa CPU y funciona aunque la computadora no
tenga una tarjeta NVIDIA.

## Requisitos físicos y de red

- Windows 11 de 64 bits actualizado.
- Procesador x64, 8 GB de RAM como mínimo; 16 GB recomendados.
- 10 GB libres para Python, dependencias, modelos y crecimiento inicial.
- Conexión de red a la cámara. Para RTSP deben ser accesibles la IP y el puerto
  configurados (normalmente TCP 554).
- Navegador Microsoft Edge, incluido con Windows 11.

No se necesita instalar Git, Node.js, Visual Studio, FFmpeg ni CUDA Toolkit para
la instalación base.

## Instalación completa desde CMD como administrador

Abre **Símbolo del sistema como administrador** y ejecuta los bloques en orden.

### 1. Instalar Microsoft Visual C++ Runtime x64

```bat
cd /d %TEMP%
curl.exe -L -o vc_redist.x64.exe https://aka.ms/vc14/vc_redist.x64.exe
start /wait vc_redist.x64.exe /install /quiet /norestart
```

Fuente oficial:
https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist

### 2. Instalar Python 3.14.6 x64

```bat
cd /d %TEMP%
curl.exe -L -o python-3.14.6-amd64.exe https://www.python.org/ftp/python/3.14.6/python-3.14.6-amd64.exe
start /wait python-3.14.6-amd64.exe /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
```

Fuente oficial:
https://www.python.org/downloads/windows/

Cierra CMD, abre uno nuevo como administrador y verifica:

```bat
py -3.14 --version
```

Debe mostrar `Python 3.14.6`.

### 3. Descomprimir Arzyz Vision

Copia `Arzyz_Vision_v20.zip` a `C:\Instaladores` y ejecuta:

```bat
mkdir C:\ArzyzVision
tar.exe -xf C:\Instaladores\Arzyz_Vision_v20.zip -C C:\ArzyzVision
cd /d C:\ArzyzVision
```

### 4. Crear el entorno aislado

```bat
py -3.14 -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip setuptools wheel
```

### 5. Instalar PyTorch para CPU

Este comando funciona en cualquier PC x64:

```bat
python -m pip install torch==2.13.0 torchvision==0.28.0 --index-url https://download.pytorch.org/whl/cpu
```

Fuente y selector oficial de PyTorch:
https://pytorch.org/get-started/locally/

### 6. Instalar las dependencias exactas de la aplicación

```bat
python -m pip install -r requirements.txt
```

Ultralytics recomienda instalar primero PyTorch y después Ultralytics:
https://docs.ultralytics.com/quickstart/

### 7. Verificar toda la instalación

```bat
python -c "import tkinter,cv2,customtkinter,PIL,numpy,torch,torchvision,ultralytics,lap; print('ARZYZ VISION OK'); print('Python y dependencias OK'); print('CUDA:',torch.cuda.is_available())"
python -m py_compile centro_control.py detector_empresarial.py
```

### 8. Iniciar

```bat
cd /d C:\ArzyzVision
iniciar_centro_control.bat
```

También se puede crear un acceso directo de
`C:\ArzyzVision\iniciar_centro_control.bat` en el escritorio.

## Opción NVIDIA

La instalación CPU anterior es la ruta universal. Si la PC tiene GPU NVIDIA,
primero instala el controlador recomendado para el modelo exacto desde:

https://www.nvidia.com/Download/index.aspx

Luego abre el selector oficial de PyTorch, elige Windows, Pip, Python y una
versión CUDA compatible:

https://pytorch.org/get-started/locally/

Activa el entorno y sustituye PyTorch CPU por el comando que entregue ese
selector:

```bat
cd /d C:\ArzyzVision
call .venv\Scripts\activate.bat
REM Pegar aquí el comando exacto generado por el selector oficial de PyTorch.
python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CUDA NO DISPONIBLE')"
```

No instales CUDA Toolkit por separado salvo que el selector o el administrador
del hardware lo requieran. Los paquetes oficiales de PyTorch incluyen las
bibliotecas de ejecución; sí se necesita un controlador NVIDIA compatible.

### Comando verificado en la laptop G15 (19-ago-2026)

Equipo: Dell G15, NVIDIA GeForce RTX 3050 Laptop (4 GB), controlador 610.60
con CUDA 13.3. Se usan las mismas versiones que en CPU, cambiando sólo el
build; por eso no hace falta desinstalar nada antes: pip reemplaza el paquete.

```bat
cd /d C:\ArzyzVision
call .venv\Scripts\activate.bat
python -m pip install --index-url https://download.pytorch.org/whl/cu130 torch==2.13.0+cu130 torchvision==0.28.0+cu130
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Debe imprimir `2.13.0+cu130 True NVIDIA GeForce RTX 3050 Laptop GPU`.

Para volver a CPU (la PC de planta) se instala el build contrario, sin tocar
código:

```bat
python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.13.0+cpu torchvision==0.28.0+cpu
```

## Componentes exactos instalados por `requirements.txt`

- CustomTkinter 5.2.2
- NumPy 2.4.4
- OpenCV Python 4.13.0.92
- Pillow 12.2.0
- Ultralytics 8.4.103
- LAP 0.5.13
- PyTorch 2.13.0 y Torchvision 0.28.0, instalados en el paso anterior
