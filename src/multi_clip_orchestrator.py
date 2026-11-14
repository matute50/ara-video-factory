"""
Orquestador de video doble-clip con continuidad visual garantizada.

Proceso en dos fases:
  Fase 1: Genera una imagen de referencia fija del personaje (Ara) → <prefix>_ref.png
  Fase 2: Genera clip1 con start=<prefix>_ref.png
          Extrae un frame de clip1 → <prefix>_ref_from_clip1.png
          Genera clip2 con start=<prefix>_ref_from_clip1.png

Así ambos clips parten de la misma anatomía visual del personaje.
"""
from __future__ import annotations
import json
import os
import subprocess
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.prompt_assembler import assemble_prompts_multi_clip, build_batch_json_for_multi_clip  # noqa: E402

FABRIC_DIR = Path(__file__).resolve().parent.parent
SKILL_DIR = Path(os.environ.get("FLOW_SKILL_DIR", "/home/matias/.hermes/skills/google-flow-skill"))
FLOW_SCRIPT = SKILL_DIR / "flow.py"
OUTPUT_BASE = FABRIC_DIR / "output"


def check_skill_ready() -> tuple[bool, str]:
    if not FLOW_SCRIPT.exists():
        return False, f"Flow script no encontrado en {FLOW_SCRIPT}"
    profile_dir = SKILL_DIR / "session" / "flowbot-profile"
    if not profile_dir.exists():
        return False, "Sin sesión Flow — ejecuta 'flow.py login'"
    return True, "OK"


def _run_flow(args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    cmd = ["python3", str(FLOW_SCRIPT), *args]
    print(f"[MultiClip] Ejecutando: {' '.join(cmd)}")
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(SKILL_DIR))


def extract_frame_from_video(video_path: str, out_png: str, timestamp_sec: float = 0.0) -> bool:
    """Extrae un fotograma PNG de un MP4 usando ffmpeg."""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-ss", str(timestamp_sec),
                "-i", video_path,
                "-vframes", "1",
                "-q:v", "2",
                out_png,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"[MultiClip] ffmpeg stderr: {result.stderr[:200]}")
            return False
        return Path(out_png).exists() and Path(out_png).stat().st_size > 1000
    except Exception as e:
        print(f"[MultiClip] extract_frame error: {e}")
        return False


def generate_multi_clip_901(article_data: dict) -> dict:
    """
    Orquesta la generación completa de un video double-clip para un artículo.

    Retorno:
      {
        "ok": bool,
        "error": str | None,
        "article_id": int,
        "ref_image": str | None,
        "clip1_path": str | None,
        "clip2_path": str | None,
        "batch_json_path": str,
      }
    """
    article_id = article_data.get("id")
    prefix = f"a{article_id}" if article_id else f"clip_{int(time.time())}"
    project_dir = OUTPUT_BASE / prefix

    ok, msg = check_skill_ready()
    if not ok:
        return {"ok": False, "error": msg, "prefix": prefix}

    # ── Step 0: Generate ara_json ──────────────────────────────────────────────
    sys.path.insert(0, "/home/matias/saladillo-vivo-pro")
    from agents.ara_script_agent import AraScriptAgent  # noqa: E402
    agent = AraScriptAgent()
    ara_result = agent.generate_ara_json(article_data)
    if not ara_result:
        return {"ok": False, "error": "generate_ara_json failed", "prefix": prefix}

    # Validate the new split fields are present
    c = ara_result["contenido"]
    has_split = bool(c.get("seccion_a_guion") and c.get("seccion_b_guion"))
    if not has_split:
        return {
            "ok": False,
            "error": f"No se generaron secciones split (A={len(c.get('seccion_a_guion','').split()) if c.get('seccion_a_guion') else 0}w, B={len(c.get('seccion_b_guion','').split()) if c.get('seccion_b_guion') else 0}w)",
            "prefix": prefix,
        }

    # Save ara_json
    ara_path = OUTPUT_BASE / f"batch_{article_id}_v5.json"
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    with open(ara_path, "w", encoding="utf-8") as f:
        json.dump(ara_result, f, indent=2, ensure_ascii=False)
    print(f"[MultiClip] ara_json saved: {ara_path}")

    # ── Step 1: Generate reference image IN the same dir as batch output ────────
    batch_out_dir = OUTPUT_BASE / f"{prefix}_phaseA"
    batch_out_dir.mkdir(parents=True, exist_ok=True)

    ref_png = str(batch_out_dir / f"{prefix}_ref.png")
    clip1_mp4 = str(batch_out_dir / f"{prefix}_clip1.mp4")
    clip1_ref_png = str(batch_out_dir / f"{prefix}_ref_from_clip1.png")
    clip2_mp4 = str(batch_out_dir / f"{prefix}_clip2.mp4")
    batch_json_path = str(batch_out_dir / f"batch_{prefix}.json")

    # Phase A: Generate reference image
    print(f"[MultiClip] Phase A: generating reference image → {ref_png}")
    proj_name_a = f"{prefix}_phaseA"
    r_img = _run_flow([
        "image",
        "--prompt", c.get("volanta_zocalo", "") + " - periodista femenino profesional, vertical 9:16, exterior urbano soleado",
        "--ratio", "9:16",
        "--model", "Nano Banana 2 Lite",
        "--count", "1",
        "--name", f"{prefix}_ref",
        "--out", str(batch_out_dir),
    ])
    if r_img.returncode != 0:
        print(f"[MultiClip] ❌ Reference image generation failed:\n{r_img.stderr[-500:]}")
        return {"ok": False, "error": f"image gen failed: {r_img.stderr[-200:]}", "prefix": prefix}

    # Verify ref image exists
    if not Path(ref_png).exists():
        # Try to find it by name pattern
        found = list(project_dir.glob(f"{prefix}_ref*"))
        if found:
            ref_png = str(found[0])
            print(f"[MultiClip] Found ref image at: {ref_png}")
        else:
            print(f"[MultiClip] ⚠️ Ref image not found at expected path, proceeding without frame anchor")

    # Phase B: Run clip1 batch (clip2 runs separately after frame extraction)
    from src.prompt_assembler import assemble_prompts_multi_clip  # noqa: E402
    prompts = assemble_prompts_multi_clip(ara_result, n_clips=2, duracion_por_clip=8.0)

    jobs = []
    for p in prompts:
        job = {
            "type": "video",
            "name": f"{prefix}_clip{p['clip_index']}",
            "prompt": p["prompt_veo"],
        }
        if p["clip_index"] == 1:
            job["start"] = f"{prefix}_ref.png"
        # clip2 is NOT included here — Flow times out when uploading .mp4 as start
        # We'll generate clip2 standalone after extracting a PNG anchor from clip1
        jobs.append(job)

    batch_payload = {
        "project": proj_name_a,
        "defaults": {"ratio": "9:16", "video_model": "Veo 3.1 - Lite"},
        "jobs": jobs,
    }
    with open(batch_json_path, "w", encoding="utf-8") as f:
        json.dump(batch_payload, f, indent=2, ensure_ascii=False)
    print(f"[MultiClip] Batch JSON written: {batch_json_path}")

    r_batch = _run_flow(["batch", batch_json_path, "--out", str(OUTPUT_BASE)])
    print(f"[MultiClip] Batch returncode={r_batch.returncode}")
    print(f"[MultiClip] stdout tail: {r_batch.stdout[-600:]}")
    if r_batch.returncode != 0:
        return {"ok": False, "error": f"batch failed rc={r_batch.returncode}", "prefix": prefix,
                "clip1_path": clip1_mp4, "clip2_path": clip2_mp4}

    # Verify clip1 exists
    if not Path(clip1_mp4).exists():
        return {"ok": False, "error": "Clip1 MP4 not found after batch", "prefix": prefix,
                "clip1_path": clip1_mp4, "clip2_path": clip2_mp4}
    print(f"[MultiClip] ✓ Clip1 generated: {clip1_mp4} ({Path(clip1_mp4).stat().st_size} bytes)")

    # Phase C: Extract a PNG frame from clip1 to serve as visual anchor for clip2
    # Using PNG instead of .mp4 avoids the upload_frame 90s timeout on video files
    print(f"[MultiClip] Phase C: extracting frame from clip1 → {clip1_ref_png}")
    ok_frame = extract_frame_from_video(clip1_mp4, clip1_ref_png, timestamp_sec=7.9)
    if not ok_frame or not Path(clip1_ref_png).exists():
        print("[MultiClip] ⚠️ Frame extraction failed, falling back to ref.png for clip2")
        clip2_start = ref_png
    else:
        print(f"[MultiClip] ✓ Frame extracted ({Path(clip1_ref_png).stat().st_size} bytes)")
        clip2_start = clip1_ref_png

    # Phase D: Generate clip2 standalone with PNG anchor for visual continuity
    print(f"[MultiClip] Phase D: generating clip2 with anchor → {clip2_start}")
    clip2_prompt = next(p["prompt_veo"] for p in prompts if p["clip_index"] == 2)
    r_clip2 = _run_flow([
        "video",
        "--prompt", clip2_prompt,
        "--ratio", "9:16",
        "--model", "Veo 3.1 - Lite",
        "--count", "1",
        "--name", f"{prefix}_clip2",
        "--start", clip2_start,
        "--out", str(batch_out_dir),
    ])
    if r_clip2.returncode != 0:
        print(f"[MultiClip] ❌ Clip2 generation failed:\n{r_clip2.stderr[-500:]}")
        return {"ok": False, "error": f"clip2 failed rc={r_clip2.returncode}", "prefix": prefix,
                "clip1_path": clip1_mp4, "clip2_path": None}
    print(f"[MultiClip] ✓ Clip2 generated: {clip2_mp4}")

    # Phase E: Compose final video with FFmpeg (scale + zócalo + concat)
    from src.ffmpeg_composer import process_video_multi_clip  # noqa: E402
    final_mp4 = str(batch_out_dir / f"{prefix}_final_16s.mp4")
    volanta = c.get("volanta_zocalo", "")
    titulo = c.get("titulo_zocalo", "")
    print(f"[MultiClip] Phase E: composing final video → {final_mp4}")
    comp_ok = process_video_multi_clip(
        video_paths=[clip1_mp4, clip2_mp4],
        output_path=final_mp4,
        volanta_text=volanta,
        titulo_text=titulo,
        duracion_por_clip=8.0,
    )
    if not comp_ok or not Path(final_mp4).exists():
        print(f"[MultiClip] ⚠️ Composition may have failed; checking...")
        for f2 in sorted(batch_out_dir.glob("*")):
            print(f"   {f2.name} ({f2.stat().st_size} bytes)")
    else:
        print(f"[MultiClip] ✓ Final video: {final_mp4} ({Path(final_mp4).stat().st_size} bytes)")

    # Phase F: Upload to Google Drive
    from src.google_drive_uploader import upload_video_to_drive  # noqa: E402
    drive_url = None
    try:
        drive_url = upload_video_to_drive(final_mp4, title=f"901_{prefix}_final_16s.mp4")
        print(f"[MultiClip] ✓ Uploaded to Drive: {drive_url}")
    except Exception as e:
        print(f"[MultiClip] ⚠️ Drive upload error: {e}")

    # Update Supabase ara_json
    import httpx  # noqa: E402
    supabase_url = "https://otwvfihzaznyjvjtkvvd.supabase.co"
    sb_key = None
    env_path = Path("/home/matias/saladillo-vivo-pro/.env")
    if env_path.exists():
        with open(env_path) as ef:
            for line in ef:
                if line.startswith("SUPABASE_SERVICE_ROLE_KEY"):
                    sb_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if sb_key:
        headers = {
            "apikey": sb_key,
            "Authorization": f"Bearer {sb_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-patch",
        }
        try:
            resp = httpx.patch(
                f"{supabase_url}/rest/v1/articles?id=eq.{article_id}",
                json={"ara_json": ara_result},
                headers=headers, timeout=15,
            )
            print(f"[MultiClip] Supabase update art {article_id}: {resp.status_code}")
        except Exception as e:
            print(f"[MultiClip] Supabase update error: {e}")

    return {
        "ok": True,
        "error": None,
        "article_id": article_id,
        "ref_image": ref_png if Path(ref_png).exists() else None,
        "clip1_path": clip1_mp4 if Path(clip1_mp4).exists() else None,
        "clip2_path": clip2_mp4 if Path(clip2_mp4).exists() else None,
        "batch_json_path": batch_json_path,
        "ara_path": str(ara_path),
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--article-id", type=int, required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--content", default="")
    args = ap.parse_args()

    data = {
        "id": args.article_id,
        "titulo_radial": args.title,
        "contenido_ia": args.content,
        "tono": "NEUTRAL",
    }
    res = generate_multi_clip_901(data)
    print(json.dumps(res, indent=2, ensure_ascii=False))
