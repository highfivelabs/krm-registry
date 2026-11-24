# The registry's hosted MCP server (registry.highfivelabs.dev/mcp): mcp_server.py
# over streamable HTTP, reading the published site. Standard library only.
FROM python:3.12-slim
WORKDIR /app
COPY mcp_server.py .
ENV PYTHONUNBUFFERED=1
USER nobody
# Cloud Run sets $PORT; --http 0 listens on it.
CMD ["python3", "mcp_server.py", "--url", "https://registry.highfivelabs.dev", "--http", "0"]
