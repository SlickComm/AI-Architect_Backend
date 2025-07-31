# 1) Wähle ein passendes Base-Image
FROM python:3.9-slim

# 2) Um sicherzustellen, dass Python direkt loggt und keine .pyc-Dateien erzeugt
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# 3) Arbeitsverzeichnis erstellen und wechseln
WORKDIR /app

# 4) Requirements kopieren und installieren
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# 5) Quellcode kopieren
COPY main.py .
COPY app/ ./app/

# 6) Exponiere den Port (Standard: 80 oder 8000)
EXPOSE 80

# 7) Befehl zum Starten des Servers
#    uvicorn: app-Datei=app, FastAPI-Instanz=app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]