# Discord Streamer Bot — versión Koyeb

Esta versión conserva las funciones del bot original y agrega un pequeño servidor HTTP:

- `GET /`
- `GET /health`

El endpoint responde HTTP 200 mientras el proceso está vivo. Esto permite desplegar el bot
como Web Service en Koyeb y monitorizarlo con un servicio de uptime.

## Funciones que se conservan

- `/setup`
- botón `✅ Verificarme`
- asignación del rol `✅・Miembro`
- selector visual de país
- selector visual de rango de Valorant
- salas temporales al entrar en `➕・Crear sala`
- borrado automático de las salas temporales vacías
- todos los roles, canales y permisos ya creados

No vuelvas a ejecutar `/setup` por obligación al migrar. El servidor de Discord ya conserva
los canales, roles y permisos. Solo usalo si realmente querés que el bot vuelva a comprobar
o actualizar la estructura.

## Variables necesarias en Koyeb

Creá estas variables en Koyeb:

- `DISCORD_TOKEN` = token real del bot
- `GUILD_ID` = ID real del servidor

NO subas `.env` a GitHub. `.gitignore` ya está preparado para ignorarlo.

Koyeb proporciona `PORT` automáticamente a los Web Services, así que no hace falta crearlo.

## Arranque

El `Procfile` ya contiene:

    web: python bot.py

## Prueba local opcional

Con tu `.env` local:

    py -m pip install -r requirements.txt
    py bot.py

Después podés abrir:

    http://127.0.0.1:8000/health

## Migración PC -> Koyeb

Podés dejar el bot local encendido mientras Koyeb está construyendo, pero cuando la instancia
de Koyeb esté funcionando correctamente, cerrá el proceso local. No conviene mantener dos
copias del mismo bot/token ejecutándose de forma permanente.

## UptimeRobot

Cuando Koyeb te entregue una URL pública como:

    https://tu-app.koyeb.app

creá un monitor HTTP(S) para:

    https://tu-app.koyeb.app/health

Un HTTP 200 indica que el proceso está vivo. El JSON además muestra `discord_ready`.
