"""
Puente controlado con google-flow-skill-v1 (CLI flow.py).
Soporta --dry-run para pruebas sin gastar créditos.
Genera payloads JSON para flow.py batch y maneja respuestas/errores.
"""
from __future__ import annotations
import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
FABRIC_DIR = Path(__file__).resolve().parent.parent
SKILL_DIR = Path(os.environ.get("FLOW_SKILL_DIR", "/home/matias/.hermes/skills/google-flow-skill"))
VENDOR_DIR = FABRIC_DIR / "vendor" / "google-flow-skill-v1"
ACTUAL_SKILL_DIR = VENDOR_DIR if VENDOR_DIR.exists() else SKILL_DIR

FLOW_SCRIPT = ACTUAL_SKILL_DIR / "flow.py"
COOLDOWN_SECONDS: int = int(os.environ.get("FLOW_COOLDOWN_SECONDS", "90"))
VIDEO_MODEL: str = os.environ.get("FLOW_VIDEO_MODEL", "Veo 3.1 - Lite")
PROJECT_NAME: str = os.environ.get("FLOW_PROJECT_NAME", "saladillo-vivo-ara")
OUTPUT_BASE = FABRIC_DIR / "output"
OUTPUT_BASE.mkdir(parents=True, exist_ok=True)


@dataclass
class FlowJob:
    job_id: int
    article_id: int
    prompt_imagen: str
    prompt_veo: str
    estado_actual: str


def check_skill_ready() -> tuple[bool, str]:
    """Verifica que google-flow-skill esté listo."""
    if not FLOW_SCRIPT.exists():
        return False, f"Flow script no encontrado en {FLOW_SCRIPT}"
    # Verificar perfil de sesión
    profile_dir = ACTUAL_SKILL_DIR / "session" / "flowbot-profile"
    if not profile_dir.exists():
        return False, "session/flowbot-profile/ no existe — requiere 'flow.py login' manual"
    return True, "OK"


def status_check() -> dict:
    """Ejecuta flow.py status para verificar login activo."""
    try:
        result = subprocess.run(
            ["python3", str(FLOW_SCRIPT), "status"],
            capture_output=True, text=True, timeout=30,
        )
        return {"ok": result.returncode == 0, "stdout": result.stdout, "stderr": result.stderr}
    except FileNotFoundError:
        return {"ok": False, "error": "Python/flow no encontrado"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout en status check"}


def generate_batch_json(job: FlowJob) -> dict:
    """
    Genera el payload JSON para flow.py batch.
    Encadena imagen → video con start=<imagen_result>.
    Estructura compatible con examples/guion_ejemplo.json de google-flow-skill.
    """
    return {
        "project": PROJECT_NAME,
        "defaults": {
            "ratio": "9:16",
            "image_model": "Nano Banana Pro",
            "video_model": VIDEO_MODEL,
        },
        "jobs": [
            {
                "type": "image",
                "name": f"img_{job.article_id}_frame",
                "prompt": job.prompt_imagen,
            },
            {
                "type": "video",
                "name": f"vid_{job.article_id}_clip",
                "start": f"img_{job.article_id}_frame",
                "prompt": job.prompt_veo,
            },
        ],
    }


def write_batch_json(payload: dict, output_path: Optional[str] = None) -> str:
    """Escribe el payload JSON en un archivo temporal."""
    # Compatibilidad con formato nuevo (jobs) y viejo (entries + metadata)
    job_id = payload.get("metadata", {}).get("job_id", 0)
    path = output_path or str(OUTPUT_BASE / f"batch_job_{job_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return path


def dry_run_batch(batch_payload: dict) -> dict:
    """Simula una corrida de batch (sin ejecutar Chrome ni gastar créditos)."""
    batch_file = write_batch_json(batch_payload)
    print(f"[FlowBridge DRY-RUN] Payload escrito a: {batch_file}")
    print(f"[FlowBridge DRY-RUN] Project: {PROJECT_NAME}")
    print(f"[FlowBridge DRY-RUN] Video model: {VIDEO_MODEL}")
    print(f"[FlowBridge DRY-RUN] Duración objetivo: 8s")
    print(f"[FlowBridge DRY-RUN] Entries: {len(batch_payload['jobs'])}")
    print("\n─── PROMPT IMAGEN ───")
    img_job = next(j for j in batch_payload["jobs"] if j["type"] == "image")
    print(img_job["prompt"])
    print("\n─── PROMPT VIDEO ───")
    vid_job = next(j for j in batch_payload["jobs"] if j["type"] == "video")
    print(vid_job["prompt"])
    print(f"\n─── RESUMEN ───")
    print(f"Word count prompt_veo: {len(vid_job['prompt'].split())}")
    return {
        "dry_run": True,
        "batch_file": batch_file,
        "project": PROJECT_NAME,
        "entries_count": len(batch_payload["jobs"]),
        "video_model": VIDEO_MODEL,
        "image_prompt_wordcount": len(img_job["prompt"].split()),
        "video_prompt_wordcount": len(vid_job["prompt"].split()),
        "status": "ready_for_real_run_if_login_active",
    }


def run_live_batch(batch_file: str) -> dict:
    """Ejecuta flow.py batch real (consume créditos)."""
    try:
        cmd = ["python3", str(FLOW_SCRIPT), "batch", batch_file]
        print(f"[FlowBridge] Ejecutando: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout[:2000],
            "stderr": result.stderr[:500],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def wait_cooldown() -> None:
    """Pausa configurable entre generaciones para evitar rate limits."""
    print(f"[FlowBridge] Cooldown {COOLDOWN_SECONDS}s...")
    time.sleep(COOLDOWN_SECONDS)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Flow Bridge CLI")
    parser.add_argument("--mode", choices=["status", "dry-run", "live"], default="status")
    parser.add_argument("--job-id", type=int, help="ID del job en ara_video_jobs")
    args = parser.parse_args()
    
    ready, msg = check_skill_ready()
    print(f"Skill ready: {ready} | {msg}")
    
    if args.mode == "status":
        print(json.dumps(status_check(), indent=2))
    elif args.mode in ("dry-run", "live"):
        if not ready:
            print("ERROR: Skill no lista. Correr 'flow.py login' manualmente.")
            exit(1)
        # Mock job for testing
        mock_job = FlowJob(
            job_id=args.job_id or 1,
            article_id=900,
            prompt_imagen="Vertical 9:16, close-up medio...",
            prompt_veo='[El personaje dice "...", voz femenina, sincronización labial precisa]; ...',
            estado_actual="pendiente_ensamblado",
        )
        payload = generate_batch_json(mock_job)
        if args.mode == "dry-run":
            result = dry_run_batch(payload)
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            bf = write_batch_json(payload)
            result = run_live_batch(bf)
            print(json.dumps(result, indent=2, ensure_ascii=False))
