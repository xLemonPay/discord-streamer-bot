# Discord Streamer Bot — versión definitiva actual

Bot personalizado para el servidor de una streamer con comunidad gaming y foco principal en Valorant.
Está preparado para alojarse 24/7 (por ejemplo en Northflank) y mantener las funciones interactivas sin depender de una PC encendida.

> **Estado de Twitch:** la automatización de Twitch (crear un canal temporal al prender stream y asignar `🔴・EN DIRECTO`) fue diseñada/conversada, pero **NO está activada en esta versión** porque se decidió dejarla para una etapa posterior.

## Funciones actuales

### ✅ Verificación
- Canal: `✅・verificación`.
- El bot publica un botón **Verificarme**.
- Al pulsarlo asigna `✅・Miembro`.
- `✅・Miembro` desbloquea Comunidad, Gaming y canales de voz.
- La vista es persistente: sigue funcionando después de reinicios del bot.

### 🎭 Reaction roles
Canal: `🎭・roles`.

Hay dos paneles:

**País**
- Se elige reaccionando con una bandera.
- Solo se conserva un país por usuario.
- Si se reacciona a otro país, el bot quita el anterior y deja el nuevo.
- Si se quita la reacción, se quita el rol.

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

**Rango de Valorant**
- Se elige reaccionando con el icono del rango.
- Solo se conserva un rango por usuario.
- Cambiar la reacción reemplaza el rango anterior.
- Quitar la reacción quita el rol.

El bot busca automáticamente estos emojis personalizados del servidor **por nombre**, por lo que no hace falta guardar IDs:

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

Si falta un emoji personalizado, `/actualizar-roles` usa temporalmente el emoji normal correspondiente y avisa cuál falta.

### 🔎 Buscar grupo de Valorant
Canal: `🔎・busco-grupo`.

Comando:

```text
/party modo:<modo> cupos:<1-4> servidor:<opcional>
```

Ejemplo:

```text
/party modo:Competitivo cupos:4 servidor:Santiago
```

El bot:
- toma automáticamente el rango visual del creador desde `🎭・roles`;
- crea una tarjeta de búsqueda;
- muestra jugadores actuales y cupos;
- ofrece **✅ Unirme**;
- ofrece **🚪 Salir**;
- ofrece **🔒 Cerrar** al creador/staff;
- marca el grupo como completo cuando llega al máximo;
- conserva los botones después de reinicios.

Modos disponibles incluyen Competitivo, Swiftplay, No competitivo, Premier, Deathmatch/TDM y Otro.

### 🎫 Tickets y reportes
En `🤖・comandos` el bot mantiene un panel **🎫 Soporte y reportes**.

Al tocar **Crear reporte**:
- crea un canal privado;
- solo lo ve el usuario y el staff;
- evita que el mismo usuario abra varios tickets a la vez;
- incluye botón para cerrar;
- al cerrar guarda un registro en `📋・reportes`.

La categoría `╭・🎫 TICKETS` permanece oculta para miembros normales.

### 📜 Logs
Canal privado de staff: `📜・logs`.

Registra, entre otros:
- entrada y salida de miembros;
- cambios de apodo;
- cambios de roles;
- creación/eliminación de canales;
- creación/eliminación de roles.

Los logs de **contenido** de mensajes editados/eliminados son opcionales y están desactivados por defecto.

Para activarlos:
1. Discord Developer Portal → aplicación → Bot.
2. Activar `MESSAGE CONTENT INTENT`.
3. En Northflank agregar `ENABLE_MESSAGE_LOGS=true`.
4. Reiniciar/redeployar el servicio.

### 🔊 Salas de voz temporales
Canal: `➕・Crear sala`.

Cuando un usuario entra:
- se crea `🎮・Sala de <usuario>`;
- el usuario es movido automáticamente;
- el creador obtiene controles sobre su sala;
- cuando queda vacía, el bot la elimina.

### 💜 Aquí solo habla la streamer
Canal: `💜・aqui-solo-habla-la-streamer`.

- La comunidad puede verlo, leer y reaccionar.
- El rol `🎥・Streamer` puede publicar texto, imágenes y enlaces.
- Admin y Moderador quedan con envío denegado por overwrite del canal.

**Limitación de Discord:** un usuario que tenga el permiso global `Administrator` siempre puede saltarse los overwrites de los canales. Por eso Owner/Co-Owner con Administrator técnicamente pueden escribir aunque el canal esté pensado para que solo publique la streamer.

### 📌 Guías automáticas
El bot puede colocar/actualizar una guía breve en cada canal para explicar su uso sin duplicar mensajes.

Incluye guías para:
- reglas;
- anuncios;
- directos;
- canal personal de la streamer;
- general;
- multimedia;
- memes;
- comandos/soporte;
- gaming;
- Valorant;
- buscar grupo;
- staff;
- reportes;
- logs.

## Comandos de administración

### `/setup`
**Solo instalación inicial completa.**

Crea/actualiza de una vez:
- roles base;
- roles visuales;
- categorías;
- canales;
- permisos;
- verificación;
- reaction roles;
- guías;
- tickets;
- estructura de Valorant.

No se recomienda usarlo para cambios pequeños porque hace muchas operaciones de Discord.

### `/actualizar-canales`
Actualiza únicamente:
- categorías principales;
- canales de texto;
- canales de voz;
- permisos;
- `💜・aqui-solo-habla-la-streamer`.

No reconstruye reaction roles, guías ni tickets.

### `/actualizar-roles`
Actualiza únicamente `🎭・roles`:
- limpia paneles viejos/duplicados;
- sincroniza banderas;
- detecta emojis personalizados de Valorant por nombre;
- elimina reacciones que ya no corresponden;
- agrega las nuevas.

Muestra progreso durante el proceso para evitar quedarse simplemente en “pensando”.

### `/actualizar-guias`
Actualiza únicamente los mensajes de guía de los canales existentes.
No toca roles ni permisos.

### `/actualizar-tickets`
Actualiza únicamente:
- categoría privada de tickets;
- panel de Crear reporte;
- canal interno `📋・reportes`.

### `/party`
Comando de usuario para buscar grupo de Valorant. Debe usarse en `🔎・busco-grupo`.

## Estructura esperada del servidor

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
╰・🤖・comandos

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

## Roles y permisos

### Staff
- `👑・Owner`: Administrator.
- `💎・Co-Owner`: Administrator.
- `🛡️・Admin`: gestión de servidor/canales/roles y moderación, sin Administrator global.
- `🔨・Moderador`: moderación de usuarios, mensajes y voz.

### Comunidad
- `✅・Miembro`: rol de acceso después de verificarse.
- `🎥・Streamer`: identidad de la streamer y permiso de publicación en sus canales.
- `💜・Subscriber`: visual.
- `⭐・VIP`: visual.

### Países y Valorant
Son roles visuales con **0 permisos administrativos**.

## Jerarquía del bot
El rol del bot (`Server Setup`) debe estar por encima de cualquier rol que necesite asignar o modificar, especialmente:
- `✅・Miembro`;
- países;
- rangos de Valorant.

El código evita intentar editar roles que estén por encima del propio bot para que una jerarquía como `👑・Owner > Server Setup` no rompa todo el proceso.

## Variables de entorno

Obligatorias:

```env
DISCORD_TOKEN=token_del_bot
GUILD_ID=id_del_servidor
```

Opcional:

```env
ENABLE_MESSAGE_LOGS=false
```

En Northflank las variables se cargan desde **Environment**. Nunca subas el token de Discord al repositorio.

`PORT` es leído automáticamente del proveedor de hosting; localmente el bot usa `8000` si no existe.

## Health check / hosting
El bot levanta:

```text
GET /
GET /health
```

`/health` devuelve un JSON indicando si el proceso y la conexión con Discord están activos.

El proceso se inicia con:

```text
python bot.py
```

El `Procfile` incluido contiene ese comando.

## Archivos del proyecto

```text
bot.py              Código principal
requirements.txt    Dependencias Python
Procfile             Comando de inicio para hosting
.env.example         Ejemplo de variables, sin secretos
.gitignore           Evita subir .env y archivos temporales
README.md            Documentación completa
```

## Flujo recomendado para cambios

Después de actualizar `bot.py` en GitHub y esperar a que Northflank vuelva a `Running`:

- cambiaste canales/permisos → `/actualizar-canales`;
- cambiaste emojis/roles visuales → `/actualizar-roles`;
- cambiaste textos/guías → `/actualizar-guias`;
- cambiaste tickets → `/actualizar-tickets`;
- `/setup` solo para una instalación completa.

## Twitch — pendiente, no activo
Está previsto para una etapa posterior:
- detectar `stream.online` / `stream.offline` mediante Twitch EventSub;
- crear `🔴・stream-en-vivo` solo mientras la streamer esté live;
- asignar temporalmente `🔴・EN DIRECTO`;
- publicar un aviso permanente en `🎥・directos`;
- quitar el rol y eliminar/cerrar el canal temporal al terminar.

Nada de esto se ejecuta todavía en esta versión.

## Seguridad
- Nunca publiques `DISCORD_TOKEN`.
- `.env` está ignorado por Git.
- No es necesario guardar IDs de los emojis de Valorant: el bot los busca por nombre.
- El contenido de mensajes no se registra salvo que se habilite explícitamente `MESSAGE CONTENT INTENT` y `ENABLE_MESSAGE_LOGS=true`.
