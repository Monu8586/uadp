import ast, json, zipfile
from decimal import Decimal
from pathlib import Path
import pytest
from uadp import reference
from uadp.analysis import AnalysisPlan, PlanError, run
from uadp.audit import AuditLog
from uadp.evidence import build_evidence, verify_evidence
from uadp.ingest import IngestError, confirm, read_table
from uadp.money import format_inr, parse_amount
from uadp.recon import Ledger, OverAllocation, Row, Strategy, detect_strategy, preselect, reconcile
from uadp.synthetic import write_sample
from uadp.verify import verify_analysis, verify_recon


@pytest.fixture()
def files(tmp_path):
    return write_sample(tmp_path)


def load(p):
    t = read_table(p, "Synthetic")
    confirm(t)
    return t


ST = Strategy("pg_vs_bank", "payout_utr", "net_amount", "settlement_date", "bank_ref", "credit", "value_date", Decimal(0), 2)


# ---- money
@pytest.mark.parametrize("s,v", [("₹1,23,456.50", "123456.50"), ("(500)", "-500"), ("500-", "-500"), ("-12.5", "-12.5"), ("Rs. 7", "7"), ("INR 1,000", "1000")])
def test_parse_amount(s, v):
    assert parse_amount(s) == Decimal(v)


@pytest.mark.parametrize("s", ["", "abc", "1E+5", "1.2.3", None])
def test_parse_amount_rejects(s):
    assert parse_amount(s) is None


def test_indian_format():
    assert format_inr(Decimal("4832150")) == "₹48,32,150.00"
    assert format_inr(Decimal("-999.5")) == "-₹999.50"


# ---- ingest
def test_upload_needs_authorization(files):
    with pytest.raises(IngestError) as e:
        read_table(files["pg.csv"], " ")
    assert e.value.code == "INGEST_NO_AUTHORIZATION"


def test_unsupported_and_empty(tmp_path):
    (tmp_path / "a.xlsx").write_bytes(b"x")
    with pytest.raises(IngestError):
        read_table(tmp_path / "a.xlsx", "Synthetic")
    (tmp_path / "e.csv").write_text("a,b\n")
    with pytest.raises(IngestError) as e:
        read_table(tmp_path / "e.csv", "Synthetic")
    assert e.value.code == "INGEST_EMPTY"


def test_encodings_delimiters_header_and_totals(tmp_path):
    body = "Bank export\nGenerated today\nref;amount\nA1;10.50\nA2;4.50\nTotal;15.00\n"
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        p = tmp_path / f"{enc}.csv"
        p.write_bytes(body.encode(enc))
        t = read_table(p, "Synthetic")
        assert t.headers == ["ref", "amount"] and t.delimiter == ";"
        assert any(w["code"] == "TOTALS_ROW" for w in t.warnings)
        assert len(t.rows) == 3                      # totals row kept until the user confirms exclusion
        assert len(read_table(p, "Synthetic", exclude_totals=True).rows) == 2


def test_profile_matches_truth(files):
    t = load(files["pg.csv"])
    amt = next(c for c in t.columns if c.name == "net_amount")
    assert amt.type == "number" and amt.role == "amount"
    assert amt.total == sum((Decimal(10000 + i * 137) + Decimal("0.25") for i in range(14)), Decimal(0))
    assert t.sha256 == __import__("hashlib").sha256(files["pg.csv"].read_bytes()).hexdigest()


# ---- analysis
def test_analysis_verified(files):
    t = load(files["sales.csv"])
    res = run(AnalysisPlan("group_total", "gross_amount", "category", 10), t)
    cert = verify_analysis(res, t)
    assert cert.status == "verified" and len(res.rows) == 4
    assert res.total == sum(v for _, v in res.rows)


def test_monthly_and_total(files):
    t = load(files["sales.csv"])
    assert verify_analysis(run(AnalysisPlan("monthly", "gross_amount", "order_date"), t), t).status == "verified"
    assert verify_analysis(run(AnalysisPlan("total", "gross_amount"), t), t).status == "verified"


def test_plan_errors(files):
    t = read_table(files["sales.csv"], "Synthetic")
    with pytest.raises(PlanError):
        run(AnalysisPlan("total", "gross_amount"), t)          # not confirmed
    confirm(t)
    with pytest.raises(PlanError):
        run(AnalysisPlan("total", "nope"), t)
    with pytest.raises(PlanError):
        run(AnalysisPlan("total", "customer"), t)              # not a number column
    with pytest.raises(PlanError):
        run(AnalysisPlan("forecast", "gross_amount"), t)


def test_tampered_file_is_caught(files):
    t = load(files["sales.csv"])
    res = run(AnalysisPlan("total", "gross_amount"), t)
    Path(files["sales.csv"]).write_text(Path(files["sales.csv"]).read_text().replace("O1001", "O1001", 1) + 'O9999,2026-04-01,C01,Home,"1,000.00"\n')
    assert verify_analysis(res, t).status == "refused"          # source total no longer matches


# ---- reconciliation
def test_recon_buckets_and_tieout(files):
    lt, rt = load(files["pg.csv"]), load(files["bank.csv"])
    res = reconcile(lt, rt, ST)
    L, R = res.tieout("L"), res.tieout("R")
    assert {b: v[0] for b, v in L.items()} == {"matched": 9, "matched_amount_date": 1, "matched_diff": 1, "split": 1, "ambiguous": 1, "unmatched": 1}
    assert {b: v[0] for b, v in R.items()} == {"matched": 9, "matched_amount_date": 1, "matched_diff": 1, "split": 2, "ambiguous": 2, "unmatched": 1}
    assert sum(v[1] for v in L.values()) == res.total("L") and sum(v[1] for v in R.values()) == res.total("R")
    cert = verify_recon(res, lt, rt)
    assert cert.status == "verified", [c for c in cert.checks if not c.ok]


def test_split_settlement_allocates_exactly(files):
    lt, rt = load(files["pg.csv"]), load(files["bank.csv"])
    res = reconcile(lt, rt, ST)
    split_left = next(i for (s, i), b in res.bucket.items() if s == "L" and b == "split")
    allocs = [a for a in res.allocs if a.left == split_left]
    assert len(allocs) == 2 and sum(a.amount for a in allocs) == res.left[split_left].amount


def test_ambiguous_is_never_matched(files):
    lt, rt = load(files["pg.csv"]), load(files["bank.csv"])
    res = reconcile(lt, rt, ST)
    amb = [i for (s, i), b in res.bucket.items() if s == "L" and b == "ambiguous"]
    assert amb and not [a for a in res.allocs if a.left in amb]


def test_ledger_rejects_over_allocation_and_sign_flip():
    l, r = Row(0, "K", Decimal(100), None), Row(0, "K", Decimal(100), None)
    led = Ledger([l], [r])
    led.allocate(0, l, r, Decimal(60))
    with pytest.raises(OverAllocation):
        led.allocate(1, l, r, Decimal(60))
    with pytest.raises(OverAllocation):
        Ledger([l], [Row(0, "K", Decimal(-100), None)]).allocate(0, l, Row(0, "K", Decimal(-100), None), Decimal(10))


def test_tolerance_changes_only_difference_row(files):
    lt, rt = load(files["pg.csv"]), load(files["bank.csv"])
    tight = reconcile(lt, rt, ST)
    loose = reconcile(lt, rt, Strategy(**{**ST.__dict__, "tolerance": Decimal(200)}))
    changed = [k for k in tight.bucket if tight.bucket[k] != loose.bucket[k]]
    assert changed and all(tight.bucket[k] == "matched_diff" for k in changed)


def test_detect_then_confirm(files):
    lt, rt = load(files["pg.csv"]), load(files["bank.csv"])
    ranked = detect_strategy(lt, rt)
    assert ranked[0][0] == "pg_vs_bank" and preselect(ranked) == "pg_vs_bank"
    assert preselect([("a", 0.5, ""), ("b", 0.45, "")]) is None


# ---- architecture rules
def test_reference_engine_is_independent():
    src = ast.parse(Path(reference.__file__).read_text())
    imported = {n.module for n in ast.walk(src) if isinstance(n, ast.ImportFrom) and n.module} | {a.name for n in ast.walk(src) if isinstance(n, ast.Import) for a in n.names}
    assert not {m for m in imported if m.startswith(("uadp", ".")) or m in {"decimal", "pandas", "duckdb"}}
    assert not [n for n in ast.walk(src) if isinstance(n, ast.ImportFrom) and n.level > 0]


def test_no_floats_for_money():
    for f in ("money.py", "analysis.py", "recon.py", "verify.py"):
        tree = ast.parse((Path(__file__).parents[1] / "uadp" / f).read_text())
        assert not [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "float"], f


def test_audit_chain_detects_tampering():
    log = AuditLog()
    for i in range(3):
        log.add("e", n=i)
    assert log.verify()
    log.events[1]["detail"]["n"] = 99
    assert not log.verify()


def test_evidence_manifest(tmp_path):
    out = tmp_path / "e.zip"
    build_evidence(out, {"result.json": "{}", "checks.json": "[]"})
    assert verify_evidence(out)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    assert "README.txt" in names and "manifest.json" in names
    tampered = tmp_path / "t.zip"
    with zipfile.ZipFile(out) as z, zipfile.ZipFile(tampered, "w") as w:
        for n in z.namelist():
            w.writestr(n, b"x" if n == "result.json" else z.read(n))
    assert not verify_evidence(tampered)


def test_cli_demo(tmp_path, capsys):
    from uadp.cli import main
    main(["demo", "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert "Status: VERIFIED" in out and "Audit chain valid: True" in out and (tmp_path / "evidence.zip").exists()
