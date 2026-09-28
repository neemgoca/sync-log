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


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def mascara(d):
    return "+".join(n for b, n in DIAS_SEMANA.items() if d & b) or str(d)


def consultar(centro, servicio):
    cal = get_json(f"{API}/disponible/centro/{centro}/servicio/{servicio}/calendario")
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
    return {"periodos": periodos, "huecos": sorted(set(huecos)), "ultimo_dia": ultimo}


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

    checks, eventos, alertas = [], [], []
    for centro, servicio, etiqueta in OBJETIVOS:
        key = f"{centro}-{servicio}"
        try:
            snap = consultar(centro, servicio)
        except Exception as e:  # red caída, 500, etc.
            checks.append([ts_utc, ts_mad, dia_sem, etiqueta, "error", "", "", str(e)[:200]])
            continue
        prev = state.get(key)
        checks.append([ts_utc, ts_mad, dia_sem, etiqueta, "ok", len(snap["huecos"]), snap["ultimo_dia"], ""])
        if prev is None:
            eventos.append([ts_utc, ts_mad, dia_sem, etiqueta, "inicio", "", "; ".join(snap["periodos"]) + f" | huecos={len(snap['huecos'])}"])
        else:
            if snap["periodos"] != prev["periodos"]:
                eventos.append([ts_utc, ts_mad, dia_sem, etiqueta, "agenda_cambiada", "",
                                "ANTES: " + "; ".join(prev["periodos"]) + " | AHORA: " + "; ".join(snap["periodos"])])
                alertas.append(f"**{etiqueta}**: cambió el calendario cargado ({ts_mad} hora de Madrid).\n\n"
                               f"- Antes: {'; '.join(prev['periodos']) or '(nada)'}\n"
                               f"- Ahora: {'; '.join(snap['periodos']) or '(nada)'}\n"
                               f"- Huecos libres ahora: {len(snap['huecos'])}"
                               + para_reservar(snap["huecos"]))
            abiertos = sorted(set(snap["huecos"]) - set(prev["huecos"]))
            tomados = sorted(set(prev["huecos"]) - set(snap["huecos"]))
            for h in abiertos:
                eventos.append([ts_utc, ts_mad, dia_sem, etiqueta, "hueco_abierto", h, ""])
            for h in tomados:
                eventos.append([ts_utc, ts_mad, dia_sem, etiqueta, "hueco_tomado_o_vencido", h, ""])
            if ALERTAR_HUECOS and abiertos:
                alertas.append(f"**{etiqueta}**: {len(abiertos)} hueco(s) nuevo(s) ({ts_mad})." + para_reservar(abiertos))
        state[key] = snap

    append(os.path.join(DATA, "checks.csv"),
           ["utc", "madrid", "dia_semana", "objetivo", "resultado", "huecos_libres", "ultimo_dia_agenda", "error"], checks)
    if eventos:
        append(os.path.join(DATA, "eventos.csv"),
               ["utc", "madrid", "dia_semana", "objetivo", "evento", "hueco", "detalle"], eventos)
    json.dump(state, open(state_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if alertas:
        with open(os.path.join(DATA, "alerta.md"), "w", encoding="utf-8") as f:
            f.write("\n\n".join(alertas) + "\n")
    print(json.dumps({"checks": checks, "eventos": eventos, "alertas": len(alertas)}, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
