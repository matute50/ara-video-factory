"""Componer video final 901 con el clip real de Flow y subir a Drive."""
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

ARTICLE_ID = 901
FLOW_OUTPUTS = Path("/home/matias/.hermes/skills/google-flow-skill/outputs/saladillo-vivo-ara")
OUTPUT_DIR = Path("/home/matias/ara-video-factory/output")


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
    raise SystemExit(f"Artículo {article_id} no encontrado")


def main():
    storage = StorageAgent()
    art = fetch_article(storage, ARTICLE_ID)
    ara = json.loads(art["ara_json"]) if isinstance(art["ara_json"], str) else art["ara_json"]

    volanta = ara.get("contenido", {}).get("volanta_zocalo", "Ara · Saladillo Vivo")
    titulo = ara.get("contenido", {}).get("titulo_zocalo", "")

    print("=" * 60)
    print(f"COMPOSICIÓN VIDEO FINAL — Artículo {ARTICLE_ID}")
    print(f"Título: {art.get('title','')}")
    print(f"Volanta: {volanta}")
    print(f"Zócalo: {titulo}")
    print("=" * 60)

    # Verificar que los archivos de Flow existen
    raw_video = FLOW_OUTPUTS / "vid_901_clip.mp4"
    if not raw_video.exists():
        raise SystemExit(f"Video de Flow no encontrado: {raw_video}")

    # Componer con zócalo
    final = OUTPUT_DIR / f"output_{ARTICLE_ID}_final.mp4"
    comp = compose_video(str(raw_video), str(final), volanta_text=volanta, titulo_text=titulo)
    print(f"[Composer] {comp}")

    if not final.exists():
        raise SystemExit("No se generó el video final")

    # Verificar propiedades
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,duration,codec_name", "-of", "csv=p=0", str(final)],
        capture_output=True, text=True)
    print(f"[OK] Video final: {final} | {probe.stdout.strip()}")

    # Subir a Drive
    res = upload_video_to_drive(str(final), ARTICLE_ID, tenor="informativo_servicio")
    print("\n=== SUBIDA A DRIVE ===")
    for k, v in res.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
