FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml README.md LICENSE ./
COPY gtm ./gtm
COPY data ./data

RUN pip install --no-cache-dir -e .

VOLUME ["/app/data"]
EXPOSE 8080

CMD ["gtm", "daemon", "--poll", "120"]
