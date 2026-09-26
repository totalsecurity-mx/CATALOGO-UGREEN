#!/usr/bin/env python3
"""
sync_catalog.py
----------------
Se conecta a la API de Syscom (developers.syscom.mx), descarga todos los
productos de la marca UGREEN con su precio y existencias, y genera un
archivo index.html con el catálogo listo para publicar.

Credenciales: se leen de variables de entorno (NUNCA las pongas en este
archivo ni las subas a un repositorio público):
    SYSCOM_CLIENT_ID
    SYSCOM_CLIENT_SECRET

Uso local:
    export SYSCOM_CLIENT_ID="tu_client_id"
    export SYSCOM_CLIENT_SECRET="tu_client_secret"
    python sync_catalog.py

En GitHub Actions estas variables se inyectan automáticamente desde los
"Secrets" del repositorio (ver workflow sync.yml).
"""

import os
import sys
import json
import html
import datetime
import urllib.request
import urllib.parse
import urllib.error

API_BASE = "https://developers.syscom.mx/api/v1"
TOKEN_URL = "https://developers.syscom.mx/api/v1/oauth/token"
MARCA_FILTRO = "ugreen"  # slug en minúsculas, tal como lo pide la API de Syscom
MARGEN = 1.12  # 12% de margen sobre el precio de costo de Syscom


def obtener_token(client_id, client_secret):
    """Solicita un access_token usando OAuth2 client_credentials."""
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }).encode()

    req = urllib.request.Request(
        TOKEN_URL,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0 (compatible; CatalogoUgreenBot/1.0)",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detalle = e.read().decode(errors="replace")
        print(f"--- Respuesta de Syscom (HTTP {e.code}) ---", file=sys.stderr)
        print(detalle, file=sys.stderr)
        print("-------------------------------------------", file=sys.stderr)
        raise
    return body["access_token"]


def obtener_productos_marca(token, marca):
    """
    Descarga todos los productos de una marca, paginando resultados.
    Ajusta el endpoint/parametros exactos según la documentación que
    veas en tu cuenta de developers.syscom.mx (puede variar: 'marca',
    'busqueda', 'pagina', etc. Este script asume el patrón estándar
    documentado por Syscom a la fecha de escritura).
    """
    productos = []
    pagina = 1
    limit = 1000
    while True:
        params = urllib.parse.urlencode({
            "marca": marca,
            "pagina": pagina,
            "limit": limit,
            "inventarios": "true",
            "moneda": "mxn",
            "iva": "true",
        })
        url = f"{API_BASE}/productos?{params}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": "Mozilla/5.0 (compatible; CatalogoUgreenBot/1.0)",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detalle = e.read().decode(errors="replace")
            print(f"Error HTTP {e.code} en página {pagina}: {detalle}", file=sys.stderr)
            break

        lote = body.get("productos", body if isinstance(body, list) else [])
        if not lote:
            break
        productos.extend(lote)

        # Si la API regresa info de paginación, respétala; si no, corta cuando el lote viene incompleto
        total_paginas = body.get("paginas") if isinstance(body, dict) else None
        if total_paginas and pagina >= total_paginas:
            break
        if not total_paginas and len(lote) < limit:
            break
        pagina += 1
        if pagina > 200:  # límite de seguridad
            break

    return productos


def normalizar(producto):
    """Extrae los campos que nos interesan con nombres consistentes."""
    precios_obj = producto.get("precios", {})
    if isinstance(precios_obj, dict):
        precio_costo = (
            precios_obj.get("precio_descuento")
            or precios_obj.get("precio_especial")
            or precios_obj.get("precio_1")
        )
    else:
        precio_costo = producto.get("precio")
    precio_costo = precio_costo or 0

    existencia_raw = producto.get("total_existencia", producto.get("existencia", 0))
    if isinstance(existencia_raw, dict):
        # Puede venir como {"disponible": N, ...} o {"total": N, ...} o con "detalle" por almacén
        existencia_num = (
            existencia_raw.get("disponible")
            or existencia_raw.get("total")
            or sum(
                (d.get("cantidad", 0) or d.get("disponible", 0) or 0)
                for d in existencia_raw.get("detalle", [])
                if isinstance(d, dict)
            )
        )
    else:
        existencia_num = existencia_raw

    return {
        "sku": producto.get("modelo") or producto.get("sku") or "",
        "titulo": producto.get("titulo") or producto.get("nombre") or "Producto sin nombre",
        "precio": round(float(precio_costo) * MARGEN, 2),
        "existencia": int(existencia_num or 0),
        "imagen": producto.get("img_portada") or producto.get("imagen") or "",
        "descripcion": producto.get("descripcion") or "",
    }


def generar_html(productos):
    fecha = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    filas = []
    for p in productos:
        disponible = "Disponible" if int(p["existencia"] or 0) > 0 else "Agotado"
        clase_stock = "in-stock" if int(p["existencia"] or 0) > 0 else "out-stock"
        filas.append(f"""
        <div class="card" data-nombre="{html.escape(p['titulo'].lower())}" data-sku="{html.escape(p['sku'].lower())}">
          <img src="{html.escape(p['imagen'])}" alt="{html.escape(p['titulo'])}" loading="lazy"
               onerror="this.style.display='none'">
          <div class="card-body">
            <span class="sku">{html.escape(p['sku'])}</span>
            <h3>{html.escape(p['titulo'])}</h3>
            <p class="precio">${p['precio']:,.2f} MXN</p>
            <span class="badge {clase_stock}">{disponible} ({p['existencia']})</span>
          </div>
        </div>""")

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Catálogo UGREEN</title>
<style>
  :root {{
    --bg: #f7f7f8; --card-bg: #ffffff; --text: #1a1a1a; --muted: #6b6b6b;
    --accent: #0a84ff; --green: #1e8e3e; --red: #c5221f; --border: #e5e5e5;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg: #0f0f10; --card-bg: #1a1a1c; --text: #f2f2f2; --muted: #9a9a9a; --border: #2a2a2c;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    padding-top: env(safe-area-inset-top, 0px);
    padding-bottom: env(safe-area-inset-bottom, 0px);
  }}
  header {{
    padding: 24px 16px 12px; text-align: center;
  }}
  header .logo {{
    max-width: 220px; max-height: 90px; width: auto; height: auto;
    margin: 0 auto 12px; display: block;
    background: #fff; padding: 8px 14px; border-radius: 8px;
  }}
  header h1 {{ margin: 0 0 4px; font-size: 1.6rem; }}
  header p {{ margin: 0; color: var(--muted); font-size: 0.85rem; }}
  .search-wrap {{ max-width: 480px; margin: 16px auto; padding: 0 16px; }}
  #buscador {{
    width: 100%; padding: 12px 14px; border-radius: 10px; border: 1px solid var(--border);
    background: var(--card-bg); color: var(--text); font-size: 1rem;
  }}
  .grid {{
    display: grid; gap: 16px; padding: 8px 16px 40px;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    max-width: 1200px; margin: 0 auto;
  }}
  .card {{
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 14px;
    overflow: hidden; display: flex; flex-direction: column;
  }}
  .card img {{ width: 100%; aspect-ratio: 1/1; object-fit: contain; background: #fff; padding: 8px; }}
  .card-body {{ padding: 12px 14px 16px; display: flex; flex-direction: column; gap: 4px; }}
  .sku {{ font-size: 0.7rem; color: var(--muted); }}
  .card h3 {{ font-size: 0.95rem; margin: 0; line-height: 1.3; }}
  .precio {{ font-size: 1.15rem; font-weight: 700; color: var(--accent); margin: 4px 0; }}
  .badge {{ font-size: 0.75rem; padding: 3px 8px; border-radius: 999px; width: fit-content; }}
  .in-stock {{ background: rgba(30,142,62,0.12); color: var(--green); }}
  .out-stock {{ background: rgba(197,34,31,0.12); color: var(--red); }}
  footer {{ text-align: center; color: var(--muted); font-size: 0.75rem; padding: 20px; }}
</style>
</head>
<body>
  <header>
    <img src="logo.png" alt="Total Security" class="logo">
    <h1>Catálogo Green para Distribuidores</h1>
    <p>Actualizado automáticamente · {fecha}</p>
  </header>
  <div class="search-wrap">
    <input id="buscador" type="text" placeholder="Buscar producto o SKU...">
  </div>
  <div class="grid" id="grid">
    {''.join(filas)}
  </div>
  <footer>Precios y existencias sujetos a cambio · Fuente: Syscom</footer>
  <script>
    const buscador = document.getElementById('buscador');
    const cards = Array.from(document.querySelectorAll('.card'));
    buscador.addEventListener('input', () => {{
      const q = buscador.value.trim().toLowerCase();
      cards.forEach(c => {{
        const match = c.dataset.nombre.includes(q) || c.dataset.sku.includes(q);
        c.style.display = match ? '' : 'none';
      }});
    }});
  </script>
</body>
</html>"""


def main():
    client_id = os.environ.get("SYSCOM_CLIENT_ID")
    client_secret = os.environ.get("SYSCOM_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("Faltan SYSCOM_CLIENT_ID / SYSCOM_CLIENT_SECRET en variables de entorno.", file=sys.stderr)
        sys.exit(1)

    print("Solicitando token de acceso...")
    token = obtener_token(client_id, client_secret)

    print(f"Descargando productos de la marca {MARCA_FILTRO}...")
    crudos = obtener_productos_marca(token, MARCA_FILTRO)
    print(f"  -> {len(crudos)} productos encontrados")
    if crudos:
        primero = crudos[0]
        print("--- Diagnóstico del primer producto ---", file=sys.stderr)
        print("Claves disponibles:", list(primero.keys()), file=sys.stderr)
        for clave in primero.keys():
            if "preci" in clave.lower():
                print(f"  {clave}: {json.dumps(primero[clave], ensure_ascii=False)}", file=sys.stderr)
        print("----------------------------------------", file=sys.stderr)

    productos = [normalizar(p) for p in crudos]

    print("Generando index.html...")
    html_final = generar_html(productos)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_final)

    print("Listo: index.html actualizado.")


if __name__ == "__main__":
    main()
