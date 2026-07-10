"""S5 — pattern analysis. Pure aggregation over out/results.json; every number
on the case-study page comes from here, none are hand-written."""
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"

SELF_SERVE = {"self_serve_free", "self_serve_paid_trial"}
GATED = {"admin_approval", "partner_gated"}


def main():
    results = [r for r in json.loads((OUT / "results.json").read_text()) if r.get("status") == "ok"]
    categories = sorted({r["category"] for r in results})

    auth = Counter()
    auth_combo = Counter()
    for r in results:
        methods = r["auth_methods"]["value"]
        auth.update(methods)
        auth_combo["+".join(sorted(methods))] += 1

    access = Counter(r["credential_access"]["value"] for r in results)
    access_by_cat = {c: Counter() for c in categories}
    build_by_cat = {c: Counter() for c in categories}
    for r in results:
        access_by_cat[r["category"]][r["credential_access"]["value"]] += 1
        build_by_cat[r["category"]][r["buildability"]["verdict"]] += 1

    blockers = Counter(
        r["buildability"]["main_blocker"] for r in results if r["buildability"]["verdict"] == "blocked"
    )
    build = Counter(r["buildability"]["verdict"] for r in results)

    composio_covered = [r["slug"] for r in results if (r.get("composio_toolkit") or {}).get("exists")]
    mcp_exists = [r["slug"] for r in results if (r.get("mcp_server") or {}).get("exists")]
    mcp_official = [r["slug"] for r in results if (r.get("mcp_server") or {}).get("official")]

    easy_wins = sorted(
        r["slug"] for r in results
        if r["buildability"]["verdict"] in ("buildable_today", "buildable_with_key_signup")
        and r["credential_access"]["value"] in SELF_SERVE
        and not (r.get("composio_toolkit") or {}).get("exists")
    )
    needs_outreach = sorted(r["slug"] for r in results if r["credential_access"]["value"] == "partner_gated")
    insufficient = sorted(r["slug"] for r in results if (r.get("research_meta") or {}).get("insufficient_public_docs"))

    n = len(results)
    patterns = {
        "n_apps": n,
        "headline": {
            "pct_oauth2": round(100 * auth["oauth2"] / n),
            "pct_api_key": round(100 * auth["api_key"] / n),
            "pct_self_serve": round(100 * sum(access[a] for a in SELF_SERVE) / n),
            "pct_gated": round(100 * sum(access[a] for a in GATED) / n),
            "pct_buildable_now": round(100 * (build["buildable_today"] + build["buildable_with_key_signup"]) / n),
            "top_blocker": blockers.most_common(1)[0] if blockers else None,
            "composio_already_covers": len(composio_covered),
            "existing_mcp_servers": len(mcp_exists),
            "easy_wins_count": len(easy_wins),
        },
        "auth_distribution": dict(auth.most_common()),
        "auth_combos": dict(auth_combo.most_common(10)),
        "credential_access": dict(access.most_common()),
        "credential_access_by_category": {c: dict(access_by_cat[c]) for c in categories},
        "buildability": dict(build.most_common()),
        "buildability_by_category": {c: dict(build_by_cat[c]) for c in categories},
        "blocker_taxonomy": dict(blockers.most_common()),
        "composio_covered": sorted(composio_covered),
        "mcp_servers": {"any": sorted(mcp_exists), "vendor_official": sorted(mcp_official)},
        "easy_wins": easy_wins,
        "needs_outreach": needs_outreach,
        "insufficient_public_docs": insufficient,
    }
    (OUT / "patterns.json").write_text(json.dumps(patterns, indent=2))
    print(json.dumps(patterns["headline"], indent=2))


if __name__ == "__main__":
    main()
