"""Output writers: JSON (primary), CSV (flat), and a compact terminal report."""
from __future__ import annotations

import csv
import json
from pathlib import Path


def write_json(result: dict, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False))


def write_csv(result: dict, path: str | Path) -> None:
    cols = ["rank", "candidate_name", "file", "eligible", "total_score", "ai_project_depth", "python_backend",
            "cloud_fullstack", "github", "engineering_depth", "penalties", "github_status", "matched_skills",
            "rejection_reasons"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for c in result["ranked_candidates"] + result["rejected_candidates"]:
            bd = c.get("score_breakdown") or {}
            w.writerow({
                "rank": c.get("rank"), "candidate_name": c["candidate_name"], "file": c["file"],
                "eligible": c["eligible"], "total_score": c.get("total_score"),
                **{k: bd.get(k) for k in ("ai_project_depth", "python_backend", "cloud_fullstack", "github",
                                          "engineering_depth")},
                "penalties": "; ".join(f"-{p['points']:g} {p['reason']}" for p in c.get("penalties", [])),
                "github_status": (c.get("github") or {}).get("status"),
                "matched_skills": ", ".join(c.get("matched_skills", [])),
                "rejection_reasons": "; ".join(c.get("rejection_reasons", [])),
            })


def terminal_report(result: dict, top: int = 10) -> str:
    s = result["summary"]
    lines = [
        f"Batch: {s['total_files']} files | parsed {s['parsed_ok']} | eligible {s['eligible']} | "
        f"rejected {s['rejected']} | failed {s['failed_unreadable']} | duplicates {s['duplicates_skipped']} "
        f"| {s['runtime_seconds']}s",
        f"GitHub ok/failed: {s['github_ok']}/{s['github_failed']} | LLM ok/failed: {s['llm_ok']}/{s['llm_failed']}",
        "", f"{'#':>3} {'Candidate':<24}{'Total':>6}  {'AI':>5}{'Py':>5}{'Cloud':>6}{'GH':>5}{'Eng':>4}  Pen",
    ]
    for c in result["ranked_candidates"][:top]:
        b = c["score_breakdown"]
        pen = sum(p["points"] for p in c["penalties"])
        lines.append(f"{c['rank']:>3} {c['candidate_name'][:23]:<24}{c['total_score']:>6}  "
                     f"{b['ai_project_depth']:>5.1f}{b['python_backend']:>5.1f}{b['cloud_fullstack']:>6.1f}"
                     f"{b['github']:>5.1f}{b['engineering_depth']:>4.0f}  {('-%g' % pen) if pen else ''}")
    for f in result["failed_files"]:
        lines.append(f"  [failed] {f['file']}: {f['reason']}")
    return "\n".join(lines)
