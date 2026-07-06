#!/usr/bin/env python3
"""Development server with HTTP Range request support.

Python's built-in http.server doesn't support Range requests, which
PMTiles requires for reading individual tiles from the archive.
This server adds that support for local development.

Usage:
    python scripts/serve.py build/ramba
    python scripts/serve.py build/ramba --port 8090
"""

import argparse
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler

import console


class RangeRequestHandler(SimpleHTTPRequestHandler):
    """HTTP handler with Range request support for PMTiles."""

    def send_head(self):
        """Handle Range header if present."""
        path = self.translate_path(self.path)

        if not os.path.isfile(path):
            return super().send_head()

        range_header = self.headers.get("Range")
        if not range_header:
            return super().send_head()

        # Parse Range: bytes=start-end. Three RFC 7233 forms:
        #   bytes=a-b   → explicit span
        #   bytes=a-    → from a to EOF
        #   bytes=-n    → SUFFIX: the LAST n bytes (not 0-n, which is
        #                 how this used to mis-parse it)
        try:
            range_spec = range_header.strip().split("=")[1]
            parts = range_spec.split("-")
            file_size = os.path.getsize(path)

            if not parts[0] and parts[1]:
                # Suffix form: last n bytes.
                start = max(file_size - int(parts[1]), 0)
                end = file_size - 1
            else:
                start = int(parts[0]) if parts[0] else 0
                end = int(parts[1]) if parts[1] else file_size - 1
            end = min(end, file_size - 1)
            length = end - start + 1
            if length <= 0 or start >= file_size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.end_headers()
                return None

            f = open(path, "rb")
            f.seek(start)
            data = f.read(length)
            f.close()

            self.send_response(206)
            self.send_header("Content-Type", self.guess_type(path))
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            import io

            return io.BytesIO(data)
        except (ValueError, IndexError):
            return super().send_head()

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def do_HEAD(self):
        """Ensure HEAD responses include Content-Length."""
        path = self.translate_path(self.path)
        if os.path.isfile(path):
            self.send_response(200)
            self.send_header("Content-Type", self.guess_type(path))
            self.send_header("Content-Length", str(os.path.getsize(path)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
        else:
            super().do_HEAD()


def main():
    parser = argparse.ArgumentParser(description="Dev server with Range request support")
    parser.add_argument("directory", nargs="?", default=".", help="Directory to serve")
    parser.add_argument("--port", "-p", type=int, default=8090, help="Port (default: 8090)")
    args = parser.parse_args()

    os.chdir(args.directory)
    server = HTTPServer(("", args.port), RangeRequestHandler)
    console.step(f"Serving {os.path.abspath('.')} at http://localhost:{args.port}")
    console.step("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.step("\nStopped.")


if __name__ == "__main__":
    main()
