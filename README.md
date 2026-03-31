# e-Catalog Specific Part Auto Approval Agent (MVP)

This repo contains an MVP implementation of the spec in `ecatalog_agent_spec.md`.

## Run (local)

```bash
python -m ecatalog_agent.main --input-excel "260327_system_data_list.xlsx" --sheet "공압실린더 56개 " --pdf-base-dir "3_attatched-file" --output-dir "output"
```

시트 이름(공백/인코딩 포함)을 잘 모르면 아래처럼 자동 선택합니다.

```bash
python -m ecatalog_agent.main --input-excel "260327_system_data_list.xlsx" --sheet-auto --pdf-base-dir "3_attatched-file" --output-dir "output" --max-rows 2
```

## Notes

- External integrations (credit API, POS-Appia, e-Catalog update, Slack/Email) are left as stubs for now.
- PDF text extraction uses PyMuPDF (`fitz`). OCR is not enabled in MVP.
