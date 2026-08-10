# Discord Streamer Bot v2

Novedades de esta versión:

- `🎭・roles`: canal dedicado para elegir país y rango de Valorant.
- Migra/elimina el panel viejo de roles de `🤖・comandos` al ejecutar `/setup`.
- `🎫 Soporte y reportes`: botón en `🤖・comandos`.
- Cada reporte crea un canal privado visible solo para el usuario y el staff.
- Botón para cerrar reportes.
- `📋・reportes`: registra apertura/cierre de tickets.
- `📜・logs`: entradas, salidas, cambios de roles/apodos, creación/eliminación de canales y roles.
- Logs opcionales de mensajes editados/eliminados.

## IMPORTANTE: logs de mensajes

Por seguridad, los logs del contenido de mensajes vienen apagados por defecto.

Para activarlos:
1. Discord Developer Portal > tu aplicación > Bot.
2. Activá `MESSAGE CONTENT INTENT`.
3. En Northflank, agregá:
   `ENABLE_MESSAGE_LOGS=true`
4. Reiniciá/redeployá el servicio.

Si no activás esa variable, el resto del bot funciona normalmente.

## Después de desplegar

Esperá a que Northflank muestre el servicio como Running y ejecutá una sola vez:

`/setup`

No borra los canales/roles existentes. Crea o actualiza solamente lo necesario.
