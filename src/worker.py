"""Worker principal de la Fábrica de Videos Ara.

Polling sobre Supabase, ensambla prompts, orquesta Flow (dry-run/live) y QC por Telegram.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests as _req

# Agregar ruta del proyecto parent para imports del pipeline
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "saladillo-vivo-pro"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # .env del factory

from src.prompt_assembler import assemble_prompts
from src import flow_bridge
from src.flow_bridge import (
    FlowJob,
    check_skill_ready,
    dry_run_batch,
    generate_image_only_json,
    run_live_batch,
    wait_cooldown,
    OUTPUT_BASE,
    SKILL_DIR,
    PROJECT_NAME,
)
# Importar el generador unificado de batch multi-clip
from src.batch_workflow_generator import generate_batch_json as generate_multi_clip_batch
from src.ffmpeg_composer import process_video as compose_video
from src.telegram_notifier import QCPayload, send_qc_request, notify_batch_complete
from src.google_drive_uploader import upload_video_to_drive
from agents.storage_agent import StorageAgent

# ---------------------------------------------------------------------------
# Constantes y tipos
# ---------------------------------------------------------------------------
POLL_INTERVAL_MINUTES: int = int(os.environ.get("POLL_INTERVAL_MINUTES", "5"))
DRY_RUN_MODE: bool = os.environ.get("FACTORIA_DRY_RUN", "true").lower() in ("true", "1", "yes")
ASSET_BUCKET: str = os.environ.get("ASSET_BUCKET", "video-factory-assets")
BATCH_REPORT_PATH = Path(SKILL_DIR) / "outputs" / PROJECT_NAME / "batch_report.json"


@dataclass(slots=True)
class JobResult:
    """Resultado estandarizado de procesamiento de job."""
    ok: bool
    job_id: Optional[int] = None
    article_id: Optional[int] = None
    error: Optional[str] = None
    dry_run: bool = False
    status: Optional[str] = None
    output_path: Optional[str] = None
    batch_report: Optional[Any] = None
    prompt_imagen: Optional[str] = None
    prompt_veo: Optional[str] = None
    master_image_url: Optional[str] = None
    image_path: Optional[str] = None
    details: Optional[dict] = None
    word_count_prompt_veo: int = 0
    tenor: Optional[str] = None


def public_bucket_url(storage: StorageAgent, bucket: str, path: str) -> str:
    """URL pública de un objeto en Supabase Storage."""
    return f"{storage.url}/storage/v1/object/public/{bucket}/{path}"


def upload_to_bucket(
    storage: StorageAgent,
    bucket: str,
    path: str,
    file_bytes: bytes,
    content_type: str = "image/png",
) -> bool:
    """Sube bytes a Supabase Storage. Devuelve True si ok."""
    headers = storage.get_headers(False)
    headers["Content-Type"] = content_type
    url = f"{storage.url}/storage/v1/object/{bucket}/{path}"
    resp = _req.post(url, headers=headers, data=file_bytes, timeout=60)
    if resp.status_code not in (200, 201):
        print(f"[Worker] Upload a Storage falló ({resp.status_code}): {resp.text[:200]}")
        return False
    return True


def _fetch_article(storage: StorageAgent, article_id: int) -> Optional[dict]:
    """Trae un artículo completo desde Supabase."""
    resp = _req.get(
        f"{storage.base_rest_url}",
        params={"select": "*", "id": f"eq.{article_id}"},
        headers=storage.get_headers(False),
        timeout=15,
    )
    if resp.status_code == 200 and resp.json():
        return resp.json()[0]
    return None


def recover_stuck_jobs(storage: StorageAgent, max_age_minutes: int = 30) -> int:
    """Reinicia trabajos atrapados en estados intermedios hace más de max_age_minutes."""
    now = datetime.now().replace(tzinfo=None)  # naive para comparar
    recovered = 0
    for estado_target in ("generando_imagen", "pendiente_ensamblado"):
        resp = _req.get(
            f"{storage.url}/rest/v1/ara_video_jobs",
            params={
                "select": "id,article_id,estado,created_at",
                "estado": f"eq.{estado_target}",
                "order": "created_at.asc",
            },
            headers=storage.get_headers(False),
            timeout=15,
        )
        if resp.status_code != 200 or not resp.json():
            continue
        for job in resp.json():
            try:
                raw = job["created_at"].replace("Z", "+00:00")
                created = datetime.fromisoformat(raw).replace(tzinfo=None)
                age_minutes = (now - created).total_seconds() / 60
                if age_minutes > max_age_minutes:
                    print(f"[Worker] Recovering stuck job #{job['id']} (art {job['article_id']}, estado={job['estado']}, age={age_minutes:.0f}min)")
                    update_job_status(
                        storage, job["id"],
                        estado="pendiente_ensamblado",
                        ultimo_error=f"recovery_timeout:{age_minutes:.0f}min",
                    )
                    recovered += 1
            except Exception as e:
                print(f"[Worker] Error recovering job {job['id']}: {e}")
    return recovered


def recover_stuck_video_jobs(storage: StorageAgent, max_age_minutes: int = 30) -> int:
    """Recupera y reinicia trabajos de video atascados en pendiente_ensamblado."""
    now = datetime.now().replace(tzinfo=None)  # naive para comparar
    recovered = 0
    resp = _req.get(
        f"{storage.url}/rest/v1/ara_video_jobs",
        params={
            "select": "id,article_id,estado,created_at",
            "estado": "eq.pendiente_ensamblado",
            "order": "created_at.asc",
        },
        headers=storage.get_headers(False),
        timeout=15,
    )
    if resp.status_code != 200 or not resp.json():
        return 0
    for job in resp.json():
        try:
            raw = job["created_at"].replace("Z", "+00:00")
            created = datetime.fromisoformat(raw).replace(tzinfo=None)
            age_minutes = (now - created).total_seconds() / 60
            if age_minutes > max_age_minutes:
                print(f"[Worker] Recovering stuck video job #{job['id']} (art {job['article_id']}, age={age_minutes:.0f}min)")
                update_job_status(
                    storage, job["id"],
                    estado="pendiente_imagen",
                    ultimo_error=f"recovery_timeout:{age_minutes:.0f}min",
                )
                recovered += 1
        except Exception as e:
            print(f"[Worker] Error recovering video job {job['id']}: {e}")
    return recovered


def fetch_pending_jobs(storage: StorageAgent, limit: int = 5) -> list[dict]:
    """Trae artículos con ara_json NO NULL que aún no tengan job en ara_video_jobs."""
    resp = _req.get(
        f"{storage.base_rest_url}",
        params={
            "select": "*",
            "ara_json": "neq.null",
            "enviado_a_ara": "eq.true",
            "order": "created_at.desc",
            "limit": str(limit),
        },
        headers=storage.get_headers(False),
        timeout=15,
    )
    if resp.status_code != 200 or not resp.json():
        return []

    # Filtrar los que ya tienen job en ara_video_jobs
    article_ids = [a["id"] for a in resp.json()]
    if not article_ids:
        return []

    jobs_resp = _req.get(
        f"{storage.url}/rest/v1/ara_video_jobs",
        params={"select": "article_id", "article_id": f"in.({','.join(map(str, article_ids))})"},
        headers=storage.get_headers(False),
        timeout=15,
    )
    known_job_ids = set()
    if jobs_resp.status_code == 200 and jobs_resp.json():
        known_job_ids = {j["article_id"] for j in jobs_resp.json()}

    return [a for a in resp.json() if a["id"] not in known_job_ids]


def fetch_pending_image_jobs(storage: StorageAgent, limit: int = 5) -> list[dict]:
    """Trae trabajos de IMAGEN maestra pendientes (estado='pendiente_imagen')."""
    resp = _req.get(
        f"{storage.url}/rest/v1/ara_video_jobs",
        params={
            "select": "id,article_id",
            "estado": "eq.pendiente_imagen",
            "order": "created_at.desc",
            "limit": str(limit),
        },
        headers=storage.get_headers(False),
        timeout=15,
    )
    if resp.status_code != 200 or not resp.json():
        return []
    return resp.json()


def get_or_create_job(storage: StorageAgent, article_id: int) -> dict:
    """Crea o devuelve registro existente en ara_video_jobs."""
    try:
        resp = _req.get(
            f"{storage.url}/rest/v1/ara_video_jobs?article_id=eq.{article_id}",
            params={"select": "*"},
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
            json={"article_id": article_id, "estado": "pendiente_ensamblado"},
            headers=storage.get_headers(True),
            timeout=15,
        )
        if create_resp.status_code in (200, 201):
            return create_resp.json()[0]
        if create_resp.status_code == 404:
            print("[Worker] Tabla ara_video_jobs no existe aún — ejecuta la migración SQL primero.")
            return {"id": None, "article_id": article_id, "estado": "pendiente_ensamblado", "_mock": True}
        raise RuntimeError(f"Error creando job: {create_resp.text[:200]}")
    except Exception as e:
        print(f"[Worker] Error creando job: {e}")
        return {"id": None, "article_id": article_id, "estado": "pendiente_ensamblado", "_mock": True}


def update_job_status(storage: StorageAgent, job_id: int, **fields: Any) -> None:
    """Actualiza campos de un job en ara_video_jobs."""
    resp = _req.patch(
        f"{storage.url}/rest/v1/ara_video_jobs?id=eq.{job_id}",
        json=fields,
        headers=storage.get_headers(True),
        timeout=15,
    )
    if resp.status_code not in (200, 204):
        print(f"[Worker] Warning updating job {job_id}: {resp.status_code} {resp.text[:100]}")


def _read_batch_report() -> Optional[list[dict]]:
    """Lee y parsea batch_report.json si existe."""
    if not BATCH_REPORT_PATH.exists():
        return None
    try:
        return json.loads(BATCH_REPORT_PATH.read_text())
    except Exception as e:
        print(f"[Worker] Error leyendo batch_report: {e}")
        return None


def _find_generated_file(batch_report: Optional[list[dict]], file_type: str) -> Optional[str]:
    """Busca en batch_report un archivo generado del tipo indicado."""
    if not batch_report:
        return None
    for entry in batch_report:
        if entry.get("type") == file_type and entry.get("file") and entry.get("ok"):
            return entry["file"]
    return None


def _ensure_test_video(article_id: int) -> str:
    """Genera un video testsrc si no existe (fallback para dry-run/live sin Flow)."""
    import subprocess
    test_path = str(OUTPUT_BASE / f"testsrc_{article_id}.mp4")
    if Path(test_path).exists():
        return test_path
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        f"testsrc2=size=1920x1080:duration=8:rate=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", test_path
    ], capture_output=True)
    return test_path


def process_image_only_job(
    storage: StorageAgent,
    article_id: int,
    dry_run: bool = True,
) -> JobResult:
    """Genera SOLO la imagen maestra (tipo='imagen'), la sube a Storage y actualiza el job."""
    article = _fetch_article(storage, article_id)
    if not article:
        return JobResult(ok=False, article_id=article_id, error=f"Artículo {article_id} no encontrado")

    ara_json = article.get("ara_json")
    if not ara_json:
        return JobResult(ok=False, article_id=article_id, error="Sin ara_json")

    parsed_ara = json.loads(ara_json) if isinstance(ara_json, str) else ara_json
    contenido = parsed_ara.get("contenido", {})
    meta = parsed_ara.get("meta_ara", {})
    requires_approval = meta.get("requiere_aprobacion_sensible", False)

    prompts = assemble_prompts(
        ara_json_input=parsed_ara,
        volanta_zocalo=contenido.get("volanta_zocalo", "Ara · Saladillo Vivo"),
        titulo_zocalo=contenido.get("titulo_zocalo", ""),
        allow_smile=not requires_approval,
    )
    if not prompts["valid"]:
        return JobResult(ok=False, article_id=article_id, error=f"Prompt assembly failed: {prompts['errors']}")

    job = get_or_create_job(storage, article_id)
    job_id = job.get("id")
    if job_id is None:
        return JobResult(ok=False, article_id=article_id, error="No se pudo crear/obtener job_id")

    if dry_run:
        return JobResult(
            ok=True, dry_run=True, job_id=job_id, article_id=article_id,
            prompt_imagen=prompts["prompt_imagen"],
        )

    # LIVE: generar imagen maestra
    update_job_status(storage, job_id, tipo="imagen", estado="generando_imagen", prompt_imagen=prompts["prompt_imagen"])

    batch_file = str(OUTPUT_BASE / f"batch_img_{job_id}.json")
    payload = generate_image_only_json(article_id, prompts["prompt_imagen"])
    with open(batch_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    result = run_live_batch(batch_file)
    if not result["ok"]:
        update_job_status(storage, job_id, estado="error_reintentable", ultimo_error=str(result))
        return JobResult(ok=False, article_id=article_id, error="Flow image generation failed", details=result)

    batch_report = _read_batch_report()
    image_path = _find_generated_file(batch_report, "image")

    if not image_path or not Path(image_path).exists():
        update_job_status(storage, job_id, estado="error_reintentable", ultimo_error="Imagen no encontrada en batch_report")
        return JobResult(ok=False, article_id=article_id, error="Imagen maestra no generada por Flow")

    # Subir a Storage y registrar URL pública
    dest_path = f"master-images/{job_id}.png"
    with open(image_path, "rb") as fh:
        upload_ok = upload_to_bucket(storage, ASSET_BUCKET, dest_path, fh.read())
    master_url = public_bucket_url(storage, ASSET_BUCKET, dest_path) if upload_ok else None

    update_job_status(
        storage, job_id,
        estado="imagen_lista" if upload_ok else "error_reintentable",
        master_image_url=master_url,
        imagen_compuesta_path=image_path,
        batch_report_json=batch_report,
    )

    return JobResult(
        ok=upload_ok, job_id=job_id, article_id=article_id,
        master_image_url=master_url, image_path=image_path,
        batch_report=batch_report,
    )


def process_pending_image_jobs(storage: StorageAgent, dry_run: bool = True, limit: int = 5) -> dict:
    """Procesa todos los trabajos de imagen maestra pendientes."""
    jobs = fetch_pending_image_jobs(storage, limit=limit)
    success = 0
    failures = []
    for job in jobs:
        try:
            result = process_image_only_job(storage, job["article_id"], dry_run=dry_run)
            if result.ok:
                success += 1
            else:
                failures.append({"article_id": job["article_id"], "error": result.error})
        except Exception as e:  # noqa: BLE001
            failures.append({"article_id": job["article_id"], "error": str(e)})
    return {"ok": len(failures) == 0, "total": len(jobs), "success": success, "failures": failures}


def _assemble_prompts_for_article(parsed_ara: dict) -> tuple[dict, str, str, str, bool]:
    """Extrae datos del ara_json y ensambla prompts. Retorna (prompts, tenor, volanta, titulo, requires_approval)."""
    meta = parsed_ara.get("meta_ara", {})
    tenor = meta.get("tenor", "informativo_servicio")
    requires_approval = meta.get("requiere_aprobacion_sensible", False)

    contenido = parsed_ara.get("contenido", {})
    volanta = contenido.get("volanta_zocalo", "Ara · Saladillo Vivo")
    titulo = contenido.get("titulo_zocalo", "")

    prompts = assemble_prompts(
        ara_json_input=parsed_ara,
        volanta_zocalo=volanta,
        titulo_zocalo=titulo,
        allow_smile=not requires_approval,
    )
    return prompts, tenor, volanta, titulo, requires_approval


def process_single_article(
    storage: StorageAgent,
    article_data: dict,
    dry_run: bool = True,
) -> JobResult:
    """Procesa un artículo completo: ensamble → Flow → FFmpeg → entrega."""
    article_id = article_data["id"]
    ara_json = article_data.get("ara_json")

    if not ara_json:
        return JobResult(ok=False, article_id=article_id, error="Sin ara_json")

    parsed_ara = json.loads(ara_json) if isinstance(ara_json, str) else ara_json
    prompts, tenor, volanta, titulo, requires_approval = _assemble_prompts_for_article(parsed_ara)

    if not prompts["valid"]:
        return JobResult(
            ok=False, article_id=article_id,
            error=f"Prompt assembly failed: {prompts['errors']}",
            prompt_imagen=prompts.get("prompt_imagen"),
            prompt_veo=prompts.get("prompt_veo"),
        )

    job = get_or_create_job(storage, article_id)
    job_id = job["id"]

    # Actualizar prompts en el job
    update_job_status(storage, job_id, prompt_imagen=prompts["prompt_imagen"], prompt_veo=prompts["prompt_veo"])

    # QC sensible
    if requires_approval:
        update_job_status(storage, job_id, estado="esperando_qc_telegram")
        qc_payload = QCPayload(
            article_id=article_id,
            tenor=tenor,
            guion_ara=parsed_ara["contenido"]["guion_ara"],
            volanta_zocalo=volanta,
            titulo_zocalo=titulo,
            prompt_veo=prompts["prompt_veo"],
            requires_approval=True,
        )
        send_qc_request(qc_payload)
        return JobResult(ok=True, status="waiting_for_qc", job_id=job_id, article_id=article_id)

    if dry_run:
        job_flow = FlowJob(
            job_id=job_id,
            article_id=article_id,
            prompt_imagen=prompts["prompt_imagen"],
            prompt_veo=prompts["prompt_veo"],
            estado_actual="generando_imagen",
        )
        # Usar nueva rutina de 3 clips (Plano americano -> Medio corto -> Medio)
        batch_payload = generate_multi_clip_batch(
            ara_json_input=parsed_ara,
            article_id=article_id,
            n_clips=3,
        )
        result = dry_run_batch(batch_payload)
        update_job_status(storage, job_id, estado="aprobado", batch_report_json=result)
        return JobResult(
            ok=True,  # dry-run siempre es exitoso (no falla)
            job_id=job_id,
            article_id=article_id,
            prompt_imagen=prompts["prompt_imagen"],
            prompt_veo=prompts["prompt_veo"],
            word_count_prompt_veo=len(prompts["prompt_veo"].split()) if prompts.get("prompt_veo") else 0,
            tenor=tenor,
            dry_run=True,
            batch_report=result,
        )

    # LIVE MODE - Rutina de 3 clips dinámicos
    batch_file_path = str(OUTPUT_BASE / f"batch_job_{job_id}.json")
    
    # Construir batch unificado de 3 clips con planos progresivos
    batch_payload = generate_multi_clip_batch(
        ara_json_input=parsed_ara,
        article_id=article_id,
        n_clips=3,
    )
    
    with open(batch_file_path, "w", encoding="utf-8") as f:
        json.dump(batch_payload, f, indent=2, ensure_ascii=False)

    result = run_live_batch(batch_file_path)
    if not result["ok"]:
        print(f"[Worker] Falló generación en Flow (posible saturación): {result.get('error') or result}")
        # Intentar resiliencia: generar clips locales fallback para poder subir algo al aire
        fallback_final = flow_bridge.assemble_fallback_batch(batch_file_path, article_id, n_clips=parsed_ara.get("n_clips", 3))
        if fallback_final and Path(fallback_final).exists():
            raw_video_path = fallback_final
            print(f"[Worker] Usando video de respaldo local: {raw_video_path}")
        else:
            update_job_status(storage, job_id, estado="error_reintentable", ultimo_error=str(result))
            return JobResult(ok=False, article_id=article_id, error="Flow generation failed + fallback failed", details=result)

    wait_cooldown()

    batch_report = _read_batch_report()
    raw_video_path = _find_generated_file(batch_report, "video")

    if not raw_video_path or not Path(raw_video_path).exists():
        print("[Worker] Video crudo de Flow no encontrado, usando testsrc como fallback.")
        raw_video_path = _ensure_test_video(article_id)

    output_path = str(OUTPUT_BASE / f"output_{article_id}_final.mp4")
    compose_video(raw_video_path, output_path, volanta_text=volanta, titulo_text=titulo)

    # Subir a Google Drive (no bloqueante — error se registra pero no falla el job)
    meta = parsed_ara.get("meta_ara", {})
    drive_result = upload_video_to_drive(output_path, article_id, tenor=meta.get("tenor", "informativo"))
    drive_ok = drive_result.get("ok", False)
    drive_url = drive_result.get("remote_url") or ""
    if not drive_ok:
        print(f"[Worker] ⚠️ Upload a Drive falló: {drive_result.get('error', '')}")

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

    return JobResult(ok=True, job_id=job_id, article_id=article_id, output_path=output_path, batch_report=batch_report)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ara Video Factory Worker")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Modo prueba sin créditos (default)")
    parser.add_argument("--live", action="store_true", help="Ejecutar generación real")
    parser.add_argument("--article-id", type=int, help="Procesar solo este artículo")
    parser.add_argument("--image", action="store_true", help="Generar SOLO la imagen maestra (tipo=imagen)")
    parser.add_argument("--poll", action="store_true", help="Modo polling continuo")
    args = parser.parse_args()

    dry_run = not args.live

    print("=" * 60)
    print("ARA VIDEO FACTORY — Worker v1.1")
    print(f"Dry-run mode: {'ON' if dry_run else 'OFF'}")
    print("=" * 60)

    skill_ok, skill_msg = check_skill_ready()
    if not skill_ok:
        if dry_run:
            print(f"[INFO] Skill no lista (sin login activo): {skill_msg}")
            print("       Continuando en modo dry-run (sin ejecutar Chrome ni Flow).")
        else:
            print(f"[ERROR] Skill no lista — se requiere 'flow.py login' manual antes de correr en vivo.")
            sys.exit(1)
    else:
        print(f"Skill ready: OK | {skill_msg}")

    storage = StorageAgent()

    if args.image:
        result = process_pending_image_jobs(storage, dry_run=dry_run)
        print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
        return

    if args.article_id:
        article = _fetch_article(storage, args.article_id)
        if not article:
            print(f"Artículo {args.article_id} no encontrado en Supabase")
            return
        result = process_single_article(storage, article, dry_run=dry_run)
        # JobResult con slots=True no tiene __dict__, usar vars() o convertir manualmente
        result_dict = {
            "ok": result.ok,
            "job_id": result.job_id,
            "article_id": result.article_id,
            "error": result.error,
            "dry_run": result.dry_run,
            "status": result.status,
            "output_path": result.output_path,
            "batch_report": result.batch_report,
            "prompt_imagen": result.prompt_imagen,
            "prompt_veo": result.prompt_veo,
            "master_image_url": result.master_image_url,
            "image_path": result.image_path,
            "details": result.details,
            "word_count_prompt_veo": result.word_count_prompt_veo,
            "tenor": result.tenor,
        }
        print(json.dumps(result_dict, indent=2, default=str, ensure_ascii=False))
        return

    if args.poll:
        print(f"Polling cada {POLL_INTERVAL_MINUTES} minutos...")
        while True:
            try:
                # Recuperar trabajos atrapados
                recovered_img = recover_stuck_jobs(storage, max_age_minutes=30)
                recovered_vid = recover_stuck_video_jobs(storage, max_age_minutes=30)
                total_recovered = recovered_img + recovered_vid
                if total_recovered:
                    print(f"[Worker] {total_recovered} trabajos recuperados de estado atascado ({recovered_img} imagen, {recovered_vid} video)")
                # Trabajos de imagen maestra
                img = process_pending_image_jobs(storage, dry_run=dry_run)
                # Trabajos de video nuevos
                jobs = fetch_pending_jobs(storage)
                print(f"[{datetime.now().isoformat()}] Pending video jobs: {len(jobs)} | image jobs: {img.get('success', 0)} ok | recovered: {total_recovered}")
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
            if result.ok:
                success += 1
            else:
                failures.append({"id": art["id"], "error": result.error})
        except Exception as e:
            failures.append({"id": art["id"], "error": str(e)})

    print(f"\nBatch complete: {success} OK, {len(failures)} errors")
    if failures:
        print("Failures:", failures)

    notify_batch_complete(len(jobs), success, len(failures))


if __name__ == "__main__":
    main()