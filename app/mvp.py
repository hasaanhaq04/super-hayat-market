"""Reviewed imports and a cash plan, using the existing local books."""
import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation
import db


def init():
    with db.connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS cash_plan (id INTEGER PRIMARY KEY CHECK(id=1), balance INTEGER NOT NULL, checked TEXT NOT NULL, reserves TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cash_events (id INTEGER PRIMARY KEY, day TEXT NOT NULL, amount INTEGER NOT NULL, kind TEXT NOT NULL, note TEXT);
        CREATE TABLE IF NOT EXISTS imports (fingerprint TEXT PRIMARY KEY, invoice_id INTEGER NOT NULL);
        ''')


def amount(value):
    try:
        number = Decimal(str(value).replace(',', '').replace('$', ''))
        if not number.is_finite() or number < 0:
            raise ValueError('Amounts must be zero or greater.')
        return int(number.quantize(Decimal('.01')) * 100)
    except (InvalidOperation, TypeError):
        raise ValueError('Enter a valid dollar amount.')


def preview(payload):
    raw = payload.get('invoices')
    if not isinstance(raw, list) or not 1 <= len(raw) <= 100:
        raise ValueError('The file must contain between 1 and 100 invoices.')
    existing = db.list_invoices()
    results = []
    seen = set()
    for item in raw:
        supplier = str(item.get('supplier', '')).strip()
        if not supplier:
            raise ValueError('Every invoice needs a supplier.')
        day = db.parse_day(item.get('invoice_date', '')).isoformat()
        due = db.parse_day(item['due_date']).isoformat() if item.get('due_date') else None
        lines = []
        for line in item.get('items', []):
            qty = db.quantity(line.get('quantity'), 'Quantity')
            if qty <= 0 or not str(line.get('name', '')).strip():
                raise ValueError('Every item needs a name and a positive quantity.')
            unit = amount(line.get('unit_price'))
            lines.append(dict(name=str(line['name']).strip(), quantity=qty,
                              unit_cost_cents=unit, line_total_cents=int(Decimal(str(qty))*unit),
                              category=line.get('category') if line.get('category') in db.LINE_CATEGORIES else 'grocery'))
        total = amount(item.get('total'))
        paid = amount(item.get('amount_paid', 0))
        if paid > total:
            raise ValueError('Amount paid cannot exceed the invoice total.')
        fingerprint = hashlib.sha256(json.dumps([supplier.lower(), day, str(item.get('invoice_number','')), total, lines], sort_keys=True).encode()).hexdigest()
        with db.connect() as c:
            imported = c.execute('SELECT invoice_id FROM imports WHERE fingerprint=?', (fingerprint,)).fetchone()
        duplicate = fingerprint in seen or bool(imported) or any(x['supplier'].lower() == supplier.lower() and x['invoice_date'] == day and x['owed_cents'] == total for x in existing)
        seen.add(fingerprint)
        results.append(dict(supplier=supplier, invoice_date=day, due_date=due, printed_total_cents=total,
                            notes='Invoice '+str(item.get('invoice_number','')), lines=lines,
                            fingerprint=fingerprint, duplicate=duplicate, amount_paid=paid,
                            difference_cents=total-sum(x['line_total_cents'] for x in lines)))
    return results


def post_import(payload):
    rows = preview(payload)
    if any(x['duplicate'] for x in rows):
        raise ValueError('A possible duplicate is present. Remove it from the preview before saving.')
    # One outer transaction prevents a partly posted batch.
    with db.connect() as conn:
        token = db._ACTIVE_CONNECTION.set(conn)
        try:
            ids = []
            for row in rows:
                saved = db.save_invoice(row)
                ids.append(saved['id'])
                conn.execute('INSERT INTO imports VALUES (?,?)', (row['fingerprint'], saved['id']))
                if row['amount_paid']:
                    db.pay_invoice(saved['id'], dict(amount_cents=row['amount_paid'], paid_on=row['invoice_date']))
            return {'ids': ids}
        finally:
            db._ACTIVE_CONNECTION.reset(token)


def overview(day):
    dash = db.dashboard(day)
    invoices = db.list_invoices()
    with db.connect() as c:
        plan = c.execute('SELECT * FROM cash_plan WHERE id=1').fetchone()
        events = [dict(r) for r in c.execute('SELECT * FROM cash_events ORDER BY id DESC')]
        payments = [dict(r) for r in c.execute('SELECT p.*, i.supplier FROM invoice_payments p JOIN invoices i ON i.id=p.invoice_id ORDER BY p.paid_on DESC,p.id DESC')]
    balance = None
    reserves = {}
    if plan:
        reserves = json.loads(plan['reserves'])
        balance = plan['balance'] + sum(x['amount'] for x in events if x['id'] > json.loads(plan['reserves']).get('_event', 0))
        reserves.pop('_event', None)
    debt = sum(x['unpaid_cents'] for x in invoices)
    return dict(dashboard=dash, invoices=invoices, payments=payments, events=events,
                cash=balance, checked=plan['checked'] if plan else None, reserves=reserves,
                debt=debt, unassigned=balance-debt-sum(reserves.values()) if balance is not None else None)


def save_plan(p):
    reserves = {key: amount(p.get(key, 0)) for key in ('Payroll','Rent & utilities','Taxes','Restocking','Cushion','Other bills')}
    with db.connect() as c:
        reserves['_event'] = c.execute('SELECT COALESCE(MAX(id),0) FROM cash_events').fetchone()[0]
        c.execute('INSERT OR REPLACE INTO cash_plan VALUES (1,?,?,?)', (amount(p['balance']), date.today().isoformat(), json.dumps(reserves)))


def cash_event(p):
    kind = p.get('kind')
    if kind not in ('deposit', 'withdrawal', 'expense'):
        raise ValueError('Choose deposit, withdrawal, or expense.')
    value = amount(p['amount'])
    if not value:
        raise ValueError('Enter an amount greater than zero.')
    with db.connect() as c:
        c.execute('INSERT INTO cash_events(day,amount,kind,note) VALUES (?,?,?,?)', (db.parse_day(p['date']).isoformat(), value if kind == 'deposit' else -value, kind, str(p.get('note',''))))
