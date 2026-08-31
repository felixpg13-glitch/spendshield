FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/felixpg13-glitch/spendshield"
LABEL org.opencontainers.image.description="AI payment guardrails: identity, intent, secret vault. Python + MCP."

RUN pip install --no-cache-dir spendshield

WORKDIR /data

# stdio MCP server 入口
ENTRYPOINT ["spendshield-mcp"]
