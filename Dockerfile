# The registry's hosted MCP server (registry.highfivelabs.dev/mcp): cmd/mcp
# over streamable HTTP, reading the published site. Go's standard library only.
FROM golang:1.25 AS build
WORKDIR /src
COPY go.mod ./
COPY cmd/mcp/*.go ./cmd/mcp/
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /mcp ./cmd/mcp

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /mcp /mcp
# Cloud Run sets $PORT; --http 0 listens on it.
ENTRYPOINT ["/mcp", "--url", "https://registry.highfivelabs.dev", "--http", "0"]
