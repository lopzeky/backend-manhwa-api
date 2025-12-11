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

# --- 1. FUNCIÓN MAESTRA DE DESCARGA (ZENROWS) ---
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

# --- 2. NUEVO ENDPOINT: BUSCADOR (TIPO PIRATE BAY) ---
@app.get("/buscar")
def buscar_manhwa(q: str):
    if not q: return {"resultados": []}

    print(f"🔍 Buscando: {q}")
    # Formato de búsqueda de Manganato
    query_formated = q.replace(" ", "_")
    search_url = f"https://chapmanganato.to/https://manganato.com/search/story/{query_formated}"

    try:
        # Usamos ZenRows también para buscar (así no nos bloquean el buscador)
        response = descargar_con_zenrows(search_url)
        
        soup = BeautifulSoup(response.text, 'lxml')
        resultados = []

        # Scrapeamos los resultados de Manganato
        for item in soup.find_all('div', class_='search-story-item'):
            titulo_tag = item.find('a', class_='item-img')
            capitulo_tag = item.find('a', class_='item-chapter')
            
            if titulo_tag:
                titulo = titulo_tag.get('title')
                link_manhwa = titulo_tag.get('href')
                img_tag = titulo_tag.find('img')
                img_cover = img_tag.get('src') if img_tag else ""
                
                # Priorizamos el link del último capítulo si existe
                link_destino = capitulo_tag.get('href') if capitulo_tag else link_manhwa

                resultados.append({
                    "titulo": titulo,
                    "cover": img_cover,
                    "link": link_destino
                })

        return {"status": "ok", "resultados": resultados}

    except Exception as e:
        print(f"Error búsqueda: {e}")
        return {"status": "error", "resultados": []}

# --- 3. ENDPOINT: ESCANEAR CAPÍTULO ---
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
        print(f"Error: {e}")
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")

# --- 4. ENDPOINT: TRADUCIR ---
@app.post("/traducir-imagen")
def traducir_imagen(payload: dict = Body(...)):
    img_url = payload.get("img_url")
    modo = payload.get("modo", "en_es")
    cfg = CONFIG_IDIOMAS.get(modo, CONFIG_IDIOMAS["en_es"])

    try:
        # Intento directo primero (ahorra créditos)
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            response = requests.get(img_url, headers=headers, stream=True, timeout=10)
            response.raise_for_status()
        except:
            # Respaldo ZenRows
            response = descargar_con_zenrows(img_url)

        img = Image.open(io.BytesIO(response.content))
        img = img.convert('L') # Optimizar RAM
        
        # Redimensionar si es gigante
        if img.width > 1500:
            ratio = 1500 / img.width
            img = img.resize((1500, int(img.height * ratio)), Image.Resampling.LANCZOS)

        try:
            text = pytesseract.image_to_string(img, lang=cfg["ocr"])
        except:
            text = pytesseract.image_to_string(img, lang="eng")
        
        del img; gc.collect()

        if not text.strip(): return {"texto_traducido": "(Sin texto detectado)"}

        translator = GoogleTranslator(source=cfg["src"], target=cfg["dest"])
        traducido = translator.translate(text)

        return {"texto_original": text, "texto_traducido": traducido}

    except Exception as e:
        return {"texto_traducido": f"Error: {str(e)}"}
