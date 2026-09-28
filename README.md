# sync-log

Registro periódico de disponibilidad de un calendario público (cada ~10-20 min).

- `data/checks.csv`: una fila por consulta.
- `data/eventos.csv`: solo los cambios (calendario cargado o cambiado, hueco abierto, hueco tomado).
- Abre un issue cuando cambia el calendario cargado. Con la variable `ALERTAR_HUECOS=true` también avisa de cada hueco nuevo.
- Configuración en secrets: `API_BASE`, `OBJETIVOS` (`centro:servicio`).
