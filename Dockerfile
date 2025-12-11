# Versión de Python ligera
FROM python:3.9-slim

# Solo necesitamos lo básico (sin tesseract-ocr de linux)
WORKDIR /app

# Copiamos e instalamos dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiamos el código
COPY . .

# Ejecutamos
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]
