import os
import asyncio
import io
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from aiohttp import web

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
PORT = int(os.getenv("PORT", "8000") or 8000)
ENABLE_MESSAGE_LOGS = os.getenv("ENABLE_MESSAGE_LOGS", "false").strip().lower() in {"1", "true", "yes", "on"}

# ──────────────────────────────────────────────────────────────────────────────
# NOMBRES
# ──────────────────────────────────────────────────────────────────────────────

ROLE_MEMBER = "✅・Miembro"

ROLE_OWNER = "👑・Owner"
ROLE_COOWNER = "💎・Co-Owner"
ROLE_ADMIN = "🛡️・Admin"
ROLE_MOD = "🔨・Moderador"
ROLE_STREAMER = "🎥・Streamer"
ROLE_SUB = "💜・Subscriber"
ROLE_VIP = "⭐・VIP"

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
RANK_REACTION_ROLES = {name.split("・", 1)[0]: name for name in VALORANT_RANKS}

ROLE_PANEL_COUNTRY_TITLE = "🌎 Elegí tu país"
ROLE_PANEL_RANK_TITLE = "🔫 Elegí tu rango de Valorant"
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

    existing = {str(reaction.emoji) for reaction in message.reactions}
    for emoji in mapping:
        if emoji not in existing:
            try:
                await message.add_reaction(emoji)
            except (discord.Forbidden, discord.HTTPException):
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
        return guild, message, RANK_REACTION_ROLES
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
    async def setup_hook(self):
        await start_health_server(self)

        self.add_view(VerifyView())
        self.add_view(TicketPanelView())
        self.add_view(CloseTicketView())
        self.add_view(PartyView())

        if GUILD_ID:
            guild_obj = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild_obj)
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


@bot.tree.command(name="setup", description="Crea y configura la estructura completa del servidor.")
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

        rank_lines = "\n".join(
            f"{emoji}  **{role_name.split('・', 1)[1]}**"
            for emoji, role_name in RANK_REACTION_ROLES.items()
        )
        await ensure_reaction_role_panel(
            ch_roles,
            ROLE_PANEL_RANK_TITLE,
            (
                "Reaccioná con tu **rango actual de Valorant**.\n"
                "Solo podés tener un rango a la vez; si cambiás, el bot reemplaza el anterior.\n\n"
                f"{rank_lines}"
            ),
            RANK_REACTION_ROLES,
        )

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
            "reaction roles, guías por canal, el sistema privado de reportes y la búsqueda de grupo de Valorant.\n\n"
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
