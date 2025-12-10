from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from deep_translator import GoogleTranslator
import pytesseract
from PIL import Image
import io
import gc
from bs4 import BeautifulSoup
# IMPORTANTE: Usamos curl_cffi en lugar de requests normal
from curl_cffi import requests as cffi_requests

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

# --- FUNCIÓN DE ESCANEO "NINJA" (CURL_CFFI) ---
@app.post("/scan")
def escanear_capitulo(payload: dict = Body(...)):
    url = payload.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="Falta la URL")
    
    print(f"🥷 Analizando con Camuflaje (curl_cffi): {url}")
    
    # Cabeceras estándar
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Referer": "https://www.google.com/"
    }

    try:
        # 1. Descargamos el HTML fingiendo ser Chrome 110 (impersonate)
        # Esto salta la mayoría de protecciones Cloudflare básicas
        response = cffi_requests.get(
            url, 
            headers=headers, 
            impersonate="chrome110", 
            timeout=15
        )
        
        if response.status_code == 403:
            raise HTTPException(status_code=403, detail="Sitio protegido nivel Dios. Intenta con Manganato.")
        
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail=f"El sitio respondió con error: {response.status_code}")

        # 2. Analizamos el HTML buscando imágenes
        soup = BeautifulSoup(response.text, 'lxml')
        imagenes = []

        for img in soup.find_all('img'):
            # Buscamos en atributos donde suelen esconder la imagen real (Lazy Load)
            src = img.get('data-src') or img.get('data-original') or img.get('data-lazy-src') or img.get('src')
            
            if src:
                src = src.strip()
                # Corregir links que empiezan con //
                if src.startswith('//'):
                    src = 'https:' + src
                
                if src.startswith('http'):
                    src_lower = src.lower()
                    # Filtros de basura (logos, anuncios)
                    if any(x in src_lower for x in ['logo', 'avatar', 'icon', 'banner', 'ads', 'facebook', 'twitter']):
                        continue
                    
                    imagenes.append(src)

        # Eliminar duplicados
        imagenes_unicas = list(dict.fromkeys(imagenes))

        if len(imagenes_unicas) < 3:
             raise HTTPException(status_code=422, detail="No encontré imágenes válidas. El sitio requiere Javascript complejo.")

        return {"status": "ok", "total": len(imagenes_unicas), "imagenes": imagenes_unicas}

    except Exception as e:
        print(f"Error: {e}")
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")

@app.post("/traducir-imagen")
def traducir_imagen(payload: dict = Body(...)):
    img_url = payload.get("img_url")
    modo = payload.get("modo", "en_es")
    cfg = CONFIG_IDIOMAS.get(modo, CONFIG_IDIOMAS["en_es"])

    try:
        # Usamos curl_cffi también aquí para que no bloqueen la descarga de la imagen
        response = cffi_requests.get(
            img_url, 
            impersonate="chrome110", 
            timeout=10
        )
        
        if response.status_code != 200:
            return {"texto_traducido": "(Error descargando imagen)"}

        img = Image.open(io.BytesIO(response.content))
        
        # Optimización RAM (Blanco y Negro + Reducción)
        img = img.convert('L')
        width, height = img.size
        if width > 1500:
            ratio = 1500 / width
            new_height = int(height * ratio)
            img = img.resize((1500, new_height), Image.Resampling.LANCZOS)

        # OCR
        try:
            text = pytesseract.image_to_string(img, lang=cfg["ocr"])
        except:
            text = pytesseract.image_to_string(img, lang="eng")
        
        del img
        gc.collect()

        if not text.strip():
            return {"texto_traducido": "(Sin texto detectado)"}

        # Traducir
        translator = GoogleTranslator(source=cfg["src"], target=cfg["dest"])
        traducido = translator.translate(text)

        return {"texto_original": text, "texto_traducido": traducido}

    except Exception as e:
        return {"texto_traducido": f"Error: {str(e)}"}
