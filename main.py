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

#     DICCIONARIO DE IDIOMAS 
# Configura cómo debe comportarse la IA para cada opción
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
                raise HTTPException(status_code=408, detail="Timeout: El sitio tardó mucho.")
            
            # Script para sacar imágenes (filtro mejorado)
            imagenes = page.evaluate("""
                () => {
                    return Array.from(document.querySelectorAll('img'))
                        .filter(img => img.naturalWidth > 300 && img.src.startsWith('http'))
                        .map(img => img.src)
                }
            """)
            browser.close()
            return {"status": "ok", "total": len(imagenes), "imagenes": imagenes}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.post("/traducir-imagen")
def traducir_imagen(payload: dict = Body(...)):
    img_url = payload.get("img_url")
    modo = payload.get("modo", "en_es") # Por defecto Inglés a Español

    # Cargar configuración según el modo seleccionado
    cfg = CONFIG_IDIOMAS.get(modo, CONFIG_IDIOMAS["en_es"])

    try:
        response = requests.get(img_url, stream=True)
        img = Image.open(io.BytesIO(response.content))

        # 1. OCR usando el idioma correcto (eng, spa, o kor)
        text = pytesseract.image_to_string(img, lang=cfg["ocr"])
        
        if not text.strip():
            return {"texto_traducido": "(Sin texto detectado)"}

        # 2. Traducir usando origen y destino correctos
        translator = GoogleTranslator(source=cfg["src"], target=cfg["dest"])
        traducido = translator.translate(text)

        return {"texto_original": text, "texto_traducido": traducido}

    except Exception as e:
        return {"texto_traducido": f"Error: {str(e)}"}
