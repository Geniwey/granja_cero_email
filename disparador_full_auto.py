import os
import re
import time
import random
from supabase import create_client, Client
from playwright.sync_api import sync_playwright

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip()
SUPABASE_KEY = (os.environ.get("SUPABASE_KEY") or "").strip()
LINKEDIN_LI_AT = (os.environ.get("LINKEDIN_LI_AT") or "").strip()
REPLY_EMAIL = (os.environ.get("REPLY_EMAIL") or "contacto@logiquote.es").strip()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

def actualizar_estado(lead_id, estado, detalle=""):
    if not supabase:
        return
    try:
        supabase.table("granja_cero_email").update({
            "estado_envio": estado,
            "detalle_envio": detalle[:250],
            "sincronizado_sheet": False
        }).eq("id", lead_id).execute()
    except Exception as e:
        print(f"  ❌ Error actualizando Supabase ID {lead_id}: {e}")

def cerrar_cookies(page):
    textos_cookie = [
        "Aceptar todo", "Aceptar todas", "Aceptar", "Accept all",
        "Accept", "Consentir", "Estoy de acuerdo", "Allow all"
    ]
    for txt in textos_cookie:
        try:
            btn = page.get_by_role("button", name=re.compile(f"^{txt}$", re.IGNORECASE)).first
            if btn.is_visible(timeout=600):
                btn.click(timeout=1000)
                time.sleep(0.4)
                return
        except Exception:
            continue

def detectar_captcha(page):
    try:
        html = page.content().lower()
        indicadores = ["g-recaptcha", "h-captcha", "cf-turnstile", "challenges.cloudflare.com", "recaptcha/api.js"]
        for ind in indicadores:
            if ind in html:
                iframe_vis = page.locator("iframe[src*='recaptcha'], iframe[src*='hcaptcha'], iframe[src*='turnstile']").first
                if iframe_vis.is_visible(timeout=800):
                    return True
    except Exception:
        pass
    return False

def rellenar_y_enviar_formulario(page, lead):
    url = lead.get("url_accion") or ""
    asunto = lead.get("mensaje_gancho") or "Consulta operativa sobre automatización de cotizaciones"
    mensaje = lead.get("guion_cierre") or ""

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        time.sleep(2)
        cerrar_cookies(page)

        if detectar_captcha(page):
            return "BLOQUEADO_CAPTCHA", "Formulario protegido con CAPTCHA"

        inputs = page.locator("form input:visible, form textarea:visible")
        total = inputs.count()
        if total == 0:
            return "SIN_FORMULARIO", "No se detectaron campos visibles en la URL"

        campos_rellenados = 0
        for i in range(total):
            el = inputs.nth(i)
            try:
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                tipo = (el.get_attribute("type") or "text").lower()
                if tipo in ["hidden", "submit", "button", "image", "file", "radio", "checkbox", "reset"]:
                    continue

                meta = " ".join([
                    el.get_attribute("name") or "",
                    el.get_attribute("id") or "",
                    el.get_attribute("placeholder") or "",
                    el.get_attribute("aria-label") or ""
                ]).lower()

                if tag == "textarea" or any(k in meta for k in ["mensaje", "message", "consulta", "comentario", "body", "descripcion"]):
                    el.fill(mensaje)
                    campos_rellenados += 1
                elif tipo == "email" or any(k in meta for k in ["email", "correo", "e-mail", "mail"]):
                    el.fill(REPLY_EMAIL)
                    campos_rellenados += 1
                elif tipo == "tel" or any(k in meta for k in ["tel", "phone", "movil", "móvil", "telefono", "teléfono"]):
                    if el.get_attribute("required") is not None:
                        el.fill("930000000")
                        campos_rellenados += 1
                elif any(k in meta for k in ["empresa", "company", "organizacion", "compañia"]):
                    el.fill("LogiQuote Software")
                    campos_rellenados += 1
                elif any(k in meta for k in ["asunto", "subject", "motivo", "tema"]):
                    el.fill(asunto)
                    campos_rellenados += 1
                elif any(k in meta for k in ["nombre", "name", "contacto", "apellidos", "persona"]):
                    el.fill("Marc - Equipo LogiQuote")
                    campos_rellenados += 1
            except Exception:
                continue

        if campos_rellenados < 2:
            return "FORM_INCOMPLETO", f"Solo se pudieron mapear {campos_rellenados} campos"

        # Marcar casilla obligatoria de privacidad / RGPD si existe
        checkboxes = page.locator("form input[type='checkbox']:visible")
        for c in range(checkboxes.count()):
            cb = checkboxes.nth(c)
            try:
                if not cb.is_checked():
                    cb.check(force=True)
            except Exception:
                continue

        # Buscar botón de envío dentro del formulario
        btn_submit = page.locator("form button[type='submit']:visible, form input[type='submit']:visible").first
        if not btn_submit.is_visible(timeout=1000):
            btn_submit = page.locator("form button:visible").filter(
                has_text=re.compile(r"(enviar|send|contactar|solicitar|submit)", re.IGNORECASE)
            ).first

        if not btn_submit.is_visible(timeout=1500):
            return "SIN_BOTON_ENVIO", "No se encontró botón de envío en el formulario"

        url_antes = page.url
        btn_submit.click(timeout=5000)
        time.sleep(3.5)

        html_despues = page.content().lower()
        exito_keywords = ["gracias", "enviado", "recibido", "correctamente", "thank", "sent", "éxito", "contactaremos"]
        if page.url != url_antes or any(k in html_despues for k in exito_keywords):
            return "ENVIADO_AUTO", "Formulario enviado y confirmado automáticamente"

        return "ENVIADO_AUTO", "Clic de envío ejecutado sin errores"

    except Exception as e:
        return "ERROR_WEB", str(e)[:180]

def ejecutar_formularios_web(browser):
    print("\n=== FASE 1: DISPARO AUTOMÁTICO DE FORMULARIOS WEB ===")
    res = supabase.table("granja_cero_email").select("*").eq("estado_envio", "PENDIENTE").eq("canal_destino", "FORMULARIO_WEB").limit(15).execute()
    leads = res.data or []
    print(f"📋 Formularios pendientes encontrados: {len(leads)}")

    if not leads:
        return

    context = browser.new_context(
        locale="es-ES",
        viewport={"width": 1366, "height": 768},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    page = context.new_page()

    for lead in leads:
        empresa = lead.get("empresa", "Empresa")
        print(f"  🚀 Atacando formulario web de: {empresa} ({lead.get('url_accion')})")
        estado, detalle = rellenar_y_enviar_formulario(page, lead)
        actualizar_estado(lead["id"], estado, detalle)
        print(f"     -> Resultado: [{estado}] {detalle}")
        time.sleep(random.uniform(2.5, 5.0))

    context.close()

def ejecutar_linkedin_auto(browser):
    print("\n=== FASE 2: PILOTO AUTOMÁTICO LINKEDIN ===")
    if not LINKEDIN_LI_AT:
        print("  ⏸️ Secret LINKEDIN_LI_AT no configurado todavía. Se dejan los contactos de LinkedIn en cola hasta que añadas la cookie.")
        return

    res = supabase.table("granja_cero_email").select("*").eq("estado_envio", "PENDIENTE").in_("canal_destino", ["LINKEDIN_PERFIL", "LINKEDIN_PARTNER"]).limit(6).execute()
    leads = res.data or []
    print(f"📋 Invitaciones LinkedIn en cola (límite seguro 6/ejecución): {len(leads)}")

    if not leads:
        return

    context = browser.new_context(
        locale="es-ES",
        viewport={"width": 1366, "height": 768},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    context.add_cookies([{
        "name": "li_at",
        "value": LINKEDIN_LI_AT,
        "domain": ".linkedin.com",
        "path": "/",
        "secure": True,
        "httpOnly": True
    }])
    page = context.new_page()

    for lead in leads:
        perfil_url = lead.get("url_accion") or ""
        nota = (lead.get("mensaje_gancho") or "")[:210]
        empresa = lead.get("empresa", "Contacto")
        print(f"  🔗 Conectando en LinkedIn con: {lead.get('nombre_contacto')} ({empresa})")

        try:
            page.goto(perfil_url, wait_until="domcontentloaded", timeout=25000)
            time.sleep(random.uniform(4.0, 7.0))

            if "login" in page.url or "checkpoint" in page.url:
                print("  ⚠️ Cookie LINKEDIN_LI_AT caducada o verificación requerida. Deteniendo fase LinkedIn.")
                break

            btn_conectar = page.locator("main button").filter(has_text=re.compile(r"^Conectar$|^Connect$", re.IGNORECASE)).first
            if not btn_conectar.is_visible(timeout=2500):
                btn_mas = page.locator("main button").filter(has_text=re.compile(r"^Más$|^More$", re.IGNORECASE)).first
                if btn_mas.is_visible(timeout=2000):
                    btn_mas.click()
                    time.sleep(1)
                    btn_conectar = page.locator("div[role='button'], span").filter(has_text=re.compile(r"^Conectar$|^Connect$", re.IGNORECASE)).first

            if not btn_conectar.is_visible(timeout=2500):
                actualizar_estado(lead["id"], "OMITIDO_LINKEDIN", "Botón Conectar no disponible (ya conectado o restringido)")
                continue

            btn_conectar.click()
            time.sleep(1.5)

            btn_nota = page.locator("button").filter(has_text=re.compile(r"Añadir una nota|Add a note", re.IGNORECASE)).first
            if btn_nota.is_visible(timeout=2500):
                btn_nota.click()
                time.sleep(1)
                caja = page.locator("textarea[name='message'], textarea#custom-message").first
                caja.fill(nota)
                time.sleep(1)

            btn_enviar = page.locator("button").filter(has_text=re.compile(r"^Enviar$|^Send$|Enviar sin nota", re.IGNORECASE)).first
            if btn_enviar.is_visible(timeout=2500):
                btn_enviar.click()
                actualizar_estado(lead["id"], "ENVIADO_AUTO", "Invitación LinkedIn enviada automáticamente")
                print("     -> ✅ Invitación enviada")
            else:
                actualizar_estado(lead["id"], "ERROR_LINKEDIN", "No apareció botón final de envío")

        except Exception as e:
            actualizar_estado(lead["id"], "ERROR_LINKEDIN", str(e)[:180])
            print(f"     -> ❌ Error: {e}")

        time.sleep(random.uniform(15.0, 30.0))

    context.close()

if __name__ == "__main__":
    if not supabase:
        print("❌ Faltan credenciales de Supabase.")
        exit(1)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ejecutar_formularios_web(browser)
        ejecutar_linkedin_auto(browser)
        browser.close()
    print("\n=== DISPARADOR FULL AUTO COMPLETADO ===")
