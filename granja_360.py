import os
import re
import json
import time
import random
import requests
import warnings
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from ddgs import DDGS
from googlesearch import search as google_search
from supabase import create_client, Client

warnings.filterwarnings("ignore")

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip()
SUPABASE_KEY = (os.environ.get("SUPABASE_KEY") or "").strip()
GROQ_KEY = (os.environ.get("GROQ_KEY") or "").strip()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'es-ES,es;q=0.9'
}

ETTS_PROHIBIDAS = [
    "randstad", "adecco", "manpower", "hays", "michael page", "robert walters",
    "synergie", "gi group", "temporing", "nortempo", "grupo crit", "eurofirms",
    "faster", "iman", "page personnel", "spring professional", "jobandtalent"
]

DOMINIOS_PROHIBIDOS = [
    "infojobs", "linkedin", "indeed", "glassdoor", "tecnoempleo", "jobatus",
    "jooble", "opcionempleo", "trabajos.com", "paginasamarillas", "einforma",
    "infocif", "empresite", "axesor", "iberinform", "kompass", "europages",
    "facebook", "instagram", "twitter", "youtube", "google", "boe.es",
    "sentry", "wix", "example", "wordpress", "cloudflare", "github", "wikipedia",
    "reddit", "amazon", "whatsapp", "walmart", "ebay", "aliexpress",
    "marca.com", "as.com", "sport.es", "mundodeportivo", "worldtimebuddy",
    "utctime", "timeanddate", "pinterest", "tiktok", "vimeo", "dailymotion",
    "shopify", "myshopify", "topcashback", "bradsdeals", "passionforsavings",
    "yahoo", "bing", "msn"
]

PALABRAS_SECTOR = [
    "transitario", "transitarios", "aduana", "aduanas", "aduanero", "flete", "fletes",
    "maritimo", "marítimo", "logistica", "logística", "freight", "forwarder",
    "naviera", "puerto", "puertos", "portuario", "comercio exterior",
    "comercio internacional", "importacion", "exportacion", "importación",
    "exportación", "pricing", "oea", "ateia", "contenedores", "transporte internacional",
    "despacho aduanero", "carga aerea", "carga aérea"
]

ANGULOS_COMERCIALES = [
    "Enfoque en VELOCIDAD DE RESPUESTA: responder cotizaciones spot marítimas y aéreas en menos de 30 segundos antes que la competencia.",
    "Enfoque en CONTROL DE RECARGOS: evitar fugas de margen por olvidos en THC, BAF, CAF o gastos locales en puerto.",
    "Enfoque en ADIÓS AL EXCEL: sustituir hojas de cálculo manuales y tarifas dispersas por un cotizador centralizado para el equipo de tráfico y pricing.",
    "Enfoque en RENTABILIDAD POR EXPEDIENTE: calcular al instante el margen neto real por contenedor FCL/LCL o envío aéreo.",
    "Enfoque en ESCALABILIDAD DEL EQUIPO: permitir que un solo técnico de pricing gestione el triple de ofertas diarias sin cuello de botella.",
    "Enfoque en IMAGEN PROFESIONAL: enviar presupuestos desglosados y claros al importador/exportador en un clic."
]

DOMINIOS_VISTOS = set()
URLS_VISTAS = set()

def detectar_modelos_groq():
    respaldo = [
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
        "qwen/qwen3-32b",
        "llama-3.3-70b-versatile"
    ]
    if not GROQ_KEY:
        return respaldo
    try:
        res = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {GROQ_KEY}"},
            timeout=10
        )
        if res.status_code == 200:
            data = res.json().get("data", [])
            ids_disponibles = [
                m["id"] for m in data
                if m.get("id") and not any(x in m["id"].lower() for x in ["whisper", "guard", "tts", "playai", "embed", "safeguard"])
            ]
            if ids_disponibles:
                ordenados = []
                for pref in ["gpt-oss-20b", "gpt-oss", "qwen", "llama-3.3", "llama-3.1", "llama"]:
                    for mid in ids_disponibles:
                        if pref in mid.lower() and mid not in ordenados:
                            ordenados.append(mid)
                return ordenados or respaldo
    except Exception:
        pass
    return respaldo

MODELOS_GROQ = detectar_modelos_groq()

def cargar_historico_completo():
    """Carga empresas y URLs ya captadas para evitar cualquier repeticion."""
    empresas = set()
    urls = set()
    if not supabase:
        return empresas, urls
    try:
        r1 = supabase.table("prospectos").select("empresa").execute()
        for row in (r1.data or []):
            if row.get("empresa"):
                empresas.add(row["empresa"].lower().strip()[:25])
        r2 = supabase.table("granja_cero_email").select("empresa,url_accion").execute()
        for row in (r2.data or []):
            if row.get("empresa"):
                empresas.add(row["empresa"].lower().strip()[:25])
            if row.get("url_accion"):
                urls.add(row["url_accion"].split("?")[0].rstrip("/").lower())
    except Exception as e:
        print(f"Aviso cargando historico: {e}")
    return empresas, urls

EMPRESAS_EXISTENTES, URLS_EXISTENTES = cargar_historico_completo()

def es_relevante_sector(texto):
    t = (texto or "").lower()
    if any(ett in t for ett in ETTS_PROHIBIDAS):
        return False
    return any(p in t for p in PALABRAS_SECTOR)

def buscar_dual(query, max_results=6):
    resultados = []
    try:
        raw = DDGS().text(query, region="es-es", max_results=max_results)
        for r in (raw or []):
            resultados.append({
                "title": r.get("title", ""),
                "href": r.get("href", ""),
                "body": r.get("body", "")
            })
    except Exception:
        pass

    if not resultados:
        try:
            for item in google_search(query, num_results=max_results, lang="es", advanced=True):
                resultados.append({
                    "title": getattr(item, "title", "") or "",
                    "href": getattr(item, "url", "") or "",
                    "body": getattr(item, "description", "") or ""
                })
                time.sleep(0.5)
        except Exception:
            pass

    return resultados

def dominio_limpio(url):
    if not url:
        return ""
    if not url.startswith("http"):
        url = "https://" + url
    try:
        return urlparse(url).netloc.lower().replace("www.", "").split(":")[0]
    except Exception:
        return ""

def es_web_valida(url):
    dom = dominio_limpio(url)
    if not dom or "." not in dom:
        return False
    if dom in DOMINIOS_VISTOS:
        return False
    if any(p in dom for p in DOMINIOS_PROHIBIDOS):
        return False
    if any(ett in dom for ett in ETTS_PROHIBIDAS):
        return False
    return True

def localizar_formulario_y_contexto(url):
    if not url or not es_web_valida(url):
        return None, ""
    if not url.startswith("http"):
        url = "https://" + url
    dom = dominio_limpio(url)
    base = f"https://{dom}"
    url_contacto = url
    texto_web = ""

    rutas_contacto = [f"{base}/contacto", f"{base}/contact", f"{base}/contactar", url]
    for u in rutas_contacto:
        try:
            res = requests.get(u, headers=HEADERS, timeout=4)
            if res.status_code == 200:
                url_contacto = u
                soup = BeautifulSoup(res.text, 'html.parser')
                for s in soup(["script", "style", "noscript", "svg", "footer", "nav"]):
                    s.extract()
                texto_web = re.sub(r'\s+', ' ', soup.get_text(separator=' ', strip=True))[:550]
                if len(texto_web) > 50:
                    break
        except Exception:
            continue

    return url_contacto, texto_web

def extraer_json_seguro(texto):
    if not texto:
        return None
    try:
        return json.loads(texto)
    except Exception:
        match = re.search(r'\{.*\}', texto, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                return None
    return None

def limpiar_texto_ia(texto):
    if not texto:
        return ""
    t = texto.replace("[Su Nombre]", "Equipo LogiQuote").replace("[Tu Nombre]", "Equipo LogiQuote").replace("[Nombre]", "Equipo LogiQuote")
    return t.strip()

def procesar_con_ia(canal, texto_bruto):
    if not GROQ_KEY:
        return None

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers_groq = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    angulo_hoy = random.choice(ANGULOS_COMERCIALES)

    prompt = f"""Eres el Director de Desarrollo de Negocio de LogiQuote (https://logiquote-saas.vercel.app), software B2B español diseñado para transitarios y agentes de aduanas que automatiza el cálculo de fletes marítimos/aéreos, recargos THC/BAF y márgenes en 30 segundos sin Excel.

REGLAS CRÍTICAS DE REDACCIÓN (OBLIGATORIAS):
1. PERSPECTIVA: NOSOTROS (LogiQuote) contactamos a la empresa transitaria para ofrecerles nuestra herramienta. NUNCA escribas como si ellos nos pidieran información a nosotros ("Le agradecemos su interés..."), NI como un candidato pidiendo empleo ("Interesado en la posición...").
2. ANTI-PLANTILLA: PROHIBIDO empezar con "Estimado/a", "Estimado Sr.", "Le agradezco su atención" o terminar con "Quedo a su disposición". Usa inicios naturales, directos y variados como: "Buenos días,", "Hola [Nombre],", "Un saludo al equipo de [Empresa],", o entra directo al grano sobre su operativa.
3. ÁNGULO COMERCIAL PARA ESTE MENSAJE: {angulo_hoy}
4. SIN PLACEHOLDERS: Nunca escribas corchetes como [Su Nombre] o [Empresa]; firma siempre como "Equipo LogiQuote".

Analiza el texto recibido y responde ÚNICAMENTE con un objeto JSON válido con estas 7 claves:
- "es_valido": boolean (true SOLO si es una transitaria, agente de aduanas, operador logístico internacional, consultor OEA, escuela de comercio exterior o noticia portuaria en España; false si es estudiante/alumni sin cargo decisor, ETT o empresa ajena).
- "empresa": string con el nombre limpio de la empresa, consultora, escuela o medio.
- "nombre_contacto": string con el nombre y apellido si aparece, o "Responsable Operaciones / Pricing".
- "cargo": string con el cargo si aparece, o "Operaciones / Pricing".
- "lead_score": entero de 0 a 100 (92 si es Empleo VIP; 88 si es Decisor LinkedIn; 84 si es Consultor OEA o Escuela; 78 si es Transitaria; 72 si es Noticia Prensa).
- "mensaje_gancho": string formal (tratando de usted) y original:
   * Si canal es LINKEDIN_DECISOR, CONSULTOR_OEA o ESCUELA_COMERCIO: invitación a conectar en LinkedIn de MÁXIMO 200 caracteres, SIN enlaces y sin sonar a robot.
   * Si canal es PRENSA_COMENTARIO: comentario técnico experto (máx. 320 caracteres) aportando valor real sobre la noticia y los márgenes en fletes.
   * Si canal es FORMULARIO_EMPLEO_VIP o FORMULARIO_ATEIA: asunto breve y natural para su formulario web (ej: "Agilizar cotizaciones marítimas/aéreas en [Empresa]").
- "guion_cierre": string formal (tratando de usted, 3-4 líneas máximo, directo y distinto en cada empresa) para enviar tras conectar en LinkedIn o pegar en el formulario web de la transitaria. Debe explicar en 1 frase cómo LogiQuote acelera sus cotizaciones según el ángulo indicado e invitarles a probarlo 14 días gratis sin compromiso (código LOGI10 para un 10% de descuento el primer mes) en https://logiquote-saas.vercel.app (en CONSULTOR_OEA propone acuerdo de partner con 25% de comisión recurrente; en ESCUELA_COMERCIO propone licencias educativas gratuitas para prácticas)."""

    for modelo in MODELOS_GROQ[:4]:
        data = {
            "model": modelo,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"Canal: {canal}\nDatos:\n{texto_bruto}"}
            ],
            "temperature": 0.65
        }

        try:
            res = requests.post(url, headers=headers_groq, json=data, timeout=20)
            if res.status_code == 200:
                contenido = res.json()['choices'][0]['message']['content']
                parsed = extraer_json_seguro(contenido)
                if parsed and isinstance(parsed, dict):
                    parsed["mensaje_gancho"] = limpiar_texto_ia(parsed.get("mensaje_gancho", ""))
                    parsed["guion_cierre"] = limpiar_texto_ia(parsed.get("guion_cierre", ""))
                    return parsed
        except Exception:
            continue

    return None

def guardar_oportunidad(registro):
    if not supabase:
        return
    empresa = (registro.get("empresa") or "").strip()
    url_norm = (registro.get("url_accion") or "").split("?")[0].rstrip("/").lower()
    if len(empresa) < 3 or not url_norm:
        return

    clave = empresa.lower()[:25]
    if clave in EMPRESAS_EXISTENTES or url_norm in URLS_EXISTENTES:
        return

    try:
        supabase.table("granja_cero_email").insert(registro).execute()
        EMPRESAS_EXISTENTES.add(clave)
        URLS_EXISTENTES.add(url_norm)
        print(f"  ✅ [GUARDADO {registro['lead_score']}pts | {registro['canal_ataque']}] {empresa}")
    except Exception as e:
        print(f"  ❌ [ERROR SUPABASE] {e}")

# 1. CANAL LINKEDIN X-RAY (DECISORES CON NOMBRE Y APELLIDO)
def canal_linkedin_decisores():
    print("\n--- Canal 1: LinkedIn X-Ray Decisores ---")
    queries = [
        'site:es.linkedin.com/in "Pricing" transitario',
        'site:es.linkedin.com/in "Director de Operaciones" transitario',
        'site:es.linkedin.com/in "Jefe de Tráfico" transitario',
        'site:es.linkedin.com/in "Aduanas" transitario Barcelona OR Valencia'
    ]
    for q in queries:
        resultados = buscar_dual(q, max_results=6)
        for r in resultados:
            link = (r.get('href') or '').split("?")[0]
            url_norm = link.rstrip("/").lower()
            titulo = r.get('title') or ''
            body = r.get('body') or ''
            if "linkedin.com/in/" not in link or link in URLS_VISTAS or url_norm in URLS_EXISTENTES:
                continue
            if not es_relevante_sector(f"{titulo} {body}"):
                continue
            URLS_VISTAS.add(link)

            ia = procesar_con_ia("LINKEDIN_DECISOR", f"URL: {link}\nTítulo: {titulo}\nExtracto: {body}")
            if not ia or not ia.get("es_valido"):
                continue

            guardar_oportunidad({
                "lead_score": ia.get("lead_score", 88),
                "canal_ataque": "LINKEDIN_DECISOR",
                "empresa": (ia.get("empresa") or "Transitaria")[:80],
                "nombre_contacto": (ia.get("nombre_contacto") or "Directivo")[:60],
                "cargo": (ia.get("cargo") or "Operaciones / Pricing")[:60],
                "canal_destino": "LINKEDIN_PERFIL",
                "url_accion": link,
                "mensaje_gancho": (ia.get("mensaje_gancho") or "")[:220],
                "guion_cierre": ia.get("guion_cierre") or "",
                "sincronizado_sheet": False
            })
            time.sleep(0.8)

# 2. CANAL FORMULARIOS WEB CORPORATIVOS (EMPRESAS CONTRATANDO + ATEIA)
def canal_formularios_web():
    print("\n--- Canal 2: Formularios Web Corporativos (Empleo VIP + ATEIA) ---")
    queries = [
        'transitario agente de aduanas Barcelona contacto',
        'transitario agente de aduanas Valencia contacto',
        'transitario agente de aduanas Madrid contacto',
        'transitario agente de aduanas Bilbao Algeciras Vigo contacto',
        'oferta empleo pricing transitario aduanas España'
    ]
    for q in queries:
        resultados = buscar_dual(q, max_results=6)
        for r in resultados:
            titulo = r.get('title') or ''
            body = r.get('body') or ''
            href = r.get('href') or ''
            if not es_relevante_sector(f"{titulo} {body}"):
                continue

            web_corp = href if es_web_valida(href) else None
            if not web_corp:
                nombre_busqueda = titulo.split("-")[0].split("|")[0].strip()
                if len(nombre_busqueda) > 3:
                    busq = buscar_dual(f'"{nombre_busqueda}" transitario aduanas España', max_results=3)
                    web_corp = next((x.get('href') for x in busq if es_web_valida(x.get('href'))), None)

            if not web_corp:
                continue

            dom = dominio_limpio(web_corp)
            if dom in DOMINIOS_VISTOS:
                continue

            url_contacto, texto_web = localizar_formulario_y_contexto(web_corp)
            if not url_contacto or url_contacto.rstrip("/").lower() in URLS_EXISTENTES:
                continue
            if not es_relevante_sector(f"{titulo} {body} {texto_web}"):
                continue

            DOMINIOS_VISTOS.add(dom)
            canal = "FORMULARIO_EMPLEO_VIP" if "empleo" in q else "FORMULARIO_ATEIA"
            ia = procesar_con_ia(canal, f"Web: {web_corp}\nTítulo: {titulo}\nExtracto: {body}\nTexto web: {texto_web[:250]}")
            if not ia or not ia.get("es_valido"):
                continue

            empresa_final = ia.get("empresa") or dom.split(".")[0].capitalize()
            guardar_oportunidad({
                "lead_score": ia.get("lead_score", 90 if canal == "FORMULARIO_EMPLEO_VIP" else 78),
                "canal_ataque": canal,
                "empresa": empresa_final[:80],
                "nombre_contacto": "Responsable Operaciones / Pricing",
                "cargo": "Dirección Operaciones",
                "canal_destino": "FORMULARIO_WEB",
                "url_accion": url_contacto,
                "mensaje_gancho": ia.get("mensaje_gancho") or "",
                "guion_cierre": ia.get("guion_cierre") or "",
                "sincronizado_sheet": False
            })
            time.sleep(0.8)

# 3. CANAL SECUESTRO DE ATENCION EN PRENSA LOGISTICA
def canal_prensa_sectorial():
    print("\n--- Canal 3: Noticias del Día en Prensa Logística ---")
    queries = [
        'site:diariodelpuerto.com transitarios fletes aduanas',
        'site:elmercantil.com transitarios fletes aduanas',
        'site:cadenadesuministro.es transitarios maritimo aduanas'
    ]
    for q in queries:
        resultados = buscar_dual(q, max_results=4)
        for r in resultados:
            link = r.get('href') or ''
            titulo = r.get('title') or ''
            body = r.get('body') or ''
            if not link or not titulo or link in URLS_VISTAS or link.rstrip("/").lower() in URLS_EXISTENTES:
                continue
            if not any(d in link.lower() for d in ["diariodelpuerto.com", "elmercantil.com", "cadenadesuministro.es", "transportexxi.com"]):
                continue
            URLS_VISTAS.add(link)

            ia = procesar_con_ia("PRENSA_COMENTARIO", f"Noticia: {titulo}\nResumen: {body}")
            if not ia or not ia.get("es_valido"):
                continue

            guardar_oportunidad({
                "lead_score": 72,
                "canal_ataque": "PRENSA_COMENTARIO",
                "empresa": f"Noticia: {titulo[:65]}",
                "nombre_contacto": "Post Sectorial",
                "cargo": "Visibilidad LinkedIn",
                "canal_destino": "PRENSA_COMENTARIO",
                "url_accion": link,
                "mensaje_gancho": ia.get("mensaje_gancho") or "",
                "guion_cierre": "Abrir noticia/LinkedIn y publicar comentario técnico desde perfil/página LogiQuote.",
                "sincronizado_sheet": False
            })
            time.sleep(0.8)

# 4. CANAL MULTIPLICADORES (CONSULTORES OEA Y ESCUELAS COMERCIO INTERNACIONAL)
def canal_multiplicadores():
    print("\n--- Canal 4: Consultores OEA y Escuelas de Comercio Internacional ---")
    queries = [
        'site:es.linkedin.com/in "Consultor OEA"',
        'site:es.linkedin.com/in "Auditor Aduanero"',
        'site:es.linkedin.com/in "Comercio Internacional" director escuela',
        'site:es.linkedin.com/in "Comercio Internacional" coordinador'
    ]
    for q in queries:
        resultados = buscar_dual(q, max_results=5)
        for r in resultados:
            link = (r.get('href') or '').split("?")[0]
            url_norm = link.rstrip("/").lower()
            titulo = r.get('title') or ''
            body = r.get('body') or ''
            if "linkedin.com/in/" not in link or link in URLS_VISTAS or url_norm in URLS_EXISTENTES:
                continue
            if not es_relevante_sector(f"{titulo} {body}"):
                continue
            URLS_VISTAS.add(link)

            subcanal = "ESCUELA_COMERCIO" if any(w in f"{titulo} {body}".lower() for w in ["escuela", "school", "cfgs", "master", "universidad", "formacion"]) else "CONSULTOR_OEA"
            ia = procesar_con_ia(subcanal, f"URL: {link}\nTítulo: {titulo}\nExtracto: {body}")
            if not ia or not ia.get("es_valido"):
                continue

            guardar_oportunidad({
                "lead_score": 84,
                "canal_ataque": subcanal,
                "empresa": (ia.get("empresa") or "Entidad Sectorial")[:80],
                "nombre_contacto": (ia.get("nombre_contacto") or "Responsable")[:60],
                "cargo": (ia.get("cargo") or "Consultor / Director")[:60],
                "canal_destino": "LINKEDIN_PARTNER",
                "url_accion": link,
                "mensaje_gancho": (ia.get("mensaje_gancho") or "")[:220],
                "guion_cierre": ia.get("guion_cierre") or "",
                "sincronizado_sheet": False
            })
            time.sleep(0.8)

if __name__ == "__main__":
    print("=== INICIANDO GRANJA LOGIQUOTE 360° (CERO-EMAIL Y CERO-TELÉFONO) ===")
    print(f"Estado claves -> SUPABASE_URL: {'OK' if SUPABASE_URL else 'FALTA'} | SUPABASE_KEY: {'OK' if SUPABASE_KEY else 'FALTA'} | GROQ_KEY: {'OK (' + GROQ_KEY[:7] + '...)' if GROQ_KEY else 'FALTA'}")
    canal_linkedin_decisores()
    canal_formularios_web()
    canal_prensa_sectorial()
    canal_multiplicadores()
    print("\n=== CICLO MULTICANAL FINALIZADO ===")
