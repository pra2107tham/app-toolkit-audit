"""Pipeline orchestrator.

Stages (each resumable — a valid per-app JSON in out/ is never recomputed):
  s0        deterministic enrichment (src/enrich.py)
  s1        pass-1 research crew per app        -> out/pass1/{slug}.json
  s2        skeptic verification per app        -> out/verification/{slug}.json
  s3        fix pass on flagged fields + merge  -> out/final/{slug}.json (+ out/fixes/)
  reverify  second verification of FIXED apps   -> out/verification2/{slug}.json
  results   merge everything                    -> out/results.json

Run:  uv run python -m src.flow --stage s1 --concurrency 6 [--limit 5] [--slugs a,b]
"""
import argparse
import concurrent.futures
import json
import time
import traceback
from pathlib import Path

from dotenv import load_dotenv

from . import llms
from .crews.fix import make_fix_crew
from .crews.research import make_research_crew
from .crews.verify import make_verify_crew
from .models import AppRecord, VerificationReport
from .tools import ANALYST_TOOLS, FIXER_TOOLS, SCOUT_TOOLS, get_tools, with_fallback
from .webcache import evidence_dump

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
LOGS = ROOT / "logs"

FLAG_VERDICTS = {"contradicted", "unsupported", "dead_url"}


def load_apps(limit=None, slugs=None):
    apps = json.loads((ROOT / "data" / "apps.json").read_text())
    if slugs:
        wanted = set(slugs)
        apps = [a for a in apps if a["slug"] in wanted]
    return apps[:limit] if limit else apps


def log_event(**kv):
    LOGS.mkdir(exist_ok=True)
    with open(LOGS / "run.log.jsonl", "a") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **kv}) + "\n")


def extract(result, model_cls):
    obj = getattr(result, "pydantic", None)
    if obj is not None:
        return model_cls.model_validate(obj.model_dump())
    return model_cls.model_validate_json(result.raw)


def run_pool(items, worker, concurrency):
    counts = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        for status in pool.map(worker, items):
            counts[status] = counts.get(status, 0) + 1
    return counts


def prefacts_for(slug: str) -> str:
    f = OUT / "enrichment" / f"{slug}.json"
    if not f.exists():
        return "none available"
    e = json.loads(f.read_text())
    ct, mcp = e.get("composio_toolkit", {}), e.get("mcp_server", {})
    return (
        f"Composio already has a toolkit for this app: {ct.get('exists')}"
        f" (slug={ct.get('slug')}, tools={ct.get('tools_count')}, auth_schemes={ct.get('auth_schemes')}). "
        f"An MCP server exists in the official registry: {mcp.get('exists')} (vendor-official: {mcp.get('official')}). "
        f"Homepage reachable: {e.get('homepage_alive')}."
    )


# ---------- S1: pass-1 research ----------

def stage_s1(apps, concurrency):
    (OUT / "pass1").mkdir(parents=True, exist_ok=True)
    llm = llms.openai_cheap()
    scout_tools = get_tools(SCOUT_TOOLS)
    analyst_tools = with_fallback(get_tools(ANALYST_TOOLS))

    def worker(app):
        out_file = OUT / "pass1" / f"{app['slug']}.json"
        if out_file.exists():
            return "cached"
        inputs = {
            "name": app["name"], "slug": app["slug"], "category": app["category"],
            "homepage": app["homepage"], "hint": app["hint"], "prefacts": prefacts_for(app["slug"]),
        }
        t0, last_err = time.time(), None
        for attempt in (1, 2):
            try:
                crew = make_research_crew(llm, scout_tools, analyst_tools)
                rec = extract(crew.kickoff(inputs=inputs), AppRecord)
                rec.slug, rec.name, rec.category = app["slug"], app["name"], app["category"]
                out_file.write_text(rec.model_dump_json(indent=2))
                log_event(stage="s1", slug=app["slug"], status="ok", attempt=attempt, secs=round(time.time() - t0))
                return "ok"
            except Exception as e:
                last_err = f"{e}\n{traceback.format_exc(limit=3)}"
                time.sleep(5 * attempt)
        (LOGS / "failed").mkdir(parents=True, exist_ok=True)
        (LOGS / "failed" / f"s1_{app['slug']}.txt").write_text(str(last_err))
        log_event(stage="s1", slug=app["slug"], status="failed")
        return "failed"

    print("s1:", run_pool(apps, worker, concurrency))


# ---------- S2: verification ----------

def stage_s2(apps, concurrency, src_dir="pass1", dst_dir="verification"):
    (OUT / dst_dir).mkdir(parents=True, exist_ok=True)
    llm = llms.verifier()

    def worker(app):
        src = OUT / src_dir / f"{app['slug']}.json"
        dst = OUT / dst_dir / f"{app['slug']}.json"
        if not src.exists():
            return "missing_input"
        if dst.exists():
            return "cached"
        record = json.loads(src.read_text())
        inputs = {
            "name": app["name"], "slug": app["slug"], "homepage": app["homepage"],
            "record_json": src.read_text(),
            "evidence_dump": evidence_dump(record, app["homepage"]),
        }
        last_err = None
        for attempt in (1, 2):
            try:
                crew = make_verify_crew(llm)
                rep = extract(crew.kickoff(inputs=inputs), VerificationReport)
                rep.slug = app["slug"]
                dst.write_text(rep.model_dump_json(indent=2))
                log_event(stage=dst_dir, slug=app["slug"], status="ok", attempt=attempt)
                return "ok"
            except Exception as e:
                last_err = f"{e}\n{traceback.format_exc(limit=3)}"
                time.sleep(5 * attempt)
        (LOGS / "failed").mkdir(parents=True, exist_ok=True)
        (LOGS / "failed" / f"{dst_dir}_{app['slug']}.txt").write_text(str(last_err))
        return "failed"

    print(f"{dst_dir}:", run_pool(apps, worker, concurrency))


# ---------- S3: fix pass + merge ----------

def flagged_fields(slug: str) -> list[dict]:
    vf = OUT / "verification" / f"{slug}.json"
    if not vf.exists():
        return []
    rep = json.loads(vf.read_text())
    return [v for v in rep["verdicts"] if v["verdict"] in FLAG_VERDICTS]


def stage_s3(apps, concurrency):
    (OUT / "final").mkdir(parents=True, exist_ok=True)
    (OUT / "fixes").mkdir(parents=True, exist_ok=True)
    llm = llms.openai_smart()
    tools = with_fallback(get_tools(FIXER_TOOLS))

    def worker(app):
        slug = app["slug"]
        src = OUT / "pass1" / f"{slug}.json"
        dst = OUT / "final" / f"{slug}.json"
        if not src.exists():
            return "missing_input"
        if dst.exists():
            return "cached"
        record = json.loads(src.read_text())
        flags = flagged_fields(slug)
        if not flags:
            dst.write_text(json.dumps(record, indent=2))
            return "clean"
        disputes = "\n".join(
            f"- {v['field']}: {v['verdict']} — {v['reasoning']}"
            + (f" | suggested correction: {v['corrected_value']}" if v.get("corrected_value") else "")
            + (f" | better source: {v['better_evidence_url']}" if v.get("better_evidence_url") else "")
            for v in flags
        )
        inputs = {
            "name": app["name"], "slug": slug, "category": app["category"], "homepage": app["homepage"],
            "record_json": json.dumps(record, indent=2), "disputes": disputes,
        }
        last_err = None
        for attempt in (1, 2):
            try:
                crew = make_fix_crew(llm, tools)
                fixed = extract(crew.kickoff(inputs=inputs), AppRecord).model_dump()
                merged, diff = dict(record), {}
                for v in flags:
                    fld = v["field"]
                    if fld in fixed:
                        diff[fld] = {"pass1_value": record.get(fld), "final_value": fixed[fld], "verdict": v["verdict"]}
                        merged[fld] = fixed[fld]
                merged.setdefault("research_meta", {})["pass"] = 2
                dst.write_text(json.dumps(merged, indent=2))
                (OUT / "fixes" / f"{slug}.json").write_text(json.dumps(diff, indent=2))
                log_event(stage="s3", slug=slug, status="fixed", fields=list(diff))
                return "fixed"
            except Exception as e:
                last_err = f"{e}\n{traceback.format_exc(limit=3)}"
                time.sleep(5 * attempt)
        (LOGS / "failed").mkdir(parents=True, exist_ok=True)
        (LOGS / "failed" / f"s3_{slug}.txt").write_text(str(last_err))
        return "failed"

    print("s3:", run_pool(apps, worker, concurrency))


def stage_reverify(apps, concurrency):
    """Second verification, but only for apps the fix pass actually changed."""
    fixed_slugs = {p.stem for p in (OUT / "fixes").glob("*.json") if json.loads(p.read_text())}
    apps = [a for a in apps if a["slug"] in fixed_slugs]
    print(f"reverify: {len(apps)} fixed apps")
    stage_s2(apps, concurrency, src_dir="final", dst_dir="verification2")


# ---------- results merge ----------

def stage_results():
    apps = load_apps()
    results = []
    for app in apps:
        slug = app["slug"]
        rec_file = OUT / "final" / f"{slug}.json"
        if not rec_file.exists():
            rec_file = OUT / "pass1" / f"{slug}.json"
        if not rec_file.exists():
            results.append({"slug": slug, "name": app["name"], "category": app["category"], "status": "missing"})
            continue
        rec = json.loads(rec_file.read_text())
        enr_file = OUT / "enrichment" / f"{slug}.json"
        enr = json.loads(enr_file.read_text()) if enr_file.exists() else {}
        ver_file = OUT / "verification" / f"{slug}.json"
        ver2_file = OUT / "verification2" / f"{slug}.json"
        fix_file = OUT / "fixes" / f"{slug}.json"
        rec["composio_toolkit"] = enr.get("composio_toolkit", {})
        rec["mcp_server"] = enr.get("mcp_server", {})
        rec["verification"] = {
            "pass1": json.loads(ver_file.read_text())["verdicts"] if ver_file.exists() else None,
            "post_fix": json.loads(ver2_file.read_text())["verdicts"] if ver2_file.exists() else None,
            "fixed_fields": list(json.loads(fix_file.read_text())) if fix_file.exists() else [],
        }
        rec["status"] = "ok"
        results.append(rec)
    OUT.mkdir(exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(results, indent=2))
    ok = sum(1 for r in results if r.get("status") == "ok")
    print(f"results.json: {ok}/{len(results)} apps complete")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True, choices=["s0", "s1", "s2", "s3", "reverify", "results", "all"])
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--slugs", type=str, help="comma-separated slugs")
    args = ap.parse_args()
    slugs = args.slugs.split(",") if args.slugs else None
    apps = load_apps(args.limit, slugs)

    if args.stage in ("s0", "all"):
        from . import enrich
        enrich.main()
    if args.stage in ("s1", "all"):
        stage_s1(apps, args.concurrency)
    if args.stage in ("s2", "all"):
        stage_s2(apps, args.concurrency)
    if args.stage in ("s3", "all"):
        stage_s3(apps, args.concurrency)
    if args.stage in ("reverify", "all"):
        stage_reverify(apps, args.concurrency)
    if args.stage in ("results", "all"):
        stage_results()


if __name__ == "__main__":
    main()
