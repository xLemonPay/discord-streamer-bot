import os
import asyncio
import io
import re
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
import aiohttp
from aiohttp import web

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
PORT = int(os.getenv("PORT", "8000") or 8000)
ENABLE_MESSAGE_LOGS = os.getenv("ENABLE_MESSAGE_LOGS", "false").strip().lower() in {"1", "true", "yes", "on"}

# Twitch: detección automática mediante la API oficial (polling).
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID", "").strip()
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET", "").strip()
TWITCH_CHANNEL = os.getenv("TWITCH_CHANNEL", "").strip().lstrip("@").lower()
STREAMER_DISCORD_ID = int(os.getenv("STREAMER_DISCORD_ID", "0") or 0)
TWITCH_POLL_SECONDS = max(30, int(os.getenv("TWITCH_POLL_SECONDS", "60") or 60))
TWITCH_OFFLINE_DELETE_DELAY = max(0, int(os.getenv("TWITCH_OFFLINE_DELETE_DELAY", "300") or 300))
TWITCH_ENABLED = bool(TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET and TWITCH_CHANNEL)

# ──────────────────────────────────────────────────────────────────────────────
# NOMBRES

# ──────────────────────────────────────────────────────────────────────────────
# TWITCH — DETECCIÓN AUTOMÁTICA DE DIRECTOS
# ──────────────────────────────────────────────────────────────────────────────

_twitch_app_token: Optional[str] = None
_twitch_token_expires_at: float = 0.0
_twitch_was_live: Optional[bool] = None
_twitch_last_stream_id: Optional[str] = None
_twitch_delete_task: Optional[asyncio.Task] = None

# Mientras esta bandera está activa, el watcher real de Twitch no modifica
# el estado simulado. /twitch-fin-prueba vuelve a sincronizar con Twitch real.
_twitch_test_mode: bool = False


def twitch_missing_config() -> list[str]:
    missing = []
    if not TWITCH_CLIENT_ID:
        missing.append("TWITCH_CLIENT_ID")
    if not TWITCH_CLIENT_SECRET:
        missing.append("TWITCH_CLIENT_SECRET")
    if not TWITCH_CHANNEL:
        missing.append("TWITCH_CHANNEL")
    return missing


def twitch_url() -> str:
    return f"https://www.twitch.tv/{TWITCH_CHANNEL}" if TWITCH_CHANNEL else "https://www.twitch.tv/"


def twitch_streamer_members(guild: discord.Guild) -> list[discord.Member]:
    """Localiza a la streamer por ID opcional o por el rol 🎥・Streamer."""
    if STREAMER_DISCORD_ID:
        member = guild.get_member(STREAMER_DISCORD_ID)
        if member is not None:
            return [member]

    role = find_role(guild, ROLE_STREAMER)
    return list(role.members) if role is not None else []


async def twitch_session() -> aiohttp.ClientSession:
    session = getattr(bot, "twitch_session", None)
    if session is None or session.closed:
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        bot.twitch_session = session
    return session


async def get_twitch_app_token(force_refresh: bool = False) -> str:
    """Obtiene y cachea un App Access Token con Client Credentials."""
    global _twitch_app_token, _twitch_token_expires_at

    if not TWITCH_ENABLED:
        raise RuntimeError("Twitch no está configurado")

    now = time.time()
    if (
        not force_refresh
        and _twitch_app_token
        and now < (_twitch_token_expires_at - 60)
    ):
        return _twitch_app_token

    session = await twitch_session()
    async with session.post(
        "https://id.twitch.tv/oauth2/token",
        params={
            "client_id": TWITCH_CLIENT_ID,
            "client_secret": TWITCH_CLIENT_SECRET,
            "grant_type": "client_credentials",
        },
    ) as response:
        data = await response.json(content_type=None)
        if response.status != 200:
            raise RuntimeError(
                f"Twitch OAuth devolvió HTTP {response.status}: {data}"
            )

    _twitch_app_token = data["access_token"]
    _twitch_token_expires_at = now + int(data.get("expires_in", 3600))
    return _twitch_app_token


async def fetch_twitch_stream() -> Optional[dict]:
    """Devuelve el stream actual del canal o None si está offline."""
    token = await get_twitch_app_token()
    session = await twitch_session()

    async def request(current_token: str):
        return await session.get(
            "https://api.twitch.tv/helix/streams",
            params={"user_login": TWITCH_CHANNEL},
            headers={
                "Authorization": f"Bearer {current_token}",
                "Client-Id": TWITCH_CLIENT_ID,
            },
        )

    response = await request(token)
    if response.status == 401:
        response.release()
        token = await get_twitch_app_token(force_refresh=True)
        response = await request(token)

    async with response:
        data = await response.json(content_type=None)
        if response.status != 200:
            raise RuntimeError(
                f"Twitch Get Streams devolvió HTTP {response.status}: {data}"
            )

    streams = data.get("data") or []
    return streams[0] if streams else None


async def ensure_twitch_roles(guild: discord.Guild) -> tuple[discord.Role, discord.Role]:
    no_perms = discord.Permissions.none()
    live_role = await ensure_role(guild, ROLE_LIVE, no_perms, 0xED4245, True)
    notify_role = await ensure_role(guild, ROLE_LIVE_NOTIFY, no_perms, 0x9146FF, False)
    return live_role, notify_role


async def ensure_twitch_notify_panel(guild: discord.Guild) -> Optional[discord.Message]:
    channel = find_text(guild, CH_ROLES)
    if channel is None:
        return None

    await ensure_twitch_roles(guild)
    return await ensure_reaction_role_panel(
        channel,
        ROLE_PANEL_NOTIFY_TITLE,
        (
            "↳ **¿Querés que Discord te avise cuando empiece el stream?**\n\n"
            "🔔 ─ reaccioná para recibir el rol de avisos.\n\n"
            "Podés quitar la reacción cuando quieras para dejar de recibir menciones."
        ),
        STREAM_NOTIFY_REACTION_ROLES,
    )


def twitch_live_channel_overwrites(guild: discord.Guild) -> dict:
    everyone = guild.default_role
    member = find_role(guild, ROLE_MEMBER)
    streamer = find_role(guild, ROLE_STREAMER)
    owner = find_role(guild, ROLE_OWNER)
    coowner = find_role(guild, ROLE_COOWNER)
    admin = find_role(guild, ROLE_ADMIN)
    mod = find_role(guild, ROLE_MOD)

    ow = {
        everyone: discord.PermissionOverwrite(view_channel=False),
    }

    normal = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        add_reactions=True,
        read_message_history=True,
        attach_files=True,
        embed_links=True,
        use_external_emojis=True,
        use_external_stickers=True,
    )

    for role in (member, streamer, owner, coowner, admin, mod):
        if role is not None:
            ow[role] = normal
    return ow


async def ensure_live_channel(guild: discord.Guild, stream: dict) -> discord.TextChannel:
    channel = find_text(guild, CH_LIVE)
    category = find_category(guild, CAT_COMMUNITY) or find_category(guild, CAT_INFO)
    if category is None:
        raise RuntimeError("No encuentro la categoría de COMUNIDAD o INFORMACIÓN")

    title = (stream.get("title") or "Directo en Twitch").strip()
    game = (stream.get("game_name") or "Sin categoría").strip()
    topic = f"🔴 EN DIRECTO • {game} • {title}"[:1024]
    overwrites = twitch_live_channel_overwrites(guild)

    if channel is None:
        channel = await guild.create_text_channel(
            CH_LIVE,
            category=category,
            overwrites=overwrites,
            topic=topic,
            reason="La streamer empezó directo en Twitch",
        )
    elif channel.topic != topic or channel.category_id != category.id:
        # Solo edita cuando cambió el título/juego o la categoría. Evita gastar
        # requests de Discord en cada consulta de Twitch.
        await channel.edit(
            category=category,
            topic=topic,
            reason="Sincronización del directo de Twitch",
        )
    return channel


def twitch_embed(stream: dict) -> discord.Embed:
    display_name = stream.get("user_name") or TWITCH_CHANNEL
    title = stream.get("title") or "Estamos en directo"
    game = stream.get("game_name") or "Sin categoría"
    viewers = stream.get("viewer_count", 0)
    stream_id = str(stream.get("id") or "")

    embed = discord.Embed(
        title=f"🔴 {display_name} está EN DIRECTO",
        url=twitch_url(),
        description=f"**{safe_text(title, 500)}**",
        colour=discord.Colour.red(),
    )
    embed.add_field(name="🎮 Categoría", value=safe_text(game, 100), inline=True)
    embed.add_field(name="👀 Espectadores", value=str(viewers), inline=True)
    started = stream.get("started_at")
    if started:
        embed.add_field(name="🕒 Empezó", value=started.replace("T", " ").replace("Z", " UTC"), inline=False)

    thumb = stream.get("thumbnail_url")
    if thumb:
        thumb = thumb.replace("{width}", "1280").replace("{height}", "720")
        embed.set_image(url=thumb)

    embed.set_footer(text=f"Twitch stream ID: {stream_id}")
    return embed


def twitch_link_view() -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="Ver en Twitch",
            emoji="🟣",
            style=discord.ButtonStyle.link,
            url=twitch_url(),
        )
    )
    return view


def twitch_test_stream() -> dict:
    """Datos ficticios para previsualizar/probar Twitch sin prender el canal real."""
    display_name = TWITCH_CHANNEL or "Streamer"
    return {
        "id": "TEST-STREAM",
        "user_name": display_name,
        "title": "Rankeds de Valorant 💜 — vista previa del directo",
        "game_name": "VALORANT",
        "viewer_count": 123,
        "started_at": discord.utils.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        # No usamos una miniatura externa inventada: en un directo real Twitch
        # entregará la miniatura automáticamente.
        "thumbnail_url": "",
    }


def twitch_test_embed(stream: Optional[dict] = None) -> discord.Embed:
    stream = stream or twitch_test_stream()
    embed = twitch_embed(stream)
    embed.title = f"🧪 PRUEBA • {embed.title}"
    embed.add_field(
        name="🧪 Simulación",
        value="Este mensaje es una prueba. La streamer no está necesariamente en directo.",
        inline=False,
    )
    embed.set_footer(text="Vista previa del sistema automático de Twitch")
    return embed


async def sync_real_twitch_after_test(guild: discord.Guild) -> str:
    """Tras una simulación, restaura el estado real consultando Twitch."""
    if not TWITCH_ENABLED:
        await bot.change_presence(status=discord.Status.online, activity=None)
        return "Twitch real no está configurado; se restauró el estado normal del bot."

    try:
        stream = await fetch_twitch_stream()
    except Exception as exc:
        await bot.change_presence(status=discord.Status.online, activity=None)
        return f"No pude volver a consultar Twitch: {type(exc).__name__}: {str(exc)[:250]}"

    if stream is not None:
        await handle_twitch_online(guild, stream)
        return f"`@{TWITCH_CHANNEL}` está realmente EN DIRECTO y quedó sincronizado."

    await handle_twitch_offline(guild)
    return f"`@{TWITCH_CHANNEL}` está realmente offline y quedó sincronizado."


async def stream_already_announced(channel: discord.TextChannel, stream_id: str) -> bool:
    wanted = f"Twitch stream ID: {stream_id}"
    try:
        async for msg in channel.history(limit=80):
            if msg.author != channel.guild.me or not msg.embeds:
                continue
            if msg.embeds[0].footer and msg.embeds[0].footer.text == wanted:
                return True
    except discord.Forbidden:
        pass
    return False


async def send_live_announcement(guild: discord.Guild, stream: dict) -> None:
    directos = find_text(guild, CH_STREAMS)
    if directos is None:
        return

    stream_id = str(stream.get("id") or "")
    if stream_id and await stream_already_announced(directos, stream_id):
        return

    notify_role = find_role(guild, ROLE_LIVE_NOTIFY)
    mention = notify_role.mention if notify_role is not None else ""
    changed_mentionable = False

    # Hacemos el rol mencionable solo durante el envío para que la comunidad no
    # pueda spamear @Avisos de directo el resto del tiempo.
    if notify_role is not None and not notify_role.mentionable:
        me = guild.me
        if me is not None and notify_role < me.top_role:
            try:
                await notify_role.edit(mentionable=True, reason="Aviso automático de Twitch")
                changed_mentionable = True
            except discord.Forbidden:
                pass

    try:
        await directos.send(
            content=mention or None,
            embed=twitch_embed(stream),
            view=twitch_link_view(),
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                users=False,
                roles=[notify_role] if notify_role is not None else False,
            ),
        )
    finally:
        if changed_mentionable:
            try:
                await notify_role.edit(mentionable=False, reason="Fin del aviso automático de Twitch")
            except discord.Forbidden:
                pass


async def add_live_role_to_streamer(guild: discord.Guild) -> int:
    live_role = find_role(guild, ROLE_LIVE)
    if live_role is None:
        live_role, _ = await ensure_twitch_roles(guild)

    count = 0
    for member in twitch_streamer_members(guild):
        if live_role not in member.roles:
            try:
                await member.add_roles(live_role, reason="Streamer en directo en Twitch")
                count += 1
            except discord.Forbidden:
                print(f"⚠️ No pude asignar {ROLE_LIVE} a {member} por jerarquía/permisos.")
    return count


async def remove_live_role_from_streamer(guild: discord.Guild) -> int:
    live_role = find_role(guild, ROLE_LIVE)
    if live_role is None:
        return 0

    count = 0
    for member in twitch_streamer_members(guild):
        if live_role in member.roles:
            try:
                await member.remove_roles(live_role, reason="Terminó el directo de Twitch")
                count += 1
            except discord.Forbidden:
                pass
    return count


async def delete_live_channel_later(guild_id: int) -> None:
    global _twitch_delete_task
    try:
        if TWITCH_OFFLINE_DELETE_DELAY:
            await asyncio.sleep(TWITCH_OFFLINE_DELETE_DELAY)

        guild = bot.get_guild(guild_id)
        if guild is None:
            return

        # Antes de borrar volvemos a consultar Twitch por si reinició stream.
        try:
            stream = await fetch_twitch_stream()
        except Exception as exc:
            print(f"⚠️ No borré el canal live porque no pude verificar Twitch: {exc}")
            return
        if stream is not None:
            return

        channel = find_text(guild, CH_LIVE)
        if channel is not None:
            try:
                await channel.delete(reason="El directo de Twitch terminó")
            except discord.Forbidden:
                pass
    finally:
        _twitch_delete_task = None


async def handle_twitch_online(guild: discord.Guild, stream: dict) -> None:
    global _twitch_was_live, _twitch_last_stream_id, _twitch_delete_task

    stream_id = str(stream.get("id") or "")
    first_sync = _twitch_was_live is not True or _twitch_last_stream_id != stream_id

    if _twitch_delete_task is not None and not _twitch_delete_task.done():
        _twitch_delete_task.cancel()
        _twitch_delete_task = None

    if first_sync:
        if find_role(guild, ROLE_LIVE) is None or find_role(guild, ROLE_LIVE_NOTIFY) is None:
            await ensure_twitch_roles(guild)
        await ensure_twitch_notify_panel(guild)

    live_channel = await ensure_live_channel(guild, stream)
    await add_live_role_to_streamer(guild)

    if first_sync:
        await bot.change_presence(
            status=discord.Status.online,
            activity=discord.Streaming(
                name=f"{stream.get('user_name') or TWITCH_CHANNEL} en Twitch",
                url=twitch_url(),
            ),
        )

        # Anuncia una sola vez por ID de stream, incluso si Northflank reinicia.
        directos = find_text(guild, CH_STREAMS)
        already = False
        if directos is not None and stream_id:
            already = await stream_already_announced(directos, stream_id)
        if not already:
            await send_live_announcement(guild, stream)

        try:
            await live_channel.send(
                embed=twitch_embed(stream),
                view=twitch_link_view(),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden:
            pass

    _twitch_was_live = True
    _twitch_last_stream_id = stream_id


async def handle_twitch_offline(guild: discord.Guild) -> None:
    global _twitch_was_live, _twitch_last_stream_id, _twitch_delete_task

    was_live = _twitch_was_live is True
    first_offline_sync = _twitch_was_live is not False
    await remove_live_role_from_streamer(guild)
    if first_offline_sync:
        await bot.change_presence(status=discord.Status.online, activity=None)

    live_channel = find_text(guild, CH_LIVE)
    if live_channel is not None and (_twitch_delete_task is None or _twitch_delete_task.done()):
        if was_live:
            minutes = max(0, TWITCH_OFFLINE_DELETE_DELAY // 60)
            text = "🌙 **El directo terminó.** Gracias por acompañar 💜"
            if TWITCH_OFFLINE_DELETE_DELAY:
                text += f"\nEste canal se cerrará automáticamente en aproximadamente {minutes or 1} minuto(s)."
            try:
                await live_channel.send(text)
            except discord.Forbidden:
                pass

        _twitch_delete_task = asyncio.create_task(delete_live_channel_later(guild.id))

    _twitch_was_live = False
    _twitch_last_stream_id = None


@tasks.loop(seconds=TWITCH_POLL_SECONDS)
async def twitch_watch():
    if _twitch_test_mode:
        return
    if not TWITCH_ENABLED or not GUILD_ID:
        return

    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return

    try:
        stream = await fetch_twitch_stream()
        if stream is not None:
            await handle_twitch_online(guild, stream)
        else:
            await handle_twitch_offline(guild)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"⚠️ Twitch watcher: {type(exc).__name__}: {exc}")


@twitch_watch.before_loop
async def before_twitch_watch():
    await bot.wait_until_ready()


# ──────────────────────────────────────────────────────────────────────────────

ROLE_MEMBER = "✅・Miembro"

ROLE_OWNER = "👑・Owner"
ROLE_COOWNER = "💎・Co-Owner"
ROLE_ADMIN = "🛡️・Admin"
ROLE_MOD = "🔨・Moderador"
ROLE_STREAMER = "🎥・Streamer"
ROLE_SUB = "💜・Subscriber"
ROLE_VIP = "⭐・VIP"
ROLE_LIVE = "🔴・EN DIRECTO"
ROLE_LIVE_NOTIFY = "🔔・Avisos de directo"

COUNTRIES = [
    "🇵🇾・Paraguay",
    "🇦🇷・Argentina",
    "🇧🇷・Brasil",
    "🇺🇾・Uruguay",
    "🇨🇱・Chile",
    "🇧🇴・Bolivia",
    "🇵🇪・Perú",
    "🇨🇴・Colombia",
    "🇻🇪・Venezuela",
    "🇪🇨・Ecuador",
    "🇲🇽・México",
    "🇪🇸・España",
    "🌎・Otro",
]

VALORANT_RANKS = [
    "⚫・Sin rango",
    "⬛・Hierro",
    "🟫・Bronce",
    "⬜・Plata",
    "🟨・Oro",
    "🟩・Platino",
    "💎・Diamante",
    "🟪・Ascendente",
    "🟥・Inmortal",
    "🌟・Radiante",
]

COUNTRY_REACTION_ROLES = {name.split("・", 1)[0]: name for name in COUNTRIES}

# El bot busca estos emojis personalizados por NOMBRE dentro del servidor.
# Si alguno no existe, usa temporalmente el emoji normal del rol como fallback.
VALORANT_CUSTOM_EMOJIS = {
    "⚫・Sin rango": None,
    "⬛・Hierro": "valoranthierro",
    "🟫・Bronce": "valorantbronce",
    "⬜・Plata": "valorantplata",
    "🟨・Oro": "valorantoro",
    "🟩・Platino": "valorantplatino",
    "💎・Diamante": "valorantdiamante",
    "🟪・Ascendente": "valorantascendente",
    "🟥・Inmortal": "valorantimmortal",
    "🌟・Radiante": "valorantradiante",
}

ROLE_PANEL_COUNTRY_TITLE = "🌎 Elegí tu país"
ROLE_PANEL_RANK_TITLE = "🔫 Elegí tu rango de Valorant"
ROLE_PANEL_NOTIFY_TITLE = "🔔 Avisos de directo"
STREAM_NOTIFY_REACTION_ROLES = {"🔔": ROLE_LIVE_NOTIFY}
GUIDE_PREFIX = "📌 Guía — "

CAT_INFO = "╭・📌 INFORMACIÓN"
CAT_COMMUNITY = "╭・💬 COMUNIDAD"
CAT_GAMING = "╭・🎮 GAMING"
CAT_VOICE = "╭・🔊 VOZ"
CAT_STAFF = "╭・🛡️ STAFF"
CAT_TICKETS = "╭・🎫 TICKETS"

CH_VERIFY = "✅・verificación"
CH_RULES = "📜・reglas"
CH_ROLES = "🎭・roles"
CH_ANNOUNCEMENTS = "📢・anuncios"
CH_STREAMS = "🎥・directos"
CH_STREAMER_ONLY = "💜・aqui-solo-habla-la-streamer"
CH_LIVE = "🔴・stream-en-vivo"

CH_GENERAL = "💬・general"
CH_MEDIA = "📸・multimedia"
CH_MEMES = "😂・memes"
CH_COMMANDS = "🤖・comandos"

CH_GAMING = "🎮・gaming"
CH_VALORANT = "🔫・valorant"
CH_LFG = "🔎・busco-grupo"

VC_GENERAL = "🔊・General"
VC_GAMING = "🎮・Gaming"
VC_VALORANT = "🔫・Valorant"
VC_CREATE = "➕・Crear sala"
TEMP_VC_PREFIX = "🎮・Sala de "

CH_STAFF = "💬・staff"
CH_REPORTS = "📋・reportes"
CH_LOGS = "📜・logs"


# ──────────────────────────────────────────────────────────────────────────────
# UTILIDADES
# ──────────────────────────────────────────────────────────────────────────────

def find_role(guild: discord.Guild, name: str) -> Optional[discord.Role]:
    return discord.utils.get(guild.roles, name=name)


def find_category(guild: discord.Guild, name: str) -> Optional[discord.CategoryChannel]:
    return discord.utils.get(guild.categories, name=name)


def find_text(guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
    return discord.utils.get(guild.text_channels, name=name)


def find_voice(guild: discord.Guild, name: str) -> Optional[discord.VoiceChannel]:
    return discord.utils.get(guild.voice_channels, name=name)


def build_rank_reaction_roles(guild: discord.Guild) -> dict[str, str]:
    """Devuelve emoji -> rol usando los emojis personalizados del servidor por nombre."""
    mapping: dict[str, str] = {}

    for role_name in VALORANT_RANKS:
        emoji_name = VALORANT_CUSTOM_EMOJIS.get(role_name)

        if emoji_name:
            custom_emoji = discord.utils.get(guild.emojis, name=emoji_name)
            if custom_emoji is not None:
                mapping[str(custom_emoji)] = role_name
                continue

        # Fallback si el emoji personalizado todavía no fue subido.
        mapping[role_name.split("・", 1)[0]] = role_name

    return mapping


async def ensure_role(
    guild: discord.Guild,
    name: str,
    permissions: discord.Permissions,
    colour: int = 0x99AAB5,
    hoist: bool = False,
) -> discord.Role:
    role = find_role(guild, name)
    if role is None:
        role = await guild.create_role(
            name=name,
            permissions=permissions,
            colour=discord.Colour(colour),
            hoist=hoist,
            mentionable=False,
            reason="Setup automático del servidor",
        )
    else:
        # Discord no permite que un bot edite roles que estén por encima de su rol.
        # Los respetamos en vez de hacer fallar todo /setup.
        bot_member = guild.me
        if bot_member is not None and role >= bot_member.top_role:
            return role

        await role.edit(
            permissions=permissions,
            colour=discord.Colour(colour),
            hoist=hoist,
            mentionable=False,
            reason="Actualización de permisos del setup",
        )
    return role


async def ensure_category(
    guild: discord.Guild,
    name: str,
    overwrites: dict,
) -> discord.CategoryChannel:
    category = find_category(guild, name)
    if category is None:
        category = await guild.create_category(
            name,
            overwrites=overwrites,
            reason="Setup automático del servidor",
        )
    else:
        await category.edit(
            overwrites=overwrites,
            reason="Actualización de permisos del setup",
        )
    return category


async def ensure_text_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    name: str,
    overwrites: Optional[dict] = None,
    topic: Optional[str] = None,
) -> discord.TextChannel:
    channel = find_text(guild, name)
    if channel is None:
        channel = await guild.create_text_channel(
            name,
            category=category,
            overwrites=overwrites,
            topic=topic,
            reason="Setup automático del servidor",
        )
    else:
        await channel.edit(
            category=category,
            overwrites=overwrites if overwrites is not None else category.overwrites,
            topic=topic,
            reason="Actualización del setup",
        )
    return channel


async def ensure_voice_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    name: str,
    overwrites: Optional[dict] = None,
) -> discord.VoiceChannel:
    channel = find_voice(guild, name)
    if channel is None:
        channel = await guild.create_voice_channel(
            name,
            category=category,
            overwrites=overwrites,
            reason="Setup automático del servidor",
        )
    else:
        await channel.edit(
            category=category,
            overwrites=overwrites if overwrites is not None else category.overwrites,
            reason="Actualización del setup",
        )
    return channel



async def ensure_guide(
    channel: discord.TextChannel,
    title: str,
    description: str,
    colour: discord.Colour = discord.Colour.blurple(),
) -> discord.Message:
    """Crea o actualiza una guía del bot sin duplicarla al repetir /setup."""
    full_title = f"{GUIDE_PREFIX}{title}"
    async for msg in channel.history(limit=60):
        if msg.author == channel.guild.me and msg.embeds and msg.embeds[0].title == full_title:
            embed = discord.Embed(title=full_title, description=description, colour=colour)
            try:
                await msg.edit(embed=embed)
            except discord.Forbidden:
                pass
            return msg

    embed = discord.Embed(title=full_title, description=description, colour=colour)
    return await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def find_bot_embed_message(
    channel: discord.TextChannel,
    title: str,
    limit: int = 80,
) -> Optional[discord.Message]:
    async for msg in channel.history(limit=limit):
        if msg.author == channel.guild.me and msg.embeds and msg.embeds[0].title == title:
            return msg
    return None


async def ensure_reaction_role_panel(
    channel: discord.TextChannel,
    title: str,
    description: str,
    mapping: dict[str, str],
) -> discord.Message:
    """Crea/actualiza un panel de reaction roles y asegura todas las reacciones."""
    message = await find_bot_embed_message(channel, title)
    embed = discord.Embed(title=title, description=description, colour=discord.Colour.blurple())
    embed.set_footer(text="Reaccioná para asignarte el rol • Quitá tu reacción para quitarlo")

    if message is None:
        message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    else:
        try:
            await message.edit(embed=embed, view=None)
        except discord.Forbidden:
            pass

    # Sincroniza las reacciones del panel:
    # quita las que ya no están configuradas y agrega las nuevas.
    wanted = set(mapping.keys())

    for reaction in list(message.reactions):
        if str(reaction.emoji) not in wanted:
            try:
                await message.clear_reaction(reaction.emoji)
            except (discord.Forbidden, discord.HTTPException):
                pass

    existing = {str(reaction.emoji) for reaction in message.reactions}
    for emoji in mapping:
        if emoji not in existing:
            try:
                reaction_emoji = (
                    discord.PartialEmoji.from_str(emoji)
                    if emoji.startswith("<")
                    else emoji
                )
                await message.add_reaction(reaction_emoji)
            except (discord.Forbidden, discord.HTTPException, ValueError):
                pass
    return message


async def get_reaction_panel_mapping(payload: discord.RawReactionActionEvent):
    guild = bot.get_guild(payload.guild_id) if payload.guild_id else None
    if guild is None:
        return None, None, None

    channel = guild.get_channel(payload.channel_id)
    if not isinstance(channel, discord.TextChannel) or channel.name != CH_ROLES:
        return guild, None, None

    try:
        message = await channel.fetch_message(payload.message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return guild, None, None

    if not message.embeds:
        return guild, message, None

    title = message.embeds[0].title
    if title == ROLE_PANEL_COUNTRY_TITLE:
        return guild, message, COUNTRY_REACTION_ROLES
    if title == ROLE_PANEL_RANK_TITLE:
        return guild, message, build_rank_reaction_roles(guild)
    if title == ROLE_PANEL_NOTIFY_TITLE:
        return guild, message, STREAM_NOTIFY_REACTION_ROLES
    return guild, message, None


async def reaction_member(guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
    member = guild.get_member(user_id)
    if member is not None:
        return member
    try:
        return await guild.fetch_member(user_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


def staff_overwrites(
    guild: discord.Guild,
    owner: discord.Role,
    coowner: discord.Role,
    admin: discord.Role,
    mod: discord.Role,
) -> dict:
    hidden = discord.PermissionOverwrite(view_channel=False)
    staff = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        connect=True,
        speak=True,
    )
    return {
        guild.default_role: hidden,
        owner: staff,
        coowner: staff,
        admin: staff,
        mod: staff,
    }


def is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    staff_names = {ROLE_OWNER, ROLE_COOWNER, ROLE_ADMIN, ROLE_MOD}
    return any(role.name in staff_names for role in member.roles)


def safe_text(text: str, limit: int = 1000) -> str:
    if not text:
        return "*(sin texto)*"
    text = discord.utils.escape_mentions(text)
    return text if len(text) <= limit else text[: limit - 3] + "..."


async def send_log(
    guild: discord.Guild,
    title: str,
    description: str,
    colour: discord.Colour = discord.Colour.blurple(),
):
    channel = find_text(guild, CH_LOGS)
    if channel is None:
        return
    embed = discord.Embed(
        title=title,
        description=description,
        colour=colour,
        timestamp=discord.utils.utcnow(),
    )
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.Forbidden:
        pass


async def send_report_staff(
    guild: discord.Guild,
    embed: discord.Embed,
    file: Optional[discord.File] = None,
):
    channel = find_text(guild, CH_REPORTS)
    if channel is None:
        return
    try:
        await channel.send(
            embed=embed,
            file=file,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.Forbidden:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# PANELES PERSISTENTES
# ──────────────────────────────────────────────────────────────────────────────

class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Verificarme",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="streamer_server:verify",
    )
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message(
                "Este botón solo funciona dentro del servidor.",
                ephemeral=True,
            )

        role = find_role(interaction.guild, ROLE_MEMBER)
        if role is None:
            return await interaction.response.send_message(
                "No encuentro el rol de Miembro. Un administrador debe ejecutar /setup.",
                ephemeral=True,
            )

        if role in interaction.user.roles:
            return await interaction.response.send_message(
                "Ya estás verificado/a ✅",
                ephemeral=True,
            )

        try:
            await interaction.user.add_roles(role, reason="Verificación automática")
            await interaction.response.send_message(
                "¡Listo! Ya tenés acceso al servidor ✅",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "No pude darte el rol. Revisá que mi rol esté por encima de ✅・Miembro.",
                ephemeral=True,
            )


class CountrySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=name.split("・", 1)[1], value=name, emoji=name.split("・", 1)[0])
            for name in COUNTRIES
        ]
        super().__init__(
            placeholder="🌎 Elegí tu país",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="streamer_server:country",
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return

        selected_name = self.values[0]
        all_roles = [find_role(interaction.guild, name) for name in COUNTRIES]
        all_roles = [r for r in all_roles if r is not None]
        selected = find_role(interaction.guild, selected_name)

        if selected is None:
            return await interaction.response.send_message(
                "Ese rol no existe. Ejecutá /setup de nuevo.",
                ephemeral=True,
            )

        to_remove = [r for r in all_roles if r in interaction.user.roles and r != selected]
        try:
            if to_remove:
                await interaction.user.remove_roles(*to_remove, reason="Cambio de país visual")
            if selected not in interaction.user.roles:
                await interaction.user.add_roles(selected, reason="País visual")
            await interaction.response.send_message(
                f"Tu país ahora es **{selected.name}**.",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "No pude modificar tus roles. Revisá la jerarquía del bot.",
                ephemeral=True,
            )


class RankSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=name.split("・", 1)[1], value=name, emoji=name.split("・", 1)[0])
            for name in VALORANT_RANKS
        ]
        super().__init__(
            placeholder="🔫 Elegí tu rango de Valorant",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="streamer_server:valorant_rank",
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return

        selected_name = self.values[0]
        all_roles = [find_role(interaction.guild, name) for name in VALORANT_RANKS]
        all_roles = [r for r in all_roles if r is not None]
        selected = find_role(interaction.guild, selected_name)

        if selected is None:
            return await interaction.response.send_message(
                "Ese rol no existe. Ejecutá /setup de nuevo.",
                ephemeral=True,
            )

        to_remove = [r for r in all_roles if r in interaction.user.roles and r != selected]
        try:
            if to_remove:
                await interaction.user.remove_roles(*to_remove, reason="Cambio de rango visual")
            if selected not in interaction.user.roles:
                await interaction.user.add_roles(selected, reason="Rango de Valorant visual")
            await interaction.response.send_message(
                f"Tu rango ahora es **{selected.name}**.",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "No pude modificar tus roles. Revisá la jerarquía del bot.",
                ephemeral=True,
            )


class SelfRolesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CountrySelect())
        self.add_item(RankSelect())


def get_member_valorant_rank(member: discord.Member) -> str:
    """Devuelve el rango visual más alto que tenga el miembro."""
    member_role_names = {role.name for role in member.roles}
    for role_name in reversed(VALORANT_RANKS):
        if role_name in member_role_names:
            return role_name
    return "⚫・Sin rango"


def parse_party_footer(embed: discord.Embed):
    footer = embed.footer.text or ""
    match = re.search(r"party_owner:(\d+)\|max:(\d+)\|closed:(0|1)", footer)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), match.group(3) == "1"


def parse_party_members(embed: discord.Embed) -> list[int]:
    for field in embed.fields:
        if field.name == "👥 Jugadores":
            return [int(value) for value in re.findall(r"<@!?(\d+)>", field.value)]
    return []


def set_party_members(embed: discord.Embed, member_ids: list[int], max_players: int):
    value = "\n".join(f"<@{member_id}>" for member_id in member_ids) or "—"
    value += f"\n\n**{len(member_ids)}/{max_players}**"
    for index, field in enumerate(embed.fields):
        if field.name == "👥 Jugadores":
            embed.set_field_at(index, name="👥 Jugadores", value=value, inline=False)
            return


def closed_party_view() -> discord.ui.View:
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Unirme", emoji="✅", style=discord.ButtonStyle.success, disabled=True))
    view.add_item(discord.ui.Button(label="Salir", emoji="🚪", style=discord.ButtonStyle.secondary, disabled=True))
    view.add_item(discord.ui.Button(label="Cerrar", emoji="🔒", style=discord.ButtonStyle.danger, disabled=True))
    return view


class PartyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Unirme",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="streamer_server:party_join",
    )
    async def join_party(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        if interaction.message is None or not interaction.message.embeds:
            return await interaction.response.send_message(
                "No pude leer esta búsqueda. Creá una nueva con `/party`.", ephemeral=True
            )

        embed = interaction.message.embeds[0].copy()
        state = parse_party_footer(embed)
        if state is None:
            return await interaction.response.send_message(
                "Esta búsqueda ya no es compatible. Creá una nueva con `/party`.", ephemeral=True
            )

        owner_id, max_players, closed = state
        members = parse_party_members(embed)

        if closed:
            return await interaction.response.send_message("Esta búsqueda ya está cerrada.", ephemeral=True)
        if interaction.user.id in members:
            return await interaction.response.send_message("Ya estás dentro de este grupo.", ephemeral=True)
        if len(members) >= max_players:
            return await interaction.response.send_message("El grupo ya está completo.", ephemeral=True)

        members.append(interaction.user.id)
        set_party_members(embed, members, max_players)

        if len(members) >= max_players:
            embed.title = "✅ Grupo completo — Valorant"
            embed.colour = discord.Colour.green()
        else:
            embed.title = "🔎 Buscando grupo — Valorant"
            embed.colour = discord.Colour.blurple()

        await interaction.response.edit_message(embed=embed, view=PartyView())

    @discord.ui.button(
        label="Salir",
        emoji="🚪",
        style=discord.ButtonStyle.secondary,
        custom_id="streamer_server:party_leave",
    )
    async def leave_party(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        if interaction.message is None or not interaction.message.embeds:
            return await interaction.response.send_message(
                "No pude leer esta búsqueda.", ephemeral=True
            )

        embed = interaction.message.embeds[0].copy()
        state = parse_party_footer(embed)
        if state is None:
            return await interaction.response.send_message("Esta búsqueda ya no es compatible.", ephemeral=True)

        owner_id, max_players, closed = state
        members = parse_party_members(embed)

        if closed:
            return await interaction.response.send_message("Esta búsqueda ya está cerrada.", ephemeral=True)
        if interaction.user.id == owner_id:
            return await interaction.response.send_message(
                "Sos quien creó el grupo. Si querés terminar la búsqueda, usá **Cerrar**.", ephemeral=True
            )
        if interaction.user.id not in members:
            return await interaction.response.send_message("No estabas dentro de este grupo.", ephemeral=True)

        members.remove(interaction.user.id)
        set_party_members(embed, members, max_players)
        embed.title = "🔎 Buscando grupo — Valorant"
        embed.colour = discord.Colour.blurple()
        await interaction.response.edit_message(embed=embed, view=PartyView())

    @discord.ui.button(
        label="Cerrar",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="streamer_server:party_close",
    )
    async def close_party(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        if interaction.message is None or not interaction.message.embeds:
            return await interaction.response.send_message("No pude leer esta búsqueda.", ephemeral=True)

        embed = interaction.message.embeds[0].copy()
        state = parse_party_footer(embed)
        if state is None:
            return await interaction.response.send_message("Esta búsqueda ya no es compatible.", ephemeral=True)

        owner_id, max_players, closed = state
        if closed:
            return await interaction.response.send_message("Esta búsqueda ya está cerrada.", ephemeral=True)

        if interaction.user.id != owner_id and not is_staff(interaction.user):
            return await interaction.response.send_message(
                "Solo quien creó la búsqueda o el staff puede cerrarla.", ephemeral=True
            )

        embed.title = "🔒 Búsqueda cerrada — Valorant"
        embed.colour = discord.Colour.dark_grey()
        embed.set_footer(text=f"party_owner:{owner_id}|max:{max_players}|closed:1")
        await interaction.response.edit_message(embed=embed, view=closed_party_view())


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Crear reporte",
        emoji="🎫",
        style=discord.ButtonStyle.primary,
        custom_id="streamer_server:create_ticket",
    )
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message(
                "Este botón solo funciona dentro del servidor.", ephemeral=True
            )

        member_role = find_role(interaction.guild, ROLE_MEMBER)
        if member_role is not None and member_role not in interaction.user.roles and not is_staff(interaction.user):
            return await interaction.response.send_message(
                "Primero tenés que verificarte en #✅・verificación.", ephemeral=True
            )

        category = find_category(interaction.guild, CAT_TICKETS)
        if category is None:
            return await interaction.response.send_message(
                "Todavía no está creada la categoría de tickets. Un administrador debe ejecutar `/setup`.",
                ephemeral=True,
            )

        owner_marker = f"ticket_owner:{interaction.user.id}"
        for channel in category.text_channels:
            if channel.topic and owner_marker in channel.topic:
                return await interaction.response.send_message(
                    f"Ya tenés un reporte abierto: {channel.mention}", ephemeral=True
                )

        guild = interaction.guild
        staff_roles = [
            role for role in (
                find_role(guild, ROLE_OWNER),
                find_role(guild, ROLE_COOWNER),
                find_role(guild, ROLE_ADMIN),
                find_role(guild, ROLE_MOD),
            )
            if role is not None
        ]

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
        }
        for role in staff_roles:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
            )

        try:
            ticket = await guild.create_text_channel(
                name=f"🎫・reporte-{interaction.user.id}",
                category=category,
                overwrites=overwrites,
                topic=f"{owner_marker}|status:open",
                reason=f"Reporte creado por {interaction.user}",
            )
        except discord.Forbidden:
            return await interaction.response.send_message(
                "No pude crear el reporte. Revisá mis permisos de Gestionar canales.",
                ephemeral=True,
            )

        embed = discord.Embed(
            title="🎫 Reporte privado",
            description=(
                f"Hola {interaction.user.mention}. Contanos acá qué pasó y el staff te va a responder.\n\n"
                "Podés adjuntar capturas o clips. Cuando termine, usá **Cerrar reporte**."
            ),
            colour=discord.Colour.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        await ticket.send(
            content=interaction.user.mention,
            embed=embed,
            view=CloseTicketView(),
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )

        staff_embed = discord.Embed(
            title="🎫 Nuevo reporte",
            description=f"**Usuario:** {interaction.user.mention} (`{interaction.user.id}`)\n**Canal:** {ticket.mention}",
            colour=discord.Colour.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        await send_report_staff(guild, staff_embed)
        await send_log(guild, "🎫 Reporte creado", f"{interaction.user.mention} creó {ticket.mention}.")

        await interaction.response.send_message(
            f"✅ Tu reporte fue creado: {ticket.mention}", ephemeral=True
        )


class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Cerrar reporte",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="streamer_server:close_ticket",
    )
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or channel.category is None or channel.category.name != CAT_TICKETS:
            return await interaction.response.send_message(
                "Este botón solo funciona dentro de un reporte.", ephemeral=True
            )

        owner_id = None
        if channel.topic:
            match = re.search(r"ticket_owner:(\d+)", channel.topic)
            if match:
                owner_id = int(match.group(1))

        allowed = is_staff(interaction.user) or interaction.user.id == owner_id
        if not allowed:
            return await interaction.response.send_message(
                "Solo el autor del reporte o el staff puede cerrarlo.", ephemeral=True
            )

        await interaction.response.send_message(
            "🔒 Cerrando el reporte...", ephemeral=True
        )

        transcript_file = None
        if ENABLE_MESSAGE_LOGS:
            lines = []
            try:
                async for message in channel.history(limit=100, oldest_first=True):
                    if message.author.bot and not message.content:
                        continue
                    timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
                    content = message.content or "[sin texto]"
                    if message.attachments:
                        content += " " + " ".join(a.url for a in message.attachments)
                    lines.append(f"[{timestamp}] {message.author}: {content}")
                transcript = "\n".join(lines) or "Sin mensajes para transcribir."
                transcript_file = discord.File(
                    io.BytesIO(transcript.encode("utf-8")),
                    filename=f"transcript-{channel.id}.txt",
                )
            except discord.Forbidden:
                transcript_file = None

        owner_text = f"<@{owner_id}>" if owner_id else "desconocido"
        staff_embed = discord.Embed(
            title="🔒 Reporte cerrado",
            description=(
                f"**Usuario:** {owner_text}\n"
                f"**Cerrado por:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                f"**Canal:** `{channel.name}`"
            ),
            colour=discord.Colour.dark_grey(),
            timestamp=discord.utils.utcnow(),
        )
        await send_report_staff(interaction.guild, staff_embed, transcript_file)
        await send_log(
            interaction.guild,
            "🔒 Reporte cerrado",
            f"{interaction.user.mention} cerró `{channel.name}`.",
            discord.Colour.dark_grey(),
        )

        await asyncio.sleep(2)
        try:
            await channel.delete(reason=f"Reporte cerrado por {interaction.user}")
        except discord.Forbidden:
            pass



# ──────────────────────────────────────────────────────────────────────────────
# HEALTH CHECK PARA KOYEB
# ──────────────────────────────────────────────────────────────────────────────

async def health_root(request: web.Request) -> web.Response:
    discord_bot = request.app["discord_bot"]
    return web.json_response(
        {
            "status": "ok",
            "discord_ready": discord_bot.is_ready(),
            "bot": str(discord_bot.user) if discord_bot.user else None,
            "twitch_enabled": TWITCH_ENABLED,
            "twitch_channel": TWITCH_CHANNEL or None,
            "twitch_watcher_running": twitch_watch.is_running(),
        }
    )


async def start_health_server(discord_bot: commands.Bot):
    app = web.Application()
    app["discord_bot"] = discord_bot
    app.router.add_get("/", health_root)
    app.router.add_get("/health", health_root)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    # Guardamos la referencia para que el runner siga vivo durante toda la ejecución.
    discord_bot.health_runner = runner
    print(f"🌐 Health server activo en 0.0.0.0:{PORT} (/health)")


# ──────────────────────────────────────────────────────────────────────────────
# BOT
# ──────────────────────────────────────────────────────────────────────────────

class SetupBot(commands.Bot):
    async def close(self):
        if twitch_watch.is_running():
            twitch_watch.cancel()
        session = getattr(self, "twitch_session", None)
        if session is not None and not session.closed:
            await session.close()
        await super().close()

    async def setup_hook(self):
        await start_health_server(self)

        self.add_view(VerifyView())
        self.add_view(TicketPanelView())
        self.add_view(CloseTicketView())
        self.add_view(PartyView())

        if GUILD_ID:
            guild_obj = discord.Object(id=GUILD_ID)

            # Copiamos los comandos al servidor para que aparezcan al instante.
            self.tree.copy_global_to(guild=guild_obj)

            # Limpia versiones globales antiguas del bot. Esto evita que Discord
            # muestre dos veces /setup u otros comandos después de migraciones.
            self.tree.clear_commands(guild=None)
            await self.tree.sync()

            await self.tree.sync(guild=guild_obj)
        else:
            await self.tree.sync()


intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.voice_states = True
intents.messages = True
intents.reactions = True
intents.message_content = ENABLE_MESSAGE_LOGS

bot = SetupBot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"✅ Conectado como {bot.user} (ID: {bot.user.id})")
    if GUILD_ID:
        print(f"✅ Comandos sincronizados en el servidor {GUILD_ID}")
    else:
        print("ℹ️ Comandos globales sincronizados. Pueden tardar en aparecer.")

    if TWITCH_ENABLED:
        if not twitch_watch.is_running():
            twitch_watch.start()
        print(f"🟣 Twitch activo: @{TWITCH_CHANNEL} (cada {TWITCH_POLL_SECONDS}s)")
    else:
        print("ℹ️ Twitch automático desactivado. Faltan: " + ", ".join(twitch_missing_config()))


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if bot.user is None or payload.user_id == bot.user.id or payload.guild_id is None:
        return

    guild, message, mapping = await get_reaction_panel_mapping(payload)
    if guild is None or message is None or mapping is None:
        return

    emoji = str(payload.emoji)
    role_name = mapping.get(emoji)
    if role_name is None:
        return

    member = await reaction_member(guild, payload.user_id)
    if member is None or member.bot:
        return

    selected = find_role(guild, role_name)
    if selected is None:
        return

    group_role_names = set(mapping.values())
    old_roles = [role for role in member.roles if role.name in group_role_names and role != selected]

    try:
        if old_roles:
            await member.remove_roles(*old_roles, reason="Cambio de reaction role visual")
        if selected not in member.roles:
            await member.add_roles(selected, reason="Reaction role visual")
    except discord.Forbidden:
        return

    # Deja visualmente una sola reacción por grupo cuando el bot puede gestionarlas.
    for reaction in message.reactions:
        reaction_emoji = str(reaction.emoji)
        if reaction_emoji in mapping and reaction_emoji != emoji:
            try:
                await reaction.remove(member)
            except (discord.Forbidden, discord.HTTPException):
                pass


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if bot.user is None or payload.user_id == bot.user.id or payload.guild_id is None:
        return

    guild, _message, mapping = await get_reaction_panel_mapping(payload)
    if guild is None or mapping is None:
        return

    role_name = mapping.get(str(payload.emoji))
    if role_name is None:
        return

    member = await reaction_member(guild, payload.user_id)
    role = find_role(guild, role_name)
    if member is None or role is None or role not in member.roles:
        return

    try:
        await member.remove_roles(role, reason="Reaction role retirada")
    except discord.Forbidden:
        pass


@bot.tree.command(name="setup", description="Instalación completa inicial. Para cambios usá los comandos por sección.")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def setup_server(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        return

    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(
            "Necesitás permiso de Administrador para usar este comando.",
            ephemeral=True,
        )

    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        # ── Roles con permisos reales ─────────────────────────────────────────
        owner_perms = discord.Permissions(administrator=True)
        coowner_perms = discord.Permissions(administrator=True)

        admin_perms = discord.Permissions.none()
        for perm in (
            "view_audit_log",
            "manage_guild",
            "manage_roles",
            "manage_channels",
            "kick_members",
            "ban_members",
            "moderate_members",
            "manage_nicknames",
            "manage_messages",
            "manage_threads",
            "manage_events",
            "mute_members",
            "deafen_members",
            "move_members",
        ):
            setattr(admin_perms, perm, True)

        mod_perms = discord.Permissions.none()
        for perm in (
            "view_audit_log",
            "kick_members",
            "ban_members",
            "moderate_members",
            "manage_nicknames",
            "manage_messages",
            "manage_threads",
            "mute_members",
            "deafen_members",
            "move_members",
        ):
            setattr(mod_perms, perm, True)

        no_perms = discord.Permissions.none()

        role_owner = await ensure_role(guild, ROLE_OWNER, owner_perms, 0xF1C40F, True)
        role_coowner = await ensure_role(guild, ROLE_COOWNER, coowner_perms, 0xE67E22, True)
        role_admin = await ensure_role(guild, ROLE_ADMIN, admin_perms, 0xE74C3C, True)
        role_mod = await ensure_role(guild, ROLE_MOD, mod_perms, 0x3498DB, True)

        role_member = await ensure_role(guild, ROLE_MEMBER, no_perms, 0x57F287, False)
        role_streamer = await ensure_role(guild, ROLE_STREAMER, no_perms, 0xEB459E, True)
        await ensure_role(guild, ROLE_SUB, no_perms, 0x9B59B6, False)
        await ensure_role(guild, ROLE_VIP, no_perms, 0xFEE75C, False)
        await ensure_role(guild, ROLE_LIVE, no_perms, 0xED4245, True)
        await ensure_role(guild, ROLE_LIVE_NOTIFY, no_perms, 0x9146FF, False)

        # Roles visuales: 0 permisos.
        for name in COUNTRIES:
            await ensure_role(guild, name, no_perms, 0x99AAB5, False)

        rank_colours = {
            "⚫・Sin rango": 0x5865F2,
            "⬛・Hierro": 0x5D5D5D,
            "🟫・Bronce": 0xA97142,
            "⬜・Plata": 0xB7C9D3,
            "🟨・Oro": 0xE5B73B,
            "🟩・Platino": 0x44C7B1,
            "💎・Diamante": 0x8FA8FF,
            "🟪・Ascendente": 0x4DD39C,
            "🟥・Inmortal": 0xC94B68,
            "🌟・Radiante": 0xFFF0A6,
        }
        for name in VALORANT_RANKS:
            await ensure_role(guild, name, no_perms, rank_colours[name], False)

        # ── Overwrites base ───────────────────────────────────────────────────
        everyone = guild.default_role

        public_readonly = {
            everyone: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False,
                add_reactions=False,
                read_message_history=True,
            ),
            role_owner: discord.PermissionOverwrite(send_messages=True),
            role_coowner: discord.PermissionOverwrite(send_messages=True),
            role_admin: discord.PermissionOverwrite(send_messages=True),
            role_mod: discord.PermissionOverwrite(send_messages=True),
        }

        verification_overwrites = {
            everyone: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False,
                add_reactions=False,
                read_message_history=True,
            ),
        }

        member_text = {
            everyone: discord.PermissionOverwrite(view_channel=False),
            role_member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                add_reactions=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
                use_external_emojis=True,
                use_external_stickers=True,
                create_public_threads=True,
                send_messages_in_threads=True,
            ),
        }

        member_readonly = {
            everyone: discord.PermissionOverwrite(view_channel=False),
            role_member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False,
                add_reactions=False,
                read_message_history=True,
            ),
            role_owner: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            role_coowner: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            role_admin: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            role_mod: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }

        roles_readonly = {
            everyone: discord.PermissionOverwrite(view_channel=False),
            role_member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False,
                add_reactions=True,
                read_message_history=True,
            ),
            role_owner: discord.PermissionOverwrite(view_channel=True, send_messages=True, add_reactions=True),
            role_coowner: discord.PermissionOverwrite(view_channel=True, send_messages=True, add_reactions=True),
            role_admin: discord.PermissionOverwrite(view_channel=True, send_messages=True, add_reactions=True),
            role_mod: discord.PermissionOverwrite(view_channel=True, send_messages=True, add_reactions=True),
        }

        member_voice = {
            everyone: discord.PermissionOverwrite(view_channel=False, connect=False),
            role_member: discord.PermissionOverwrite(
                view_channel=True,
                connect=True,
                speak=True,
                stream=True,
                use_voice_activation=True,
            ),
        }

        staff_ow = staff_overwrites(
            guild, role_owner, role_coowner, role_admin, role_mod
        )

        # ── Categorías ────────────────────────────────────────────────────────
        cat_info = await ensure_category(guild, CAT_INFO, verification_overwrites)
        cat_community = await ensure_category(guild, CAT_COMMUNITY, member_text)
        cat_gaming = await ensure_category(guild, CAT_GAMING, member_text)
        cat_voice = await ensure_category(guild, CAT_VOICE, member_voice)
        cat_staff = await ensure_category(guild, CAT_STAFF, staff_ow)
        cat_tickets = await ensure_category(guild, CAT_TICKETS, staff_ow)

        # ── Información ───────────────────────────────────────────────────────
        ch_verify = await ensure_text_channel(
            guild,
            cat_info,
            CH_VERIFY,
            verification_overwrites,
            "Verificate para desbloquear el resto del servidor.",
        )
        ch_rules = await ensure_text_channel(
            guild,
            cat_info,
            CH_RULES,
            public_readonly,
            "Reglas y convivencia de la comunidad.",
        )
        ch_roles = await ensure_text_channel(
            guild,
            cat_info,
            CH_ROLES,
            roles_readonly,
            "Reaccioná con una bandera y un rango para elegir tus roles visuales.",
        )
        ch_announcements = await ensure_text_channel(
            guild,
            cat_info,
            CH_ANNOUNCEMENTS,
            public_readonly,
            "Anuncios oficiales del servidor.",
        )

        stream_ow = dict(public_readonly)
        stream_ow[role_streamer] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
        )
        ch_streams = await ensure_text_channel(
            guild,
            cat_info,
            CH_STREAMS,
            stream_ow,
            "Avisos de directos y contenido de la streamer.",
        )

        streamer_only_ow = {
            everyone: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False,
                add_reactions=True,
                read_message_history=True,
            ),
            role_member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False,
                add_reactions=True,
                read_message_history=True,
            ),
            role_streamer: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                add_reactions=True,
                read_message_history=True,
                embed_links=True,
                attach_files=True,
                use_external_emojis=True,
                use_external_stickers=True,
            ),
            role_admin: discord.PermissionOverwrite(view_channel=True, send_messages=False),
            role_mod: discord.PermissionOverwrite(view_channel=True, send_messages=False),
        }
        ch_streamer_only = await ensure_text_channel(
            guild,
            cat_info,
            CH_STREAMER_ONLY,
            streamer_only_ow,
            "Canal personal: solo la streamer publica; la comunidad puede leer y reaccionar.",
        )

        # ── Comunidad ─────────────────────────────────────────────────────────
        ch_general = await ensure_text_channel(guild, cat_community, CH_GENERAL, member_text, "Chat principal.")
        ch_media = await ensure_text_channel(guild, cat_community, CH_MEDIA, member_text, "Fotos, clips y contenido multimedia.")
        ch_memes = await ensure_text_channel(guild, cat_community, CH_MEMES, member_text, "Memes de la comunidad.")
        ch_commands = await ensure_text_channel(guild, cat_community, CH_COMMANDS, member_text, "Comandos, soporte y utilidades del bot.")

        # ── Gaming ────────────────────────────────────────────────────────────
        ch_gaming = await ensure_text_channel(guild, cat_gaming, CH_GAMING, member_text, "Juegos en general.")
        ch_valorant = await ensure_text_channel(guild, cat_gaming, CH_VALORANT, member_text, "Todo sobre Valorant.")
        ch_lfg = await ensure_text_channel(guild, cat_gaming, CH_LFG, member_text, "Buscá duo, team o gente para jugar.")

        # ── Voz ───────────────────────────────────────────────────────────────
        await ensure_voice_channel(guild, cat_voice, VC_GENERAL, member_voice)
        await ensure_voice_channel(guild, cat_voice, VC_GAMING, member_voice)
        await ensure_voice_channel(guild, cat_voice, VC_VALORANT, member_voice)
        await ensure_voice_channel(guild, cat_voice, VC_CREATE, member_voice)

        # ── Staff ─────────────────────────────────────────────────────────────
        ch_staff = await ensure_text_channel(guild, cat_staff, CH_STAFF, staff_ow, "Chat privado del staff.")
        ch_reports = await ensure_text_channel(guild, cat_staff, CH_REPORTS, staff_ow, "Seguimiento interno de reportes.")
        ch_logs = await ensure_text_channel(guild, cat_staff, CH_LOGS, staff_ow, "Logs y registros de moderación.")

        # ── Paneles automáticos ───────────────────────────────────────────────
        # Evita repetir paneles si /setup se ejecuta varias veces.
        verify_already = False
        async for msg in ch_verify.history(limit=30):
            if msg.author == guild.me and msg.embeds and msg.embeds[0].title == "✅ Verificación":
                verify_already = True
                break

        if not verify_already:
            embed = discord.Embed(
                title="✅ Verificación",
                description=(
                    "Presioná el botón de abajo para verificarte y desbloquear "
                    "los canales de la comunidad."
                ),
                colour=discord.Colour.green(),
            )
            await ch_verify.send(embed=embed, view=VerifyView())

        # Migra/limpia paneles viejos de roles con selectores.
        for old_channel in (ch_commands, ch_roles):
            try:
                async for msg in old_channel.history(limit=80):
                    if (
                        msg.author == guild.me
                        and msg.embeds
                        and msg.embeds[0].title in {"🌎 Roles de perfil", "🎭 Elegí tus roles"}
                    ):
                        await msg.delete()
            except discord.Forbidden:
                pass

        country_lines = "\n".join(
            f"{emoji}  **{role_name.split('・', 1)[1]}**"
            for emoji, role_name in COUNTRY_REACTION_ROLES.items()
        )
        await ensure_reaction_role_panel(
            ch_roles,
            ROLE_PANEL_COUNTRY_TITLE,
            (
                "Reaccioná con la **bandera de tu país** y el bot te dará ese rol.\n"
                "Si elegís otra bandera, reemplaza automáticamente la anterior.\n\n"
                f"{country_lines}"
            ),
            COUNTRY_REACTION_ROLES,
        )

        rank_reaction_roles = build_rank_reaction_roles(guild)
        rank_lines = "\n".join(
            f"{emoji}  **{role_name.split('・', 1)[1]}**"
            for emoji, role_name in rank_reaction_roles.items()
        )
        await ensure_reaction_role_panel(
            ch_roles,
            ROLE_PANEL_RANK_TITLE,
            (
                "Reaccioná con tu **rango actual de Valorant**.\n"
                "Solo podés tener un rango a la vez; si cambiás, el bot reemplaza el anterior.\n\n"
                f"{rank_lines}"
            ),
            rank_reaction_roles,
        )

        await ensure_twitch_notify_panel(guild)

        ticket_panel_already = False
        async for msg in ch_commands.history(limit=30):
            if msg.author == guild.me and msg.embeds and msg.embeds[0].title == "🎫 Soporte y reportes":
                ticket_panel_already = True
                break

        if not ticket_panel_already:
            embed = discord.Embed(
                title="🎫 Soporte y reportes",
                description=(
                    "¿Necesitás hablar con el staff o reportar un problema?\n"
                    "Tocá **Crear reporte** y el bot abrirá un canal privado que solo vos y el staff podrán ver."
                ),
                colour=discord.Colour.blurple(),
            )
            await ch_commands.send(embed=embed, view=TicketPanelView())

        # ── Guías de uso por canal ─────────────────────────────────────────────
        # Los paneles de verificación y roles ya cumplen la función de guía en esos canales.
        await ensure_guide(
            ch_rules,
            "Reglas",
            "Este canal es de **solo lectura**. Acá se publican las normas oficiales de la comunidad. "
            "Leelas antes de participar y consultá al staff si alguna regla no queda clara.",
        )
        await ensure_guide(
            ch_announcements,
            "Anuncios",
            "Acá se publican novedades importantes del servidor, eventos, cambios y avisos del staff. "
            "Los miembros pueden leer, pero solo el staff publica.",
        )
        await ensure_guide(
            ch_streams,
            "Directos",
            "Canal destinado a los avisos de stream y contenido de la streamer. "
            "Más adelante puede conectarse con Twitch para publicar los directos automáticamente.",
        )
        await ensure_guide(
            ch_streamer_only,
            "Aquí solo habla la streamer",
            "Este es el espacio personal de la streamer. La comunidad puede **leer y reaccionar**, "
            "pero solo el rol `🎥・Streamer` puede publicar mensajes, imágenes y enlaces.",
        )
        await ensure_guide(
            ch_general,
            "General",
            "Chat principal de la comunidad. Hablá, conocé gente y compartí con respeto. "
            "Para buscar jugadores usá `🔎・busco-grupo` y para multimedia usá `📸・multimedia`.",
        )
        await ensure_guide(
            ch_media,
            "Multimedia",
            "Compartí clips, capturas, fotos, fanarts y otro contenido multimedia. "
            "Evitá contenido NSFW, spam o material que incumpla las reglas.",
        )
        await ensure_guide(
            ch_memes,
            "Memes",
            "Canal para memes y humor de la comunidad. Mantené el contenido dentro de las reglas y sin ataques personales.",
        )
        await ensure_guide(
            ch_commands,
            "Comandos y soporte",
            "Usá este canal para las utilidades del bot. Si necesitás hablar en privado con el staff, "
            "usá el botón **Crear reporte** que aparece debajo.",
        )
        await ensure_guide(
            ch_gaming,
            "Gaming",
            "Charlá sobre cualquier juego: Minecraft, cooperativos, shooters, juegos de historia y más. "
            "Valorant tiene su canal propio para mantener todo ordenado.",
        )
        await ensure_guide(
            ch_valorant,
            "Valorant",
            "Canal general de Valorant: rankeds, agentes, mapas, clips, estrategias y partidas. "
            "Para armar grupo usá `🔎・busco-grupo`.",
        )
        await ensure_guide(
            ch_lfg,
            "Buscar grupo — Valorant",
            "Usá **`/party`** acá para crear una búsqueda. Elegís modo, cuántas personas faltan y servidor.\n\n"
            "El bot toma tu rango desde `🎭・roles` y publica una tarjeta con **Unirme**, **Salir** y **Cerrar**. "
            "Cuando se completa el grupo, la tarjeta lo muestra automáticamente.",
        )
        await ensure_guide(
            ch_staff,
            "Staff",
            "Chat interno del equipo de moderación. Usalo para coordinar decisiones, consultas y organización del servidor.",
        )
        await ensure_guide(
            ch_reports,
            "Reportes",
            "Registro interno de tickets: el bot avisa acá cuando un miembro abre o cierra un reporte privado.",
        )
        await ensure_guide(
            ch_logs,
            "Logs",
            "Registro automático del servidor: entradas, salidas, cambios de roles/apodos y cambios de canales/roles. "
            "Los logs de contenido de mensajes son opcionales y están apagados por defecto.",
        )

        await interaction.followup.send(
            "✅ **Setup completado.**\n"
            "Creé/actualicé roles, categorías, canales, permisos, verificación, "
            "reaction roles, guías por canal, el canal exclusivo de la streamer, el sistema privado de reportes y la búsqueda de grupo de Valorant.\n\n"
            "No borré ningún canal ni rol que ya existiera.",
            ephemeral=True,
        )

    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Discord me bloqueó una acción por permisos o jerarquía.\n"
            "Poné el rol del bot por encima de los roles que debe administrar "
            "y asegurate de que tenga **Gestionar roles** y **Gestionar canales**.",
            ephemeral=True,
        )
    except Exception as exc:
        await interaction.followup.send(
            f"❌ Ocurrió un error: `{type(exc).__name__}: {exc}`",
            ephemeral=True,
        )
        raise



# ──────────────────────────────────────────────────────────────────────────────
# ACTUALIZACIONES POR SECCIÓN
# ──────────────────────────────────────────────────────────────────────────────

async def require_admin(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return False
    if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.administrator:
        if interaction.response.is_done():
            await interaction.followup.send("Necesitás permiso de Administrador para usar este comando.", ephemeral=True)
        else:
            await interaction.response.send_message("Necesitás permiso de Administrador para usar este comando.", ephemeral=True)
        return False
    return True


async def set_progress(interaction: discord.Interaction, text: str):
    """Actualiza el mensaje efímero para que nunca quede 'pensando' sin información."""
    try:
        await interaction.edit_original_response(content=text)
    except (discord.NotFound, discord.HTTPException):
        pass


def role_panel_line(guild: discord.Guild, emoji: str, role_name: str) -> str:
    role = find_role(guild, role_name)
    label = role.mention if role is not None else f"**{role_name.split('・', 1)[-1]}**"
    return f"{emoji} ─ {label}"



@bot.tree.command(name="actualizar-canales", description="Actualiza solo canales, categorías y permisos del servidor.")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def update_channels_command(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return

    guild = interaction.guild
    await interaction.response.defer(ephemeral=True, thinking=True)
    await set_progress(interaction, "🔄 **Canales:** comprobando roles necesarios...")

    role_owner = find_role(guild, ROLE_OWNER)
    role_coowner = find_role(guild, ROLE_COOWNER)
    role_admin = find_role(guild, ROLE_ADMIN)
    role_mod = find_role(guild, ROLE_MOD)
    role_member = find_role(guild, ROLE_MEMBER)
    role_streamer = find_role(guild, ROLE_STREAMER)

    required = [role_owner, role_coowner, role_admin, role_mod, role_member, role_streamer]
    if any(role is None for role in required):
        return await set_progress(
            interaction,
            "❌ Falta alguno de los roles base. Usá `/setup` únicamente para completar la instalación inicial.",
        )

    everyone = guild.default_role

    verification_overwrites = {
        everyone: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            add_reactions=False,
            read_message_history=True,
        ),
    }

    public_readonly = {
        everyone: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            add_reactions=False,
            read_message_history=True,
        ),
        role_owner: discord.PermissionOverwrite(send_messages=True),
        role_coowner: discord.PermissionOverwrite(send_messages=True),
        role_admin: discord.PermissionOverwrite(send_messages=True),
        role_mod: discord.PermissionOverwrite(send_messages=True),
    }

    member_text = {
        everyone: discord.PermissionOverwrite(view_channel=False),
        role_member: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            add_reactions=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
            use_external_emojis=True,
            use_external_stickers=True,
            create_public_threads=True,
            send_messages_in_threads=True,
        ),
    }

    roles_readonly = {
        everyone: discord.PermissionOverwrite(view_channel=False),
        role_member: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            add_reactions=True,
            read_message_history=True,
        ),
        role_owner: discord.PermissionOverwrite(view_channel=True, send_messages=True, add_reactions=True),
        role_coowner: discord.PermissionOverwrite(view_channel=True, send_messages=True, add_reactions=True),
        role_admin: discord.PermissionOverwrite(view_channel=True, send_messages=True, add_reactions=True),
        role_mod: discord.PermissionOverwrite(view_channel=True, send_messages=True, add_reactions=True),
    }

    member_voice = {
        everyone: discord.PermissionOverwrite(view_channel=False, connect=False),
        role_member: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
            use_voice_activation=True,
        ),
    }

    staff_ow = staff_overwrites(guild, role_owner, role_coowner, role_admin, role_mod)

    await set_progress(interaction, "🔄 **Canales:** actualizando categorías...")
    cat_info = await ensure_category(guild, CAT_INFO, verification_overwrites)
    cat_community = await ensure_category(guild, CAT_COMMUNITY, member_text)
    cat_gaming = await ensure_category(guild, CAT_GAMING, member_text)
    cat_voice = await ensure_category(guild, CAT_VOICE, member_voice)
    cat_staff = await ensure_category(guild, CAT_STAFF, staff_ow)
    await ensure_category(guild, CAT_TICKETS, staff_ow)

    await set_progress(interaction, "🔄 **Canales:** actualizando INFORMACIÓN...")
    await ensure_text_channel(guild, cat_info, CH_VERIFY, verification_overwrites, "Verificate para desbloquear el resto del servidor.")
    await ensure_text_channel(guild, cat_info, CH_RULES, public_readonly, "Reglas y convivencia de la comunidad.")
    await ensure_text_channel(guild, cat_info, CH_ROLES, roles_readonly, "Reaccioná con una bandera y un rango para elegir tus roles visuales.")
    await ensure_text_channel(guild, cat_info, CH_ANNOUNCEMENTS, public_readonly, "Anuncios oficiales del servidor.")

    stream_ow = dict(public_readonly)
    stream_ow[role_streamer] = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
    )
    await ensure_text_channel(guild, cat_info, CH_STREAMS, stream_ow, "Avisos de directos y contenido de la streamer.")

    streamer_only_ow = {
        everyone: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            add_reactions=True,
            read_message_history=True,
        ),
        role_member: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            add_reactions=True,
            read_message_history=True,
        ),
        role_streamer: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            add_reactions=True,
            read_message_history=True,
            embed_links=True,
            attach_files=True,
            use_external_emojis=True,
            use_external_stickers=True,
        ),
        role_admin: discord.PermissionOverwrite(view_channel=True, send_messages=False),
        role_mod: discord.PermissionOverwrite(view_channel=True, send_messages=False),
    }
    await ensure_text_channel(
        guild,
        cat_info,
        CH_STREAMER_ONLY,
        streamer_only_ow,
        "Canal personal: solo la streamer publica; la comunidad puede leer y reaccionar.",
    )

    await set_progress(interaction, "🔄 **Canales:** actualizando COMUNIDAD y GAMING...")
    await ensure_text_channel(guild, cat_community, CH_GENERAL, member_text, "Chat principal.")
    await ensure_text_channel(guild, cat_community, CH_MEDIA, member_text, "Fotos, clips y contenido multimedia.")
    await ensure_text_channel(guild, cat_community, CH_MEMES, member_text, "Memes de la comunidad.")
    await ensure_text_channel(guild, cat_community, CH_COMMANDS, member_text, "Comandos, soporte y utilidades del bot.")
    await ensure_text_channel(guild, cat_gaming, CH_GAMING, member_text, "Juegos en general.")
    await ensure_text_channel(guild, cat_gaming, CH_VALORANT, member_text, "Todo sobre Valorant.")
    await ensure_text_channel(guild, cat_gaming, CH_LFG, member_text, "Buscá duo, team o gente para jugar.")

    await set_progress(interaction, "🔄 **Canales:** actualizando VOZ y STAFF...")
    await ensure_voice_channel(guild, cat_voice, VC_GENERAL, member_voice)
    await ensure_voice_channel(guild, cat_voice, VC_GAMING, member_voice)
    await ensure_voice_channel(guild, cat_voice, VC_VALORANT, member_voice)
    await ensure_voice_channel(guild, cat_voice, VC_CREATE, member_voice)
    await ensure_text_channel(guild, cat_staff, CH_STAFF, staff_ow, "Chat privado del staff.")
    await ensure_text_channel(guild, cat_staff, CH_REPORTS, staff_ow, "Seguimiento interno de reportes.")
    await ensure_text_channel(guild, cat_staff, CH_LOGS, staff_ow, "Logs y registros de moderación.")

    await set_progress(
        interaction,
        "✅ **Canales y permisos actualizados.**\n"
        "💜 También quedó listo `aqui-solo-habla-la-streamer`: la comunidad lee/reacciona y el rol `🎥・Streamer` publica.",
    )


@bot.tree.command(name="actualizar-roles", description="Actualiza países, rangos de Valorant y avisos de directo.")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def update_roles_command(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return

    guild = interaction.guild
    await interaction.response.defer(ephemeral=True, thinking=True)
    await set_progress(interaction, "🔄 **Roles:** buscando el canal y los emojis...")

    ch_roles = find_text(guild, CH_ROLES)
    if ch_roles is None:
        return await set_progress(
            interaction,
            "❌ No encuentro `🎭・roles`. Ejecutá `/setup` solo si todavía no hiciste la instalación inicial.",
        )

    bot_member = guild.me
    if bot_member is None:
        return await set_progress(interaction, "❌ No pude comprobar la jerarquía del bot.")

    await ensure_twitch_roles(guild)

    # Verifica qué roles puede administrar antes de tocar los paneles.
    blocked_roles = []
    for role_name in list(COUNTRIES) + list(VALORANT_RANKS) + [ROLE_LIVE, ROLE_LIVE_NOTIFY]:
        role = find_role(guild, role_name)
        if role is not None and role >= bot_member.top_role:
            blocked_roles.append(role.name)

    if blocked_roles:
        return await set_progress(
            interaction,
            "❌ El rol **Server Setup** debe estar por encima de los roles visuales que asigna.\n"
            f"Roles bloqueados: {', '.join(blocked_roles[:8])}"
            + ("..." if len(blocked_roles) > 8 else ""),
        )

    await set_progress(interaction, "🔄 **Roles:** limpiando paneles antiguos...")

    # Borra paneles viejos con selectores y elimina duplicados de los paneles actuales.
    current_seen = set()
    async for msg in ch_roles.history(limit=100):
        if msg.author != guild.me or not msg.embeds:
            continue

        title = msg.embeds[0].title
        if title in {"🌎 Roles de perfil", "🎭 Elegí tus roles"}:
            try:
                await msg.delete()
            except discord.Forbidden:
                pass
            continue

        if title in {ROLE_PANEL_COUNTRY_TITLE, ROLE_PANEL_RANK_TITLE, ROLE_PANEL_NOTIFY_TITLE}:
            if title in current_seen:
                try:
                    await msg.delete()
                except discord.Forbidden:
                    pass
            else:
                current_seen.add(title)

    await set_progress(interaction, "🔄 **Roles:** actualizando países...")
    country_lines = "\n".join(
        role_panel_line(guild, emoji, role_name)
        for emoji, role_name in COUNTRY_REACTION_ROLES.items()
    )
    await ensure_reaction_role_panel(
        ch_roles,
        ROLE_PANEL_COUNTRY_TITLE,
        (
            "↳ **Seleccioná tu nacionalidad.**\n\n"
            f"{country_lines}\n\n"
            "Solo podés tener **un país** a la vez. Si reaccionás a otro, el bot reemplaza el anterior."
        ),
        COUNTRY_REACTION_ROLES,
    )

    await set_progress(interaction, "🔄 **Roles:** buscando iconos personalizados de Valorant...")
    rank_reaction_roles = build_rank_reaction_roles(guild)
    missing_custom = [
        emoji_name
        for role_name, emoji_name in VALORANT_CUSTOM_EMOJIS.items()
        if emoji_name and discord.utils.get(guild.emojis, name=emoji_name) is None
    ]

    rank_lines = "\n".join(
        role_panel_line(guild, emoji, role_name)
        for emoji, role_name in rank_reaction_roles.items()
    )
    await ensure_reaction_role_panel(
        ch_roles,
        ROLE_PANEL_RANK_TITLE,
        (
            "↳ **Seleccioná tu rango actual.**\n\n"
            f"{rank_lines}\n\n"
            "Solo podés tener **un rango** a la vez. Si reaccionás a otro, el bot reemplaza el anterior."
        ),
        rank_reaction_roles,
    )

    await set_progress(interaction, "🔄 **Roles:** actualizando avisos de directo...")
    await ensure_twitch_notify_panel(guild)

    if missing_custom:
        await set_progress(
            interaction,
            "✅ **Panel de roles actualizado.**\n"
            "⚠️ No encontré estos emojis personalizados, así que usé un emoji normal como reemplazo temporal:\n"
            + ", ".join(f"`:{name}:`" for name in missing_custom),
        )
    else:
        await set_progress(
            interaction,
            "✅ **Panel de roles actualizado.**\n"
            "🇵🇾 Países sincronizados.\n"
            "🎖️ Rangos de Valorant sincronizados con los emojis personalizados.\n"
            "🔔 Avisos de directo listos.",
        )


@bot.tree.command(name="actualizar-guias", description="Actualiza solo las guías de uso de los canales.")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def update_guides_command(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return

    guild = interaction.guild
    await interaction.response.defer(ephemeral=True, thinking=True)
    await set_progress(interaction, "🔄 **Guías:** preparando canales...")

    guides = [
        (CH_RULES, "Reglas", "Este canal es de **solo lectura**. Acá se publican las normas oficiales de la comunidad. Leelas antes de participar y consultá al staff si alguna regla no queda clara."),
        (CH_ANNOUNCEMENTS, "Anuncios", "Acá se publican novedades importantes del servidor, eventos, cambios y avisos del staff. Los miembros pueden leer, pero solo el staff publica."),
        (CH_STREAMS, "Directos", "Historial de avisos automáticos de Twitch. Cuando la streamer entra en vivo, el bot publica título, categoría, miniatura y enlace; quienes tengan `🔔・Avisos de directo` reciben la mención."),
        (CH_STREAMER_ONLY, "Aquí solo habla la streamer", "Este es el espacio personal de la streamer. La comunidad puede **leer y reaccionar**, pero solo el rol `🎥・Streamer` puede publicar mensajes, imágenes y enlaces."),
        (CH_GENERAL, "General", "Chat principal de la comunidad. Hablá, conocé gente y compartí con respeto. Para buscar jugadores usá `🔎・busco-grupo` y para multimedia usá `📸・multimedia`."),
        (CH_MEDIA, "Multimedia", "Compartí clips, capturas, fotos, fanarts y otro contenido multimedia. Evitá contenido NSFW, spam o material que incumpla las reglas."),
        (CH_MEMES, "Memes", "Canal para memes y humor de la comunidad. Mantené el contenido dentro de las reglas y sin ataques personales."),
        (CH_COMMANDS, "Comandos y soporte", "Usá este canal para las utilidades del bot. Si necesitás hablar en privado con el staff, usá el botón **Crear reporte**."),
        (CH_GAMING, "Gaming", "Charlá sobre cualquier juego: Minecraft, cooperativos, shooters, juegos de historia y más. Valorant tiene su canal propio para mantener todo ordenado."),
        (CH_VALORANT, "Valorant", "Canal general de Valorant: rankeds, agentes, mapas, clips, estrategias y partidas. Para armar grupo usá `🔎・busco-grupo`."),
        (CH_LFG, "Buscar grupo — Valorant", "Usá **`/party`** acá para crear una búsqueda. Elegís modo, cuántas personas faltan y servidor.\n\nEl bot toma tu rango desde `🎭・roles` y publica una tarjeta con **Unirme**, **Salir** y **Cerrar**."),
        (CH_STAFF, "Staff", "Chat interno del equipo de moderación. Usalo para coordinar decisiones, consultas y organización del servidor."),
        (CH_REPORTS, "Reportes", "Registro interno de tickets: el bot avisa acá cuando un miembro abre o cierra un reporte privado."),
        (CH_LOGS, "Logs", "Registro automático del servidor: entradas, salidas, cambios de roles/apodos y cambios de canales/roles. Los logs de contenido de mensajes son opcionales."),
    ]

    updated = 0
    missing = []
    total = len(guides)
    for index, (channel_name, title, description) in enumerate(guides, start=1):
        channel = find_text(guild, channel_name)
        if channel is None:
            missing.append(channel_name)
            continue
        await ensure_guide(channel, title, description)
        updated += 1
        if index in {3, 6, 9, total}:
            await set_progress(interaction, f"🔄 **Guías:** {index}/{total} revisadas...")

    result = f"✅ **Guías actualizadas:** {updated}/{total}."
    if missing:
        result += "\n⚠️ No encontré: " + ", ".join(f"`{name}`" for name in missing)
    await set_progress(interaction, result)


@bot.tree.command(name="actualizar-tickets", description="Actualiza solo el sistema de tickets y reportes.")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def update_tickets_command(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return

    guild = interaction.guild
    await interaction.response.defer(ephemeral=True, thinking=True)
    await set_progress(interaction, "🔄 **Tickets:** comprobando roles y canales...")

    role_owner = find_role(guild, ROLE_OWNER)
    role_coowner = find_role(guild, ROLE_COOWNER)
    role_admin = find_role(guild, ROLE_ADMIN)
    role_mod = find_role(guild, ROLE_MOD)
    staff_roles = [role_owner, role_coowner, role_admin, role_mod]
    if any(role is None for role in staff_roles):
        return await set_progress(interaction, "❌ Falta uno de los roles de staff. Ejecutá `/setup` solo para la instalación inicial.")

    ch_commands = find_text(guild, CH_COMMANDS)
    cat_staff = find_category(guild, CAT_STAFF)
    if ch_commands is None or cat_staff is None:
        return await set_progress(interaction, "❌ No encuentro `🤖・comandos` o la categoría de STAFF.")

    staff_ow = staff_overwrites(guild, role_owner, role_coowner, role_admin, role_mod)
    await set_progress(interaction, "🔄 **Tickets:** actualizando categoría privada...")
    await ensure_category(guild, CAT_TICKETS, staff_ow)

    ch_reports = find_text(guild, CH_REPORTS)
    if ch_reports is None:
        ch_reports = await ensure_text_channel(guild, cat_staff, CH_REPORTS, staff_ow, "Seguimiento interno de reportes.")

    await set_progress(interaction, "🔄 **Tickets:** actualizando panel...")
    panel = await find_bot_embed_message(ch_commands, "🎫 Soporte y reportes")
    embed = discord.Embed(
        title="🎫 Soporte y reportes",
        description=(
            "¿Necesitás hablar con el staff o reportar un problema?\n"
            "Tocá **Crear reporte** y el bot abrirá un canal privado que solo vos y el staff podrán ver."
        ),
        colour=discord.Colour.blurple(),
    )
    if panel is None:
        await ch_commands.send(embed=embed, view=TicketPanelView())
    else:
        await panel.edit(embed=embed, view=TicketPanelView())

    await ensure_guide(ch_reports, "Reportes", "Registro interno de tickets: el bot avisa acá cuando un miembro abre o cierra un reporte privado.")
    await set_progress(interaction, "✅ **Tickets actualizados.** El panel y la categoría privada están listos.")




@bot.tree.command(name="actualizar-twitch", description="Configura y comprueba la automatización de Twitch.")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def update_twitch_command(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return

    guild = interaction.guild
    await interaction.response.defer(ephemeral=True, thinking=True)

    missing = twitch_missing_config()
    if missing:
        return await set_progress(
            interaction,
            "❌ **Twitch todavía no tiene credenciales.**\n"
            "Agregá en Northflank: " + ", ".join(f"`{name}`" for name in missing) +
            "\nDespués reiniciá el servicio y ejecutá `/actualizar-twitch` otra vez."
        )

    await set_progress(interaction, "🔄 **Twitch:** creando/comprobando roles y panel de avisos...")
    try:
        live_role, notify_role = await ensure_twitch_roles(guild)
        await ensure_twitch_notify_panel(guild)
    except discord.Forbidden:
        return await set_progress(
            interaction,
            "❌ No puedo administrar los roles de Twitch. Poné **Server Setup** por encima de "
            f"`{ROLE_LIVE}` y `{ROLE_LIVE_NOTIFY}`."
        )

    me = guild.me
    blocked = [role.name for role in (live_role, notify_role) if me is not None and role >= me.top_role]
    if blocked:
        return await set_progress(
            interaction,
            "❌ El rol del bot debe quedar por encima de: " + ", ".join(blocked)
        )

    await set_progress(interaction, f"🔄 **Twitch:** consultando `@{TWITCH_CHANNEL}`...")
    try:
        stream = await fetch_twitch_stream()
    except Exception as exc:
        return await set_progress(
            interaction,
            "❌ Twitch rechazó la conexión. Revisá Client ID/Secret.\n"
            f"Detalle: `{type(exc).__name__}: {str(exc)[:500]}`"
        )

    if not twitch_watch.is_running():
        twitch_watch.start()

    if stream is not None:
        await handle_twitch_online(guild, stream)
        await set_progress(
            interaction,
            "✅ **Twitch funcionando.**\n"
            f"🔴 `@{TWITCH_CHANNEL}` está EN DIRECTO ahora.\n"
            f"✅ `{CH_LIVE}` sincronizado.\n"
            f"✅ `{ROLE_LIVE}` asignado a la streamer.\n"
            f"✅ El bot revisará Twitch cada {TWITCH_POLL_SECONDS} segundos."
        )
    else:
        await handle_twitch_offline(guild)
        await set_progress(
            interaction,
            "✅ **Twitch funcionando.**\n"
            f"⚫ `@{TWITCH_CHANNEL}` está offline ahora.\n"
            f"✅ `{ROLE_LIVE}` se asignará automáticamente al prender.\n"
            f"✅ `{CH_LIVE}` aparecerá automáticamente al prender.\n"
            f"✅ El bot revisará Twitch cada {TWITCH_POLL_SECONDS} segundos."
        )


@bot.tree.command(name="twitch-estado", description="Muestra el estado de la conexión automática con Twitch.")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def twitch_status_command(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return

    missing = twitch_missing_config()
    if missing:
        return await interaction.response.send_message(
            "❌ Twitch no está configurado. Faltan: " + ", ".join(f"`{name}`" for name in missing),
            ephemeral=True,
        )

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        stream = await fetch_twitch_stream()
    except Exception as exc:
        return await set_progress(interaction, f"❌ Error consultando Twitch: `{type(exc).__name__}: {str(exc)[:500]}`")

    live_channel = find_text(interaction.guild, CH_LIVE)
    live_role = find_role(interaction.guild, ROLE_LIVE)
    streamer_members = twitch_streamer_members(interaction.guild)
    role_active = bool(live_role and any(live_role in member.roles for member in streamer_members))

    lines = [
        "✅ **Integración de Twitch conectada**",
        f"Canal: `@{TWITCH_CHANNEL}`",
        f"Estado Twitch: {'🔴 EN DIRECTO' if stream else '⚫ Offline'}",
        f"Canal temporal: {'✅ existe' if live_channel else '— no existe'}",
        f"Rol EN DIRECTO: {'✅ activo' if role_active else '— inactivo'}",
        f"Modo de prueba: {'🧪 ACTIVO' if _twitch_test_mode else '— inactivo'}",
        f"Chequeo: cada {TWITCH_POLL_SECONDS}s",
    ]
    await set_progress(interaction, "\n".join(lines))


@bot.tree.command(name="twitch-preview", description="Muestra una vista previa privada del aviso de Twitch.")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def twitch_preview_command(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return

    stream = twitch_test_stream()
    await interaction.response.send_message(
        content="🧪 **Vista previa privada.** No crea canales, no da roles y no menciona a nadie.",
        embed=twitch_test_embed(stream),
        view=twitch_link_view(),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.tree.command(name="twitch-simular", description="Simula un directo completo sin prender Twitch.")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def twitch_simulate_command(interaction: discord.Interaction):
    global _twitch_test_mode, _twitch_delete_task

    if not await require_admin(interaction):
        return

    guild = interaction.guild
    await interaction.response.defer(ephemeral=True, thinking=True)

    if _twitch_test_mode:
        return await set_progress(
            interaction,
            "🧪 Ya hay una simulación activa. Usá `/twitch-fin-prueba` antes de iniciar otra."
        )

    _twitch_test_mode = True

    # Evita que una eliminación pendiente del canal real/offline se dispare
    # durante la demostración.
    if _twitch_delete_task is not None and not _twitch_delete_task.done():
        _twitch_delete_task.cancel()
        _twitch_delete_task = None

    try:
        await set_progress(interaction, "🧪 **Twitch prueba:** preparando roles...")
        await ensure_twitch_roles(guild)
        await ensure_twitch_notify_panel(guild)

        await set_progress(interaction, "🧪 **Twitch prueba:** creando el canal temporal...")
        stream = twitch_test_stream()
        live_channel = await ensure_live_channel(guild, stream)

        assigned = await add_live_role_to_streamer(guild)

        await bot.change_presence(
            status=discord.Status.online,
            activity=discord.Streaming(
                name=f"{stream.get('user_name') or 'Streamer'} en Twitch [PRUEBA]",
                url=twitch_url(),
            ),
        )

        # En la prueba NO mencionamos el rol de avisos para no molestar a nadie.
        directos = find_text(guild, CH_STREAMS)
        if directos is not None:
            await directos.send(
                content="🧪 **SIMULACIÓN DE DIRECTO — no es un stream real y nadie fue mencionado.**",
                embed=twitch_test_embed(stream),
                view=twitch_link_view(),
                allowed_mentions=discord.AllowedMentions.none(),
            )

        await live_channel.send(
            content="🧪 **Canal temporal creado en modo de prueba.**",
            embed=twitch_test_embed(stream),
            view=twitch_link_view(),
            allowed_mentions=discord.AllowedMentions.none(),
        )

        streamer_note = (
            f"✅ `{ROLE_LIVE}` asignado a {assigned} streamer."
            if assigned
            else f"⚠️ No encontré a nadie con `{ROLE_STREAMER}` para asignarle `{ROLE_LIVE}`."
        )

        await set_progress(
            interaction,
            "✅ **Simulación de Twitch activa.**\n"
            f"✅ `{CH_LIVE}` creado/sincronizado.\n"
            f"{streamer_note}\n"
            f"✅ Se publicó un aviso de prueba en `{CH_STREAMS}` sin mencionar a nadie.\n"
            "✅ El estado del bot muestra Streaming [PRUEBA].\n\n"
            "Cuando termines de mirar, usá `/twitch-fin-prueba`."
        )
    except Exception as exc:
        _twitch_test_mode = False
        await bot.change_presence(status=discord.Status.online, activity=None)
        await set_progress(
            interaction,
            f"❌ La simulación falló: `{type(exc).__name__}: {str(exc)[:500]}`"
        )


@bot.tree.command(name="twitch-fin-prueba", description="Termina la simulación de Twitch y restaura el estado real.")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def twitch_end_test_command(interaction: discord.Interaction):
    global _twitch_test_mode

    if not await require_admin(interaction):
        return

    guild = interaction.guild
    await interaction.response.defer(ephemeral=True, thinking=True)

    if not _twitch_test_mode:
        return await set_progress(
            interaction,
            "ℹ️ No hay ninguna simulación de Twitch activa."
        )

    await set_progress(interaction, "🧪 **Twitch prueba:** limpiando la simulación...")

    # Primero desactivamos la bandera. A partir de aquí el watcher puede volver
    # a trabajar con Twitch real.
    _twitch_test_mode = False

    await remove_live_role_from_streamer(guild)

    live_channel = find_text(guild, CH_LIVE)
    if live_channel is not None:
        try:
            await live_channel.delete(reason="Fin de la simulación de Twitch")
        except discord.Forbidden:
            return await set_progress(
                interaction,
                f"❌ No pude borrar `{CH_LIVE}`. Revisá el permiso Gestionar canales."
            )

    await bot.change_presence(status=discord.Status.online, activity=None)

    result = await sync_real_twitch_after_test(guild)
    await set_progress(
        interaction,
        "✅ **Simulación terminada.**\n"
        f"✅ `{ROLE_LIVE}` de prueba retirado.\n"
        f"✅ Canal temporal de prueba eliminado.\n"
        f"🔄 Estado real: {result}"
    )


@bot.tree.command(name="party", description="Buscá gente para jugar Valorant.")
@app_commands.guild_only()
@app_commands.describe(
    modo="Qué modo van a jugar",
    cupos="Cuántas personas te faltan (1 a 4)",
    servidor="Servidor o región, por ejemplo Santiago, São Paulo o Miami",
)
@app_commands.choices(
    modo=[
        app_commands.Choice(name="Competitivo", value="Competitivo"),
        app_commands.Choice(name="Swiftplay", value="Swiftplay"),
        app_commands.Choice(name="No competitivo", value="No competitivo"),
        app_commands.Choice(name="Premier", value="Premier"),
        app_commands.Choice(name="Deathmatch / TDM", value="Deathmatch / TDM"),
        app_commands.Choice(name="Otro", value="Otro"),
    ]
)
async def party_command(
    interaction: discord.Interaction,
    modo: app_commands.Choice[str],
    cupos: app_commands.Range[int, 1, 4] = 4,
    servidor: Optional[str] = None,
):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return

    lfg_channel = find_text(interaction.guild, CH_LFG)
    if lfg_channel is None:
        return await interaction.response.send_message(
            "No encuentro el canal de búsqueda de grupo. Un administrador debe ejecutar `/setup`.",
            ephemeral=True,
        )

    if interaction.channel_id != lfg_channel.id:
        return await interaction.response.send_message(
            f"Usá `/party` dentro de {lfg_channel.mention} para no llenar otros canales.",
            ephemeral=True,
        )

    member_role = find_role(interaction.guild, ROLE_MEMBER)
    if member_role is not None and member_role not in interaction.user.roles and not is_staff(interaction.user):
        return await interaction.response.send_message(
            "Primero tenés que verificarte.", ephemeral=True
        )

    max_players = 1 + int(cupos)
    rank = get_member_valorant_rank(interaction.user)
    server_text = safe_text(servidor.strip(), 80) if servidor and servidor.strip() else "No especificado"

    embed = discord.Embed(
        title="🔎 Buscando grupo — Valorant",
        description=(
            f"**{interaction.user.display_name}** está buscando gente para jugar.\\n"
            "Tocá **Unirme** para sumarte."
        ),
        colour=discord.Colour.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="🎯 Modo", value=modo.value, inline=True)
    embed.add_field(name="🏅 Rango", value=rank, inline=True)
    embed.add_field(name="🌐 Servidor", value=server_text, inline=True)
    embed.add_field(
        name="👥 Jugadores",
        value=f"{interaction.user.mention}\\n\\n**1/{max_players}**",
        inline=False,
    )
    embed.set_footer(text=f"party_owner:{interaction.user.id}|max:{max_players}|closed:0")

    await interaction.response.send_message(
        embed=embed,
        view=PartyView(),
        allowed_mentions=discord.AllowedMentions.none(),
    )

# ──────────────────────────────────────────────────────────────────────────────
# LOGS AUTOMÁTICOS
# ──────────────────────────────────────────────────────────────────────────────

@bot.event
async def on_member_join(member: discord.Member):
    await send_log(
        member.guild,
        "📥 Miembro entró",
        f"{member.mention} (`{member.id}`) se unió al servidor.",
        discord.Colour.green(),
    )


@bot.event
async def on_member_remove(member: discord.Member):
    await send_log(
        member.guild,
        "📤 Miembro salió",
        f"**{discord.utils.escape_markdown(str(member))}** (`{member.id}`) salió del servidor.",
        discord.Colour.orange(),
    )


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    before_roles = {role.id: role for role in before.roles}
    after_roles = {role.id: role for role in after.roles}
    added = [role for role_id, role in after_roles.items() if role_id not in before_roles]
    removed = [role for role_id, role in before_roles.items() if role_id not in after_roles]

    if added or removed:
        parts = [f"**Usuario:** {after.mention} (`{after.id}`)"]
        if added:
            parts.append("**Roles agregados:** " + ", ".join(discord.utils.escape_mentions(r.name) for r in added))
        if removed:
            parts.append("**Roles quitados:** " + ", ".join(discord.utils.escape_mentions(r.name) for r in removed))
        await send_log(after.guild, "🎭 Cambio de roles", "\n".join(parts))

    if before.nick != after.nick:
        await send_log(
            after.guild,
            "✏️ Cambio de apodo",
            f"{after.mention}: `{before.nick or before.name}` → `{after.nick or after.name}`",
        )


@bot.event
async def on_message_delete(message: discord.Message):
    if not ENABLE_MESSAGE_LOGS or message.guild is None or message.author.bot:
        return
    if isinstance(message.channel, discord.TextChannel) and message.channel.name == CH_LOGS:
        return
    description = (
        f"**Autor:** {message.author.mention} (`{message.author.id}`)\n"
        f"**Canal:** {message.channel.mention}\n"
        f"**Contenido:**\n{safe_text(message.content, 1200)}"
    )
    if message.attachments:
        description += "\n**Adjuntos:** " + ", ".join(a.filename for a in message.attachments[:5])
    await send_log(message.guild, "🗑️ Mensaje eliminado", description, discord.Colour.red())


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if not ENABLE_MESSAGE_LOGS or after.guild is None or after.author.bot:
        return
    if before.content == after.content:
        return
    if isinstance(after.channel, discord.TextChannel) and after.channel.name == CH_LOGS:
        return
    description = (
        f"**Autor:** {after.author.mention} (`{after.author.id}`)\n"
        f"**Canal:** {after.channel.mention}\n"
        f"**Antes:**\n{safe_text(before.content, 700)}\n\n"
        f"**Después:**\n{safe_text(after.content, 700)}\n"
        f"[Ir al mensaje]({after.jump_url})"
    )
    await send_log(after.guild, "✏️ Mensaje editado", description, discord.Colour.orange())


@bot.event
async def on_guild_channel_create(channel: discord.abc.GuildChannel):
    if channel.name in {CH_LOGS, CH_REPORTS} or channel.category and channel.category.name == CAT_TICKETS:
        return
    await send_log(channel.guild, "➕ Canal creado", f"Se creó **{discord.utils.escape_mentions(channel.name)}** (`{channel.id}`).")


@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    if channel.name in {CH_LOGS, CH_REPORTS} or channel.category and channel.category.name == CAT_TICKETS:
        return
    await send_log(channel.guild, "➖ Canal eliminado", f"Se eliminó **{discord.utils.escape_mentions(channel.name)}** (`{channel.id}`).", discord.Colour.red())


@bot.event
async def on_guild_role_create(role: discord.Role):
    await send_log(role.guild, "➕ Rol creado", f"Se creó **{discord.utils.escape_mentions(role.name)}** (`{role.id}`).")


@bot.event
async def on_guild_role_delete(role: discord.Role):
    await send_log(role.guild, "➖ Rol eliminado", f"Se eliminó **{discord.utils.escape_mentions(role.name)}** (`{role.id}`).", discord.Colour.red())


# ──────────────────────────────────────────────────────────────────────────────
# SALAS DE VOZ TEMPORALES
# ──────────────────────────────────────────────────────────────────────────────

@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):
    # Entrar en "Crear sala" => crea una sala temporal y mueve al usuario.
    if after.channel and after.channel.name == VC_CREATE:
        category = after.channel.category
        if category is not None:
            overwrites = dict(category.overwrites)
            overwrites[member] = discord.PermissionOverwrite(
                view_channel=True,
                connect=True,
                speak=True,
                stream=True,
                manage_channels=True,
                move_members=True,
                mute_members=True,
                deafen_members=True,
            )
            try:
                temp = await member.guild.create_voice_channel(
                    name=f"{TEMP_VC_PREFIX}{member.display_name}"[:100],
                    category=category,
                    overwrites=overwrites,
                    user_limit=0,
                    reason="Sala temporal creada automáticamente",
                )
                await member.move_to(temp)
            except discord.Forbidden:
                pass

    # Borra salas temporales vacías.
    if before.channel and before.channel.name.startswith(TEMP_VC_PREFIX):
        await asyncio.sleep(1)
        if len(before.channel.members) == 0:
            try:
                await before.channel.delete(reason="Sala temporal vacía")
            except (discord.NotFound, discord.Forbidden):
                pass


if not TOKEN:
    raise RuntimeError(
        "Falta DISCORD_TOKEN. Copiá .env.example como .env y pegá ahí el token del bot."
    )

bot.run(TOKEN)
