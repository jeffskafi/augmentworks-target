"""Loopback-only synthetic agent for verifying a workflow from a consumer repository."""

import argparse
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

QUESTION = "How long do I have to return an item?"


def serve(port: int, api_key: str, days: int) -> None:
    class ChatHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/chat":
                self.send_error(404)
                return
            if not hmac.compare_digest(
                self.headers.get("Authorization", "").encode(), f"Bearer {api_key}".encode()
            ):
                self.send_error(403)
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 4096:
                    raise ValueError
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict) or body.get("message") != QUESTION:
                    raise ValueError
            except (ValueError, UnicodeDecodeError):
                self.send_error(400)
                return

            reply = {"answer": f"You have {days} days to return your order.", "finished": True}
            encoded = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    with HTTPServer(("127.0.0.1", port), ChatHandler) as server:
        server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8197)
    args = parser.parse_args()
    key = os.environ.get("CHATBOT_API_KEY")
    if not key:
        parser.error("Set CHATBOT_API_KEY to a disposable test value.")
    days = int(Path(__file__).with_name("window-days.txt").read_text(encoding="utf-8").strip())
    serve(args.port, key, days)
