# Phone access

Play from a phone while the backend and engines run on your computer. Nothing here is a code change: you set two `make dev` variables.

| Variable | Default | Meaning |
|---|---|---|
| `DEV_HOST` | `127.0.0.1` | Interface the frontend binds. `127.0.0.1` is this computer only; `0.0.0.0` is every interface. |
| `PUBLIC_HOST` | empty | The address your phone types: the computer's LAN IP, or its Tailscale name. |

When `PUBLIC_HOST` is set, the browser calls the API at the relative path `/api` and the frontend server proxies it to the backend (`/api/health` reaches the backend's `/health`). The phone therefore needs one address, one port, and no CORS entry. The backend stays on `127.0.0.1` and is never exposed to the network.

## Option 1: Tailscale with HTTPS (recommended, installable)

Phones only offer "install app" over HTTPS, and `tailscale serve` provides a certificate for your tailnet name.

1. Install Tailscale on the computer and the phone and sign both into the same tailnet. Enable MagicDNS and HTTPS certificates in the Tailscale admin console (DNS page).
2. Start the app. The frontend can stay on loopback, because `tailscale serve` connects from the same machine:

   ```bash
   make dev PUBLIC_HOST=<your-machine>.<tailnet>.ts.net
   ```

3. In a second terminal, publish the frontend port over HTTPS to your tailnet only:

   ```bash
   tailscale serve --bg 3000      # use your FRONTEND_PORT if you changed it
   tailscale serve status         # prints the https://<your-machine>.<tailnet>.ts.net URL
   ```

4. Open that URL on the phone and install it (below). Stop sharing with `tailscale serve reset`.

Do not use `tailscale funnel`: that publishes the app to the public internet.

## Option 2: Home network (plain HTTP, playable but not installable)

```bash
make dev DEV_HOST=0.0.0.0 PUBLIC_HOST=192.168.x.y    # your computer's LAN address
```

Open `http://192.168.x.y:3000` on a phone on the same Wi-Fi. Find the address with `ip addr` (Linux), `ipconfig getifaddr en0` (macOS) or `ipconfig` (Windows). The game works, but browsers will not offer to install it, because plain HTTP is not a secure context. `DEV_HOST=0.0.0.0` exposes the frontend to everyone on that network, so use it on networks you trust.

`make dev` stops with a message naming the variable if the port is busy or `DEV_HOST` is not one of this computer's addresses.

## Install the app (PWA)

Needs the HTTPS address from Option 1.

- **Android, Chrome:** open the URL, tap the menu (three dots), then **Install app** (or **Add to Home screen**). Confirm.
- **iPhone or iPad, Safari:** open the URL in Safari (not an in-app browser), tap **Share**, then **Add to Home Screen**, then **Add**.

The icon opens the app full screen, like a native app.

## Troubleshooting

- **The page never loads.** The computer's firewall may block the frontend port (LAN option). Allow inbound TCP on `FRONTEND_PORT`, for example `sudo ufw allow from 192.168.0.0/16 to any port 3000 proto tcp`. Some Wi-Fi networks isolate clients from each other; try a different network or use Tailscale. On Tailscale, check both devices show as connected.
- **The page loads but styling or the board is stuck, with blocked-request warnings in the dev server log.** `PUBLIC_HOST` is missing or does not match the address in the phone's address bar. Next.js blocks dev-server requests from unlisted hosts. Restart `make dev` with the exact host you type on the phone.
- **"Failed to fetch" or CORS errors.** The phone is calling the API directly instead of through `/api`, usually because `NEXT_PUBLIC_API_BASE_URL` is set in your environment or `PUBLIC_HOST` was not passed. Unset the variable, or add the phone's origin to `CHESS_TUTOR_FRONTEND_ORIGINS` (a JSON list, for example `["http://192.168.x.y:3000"]`) if you do call the backend directly.
- **Blocked as mixed content.** An HTTPS page cannot call an `http://` API. Keep `NEXT_PUBLIC_API_BASE_URL=/api` (the default when `PUBLIC_HOST` is set) so the API shares the page's HTTPS origin.
- **No install option.** The address must start with `https://`, and iOS requires Safari. Confirm with `tailscale serve status`.
- **Moves from the opponent never appear.** Server-sent events pass through `/api`; a different proxy in front must not buffer them (see the Caddy `flush_interval -1` note in [deploy/README.md](../deploy/README.md)).

`make dev` proxies `/api` in development only. The Docker deployment bakes its API address into the frontend image; see [deploy/README.md](../deploy/README.md).
