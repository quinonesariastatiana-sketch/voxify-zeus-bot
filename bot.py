"""Zeus VoxifyHub — Bot Telegram directo (Python, sin n8n)"""
import os, json, re, logging
from datetime import datetime
import requests
import anthropic
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(
    format='%(asctime)s %(levelname)s %(name)s — %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN     = os.environ['TELEGRAM_BOT_TOKEN']
ANTHROPIC_KEY = os.environ['ANTHROPIC_API_KEY']
HUBSPOT_KEY   = os.environ.get('HUBSPOT_API_KEY', '')
SLACK_WEBHOOK = os.environ.get('SLACK_WEBHOOK_URL', '')
FLASK_URL     = os.environ.get('AGENCIA_CREATIVA_URL',
                    'https://voxify-agencia-creativa-production.up.railway.app')

claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
HS = {'Authorization': f'Bearer {HUBSPOT_KEY}', 'Content-Type': 'application/json'}

ZEUS_SYSTEM = """Eres Zeus, CEO virtual de VoxifyHub — agencia de marketing con IA para negocios hispanos en EE.UU.
Tatiana Quiñones (fundadora) es quien te escribe. Trabajas como su brazo ejecutor.

REGLAS ABSOLUTAS — NUNCA las violes:
1. NUNCA pidas copies, textos o mensajes a Tatiana. Si necesitas generar contenido, LO GENERAS TÚ.
2. NUNCA preguntes "¿qué quieres que diga el mensaje?". TÚ decides el mensaje.
3. NUNCA pidas aprobación para generar contenido de outreach/SDR. Lo generas y lo presentas.
4. Solo escalas a Tatiana para: errores técnicos críticos, gasto >$80/mes, cliente cerrado, demo agendada.
5. Para TODO lo creativo (copies, propuestas, mensajes, emails): lo generas solo.

CUÁNDO GENERAR COPY SDR AUTOMÁTICAMENTE:
Si Tatiana menciona un prospecto con cualquier combinación de: nombre de negocio, tipo, ciudad, dolor, canal
→ Generas el copy de inmediato, sin preguntar nada más.
→ Formato: presenta el copy listo para copiar/pegar.
→ Reglas copy SMS (≤160 chars): mencionar negocio por nombre, CTA claro, sin precio día 1, tono Tatiana.
→ Reglas copy WhatsApp (≤300 chars): igual + puede ser levemente más cálido.

MANUAL DE MARCA VOXIFY (para todos los copies):
- Voz: cercana, empática, como Tatiana hablando directamente
- Nunca frío ni corporativo
- Español latinoamericano natural
- No mencionar que somos una agencia en día 1
- No revelar IA
- Inspirado en la historia de Tatiana (mamá emprendedora que entiende el negocio)

ESTADO DEL NEGOCIO (se actualiza en cada mensaje):
{stats_block}

Responde en español, directo y ejecutivo. Usa *negrita* para datos clave (Telegram Markdown).
Máximo 400 palabras salvo que generes copies (en ese caso el copy debe estar completo)."""

SDR_PROMPT = """Eres el SDR Agent de VoxifyHub. Genera UN SOLO mensaje de outreach para este prospecto:

Negocio: {nombre_negocio}
Tipo: {vertical}
Ciudad: {ciudad}
Dolor detectado: {dolor}
Canal: {canal} — DÍA {dia}

REGLAS ESTRICTAS:
- Genera SOLO el mensaje del día {dia}. NO generes secuencias ni múltiples días.
- Máximo 160 caracteres para SMS (incluyendo espacios y emojis)
- Máximo 300 caracteres para WhatsApp
- Mencionar el negocio por nombre
- No mencionar precio en día 1 o 3
- No revelar que eres IA
- Tono: Tatiana hablando directamente, cálida, latina
- En español latinoamericano natural
- CTA claro al final (ej: "¿Te llamo 5 min?", "¿Cuándo puedo llamarte?")

MANUAL DE MARCA VOXIFY:
- Cercano, empático, sin tecnicismos
- Nunca frío ni corporativo
- Tatiana es mamá latina que entiende el esfuerzo del negocio hispano

Responde con SOLO el texto del mensaje. Sin título, sin "Día X:", sin comillas extra."""


# ── Data helpers ──────────────────────────────────────────────────────────────

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
            'https://api.hubapi.com/crm/v3/objects/contacts', headers=HS,
            params={'limit': limit,
                    'properties': 'firstname,lastname,email,phone,createdate,lifecyclestage'},
            timeout=10)
        return r.json().get('results', [])
    except Exception as e:
        logger.warning(f'hs_contacts error: {e}')
        return []


def hs_deals(limit=10) -> list:
    try:
        r = requests.get(
            'https://api.hubapi.com/crm/v3/objects/deals', headers=HS,
            params={'limit': limit,
                    'properties': 'dealname,amount,dealstage,closedate,pipeline'},
            timeout=10)
        return r.json().get('results', [])
    except Exception as e:
        logger.warning(f'hs_deals error: {e}')
        return []


def generate_sdr_copy(nombre_negocio: str, vertical: str = 'negocio',
                      ciudad: str = 'Orlando', dolor: str = 'sin presencia digital',
                      canal: str = 'SMS', dia: int = 1) -> str:
    prompt = SDR_PROMPT.format(
        nombre_negocio=nombre_negocio,
        vertical=vertical,
        ciudad=ciudad,
        dolor=dolor,
        canal=canal,
        dia=dia
    )
    msg = claude.messages.create(
        model='claude-opus-4-8',
        max_tokens=300,
        messages=[{'role': 'user', 'content': prompt}]
    )
    return msg.content[0].text.strip()


def zeus_claude(user_text: str, stats: dict) -> str:
    fecha = stats.get('fecha', datetime.now().strftime('%Y-%m-%d'))
    stats_block = (
        f"Fecha: {fecha}\n"
        f"• Prospectos total: {stats.get('total_prospectos', '?')}\n"
        f"• Calificados (score≥8): {stats.get('calificados', '?')}\n"
        f"• Nuevos hoy: {stats.get('nuevos', '?')}\n"
        f"• Contactados: {stats.get('contactados', '?')}\n"
        f"• Google API calls hoy: {stats.get('google_calls_hoy', '?')}\n"
        f"• Costo Google acumulado: ${stats.get('google_costo_total', 0):.4f}"
    )
    system = ZEUS_SYSTEM.format(stats_block=stats_block)
    msg = claude.messages.create(
        model='claude-opus-4-8',
        max_tokens=1000,
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


def detect_copy_request(text: str) -> dict | None:
    """Detect if user is asking for an SDR copy and extract prospect data."""
    text_lower = text.lower()
    copy_keywords = ['copy', 'mensaje', 'sms', 'whatsapp', 'primer contacto',
                     'contactar', 'mensaje para', 'escríbele', 'escribe para',
                     'genera para', 'genera un mensaje']
    if not any(kw in text_lower for kw in copy_keywords):
        return None

    # Try to extract prospect info from text
    # Pattern: common format like "copy para [negocio], [tipo], [ciudad], dolor: [dolor]"
    data = {}

    # Extract canal
    data['canal'] = 'WhatsApp' if 'whatsapp' in text_lower else 'SMS'

    # Extract day
    day_match = re.search(r'día\s*(\d)', text_lower)
    data['dia'] = int(day_match.group(1)) if day_match else 1

    return data  # Caller should fill in missing fields via Zeus Claude context


# ── Handlers ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚡ *Zeus VoxifyHub — activo y listo.*\n\n"
        "Comandos:\n"
        "/stats — métricas de prospectos\n"
        "/leads — últimos 5 contactos en HubSpot\n"
        "/pipeline — deals activos en HubSpot\n"
        "/reporte — resumen ejecutivo del día\n"
        "/copy — generar copy SDR para un prospecto\n\n"
        "O escríbeme directamente — genero copies, analizo prospectos, reporto datos.",
        parse_mode='Markdown'
    )


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Obteniendo métricas...")
    s = voxify_stats()
    nota = "\n\n⚠️ _DB local no disponible en cloud_" if s.get('error') == 'DB not mounted' else ''
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


async def cmd_copy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Generate SDR copy. Usage: /copy NombreNegocio | tipo | ciudad | dolor | canal"""
    args = ' '.join(ctx.args) if ctx.args else ''

    if not args:
        await update.message.reply_text(
            "📝 *Generador de Copy SDR*\n\n"
            "Uso: `/copy NombreNegocio | tipo | ciudad | dolor | canal`\n\n"
            "Ejemplo:\n"
            "`/copy Ají Pique | restaurante colombiano | Orlando | sin website | SMS`\n\n"
            "O simplemente dime: _\"genera un SMS para Ají Pique, restaurante colombiano en Orlando, sin website\"_",
            parse_mode='Markdown'
        )
        return

    parts = [p.strip() for p in args.split('|')]
    nombre   = parts[0] if len(parts) > 0 else args
    vertical = parts[1] if len(parts) > 1 else 'negocio'
    ciudad   = parts[2] if len(parts) > 2 else 'Orlando'
    dolor    = parts[3] if len(parts) > 3 else 'sin presencia digital'
    canal    = parts[4] if len(parts) > 4 else 'SMS'

    await update.message.reply_text(f"⏳ Generando copy {canal} para *{nombre}*...", parse_mode='Markdown')
    copy_text = generate_sdr_copy(nombre, vertical, ciudad, dolor, canal, dia=1)
    char_count = len(copy_text)
    limit = 160 if canal.upper() == 'SMS' else 300

    status = "✅" if char_count <= limit else "⚠️"
    reply = (
        f"📱 *Copy {canal} — {nombre}*\n"
        f"_{status} {char_count}/{limit} caracteres_\n\n"
        f"`{copy_text}`"
    )
    await update.message.reply_text(reply, parse_mode='Markdown')


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user.first_name or 'Tatiana'
    text = update.message.text
    logger.info(f'Mensaje de {user}: {text[:80]}')

    # Quick copy detection: "copy/mensaje/sms para [negocio]" + enough data
    # Use Zeus Claude with the full system prompt so it generates the copy itself
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
    app.add_handler(CommandHandler('copy',     cmd_copy))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info('Zeus bot iniciando polling...')
    app.run_polling(drop_pending_updates=True, allowed_updates=['message'])


if __name__ == '__main__':
    main()
