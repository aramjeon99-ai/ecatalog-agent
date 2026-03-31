from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path

import pandas as pd

from ecatalog_agent.db.logger import init_db
from ecatalog_agent.models.state import NPRRecord
from ecatalog_agent.workflow.graph import run_agent_for_record
from ecatalog_agent.utils.text_normalize import normalize_maker


def _is_nan(v) -> bool:
    # pandas typically gives NaN as float('nan'), but dtype=str usually keeps NaN strings.
    if v is None:
        return True
    if isinstance(v, float):
        return pd.isna(v)
    if isinstance(v, str):
        vv = v.strip()
        return vv == "" or vv.lower() == "nan"
    return False


def load_manufacturer_names(path: str | Path) -> set[str]:
    p = Path(path)
    if not p.exists():
        return set()

    xls = pd.ExcelFile(str(p))
    names: set[str] = set()
    for sheet in xls.sheet_names:
        df = pd.read_excel(str(p), sheet_name=sheet, dtype=str)
        # Expect column "Manufacturer"
        cols = {str(c).strip().lower(): c for c in df.columns}
        mf_col = None
        for key in ["manufacturer", "제조사", "업체"]:
            if key in cols:
                mf_col = cols[key]
                break
        if mf_col is None:
            continue
        for v in df[mf_col].dropna().tolist():
            if _is_nan(v):
                continue
            names.add(normalize_maker(str(v)))
    return names


def records_from_system_excel(
    *,
    input_excel: str | Path,
    sheet: str,
    pdf_base_dir: str | Path,
    max_rows: int | None = None,
) -> list[NPRRecord]:
    input_excel = Path(input_excel)
    pdf_base_dir = Path(pdf_base_dir)
    df = pd.read_excel(str(input_excel), sheet_name=sheet, dtype=str)
    if max_rows is not None:
        df = df.head(max_rows)

    col_q = "Q-Code"
    col_specs_candidates = ["사양 및 규격", "사양/규격", "사양 및 규격.1"]
    specs_col = next((c for c in col_specs_candidates if c in df.columns), None)

    records: list[NPRRecord] = []
    for idx, row in df.iterrows():
        request_id = str(row.get(col_q, "")).strip() if col_q in df.columns else str(idx)
        if _is_nan(request_id):
            continue

        spec_raw = ""
        if specs_col and not _is_nan(row.get(specs_col)):
            spec_raw = str(row.get(specs_col))

        # Model-1..3 mapping
        for i in (1, 2, 3):
            model_col = f"Model-{i}"
            maker_col = f"Maker-{i}"
            pdf_file_col = f"Model-{i} 첨부파일명"
            homepage_col = f"Model-{i} 첨부URL1"

            if model_col not in df.columns or maker_col not in df.columns:
                continue

            model_name = row.get(model_col)
            maker_name = row.get(maker_col)
            if _is_nan(model_name) or _is_nan(maker_name):
                continue

            pdf_filename = row.get(pdf_file_col) if pdf_file_col in df.columns else None
            pdf_path = None
            if pdf_filename is not None and not _is_nan(pdf_filename):
                pdf_path = str(pdf_base_dir / str(pdf_filename).strip())

            homepage_url = row.get(homepage_col) if homepage_col in df.columns else None
            if _is_nan(homepage_url):
                homepage_url = None

            records.append(
                NPRRecord(
                    row_index=int(idx),
                    request_id=str(request_id),
                    model_name=str(model_name).strip(),
                    maker_name=str(maker_name).strip(),
                    specifications={"spec_raw": spec_raw} if spec_raw else {},
                    pdf_path=pdf_path,
                    homepage_url=homepage_url,
                    is_foreign=None,
                )
            )
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-excel", required=True, help="Input excel path")
    parser.add_argument("--sheet", default=None, help="Sheet name to process (optional)")
    parser.add_argument(
        "--sheet-auto",
        action="store_true",
        help="Automatically choose a sheet that looks like NPR request data",
    )
    parser.add_argument("--pdf-base-dir", required=True, help="Base dir for PDF files")
    parser.add_argument("--output-dir", default="output", help="Output directory")
    parser.add_argument("--db-path", default="ecatalog_agent/db/ecatalog_agent.sqlite3", help="SQLite db path")
    parser.add_argument("--max-rows", type=int, default=None, help="Limit number of excel rows")

    args = parser.parse_args()

    input_excel = Path(args.input_excel)
    output_dir = Path(args.output_dir)
    db_path = Path(args.db_path)

    output_dir.mkdir(parents=True, exist_ok=True)

    # init db
    init_db(db_path)

    # Load manufacturer list
    maker_list_path = input_excel.parent / "industrial_manufacturers_list.xlsx"
    manufacturer_names = load_manufacturer_names(maker_list_path)

    chosen_sheet = args.sheet
    if args.sheet_auto or not chosen_sheet:
        xls = pd.ExcelFile(str(input_excel))
        wanted = {"Q-Code", "Model-1", "Maker-1"}
        for s in xls.sheet_names:
            df_head = pd.read_excel(str(input_excel), sheet_name=s, nrows=5, dtype=str)
            if wanted.issubset(set(df_head.columns)):
                chosen_sheet = s
                break

    if not chosen_sheet:
        raise SystemExit("Could not choose a sheet automatically. Please pass --sheet explicitly.")

    records = records_from_system_excel(
        input_excel=input_excel,
        sheet=chosen_sheet,
        pdf_base_dir=args.pdf_base_dir,
        max_rows=args.max_rows,
    )

    if not records:
        print("No records generated. Check sheet/columns mapping.")
        return

    for rec in records:
        state = run_agent_for_record(
            rec,
            db_path=db_path,
            output_dir=output_dir,
            manufacturer_names=manufacturer_names,
        )
        print(
            f"{rec.request_id} | {rec.model_name} | {state.final_decision.outcome} | errors={[e.code for e in state.error_flags]}"
        )


if __name__ == "__main__":
    main()

