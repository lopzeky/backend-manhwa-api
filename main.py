from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from deep_translator import GoogleTranslator
import pytesseract
from PIL import Image
import requests
import io
import gc  # Importamos el recolector de basura para liberar RAM

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CONFIG_IDIOMAS = {
    "en_es": {"ocr": "eng", "src": "en", "dest": "es"},
    "es_en": {"ocr": "spa", "src": "es", "dest": "en"},
    "ko_es": {"ocr": "kor", "src": "ko", "dest": "es"},
    "ko_en": {"ocr": "kor", "src": "ko", "dest": "en"}
}

@app.post("/scan")
def escanear_capitulo(payload: dict = Body(...)):
    url = payload.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="Falta la URL")
    
    print(f"🌍 Analizando (Low RAM Mode): {url}")
    
    # Argumentos para que Chrome consuma el mínimo de RAM posible
    chrome_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage", # Vital para Docker/Render
        "--disable-accelerated-2d-canvas",
        "--no-first-run",
        "--no-zygote",
        "--single-process", # Arriesgado pero ahorra mucha RAM
        "--disable-gpu"
    ]

    with sync_playwright() as p:
        try:
            # Lanzamos con los argumentos de optimización
            browser = p.chromium.launch(headless=True, args=chrome_args)
            
            # Contexto mínimo sin cargar cosas innecesarias
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            )
            page = context.new_page()
            
            # Bloquear carga de recursos pesados (imágenes, fuentes, css) para ahorrar RAM
            # Solo necesitamos el HTML para sacar los links
            page.route("**/*", lambda route: route.continue_() if route.request.resource_type in ["document", "script", "xhr", "fetch"] else route.abort())

            try:
                page.goto(url, timeout=45000, wait_until="domcontentloaded")
            except PlaywrightTimeout:
                browser.close()
                raise HTTPException(status_code=408, detail="El sitio tardó mucho (0.1 CPU es lento). Intenta de nuevo.")
            
            # Extraer imágenes
            imagenes = page.evaluate("""
                () => {
                    return Array.from(document.querySelectorAll('img'))
                        .filter(img => img.src.startsWith('http'))
                        .map(img => img.src)
                }
            """)
            
            browser.close()
            gc.collect() # Forzar limpieza de memoria
            
            # Filtro simple en Python para asegurar que son imágenes válidas
            imagenes_limpias = [img for img in imagenes if len(img) > 10]

            if not imagenes_limpias:
                raise HTTPException(status_code=422, detail="No encontré imágenes. Sitio protegido.")

            return {"status": "ok", "total": len(imagenes_limpias), "imagenes": imagenes_limpias}

        except Exception as e:
            print(f"Error: {e}")
            raise HTTPException(status_code=500, detail=f"Error servidor: {str(e)}")

@app.post("/traducir-imagen")
def traducir_imagen(payload: dict = Body(...)):
    img_url = payload.get("img_url")
    modo = payload.get("modo", "en_es")
    cfg = CONFIG_IDIOMAS.get(modo, CONFIG_IDIOMAS["en_es"])

    try:
        # Timeout corto para no colgar el servidor
        response = requests.get(img_url, stream=True, timeout=10)
        response.raise_for_status()
        
        # Cargar imagen
        img = Image.open(io.BytesIO(response.content))

        # --- OPTIMIZACIÓN DE MEMORIA PARA TESSERACT ---
        
        # 1. Convertir a Escala de Grises (Ahorra 66% de RAM vs RGB)
        img = img.convert('L') 

        # 2. Redimensionar si es gigante (Tesseract explota con imágenes de >2000px ancho en 512MB RAM)
        width, height = img.size
        if width > 1500:
            ratio = 1500 / width
            new_height = int(height * ratio)
            img = img.resize((1500, new_height), Image.Resampling.LANCZOS)

        # 1. OCR
        try:
            text = pytesseract.image_to_string(img, lang=cfg["ocr"])
        except:
            text = pytesseract.image_to_string(img, lang="eng")
        
        # Limpiar imagen de la memoria inmediatamente
        del img
        gc.collect()

        if not text.strip():
            return {"texto_traducido": "(Sin texto detectado)"}

        # 2. Traducir
        translator = GoogleTranslator(source=cfg["src"], target=cfg["dest"])
        traducido = translator.translate(text)

        return {"texto_original": text, "texto_traducido": traducido}

    except Exception as e:
        return {"texto_traducido": f"Error: {str(e)}"}
