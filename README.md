# SM5K WRC Rotor Control — Pi network edition

A **daemon + network client** split for controlling a **WRC Wi-Fi Rotor Controller** (GS-232 over TCP) from anywhere on your network — not just the same LAN as the rotor. Same compass-dial GUI as the classic [sm5tog/wrc-rotor-control](https://github.com/sm5tog/wrc-rotor-control), but a daemon on a small always-on box (e.g. a Raspberry Pi) owns the connection to the rotor, and any number of GUI clients elsewhere on the network (or over Tailscale/VPN) talk to that daemon instead of the rotor directly.

![status](https://img.shields.io/badge/status-active-brightgreen)

## Why a separate repo

This is a different architecture, not a drop-in replacement — pick the one that matches your setup:

- **[wrc-rotor-control](https://github.com/sm5tog/wrc-rotor-control)** — single Windows app, talks directly to the rotor over your LAN. Simplest option if you're always on the same network as the rotor.
- **wrc-rotor-pi (this repo)** — for controlling the rotor from a *different* location/network than where the rotor physically is (e.g. a remote shack, over a VPN). Requires a small always-on Linux box (Pi or similar) on the same LAN as the rotor to run the daemon.

**Run only one of the two against a given rotor at a time.** The WRC's GS-232/telnet server does not reliably support multiple simultaneous connections — running the classic app and this daemon against the same rotor at once can cause dropped commands or connection issues.

## Components

- **`rotor_daemon.py`** — headless daemon. Runs on a Raspberry Pi (or any always-on Linux box) on the same LAN as the WRC. Owns the GS-232/TCP connection to the rotor and the N1MM/Log4OM/DXLab UDP listener. Exposes a small JSON-lines protocol over TCP (default port 5561) for clients.
- **`rotor_klient.py`** — the GUI, identical compass-dial interaction as the classic app. Connects to `rotor_daemon.py` over the network instead of to the rotor directly.

## Requirements

- **Daemon:** any Linux box with Python 3 (no extra packages — standard library only), reachable on the same LAN as the WRC.
- **Client:** Windows (tested) — should run anywhere Python + Tkinter is available.

## Running the daemon

```
python3 rotor_daemon.py
```

Creates `rotord_config.json` next to the script on first change (defaults: WRC at `192.168.1.1:23`, UDP listener on port 12040, TCP server on `0.0.0.0:5561`). Recommended: run it as a systemd service with `Restart=always` so it survives reboots.

## Running the client

```
python rotor_klient.py
```

Creates `rotor_klient_settings.json` next to the script with default values. Open the ⚙ settings icon to set the daemon's IP/port, and — since the daemon owns the UDP listener your logging software talks to — you can also change that UDP port remotely from here, no SSH to the Pi needed.

## Support this project

If this saved you some time or money, consider [buying me a coffee via PayPal](https://www.paypal.com/donate/?hosted_button_id=5VX8NE7LQEGF4). Entirely optional — the software is free either way.

## License

MIT — see [LICENSE](LICENSE).

73, Jonny — SM5TOG
