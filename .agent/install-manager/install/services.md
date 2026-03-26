# Service Setup

## Local database service

Start a local PostgreSQL + VectorChord container:

```bash
docker run -d \
  --name vchord-postgres \
  -e POSTGRES_PASSWORD=mysecretpassword \
  -p 5433:5432 \
  tensorchord/vchord-postgres:latest
```

## MCP server (local shell)

```bash
doc-hub serve mcp
```

## MCP server (persistent service)

Example systemd unit:

```bash
cat > ~/.config/systemd/user/doc-hub-mcp.service << 'EOF'
[Unit]
Description=doc-hub MCP Server (SSE on :8340)
After=network.target postgresql.service

[Service]
Type=simple
WorkingDirectory=%h
ExecStart=%h/.local/bin/doc-hub serve mcp --transport sse --port 8340
Restart=always
RestartSec=10
Environment=HOME=%h
EnvironmentFile=%h/.local/share/doc-hub/env

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now doc-hub-mcp.service
```

## Verify MCP service state

```bash
./.agent/install-manager/scripts/check-mcp.sh
```
