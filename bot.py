import os
import asyncio
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

CH_VERIFY = "✅・verificación"
CH_RULES = "📜・reglas"
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
        ch_commands = await ensure_text_channel(guild, cat_community, CH_COMMANDS, member_text, "Comandos y elección de roles.")

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

        roles_already = False
        async for msg in ch_commands.history(limit=30):
            if msg.author == guild.me and msg.embeds and msg.embeds[0].title == "🌎 Roles de perfil":
                roles_already = True
                break

        if not roles_already:
            embed = discord.Embed(
                title="🌎 Roles de perfil",
                description=(
                    "Estos roles son **solo visuales** y no cambian tus permisos.\n\n"
                    "• Elegí tu país.\n"
                    "• Elegí tu rango actual de Valorant.\n"
                    "Podés cambiarlos cuando quieras."
                ),
                colour=discord.Colour.blurple(),
            )
            await ch_commands.send(embed=embed, view=SelfRolesView())

        await interaction.followup.send(
            "✅ **Setup completado.**\n"
            "Creé/actualicé roles, categorías, canales, permisos, verificación "
            "y los selectores visuales de país + rango de Valorant.\n\n"
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
