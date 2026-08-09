FROM mcr.microsoft.com/playwright/python:v1.40.0

WORKDIR /app

# La imagen base YA trae playwright + chromium alineados para python3.
# Nos aseguramos de que el paquete python este disponible en el python3
# que ejecuta el CMD (sin reinstalar chromium, que ya viene en la imagen).
RUN python3 -m pip install --no-cache-dir playwright==1.40.0

COPY main.py ./

# NO fijar ENV PORT: Render lo inyecta dinamicamente en el contenedor.
# Si lo fijamos, el server escucha en 10000 pero Render espera el PORT que
# asigna, y responde 502. main.py usa os.environ.get("PORT","10000") como fallback.
EXPOSE 10000

CMD ["python3", "main.py"]
