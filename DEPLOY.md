# Deploying to a VPS (24/7)

This runs four always-on containers (dashboard, live scanner, outcome updater, Telegram
bot) plus an nginx reverse proxy in front of the dashboard, all wired together with
`restart: unless-stopped` so they keep running through crashes and VPS reboots.

**If the app talks to Binance**: don't use a US-region VM. Binance rejects API calls from
US-geolocated cloud IPs outright ("Service unavailable from a restricted location"). Use
Europe or Asia.

## 1. Get a VPS

Any small Ubuntu 22.04/24.04 VPS works (DigitalOcean, Hetzner, a non-US GCP/AWS region,
etc. -- 2 vCPU / 4GB RAM gives comfortable headroom for the four Python processes plus
nginx). Note its IP address and SSH in:

```
ssh root@<your-vps-ip>
```

## 2. Install Docker

```
curl -fsSL https://get.docker.com | sh
```

## 3. Copy the project to the VPS

From your own machine (adjust the source path):

```
scp -r "/path/to/crypto-signal-tool" root@<your-vps-ip>:/opt/crypto-signal-tool
```

Or, if you push this project to a Git remote, `git clone` it on the VPS instead.

## 4. Configure secrets

On the VPS, inside `/opt/crypto-signal-tool`:

```
cp .env.example .env
nano .env
```

Fill in `TELEGRAM_BOT_TOKEN` -- create a bot via [@BotFather](https://t.me/BotFather) on
Telegram (`/newbot`), paste the token it gives you. The `telegram_bot` container will
keep crash-looping (harmlessly) until this is set. Everything else has sane defaults
from `.env.example`.

## 5. Get a nicer hostname (optional, no signup needed)

A raw IP address works fine, but [nip.io](https://nip.io) gives you a real,
memorable hostname for free with zero signup -- it's a wildcard DNS service that
resolves `<anything>.<ip-with-dashes>.nip.io` straight to that IP. E.g. for
`203.0.113.10`, `patternpulse.203-0-113-10.nip.io` just works, no configuration needed
beyond deciding on the name.

## 6. Bring the stack up

```
docker compose build
docker compose up -d
```

Check everything started:

```
docker compose ps
docker compose logs -f scanner
```

## 7. Use it

Open `http://<your-hostname-or-ip>/` in a browser to land straight on the dashboard --
no login. On Telegram, message your bot and send `/subscribe` to start getting alerts.

**Note**: the dashboard has no authentication. Anyone with the link can view it (watchlist,
signals, portfolio size you've entered -- no credentials or funds are ever at risk since
this tool never executes trades). Don't publish the link anywhere public if you'd rather
keep it private.

## Updating after a code change

```
cd /opt/crypto-signal-tool
# copy/pull your changes, then:
docker compose build
docker compose up -d
```

## Optional: HTTPS

Once you have a real hostname (nip.io or a proper domain), you can add free HTTPS via
Let's Encrypt/certbot -- ask if you want this wired up.
