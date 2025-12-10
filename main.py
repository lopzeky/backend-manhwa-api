from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from deep_translator import GoogleTranslator
import pytesseract
from PIL import Image
import requests
import io

app = FastAPI()

# Configurar CORS (Para que Netlify pueda hablar con este Backend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # En producción cambiar esto por tu URL de Netlify
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- FUNCIÓN 1: ESCANEAR URL (Scraping Seguro) ---
@app.post("/scan")
def escanear_capitulo(payload: dict = Body(...)):
    url = payload.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="Falta la URL")

    print(f"🌍 Analizando: {url}")
    
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # Navegar con timeout de 30 segs
            try:
                response = page.goto(url, timeout=30000, wait_until="domcontentloaded")
            except PlaywrightTimeout:
                browser.close()
                raise HTTPException(status_code=408, detail="El sitio web tardó mucho en responder. Intenta otro.")
            
            # Verificar Bloqueos Anti-Bot
            if "Just a moment" in page.title() or response.status == 403:
                browser.close()
                raise HTTPException(status_code=403, detail="Sitio protegido contra bots (Cloudflare). Prueba otra web.")

            # Extraer imágenes grandes (filtro anti-iconos)
            imagenes = page.evaluate("""
                () => {
                    return Array.from(document.querySelectorAll('img'))
                        .filter(img => img.naturalWidth > 400) # Solo imágenes anchas
                        .map(img => img.src)
                }
            """)
            
            browser.close()
            
            if not imagenes:
                raise HTTPException(status_code=422, detail="No encontré imágenes de manhwa. El sitio puede ser incompatible.")

            return {"status": "ok", "total": len(imagenes), "imagenes": imagenes}

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")

# --- FUNCIÓN 2: TRADUCIR UNA IMAGEN ---
@app.post("/traducir-imagen")
def traducir_imagen(payload: dict = Body(...)):
    img_url = payload.get("img_url")
    idioma_destino = payload.get("lang", "es") # Por defecto español
    
    try:
        # 1. Descargar la imagen
        response = requests.get(img_url, stream=True)
        response.raise_for_status()
        img = Image.open(io.BytesIO(response.content))

        # 2. OCR (Leer Texto)
        text = pytesseract.image_to_string(img)
        
        if not text.strip():
            return {"texto_original": "", "texto_traducido": "(Sin texto detectado)"}

        # 3. Traducir
        translator = GoogleTranslator(source='auto', target=idioma_destino)
        traducido = translator.translate(text)

        return {"texto_original": text, "texto_traducido": traducido}

    except Exception as e:
        return {"error": str(e)}