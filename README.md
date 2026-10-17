# UADP – Universal AI Data Platform (v0.1 core prototype)

Python core plus a browser app. Rule: **AI recommends; deterministic code computes; an independent engine verifies; the user confirms.**

## Run
```
pip install -e .[dev]
python -m pytest -q          # 31 tests
python -m uadp.demo          # synthetic sales + PG-vs-bank run, verified, evidence.zip
uadp profile file.csv --auth "Authorized by X on 2026-10-17"
uadp ask sales.csv --auth Synthetic --kind group_total --amount gross_amount --group category
uadp recon pg.csv bank.csv --auth Synthetic --lk payout_utr --la net_amount --ld settlement_date --rk bank_ref --ra credit --rd value_date --evidence out.zip
```
`web/index.html` is the standalone browser app (no build step, no network calls). Open it directly, or host the folder on any static host (GitHub Pages, Netlify, Cloudflare Pages).

## What is implemented (PRD features)
F-01 upload with authorization note and SHA-256 · F-02 encoding, delimiter, header row, totals row · F-03 profile and warnings · F-04 role confirmation · F-05 typed plans (total, group total, monthly) · F-08 explicit plan/result · F-12/F-13 strategy, detect-then-confirm, tolerance and date window · F-14 buckets with tie-out · F-17 certification (verified / unverified / refused) · F-18 evidence zip with manifest hashes · allocation ledger with split settlements and over-allocation rejection · independent reference engine (separate file reader, Fraction maths, import-lint test) · hash-chained audit log.

## Not yet implemented
AI model gateway and spend cap · DuckDB worker sandbox and two-parser SQL guard · XLSX input · Streamlit UI · replay, delete-data, benchmark dashboard, metric versioning · custom strategies from plain English and saved templates · gold-case set (200) and mutation tests · GST, vendor and balance-mode strategies · hosting, auth, multi-tenant isolation.

## Before real customer data (gate RG-4)
Use synthetic data only until you have an independent code review, a DPDP Act review, tested backup/restore/deletion and an incident runbook. This is not legal advice.

## Licence
Choose one before publishing (MIT or Apache-2.0 are common). No licence file means others may not legally reuse the code.
# uadp
