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
    
    print(f"🌍 Analizando (Modo Ciego): {url}")
    
    chrome_args = [
        "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
        "--disable-accelerated-2d-canvas", "--no-first-run", "--no-zygote",
        "--single-process", "--disable-gpu"
    ]

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, args=chrome_args)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720}
            )
            page = context.new_page()
            
            # Bloquear fuentes y CSS para ir más rápido, pero permitimos imágenes para que existan en el DOM
            page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["font", "stylesheet"] else route.continue_())

            try:
                page.goto(url, timeout=60000, wait_until="domcontentloaded")
                # Esperamos un poco extra para que el sitio cargue el HTML dinámico
                page.wait_for_timeout(3000) 
            except PlaywrightTimeout:
                browser.close()
                raise HTTPException(status_code=408, detail="El sitio tardó mucho en responder.")
            
            # --- CAMBIO IMPORTANTE: MODO CIEGO ---
            # Ya no medimos el ancho (naturalWidth) porque el servidor es lento.
            # Solo pedimos todas las etiquetas <img> que tengan src.
            imagenes = page.evaluate("""
                () => {
                    return Array.from(document.querySelectorAll('img'))
                        .map(img => img.src)
                        .filter(src => src.startsWith('http'))
                }
            """)
            
            browser.close()
            gc.collect()

            # --- FILTRADO EN PYTHON (Más rápido) ---
            # Eliminamos basura (logos, iconos, avatares) basándonos en palabras clave
            palabras_basura = ['logo', 'icon', 'avatar', 'thumb', 'banner', 'facebook', 'twitter', 'discord']
            imagenes_limpias = []
            
            for img in imagenes:
                # Si contiene alguna palabra basura, la saltamos
                if any(basura in img.lower() for basura in palabras_basura):
                    continue
                # Si parece una imagen válida, la guardamos
                imagenes_limpias.append(img)

            # Eliminamos duplicados manteniendo el orden
            imagenes_unicas = list(dict.fromkeys(imagenes_limpias))

            if not imagenes_unicas:
                raise HTTPException(status_code=422, detail="No pude detectar imágenes. El sitio tiene protección avanzada.")

            return {"status": "ok", "total": len(imagenes_unicas), "imagenes": imagenes_unicas}

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

