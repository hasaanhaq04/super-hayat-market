"""Local books for Super Hayat Market. Listens on this computer only."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import socket
import uuid
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import db
import ocr
import mvp

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
HOST = "127.0.0.1"
PORT = 8765
MAX_IMAGE = 12_000_000

db.init_db()
mvp.init()


def _save_incoming(payload: dict) -> Path:
    raw_text = str(payload.get("image_base64") or "")
    if not raw_text:
        raise ValueError("Choose a picture first.")
    encoded = raw_text.split(",", 1)[-1]
    try:
        raw = base64.b64decode(encoded, validate=False)
    except Exception as exc:
        raise ValueError("That picture could not be read.") from exc
    if not raw or len(raw) > MAX_IMAGE:
        raise ValueError("That picture is too large.")
    if raw[:3] == b"\xff\xd8\xff":
        ext = ".jpg"
    elif raw[:8] == b"\x89PNG\r\n\x1a\n":
        ext = ".png"
    elif raw[:4] == b"RIFF":
        ext = ".webp"
    else:
        ext = ".img"
    name = f"{date.today().strftime('%Y%m%d')}-{uuid.uuid4().hex[:10]}{ext}"
    path = db.INCOMING / name
    path.write_bytes(raw)
    return path


def _claim_photo(token: str | None, folder: Path) -> str | None:
    if not token:
        return None
    if not re.fullmatch(r"[A-Za-z0-9._-]+", token):
        raise ValueError("That picture is not valid.")
    source = db.INCOMING / token
    if not source.is_file():
        raise ValueError("That picture is no longer available. Choose it again.")
    folder.mkdir(parents=True, exist_ok=True)
    source.replace(folder / token)
    return f"{folder.name}/{token}"


class Handler(BaseHTTPRequestHandler):
    server_version = "SuperHayat/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, code: int, message: str) -> None:
        self._json(code, {"error": message})

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_IMAGE + 100_000:
            raise ValueError("That upload is too large.")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("The form could not be read.") from exc
        if not isinstance(data, dict):
            raise ValueError("The form could not be read.")
        return data

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            if path == "/api/mvp":
                self._json(200, mvp.overview((query.get("date") or [None])[0]))
            elif path == "/api/bootstrap":
                self._json(200, db.bootstrap())
            elif path == "/api/week":
                self._json(200, db.get_week((query.get("start") or [None])[0]))
            elif path == "/api/sales":
                today = date.today().isoformat()
                start = (query.get("start") or [today])[0]
                end = (query.get("end") or [start])[0]
                if "date" in query:
                    self._json(200, db.get_sale(query["date"][0]))
                else:
                    self._json(200, {"sales": db.list_sales(start, end)})
            elif path == "/api/invoices":
                self._json(200, {"invoices": db.list_invoices()})
            elif path.startswith("/api/invoices/"):
                self._json(200, db.get_invoice(int(path.rsplit("/", 1)[-1])))
            elif path == "/api/loans":
                self._json(200, {"loans": db.list_loans()})
            elif path == "/api/bills":
                self._json(200, {"bills": db.list_bills()})
            elif path == "/api/stock":
                self._json(200, db.list_stock((query.get("category") or [None])[0], (query.get("q") or [""])[0]))
            elif path.startswith("/api/products/") and path.endswith("/detail"):
                product_id = int(path.split("/")[3])
                grain = (query.get("grain") or ["week"])[0]
                day = (query.get("date") or [date.today().isoformat()])[0]
                self._json(200, db.product_detail(product_id, grain, day))
            elif path == "/api/products":
                self._json(200, {"products": db.search_products((query.get("q") or [""])[0])})
            elif path == "/api/flow":
                self._json(200, db.money_flow((query.get("date") or [None])[0]))
            elif path == "/api/dashboard":
                self._json(200, db.dashboard((query.get("date") or [None])[0]))
            elif path == "/api/report":
                grain = (query.get("grain") or ["week"])[0]
                day = (query.get("date") or [date.today().isoformat()])[0]
                self._json(200, db.report(grain, day))
            elif path.startswith("/media/"):
                self._media(path)
            else:
                self._static(path)
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception as exc:
            self._error(500, str(exc) or "Something went wrong.")

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            payload = self._read_json()
            if path == "/api/import/preview":
                self._json(200, {"invoices": mvp.preview(payload)})
            elif path == "/api/import/save":
                self._json(200, mvp.post_import(payload))
            elif path == "/api/plan":
                mvp.save_plan(payload)
                self._json(200, {"ok": True})
            elif path == "/api/cash-event":
                mvp.cash_event(payload)
                self._json(200, {"ok": True})
            elif path == "/api/week":
                self._json(200, db.save_week(payload))
            elif path == "/api/sales":
                payload["totals_photo"] = _claim_photo(payload.get("photo_token"), db.SALES)
                self._json(200, db.upsert_sale(payload))
            elif path == "/api/sales/clear":
                self._json(200, db.clear_waiting(payload.get("sale_date"), bool(payload.get("add_to_balance"))))
            elif path == "/api/sales/items":
                payload["items_photo"] = _claim_photo(payload.get("photo_token"), db.SALES)
                self._json(200, db.save_sale_items(payload))
            elif path == "/api/ocr/totals":
                self._json(200, self._ocr(payload, "totals"))
            elif path == "/api/ocr/items":
                self._json(200, self._ocr(payload, "items"))
            elif path == "/api/ocr/invoice":
                self._json(200, self._ocr(payload, "invoice"))
            elif path == "/api/invoices":
                payload["photo_path"] = _claim_photo(payload.get("photo_token"), db.INVOICES)
                self._json(200, db.save_invoice(payload))
            elif path.startswith("/api/invoices/") and path.endswith("/payments"):
                invoice_id = int(path.split("/")[3])
                self._json(200, db.pay_invoice(invoice_id, payload))
            elif path == "/api/loans":
                self._json(200, db.save_loan(payload))
            elif path.startswith("/api/loans/") and path.endswith("/payments"):
                loan_id = int(path.split("/")[3])
                self._json(200, db.pay_loan(loan_id, payload))
            elif path == "/api/bills":
                self._json(200, db.save_bill(payload))
            elif path.startswith("/api/bills/") and path.endswith("/payments"):
                bill_id = int(path.split("/")[3])
                self._json(200, db.pay_bill(bill_id, payload))
            elif path.startswith("/api/products/") and path.endswith("/track"):
                product_id = int(path.split("/")[3])
                self._json(200, db.set_tracking(product_id, bool(payload.get("track"))))
            elif path == "/api/categories":
                self._json(200, db.add_category(payload.get("name", "")))
            elif path == "/api/stock/spoil":
                self._json(200, db.spoil_product(int(payload.get("product_id")), payload.get("qty"), payload.get("day")))
            elif path == "/api/stock/count":
                self._json(200, db.count_product(int(payload.get("product_id")), payload.get("qty"), payload.get("day")))
            elif path == "/api/backup":
                self._json(200, db.backup_database())
            else:
                self._error(404, "That page is not part of the books.")
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception as exc:
            self._error(500, str(exc) or "Something went wrong.")

    def do_PUT(self) -> None:
        try:
            path = unquote(urlparse(self.path).path)
            payload = self._read_json()
            if path.startswith("/api/invoices/"):
                payload["photo_path"] = _claim_photo(payload.get("photo_token"), db.INVOICES)
                self._json(200, db.save_invoice(payload, int(path.rsplit("/", 1)[-1])))
            elif path.startswith("/api/products/"):
                self._json(200, db.update_product(int(path.rsplit("/", 1)[-1]), payload))
            else:
                self._error(404, "That page is not part of the books.")
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception as exc:
            self._error(500, str(exc) or "Something went wrong.")

    def do_DELETE(self) -> None:
        try:
            path = unquote(urlparse(self.path).path)
            if path.startswith("/api/invoices/"):
                db.delete_invoice(int(path.rsplit("/", 1)[-1]))
            elif path.startswith("/api/loans/"):
                db.delete_loan(int(path.rsplit("/", 1)[-1]))
            elif path.startswith("/api/bills/"):
                db.delete_bill(int(path.rsplit("/", 1)[-1]))
            elif path.startswith("/api/products/") and path.endswith("/track"):
                product_id = int(path.split("/")[3])
                self._json(200, db.set_tracking(product_id, False))
                return
            elif path.startswith("/api/products/"):
                db.remove_product(int(path.strip("/").split("/")[-1]))
            else:
                self._error(404, "That page is not part of the books.")
                return
            self._json(200, {"ok": True})
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception as exc:
            self._error(500, str(exc) or "Something went wrong.")

    def _ocr(self, payload: dict, kind: str) -> dict:
        path = _save_incoming(payload)
        reader = "windows"
        try:
            if kind == "invoice":
                lines = ocr.read_paddle(path)
                reader = "pp-ocrv6-medium"
            else:
                lines = ocr.read_lines(path)
        except Exception:
            if kind != "invoice":
                return {
                    "ok": False,
                    "message": "Could not read that picture. Type the numbers. Nothing is saved until you tap Save.",
                    "photo_token": path.name,
                    "lines": [],
                }
            try:
                lines = ocr.read_lines(path)
            except Exception:
                return {
                    "ok": False,
                    "message": "Could not read that picture. Type the numbers. Nothing is saved until you tap Save.",
                    "photo_token": path.name,
                    "lines": [],
                }
        words = [line.get("text") or "" for line in lines if line.get("text")]
        base = {
            "ok": True,
            "message": "These are suggestions. Nothing is saved until you tap Save.",
            "photo_token": path.name,
            "reader": reader,
            "words": words[:80],
        }
        if kind == "totals":
            base.update(ocr.suggest_totals(lines))
        elif kind == "items":
            rows = ocr.suggest_sold(lines)
            matches = db.match_names([row["name"] for row in rows])
            for row, match in zip(rows, matches):
                row["product_id"] = match["product_id"]
                row["product_name"] = match["product_name"]
            base["items"] = rows
        else:
            suggestion = ocr.suggest_invoice(lines)
            matches = db.match_names([row["name"] for row in suggestion["lines"]])
            for row, match in zip(suggestion["lines"], matches):
                row["product_id"] = match["product_id"]
                row["product_name"] = match["product_name"]
                row["category"] = row.get("category") or "grocery"
            base.update(suggestion)
        return base

    def _media(self, path: str) -> None:
        parts = path.split("/")
        if len(parts) != 4 or parts[2] not in ("invoices", "sales"):
            self._error(404, "That picture is not in the books.")
            return
        name = parts[3]
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            self._error(404, "That picture is not in the books.")
            return
        folder = db.INVOICES if parts[2] == "invoices" else db.SALES
        file_path = folder / name
        if not file_path.is_file():
            self._error(404, "That picture is not in the books.")
            return
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        self._send(200, file_path.read_bytes(), mime)

    def _static(self, path: str) -> None:
        if path in ("/", ""):
            path = "/index.html"
        relative = path.lstrip("/")
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", relative) or ".." in relative:
            self._error(404, "That page is not part of the books.")
            return
        file_path = (STATIC / relative).resolve()
        if not str(file_path).startswith(str(STATIC.resolve())) or not file_path.is_file():
            self._error(404, "That page is not part of the books.")
            return
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        if file_path.suffix in (".html", ".css", ".js"):
            mime += "; charset=utf-8"
        self._send(200, file_path.read_bytes(), mime)


class Server(ThreadingHTTPServer):
    """One listener on this PC. A second Start refuses the port instead of sharing it."""

    allow_reuse_address = False

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        host, port = self.server_address[:2]
        self.server_name = socket.getfqdn(host)
        self.server_port = port


def _port_busy(exc: OSError) -> bool:
    winerror = getattr(exc, "winerror", None)
    return winerror in (10048, 10013) or exc.errno in (13, 48, 98)


def main() -> None:
    url = f"http://{HOST}:{PORT}"
    try:
        httpd = Server((HOST, PORT), Handler)
    except OSError as exc:
        if not _port_busy(exc):
            raise
        print(f"Already running at {url}", flush=True)
        print("Use that page. Close the first window if you want to start it again.", flush=True)
        if os.environ.get("SUPERHAYAT_NO_BROWSER") != "1":
            webbrowser.open(url)
        return
    print(f"Super Hayat Market is open at {url}", flush=True)
    print("Close this window to stop it.", flush=True)
    if os.environ.get("SUPERHAYAT_NO_BROWSER") != "1":
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
