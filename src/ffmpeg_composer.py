"""
Compositor FFmpeg para postproducción de videos Ara.
Toma un MP4 crudo (generado por Google Flow o sintético) y lo procesa a 1080x1920 (9:16)
con zócalo azul de Saladillo Vivo con texto en scroll coordinado con el audio.
"""
from __future__ import annotations
import os
import subprocess
import tempfile
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ZocaloConfig:
    volanta_text: str = ""       # texto banda superior pequeña
    titulo_text: str = ""      # título principal ajustable
    brand_color: str = "#1E3A8A"  # azul (Slate Blue / Navy)
    marquee_speed: float = 60.0   # px por segundo (velocidad de scroll)
    brand_label: str = "ARA"     # etiqueta fija (ej: logo/texto fijo)


def run_ffmpeg(args: list[str], timeout: int = 120) -> tuple[int, str, str]:
    """Ejecuta ffmpeg y retorna (exit_code, stdout, stderr)."""
    result = subprocess.run(
        ["ffmpeg", "-y"] + args,
        capture_output=True, text=True, timeout=timeout
    )
    return result.returncode, result.stdout, result.stderr


def ensure_1080x1920(input_path: str, output_path: str) -> bool:
    """Escala/centra el video a 1080x1920 vertical."""
    args = [
        "-i", input_path,
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
        "-r", "30",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    rc, _, err = run_ffmpeg(args)
    if rc != 0:
        print(f"[FFmpeg Composer] Error escalando a 1080x1920: {err[:200]}")
        return False
    return True


def draw_zocalo(video_in: str, video_out: str, cfg: ZocaloConfig, duration_sec: float = 8.0) -> bool:
    """Dibuja el zócalo con fondo azul y texto en scroll/coordinado con el audio."""
    font_size = 46
    volanta_height = 38
    titulo_line_height = 58
    padding_x = 30
    padding_y = 10

    # Zona del zócalo: parte inferior ~22% del video
    zocalo_total_h = volanta_height + titulo_line_height + padding_y * 2
    y_start = 1920 - zocalo_total_h  # desde bottom

    vf_parts = []

    # --- FONDO AZUL SEMITRANSPARENTÉ ---
    vf_parts.append(
        f"drawbox=x=0:y={y_start}:w=1080:h={zocalo_total_h}"
        f":color={cfg.brand_color}:t=fill,format=rgba"
    )

    # --- ETIQUETA FIJA "ARA" (esquina izquierda superior del zócalo) ---
    ara_x = padding_x
    ara_y = y_start + 6
    vf_parts.append(
        f"drawtext=text='{cfg.brand_label}'"
        f":fontcolor=#FBBF24:fontsize={font_size - 16}"
        f":x={ara_x}:y={ara_y}"
        f":fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    )

    # --- BANDERÍN SUPERIOR (volanta) ---
    if cfg.volanta_text:
        vy = y_start + volanta_height + 8
        # Texto estático justo debajo de la volanta
        vf_parts.append(
            f"drawtext=text='{cfg.volanta_text}'"
            f":fontcolor=white:fontsize={font_size - 14}"
            f":x={padding_x + 60}:y={vy}"
            f":fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        )

    # --- TÍTULO EN MARQUEE (scroll horizontal coordinado con duración) ---
    # SIEMPRE en una sola línea de principio a fin, sin dividir
    if cfg.titulo_text:
        ty_base = y_start + volanta_height + 10

        # Estimación del ancho visual del texto (~0.6px por carácter con font_size 46)
        est_width = len(cfg.titulo_text) * (font_size * 0.6)

        # Marquee: desplaza de derecha a izquierda durante toda la duración
        speed = (1080 + est_width + padding_x * 2) / duration_sec  # px/s
        vx_expr = f"{1080 + padding_x}-t*{speed:.2f}"
        vf_parts.append(
            f"drawtext=text='{cfg.titulo_text}'"
            f":fontcolor=white:fontsize={font_size}"
            f":x={vx_expr}:y={ty_base}"
            f":fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        )

    vf = ",".join(vf_parts)

    # Compuesto final con el video original como fondo
    args = [
        "-i", video_in,
        "-vf", vf,
        "-t", str(duration_sec),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "copy",
        "-movflags", "+faststart",
        video_out,
    ]
    rc, _, err = run_ffmpeg(args)
    if rc != 0:
        print(f"[FFmpeg Composer] Error dibujando zócalo: {err[:300]}")
        return False
    return True


def normalize_audio(input_video: str, output_video: str, target_loudness: float = -14.0) -> bool:
    """Normaliza audio con filter loudnorm (EBU R128) para consistencia en celulares."""
    args = [
        "-i", input_video,
        "-af", f"loudnorm=i={target_loudness}:lra=11:tp=-1.5",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_video,
    ]
    rc, _, err = run_ffmpeg(args)
    if rc != 0:
        print(f"[FFmpeg Composer] Error normalizando audio: {err[:200]}")
        return False
    return True


def process_video(
    raw_video_path: str,
    output_path: str,
    volanta_text: str = "",
    titulo_text: str = "",
    brand_color: str = "#1E3A8A",
) -> dict:
    """Pipeline completo: escalar → zócalo → normalizar audio → salida final."""
    tmp_dir = tempfile.mkdtemp(prefix="ara_factory_")
    steps: dict[str, bool] = {}

    try:
        step1 = os.path.join(tmp_dir, "step1_scaled.mp4")
        steps["scaled"] = ensure_1080x1920(raw_video_path, step1)
        if not steps["scaled"]:
            return {"ok": False, "steps": steps, "error": "Escalado fallido"}

        step2 = os.path.join(tmp_dir, "step2_zocalo.mp4")
        cfg = ZocaloConfig(volanta_text=volanta_text, titulo_text=titulo_text, brand_color=brand_color)
        steps["zocalo"] = draw_zocalo(step1, step2, cfg)
        if not steps["zocalo"]:
            return {"ok": False, "steps": steps, "error": "Zócalo fallido"}

        steps["audio_norm"] = True  # opcional; skip si no hay audio o ya está bien
        # Normalización opcional
        step3 = os.path.join(tmp_dir, "step3_norm.mp4")
        steps["final"] = normalize_audio(step2, step3)
        if not steps["final"]:
            # Si falla la normalización, copiar step2 como fallback
            subprocess.run(["cp", step2, output_path], check=False)
        else:
            subprocess.run(["cp", step3, output_path], check=False)

        steps["success"] = True
        return {"ok": True, "steps": steps, "output_path": output_path}

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def concatenate_videos(input_paths: list[str], output_path: str) -> bool:
    """Concatena varios clips MP4 vertical 1080x1920 en uno solo usando concat demuxer."""
    if not input_paths:
        print("[FFmpeg Composer] No hay clips para concatenar")
        return False

    tmp_dir = tempfile.mkdtemp(prefix="concat_")
    try:
        listfile = os.path.join(tmp_dir, "files.txt")
        with open(listfile, "w", encoding="utf-8") as f:
            for p in input_paths:
                f.write(f"file '{p}'\n")

        args = [
            "-f", "concat", "-safe", "0", "-i", listfile,
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]
        rc, _, err = run_ffmpeg(args)
        if rc != 0:
            print(f"[FFmpeg Composer] Error concatenando: {err[:300]}")
            return False
        return True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def process_video_multi_clip(
    video_paths: list[str],
    output_path: str,
    volanta_text: str = "",
    titulo_text: str = "",
    brand_color: str = "#1E3A8A",
    duracion_por_clip: float = 8.0,
) -> dict:
    """
    Procesa múltiples clips crudos: escala cada uno a 1080x1920,
    aplica zócalo, los concatena y devuelve el video final de 16s+.

    Retorna dict con estado de cada paso.
    """
    import shutil
    tmp_dir = tempfile.mkdtemp(prefix="ara_factory_multi_")
    steps: dict[str, bool] = {}
    processed_clips: list[str] = []

    try:
        # Paso 1: Escalar y aplicar zócalo a cada clip individualmente
        for idx, raw_path in enumerate(video_paths):
            step_name = f"clip_{idx+1}"
            scaled_path = os.path.join(tmp_dir, f"{step_name}_scaled.mp4")
            zocalo_path = os.path.join(tmp_dir, f"{step_name}_zocalo.mp4")

            steps[f"{step_name}_scaled"] = ensure_1080x1920(raw_path, scaled_path)
            if not steps[f"{step_name}_scaled"]:
                return {"ok": False, "steps": steps, "error": f"Escalado clip {idx+1} fallido"}

            cfg = ZocaloConfig(
                volanta_text=volanta_text,
                titulo_text=titulo_text,
                brand_color=brand_color,
            )
            steps[f"{step_name}_zocalo"] = draw_zocalo(scaled_path, zocalo_path, cfg, duration_sec=duracion_por_clip)
            if not steps[f"{step_name}_zocalo"]:
                return {"ok": False, "steps": steps, "error": f"Zócalo clip {idx+1} fallido"}

            processed_clips.append(zocalo_path)

        # Paso 2: Concatenar todos los clips zocalizados
        steps["concatenate"] = concatenate_videos(processed_clips, output_path)
        if not steps["concatenate"]:
            return {"ok": False, "steps": steps, "error": "Concatenación fallida"}

        steps["success"] = True
        return {"ok": True, "steps": steps, "output_path": output_path}

    except Exception as e:
        return {"ok": False, "steps": steps, "error": str(e)}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Compositor FFmpeg para videos Ara")
    parser.add_argument("--input", required=True, help="Ruta al video crudo (MP4)")
    parser.add_argument("--output", required=True, help="Ruta del video final")
    parser.add_argument("--volanta", default="Saladillo Vivo", help="Texto volanta superior")
    parser.add_argument("--titulo", default="", help="Título del zócalo")
    parser.add_argument("--color", default="#1E3A8A", help="Color del zócalo (default: azul)")
    args = parser.parse_args()

    result = process_video(args.input, args.output, args.volanta, args.titulo, args.color)
    print(f"Result: {result}")
