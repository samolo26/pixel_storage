@echo off
setlocal EnableExtensions EnableDelayedExpansion

title Instalador Pixel Storage - FFmpeg en C:\ffmpeg

echo ============================================================
echo  Instalador Pixel Storage
echo  FFmpeg fijo en C:\ffmpeg\bin\ffmpeg.exe
echo ============================================================
echo.
echo Este instalador hace:
echo   1. Comprueba Python.
echo   2. Instala paquetes Python: opencv-python, numpy, yt-dlp.
echo   3. Descarga FFmpeg full build desde gyan.dev.
echo   4. Extrae FFmpeg en C:\ffmpeg.
echo   5. Anade C:\ffmpeg\bin al PATH de usuario.
echo   6. Comprueba ffmpeg -version.
echo.
echo IMPORTANTE:
echo   - Se recomienda ejecutar como administrador para poder escribir en C:\ffmpeg.
echo   - Si no lo ejecutas como administrador y falla, vuelve a abrirlo como admin.
echo   - Al terminar, cierra y abre PowerShell/CMD de nuevo.
echo.

pause

REM ============================================================
REM Comprobar Python
REM ============================================================

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo [ERROR] Python no esta en PATH.
    echo Instala Python desde:
    echo https://www.python.org/downloads/
    echo.
    echo Marca la opcion "Add Python to PATH" durante la instalacion.
    pause
    exit /b 1
)

echo.
echo [OK] Python detectado:
python --version

echo.
echo Actualizando pip...
python -m pip install --upgrade pip

echo.
echo Instalando dependencias Python...
python -m pip install -U opencv-python numpy yt-dlp
if errorlevel 1 (
    echo.
    echo [ERROR] Fallo instalando dependencias Python.
    pause
    exit /b 1
)

REM ============================================================
REM Preparar rutas
REM ============================================================

set "INSTALL_DIR=C:\ffmpeg"
set "FFMPEG_BIN=C:\ffmpeg\bin"
set "FFMPEG_EXE=C:\ffmpeg\bin\ffmpeg.exe"
set "TEMP_DIR=%TEMP%\ffmpeg_install_pixel_storage"
set "ZIP_FILE=%TEMP_DIR%\ffmpeg-release-full.7z"
set "EXTRACT_DIR=%TEMP_DIR%\extract"
set "FFMPEG_URL=https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-full.7z"

echo.
echo ============================================================
echo  Instalando FFmpeg en C:\ffmpeg
echo ============================================================

if exist "%FFMPEG_EXE%" (
    echo [OK] FFmpeg ya existe en:
    echo   %FFMPEG_EXE%
    goto ADD_PATH
)

echo.
echo Creando carpeta temporal...
if exist "%TEMP_DIR%" rmdir /s /q "%TEMP_DIR%"
mkdir "%TEMP_DIR%"
mkdir "%EXTRACT_DIR%"

echo.
echo Descargando FFmpeg full build...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri '%FFMPEG_URL%' -OutFile '%ZIP_FILE%'"

if not exist "%ZIP_FILE%" (
    echo.
    echo [ERROR] No se pudo descargar FFmpeg.
    echo Descargalo manualmente desde:
    echo https://www.gyan.dev/ffmpeg/builds/
    pause
    exit /b 1
)

REM ============================================================
REM Buscar 7-Zip o instalarlo si no existe
REM ============================================================

set "SEVENZIP=C:\Program Files\7-Zip\7z.exe"

if exist "%SEVENZIP%" (
    echo [OK] 7-Zip detectado.
    goto EXTRACT_FFMPEG
)

echo.
echo 7-Zip no detectado. Descargando e instalando 7-Zip...
set "SEVENZIP_INSTALLER=%TEMP_DIR%\7zip.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://www.7-zip.org/a/7z2409-x64.exe' -OutFile '%SEVENZIP_INSTALLER%'"

if not exist "%SEVENZIP_INSTALLER%" (
    echo.
    echo [ERROR] No se pudo descargar 7-Zip.
    echo Instala 7-Zip manualmente y vuelve a ejecutar este instalador.
    pause
    exit /b 1
)

"%SEVENZIP_INSTALLER%" /S

if not exist "%SEVENZIP%" (
    echo.
    echo [ERROR] 7-Zip no se instalo correctamente.
    echo Instala 7-Zip manualmente desde https://www.7-zip.org/
    pause
    exit /b 1
)

:EXTRACT_FFMPEG
echo.
echo Extrayendo FFmpeg...
"%SEVENZIP%" x "%ZIP_FILE%" -o"%EXTRACT_DIR%" -y >nul

if errorlevel 1 (
    echo.
    echo [ERROR] No se pudo extraer FFmpeg.
    pause
    exit /b 1
)

echo.
echo Buscando carpeta extraida...
set "FOUND_BIN="

for /f "delims=" %%F in ('dir /s /b "%EXTRACT_DIR%\ffmpeg.exe" 2^>nul') do (
    set "FOUND_BIN=%%~dpF"
    goto FOUND_EXTRACTED
)

echo.
echo [ERROR] No se encontro ffmpeg.exe dentro del archivo extraido.
pause
exit /b 1

:FOUND_EXTRACTED
if "%FOUND_BIN:~-1%"=="\" set "FOUND_BIN=%FOUND_BIN:~0,-1%"

echo [OK] ffmpeg.exe encontrado en:
echo   %FOUND_BIN%

echo.
echo Instalando en C:\ffmpeg...
if exist "%INSTALL_DIR%" rmdir /s /q "%INSTALL_DIR%"
mkdir "%INSTALL_DIR%"
xcopy "%FOUND_BIN%\*" "%FFMPEG_BIN%\" /E /I /Y >nul

if not exist "%FFMPEG_EXE%" (
    echo.
    echo [ERROR] No se pudo copiar FFmpeg a:
    echo   %FFMPEG_EXE%
    echo.
    echo Prueba a ejecutar este .bat como administrador.
    pause
    exit /b 1
)

echo [OK] FFmpeg instalado en:
echo   %FFMPEG_EXE%

REM ============================================================
REM Anadir C:\ffmpeg\bin al PATH de usuario
REM ============================================================

:ADD_PATH
echo.
echo ============================================================
echo  Anadiendo C:\ffmpeg\bin al PATH de usuario
echo ============================================================

for /f "tokens=2,*" %%A in ('reg query HKCU\Environment /v Path 2^>nul') do set "USER_PATH=%%B"

echo %USER_PATH% | find /I "%FFMPEG_BIN%" >nul
if not errorlevel 1 (
    echo [OK] C:\ffmpeg\bin ya estaba en el PATH de usuario.
) else (
    echo [INFO] Anadiendo C:\ffmpeg\bin al PATH de usuario...

    if defined USER_PATH (
        setx Path "%USER_PATH%;%FFMPEG_BIN%" >nul
    ) else (
        setx Path "%FFMPEG_BIN%" >nul
    )

    if errorlevel 1 (
        echo.
        echo [ERROR] No se pudo modificar el PATH.
        pause
        exit /b 1
    )

    echo [OK] PATH de usuario actualizado.
)

REM Actualizar PATH de esta ventana.
set "PATH=%PATH%;%FFMPEG_BIN%"

REM ============================================================
REM Comprobaciones
REM ============================================================

echo.
echo ============================================================
echo  Comprobaciones finales
echo ============================================================

echo.
echo Ejecutable FFmpeg esperado:
echo   %FFMPEG_EXE%

echo.
echo where ffmpeg:
where ffmpeg

echo.
echo ffmpeg -version:
"%FFMPEG_EXE%" -version

echo.
echo Encoders importantes:
"%FFMPEG_EXE%" -hide_banner -encoders | findstr /i "amf nvenc qsv libx264 mpeg4 h264_mf"

echo.
echo yt-dlp:
python -m yt_dlp --version

echo.
echo ============================================================
echo  Instalacion finalizada
echo ============================================================
echo.
echo El codigo Python debe tener:
echo   FFMPEG_PATH = r"C:\ffmpeg\bin\ffmpeg.exe"
echo.
echo IMPORTANTE:
echo   Cierra esta ventana y abre una nueva terminal para que Windows recargue el PATH.
echo.
pause

endlocal