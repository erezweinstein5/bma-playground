FROM node:24.14.0-bookworm-slim AS node
FROM public.ecr.aws/lambda/microvms:al2023-minimal
RUN dnf install -y tar gzip findutils shadow-utils ca-certificates git libstdc++ \
 && dnf clean all && useradd -m -u 1000 app
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && node --version && npm --version
ARG CODEX_RELEASE=0.154.0
RUN curl -fsSL https://chatgpt.com/codex/install.sh -o /tmp/install-codex.sh \
 && CODEX_NON_INTERACTIVE=1 CODEX_INSTALL_DIR=/opt/bma/bin CODEX_HOME=/opt/bma/codex \
    CODEX_RELEASE=${CODEX_RELEASE} sh /tmp/install-codex.sh \
 && chmod -R a+rX /opt/bma && rm /tmp/install-codex.sh \
 && /opt/bma/bin/codex exec-server --help >/dev/null
RUN curl -fsSL -o /tmp/cwagent.rpm \
    https://amazoncloudwatch-agent.s3.amazonaws.com/amazon_linux/arm64/latest/amazon-cloudwatch-agent.rpm \
 && rpm -i /tmp/cwagent.rpm && rm /tmp/cwagent.rpm
COPY --from=ghcr.io/astral-sh/uv:0.11.13 /uv /bin/
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_PYTHON_BIN_DIR=/usr/local/bin
RUN uv python install 3.12 --default
WORKDIR /opt/bma
ENV UV_COMPILE_BYTECODE=1 UV_NO_PROGRESS=1
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev
COPY lifecycle/ lifecycle/
COPY otel/ otel/
COPY plugins/ plugins/
COPY tools/ tools/
RUN mkdir -p /mnt/home && chown app:app /mnt/home \
 && chmod -R a+rX /opt/bma /opt/python
ENV BMA_HOME_DIR=/mnt/home PATH="/opt/bma/.venv/bin:/usr/local/bin:/usr/bin:/bin"
USER app
EXPOSE 8080
CMD ["uv", "run", "--no-sync", "opentelemetry-instrument", "python", "-u", "lifecycle/server.py"]
