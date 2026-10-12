# sync-log

Registro periódico de disponibilidad de un calendario público (cada ~10-20 min).

- `data/checks.csv`: una fila por consulta.
- `data/eventos.csv`: solo los cambios (calendario cargado o cambiado, hueco abierto, hueco tomado).
- Abre un issue cuando cambia el calendario cargado. Con la variable `ALERTAR_HUECOS=true` también avisa de cada hueco nuevo.
- También abre un issue si no puede leer el calendario 6 veces seguidas (~1 h), cuando se recupera, y si la respuesta cambia de forma (claves distintas): en esos casos "sin agenda" puede no ser cierto.
- Configuración en secrets: `API_BASE`, `OBJETIVOS` (`centro:servicio`).
