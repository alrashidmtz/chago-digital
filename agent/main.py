# agent/main.py — Servidor FastAPI + Webhooks de WhatsApp y Telegram
# Generado por AgentKit

"""
Servidor principal del agente multicanal.
Soporta WhatsApp (Whapi/Meta/Twilio) y Telegram simultáneamente.
"""

import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv
import httpx

from agent.brain import generar_respuesta
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial
from agent.providers import obtener_proveedor, obtener_proveedor_telegram, obtener_proveedor_meta

load_dotenv()

# Configuración de logging según entorno
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
log_level = logging.DEBUG if ENVIRONMENT == "development" else logging.INFO
logging.basicConfig(level=log_level)
logger = logging.getLogger("agentkit")

# Proveedores de mensajería
proveedor = obtener_proveedor()              # WhatsApp (siempre activo)
proveedor_telegram = obtener_proveedor_telegram()  # Telegram (si hay token)
proveedor_meta = obtener_proveedor_meta()    # Facebook + Instagram (si hay token)
PORT = int(os.getenv("PORT", 8000))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa la base de datos y registra webhooks al arrancar."""
    await inicializar_db()
    logger.info("Base de datos inicializada")
    logger.info(f"Servidor AgentKit corriendo en puerto {PORT}")
    logger.info(f"WhatsApp: {proveedor.__class__.__name__}")

    # Registrar webhook de Telegram automáticamente si hay token y URL base
    if proveedor_telegram:
        logger.info("Telegram: ACTIVO")
        base_url = os.getenv("BASE_URL", "")
        if base_url:
            token = os.getenv("TELEGRAM_BOT_TOKEN")
            webhook_url = f"{base_url}/webhook/telegram"
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    f"https://api.telegram.org/bot{token}/setWebhook",
                    json={"url": webhook_url}
                )
                logger.info(f"Telegram setWebhook → {r.status_code}: {r.text}")
    else:
        logger.info("Telegram: DESACTIVADO (sin TELEGRAM_BOT_TOKEN)")

    if proveedor_meta:
        logger.info("Meta (Facebook + Instagram): ACTIVO")
    else:
        logger.info("Meta (Facebook + Instagram): DESACTIVADO (sin FACEBOOK_PAGE_TOKEN)")

    yield


app = FastAPI(
    title="AgentKit — WhatsApp + Telegram AI Agent",
    version="1.1.0",
    lifespan=lifespan,
    redirect_slashes=False
)


@app.middleware("http")
async def log_all_requests(request: Request, call_next):
    """Loguea todas las peticiones para diagnóstico."""
    logger.info(f">>> {request.method} {request.url.path} from {request.client.host if request.client else 'unknown'}")
    response = await call_next(request)
    logger.info(f"<<< {request.method} {request.url.path} → {response.status_code}")
    return response


# ─── Lógica compartida ───────────────────────────────────────────────

async def procesar_mensajes(mensajes: list, proveedor_activo):
    """Procesa mensajes de cualquier canal y responde via el proveedor dado."""
    for msg in mensajes:
        if msg.es_propio or not msg.texto:
            continue

        logger.info(f"[{msg.canal}] Mensaje de {msg.telefono}: {msg.texto}")

        historial = await obtener_historial(msg.telefono, canal=msg.canal)
        respuesta = await generar_respuesta(msg.texto, historial)

        await guardar_mensaje(msg.telefono, "user", msg.texto, canal=msg.canal)
        await guardar_mensaje(msg.telefono, "assistant", respuesta, canal=msg.canal)

        enviado = await proveedor_activo.enviar_mensaje(msg.telefono, respuesta)
        if not enviado:
            logger.error(f"[{msg.canal}] FALLO envío a {msg.telefono}")
        else:
            logger.info(f"[{msg.canal}] Respuesta enviada a {msg.telefono}: {respuesta[:100]}")


# ─── Endpoints ───────────────────────────────────────────────────────

@app.get("/")
async def health_check():
    """Endpoint de salud para Railway/monitoreo."""
    return {
        "status": "ok",
        "service": "agentkit",
        "canales": {
            "whatsapp": True,
            "telegram": proveedor_telegram is not None,
            "facebook": proveedor_meta is not None,
            "instagram": proveedor_meta is not None,
        }
    }


@app.get("/webhook")
@app.get("/webhook/")
async def webhook_verificacion(request: Request):
    """Verificación GET del webhook (requerido por Meta Cloud API, no-op para otros)."""
    resultado = await proveedor.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"status": "ok"}


# ─── WhatsApp ────────────────────────────────────────────────────────

@app.post("/webhook")
@app.post("/webhook/")
@app.post("/webhook/messages")
@app.post("/webhook/messages/")
async def webhook_whatsapp(request: Request):
    """Recibe mensajes de WhatsApp via Whapi.cloud (/webhook/messages)."""
    logger.info("Webhook WhatsApp POST recibido")
    try:
        mensajes = await proveedor.parsear_webhook(request)
        await procesar_mensajes(mensajes, proveedor)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error en webhook WhatsApp: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/webhook/statuses")
@app.post("/webhook/statuses/")
async def webhook_statuses(request: Request):
    """Whapi envía actualizaciones de estado aquí. Solo aceptamos sin procesar."""
    return {"status": "ok"}


# ─── Telegram ────────────────────────────────────────────────────────

@app.post("/webhook/telegram")
@app.post("/webhook/telegram/")
async def webhook_telegram(request: Request):
    """Recibe mensajes de Telegram via Bot API."""
    if not proveedor_telegram:
        raise HTTPException(status_code=404, detail="Telegram no configurado")

    logger.info("Webhook Telegram POST recibido")
    try:
        mensajes = await proveedor_telegram.parsear_webhook(request)
        await procesar_mensajes(mensajes, proveedor_telegram)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error en webhook Telegram: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Facebook + Instagram (Meta) ────────────────────────────────────

@app.get("/webhook/meta")
@app.get("/webhook/meta/")
async def webhook_meta_verificacion(request: Request):
    """Verificación GET del webhook de Meta (Facebook + Instagram)."""
    if not proveedor_meta:
        return {"status": "ok"}

    resultado = await proveedor_meta.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"error": "Verificación fallida"}, 403


@app.post("/webhook/meta")
@app.post("/webhook/meta/")
async def webhook_meta(request: Request):
    """Recibe comentarios de Facebook Pages e Instagram."""
    if not proveedor_meta:
        raise HTTPException(status_code=404, detail="Meta no configurado")

    logger.info("Webhook Meta POST recibido")
    try:
        mensajes = await proveedor_meta.parsear_webhook(request)
        await procesar_mensajes(mensajes, proveedor_meta)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error en webhook Meta: {e}")
        raise HTTPException(status_code=500, detail=str(e))
