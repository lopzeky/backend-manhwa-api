from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from deep_translator import GoogleTranslator
import pytesseract
from PIL import Image
import io
import gc
import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_fixed

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

# --- FUNCIÓN DE DESCARGA MAESTRA (ZENROWS CORREGIDO) ---
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def descargar_con_zenrows(url, timeout=40):
    
    # 🔴🔴🔴 TU API KEY DE ZENROWS 🔴🔴🔴
    # (Asegúrate de que no tenga espacios al inicio o final)
    API_KEY = "16ec4b42117e5328f574d7cf53b32bbbb17daa75" 
    
    # --- CONFIGURACIÓN CORREGIDA ---
    params = {
        "apikey": API_KEY,
        "url": url,
        "js_render": "true",  # Necesario para sitios modernos
        "antibot": "true",    # <--- ESTA ES LA CLAVE PARA CLOUDFLARE
        "premium_proxy": "true" # Ayuda a evitar bloqueos de IP
    }

    try:
        # Aumentamos el timeout porque el modo 'antibot' tarda un poco más en resolver el captcha
        response = requests.get("https://api.zenrows.com/v1/", params=params, timeout=timeout)
        return response
    except Exception as e:
        print(f"Error conectando con ZenRows: {e}")
        raise e

# --- ENDPOINT 1: ESCANEAR ---
@app.post("/scan")
def escanear_capitulo(payload: dict = Body(...)):
    url = payload.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="Falta la URL")
    
    print(f"🚀 Enviando a ZenRows (Modo Antibot): {url}")
    
    try:
        response = descargar_con_zenrows(url)
        
        # Si ZenRows devuelve error, mostramos el mensaje exacto que nos da
        if response.status_code != 200:
            print(f"❌ Error ZenRows: {response.text}")
            detail_msg = f"ZenRows falló ({response.status_code}): {response.text}"
            raise HTTPException(status_code=400, detail=detail_msg)

        soup = BeautifulSoup(response.text, 'lxml')
        imagenes = []

        for img in soup.find_all('img'):
            # Buscamos en todos los atributos posibles
            src = img.get('data-src') or img.get('data-original') or img.get('data-lazy-src') or img.get('src')
            
            if src:
                src = src.strip()
                if src.startswith('//'): src = 'https:' + src
                
                if src.startswith('http'):
                    src_lower = src.lower()
                    if any(x in src_lower for x in ['logo', 'avatar', 'icon', 'banner', 'ads', 'facebook', 'twitter', 'button']):
                        continue
                    imagenes.append(src)

        imagenes_unicas = list(dict.fromkeys(imagenes))

        if len(imagenes_unicas) < 3:
             # Si no encuentra imágenes, es posible que el selector falle
             print("⚠️ HTML recibido pero sin imágenes claras.")
             raise HTTPException(status_code=422, detail="Pude entrar al sitio, pero no encontré las imágenes (Estructura desconocida).")

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
        # Intento directo primero (ahorra créditos)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        try:
            response = requests.get(img_url, headers=headers, stream=True, timeout=10)
            response.raise_for_status()
        except:
            # Respaldo ZenRows si falla la descarga directa
            print("Fallo directo, usando ZenRows para imagen...")
            response = descargar_con_zenrows(img_url)

        img = Image.open(io.BytesIO(response.content))
        img = img.convert('L') # Optimizar RAM
        
        if img.width > 1500:
            ratio = 1500 / img.width
            img = img.resize((1500, int(img.height * ratio)), Image.Resampling.LANCZOS)

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
