"""
Worker principal de la Fábrica de Videos Ara.
Polling sobre Supabase, ensambla prompts, orquesta Flow (dry-run/literal) y QC por Telegram.
"""
from __future__ import annotations
import json
import os
import sys
import time
import argparse
from pathlib import Path
from typing import Dict, Optional
import requests as _req

# Agregar ruta del proyecto parent para imports del pipeline
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "saladillo-vivo-pro"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # .env del factory

from src.prompt_assembler import assemble_prompts
from src.flow_bridge import (
    FlowJob, check_skill_ready, dry_run_batch, generate_batch_json,
    run_live_batch, wait_cooldown, OUTPUT_BASE, SKILL_DIR, PROJECT_NAME,
)
from src.ffmpeg_composer import process_video as compose_video
from src.telegram_notifier import QCPayload, send_qc_request, notify_batch_complete
from agents.storage_agent import StorageAgent

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
POLL_INTERVAL_MINUTES: int = int(os.environ.get("POLL_INTERVAL_MINUTES", "5"))
DRY_RUN_MODE: bool = os.environ.get("FACTORIA_DRY_RUN", "true").lower() in ("true", "1", "yes")


def _fetch_article(storage: StorageAgent, article_id: int) -> Optional[dict]:
    """Trae un artículo completo desde Supabase."""
    resp = _req.get(
        f"{storage.base_rest_url}",
        params={'select': '*', 'id': f'eq.{article_id}'},
        headers=storage.get_headers(False),
        timeout=15,
    )
    if resp.status_code == 200 and resp.json():
        return resp.json()[0]
    return None


def fetch_pending_jobs(storage: StorageAgent, limit: int = 5) -> list[dict]:
    """Trae artículos con ara_json NO NULL que aún no tengan job en ara_video_jobs."""
    # Buscar artículos con ara_json no nulo y enviado_a_ara=true
    resp = _req.get(
        f"{storage.base_rest_url}",
        params={
            'select': '*',
            'ara_json': 'neq.null',
            'enviado_a_ara': 'eq.true',
            'order': 'created_at.desc',
            'limit': str(limit),
        },
        headers=storage.get_headers(False),
        timeout=15,
    )
    if resp.status_code != 200 or not resp.json():
        return []
    # Filtrar los que ya tienen job en ara_video_jobs
    known_job_ids = set()
    jobs_resp = _req.get(
        f"{storage.url}/rest/v1/ara_video_jobs?id=in.({','.join(map(str, [a['id'] for a in resp.json()]))})",
        params={'select': 'article_id'},
        headers=storage.get_headers(False),
        timeout=15,
    )
    if jobs_resp.status_code == 200 and jobs_resp.json():
        known_job_ids = {j['article_id'] for j in jobs_resp.json()}
    return [a for a in resp.json() if a['id'] not in known_job_ids]


def get_or_create_job(storage: StorageAgent, article_id: int) -> dict:
    """Crea o devuelve registro existente en ara_video_jobs."""
    try:
        resp = _req.get(
            f"{storage.url}/rest/v1/ara_video_jobs?article_id=eq.{article_id}",
            params={'select': '*'},
            headers=storage.get_headers(False),
            timeout=15,
        )
        if resp.status_code == 200 and resp.json():
            return resp.json()[0]
    except Exception as e:
        print(f"[Worker] Warning consultando jobs: {e}")
    # Crear nuevo
    try:
        create_resp = _req.post(
            f"{storage.url}/rest/v1/ara_video_jobs",
            json={'article_id': article_id, 'estado': 'pendiente_ensamblado'},
            headers=storage.get_headers(True),
            timeout=15,
        )
        if create_resp.status_code in (200, 201):
            return create_resp.json()[0]
        if create_resp.status_code == 404:
            print(f"[Worker] Tabla ara_video_jobs no existe aún — ejecuta la migración SQL primero.")
            return {'id': None, 'article_id': article_id, 'estado': 'pendiente_ensamblado', '_mock': True}
        raise RuntimeError(f"Error creando job: {create_resp.text[:200]}")
    except Exception as e:
        print(f"[Worker] Error creando job: {e}")
        return {'id': None, 'article_id': article_id, 'estado': 'pendiente_ensamblado', '_mock': True}


def update_job_status(storage: StorageAgent, job_id: int, **fields) -> None:
    """Actualiza campos de un job en ara_video_jobs."""
    resp = _req.patch(
        f"{storage.url}/rest/v1/ara_video_jobs?id=eq.{job_id}",
        json=fields,
        headers=storage.get_headers(True),
        timeout=15,
    )
    if resp.status_code not in (200, 204):
        print(f"[Worker] Warning updating job {job_id}: {resp.status_code} {resp.text[:100]}")


def process_single_article(
    storage: StorageAgent,
    article_data: dict,
    dry_run: bool = True,
) -> dict:
    """Procesa un artículo completo: ensamble → Flow → FFmpeg → entrega."""
    article_id = article_data['id']
    ara_json = article_data.get('ara_json')

    if not ara_json:
        return {"ok": False, "error": "Sin ara_json"}

    # Assemblar prompts
    parsed_ara = json.loads(ara_json) if isinstance(ara_json, str) else ara_json
    meta = parsed_ara.get('meta_ara', {})
    tenor = meta.get('tenor', 'informativo_servicio')
    requires_approval = meta.get('requiere_aprobacion_sensible', False)

    volanta = parsed_ara.get('contenido', {}).get('volanta_zocalo', 'Ara · Saladillo Vivo')
    titulo = parsed_ara.get('contenido', {}).get('titulo_zocalo', '')

    prompts = assemble_prompts(
        ara_json_input=parsed_ara,
        volanta_zocalo=volanta,
        titulo_zocalo=titulo,
        allow_smile=not requires_approval,
    )

    if not prompts['valid']:
        return {
            "ok": False,
            "error": f"Prompt assembly failed: {prompts['errors']}",
            "prompt_debug": prompts.get('_debug', {}),
        }

    job = get_or_create_job(storage, article_id)
    job_id = job['id']

    # Actualizar prompts en el job
    update_job_status(storage, job_id,
                      prompt_imagen=prompts['prompt_imagen'],
                      prompt_veo=prompts['prompt_veo'])

    # QC sensible
    if requires_approval:
        update_job_status(storage, job_id, estado="esperando_qc_telegram")
        qc_payload = QCPayload(
            article_id=article_id,
            tenor=tenor,
            guion_ara=parsed_ara['contenido']['guion_ara'],
            volanta_zocalo=volanta,
            titulo_zocalo=titulo,
            prompt_veo=prompts['prompt_veo'],
            requires_approval=True,
        )
        send_qc_request(qc_payload)
        return {"ok": True, "status": "waiting_for_qc", "job_id": job_id}

    if dry_run:
        job_flow = FlowJob(
            job_id=job_id,
            article_id=article_id,
            prompt_imagen=prompts['prompt_imagen'],
            prompt_veo=prompts['prompt_veo'],
            estado_actual="generando_imagen",
        )
        batch_payload = generate_batch_json(job_flow)
        result = dry_run_batch(batch_payload)
        update_job_status(storage, job_id, estado="aprobado", batch_report_json=result)
        return {
            **result,
            "job_id": job_id,
            "article_id": article_id,
            "prompt_imagen": prompts['prompt_imagen'],
            "prompt_veo": prompts['prompt_veo'],
            "word_count_prompt_veo": len(prompts['prompt_veo'].split()),
            "tenor": tenor,
        }

    # LIVE MODE (no usado en desarrollo/testing)
    return _process_live(storage, job_id, article_id, prompts, parsed_ara)


def _process_live(storage: StorageAgent, job_id: int, article_id: int,
                  prompts: dict, parsed_ara: dict) -> dict:
    """Flujo completo live: imagen → video → FFmpeg → Telegram delivery."""
    job = FlowJob(job_id, article_id, prompts['prompt_imagen'], prompts['prompt_veo'], "generando_imagen")
    batch_file_path = str(OUTPUT_BASE / f"batch_job_{job_id}.json")
    batch_payload = generate_batch_json(job)
    with open(batch_file_path, "w", encoding="utf-8") as f:
        json.dump(batch_payload, f, indent=2, ensure_ascii=False)

    result = run_live_batch(batch_file_path)
    if not result['ok']:
        update_job_status(storage, job_id, estado="error_reintentable", ultimo_error=str(result))
        return {"ok": False, "error": "Flow generation failed", "details": result}

    wait_cooldown()

    # Leer batch_report.json generado por flow.py
    project_dir = SKILL_DIR / "outputs" / PROJECT_NAME
    report_path = project_dir / "batch_report.json"
    batch_report = None
    raw_video_path = None
    if report_path.exists():
        try:
            batch_report = json.loads(report_path.read_text())
        except Exception as e:
            print(f"[Worker] Error leyendo batch_report: {e}")

    # Determinar video crudo: usar el generado por Flow si existe, sino mock
    for entry in (batch_report or []):
        if entry.get("type") == "video" and entry.get("file"):
            raw_video_path = entry["file"]
            break

    if not raw_video_path or not Path(raw_video_path).exists():
        print("[Worker] Video crudo de Flow no encontrado, usando testsrc como fallback.")
        raw_video_path = str(OUTPUT_BASE / f"testsrc_{article_id}.mp4")
        if not Path(raw_video_path).exists():
            import subprocess
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi", "-i",
                f"testsrc2=size=1920x1080:duration=8:rate=30",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", raw_video_path
            ], capture_output=True)

    output_path = str(OUTPUT_BASE / f"output_{article_id}_final.mp4")
    compose_video(raw_video_path, output_path,
                  volanta_text=parsed_ara['contenido']['volanta_zocalo'],
                  titulo_text=parsed_ara['contenido']['titulo_zocalo'])

    # Subir a Google Drive (no bloqueante — error se registra pero no falla el job)
    from src.google_drive_uploader import upload_video_to_drive
    drive_result = upload_video_to_drive(output_path, article_id, tenor=meta.get('tenor', 'informativo'))
    drive_ok = drive_result.get("ok", False)
    drive_url = drive_result.get("remote_url") or ""
    if not drive_ok:
        print(f"[Worker] ⚠️ Upload a Drive falló: {drive_result.get('error','')}")

    # Actualizar estado y guardar reporte
    update_job_status(
        storage, job_id,
        estado="listo_entregado",
        video_final_path=output_path,
        batch_report_json=batch_report,
        drive_upload_ok=drive_ok,
        drive_remote_url=drive_url,
    )

    # Notificar entrega por Telegram
    notify_batch_complete(1, 1, 0)

    return {"ok": True, "job_id": job_id, "output_path": output_path, "batch_report": batch_report}


def main():
    parser = argparse.ArgumentParser(description="Ara Video Factory Worker")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Modo prueba sin créditos (default)")
    parser.add_argument("--live", action="store_true", help="Ejecutar generación real")
    parser.add_argument("--article-id", type=int, help="Procesar solo este artículo")
    parser.add_argument("--poll", action="store_true", help="Modo polling continuo")
    args = parser.parse_args()

    dry_run = not args.live

    print("=" * 60)
    print("ARA VIDEO FACTORY — Worker v1.0")
    print(f"Dry-run mode: {'ON' if dry_run else 'OFF'}")
    print("=" * 60)

    skill_ok, skill_msg = check_skill_ready()
    if not skill_ok:
        if dry_run:
            print(f"[INFO] Skill no lista (sin login activo): {skill_msg}")
            print("       Continuando en modo dry-run (sin ejecutar Chrome ni Flow).")
        else:
            print(f"[ERROR] Skill no lista — se requiere 'flow.py login' manual antes de correr en vivo.")
            exit(1)
    else:
        print(f"Skill ready: OK | {skill_msg}")

    storage = StorageAgent()

    if args.article_id:
        article = _fetch_article(storage, args.article_id)
        if not article:
            print(f"Artículo {args.article_id} no encontrado en Supabase")
            return
        result = process_single_article(storage, article, dry_run=dry_run)
        print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
        return

    if args.poll:
        from datetime import datetime
        print(f"Polling cada {POLL_INTERVAL_MINUTES} minutos...")
        while True:
            try:
                jobs = fetch_pending_jobs(storage)
                print(f"[{datetime.now().isoformat()}] Pending jobs: {len(jobs)}")
                for job_art in jobs:
                    process_single_article(storage, job_art, dry_run=dry_run)
                    if not dry_run:
                        wait_cooldown()
                time.sleep(POLL_INTERVAL_MINUTES * 60)
            except KeyboardInterrupt:
                print("\nWorker detenido.")
                break
            except Exception as e:
                print(f"Error en polling loop: {e}")
                time.sleep(60)
        return

    # Default: procesar todos los pendientes
    jobs = fetch_pending_jobs(storage)
    print(f"Artículos pendientes para factoría: {len(jobs)}")

    success = 0
    failures = []
    for art in jobs:
        try:
            result = process_single_article(storage, art, dry_run=dry_run)
            if result.get('ok'):
                success += 1
            else:
                failures.append({"id": art['id'], "error": result.get('error')})
        except Exception as e:
            failures.append({"id": art['id'], "error": str(e)})

    print(f"\nBatch complete: {success} OK, {len(failures)} errors")
    if failures:
        print("Failures:", failures)

    notify_batch_complete(len(jobs), success, len(failures))


if __name__ == "__main__":
    main()
