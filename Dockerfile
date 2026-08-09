FROM mcr.microsoft.com/playwright/python:v1.40.0

WORKDIR /app

# La imagen base YA trae playwright + chromium alineados para python3.
# Nos aseguramos de que el paquete python este disponible en el python3
# que ejecuta el CMD (sin reinstalar chromium, que ya viene en la imagen).
RUN python3 -m pip install --no-cache-dir playwright==1.40.0

COPY main.py ./

ENV PORT=10000
EXPOSE 10000

CMD ["python3", "main.py"]
