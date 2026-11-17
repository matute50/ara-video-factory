"""Tests para process_image_only_job (generación de imagen maestra, Fase 2)."""
import json
from pathlib import Path
from unittest import mock

import src.worker as worker


def _fake_article() -> dict:
    return {
        "id": 900,
        "ara_json": json.dumps({
            "version": "1.0",
            "contenido": {
                "guion_ara": "Estudiantes ganó el campeonato tras un partido intenso y la gente festejó en la plaza central de la ciudad hasta la noche",
                "volanta_zocalo": "Ara · Saladillo Vivo",
                "titulo_zocalo": "Título",
            },
            "meta_ara": {
                "tenor": "informativo_servicio",
                "permitir_sonrisa": True,
                "tono_vocal": "Voz femenina joven y clara",
                "expresion_inicio": "Mirada fija al lente con leve asentimiento",
                "expresion_desarrollo": "Expresión serena natural",
                "expresion_cierre": "Último guiño sutil",
                "lenguaje_corporal": "postura_firme_contenida",
                "ambiente_sonoro": "Sonido de calle",
                "ritmo_camara": "plano_fijo_estable",
                "requiere_aprobacion_sensible": False,
            },
        }),
    }


def test_process_image_only_live_uploads_and_marks_lista(tmp_path, monkeypatch):
    # --- preparar un batch_report.json con la salida de imagen ---
    project_dir = tmp_path / "outputs" / "saladillo-vivo-ara"
    project_dir.mkdir(parents=True, exist_ok=True)
    img_file = project_dir / "img_900_frame.png"
    img_file.write_bytes(b"PNGDATA")
    (project_dir / "batch_report.json").write_text(json.dumps([
        {"name": "img_900_frame", "type": "image", "file": str(img_file), "ok": True}
    ]))

    monkeypatch.setattr(worker, "SKILL_DIR", tmp_path)
    monkeypatch.setattr(worker, "PROJECT_NAME", "saladillo-vivo-ara")

    # --- límites ---
    monkeypatch.setattr(worker, "_fetch_article", lambda s, aid: _fake_article())
    monkeypatch.setattr(worker, "get_or_create_job", lambda s, aid: {"id": 7, "article_id": aid})
    calls = {}
    monkeypatch.setattr(worker, "update_job_status", lambda s, jid, **f: calls.setdefault(jid, []).append(f))
    monkeypatch.setattr(worker, "run_live_batch", lambda bf: {"ok": True, "returncode": 0})
    monkeypatch.setattr(worker, "upload_to_bucket",
                        lambda s, b, path, data, content_type="image/png": True)
    monkeypatch.setattr(worker, "public_bucket_url",
                        lambda s, b, path: f"https://x.supabase.co/storage/v1/object/public/{b}/{path}")

    result = worker.process_image_only_job("storage", article_id=900, dry_run=False)

    assert result["ok"] is True
    assert result["master_image_url"].endswith("master-images/7.png")
    final_update = calls[7][-1]
    assert final_update["estado"] == "imagen_lista"
    assert final_update["master_image_url"].endswith("master-images/7.png")
    assert final_update["imagen_compuesta_path"] == str(img_file)


def test_process_image_only_dry_run_does_not_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "_fetch_article", lambda s, aid: _fake_article())
    monkeypatch.setattr(worker, "get_or_create_job", lambda s, aid: {"id": 7, "article_id": aid})
    db_writes = []
    monkeypatch.setattr(worker, "update_job_status",
                        lambda s, jid, **f: db_writes.append((jid, f)))
    uploaded = []
    monkeypatch.setattr(worker, "upload_to_bucket",
                        lambda s, b, path, data, content_type="image/png": uploaded.append(path) or True)

    result = worker.process_image_only_job("storage", article_id=900, dry_run=True)

    assert result["ok"] is True
    assert result.get("dry_run") is True
    assert uploaded == []
    assert db_writes == []  # el dry-run no debe tocar la DB