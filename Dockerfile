# docparse Studio - deployable to Google Cloud Run (or any container host).
#   docker build -t docparse-studio .
#   docker run -p 8080:8080 docparse-studio
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr poppler-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY pyproject.toml ./
COPY docparse ./docparse
COPY app ./app
RUN pip install --no-cache-dir ".[pdf,app]"
ENV DOCPARSE_APP_DATA=/tmp/app_data PORT=8080
EXPOSE 8080
CMD exec uvicorn app.server:app --host 0.0.0.0 --port ${PORT} --workers 1
