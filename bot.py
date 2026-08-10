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
        self.add_view(SelfRolesView())
        self.add_view(TicketPanelView())
        self.add_view(CloseTicketView())

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
intents.message_content = ENABLE_MESSAGE_LOGS

bot = SetupBot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"✅ Conectado como {bot.user} (ID: {bot.user.id})")
    if GUILD_ID:
        print(f"✅ Comandos sincronizados en el servidor {GUILD_ID}")
    else:
        print("ℹ️ Comandos globales sincronizados. Pueden tardar en aparecer.")


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
        await ensure_text_channel(
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
            member_readonly,
            "Elegí tus roles visuales de país y rango de Valorant.",
        )
        await ensure_text_channel(
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
        await ensure_text_channel(
            guild,
            cat_info,
            CH_STREAMS,
            stream_ow,
            "Avisos de directos y contenido de la streamer.",
        )

        # ── Comunidad ─────────────────────────────────────────────────────────
        await ensure_text_channel(guild, cat_community, CH_GENERAL, member_text, "Chat principal.")
        await ensure_text_channel(guild, cat_community, CH_MEDIA, member_text, "Fotos, clips y contenido multimedia.")
        await ensure_text_channel(guild, cat_community, CH_MEMES, member_text, "Memes de la comunidad.")
        ch_commands = await ensure_text_channel(guild, cat_community, CH_COMMANDS, member_text, "Comandos, soporte y utilidades del bot.")

        # ── Gaming ────────────────────────────────────────────────────────────
        await ensure_text_channel(guild, cat_gaming, CH_GAMING, member_text, "Juegos en general.")
        await ensure_text_channel(guild, cat_gaming, CH_VALORANT, member_text, "Todo sobre Valorant.")
        await ensure_text_channel(guild, cat_gaming, CH_LFG, member_text, "Buscá duo, team o gente para jugar.")

        # ── Voz ───────────────────────────────────────────────────────────────
        await ensure_voice_channel(guild, cat_voice, VC_GENERAL, member_voice)
        await ensure_voice_channel(guild, cat_voice, VC_GAMING, member_voice)
        await ensure_voice_channel(guild, cat_voice, VC_VALORANT, member_voice)
        await ensure_voice_channel(guild, cat_voice, VC_CREATE, member_voice)

        # ── Staff ─────────────────────────────────────────────────────────────
        await ensure_text_channel(guild, cat_staff, CH_STAFF, staff_ow, "Chat privado del staff.")
        await ensure_text_channel(guild, cat_staff, CH_REPORTS, staff_ow, "Seguimiento interno de reportes.")
        await ensure_text_channel(guild, cat_staff, CH_LOGS, staff_ow, "Logs y registros de moderación.")

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

        # Migra el panel de roles desde #comandos a #roles si existe allí.
        try:
            async for msg in ch_commands.history(limit=50):
                if msg.author == guild.me and msg.embeds and msg.embeds[0].title == "🌎 Roles de perfil":
                    await msg.delete()
        except discord.Forbidden:
            pass

        roles_already = False
        async for msg in ch_roles.history(limit=30):
            if msg.author == guild.me and msg.embeds and msg.embeds[0].title == "🎭 Elegí tus roles":
                roles_already = True
                break

        if not roles_already:
            embed = discord.Embed(
                title="🎭 Elegí tus roles",
                description=(
                    "Personalizá tu perfil de la comunidad. Estos roles son **solo visuales** "
                    "y no cambian tus permisos.\n\n"
                    "🌎 **País:** elegí de dónde sos.\n"
                    "🔫 **Valorant:** elegí tu rango actual.\n\n"
                    "Podés cambiarlos cuando quieras."
                ),
                colour=discord.Colour.blurple(),
            )
            await ch_roles.send(embed=embed, view=SelfRolesView())

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

        await interaction.followup.send(
            "✅ **Setup completado.**\n"
            "Creé/actualicé roles, categorías, canales, permisos, verificación, "
            "el canal de roles y el sistema privado de reportes.\n\n"
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
