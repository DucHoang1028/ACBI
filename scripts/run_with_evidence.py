# ruff: noqa: E501
"""One run of the RAG, hard and compare suites that keeps the evidence for every case:
each question, the full answer, rows, chart, SQL and the warehouse queries used to check it.

    python scripts/run_with_evidence.py BASE OUT.json [rag|hard|multi ...]   (ONLY=ID,ID limits the cases)
A case that only failed because the AI was busy is marked "excluded" (not retried)."""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import verify_compare_multitask as M
import verify_hard_cases as H
import verify_rag_cases as R

base, out = sys.argv[1].rstrip("/"), Path(sys.argv[2])
ONLY = set(filter(None, os.environ.get("ONLY", "").split(",")))
suites = sys.argv[3:] or ["rag", "hard", "multi"]

EVENTS: list[dict] = []
results: list[dict] = []
original_call = M.call
original_sql = H.sql


def call(b, path, body, token=None):
    result = original_call(b, path, body, token)
    if path.endswith("/chat/ask"):
        src = result.get("sources") or {}
        EVENTS.append(
            {
                "kind": "ask",
                "question": body.get("question"),
                "status": result.get("status"),
                "message": result.get("message"),
                "answer_text": result.get("answer_text"),
                "rows": len(result.get("table") or []),
                "first_rows": (result.get("table") or [])[:3],
                "chart": (result.get("viz_config") or {}).get("type"),
                "query_path": result.get("query_path"),
                "llm_calls": result.get("llm_calls"),
                "sql": (src.get("sql") or "")[:1800],
                "parameters": src.get("parameters"),
                "parts": [
                    {
                        "title": p.get("title"),
                        "status": p.get("status"),
                        "text": (p.get("answer_text") or p.get("message") or "")[:300],
                        "rows": len(p.get("table") or []),
                    }
                    for p in result.get("parts") or []
                ],
            }
        )
    return result


def sql(query, *params):
    rows = original_sql(query, *params)
    EVENTS.append(
        {
            "kind": "warehouse",
            "query": " ".join(query.split())[:400],
            "params": [str(p) for p in params],
            "result": None if rows is None else [[str(v) for v in r] for r in rows[:4]],
            "rows_total": None if rows is None else len(rows),
        }
    )
    return rows


original_truth = M.truth_revenue


def truth_revenue(territory, start, end):
    value = original_truth(territory, start, end)
    EVENTS.append(
        {
            "kind": "warehouse",
            "query": "SELECT SUM(h.subtotal) FROM sales.salesorderheader h JOIN sales.salesterritory t ON t.territoryid=h.territoryid WHERE h.orderdate>=%s AND h.orderdate<%s"
            + (" AND t.name=%s" if territory else ""),
            "params": [start, end] + ([territory] if territory else []),
            "result": None if value is None else [[f"{value:.4f}"]],
            "rows_total": None if value is None else 1,
        }
    )
    return value


M.truth_revenue = truth_revenue
M.call = H.call = R.call = call
H.sql = R.sql = sql


def busy(events):
    return any(
        e["kind"] == "ask"
        and e["status"] == "technical_failure"
        and "busy" in (e.get("message") or "").lower()
        for e in events
    )


def spec_of(t):
    if isinstance(t, dict):
        return {k: v for k, v in t.items() if k != "q" and not callable(v)}
    return {k: v for k, v in (t[1] or {}).items() if not callable(v)}


def run(suite_name, cases, runner, question_of, role_of):
    out_rows = results
    for item in cases:
        if ONLY and item["id"] not in ONLY:
            continue
        EVENTS.clear()
        started = time.time()
        errors = runner(item)
        events = list(EVENTS)
        verdict = "PASS" if not errors else ("EXCLUDED" if busy(events) else "FAIL")
        out_rows.append(
            {
                "suite": suite_name,
                "id": item["id"],
                "role": role_of(item),
                "lang": item.get("lang", "vi"),
                "turns": (
                    [
                        {"question": question_of(t), "spec": spec_of(t)}
                        for t in item.get("turns", [item])
                    ]
                    if suite_name != "rag"
                    else [
                        {
                            "question": item["q"],
                            "spec": {"status": item.get("status", "ok")},
                        }
                    ]
                ),
                "has_final_check": bool(item.get("final")),
                "events": events,
                "errors": errors,
                "verdict": verdict,
                "seconds": round(time.time() - started, 1),
            }
        )
        print(verdict, item["id"], flush=True)
        out.write_text(
            json.dumps(out_rows, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        time.sleep(3)
    return out_rows


if "rag" in suites:

    def rag_runner(item):
        errors, _ = R.run_case(base, item)
        return errors

    run(
        "rag",
        R.CASES,
        rag_runner,
        lambda t: t["q"] if isinstance(t, dict) and "q" in t else t,
        lambda i: i.get("role", "manager"),
    )
if "hard" in suites:
    run(
        "hard",
        H.CASES,
        lambda i: H.run_case(base, i),
        lambda t: t["q"],
        lambda i: i["user"],
    )
if "multi" in suites:
    run(
        "multi",
        M.CASES,
        lambda i: M.run_case(base, i),
        lambda t: t[0],
        lambda i: i["user"],
    )
out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
print("done", len(results))
