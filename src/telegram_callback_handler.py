"""Manejador de callbacks de Telegram para QC de videos Ara.

Polling de getUpdates, procesa approve/discard/edit y actualiza BD.
"""
from __future__ import annotations
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests as rq
from dotenv import load_dotenv

# Agregar ruta del proyecto parent para imports del pipeline (igual que worker.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "saladillo-vivo-pro"))

# Cargar .env del factory
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from agents.storage_agent import StorageAgent


TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN_2")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else None


@dataclass
class CallbackUpdate:
    update_id: int
    callback_query_id: str
    chat_id: str
    message_id: int
    data: str  # ej: "approve_901", "discard_901", "edit_901"
    from_user_id: int


def _get_updates(offset: int, timeout: int = 30) -> list[dict]:
    """Obtiene actualizaciones de Telegram (long polling)."""
    if not API_BASE:
        return []
    try:
        resp = rq.get(
            f"{API_BASE}/getUpdates",
            params={"offset": offset, "timeout": timeout, "allowed_updates": '["callback_query"]'},
            timeout=timeout + 5,
        )
        if resp.status_code == 200:
            return resp.json().get("result", [])
    except Exception as e:
        print(f"[CallbackHandler] Error getUpdates: {e}")
    return []


def _answer_callback(callback_query_id: str, text: str, show_alert: bool = False) -> bool:
    """Responde al callback query (quita el 'loading' del botón)."""
    if not API_BASE:
        return False
    try:
        resp = rq.post(
            f"{API_BASE}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text, "show_alert": show_alert},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"[CallbackHandler] Error answerCallbackQuery: {e}")
    return False


def _edit_message_text(chat_id: str, message_id: int, text: str) -> bool:
    """Edita el mensaje original para reflejar la decisión."""
    if not API_BASE:
        return False
    try:
        resp = rq.post(
            f"{API_BASE}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"[CallbackHandler] Error editMessageText: {e}")
    return False


def _parse_callback_data(data: str) -> tuple[str, int]:
    """Parsea 'approve_901' -> ('approve', 901)."""
    parts = data.split("_", 1)
    action = parts[0]
    article_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    return action, article_id


def handle_approve(storage: StorageAgent, article_id: int) -> dict:
    """Aprueba la generación: cambia estado a 'aprobado' y dispara worker live."""
    try:
        # Actualizar job a 'aprobado' — el worker en modo live lo tomará en el próximo poll
        resp = rq.patch(
            f"{storage.url}/rest/v1/ara_video_jobs?article_id=eq.{article_id}",
            json={"estado": "aprobado"},
            headers=storage.get_headers(True),
            timeout=15,
        )
        if resp.status_code in (200, 204):
            return {"ok": True, "action": "approved", "article_id": article_id}
        return {"ok": False, "error": f"DB update failed: {resp.status_code} {resp.text[:100]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handle_discard(storage: StorageAgent, article_id: int) -> dict:
    """Descarta el job: marca como 'descartado'."""
    try:
        resp = rq.patch(
            f"{storage.url}/rest/v1/ara_video_jobs?article_id=eq.{article_id}",
            json={"estado": "descartado", "ultimo_error": "Descartado por usuario vía Telegram"},
            headers=storage.get_headers(True),
            timeout=15,
        )
        if resp.status_code in (200, 204):
            return {"ok": True, "action": "discarded", "article_id": article_id}
        return {"ok": False, "error": f"DB update failed: {resp.status_code} {resp.text[:100]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handle_edit(storage: StorageAgent, article_id: int) -> dict:
    """Editar guion: por ahora marca para re-procesamiento manual.
    
    En una versión futura, abriría un formulario o esperaría mensaje de texto.
    """
    try:
        resp = rq.patch(
            f"{storage.url}/rest/v1/ara_video_jobs?article_id=eq.{article_id}",
            json={"estado": "pendiente_ensamblado", "ultimo_error": "Solicitada edición de guion vía Telegram"},
            headers=storage.get_headers(True),
            timeout=15,
        )
        if resp.status_code in (200, 204):
            return {"ok": True, "action": "edit_requested", "article_id": article_id}
        return {"ok": False, "error": f"DB update failed: {resp.status_code} {resp.text[:100]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


ACTION_HANDLERS = {
    "approve": handle_approve,
    "discard": handle_discard,
    "edit": handle_edit,
}


def process_callback(update: CallbackUpdate, storage: StorageAgent) -> dict:
    """Procesa un callback individual."""
    action, article_id = _parse_callback_data(update.data)

    if action not in ACTION_HANDLERS:
        return {"ok": False, "error": f"Acción desconocida: {action}"}

    if article_id == 0:
        return {"ok": False, "error": "article_id inválido en callback_data"}

    handler = ACTION_HANDLERS[action]
    result = handler(storage, article_id)

    # Responder al callback (quita loading)
    ack_text = {
        "approve": "✅ Generación aprobada. El worker la procesará en vivo.",
        "discard": "❌ Job descartado.",
        "edit": "✏️ Edición solicitada. El job vuelve a 'pendiente_ensamblado'.",
    }.get(action, "Procesado")
    _answer_callback(update.callback_query_id, ack_text)

    # Editar mensaje original para mostrar decisión
    new_text = {
        "approve": f"✅ <b>APROBADO</b> — Article {article_id}\nEl worker generará el video en el próximo ciclo.",
        "discard": f"❌ <b>DESCARTADO</b> — Article {article_id}",
        "edit": f"✏️ <b>EDICIÓN SOLICITADA</b> — Article {article_id}\nJob devuelto a estado pendiente.",
    }.get(action, "Procesado")
    _edit_message_text(update.chat_id, update.message_id, new_text)

    return result


def run_polling_loop(poll_interval: int = 3, max_iterations: int = 0) -> None:
    """Loop principal de polling de callbacks.

    Args:
        poll_interval: Segundos entre polls si no hay updates.
        max_iterations: 0 = infinito; >0 = número de iteraciones (útil para tests).
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[CallbackHandler] ERROR: TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no configurados")
        return

    storage = StorageAgent()
    offset = 0
    iteration = 0

    print("=" * 60)
    print("TELEGRAM CALLBACK HANDLER — Iniciando polling")
    print("=" * 60)

    while max_iterations == 0 or iteration < max_iterations:
        iteration += 1
        updates = _get_updates(offset)

        for upd in updates:
            offset = upd["update_id"] + 1
            cb = upd.get("callback_query")
            if not cb:
                continue

            try:
                cb_update = CallbackUpdate(
                    update_id=upd["update_id"],
                    callback_query_id=cb["id"],
                    chat_id=str(cb["message"]["chat"]["id"]),
                    message_id=cb["message"]["message_id"],
                    data=cb["data"],
                    from_user_id=cb["from"]["id"],
                )
                # Verificar que el callback viene del chat autorizado
                if str(cb_update.chat_id) != str(TELEGRAM_CHAT_ID):
                    print(f"[CallbackHandler] Callback de chat no autorizado: {cb_update.chat_id}")
                    _answer_callback(cb_update.callback_query_id, "⛔ Chat no autorizado", show_alert=True)
                    continue

                result = process_callback(cb_update, storage)
                print(f"[CallbackHandler] {cb_update.data} -> {result}")

            except Exception as e:  # noqa: BLE001
                print(f"[CallbackHandler] Error procesando callback: {e}")

        if not updates:
            time.sleep(poll_interval)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Telegram Callback Handler for Ara Video Factory")
    parser.add_argument("--once", action="store_true", help="Procesa updates pendientes y sale")
    parser.add_argument("--interval", type=int, default=3, help="Segundos entre polls")
    args = parser.parse_args()

    max_iter = 1 if args.once else 0
    run_polling_loop(poll_interval=args.interval, max_iterations=max_iter)