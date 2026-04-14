# agent/providers/meta_comments.py — Adaptador para comentarios de Facebook e Instagram
# Generado por AgentKit

"""
Proveedor unificado para responder comentarios en Facebook Pages e Instagram.
Ambas plataformas usan la Meta Graph API y comparten webhook.

Webhook: POST /webhook/meta
- Facebook envía: {"object": "page", ...}
- Instagram envía: {"object": "instagram", ...}
"""

import os
import hmac
import hashlib
import logging
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")

API_VERSION = "v21.0"


class ProveedorMetaComentarios(ProveedorWhatsApp):
    """Proveedor unificado para comentarios de Facebook Pages e Instagram."""

    def __init__(self):
        self.page_token = os.getenv("FACEBOOK_PAGE_TOKEN")
        self.app_secret = os.getenv("FACEBOOK_APP_SECRET")
        self.verify_token = os.getenv("FACEBOOK_VERIFY_TOKEN", "chago-digital-verify")

    def _verificar_firma(self, body: bytes, firma_header: str) -> bool:
        """Verifica la firma X-Hub-Signature-256 de Meta."""
        if not self.app_secret or not firma_header:
            return True  # Sin app_secret, saltamos verificación (desarrollo)
        esperada = "sha256=" + hmac.new(
            self.app_secret.encode(),
            body,
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(firma_header, esperada)

    async def validar_webhook(self, request: Request) -> dict | int | None:
        """Verificación GET del webhook — Meta envía hub.mode + hub.challenge."""
        params = request.query_params
        mode = params.get("hub.mode")
        token = params.get("hub.verify_token")
        challenge = params.get("hub.challenge")

        if mode == "subscribe" and token == self.verify_token:
            logger.info("Meta webhook verificado correctamente")
            return challenge
        return None

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Parsea webhooks de Facebook (feed) e Instagram (comments)."""
        body_bytes = await request.body()

        # Verificar firma de Meta
        firma = request.headers.get("X-Hub-Signature-256", "")
        if not self._verificar_firma(body_bytes, firma):
            logger.warning("Meta webhook: firma inválida — ignorando")
            return []

        try:
            body = await request.json()
        except Exception as e:
            logger.error(f"Error parseando JSON del webhook Meta: {e}")
            return []

        logger.info(f"Meta webhook payload: {body}")

        mensajes = []
        objeto = body.get("object")

        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                field = change.get("field")
                value = change.get("value", {})

                # Facebook: comentarios en posts de la página
                if objeto == "page" and field == "feed":
                    msg = self._parsear_comentario_facebook(value)
                    if msg:
                        mensajes.append(msg)

                # Instagram: comentarios en posts/reels
                elif objeto == "instagram" and field == "comments":
                    msg = self._parsear_comentario_instagram(value)
                    if msg:
                        mensajes.append(msg)

        logger.info(f"Meta: {len(mensajes)} comentario(s) parseado(s)")
        return mensajes

    def _parsear_comentario_facebook(self, value: dict) -> MensajeEntrante | None:
        """Parsea un comentario de Facebook."""
        # Solo procesamos comentarios nuevos
        if value.get("item") != "comment" or value.get("verb") != "add":
            return None

        # Ignorar comentarios ocultos
        if value.get("is_hidden", False):
            return None

        comment_id = value.get("comment_id", "")
        texto = value.get("message", "")
        user_id = value.get("from", {}).get("id", "")
        user_name = value.get("from", {}).get("name", "")

        if not texto or not comment_id:
            return None

        logger.info(f"[facebook] Comentario de {user_name}: {texto[:80]}")

        return MensajeEntrante(
            telefono=comment_id,  # Usamos comment_id para responder al comentario
            texto=texto,
            mensaje_id=f"fb_{comment_id}",
            es_propio=False,
            canal="facebook",
        )

    def _parsear_comentario_instagram(self, value: dict) -> MensajeEntrante | None:
        """Parsea un comentario de Instagram."""
        comment_id = value.get("id", "")
        texto = value.get("text", "")
        username = value.get("from", {}).get("username", "")

        if not texto or not comment_id:
            return None

        logger.info(f"[instagram] Comentario de @{username}: {texto[:80]}")

        return MensajeEntrante(
            telefono=comment_id,  # Usamos comment_id para responder al comentario
            texto=texto,
            mensaje_id=f"ig_{comment_id}",
            es_propio=False,
            canal="instagram",
        )

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """
        Responde a un comentario de Facebook o Instagram.
        'telefono' aquí es el comment_id al que se responde.
        """
        if not self.page_token:
            logger.warning("FACEBOOK_PAGE_TOKEN no configurado — respuesta no enviada")
            return False

        # Determinar plataforma por el prefijo del mensaje_id no es posible aquí,
        # pero el campo 'telefono' siempre es un comment_id de Meta.
        # Facebook: POST /{comment-id}/comments
        # Instagram: POST /{comment-id}/replies
        # Intentamos ambos endpoints — el correcto funciona, el otro falla silenciosamente.

        # Primero intentamos como Facebook (más común)
        exito = await self._responder_facebook(telefono, mensaje)
        if not exito:
            # Si falla, intentamos como Instagram
            exito = await self._responder_instagram(telefono, mensaje)

        return exito

    async def _responder_facebook(self, comment_id: str, mensaje: str) -> bool:
        """Responde a un comentario de Facebook."""
        url = f"https://graph.facebook.com/{API_VERSION}/{comment_id}/comments"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(url, params={
                    "access_token": self.page_token,
                    "message": mensaje,
                })
                if r.is_success:
                    logger.info(f"Facebook respuesta enviada a comentario {comment_id}")
                    return True
                logger.info(f"Facebook respuesta falló: {r.status_code} — {r.text}")
                return False
        except Exception as e:
            logger.error(f"Error respondiendo comentario Facebook: {e}")
            return False

    async def _responder_instagram(self, comment_id: str, mensaje: str) -> bool:
        """Responde a un comentario de Instagram."""
        url = f"https://graph.facebook.com/{API_VERSION}/{comment_id}/replies"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(url, params={
                    "access_token": self.page_token,
                    "message": mensaje,
                })
                if r.is_success:
                    logger.info(f"Instagram respuesta enviada a comentario {comment_id}")
                    return True
                logger.info(f"Instagram respuesta falló: {r.status_code} — {r.text}")
                return False
        except Exception as e:
            logger.error(f"Error respondiendo comentario Instagram: {e}")
            return False
