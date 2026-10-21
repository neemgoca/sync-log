"""Registro periódico de disponibilidad de un calendario.

Cada ejecución consulta el calendario configurado, compara con el estado
anterior y registra los cambios:
  data/checks.csv   una fila por consulta (sirve para saber que el monitor corrió)
  data/eventos.csv  una fila por cambio (agenda cargada, hueco abierto/tomado)
  data/state.json   último estado visto
Si hay un evento que merece aviso, escribe data/alerta.md para que el workflow
abra un issue (GitHub avisa por correo).
"""
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Configuración por secrets: API_BASE y OBJETIVOS ("centro:servicio,centro:servicio")
API = os.environ.get("API_BASE", "").rstrip("/")
OBJETIVOS = [
    (int(c), int(s), chr(65 + i))
    for i, (c, s) in enumerate(x.split(":") for x in os.environ.get("OBJETIVOS", "").split(",") if x)
]
ALERTAR_HUECOS = os.environ.get("ALERTAR_HUECOS", "false").lower() == "true"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
MADRID = ZoneInfo("Europe/Madrid")
DIAS_SEMANA = {1: "lun", 2: "mar", 4: "mié", 8: "jue", 16: "vie", 32: "sáb", 64: "dom"}
ERRORES_PARA_AVISAR = 6  # consultas fallidas seguidas (~1 h) antes de abrir un issue
# Push al celular con la app ntfy: el topic va como secret porque quien lo sepa puede leer los avisos.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
NTFY_URL = os.environ.get("NTFY_URL", "https://ntfy.sh/")
PROBAR_PUSH = os.environ.get("PROBAR_PUSH", "false").lower() == "true"
REPETIR_PUSH = "1m"  # ntfy reenvía cada aviso a este plazo, por si el primero no llega al celular


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def mascara(d):
    return "+".join(n for b, n in DIAS_SEMANA.items() if d & b) or str(d)


def consultar(centro, servicio):
    cal = get_json(f"{API}/disponible/centro/{centro}/servicio/{servicio}/calendario")
    if not isinstance(cal, dict):
        raise ValueError(f"la respuesta no es un objeto JSON ({type(cal).__name__})")
    periodos = sorted(
        f"{p['fecha_inicial']}→{p['fecha_final']} ({mascara(p['dias'])})" for p in cal.get("periodos", [])
    )
    huecos = []
    for d in cal.get("dias", []):
        if d.get("estado") == 0:
            f = datetime.strptime(d["dia"], "%Y-%m-%d").strftime("%m%d%Y")
            time.sleep(1)  # sin prisa: no cargar el servidor
            for s in get_json(f"{API}/disponible/horas/centro/{centro}/servicio/{servicio}/fecha/{f}"):
                huecos.append(f"{d['dia']} {s['hora_cita'][:5]}")
    ultimo = cal["dias"][-1]["dia"] if cal.get("dias") else ""
    # Las claves de la respuesta: si cambian, "vacío" puede ser que ya no sepamos leerla.
    return {"periodos": periodos, "huecos": sorted(set(huecos)), "ultimo_dia": ultimo, "claves": sorted(cal)}


def push(titulo, texto, prioridad):
    if not NTFY_TOPIC:
        return
    body = {"topic": NTFY_TOPIC, "title": titulo, "message": texto.replace("**", "").replace("`", ""),
            "priority": prioridad, "tags": ["calendar"]}
    for extra in ({}, {"title": f"{titulo} (repetido)", "delay": REPETIR_PUSH}):
        req = urllib.request.Request(NTFY_URL, data=json.dumps(body | extra).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=15).read()
        except Exception as e:  # el issue sigue siendo el aviso de respaldo
            print(f"No se pudo enviar el push: {e}", file=sys.stderr)


def para_reservar(huecos):
    if not huecos:
        return ""
    lista = "\n".join(f"- {h}" for h in huecos[:15]) + (f"\n- … y {len(huecos) - 15} más" if len(huecos) > 15 else "")
    return f"\n\nHuecos libres:\n{lista}\n\nPara reservar, escribe en Claude: `reserva {huecos[0]}` (o la fecha y hora que prefieras)."


def append(path, header, rows):
    nuevo = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if nuevo:
            w.writerow(header)
        w.writerows(rows)


def main():
    if not API or not OBJETIVOS:
        print("Sin configuración (faltan secrets API_BASE / OBJETIVOS); no se consulta nada.")
        return 0
    os.makedirs(DATA, exist_ok=True)
    state_path = os.path.join(DATA, "state.json")
    state = json.load(open(state_path, encoding="utf-8")) if os.path.exists(state_path) else {}
    now = datetime.now(timezone.utc)
    ts_utc = now.strftime("%Y-%m-%d %H:%M")
    local = now.astimezone(MADRID)
    ts_mad = local.strftime("%Y-%m-%d %H:%M")
    dia_sem = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"][local.weekday()]

    checks, eventos, alertas, pushes = [], [], [], []

    def avisar(texto, titulo="Aviso del monitor", prioridad=4, issue=True):
        if issue:
            alertas.append(texto)
        pushes.append((titulo, texto, prioridad))

    if PROBAR_PUSH:
        avisar("Prueba: si ves esto, los avisos del monitor te llegan al celular.", "Prueba del monitor", 3, issue=False)
    errores = state.setdefault("_errores", {})
    for centro, servicio, etiqueta in OBJETIVOS:
        key = f"{centro}-{servicio}"
        try:
            snap = consultar(centro, servicio)
        except Exception as e:  # red caída, 500, respuesta rara, etc.
            checks.append([ts_utc, ts_mad, dia_sem, etiqueta, "error", "", "", str(e)[:200]])
            err = errores.setdefault(key, {"seguidos": 0, "desde": ts_mad})
            err["seguidos"] += 1
            if err["seguidos"] == ERRORES_PARA_AVISAR:
                avisar(f"**{etiqueta}**: el monitor no puede leer el calendario "
                       f"({err['seguidos']} consultas fallidas seguidas desde {err['desde']}, hora de Madrid).\n\n"
                       f"Último error: `{str(e)[:200]}`\n\n"
                       f"Mientras siga así, no vas a recibir avisos de agenda nueva ni de huecos.")
            continue
        err = errores.pop(key, None)
        if err and err["seguidos"] >= ERRORES_PARA_AVISAR:
            avisar(f"**{etiqueta}**: el monitor vuelve a leer el calendario ({ts_mad} hora de Madrid), "
                   f"tras {err['seguidos']} consultas fallidas desde {err['desde']}.")
        prev = state.get(key)
        checks.append([ts_utc, ts_mad, dia_sem, etiqueta, "ok", len(snap["huecos"]), snap["ultimo_dia"], ""])
        if prev is None:
            eventos.append([ts_utc, ts_mad, dia_sem, etiqueta, "inicio", "", "; ".join(snap["periodos"]) + f" | huecos={len(snap['huecos'])}"])
        else:
            if "claves" in prev and snap["claves"] != prev["claves"]:
                eventos.append([ts_utc, ts_mad, dia_sem, etiqueta, "respuesta_cambiada", "",
                                "ANTES: " + ",".join(prev["claves"]) + " | AHORA: " + ",".join(snap["claves"])])
                avisar(f"**{etiqueta}**: la respuesta del calendario cambió de forma ({ts_mad} hora de Madrid). "
                       f"Puede que el monitor ya no la lea bien y que \"sin agenda\" no sea cierto.\n\n"
                       f"- Antes: {', '.join(prev['claves']) or '(nada)'}\n"
                       f"- Ahora: {', '.join(snap['claves']) or '(nada)'}")
            if snap["periodos"] != prev["periodos"]:
                eventos.append([ts_utc, ts_mad, dia_sem, etiqueta, "agenda_cambiada", "",
                                "ANTES: " + "; ".join(prev["periodos"]) + " | AHORA: " + "; ".join(snap["periodos"])])
                avisar(f"**{etiqueta}**: cambió el calendario cargado ({ts_mad} hora de Madrid).\n\n"
                       f"- Antes: {'; '.join(prev['periodos']) or '(nada)'}\n"
                       f"- Ahora: {'; '.join(snap['periodos']) or '(nada)'}\n"
                       f"- Huecos libres ahora: {len(snap['huecos'])}"
                       + para_reservar(snap["huecos"]), "Cambió la agenda")
            abiertos = sorted(set(snap["huecos"]) - set(prev["huecos"]))
            tomados = sorted(set(prev["huecos"]) - set(snap["huecos"]))
            for h in abiertos:
                eventos.append([ts_utc, ts_mad, dia_sem, etiqueta, "hueco_abierto", h, ""])
            for h in tomados:
                eventos.append([ts_utc, ts_mad, dia_sem, etiqueta, "hueco_tomado_o_vencido", h, ""])
            if abiertos:  # al celular siempre; issue solo con ALERTAR_HUECOS
                avisar(f"**{etiqueta}**: {len(abiertos)} hueco(s) nuevo(s) ({ts_mad})." + para_reservar(abiertos),
                       "Hueco libre", 5, issue=ALERTAR_HUECOS)
        state[key] = snap

    append(os.path.join(DATA, "checks.csv"),
           ["utc", "madrid", "dia_semana", "objetivo", "resultado", "huecos_libres", "ultimo_dia_agenda", "error"], checks)
    if eventos:
        append(os.path.join(DATA, "eventos.csv"),
               ["utc", "madrid", "dia_semana", "objetivo", "evento", "hueco", "detalle"], eventos)
    json.dump(state, open(state_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for titulo, texto, prioridad in pushes:
        push(titulo, texto, prioridad)
    if alertas:
        # "a": en la ventana del viernes varias consultas seguidas comparten un solo issue al final
        with open(os.path.join(DATA, "alerta.md"), "a", encoding="utf-8") as f:
            f.write("\n\n".join(alertas) + "\n\n")
    print(json.dumps({"checks": checks, "eventos": eventos, "alertas": len(alertas)}, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
