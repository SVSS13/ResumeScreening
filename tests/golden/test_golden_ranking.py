import json
from pathlib import Path
import pytest

from screener.config import Settings
from screener.pipeline import run

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = REPO_ROOT / "tests" / "golden" / "results.json"
RESUMES_DIR = REPO_ROOT / "resumes"


def test_golden_ranking_identical():
    """Verify that running the pipeline produces deterministic ranking output.

    IMPORTANT AUDIT NOTE: This golden test compares ONLY deterministic domain fields
    (`ranked_candidates` and `rejected_candidates`) with GitHub and LLM disabled.
    It intentionally excludes non-deterministic execution metadata such as
    `summary.runtime_seconds` and system timestamps to ensure honest, reproducible assertions.
    """
    assert GOLDEN_PATH.exists(), f"Golden file not found at {GOLDEN_PATH}"
    assert RESUMES_DIR.exists(), f"Resumes directory not found at {RESUMES_DIR}"

    golden_data = json.loads(GOLDEN_PATH.read_text())
    expected_ranked = golden_data["ranked_candidates"]
    expected_rejected = golden_data["rejected_candidates"]

    # Run pipeline with default settings, GitHub disabled, LLM disabled
    settings = Settings()
    current_result = run(RESUMES_DIR, settings, use_github=False)
    actual_ranked = current_result["ranked_candidates"]
    actual_rejected = current_result["rejected_candidates"]

    # Verify counts
    assert len(actual_ranked) == len(expected_ranked), (
        f"Ranked count mismatch: {len(actual_ranked)} != {len(expected_ranked)}"
    )
    assert len(actual_rejected) == len(expected_rejected), (
        f"Rejected count mismatch: {len(actual_rejected)} != {len(expected_rejected)}"
    )

    # Verify candidates, scores, and rank order
    for idx, (act, exp) in enumerate(zip(actual_ranked, expected_ranked)):
        assert act["rank"] == exp["rank"], f"Candidate {idx} rank mismatch: {act['rank']} != {exp['rank']}"
        assert act["file"] == exp["file"], f"Candidate {idx} file mismatch: {act['file']} != {exp['file']}"
        assert act["candidate_name"] == exp["candidate_name"], (
            f"Candidate {idx} name mismatch: {act['candidate_name']} != {exp['candidate_name']}"
        )
        assert act["total_score"] == pytest.approx(exp["total_score"], abs=1e-4), (
            f"Candidate {idx} total score mismatch: {act['total_score']} != {exp['total_score']}"
        )
        assert act["eligible"] == exp["eligible"]
        # Score breakdown match
        for key in ["ai_project_depth", "python_backend", "cloud_fullstack", "github", "engineering_depth"]:
            assert act["score_breakdown"][key] == pytest.approx(exp["score_breakdown"][key], abs=1e-4), (
                f"Candidate {idx} breakdown {key} mismatch: {act['score_breakdown'][key]} != {exp['score_breakdown'][key]}"
            )

    # Verify rejected candidate schema and reasons
    for idx, (act, exp) in enumerate(zip(actual_rejected, expected_rejected)):
        assert act["file"] == exp["file"], f"Rejected candidate {idx} file mismatch"
        assert act["eligible"] == exp["eligible"]
        assert act.get("rejection_reasons") == exp.get("rejection_reasons"), (
            f"Rejected candidate {idx} reason mismatch: {act.get('rejection_reasons')} != {exp.get('rejection_reasons')}"
        )
