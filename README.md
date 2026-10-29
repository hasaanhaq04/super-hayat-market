# Super Hayat Market

A local accounting and inventory MVP built for my dad's international grocery and meat store.

The project grew out of a weekly paper worksheet and a practical question: how can a small store keep track of sales, vendor purchases, stock, and unpaid bills without entering the same information in several places?

Super Hayat brings those records into one application. It runs in a browser on the store computer, while Python handles the calculations and SQLite stores the books locally.

## What it does

- **Overview:** monthly revenue, invoice purchases, outstanding supplier balances, and unassigned cash.
- **Invoice imports:** review structured files prepared from invoice pictures by ChatGPT, correct extracted fields, check totals, flag possible duplicates, and post the reviewed invoices.
- **Suppliers and payments:** track unpaid and partially paid invoices, record payments, and see payment history.
- **Inventory:** receive stock from invoices, edit product details, record physical counts and waste, and remove items from the active inventory list.
- **Money plan:** start with a checked cash balance, protect money for supplier invoices, payroll, rent, taxes, restocking, and a cushion, then see what remains unassigned.
- **Sales and reports:** enter daily meat and grocery sales, revisit past dates, and review daily, weekly, monthly, and yearly activity.
- **Local backups:** save a dated copy of the database.

## The invoice workflow

1. Download the ChatGPT instructions from **Import invoices**.
2. Give ChatGPT those instructions and pictures of supplier invoices.
3. Download the resulting JSON file.
4. Import it into Super Hayat and review the supplier, original date, total, quantities, prices, and categories.
5. Save the reviewed invoices. Their costs land on the original purchase dates, stock quantities increase, and unpaid amounts appear in supplier balances.

ChatGPT is an optional preparation step, not a live connection to the application. Images submitted to ChatGPT are processed by that service; the database remains on the store computer. A fictional example is included at `examples/sample-import.json`, and the import screen also has a practice example.

The source also includes a local PaddleOCR invoice reader and a Windows OCR fallback from the earlier prototype. The current MVP interface centers on reviewed JSON imports; it does not expose a direct scan button.

## How the records fit together

An invoice records a purchase and stock receipt. A payment reduces cash and the amount owed; it does not create another purchase. Daily sales accumulate into monthly revenue. A cash deposit changes cash but does not create another sale.

Revenue, cash, debt, and inventory value are separate figures. The money plan protects all outstanding supplier invoices plus manually entered commitments. It is a conservative planning view, not a scheduled debt forecast. Inventory is estimated using the latest unit cost. Purchases are not presented as cost of goods sold or accounting profit.

## Technology

- Python standard-library HTTP server, bound to `127.0.0.1`
- SQLite with foreign keys and transaction handling
- Vanilla JavaScript, HTML, and CSS
- Optional PaddleOCR / PaddlePaddle for local image extraction
- Windows PowerShell OCR fallback

Money is stored as integer cents. Batch imports use a transaction, and payments and their cash movements are saved together. No frontend framework or build step is required.

## Run locally

Use Python 3.11 or newer; development used Python 3.13. Git and Python are the only requirements for the core dashboard and JSON import workflow.

```sh
git clone https://github.com/hasaanhaq04/super-hayat-market.git
cd super-hayat-market
python app/server.py
```

Open http://127.0.0.1:8765. On Windows, you can also double-click **Start Super Hayat.bat**. A fresh checkout creates an empty `data/` folder and database automatically. Keep the server running while using the app.

Optional local OCR dependencies:

```sh
python -m pip install -r requirements-ocr.txt
```

OCR model weights download on first use and require internet access for that download. Windows OCR requires Windows. The core accounting screens and reviewed JSON imports do not require OCR packages or an OpenAI API key.

## Verification

```sh
python check_mvp.py
```

The check uses a temporary database and exercises invoice import, inventory receipts, duplicate prevention, retroactive costs, payments, cash allocation, and balance reconciliation. It does not alter the store database.

## Current scope and next steps

This is a working personal project and local prototype, not production accounting or payroll software. Current limitations include manual cash reconciliation, manual adjustment of protected payroll and other commitments after payment, and estimated inventory valuation. A matching supplier, purchase date, and total can trigger a duplicate warning even for genuinely different invoices.

Planned work includes employee payroll records, opening supplier balances, scheduled debt repayments, and imports of PC America revenue and item-level sales reports. A direct ChatGPT integration has been discussed but is not implemented.

## Public demo data

This repository contains source code and fictional sample inputs only. The store database, invoice images, original worksheet, uploads, logs, and database backups are excluded. Run this as a local application; the development server has no public-hosting authentication layer.
