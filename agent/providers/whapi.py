# agent/providers/whapi.py — Adaptador para Whapi.cloud
# Generado por AgentKit

import os
import logging
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")


def _extraer_texto(msg: dict) -> str:
    """Extrae el texto del mensaje probando múltiples formatos de Whapi."""
    # Formato 1: text es un dict con body (documentado)
    text_field = msg.get("text")
    if isinstance(text_field, dict):
        body = text_field.get("body", "")
        if body:
            return body

    # Formato 2: body directo en el mensaje
    body = msg.get("body", "")
    if body:
        return body

    # Formato 3: text es un string directo
    if isinstance(text_field, str) and text_field:
        return text_field

    return ""


class ProveedorWhapi(ProveedorWhatsApp):
    """Proveedor de WhatsApp usando Whapi.cloud (REST API simple)."""

    def __init__(self):
        self.token = os.getenv("WHAPI_TOKEN")
        self.url_envio = "https://gate.whapi.cloud/messages/text"

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Parsea el payload de Whapi.cloud."""
        try:
            body = await request.json()
        except Exception as e:
            logger.error(f"Error parseando JSON del webhook: {e}")
            return []

        logger.debug(f"Whapi webhook payload: {body}")

        raw_messages = body.get("messages", [])
        if not raw_messages:
            logger.warning(f"Whapi: no 'messages' en payload. Keys recibidas: {list(body.keys())}")
            return []

        mensajes = []
        for msg in raw_messages:
            texto = _extraer_texto(msg)
            mensajes.append(MensajeEntrante(
                telefono=msg.get("chat_id", ""),
                texto=texto,
                mensaje_id=msg.get("id", ""),
                es_propio=msg.get("from_me", False),
            ))

        logger.info(f"Whapi: {len(mensajes)} mensaje(s) parseado(s)")
        return mensajes

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """Envía mensaje via Whapi.cloud."""
        if not self.token:
            logger.warning("WHAPI_TOKEN no configurado — mensaje no enviado")
            return False
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    self.url_envio,
                    json={"to": telefono, "body": mensaje},
                    headers=headers,
                )
                if not r.is_success:
                    logger.error(f"Error Whapi envío: {r.status_code} — {r.text}")
                    return False
                logger.debug(f"Whapi envío exitoso: {r.status_code} — {r.text}")
                return True
        except httpx.TimeoutException:
            logger.error(f"Timeout al enviar mensaje a {telefono}")
            return False
        except httpx.RequestError as e:
            logger.error(f"Error de conexión al enviar mensaje: {e}")
            return False
