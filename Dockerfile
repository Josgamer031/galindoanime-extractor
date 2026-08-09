FROM mcr.microsoft.com/playwright/python:v1.40.0

WORKDIR /app

# La imagen base YA trae playwright + chromium instalados y alineados.
# NO reinstalar playwright (desalinearia el browser). Solo copiar el codigo.
COPY main.py ./

ENV PORT=10000
EXPOSE 10000

CMD ["python3", "main.py"]
