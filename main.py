from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from deep_translator import GoogleTranslator
import pytesseract
from PIL import Image
import requests
import io
import gc  # Recolector de basura para limpiar RAM

app = FastAPI()

# Configuración de permisos CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuración de Idiomas
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
    
    print(f"🌍 Analizando (Modo Humano): {url}")
    
    # 1. Argumentos para ahorrar RAM en Render (Vital para 512MB)
    chrome_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-accelerated-2d-canvas",
        "--no-first-run",
        "--no-zygote",
        "--single-process",
        "--disable-gpu"
    ]

    with sync_playwright() as p:
        try:
            # Lanzamos el navegador
            browser = p.chromium.launch(headless=True, args=chrome_args)
            
            # 2. EL CABALLO DE TROYA: Configuración de "Humano Real"
            context = browser.new_context(
                # Fingimos ser un PC con Windows 10 y Chrome reciente
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                # Tamaño de pantalla de laptop estándar
                viewport={"width": 1366, "height": 768},
                # Idioma y zona horaria de Chile (para parecer local)
                locale="es-CL",
                timezone_id="America/Santiago",
                # Cabeceras HTTP extra para dar confianza
                extra_http_headers={
                    "Accept-Language": "es-CL,es;q=0.9,en;q=0.8",
                    "Referer": "https://www.google.com/"  # Fingimos venir de Google
                }
            )
            
            page = context.new_page()
            
            # Bloqueo de recursos pesados para ahorrar RAM (No cargamos CSS/Fuentes/Imágenes del sitio, solo el HTML)
            page.route("**/*", lambda route: route.continue_() if route.request.resource_type in ["document", "script", "xhr", "fetch"] else route.abort())

            # Tiempo de espera largo (60s) porque Render gratis es lento
            try:
                page.goto(url, timeout=60000, wait_until="domcontentloaded")
            except PlaywrightTimeout:
                browser.close()
                raise HTTPException(status_code=408, detail="El sitio tardó mucho en responder (Timeout).")
            
            # Script de extracción limpio (sin comentarios para evitar SyntaxError)
            imagenes = page.evaluate("""
                () => {
                    return Array.from(document.querySelectorAll('img'))
                        .filter(img => img.src.startsWith('http'))
                        .map(img => img.src)
                }
            """)
            
            browser.close()
            gc.collect() # Limpiamos RAM
            
            # Filtro de seguridad en Python
            imagenes_limpias = [img for img in imagenes if len(img) > 10]

            if not imagenes_limpias:
                raise HTTPException(status_code=422, detail="No pude detectar imágenes. El sitio tiene protección avanzada.")

            return {"status": "ok", "total": len(imagenes_limpias), "imagenes": imagenes_limpias}

        except Exception as e:
            print(f"Error: {e}")
            raise HTTPException(status_code=500, detail=f"Error del servidor: {str(e)}")

@app.post("/traducir-imagen")
def traducir_imagen(payload: dict = Body(...)):
    img_url = payload.get("img_url")
    modo = payload.get("modo", "en_es")
    cfg = CONFIG_IDIOMAS.get(modo, CONFIG_IDIOMAS["en_es"])

    try:
        # Descargamos la imagen fingiendo ser un navegador (User-Agent header)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(img_url, headers=headers, stream=True, timeout=15)
        response.raise_for_status()
        
        img = Image.open(io.BytesIO(response.content))

        # --- OPTIMIZACIÓN LOW RAM ---
        # 1. Convertir a Blanco y Negro (Ahorra mucha memoria)
        img = img.convert('L') 

        # 2. Redimensionar si es gigante (>1500px)
        width, height = img.size
        if width > 1500:
            ratio = 1500 / width
            new_height = int(height * ratio)
            img = img.resize((1500, new_height), Image.Resampling.LANCZOS)

        # 3. OCR (Lectura)
        try:
            text = pytesseract.image_to_string(img, lang=cfg["ocr"])
        except:
            text = pytesseract.image_to_string(img, lang="eng")
        
        # Liberar memoria de imagen ya procesada
        del img
        gc.collect()

        if not text.strip():
            return {"texto_traducido": "(Sin texto detectado)"}

        # 4. Traducir Texto
        translator = GoogleTranslator(source=cfg["src"], target=cfg["dest"])
        traducido = translator.translate(text)

        return {"texto_original": text, "texto_traducido": traducido}

    except Exception as e:
        return {"texto_traducido": f"Error: {str(e)}"}
