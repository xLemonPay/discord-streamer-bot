# Discord Streamer Bot — versión definitiva con Twitch

Bot personalizado para una comunidad de streamer con foco gaming/Valorant. Está pensado para ejecutarse 24/7 en Northflank y mantener verificación, roles, tickets, grupos, salas temporales y avisos automáticos de Twitch.

## Estado actual

Funciones activas en el código:

- verificación por botón;
- reaction roles de país;
- reaction roles de rango de Valorant con emojis personalizados;
- rol optativo `🔔・Avisos de directo`;
- búsqueda de grupo con `/party`;
- tickets/reportes privados;
- logs;
- salas de voz temporales;
- canal `💜・aqui-solo-habla-la-streamer`;
- guías automáticas;
- Twitch automático;
- health check para hosting.

> Twitch queda habilitado únicamente cuando `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` y `TWITCH_CHANNEL` están configurados en Northflank. Si faltan, el resto del bot funciona normalmente.

---

## 1. Verificación

Canal: `✅・verificación`

El bot publica un botón **Verificarme**. Al pulsarlo asigna `✅・Miembro`, que desbloquea Comunidad, Gaming y Voz. El botón es persistente y sigue funcionando después de reinicios.

---

## 2. Reaction roles

Canal: `🎭・roles`

### País

El usuario reacciona con una bandera. Solo puede conservar un país; elegir otro reemplaza el anterior. Quitar la reacción quita el rol.

Países incluidos:

- 🇵🇾 Paraguay
- 🇦🇷 Argentina
- 🇧🇷 Brasil
- 🇺🇾 Uruguay
- 🇨🇱 Chile
- 🇧🇴 Bolivia
- 🇵🇪 Perú
- 🇨🇴 Colombia
- 🇻🇪 Venezuela
- 🇪🇨 Ecuador
- 🇲🇽 México
- 🇪🇸 España
- 🌎 Otro

### Rango de Valorant

El usuario reacciona con el icono de su rango. Solo puede conservar un rango. Cambiar reacción reemplaza el anterior; quitarla quita el rol.

El bot busca estos emojis personalizados **por nombre**, sin guardar IDs:

- `valoranthierro`
- `valorantbronce`
- `valorantplata`
- `valorantoro`
- `valorantplatino`
- `valorantdiamante`
- `valorantascendente`
- `valorantimmortal`
- `valorantradiante`

`Sin rango` usa `⚫`.

Si falta un emoji, `/actualizar-roles` usa temporalmente el emoji normal correspondiente y avisa cuál falta.

### Avisos de directo

Panel: `🔔 Avisos de directo`

- reaccionar con 🔔 asigna `🔔・Avisos de directo`;
- quitar la reacción quita el rol;
- cuando empieza un stream, el bot menciona a ese rol en `🎥・directos`.

---

## 3. Buscar grupo de Valorant

Canal: `🔎・busco-grupo`

Comando:

```text
/party modo:<modo> cupos:<1-4> servidor:<opcional>
```

Ejemplo:

```text
/party modo:Competitivo cupos:4 servidor:Santiago
```

El bot toma el rango del creador, crea una tarjeta, muestra cupos y ofrece:

- `✅ Unirme`
- `🚪 Salir`
- `🔒 Cerrar`

Los botones son persistentes después de reinicios.

---

## 4. Tickets y reportes

Canal de panel: `🤖・comandos`

El botón **Crear reporte**:

- crea un canal privado;
- solo lo ve el usuario y staff;
- evita múltiples tickets simultáneos del mismo usuario;
- incluye botón de cierre;
- registra apertura/cierre en `📋・reportes`.

Categoría: `╭・🎫 TICKETS`.

---

## 5. Logs

Canal: `📜・logs`

Registra:

- entradas/salidas;
- cambios de apodo;
- cambios de roles;
- creación/eliminación de canales;
- creación/eliminación de roles.

Los logs de contenido de mensajes editados/eliminados son opcionales.

Para habilitarlos:

1. Discord Developer Portal → aplicación → Bot.
2. Activar `MESSAGE CONTENT INTENT`.
3. Northflank: `ENABLE_MESSAGE_LOGS=true`.
4. Reiniciar el servicio.

---

## 6. Salas de voz temporales

Canal: `➕・Crear sala`

Al entrar:

- crea `🎮・Sala de <usuario>`;
- mueve al creador;
- le da controles sobre su sala;
- cuando queda vacía, la elimina.

---

## 7. Canal personal de la streamer

Canal: `💜・aqui-solo-habla-la-streamer`

- miembros: leen y reaccionan;
- `🎥・Streamer`: publica texto, imágenes y enlaces;
- Admin/Moderador tienen envío denegado por overwrite.

Discord permite que usuarios con `Administrator` global salten overwrites, por lo que Owner/Co-Owner técnicamente pueden escribir.

---

## 8. Twitch automático

La integración consulta la API oficial de Twitch cada `TWITCH_POLL_SECONDS` segundos (60 por defecto).

Usa un **App Access Token** obtenido automáticamente mediante Client Credentials. No hay que pegar manualmente un token OAuth de la streamer.

### Variables necesarias

```env
TWITCH_CLIENT_ID=tu_client_id
TWITCH_CLIENT_SECRET=tu_client_secret
TWITCH_CHANNEL=nombre_del_canal_sin_@
```

### Variables opcionales

```env
STREAMER_DISCORD_ID=0
TWITCH_POLL_SECONDS=60
TWITCH_OFFLINE_DELETE_DELAY=300
```

Si `STREAMER_DISCORD_ID=0`, el bot localiza a quien tenga `🎥・Streamer`.

### Cuando la streamer prende Twitch

El bot automáticamente:

1. detecta que `TWITCH_CHANNEL` está live;
2. crea/comprueba `🔴・EN DIRECTO`;
3. asigna `🔴・EN DIRECTO` a la streamer;
4. crea `🔴・stream-en-vivo` en Comunidad;
5. publica en `🎥・directos` título, categoría, espectadores, miniatura y botón **Ver en Twitch**;
6. menciona `🔔・Avisos de directo`;
7. publica la tarjeta dentro del canal temporal;
8. cambia la presencia del bot a estado de streaming;
9. evita anunciar dos veces el mismo stream, incluso después de un reinicio.

### Cuando termina

El bot:

1. quita `🔴・EN DIRECTO`;
2. restaura su presencia;
3. avisa dentro de `🔴・stream-en-vivo` que terminó;
4. espera `TWITCH_OFFLINE_DELETE_DELAY` segundos (300 = 5 minutos por defecto);
5. vuelve a consultar Twitch;
6. si sigue offline, elimina `🔴・stream-en-vivo`.

Si vuelve a prender durante la espera, el canal no se elimina.

### Comandos Twitch

```text
/actualizar-twitch
```

- crea/comprueba roles de Twitch;
- crea/comprueba panel 🔔;
- prueba Client ID/Secret;
- consulta el estado real del canal;
- sincroniza inmediatamente rol/canal si ya está live;
- arranca el watcher si todavía no estaba corriendo.

```text
/twitch-estado
```

Muestra:

- canal configurado;
- online/offline;
- existencia del canal temporal;
- estado del rol EN DIRECTO;
- frecuencia de consulta.

---

## 9. Guías automáticas

`/actualizar-guias` coloca o edita una guía breve sin duplicarla en los canales principales: reglas, anuncios, directos, canal personal, general, multimedia, memes, comandos, gaming, Valorant, buscar grupo, staff, reportes y logs.

---

## 10. Comandos administrativos

### `/setup`

Instalación inicial completa. Crea/actualiza roles base, roles visuales, categorías, canales, permisos, verificación, reaction roles, guías y tickets.

**No usar para cambios pequeños.**

### `/actualizar-canales`

Actualiza solo categorías, canales y permisos, incluido `💜・aqui-solo-habla-la-streamer`.

### `/actualizar-roles`

Actualiza:

- países;
- rangos de Valorant;
- emojis personalizados;
- panel `🔔 Avisos de directo`;
- elimina paneles/reacciones antiguos que ya no corresponden.

### `/actualizar-guias`

Actualiza solo mensajes guía.

### `/actualizar-tickets`

Actualiza solo categoría/panel/reportes de tickets.

### `/actualizar-twitch`

Configura y prueba Twitch.

### `/twitch-estado`

Comprueba el estado actual de Twitch.

### `/party`

Comando de usuario para buscar grupo de Valorant.

---

## 11. Estructura del servidor

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

---

## 12. Roles

### Staff

- `👑・Owner`: Administrator.
- `💎・Co-Owner`: Administrator.
- `🛡️・Admin`: gestión/moderación sin Administrator global.
- `🔨・Moderador`: moderación.

### Comunidad

- `✅・Miembro`
- `🎥・Streamer`
- `💜・Subscriber`
- `⭐・VIP`
- `🔴・EN DIRECTO` — temporal mientras Twitch está live.
- `🔔・Avisos de directo` — optativo para recibir menciones.

Países y rangos de Valorant son visuales y no tienen permisos administrativos.

---

## 13. Jerarquía del bot

`Server Setup` debe estar por encima de cualquier rol que necesite asignar/quitar:

- `✅・Miembro`;
- países;
- rangos;
- `🔴・EN DIRECTO`;
- `🔔・Avisos de directo`.

Puede permanecer debajo de `👑・Owner`: el código evita editar roles superiores al propio bot.

---

## 14. Variables de entorno completas

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

---

## 15. Health check / Northflank

Endpoints:

```text
GET /
GET /health
```

El JSON incluye:

- `discord_ready`;
- nombre del bot;
- `twitch_enabled`;
- `twitch_channel`;
- `twitch_watcher_running`.

El proceso se inicia con:

```text
python bot.py
```

---

## 16. Archivos

```text
bot.py              Código principal
requirements.txt    Dependencias
Procfile             Comando de inicio
.env.example         Variables de ejemplo sin secretos
.gitignore           Ignora .env/temporales
README.md            Manual completo
```

---

## 17. Flujo recomendado de mantenimiento

- canales/permisos → `/actualizar-canales`
- emojis/roles → `/actualizar-roles`
- guías → `/actualizar-guias`
- tickets → `/actualizar-tickets`
- Twitch → `/actualizar-twitch`
- instalación completa → `/setup`

---

## Seguridad

- nunca publicar `DISCORD_TOKEN`;
- nunca publicar `TWITCH_CLIENT_SECRET`;
- `.env` debe permanecer fuera del repositorio;
- el bot no registra contenido de mensajes salvo que se habilite expresamente;
- los IDs de emojis de Valorant no necesitan guardarse porque se resuelven por nombre.
