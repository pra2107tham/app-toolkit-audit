"""S6 — render docs/index.html from out/*.json. Every number on the page is
injected from pipeline artifacts; nothing is hand-written. Template tokens use
@@NAME@@ markers so CSS/JS braces stay untouched."""
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
DOCS = ROOT / "docs"

ACCESS_LABEL = {
    "self_serve_free": "self-serve · free",
    "self_serve_paid_trial": "self-serve · paid/trial",
    "admin_approval": "admin approval",
    "partner_gated": "partner gated",
    "unclear": "unclear",
}
BUILD_LABEL = {
    "buildable_today": "buildable today",
    "buildable_with_key_signup": "buildable · key signup",
    "blocked": "blocked",
}
BLOCKER_LABEL = {
    "none": "none", "partner_gate": "partner gate", "no_public_api": "no public API",
    "insufficient_docs": "insufficient docs", "enterprise_only": "enterprise only",
    "paid_only": "paid only", "other": "other",
}
FIELDS = ["description", "auth_methods", "credential_access", "api_surface", "buildability"]


def bar_rows(counter: dict, labels: dict | None = None, cls_map: dict | None = None) -> str:
    total = sum(counter.values()) or 1
    mx = max(counter.values()) if counter else 1
    rows = []
    for key, n in counter.items():
        label = (labels or {}).get(key, key)
        cls = (cls_map or {}).get(key, "")
        rows.append(
            f'<div class="bar-row"><span class="bar-label">{label}</span>'
            f'<span class="bar-track"><span class="bar-fill {cls}" style="width:{100 * n / mx:.0f}%"></span></span>'
            f'<span class="bar-num mono">{n}<span class="bar-pct">/{total}</span></span></div>'
        )
    return "\n".join(rows)


def matrix_table(by_cat: dict) -> str:
    cols = ["self_serve_free", "self_serve_paid_trial", "admin_approval", "partner_gated", "unclear"]
    col_cls = {"self_serve_free": "ok", "self_serve_paid_trial": "ok2", "admin_approval": "warn",
               "partner_gated": "bad", "unclear": "mut"}
    head = "".join(f'<th class="mono">{ACCESS_LABEL[c].replace(" · ", "<br>")}</th>' for c in cols)
    body = []
    for cat, counts in by_cat.items():
        cells = []
        for c in cols:
            n = counts.get(c, 0)
            cells.append(f'<td class="cell {col_cls[c] if n else "zero"}">{n if n else "·"}</td>')
        short = cat.split(" and ")[0].split(",")[0]
        body.append(f'<tr><th class="rowh">{short}</th>{"".join(cells)}</tr>')
    return f'<table class="matrix"><thead><tr><th></th>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def accuracy_rows(acc: dict) -> str:
    rows = []
    for f in FIELDS:
        d = acc["per_field"].get(f, {})
        p1, fin = d.get("pass1_accuracy"), d.get("final_accuracy")
        if p1 is None:
            continue
        rows.append(
            f'<div class="acc-row"><span class="bar-label mono">{f}</span>'
            f'<span class="acc-track"><span class="acc-p1" style="left:{p1 * 100:.1f}%"></span>'
            f'<span class="acc-span" style="left:{p1 * 100:.1f}%;width:{max(0.0, (fin - p1)) * 100:.1f}%"></span>'
            f'<span class="acc-fin" style="left:{fin * 100:.1f}%"></span></span>'
            f'<span class="bar-num mono">{p1 * 100:.0f}→<b>{fin * 100:.0f}%</b></span></div>'
        )
    return "\n".join(rows)


def miss_items(results: list) -> str:
    items = []
    for r in results:
        ver = r.get("verification") or {}
        fixed = ver.get("fixed_fields") or []
        pass1 = {v["field"]: v for v in ver.get("pass1") or []}
        for f in fixed:
            v = pass1.get(f, {})
            if not v:
                continue
            items.append(
                f'<li><span class="stamp bad">{v.get("verdict", "flagged")}</span> '
                f'<b>{r["name"]}</b> · <span class="mono">{f}</span> — {v.get("reasoning", "")[:220]}</li>'
            )
    return "\n".join(items[:12]) or "<li>No fields needed fixing.</li>"


def render() -> None:
    results = json.loads((OUT / "results.json").read_text())
    patterns = json.loads((OUT / "patterns.json").read_text())
    acc_file = OUT / "accuracy.json"
    accuracy = json.loads(acc_file.read_text()) if acc_file.exists() else None
    h = patterns["headline"]
    ok = [r for r in results if r.get("status") == "ok"]

    template = (ROOT / "src" / "site_template.html").read_text()
    n = patterns["n_apps"]
    top_blocker = h.get("top_blocker") or ["—", 0]
    reps = {
        "N_APPS": str(n),
        "PCT_BUILDABLE": str(h["pct_buildable_now"]),
        "N_BUILDABLE": str(round(h["pct_buildable_now"] * n / 100)),
        "PCT_SELF_SERVE": str(h["pct_self_serve"]),
        "PCT_GATED": str(h["pct_gated"]),
        "PCT_OAUTH2": str(h["pct_oauth2"]),
        "PCT_API_KEY": str(h["pct_api_key"]),
        "TOP_BLOCKER": BLOCKER_LABEL.get(top_blocker[0], top_blocker[0]),
        "TOP_BLOCKER_N": str(top_blocker[1]),
        "N_COMPOSIO": str(h["composio_already_covers"]),
        "N_MCP": str(h["existing_mcp_servers"]),
        "N_EASY_WINS": str(h["easy_wins_count"]),
        "EASY_WINS": ", ".join(patterns["easy_wins"]) or "—",
        "NEEDS_OUTREACH": ", ".join(patterns["needs_outreach"]) or "—",
        "INSUFFICIENT": ", ".join(patterns["insufficient_public_docs"]) or "none",
        "AUTH_BARS": bar_rows(patterns["auth_distribution"],
                              cls_map={"oauth2": "ok", "api_key": "ok2", "basic": "warn", "token": "warn",
                                       "jwt": "warn", "other": "mut", "none_public": "bad"}),
        "ACCESS_BARS": bar_rows(patterns["credential_access"], ACCESS_LABEL,
                                {"self_serve_free": "ok", "self_serve_paid_trial": "ok2",
                                 "admin_approval": "warn", "partner_gated": "bad", "unclear": "mut"}),
        "BLOCKER_BARS": bar_rows(patterns["blocker_taxonomy"], BLOCKER_LABEL,
                                 {k: "bad" for k in patterns["blocker_taxonomy"]}),
        "MATRIX": matrix_table(patterns["credential_access_by_category"]),
        "RESULTS_JSON": json.dumps(results, separators=(",", ":")),
        "N_OK": str(len(ok)),
    }
    if accuracy:
        ov = accuracy["overall"]
        p1v = ov.get("pass1_verdicts", {})
        graded = sum(p1v.values())
        flagged = sum(v for k, v in p1v.items() if k != "confirmed")
        judge = accuracy.get("judge_sample") or {}
        human = accuracy.get("human_check") or {}
        reps.update({
            "ACC_PASS1": f"{(ov['pass1_accuracy'] or 0) * 100:.0f}",
            "ACC_FINAL": f"{(ov['final_accuracy'] or 0) * 100:.0f}",
            "ACC_ROWS": accuracy_rows(accuracy),
            "N_GRADED": str(graded),
            "N_FLAGGED": str(flagged),
            "N_FIXED_APPS": str(accuracy.get("fixed_apps", 0)),
            "JUDGE_RATE": f"{(judge.get('faithful_rate') or 0) * 100:.0f}",
            "JUDGE_N": str(judge.get("fields_scored", 0)),
            "HUMAN_RATE": f"{(human.get('agreement_rate') or 0) * 100:.0f}" if human else "—",
            "HUMAN_N": str(human.get("graded", 0)) if human else "0",
            "MISSES": miss_items(results),
        })
    else:
        reps.update({k: "—" for k in ("ACC_PASS1", "ACC_FINAL", "JUDGE_RATE", "HUMAN_RATE")})
        reps.update({"ACC_ROWS": "", "MISSES": "", "N_GRADED": "0", "N_FLAGGED": "0",
                     "N_FIXED_APPS": "0", "JUDGE_N": "0", "HUMAN_N": "0"})

    html = template
    for k, v in reps.items():
        html = html.replace(f"@@{k}@@", v)
    DOCS.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(html)
    shutil.copy(OUT / "results.json", DOCS / "results.json")
    print(f"docs/index.html rendered ({len(html) // 1024} KB), results.json copied")


if __name__ == "__main__":
    render()
