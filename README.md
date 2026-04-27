# Pixel Storage Youtube

Proyecto experimental para **codificar archivos dentro de un vídeo** usando bloques de color, subirlo a YouTube y después intentar descargarlo y reconstruir el archivo original.

No usa QR. Convierte bytes en bloques de color dentro de frames de vídeo.

> Aviso serio: esto es un experimento técnico. YouTube recomprime los vídeos y puede romper datos. El programa verifica el archivo final con SHA-256: si coincide, el archivo recuperado es correcto; si no coincide, se ha corrompido.

---

## Qué hace

Flujo general:

```text
archivo original
↓
bytes
↓
bloques de color en frames 1080p
↓
vídeo MP4
↓
subida a YouTube
↓
descarga automática con yt-dlp
↓
descodificación
↓
archivo reconstruido
↓
verificación SHA-256
```

Cada bloque representa 2 bits:

```text
00 = negro
01 = rojo
10 = verde
11 = azul
blanco = relleno / fin de frame
```

---

## Archivo principal recomendado

Usa esta versión:

```text
youtube_pixel_storage_robust_auto_hw.py
```

O, si quieres la versión modificada para descargar siempre a máxima resolución:

```text
youtube_pixel_storage_auto_hw_maxres.py
```

La versión robusta está pensada para que sobreviva mejor a YouTube:

```text
Resolución: 1920x1080
Bloque: 8x8
FPS: 24
Repetición: x5
Codec: H.264
Calidad: alta
```

---

## Requisitos

Necesitas:

- Windows 10/11
- Python 3.10 o superior
- FFmpeg
- Deno
- yt-dlp
- OpenCV para Python
- NumPy

Dependencias Python:

```powershell
pip install opencv-python numpy yt-dlp
```

Herramientas externas:

```powershell
ffmpeg -version
deno --version
yt-dlp --version
```

---

## Instalación automática en Windows

Ejecuta:

```bat
install_requirements.bat
```

Ese `.bat` intenta instalar:

- FFmpeg usando `winget`
- Deno usando `winget`
- paquetes Python necesarios
- actualización de `yt-dlp`

Después cierra y abre una nueva terminal para que Windows recargue el `PATH`.

---

## Instalación manual

### 1. Instalar dependencias Python

```powershell
python -m pip install --upgrade pip
python -m pip install -U opencv-python numpy yt-dlp
```

### 2. Instalar FFmpeg

Opción fácil:

```powershell
winget install Gyan.FFmpeg
```

Comprueba:

```powershell
ffmpeg -version
```

Si no lo detecta, cierra y abre la terminal.

### 3. Instalar Deno

```powershell
winget install DenoLand.Deno
```

Comprueba:

```powershell
deno --version
```

Deno se usa porque YouTube puede exigir resolver desafíos JavaScript para que `yt-dlp` pueda listar o descargar ciertos vídeos.

---

## Cómo codificar un archivo

Ejecuta:

```powershell
python youtube_pixel_storage_robust_auto_hw.py
```

Elige:

```text
1) Codificar archivo a vídeo
```

El programa abrirá el explorador de Windows para seleccionar el archivo.

Luego te pedirá dónde guardar el vídeo `.mp4`.

Al terminar tendrás un vídeo listo para subir a YouTube.

---

## Cómo subirlo a YouTube

Sube el vídeo generado como un vídeo normal.

Recomendaciones:

```text
Visibilidad: No listado
Resolución: espera a que termine el procesamiento HD/1080p
Restricciones: ninguna
Audio: no importa
```

Es importante esperar a que YouTube termine de procesar la versión 1080p. Si descargas demasiado pronto, puedes obtener una versión de baja calidad y fallará la recuperación.

---

## Cómo descodificar desde YouTube

Ejecuta:

```powershell
python youtube_pixel_storage_auto_hw_maxres.py
```

Elige:

```text
2) Descodificar vídeo / enlace de YouTube a máxima resolución
1) Pegar enlace de YouTube, descargar máxima resolución y descodificar
```

Pega la URL:

```text
https://www.youtube.com/watch?v=ID_DEL_VIDEO
```

El programa hará:

```text
yt-dlp descarga el vídeo
↓
elige la máxima resolución disponible
↓
descodifica
↓
reconstruye el archivo
↓
verifica SHA-256
```

Si termina con:

```text
INTEGRIDAD OK
```

el archivo se recuperó correctamente.

Si termina con:

```text
INTEGRIDAD FALLIDA
```

YouTube modificó demasiado los bloques y el archivo recuperado no es idéntico al original.

---

## Descargar manualmente con yt-dlp

Para comprobar si `yt-dlp` ve un vídeo:

```powershell
yt-dlp --remote-components ejs:github -F "https://www.youtube.com/watch?v=ID_DEL_VIDEO"
```

Para descargar el mejor vídeo disponible:

```powershell
yt-dlp --remote-components ejs:github --force-ipv4 -f "bv*[ext=mp4]/bv*/bestvideo/best" --merge-output-format mp4 -o "video_descargado.%(ext)s" "https://www.youtube.com/watch?v=ID_DEL_VIDEO"
```

Luego puedes descodificarlo como archivo local desde el programa.

---

## Si yt-dlp dice “This video is not available”

Comprueba:

1. Que el vídeo esté en **No listado**, no en privado.
2. Que el procesamiento HD haya terminado.
3. Que puedas verlo desde una ventana de incógnito.
4. Que `yt-dlp` esté actualizado:

```powershell
python -m pip install -U yt-dlp
```

5. Que Deno esté instalado:

```powershell
deno --version
```

6. Usa:

```powershell
yt-dlp --remote-components ejs:github -F "URL"
```

---

## Hardware y rendimiento

El script intenta detectar y usar automáticamente el mejor backend de codificación:

```text
NVIDIA NVENC
Intel QuickSync
AMD AMF
CPU libx264
```

Para descodificación también prueba aceleración por hardware:

```text
CUDA/NVDEC
Intel QSV
D3D11VA
DXVA2
CPU FFmpeg decode
```

No siempre la GPU gana. En algunas máquinas la CPU descodifica más rápido que la GPU. El script hace pruebas cortas y elige el backend viable más rápido.

---

## CPU vs GPU

Regla práctica:

```text
Archivos pequeños → CPU robusto puede ser más fiable
Archivos grandes → GPU compensa mucho por velocidad
```

Para YouTube, lo más importante no es solo la velocidad, sino que el SHA-256 final coincida.

---

## Parámetros importantes

En el script robusto:

```python
PIXEL_SIZE = 8
FPS = 24
REPEAT = 5
CRF_CPU = 8
PRESET_CPU = "slow"
NVENC_QP = 8
AMF_QP = 8
QSV_QP = 8
```

Significado:

- `PIXEL_SIZE`: tamaño de cada bloque. Más grande = más robusto, menos capacidad.
- `REPEAT`: cuántas veces se repite cada frame lógico. Más repetición = más robusto, vídeo más largo.
- `CRF_CPU` / `QP`: calidad de codificación. Menor = más calidad y más peso.
- `PRESET_CPU`: `slow` suele conservar mejor, pero tarda más.

---

## Problemas conocidos

### El vídeo se descarga bien pero falla el SHA

YouTube recomprimió demasiado. Soluciones:

```text
- usar perfil más robusto
- aumentar REPEAT
- usar PIXEL_SIZE más grande
- bajar QP/CRF
- esperar a que YouTube procese 1080p
```

### Va muy lento

Normal: este sistema sacrifica velocidad por resistencia. Para archivos grandes se generan muchísimos frames.

### El archivo recuperado no tiene extensión

Al guardar, pon la extensión correcta:

```text
decoded_test.7z
decoded_archivo.zip
decoded_maquina.ova
```

Aunque la extensión no afecta al SHA, sí afecta a que Windows lo abra correctamente.

### yt-dlp no detecta Deno

Cierra y abre la terminal después de instalar Deno.

```powershell
deno --version
```

---

## Recomendación de uso

Para pruebas:

```text
1. Prueba con un .txt pequeño.
2. Luego con un .7z de pocos MB.
3. Sube a YouTube como No listado.
4. Espera procesamiento 1080p.
5. Descarga y descodifica.
6. Comprueba que sale INTEGRIDAD OK.
```

No empieces con una OVA grande. Primero valida que el flujo funciona con archivos pequeños.

---

## Limitación importante

Esto no sustituye a Drive, Mega, S3, Backblaze, Hetzner Storage Box ni almacenamiento real.

Es una prueba técnica interesante, pero YouTube no está diseñado para preservar bytes exactos dentro de un vídeo.
