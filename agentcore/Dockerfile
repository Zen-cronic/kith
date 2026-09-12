# AgentCore requires linux/arm64; select it with docker buildx --platform.
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

RUN apt-get update && apt-get install -y --no-install-recommends pipx \
    && rm -rf /var/lib/apt/lists/*
ENV PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin \
    POETRY_VIRTUALENVS_CREATE=false POETRY_CACHE_DIR=/tmp/poetry-cache \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN pipx install --python /usr/local/bin/python poetry==2.2.1
WORKDIR /app
COPY pyproject.toml poetry.lock README.md LICENSE ./
COPY household ./household
COPY fixtures ./fixtures
COPY agentcore/app.py ./agentcore/app.py
RUN poetry install --only main --no-interaction --no-ansi \
    && rm -rf /tmp/poetry-cache \
    && useradd --uid 10001 --create-home household

# Live household defaults; the deploy plan sets the same values on the Runtime, and the local image verifier
# overrides MODEL_PROVIDER=fake / EXECUTION_MODE=simulated so its fixture proof stays offline and deterministic.
ENV MODEL_PROVIDER=bedrock \
    BEDROCK_MODEL_ID=us.amazon.nova-2-lite-v1:0 \
    NODE_MODELS=intake=bedrock:us.amazon.nova-pro-v1:0 \
    SONIC_MODEL_ID=amazon.nova-2-sonic-v1:0 \
    EXECUTION_MODE=live \
    MAX_MODEL_CALLS=30 \
    OTEL_SDK_DISABLED=true
USER household
EXPOSE 8080
CMD ["python", "agentcore/app.py"]
