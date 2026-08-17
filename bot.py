import os
import asyncio
import io
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

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
DISCORD_INVITE_URL = os.getenv("DISCORD_INVITE_URL", "").strip()

# Twitch: detección automática mediante la API oficial (polling).
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID", "").strip()
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET", "").strip()
TWITCH_CHANNEL = os.getenv("TWITCH_CHANNEL", "").strip().lstrip("@").lower()
STREAMER_DISCORD_ID = int(os.getenv("STREAMER_DISCORD_ID", "0") or 0)
TWITCH_POLL_SECONDS = max(30, int(os.getenv("TWITCH_POLL_SECONDS", "60") or 60))
TWITCH_OFFLINE_DELETE_DELAY = max(0, int(os.getenv("TWITCH_OFFLINE_DELETE_DELAY", "300") or 300))
TWITCH_CLIPS_POLL_SECONDS = max(60, int(os.getenv("TWITCH_CLIPS_POLL_SECONDS", "60") or 60))
TWITCH_CLIPS_LOOKBACK_MINUTES = max(30, int(os.getenv("TWITCH_CLIPS_LOOKBACK_MINUTES", "180") or 180))
STARBOARD_THRESHOLD = max(2, int(os.getenv("STARBOARD_THRESHOLD", "5") or 5))
EVENT_TIMEZONE = os.getenv("EVENT_TIMEZONE", "America/Asuncion").strip() or "America/Asuncion"
TWITCH_ENABLED = bool(TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET and TWITCH_CHANNEL)

try:
    EVENT_TZ = ZoneInfo(EVENT_TIMEZONE)
except Exception:
    EVENT_TZ = timezone(timedelta(hours=-3))

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
_twitch_broadcaster_id: Optional[str] = None
_twitch_clips_last_check_at: Optional[datetime] = None
_twitch_clips_last_error: Optional[str] = None
_twitch_clips_last_found: int = 0
_twitch_clips_last_published: int = 0
_twitch_clips_last_newest_title: Optional[str] = None
_twitch_clips_last_newest_created: Optional[str] = None

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
    await ensure_streamer_role_order(guild)
    return live_role, notify_role


async def ensure_streamer_role_order(guild: discord.Guild) -> None:
    """
    Coloca EN DIRECTO y Streamer como los dos roles más altos que el bot puede
    administrar. Owner puede permanecer por encima del bot; para que no tape
    visualmente a la streamer debe estar sin hoist y sin color, configurado
    manualmente porque un bot no puede editar un rol que esté por encima suyo.
    """
    me = guild.me
    live_role = find_role(guild, ROLE_LIVE)
    streamer_role = find_role(guild, ROLE_STREAMER)
    if me is None or live_role is None or streamer_role is None:
        return

    try:
        if live_role < me.top_role:
            target = max(1, me.top_role.position - 1)
            if live_role.position != target:
                await live_role.edit(position=target, reason="Jerarquía visual de Twitch")

        # Releer posiciones después de mover EN DIRECTO.
        live_role = find_role(guild, ROLE_LIVE)
        streamer_role = find_role(guild, ROLE_STREAMER)
        if live_role is not None and streamer_role is not None and streamer_role < me.top_role:
            target = max(1, live_role.position - 1)
            if streamer_role.position != target:
                await streamer_role.edit(position=target, reason="Jerarquía visual de la streamer")
    except (discord.Forbidden, discord.HTTPException):
        # No hacemos fallar Twitch si Discord no permite reordenar un rol.
        pass


async def ensure_twitch_notify_panel(guild: discord.Guild) -> Optional[discord.Message]:
    channel = find_text(guild, CH_ROLES)
    if channel is None:
        return None

    await ensure_twitch_roles(guild)
    no_perms = discord.Permissions.none()
    await ensure_role(guild, ROLE_EVENT_NOTIFY, no_perms, 0xF1C40F, False)
    lines = "\n".join(role_panel_line(guild, emoji, role_name) for emoji, role_name in NOTIFY_REACTION_ROLES.items())
    return await ensure_reaction_role_panel(
        channel,
        ROLE_PANEL_NOTIFY_TITLE,
        (
            "↳ **Elegí qué avisos querés recibir.** Podés marcar más de uno.\n\n"
            f"{lines}\n\n"
            "Quitá una reacción cuando quieras dejar de recibir ese aviso."
        ),
        NOTIFY_REACTION_ROLES,
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


def twitch_live_voice_overwrites(guild: discord.Guild) -> dict:
    everyone = guild.default_role
    member = find_role(guild, ROLE_MEMBER)
    streamer = find_role(guild, ROLE_STREAMER)
    owner = find_role(guild, ROLE_OWNER)
    coowner = find_role(guild, ROLE_COOWNER)
    admin = find_role(guild, ROLE_ADMIN)
    mod = find_role(guild, ROLE_MOD)

    ow = {
        everyone: discord.PermissionOverwrite(view_channel=False, connect=False),
    }

    if member is not None:
        ow[member] = discord.PermissionOverwrite(
            view_channel=True, connect=True, speak=True, stream=True,
            use_voice_activation=True,
        )

    # La streamer tiene controles de moderación dentro de la voz del directo.
    if streamer is not None:
        ow[streamer] = discord.PermissionOverwrite(
            view_channel=True, connect=True, speak=True, stream=True,
            use_voice_activation=True, priority_speaker=True,
            mute_members=True, deafen_members=True, move_members=True,
        )

    staff_voice = discord.PermissionOverwrite(
        view_channel=True, connect=True, speak=True, stream=True,
        use_voice_activation=True, mute_members=True, deafen_members=True,
        move_members=True,
    )
    for role in (owner, coowner, admin, mod):
        if role is not None:
            ow[role] = staff_voice
    return ow


async def ensure_live_voice_channel(guild: discord.Guild) -> discord.VoiceChannel:
    channel = find_voice(guild, VC_LIVE)
    category = find_category(guild, CAT_VOICE)
    if category is None:
        raise RuntimeError("No encuentro la categoría de VOZ")

    overwrites = twitch_live_voice_overwrites(guild)
    if channel is None:
        channel = await guild.create_voice_channel(
            VC_LIVE,
            category=category,
            overwrites=overwrites,
            user_limit=0,
            reason="La streamer empezó directo en Twitch",
        )
    elif channel.category_id != category.id:
        await channel.edit(
            category=category,
            overwrites=overwrites,
            reason="Sincronización de la voz del directo",
        )
    return channel


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

        voice_channel = find_voice(guild, VC_LIVE)
        if voice_channel is not None:
            try:
                await voice_channel.delete(reason="El directo de Twitch terminó")
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
    live_voice = await ensure_live_voice_channel(guild)
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
            await live_channel.send(
                "🔊 **Canal de voz del directo:** " + live_voice.mention + "\n"
                "⚠️ Al entrar, tu voz puede escucharse en Twitch. Entrá con respeto: "
                "sin gritos, insultos, spam ni contenido inapropiado. La streamer y el staff pueden moderar la sala.",
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
    live_voice = find_voice(guild, VC_LIVE)
    if (live_channel is not None or live_voice is not None) and (_twitch_delete_task is None or _twitch_delete_task.done()):
        if was_live and live_channel is not None:
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



async def get_twitch_broadcaster_id(force_refresh: bool = False) -> str:
    """Resuelve y cachea el ID numérico del canal configurado en Twitch."""
    global _twitch_broadcaster_id
    if _twitch_broadcaster_id and not force_refresh:
        return _twitch_broadcaster_id

    token = await get_twitch_app_token()
    session = await twitch_session()
    async with session.get(
        "https://api.twitch.tv/helix/users",
        params={"login": TWITCH_CHANNEL},
        headers={
            "Authorization": f"Bearer {token}",
            "Client-Id": TWITCH_CLIENT_ID,
        },
    ) as response:
        data = await response.json(content_type=None)
        if response.status != 200:
            raise RuntimeError(f"Twitch Get Users devolvió HTTP {response.status}: {data}")

    users = data.get("data") or []
    if not users:
        raise RuntimeError(f"No existe el canal de Twitch @{TWITCH_CHANNEL}")
    _twitch_broadcaster_id = str(users[0]["id"])
    return _twitch_broadcaster_id


async def fetch_recent_twitch_clips() -> list[dict]:
    """Obtiene clips recientes del canal con paginación y renovación de token.

    Twitch ordena los clips de broadcaster por vistas, no por fecha. Por eso
    limitamos por una ventana reciente y paginamos hasta 300 resultados antes
    de volver a ordenarlos por ``created_at``.
    """
    broadcaster_id = await get_twitch_broadcaster_id()
    session = await twitch_session()
    now = discord.utils.utcnow()
    started = now - timedelta(minutes=TWITCH_CLIPS_LOOKBACK_MINUTES)
    started_at = started.strftime("%Y-%m-%dT%H:%M:%SZ")

    async def get_page(after: Optional[str] = None, refresh: bool = False) -> dict:
        token = await get_twitch_app_token(force_refresh=refresh)
        params = {
            "broadcaster_id": broadcaster_id,
            "started_at": started_at,
            "first": "100",
        }
        if after:
            params["after"] = after
        async with session.get(
            "https://api.twitch.tv/helix/clips",
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Client-Id": TWITCH_CLIENT_ID,
            },
        ) as response:
            data = await response.json(content_type=None)
            if response.status == 401 and not refresh:
                return await get_page(after=after, refresh=True)
            if response.status != 200:
                raise RuntimeError(f"Twitch Get Clips devolvió HTTP {response.status}: {data}")
            return data

    clips: list[dict] = []
    cursor: Optional[str] = None
    for _ in range(3):
        data = await get_page(after=cursor)
        clips.extend(data.get("data") or [])
        cursor = (data.get("pagination") or {}).get("cursor")
        if not cursor:
            break

    # Dedupe por ID y orden cronológico para publicarlos de viejo -> nuevo.
    unique: dict[str, dict] = {}
    for clip in clips:
        clip_id = str(clip.get("id") or "")
        if clip_id:
            unique[clip_id] = clip
    return sorted(unique.values(), key=lambda clip: clip.get("created_at") or "")

def twitch_clip_embed(clip: dict) -> discord.Embed:
    title = clip.get("title") or "Nuevo clip"
    creator = clip.get("creator_name") or "Desconocido"
    views = int(clip.get("view_count") or 0)
    url = clip.get("url") or twitch_url()
    clip_id = str(clip.get("id") or "")

    embed = discord.Embed(
        title=f"🎬 Nuevo clip de {clip.get('broadcaster_name') or TWITCH_CHANNEL}",
        url=url,
        description=f"**{safe_text(title, 500)}**",
        colour=discord.Colour.purple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="✂️ Creado por", value=safe_text(creator, 100), inline=True)
    embed.add_field(name="👀 Vistas", value=str(views), inline=True)
    created = clip.get("created_at")
    if created:
        embed.add_field(name="🕒 Creado", value=created.replace("T", " ").replace("Z", " UTC"), inline=False)
    thumbnail = clip.get("thumbnail_url")
    if thumbnail:
        embed.set_image(url=thumbnail)
    embed.set_footer(text=f"Twitch clip ID: {clip_id}")
    return embed


def twitch_clip_view(clip: dict) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="Ver clip",
            emoji="🟣",
            style=discord.ButtonStyle.link,
            url=clip.get("url") or twitch_url(),
        )
    )
    return view


async def clip_already_posted(channel: discord.TextChannel, clip_id: str) -> bool:
    wanted = f"Twitch clip ID: {clip_id}"
    try:
        async for msg in channel.history(limit=500):
            if msg.author != channel.guild.me:
                continue
            for embed in msg.embeds:
                if embed.footer and embed.footer.text == wanted:
                    return True
    except discord.Forbidden:
        return False
    return False


async def publish_new_twitch_clips(guild: discord.Guild) -> int:
    global _twitch_clips_last_check_at, _twitch_clips_last_error
    global _twitch_clips_last_found, _twitch_clips_last_published
    global _twitch_clips_last_newest_title, _twitch_clips_last_newest_created

    _twitch_clips_last_check_at = discord.utils.utcnow()
    channel = find_text(guild, CH_CLIPS)
    if channel is None:
        _twitch_clips_last_error = f"No existe el canal {CH_CLIPS}"
        raise RuntimeError(_twitch_clips_last_error)

    try:
        clips = await fetch_recent_twitch_clips()
    except Exception as exc:
        _twitch_clips_last_error = f"{type(exc).__name__}: {exc}"
        raise

    _twitch_clips_last_found = len(clips)
    newest_clip = clips[-1] if clips else None
    _twitch_clips_last_newest_title = (newest_clip.get("title") or "Sin título") if newest_clip else None
    _twitch_clips_last_newest_created = newest_clip.get("created_at") if newest_clip else None
    published = 0
    for clip in clips:
        clip_id = str(clip.get("id") or "")
        if not clip_id or await clip_already_posted(channel, clip_id):
            continue
        try:
            await channel.send(
                embed=twitch_clip_embed(clip),
                view=twitch_clip_view(clip),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            published += 1
        except discord.Forbidden as exc:
            _twitch_clips_last_error = "No puedo enviar mensajes en el canal de clips"
            raise RuntimeError(_twitch_clips_last_error) from exc

    _twitch_clips_last_error = None
    _twitch_clips_last_published = published
    if clips or published:
        newest = clips[-1] if clips else None
        newest_text = (
            f" • último: {newest.get('title', 'sin título')[:80]}"
            if newest else ""
        )
        print(f"🎬 Clips: encontrados {len(clips)}, publicados {published}{newest_text}")
    return published


@tasks.loop(seconds=TWITCH_CLIPS_POLL_SECONDS)
async def twitch_clips_watch():
    if not TWITCH_ENABLED or not GUILD_ID:
        return
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return
    try:
        await publish_new_twitch_clips(guild)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"⚠️ Twitch clips watcher: {type(exc).__name__}: {exc}")


@twitch_clips_watch.before_loop
async def before_twitch_clips_watch():
    await bot.wait_until_ready()


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
ROLE_EVENT_NOTIFY = "🎉・Avisos de eventos"
ROLE_GAME_VALORANT = "🔫・Valorant"
ROLE_GAME_MINECRAFT = "⛏️・Minecraft"
ROLE_GAME_OTHER = "🎮・Otros juegos"
ROLE_PLATFORM_PC = "🖥️・PC"
ROLE_PLATFORM_CONSOLE = "🎮・Consola"
ROLE_PLATFORM_MOBILE = "📱・Mobile"

# Roles visuales de edad. No guardan la edad exacta, solo un rango general.
AGE_ROLES = [
    "🧒・Menor de 18",
    "🎂・18-25",
    "🧑・26+",
]

AGE_REACTION_ROLES = {
    "🧒": "🧒・Menor de 18",
    "🎂": "🎂・18-25",
    "🧑": "🧑・26+",
}

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
ROLE_PANEL_AGE_TITLE = "🎂 Elegí tu rango de edad"
ROLE_PANEL_RANK_TITLE = "🔫 Elegí tu rango de Valorant"
ROLE_PANEL_GAMES_TITLE = "🎮 Elegí tus juegos"
ROLE_PANEL_PLATFORM_TITLE = "🖥️ Elegí tus plataformas"
ROLE_PANEL_NOTIFY_TITLE = "📣 Elegí tus avisos"
LEGACY_ROLE_PANEL_NOTIFY_TITLE = "🔔 Avisos de directo"
GAME_REACTION_ROLES = {"🔫": ROLE_GAME_VALORANT, "⛏️": ROLE_GAME_MINECRAFT, "🎮": ROLE_GAME_OTHER}
PLATFORM_REACTION_ROLES = {"🖥️": ROLE_PLATFORM_PC, "🎮": ROLE_PLATFORM_CONSOLE, "📱": ROLE_PLATFORM_MOBILE}
NOTIFY_REACTION_ROLES = {"🔔": ROLE_LIVE_NOTIFY, "🎉": ROLE_EVENT_NOTIFY}
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
CH_WELCOME = "👋・bienvenidas"
CH_MEDIA = "📸・multimedia"
CH_MEMES = "😂・memes"
CH_COMMANDS = "🤖・comandos"
CH_CLIPS = "🎬・clips"
CH_PETS = "🐾・mascotas"
CH_SUGGESTIONS = "💡・sugerencias"
CH_STARBOARD = "⭐・destacados"
CH_EVENTS = "🎉・eventos"
CH_INVITE = "🔗・invitar-amigos"

CH_GAMING = "🎮・gaming"
CH_VALORANT = "🔫・valorant"
CH_LFG = "🔎・busco-grupo"

VC_GENERAL = "🔊・General"
VC_GAMING = "🎮・Gaming"
VC_VALORANT = "🔫・Valorant"
VC_CREATE = "➕・Crear sala"
VC_LIVE = "🔴・EN DIRECTO | RESPETO"
VC_INVITE_INDICATOR = "🔗 Invitar Amigos"
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

        wanted_colour = discord.Colour(colour)
        if (
            role.permissions != permissions
            or role.colour != wanted_colour
            or role.hoist != hoist
            or role.mentionable
        ):
            await role.edit(
                permissions=permissions,
                colour=wanted_colour,
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
        return await guild.create_category(
            name,
            overwrites=overwrites,
            reason="Setup automático del servidor",
        )

    if category.overwrites != overwrites:
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
    wanted_overwrites = overwrites if overwrites is not None else category.overwrites
    if channel is None:
        return await guild.create_text_channel(
            name,
            category=category,
            overwrites=wanted_overwrites,
            topic=topic,
            reason="Setup automático del servidor",
        )

    edits = {}
    if channel.category_id != category.id:
        edits["category"] = category
    if channel.overwrites != wanted_overwrites:
        edits["overwrites"] = wanted_overwrites
    if channel.topic != topic:
        edits["topic"] = topic
    if edits:
        await channel.edit(**edits, reason="Actualización del setup")
    return channel


async def ensure_voice_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    name: str,
    overwrites: Optional[dict] = None,
) -> discord.VoiceChannel:
    channel = find_voice(guild, name)
    wanted_overwrites = overwrites if overwrites is not None else category.overwrites
    if channel is None:
        return await guild.create_voice_channel(
            name,
            category=category,
            overwrites=wanted_overwrites,
            reason="Setup automático del servidor",
        )

    edits = {}
    if channel.category_id != category.id:
        edits["category"] = category
    if channel.overwrites != wanted_overwrites:
        edits["overwrites"] = wanted_overwrites
    if edits:
        await channel.edit(**edits, reason="Actualización del setup")
    return channel



def current_member_count(guild: discord.Guild) -> int:
    if guild.member_count is not None:
        return int(guild.member_count)
    return len(guild.members)


def member_counter_name(guild: discord.Guild) -> str:
    return f"💜 {current_member_count(guild)} Miembros 💜"


def find_member_counter_voice(guild: discord.Guild) -> Optional[discord.VoiceChannel]:
    for channel in guild.voice_channels:
        lower = channel.name.lower()
        if "miembro" in lower and any(char.isdigit() for char in channel.name):
            return channel
    return None


def indicator_overwrites(guild: discord.Guild) -> dict:
    return {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True,
            connect=False,
            speak=False,
            stream=False,
        )
    }


async def ensure_top_indicators(guild: discord.Guild) -> tuple[discord.VoiceChannel, discord.VoiceChannel]:
    """Crea/migra los indicadores. Si ya están bien, no los vuelve a mover."""
    overwrites = indicator_overwrites(guild)
    wanted_count = member_counter_name(guild)

    counter = find_member_counter_voice(guild)
    if counter is None:
        counter = await guild.create_voice_channel(
            wanted_count,
            category=None,
            position=0,
            overwrites=overwrites,
            reason="Contador visual de miembros",
        )
    else:
        edits = {}
        if counter.name != wanted_count:
            edits["name"] = wanted_count
        if counter.category is not None:
            edits["category"] = None
        if counter.overwrites != overwrites:
            edits["overwrites"] = overwrites
        if edits:
            await counter.edit(**edits, reason="Actualizar contador visual de miembros")

    invite_indicator = find_voice(guild, VC_INVITE_INDICATOR)
    if invite_indicator is None:
        invite_indicator = next(
            (vc for vc in guild.voice_channels if "invitar amigos" in vc.name.lower()),
            None,
        )
    if invite_indicator is None:
        invite_indicator = await guild.create_voice_channel(
            VC_INVITE_INDICATOR,
            category=None,
            position=1,
            overwrites=overwrites,
            reason="Indicador visual de invitación",
        )
    else:
        edits = {}
        if invite_indicator.name != VC_INVITE_INDICATOR:
            edits["name"] = VC_INVITE_INDICATOR
        if invite_indicator.category is not None:
            edits["category"] = None
        if invite_indicator.overwrites != overwrites:
            edits["overwrites"] = overwrites
        if edits:
            await invite_indicator.edit(**edits, reason="Actualizar indicador de invitación")

    return counter, invite_indicator


async def update_member_counter(guild: discord.Guild, count_override: Optional[int] = None) -> None:
    channel = find_member_counter_voice(guild)
    if channel is None:
        return
    count = current_member_count(guild) if count_override is None else max(0, int(count_override))
    wanted = f"💜 {count} Miembros 💜"
    if channel.name == wanted:
        return
    try:
        previous = channel.name
        await channel.edit(name=wanted, reason="Actualizar cantidad de miembros")
        print(f"👥 Contador actualizado: {previous} -> {wanted}")
    except discord.Forbidden:
        print("⚠️ No pude actualizar el contador: falta Administrar canales.")
    except discord.HTTPException as exc:
        print(f"⚠️ Discord demoró/rechazó el contador: {exc}")


_member_counter_pending_delta: dict[int, int] = {}
_member_counter_debounce_tasks: dict[int, asyncio.Task] = {}


async def schedule_member_counter_update(guild: discord.Guild, delta: int) -> None:
    """Agrupa entradas/salidas cercanas y hace un solo rename del canal."""
    guild_id = guild.id
    _member_counter_pending_delta[guild_id] = _member_counter_pending_delta.get(guild_id, 0) + delta
    running = _member_counter_debounce_tasks.get(guild_id)
    if running is not None and not running.done():
        return

    async def worker():
        try:
            await asyncio.sleep(8)
            pending = _member_counter_pending_delta.pop(guild_id, 0)
            visible_channel = find_member_counter_voice(guild)
            if visible_channel is None:
                return
            match = re.search(r"(\d+)", visible_channel.name.replace(".", "").replace(",", ""))
            visible = int(match.group(1)) if match else current_member_count(guild)
            target = max(0, visible + pending)
            await update_member_counter(guild, target)
        finally:
            _member_counter_debounce_tasks.pop(guild_id, None)

    _member_counter_debounce_tasks[guild_id] = asyncio.create_task(worker())


@tasks.loop(minutes=10)
async def member_counter_watch():
    """Respaldo poco frecuente para corregir diferencias sin castigar la API."""
    for guild in bot.guilds:
        try:
            await update_member_counter(guild)
        except Exception as exc:
            print(f"⚠️ Error resincronizando contador en {guild.name}: {exc}")


@member_counter_watch.before_loop
async def before_member_counter_watch():
    await bot.wait_until_ready()


async def get_or_create_invite_url(guild: discord.Guild, target: discord.TextChannel) -> Optional[str]:
    if DISCORD_INVITE_URL:
        return DISCORD_INVITE_URL

    try:
        invites = await guild.invites()
        for invite in invites:
            if invite.max_age == 0 and invite.max_uses == 0 and invite.channel and invite.channel.id == target.id:
                return invite.url
    except (discord.Forbidden, discord.HTTPException):
        pass

    try:
        invite = await target.create_invite(
            max_age=0,
            max_uses=0,
            unique=False,
            reason="Invitación permanente de la comunidad",
        )
        return invite.url
    except (discord.Forbidden, discord.HTTPException):
        return None


async def _bot_embed_messages(
    channel: discord.TextChannel,
    title: str,
    limit: int = 500,
) -> list[discord.Message]:
    """Busca mensajes embed del propio bot por título, del más nuevo al más viejo."""
    me_id = bot.user.id if bot.user is not None else (channel.guild.me.id if channel.guild.me else None)
    if me_id is None:
        return []

    found: list[discord.Message] = []
    try:
        async for msg in channel.history(limit=limit):
            if (
                msg.author.id == me_id
                and msg.embeds
                and msg.embeds[0].title == title
            ):
                found.append(msg)
    except (discord.Forbidden, discord.HTTPException):
        pass
    return found


async def find_bot_embed_message(
    channel: discord.TextChannel,
    title: str,
    limit: int = 500,
    delete_duplicates: bool = True,
) -> Optional[discord.Message]:
    """Devuelve un único mensaje del sistema y, por defecto, limpia duplicados."""
    matches = await _bot_embed_messages(channel, title, limit=limit)
    if not matches:
        return None

    # history() devuelve primero el más nuevo. Conservamos ese y borramos copias viejas.
    keep = matches[0]
    if delete_duplicates and len(matches) > 1:
        for duplicate in matches[1:]:
            try:
                await duplicate.delete()
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass
    return keep


async def ensure_invite_message(guild: discord.Guild) -> None:
    """Crea o edita UNA sola tarjeta de invitación. Nunca la duplica al reconectar."""
    channel = find_text(guild, CH_INVITE)
    verify_channel = find_text(guild, CH_VERIFY)
    if channel is None or verify_channel is None:
        return

    url = await get_or_create_invite_url(guild, verify_channel)
    description = (
        "¿Conocés a alguien que disfrutaría de la comunidad? 💜\n\n"
        + (
            f"### 🔗 {url}\n\nCompartí este enlace para invitarlo al servidor."
            if url
            else "⚠️ No pude crear la invitación. Dale al bot el permiso **Crear invitación** "
                 "o configurá `DISCORD_INVITE_URL` en Northflank."
        )
    )
    title = "💜 Invitá a tus amigos"
    embed = discord.Embed(title=title, description=description, colour=discord.Colour.purple())

    # Escanea bastante historial y elimina cualquier copia anterior antes de decidir crear.
    message = await find_bot_embed_message(channel, title, limit=500, delete_duplicates=True)
    if message is None:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    else:
        try:
            await message.edit(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass


async def ensure_guide(
    channel: discord.TextChannel,
    title: str,
    description: str,
    colour: discord.Colour = discord.Colour.blurple(),
) -> discord.Message:
    """Crea o actualiza una sola guía; limpia copias antiguas si las hubiera."""
    full_title = f"{GUIDE_PREFIX}{title}"
    embed = discord.Embed(title=full_title, description=description, colour=colour)
    message = await find_bot_embed_message(channel, full_title, limit=500, delete_duplicates=True)

    if message is not None:
        try:
            await message.edit(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass
        return message

    return await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def ensure_reaction_role_panel(
    channel: discord.TextChannel,
    title: str,
    description: str,
    mapping: dict[str, str],
) -> discord.Message:
    """Crea/actualiza UN solo panel y sincroniza sus reacciones sin duplicar mensajes."""
    message = await find_bot_embed_message(channel, title, limit=500, delete_duplicates=True)
    embed = discord.Embed(title=title, description=description, colour=discord.Colour.blurple())
    embed.set_footer(text="Reaccioná para asignarte el rol • Quitá tu reacción para quitarlo")

    if message is None:
        message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    else:
        try:
            await message.edit(embed=embed, view=None, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

    # Discord agrupa una misma reacción, por lo que solo agregamos emojis realmente ausentes.
    wanted = set(mapping.keys())
    for reaction in list(message.reactions):
        if str(reaction.emoji) not in wanted:
            try:
                await message.clear_reaction(reaction.emoji)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass

    # Volvemos a consultar el mensaje tras editar/limpiar para tener el estado real de reacciones.
    try:
        message = await channel.fetch_message(message.id)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        pass

    existing = {str(reaction.emoji) for reaction in message.reactions}
    for emoji in mapping:
        if emoji in existing:
            continue
        try:
            reaction_emoji = discord.PartialEmoji.from_str(emoji) if emoji.startswith("<") else emoji
            await message.add_reaction(reaction_emoji)
            existing.add(emoji)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException, ValueError):
            pass

    return message


async def cleanup_duplicate_system_messages(guild: discord.Guild) -> None:
    """Limpieza silenciosa al arrancar: no crea nada, solo elimina copias repetidas."""
    targets: list[tuple[Optional[discord.TextChannel], list[str]]] = [
        (
            find_text(guild, CH_ROLES),
            [
                ROLE_PANEL_COUNTRY_TITLE,
                ROLE_PANEL_AGE_TITLE,
                ROLE_PANEL_RANK_TITLE,
                ROLE_PANEL_GAMES_TITLE,
                ROLE_PANEL_PLATFORM_TITLE,
                ROLE_PANEL_NOTIFY_TITLE,
                LEGACY_ROLE_PANEL_NOTIFY_TITLE,
            ],
        ),
        (find_text(guild, CH_INVITE), ["💜 Invitá a tus amigos"]),
        (find_text(guild, CH_VERIFY), ["✅ Verificación"]),
        (find_text(guild, CH_COMMANDS), ["🎫 Soporte y reportes"]),
        (find_text(guild, CH_SUGGESTIONS), [SUGGESTION_PANEL_TITLE]),
    ]

    for channel, titles in targets:
        if channel is None:
            continue
        for title in titles:
            await find_bot_embed_message(channel, title, limit=500, delete_duplicates=True)


async def cleanup_legacy_giveaway_role(guild: discord.Guild) -> None:
    """Elimina el antiguo rol de avisos que ya no se usa."""
    role = find_role(guild, "🎁・Avisos de sorteos")
    if role is None:
        return
    me = guild.me
    if me is None or role >= me.top_role:
        print("⚠️ No pude eliminar un rol legado de avisos por jerarquía.")
        return
    try:
        await role.delete(reason="Rol de avisos antiguo retirado del servidor")
        print("🧹 Rol legado de avisos eliminado.")
    except (discord.Forbidden, discord.HTTPException):
        pass


async def get_reaction_panel_mapping(payload: discord.RawReactionActionEvent):
    guild = bot.get_guild(payload.guild_id) if payload.guild_id else None
    if guild is None:
        return None, None, None, False

    channel = guild.get_channel(payload.channel_id)
    if not isinstance(channel, discord.TextChannel) or channel.name != CH_ROLES:
        return guild, None, None, False

    try:
        message = await channel.fetch_message(payload.message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return guild, None, None, False

    if not message.embeds:
        return guild, message, None, False

    title = message.embeds[0].title
    if title == ROLE_PANEL_COUNTRY_TITLE:
        return guild, message, COUNTRY_REACTION_ROLES, True
    if title == ROLE_PANEL_AGE_TITLE:
        return guild, message, AGE_REACTION_ROLES, True
    if title == ROLE_PANEL_RANK_TITLE:
        return guild, message, build_rank_reaction_roles(guild), True
    if title == ROLE_PANEL_GAMES_TITLE:
        return guild, message, GAME_REACTION_ROLES, False
    if title == ROLE_PANEL_PLATFORM_TITLE:
        return guild, message, PLATFORM_REACTION_ROLES, False
    if title in {ROLE_PANEL_NOTIFY_TITLE, LEGACY_ROLE_PANEL_NOTIFY_TITLE}:
        return guild, message, NOTIFY_REACTION_ROLES, False
    return guild, message, None, False


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



async def send_welcome(member: discord.Member) -> None:
    channel = find_text(member.guild, CH_WELCOME)
    if channel is None:
        return
    count = member.guild.member_count if member.guild.member_count is not None else len(member.guild.members)
    embed = discord.Embed(
        title="💜 ¡Bienvenido/a a la comunidad!",
        description=(
            f"¡Hola {member.mention}! Ya sos parte del servidor de **{TWITCH_CHANNEL or 's0ftbl4de'}**.\n\n"
            "🎭 Elegí tus roles en **#roles**\n"
            "💬 Pasate por **#general**\n"
            "📜 Recordá respetar las reglas\n\n"
            f"✨ Ahora somos **{count} miembros**."
        ),
        colour=discord.Colour.purple(),
        timestamp=discord.utils.utcnow(),
    )
    try:
        await channel.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=[member], roles=False, everyone=False),
        )
    except (discord.Forbidden, discord.HTTPException):
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
            await send_welcome(interaction.user)
            await update_member_counter(interaction.guild)
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
# SUGERENCIAS, DESTACADOS Y EVENTOS
# ──────────────────────────────────────────────────────────────────────────────

SUGGESTION_PANEL_TITLE = "💡 Buzón de sugerencias"
SUGGESTION_FOOTER_PREFIX = "suggestion|"
STARBOARD_FOOTER_PREFIX = "starboard_source:"
EVENT_FOOTER_PREFIX = "event|"


def set_embed_field(embed: discord.Embed, name: str, value: str, inline: bool = False) -> None:
    for index, field in enumerate(embed.fields):
        if field.name == name:
            embed.set_field_at(index, name=name, value=value, inline=inline)
            return
    embed.add_field(name=name, value=value, inline=inline)


def suggestion_is_message(message: discord.Message) -> bool:
    if not message.embeds or not message.embeds[0].footer:
        return False
    return bool((message.embeds[0].footer.text or "").startswith(SUGGESTION_FOOTER_PREFIX))


async def count_non_bot_reaction_users(message: discord.Message, emoji: str) -> int:
    for reaction in message.reactions:
        if str(reaction.emoji) != emoji:
            continue
        total = 0
        try:
            async for user in reaction.users(limit=None):
                if not user.bot:
                    total += 1
        except (discord.Forbidden, discord.HTTPException):
            return max(0, reaction.count - 1)
        return total
    return 0


async def update_suggestion_votes(message: discord.Message) -> None:
    if not suggestion_is_message(message) or not message.embeds:
        return
    up = await count_non_bot_reaction_users(message, "👍")
    down = await count_non_bot_reaction_users(message, "👎")
    embed = discord.Embed.from_dict(message.embeds[0].to_dict())
    set_embed_field(embed, "🗳️ Votación", f"👍 **{up}** a favor   •   👎 **{down}** en contra")
    try:
        await message.edit(embed=embed, view=SuggestionStaffView())
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        pass


class SuggestionModal(discord.ui.Modal, title="Enviar sugerencia"):
    suggestion_title = discord.ui.TextInput(
        label="Título",
        placeholder="Ej: Noche de Minecraft",
        max_length=100,
    )
    suggestion_description = discord.ui.TextInput(
        label="Descripción",
        placeholder="Contanos tu idea de forma clara...",
        style=discord.TextStyle.paragraph,
        max_length=1500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Solo funciona dentro del servidor.", ephemeral=True)
        channel = find_text(interaction.guild, CH_SUGGESTIONS)
        if channel is None:
            return await interaction.response.send_message("No encuentro el canal de sugerencias.", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)
        embed = discord.Embed(
            title=f"💡 {safe_text(self.suggestion_title.value, 100)}",
            description=safe_text(self.suggestion_description.value, 1500),
            colour=discord.Colour.gold(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="👤 Propuesta por", value=interaction.user.mention, inline=True)
        embed.add_field(name="📌 Estado", value="🟡 Pendiente", inline=True)
        embed.add_field(name="🗳️ Votación", value="👍 **0** a favor   •   👎 **0** en contra", inline=False)
        embed.set_footer(text=f"{SUGGESTION_FOOTER_PREFIX}author={interaction.user.id}|status=pending")
        message = await channel.send(
            embed=embed,
            view=SuggestionStaffView(),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        for emoji in ("👍", "👎"):
            try:
                await message.add_reaction(emoji)
            except (discord.Forbidden, discord.HTTPException):
                pass
        await interaction.followup.send(
            f"✅ Tu sugerencia fue publicada: {message.jump_url}",
            ephemeral=True,
        )


class SuggestionPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Enviar sugerencia",
        emoji="💡",
        style=discord.ButtonStyle.primary,
        custom_id="streamer_server:suggestion_open",
    )
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SuggestionModal())


class SuggestionStaffView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def change_status(self, interaction: discord.Interaction, status: str, label: str, colour: discord.Colour):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            return await interaction.response.send_message("Solo el staff puede cambiar el estado.", ephemeral=True)
        if interaction.message is None or not interaction.message.embeds:
            return await interaction.response.send_message("No pude leer esta sugerencia.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        embed = discord.Embed.from_dict(interaction.message.embeds[0].to_dict())
        set_embed_field(embed, "📌 Estado", label, inline=True)
        footer = embed.footer.text or ""
        if footer.startswith(SUGGESTION_FOOTER_PREFIX):
            footer = re.sub(r"status=[^|]+", f"status={status}", footer)
        embed.set_footer(text=footer)
        embed.colour = colour
        await interaction.message.edit(embed=embed, view=self)
        await interaction.followup.send(f"✅ Estado cambiado a **{label}**.", ephemeral=True)

    @discord.ui.button(label="En revisión", emoji="🟡", style=discord.ButtonStyle.secondary, custom_id="streamer_server:suggestion_review")
    async def review(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.change_status(interaction, "review", "🟡 En revisión", discord.Colour.orange())

    @discord.ui.button(label="Aceptar", emoji="✅", style=discord.ButtonStyle.success, custom_id="streamer_server:suggestion_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.change_status(interaction, "accepted", "✅ Aceptada", discord.Colour.green())

    @discord.ui.button(label="Rechazar", emoji="❌", style=discord.ButtonStyle.danger, custom_id="streamer_server:suggestion_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.change_status(interaction, "rejected", "❌ Rechazada", discord.Colour.red())


async def ensure_suggestion_panel(guild: discord.Guild) -> Optional[discord.Message]:
    channel = find_text(guild, CH_SUGGESTIONS)
    if channel is None:
        return None
    embed = discord.Embed(
        title=SUGGESTION_PANEL_TITLE,
        description=(
            "¿Tenés una idea para mejorar el Discord, los streams o los eventos?\n\n"
            "Tocá **Enviar sugerencia**. La comunidad podrá votar con 👍 o 👎 y el staff marcará su estado."
        ),
        colour=discord.Colour.blurple(),
    )
    message = await find_bot_embed_message(channel, SUGGESTION_PANEL_TITLE, limit=500, delete_duplicates=True)
    if message is None:
        return await channel.send(embed=embed, view=SuggestionPanelView())
    try:
        await message.edit(embed=embed, view=SuggestionPanelView())
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        pass
    return message


async def handle_suggestion_reaction(payload: discord.RawReactionActionEvent, added: bool) -> None:
    if payload.guild_id is None or str(payload.emoji) not in {"👍", "👎"}:
        return
    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return
    channel = guild.get_channel(payload.channel_id)
    if not isinstance(channel, discord.TextChannel) or channel.name != CH_SUGGESTIONS:
        return
    try:
        message = await channel.fetch_message(payload.message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return
    if not suggestion_is_message(message):
        return

    if added:
        member = await reaction_member(guild, payload.user_id)
        if member is not None and not member.bot:
            opposite = "👎" if str(payload.emoji) == "👍" else "👍"
            for reaction in message.reactions:
                if str(reaction.emoji) == opposite:
                    try:
                        await reaction.remove(member)
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                    break
    try:
        message = await channel.fetch_message(payload.message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return
    await update_suggestion_votes(message)


async def starboard_reactors(message: discord.Message) -> int:
    for reaction in message.reactions:
        if str(reaction.emoji) != "⭐":
            continue
        unique: set[int] = set()
        try:
            async for user in reaction.users(limit=None):
                if user.bot or user.id == message.author.id:
                    continue
                unique.add(user.id)
        except (discord.Forbidden, discord.HTTPException):
            return max(0, reaction.count - 1)
        return len(unique)
    return 0


async def find_starboard_post(channel: discord.TextChannel, source_id: int) -> Optional[discord.Message]:
    marker = f"{STARBOARD_FOOTER_PREFIX}{source_id}"
    try:
        async for msg in channel.history(limit=500):
            if msg.author != channel.guild.me or not msg.embeds:
                continue
            footer = msg.embeds[0].footer.text if msg.embeds[0].footer else None
            if footer == marker:
                return msg
    except (discord.Forbidden, discord.HTTPException):
        pass
    return None


def starboard_embed(message: discord.Message, stars: int) -> discord.Embed:
    content = message.content.strip() if message.content else ""
    description = safe_text(content, 1500) if content else "*Abrí el mensaje original para ver el contenido completo.*"
    embed = discord.Embed(
        title="⭐ Mensaje destacado",
        description=description,
        colour=discord.Colour.gold(),
        timestamp=message.created_at,
    )
    embed.add_field(name="👤 Autor", value=message.author.mention, inline=True)
    embed.add_field(name="💬 Canal", value=message.channel.mention, inline=True)
    tier = "⭐ Destacado"
    if stars >= 20:
        tier = "🏆 Legendario"
    elif stars >= 10:
        tier = "🔥 Muy destacado"
    embed.add_field(name="⭐ Reacciones", value=f"**{stars}** • {tier}", inline=False)

    image_url = None
    for attachment in message.attachments:
        content_type = (attachment.content_type or "").lower()
        if content_type.startswith("image/"):
            image_url = attachment.url
            break
    if image_url is None and message.embeds:
        original = message.embeds[0]
        if original.image and original.image.url:
            image_url = original.image.url
        elif original.thumbnail and original.thumbnail.url:
            image_url = original.thumbnail.url
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(text=f"{STARBOARD_FOOTER_PREFIX}{message.id}")
    return embed


def jump_link_view(url: str, label: str = "Ir al mensaje") -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label=label, emoji="🔗", style=discord.ButtonStyle.link, url=url))
    return view


async def handle_starboard_reaction(payload: discord.RawReactionActionEvent) -> None:
    if payload.guild_id is None or str(payload.emoji) != "⭐":
        return
    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return
    channel = guild.get_channel(payload.channel_id)
    if not isinstance(channel, discord.TextChannel):
        return
    excluded = {
        CH_STARBOARD, CH_VERIFY, CH_ROLES, CH_RULES, CH_ANNOUNCEMENTS,
        CH_STREAMS, CH_CLIPS, CH_COMMANDS, CH_SUGGESTIONS, CH_INVITE, CH_WELCOME,
        CH_EVENTS, CH_LOGS, CH_REPORTS, CH_STAFF,
    }
    if channel.name in excluded or (channel.category and channel.category.name == CAT_TICKETS):
        return
    try:
        message = await channel.fetch_message(payload.message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return
    if message.author.bot:
        return

    # La propia persona no cuenta para destacar su mensaje.
    if payload.user_id == message.author.id:
        reaction = discord.utils.get(message.reactions, emoji="⭐")
        member = await reaction_member(guild, payload.user_id)
        if reaction is not None and member is not None:
            try:
                await reaction.remove(member)
                message = await channel.fetch_message(payload.message_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass

    stars = await starboard_reactors(message)
    star_channel = find_text(guild, CH_STARBOARD)
    if star_channel is None:
        return
    existing = await find_starboard_post(star_channel, message.id)
    if stars < STARBOARD_THRESHOLD:
        if existing is not None:
            try:
                await existing.delete()
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass
        return

    embed = starboard_embed(message, stars)
    view = jump_link_view(message.jump_url)
    if existing is None:
        try:
            await star_channel.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            pass
    else:
        try:
            await existing.edit(embed=embed, view=view)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass


def parse_event_datetime(value: str) -> datetime:
    value = value.strip()
    now_local = datetime.now(EVENT_TZ)
    formats = ("%d/%m/%Y %H:%M", "%d/%m %H:%M")
    last_error = None
    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt)
            if fmt == "%d/%m %H:%M":
                parsed = parsed.replace(year=now_local.year)
                local = parsed.replace(tzinfo=EVENT_TZ)
                if local <= now_local:
                    local = local.replace(year=now_local.year + 1)
                return local
            return parsed.replace(tzinfo=EVENT_TZ)
        except ValueError as exc:
            last_error = exc
    raise ValueError("Usá `DD/MM HH:MM` o `DD/MM/AAAA HH:MM`.") from last_error


def parse_event_footer(embed: discord.Embed) -> Optional[dict]:
    footer = embed.footer.text if embed.footer else ""
    if not footer.startswith(EVENT_FOOTER_PREFIX):
        return None
    state = {"users": []}
    for piece in footer[len(EVENT_FOOTER_PREFIX):].split("|"):
        if "=" not in piece:
            continue
        key, value = piece.split("=", 1)
        state[key] = value
    try:
        state["ts"] = int(state.get("ts", 0))
        state["max"] = int(state.get("max", 10))
        state["owner"] = int(state.get("owner", 0))
        state["rem30"] = int(state.get("rem30", 0))
        state["started"] = int(state.get("started", 0))
        raw_users = state.get("users", "")
        state["users"] = [int(x) for x in raw_users.split(",") if x.isdigit()]
    except (TypeError, ValueError):
        return None
    return state


def serialize_event_footer(state: dict) -> str:
    users = ",".join(str(x) for x in state.get("users", []))
    return (
        f"{EVENT_FOOTER_PREFIX}ts={state['ts']}|max={state['max']}|owner={state['owner']}|"
        f"status={state.get('status', 'open')}|rem30={state.get('rem30', 0)}|"
        f"started={state.get('started', 0)}|users={users}"
    )


def event_status_label(state: dict) -> str:
    status = state.get("status", "open")
    if status == "closed":
        return "🔒 Cerrado"
    if status == "started":
        return "🎉 En curso"
    if len(state.get("users", [])) >= state.get("max", 10):
        return "🔒 Cupos completos"
    return "🟢 Inscripciones abiertas"


def update_event_embed(embed: discord.Embed, state: dict) -> discord.Embed:
    users = state.get("users", [])
    participant_text = "\n".join(f"<@{uid}>" for uid in users) if users else "*Todavía no se anotó nadie.*"
    participant_text += f"\n\n**{len(users)}/{state.get('max', 10)} participantes**"
    set_embed_field(embed, "👥 Participantes", participant_text, inline=False)
    set_embed_field(embed, "📌 Estado", event_status_label(state), inline=True)
    embed.set_footer(text=serialize_event_footer(state))
    return embed


class EventView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Participar", emoji="✅", style=discord.ButtonStyle.success, custom_id="streamer_server:event_join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.message is None or not interaction.message.embeds or not isinstance(interaction.user, discord.Member):
            return
        state = parse_event_footer(interaction.message.embeds[0])
        if state is None:
            return await interaction.response.send_message("No pude leer este evento.", ephemeral=True)
        if state.get("status") != "open" or int(datetime.now(EVENT_TZ).timestamp()) >= state["ts"]:
            return await interaction.response.send_message("Este evento ya no acepta participantes.", ephemeral=True)
        users = state["users"]
        if interaction.user.id in users:
            return await interaction.response.send_message("Ya estás anotado/a.", ephemeral=True)
        if len(users) >= state["max"]:
            return await interaction.response.send_message("Los cupos están completos.", ephemeral=True)
        users.append(interaction.user.id)
        await interaction.response.defer(ephemeral=True)
        embed = update_event_embed(discord.Embed.from_dict(interaction.message.embeds[0].to_dict()), state)
        await interaction.message.edit(embed=embed, view=self)
        await interaction.followup.send("✅ Te anotaste al evento.", ephemeral=True)

    @discord.ui.button(label="Salirme", emoji="🚪", style=discord.ButtonStyle.secondary, custom_id="streamer_server:event_leave")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.message is None or not interaction.message.embeds or not isinstance(interaction.user, discord.Member):
            return
        state = parse_event_footer(interaction.message.embeds[0])
        if state is None:
            return await interaction.response.send_message("No pude leer este evento.", ephemeral=True)
        if interaction.user.id not in state["users"]:
            return await interaction.response.send_message("No estabas anotado/a.", ephemeral=True)
        state["users"].remove(interaction.user.id)
        await interaction.response.defer(ephemeral=True)
        embed = update_event_embed(discord.Embed.from_dict(interaction.message.embeds[0].to_dict()), state)
        await interaction.message.edit(embed=embed, view=self)
        await interaction.followup.send("🚪 Saliste del evento.", ephemeral=True)

    @discord.ui.button(label="Cerrar", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="streamer_server:event_close")
    async def close_event(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None or interaction.message is None or not interaction.message.embeds or not isinstance(interaction.user, discord.Member):
            return
        state = parse_event_footer(interaction.message.embeds[0])
        if state is None:
            return await interaction.response.send_message("No pude leer este evento.", ephemeral=True)
        if not is_staff(interaction.user) and interaction.user.id != state.get("owner"):
            return await interaction.response.send_message("Solo el organizador o el staff puede cerrarlo.", ephemeral=True)
        state["status"] = "closed"
        await interaction.response.defer(ephemeral=True)
        embed = update_event_embed(discord.Embed.from_dict(interaction.message.embeds[0].to_dict()), state)
        await interaction.message.edit(embed=embed, view=None)
        await interaction.followup.send("🔒 Evento cerrado.", ephemeral=True)


class EventModal(discord.ui.Modal, title="Crear evento"):
    event_name = discord.ui.TextInput(label="Nombre", placeholder="Ej: Custom de Valorant", max_length=100)
    event_date = discord.ui.TextInput(label="Fecha y hora", placeholder="Ej: 15/08 22:00", max_length=30)
    event_slots = discord.ui.TextInput(label="Cupos", placeholder="Ej: 10", max_length=3)
    event_description = discord.ui.TextInput(
        label="Descripción",
        placeholder="Qué van a hacer, requisitos, modo de juego...",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            return await interaction.response.send_message("Solo el staff puede crear eventos.", ephemeral=True)
        try:
            event_dt = parse_event_datetime(self.event_date.value)
            slots = int(self.event_slots.value.strip())
            if not 2 <= slots <= 50:
                raise ValueError("Los cupos deben estar entre 2 y 50.")
        except ValueError as exc:
            return await interaction.response.send_message(f"❌ {exc}", ephemeral=True)

        channel = find_text(interaction.guild, CH_EVENTS)
        if channel is None:
            return await interaction.response.send_message("No encuentro el canal de eventos.", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)
        ts = int(event_dt.timestamp())
        state = {
            "ts": ts,
            "max": slots,
            "owner": interaction.user.id,
            "status": "open",
            "rem30": 0,
            "started": 0,
            "users": [],
        }
        embed = discord.Embed(
            title=f"🎉 {safe_text(self.event_name.value, 100)}",
            description=safe_text(self.event_description.value, 1000) if self.event_description.value.strip() else "Evento de la comunidad.",
            colour=discord.Colour.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="📅 Fecha", value=f"<t:{ts}:F>\n<t:{ts}:R>", inline=True)
        embed.add_field(name="👤 Organiza", value=interaction.user.mention, inline=True)
        embed = update_event_embed(embed, state)

        event_role = find_role(interaction.guild, ROLE_EVENT_NOTIFY)
        mention = event_role.mention if event_role else None
        changed = False
        if event_role is not None and not event_role.mentionable and interaction.guild.me and event_role < interaction.guild.me.top_role:
            try:
                await event_role.edit(mentionable=True, reason="Aviso de nuevo evento")
                changed = True
            except discord.Forbidden:
                pass
        try:
            message = await channel.send(
                content=mention,
                embed=embed,
                view=EventView(),
                allowed_mentions=discord.AllowedMentions(roles=[event_role] if event_role else False, users=False, everyone=False),
            )
        finally:
            if changed:
                try:
                    await event_role.edit(mentionable=False, reason="Fin del aviso de evento")
                except discord.Forbidden:
                    pass
        await interaction.followup.send(f"✅ Evento publicado: {message.jump_url}", ephemeral=True)


@tasks.loop(seconds=60)
async def event_watch():
    if not GUILD_ID:
        return
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return
    channel = find_text(guild, CH_EVENTS)
    if channel is None:
        return
    now_ts = int(datetime.now(EVENT_TZ).timestamp())
    try:
        async for message in channel.history(limit=100):
            if message.author != guild.me or not message.embeds:
                continue
            state = parse_event_footer(message.embeds[0])
            if state is None or state.get("status") in {"closed", "started"}:
                continue
            remaining = state["ts"] - now_ts
            changed = False
            users = state.get("users", [])
            mention_text = " ".join(f"<@{uid}>" for uid in users)

            if 0 < remaining <= 1800 and not state.get("rem30"):
                state["rem30"] = 1
                changed = True
                await channel.send(
                    content=(mention_text + "\n" if mention_text else "") + f"🔔 **El evento empieza en menos de 30 minutos.** {message.jump_url}",
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )

            if remaining <= 0 and not state.get("started"):
                state["started"] = 1
                state["status"] = "started"
                changed = True
                await channel.send(
                    content=(mention_text + "\n" if mention_text else "") + f"🎉 **¡El evento empieza ahora!** {message.jump_url}",
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )

            if changed:
                embed = update_event_embed(discord.Embed.from_dict(message.embeds[0].to_dict()), state)
                await message.edit(embed=embed, view=None if state.get("status") == "started" else EventView())
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"⚠️ Event watcher: {type(exc).__name__}: {exc}")


@event_watch.before_loop
async def before_event_watch():
    await bot.wait_until_ready()



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
            "twitch_clips_watcher_running": twitch_clips_watch.is_running(),
            "event_watcher_running": event_watch.is_running(),
            "starboard_threshold": STARBOARD_THRESHOLD,
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
        if twitch_clips_watch.is_running():
            twitch_clips_watch.cancel()
        if event_watch.is_running():
            event_watch.cancel()
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
        self.add_view(SuggestionPanelView())
        self.add_view(SuggestionStaffView())
        self.add_view(EventView())

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

    if GUILD_ID:
        guild = bot.get_guild(GUILD_ID)
        if guild is not None:
            try:
                await cleanup_duplicate_system_messages(guild)
                await cleanup_legacy_giveaway_role(guild)
                await ensure_top_indicators(guild)
                await ensure_invite_message(guild)
                await ensure_suggestion_panel(guild)
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"⚠️ Limpieza/indicadores/invitación: {type(exc).__name__}: {exc}")

    if not member_counter_watch.is_running():
        member_counter_watch.start()
    print("👥 Contador de miembros activo (debounce 8s + respaldo cada 10m)")

    if not event_watch.is_running():
        event_watch.start()
    print(f"🎉 Eventos automáticos activos ({EVENT_TIMEZONE})")

    if TWITCH_ENABLED:
        if not twitch_watch.is_running():
            twitch_watch.start()
        if not twitch_clips_watch.is_running():
            twitch_clips_watch.start()
        print(f"🟣 Twitch activo: @{TWITCH_CHANNEL} (cada {TWITCH_POLL_SECONDS}s)")
        print(f"🎬 Clips automáticos activos (cada {TWITCH_CLIPS_POLL_SECONDS}s)")
    else:
        print("ℹ️ Twitch automático desactivado. Faltan: " + ", ".join(twitch_missing_config()))


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if bot.user is None or payload.user_id == bot.user.id or payload.guild_id is None:
        return

    await handle_suggestion_reaction(payload, added=True)
    await handle_starboard_reaction(payload)

    guild, message, mapping, exclusive = await get_reaction_panel_mapping(payload)
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
    old_roles = [role for role in member.roles if role.name in group_role_names and role != selected] if exclusive else []

    try:
        if old_roles:
            await member.remove_roles(*old_roles, reason="Cambio de reaction role visual")
        if selected not in member.roles:
            await member.add_roles(selected, reason="Reaction role visual")
    except discord.Forbidden:
        return

    # País, edad y rango son exclusivos; juegos/plataformas/avisos permiten varios.
    if exclusive:
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

    await handle_suggestion_reaction(payload, added=False)
    await handle_starboard_reaction(payload)

    guild, _message, mapping, _exclusive = await get_reaction_panel_mapping(payload)
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

        role_owner = await ensure_role(guild, ROLE_OWNER, owner_perms, 0x000000, False)
        role_coowner = await ensure_role(guild, ROLE_COOWNER, coowner_perms, 0xE67E22, True)
        role_admin = await ensure_role(guild, ROLE_ADMIN, admin_perms, 0xE74C3C, True)
        role_mod = await ensure_role(guild, ROLE_MOD, mod_perms, 0x3498DB, True)

        role_member = await ensure_role(guild, ROLE_MEMBER, no_perms, 0x57F287, False)
        role_streamer = await ensure_role(guild, ROLE_STREAMER, no_perms, 0xEB459E, True)
        await ensure_role(guild, ROLE_SUB, no_perms, 0x9B59B6, False)
        await ensure_role(guild, ROLE_VIP, no_perms, 0xFEE75C, False)
        await ensure_role(guild, ROLE_LIVE, no_perms, 0xED4245, True)
        await ensure_role(guild, ROLE_LIVE_NOTIFY, no_perms, 0x9146FF, False)
        await ensure_role(guild, ROLE_EVENT_NOTIFY, no_perms, 0xF1C40F, False)
        for role_name in (ROLE_GAME_VALORANT, ROLE_GAME_MINECRAFT, ROLE_GAME_OTHER, ROLE_PLATFORM_PC, ROLE_PLATFORM_CONSOLE, ROLE_PLATFORM_MOBILE):
            await ensure_role(guild, role_name, no_perms, 0x99AAB5, False)

        # Roles visuales: 0 permisos.
        for name in AGE_ROLES:
            await ensure_role(guild, name, no_perms, 0x99AAB5, False)

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
        ch_invite = await ensure_text_channel(
            guild,
            cat_info,
            CH_INVITE,
            public_readonly,
            "Invitación oficial del servidor.",
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
            "Elegí país, edad, rango de Valorant, juegos, plataformas y avisos.",
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
        ch_welcome = await ensure_text_channel(
            guild,
            cat_community,
            CH_WELCOME,
            member_readonly,
            "Bienvenidas automáticas a nuevos miembros verificados.",
        )
        ch_media = await ensure_text_channel(guild, cat_community, CH_MEDIA, member_text, "Fotos, clips y contenido multimedia.")
        ch_memes = await ensure_text_channel(guild, cat_community, CH_MEMES, member_text, "Memes de la comunidad.")
        ch_commands = await ensure_text_channel(guild, cat_community, CH_COMMANDS, member_text, "Comandos, soporte y utilidades del bot.")
        ch_clips = await ensure_text_channel(guild, cat_community, CH_CLIPS, member_readonly, "Clips nuevos de Twitch publicados automáticamente.")
        ch_pets = await ensure_text_channel(guild, cat_community, CH_PETS, member_text, "Fotos y videos de las mascotas de la comunidad.")
        ch_suggestions = await ensure_text_channel(guild, cat_community, CH_SUGGESTIONS, member_readonly, "Sugerencias con formulario, votos y estados del staff.")
        ch_starboard = await ensure_text_channel(guild, cat_community, CH_STARBOARD, member_readonly, "Mensajes que alcanzan el mínimo de estrellas de la comunidad.")
        ch_events = await ensure_text_channel(guild, cat_community, CH_EVENTS, member_readonly, "Eventos y customs organizados por el staff.")
        await ensure_top_indicators(guild)
        await ensure_invite_message(guild)
        await ensure_suggestion_panel(guild)

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
                        and msg.embeds[0].title in {"🌎 Roles de perfil", "🎭 Elegí tus roles", LEGACY_ROLE_PANEL_NOTIFY_TITLE}
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

        age_lines = "\n".join(
            f"{emoji}  **{role_name.split('・', 1)[1]}**"
            for emoji, role_name in AGE_REACTION_ROLES.items()
        )
        await ensure_reaction_role_panel(
            ch_roles,
            ROLE_PANEL_AGE_TITLE,
            (
                "Reaccioná con tu **rango de edad**. No hace falta publicar tu edad exacta.\n"
                "Solo podés tener un rango de edad a la vez; si cambiás, el bot reemplaza el anterior.\n\n"
                f"{age_lines}"
            ),
            AGE_REACTION_ROLES,
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

        game_lines = "\n".join(role_panel_line(guild, emoji, role_name) for emoji, role_name in GAME_REACTION_ROLES.items())
        await ensure_reaction_role_panel(ch_roles, ROLE_PANEL_GAMES_TITLE, "↳ **¿Qué juegos te interesan?** Podés elegir varios.\n\n" + game_lines, GAME_REACTION_ROLES)

        platform_lines = "\n".join(role_panel_line(guild, emoji, role_name) for emoji, role_name in PLATFORM_REACTION_ROLES.items())
        await ensure_reaction_role_panel(ch_roles, ROLE_PANEL_PLATFORM_TITLE, "↳ **¿Dónde jugás?** Podés elegir varias plataformas.\n\n" + platform_lines, PLATFORM_REACTION_ROLES)

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
            ch_invite,
            "Invitar amigos",
            "Acá encontrás el enlace oficial para sumar amigos a la comunidad. El indicador `🔗 Invitar Amigos` de arriba es solo decorativo y permanece bloqueado.",
        )
        await ensure_guide(
            ch_clips,
            "Clips",
            "Los clips nuevos creados en el Twitch de la streamer aparecen **automáticamente** acá. El canal es de solo lectura para mantenerlo ordenado.",
        )
        await ensure_guide(
            ch_pets,
            "Mascotas",
            "🐾 El rincón para presumir gatos, perros, hámsters y cualquier compañero animal. Compartí fotos o videos con respeto y sin contenido desagradable.",
        )
        await ensure_guide(
            ch_suggestions,
            "Sugerencias",
            "💡 Usá **Enviar sugerencia** para abrir el formulario. La comunidad vota con 👍/👎 y el staff puede marcarla como pendiente, en revisión, aceptada o rechazada.",
        )
        await ensure_guide(
            ch_starboard,
            "Destacados",
            f"⭐ Los mensajes de la comunidad que alcancen **{STARBOARD_THRESHOLD} estrellas** aparecen automáticamente acá. Tu propia estrella no cuenta.",
        )
        await ensure_guide(
            ch_events,
            "Eventos",
            "🎉 Eventos y customs de la comunidad. El staff los crea con `/evento`; podés anotarte con **Participar** y recibirás recordatorios si estás inscripto/a.",
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


    member_readonly = {
        everyone: discord.PermissionOverwrite(view_channel=False),
        role_member: discord.PermissionOverwrite(
            view_channel=True, send_messages=False, add_reactions=True, read_message_history=True
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
    await ensure_text_channel(guild, cat_info, CH_INVITE, public_readonly, "Invitación oficial del servidor.")
    await ensure_text_channel(guild, cat_info, CH_RULES, public_readonly, "Reglas y convivencia de la comunidad.")
    await ensure_text_channel(guild, cat_info, CH_ROLES, roles_readonly, "Elegí país, edad, rango de Valorant, juegos, plataformas y avisos.")
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
    await ensure_text_channel(
        guild,
        cat_community,
        CH_WELCOME,
        member_readonly,
        "Bienvenidas automáticas a nuevos miembros verificados.",
    )
    await ensure_text_channel(guild, cat_community, CH_MEDIA, member_text, "Fotos, clips y contenido multimedia.")
    await ensure_text_channel(guild, cat_community, CH_MEMES, member_text, "Memes de la comunidad.")
    await ensure_text_channel(guild, cat_community, CH_COMMANDS, member_text, "Comandos, soporte y utilidades del bot.")
    await ensure_text_channel(guild, cat_community, CH_CLIPS, member_readonly, "Clips nuevos de Twitch publicados automáticamente.")
    await ensure_text_channel(guild, cat_community, CH_PETS, member_text, "Fotos y videos de las mascotas de la comunidad.")
    await ensure_text_channel(guild, cat_community, CH_SUGGESTIONS, member_readonly, "Sugerencias con formulario, votos y estados del staff.")
    await ensure_text_channel(guild, cat_community, CH_STARBOARD, member_readonly, "Mensajes que alcanzan el mínimo de estrellas de la comunidad.")
    await ensure_text_channel(guild, cat_community, CH_EVENTS, member_readonly, "Eventos y customs organizados por el staff.")
    await ensure_top_indicators(guild)
    await ensure_invite_message(guild)
    await ensure_suggestion_panel(guild)
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
        "💜 Quedaron listos indicadores, invitación, clips, mascotas, sugerencias, destacados, eventos y el canal exclusivo de la streamer.",
    )


@bot.tree.command(name="actualizar-roles", description="Actualiza perfil, juegos, plataformas y avisos.")
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
    await cleanup_legacy_giveaway_role(guild)

    no_perms = discord.Permissions.none()
    for role_name in (ROLE_EVENT_NOTIFY, ROLE_GAME_VALORANT, ROLE_GAME_MINECRAFT, ROLE_GAME_OTHER, ROLE_PLATFORM_PC, ROLE_PLATFORM_CONSOLE, ROLE_PLATFORM_MOBILE):
        await ensure_role(guild, role_name, no_perms, 0x99AAB5, False)
    for role_name in AGE_ROLES:
        await ensure_role(guild, role_name, no_perms, 0x99AAB5, False)

    # Verifica qué roles puede administrar antes de tocar los paneles.
    blocked_roles = []
    for role_name in list(COUNTRIES) + list(AGE_ROLES) + list(VALORANT_RANKS) + list(GAME_REACTION_ROLES.values()) + list(PLATFORM_REACTION_ROLES.values()) + list(NOTIFY_REACTION_ROLES.values()) + [ROLE_LIVE]:
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
    async for msg in ch_roles.history(limit=500):
        if msg.author != guild.me or not msg.embeds:
            continue

        title = msg.embeds[0].title
        if title in {"🌎 Roles de perfil", "🎭 Elegí tus roles", LEGACY_ROLE_PANEL_NOTIFY_TITLE}:
            try:
                await msg.delete()
            except discord.Forbidden:
                pass
            continue

        if title in {ROLE_PANEL_COUNTRY_TITLE, ROLE_PANEL_AGE_TITLE, ROLE_PANEL_RANK_TITLE, ROLE_PANEL_GAMES_TITLE, ROLE_PANEL_PLATFORM_TITLE, ROLE_PANEL_NOTIFY_TITLE}:
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

    await set_progress(interaction, "🔄 **Roles:** actualizando rangos de edad...")
    age_lines = "\n".join(
        role_panel_line(guild, emoji, role_name)
        for emoji, role_name in AGE_REACTION_ROLES.items()
    )
    await ensure_reaction_role_panel(
        ch_roles,
        ROLE_PANEL_AGE_TITLE,
        (
            "↳ **Seleccioná tu rango de edad.** No hace falta decir tu edad exacta.\n\n"
            f"{age_lines}\n\n"
            "Solo podés tener **un rango de edad** a la vez. Si reaccionás a otro, el bot reemplaza el anterior."
        ),
        AGE_REACTION_ROLES,
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

    await set_progress(interaction, "🔄 **Roles:** actualizando juegos y plataformas...")
    game_lines = "\n".join(role_panel_line(guild, emoji, role_name) for emoji, role_name in GAME_REACTION_ROLES.items())
    await ensure_reaction_role_panel(ch_roles, ROLE_PANEL_GAMES_TITLE, "↳ **Elegí los juegos que te interesan.** Podés marcar varios.\n\n" + game_lines, GAME_REACTION_ROLES)

    platform_lines = "\n".join(role_panel_line(guild, emoji, role_name) for emoji, role_name in PLATFORM_REACTION_ROLES.items())
    await ensure_reaction_role_panel(ch_roles, ROLE_PANEL_PLATFORM_TITLE, "↳ **Elegí dónde jugás.** Podés marcar varias plataformas.\n\n" + platform_lines, PLATFORM_REACTION_ROLES)

    await set_progress(interaction, "🔄 **Roles:** actualizando avisos...")
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
            "🎂 Rangos de edad sincronizados.\n"
            "🎖️ Rangos de Valorant sincronizados con los emojis personalizados.\n"
            "🎮 Juegos y plataformas listos.\n"
            "📣 Avisos de directos y eventos listos.",
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
        (CH_INVITE, "Invitar amigos", "Acá está la invitación oficial del servidor. El indicador `🔗 Invitar Amigos` de arriba es visual; el enlace clickeable está en este canal."),
        (CH_RULES, "Reglas", "Este canal es de **solo lectura**. Acá se publican las normas oficiales de la comunidad. Leelas antes de participar."),
        (CH_ANNOUNCEMENTS, "Anuncios", "Novedades importantes del servidor, eventos, cambios y avisos del staff. Los miembros leen; el staff publica."),
        (CH_STREAMS, "Directos", "Historial de avisos automáticos de Twitch. Al prender stream se publica título, categoría, miniatura y enlace; `🔔・Avisos de directo` recibe la mención."),
        (CH_STREAMER_ONLY, "Aquí solo habla la streamer", "Espacio personal de la streamer. La comunidad puede **leer y reaccionar**, pero solo `🎥・Streamer` publica."),
        (CH_GENERAL, "General", "Chat principal de la comunidad. Hablá, conocé gente y compartí con respeto."),
        (CH_MEDIA, "Multimedia", "Compartí capturas, fotos, fanarts y contenido multimedia. Los clips de Twitch tienen su canal automático `🎬・clips`."),
        (CH_MEMES, "Memes", "Memes y humor de la comunidad dentro de las reglas y sin ataques personales."),
        (CH_COMMANDS, "Comandos y soporte", "Utilidades del bot. Para hablar en privado con el staff usá **Crear reporte**."),
        (CH_CLIPS, "Clips", "🎬 Los clips nuevos creados en el Twitch de la streamer aparecen **automáticamente** acá. Canal de solo lectura para mantenerlo ordenado."),
        (CH_PETS, "Mascotas", "🐾 Compartí fotos y videos de gatos, perros, hámsters y cualquier compañero animal. Con cariño y sin contenido desagradable."),
        (CH_SUGGESTIONS, "Sugerencias", "💡 Usá **Enviar sugerencia** para abrir el formulario. La comunidad vota con 👍/👎 y el staff marca el estado."),
        (CH_STARBOARD, "Destacados", f"⭐ Los mensajes que alcancen **{STARBOARD_THRESHOLD} estrellas** aparecen automáticamente acá. La estrella del autor no cuenta."),
        (CH_EVENTS, "Eventos", "🎉 Eventos y customs. El staff usa `/evento`; los participantes pueden anotarse y reciben recordatorios."),
        (CH_GAMING, "Gaming", "Charlá sobre cualquier juego. Valorant tiene su canal propio."),
        (CH_VALORANT, "Valorant", "Rankeds, agentes, mapas, estrategias y partidas. Para armar grupo usá `🔎・busco-grupo`."),
        (CH_LFG, "Buscar grupo — Valorant", "Usá **`/party`** acá para crear una búsqueda. El bot toma tu rango y publica botones de **Unirme**, **Salir** y **Cerrar**."),
        (CH_STAFF, "Staff", "Chat interno del equipo de moderación."),
        (CH_REPORTS, "Reportes", "Registro interno de tickets abiertos/cerrados."),
        (CH_LOGS, "Logs", "Registro automático de entradas, salidas, roles, apodos y cambios de canales/roles."),
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




@bot.tree.command(name="evento", description="Crea un evento o custom para la comunidad.")
@app_commands.guild_only()
async def event_command(interaction: discord.Interaction):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
        return await interaction.response.send_message("Solo el staff puede crear eventos.", ephemeral=True)
    if find_text(interaction.guild, CH_EVENTS) is None:
        return await interaction.response.send_message(
            f"No encuentro `{CH_EVENTS}`. Ejecutá `/actualizar-canales` una vez.",
            ephemeral=True,
        )
    await interaction.response.send_modal(EventModal())


@bot.tree.command(name="clips-revisar", description="Fuerza una revisión inmediata de clips de Twitch.")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def clips_check_command(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        published = await publish_new_twitch_clips(interaction.guild)
        newest = ""
        if _twitch_clips_last_newest_title:
            newest = (
                f"\nÚltimo detectado: **{safe_text(_twitch_clips_last_newest_title, 120)}**"
                f"\nCreado: `{_twitch_clips_last_newest_created or 'desconocido'}`"
            )
        await set_progress(
            interaction,
            f"✅ Revisión completada. Twitch devolvió **{_twitch_clips_last_found}** clips dentro de las últimas "
            f"**{TWITCH_CLIPS_LOOKBACK_MINUTES} min** y publiqué **{published}** nuevos.{newest}",
        )
    except Exception as exc:
        await set_progress(interaction, f"❌ Error revisando clips: `{type(exc).__name__}: {str(exc)[:700]}`")


@bot.tree.command(name="clips-estado", description="Muestra el diagnóstico del publicador automático de clips.")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def clips_status_command(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return
    last = _twitch_clips_last_check_at.strftime("%Y-%m-%d %H:%M:%S UTC") if _twitch_clips_last_check_at else "todavía no"
    lines = [
        "🎬 **Estado de clips automáticos**",
        f"Watcher: {'✅ activo' if twitch_clips_watch.is_running() else '⚠️ detenido'}",
        f"Revisión: cada **{TWITCH_CLIPS_POLL_SECONDS}s**",
        f"Ventana: últimos **{TWITCH_CLIPS_LOOKBACK_MINUTES} min**",
        f"Última revisión: `{last}`",
        f"Últimos encontrados: **{_twitch_clips_last_found}**",
        f"Últimos publicados: **{_twitch_clips_last_published}**",
        f"Último error: `{_twitch_clips_last_error or 'ninguno'}`",
    ]
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


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
    if not twitch_clips_watch.is_running():
        twitch_clips_watch.start()
    try:
        await publish_new_twitch_clips(guild)
    except Exception as exc:
        print(f"⚠️ Comprobación de clips: {type(exc).__name__}: {exc}")

    if stream is not None:
        await handle_twitch_online(guild, stream)
        await set_progress(
            interaction,
            "✅ **Twitch funcionando.**\n"
            f"🔴 `@{TWITCH_CHANNEL}` está EN DIRECTO ahora.\n"
            f"✅ `{CH_LIVE}` sincronizado.\n"
            f"✅ `{VC_LIVE}` creado/sincronizado.\n"
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
            f"✅ `{VC_LIVE}` aparecerá automáticamente al prender.\n"
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
    live_voice = find_voice(interaction.guild, VC_LIVE)
    live_role = find_role(interaction.guild, ROLE_LIVE)
    streamer_members = twitch_streamer_members(interaction.guild)
    role_active = bool(live_role and any(live_role in member.roles for member in streamer_members))

    lines = [
        "✅ **Integración de Twitch conectada**",
        f"Canal: `@{TWITCH_CHANNEL}`",
        f"Estado Twitch: {'🔴 EN DIRECTO' if stream else '⚫ Offline'}",
        f"Canal de texto temporal: {'✅ existe' if live_channel else '— no existe'}",
        f"Canal de voz temporal: {'✅ existe' if live_voice else '— no existe'}",
        f"Rol EN DIRECTO: {'✅ activo' if role_active else '— inactivo'}",
        f"Modo de prueba: {'🧪 ACTIVO' if _twitch_test_mode else '— inactivo'}",
        f"Chequeo del directo: cada {TWITCH_POLL_SECONDS}s",
        f"Clips automáticos: {'✅ activos' if twitch_clips_watch.is_running() else '⚠️ detenidos'} (cada {TWITCH_CLIPS_POLL_SECONDS}s, ventana {TWITCH_CLIPS_LOOKBACK_MINUTES}m)",
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
        live_voice = await ensure_live_voice_channel(guild)

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
            content=(
                "🧪 **Canal temporal creado en modo de prueba.**\n"
                f"🔊 Voz de prueba: {live_voice.mention}\n"
                "⚠️ Al entrar a la voz, asumí que tu audio podría salir en el directo: respeto ante todo."
            ),
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
            f"✅ `{VC_LIVE}` creado/sincronizado.\n"
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

    live_voice = find_voice(guild, VC_LIVE)
    if live_voice is not None:
        try:
            await live_voice.delete(reason="Fin de la simulación de Twitch")
        except discord.Forbidden:
            return await set_progress(
                interaction,
                f"❌ No pude borrar `{VC_LIVE}`. Revisá el permiso Gestionar canales."
            )

    await bot.change_presence(status=discord.Status.online, activity=None)

    result = await sync_real_twitch_after_test(guild)
    await set_progress(
        interaction,
        "✅ **Simulación terminada.**\n"
        f"✅ `{ROLE_LIVE}` de prueba retirado.\n"
        f"✅ Canal de texto temporal de prueba eliminado.\n"
        f"✅ Canal de voz temporal de prueba eliminado.\n"
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
            f"**{interaction.user.display_name}** está buscando gente para jugar.\n"
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
        value=f"{interaction.user.mention}\n\n**1/{max_players}**",
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
    await schedule_member_counter_update(member.guild, +1)


@bot.event
async def on_member_remove(member: discord.Member):
    await send_log(
        member.guild,
        "📤 Miembro salió",
        f"**{discord.utils.escape_markdown(str(member))}** (`{member.id}`) salió del servidor.",
        discord.Colour.orange(),
    )
    await schedule_member_counter_update(member.guild, -1)


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
