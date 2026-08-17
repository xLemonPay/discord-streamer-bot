from pathlib import Path

path = Path("bot.py")
code = path.read_text(encoding="utf-8")

start_marker = "async def ensure_top_indicators(guild: discord.Guild) -> tuple[discord.VoiceChannel, discord.VoiceChannel]:"
end_marker = "\n\nasync def update_member_counter"
start = code.index(start_marker)
end = code.index(end_marker, start)

replacement = '''async def ensure_top_indicators(guild: discord.Guild) -> tuple[discord.VoiceChannel, discord.VoiceChannel]:
    """Crea/migra y fuerza los indicadores dentro de la categoría CLAN KITEZH."""
    overwrites = indicator_overwrites(guild)
    wanted_count = member_counter_name(guild)

    def compact_name(value: str) -> str:
        return "".join(ch.lower() for ch in value if ch.isalnum())

    home_category = next(
        (category for category in guild.categories if "clankitezh" in compact_name(category.name)),
        None,
    )
    if home_category is None:
        home_category = next(
            (category for category in guild.categories if "kitezh" in compact_name(category.name)),
            None,
        )

    counter = find_member_counter_voice(guild)
    if counter is None:
        counter = await guild.create_voice_channel(
            wanted_count,
            category=home_category,
            overwrites=overwrites,
            reason="Contador visual de miembros",
        )
    else:
        edits = {}
        if counter.name != wanted_count:
            edits["name"] = wanted_count
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
            category=home_category,
            overwrites=overwrites,
            reason="Indicador visual de invitación",
        )
    else:
        edits = {}
        if invite_indicator.name != VC_INVITE_INDICATOR:
            edits["name"] = VC_INVITE_INDICATOR
        if invite_indicator.overwrites != overwrites:
            edits["overwrites"] = overwrites
        if edits:
            await invite_indicator.edit(**edits, reason="Actualizar indicador de invitación")

    if home_category is not None:
        await counter.move(
            category=home_category,
            beginning=True,
            sync_permissions=False,
            reason="Mover indicador de miembros a CLAN KITEZH",
        )
        await invite_indicator.move(
            category=home_category,
            after=counter,
            sync_permissions=False,
            reason="Mover indicador de invitación a CLAN KITEZH",
        )
        print(
            f"🏠 Indicadores movidos a {home_category.name}: "
            f"{counter.name} | {invite_indicator.name}"
        )
    else:
        print("⚠️ No encontré una categoría cuyo nombre contenga CLAN KITEZH.")

    return counter, invite_indicator
'''

code = code[:start] + replacement + code[end:]

command_marker = '@bot.tree.command(name="actualizar-roles", description="Actualiza perfil, juegos, plataformas y avisos.")'
if '@bot.tree.command(name="actualizar-indicadores"' not in code:
    new_command = '''@bot.tree.command(name="actualizar-indicadores", description="Fuerza los indicadores de miembros e invitación dentro de CLAN KITEZH.")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def update_indicators_command(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        counter, invite_indicator = await ensure_top_indicators(interaction.guild)
        category = counter.category
        if category is None:
            return await set_progress(
                interaction,
                "❌ No encontré la categoría **CLAN KITEZH**. Los indicadores siguen fuera de categoría.",
            )

        await set_progress(
            interaction,
            "✅ **Indicadores movidos.**\\n"
            f"📁 Categoría: **{category.name}**\\n"
            f"👥 `{counter.name}`\\n"
            f"🔗 `{invite_indicator.name}`",
        )
    except discord.Forbidden:
        await set_progress(
            interaction,
            "❌ Discord no me dejó moverlos. Revisá que el bot tenga **Gestionar canales**.",
        )
    except Exception as exc:
        await set_progress(
            interaction,
            f"❌ Error moviendo indicadores: `{type(exc).__name__}: {str(exc)[:500]}`",
        )


'''
    code = code.replace(command_marker, new_command + command_marker, 1)

assert 'await counter.move(' in code
assert 'await invite_indicator.move(' in code
assert 'name="actualizar-indicadores"' in code
assert 'CH_WELCOME = "👋・bienvenidas"' in code

path.write_text(code, encoding="utf-8")
