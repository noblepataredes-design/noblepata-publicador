"""
Publicador automático de Instagram para NoblePata.

Lee calendario.json, publica en Instagram los posts cuya fecha ya ha llegado
y que están aprobados, y marca cada uno como publicado.

Usa la API oficial de Instagram (Instagram API with Instagram Login).
Sin dependencias externas: solo librería estándar de Python 3.9+.

Variables de entorno:
  IG_TOKEN        Token de acceso de larga duración de Instagram (secreto).
  MEDIA_BASE_URL  URL pública donde están los archivos de /media
                  (por defecto, la de GitHub Pages del repositorio).
  DRY_RUN=1       Simula sin llamar a Instagram.
"""

import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

API = "https://graph.instagram.com/v26.0"
ZONA = ZoneInfo("Europe/Madrid")
CALENDARIO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calendario.json")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
VIDEO = (".mp4", ".mov")


def base_url_por_defecto():
    repo = os.environ.get("GITHUB_REPOSITORY", "usuario/repo")
    usuario, nombre = repo.split("/", 1)
    return f"https://{usuario.lower()}.github.io/{nombre}/media/"


MEDIA_BASE_URL = os.environ.get("MEDIA_BASE_URL") or base_url_por_defecto()
if not MEDIA_BASE_URL.endswith("/"):
    MEDIA_BASE_URL += "/"


def llamar(metodo, ruta, params=None):
    if DRY_RUN:
        print(f"   [simulación] {metodo} {ruta} {params or ''}")
        return {"id": "SIMULADO", "status_code": "FINISHED", "permalink": "https://instagram.com/p/SIMULADO"}
    params = dict(params or {})
    params["access_token"] = os.environ["IG_TOKEN"]
    datos = urllib.parse.urlencode(params)
    url = f"{API}/{ruta}"
    if metodo == "GET":
        req = urllib.request.Request(f"{url}?{datos}")
    else:
        req = urllib.request.Request(url, data=datos.encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Instagram respondió {e.code}: {e.read().decode(errors='replace')}") from None


def esperar_listo(contenedor_id, max_segundos=600):
    """Los vídeos tardan en procesarse; esperamos a que el contenedor esté FINISHED."""
    inicio = time.time()
    while time.time() - inicio < max_segundos:
        estado = llamar("GET", contenedor_id, {"fields": "status_code"}).get("status_code")
        if estado == "FINISHED":
            return
        if estado in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"El contenedor {contenedor_id} falló con estado {estado}")
        time.sleep(10)
    raise RuntimeError(f"El contenedor {contenedor_id} no terminó de procesarse a tiempo")


def es_video(nombre):
    return nombre.lower().endswith(VIDEO)


def crear_contenedor(post):
    archivos = post["archivos"]
    texto = post.get("texto", "")
    tipo = post["tipo"].upper()

    if tipo == "IMAGEN":
        return llamar("POST", "me/media", {"image_url": MEDIA_BASE_URL + archivos[0], "caption": texto})["id"]

    if tipo == "REEL":
        params = {"media_type": "REELS", "video_url": MEDIA_BASE_URL + archivos[0],
                  "caption": texto, "share_to_feed": "true"}
        if post.get("portada"):
            params["cover_url"] = MEDIA_BASE_URL + post["portada"]
        cid = llamar("POST", "me/media", params)["id"]
        esperar_listo(cid)
        return cid

    if tipo == "CARRUSEL":
        if not 2 <= len(archivos) <= 10:
            raise ValueError("Un carrusel necesita entre 2 y 10 archivos")
        hijos = []
        for a in archivos:
            if es_video(a):
                hid = llamar("POST", "me/media", {"media_type": "VIDEO", "video_url": MEDIA_BASE_URL + a,
                                                  "is_carousel_item": "true"})["id"]
                esperar_listo(hid)
            else:
                hid = llamar("POST", "me/media", {"image_url": MEDIA_BASE_URL + a, "is_carousel_item": "true"})["id"]
            hijos.append(hid)
        cid = llamar("POST", "me/media", {"media_type": "CAROUSEL", "children": ",".join(hijos), "caption": texto})["id"]
        esperar_listo(cid)
        return cid

    raise ValueError(f"Tipo desconocido: {tipo} (usa IMAGEN, REEL o CARRUSEL)")


def main():
    with open(CALENDARIO, encoding="utf-8") as f:
        calendario = json.load(f)

    ahora = datetime.datetime.now(ZONA)
    cambios = False
    errores = 0

    for post in calendario["posts"]:
        if post.get("estado") != "aprobado":
            continue
        fecha = datetime.datetime.strptime(post["fecha"], "%Y-%m-%d %H:%M").replace(tzinfo=ZONA)
        if fecha > ahora:
            continue

        print(f"→ Publicando '{post['id']}' ({post['tipo']}, programado {post['fecha']})")
        try:
            cid = crear_contenedor(post)
            media_id = llamar("POST", "me/media_publish", {"creation_id": cid})["id"]
            enlace = llamar("GET", media_id, {"fields": "permalink"}).get("permalink", "")
            post["estado"] = "publicado"
            post["publicado_el"] = ahora.strftime("%Y-%m-%d %H:%M")
            post["enlace"] = enlace
            print(f"   ✓ Publicado: {enlace}")
        except Exception as e:  # se registra y se sigue con el resto
            post["estado"] = "error"
            post["error"] = str(e)[:500]
            errores += 1
            print(f"   ✗ Error: {e}")
        cambios = True

    if cambios and not DRY_RUN:
        with open(CALENDARIO, "w", encoding="utf-8") as f:
            json.dump(calendario, f, ensure_ascii=False, indent=2)
            f.write("\n")

    if not cambios:
        print("Nada que publicar ahora.")
    sys.exit(1 if errores else 0)


if __name__ == "__main__":
    main()
