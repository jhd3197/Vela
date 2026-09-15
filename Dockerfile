FROM node:22-bookworm-slim AS dashboard
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VELA_DATA_DIR=/data
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY vela/ ./vela/
COPY --from=dashboard /build/web/dist ./web/dist
EXPOSE 7700
CMD ["python", "-m", "vela.container"]
