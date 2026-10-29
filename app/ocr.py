"""Read words from a picture on this PC. Nothing here writes the books."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "0")

PS1 = Path(__file__).resolve().parent / "read_image.ps1"

MONEY = re.compile(r"\$?\s*(\d{1,3}(?:,\d{3})*|\d+)(?:\.(\d{2}))?")
CURRENCY = re.compile(r"\$\s*\d[\d,]*(?:\.\d{2})?|\b\d[\d,]*\.\d{2}\b")
DATE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")
SKIP_LINE = re.compile(
    r"^(qty|quantity|item|description|price|amount|unit|total|subtotal|invoice|page)\b",
    re.I,
)


_PADDLE = None


def read_paddle(path: Path) -> list[dict]:
    """Read an invoice with PP-OCRv6 medium. The model stays on this PC."""
    engine = _paddle_engine()
    pages = engine.predict(str(path))
    lines = []
    for page in pages:
        payload = page.json if hasattr(page, "json") else page
        if isinstance(payload, dict) and "rec_texts" not in payload and isinstance(payload.get("res"), dict):
            payload = payload["res"]
        texts = list(payload.get("rec_texts") or [])
        boxes = list(payload.get("rec_boxes") or [])
        for text, box in zip(texts, boxes):
            cleaned = " ".join(str(text).split())
            if not cleaned:
                continue
            left = int(box[0])
            top = int(box[1])
            lines.append({"text": cleaned, "words": [{"t": cleaned, "x": left, "y": top}]})
    return lines


def _paddle_engine():
    global _PADDLE
    if _PADDLE is None:
        from paddleocr import PaddleOCR

        _PADDLE = PaddleOCR(
            text_detection_model_name="PP-OCRv6_medium_det",
            text_recognition_model_name="PP-OCRv6_medium_rec",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device="cpu",
        )
    return _PADDLE


def read_lines(path: Path) -> list[dict]:
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PS1),
            str(path),
        ],
        capture_output=True,
        timeout=60,
    )
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(message or "Could not read that picture.")
    raw = completed.stdout.decode("utf-8-sig", errors="replace").strip()
    if not raw:
        return []
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        parsed = [parsed]
    lines = []
    for line in parsed:
        words = line.get("words") or []
        lines.append({"text": line.get("text") or "", "words": words})
    return lines


def _money_values(text: str) -> list[int]:
    found = []
    for match in MONEY.finditer(text):
        dollars = int(match.group(1).replace(",", ""))
        cents = int(match.group(2) or "0")
        if match.group(2) or "$" in match.group(0):
            found.append(dollars * 100 + cents)
    return found


def _plain_numbers(text: str) -> list[float]:
    numbers = []
    for match in re.finditer(r"\d+(?:\.\d+)?", text.replace(",", "")):
        numbers.append(float(match.group(0)))
    return numbers


def _line_has(line: dict, word: str) -> bool:
    return word in line.get("text", "").lower()


def suggest_totals(lines: list[dict]) -> dict:
    meat = None
    grocery = None
    for line in lines:
        text = line.get("text") or ""
        low = text.lower()
        amounts = _money_values(text)
        if not amounts:
            continue
        if "meat" in low and "groc" in low:
            words = line.get("words") or []
            meat_x = next((word.get("x", 0) for word in words if "meat" in str(word.get("t", "")).lower()), None)
            groc_x = next((word.get("x", 0) for word in words if "groc" in str(word.get("t", "")).lower()), None)
            if meat_x is not None and groc_x is not None and len(amounts) >= 2:
                if meat_x < groc_x:
                    meat = meat if meat is not None else amounts[0]
                    grocery = grocery if grocery is not None else amounts[-1]
                else:
                    grocery = grocery if grocery is not None else amounts[0]
                    meat = meat if meat is not None else amounts[-1]
            continue
        if "meat" in low and meat is None:
            meat = amounts[-1]
        elif "groc" in low and grocery is None:
            grocery = amounts[-1]
    return {"meat_cents": meat, "grocery_cents": grocery}


def _date_in(text: str) -> str | None:
    match = DATE.search(text)
    if not match:
        return None
    month, day, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    if year < 100:
        year += 2000
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _supplier(lines: list[dict]) -> str:
    for line in lines:
        text = " ".join((line.get("text") or "").split())
        if len(re.sub(r"[^A-Za-z]", "", text)) < 3:
            continue
        if re.search(r"\b(invoice|total|date|page|bill to|ship)\b", text, re.I):
            continue
        return text[:80]
    return ""


def _printed_total(lines: list[dict]) -> int | None:
    ranked = []
    for line in lines:
        text = line.get("text") or ""
        low = text.lower()
        amounts = _money_values(text)
        if not amounts:
            continue
        if re.search(r"amount due|balance due|total due", low):
            ranked.append((0, amounts[-1]))
        elif re.search(r"invoice total|grand total", low):
            ranked.append((1, amounts[-1]))
        elif re.search(r"\btotal\b", low) and "subtotal" not in low:
            ranked.append((2, amounts[-1]))
    if ranked:
        ranked.sort(key=lambda item: item[0])
        return ranked[0][1]
    amounts = [amount for line in lines for amount in _money_values(line.get("text") or "")]
    return max(amounts) if amounts else None


NOT_AN_ITEM = re.compile(
    r"\b(subtotal|amount due|balance due|grand total|invoice total|unit cost|line total|"
    r"invoice number|invoice date|pay by|bill to|sample|phone|qty|quantity)\b|^total\b|^tax\b",
    re.I,
)


def _clean_name(text: str) -> str:
    name = " ".join(re.sub(r"[^A-Za-z0-9&'./ -]", " ", text).split())
    return name


def _row_from_line(text: str) -> dict | None:
    cleaned = " ".join(text.split())
    if not cleaned or SKIP_LINE.search(cleaned) or NOT_AN_ITEM.search(cleaned):
        return None
    amounts = _money_values(cleaned)
    if not amounts:
        return None
    if len(amounts) >= 2:
        unit, total = amounts[-2], amounts[-1]
    else:
        total = amounts[0]
        unit = None
    stripped = CURRENCY.sub(" ", cleaned)
    numbers = list(re.finditer(r"\d+(?:\.\d+)?", stripped))
    if numbers:
        qty = float(numbers[-1].group(0))
        name_text = stripped[: numbers[-1].start()] + " " + stripped[numbers[-1].end() :]
    else:
        qty = round(total / unit, 3) if unit else 1
        name_text = stripped
    name = _clean_name(name_text)
    if len(re.sub(r"[^A-Za-z]", "", name)) < 2 or NOT_AN_ITEM.search(name):
        return None
    if unit is None:
        unit = int(round(total / qty)) if qty else total
    return {
        "name": name,
        "quantity": qty,
        "unit_cost_cents": unit,
        "line_total_cents": total,
        "category": _kind(name),
    }


def _kind(name: str) -> str:
    low = name.lower()
    if any(word in low for word in ("beef", "chicken", "lamb", "meat", "steak", "pork", "turkey", "fish", "ground")):
        return "meat"
    if any(word in low for word in ("apple", "banana", "lettuce", "tomato", "onion", "produce", "pepper")):
        return "produce"
    return "grocery"


def _reading_order(lines: list[dict]) -> list[str]:
    """Put words that sit on the same row back together, even if the reader split the columns."""
    words = []
    for line in lines:
        for word in line.get("words") or []:
            token = str(word.get("t") or "").strip()
            if token:
                words.append({"t": token, "x": int(word.get("x") or 0), "y": int(word.get("y") or 0)})
    if not words:
        return [" ".join((line.get("text") or "").split()) for line in lines if line.get("text")]
    words.sort(key=lambda word: (word["y"], word["x"]))
    rows: list[list[dict]] = []
    for word in words:
        if rows and word["y"] - rows[-1][0]["y"] <= 18:
            rows[-1].append(word)
        else:
            rows.append([word])
    texts = []
    for row in rows:
        row.sort(key=lambda word: word["x"])
        texts.append(" ".join(word["t"] for word in row))
    return texts


def _item_rows(lines: list[dict]) -> list[dict]:
    rows = []
    pending_name = ""
    for text in _reading_order(lines):
        if not text or SKIP_LINE.search(text) or NOT_AN_ITEM.search(text):
            pending_name = ""
            continue
        row = _row_from_line(text)
        if row:
            rows.append(row)
            pending_name = ""
            continue
        amounts = _money_values(text)
        letters = re.sub(r"[^A-Za-z]", "", text)
        if amounts and pending_name:
            joined = _row_from_line(f"{pending_name} {text}")
            pending_name = ""
            if joined:
                rows.append(joined)
            continue
        if len(letters) >= 2 and not amounts and not re.search(r"\b(company|market|street|avenue|invoice)\b", text, re.I):
            pending_name = text
        else:
            pending_name = ""
    return rows


def suggest_invoice(lines: list[dict]) -> dict:
    ordered = [{"text": text, "words": []} for text in _reading_order(lines)]
    text = "\n".join(line["text"] for line in ordered)
    return {
        "supplier": _supplier(ordered),
        "invoice_date": _date_in(text),
        "printed_total_cents": _printed_total(ordered),
        "lines": _item_rows(lines)[:40],
    }


def suggest_sold(lines: list[dict]) -> list[dict]:
    rows = []
    for line in lines:
        text = " ".join((line.get("text") or "").split())
        if not text or SKIP_LINE.search(text):
            continue
        if re.search(r"\b(subtotal|tax|total|change|cash|visa|debit|ebt|balance)\b", text, re.I):
            continue
        amounts = _money_values(text)
        stripped = MONEY.sub(" ", text)
        qty_match = re.search(r"\b\d+(?:\.\d+)?\b", stripped)
        name = re.sub(r"\d+(?:\.\d+)?", " ", stripped)
        name = " ".join(re.sub(r"[^A-Za-z&'./ -]", " ", name).split())
        if len(name) < 2:
            continue
        qty = float(qty_match.group(0)) if qty_match else 1
        price = amounts[-1] if amounts else None
        rows.append({"name": name, "qty": qty, "price_cents": price})
    return rows[:80]
