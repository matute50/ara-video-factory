"""Genera el video final completo del artículo 897 (noticia real) y lo sube a Drive.

Usa video sintético de FFmpeg como raw (NO consume créditos de Flow — restricción
de pruebas), compone el zócalo con compose_video y sube el final a Google Drive.
"""
from __future__ import annotations
import sys, json, subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, "/home/matias/saladillo-vivo-pro")

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from src.ffmpeg_composer import process_video as compose_video
from src.google_drive_uploader import upload_video_to_drive
from agents.storage_agent import StorageAgent

ARTICLE_ID = 897
OUTPUT = Path("/home/matias/ara-video-factory/output")


def fetch_article(storage, article_id):
    import requests as rq
    resp = rq.get(
        storage.base_rest_url,
        params={"select": "*", "id": f"eq.{article_id}"},
        headers=storage.get_headers(False),
        timeout=15,
    )
    if resp.status_code == 200 and resp.json():
        return resp.json()[0]
    raise SystemExit(f"Artículo {article_id} no encontrado: {resp.text[:200]}")


def main():
    storage = StorageAgent()
    art = fetch_article(storage, ARTICLE_ID)
    ara = json.loads(art["ara_json"]) if isinstance(art["ara_json"], str) else art["ara_json"]

    volanta = ara.get("contenido", {}).get("volanta_zocalo", "Ara · Saladillo Vivo")
    titulo = ara.get("contenido", {}).get("titulo_zocalo", "")

    print("=" * 60)
    print(f"GENERANDO VIDEO FINAL — Artículo {ARTICLE_ID}")
    print(f"Título: {art.get('title','')}")
    print(f"Volanta zócalo: {volanta}")
    print(f"Zócalo título: {titulo}")
    print("=" * 60)

    # 1) Video crudo sintético (NO Flow, NO créditos)
    raw = OUTPUT / f"testsrc_{ARTICLE_ID}.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        "testsrc2=size=1920x1080:duration=8:rate=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(raw),
    ], check=True)
    print(f"[OK] Raw sintético: {raw}")

    # 2) Componer el final con zócalo
    final = OUTPUT / f"output_{ARTICLE_ID}_final.mp4"
    comp = compose_video(str(raw), str(final), volanta_text=volanta, titulo_text=titulo)
    print(f"[Composer] {comp}")

    # Verificar el final
    if not final.exists():
        raise SystemExit("No se generó el video final")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,duration", "-of", "csv=p=0", str(final)],
        capture_output=True, text=True)
    print(f"[OK] Video final: {final} | {probe.stdout.strip()}")

    # 3) Subir a Drive
    res = upload_video_to_drive(str(final), ARTICLE_ID, tenor="informativo_servicio")
    print("\n=== SUBIDA A DRIVE ===")
    for k, v in res.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()