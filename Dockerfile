FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY ariadne ./ariadne
RUN pip install --no-cache-dir .
# Rules-only by default: no API key or secrets belong in the image.
ENTRYPOINT ["python", "-m", "ariadne"]
