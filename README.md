# Discord Streamer Bot — s0ftbl4de

Bot de comunidad para Discord con verificación, roles, tickets, Valorant, voz temporal y automatización de Twitch.

## Cambios incluidos en esta reconstrucción

### Indicadores superiores
El bot crea/migra dos canales de voz bloqueados, sin categoría, para que aparezcan separados arriba del servidor:

```text
🔒 💜 253 Miembros 💜
🔒 🔗 Invitar Amigos
```

El candado lo muestra Discord porque `@everyone` puede verlos pero no conectarse. El contador cambia automáticamente cuando entra o sale gente.

El segundo indicador es visual. El enlace clickeable queda en `🔗・invitar-amigos` dentro de Información.

### Canales nuevos

- `🔗・invitar-amigos`: invitación oficial clickeable.
- `🎬・clips`: clips nuevos del Twitch de la streamer, publicados automáticamente y en solo lectura para miembros.
- `🐾・mascotas`: fotos y videos de mascotas de la comunidad.
- `💡・sugerencias`: ideas para Discord, streams, eventos y juegos.

Cada uno recibe una guía automática con su función.

### Bienvenida
Al pulsar **Verificarme** y recibir `✅・Miembro`, el bot publica una bienvenida en `💬・general` con accesos rápidos a roles, general y reglas.

### Roles de perfil
Exclusivos (solo uno por grupo):

- país;
- rango de edad (`🧒・Menor de 18`, `🎂・18-25`, `🧑・26+`);
- rango de Valorant.

Múltiples permitidos:

**Juegos**
- `🔫・Valorant`
- `⛏️・Minecraft`
- `🎮・Otros juegos`

**Plataforma**
- `🖥️・PC`
- `🎮・Consola`
- `📱・Mobile`

**Avisos**
- `🔔・Avisos de directo`
- `🎉・Avisos de eventos`
- `🎁・Avisos de sorteos`

### Twitch en directo
Al detectar que `TWITCH_CHANNEL` está live:

- asigna `🔴・EN DIRECTO` a la streamer;
- crea `🔴・stream-en-vivo`;
- crea `🔴・EN DIRECTO | RESPETO` en voz;
- publica título, categoría, espectadores y miniatura real 1280×720;
- agrega botón **Ver en Twitch**;
- menciona `🔔・Avisos de directo`;
- cambia la presencia del bot a Streaming.

Al terminar, quita el rol y elimina los canales temporales después de `TWITCH_OFFLINE_DELETE_DELAY`.

### Clips automáticos
El bot consulta Twitch cada `TWITCH_CLIPS_POLL_SECONDS` segundos y revisa una ventana reciente de `TWITCH_CLIPS_LOOKBACK_MINUTES` minutos. Antes de publicar revisa los mensajes del canal por el ID del clip, por lo que un reinicio no debería duplicar clips ya publicados.

Cada clip muestra título, creador, vistas, miniatura y botón **Ver clip**.

## Comandos principales

```text
/setup
```
Instalación inicial completa. No hace falta repetirlo para cambios pequeños.

```text
/actualizar-canales
```
Crea/actualiza categorías, canales, permisos, invitación, indicadores superiores, clips, mascotas y sugerencias.

```text
/actualizar-roles
```
Actualiza país, edad, rango de Valorant, juegos, plataformas y avisos.

```text
/actualizar-guias
```
Actualiza las explicaciones de los canales.

```text
/actualizar-tickets
```
Actualiza el sistema privado de reportes.

```text
/actualizar-twitch
/twitch-estado
```
Comprueba Twitch e inicia/sincroniza el watcher de directos y clips.

```text
/twitch-preview
/twitch-simular
/twitch-fin-prueba
```
Permiten ver/probar el flujo de Twitch sin que la streamer tenga que prender.

## Variables de entorno

```env
DISCORD_TOKEN=
GUILD_ID=
ENABLE_MESSAGE_LOGS=false
DISCORD_INVITE_URL=

TWITCH_CLIENT_ID=
TWITCH_CLIENT_SECRET=
TWITCH_CHANNEL=
STREAMER_DISCORD_ID=0
TWITCH_POLL_SECONDS=60
TWITCH_OFFLINE_DELETE_DELAY=300
TWITCH_CLIPS_POLL_SECONDS=120
TWITCH_CLIPS_LOOKBACK_MINUTES=20
```

`DISCORD_INVITE_URL` es opcional. Si queda vacío, el bot intenta reutilizar o crear una invitación permanente a `✅・verificación`.

## Jerarquía recomendada

```text
👑・Owner                 ← sin color y sin mostrar separado
Server Setup              ← rol del bot
🔴・EN DIRECTO             ← mostrar separado, rojo
🎥・Streamer               ← mostrar separado, violeta/rosa
💎・Co-Owner
🛡️・Admin
🔨・Moderador
...
```

El bot intenta mantener `🔴・EN DIRECTO` justo debajo de su propio rol y `🎥・Streamer` debajo de `🔴・EN DIRECTO`. `👑・Owner` puede estar por encima del bot, pero si ya está por encima el bot no puede editar su color/hoist: eso se configura manualmente en Discord.

## Después de reemplazar `bot.py`

En un servidor que ya estaba configurado, no hace falta `/setup`. Ejecutá:

```text
/actualizar-canales
/actualizar-roles
/actualizar-guias
/actualizar-twitch
/twitch-estado
```

El bot necesita permisos suficientes para administrar canales/roles y **Crear invitación** si no configurás `DISCORD_INVITE_URL` manualmente.
