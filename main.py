from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from deep_translator import GoogleTranslator
import pytesseract
from pytesseract import Output
from PIL import Image
import io
import gc
import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_fixed
import re

app = FastAPI()

# --- CONFIGURACIÓN CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- CONFIGURACIÓN DE IDIOMAS ---
CONFIG_IDIOMAS = {
    "en_es": {"ocr": "eng", "src": "en", "dest": "es"},
    "es_en": {"ocr": "spa", "src": "es", "dest": "en"},
    "ko_es": {"ocr": "kor", "src": "ko", "dest": "es"},
    "ko_en": {"ocr": "kor", "src": "ko", "dest": "en"}
}

# --- 1. OCR INTELIGENTE CON COORDENADAS ---
def procesar_ocr_inteligente(img, lang_ocr):
    # output_type=Output.DICT nos da coordenadas y texto
    data = pytesseract.image_to_data(img, lang=lang_ocr, output_type=Output.DICT)
    
    n_boxes = len(data['text'])
    bloques = []         
    
    # Variables para agrupar palabras en una sola burbuja
    bloque_texto = []   
    min_left, min_top = float('inf'), float('inf')
    max_right, max_bottom = 0, 0
    
    ultimo_bottom = 0
    UMBRAL_SEPARACION = 60 # Píxeles de separación para cortar burbuja

    for i in range(n_boxes):
        if int(data['conf'][i]) > 40: # Confianza > 40%
            texto = data['text'][i].strip()
            if not texto: continue

            # Coordenadas de la palabra actual
            top = data['top'][i]
            left = data['left'][i]
            width = data['width'][i]
            height = data['height'][i]
            bottom = top + height
            right = left + width
            
            # SI HAY UN SALTO GRANDE HACIA ABAJO -> NUEVA BURBUJA
            if bloque_texto and (top - ultimo_bottom) > UMBRAL_SEPARACION:
                # Guardamos el bloque anterior
                bloques.append({
                    "texto": " ".join(bloque_texto),
                    "box": {
                        "x": min_left, 
                        "y": min_top, 
                        "w": max_right - min_left, 
                        "h": max_bottom - min_top
                    }
                })
                # Reseteamos variables
                bloque_texto = []
                min_left, min_top = float('inf'), float('inf')
                max_right, max_bottom = 0, 0
            
            # Agregamos palabra al buffer
            bloque_texto.append(texto)
            ultimo_bottom = bottom
            
            # Expandimos el área de la caja contenedora
            if left < min_left: min_left = left
            if top < min_top: min_top = top
            if right > max_right: max_right = right
            if bottom > max_bottom: max_bottom = bottom

    # Guardar el último bloque que quedó pendiente
    if bloque_texto:
        bloques.append({
            "texto": " ".join(bloque_texto),
            "box": {
                "x": min_left, "y": min_top, 
                "w": max_right - min_left, "h": max_bottom - min_top
            }
        })
    
    return bloques

# --- 2. ZENROWS (Manteniendo tu configuración) ---
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def descargar_con_zenrows(url, timeout=40):
    API_KEY = "16ec4b42117e5328f574d7cf53b32bbbb17daa75" 
    params = {
        "apikey": API_KEY, "url": url, "js_render": "true",
        "antibot": "true", "premium_proxy": "true", "wait": "3000" 
    }
    try:
        print(f"📡 ZenRows: {url}...")
        response = requests.get("https://api.zenrows.com/v1/", params=params, timeout=timeout)
        return response
    except Exception as e:
        print(f"❌ Error ZenRows: {e}")
        raise e

# --- ENDPOINT SCAN (Igual que antes, con filtros) ---
@app.post("/scan")
def escanear_capitulo(payload: dict = Body(...)):
    url = payload.get("url")
    if not url: raise HTTPException(status_code=400, detail="Falta la URL")
    
    print(f"🚀 Escaneando: {url}")
    try:
        response = descargar_con_zenrows(url)
        if response.status_code != 200: 
            raise HTTPException(status_code=400, detail=f"ZenRows falló: {response.text}")

        soup = BeautifulSoup(response.text, 'lxml')
        imagenes = []

        contenedores = ['readerarea', 'chapter-content', 'reading-content', 'page-content', 'vung_doc']
        area = None
        
        if not area: area = soup.find('div', id=re.compile(r'reader|content|chapter', re.I))
        if not area:
            for c in contenedores:
                area = soup.find('div', class_=c)
                if area: break
        
        target = area if area else soup
        basura = ['logo', 'banner', 'ads', 'icon', 'avatar', 'gravatar', 'comment', 'profile', 'recaptcha']

        for img in target.find_all('img'):
            src = img.get('data-src') or img.get('data-original') or img.get('data-lazy-src') or img.get('src')
            if src and src.startswith('http'):
                if any(x in src.lower() for x in basura): continue
                try:
                    if int(img.get('width', 999)) < 200 and int(img.get('height', 999)) < 200: continue
                except: pass
                imagenes.append(src.split('?')[0])

        if not imagenes: # Fallback Regex
             patron = r'(https?://[^"\s\'>]+\.(?:jpg|jpeg|png|webp))'
             raw = re.findall(patron, response.text)
             imagenes = [l for l in raw if not any(x in l.lower() for x in basura)]

        imagenes = list(dict.fromkeys(imagenes))
        return {"status": "ok", "total": len(imagenes), "imagenes": imagenes}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- ENDPOINT TRADUCIR (¡Ahora devuelve coordenadas!) ---
@app.post("/traducir-imagen")
def traducir_imagen(payload: dict = Body(...)):
    img_url = payload.get("img_url")
    modo = payload.get("modo", "en_es")
    cfg = CONFIG_IDIOMAS.get(modo, CONFIG_IDIOMAS["en_es"])

    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            response = requests.get(img_url, headers=headers, stream=True, timeout=10)
            response.raise_for_status()
        except:
            response = descargar_con_zenrows(img_url)

        img = Image.open(io.BytesIO(response.content))
        img = img.convert('L')
        
        # 1. REDIMENSIONAR (Estandarizamos a 1500px de ancho para mejor OCR)
        target_width = 1500
        original_width, original_height = img.size
        
        if original_width > target_width:
            ratio = target_width / original_width
            new_height = int(original_height * ratio)
            img = img.resize((target_width, new_height), Image.Resampling.LANCZOS)
        
        # 2. GUARDAMOS LAS DIMENSIONES REALES USADAS PARA EL OCR
        ancho_final, alto_final = img.size

        # 3. OCR
        try:
            lista_bloques = procesar_ocr_inteligente(img, cfg["ocr"])
        except:
            lista_bloques = procesar_ocr_inteligente(img, "eng")
        
        del img
        gc.collect()

        if not lista_bloques: return {"bloques": []}

        # 4. TRADUCCIÓN
        textos = [b['texto'] for b in lista_bloques]
        texto_unido = " ||| ".join(textos)
        
        translator = GoogleTranslator(source=cfg["src"], target=cfg["dest"])
        try:
            traduccion_raw = translator.translate(texto_unido)
            lista_traducida = traduccion_raw.split(" ||| ")
        except:
            lista_traducida = textos

        resultado_final = []
        limit = min(len(lista_bloques), len(lista_traducida))
        
        for i in range(limit):
            trad = lista_traducida[i].replace("|||", "").strip()
            if len(trad) > 1:
                resultado_final.append({
                    "original": lista_bloques[i]['texto'],
                    "traducido": trad,
                    "coords": lista_bloques[i]['box']
                })

        # DEVOLVEMOS LOS DATOS + LAS DIMENSIONES DE REFERENCIA
        return {
            "bloques": resultado_final,
            "ref_w": ancho_final,  # <--- ESTO ES LA CLAVE
            "ref_h": alto_final    # <--- ESTO ES LA CLAVE
        }

    except Exception as e:
        print(f"Error: {e}")
        return {"bloques": [], "error": str(e)}
        return {"bloques": [], "error": str(e)}

