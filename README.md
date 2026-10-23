# sync-log

Registro periódico de disponibilidad de un calendario público (cada ~10-20 min).

- `data/checks.csv`: una fila por consulta.
- `data/eventos.csv`: solo los cambios (calendario cargado o cambiado, hueco abierto, hueco tomado).
- Abre un issue cuando cambia el calendario cargado. Con la variable `ALERTAR_HUECOS=true` también avisa de cada hueco nuevo.
- También abre un issue si no puede leer el calendario 6 veces seguidas (~1 h), cuando se recupera, y si la respuesta cambia de forma (claves distintas): en esos casos "sin agenda" puede no ser cierto.
- Con el secret `NTFY_TOPIC` manda además cada aviso y cada hueco nuevo al celular (app ntfy, suscrita a ese topic), dos veces: la segunda, marcada "(repetido)", un minuto después. Para probarlo: Actions → sync → Run workflow → marcar "Mandar una notificación de prueba".
- Los viernes de 14:00 a 14:15 (hora de Madrid) consulta cada 20 s, porque la oficina suele abrir citas hacia las 14:00 y vuelan en segundos. Para probar ese modo: Run workflow con "Consultar cada 20 s durante estos minutos".
- Configuración en secrets: `API_BASE`, `OBJETIVOS` (`centro:servicio`).
