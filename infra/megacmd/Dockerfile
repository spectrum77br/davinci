# Sidecar MEGAcmd: expõe uma API HTTP interna (rede docker, sem porta
# publicada) que a API DaVinci usa pra falar com a conta MEGA do usuário
# (listar pastas de fotos, gerar links públicos, subir fotos).
# A sessão do MEGA fica persistida em /root/.megaCmd (volume).
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends wget gpg ca-certificates \
    && wget -qO- https://mega.nz/linux/repo/Debian_12/Release.key \
        | gpg --dearmor -o /usr/share/keyrings/meganz.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/meganz.gpg] https://mega.nz/linux/repo/Debian_12/ ./" \
        > /etc/apt/sources.list.d/megacmd.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends megacmd \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir fastapi uvicorn python-multipart

WORKDIR /srv
COPY app.py .

EXPOSE 9000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9000"]
