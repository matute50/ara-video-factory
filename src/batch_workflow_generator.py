"""
Generador de workflows en lote unificado para Google Flow.

Convierte ara_json en un archivo JSON de batch compatible con flow.py batch,
conencadenamiento automático de clips mediante 'start' referenciando jobs previos.
"""
from __future__ import annotations
import json
import re
import time
from pathlib import Path
from typing import Optional

# Importar funciones del ensamblador de prompts
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from prompt_assembler import assemble_prompts_multi_clip  # noqa: E402


def generate_batch_json(
    ara_json_input,
    article_id: Optional[int] = None,
    n_clips: int = 3,
    duracion_por_clip: float = 8.0,
    project_name: Optional[str] = None,
) -> dict:
    """
    Genera el JSON de batch unificado para flow.py con encadenamiento nativo.

    El primer clip referencia la imagen maestra; cada clip posterior referencia
    el video anterior para continuidad visual sin necesidad de extracción manual
    de frames.

    Retorna:
        dict con estructura compatible con flow.py batch:
        {
          "project": "<nombre>",
          "defaults": {"ratio": "9:16", "video_model": "Veo 3.1 - Lite"},
          "jobs": [
            {"type": "video", "name": "..._clip1", "prompt": "...",
             "start": "<img_maestra>"},
            {"type": "video", "name": "..._clip2", "prompt": "...",
             "start": "..._clip1.mp4"},
          ]
        }
    """
    prompts = assemble_prompts_multi_clip(
        ara_json_input, n_clips=n_clips, duracion_por_clip=duracion_por_clip
    )

    if not prompts:
        raise ValueError("No se generaron prompts para ningún clip")

    # Determinar ID y prefijo
    raw_id = article_id or _extract_article_id(ara_json_input)
    prefix = project_name or f"a{raw_id}" if raw_id else f"batch_{int(time.time())}"
    safe_prefix = re.sub(r"[^A-Za-z0-9_-]", "_", str(prefix))

    # Obtener referencia de imagen maestra desde el entorno o usar nombre por defecto
    img_ref = _get_master_image_ref()

    jobs = []
    prev_job_name = None

    for p in prompts:
        job = {
            "type": "video",
            "name": f"{safe_prefix}_clip{p['clip_index']}",
            "prompt": p["prompt_veo"],
        }

        # Encadenamiento: primer clip usa imagen maestra, siguientes usan el clip anterior
        if prev_job_name is None:
            # Primer clip: apuntar a imagen de referencia si existe
            if img_ref:
                job["start"] = img_ref
        else:
            # Clips subsiguientes: apuntar al video anterior
            job["start"] = prev_job_name + ".mp4"

        jobs.append(job)
        prev_job_name = job["name"]

    return {
        "project": safe_prefix,
        "defaults": {
            "ratio": "9:16",
            "video_model": "Veo 3.1 - Lite",
        },
        "jobs": jobs,
    }


def save_batch_json(batch_data: dict, output_path: str) -> str:
    """Escribe el JSON de batch en disco y retorna la ruta absoluta."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(batch_data, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path.resolve())


def _extract_article_id(ara_json_input) -> Optional[int]:
    """Extrae article_id del ara_json o retorna None."""
    try:
        parsed = ara_json_input if isinstance(ara_json_input, dict) else json.loads(ara_json_input)
        c = parsed.get("contenido", {})
        aid = c.get("article_id") or c.get("id")
        return int(aid) if aid else None
    except Exception:
        return None


def _get_master_image_ref() -> Optional[str]:
    """
    Retorna la ruta de la imagen maestra de Ara si está configurada en el entorno.
    Devuelve None si no hay configuración disponible.
    """
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return None
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("FLOW_BASE_IMAGE_REF") and "=" in line:
                val = line.split("=", 1)[1].strip().strip("\"'")
                if val and Path(val).exists():
                    return val
    return None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Genera JSON de batch unificado para Flow")
    parser.add_argument("--input", required=True, help="Path a ara_json")
    parser.add_argument("--article-id", type=int, default=None, help="ID del artículo")
    parser.add_argument("--output", required=True, help="Ruta de salida del JSON")
    parser.add_argument("--n-clips", type=int, default=3, help="Cantidad de clips (default: 3)")
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        ara_json = json.load(f)

    batch = generate_batch_json(
        ara_json_input=ara_json,
        article_id=args.article_id,
        n_clips=args.n_clips,
    )
    saved = save_batch_json(batch, args.output)
    print(f"[BatchWorkflow] JSON generado: {saved}")
    print(json.dumps(batch, indent=2, ensure_ascii=False))
