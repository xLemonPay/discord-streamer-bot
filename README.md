# Discord Streamer Bot — s0ftbl4de

Bot de comunidad para Discord con verificación, roles, tickets, Valorant, voz temporal, Twitch automático y sistemas de participación.

## Novedades de esta versión

### 💡 Sugerencias con votación
Canal: `💡・sugerencias`.

El canal queda ordenado y de solo lectura. El bot publica un panel con el botón **Enviar sugerencia**. Al tocarlo se abre un formulario con título y descripción.

Cada sugerencia se publica con:

- 👍 voto a favor;
- 👎 voto en contra;
- estado `🟡 Pendiente`;
- botones de staff para `En revisión`, `Aceptar` o `Rechazar`.

Los votos se guardan como reacciones de Discord, por lo que siguen existiendo después de reiniciar Northflank.

### ⭐ Destacados / Starboard
Canal: `⭐・destacados`.

Un mensaje aparece automáticamente cuando alcanza `STARBOARD_THRESHOLD` estrellas de usuarios distintos. Por defecto son **5 ⭐**.

- la estrella del autor sobre su propio mensaje no cuenta;
- 10 estrellas: `🔥 Muy destacado`;
- 20 estrellas: `🏆 Legendario`;
- si baja del mínimo, se retira del canal de destacados;
- incluye botón para volver al mensaje original.

### 🎉 Eventos y customs
Canal: `🎉・eventos`.

El staff usa `/evento` y completa un formulario con nombre, fecha/hora, cupos y descripción.

La publicación ofrece:

- `✅ Participar`;
- `🚪 Salirme`;
- `🔒 Cerrar` para organizador/staff;
- contador de participantes;
- recordatorio dentro de los 30 minutos previos;
- aviso al comenzar;
- menciones a los participantes anotados.

Al publicar un evento se avisa al rol `🎉・Avisos de eventos`.

La zona horaria por defecto es `America/Asuncion` y puede cambiarse con `EVENT_TIMEZONE`.

### 🎬 Clips automáticos de Twitch — revisado
Canal: `🎬・clips`.

El bot revisa Twitch cada **60 segundos**. La consulta ahora:

- usa una ventana de 180 minutos por defecto;
- pagina hasta 300 clips recientes;
- ordena los resultados por fecha antes de publicarlos;
- evita duplicados comprobando el ID del clip en Discord;
- guarda diagnóstico de la última consulta y del último error.

Comandos de diagnóstico:

```text
/clips-revisar
/clips-estado
```

`/clips-revisar` fuerza una búsqueda inmediata y publica cualquier clip nuevo que encuentre.

### 👥 Contador y rate limits
Los indicadores siguen siendo:

```text
🔒 💜 253 Miembros 💜
🔒 🔗 Invitar Amigos
```

El bot ya no reposiciona ni edita canales/roles que están correctamente configurados. El contador agrupa entradas y salidas durante unos segundos para hacer un solo rename y usa una resincronización de respaldo cada 10 minutos.

Esto reduce de forma importante los `PATCH` innecesarios y los rate limits de Discord.

## Canales de comunidad

```text
💬・general
📸・multimedia
😂・memes
🤖・comandos
🎬・clips
🐾・mascotas
💡・sugerencias
⭐・destacados
🎉・eventos
```

## Twitch

Cuando `TWITCH_CHANNEL` está en directo, el bot:

- asigna `🔴・EN DIRECTO`;
- crea `🔴・stream-en-vivo`;
- crea `🔴・EN DIRECTO | RESPETO` en voz;
- publica título, categoría, espectadores y miniatura real de Twitch;
- agrega botón **Ver en Twitch**;
- menciona `🔔・Avisos de directo`;
- cambia la presencia del bot a Streaming.

Al terminar quita el rol y elimina los canales temporales después de `TWITCH_OFFLINE_DELETE_DELAY`.

## Comandos importantes

```text
/actualizar-canales
/actualizar-roles
/actualizar-guias
/actualizar-tickets
/actualizar-twitch
/twitch-estado
```

Pruebas de Twitch:

```text
/twitch-preview
/twitch-simular
/twitch-fin-prueba
```

Clips:

```text
/clips-revisar
/clips-estado
```

Eventos:

```text
/evento
```

`/setup` queda reservado para una instalación inicial completa. En un servidor ya instalado no hace falta repetirlo.

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
TWITCH_CLIPS_POLL_SECONDS=60
TWITCH_CLIPS_LOOKBACK_MINUTES=180

STARBOARD_THRESHOLD=5
EVENT_TIMEZONE=America/Asuncion
```

`DISCORD_INVITE_URL` es opcional. Si queda vacío, el bot intenta reutilizar o crear una invitación permanente.

## Después de reemplazar `bot.py`

En el servidor ya configurado, esperá el reinicio de Northflank y ejecutá una sola vez:

```text
/actualizar-canales
/actualizar-roles
/actualizar-guias
/actualizar-twitch
/clips-revisar
```

Después podés usar `/clips-estado` para comprobar que el watcher de clips quedó funcionando.
