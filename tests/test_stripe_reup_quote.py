"""A7C5G4 source-isolated, zero-provider Seeker quote contract tests."""
import ast
import datetime as dt
import os
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("GODINC_QUOTE_MAIN_PATH", str(ROOT / "main.py")))

def load_quote():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), str(SOURCE))
    targets = [n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name == "billing_stripe_early_reup_quote"]
    assert len(targets) == 1, "Exactly one approved quote route required"
    node = targets[0]
    decorators = [ast.unparse(d) for d in node.decorator_list]
    assert [d.replace(chr(39), chr(34)) for d in decorators] == ['app.get("/billing/stripe/early-reup/quote")'], decorators
    node.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    return compile(module, str(SOURCE), "exec")

class HTTPError(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail

class Cursor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.query = ""
        self.commands = []
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql, args):
        assert sql.lstrip().upper().startswith("SELECT "), "Only SELECT allowed"
        self.query = sql
        self.commands.append((sql, args))
    def fetchall(self):
        assert "FROM plan_catalog" in self.query
        return self.cfg['catalog']
    def fetchone(self):
        if "FROM stripe_price_map" in self.query:
            return {'matches': self.cfg['mapped']}
        assert "FROM billing_transactions" in self.query
        return {'pending': self.cfg['pending']}

class Connection:
    def __init__(self, cfg):
        self.cfg = cfg; self.readonly = False; self.rolled = False; self.closed = False
        self.cur = Cursor(cfg)
    def set_session(self, *, readonly, autocommit):
        assert readonly is True and autocommit is False
        self.readonly = True
    def cursor(self):
        assert self.readonly
        return self.cur
    def rollback(self): self.rolled = True
    def close(self): self.closed = True

class QuoteTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime.now(dt.timezone.utc)
        self.start = self.now - dt.timedelta(days=6)
        self.end = self.now + dt.timedelta(days=24)
        self.rail = {
            'provider':'stripe','plan_code':'seeker','support_mode':'monthly_recurring',
            'provider_status':'active','internal_status':'active',
            'cancel_at_period_end':False,'livemode':False,
            'stripe_subscription_id':'sub_synthetic','stripe_price_id':'price_synthetic',
            'current_period_start':self.start,'current_period_end':self.end,
        }
        self.ent = {'raw_plan_code':'seeker','effective_plan_code':'seeker',
                    'last_support_mode':'monthly_recurring','entitlement_status':'active',
                    'cancel_at_period_end':False,
                    'current_period_started_at':self.start,
                    'subscription_expires_at':self.end}
        self.config = {'catalog':[{'monthly_price_cents':199}], 'mapped':1,'pending':0}
        self.conns=[]; self.calls=[]
        class FakeOS:
            def __init__(self, obj): self.obj=obj
            def getenv(self, key, default=None): return self.obj.get(key,default)
        self.flags={'GODINC_STRIPE_REUP_PURCHASE_MODE':'enabled',
                    'GODINC_STRIPE_REUP_WEBHOOK_MODE':'verified_settlement'}
        def connect():
            self.calls.append('db'); c=Connection(self.config); self.conns.append(c); return c
        namespace={
            'os':FakeOS(self.flags),'STRIPE_SECRET_KEY':'synthetic',
            'STRIPE_WEBHOOK_SECRET':'synthetic',
            'Request':object,'HTTPException':HTTPError,'get_current_user':lambda request: {'user_id':'00000000-0000-4000-8000-000000000001'},
            'get_active_paid_rail_for_user':lambda user_id:self.rail,
            'get_user_entitlement_snapshot':lambda user_id:self.ent,
            'get_stripe_livemode':lambda:False,
            'PLAN_LIMITS':{'seeker':17},'plan_has_unlimited_questions':lambda plan:False,
            'get_oracle_usage_counts':lambda **kwargs: {'questions_used':17},
            'get_effective_usage_window_start':lambda ent:self.start,
            'get_db_connection':connect, 'serialize_dt':lambda value:value.isoformat(),
            'datetime':dt,
        }
        exec(load_quote(), namespace)
        self.func=namespace['billing_stripe_early_reup_quote']; self.globals=namespace
    def invoke(self): return self.func(object())
    def test_eligible_quote_contains_only_catalog_and_policy_values(self):
        out=self.invoke()
        self.assertTrue(out['eligible'])
        self.assertEqual((out['amount_cents'],out['new_period_question_limit'],out['currency']),(199,17,'usd'))
        self.assertTrue(out['requires_explicit_charge_confirmation'])
        self.assertFalse(out['payment_confirmed']); self.assertFalse(out['questions_replenished'])
        self.assertTrue(self.conns[0].readonly and self.conns[0].rolled and self.conns[0].closed)
        self.assertEqual(len(self.conns[0].cur.commands),3)
    def test_default_disabled_before_identity_or_db(self):
        self.flags.clear()
        self.globals['get_current_user']=lambda request:self.fail('Never authenticate disabled offer')
        with self.assertRaises(HTTPError) as e: self.invoke()
        self.assertEqual(e.exception.status_code,404)
        self.assertFalse(self.conns)
    def test_noncanonical_switches_fail_closed(self):
        self.flags['GODINC_STRIPE_REUP_PURCHASE_MODE']='true'
        with self.assertRaises(HTTPError) as e: self.invoke()
        self.assertEqual(e.exception.status_code,404)
        self.flags['GODINC_STRIPE_REUP_PURCHASE_MODE']='enabled'
        self.flags['GODINC_STRIPE_REUP_WEBHOOK_MODE']='true'
        with self.assertRaises(HTTPError) as e: self.invoke()
        self.assertEqual(e.exception.status_code,503)
    def test_provider_secrets_required(self):
        self.globals['STRIPE_SECRET_KEY']=''
        with self.assertRaises(HTTPError) as e: self.invoke()
        self.assertEqual(e.exception.status_code,503)
    def test_authentication_required(self):
        self.globals['get_current_user']=lambda request:None
        with self.assertRaises(HTTPError) as e: self.invoke()
        self.assertEqual(e.exception.status_code,401)
        self.assertFalse(self.conns)
    def test_native_or_cross_rail_never_quoted(self):
        self.rail['provider']='apple'
        self.assertEqual(self.invoke(),{'eligible':False})
        self.assertFalse(self.conns)
    def test_only_seeker_monthly_supported(self):
        self.rail['plan_code']='magister'
        self.assertFalse(self.invoke()['eligible'])
        self.rail['plan_code']='seeker';self.rail['support_mode']='annual_recurring'
        self.assertFalse(self.invoke()['eligible'])
    def test_cancelled_or_wrong_env_never_quoted(self):
        self.rail['cancel_at_period_end']=True
        self.assertFalse(self.invoke()['eligible'])
        self.rail['cancel_at_period_end']=False;self.rail['livemode']=True
        self.assertFalse(self.invoke()['eligible'])
    def test_stale_or_inconsistent_paid_window_rejected(self):
        self.ent['current_period_started_at']=self.start-dt.timedelta(days=1)
        self.assertFalse(self.invoke()['eligible'])
        self.ent['current_period_started_at']=self.start;self.ent['entitlement_status']='expired'
        self.assertFalse(self.invoke()['eligible'])
    def test_unexhausted_quota_never_quoted(self):
        self.globals['get_oracle_usage_counts']=lambda **kwargs:{'questions_used':16}
        self.assertFalse(self.invoke()['eligible'])
        self.assertFalse(self.conns)
    def test_unlimited_tier_not_quoted(self):
        self.globals['plan_has_unlimited_questions']=lambda plan:True
        self.assertFalse(self.invoke()['eligible'])
        self.assertFalse(self.conns)
    def test_missing_catalog_refuses(self):
        self.config['catalog']=[]
        self.assertFalse(self.invoke()['eligible'])
    def test_price_mapping_mismatch_refuses(self):
        self.config['mapped']=0
        self.assertFalse(self.invoke()['eligible'])
        self.config['mapped']=2
        self.assertFalse(self.invoke()['eligible'])
    def test_invalid_catalog_price_refuses(self):
        self.config['catalog']=[{'monthly_price_cents':0}]
        self.assertFalse(self.invoke()['eligible'])
        self.config['catalog']=[{'monthly_price_cents':'199'}]
        self.assertFalse(self.invoke()['eligible'])
    def test_pending_purchase_never_quoted(self):
        self.config['pending']=1
        self.assertFalse(self.invoke()['eligible'])
    def test_no_payment_method_provider_request_or_write_statement(self):
        source=ast.unparse(ast.parse(SOURCE.read_text(encoding='utf-8')))
        self.assertEqual(source.count('def billing_stripe_early_reup_quote('),1)
        statements=[s.lower() for s,_ in self.conns[0].cur.commands] if self.conns else []
        self.assertFalse(any('update ' in s or 'insert ' in s or 'delete ' in s for s in statements))

if __name__=='__main__':
    unittest.main(verbosity=2)
