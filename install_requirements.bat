@echo off
setlocal EnableExtensions EnableDelayedExpansion

title Instalador - YouTube Pixel Storage

echo ============================================================
echo  Instalador de dependencias - YouTube Pixel Storage
echo ============================================================
echo.
echo Este script intentara instalar:
echo   - FFmpeg
echo   - Deno
echo   - Paquetes Python: opencv-python, numpy, yt-dlp
echo.
echo Recomendado: ejecutar en una terminal normal de tu usuario.
echo No hace falta cerrar Chrome.
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python no esta en PATH.
    echo Instala Python desde https://www.python.org/downloads/
    echo Marca la casilla "Add Python to PATH" durante la instalacion.
    pause
    exit /b 1
)

echo [OK] Python detectado:
python --version
echo.

echo Actualizando pip...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo [AVISO] No se pudo actualizar pip, continuo igualmente.
)
echo.

echo Instalando paquetes Python...
python -m pip install -U opencv-python numpy yt-dlp
if errorlevel 1 (
    echo [ERROR] Fallo instalando paquetes Python.
    pause
    exit /b 1
)
echo.

where winget >nul 2>nul
if errorlevel 1 (
    echo [AVISO] winget no esta disponible.
    echo Instala FFmpeg y Deno manualmente si no los tienes.
    goto CHECK_TOOLS
)

echo Instalando FFmpeg con winget...
winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
if errorlevel 1 (
    echo [AVISO] winget no pudo instalar FFmpeg o ya estaba instalado.
)
echo.

echo Instalando Deno con winget...
winget install --id DenoLand.Deno -e --accept-source-agreements --accept-package-agreements
if errorlevel 1 (
    echo [AVISO] winget no pudo instalar Deno o ya estaba instalado.
)
echo.

:CHECK_TOOLS
echo ============================================================
echo  Comprobaciones
echo ============================================================
echo.

echo Comprobando yt-dlp...
python -m yt_dlp --version
if errorlevel 1 (
    echo [ERROR] yt-dlp no funciona correctamente.
) else (
    echo [OK] yt-dlp funciona mediante python -m yt_dlp.
)
echo.

echo Comprobando ffmpeg...
where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo [AVISO] ffmpeg no aparece en PATH en esta terminal.
    echo Cierra esta ventana y abre una nueva.
    echo Si sigue sin aparecer, instala FFmpeg manualmente y anade su carpeta bin al PATH.
) else (
    ffmpeg -version
)
echo.

echo Comprobando deno...
where deno >nul 2>nul
if errorlevel 1 (
    echo [AVISO] deno no aparece en PATH en esta terminal.
    echo Cierra esta ventana y abre una nueva.
    echo Si sigue sin aparecer, reinstala Deno con winget.
) else (
    deno --version
)
echo.

echo ============================================================
echo  Prueba recomendada de yt-dlp con YouTube
echo ============================================================
echo.
echo Cuando quieras probar un video:
echo yt-dlp --remote-components ejs:github -F "URL_DE_YOUTUBE"
echo.
echo Si eso lista formatos, el script tambien deberia poder descargarlo.
echo.

echo Instalacion finalizada.
echo IMPORTANTE: cierra esta terminal y abre una nueva para recargar PATH.
echo.
pause
endlocal
