import os
import re
import json
import time
import requests
import warnings
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from ddgs import DDGS
from supabase import create_client, Client

warnings.filterwarnings("ignore")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
GROQ_KEY = os.environ.get("GROQ_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
}

ETTS_PROHIBIDAS = [
    "randstad", "adecco", "manpower", "hays", "michael page", "robert walters",
    "synergie", "gi group", "temporing", "nortempo", "grupo crit", "eurofirms",
    "faster", "iman", "page personnel", "spring professional", "seleccion", "rrhh", "jobandtalent"
]

DOMINIOS_PROHIBIDOS = [
    "infojobs", "linkedin", "indeed", "glassdoor", "tecnoempleo", "jobatus",
    "jooble", "opcionempleo", "trabajos.com", "paginasamarillas", "einforma",
    "infocif", "empresite", "axesor", "iberinform", "kompass", "europages",
    "facebook", "instagram", "twitter", "youtube", "google", "boe.es",
    "sentry", "wix", "example", "wordpress", "cloudflare", "github"
]

def cargar_empresas_existentes():
    """Escudo anti-duplicados cruzado: lee empresas ya captadas en prospectos y en granja_cero_email."""
    existentes = set()
    if not supabase:
        return existentes
    try:
        r1 = supabase.table("prospectos").select("empresa").execute()
        for row in (r1.data or []):
            if row.get("empresa"):
                existentes.add(row["empresa"].lower().strip()[:25])
        r2 = supabase.table("granja_cero_email").select("empresa").execute()
        for row in (r2.data or []):
            if row.get("empresa"):
                existentes.add(row["empresa"].lower().strip()[:25])
    except Exception as e:
        print(f"Aviso cargando historico: {e}")
    return existentes

EMPRESAS_EXISTENTES = cargar_empresas_existentes()

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
    if any(p in dom for p in DOMINIOS_PROHIBIDOS):
        return False
    if any(ett in dom for ett in ETTS_PROHIBIDAS):
        return False
    return True

def localizar_formulario_y_contexto(url):
    """Detecta la pagina de contacto (/contacto) y extrae contexto operativo de la web."""
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
                if len(texto_web) > 60:
                    break
        except Exception:
            continue

    return url_contacto, texto_web

def procesar_con_ia(canal, empresa, contacto, cargo, contexto):
    if not GROQ_KEY:
        return None

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers_groq = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}

    prompt = """Eres el Director Comercial B2B de LogiQuote (https://logiquote-saas.vercel.app), software español para transitarios y agentes de aduanas que automatiza el cálculo de fletes marítimos/aéreos, recargos THC/BAF y márgenes en 30 segundos sin Excel.
Analiza este objetivo y devuelve ÚNICAMENTE un JSON válido con estas 4 claves:
- "es_valido": boolean (true SOLO si pertenece al sector transitario, aduanero, comercio exterior o prensa logística en España; false si es ETT, irrelevante o competidor).
- "lead_score": entero de 0 a 100 (92 si es Empleo VIP; 88 si es Decisor LinkedIn; 83 si es Consultor OEA o Escuela; 78 si es Transitaria ATEIA; 72 si es Noticia Prensa).
- "mensaje_gancho": string corto y formal (tratando de usted):
   * Si canal es LINKEDIN_DECISOR o MULTIPLICADOR_OEA: invitación a conectar en LinkedIn de MÁXIMO 210 caracteres, SIN enlaces.
   * Si canal es PRENSA_COMENTARIO: comentario técnico experto (máximo 350 caracteres) para publicar en la noticia de LinkedIn aportando visión sobre agilidad en cotizaciones y márgenes.
   * Si canal es FORMULARIO_EMPLEO_VIP o FORMULARIO_ATEIA: asunto corto de 1 línea para el formulario web corporativo.
- "guion_cierre": string formal (tratando de usted, 3-4 líneas) para cerrar tras conectar en LinkedIn o pegar en el formulario web /contacto de la empresa, mencionando siempre los 14 días de prueba gratuita sin compromiso y el código LOGI10 (10% de descuento el primer mes) en https://logiquote-saas.vercel.app (en el caso de CONSULTOR_OEA propone alianza con 25% de comisión recurrente de por vida; en ESCUELA_COMERCIO propone licencias educativas gratuitas)."""

    data = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"Canal: {canal}\nEmpresa: {empresa}\nContacto: {contacto}\nCargo: {cargo}\nContexto: {contexto}"}
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"}
    }

    try:
        res = requests.post(url, headers=headers_groq, json=data, timeout=15)
        if res.status_code == 200:
            return json.loads(res.json()['choices'][0]['message']['content'])
    except Exception:
        pass
    return None

def guardar_oportunidad(registro):
    if not supabase:
        return
    clave = registro["empresa"].lower().strip()[:25]
    if clave in EMPRESAS_EXISTENTES:
        return

    try:
        supabase.table("granja_cero_email").insert(registro).execute()
        EMPRESAS_EXISTENTES.add(clave)
        print(f"[OK {registro['lead_score']}pts | {registro['canal_ataque']}] {registro['empresa']}")
    except Exception as e:
        print(f"[ERROR SUPABASE] {e}")

# 1. CANAL LINKEDIN X-RAY (DECISORES CON NOMBRE Y APELLIDO)
def canal_linkedin_decisores():
    print("\n--- Canal 1: LinkedIn X-Ray Decisores ---")
    queries = [
        'site:es.linkedin.com/in ("Pricing Manager" OR "Responsable de Pricing" OR "Jefe de Tráfico") ("transitario" OR "aduanas" OR "freight forwarder")',
        'site:es.linkedin.com/in ("Director de Operaciones" OR "Gerente") ("transitario" OR "agente de aduanas") ("Barcelona" OR "Valencia" OR "Madrid" OR "Algeciras" OR "Bilbao")'
    ]
    for q in queries:
        try:
            for r in DDGS().text(q, max_results=7):
                link = r.get('href', '')
                titulo = r.get('title', '')
                body = r.get('body', '')
                if "linkedin.com/in/" not in link:
                    continue

                partes = titulo.split("-")
                nombre = partes[0].strip() if len(partes) > 0 else ""
                cargo = partes[1].strip() if len(partes) > 1 else "Operaciones / Pricing"
                empresa = partes[2].replace("| LinkedIn", "").strip() if len(partes) > 2 else ""

                if not nombre or not empresa or len(empresa) < 3:
                    continue

                ia = procesar_con_ia("LINKEDIN_DECISOR", empresa, nombre, cargo, f"{titulo}. {body}")
                if not ia or not ia.get("es_valido"):
                    continue

                guardar_oportunidad({
                    "lead_score": ia.get("lead_score", 88),
                    "canal_ataque": "LINKEDIN_DECISOR",
                    "empresa": empresa[:80],
                    "nombre_contacto": nombre[:60],
                    "cargo": cargo[:60],
                    "canal_destino": "LINKEDIN_PERFIL",
                    "url_accion": link,
                    "mensaje_gancho": ia.get("mensaje_gancho", "")[:220],
                    "guion_cierre": ia.get("guion_cierre", ""),
                    "sincronizado_sheet": False
                })
                time.sleep(1)
        except Exception as e:
            print(f"Error Canal 1: {e}")

# 2. CANAL FORMULARIOS WEB CORPORATIVOS (EMPRESAS CONTRATANDO PRICING/ADUANAS + ATEIA)
def canal_formularios_web():
    print("\n--- Canal 2: Formularios Web Corporativos (Empleo VIP + ATEIA) ---")
    queries = [
        'site:infojobs.net ("pricing" OR "cotizaciones") ("transitario" OR "maritimo" OR "aduanas")',
        '("oferta de empleo" OR "buscamos") ("tecnico de pricing" OR "cotizaciones maritimas") ("Barcelona" OR "Valencia" OR "Madrid" OR "Algeciras")',
        '"ATEIA" ("Barcelona" OR "Valencia" OR "Madrid" OR "Algeciras" OR "Bilbao") transitario'
    ]
    for q in queries:
        try:
            for r in DDGS().text(q, max_results=6):
                titulo = r.get('title', '')
                body = r.get('body', '')
                href = r.get('href', '')
                if any(ett in f"{titulo} {body}".lower() for ett in ETTS_PROHIBIDAS):
                    continue

                web_corp = href if es_web_valida(href) else None
                if not web_corp:
                    nombre_busqueda = titulo.split("-")[0].strip()
                    busq = DDGS().text(f'"{nombre_busqueda}" transitario OR aduanas España', max_results=3)
                    web_corp = next((x.get('href') for x in busq if es_web_valida(x.get('href'))), None)

                if not web_corp:
                    continue

                url_contacto, texto_web = localizar_formulario_y_contexto(web_corp)
                if not url_contacto:
                    continue

                empresa = dominio_limpio(web_corp).split(".")[0].capitalize()
                canal = "FORMULARIO_EMPLEO_VIP" if "infojobs" in href or "buscamos" in q else "FORMULARIO_ATEIA"

                ia = procesar_con_ia(canal, empresa, "Responsable Operaciones / Pricing", "Dirección Operaciones", f"{titulo}. {body}. {texto_web[:250]}")
                if not ia or not ia.get("es_valido"):
                    continue

                guardar_oportunidad({
                    "lead_score": ia.get("lead_score", 90 if canal == "FORMULARIO_EMPLEO_VIP" else 78),
                    "canal_ataque": canal,
                    "empresa": empresa[:80],
                    "nombre_contacto": "Responsable Operaciones / Pricing",
                    "cargo": "Dirección Operaciones",
                    "canal_destino": "FORMULARIO_WEB",
                    "url_accion": url_contacto,
                    "mensaje_gancho": ia.get("mensaje_gancho", ""),
                    "guion_cierre": ia.get("guion_cierre", ""),
                    "sincronizado_sheet": False
                })
                time.sleep(1)
        except Exception as e:
            print(f"Error Canal 2: {e}")

# 3. CANAL SECUESTRO DE ATENCION EN PRENSA LOGISTICA
def canal_prensa_sectorial():
    print("\n--- Canal 3: Noticias del Día en Prensa Logística ---")
    queries = [
        'site:linkedin.com/posts ("diario del puerto" OR "el mercantil" OR "cadena de suministro") ("fletes" OR "transitarios" OR "aduanas" OR "puertos")',
        '("diariodelpuerto.com" OR "elmercantil.com" OR "cadenadesuministro.es") ("transitarios" OR "fletes maritimos" OR "aduanas")'
    ]
    for q in queries:
        try:
            for r in DDGS().text(q, max_results=4):
                link = r.get('href', '')
                titulo = r.get('title', '')
                body = r.get('body', '')
                if not link or not titulo:
                    continue

                ia = procesar_con_ia("PRENSA_COMENTARIO", titulo[:60], "Comunidad Logística", "Comentario Público", f"{titulo}. {body}")
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
                    "mensaje_gancho": ia.get("mensaje_gancho", ""),
                    "guion_cierre": "Publicar comentario en los primeros 30 min desde perfil/página LogiQuote.",
                    "sincronizado_sheet": False
                })
                time.sleep(1)
        except Exception as e:
            print(f"Error Canal 3: {e}")

# 4. CANAL MULTIPLICADORES (CONSULTORES OEA Y ESCUELAS COMERCIO INTERNACIONAL)
def canal_multiplicadores():
    print("\n--- Canal 4: Consultores OEA y Escuelas de Comercio Internacional ---")
    queries = [
        'site:es.linkedin.com/in ("Consultor OEA" OR "Auditor Aduanero" OR "Asesor Aduanero") España',
        'site:es.linkedin.com/in ("Director" OR "Coordinador") ("Comercio Internacional" OR "Logística") ("Escuela" OR "Business School" OR "CFGS") España'
    ]
    for q in queries:
        try:
            for r in DDGS().text(q, max_results=5):
                link = r.get('href', '')
                titulo = r.get('title', '')
                body = r.get('body', '')
                if "linkedin.com/in/" not in link:
                    continue

                partes = titulo.split("-")
                nombre = partes[0].strip() if len(partes) > 0 else "Responsable"
                cargo = partes[1].strip() if len(partes) > 1 else "Consultor / Director"
                empresa = partes[2].replace("| LinkedIn", "").strip() if len(partes) > 2 else "Entidad Sectorial"
                subcanal = "ESCUELA_COMERCIO" if any(w in titulo.lower() for w in ["escuela", "school", "cfgs", "master", "universidad"]) else "CONSULTOR_OEA"

                ia = procesar_con_ia(subcanal, empresa, nombre, cargo, f"{titulo}. {body}")
                if not ia or not ia.get("es_valido"):
                    continue

                guardar_oportunidad({
                    "lead_score": 84,
                    "canal_ataque": subcanal,
                    "empresa": empresa[:80],
                    "nombre_contacto": nombre[:60],
                    "cargo": cargo[:60],
                    "canal_destino": "LINKEDIN_PARTNER",
                    "url_accion": link,
                    "mensaje_gancho": ia.get("mensaje_gancho", "")[:220],
                    "guion_cierre": ia.get("guion_cierre", ""),
                    "sincronizado_sheet": False
                })
                time.sleep(1)
        except Exception as e:
            print(f"Error Canal 4: {e}")

if __name__ == "__main__":
    print("=== INICIANDO GRANJA LOGIQUOTE 360° (CERO-EMAIL Y CERO-TELÉFONO) ===")
    canal_linkedin_decisores()
    canal_formularios_web()
    canal_prensa_sectorial()
    canal_multiplicadores()
    print("\n=== CICLO MULTICANAL FINALIZADO ===")
