"""
Production server voor lokaal netwerk.
Start met:  py serve.py

Bereikbaar voor iedereen op hetzelfde wifi via http://<jouw-ip>:5000
"""

import logging
import socket

from dotenv import load_dotenv
load_dotenv()

from waitress import serve
from app import app
import scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 5000))

# Zorg dat de DB tabellen en seed-data aanwezig zijn (veilig bij redeploy)
from seed import seed
seed()


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


if __name__ == "__main__":
    scheduler.start(app)

    ip = local_ip()
    print()
    print("=" * 52)
    print("  WK Poule 2026 — server gestart")
    print(f"  Lokaal:   http://localhost:{PORT}")
    print(f"  Netwerk:  http://{ip}:{PORT}")
    print("  Stop:     Ctrl+C")
    print("=" * 52)
    print()

    serve(app, host=HOST, port=PORT, threads=8)
