# agent/providers/telegram.py — Adaptador para Telegram Bot API
# Generado por AgentKit

import os
import logging
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")


class ProveedorTelegram(ProveedorWhatsApp):
    """Proveedor de Telegram usando la Bot API oficial."""

    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.url_base = f"https://api.telegram.org/bot{self.token}"

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Parsea el payload de un Update de Telegram."""
        try:
            body = await request.json()
        except Exception as e:
            logger.error(f"Error parseando JSON del webhook Telegram: {e}")
            return []

        logger.info(f"Telegram webhook payload: {body}")

        # Telegram envía un Update con campo "message"
        mensaje_tg = body.get("message")
        if not mensaje_tg:
            logger.warning(f"Telegram: no 'message' en payload. Keys: {list(body.keys())}")
            return []

        texto = mensaje_tg.get("text", "")
        chat_id = str(mensaje_tg.get("chat", {}).get("id", ""))
        message_id = str(mensaje_tg.get("message_id", ""))

        # Los bots no reciben sus propios mensajes via webhook
        return [MensajeEntrante(
            telefono=chat_id,
            texto=texto,
            mensaje_id=message_id,
            es_propio=False,
            canal="telegram",
        )]

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """Envía un mensaje de texto via Telegram Bot API."""
        if not self.token:
            logger.warning("TELEGRAM_BOT_TOKEN no configurado — mensaje no enviado")
            return False

        url = f"{self.url_base}/sendMessage"
        payload = {
            "chat_id": int(telefono),
            "text": mensaje,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(url, json=payload)
                if not r.is_success:
                    logger.error(f"Error Telegram envío: {r.status_code} — {r.text}")
                    return False
                logger.info(f"Telegram envío exitoso a chat {telefono}")
                return True
        except httpx.TimeoutException:
            logger.error(f"Timeout al enviar mensaje Telegram a {telefono}")
            return False
        except httpx.RequestError as e:
            logger.error(f"Error de conexión Telegram: {e}")
            return False
