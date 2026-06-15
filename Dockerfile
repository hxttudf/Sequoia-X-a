FROM python:3.11-slim

RUN pip install uv --quiet

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

RUN chmod +x entrypoint.sh

RUN apt-get update -qq && apt-get install -y -qq cron && rm -rf /var/lib/apt/lists/*

RUN echo "30 7 * * 1-5 root cd /app && .venv/bin/python main.py >> /app/logs/daily.log 2>&1" > /etc/cron.d/sequoia \
    && chmod 0644 /etc/cron.d/sequoia

RUN mkdir -p /app/logs /app/data

VOLUME ["/app/data", "/app/logs"]

ENTRYPOINT ["/app/entrypoint.sh"]
