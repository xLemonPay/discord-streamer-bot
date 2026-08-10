# Discord Streamer Bot v4

## Cambios principales

- `🎭・roles` ahora usa **reaction roles**.
- País: reaccionás con la bandera.
- Valorant: reaccionás con el emoji del rango.
- Solo se mantiene un país y un rango por usuario.
- Al quitar la reacción, se quita el rol.
- `/setup` elimina el panel viejo con menús desplegables y crea los nuevos paneles.
- Se agregan guías automáticas en los canales de texto para explicar qué se hace en cada uno.
- Se conserva `/party` en `🔎・busco-grupo`.
- Se conservan tickets, logs, verificación y salas temporales.

## Después del deploy

Cuando Northflank vuelva a mostrar `Running`, ejecutá:

`/setup`

una sola vez para migrar los paneles y crear/actualizar las guías.
