"""Tests para helpers de Supabase Storage en worker.py (Fase 2)."""
import json
from unittest import mock

import src.worker as worker


class FakeStorage:
    url = "https://otwvfihzaznyjvjtkvvd.supabase.co"
    key = "fake-key"

    def get_headers(self, return_representation=True):
        return {
            "Authorization": f"Bearer {self.key}",
            "apikey": self.key,
            "Content-Type": "application/json",
        }


def test_public_bucket_url_formats():
    storage = FakeStorage()
    url = worker.public_bucket_url(
        storage, "video-factory-assets", "master-images/3.png"
    )
    assert url == (
        "https://otwvfihzaznyjvjtkvvd.supabase.co"
        "/storage/v1/object/public/video-factory-assets/master-images/3.png"
    )


def test_upload_to_bucket_posts_to_storage(monkeypatch):
    storage = FakeStorage()
    captured = {}

    def fake_post(url, *, headers=None, data=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data

        class R:
            status_code = 200

        return R()

    monkeypatch.setattr(worker._req, "post", fake_post)

    ok = worker.upload_to_bucket(
        storage, "video-factory-assets", "master-images/3.png", b"PNGDATA"
    )

    assert ok is True
    assert captured["url"] == (
        "https://otwvfihzaznyjvjtkvvd.supabase.co"
        "/storage/v1/object/video-factory-assets/master-images/3.png"
    )
    assert captured["headers"]["Content-Type"] == "image/png"
    assert captured["data"] == b"PNGDATA"


def test_upload_to_bucket_non_ok_returns_false(monkeypatch):
    storage = FakeStorage()

    def fake_post(url, *, headers=None, data=None, timeout=None, **kwargs):
        class R:
            status_code = 400
            text = "bad"

        return R()

    monkeypatch.setattr(worker._req, "post", fake_post)

    ok = worker.upload_to_bucket(
        storage, "video-factory-assets", "master-images/3.png", b"PNGDATA"
    )
    assert ok is False