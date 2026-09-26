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

# Vendedores que reciben los pedidos armados desde el carrito (WhatsApp, lada +52)
VENDEDORES = [
    {"nombre": "Saul Rodríguez", "telefono": "528124661781"},
    {"nombre": "Sandra Toral", "telefono": "528117990800"},
    {"nombre": "Ana Ochoa", "telefono": "528124150260"},
]


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
            "financiero": "true",
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
        sin_stock = int(p["existencia"] or 0) <= 0
        titulo_attr = html.escape(p['titulo'], quote=True)
        controles = (
            '<span class="agotado-msg">Sin existencia</span>'
            if sin_stock else
            f"""<div class="cart-controls">
              <input type="number" class="qty-input" value="1" min="1" step="1">
              <button type="button" class="add-btn"
                data-sku="{html.escape(p['sku'], quote=True)}"
                data-titulo="{titulo_attr}"
                data-precio="{p['precio']}">Agregar</button>
            </div>"""
        )
        filas.append(f"""
        <div class="card" data-nombre="{html.escape(p['titulo'].lower())}" data-sku="{html.escape(p['sku'].lower())}">
          <img src="{html.escape(p['imagen'])}" alt="{html.escape(p['titulo'])}" loading="lazy"
               onerror="this.style.display='none'">
          <div class="card-body">
            <span class="sku">{html.escape(p['sku'])}</span>
            <h3>{html.escape(p['titulo'])}</h3>
            <p class="precio">${p['precio']:,.2f} MXN</p>
            <span class="badge {clase_stock}">{disponible} ({p['existencia']})</span>
            {controles}
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

  .cart-controls {{ display: flex; gap: 6px; margin-top: 8px; }}
  .qty-input {{
    width: 56px; padding: 8px 6px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--bg); color: var(--text); text-align: center; font-size: 0.9rem;
  }}
  .add-btn {{
    flex: 1; padding: 8px 10px; border-radius: 8px; border: none;
    background: var(--accent); color: #fff; font-weight: 600; font-size: 0.85rem; cursor: pointer;
  }}
  .add-btn:active {{ opacity: 0.8; }}
  .agotado-msg {{ margin-top: 8px; font-size: 0.8rem; color: var(--muted); }}

  #cart-fab {{
    position: fixed; right: 18px; bottom: 18px; z-index: 40;
    width: 58px; height: 58px; border-radius: 50%; border: none;
    background: var(--accent); color: #fff; font-size: 1.5rem; cursor: pointer;
    box-shadow: 0 4px 16px rgba(0,0,0,0.3); display: flex; align-items: center; justify-content: center;
  }}
  #cart-fab .badge-count {{
    position: absolute; top: -4px; right: -4px; background: var(--red); color: #fff;
    border-radius: 999px; font-size: 0.7rem; font-weight: 700; min-width: 20px; height: 20px;
    display: flex; align-items: center; justify-content: center; padding: 0 4px;
  }}

  #cart-overlay {{
    position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 50;
    display: none; align-items: flex-end; justify-content: center;
  }}
  #cart-overlay.open {{ display: flex; }}
  #cart-panel {{
    background: var(--card-bg); color: var(--text); width: 100%; max-width: 480px;
    max-height: 85vh; border-radius: 16px 16px 0 0; padding: 16px;
    display: flex; flex-direction: column; gap: 12px;
    padding-bottom: calc(16px + env(safe-area-inset-bottom, 0px));
  }}
  @media (min-width: 560px) {{
    #cart-overlay {{ align-items: center; }}
    #cart-panel {{ border-radius: 16px; max-height: 80vh; }}
  }}
  #cart-panel h2 {{ margin: 0; font-size: 1.2rem; }}
  #cart-items {{ overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }}
  .cart-item {{
    display: flex; justify-content: space-between; align-items: center; gap: 8px;
    border-bottom: 1px solid var(--border); padding-bottom: 8px;
  }}
  .cart-item .info {{ flex: 1; min-width: 0; }}
  .cart-item .info .t {{ font-size: 0.85rem; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .cart-item .info .s {{ font-size: 0.75rem; color: var(--muted); }}
  .cart-item .qty {{ display: flex; align-items: center; gap: 4px; }}
  .cart-item .qty button {{
    width: 26px; height: 26px; border-radius: 6px; border: 1px solid var(--border);
    background: var(--bg); color: var(--text); cursor: pointer; font-size: 1rem;
  }}
  .cart-item .qty span {{ min-width: 20px; text-align: center; font-size: 0.9rem; }}
  .cart-item .remove {{ background: none; border: none; color: var(--red); font-size: 1.1rem; cursor: pointer; }}
  .cart-empty {{ color: var(--muted); text-align: center; padding: 20px 0; }}
  #cart-total {{ font-size: 1.1rem; font-weight: 700; display: flex; justify-content: space-between; }}
  #seller-select {{
    width: 100%; padding: 10px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--bg); color: var(--text); font-size: 0.95rem;
  }}
  #send-whatsapp {{
    width: 100%; padding: 12px; border-radius: 10px; border: none;
    background: #25D366; color: #fff; font-weight: 700; font-size: 1rem; cursor: pointer;
  }}
  #send-whatsapp:disabled {{ background: var(--muted); cursor: not-allowed; }}
  #close-cart {{ background: none; border: none; font-size: 1.4rem; color: var(--muted); cursor: pointer; }}
  .cart-header {{ display: flex; justify-content: space-between; align-items: center; }}
</style>
</head>
<body>
  <header>
    <img src="logo.png" alt="Total Security" class="logo">
    <h1>Catálogo UGREEN para Distribuidores</h1>
    <p>Actualizado automáticamente · {fecha}</p>
  </header>
  <div class="search-wrap">
    <input id="buscador" type="text" placeholder="Buscar producto o SKU...">
  </div>
  <div class="grid" id="grid">
    {''.join(filas)}
  </div>
  <footer>Precios y existencias sujetos a cambio · Fuente: Syscom</footer>

  <button id="cart-fab" aria-label="Ver carrito">🛒<span class="badge-count" id="cart-count" style="display:none">0</span></button>

  <div id="cart-overlay">
    <div id="cart-panel">
      <div class="cart-header">
        <h2>Tu pedido</h2>
        <button id="close-cart" aria-label="Cerrar">✕</button>
      </div>
      <div id="cart-items"></div>
      <div id="cart-total"><span>Total</span><span id="cart-total-amount">$0.00 MXN</span></div>
      <select id="seller-select">
        <option value="">Elige un vendedor...</option>
        {''.join(f'<option value="{v["telefono"]}">{html.escape(v["nombre"])}</option>' for v in VENDEDORES)}
      </select>
      <button id="send-whatsapp" disabled>Enviar pedido por WhatsApp</button>
    </div>
  </div>

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

    // ---- Carrito de compras ----
    let carrito = [];
    try {{
      const guardado = localStorage.getItem('carrito_ugreen');
      if (guardado) carrito = JSON.parse(guardado);
    }} catch (e) {{ carrito = []; }}

    const cartFab = document.getElementById('cart-fab');
    const cartCount = document.getElementById('cart-count');
    const cartOverlay = document.getElementById('cart-overlay');
    const cartItemsEl = document.getElementById('cart-items');
    const cartTotalEl = document.getElementById('cart-total-amount');
    const sellerSelect = document.getElementById('seller-select');
    const sendBtn = document.getElementById('send-whatsapp');
    const closeCartBtn = document.getElementById('close-cart');

    function guardarCarrito() {{
      try {{ localStorage.setItem('carrito_ugreen', JSON.stringify(carrito)); }} catch (e) {{}}
    }}

    function formatoMoneda(n) {{
      return '$' + n.toLocaleString('es-MX', {{minimumFractionDigits: 2, maximumFractionDigits: 2}}) + ' MXN';
    }}

    function renderCarrito() {{
      const totalItems = carrito.reduce((s, i) => s + i.cantidad, 0);
      if (totalItems > 0) {{
        cartCount.style.display = 'flex';
        cartCount.textContent = totalItems;
      }} else {{
        cartCount.style.display = 'none';
      }}

      if (carrito.length === 0) {{
        cartItemsEl.innerHTML = '<p class="cart-empty">Tu carrito está vacío.</p>';
      }} else {{
        cartItemsEl.innerHTML = carrito.map((item, idx) => `
          <div class="cart-item">
            <div class="info">
              <div class="t">${{item.titulo}}</div>
              <div class="s">SKU ${{item.sku}} · ${{formatoMoneda(item.precio)}} c/u</div>
            </div>
            <div class="qty">
              <button data-idx="${{idx}}" data-op="menos">-</button>
              <span>${{item.cantidad}}</span>
              <button data-idx="${{idx}}" data-op="mas">+</button>
            </div>
            <button class="remove" data-idx="${{idx}}" data-op="quitar">✕</button>
          </div>
        `).join('');
      }}

      const total = carrito.reduce((s, i) => s + i.precio * i.cantidad, 0);
      cartTotalEl.textContent = formatoMoneda(total);

      sendBtn.disabled = carrito.length === 0 || !sellerSelect.value;
      guardarCarrito();
    }}

    document.querySelectorAll('.add-btn').forEach(btn => {{
      btn.addEventListener('click', () => {{
        const sku = btn.dataset.sku;
        const titulo = btn.dataset.titulo;
        const precio = parseFloat(btn.dataset.precio);
        const qtyInput = btn.closest('.cart-controls').querySelector('.qty-input');
        let cantidad = parseInt(qtyInput.value, 10);
        if (!cantidad || cantidad < 1) cantidad = 1;

        const existente = carrito.find(i => i.sku === sku);
        if (existente) {{
          existente.cantidad += cantidad;
        }} else {{
          carrito.push({{ sku, titulo, precio, cantidad }});
        }}
        renderCarrito();
        cartOverlay.classList.add('open');
      }});
    }});

    cartItemsEl.addEventListener('click', (e) => {{
      const btn = e.target.closest('button[data-idx]');
      if (!btn) return;
      const idx = parseInt(btn.dataset.idx, 10);
      const op = btn.dataset.op;
      if (op === 'mas') carrito[idx].cantidad += 1;
      if (op === 'menos') {{
        carrito[idx].cantidad -= 1;
        if (carrito[idx].cantidad <= 0) carrito.splice(idx, 1);
      }}
      if (op === 'quitar') carrito.splice(idx, 1);
      renderCarrito();
    }});

    cartFab.addEventListener('click', () => cartOverlay.classList.add('open'));
    closeCartBtn.addEventListener('click', () => cartOverlay.classList.remove('open'));
    cartOverlay.addEventListener('click', (e) => {{
      if (e.target === cartOverlay) cartOverlay.classList.remove('open');
    }});
    sellerSelect.addEventListener('change', renderCarrito);

    sendBtn.addEventListener('click', () => {{
      if (carrito.length === 0 || !sellerSelect.value) return;
      const total = carrito.reduce((s, i) => s + i.precio * i.cantidad, 0);
      let mensaje = 'Hola, quiero hacer el siguiente pedido:\\n\\n';
      carrito.forEach(item => {{
        const subtotal = item.precio * item.cantidad;
        mensaje += `• ${{item.titulo}} (SKU ${{item.sku}}) x${{item.cantidad}} - ${{formatoMoneda(subtotal)}}\\n`;
      }});
      mensaje += `\\nTotal: ${{formatoMoneda(total)}}`;

      const url = `https://wa.me/${{sellerSelect.value}}?text=${{encodeURIComponent(mensaje)}}`;
      window.open(url, '_blank');
    }});

    renderCarrito();
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
