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
import re # <--- IMPORTANTE: Nueva herramienta para búsqueda por fuerza bruta

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

# --- FUNCIÓN DE DESCARGA MAESTRA (ZENROWS) ---
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def descargar_con_zenrows(url, timeout=40):
    
    # 🔴🔴🔴 TU API KEY DE ZENROWS 🔴🔴🔴
    API_KEY = "16ec4b42117e5328f574d7cf53b32bbbb17daa75" 
    
    params = {
        "apikey": API_KEY,
        "url": url,
        "js_render": "true",
        "antibot": "true",    # Vital para ManhwaClan
        "premium_proxy": "true",
        "wait": "3000"        # Esperamos 3 segundos para asegurar que carguen imágenes
    }

    try:
        response = requests.get("https://api.zenrows.com/v1/", params=params, timeout=timeout)
        return response
    except Exception as e:
        print(f"Error conectando con ZenRows: {e}")
        raise e

# --- ENDPOINT 1: ESCANEAR (CON FUERZA BRUTA) ---
@app.post("/scan")
def escanear_capitulo(payload: dict = Body(...)):
    url = payload.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="Falta la URL")
    
    print(f"🚀 Escaneando: {url}")
    
    try:
        response = descargar_con_zenrows(url)
        
        if response.status_code != 200:
            print(f"❌ Error ZenRows: {response.text}")
            raise HTTPException(status_code=400, detail=f"ZenRows falló: {response.status_code}")

        # --- ESTRATEGIA 1: BÚSQUEDA NORMAL (Etiquetas IMG) ---
        soup = BeautifulSoup(response.text, 'lxml')
        imagenes = []

        for img in soup.find_all('img'):
            src = img.get('data-src') or img.get('data-original') or img.get('data-lazy-src') or img.get('src')
            if src:
                if src.startswith('//'): src = 'https:' + src
                if src.startswith('http'):
                    src_lower = src.lower()
                    if any(x in src_lower for x in ['logo', 'avatar', 'icon', 'banner', 'ads']): continue
                    imagenes.append(src)

        # --- ESTRATEGIA 2: FUERZA BRUTA (Regex) ---
        # Si la estrategia 1 falló o encontró pocas imágenes, usamos Regex
        # Buscamos enlaces directos a imágenes dentro del código fuente
        if len(imagenes) < 3:
            print("⚠️ Método HTML falló. Usando Fuerza Bruta (Regex)...")
            # Patrón: busca http.....jpg/png/webp
            patron = r'(https?://[^"\s\'>]+\.(?:jpg|jpeg|png|webp))'
            enlaces_raw = re.findall(patron, response.text)
            
            for link in enlaces_raw:
                # Filtramos basura común
                if any(x in link.lower() for x in ['logo', 'avatar', 'icon', 'banner', 'ads', 'facebook', 'svg']):
                    continue
                imagenes.append(link)

        # Limpiar y Ordenar
        imagenes_unicas = list(dict.fromkeys(imagenes))

        if len(imagenes_unicas) < 3:
             # Si aún así falla, imprimimos un trozo del HTML en los logs de Render para depurar
             print("🔍 HTML RECIBIDO (Primeros 500 chars):")
             print(response.text[:500])
             raise HTTPException(status_code=422, detail="Pude entrar, pero el sitio no tiene imágenes legibles ni con fuerza bruta.")

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
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        try:
            response = requests.get(img_url, headers=headers, stream=True, timeout=10)
            response.raise_for_status()
        except:
            print("Fallo directo, usando ZenRows para imagen...")
            response = descargar_con_zenrows(img_url)

        img = Image.open(io.BytesIO(response.content))
        img = img.convert('L')
        
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
