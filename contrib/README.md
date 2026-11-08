# Running the MemALL Gateway as a service

`memall start-gateway` runs in the foreground until Ctrl+C. For a NAS / server
you usually want it to start at boot and restart if it crashes. This directory
ships a systemd unit template for that.

## What the unit does

- Runs `memall start-gateway` as a **non-root** system user (`memall` by default).
- Restarts automatically on failure (`Restart=on-failure`).
- Binds to loopback only by default (`--host 127.0.0.1`), so the API/MCP
  endpoints are not exposed to the network. Change to `0.0.0.0` if you
  deliberately want LAN access — the API endpoints then require a Bearer token.
- Reads the auth token from `/etc/memall/gateway.env` (`MEMALL_SECRET_KEY`), so
  the token stays stable across restarts and MCP clients can pin it.

## Install

```bash
# 1. A dedicated unprivileged user for the gateway
useradd --system --home /var/lib/memall --shell /usr/sbin/nologin memall

# 2. Install the package (system-wide or into a venv the unit can see)
pip install memall-os          # or: pip install -e /path/to/MemALL

# 3. A fixed token so clients survive restarts
install -d -o memall -g memall -m 750 /etc/memall
echo "MEMALL_SECRET_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')" \
  > /etc/memall/gateway.env
chmod 640 /etc/memall/gateway.env
chown root:memall /etc/memall/gateway.env

# 4. Install the unit
install -m 644 contrib/memall-gateway.service /etc/systemd/system/

# 5. Enable + start
systemctl daemon-reload
systemctl enable --now memall-gateway
systemctl status memall-gateway
```

## LAN exposure

Edit the unit's `ExecStart` to change the bind address and port:

```ini
ExecStart=/usr/local/bin/memall start-gateway --host 0.0.0.0 --port 9920
```

Then `systemctl daemon-reload && systemctl restart memall-gateway`. When bound
to `0.0.0.0`, every `/api`/`/mcp` request from a non-loopback peer must carry
`Authorization: Bearer <MEMALL_SECRET_KEY>` — the token is the only thing
standing between the LAN and your memory store, so keep `gateway.env` root-only.

## Read the token back

```bash
sudo cat /etc/memall/gateway.env      # MEMALL_SECRET_KEY=...
```

MCP clients connect to `http://127.0.0.1:9920/mcp` (or the LAN IP) with
`Authorization: Bearer <that value>`.
