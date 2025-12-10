from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from deep_translator import GoogleTranslator
import pytesseract
from PIL import Image
import requests
import io
import gc
from bs4 import BeautifulSoup # Importamos la nueva herramienta ligera

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

# --- NUEVA FUNCIÓN DE ESCANEO LIGERO (SIN CHROME) ---
@app.post("/scan")
def escanear_capitulo(payload: dict = Body(...)):
    url = payload.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="Falta la URL")
    
    print(f"🌍 Analizando (Modo Ligero): {url}")
    
    # Cabeceras para parecer un humano real (Vital para que no nos bloqueen)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Referer": "https://www.google.com/"
    }

    try:
        # 1. Descargamos solo el código HTML (Muy rápido)
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code == 403:
            raise HTTPException(status_code=403, detail="Sitio protegido por Cloudflare. Intenta con Manganato o ManhwaClan.")
        
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail=f"El sitio respondió con error: {response.status_code}")

        # 2. Analizamos el HTML buscando imágenes
        soup = BeautifulSoup(response.text, 'lxml')
        imagenes = []

        # Buscamos todas las etiquetas <img>
        for img in soup.find_all('img'):
            # Truco: Muchos sitios ocultan la imagen real en 'data-src' o 'data-original'
            src = img.get('data-src') or img.get('data-original') or img.get('src')
            
            if src and src.startswith('http'):
                # Filtros de basura
                src_lower = src.lower()
                if any(x in src_lower for x in ['logo', 'avatar', 'icon', 'banner', 'ads', 'facebook', 'twitter']):
                    continue
                
                imagenes.append(src)

        # Eliminar duplicados manteniendo orden
        imagenes_unicas = list(dict.fromkeys(imagenes))

        # Validación final
        if len(imagenes_unicas) < 3:
             # Si encontramos muy pocas, quizás es porque el sitio usa JS complejo
             raise HTTPException(status_code=422, detail="No encontré imágenes. Este sitio requiere un navegador completo (Plan Pro).")

        return {"status": "ok", "total": len(imagenes_unicas), "imagenes": imagenes_unicas}

    except Exception as e:
        print(f"Error: {e}")
        # Si ya es un HTTPException, lo dejamos pasar
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")

@app.post("/traducir-imagen")
def traducir_imagen(payload: dict = Body(...)):
    img_url = payload.get("img_url")
    modo = payload.get("modo", "en_es")
    cfg = CONFIG_IDIOMAS.get(modo, CONFIG_IDIOMAS["en_es"])

    try:
        # Headers para descargar la imagen sin ser bloqueado
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(img_url, headers=headers, stream=True, timeout=10)
        response.raise_for_status()
        
        img = Image.open(io.BytesIO(response.content))
        
        # Optimización RAM
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
