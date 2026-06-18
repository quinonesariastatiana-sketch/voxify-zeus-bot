"""Zeus VoxifyHub — Bot Telegram directo (Python, sin n8n)"""
import os, json, logging
from datetime import datetime
import requests
import anthropic
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)

logging.basicConfig(
    format='%(asctime)s %(levelname)s %(name)s — %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN         = os.environ['TELEGRAM_BOT_TOKEN']
ANTHROPIC_KEY     = os.environ['ANTHROPIC_API_KEY']
HUBSPOT_KEY       = os.environ.get('HUBSPOT_API_KEY', '')
SLACK_WEBHOOK     = os.environ.get('SLACK_WEBHOOK_URL', '')
FLASK_URL         = os.environ.get('AGENCIA_CREATIVA_URL',
                        'https://voxify-agencia-creativa-production.up.railway.app')

claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

HS = {'Authorization': f'Bearer {HUBSPOT_KEY}', 'Content-Type': 'application/json'}


# ── Data helpers ─────────────────────────────────────────────────────────────

def voxify_stats() -> dict:
    try:
        r = requests.get(f'{FLASK_URL}/voxify-stats', timeout=8)
        return r.json()
    except Exception as e:
        logger.warning(f'voxify_stats error: {e}')
        return {}


def hs_contacts(limit=5) -> list:
    try:
        r = requests.get(
            'https://api.hubapi.com/crm/v3/objects/contacts',
            headers=HS,
            params={'limit': limit,
                    'properties': 'firstname,lastname,email,phone,createdate,lifecyclestage'},
            timeout=10
        )
        return r.json().get('results', [])
    except Exception as e:
        logger.warning(f'hs_contacts error: {e}')
        return []


def hs_deals(limit=10) -> list:
    try:
        r = requests.get(
            'https://api.hubapi.com/crm/v3/objects/deals',
            headers=HS,
            params={'limit': limit,
                    'properties': 'dealname,amount,dealstage,closedate,pipeline'},
            timeout=10
        )
        return r.json().get('results', [])
    except Exception as e:
        logger.warning(f'hs_deals error: {e}')
        return []


def zeus_claude(user_text: str, stats: dict) -> str:
    system = f"""Eres Zeus, CEO virtual de VoxifyHub — agencia de marketing con IA para negocios hispanos en EE.UU.

Estado actual del negocio ({stats.get('fecha', datetime.now().strftime('%Y-%m-%d'))}):
• Prospectos en pool: {stats.get('total_prospectos', '?')}
• Calificados: {stats.get('calificados', '?')}
• Nuevos hoy: {stats.get('nuevos', '?')}
• Contactados: {stats.get('contactados', '?')}
• Google API calls hoy: {stats.get('google_calls_hoy', '?')}

Responde en español, directo y ejecutivo. Máximo 350 palabras.
Usa *negrita* para énfasis (Telegram Markdown)."""

    msg = claude.messages.create(
        model='claude-opus-4-8',
        max_tokens=900,
        system=system,
        messages=[{'role': 'user', 'content': user_text}]
    )
    return msg.content[0].text


def slack_notify(text: str):
    if not SLACK_WEBHOOK:
        return
    try:
        requests.post(SLACK_WEBHOOK, json={'text': text}, timeout=5)
    except Exception:
        pass


# ── Handlers ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚡ *Zeus VoxifyHub — activo y listo.*\n\n"
        "Comandos:\n"
        "/stats — métricas de prospectos\n"
        "/leads — últimos 5 contactos en HubSpot\n"
        "/pipeline — deals activos en HubSpot\n"
        "/reporte — resumen ejecutivo del día\n\n"
        "O escríbeme en texto libre y respondo como CEO.",
        parse_mode='Markdown'
    )


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Obteniendo métricas...")
    s = voxify_stats()
    nota = "\n\n⚠️ _DB local no disponible en cloud (normal)_" if s.get('error') == 'DB not mounted' else ''
    text = (
        f"📊 *Stats VoxifyHub — {s.get('fecha', 'hoy')}*\n\n"
        f"Prospectos total: *{s.get('total_prospectos', 0)}*\n"
        f"Calificados: *{s.get('calificados', 0)}*\n"
        f"Nuevos: *{s.get('nuevos', 0)}*\n"
        f"Contactados: *{s.get('contactados', 0)}*\n"
        f"Google API calls hoy: *{s.get('google_calls_hoy', 0)}*\n"
        f"Costo Google acumulado: *${s.get('google_costo_total', 0):.4f}*"
        f"{nota}"
    )
    await update.message.reply_text(text, parse_mode='Markdown')


async def cmd_leads(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Consultando HubSpot...")
    contacts = hs_contacts(5)
    if not contacts:
        await update.message.reply_text(
            "❌ Sin contactos en HubSpot o error de API.\n"
            "Verifica que HUBSPOT_API_KEY esté configurado."
        )
        return
    lines = ["👥 *Últimos 5 contactos (HubSpot):*\n"]
    for c in contacts:
        p = c.get('properties', {})
        name = f"{p.get('firstname', '')} {p.get('lastname', '')}".strip() or 'Sin nombre'
        stage = p.get('lifecyclestage', '—')
        created = p.get('createdate', '')[:10] if p.get('createdate') else '—'
        lines.append(f"• *{name}* — {stage} ({created})")
    await update.message.reply_text('\n'.join(lines), parse_mode='Markdown')


async def cmd_pipeline(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Consultando pipeline...")
    deals = hs_deals()
    if not deals:
        await update.message.reply_text("❌ Sin deals en HubSpot o error de API.")
        return
    total = sum(float(d.get('properties', {}).get('amount') or 0) for d in deals)
    lines = [f"💼 *Pipeline HubSpot ({len(deals)} deals — ${total:,.0f} total):*\n"]
    for d in deals[:8]:
        p = d.get('properties', {})
        name = p.get('dealname', 'Sin nombre')
        amt = float(p.get('amount') or 0)
        stage = p.get('dealstage', '—')
        lines.append(f"• *{name}* — ${amt:,.0f} | {stage}")
    await update.message.reply_text('\n'.join(lines), parse_mode='Markdown')


async def cmd_reporte(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Generando reporte ejecutivo...")
    stats = voxify_stats()
    deals = hs_deals()
    contacts = hs_contacts(3)
    total_deals = sum(float(d.get('properties', {}).get('amount') or 0) for d in deals)

    prompt = (
        f"Genera un reporte ejecutivo diario para VoxifyHub.\n"
        f"Stats prospectos: {json.dumps(stats)}\n"
        f"HubSpot: {len(deals)} deals activos, ${total_deals:,.0f} en pipeline, "
        f"{len(contacts)} contactos recientes.\n"
        "Incluye: resumen de situación, logros del día, próximos pasos, alertas si hay algo urgente. "
        "Bullet points, tono ejecutivo, máximo 400 palabras."
    )
    reply = zeus_claude(prompt, stats)
    slack_notify(f"📋 *Reporte Zeus — {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n{reply}")
    await update.message.reply_text(f"📋 *Reporte del día*\n\n{reply}", parse_mode='Markdown')


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user.first_name or 'Tatiana'
    text = update.message.text
    logger.info(f'Mensaje de {user}: {text[:60]}')
    await update.message.reply_text("⏳ Procesando...")
    stats = voxify_stats()
    reply = zeus_claude(text, stats)
    await update.message.reply_text(reply, parse_mode='Markdown')


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start',    cmd_start))
    app.add_handler(CommandHandler('stats',    cmd_stats))
    app.add_handler(CommandHandler('leads',    cmd_leads))
    app.add_handler(CommandHandler('pipeline', cmd_pipeline))
    app.add_handler(CommandHandler('reporte',  cmd_reporte))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info('Zeus bot iniciando polling...')
    app.run_polling(drop_pending_updates=True, allowed_updates=['message'])


if __name__ == '__main__':
    main()
