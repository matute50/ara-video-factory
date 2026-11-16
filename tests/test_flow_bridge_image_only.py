"""Tests para generar un payload de batch de solo imagen (Fase 2)."""
import src.flow_bridge as fb


def test_generate_image_only_json_produces_single_image_job():
    payload = fb.generate_image_only_json(article_id=900, prompt_imagen="un prompt")
    assert payload["project"] == fb.PROJECT_NAME
    assert payload["defaults"]["ratio"] == "9:16"
    assert payload["defaults"]["image_model"] == fb.IMAGE_MODEL
    jobs = payload["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["type"] == "image"
    assert jobs[0]["name"] == "img_900_frame"
    assert jobs[0]["prompt"] == "un prompt"
    # No debe haber job de video
    assert all(j["type"] != "video" for j in jobs)