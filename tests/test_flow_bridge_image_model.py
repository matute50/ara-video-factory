"""Tests para el modelo de imagen maestra en flow_bridge.

Fase 2: la imagen maestra debe usar el tier básico (Nano Banana 2 Lite),
NO Nano Banana Pro. Debe ser configurable por env FLOW_IMAGE_MODEL.
"""
import os

from src.flow_bridge import FlowJob, generate_batch_json, IMAGE_MODEL


def _mock_job() -> FlowJob:
    return FlowJob(
        job_id=1,
        article_id=900,
        prompt_imagen="un prompt",
        prompt_veo="otro prompt",
        estado_actual="pendiente_ensamblado",
    )


def test_default_image_model_is_nano_banana_2_lite(monkeypatch):
    # El default no debe ser Pro
    assert IMAGE_MODEL == "Nano Banana 2 Lite"
    assert "Pro" not in IMAGE_MODEL
    payload = generate_batch_json(_mock_job())
    assert payload["defaults"]["image_model"] == "Nano Banana 2 Lite"


def test_image_model_override_via_env(monkeypatch):
    monkeypatch.setenv("FLOW_IMAGE_MODEL", "Nano Banana 2.1")
    import importlib
    import src.flow_bridge as fb
    importlib.reload(fb)
    try:
        assert fb.IMAGE_MODEL == "Nano Banana 2.1"
        payload = fb.generate_batch_json(_mock_job())
        assert payload["defaults"]["image_model"] == "Nano Banana 2.1"
    finally:
        monkeypatch.delenv("FLOW_IMAGE_MODEL", raising=False)
        importlib.reload(fb)