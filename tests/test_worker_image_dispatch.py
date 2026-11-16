"""Tests para el despacho de trabajos de imagen maestra en el worker."""
from unittest import mock

import src.worker as worker


def test_fetch_pending_image_jobs_filters_by_tipo_y_estado(monkeypatch):
    fake_resp = mock.Mock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = [
        {"id": 7, "article_id": 900, "tipo": "imagen", "estado": "pendiente_imagen"},
        {"id": 8, "article_id": 901, "tipo": "imagen", "estado": "pendiente_imagen"},
    ]
    storage = mock.Mock()
    monkeypatch.setattr(worker, "_req", mock.Mock(get=lambda *a, **k: fake_resp))

    rows = worker.fetch_pending_image_jobs(storage, limit=2)

    assert [r["article_id"] for r in rows] == [900, 901]


def test_fetch_pending_image_jobs_empty_on_non_200(monkeypatch):
    storage = mock.Mock()
    monkeypatch.setattr(worker, "_req", mock.Mock(get=lambda *a, **k: mock.Mock(status_code=500)))

    assert worker.fetch_pending_image_jobs(storage) == []


def test_dispatch_image_job_calls_process_image_only(monkeypatch):
    storage = mock.Mock()
    monkeypatch.setattr(worker, "fetch_pending_image_jobs", lambda s, limit=5: [{"id": 7, "article_id": 900}])
    calls = []
    monkeypatch.setattr(worker, "process_image_only_job",
                        lambda s, article_id, dry_run: calls.append((article_id, dry_run)) or {"ok": True})

    result = worker.process_pending_image_jobs(storage, dry_run=True)

    assert calls == [(900, True)]
    assert result["ok"] is True