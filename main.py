from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from deep_translator import GoogleTranslator
import pytesseract
from PIL import Image
import io
import gc
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from tenacity import retry, stop_after_attempt, wait_fixed

app = FastAPI()

# Configuración de permisos
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

# --- FUNCIÓN DE DESCARGA RESILIENTE (CON REINTENTOS) ---
# --- FUNCIÓN DE DESCARGA RESILIENTE (MEJORADA) ---
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def descargar_seguro(url, timeout=15):
    # ELIMINAMOS las cabeceras manuales para evitar errores de huella digital.
    # Dejamos que curl_cffi genere todo automáticamente.
    
    return cffi_requests.get(
        url, 
        # Actualizamos a una versión más reciente de Chrome
        impersonate="chrome124", 
        timeout=timeout
    )
# --- ENDPOINT 1: ESCANEAR ---
@app.post("/scan")
def escanear_capitulo(payload: dict = Body(...)):
    url = payload.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="Falta la URL")
    
    print(f"🥷 Analizando con Tenacity + Curl_Cffi: {url}")
    
    try:
        # Usamos la función inteligente que reintenta si falla
        response = descargar_seguro(url)
        
        if response.status_code == 403:
            raise HTTPException(status_code=403, detail="Sitio protegido. Intenta con Manganato.")
        
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail=f"El sitio respondió con error: {response.status_code}")

        soup = BeautifulSoup(response.text, 'lxml')
        imagenes = []

        for img in soup.find_all('img'):
            src = img.get('data-src') or img.get('data-original') or img.get('data-lazy-src') or img.get('src')
            
            if src:
                src = src.strip()
                if src.startswith('//'): src = 'https:' + src
                
                if src.startswith('http'):
                    src_lower = src.lower()
                    if any(x in src_lower for x in ['logo', 'avatar', 'icon', 'banner', 'ads', 'facebook', 'twitter']):
                        continue
                    imagenes.append(src)

        imagenes_unicas = list(dict.fromkeys(imagenes))

        if len(imagenes_unicas) < 3:
             raise HTTPException(status_code=422, detail="No encontré imágenes válidas.")

        return {"status": "ok", "total": len(imagenes_unicas), "imagenes": imagenes_unicas}

    except Exception as e:
        print(f"Error: {e}")
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")

# --- ENDPOINT 2: TRADUCIR ---
@app.post("/traducir-imagen")
def traducir_imagen(payload: dict = Body(...)):
    img_url = payload.get("img_url")
    modo = payload.get("modo", "en_es")
    cfg = CONFIG_IDIOMAS.get(modo, CONFIG_IDIOMAS["en_es"])

    try:
        response = descargar_seguro(img_url, timeout=10)
        
        if response.status_code != 200:
            return {"texto_traducido": "(Error descargando imagen)"}

        img = Image.open(io.BytesIO(response.content))
        
        # Optimización RAM
        img = img.convert('L')
        width, height = img.size
        if width > 1500:
            ratio = 1500 / width
            new_height = int(height * ratio)
            img = img.resize((1500, new_height), Image.Resampling.LANCZOS)

        try:
            text = pytesseract.image_to_string(img, lang=cfg["ocr"])
        except:
            text = pytesseract.image_to_string(img, lang="eng")
        
        del img
        gc.collect()

        if not text.strip():
            return {"texto_traducido": "(Sin texto detectado)"}

        translator = GoogleTranslator(source=cfg["src"], target=cfg["dest"])
        traducido = translator.translate(text)

        return {"texto_original": text, "texto_traducido": traducido}

    except Exception as e:
        return {"texto_traducido": f"Error: {str(e)}"}

