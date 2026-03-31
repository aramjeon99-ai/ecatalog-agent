# e-Catalog 특정사양품 자동 승인 Agent — 구현 명세서

> **문서 목적** : 이 파일은 코드 에이전트(Code Agent)가 e-Catalog 자동 승인 Agent를 구현할 때 필요한 모든 컨텍스트를 단일 파일로 제공합니다.  
> 플로우차트 기반 처리 흐름, 기능 목록, 세부 요건, 입출력 계약, 에러 처리 규칙, 구현 기술 스택이 모두 포함되어 있습니다.

---

## 목차

1. [프로젝트 개요](#1-프로젝트-개요)
2. [아키텍처 및 기술 스택](#2-아키텍처-및-기술-스택)
3. [데이터 모델 및 상태 계약](#3-데이터-모델-및-상태-계약)
4. [플로우차트 처리 흐름 (Step-by-Step)](#4-플로우차트-처리-흐름)
5. [기능 목록 및 구현 요건 (12개 기능 / 36개 세부 요건)](#5-기능-목록-및-구현-요건)
6. [반려 코드 정의](#6-반려-코드-정의)
7. [에러 처리 및 재시도 정책](#7-에러-처리-및-재시도-정책)
8. [외부 도구(Tool) 인터페이스 명세](#8-외부-도구tool-인터페이스-명세)
9. [검토표 출력 스키마](#9-검토표-출력-스키마)
10. [감사 로그 스키마](#10-감사-로그-스키마)
11. [구현 순서 및 우선순위](#11-구현-순서-및-우선순위)
12. [코드 구조 가이드](#12-코드-구조-가이드)

---

## 1. 프로젝트 개요

### 목표

e-Catalog 시스템 내 **특정사양품 NPR(New Part Request) 신청**을 자동으로 검토하여 사람의 개입 없이 **승인 / 자동 회송 / 보류(사람 확인)** 세 가지 결과를 도출하는 AI Agent를 구현한다.

### 핵심 처리 원칙

- **엑셀 행 단위 순차 처리** : NPR 신청 데이터는 엑셀 파일의 각 행에 기재되며, 행 순서대로 처리 후 다음 행으로 루프백한다.
- **플래그 누적 방식** : Step 0~5에서 발생한 모든 오류·반려 플래그는 누적되고, Step 6에서 일괄 판단한다.
- **에이전트 자율 도구 호출** : LLM이 상황에 따라 웹 검색, PDF 파서, 크롤러, 외부 API를 자율적으로 선택·호출한다.
- **룰 베이스 최종 판단** : 최종 승인·반려 결정은 반드시 결정 룰 트리에 따르며, LLM 단독 판단으로 승인하지 않는다.
- **전 단계 감사 로그** : 모든 처리 결과와 LLM 판단 근거를 DB에 영구 저장한다.

### 처리 결과 3종

| 결과 | 조건 | 후속 처리 |
|---|---|---|
| **자동 승인** | Step 0~5 전체 통과 + LLM 신뢰도 충족 | 상태값 `APPROVED`로 업데이트 |
| **자동 회송** | 반려 플래그 1개 이상 존재 | 사유 코드 매핑 후 `REJECTED` 처리 |
| **보류** | 플래그 없음 but LLM 신뢰도 낮은 항목 존재 | 담당자 대기열 이관 + 알림 발송 |

---

## 2. 아키텍처 및 기술 스택

### 에이전트 프레임워크

```
LangGraph (권장) 또는 CrewAI
- 각 Step을 그래프 노드로 정의
- Step 간 상태(AgentState)를 엣지로 전달
- 조건부 엣지로 분기 처리
```

### 기술 스택 매핑

| 영역 | 라이브러리 / 도구 | 용도 |
|---|---|---|
| 에이전트 오케스트레이션 | LangGraph / CrewAI | Step 간 워크플로우 제어 |
| LLM | GPT-4o / Claude 3.5 Sonnet | 문서 이해, 판단, 요약 |
| Vision LLM | GPT-4o Vision / Claude 3.5 Sonnet | 로고 감지, 이미지 PDF 분석 |
| 엑셀 파싱 | pandas, openpyxl | NPR 입력 데이터 읽기 |
| PDF 파싱 (텍스트) | pdfplumber, PyMuPDF | 텍스트 기반 PDF 처리 |
| PDF 파싱 (이미지) | Tesseract OCR, pdf2image | 스캔 이미지 PDF OCR |
| 문자열 매칭 | rapidfuzz | 모델명·메이커명 유사도 비교 |
| 웹 검색 | Tavily API / SerpAPI | 제조사 여부 확인 |
| 웹 크롤링 | playwright / BeautifulSoup | 홈페이지 Footer 크롤링 |
| 외부 API | 신용평가사 REST API | 사업자번호 업태 조회 |
| 검색 API | POS-Appia Smart API | 동일품 중복 검증 |
| 검토표 생성 | openpyxl, ReportLab | Excel/PDF 검토표 출력 |
| DB 로그 | PostgreSQL / SQLite | 감사 로그 영구 저장 |
| 알림 | slack_sdk, smtplib | Slack / 이메일 알림 |

---

## 3. 데이터 모델 및 상태 계약

### 3-1. NPR 입력 레코드 (엑셀 1행)

```python
class NPRRecord(BaseModel):
    row_index: int                  # 엑셀 행 번호
    request_id: str                 # NPR 신청 번호 (고유 키)
    model_name: str                 # 모델명 (시스템 입력값)
    maker_name: str                 # 메이커명 (시스템 입력값)
    specifications: dict[str, str]  # 사양값 {항목명: 값} (시스템 입력값)
    pdf_path: str | None            # 첨부 PDF 파일 경로
    homepage_url: str | None        # 제조사 홈페이지 URL (있으면)
    is_foreign: bool | None         # 외국계 기업 여부 (있으면)
```

### 3-2. 에이전트 상태 객체 (Step 간 공유)

```python
class AgentState(TypedDict):
    # 입력
    record: NPRRecord

    # Step별 처리 결과
    step0_result: StepResult        # 입력값 유효성
    step1_result: StepResult        # 모델명·메이커 대조
    step2_result: StepResult        # 신뢰성 평가
    step3_result: StepResult        # 사양값 대조
    step4_result: StepResult        # 제조사 여부
    step5_result: StepResult        # 동일품 검증

    # 누적 플래그
    error_flags: list[ErrorFlag]    # 반려 플래그 목록

    # 최종 판단
    final_decision: FinalDecision   # APPROVED / REJECTED / PENDING

    # 검토표
    review_report: ReviewReport
```

### 3-3. 공통 StepResult

```python
class StepResult(BaseModel):
    step_name: str
    status: Literal["PASS", "FAIL", "SKIP", "ERROR"]
    confidence: float               # LLM 판단 신뢰도 0.0~1.0
    details: dict                   # Step별 세부 결과
    flags_raised: list[ErrorFlag]   # 이 Step에서 발생한 플래그
    processing_time_ms: int
    llm_prompt: str | None          # 감사용 — LLM에 전달한 프롬프트 원문
    llm_response: str | None        # 감사용 — LLM 응답 원문
```

### 3-4. ErrorFlag (반려 플래그)

```python
class ErrorFlag(BaseModel):
    code: str          # 반려 코드 (섹션 6 참조)
    step: str          # 발생 Step
    message: str       # 사람이 읽을 수 있는 설명
    evidence: str      # 근거 텍스트 또는 수치
```

### 3-5. FinalDecision

```python
class FinalDecision(BaseModel):
    outcome: Literal["APPROVED", "REJECTED", "PENDING"]
    rejection_codes: list[str]      # REJECTED인 경우 반려 코드 목록
    rejection_summary: str          # LLM이 생성한 반려 사유 요약문
    low_confidence_items: list[str] # PENDING인 경우 신뢰도 낮은 항목
    decided_at: datetime
```

---

## 4. 플로우차트 처리 흐름

> 플로우차트 원본: `ecatalog_agent_flowchart.drawio`  
> 아래는 플로우차트의 모든 분기를 코드 에이전트가 직접 구현할 수 있도록 의사코드(pseudo-code) 수준으로 기술한다.

### 전체 루프 구조

```
엑셀 파일 열기
FOR each row IN excel_rows:
    record = parse_row(row)
    state  = init_state(record)

    state = step0_intake(state)
    IF state has critical flag → skip to step6

    state = step1_pdf_parse_and_match(state)
    state = step2_reliability_check(state)
    state = step3_spec_comparison(state)
    state = step4_manufacturer_verify(state)
    state = step5_duplicate_check(state)
    state = step6_final_decision(state)

    save_audit_log(state)
    generate_review_report(state)
    send_notification_if_needed(state)

END FOR
```

---

### STEP 0 — NPR 신청 접수

**목적** : 엑셀 파일에서 행을 읽어 필수 항목 및 첨부 PDF 존재 여부를 확인한다.

```
INPUT  : 엑셀 파일 경로, 현재 처리 행 번호
OUTPUT : NPRRecord (파싱 완료) + StepResult

[시작]
  ↓
엑셀 파일 읽기 (pandas.read_excel, 행 순차)
  ↓
판단: 필수 입력항목 누락 여부?
  ├─ YES (누락 있음) → 플래그: ERR_MISSING_FIELD
  │                   → step0_result.status = FAIL
  │                   → error_flags에 추가 후 STEP 6으로 이동
  └─ NO  (모두 존재)
          ↓
        판단: 첨부 PDF 파일 경로 존재 및 파일 확인됨?
          ├─ NO  → 플래그: ERR_NO_PDF
          │       → STEP 6으로 이동
          └─ YES → step0_result.status = PASS → [커넥터 A] → STEP 1
```

**필수 입력항목 목록** : `request_id`, `model_name`, `maker_name`, `specifications` (최소 1개 이상), `pdf_path`

---

### STEP 1 — PDF 파싱 & 입력값 대조

**목적** : 첨부 PDF에서 모델명·메이커명을 추출하고 시스템 입력값과 대조한다.

```
INPUT  : NPRRecord, pdf_path
OUTPUT : StepResult (model_match, maker_match, is_order_code)

[커넥터 A]
  ↓
PDF 파싱 실행
  - pdfplumber로 텍스트 추출 시도
  - 텍스트 추출 실패(이미지 PDF) → Tesseract OCR 적용
  ↓
판단: 모델명 일치?
  (정규식 매칭 + rapidfuzz 유사도 ≥ 0.90)
  ├─ YES → 다음 판단으로
  └─ NO  → 판단: 형번(Order Code) 체계 형식인가?
              ├─ YES → LLM 호출: "이 모델명이 해당 형번 체계에서 존재 가능한가?"
              │         ├─ 유효 → 계속 진행 (일치로 처리)
              │         └─ 무효 → 플래그: ERR_MODEL_MISMATCH
              └─ NO  → 플래그: ERR_MODEL_MISMATCH
  ↓
판단: 메이커명 일치?
  (alias 사전 조회 후 rapidfuzz 유사도 ≥ 0.85)
  ├─ YES → step1_result.status = PASS → [커넥터 B]
  └─ NO  → 플래그: ERR_MAKER_MISMATCH → step1_result.status = FAIL
```

**Order Code 판단 기준** : 모델명이 영문자+숫자+하이픈 조합이며 연속된 코드 세그먼트 패턴(`[A-Z]{1,4}-?\d{2,4}[A-Z]{0,3}`)일 때 형번 체계 의심으로 판단하고 LLM에 위임한다.

**LLM 프롬프트 템플릿 (형번 체계 검증)**:

```
당신은 산업용 부품 카탈로그 전문가입니다.
메이커: {maker_name}
모델명: {model_name}
PDF 원문 관련 텍스트: {extracted_text}

위 모델명이 해당 메이커의 형번(Order Code) 체계에서
실제로 존재 가능한 코드인지 판단하십시오.

응답 형식 (JSON만 출력):
{
  "is_valid_order_code": true | false,
  "confidence": 0.0~1.0,
  "reason": "판단 근거 1~2문장"
}
```

---

### STEP 2 — 검증자료 신뢰성 확인

**목적** : 첨부 자료가 신뢰할 수 있는 제조사 발행 문서인지 검증한다.

```
INPUT  : pdf_path, maker_name
OUTPUT : StepResult (doc_type, logo_detected, reliability_score)

[커넥터 B]
  ↓
문서 유형 분류 (LLM + 키워드 룰)
  - 키워드 룰: ["견적", "QUOTATION", "QUOTE", "발주", "PURCHASE ORDER", "PO"] 포함 시 즉시 견적서 판정
  - LLM 분류: 카탈로그 / 데이터시트 / 도면 / 명판사진 / 견적서 / 기타
  ↓
판단: 견적서 또는 발주서인가?
  ├─ YES → 플래그: ERR_QUOTE_DOCUMENT → FAIL
  └─ NO
      ↓
    Vision LLM: PDF 이미지 레이어에서 메이커 로고·상호명 존재 여부 감지
      ↓
    판단: 로고 또는 상호명 확인됨?
      ├─ NO → 플래그: ERR_NO_LOGO
      └─ YES → 계속
          ↓
        신뢰성 점수 산출 (0~100)
          - 로고 존재: +40
          - 문서 유형(카탈로그/데이터시트): +30
          - 발급 기관 명시: +20
          - 날짜/버전 정보: +10
          ↓
        판단: 신뢰성 점수 ≥ 임계값(70)?
          ├─ NO  → 플래그: ERR_LOW_RELIABILITY → FAIL
          └─ YES → step2_result.status = PASS → [커넥터 C]
```

**LLM 프롬프트 템플릿 (문서 유형 분류)**:

```
다음 PDF 문서의 유형을 분류하십시오.
메이커명: {maker_name}
PDF 텍스트 (처음 2000자): {pdf_text[:2000]}

응답 형식 (JSON만 출력):
{
  "doc_type": "CATALOG" | "DATASHEET" | "DRAWING" | "NAMEPLATE" | "QUOTATION" | "OTHER",
  "confidence": 0.0~1.0,
  "reason": "판단 근거"
}
```

---

### STEP 3 — 사양값 추출 및 대조

**목적** : PDF에서 사양 항목-값 쌍을 구조화하여 입력값과 수치 비교한다.

```
INPUT  : pdf_path, record.specifications
OUTPUT : StepResult (completeness, match_rate, unmatched_items)

[커넥터 C]
  ↓
OCR + LLM: PDF에서 사양 항목-값 구조화 추출
  - 출력 형식: {"항목명": {"value": "값", "unit": "단위"}, ...}
  ↓
완성도(Completeness) 검사
  - completeness = (PDF에서 확인된 항목 수) / (입력된 전체 사양 항목 수) × 100
  ↓
판단: Completeness = 100%?
  ├─ NO  → 플래그: ERR_INCOMPLETE_SPEC (누락 항목 목록 포함) → FAIL
  └─ YES
      ↓
    항목별 수치 1:1 매핑 비교
      - 수치 정규화: 단위 통일 (kPa↔MPa, kW↔W 등)
      - match_rate = (일치 항목 수) / (전체 항목 수) × 100
      ↓
    판단: match_rate ≥ 95%?
      ├─ NO  → 플래그: ERR_SPEC_MISMATCH (불일치 항목 목록 포함) → FAIL
      └─ YES → step3_result.status = PASS → [커넥터 D]
```

**LLM 프롬프트 템플릿 (사양 구조화 추출)**:

```
다음 PDF 문서에서 제품 사양 데이터를 추출하십시오.
추출 대상 항목: {list(record.specifications.keys())}

PDF 전체 텍스트:
{pdf_full_text}

응답 형식 (JSON만 출력, 다른 텍스트 절대 포함 금지):
{
  "extracted_specs": {
    "항목명": {"value": "추출된 값", "unit": "단위", "raw_text": "원문"},
    ...
  },
  "not_found": ["PDF에서 찾지 못한 항목명 목록"]
}
```

---

### STEP 4 — 제조사 여부 확인 (AI 에이전트 핵심)

**목적** : 메이커가 실제 제조사인지 확인한다. 외국계/국내 기업별로 다른 경로를 처리한다.

```
INPUT  : maker_name, homepage_url (있으면), is_foreign (있으면)
OUTPUT : StepResult (is_manufacturer, biz_reg_no, industry_type)

[커넥터 D]
  ↓
판단: 외국계 기업인가?
  (is_foreign 필드 또는 메이커명이 한글 아닌 경우 외국계로 추정)
  │
  ├─ YES (외국계) ─────────────────────────────────────────┐
  │   에이전트: 웹 검색 실행                                │
  │   쿼리: "{maker_name} manufacturer official site"      │
  │   + "{maker_name} 제조사 공식"                         │
  │   LLM이 검색 결과에서 제조사 여부 판단                  │
  │   ↓                                                    │
  │   판단: 제조사 가능성 높음? (confidence ≥ 0.75)        │
  │     ├─ YES → [커넥터 E] (제조사 확인으로 처리)         │
  │     └─ NO  → 플래그: ERR_NOT_MANUFACTURER              │
  │                                                         │
  └─ NO (국내 기업) ────────────────────────────────────────┘
      ↓
    홈페이지 URL 확인
      ├─ URL 없음 → 에이전트: 검색으로 홈페이지 URL 탐색
      │             쿼리: "{maker_name} 공식 홈페이지"
      └─ URL 있음 → 계속
      ↓
    에이전트: 홈페이지 크롤링
      - Footer 영역 우선 탐색
      - 정규식: r'\d{3}-\d{2}-\d{5}' (사업자등록번호 패턴)
      - 없으면: LLM에 회사명으로 사업자번호 검색 의뢰
      ↓
    판단: 사업자등록번호 확보됨?
      ├─ NO  → 플래그: ERR_NO_BIZ_REG_NO
      └─ YES
          ↓
        신용평가사 API 호출 ({biz_reg_no})
        → 업태(業態) 조회
          ↓
        판단: 업태에 "제조" 포함?
          ├─ NO  → 플래그: ERR_NOT_MANUFACTURER
          └─ YES → step4_result.status = PASS → [커넥터 E]
```

---

### STEP 5 — 동일품 중복 검증

**목적** : POS-Appia Smart 시스템에서 이미 등록된 동일 품목인지 확인한다.

```
INPUT  : model_name, maker_name
OUTPUT : StepResult (duplicate_found, duplicate_item_id)

[커넥터 E]
  ↓
모델명 전처리 (정규화)
  - 특수문자(-, _, /, .) → 공백으로 치환
  - 연속 공백 → 단일 공백
  - 대소문자 통일 (소문자)
  - 예: "ABC-100/A_V2" → "abc 100 a v2"
  ↓
메이커명 표준화
  - 제거 대상: Co., Ltd., Inc., Corp., GmbH, S.A., 주식회사, (주)
  - 대소문자 통일 (소문자)
  - 예: "Siemens AG Co., Ltd." → "siemens ag"
  ↓
POS-Appia Smart API 호출
  - 검색 조건: model_name_normalized AND maker_name_normalized
  - 파라미터: {"model": ..., "maker": ..., "exact": false}
  ↓
판단: 검색 결과 1건 이상 존재?
  ├─ YES → 플래그: ERR_DUPLICATE_ITEM (기존 품목 ID 포함) → FAIL
  └─ NO  → step5_result.status = PASS → [커넥터 F]
```

---

### STEP 6 — 최종 판단

**목적** : Step 0~5의 모든 결과와 플래그를 종합하여 최종 결정을 내린다.

```
INPUT  : AgentState (전체 Step 결과 + error_flags 목록)
OUTPUT : FinalDecision + ReviewReport

[커넥터 F]
  ↓
전 단계 결과 종합 (error_flags 집계)
  ↓
판단: error_flags 1개 이상 존재?
  ├─ YES → 자동 회송 처리
  │         - 반려 코드 매핑 (섹션 6 참조)
  │         - LLM: 반려 사유 자연어 요약문 생성
  │         - outcome = REJECTED
  │
  └─ NO
      ↓
    판단: LLM 신뢰도 낮은 항목 존재?
    (어느 Step이든 confidence < 0.75인 항목)
      ├─ YES → 보류 처리
      │         - outcome = PENDING
      │         - 담당자 알림 발송
      │
      └─ NO → 자동 승인
               - outcome = APPROVED
               - e-Catalog 상태값 APPROVED로 업데이트
  ↓
검토표 생성 (모든 경우 공통)
  - 단계별 판단 결과 기록
  - Excel(.xlsx) 및 PDF 형식으로 저장
  ↓
감사 로그 DB 저장
  ↓
판단: 다음 처리할 행 존재?
  ├─ YES → 다음 행으로 이동 → [STEP 0 루프백]
  └─ NO  → [종료]
```

**LLM 프롬프트 템플릿 (반려 사유 요약)**:

```
당신은 구매자재 담당자입니다. 아래 검토 결과를 바탕으로
담당자가 이해하기 쉬운 반려 사유를 2~4문장으로 작성하십시오.

NPR 신청 번호: {request_id}
모델명: {model_name} / 메이커: {maker_name}

발생한 문제:
{error_flags_list}

응답은 반려 사유 문장만 출력하십시오. JSON 형식 불필요.
```

---

## 5. 기능 목록 및 구현 요건

> 출처: `feature_list.xlsx` — 12개 기능 / 36개 세부 요건

---

### F-01. NPR 입력값 파싱
**담당 Step**: Step 0 | **우선순위**: 필수

| 세부 요건 | 설명 | 구현 기술 |
|---|---|---|
| 엑셀 행 순차 읽기 | 엑셀 파일을 행 단위로 순차적으로 읽어 모델명·메이커·사양값 등 항목별 데이터를 추출 | Python / pandas |
| 필드 유효성 검사 | 필수 입력 항목 누락 여부 및 허용 형식 범위 초과 시 즉시 오류 플래그 처리 | 룰 베이스 스크립트 |
| 첨부 PDF 연계 | 각 행과 매핑된 첨부 PDF 파일 경로를 자동 확인하고, 파일 부재 시 예외 처리 | 파일 시스템 API |

**구현 시 주의사항**:
- `pandas.read_excel()` 사용 시 `dtype=str`로 읽어 타입 자동 변환 방지
- 사양값 컬럼이 여러 열에 분산된 경우 헤더 행으로 항목명 자동 감지 처리
- PDF 경로가 상대 경로인 경우 엑셀 파일 위치 기준으로 절대 경로 변환

---

### F-02. PDF 파싱 및 텍스트 추출
**담당 Step**: Step 1, Step 3 | **우선순위**: 필수

| 세부 요건 | 설명 | 구현 기술 |
|---|---|---|
| 다중 문서 유형 지원 | 카탈로그·도면·명판사진·데이터시트 등 다양한 포맷의 PDF를 단일 파이프라인으로 처리 | pdfplumber / PyMuPDF |
| OCR 처리 | 스캔 이미지 기반 PDF에 OCR을 적용하여 텍스트·수치 데이터를 추출 (한글·영문·숫자 지원) | Tesseract / Vision LLM |
| 구조화 데이터 변환 | 추출된 텍스트를 항목명-값 쌍(JSON)으로 정리하여 후속 비교 모듈에 전달 | LLM (구조화 추출) |

**구현 시 주의사항**:
- pdfplumber 텍스트 추출 결과가 빈 문자열이면 이미지 PDF로 판단, OCR 전환
- Tesseract 언어 설정: `lang='kor+eng'`
- Vision LLM 사용 시 PDF를 페이지별 이미지(png)로 변환 후 전달 (`pdf2image` 활용)
- 대형 PDF(30페이지 이상)는 처음 10페이지만 처리 후 결과 부족 시 확장

---

### F-03. 입력값-PDF 대조 검증
**담당 Step**: Step 1 | **우선순위**: 필수(모델·메이커), 권장(형번)

| 세부 요건 | 설명 | 구현 기술 |
|---|---|---|
| 모델명 일치 확인 | 시스템 입력 모델명과 PDF 내 모델명을 정규식·문자열 유사도로 비교, 불일치 시 플래그 | 정규식 + Fuzzy Match |
| 메이커명 일치 확인 | 입력 메이커와 PDF 내 상호·로고 텍스트 비교, 이명(alias) 사전 활용 | 룰 베이스 + LLM |
| 형번 체계 유효성 검토 | Order Code 조합 형식의 모델명인 경우 LLM이 형번 체계 규칙에서 해당 코드 존재 가능 여부 판단 | LLM (언어 추론) |

**구현 시 주의사항**:
- rapidfuzz `fuzz.token_sort_ratio` 사용 (어순 무관 매칭)
- 메이커 alias 사전: JSON 파일로 외부 관리 (`maker_aliases.json`)
- 형번 체계 의심 패턴 정규식: `r'^[A-Z]{1,5}[-_]?[\dA-Z]{2,10}([-_][A-Z\d]{1,6})*$'`

---

### F-04. 검증자료 신뢰성 평가
**담당 Step**: Step 2 | **우선순위**: 필수(로고·문서유형), 권장(점수)

| 세부 요건 | 설명 | 구현 기술 |
|---|---|---|
| 제조사 로고·상호 감지 | PDF 이미지·텍스트 레이어에서 입력 메이커의 로고 또는 상호명 존재 여부를 Vision LLM으로 판별 | Vision LLM |
| 문서 유형 분류 | '견적서'·'발주서' 등 신뢰성 미달 문서를 LLM이 분류하여 자동 제외 처리 | LLM 분류기 + 키워드 룰 |
| 신뢰성 점수 산출 | 로고 유무·문서 유형·발급 기관 정보를 종합한 신뢰성 점수(0~100)를 산출하여 임계값 미달 시 반려 | 룰 기반 스코어링 |

**구현 시 주의사항**:
- 키워드 룰은 LLM 호출 전 먼저 실행 (비용 절감)
- 견적서 키워드: `["견적", "QUOTATION", "QUOTE", "발주", "PURCHASE ORDER", "견적금액", "단가"]`
- Vision LLM에는 PDF 1페이지(표지) 이미지만 우선 전달
- 신뢰성 점수 임계값: `RELIABILITY_THRESHOLD = 70` (환경변수로 조정 가능)

---

### F-05. 사양값 추출 및 대조
**담당 Step**: Step 3 | **우선순위**: 필수

| 세부 요건 | 설명 | 구현 기술 |
|---|---|---|
| 항목별 수치 추출 | PDF에서 전압·전류·압력 등 사양 항목의 수치와 단위를 구조화하여 추출 | OCR + LLM |
| 입력값 매핑 비교 | 추출된 항목을 입력 사양값과 1:1 매핑하여 일치율(%) 산출, 기준(≥95%) 미달 시 반려 | 수치 비교 룰 |
| 완성도(Completeness) 검사 | 입력된 전체 사양 항목 대비 PDF에서 확인된 항목 수 비율이 100%인지 검증 | 룰 베이스 |

**구현 시 주의사항**:
- 단위 변환 테이블 내장 필수: `kPa↔MPa↔bar↔psi`, `kW↔W↔HP`, `°C↔°F`, `mm↔cm↔m`
- 수치 비교 시 허용 오차: ±2% (제조사 공차 고려)
- 완성도 100% 조건은 엄격 적용 — 항목 누락 시 즉시 FAIL
- `match_rate` 임계값: `SPEC_MATCH_THRESHOLD = 95.0` (환경변수)

---

### F-06. 제조사 여부 확인
**담당 Step**: Step 4 | **우선순위**: 필수

| 세부 요건 | 설명 | 구현 기술 |
|---|---|---|
| 외국계 기업 판별 | 메이커명을 웹 검색하여 제조사 여부를 LLM이 판단, 공식 홈페이지·위키·뉴스 등 다중 출처 활용 | AI 에이전트 + 웹 검색 |
| 국내 기업 사업자번호 확보 | 홈페이지 URL 크롤링 또는 검색으로 사업자등록번호를 자동 추출 (하단 Footer 우선 탐색) | 웹 크롤러 도구 |
| 신용평가사 업태 조회 | 확보한 사업자등록번호로 신용평가 API를 호출하여 업태(제조업 여부) 자동 확인 | 외부 API 연동 |

**구현 시 주의사항**:
- 외국계 판별 기준: `maker_name`이 완전 한글이 아닌 경우 외국계 추정
- 사업자등록번호 정규식: `r'(\d{3}[-]\d{2}[-]\d{5}|\d{10})'`
- Footer 크롤링 시 `BeautifulSoup`으로 `<footer>`, `id="footer"`, `class="footer"` 탐색
- 제조업 업태 키워드: `["제조", "manufacturing", "製造"]`
- 웹 검색 최대 3회 재시도, 그래도 실패 시 `PENDING` 처리

---

### F-07. 동일품 중복 검증
**담당 Step**: Step 5 | **우선순위**: 필수

| 세부 요건 | 설명 | 구현 기술 |
|---|---|---|
| 모델명 전처리 표준화 | 특수문자(-, _, /) → 공백 변환, 대소문자 통일 등 검색 전 모델명 정규화 처리 | 정규식 전처리 룰 |
| 메이커명 표준화 | Co., Ltd. 등 법인 표기 제거, 영문·한글 병기 통일로 검색 정확도 향상 | 룰 베이스 + 사전 |
| POS-Appia Smart 검색 연동 | 표준화된 모델명+메이커를 AND 조건으로 POS-Appia Smart API에 전달하고 결과 해석 | 검색 API + LLM |

**구현 시 주의사항**:
- 법인 표기 제거 정규식: `r'\b(Co\.|Ltd\.|Inc\.|Corp\.|GmbH|S\.A\.|주식회사|\(주\))\b'`
- POS-Appia Smart API 응답에 결과가 있더라도 동일 `request_id`면 중복 제외 처리
- API 타임아웃: 10초, 실패 시 1회 재시도

---

### F-08. 최종 승인 판단 엔진
**담당 Step**: Step 6 | **우선순위**: 필수

| 세부 요건 | 설명 | 구현 기술 |
|---|---|---|
| 조건 충족 시 자동 승인 | Step 1~5 전체 통과 시 자동 승인 처리 및 상태값 업데이트 | 결정 룰 트리 |
| 부적합 사유별 자동 회송 | 사전 정의된 반려 사유(신뢰성 미달, 메이커 불일치, 동일품 존재 등) 매핑 후 자동 회송 | 룰 베이스 |
| 보류(사람 확인) 분기 | 애매한 케이스(LLM 판단 신뢰도 낮음, 규칙 미적용 예외)는 담당자 확인 대기열로 이관 | LLM 신뢰도 점수 + 룰 |

**구현 시 주의사항**:
- 보류 신뢰도 임계값: `LOW_CONFIDENCE_THRESHOLD = 0.75`
- 자동 승인은 반드시 **룰 트리 통과 후** 처리, LLM 판단만으로 승인 불가
- REJECTED 처리 시 e-Catalog API의 `update_status(request_id, "REJECTED", reason_code)` 호출

---

### F-09. 검토표 자동 생성
**담당 Step**: Step 6 | **우선순위**: 필수(기록), 권장(LLM 요약·파일출력)

| 세부 요건 | 설명 | 구현 기술 |
|---|---|---|
| 단계별 판단 결과 기록 | 각 Step의 처리 결과(통과/실패/보류)와 근거 데이터를 구조화하여 검토표 항목으로 자동 기재 | 템플릿 엔진 |
| 반려 사유 텍스트 생성 | LLM이 반려 원인을 자연어로 요약하여 담당자가 이해하기 쉬운 사유 문구 자동 생성 | LLM (요약 생성) |
| 검토표 파일 출력 | Excel·PDF 형식으로 검토표를 자동 저장하고 e-Catalog 시스템에 첨부 링크 생성 | openpyxl / ReportLab |

검토표 상세 스키마는 **섹션 9** 참조.

---

### F-10. 에이전트 오케스트레이션
**담당 Step**: 전체 | **우선순위**: 필수

| 세부 요건 | 설명 | 구현 기술 |
|---|---|---|
| Step 간 상태 관리 | 각 Step 처리 결과를 상태 객체로 관리하고 다음 Step에 컨텍스트를 전달하는 워크플로우 제어 | LangGraph / CrewAI |
| 도구(Tool) 등록·호출 | 웹 검색·PDF 파서·API 클라이언트 등 개별 도구를 에이전트에 등록하고 LLM이 필요 시 자율 호출 | Tool-use (Function Call) |
| 오류 복구 및 재시도 | 도구 호출 실패·타임아웃 시 자동 재시도(최대 3회) 후 보류 처리로 폴백 | 예외 처리 로직 |

**구현 시 주의사항**:
- LangGraph 사용 시 각 Step을 `@node` 데코레이터로 정의
- 조건부 엣지: `add_conditional_edges()`로 플래그 존재 여부에 따라 분기
- 도구 타임아웃: 각 도구별 `timeout` 파라미터 명시 (`default: 30s`)
- Step 간 상태 전달 시 `deepcopy` 사용 (사이드이펙트 방지)

---

### F-11. 감사 로그 및 추적
**담당 Step**: 전체 | **우선순위**: 필수(로그 저장), 권장(조회 API)

| 세부 요건 | 설명 | 구현 기술 |
|---|---|---|
| 전 단계 처리 로그 저장 | Step별 입력값·LLM 판단 근거·API 응답을 타임스탬프와 함께 DB에 영구 저장 | 로깅 프레임워크 + DB |
| LLM 판단 근거 기록 | LLM이 내린 판단의 프롬프트·응답·신뢰도 점수를 원문 보관하여 감사 및 재검토 가능 | LLM 로깅 |
| 처리 이력 조회 API | 신청 번호 기준으로 전체 처리 이력을 조회할 수 있는 내부 API 제공 | REST API |

감사 로그 DB 스키마는 **섹션 10** 참조.

---

### F-12. 예외 처리 및 알림
**담당 Step**: 전체 | **우선순위**: 권장(알림·보류), 선택(대시보드)

| 세부 요건 | 설명 | 구현 기술 |
|---|---|---|
| 처리 실패 알림 | API 오류·파싱 실패·타임아웃 등 시스템 예외 발생 시 담당자에게 이메일·슬랙 알림 자동 발송 | 알림 연동 (Slack/Email) |
| 사람 확인 요청 알림 | 보류 판정된 건에 대해 담당자에게 검토 요청 알림을 발송하고 처리 기한 설정 | 알림 + 워크플로우 |
| 처리 현황 대시보드 | 자동 승인·반려·보류 건수 및 처리 소요 시간을 실시간으로 시각화하는 모니터링 화면 | Web UI (차트) |

---

## 6. 반려 코드 정의

| 코드 | 발생 Step | 설명 | 자동 회송 여부 |
|---|---|---|---|
| `ERR_MISSING_FIELD` | Step 0 | 필수 입력 항목 누락 | 자동 회송 |
| `ERR_NO_PDF` | Step 0 | 첨부 PDF 파일 없음 | 자동 회송 |
| `ERR_MODEL_MISMATCH` | Step 1 | 모델명 불일치 (형번 체계 포함) | 자동 회송 |
| `ERR_MAKER_MISMATCH` | Step 1 | 메이커명 불일치 | 자동 회송 |
| `ERR_QUOTE_DOCUMENT` | Step 2 | 견적서·발주서 첨부 (신뢰 불가) | 자동 회송 |
| `ERR_NO_LOGO` | Step 2 | 제조사 로고·상호명 미확인 | 자동 회송 |
| `ERR_LOW_RELIABILITY` | Step 2 | 신뢰성 점수 임계값 미달 | 자동 회송 |
| `ERR_INCOMPLETE_SPEC` | Step 3 | 사양값 완성도 100% 미달 | 자동 회송 |
| `ERR_SPEC_MISMATCH` | Step 3 | 사양값 일치율 95% 미달 | 자동 회송 |
| `ERR_NOT_MANUFACTURER` | Step 4 | 제조사 아님 (판매사·대리점 등) | 자동 회송 |
| `ERR_NO_BIZ_REG_NO` | Step 4 | 사업자등록번호 확보 실패 | 보류 (사람 확인) |
| `ERR_DUPLICATE_ITEM` | Step 5 | 동일품 기등록 존재 | 자동 회송 |
| `ERR_SYSTEM_ERROR` | 전체 | 시스템 오류 (API 다운 등) | 보류 (사람 확인) |

---

## 7. 에러 처리 및 재시도 정책

### 재시도 규칙

```python
RETRY_POLICY = {
    "web_search": {"max_retries": 3, "backoff_seconds": [2, 5, 10]},
    "web_crawl":  {"max_retries": 3, "backoff_seconds": [2, 5, 10]},
    "llm_call":   {"max_retries": 2, "backoff_seconds": [3, 8]},
    "pos_api":    {"max_retries": 2, "backoff_seconds": [2, 5]},
    "credit_api": {"max_retries": 2, "backoff_seconds": [3, 8]},
}
```

### 폴백(Fallback) 규칙

```
도구 호출 최종 실패 시:
  - Step 1 (PDF 파싱 실패) → OCR 방식으로 전환
  - Step 4 (웹 검색 실패) → 플래그 ERR_SYSTEM_ERROR + PENDING 처리
  - Step 5 (POS API 실패) → 플래그 ERR_SYSTEM_ERROR + PENDING 처리
  - LLM 호출 실패 → 해당 판단을 confidence=0으로 처리 → 자동 PENDING
```

### 예외 분류

```python
class AgentException(Exception): pass
class ToolTimeoutError(AgentException): pass      # 재시도 대상
class ToolAuthError(AgentException): pass         # 즉시 알림 + 중단
class PDFParseError(AgentException): pass         # OCR 폴백
class LLMRateLimitError(AgentException): pass     # 재시도 대상
class CriticalSystemError(AgentException): pass   # 즉시 알림 + 전체 중단
```

---

## 8. 외부 도구(Tool) 인터페이스 명세

코드 에이전트는 아래 인터페이스를 `@tool` 또는 Function Call 형식으로 구현해야 한다.

### Tool 1: `pdf_parse`

```python
def pdf_parse(pdf_path: str, use_ocr: bool = False) -> dict:
    """
    PDF 파일을 파싱하여 텍스트와 이미지를 반환한다.
    Returns:
        {
            "text": str,           # 전체 텍스트 (페이지 구분: \n---PAGE---\n)
            "pages": int,          # 총 페이지 수
            "is_image_based": bool,# 이미지 PDF 여부
            "images": list[bytes]  # 첫 3페이지 이미지 (PNG bytes)
        }
    """
```

### Tool 2: `web_search`

```python
def web_search(query: str, num_results: int = 5) -> list[dict]:
    """
    웹 검색을 수행하고 결과를 반환한다.
    Returns:
        [{"title": str, "url": str, "snippet": str}, ...]
    """
```

### Tool 3: `web_crawl`

```python
def web_crawl(url: str, target: str = "footer") -> dict:
    """
    URL을 크롤링하여 대상 영역의 텍스트를 반환한다.
    Args:
        target: "footer" | "full" | "meta"
    Returns:
        {"text": str, "biz_reg_no": str | None}
    """
```

### Tool 4: `credit_api_lookup`

```python
def credit_api_lookup(biz_reg_no: str) -> dict:
    """
    신용평가사 API로 사업자 정보를 조회한다.
    Returns:
        {
            "company_name": str,
            "industry_type": str,    # 업태
            "is_manufacturer": bool, # 제조업 여부
            "status": str            # 정상 | 폐업 | 휴업
        }
    """
```

### Tool 5: `pos_appia_search`

```python
def pos_appia_search(model_name: str, maker_name: str) -> dict:
    """
    POS-Appia Smart에서 동일품을 검색한다.
    Returns:
        {
            "found": bool,
            "items": [{"item_id": str, "model": str, "maker": str}, ...]
        }
    """
```

### Tool 6: `send_notification`

```python
def send_notification(
    channel: Literal["slack", "email"],
    recipient: str,
    subject: str,
    body: str,
    request_id: str
) -> bool:
    """알림을 발송하고 성공 여부를 반환한다."""
```

---

## 9. 검토표 출력 스키마

검토표는 NPR 1건당 1개 Excel 파일로 생성하며, 아래 항목을 포함한다.

```
[헤더]
- NPR 신청 번호 (request_id)
- 모델명 / 메이커명
- 처리 일시
- 최종 결과 (승인 / 반려 / 보류)

[Step별 결과 테이블]
| Step | 항목 | 결과 | 근거 | 신뢰도 |
|------|------|------|------|--------|
| Step 0 | 필드 유효성 | PASS | 모든 필수항목 입력 확인 | - |
| Step 0 | PDF 첨부 | PASS | 파일 경로 확인됨 | - |
| Step 1 | 모델명 대조 | PASS | 유사도 98% | 0.98 |
| Step 1 | 메이커명 대조 | FAIL | "Siemens" vs "SIEMENS AG" | 0.82 |
| Step 2 | 문서 유형 | PASS | CATALOG | 0.91 |
| Step 2 | 로고 감지 | PASS | 좌측 상단 Siemens 로고 확인 | 0.95 |
| Step 2 | 신뢰성 점수 | PASS | 85/100 | - |
| Step 3 | 완성도 | PASS | 10/10 항목 확인 | - |
| Step 3 | 일치율 | PASS | 97.5% | - |
| Step 4 | 제조사 여부 | PASS | 외국계 제조사 확인 | 0.89 |
| Step 5 | 동일품 검증 | PASS | 검색 결과 0건 | - |

[반려 사유 (REJECTED인 경우)]
- 반려 코드: ERR_MAKER_MISMATCH
- 반려 요약: (LLM 생성 자연어 문장)

[서명란]
- 검토 시스템: e-Catalog Agent v1.0
- 처리 방식: 자동 / 담당자 확인 필요
```

---

## 10. 감사 로그 스키마

```sql
-- 처리 이력 테이블
CREATE TABLE agent_processing_log (
    id              BIGSERIAL PRIMARY KEY,
    request_id      VARCHAR(50) NOT NULL,
    row_index       INT,
    step_name       VARCHAR(20),          -- STEP0 ~ STEP6
    status          VARCHAR(10),          -- PASS / FAIL / ERROR / SKIP
    confidence      FLOAT,
    flags_raised    JSONB,                -- ErrorFlag 목록
    details         JSONB,                -- Step별 세부 결과
    llm_prompt      TEXT,                 -- LLM 프롬프트 원문
    llm_response    TEXT,                 -- LLM 응답 원문
    tool_calls      JSONB,                -- 호출한 도구 목록 및 응답
    processing_ms   INT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 최종 결과 테이블
CREATE TABLE agent_final_decision (
    id              BIGSERIAL PRIMARY KEY,
    request_id      VARCHAR(50) UNIQUE NOT NULL,
    outcome         VARCHAR(10),          -- APPROVED / REJECTED / PENDING
    rejection_codes JSONB,
    rejection_summary TEXT,
    low_confidence_items JSONB,
    review_report_path TEXT,             -- 검토표 파일 경로
    decided_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 인덱스
CREATE INDEX idx_log_request_id ON agent_processing_log(request_id);
CREATE INDEX idx_log_created_at ON agent_processing_log(created_at);
```

---

## 11. 구현 순서 및 우선순위

코드 에이전트는 아래 순서로 구현을 진행한다.

### Phase 1 — 핵심 파이프라인 (필수)

```
1. 데이터 모델 정의 (AgentState, NPRRecord, ErrorFlag 등 Pydantic 모델)
2. F-01: NPR 입력값 파싱 (엑셀 읽기 + 유효성 검사)
3. F-02: PDF 파싱 도구 구현 (pdfplumber + OCR 폴백)
4. F-10: LangGraph 워크플로우 뼈대 구성 (노드 + 엣지 + 상태 전달)
5. F-03: 입력값-PDF 대조 검증 (모델명 + 메이커명 매칭)
6. F-05: 사양값 추출 및 대조 (LLM 구조화 추출 + 수치 비교)
7. F-08: 최종 판단 엔진 (룰 트리)
8. F-09: 검토표 생성 (Excel 출력)
9. F-11: 감사 로그 DB 저장
```

### Phase 2 — 고급 검증 (필수)

```
10. F-04: 신뢰성 평가 (Vision LLM 로고 감지 + 문서 유형 분류)
11. F-06: 제조사 확인 에이전트 (웹 검색 + 크롤링 + 신용평가 API)
12. F-07: 동일품 검증 (전처리 + POS-Appia 연동)
```

### Phase 3 — 운영 기능 (권장/선택)

```
13. F-12: 알림 연동 (Slack + Email)
14. F-11: 처리 이력 조회 REST API
15. F-12: 처리 현황 대시보드
16. F-07: 형번 체계 LLM 검토 (권장 요건)
```

---

## 12. 코드 구조 가이드

```
ecatalog_agent/
│
├── main.py                      # 진입점 — 엑셀 파일 경로를 인수로 받아 루프 실행
│
├── models/
│   ├── __init__.py
│   ├── state.py                 # AgentState, NPRRecord, StepResult, ErrorFlag, FinalDecision
│   └── report.py                # ReviewReport
│
├── workflow/
│   ├── __init__.py
│   ├── graph.py                 # LangGraph 그래프 정의 (노드 연결, 조건부 엣지)
│   └── conditions.py            # 분기 조건 함수 (has_flags, is_low_confidence 등)
│
├── steps/
│   ├── __init__.py
│   ├── step0_intake.py          # F-01: NPR 입력값 파싱
│   ├── step1_pdf_match.py       # F-02, F-03: PDF 파싱 + 모델·메이커 대조
│   ├── step2_reliability.py     # F-04: 신뢰성 평가
│   ├── step3_spec_compare.py    # F-05: 사양값 추출·대조
│   ├── step4_manufacturer.py    # F-06: 제조사 여부 확인
│   ├── step5_duplicate.py       # F-07: 동일품 검증
│   └── step6_decision.py        # F-08, F-09: 최종 판단 + 검토표 생성
│
├── tools/
│   ├── __init__.py
│   ├── pdf_parser.py            # pdf_parse 도구
│   ├── web_search.py            # web_search 도구
│   ├── web_crawler.py           # web_crawl 도구
│   ├── credit_api.py            # credit_api_lookup 도구
│   ├── pos_appia.py             # pos_appia_search 도구
│   └── notifier.py              # send_notification 도구
│
├── utils/
│   ├── __init__.py
│   ├── text_normalize.py        # 모델명·메이커명 전처리 함수
│   ├── unit_converter.py        # 단위 변환 테이블
│   ├── fuzzy_match.py           # rapidfuzz 래퍼
│   └── biz_reg_extractor.py     # 사업자등록번호 추출 정규식
│
├── output/
│   ├── report_generator.py      # 검토표 Excel/PDF 생성 (F-09)
│   └── templates/
│       └── review_report.xlsx   # 검토표 Excel 템플릿
│
├── db/
│   ├── __init__.py
│   ├── models.py                # SQLAlchemy ORM 모델
│   └── logger.py                # 감사 로그 저장 함수 (F-11)
│
├── config/
│   ├── settings.py              # 환경변수 로드 (pydantic-settings)
│   └── maker_aliases.json       # 메이커 이명(alias) 사전
│
├── tests/
│   ├── test_step0.py
│   ├── test_step1.py
│   ├── ... (Step별 단위 테스트)
│   └── fixtures/                # 테스트용 샘플 엑셀, PDF
│
├── .env.example                 # 환경변수 예시
├── requirements.txt
└── README.md
```

### 환경변수 목록 (`.env.example`)

```bash
# LLM
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
LLM_MODEL=gpt-4o                        # 또는 claude-3-5-sonnet-20241022

# 외부 서비스
TAVILY_API_KEY=                          # 웹 검색
CREDIT_API_BASE_URL=                     # 신용평가사 API
CREDIT_API_KEY=
POS_APPIA_BASE_URL=                      # POS-Appia Smart API
POS_APPIA_API_KEY=

# 알림
SLACK_BOT_TOKEN=
SLACK_CHANNEL_ID=
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
ALERT_EMAIL=

# DB
DATABASE_URL=postgresql://user:pass@localhost:5432/ecatalog_agent

# 임계값 (기본값 제공)
RELIABILITY_THRESHOLD=70
SPEC_MATCH_THRESHOLD=95.0
LOW_CONFIDENCE_THRESHOLD=0.75
FUZZY_MODEL_THRESHOLD=90
FUZZY_MAKER_THRESHOLD=85

# 파일 경로
REVIEW_REPORT_OUTPUT_DIR=./output/reports
AUDIT_LOG_LEVEL=INFO
```

---

*문서 버전: v1.0 | 최종 업데이트: 2025-03*  
*참조 파일: `ecatalog_agent_flowchart.drawio`, `feature_list.xlsx`*
