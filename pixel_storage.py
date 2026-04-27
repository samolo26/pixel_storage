#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pixel_storage_gpu_max.py

Codificador/descodificador experimental de archivos dentro de vídeo usando bloques de color.
No usa QR.

Objetivo:
- Priorizar GPU y rendimiento.
- Detectar y probar AMD AMF, NVIDIA NVENC, Intel QSV, Windows h264_mf y CPU fallback.
- Usar automáticamente la GPU funcional más rápida.
- Descargar de YouTube a máxima resolución con yt-dlp y descodificar directamente.
- Verificar SHA-256.

Dependencias:
    pip install opencv-python numpy yt-dlp

Necesario:
    ffmpeg en PATH
    deno recomendado para yt-dlp con YouTube
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


# ============================================================
# PERFIL RÁPIDO GPU
# ============================================================
# Más rápido que el perfil robusto:
# - BLOCK 4 = más datos por frame
# - REPEAT 2 = algo de redundancia sin vídeo absurdo
# Si YouTube rompe el SHA, sube REPEAT a 3/5 o BLOCK a 8.
WIDTH = 1920
HEIGHT = 1080
BLOCK = 4
FPS = 30
REPEAT = 2

# Calidad. Menor = más calidad / más peso.
GPU_QP = 6
CPU_CRF = 8
CPU_PRESET = "veryfast"

OUTPUT_SUFFIX = ".gpu_max.mp4"

# ============================================================
# RUTAS DE HERRAMIENTAS
# ============================================================
# Cambia esta ruta si tienes ffmpeg.exe en otro sitio.
# Recomendado:
#   C:\ffmpeg\bin\ffmpeg.exe
FFMPEG_PATH = r"C:\ffmpeg\bin\ffmpeg.exe"

# Normalmente yt-dlp se instala con pip y queda en PATH.
# Si tienes yt-dlp.exe en una ruta concreta, puedes ponerla aquí.
YTDLP_PATH = "yt-dlp"


BLOCKS_X = WIDTH // BLOCK
BLOCKS_Y = HEIGHT // BLOCK
TOTAL_BLOCKS = BLOCKS_X * BLOCKS_Y
PAYLOAD_BYTES_PER_FRAME = TOTAL_BLOCKS // 4  # 4 bloques de 2 bits = 1 byte

META_MAGIC = b"YPGM1"
META_PREFIX_SIZE = 16
RESERVED_META_BYTES = 2048

# OpenCV/FFmpeg raw usa BGR.
BLACK = np.array([0, 0, 0], dtype=np.uint8)        # 00
RED   = np.array([0, 0, 255], dtype=np.uint8)      # 01
GREEN = np.array([0, 255, 0], dtype=np.uint8)      # 10
BLUE  = np.array([255, 0, 0], dtype=np.uint8)      # 11
WHITE = np.array([255, 255, 255], dtype=np.uint8)  # relleno

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
    is_gpu: bool


@dataclass
class DecodeBackend:
    key: str
    name: str
    input_args: list[str]
    is_gpu: bool


def human_size(n: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    x = float(n)
    for u in units:
        if x < 1024 or u == units[-1]:
            return f"{x:.2f} {u}"
        x /= 1024


def run_capture(cmd: list[str], timeout: int = 30) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, errors="ignore", timeout=timeout)
        return (p.stdout or "") + "\n" + (p.stderr or "")
    except Exception as e:
        return str(e)


def tool_exists(name: str) -> bool:
    if name == "ffmpeg":
        return Path(FFMPEG_PATH).is_file() or shutil.which("ffmpeg") is not None
    if name == "yt-dlp":
        return Path(YTDLP_PATH).is_file() or shutil.which(YTDLP_PATH) is not None
    return shutil.which(name) is not None


def ffmpeg_exe() -> str:
    if Path(FFMPEG_PATH).is_file():
        return FFMPEG_PATH
    found = shutil.which("ffmpeg")
    return found if found else "ffmpeg"


def ytdlp_exe() -> str:
    if Path(YTDLP_PATH).is_file():
        return YTDLP_PATH
    found = shutil.which(YTDLP_PATH)
    return found if found else YTDLP_PATH


def ffmpeg_encoders() -> str:
    if not tool_exists("ffmpeg"):
        return ""
    return run_capture([ffmpeg_exe(), "-hide_banner", "-encoders"]).lower()


def ffmpeg_hwaccels() -> str:
    if not tool_exists("ffmpeg"):
        return ""
    return run_capture([ffmpeg_exe(), "-hide_banner", "-hwaccels"]).lower()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(8 * 1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def choose_open_file(title: str, filetypes=None) -> Optional[Path]:
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
    return Path(filename) if filename else None


def choose_save_file(title: str, default_name: str, ext: str = ".mp4") -> Optional[Path]:
    if tk is None or filedialog is None:
        raw = input(f"{title} [{default_name}]: ").strip().strip('"')
        return Path(raw).expanduser() if raw else Path(default_name)

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    filename = filedialog.asksaveasfilename(
        title=title,
        initialfile=default_name,
        defaultextension=ext,
        filetypes=[("MP4", "*.mp4"), ("Todos los archivos", "*.*")]
    )
    root.destroy()
    return Path(filename) if filename else None


def choose_save_decoded(default_name: str) -> Optional[Path]:
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
    return Path(filename) if filename else None


def get_encode_backends() -> list[EncodeBackend]:
    enc = ffmpeg_encoders()
    out: list[EncodeBackend] = []

    # AMD AMF - primero porque el usuario usa AMD. El benchmark decide si funciona.
    if "h264_amf" in enc:
        out.append(EncodeBackend(
            "amf_cqp",
            "GPU AMD AMF h264_amf CQP",
            [
                "-c:v", "h264_amf",
                "-usage", "transcoding",
                "-quality", "speed",
                "-rc", "cqp",
                "-qp_i", str(GPU_QP),
                "-qp_p", str(GPU_QP),
                "-pix_fmt", "yuv420p",
            ],
            True,
        ))
        out.append(EncodeBackend(
            "amf_simple",
            "GPU AMD AMF h264_amf simple",
            [
                "-c:v", "h264_amf",
                "-usage", "transcoding",
                "-quality", "speed",
                "-pix_fmt", "yuv420p",
            ],
            True,
        ))

    if "h264_nvenc" in enc:
        out.append(EncodeBackend(
            "nvenc_p1",
            "GPU NVIDIA NVENC h264_nvenc máximo rendimiento",
            [
                "-c:v", "h264_nvenc",
                "-preset", "p1",
                "-tune", "hq",
                "-rc", "constqp",
                "-qp", str(GPU_QP),
                "-pix_fmt", "yuv420p",
            ],
            True,
        ))
        out.append(EncodeBackend(
            "nvenc_p5",
            "GPU NVIDIA NVENC h264_nvenc calidad/rendimiento",
            [
                "-c:v", "h264_nvenc",
                "-preset", "p5",
                "-tune", "hq",
                "-rc", "constqp",
                "-qp", str(GPU_QP),
                "-pix_fmt", "yuv420p",
            ],
            True,
        ))

    if "h264_qsv" in enc:
        out.append(EncodeBackend(
            "qsv",
            "GPU Intel QuickSync h264_qsv",
            [
                "-c:v", "h264_qsv",
                "-preset", "veryfast",
                "-global_quality", str(GPU_QP),
                "-pix_fmt", "nv12",
            ],
            True,
        ))

    if "h264_mf" in enc:
        out.append(EncodeBackend(
            "h264_mf",
            "Windows Media Foundation h264_mf",
            ["-c:v", "h264_mf", "-pix_fmt", "yuv420p"],
            True,
        ))

    # CPU fallback.
    if "libx264" in enc:
        out.append(EncodeBackend(
            "cpu_x264",
            "CPU libx264 fallback",
            [
                "-c:v", "libx264",
                "-preset", CPU_PRESET,
                "-crf", str(CPU_CRF),
                "-pix_fmt", "yuv420p",
                "-threads", "0",
            ],
            False,
        ))

    if "mpeg4" in enc:
        out.append(EncodeBackend(
            "cpu_mpeg4",
            "CPU MPEG4 fallback",
            ["-c:v", "mpeg4", "-q:v", "1", "-pix_fmt", "yuv420p"],
            False,
        ))

    return out


def get_decode_backends() -> list[DecodeBackend]:
    hw = ffmpeg_hwaccels()
    out: list[DecodeBackend] = []

    if "cuda" in hw:
        out.append(DecodeBackend("cuda", "GPU NVIDIA CUDA/NVDEC", ["-hwaccel", "cuda"], True))
    if "qsv" in hw:
        out.append(DecodeBackend("qsv", "GPU Intel QuickSync decode", ["-hwaccel", "qsv"], True))
    if "d3d11va" in hw:
        out.append(DecodeBackend("d3d11va", "GPU Windows D3D11VA", ["-hwaccel", "d3d11va"], True))
    if "dxva2" in hw:
        out.append(DecodeBackend("dxva2", "GPU Windows DXVA2", ["-hwaccel", "dxva2"], True))

    out.append(DecodeBackend("cpu", "CPU FFmpeg decode", [], False))
    return out


YS = np.arange(BLOCK // 2, HEIGHT, BLOCK)
XS = np.arange(BLOCK // 2, WIDTH, BLOCK)


def make_meta_bytes(input_path: Path) -> bytes:
    size = input_path.stat().st_size
    total_data_frames = math.ceil(size / PAYLOAD_BYTES_PER_FRAME)
    meta = {
        "magic": META_MAGIC.decode("ascii"),
        "filename": input_path.name,
        "size": size,
        "sha256": sha256_file(input_path),
        "width": WIDTH,
        "height": HEIGHT,
        "block": BLOCK,
        "fps": FPS,
        "repeat": REPEAT,
        "bytes_per_frame": PAYLOAD_BYTES_PER_FRAME,
        "total_data_frames": total_data_frames,
        "profile": "gpu_max_v1",
    }
    data = json.dumps(meta, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(data) > RESERVED_META_BYTES - META_PREFIX_SIZE:
        raise ValueError("Metadata demasiado grande.")
    prefix = META_MAGIC + len(data).to_bytes(4, "big") + b"\x00" * 7
    return (prefix + data).ljust(RESERVED_META_BYTES, b"\x00")


def parse_meta_bytes(raw: bytes) -> Optional[dict]:
    if len(raw) < META_PREFIX_SIZE:
        return None
    if raw[:5] != META_MAGIC:
        return None
    length = int.from_bytes(raw[5:9], "big")
    if length <= 0 or length > RESERVED_META_BYTES:
        return None
    try:
        meta = json.loads(raw[META_PREFIX_SIZE:META_PREFIX_SIZE + length].decode("utf-8"))
    except Exception:
        return None
    return meta if meta.get("magic") == META_MAGIC.decode("ascii") else None


def bytes_to_frame(chunk: bytes) -> np.ndarray:
    if len(chunk) > PAYLOAD_BYTES_PER_FRAME:
        raise ValueError("Chunk mayor que capacidad de frame.")

    groups = np.full(TOTAL_BLOCKS, 255, dtype=np.uint8)

    if chunk:
        data = np.frombuffer(chunk, dtype=np.uint8)
        g = np.empty(data.size * 4, dtype=np.uint8)
        g[0::4] = (data >> 6) & 3
        g[1::4] = (data >> 4) & 3
        g[2::4] = (data >> 2) & 3
        g[3::4] = data & 3
        groups[:g.size] = g

    grid = np.empty((BLOCKS_Y, BLOCKS_X, 3), dtype=np.uint8)
    grid[:] = WHITE

    g2 = groups.reshape(BLOCKS_Y, BLOCKS_X)
    grid[g2 == 0] = BLACK
    grid[g2 == 1] = RED
    grid[g2 == 2] = GREEN
    grid[g2 == 3] = BLUE

    return np.repeat(np.repeat(grid, BLOCK, axis=0), BLOCK, axis=1)


def classify_fast(sample: np.ndarray) -> np.ndarray:
    b = sample[:, :, 0].astype(np.int16)
    g = sample[:, :, 1].astype(np.int16)
    r = sample[:, :, 2].astype(np.int16)

    white = (b > 175) & (g > 175) & (r > 175)
    black = (b < 90) & (g < 90) & (r < 90)

    stacked = np.stack([b, r, g], axis=2)
    dom = stacked.argmax(axis=2)

    out = np.zeros(b.shape, dtype=np.uint8)
    out[dom == 0] = 3  # azul
    out[dom == 1] = 1  # rojo
    out[dom == 2] = 2  # verde
    out[black] = 0
    out[white] = 255
    return out


def classify_distance(sample: np.ndarray) -> np.ndarray:
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
    if frame.shape[1] != WIDTH or frame.shape[0] != HEIGHT:
        frame = cv2.resize(frame, (WIDTH, HEIGHT), interpolation=cv2.INTER_AREA)
    sample = frame[np.ix_(YS, XS)]
    groups = classify_distance(sample) if method == "distance" else classify_fast(sample)
    return groups_to_bytes(groups)


def majority_vote(chunks: list[bytes]) -> bytes:
    chunks = [c for c in chunks if c]
    if not chunks:
        return b""
    from collections import Counter
    counter = Counter(chunks)
    best, count = counter.most_common(1)[0]
    if count >= (len(chunks) // 2 + 1):
        return best
    tied = [c for c, n in counter.items() if n == count]
    return max(tied, key=len)


def encode_cmd(output_path: Path, backend: EncodeBackend) -> list[str]:
    return [
        ffmpeg_exe(),
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


def benchmark_encoder(backend: EncodeBackend, frames: int = 60) -> Optional[float]:
    tmp = Path(tempfile.gettempdir()) / f"pixel_gpu_bench_{backend.key}_{os.getpid()}.mp4"
    cmd = encode_cmd(tmp, backend)
    frame = bytes_to_frame(b"\x55" * min(PAYLOAD_BYTES_PER_FRAME, 1024 * 64))
    raw = frame.tobytes()

    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if proc.stdin is None:
            return None
        start = time.time()
        for _ in range(frames):
            proc.stdin.write(raw)
        proc.stdin.close()
        stderr = proc.stderr.read().decode(errors="ignore") if proc.stderr else ""
        ret = proc.wait(timeout=90)
        elapsed = max(time.time() - start, 0.001)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        if ret != 0:
            if stderr.strip():
                print(f"      {backend.key}: {stderr.strip().splitlines()[-1]}")
            return None
        return frames / elapsed
    except Exception as e:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        print(f"      {backend.key}: {e}")
        return None


def select_encoder() -> EncodeBackend:
    backends = get_encode_backends()
    if not backends:
        raise RuntimeError("No se detectó ningún encoder en FFmpeg.")

    print("\nBackends detectados:")
    for b in backends:
        print(f"  - {b.name}")

    print("\nBenchmark de codificación:")
    results = []
    for b in backends:
        fps = benchmark_encoder(b)
        if fps is None:
            print(f"  NO  {b.name}")
        else:
            tag = "GPU" if b.is_gpu else "CPU"
            print(f"  OK  {b.name}: {fps:.1f} fps [{tag}]")
            results.append((fps, b))

    if not results:
        raise RuntimeError("Todos los encoders fallaron. Revisa FFmpeg/drivers.")

    # Prioridad: si hay GPU funcional, usamos la GPU más rápida.
    gpu_results = [(fps, b) for fps, b in results if b.is_gpu]
    if gpu_results:
        gpu_results.sort(key=lambda x: x[0], reverse=True)
        best = gpu_results[0][1]
        print(f"\nEncoder elegido: {best.name} (mejor GPU funcional)")
        return best

    results.sort(key=lambda x: x[0], reverse=True)
    best = results[0][1]
    print(f"\nEncoder elegido: {best.name} (fallback CPU)")
    return best


def encode():
    if not tool_exists("ffmpeg"):
        print("ERROR: FFmpeg no está en PATH.")
        return

    input_path = choose_open_file("Selecciona archivo a codificar")
    if not input_path or not input_path.is_file():
        print("No se seleccionó archivo válido.")
        return

    output_path = choose_save_file("Guardar vídeo", input_path.with_suffix(OUTPUT_SUFFIX).name)
    if not output_path:
        print("Cancelado.")
        return

    try:
        backend = select_encoder()
    except Exception as e:
        print(f"ERROR: {e}")
        return

    size = input_path.stat().st_size
    total_data_frames = math.ceil(size / PAYLOAD_BYTES_PER_FRAME)
    total_logical = total_data_frames + 1
    total_real = total_logical * REPEAT
    duration = total_real / FPS

    print("\nRESUMEN")
    print(f"  Entrada:         {input_path}")
    print(f"  Tamaño:          {human_size(size)}")
    print(f"  Salida:          {output_path}")
    print(f"  Perfil:          {WIDTH}x{HEIGHT}, block={BLOCK}, fps={FPS}, repeat={REPEAT}")
    print(f"  Capacidad/frame: {human_size(PAYLOAD_BYTES_PER_FRAME)}")
    print(f"  Frames datos:    {total_data_frames}")
    print(f"  Frames reales:   {total_real}")
    print(f"  Duración vídeo:  {duration/60:.2f} min")
    print(f"  Encoder:         {backend.name}")
    print("\nAVISO: máxima velocidad GPU no garantiza integridad tras YouTube.")
    if input("¿Continuar? [s/N]: ").strip().lower() != "s":
        return

    cmd = encode_cmd(output_path, backend)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if proc.stdin is None:
        print("No se pudo iniciar FFmpeg.")
        return

    time.sleep(0.2)
    if proc.poll() is not None:
        stderr = proc.stderr.read().decode(errors="ignore") if proc.stderr else ""
        print("FFmpeg se cerró al iniciar.")
        print(stderr)
        return

    start = time.time()
    written = 0

    def write_rep(raw: bytes):
        nonlocal written
        for _ in range(REPEAT):
            proc.stdin.write(raw)
            written += 1

    try:
        write_rep(bytes_to_frame(make_meta_bytes(input_path)).tobytes())

        with input_path.open("rb") as f:
            for i in range(total_data_frames):
                chunk = f.read(PAYLOAD_BYTES_PER_FRAME)
                write_rep(bytes_to_frame(chunk).tobytes())

                if (i + 1) % 100 == 0 or (i + 1) == total_data_frames:
                    elapsed = max(time.time() - start, 0.001)
                    fps = written / elapsed
                    pct = (i + 1) / total_data_frames * 100
                    eta = (total_real - written) / max(fps, 0.001)
                    print(f"  Progreso: {i+1}/{total_data_frames} ({pct:.1f}%) | {fps:.1f} fps | ETA {eta/60:.1f} min", flush=True)

    except (BrokenPipeError, OSError) as e:
        print(f"ERROR escribiendo a FFmpeg: {e}")
        stderr = proc.stderr.read().decode(errors="ignore") if proc.stderr else ""
        if stderr:
            print(stderr)
        return
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass

    stderr = proc.stderr.read().decode(errors="ignore") if proc.stderr else ""
    ret = proc.wait()
    if ret != 0:
        print(f"FFmpeg terminó con error {ret}")
        if stderr:
            print(stderr)
        return

    elapsed = max(time.time() - start, 0.001)
    print("\nCODIFICACIÓN COMPLETA")
    print(f"  Archivo: {output_path}")
    print(f"  Tamaño:  {human_size(output_path.stat().st_size)}")
    print(f"  Tiempo:  {elapsed/60:.2f} min")
    print(f"  FPS:     {written/elapsed:.1f}")


def download_youtube() -> Optional[Path]:
    if not tool_exists("yt-dlp"):
        print("ERROR: yt-dlp no está instalado.")
        return None

    url = input("URL de YouTube: ").strip()
    if not url:
        return None

    tmpdir = Path(tempfile.mkdtemp(prefix="pixel_gpu_yt_"))
    output = str(tmpdir / "downloaded.%(ext)s")
    cmd = [
        ytdlp_exe(),
        "--remote-components", "ejs:github",
        "--force-ipv4",
        "-f", "bv*[ext=mp4]/bv*/bestvideo/best",
        "--merge-output-format", "mp4",
        "-o", output,
        url,
    ]
    print("Descargando máxima resolución disponible...")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        print("yt-dlp falló.")
        return None

    videos = [p for p in tmpdir.glob("*") if p.suffix.lower() in [".mp4", ".mkv", ".webm"]]
    if not videos:
        print("No se encontró vídeo descargado.")
        return None

    best = max(videos, key=lambda p: p.stat().st_size)
    print(f"Descargado: {best} ({human_size(best.stat().st_size)})")
    return best


def decode_cmd(video_path: Path, backend: DecodeBackend, max_frames: Optional[int] = None) -> list[str]:
    cmd = [
        ffmpeg_exe(),
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


def benchmark_decoder(video_path: Path, backend: DecodeBackend, frames: int = 90) -> Optional[float]:
    cmd = decode_cmd(video_path, backend, max_frames=frames)
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
        if count < max(5, frames // 4):
            return None
        return count / max(time.time() - start, 0.001)
    except Exception:
        return None


def select_decoder(video_path: Path) -> DecodeBackend:
    backends = get_decode_backends()
    print("\nBenchmark de descodificación:")
    results = []
    for b in backends:
        fps = benchmark_decoder(video_path, b)
        if fps is None:
            print(f"  NO  {b.name}")
        else:
            print(f"  OK  {b.name}: {fps:.1f} fps")
            results.append((fps, b))

    if not results:
        return DecodeBackend("cpu", "CPU FFmpeg decode", [], False)

    # Para decode elegimos el más rápido real, CPU o GPU.
    results.sort(key=lambda x: x[0], reverse=True)
    best = results[0][1]
    print(f"Decoder elegido: {best.name}")
    return best


def read_raw_frame(proc) -> Optional[np.ndarray]:
    frame_size = WIDTH * HEIGHT * 3
    if proc.stdout is None:
        return None
    raw = proc.stdout.read(frame_size)
    if len(raw) < frame_size:
        return None
    return np.frombuffer(raw, dtype=np.uint8).reshape((HEIGHT, WIDTH, 3))


def read_logical(proc, method: str = "fast") -> tuple[bool, bytes, int]:
    chunks = []
    n = 0
    for _ in range(REPEAT):
        frame = read_raw_frame(proc)
        if frame is None:
            break
        chunks.append(frame_to_bytes(frame, method=method))
        n += 1
    if n == 0:
        return False, b"", 0
    return True, majority_vote(chunks), n


def decode_video(video_path: Path):
    if not tool_exists("ffmpeg"):
        print("ERROR: FFmpeg no está en PATH.")
        return

    backend = select_decoder(video_path)
    proc = subprocess.Popen(decode_cmd(video_path, backend), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    start = time.time()
    physical = 0

    ok, meta_chunk, n = read_logical(proc, "fast")
    physical += n
    meta = parse_meta_bytes(meta_chunk) if ok else None

    if not meta:
        try:
            proc.kill()
        except Exception:
            pass
        cpu = DecodeBackend("cpu", "CPU FFmpeg decode", [], False)
        proc = subprocess.Popen(decode_cmd(video_path, cpu), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        physical = 0
        ok, meta_chunk, n = read_logical(proc, "distance")
        physical += n
        meta = parse_meta_bytes(meta_chunk) if ok else None
        backend = cpu

    if not meta:
        try:
            proc.kill()
        except Exception:
            pass
        print("No se pudo leer metadata. Vídeo corrupto o perfil diferente.")
        return

    print("\nMetadata:")
    print(f"  Nombre: {meta['filename']}")
    print(f"  Tamaño: {human_size(int(meta['size']))}")
    print(f"  SHA:    {meta['sha256']}")
    print(f"  Chunks: {meta['total_data_frames']}")

    out_path = choose_save_decoded("decoded_" + Path(meta["filename"]).name)
    if not out_path:
        try:
            proc.kill()
        except Exception:
            pass
        return

    expected_size = int(meta["size"])
    expected_chunks = int(meta["total_data_frames"])
    expected_hash = meta["sha256"]

    written = 0
    done = 0

    with out_path.open("wb") as out:
        while done < expected_chunks:
            ok, chunk, n = read_logical(proc, "fast")
            physical += n
            if not ok:
                break

            if written + len(chunk) > expected_size:
                chunk = chunk[:expected_size - written]

            out.write(chunk)
            written += len(chunk)
            done += 1

            if done % 100 == 0 or done == expected_chunks:
                elapsed = max(time.time() - start, 0.001)
                fps = physical / elapsed
                pct = done / expected_chunks * 100
                eta = ((expected_chunks - done) * REPEAT) / max(fps, 0.001)
                print(f"  Progreso: {done}/{expected_chunks} ({pct:.1f}%) | {fps:.1f} fps | ETA {eta/60:.1f} min", flush=True)

            if written >= expected_size:
                break

    try:
        proc.terminate()
    except Exception:
        pass

    if written < expected_size:
        print(f"Archivo incompleto: {human_size(written)} de {human_size(expected_size)}")
        return

    with out_path.open("rb+") as f:
        f.truncate(expected_size)

    actual = sha256_file(out_path)

    print("\nDESCODIFICACIÓN COMPLETA")
    print(f"  Archivo:      {out_path}")
    print(f"  SHA esperado: {expected_hash}")
    print(f"  SHA obtenido: {actual}")
    print(f"  Decoder:      {backend.name}")
    print(f"  Tiempo:       {(time.time()-start)/60:.2f} min")

    if actual == expected_hash:
        print("\nINTEGRIDAD OK")
    else:
        print("\nINTEGRIDAD FALLIDA")


def decode():
    print("\n1) Descargar desde YouTube y descodificar")
    print("2) Elegir vídeo local")
    op = input("Opción: ").strip()

    if op == "1":
        video = download_youtube()
        if video:
            decode_video(video)
    elif op == "2":
        video = choose_open_file("Selecciona vídeo", [("Vídeos", "*.mp4 *.mkv *.webm *.avi"), ("Todos", "*.*")])
        if video:
            decode_video(video)
    else:
        print("Opción no válida.")


def diagnostics():
    print("\nDIAGNÓSTICO")
    print(f"Python:       {sys.version.split()[0]}")
    print(f"CPU:          {os.cpu_count() or 'desconocido'}")
    print(f"OpenCV hilos: {cv2.getNumThreads()}")
    print(f"FFmpeg:       {'sí' if tool_exists('ffmpeg') else 'NO'}")
    print(f"yt-dlp:       {'sí' if tool_exists('yt-dlp') else 'NO'}")

    if tool_exists("ffmpeg"):
        print("\nwhere/which ffmpeg:")
        if os.name == "nt":
            print(run_capture(["where", "ffmpeg"]))
        else:
            print(run_capture(["which", "ffmpeg"]))

        print("\nEncoders detectados:")
        for b in get_encode_backends():
            print(f"  - {b.name}")

        print("\nDecoders/aceleradores detectados:")
        for b in get_decode_backends():
            print(f"  - {b.name}")

        print("\nComandos de prueba útiles:")
        print('ffmpeg -hide_banner -encoders | findstr /i "amf nvenc qsv libx264 mpeg4 h264_mf"')
        print("ffmpeg -hide_banner -f lavfi -i testsrc2=size=1920x1080:rate=30 -t 5 -c:v h264_amf -usage transcoding -quality speed test_amf.mp4")


def info():
    duration_mib = ((1024**2) / PAYLOAD_BYTES_PER_FRAME * REPEAT) / FPS
    duration_gib = ((1024**3) / PAYLOAD_BYTES_PER_FRAME * REPEAT) / FPS / 60
    print("\nCONFIGURACIÓN")
    print(f"  Resolución:      {WIDTH}x{HEIGHT}")
    print(f"  Block:           {BLOCK}x{BLOCK}")
    print(f"  FPS:             {FPS}")
    print(f"  Repeat:          x{REPEAT}")
    print(f"  Capacidad/frame: {human_size(PAYLOAD_BYTES_PER_FRAME)}")
    print(f"  Duración/MiB:    {duration_mib:.2f} s")
    print(f"  Duración/GiB:    {duration_gib:.2f} min")
    print("  Modo:            velocidad GPU prioritaria")


def main():
    print("Pixel Storage GPU MAX")
    print("---------------------")
    print("No QR. Bloques de color. Prioriza GPU y rendimiento.")

    while True:
        print("\n1) Codificar archivo a vídeo")
        print("2) Descodificar vídeo / YouTube")
        print("3) Ver configuración")
        print("4) Diagnóstico")
        print("0) Salir")

        op = input("\nOpción: ").strip()
        if op == "1":
            encode()
        elif op == "2":
            decode()
        elif op == "3":
            info()
        elif op == "4":
            diagnostics()
        elif op == "0":
            break
        else:
            print("Opción no válida.")


if __name__ == "__main__":
    main()
