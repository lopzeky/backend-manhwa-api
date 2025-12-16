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

# --- 1. FUNCIÓN OCR INTELIGENTE (Detecta Burbujas) ---
def procesar_ocr_inteligente(img, lang_ocr):
    # output_type=Output.DICT nos da coordenadas
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

    # Agregar el último bloque pendiente
    if bloque_actual:
        bloques.append(" ".join(bloque_actual))
    
    return bloques

# --- 2. DESCARGA CON ZENROWS (CORREGIDO ERROR 400) ---
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def descargar_con_zenrows(url, timeout=40):
    # TU API KEY REAL
    API_KEY = "16ec4b42117e5328f574d7cf53b32bbbb17daa75" 
    
    params = {
        "apikey": API_KEY,
        "url": url,
        "js_render": "true",
        "antibot": "true",
        "premium_proxy": "true",
        # AJUSTE CRÍTICO: Bajamos a 3000 para evitar timeout del servidor proxy
        "wait": "3000" 
    }
    try:
        print(f"📡 Conectando a ZenRows para: {url}...")
        response = requests.get("https://api.zenrows.com/v1/", params=params, timeout=timeout)
        return response
    except Exception as e:
        print(f"❌ Error interno ZenRows: {e}")
        raise e

# --- ENDPOINT 1: ESCANEAR CAPÍTULO (CORREGIDO IMÁGENES GRISES) ---
@app.post("/scan")
def escanear_capitulo(payload: dict = Body(...)):
    url = payload.get("url")
    if not url: raise HTTPException(status_code=400, detail="Falta la URL")
    
    print(f"🚀 Escaneando: {url}")
    try:
        response = descargar_con_zenrows(url)
        
        # Diagnóstico de errores HTTP de ZenRows
        if response.status_code != 200:
            print(f"⚠️ ZenRows Error {response.status_code}: {response.text}") 
            raise HTTPException(status_code=400, detail=f"ZenRows falló: {response.text}")

        soup = BeautifulSoup(response.text, 'lxml')
        imagenes = []

        # A. ESTRATEGIA DE CONTENEDORES (Prioridad 1)
        # Buscamos primero donde vive el cómic para ignorar el footer/comentarios
        contenedores_comunes = [
            'readerarea', 'chapter-content', 'reading-content', 'page-content', 
            'main-content', 'post-body', 'entry-content', 'vung_doc'
        ]
        
        area_lectura = None
        
        # 1. Buscar por ID (más preciso)
        if not area_lectura:
            area_lectura = soup.find('div', id=re.compile(r'reader|content|chapter', re.I))
            
        # 2. Buscar por Clases comunes
        if not area_lectura:
            for clase in contenedores_comunes:
                area_lectura = soup.find('div', class_=clase)
                if area_lectura: 
                    print(f"✅ Contenedor encontrado: {clase}")
                    break
        
        # Si no encontramos contenedor, usamos todo el body pero con miedo
        target = area_lectura if area_lectura else soup

        # B. FILTRADO DE IMÁGENES (Anti-Avatares y Anti-Logos)
        palabras_basura = [
            'logo', 'banner', 'ads', 'icon', 'avatar', 'gravatar', 'comment', 
            'profile', 'recaptcha', 'gif', 'svg', 'author', 'share', 'facebook'
        ]

        for img in target.find_all('img'):
            # Buscar la URL real en atributos lazy loading
            src = img.get('data-src') or img.get('data-original') or img.get('data-lazy-src') or img.get('src')
            
            if src and src.startswith('http'):
                src_lower = src.lower()
                
                # Filtro 1: Palabras prohibidas en la URL
                if any(x in src_lower for x in palabras_basura):
                    continue
                
                # Filtro 2: Dimensiones HTML (Crucial para matar avatares de comentarios)
                try:
                    w = int(img.get('width', 999))
                    h = int(img.get('height', 999))
                    # Un panel de manhwa nunca es menor a 200px. Un avatar suele ser 96x96.
                    if w < 200 and h < 200: 
                        continue
                except:
                    pass # Si no tiene dimensiones, asumimos que sirve

                # Limpieza de URL (quitar ?resize=...)
                src_clean = src.split('?')[0]
                imagenes.append(src_clean)

        # C. FALLBACK (Plan de Respaldo - Regex)
        # Si la estrategia HTML falló y no tenemos imágenes, buscamos enlaces crudos
        if len(imagenes) == 0:
            print("⚠️ HTML falló. Usando búsqueda Regex de respaldo...")
            patron = r'(https?://[^"\s\'>]+\.(?:jpg|jpeg|png|webp))'
            enlaces_raw = re.findall(patron, response.text)
            for link in enlaces_raw:
                if not any(x in link.lower() for x in palabras_basura):
                    imagenes.append(link)

        # Eliminar duplicados manteniendo orden
        imagenes_unicas = list(dict.fromkeys(imagenes))
        
        print(f"📊 Total imágenes encontradas: {len(imagenes_unicas)}")

        if not imagenes_unicas:
             return {"status": "error", "message": "No se detectaron imágenes válidas. Sitio protegido o estructura desconocida."}

        return {"status": "ok", "total": len(imagenes_unicas), "imagenes": imagenes_unicas}

    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"🔥 Error crítico en scan: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# --- ENDPOINT 2: TRADUCIR IMAGEN (Optimizado) ---
@app.post("/traducir-imagen")
def traducir_imagen(payload: dict = Body(...)):
    img_url = payload.get("img_url")
    modo = payload.get("modo", "en_es")
    cfg = CONFIG_IDIOMAS.get(modo, CONFIG_IDIOMAS["en_es"])

    try:
        # 1. Descargar imagen
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            # Intento rápido directo
            response = requests.get(img_url, headers=headers, stream=True, timeout=10)
            response.raise_for_status()
        except:
            # Intento fuerte con ZenRows si falla el directo
            print("⚠️ Descarga directa falló, usando ZenRows para la imagen...")
            response = descargar_con_zenrows(img_url)

        img = Image.open(io.BytesIO(response.content))
        img = img.convert('L') # Escala de grises mejora OCR
        
        # Optimización de tamaño (mejora velocidad OCR)
        if img.width > 1500:
            ratio = 1500 / img.width
            img = img.resize((1500, int(img.height * ratio)), Image.Resampling.LANCZOS)

        # 2. OCR Inteligente
        try:
            lista_burbujas = procesar_ocr_inteligente(img, cfg["ocr"])
        except:
            # Fallback a inglés si falla el idioma específico
            lista_burbujas = procesar_ocr_inteligente(img, "eng")
        
        del img
        gc.collect()

        if not lista_burbujas:
            return {"bloques": []}

        # 3. Traducción Batch (Lotes)
        # Unimos todo para hacer 1 sola petición a Google
        texto_unido = " ||| ".join(lista_burbujas)
        
        translator = GoogleTranslator(source=cfg["src"], target=cfg["dest"])
        traduccion_raw = translator.translate(texto_unido)
        
        # Separamos de nuevo
        if traduccion_raw:
            lista_traducida = traduccion_raw.split(" ||| ")
        else:
            lista_traducida = lista_burbujas # Fallback si falla traducción

        # 4. Construir respuesta
        resultado_final = []
        limit = min(len(lista_burbujas), len(lista_traducida))
        
        for i in range(limit):
            original = lista_burbujas[i]
            traducido = lista_traducida[i]
            
            traducido = traducido.replace("|||", "").strip()
            
            if len(traducido) > 1:
                resultado_final.append({
                    "original": original,
                    "traducido": traducido
                })

        return {"bloques": resultado_final}

    except Exception as e:
        print(f"Error en traducción: {e}")
        return {"bloques": [], "error": str(e)}

# Para correr: uvicorn main:app --reload
