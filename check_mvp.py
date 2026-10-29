import os,tempfile,sys
from pathlib import Path
os.environ['SUPERHAYAT_DATA']=tempfile.mkdtemp(prefix='hayat-check-')
sys.path.insert(0,str(Path('app').resolve()))
import db,mvp
db.init_db();mvp.init()
p={'invoices':[{'supplier':'Test vendor','invoice_number':'T1','invoice_date':'2026-10-01','total':'56','items':[{'name':'Chicken leg','quantity':10,'unit_price':'2','category':'meat'},{'name':'Ground beef','quantity':8,'unit_price':'4.50','category':'meat'}]}]}
assert not mvp.preview(p)[0]['duplicate']
r=mvp.post_import(p)
assert mvp.preview(p)[0]['duplicate']
assert db.dashboard('2026-10-01')['cost_cents']==5600
assert len(db.list_stock()['products'])==2
try:mvp.post_import(p);raise AssertionError('duplicate allowed')
except ValueError:pass
mvp.save_plan({'balance':'1000','Payroll':'100'})
a=mvp.overview('2026-10-01');assert a['unassigned']==84400,a
with db.connect() as c:
    token=db._ACTIVE_CONNECTION.set(c)
    try:
        db.pay_invoice(r['ids'][0],{'amount_cents':1000,'paid_on':'2026-10-02'})
        c.execute("INSERT INTO cash_events(day,amount,kind,note) VALUES ('2026-10-02',-1000,'supplier payment','Test')")
    finally:db._ACTIVE_CONNECTION.reset(token)
a=mvp.overview('2026-10-01');assert a['unassigned']==84400,a
assert a['cash']==99000 and a['debt']==4600
assert db.dashboard('2026-10-01')['cost_cents']==5600
mvp.save_plan({'balance':'990','Payroll':'100'})
assert mvp.overview('2026-10-01')['cash']==99000
print('PASS: import, stock, duplicate prevention, original purchase date, payment cash/debt, reserve math and balance reconciliation. Real books untouched.')
