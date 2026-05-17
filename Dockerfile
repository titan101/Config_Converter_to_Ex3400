FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CONFIG_CONVERT_HOST=0.0.0.0 \
    CONFIG_CONVERT_PORT=5050 \
    CONFIG_CONVERT_OPEN_BROWSER=0

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5050
CMD ["python", "app.py"]
