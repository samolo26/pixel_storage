#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
youtube_pixel_storage_auto_hw.py

Codificador/descodificador de archivos en vídeo usando bloques de color.
Inspirado en Brendan-Kirtlan/Video-Encode, pero con selección automática de hardware.

Objetivo:
- NO usa QR.
- Perfil único robusto para YouTube:
    1920x1080, pixelSize=8, repeat=x5, 24 FPS.
- Al codificar:
    1) detecta encoders FFmpeg disponibles: NVENC, QSV, AMF, CPU/libx264
    2) hace una prueba corta de benchmark real
    3) usa el backend más rápido que funcione
- Al descodificar:
    1) detecta decoders/aceleradores: CUDA/NVDEC, QSV, D3D11VA/DXVA2, CPU
    2) hace una prueba corta de lectura/decodificación
    3) usa el backend más rápido que funcione
- Explorador de archivos de Windows.
- Puede descargar desde YouTube con yt-dlp.
- Verifica SHA-256 al final.

Dependencias:
    pip install opencv-python numpy yt-dlp

Necesario:
    ffmpeg en PATH

Aviso:
    YouTube recomprime. No existe garantía perfecta.
    Si el SHA-256 coincide, el archivo recuperado es correcto.
"""

import os
import sys
import math
import time
import shutil
import subprocess
import tempfile
import json
import hashlib
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

try:
    import tkinter as tk
    from tkinter import filedialog
except Exception:
    tk = None
    filedialog = None


# ==========================
# PERFIL ÚNICO RÁPIDO/YOUTUBE
# ==========================
WIDTH = 1920
HEIGHT = 1080
PIXEL_SIZE = 8
FPS = 24
REPEAT = 5

# CPU fallback
CRF_CPU = 8
PRESET_CPU = "slow"

# GPU quality
NVENC_QP = 8
AMF_QP = 8
QSV_QP = 8

OUTPUT_SUFFIX = ".ytpixel_youtube_robust.mp4"

BLOCKS_X = WIDTH // PIXEL_SIZE
BLOCKS_Y = HEIGHT // PIXEL_SIZE
TOTAL_BLOCKS = BLOCKS_X * BLOCKS_Y
BYTES_PER_FRAME_RAW = TOTAL_BLOCKS // 4

META_MAGIC = b"YTPX3"
META_PREFIX_SIZE = 16
RESERVED_META_BYTES = 2048
PAYLOAD_BYTES_PER_FRAME = BYTES_PER_FRAME_RAW

# OpenCV usa BGR.
BLACK = np.array([0, 0, 0], dtype=np.uint8)
RED   = np.array([0, 0, 255], dtype=np.uint8)
GREEN = np.array([0, 255, 0], dtype=np.uint8)
BLUE  = np.array([255, 0, 0], dtype=np.uint8)
WHITE = np.array([255, 255, 255], dtype=np.uint8)
PALETTE = np.array([BLACK, RED, GREEN, BLUE], dtype=np.uint8)

try:
    cv2.setNumThreads(os.cpu_count() or 0)
except Exception:
    pass


@dataclass
class EncodeBackend:
    key: str
    name: str
    args: list[str]
    priority: int


@dataclass
class DecodeBackend:
    key: str
    name: str
    input_args: list[str]
    priority: int


def human_size(n: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    x = float(n)
    for u in units:
        if x < 1024 or u == units[-1]:
            return f"{x:.2f} {u}"
        x /= 1024


def run_capture(cmd: list[str], timeout: int = 15) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, errors="ignore", timeout=timeout)
        return (p.stdout or "") + "\n" + (p.stderr or "")
    except Exception:
        return ""


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def yt_dlp_available() -> bool:
    return shutil.which("yt-dlp") is not None


def ffmpeg_encoders() -> str:
    if not ffmpeg_available():
        return ""
    return run_capture(["ffmpeg", "-hide_banner", "-encoders"], timeout=20).lower()


def ffmpeg_hwaccels() -> str:
    if not ffmpeg_available():
        return ""
    return run_capture(["ffmpeg", "-hide_banner", "-hwaccels"], timeout=20).lower()


def get_encode_backends() -> list[EncodeBackend]:
    enc = ffmpeg_encoders()
    backends: list[EncodeBackend] = []

    # NVIDIA NVENC. Normalmente muy rápido y buena opción si existe.
    if "h264_nvenc" in enc:
        backends.append(EncodeBackend(
            key="nvenc",
            name="GPU NVIDIA NVENC h264_nvenc",
            priority=100,
            args=[
                "-c:v", "h264_nvenc",
                "-preset", "p5",
                "-tune", "hq",
                "-rc", "constqp",
                "-qp", str(NVENC_QP),
                "-pix_fmt", "yuv420p",
            ],
        ))

    # Intel Quick Sync. Muy útil si hay iGPU Intel.
    if "h264_qsv" in enc:
        backends.append(EncodeBackend(
            key="qsv",
            name="GPU Intel QuickSync h264_qsv",
            priority=90,
            args=[
                "-c:v", "h264_qsv",
                "-preset", "veryfast",
                "-global_quality", str(QSV_QP),
                "-pix_fmt", "nv12",
            ],
        ))

    # AMD AMF.
    if "h264_amf" in enc:
        backends.append(EncodeBackend(
            key="amf",
            name="GPU AMD AMF h264_amf",
            priority=80,
            args=[
                "-c:v", "h264_amf",
                "-quality", "speed",
                "-usage", "transcoding",
                "-qp_i", str(AMF_QP),
                "-qp_p", str(AMF_QP),
                "-qp_b", str(AMF_QP),
                "-pix_fmt", "yuv420p",
            ],
        ))

    # CPU siempre como fallback.
    backends.append(EncodeBackend(
        key="cpu",
        name="CPU libx264",
        priority=10,
        args=[
            "-c:v", "libx264",
            "-preset", PRESET_CPU,
            "-crf", str(CRF_CPU),
            "-pix_fmt", "yuv420p",
            "-threads", "0",
        ],
    ))

    return backends


def get_decode_backends() -> list[DecodeBackend]:
    hw = ffmpeg_hwaccels()
    backends: list[DecodeBackend] = []

    # En Windows, d3d11va/dxva2 suelen existir y pueden usar GPU del sistema.
    # CUDA/NVDEC si NVIDIA está soportada.
    if "cuda" in hw:
        backends.append(DecodeBackend(
            key="cuda",
            name="GPU NVIDIA CUDA/NVDEC",
            priority=100,
            input_args=["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"],
        ))

    if "qsv" in hw:
        backends.append(DecodeBackend(
            key="qsv",
            name="GPU Intel QuickSync decode",
            priority=90,
            input_args=["-hwaccel", "qsv"],
        ))

    if "d3d11va" in hw:
        backends.append(DecodeBackend(
            key="d3d11va",
            name="GPU Windows D3D11VA",
            priority=70,
            input_args=["-hwaccel", "d3d11va"],
        ))

    if "dxva2" in hw:
        backends.append(DecodeBackend(
            key="dxva2",
            name="GPU Windows DXVA2",
            priority=60,
            input_args=["-hwaccel", "dxva2"],
        ))

    # CPU siempre fallback.
    backends.append(DecodeBackend(
        key="cpu",
        name="CPU FFmpeg decode",
        priority=10,
        input_args=[],
    ))

    return backends


# Coordenadas de muestreo.
YS = np.arange(PIXEL_SIZE // 2, HEIGHT, PIXEL_SIZE)
XS = np.arange(PIXEL_SIZE // 2, WIDTH, PIXEL_SIZE)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(1024 * 1024 * 8)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def open_file_dialog(title: str, filetypes=None) -> Optional[Path]:
    if tk is None or filedialog is None:
        raw = input(f"{title} - ruta: ").strip().strip('"')
        return Path(raw).expanduser() if raw else None

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    if filetypes is None:
        filetypes = [("Todos los archivos", "*.*")]

    filename = filedialog.askopenfilename(title=title, filetypes=filetypes)
    root.destroy()

    if not filename:
        return None
    return Path(filename)


def save_file_dialog(title: str, default_name: str, default_ext: str = ".mp4") -> Optional[Path]:
    if tk is None or filedialog is None:
        raw = input(f"{title} [{default_name}]: ").strip().strip('"')
        return Path(raw).expanduser() if raw else Path(default_name)

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    filename = filedialog.asksaveasfilename(
        title=title,
        initialfile=default_name,
        defaultextension=default_ext,
        filetypes=[("MP4 video", "*.mp4"), ("Todos los archivos", "*.*")]
    )
    root.destroy()

    if not filename:
        return None
    return Path(filename)


def save_decoded_dialog(default_name: str) -> Optional[Path]:
    if tk is None or filedialog is None:
        raw = input(f"Guardar archivo recuperado como [{default_name}]: ").strip().strip('"')
        return Path(raw).expanduser() if raw else Path(default_name)

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    filename = filedialog.asksaveasfilename(
        title="Guardar archivo recuperado",
        initialfile=default_name,
        filetypes=[("Todos los archivos", "*.*")]
    )
    root.destroy()

    if not filename:
        return None
    return Path(filename)


def make_meta_bytes(input_path: Path) -> bytes:
    size = input_path.stat().st_size
    file_hash = sha256_file(input_path)
    total_data_frames = math.ceil(size / PAYLOAD_BYTES_PER_FRAME)

    meta = {
        "magic": META_MAGIC.decode("ascii"),
        "filename": input_path.name,
        "size": size,
        "sha256": file_hash,
        "width": WIDTH,
        "height": HEIGHT,
        "pixel_size": PIXEL_SIZE,
        "fps": FPS,
        "repeat": REPEAT,
        "bytes_per_frame": PAYLOAD_BYTES_PER_FRAME,
        "total_data_frames": total_data_frames,
        "profile": "youtube_robust_v4",
    }

    data = json.dumps(meta, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(data) > RESERVED_META_BYTES - META_PREFIX_SIZE:
        raise ValueError("Metadata demasiado grande.")

    prefix = META_MAGIC + len(data).to_bytes(4, "big") + b"\x00" * 7
    blob = prefix + data
    return blob.ljust(RESERVED_META_BYTES, b"\x00")


def parse_meta_bytes(raw: bytes) -> Optional[dict]:
    if len(raw) < META_PREFIX_SIZE:
        return None
    if raw[:5] != META_MAGIC:
        return None

    length = int.from_bytes(raw[5:9], "big")
    if length <= 0 or length > RESERVED_META_BYTES:
        return None

    data = raw[META_PREFIX_SIZE:META_PREFIX_SIZE + length]
    try:
        meta = json.loads(data.decode("utf-8"))
    except Exception:
        return None

    if meta.get("magic") != META_MAGIC.decode("ascii"):
        return None
    return meta


def bytes_to_frame(chunk: bytes) -> np.ndarray:
    if len(chunk) > PAYLOAD_BYTES_PER_FRAME:
        raise ValueError("Chunk mayor que capacidad de frame.")

    groups = np.full(TOTAL_BLOCKS, 255, dtype=np.uint8)

    if chunk:
        data = np.frombuffer(chunk, dtype=np.uint8)
        g = np.empty(data.size * 4, dtype=np.uint8)
        g[0::4] = (data >> 6) & 0b11
        g[1::4] = (data >> 4) & 0b11
        g[2::4] = (data >> 2) & 0b11
        g[3::4] = data & 0b11
        groups[:g.size] = g

    grid = np.empty((BLOCKS_Y, BLOCKS_X, 3), dtype=np.uint8)
    grid[:] = WHITE

    g2 = groups.reshape(BLOCKS_Y, BLOCKS_X)
    grid[g2 == 0] = BLACK
    grid[g2 == 1] = RED
    grid[g2 == 2] = GREEN
    grid[g2 == 3] = BLUE

    return np.repeat(np.repeat(grid, PIXEL_SIZE, axis=0), PIXEL_SIZE, axis=1)


def classify_block_centers_fast(sample: np.ndarray) -> np.ndarray:
    b = sample[:, :, 0].astype(np.int16)
    g = sample[:, :, 1].astype(np.int16)
    r = sample[:, :, 2].astype(np.int16)

    white = (b > 175) & (g > 175) & (r > 175)
    black = (b < 90) & (g < 90) & (r < 90)

    # Orden interno para mapear:
    # max B -> código azul = 3
    # max R -> código rojo = 1
    # max G -> código verde = 2
    stacked = np.stack([b, r, g], axis=2)
    dominant = stacked.argmax(axis=2)

    out = np.zeros(b.shape, dtype=np.uint8)
    out[dominant == 1] = 1
    out[dominant == 2] = 2
    out[dominant == 0] = 3

    out[black] = 0
    out[white] = 255
    return out


def classify_block_centers_distance(sample: np.ndarray) -> np.ndarray:
    s = sample.astype(np.int16)
    white = (s[:, :, 0] > 175) & (s[:, :, 1] > 175) & (s[:, :, 2] > 175)

    pal = PALETTE.astype(np.int16)
    d = ((s[:, :, None, :] - pal[None, None, :, :]) ** 2).sum(axis=3)
    nearest = d.argmin(axis=2).astype(np.uint8)
    nearest[white] = 255
    return nearest


def groups_to_bytes(groups: np.ndarray) -> bytes:
    groups = groups.reshape(-1)

    white_pos = np.where(groups == 255)[0]
    if white_pos.size:
        groups = groups[:white_pos[0]]

    usable = (groups.size // 4) * 4
    groups = groups[:usable]

    if groups.size == 0:
        return b""

    q = groups.reshape(-1, 4).astype(np.uint8)
    out = ((q[:, 0] << 6) | (q[:, 1] << 4) | (q[:, 2] << 2) | q[:, 3]).astype(np.uint8)
    return out.tobytes()


def frame_to_bytes(frame: np.ndarray, method: str = "fast") -> bytes:
    # frame ya llega 1920x1080 bgr24 desde FFmpeg. Por seguridad:
    if frame.shape[1] != WIDTH or frame.shape[0] != HEIGHT:
        frame = cv2.resize(frame, (WIDTH, HEIGHT), interpolation=cv2.INTER_AREA)

    sample = frame[np.ix_(YS, XS)]
    groups = classify_block_centers_distance(sample) if method == "distance" else classify_block_centers_fast(sample)
    return groups_to_bytes(groups)


def majority_vote(chunks: list[bytes]) -> bytes:
    """
    Votación por mayoría real para frames repetidos.

    Con REPEAT=5:
    - Si 3 o más copias coinciden, gana esa versión.
    - Si no hay mayoría clara, gana la versión más repetida.
    - Si todas difieren, se elige la más larga como último recurso.

    Esto no sustituye a ECC/Reed-Solomon, pero mejora mucho frente a repeat=2.
    """
    chunks = [c for c in chunks if c]
    if not chunks:
        return b""

    from collections import Counter
    counter = Counter(chunks)
    best, count = counter.most_common(1)[0]

    if count >= (len(chunks) // 2 + 1):
        return best

    # Sin mayoría estricta: si hay empate, prioriza longitud esperada/mayor.
    best_count = count
    tied = [c for c, n in counter.items() if n == best_count]
    return max(tied, key=len)


def make_encode_cmd(output_path: Path, backend: EncodeBackend) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-s", f"{WIDTH}x{HEIGHT}",
        "-r", str(FPS),
        "-i", "-",
        "-an",
        *backend.args,
        "-movflags", "+faststart",
        str(output_path),
    ]


def benchmark_encoder_backend(backend: EncodeBackend, test_frame: np.ndarray, frames: int = 90) -> Optional[float]:
    """
    Devuelve fps medidos o None si falla.
    """
    tmp = Path(tempfile.gettempdir()) / f"ytpixel_bench_{backend.key}_{os.getpid()}.mp4"
    cmd = make_encode_cmd(tmp, backend)

    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if proc.stdin is None:
            return None

        raw = test_frame.tobytes()
        start = time.time()
        for _ in range(frames):
            proc.stdin.write(raw)
        proc.stdin.close()
        ret = proc.wait(timeout=60)
        elapsed = max(time.time() - start, 0.001)

        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass

        if ret != 0:
            return None
        return frames / elapsed

    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return None


def select_best_encoder() -> EncodeBackend:
    backends = get_encode_backends()

    # Si solo hay CPU, no benchmark obligatorio.
    if len(backends) == 1:
        return backends[0]

    print("\nProbando backends de codificación disponibles...")
    test_frame = bytes_to_frame(b"\x55" * min(PAYLOAD_BYTES_PER_FRAME, 1024 * 64))

    results = []
    for b in backends:
        fps = benchmark_encoder_backend(b, test_frame, frames=60)
        if fps is not None:
            results.append((fps, b))
            print(f"  OK  {b.name}: {fps:.1f} fps")
        else:
            print(f"  NO  {b.name}: falló")

    if not results:
        # Fallback duro.
        for b in backends:
            if b.key == "cpu":
                return b
        return backends[-1]

    # Elegimos el más rápido medido.
    results.sort(key=lambda x: x[0], reverse=True)
    best = results[0][1]
    print(f"Backend de codificación elegido: {best.name}")
    return best


def open_ffmpeg_writer(output_path: Path, backend: EncodeBackend):
    cmd = make_encode_cmd(output_path, backend)
    return subprocess.Popen(cmd, stdin=subprocess.PIPE), cmd


def encode():
    if not ffmpeg_available():
        print("\nERROR: FFmpeg no está en PATH.")
        print("Instálalo y comprueba con: ffmpeg -version")
        return

    input_path = open_file_dialog("Selecciona el archivo que quieres codificar")
    if not input_path or not input_path.is_file():
        print("No se seleccionó archivo válido.")
        return

    default_output = input_path.with_suffix(OUTPUT_SUFFIX).name
    output_path = save_file_dialog("Guardar vídeo codificado como", default_output)
    if not output_path:
        print("Cancelado.")
        return

    backend = select_best_encoder()

    size = input_path.stat().st_size
    total_data_frames = math.ceil(size / PAYLOAD_BYTES_PER_FRAME)
    total_logical_frames = 1 + total_data_frames
    total_real_frames = total_logical_frames * REPEAT
    duration = total_real_frames / FPS

    print("\nPERFIL ÚNICO YOUTUBE ROBUSTO AUTO-HW")
    print(f"  Resolución:       {WIDTH}x{HEIGHT}")
    print(f"  Bloque:           {PIXEL_SIZE}x{PIXEL_SIZE}")
    print(f"  FPS:              {FPS}")
    print(f"  Repetición:       x{REPEAT}")
    print(f"  Capacidad/frame:  {human_size(PAYLOAD_BYTES_PER_FRAME)}")
    print(f"  Encoder elegido:  {backend.name}")
    print("\nARCHIVO")
    print(f"  Entrada:          {input_path}")
    print(f"  Tamaño:           {human_size(size)}")
    print(f"  Frames lógicos:   {total_logical_frames}")
    print(f"  Frames reales:    {total_real_frames}")
    print(f"  Duración vídeo:   {duration/60:.2f} min")
    print(f"  Salida:           {output_path}")
    print("\nAVISO: YouTube puede recomprimir y romper datos. El SHA-256 final manda.")

    ok = input("¿Continuar? [s/N]: ").strip().lower()
    if ok != "s":
        print("Cancelado.")
        return

    proc, cmd = open_ffmpeg_writer(output_path, backend)
    if proc.stdin is None:
        print("No se pudo iniciar FFmpeg.")
        return

    start = time.time()
    written = 0

    try:
        meta = make_meta_bytes(input_path)
        meta_frame = bytes_to_frame(meta)
        meta_raw = meta_frame.tobytes()
        for _ in range(REPEAT):
            proc.stdin.write(meta_raw)
            written += 1

        with input_path.open("rb") as f:
            for idx in range(total_data_frames):
                chunk = f.read(PAYLOAD_BYTES_PER_FRAME)
                frame = bytes_to_frame(chunk)
                raw = frame.tobytes()

                for _ in range(REPEAT):
                    proc.stdin.write(raw)
                    written += 1

                if (idx + 1) % 100 == 0 or (idx + 1) == total_data_frames:
                    elapsed = max(time.time() - start, 0.001)
                    fps_real = written / elapsed
                    pct = (idx + 1) / total_data_frames * 100
                    eta = (total_real_frames - written) / max(fps_real, 0.001)
                    print(
                        f"  Progreso: {idx+1}/{total_data_frames} frames de datos "
                        f"({pct:.1f}%) | {fps_real:.1f} fps reales | ETA {eta/60:.1f} min",
                        flush=True,
                    )

    except BrokenPipeError:
        print("\nERROR: FFmpeg cerró el pipe. El backend elegido falló.")
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass

    ret = proc.wait()
    if ret != 0:
        print(f"\nERROR: FFmpeg terminó con código {ret}.")
        return

    elapsed = max(time.time() - start, 0.001)
    print("\nCODIFICACIÓN TERMINADA")
    print(f"  Salida:      {output_path}")
    print(f"  Tamaño vídeo:{human_size(output_path.stat().st_size)}")
    print(f"  Tiempo:      {elapsed/60:.2f} min")
    print(f"  FPS reales:  {written/elapsed:.1f}")


def download_youtube_video() -> Optional[Path]:
    """
    Descarga automáticamente el vídeo de YouTube a la máxima resolución disponible
    y devuelve la ruta local para descodificarlo directamente.

    Usa:
      --remote-components ejs:github
    porque YouTube puede exigir challenge JS.

    Descarga preferida:
      mejor vídeo disponible, sin audio, hasta resolución máxima.
    """
    if not yt_dlp_available():
        print("\nERROR: yt-dlp no está instalado o no está en PATH.")
        print("Instala con: pip install -U yt-dlp")
        return None

    if not ffmpeg_available():
        print("\nAVISO: FFmpeg no está en PATH. yt-dlp puede no poder fusionar formatos si hiciera falta.")

    url = input("\nPega la URL de YouTube: ").strip()
    if not url:
        print("URL vacía.")
        return None

    tmpdir = Path(tempfile.mkdtemp(prefix="ytpixel_auto_hw_"))
    output_template = str(tmpdir / "downloaded.%(ext)s")

    # Máxima calidad de vídeo disponible.
    # Para descodificar nuestros datos NO necesitamos audio.
    # Orden:
    # 1) mejor vídeo MP4 disponible
    # 2) mejor vídeo en cualquier contenedor
    # 3) mejor formato general si lo anterior falla
    cmd = [
        "yt-dlp",
        "--remote-components", "ejs:github",
        "--force-ipv4",
        "-f", "bv*[ext=mp4]/bv*/bestvideo/best",
        "--merge-output-format", "mp4",
        "-o", output_template,
        url,
    ]

    print("\nDescargando vídeo con yt-dlp a máxima resolución disponible...")
    print(f"Carpeta temporal: {tmpdir}")
    print("Esto puede tardar según el tamaño del vídeo y tu conexión.")

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        print("\nERROR: yt-dlp falló descargando el vídeo.")
        print("Prueba manualmente:")
        print(f'yt-dlp --remote-components ejs:github -F "{url}"')
        return None

    videos = [p for p in tmpdir.glob("*") if p.suffix.lower() in [".mp4", ".mkv", ".webm"]]
    if not videos:
        print("No se encontró vídeo descargado.")
        return None

    # Elegimos el más grande, normalmente será el de mayor calidad/resolución.
    video = max(videos, key=lambda p: p.stat().st_size)
    print(f"\nVídeo descargado: {video}")
    print(f"Tamaño descargado: {human_size(video.stat().st_size)}")
    print("Descodificando directamente ese archivo...")
    return video

def make_decode_cmd(video_path: Path, backend: DecodeBackend, max_frames: Optional[int] = None) -> list[str]:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        *backend.input_args,
        "-i", str(video_path),
        "-vf", f"scale={WIDTH}:{HEIGHT}",
        "-an",
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
    ]
    if max_frames is not None:
        cmd += ["-frames:v", str(max_frames)]
    cmd += ["-"]
    return cmd


def benchmark_decode_backend(video_path: Path, backend: DecodeBackend, frames: int = 90) -> Optional[float]:
    cmd = make_decode_cmd(video_path, backend, max_frames=frames)
    frame_size = WIDTH * HEIGHT * 3

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if proc.stdout is None:
            return None

        start = time.time()
        count = 0
        while count < frames:
            raw = proc.stdout.read(frame_size)
            if len(raw) < frame_size:
                break
            count += 1

        try:
            proc.stdout.close()
        except Exception:
            pass

        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

        elapsed = max(time.time() - start, 0.001)
        if count < max(5, frames // 4):
            return None
        return count / elapsed

    except Exception:
        return None


def select_best_decoder(video_path: Path) -> DecodeBackend:
    backends = get_decode_backends()
    print("\nProbando backends de descodificación disponibles...")

    results = []
    for b in backends:
        fps = benchmark_decode_backend(video_path, b, frames=90)
        if fps is not None:
            results.append((fps, b))
            print(f"  OK  {b.name}: {fps:.1f} fps")
        else:
            print(f"  NO  {b.name}: falló o demasiado lento")

    if not results:
        for b in backends:
            if b.key == "cpu":
                return b
        return backends[-1]

    results.sort(key=lambda x: x[0], reverse=True)
    best = results[0][1]
    print(f"Backend de descodificación elegido: {best.name}")
    return best


def open_ffmpeg_reader(video_path: Path, backend: DecodeBackend):
    cmd = make_decode_cmd(video_path, backend, max_frames=None)
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL), cmd


def read_raw_frame(proc) -> Optional[np.ndarray]:
    frame_size = WIDTH * HEIGHT * 3
    if proc.stdout is None:
        return None
    raw = proc.stdout.read(frame_size)
    if len(raw) < frame_size:
        return None
    return np.frombuffer(raw, dtype=np.uint8).reshape((HEIGHT, WIDTH, 3))


def read_logical_frame(proc, method: str = "fast") -> tuple[bool, bytes, int]:
    chunks = []
    read_count = 0

    for _ in range(REPEAT):
        frame = read_raw_frame(proc)
        if frame is None:
            break
        chunks.append(frame_to_bytes(frame, method=method))
        read_count += 1

    if read_count == 0:
        return False, b"", 0

    return True, majority_vote(chunks), read_count


def decode_from_video_path(video_path: Path):
    if not ffmpeg_available():
        print("\nERROR: FFmpeg no está en PATH.")
        return

    if not video_path or not video_path.is_file():
        print("Archivo de vídeo no válido.")
        return

    backend = select_best_decoder(video_path)

    print("\nDESCODIFICANDO")
    print(f"  Vídeo:        {video_path}")
    print(f"  Decoder:      {backend.name}")
    print(f"  Perfil fijo:  {WIDTH}x{HEIGHT}, bloque {PIXEL_SIZE}, repeat x{REPEAT}")

    start = time.time()
    physical_frames = 0

    proc, cmd = open_ffmpeg_reader(video_path, backend)

    ok, meta_chunk, n = read_logical_frame(proc, method="fast")
    physical_frames += n
    meta = parse_meta_bytes(meta_chunk) if ok else None

    if not meta:
        # Reintento usando CPU y método distancia, más tolerante.
        try:
            proc.kill()
        except Exception:
            pass

        cpu_backend = DecodeBackend("cpu", "CPU FFmpeg decode", [], 10)
        proc, cmd = open_ffmpeg_reader(video_path, cpu_backend)
        physical_frames = 0
        ok, meta_chunk, n = read_logical_frame(proc, method="distance")
        physical_frames += n
        meta = parse_meta_bytes(meta_chunk) if ok else None
        backend = cpu_backend

    if not meta:
        try:
            proc.kill()
        except Exception:
            pass
        print("\nERROR: No se pudo leer metadata.")
        print("Causas típicas: vídeo recomprimido demasiado, perfil distinto, resolución cambiada o descarga incorrecta.")
        return

    print("\nMetadata detectada:")
    print(f"  Nombre original: {meta.get('filename')}")
    print(f"  Tamaño original: {human_size(int(meta.get('size', 0)))}")
    print(f"  SHA-256:         {meta.get('sha256')}")
    print(f"  Frames datos:    {meta.get('total_data_frames')}")

    expected_size = int(meta["size"])
    expected_chunks = int(meta["total_data_frames"])
    expected_hash = meta["sha256"]
    original_name = Path(meta["filename"]).name

    default_name = "decoded_" + original_name
    output_path = save_decoded_dialog(default_name)
    if not output_path:
        try:
            proc.kill()
        except Exception:
            pass
        print("Cancelado.")
        return

    print("\nReconstruyendo archivo...")
    written = 0
    chunks_done = 0

    with output_path.open("wb") as out:
        while chunks_done < expected_chunks:
            ok, chunk, n = read_logical_frame(proc, method="fast")
            physical_frames += n
            if not ok:
                break

            if written + len(chunk) > expected_size:
                chunk = chunk[:expected_size - written]

            out.write(chunk)
            written += len(chunk)
            chunks_done += 1

            if chunks_done % 300 == 0 or chunks_done == expected_chunks:
                elapsed = max(time.time() - start, 0.001)
                fps_real = physical_frames / elapsed
                pct = chunks_done / expected_chunks * 100
                eta = ((expected_chunks - chunks_done) * REPEAT) / max(fps_real, 0.001)
                print(
                    f"  Progreso: {chunks_done}/{expected_chunks} chunks "
                    f"({pct:.1f}%) | {fps_real:.1f} fps lectura/proceso | ETA {eta/60:.1f} min",
                    flush=True,
                )

            if written >= expected_size:
                break

    try:
        proc.stdout.close()
    except Exception:
        pass
    try:
        proc.terminate()
    except Exception:
        pass

    if written < expected_size:
        print(f"\nERROR: Archivo incompleto. Escrito {human_size(written)} de {human_size(expected_size)}.")
        return

    with output_path.open("rb+") as f:
        f.truncate(expected_size)

    print(f"\nArchivo escrito: {output_path}")
    print(f"Tamaño: {human_size(output_path.stat().st_size)}")

    print("Verificando SHA-256...")
    actual_hash = sha256_file(output_path)

    print(f"SHA esperado:  {expected_hash}")
    print(f"SHA obtenido:  {actual_hash}")

    elapsed = max(time.time() - start, 0.001)
    print(f"Tiempo descodificación: {elapsed/60:.2f} min")
    print(f"FPS lectura/proceso:    {physical_frames/elapsed:.1f}")
    print(f"Decoder usado:          {backend.name}")

    if actual_hash == expected_hash:
        print("\nINTEGRIDAD OK: el archivo se recuperó correctamente.")
    else:
        print("\nINTEGRIDAD FALLIDA: el archivo NO coincide.")
        print("El vídeo cambió demasiados bloques. Prueba más calidad al subir/descargar o un perfil más robusto.")


def decode():
    print("\nDESCODIFICAR")
    print("1) Pegar enlace de YouTube, descargar máxima resolución y descodificar")
    print("2) Elegir archivo de vídeo local")

    opt = input("\nOpción: ").strip()

    if opt == "1":
        video_path = download_youtube_video()
        if video_path:
            decode_from_video_path(video_path)
    elif opt == "2":
        video_path = open_file_dialog(
            "Selecciona el vídeo codificado",
            filetypes=[
                ("Vídeos", "*.mp4 *.mkv *.webm *.avi"),
                ("Todos los archivos", "*.*"),
            ],
        )
        if video_path:
            decode_from_video_path(video_path)
        else:
            print("No se seleccionó vídeo.")
    else:
        print("Opción no válida.")


def info():
    duration_per_mib = ((1024**2) / PAYLOAD_BYTES_PER_FRAME * REPEAT) / FPS
    duration_per_gib = ((1024**3) / PAYLOAD_BYTES_PER_FRAME * REPEAT) / FPS / 60

    print("\nCONFIGURACIÓN FIJA")
    print(f"  Resolución:        {WIDTH}x{HEIGHT}")
    print(f"  Bloque:            {PIXEL_SIZE}x{PIXEL_SIZE}")
    print(f"  FPS:               {FPS}")
    print(f"  Repetición:        x{REPEAT}")
    print(f"  Capacidad/frame:   {human_size(PAYLOAD_BYTES_PER_FRAME)}")
    print(f"  Duración/MiB:      {duration_per_mib:.2f} s")
    print(f"  Duración/GiB:      {duration_per_gib:.2f} min")
    print(f"  OpenCV hilos:      {cv2.getNumThreads()}")

    if ffmpeg_available():
        print("\nEncoders disponibles candidatos:")
        for b in get_encode_backends():
            print(f"  - {b.name}")

        print("\nDecoders/aceleradores candidatos:")
        for b in get_decode_backends():
            print(f"  - {b.name}")
    else:
        print("\nFFmpeg no detectado.")


def main():
    print("YouTube Pixel Storage Auto-HW")
    print("-----------------------------")
    print("Sin QR. Bloques de color. Perfil robusto para YouTube con selección automática de hardware.")
    print(f"CPU detectadas: {os.cpu_count() or 'desconocido'}")
    print(f"OpenCV hilos:   {cv2.getNumThreads()}")
    print(f"FFmpeg:         {'detectado' if ffmpeg_available() else 'NO detectado'}")
    print(f"yt-dlp:         {'detectado' if yt_dlp_available() else 'NO detectado'}")

    while True:
        print("\n1) Codificar archivo a vídeo")
        print("2) Descodificar vídeo / enlace de YouTube a máxima resolución")
        print("3) Ver configuración y hardware detectado")
        print("0) Salir")

        op = input("\nOpción: ").strip()

        if op == "1":
            encode()
        elif op == "2":
            decode()
        elif op == "3":
            info()
        elif op == "0":
            break
        else:
            print("Opción no válida.")


if __name__ == "__main__":
    main()
