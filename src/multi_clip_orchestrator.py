"""
Orquestador de múltiples clips usando Google Flow batch + FFmpeg post-producción.

Flujo mejorado (una sola llamada a Flow):
  1. Genera JSON de batch unificado con encadenamiento nativo de clips.
  2. Ejecuta 'flow.py batch <archivo>' en un solo paso (sin fases separadas).
  3. Busca los videos generados en output/<project_id>/.
  4. Aplica zócalo y concatena con FFmpeg → video final de ~16s.
  5. Sube a Google Drive si está configurado.

Este módulo reemplaza las Fases A-F del antiguo orquestador
por una ruta más simple, rápida y confiable.
"""
from __future__ import annotations
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from prompt_assembler import assemble_prompts_multi_clip  # noqa: E402
from batch_workflow_generator import generate_batch_json, save_batch_json  # noqa: E402
from ffmpeg_composer import process_video_multi_clip  # noqa: E402


FABRIC_DIR = Path(__file__).resolve().parent.parent
SKILL_DIR = Path(os.environ.get("FLOW_SKILL_DIR", "/home/matias/.hermes/skills/google-flow-skill"))
FLOW_SCRIPT = SKILL_DIR / "flow.py"
OUTPUT_BASE = FABRIC_DIR / "output"


def _find_python_with_playwright():
    """Encuentra el mejor python3 disponible con playwright instalado."""
    import glob
    candidates = (
        "/home/matias/.hermes/tools/python-*/bin/python3",
        "/usr/bin/python3.14",
        "/usr/bin/python3",
    )
    for pattern in candidates:
        matches = glob.glob(pattern)
        for p in matches:
            if os.path.isfile(p):
                r = subprocess.run([p, "-c", "import playwright"], capture_output=True, timeout=5)
                if r.returncode == 0:
                    return p
    return "python3"


_PYTHON_BIN = _find_python_with_playwright()


@dataclass
class BatchConfig:
    """Configuración para la ejecución de batch en Flow."""
    n_clips: int = 3
    duracion_por_clip: float = 8.0
    project_name: Optional[str] = None
    max_wait_seconds: int = 600
    poll_interval: int = 15


class MultiClipOrchestrator:
    """
    Orquesta la generación de videos multi-clip usando un batch unificado
    de Google Flow y post-procesamiento FFmpeg.
    """

    def __init__(
        self,
        article_id: int,
        ara_json,
        output_dir: str = "output",
        volanta_text: str = "",
        titulo_text: str = "",
        n_clips: int = 3,
        brand_color: str = "#1E3A8A",
    ):
        self.article_id = article_id
        self.ara_json = ara_json
        self.output_dir = Path(output_dir)
        self.volanta_text = volanta_text
        self.titulo_text = titulo_text
        self.n_clips = n_clips
        self.brand_color = brand_color
        self.project_name = f"a{article_id}"
        self.batch_json_path: Optional[str] = None
        self.generated_clips: list[str] = []

    # -----------------------------------------------------------------------
    # Método principal
    # -----------------------------------------------------------------------

    def run(self, batch_config: Optional[BatchConfig] = None) -> dict:
        """
        Ejecuta el pipeline completo en un solo paso.

        Retorna dict con estado:
            {
                "ok": bool,
                "final_video_path": str | None,
                "n_clips_generated": int,
                "steps": dict[str, object],
                "error": str | None,
            }
        """
        cfg = batch_config or BatchConfig()
        steps: dict[str, object] = {}

        try:
            # -------------------------------------------------------------------------
            # Paso 1: Generar JSON de batch unificado con encadenamiento nativo
            # -------------------------------------------------------------------------
            print(f"[MultiClip] Generando batch JSON para artículo {self.article_id}...")
            batch_data = generate_batch_json(
                ara_json_input=self.ara_json,
                article_id=self.article_id,
                n_clips=cfg.n_clips,
                duracion_por_clip=cfg.duracion_por_clip,
                project_name=cfg.project_name or self.project_name,
            )
            out_dir = self.output_dir / self.project_name
            out_dir.mkdir(parents=True, exist_ok=True)

            self.batch_json_path = save_batch_json(
                batch_data, str(out_dir / "batch_job.json")
            )
            steps["batch_json_generated"] = True
            print(f"[MultiClip] Batch guardado en {self.batch_json_path}")

            # -------------------------------------------------------------------------
            # Paso 2: Ejecutar flow.py batch (llamada única, todos los clips)
            # -------------------------------------------------------------------------
            print(f"[MultiClip] Ejecutando flow.py batch...")
            ok, report_path = self._run_flow_batch(cfg)
            steps["flow_batch_executed"] = ok

            if not ok:
                return {
                    "ok": False,
                    "error": f"Falló ejecución de flow.py batch.",
                    "report_path": report_path,
                    "steps": steps,
                }

            steps["batch_completed"] = True

            # -------------------------------------------------------------------------
            # Paso 3: Encontrar videos generados desde el reporte o por patrón
            # -------------------------------------------------------------------------
            self.generated_clips = self._find_generated_videos(out_dir)
            steps["videos_found"] = len(self.generated_clips) > 0
            print(f"[MultiClip] {len(self.generated_clips)} clips encontrados.")

            if not self.generated_clips:
                return {
                    "ok": False,
                    "error": "No se encontraron videos generados después del batch.",
                    "steps": steps,
                }

            # -------------------------------------------------------------------------
            # Paso 4: Aplicar zócalo y concatenar con FFmpeg
            # -------------------------------------------------------------------------
            final_output = out_dir / f"{self.project_name}_final.mp4"
            total_dur = cfg.duracion_por_clip * len(self.generated_clips)

            print(f"[MultiClip] Procesando {len(self.generated_clips)} clips con zócalo y concatenando...")
            proc_result = process_video_multi_clip(
                video_paths=self.generated_clips,
                output_path=str(final_output),
                volanta_text=self.volanta_text,
                titulo_text=self.titulo_text,
                brand_color=self.brand_color,
                duracion_por_clip=cfg.duracion_por_clip,
            )
            steps["video_processed"] = proc_result.get("ok", False)

            if not proc_result.get("ok"):
                return {
                    "ok": False,
                    "error": proc_result.get("error", "Error procesando videos"),
                    "steps": steps,
                }

            print(f"[MultiClip] Video final generado: {final_output}")
            return {
                "ok": True,
                "final_video_path": str(final_output),
                "n_clips_generated": len(self.generated_clips),
                "total_duration_sec": total_dur,
                "steps": steps,
            }

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[MultiClip] ERROR: {e}\n{tb}")
            steps["error"] = str(e)
            return {"ok": False, "error": str(e), "traceback": tb, "steps": steps}

    # -----------------------------------------------------------------------
    # Métodos internos
    # -----------------------------------------------------------------------

    def _run_flow_batch(self, cfg: BatchConfig) -> tuple[bool, Optional[str]]:
        """
        Ejecuta flow.py batch y retorna (exit_code, ruta_del_reporte).
        El script flow.py descarga los archivos directamente al directorio indicado.
        """
        if not FLOW_SCRIPT.exists():
            return False, None

        env_path = Path(__file__).resolve().parent / ".env"
        project_for_path = cfg.project_name or self.project_name
        cmd = [
            _PYTHON_BIN, str(FLOW_SCRIPT),
            "batch", self.batch_json_path,
            "--out", str(self.output_dir / project_for_path),
        ]
        if env_path.exists():
            cmd.extend(["--env-file", str(env_path)])

        print(f"[MultiClip] Corriendo: {' '.join(cmd)}")
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True, text=True,
                timeout=cfg.max_wait_seconds + 120,
                cwd=str(SKILL_DIR),
            )
        except subprocess.TimeoutExpired:
            return False, None

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""

        # Intentar extraer ruta del reporte desde stdout
        report_path = None
        for line in stdout.splitlines():
            stripped = line.strip()
            if stripped.endswith(".json") and ("report" in stripped.lower() or "batch" in stripped.lower()):
                if Path(stripped).exists():
                    report_path = stripped
                    break

        if not report_path:
            candidate = self.output_dir / project_for_path / "batch_report.json"
            if candidate.exists():
                report_path = str(candidate)

        # Parsear reporte si existe
        if report_path and Path(report_path).exists():
            try:
                with open(report_path, encoding="utf-8") as f:
                    report = json.load(f)
                jobs = report.get("jobs", [])
                completed = sum(1 for j in jobs if j.get("status") == "completed")
                failed = sum(1 for j in jobs if j.get("status") == "failed")
                running = sum(1 for j in jobs if j.get("status") in ("running", "pending"))
                print(f"[MultiClip] Reporte: {completed} ok, {failed} fail, {running} pendientes")
            except Exception as e:
                print(f"[MultiClip] Advertencia: no se pudo leer reporte: {e}")

        ok = proc.returncode == 0 and len(stdout) > 0
        if not ok:
            print(f"[MultiClip] STDERR: {stderr[:500]}")
        return ok, report_path

    def _find_generated_videos(self, out_dir: Path) -> list[str]:
        """
        Encuentra archivos .mp4 generados por Flow en la carpeta del proyecto,
        excluyendo el archivo final ya existente.
        Orden cronológico inverso (más reciente primero).
        """
        mp4_files = sorted(out_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        existing_final = set(str(p) for p in out_dir.glob("*_final*.mp4"))
        clips = [str(f) for f in mp4_files if str(f) not in existing_final]
        return clips

    def get_batch_json_content(self) -> dict:
        """Retorna el contenido del JSON de batch generado (para depuración)."""
        if self.batch_json_path and Path(self.batch_json_path).exists():
            with open(self.batch_json_path, encoding="utf-8") as f:
                return json.load(f)
        return {}


def generate_multi_clip_batch(article_data: dict) -> dict:
    """
    Función principal para generar video multi-clip desde datos de artículo.
    Equivalente refactorizado a generate_multi_clip_901 pero con flujo batch único.
    """
    raw_id = article_data.get("id")
    if not raw_id:
        return {"ok": False, "error": "article_data missing 'id'", "prefix": "unknown"}
    article_id: int = int(raw_id)
    prefix = f"a{article_id}"
    out_dir = OUTPUT_BASE / prefix

    # Generar ara_json si no existe
    from agents.ara_script_agent import AraScriptAgent  # noqa: E402 (después de sys.path)
    agent = AraScriptAgent()
    ara_result = agent.generate_ara_json(article_data)
    if not ara_result:
        return {"ok": False, "error": "generate_ara_json failed", "prefix": prefix}

    # Guardar ara_json
    ara_path = OUTPUT_BASE / f"batch_{article_id}_batch.json"
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    with open(ara_path, "w", encoding="utf-8") as f:
        json.dump(ara_result, f, indent=2, ensure_ascii=False)

    c = ara_result.get("contenido", {})
    bloques = c.get("bloques", [])
    has_split = bool(
        (c.get("seccion_a_guion") and c.get("seccion_b_guion"))
        or (isinstance(bloques, list) and len(bloques) >= 2)
    )
    if not has_split:
        return {
            "ok": False,
            "error": "No se generaron secciones split en ara_json",
            "prefix": prefix,
        }

    # Crear orquestador y ejecutar
    orchestrator = MultiClipOrchestrator(
        article_id=article_id,
        ara_json=ara_result,
        output_dir=str(OUTPUT_BASE),
        volanta_text=c.get("volanta_zocalo", ""),
        titulo_text=c.get("titulo_zocalo", ""),
        n_clips=3,
    )
    result = orchestrator.run()

    # Actualizar Supabase con ara_json actualizado
    supabase_url = "https://otwvfihzaznyjvjtkvvd.supabase.co"
    env_path = Path("/home/matias/saladillo-vivo-pro/.env")
    sb_key = None
    if env_path.exists():
        with open(env_path) as ef:
            for line in ef:
                if line.startswith("SUPABASE_SERVICE_ROLE_KEY"):
                    sb_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if sb_key:
        try:
            import httpx
            headers = {
                "apikey": sb_key,
                "Authorization": f"Bearer {sb_key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-patch",
            }
            resp = httpx.patch(
                f"{supabase_url}/rest/v1/articles?id=eq.{article_id}",
                json={"ara_json": ara_result},
                headers=headers, timeout=15,
            )
            print(f"[MultiClip] Supabase update art {article_id}: {resp.status_code}")
        except Exception as e:
            print(f"[MultiClip] Supabase error: {e}")

    # Subir a Drive si el video fue exitoso
    drive_url = None
    if result.get("ok") and result.get("final_video_path"):
        try:
            from src.google_drive_uploader import upload_video_to_drive  # noqa: E402
            drive_result = upload_video_to_drive(result["final_video_path"], article_id=article_id)
            drive_url = drive_result.get("remote_url")
            print(f"[MultiClip] Uploaded to Drive: {drive_url}")
        except Exception as e:
            print(f"[MultiClip] Drive upload error: {e}")

    result.update({
        "article_id": article_id,
        "prefix": prefix,
        "ara_path": str(ara_path),
        "drive_url": drive_url,
    })
    return result


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
    res = generate_multi_clip_batch(data)
    print(json.dumps(res, indent=2, ensure_ascii=False))
