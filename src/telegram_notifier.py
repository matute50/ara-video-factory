"""
Notificador por Telegram para QC humano de noticias sensibles.
Envía tenor, guion, zócalos y prompt_veo con botones inline de aprobación.
NUNCA genera video sin aprobación humana para tenores sensibles.
"""
from __future__ import annotations
import json
import os
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class QCPayload:
    article_id: int
    tenor: str
    guion_ara: str
    volanta_zocalo: str
    titulo_zocalo: str
    prompt_veo: str
    requires_approval: bool = True


def _get_bot_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN_2")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN no configurada en entorno")
    return token


def _get_chat_id() -> str:
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not chat_id:
        raise ValueError("TELEGRAM_CHAT_ID no configurada en entorno")
    return chat_id


def _send_telegram_message(text: str, parse_mode: str = "HTML", reply_markup: Optional[dict] = None) -> dict:
    """Envía mensaje a Telegram con error handling."""
    token = _get_bot_token()
    chat_id = _get_chat_id()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[TelegramNotifier] Error enviando mensaje: {e}")
        return {"ok": False, "error": str(e)}


def send_qc_request(payload: QCPayload) -> dict:
    """
    Envía solicitud de QC por Telegram con botones inline.
    Retorna response de Telegram o dict con error.
    """
    tenor_label = {
        "sensible_luto": "⚰️ LUTO",
        "policial_grave": "🚨 POLICIAL",
        "alegre_cultural": "🎉 CULTURAL",
        "informativo_servicio": "📰 INFORMATIVO",
    }.get(payload.tenor, "📋 GENERAL")

    approval_indicator = "✅ APROBACIÓN REQUERIDA" if payload.requires_approval else "ℹ️ INFO"

    message = (
        f"<b>{approval_indicator}</b>\n"
        f"<b>Article ID:</b> <code>{payload.article_id}</code>\n"
        f"<b>Tenor:</b> {tenor_label}\n"
        f"<b>Guion Ara ({len(payload.guion_ara.split())} palabras):</b>\n"
        f"<code>{payload.guion_ara}</code>\n\n"
        f"<b>Zócalo:</b> {payload.volanta_zocalo} | {payload.titulo_zocalo}\n"
        f"<b>Prompt Veo:</b> <code>{payload.prompt_veo[:150]}...</code>"
    )

    inline_keyboard = [
        [
            {"text": "✅ Aprobar generación", "callback_data": f"approve_{payload.article_id}"},
            {"text": "❌ Descartar", "callback_data": f"discard_{payload.article_id}"},
        ],
        [{"text": "✏️ Editar guion", "callback_data": f"edit_{payload.article_id}"}],
    ]

    return _send_telegram_message(message, reply_markup={"inline_keyboard": inline_keyboard})


def notify_batch_complete(total: int, success: int, failed: int, errors: list[str] = None) -> None:
    """Notifica fin de batch de procesamiento."""
    errors_txt = "\n".join(f"• {e}" for e in (errors or []))
    text = (
        f"📊 <b>Batch completado</b>\n"
        f"Total: {total} | Éxito: {success} | Fallidos: {failed}\n"
        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    if errors_txt:
        text += f"\n\n<b>Errores:</b>\n{errors_txt[:500]}"
    _send_telegram_message(text)


if __name__ == "__main__":
    test_payload = QCPayload(
        article_id=900,
        tenor="informativo_servicio",
        guion_ara="¡Atención, choferes! Saladillo tendrá su sede ISITRANS desde el veinte de noviembre.",
        volanta_zocalo="Ara · Saladillo Vivo",
        titulo_zocalo="Abren cursos de licencia profesional",
        prompt_veo='[El personaje dice "...", voz femenina clara, sincronización labial precisa]; ...',
        requires_approval=True,
    )
    result = send_qc_request(test_payload)
    print(json.dumps(result, indent=2, ensure_ascii=False))
