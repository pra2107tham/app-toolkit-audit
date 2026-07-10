"""S4 — eval layer. Three independent checks feed out/accuracy.json:

e1 deterministic : schema re-validation, evidence-URL liveness, field coverage
e2 LLM judge     : third model family scores field<->evidence faithfulness on a
                   stratified sample (2 apps per category)
e3 human         : emits a spot-check CSV; ingests the filled copy

Accuracy is measured per field, pass-1 vs post-fix, from verifier verdicts —
never self-reported by the researcher.
"""
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import requests
from dotenv import load_dotenv

from .models import RESEARCHED_FIELDS, AppRecord, JudgeReport

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
UA = {"User-Agent": "Mozilla/5.0 (research-pipeline eval)"}


def load_results():
    return json.loads((OUT / "results.json").read_text())


def sample_slugs(results, per_category=2):
    by_cat = defaultdict(list)
    for r in results:
        if r.get("status") == "ok":
            by_cat[r["category"]].append(r["slug"])
    return sorted(s for cat in sorted(by_cat) for s in sorted(by_cat[cat])[:per_category])


# ---------- e1: deterministic ----------

def evidence_urls(record):
    urls = []
    for f in RESEARCHED_FIELDS:
        claim = record.get(f)
        if isinstance(claim, dict):
            for key in ("evidence_url", "docs_url"):
                u = claim.get(key)
                if u and u.startswith("http"):
                    urls.append(u)
    return urls


def run_deterministic(results):
    cache_file = OUT / "url_liveness.json"
    cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    schema_ok, coverage_missing, all_urls = 0, [], set()
    for r in results:
        if r.get("status") != "ok":
            continue
        try:
            AppRecord.model_validate({k: r[k] for k in AppRecord.model_fields if k in r})
            schema_ok += 1
        except Exception:
            coverage_missing.append({"slug": r["slug"], "problem": "schema_invalid"})
        insufficient = (r.get("research_meta") or {}).get("insufficient_public_docs")
        for f in RESEARCHED_FIELDS:
            claim = r.get(f)
            if isinstance(claim, dict) and not claim.get("evidence_url") and not insufficient and f != "buildability":
                coverage_missing.append({"slug": r["slug"], "problem": f"no_evidence:{f}"})
        all_urls.update(evidence_urls(r))
    for u in sorted(all_urls):
        if u in cache:
            continue
        try:
            resp = requests.get(u, timeout=15, headers=UA, allow_redirects=True, stream=True)
            cache[u] = resp.status_code
            resp.close()
        except Exception:
            cache[u] = 0
    cache_file.write_text(json.dumps(cache, indent=2))
    live = sum(1 for u in all_urls if 200 <= cache.get(u, 0) < 400)
    blocked = sum(1 for u in all_urls if cache.get(u, 0) in (401, 403, 405, 429))
    report = {
        "records_ok": sum(1 for r in results if r.get("status") == "ok"),
        "schema_valid": schema_ok,
        "evidence_urls_total": len(all_urls),
        "evidence_urls_live": live,
        "evidence_urls_bot_blocked": blocked,
        "evidence_urls_dead": len(all_urls) - live - blocked,
        "coverage_problems": coverage_missing,
    }
    (OUT / "deterministic_checks.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "coverage_problems"}, indent=2))
    print(f"coverage problems: {len(coverage_missing)}")


# ---------- e2: LLM judge ----------

def run_judge(results, concurrency=4):
    import concurrent.futures

    from . import llms
    from .crews.judge import make_judge_crew
    from .webcache import evidence_dump

    (OUT / "judge").mkdir(exist_ok=True)
    slugs = set(sample_slugs(results))
    todo = [r for r in results if r["slug"] in slugs and not (OUT / "judge" / f"{r['slug']}.json").exists()]
    llm = llms.judge()
    homepages = {a["slug"]: a["homepage"] for a in json.loads((ROOT / "data" / "apps.json").read_text())}

    def worker(r):
        try:
            crew = make_judge_crew(llm)
            record_json = json.dumps({k: r[k] for k in ["slug", "name", *RESEARCHED_FIELDS, "research_meta"] if k in r}, indent=2)
            result = crew.kickoff(inputs={
                "name": r["name"], "slug": r["slug"], "record_json": record_json,
                "evidence_dump": evidence_dump(r, homepages.get(r["slug"], "")),
            })
            rep = getattr(result, "pydantic", None)
            rep = JudgeReport.model_validate(rep.model_dump()) if rep else JudgeReport.model_validate_json(result.raw)
            rep.slug = r["slug"]
            (OUT / "judge" / f"{r['slug']}.json").write_text(rep.model_dump_json(indent=2))
            return "ok"
        except Exception as e:
            print(f"judge failed {r['slug']}: {e}")
            return "failed"

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        statuses = list(pool.map(worker, todo))
    print(f"judge: {statuses.count('ok')} ok / {len(todo)} run / {len(slugs)} sampled")


# ---------- e3: human spot-check ----------

def emit_csv(results):
    slugs = set(sample_slugs(results))
    rows = []
    for r in results:
        if r["slug"] not in slugs:
            continue
        for f in RESEARCHED_FIELDS:
            claim = r.get(f)
            value = json.dumps(claim.get("value", claim.get("verdict")) if isinstance(claim, dict) else claim)
            evidence = claim.get("evidence_url", claim.get("docs_url", "")) if isinstance(claim, dict) else ""
            rows.append({"slug": r["slug"], "field": f, "value": value, "evidence_url": evidence,
                         "human_agrees (y/n)": "", "correction": ""})
    path = OUT / "human_check_sample.csv"
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {path} ({len(rows)} rows across {len(slugs)} apps) — fill 'human_agrees' and re-run with --ingest-csv")


def ingest_csv(path):
    with open(path, newline="") as fh:
        rows = [row for row in csv.DictReader(fh)]
    graded = [r for r in rows if r["human_agrees (y/n)"].strip().lower() in ("y", "n")]
    agree = sum(1 for r in graded if r["human_agrees (y/n)"].strip().lower() == "y")
    by_field = defaultdict(lambda: {"agree": 0, "total": 0})
    for r in graded:
        by_field[r["field"]]["total"] += 1
        by_field[r["field"]]["agree"] += r["human_agrees (y/n)"].strip().lower() == "y"
    report = {
        "graded": len(graded), "agree": agree,
        "agreement_rate": round(agree / len(graded), 3) if graded else None,
        "by_field": {k: dict(v) for k, v in by_field.items()},
        "disagreements": [
            {"slug": r["slug"], "field": r["field"], "value": r["value"], "correction": r["correction"]}
            for r in graded if r["human_agrees (y/n)"].strip().lower() == "n"
        ],
    }
    (OUT / "human_check.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report[k] for k in ("graded", "agree", "agreement_rate")}, indent=2))


# ---------- accuracy roll-up ----------

def verdict_counts(verdicts):
    c = defaultdict(int)
    for v in verdicts or []:
        c[v["verdict"]] += 1
    return c


def run_accuracy(results):
    per_field = {f: {"pass1": defaultdict(int), "final": defaultdict(int)} for f in RESEARCHED_FIELDS}
    for r in results:
        ver = r.get("verification") or {}
        pass1 = {v["field"]: v["verdict"] for v in ver.get("pass1") or []}
        post = {v["field"]: v["verdict"] for v in ver.get("post_fix") or []}
        for f in RESEARCHED_FIELDS:
            if f in pass1:
                per_field[f]["pass1"][pass1[f]] += 1
                # final verdict: post-fix grade if the field was re-verified, else the pass-1 grade
                per_field[f]["final"][post.get(f, pass1[f])] += 1

    def rate(counts):
        total = sum(counts.values())
        return round(counts.get("confirmed", 0) / total, 3) if total else None

    summary = {
        f: {
            "pass1_accuracy": rate(per_field[f]["pass1"]),
            "final_accuracy": rate(per_field[f]["final"]),
            "pass1_verdicts": dict(per_field[f]["pass1"]),
            "final_verdicts": dict(per_field[f]["final"]),
        }
        for f in RESEARCHED_FIELDS
    }
    all_p1 = defaultdict(int)
    all_fin = defaultdict(int)
    for f in RESEARCHED_FIELDS:
        for k, v in per_field[f]["pass1"].items():
            all_p1[k] += v
        for k, v in per_field[f]["final"].items():
            all_fin[k] += v
    judge_scores = []
    for p in (OUT / "judge").glob("*.json") if (OUT / "judge").exists() else []:
        judge_scores.extend(json.loads(p.read_text())["scores"])
    human_file = OUT / "human_check.json"
    accuracy = {
        "overall": {"pass1_accuracy": rate(all_p1), "final_accuracy": rate(all_fin),
                    "pass1_verdicts": dict(all_p1), "final_verdicts": dict(all_fin)},
        "per_field": summary,
        "fixed_apps": len(list((OUT / "fixes").glob("*.json"))) if (OUT / "fixes").exists() else 0,
        "judge_sample": {
            "fields_scored": len(judge_scores),
            "faithful_rate": round(sum(s["faithful"] for s in judge_scores) / len(judge_scores), 3) if judge_scores else None,
        },
        "human_check": json.loads(human_file.read_text()) if human_file.exists() else None,
    }
    (OUT / "accuracy.json").write_text(json.dumps(accuracy, indent=2))
    print(json.dumps(accuracy["overall"], indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deterministic", action="store_true")
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--emit-csv", action="store_true")
    ap.add_argument("--ingest-csv", type=str)
    ap.add_argument("--accuracy", action="store_true")
    args = ap.parse_args()
    results = load_results()
    if args.deterministic:
        run_deterministic(results)
    if args.judge:
        run_judge(results)
    if args.emit_csv:
        emit_csv(results)
    if args.ingest_csv:
        ingest_csv(args.ingest_csv)
    if args.accuracy:
        run_accuracy(results)


if __name__ == "__main__":
    main()
