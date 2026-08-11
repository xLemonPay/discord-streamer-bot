# Discord Streamer Bot — versión definitiva

Bot personalizado para una comunidad de streamer con foco gaming/Valorant. Está pensado para ejecutarse 24/7 en Northflank.

## Funciones incluidas

### ✅ Verificación
Canal: `✅・verificación`.

El botón **Verificarme** asigna `✅・Miembro`, que desbloquea Comunidad, Gaming y Voz. La vista es persistente y sigue funcionando después de reinicios.

### 🎭 Reaction roles
Canal: `🎭・roles`.

**País:** el usuario reacciona con una bandera. Solo conserva un país; elegir otro reemplaza el anterior y quitar la reacción quita el rol.

Países incluidos: Paraguay, Argentina, Brasil, Uruguay, Chile, Bolivia, Perú, Colombia, Venezuela, Ecuador, México, España y Otro.

**Rango de Valorant:** el usuario reacciona con su rango. Solo conserva un rango y cambiarlo reemplaza el anterior.

El bot busca los emojis personalizados por nombre, sin guardar IDs:

- `valoranthierro`
- `valorantbronce`
- `valorantplata`
- `valorantoro`
- `valorantplatino`
- `valorantdiamante`
- `valorantascendente`
- `valorantimmortal`
- `valorantradiante`

`Sin rango` usa `⚫`. Si falta un emoji personalizado, `/actualizar-roles` usa temporalmente el emoji normal correspondiente.

**Avisos de directo:** el panel `🔔 Avisos de directo` permite reaccionar con 🔔 para recibir `🔔・Avisos de directo`. Al quitar la reacción se quita el rol.

### 🔎 Buscar grupo de Valorant
Canal: `🔎・busco-grupo`.

```text
/party modo:<modo> cupos:<1-4> servidor:<opcional>
```

El bot toma el rango del creador y publica una tarjeta con jugadores/cupos y botones **Unirme**, **Salir** y **Cerrar**. Los botones siguen funcionando después de reinicios.

### 🎫 Tickets y reportes
En `🤖・comandos`, el botón **Crear reporte** abre un canal privado visible solo para el usuario y staff. Evita tickets duplicados, incluye botón de cierre y registra apertura/cierre en `📋・reportes`.

### 📜 Logs
Canal: `📜・logs`.

Registra entradas/salidas, cambios de apodo/roles y creación/eliminación de canales y roles.

Los logs de contenido de mensajes son opcionales. Para activarlos, habilitar `MESSAGE CONTENT INTENT` en Discord Developer Portal y configurar:

```env
ENABLE_MESSAGE_LOGS=true
```

### 🔊 Salas de voz temporales
Canal: `➕・Crear sala`.

Al entrar se crea `🎮・Sala de <usuario>`, el usuario es movido, recibe controles y el canal se elimina cuando queda vacío.

### 💜 Aquí solo habla la streamer
Canal: `💜・aqui-solo-habla-la-streamer`.

La comunidad puede leer y reaccionar. `🎥・Streamer` puede publicar texto, imágenes y enlaces. Admin/Moderador tienen envío denegado por overwrite. Usuarios con `Administrator` global pueden saltar overwrites por funcionamiento de Discord.

## 🟣 Twitch automático

La versión nueva consulta la API oficial de Twitch automáticamente. Twitch solo se activa cuando existen estas variables en Northflank:

```env
TWITCH_CLIENT_ID=tu_client_id
TWITCH_CLIENT_SECRET=tu_client_secret
TWITCH_CHANNEL=nombre_del_canal_sin_@
```

Opcionales:

```env
STREAMER_DISCORD_ID=0
TWITCH_POLL_SECONDS=60
TWITCH_OFFLINE_DELETE_DELAY=300
```

Si `STREAMER_DISCORD_ID=0`, el bot busca a quien tenga `🎥・Streamer`.

### Cuando la streamer prende

El bot:

1. detecta que el canal de Twitch está live;
2. crea/comprueba `🔴・EN DIRECTO`;
3. asigna `🔴・EN DIRECTO` a la streamer;
4. crea `🔴・stream-en-vivo` en Comunidad;
5. publica en `🎥・directos` título, categoría, espectadores, miniatura y botón **Ver en Twitch**;
6. menciona `🔔・Avisos de directo`;
7. publica la tarjeta dentro del canal temporal;
8. cambia la presencia del bot a streaming;
9. evita anunciar dos veces el mismo stream incluso si el bot se reinicia.

### Cuando termina

El bot quita `🔴・EN DIRECTO`, restaura su presencia, avisa dentro de `🔴・stream-en-vivo`, espera `TWITCH_OFFLINE_DELETE_DELAY` (300 segundos = 5 minutos por defecto), vuelve a consultar Twitch y elimina el canal temporal solo si sigue offline.

Si vuelve a prender durante esa espera, el canal no se elimina.

### Comandos Twitch

```text
/actualizar-twitch
```

Crea/comprueba roles y panel de avisos, prueba Client ID/Secret, consulta el estado real del canal y sincroniza inmediatamente Discord.

```text
/twitch-estado
```

Muestra canal configurado, online/offline, existencia del canal temporal, estado del rol EN DIRECTO y frecuencia de consulta.

## 📌 Guías automáticas

`/actualizar-guias` crea o actualiza mensajes de guía sin duplicarlos en los canales principales.

## Comandos administrativos

### `/setup`
Instalación inicial completa. Crea/actualiza roles, categorías, canales, permisos, verificación, reaction roles, guías y tickets. No se recomienda para cambios pequeños.

### `/actualizar-canales`
Actualiza solo categorías, canales y permisos, incluido `💜・aqui-solo-habla-la-streamer`.

### `/actualizar-roles`
Actualiza países, rangos de Valorant, emojis personalizados y el panel `🔔 Avisos de directo`; también limpia paneles/reacciones antiguos.

### `/actualizar-guias`
Actualiza solo las guías.

### `/actualizar-tickets`
Actualiza solo la categoría/panel/reportes del sistema de tickets.

### `/actualizar-twitch`
Configura y prueba Twitch.

### `/twitch-estado`
Comprueba el estado actual de Twitch.

### `/party`
Comando de usuario para buscar grupo de Valorant en `🔎・busco-grupo`.

## Estructura esperada

```text
╭・📌 INFORMACIÓN
│・✅・verificación
│・📜・reglas
│・🎭・roles
│・📢・anuncios
│・🎥・directos
╰・💜・aqui-solo-habla-la-streamer

╭・💬 COMUNIDAD
│・💬・general
│・📸・multimedia
│・😂・memes
│・🤖・comandos
╰・🔴・stream-en-vivo      ← solo existe durante stream

╭・🎮 GAMING
│・🎮・gaming
│・🔫・valorant
╰・🔎・busco-grupo

╭・🔊 VOZ
│・🔊・General
│・🎮・Gaming
│・🔫・Valorant
╰・➕・Crear sala

╭・🛡️ STAFF
│・💬・staff
│・📋・reportes
╰・📜・logs

╭・🎫 TICKETS
╰・tickets privados temporales
```

## Roles

Staff:
- `👑・Owner`: Administrator.
- `💎・Co-Owner`: Administrator.
- `🛡️・Admin`: gestión/moderación sin Administrator global.
- `🔨・Moderador`: moderación.

Comunidad:
- `✅・Miembro`
- `🎥・Streamer`
- `💜・Subscriber`
- `⭐・VIP`
- `🔴・EN DIRECTO` — temporal mientras Twitch está live.
- `🔔・Avisos de directo` — optativo para recibir menciones.

Países y rangos son visuales y no tienen permisos administrativos.

## Jerarquía

`Server Setup` debe estar por encima de cualquier rol que necesite asignar/quitar: `✅・Miembro`, países, rangos, `🔴・EN DIRECTO` y `🔔・Avisos de directo`.

Puede permanecer debajo de `👑・Owner`; el código evita editar roles superiores al propio bot.

## Variables de entorno

```env
DISCORD_TOKEN=token_del_bot
GUILD_ID=id_del_servidor
ENABLE_MESSAGE_LOGS=false

TWITCH_CLIENT_ID=
TWITCH_CLIENT_SECRET=
TWITCH_CHANNEL=
STREAMER_DISCORD_ID=0
TWITCH_POLL_SECONDS=60
TWITCH_OFFLINE_DELETE_DELAY=300
```

Nunca subir secretos reales a GitHub.

## Health check / Northflank

Endpoints:

```text
GET /
GET /health
```

La nueva versión incluye en el JSON el estado de Discord y de la integración de Twitch.

El proceso se inicia con:

```text
python bot.py
```

## Archivos

```text
bot.py              Código principal
requirements.txt    Dependencias
Procfile             Comando de inicio
.env.example         Variables de ejemplo sin secretos
.gitignore           Ignora .env/temporales
README.md            Manual completo
```

## Flujo recomendado

- canales/permisos → `/actualizar-canales`
- emojis/roles → `/actualizar-roles`
- guías → `/actualizar-guias`
- tickets → `/actualizar-tickets`
- Twitch → `/actualizar-twitch`
- instalación completa → `/setup`

## Seguridad

- nunca publicar `DISCORD_TOKEN`;
- nunca publicar `TWITCH_CLIENT_SECRET`;
- `.env` debe permanecer fuera del repositorio;
- el bot no registra contenido de mensajes salvo habilitación explícita;
- los IDs de emojis de Valorant no necesitan guardarse porque se resuelven por nombre.
