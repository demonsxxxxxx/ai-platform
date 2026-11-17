"""Synthetic external company directory; production identity checks stay active."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os


class DirectoryHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        user_id = os.environ["LIFECYCLE_USER_ID"]
        if self.path != f"/api/userManage/{user_id}/info":
            self.send_error(404)
            return
        payload = json.dumps({
            "workid": user_id, "tenant_id": "default", "role": "user", "active": True,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), DirectoryHandler).serve_forever()
