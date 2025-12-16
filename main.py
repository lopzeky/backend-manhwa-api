from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from deep_translator import GoogleTranslator
import pytesseract
from pytesseract import Output  # <--- IMPORTANTE: Necesario para leer coordenadas
from PIL import Image
import io
import gc
import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_fixed
import re 

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

# --- FUNCIÓN INTELIGENTE: Agrupa texto por cercanía (Detecta Burbujas) ---
def procesar_ocr_inteligente(img, lang_ocr):
    # output_type=Output.DICT nos da coordenadas (top, left, height, etc.)
    data = pytesseract.image_to_data(img, lang=lang_ocr, output_type=Output.DICT)
    
    n_boxes = len(data['text'])
    bloques = []         # Lista final de burbujas
    bloque_actual = []   # Palabras de la burbuja actual
    ultimo_bottom = 0    # Posición inferior de la última palabra procesada
    
    # UMBRAL: Si hay más de 60px de espacio vertical entre palabras, es otra burbuja
    UMBRAL_SEPARACION = 60 

    for i in range(n_boxes):
        # Filtramos basura (confianza < 40 o espacios vacíos)
        if int(data['conf'][i]) > 40:
            texto = data['text'][i].strip()
            if not texto: continue

            top = data['top'][i]
            height = data['height'][i]
            bottom = top + height
            
            # LÓGICA DE AGRUPACIÓN:
            # Si ya tenemos palabras y la nueva palabra está muy lejos abajo...
            if bloque_actual and (top - ultimo_bottom) > UMBRAL_SEPARACION:
                # 1. Cerramos la burbuja anterior
                bloques.append(" ".join(bloque_actual))
                # 2. Iniciamos una nueva
                bloque_actual = []
            
            bloque_actual.append(texto)
            ultimo_bottom = bottom # Actualizamos la referencia

    # Agregar el último bloque que quedó pendiente en el buffer
    if bloque_actual:
        bloques.append(" ".join(bloque_actual))
    
    return bloques

# --- ZENROWS (Igual que antes) ---
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def descargar_con_zenrows(url, timeout=40):
    API_KEY = "16ec4b42117e5328f574d7cf53b32bbbb17daa75" 
    params = {
        "apikey": API_KEY,
        "url": url,
        "js_render": "true",
        "antibot": "true",
        "premium_proxy": "true",
        "wait": "3000"
    }
    try:
        response = requests.get("https://api.zenrows.com/v1/", params=params, timeout=timeout)
        return response
    except Exception as e:
        print(f"Error ZenRows: {e}")
        raise e

# --- ENDPOINT 1: ESCANEAR (Igual que antes) ---
@app.post("/scan")
def escanear_capitulo(payload: dict = Body(...)):
    url = payload.get("url")
    if not url: raise HTTPException(status_code=400, detail="Falta la URL")
    
    print(f"🚀 Escaneando: {url}")
    try:
        response = descargar_con_zenrows(url)
        if response.status_code != 200: raise HTTPException(status_code=400, detail="ZenRows falló")

        soup = BeautifulSoup(response.text, 'lxml')
        imagenes = []
        for img in soup.find_all('img'):
            src = img.get('data-src') or img.get('data-original') or img.get('src')
            if src and src.startswith('http') and not any(x in src.lower() for x in ['logo', 'banner', 'ads']):
                imagenes.append(src)

        if len(imagenes) < 3:
            patron = r'(https?://[^"\s\'>]+\.(?:jpg|jpeg|png|webp))'
            enlaces_raw = re.findall(patron, response.text)
            for link in enlaces_raw:
                if not any(x in link.lower() for x in ['logo', 'banner', 'ads']):
                    imagenes.append(link)

        imagenes_unicas = list(dict.fromkeys(imagenes))
        return {"status": "ok", "total": len(imagenes_unicas), "imagenes": imagenes_unicas}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- ENDPOINT 2: TRADUCIR (¡RENOVADO!) ---
@app.post("/traducir-imagen")
def traducir_imagen(payload: dict = Body(...)):
    img_url = payload.get("img_url")
    modo = payload.get("modo", "en_es")
    cfg = CONFIG_IDIOMAS.get(modo, CONFIG_IDIOMAS["en_es"])

    try:
        # 1. Descargar imagen
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            response = requests.get(img_url, headers=headers, stream=True, timeout=10)
            response.raise_for_status()
        except:
            response = descargar_con_zenrows(img_url)

        img = Image.open(io.BytesIO(response.content))
        img = img.convert('L') # Escala de grises mejora OCR
        
        # Optimización de tamaño
        if img.width > 1500:
            ratio = 1500 / img.width
            img = img.resize((1500, int(img.height * ratio)), Image.Resampling.LANCZOS)

        # 2. OCR Inteligente (Obtener lista de burbujas, no texto plano)
        try:
            lista_burbujas = procesar_ocr_inteligente(img, cfg["ocr"])
        except:
            lista_burbujas = procesar_ocr_inteligente(img, "eng")
        
        del img
        gc.collect()

        if not lista_burbujas:
            return {"bloques": []} # Retorno vacío si no hay texto

        # 3. Traducción Optimizada (Batch)
        # Unimos todo con un separador único " ||| " para hacer solo 1 petición a Google
        texto_unido = " ||| ".join(lista_burbujas)
        
        translator = GoogleTranslator(source=cfg["src"], target=cfg["dest"])
        traduccion_raw = translator.translate(texto_unido)
        
        # Separamos de nuevo
        lista_traducida = traduccion_raw.split(" ||| ")

        # 4. Construir respuesta JSON estructurada para el Frontend
        resultado_final = []
        
        # Aseguramos que las listas tengan el mismo tamaño (seguridad)
        limit = min(len(lista_burbujas), len(lista_traducida))
        
        for i in range(limit):
            original = lista_burbujas[i]
            traducido = lista_traducida[i]
            
            # Limpieza extra de caracteres raros
            traducido = traducido.replace("|||", "").strip()
            
            if len(traducido) > 1: # Ignorar ruido de 1 letra
                resultado_final.append({
                    "original": original,
                    "traducido": traducido
                })

        return {"bloques": resultado_final}

    except Exception as e:
        print(f"Error: {e}")
        return {"bloques": [], "error": str(e)}
