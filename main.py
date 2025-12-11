from fastapi import FastAPI, HTTPException, Body, Response
from fastapi.middleware.cors import CORSMiddleware
from deep_translator import GoogleTranslator
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

# --- 1. FUNCIÓN ZENROWS (Para obtener links sin bloqueo) ---
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def descargar_con_zenrows(url, timeout=30):
    
    # 🔴🔴🔴 TU API KEY DE ZENROWS AQUÍ 🔴🔴🔴
    API_KEY = "P16ec4b42117e5328f574d7cf53b32bbbb17daa75" 
    
    params = {
        "apikey": API_KEY,
        "url": url,
        "js_render": "true",
        "premium_proxy": "true",
        "wait": "2000"
    }
    # Si no tienes ZenRows, usa requests normal con headers, 
    # pero ZenRows es lo mejor para evitar bloqueos.
    return requests.get("https://api.zenrows.com/v1/", params=params, timeout=timeout)

# --- 2. ESCANEAR (Solo devuelve los links de las imágenes) ---
@app.post("/scan")
def escanear_capitulo(payload: dict = Body(...)):
    url = payload.get("url")
    if not url: raise HTTPException(status_code=400, detail="Falta URL")
    
    try:
        response = descargar_con_zenrows(url)
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail=f"ZenRows Error: {response.status_code}")

        soup = BeautifulSoup(response.text, 'lxml')
        imagenes = []

        for img in soup.find_all('img'):
            # Buscamos en atributos lazy load
            src = img.get('data-src') or img.get('data-original') or img.get('src')
            if src and src.strip().startswith(('http', '//')):
                src = src.strip()
                if src.startswith('//'): src = 'https:' + src
                # Filtros básicos
                if any(x in src.lower() for x in ['logo', 'avatar', 'icon', 'banner']): continue
                imagenes.append(src)

        imagenes_unicas = list(dict.fromkeys(imagenes))
        
        if len(imagenes_unicas) < 3:
             raise HTTPException(status_code=422, detail="No encontré imágenes válidas.")

        return {"status": "ok", "total": len(imagenes_unicas), "imagenes": imagenes_unicas}

    except Exception as e:
        print(f"Error: {e}")
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))

# --- 3. PROXY DE IMAGEN (Vital para Tesseract.js) ---
# El navegador no puede leer imágenes de otros dominios por seguridad (CORS).
# Este endpoint descarga la imagen y se la entrega al navegador "limpia".
@app.get("/proxy-imagen")
def proxy_imagen(url: str):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        # Usamos requests directo para ahorrar créditos de ZenRows en las imágenes
        resp = requests.get(url, headers=headers, stream=True, timeout=15)
        return Response(content=resp.content, media_type="image/jpeg")
    except:
        return Response(status_code=404)

# --- 4. TRADUCTOR DE TEXTO PURO ---
@app.post("/traducir-texto")
def traducir_texto(payload: dict = Body(...)):
    texto = payload.get("texto", "")
    dest = payload.get("dest", "es") # Destino (Español)

    if not texto or not texto.strip():
        return {"traduccion": ""}

    try:
        # Aquí solo traducimos texto, es muy rápido y ligero
        translator = GoogleTranslator(source='auto', target=dest)
        traduccion = translator.translate(texto)
        return {"traduccion": traduccion}
    except Exception as e:
        return {"traduccion": "Error traduciendo"}
