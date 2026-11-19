import argparse
import os

from config import MODELS
from src.pipeline import run_stage1, run_stage2, run_stage3, run_stage4


def main():
    parser = argparse.ArgumentParser(description="Automated Semantic Annotation for CellML models")
    parser.add_argument("--model", help="Only process this model (CellML file name without extension, e.g. hodgkin_huxley)")
    parser.add_argument("--stage", choices=["1", "2", "3", "4", "all"], default="all",
                        help="Stage to run: 1 (CellML + paper extraction), 2 (LLM synonyms + contexts), "
                             "3 (biological process inference), 4 (ontology lookup with OLS), or all (default)")
    parser.add_argument("--skip-pdf", action="store_true", help="Stage 1: only extract CellML variables; do not call LlamaParse")
    args = parser.parse_args()

    models = MODELS
    if args.model:
        models = [m for m in MODELS if os.path.splitext(m["cellml"])[0] == args.model]
        if not models:
            names = ", ".join(os.path.splitext(m["cellml"])[0] for m in MODELS)
            parser.error(f"Unknown model '{args.model}'. Available: {names}")

    print("============================================================")
    print("AUTOMATED SEMANTIC ANNOTATION FOR CELLML MODELS")
    print("============================================================")

    for idx, model in enumerate(models):
        print(f"\nModel {idx + 1}/{len(models)}: {model['cellml']}")
        try:
            if args.stage in ("1", "all"):
                run_stage1(model, skip_pdf=args.skip_pdf)
            if args.stage in ("2", "all") and not args.skip_pdf:
                run_stage2(model)
            if args.stage in ("3", "all") and not args.skip_pdf:
                run_stage3(model)
            if args.stage in ("4", "all") and not args.skip_pdf:
                run_stage4(model)
        except Exception as e:
            print(f"ERROR processing {model['cellml']}: {e}")

    print("\nDone. Results are in the output/ folder.")


if __name__ == "__main__":
    main()
