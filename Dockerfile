# Stage 1: Build Frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Stage 2: Final Runtime
FROM python:3.12-slim

# Install system dependencies
# libgomp1 is needed for faiss-cpu/onnxruntime
RUN apt-get update && apt-get install -y \
    supervisor \
    redis-server \
    libgomp1 \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Kolkata

WORKDIR /app

# Copy backend requirements and install
COPY backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -r ./backend/requirements.txt

# Copy backend source code
COPY backend/ ./backend/

# Copy frontend build from Stage 1
# We put it in /app/frontend/dist so main.py can find it at ../frontend/dist
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Copy supervisord config
# We place it where supervisord expects or specify it in the CMD
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Expose port 8000
EXPOSE 8000

# Run supervisord
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
