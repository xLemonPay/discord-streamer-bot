# Discord Streamer Bot v5

## Emojis personalizados de Valorant

El bot busca automáticamente estos nombres dentro del servidor:

- `valoranthierro`
- `valorantbronce`
- `valorantplata`
- `valorantoro`
- `valorantplatino`
- `valorantdiamante`
- `valorantascendente`
- `valorantimmortal`
- `valorantradiante`

`Sin rango` usa ⚫.

No hace falta copiar IDs de emojis.

## Actualizar el panel

1. Subí/reemplazá `bot.py` en GitHub.
2. Esperá que Northflank vuelva a `Running`.
3. Ejecutá `/setup`.
4. El bot:
   - busca los emojis por nombre;
   - elimina las reacciones viejas del panel de Valorant;
   - coloca las nuevas;
   - actualiza el texto del embed.

Si falta un emoji personalizado, usa temporalmente el emoji de color correspondiente.

## Jerarquía

`/setup` ahora respeta roles que estén por encima del rol del bot y no intenta editarlos,
evitando que toda la configuración falle por el rol Owner.
