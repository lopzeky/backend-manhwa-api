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

# --- FUNCIÓN MAESTRA (ZENROWS) ---
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def descargar_con_zenrows(url, timeout=30):
    
    # 🔴🔴🔴 TU API KEY DE ZENROWS AQUÍ 🔴🔴🔴
    API_KEY = "16ec4b42117e5328f574d7cf53b32bbbb17daa75" 
    
    params = {
        "apikey": API_KEY,
        "url": url,
        "js_render": "true",
        "premium_proxy": "true",
        "wait": "2000"
    }

    try:
        response = requests.get("https://api.zenrows.com/v1/", params=params, timeout=timeout)
        return response
    except Exception as e:
        print(f"Error ZenRows: {e}")
        raise e

# --- ENDPOINT 1: ESCANEAR CAPÍTULO ---
@app.post("/scan")
def escanear_capitulo(payload: dict = Body(...)):
    url = payload.get("url")
    if not url: raise HTTPException(status_code=400, detail="Falta URL")
    
    print(f"🚀 Escaneando: {url}")
    try:
        response = descargar_con_zenrows(url)
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail=f"ZenRows Error: {response.status_code}")

        soup = BeautifulSoup(response.text, 'lxml')
        imagenes = []

        for img in soup.find_all('img'):
            src = img.get('data-src') or img.get('data-original') or img.get('data-lazy-src') or img.get('src')
            if src and src.strip().startswith(('http', '//')):
                src = src.strip()
                if src.startswith('//'): src = 'https:' + src
                if any(x in src.lower() for x in ['logo', 'avatar', 'icon', 'banner', 'ads']): continue
                imagenes.append(src)

        imagenes_unicas = list(dict.fromkeys(imagenes))
        if len(imagenes_unicas) < 3:
             raise HTTPException(status_code=422, detail="No encontré imágenes válidas.")

        return {"status": "ok", "total": len(imagenes_unicas), "imagenes": imagenes_unicas}

    except Exception as e:
        print(f"Error: {e
