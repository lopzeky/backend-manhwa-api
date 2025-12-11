from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from deep_translator import GoogleTranslator
import pytesseract
from pytesseract import Output # <--- IMPORTANTE: Necesario para obtener coordenadas
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

# --- [TUS FUNCIONES DE DESCARGA / ZENROWS AQUÍ] ---
# (He mantenido tu función descargar_con_zenrows intacta, 
#  asúmela presente aquí tal cual la escribiste)

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
        print(f"Error conectando con ZenRows: {e}")
        raise e

# --- [TU ENDPOINT DE SCAN AQUÍ] ---
# (Mantenemos tu endpoint /scan intacto)
@app.post("/scan")
def escanear_capitulo(payload: dict = Body(...)):
    url = payload.get("url")
    if not url: raise HTTPException(status_code=400, detail="Falta la URL")
    print(f"🚀 Escaneando: {url}")
    try:
        response = descargar_con_zenrows(url)
        if response.status_code != 200: raise HTTPException(status_code=400, detail=f"ZenRows falló: {response.status_code}")

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

        if len(imagenes) < 3:
            print("⚠️ Método HTML falló. Usando Fuerza Bruta (Regex)...")
            patron = r'(https?://[^"\s\'>]+\.(?:jpg|jpeg|png|webp))'
            enlaces_raw = re.findall(patron, response.text)
            for link in enlaces_raw:
                if any(x in link.lower() for x in ['logo', 'avatar', 'icon', 'banner', 'ads', 'facebook', 'svg']): continue
                imagenes.append(link)

        imagenes_unicas = list(dict.fromkeys(imagenes))
        if len(imagenes_unicas) < 3:
             raise HTTPException(status_code=422, detail="No se encontraron imágenes legibles.")

        return {"status": "ok", "total": len(imagenes_unicas), "imagenes": imagenes_unicas}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")


# --- LÓGICA DE PROCESAMIENTO (AHORA INTEGRADA) ---

def procesar_texto_manhwa(cajas_ocr):
    # 1. Ordenar cajas de arriba a abajo
    cajas_ordenadas = sorted(cajas_ocr, key=lambda c: c['y'])
    
    bloques_finales = []
    bloque_actual = []
    
    # 2. Agrupar por proximidad (Detectar globos)
    for i, caja in enumerate(cajas_ordenadas):
        # Limpieza inicial de caracteres basura comunes en bordes
        texto_limpio = caja['texto'].replace('|', '').replace('_', '').strip()
        
        if not texto_limpio: continue # Saltar vacíos

        if not bloque_actual:
            bloque_actual.append({'texto': texto_limpio, 'y': caja['y']}) # Guardamos Y para referencia futura si se necesita
            continue
            
        # Calcular distancia con el elemento anterior del bloque
        # Nota: Usamos el último elemento añadido al bloque actual para comparar
        ultimo_elemento = bloque_actual[-1]
        distancia_y = caja['y'] - ultimo_elemento['y']
        
        # UMBRAL: Si hay más de 60px de diferencia, es otro globo
        UMBRAL_DISTANCIA = 60 
        
        if distancia_y > UMBRAL_DISTANCIA:
            # Cerrar bloque anterior
            bloques_finales.append(unir_texto_inteligente([b['texto'] for b in bloque_actual]))
            bloque_actual = [{'texto': texto_limpio, 'y': caja['y']}] # Iniciar nuevo
        else:
            bloque_actual.append({'texto': texto_limpio, 'y': caja['y']})
    
    # Agregar el último bloque remanente
    if bloque_actual:
        bloques_finales.append(unir_texto_inteligente([b['texto'] for b in bloque_actual]))
        
    return bloques_finales

def unir_texto_inteligente(lista_textos):
    texto_completo = ""
    for linea in lista_textos:
        if texto_completo.endswith("-"):
            # Contexto Inteligente: "go-" + "ing" -> "going"
            texto_completo = texto_completo[:-1] + linea
        else:
            # Caso normal con espacio
            texto_completo += " " + linea
    return texto_completo.strip()

# --- ENDPOINT 2: TRADUCIR (REESCRITO) ---
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
            print("Fallo directo, usando ZenRows para imagen...")
            response = descargar_con_zenrows(img_url)

        img = Image.open(io.BytesIO(response.content))
        
        # Pre-procesamiento de imagen
        img = img.convert('L') # Escala de grises
        # Opcional: Aumentar contraste aquí ayudaría al OCR
        
        if img.width > 1500:
            ratio = 1500 / img.width
            img = img.resize((1500, int(img.height * ratio)), Image.Resampling.LANCZOS)

        # ---------------------------------------------------------
        #  CAMBIO CRÍTICO: Usamos image_to_data para tener coordenadas
        # ---------------------------------------------------------
        try:
            # Output.DICT nos da listas: d['text'], d['top'], d['conf'], etc.
            d = pytesseract.image_to_data(img, lang=cfg["ocr"], output_type=Output.DICT)
        except:
            # Fallback a inglés si falla el idioma específico
            d = pytesseract.image_to_data(img, lang="eng", output_type=Output.DICT)
        
        del img
        gc.collect()

        # Transformar la salida cruda de Tesseract a una lista de objetos limpios
        cajas_ocr = []
        n_boxes = len(d['text'])
        
        for i in range(n_boxes):
            # Filtramos confianza baja (<40) y textos vacíos
            if int(d['conf'][i]) > 40 and d['text'][i].strip():
                cajas_ocr.append({
                    "texto": d['text'][i],
                    "y": d['top'][i],     # Coordenada Y (vertical)
                    "x": d['left'][i],    # Coordenada X (horizontal) - Opcional
                    "h": d['height'][i]   # Altura - Opcional
                })

        if not cajas_ocr:
            return {"bloques": []}

        # 1. Aplicar agrupación espacial + limpieza + contexto inteligente
        bloques_texto = procesar_texto_manhwa(cajas_ocr)

        # 2. Traducir cada bloque por separado
        translator = GoogleTranslator(source=cfg["src"], target=cfg["dest"])
        resultados = []

        for bloque in bloques_texto:
            if not bloque.strip(): continue
            try:
                traducido = translator.translate(bloque)
                resultados.append({
                    "original": bloque,
                    "traducido": traducido
                })
            except Exception as e:
                resultados.append({
                    "original": bloque,
                    "traducido": "[Error traducción]"
                })

        # Devolvemos la lista de bloques para que el Frontend los pinte separados
        return {"bloques": resultados}

    except Exception as e:
        print(f"Error fatal: {e}")
        return {"error": str(e), "bloques": []}
