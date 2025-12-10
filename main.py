from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from deep_translator import GoogleTranslator
import pytesseract
from PIL import Image
import requests
import io

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- CONFIGURACIÓN DE IDIOMAS ---
# Aquí definimos las reglas para cada traducción
CONFIG_IDIOMAS = {
    "en_es": {"ocr": "eng", "src": "en", "dest": "es"}, # Inglés -> Español
    "es_en": {"ocr": "spa", "src": "es", "dest": "en"}, # Español -> Inglés
    "ko_es": {"ocr": "kor", "src": "ko", "dest": "es"}, # Coreano -> Español
    "ko_en": {"ocr": "kor", "src": "ko", "dest": "en"}  # Coreano -> Inglés
}

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
            
            try:
                page.goto(url, timeout=40000, wait_until="domcontentloaded")
            except PlaywrightTimeout:
                browser.close()
                raise HTTPException(status_code=408, detail="El sitio web tardó demasiado en responder.")
            
            # --- CORRECCIÓN DEL ERROR ROJO ---
            # Eliminamos comentarios dentro del string de JS para evitar SyntaxError
            imagenes = page.evaluate("""
                () => {
                    return Array.from(document.querySelectorAll('img'))
                        .filter(img => img.naturalWidth > 300 && img.src.startsWith('http'))
                        .map(img => img.src)
                }
            """)
            
            browser.close()
            
            if not imagenes:
                raise HTTPException(status_code=422, detail="No encontré imágenes válidas. Sitio protegido.")

            return {"status": "ok", "total": len(imagenes), "imagenes": imagenes}

        except Exception as e:
            print(f"Error: {e}")
            raise HTTPException(status_code=500, detail=f"Error del servidor: {str(e)}")

@app.post("/traducir-imagen")
def traducir_imagen(payload: dict = Body(...)):
    img_url = payload.get("img_url")
    modo = payload.get("modo", "en_es") # Recibimos el modo elegido por el usuario

    # Cargamos la configuración (si no existe, usamos inglés->español por defecto)
    cfg = CONFIG_IDIOMAS.get(modo, CONFIG_IDIOMAS["en_es"])

    try:
        response = requests.get(img_url, stream=True, timeout=10)
        response.raise_for_status()
        img = Image.open(io.BytesIO(response.content))

        # 1. Intentamos leer con el idioma seleccionado
        try:
            text = pytesseract.image_to_string(img, lang=cfg["ocr"])
        except:
            # Si falla (ej: no está instalado el idioma), intentamos inglés
            text = pytesseract.image_to_string(img, lang="eng")
        
        if not text.strip():
            return {"texto_traducido": "(Sin texto detectado)"}

        # 2. Traducimos
        translator = GoogleTranslator(source=cfg["src"], target=cfg["dest"])
        traducido = translator.translate(text)

        return {"texto_original": text, "texto_traducido": traducido}

    except Exception as e:
        return {"texto_traducido": f"Error: {str(e)}"}
