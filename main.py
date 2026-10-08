#!/usr/bin/env python3
"""CLI:  python main.py --input ./resumes --output ./output/results.json"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from screener.config import Settings          # noqa: E402
from screener.pipeline import run              # noqa: E402
from screener.report import terminal_report, write_csv, write_json   # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AI Resume Screening & Ranking")
    ap.add_argument("--input", required=True, help="folder containing resumes (pdf/docx/txt)")
    ap.add_argument("--output", default="output/results.json")
    ap.add_argument("--config", help="optional JSON file overriding any Settings field")
    ap.add_argument("--no-github", action="store_true", help="skip GitHub enrichment (offline run)")
    ap.add_argument("--llm", choices=["none", "anthropic", "openai"], help="override LLM_PROVIDER")
    ap.add_argument("--top", type=int, default=10, help="rows in terminal report")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    settings = Settings.load(args.config)
    if args.llm:
        import dataclasses as dc
        settings = dc.replace(settings, llm=dc.replace(settings.llm, provider=args.llm))

    result = run(args.input, settings, use_github=not args.no_github)
    out = Path(args.output)
    write_json(result, out)
    write_csv(result, out.with_suffix(".csv"))
    print(terminal_report(result, args.top))
    print(f"\nWrote {out} and {out.with_suffix('.csv')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
