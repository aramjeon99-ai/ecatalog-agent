# e-Catalog 특정사양품 자동 승인 Agent — RDG (Requirements Definition Guide)

> **문서 목적** : 이 문서는 e-Catalog 특정사양품 자동 승인 Agent의 요구사항 정의서(RDG)이다.
> POC 및 정식 구현의 기준 문서로 사용되며, 기능 범위·처리 흐름·판단 기준·에러 코드를 정의한다.
> 상세 구현 명세는 `ecatalog_agent_spec.md` 를 참조한다.

---

## 목차

1. [시스템 목적 및 목표](#1-시스템-목적-및-목표)
2. [처리 결과 3종](#2-처리-결과-3종)
3. [입력 데이터 정의](#3-입력-데이터-정의)
4. [처리 흐름 (Step별 요건)](#4-처리-흐름-step별-요건)
5. [반려 코드 정의](#5-반려-코드-정의)
6. [출력 결과 정의](#6-출력-결과-정의)
7. [UI 요구사항 (Streamlit)](#7-ui-요구사항-streamlit)
8. [데이터 저장 방식](#8-데이터-저장-방식)
9. [핵심 원칙 및 제약](#9-핵심-원칙-및-제약)

---

## 1. 시스템 목적 및 목표

e-Catalog 시스템 내 **특정사양품 NPR(New Part Request) 신청**을 자동으로 검토하여,
사람의 개입 없이 **승인 / 자동 회송 / 보류(담당자 확인)** 세 가지 결과를 도출하는 AI Agent를 구현한다.

### 핵심 처리 원칙

| 원칙 | 설명 |
|---|---|
| Q코드 단위 처리 | NPR 신청은 Q코드(품목 식별자) 단위로 순차 처리한다 |
| 플래그 누적 방식 | Step 0~5에서 발생한 모든 오류·반려 플래그는 누적, Step 6에서 일괄 판단 |
| 룰 기반 최종 판단 | 최종 승인·반려 결정은 반드시 결정 룰 트리에 따른다. LLM 단독 판단으로 승인 불가 |
| 전 단계 감사 로그 | 모든 처리 결과와 판단 근거를 DB에 영구 저장 |

---

## 2. 처리 결과 3종

| 결과 | 조건 | 후속 처리 |
|---|---|---|
| **APPROVED** (자동 승인) | Step 0~5 전체 통과 + LLM 신뢰도 충족 | 상태값 `APPROVED`로 업데이트 |
| **REJECTED** (자동 회송) | 반려 플래그 1개 이상 존재 | 반려 사유 코드 매핑 후 회송 |
| **PENDING** (보류) | 플래그 없음 but LLM 신뢰도 낮은 항목 존재, 또는 시스템 오류 | 담당자 대기열 이관 + 알림 발송 |

---

## 3. 입력 데이터 정의

### 3.1 Q코드 Master (`qcode_master.xlsx`)

| 컬럼 | 설명 | 필수 |
|---|---|---|
| `q_code` | Q코드 (품목 식별자, 고유 키) | ✅ |
| `maker_name` | 메이커명 (시스템 등록값) | ✅ |

### 3.2 상세 사양 데이터 (`spec_detail.xlsx`)

| 컬럼 | 설명 | 필수 |
|---|---|---|
| `q_code` | Q코드 | ✅ |
| `SPEC_TITLE_N` | 사양 항목명 (N: 순번) | ✅ |
| `SPEC_VALUE_N` | 사양 항목값 (N: 순번) | ✅ |

### 3.3 PDF 매핑 데이터 (`pdf_mapping.xlsx`)

| 컬럼 | 설명 | 필수 |
|---|---|---|
| `q_code` | Q코드 | ✅ |
| `pdf_filename` | 첨부 PDF 파일명 | ✅ |

### 3.4 제조사명 List (`maker_list.xlsx`)

| 컬럼 | 설명 | 필수 |
|---|---|---|
| `maker_name` | 기등록된 제조사명 목록 | ✅ |

### 3.5 PDF 파일

- PDF 파일은 지정된 로컬 디렉터리(`pdf_base_dir`) 하위에 저장
- `pdf_mapping.xlsx`의 `pdf_filename`으로 경로 조합하여 참조
- 텍스트 기반 PDF 우선 처리, 이미지 PDF는 OCR 폴백

---

## 4. 처리 흐름 (Step별 요건)

### 전체 루프 구조

```
FOR each q_code IN q_code_list:
    Step 0: 입력 유효성 검사
    Step 1: PDF 파싱 & 입력값 대조
    Step 2: 검증자료 신뢰성 확인
    Step 3: 사양값 추출 및 대조
    Step 4: 제조사 여부 확인
    Step 5: 동일품 중복 검증
    Step 6: 최종 판단 → APPROVED / REJECTED / PENDING

    감사 로그 저장
    검토표 생성
    알림 발송 (필요 시)
END FOR
```

---

### STEP 0 — 입력 유효성 검사

**목적** : 필수 입력 항목 누락 및 첨부 PDF 존재 여부를 확인한다.

| 검사 항목 | 통과 조건 | 실패 플래그 |
|---|---|---|
| 필수 필드 존재 | `q_code`, `maker_name`, `pdf_filename` 모두 존재 | `ERR_MISSING_FIELD` |
| PDF 파일 존재 | `pdf_base_dir / pdf_filename` 경로에 파일 실재 | `ERR_NO_PDF` |

- 이 단계에서 Critical 플래그 발생 시 Step 1~5를 건너뛰고 Step 6으로 직행

---

### STEP 1 — PDF 파싱 & 입력값 대조

**목적** : 첨부 PDF에서 모델명·메이커명을 추출하고 시스템 입력값과 대조한다.

| 검사 항목 | 통과 조건 | 실패 플래그 |
|---|---|---|
| 모델명 일치 | 정규식 매칭 또는 rapidfuzz 유사도 ≥ 0.90 | `ERR_MODEL_MISMATCH` |
| 메이커명 일치 | alias 사전 조회 후 rapidfuzz 유사도 ≥ 0.85 | `ERR_MAKER_MISMATCH` |
| 형번(Order Code) 체계 | 패턴 해당 시 LLM으로 유효성 판단 | `ERR_MODEL_MISMATCH` |

**PDF 파싱 순서**:
1. pdfplumber / PyMuPDF 텍스트 추출 시도
2. 텍스트 추출 실패(이미지 PDF) → Tesseract OCR 적용 (`lang='kor+eng'`)

---

### STEP 2 — 검증자료 신뢰성 확인

**목적** : 첨부 자료가 신뢰할 수 있는 제조사 발행 문서인지 검증한다.

| 검사 항목 | 통과 조건 | 실패 플래그 |
|---|---|---|
| 문서 유형 | 견적서·발주서가 아닌 카탈로그/데이터시트/도면/명판 | `ERR_QUOTE_DOCUMENT` |
| 제조사 로고·상호 | Vision LLM으로 로고 또는 상호명 감지 | `ERR_NO_LOGO` |
| 신뢰성 점수 | 로고·문서유형·발급기관·날짜 종합 점수 ≥ 70 | `ERR_LOW_RELIABILITY` |

**신뢰성 점수 산출 기준**:

| 항목 | 점수 |
|---|---|
| 제조사 로고 존재 | +40 |
| 문서 유형 (카탈로그/데이터시트) | +30 |
| 발급 기관 명시 | +20 |
| 날짜/버전 정보 | +10 |
| **합계 기준** | **≥ 70 통과** |

> 견적서 키워드 룰은 LLM 호출 전 먼저 실행 (비용 절감):
> `["견적", "QUOTATION", "QUOTE", "발주", "PURCHASE ORDER", "견적금액", "단가"]`

---

### STEP 3 — 사양값 추출 및 대조

**목적** : PDF에서 사양 항목-값 쌍을 구조화하여 입력값과 비교한다.

| 검사 항목 | 통과 조건 | 실패 플래그 |
|---|---|---|
| 완성도(Completeness) | 입력 전체 사양 항목 대비 PDF 확인 비율 = 100% | `ERR_INCOMPLETE_SPEC` |
| 일치율(Match Rate) | 항목별 수치 1:1 비교 ≥ 95% | `ERR_SPEC_MISMATCH` |

**수치 비교 규칙**:
- 단위 자동 변환: `kPa↔MPa↔bar↔psi`, `kW↔W↔HP`, `°C↔°F`, `mm↔cm↔m`
- 허용 오차: ±2% (제조사 공차 고려)
- 완성도 100% 조건은 엄격 적용 — 항목 1개라도 누락 시 즉시 FAIL

---

### STEP 4 — 제조사 여부 확인

**목적** : 메이커가 실제 제조사인지 확인한다. 외국계/국내 기업별로 다른 경로를 처리한다.

#### 외국계 기업 경로
- 판별 기준: `maker_name`이 완전 한글이 아닌 경우
- 웹 검색 실행 → LLM이 제조사 여부 판단
- LLM 신뢰도 ≥ 0.75 → 제조사 확인 (PASS)
- 그 이하 → `ERR_NOT_MANUFACTURER`

#### 국내 기업 경로
1. 홈페이지 크롤링 (Footer에서 사업자등록번호 탐색)
2. 사업자등록번호 미확보 시 → `ERR_NO_BIZ_REG_NO` (PENDING 처리)
3. 신용평가사 API 호출 → 업태 "제조" 포함 여부 확인
4. 업태 불일치 시 → `ERR_NOT_MANUFACTURER`

> 사업자등록번호 정규식: `r'(\d{3}[-]\d{2}[-]\d{5}|\d{10})'`

---

### STEP 5 — 동일품 중복 검증

**목적** : POS-Appia Smart 시스템에서 이미 등록된 동일 품목인지 확인한다.

| 처리 | 설명 |
|---|---|
| 모델명 전처리 | 특수문자(-, _, /, .) → 공백, 소문자 통일 |
| 메이커명 표준화 | Co., Ltd., Inc., 주식회사, (주) 등 제거 |
| API 검색 | 표준화된 모델명+메이커 AND 조건 검색 |
| 판단 | 결과 1건 이상 존재 시 → `ERR_DUPLICATE_ITEM` |

---

### STEP 6 — 최종 판단

**목적** : Step 0~5의 모든 결과와 플래그를 종합하여 최종 결정을 내린다.

```
error_flags 집계
  ↓
1개 이상 존재? → REJECTED (반려 사유 자연어 요약 생성)
  ↓
없음 + LLM 신뢰도 낮은 항목 존재? → PENDING (담당자 알림)
  ↓
없음 + 신뢰도 충족 → APPROVED (상태값 업데이트)
```

**보류 신뢰도 임계값**: `LOW_CONFIDENCE_THRESHOLD = 0.75`
**자동 승인**: 반드시 룰 트리 통과 후 처리, LLM 판단만으로 승인 불가

---

## 5. 반려 코드 정의

| 코드 | 발생 Step | 설명 | 처리 결과 |
|---|---|---|---|
| `ERR_MISSING_FIELD` | Step 0 | 필수 입력 항목 누락 | REJECTED |
| `ERR_NO_PDF` | Step 0 | 첨부 PDF 파일 없음 | REJECTED |
| `ERR_MODEL_MISMATCH` | Step 1 | 모델명 불일치 (형번 체계 포함) | REJECTED |
| `ERR_MAKER_MISMATCH` | Step 1 | 메이커명 불일치 | REJECTED |
| `ERR_QUOTE_DOCUMENT` | Step 2 | 견적서·발주서 첨부 (신뢰 불가) | REJECTED |
| `ERR_NO_LOGO` | Step 2 | 제조사 로고·상호명 미확인 | REJECTED |
| `ERR_LOW_RELIABILITY` | Step 2 | 신뢰성 점수 임계값 미달 | REJECTED |
| `ERR_INCOMPLETE_SPEC` | Step 3 | 사양값 완성도 100% 미달 | REJECTED |
| `ERR_SPEC_MISMATCH` | Step 3 | 사양값 일치율 95% 미달 | REJECTED |
| `ERR_NOT_MANUFACTURER` | Step 4 | 제조사 아님 (판매사·대리점 등) | REJECTED |
| `ERR_NO_BIZ_REG_NO` | Step 4 | 사업자등록번호 확보 실패 | **PENDING** |
| `ERR_DUPLICATE_ITEM` | Step 5 | 동일품 기등록 존재 | REJECTED |
| `ERR_SYSTEM_ERROR` | 전체 | 시스템 오류 (API 다운 등) | **PENDING** |

---

## 6. 출력 결과 정의

### 기본 출력 (Q코드별)

| 필드 | 설명 |
|---|---|
| `q_code` | Q코드 |
| `maker_name` | 입력 메이커명 |
| `connected_pdf_filename` | 연결된 PDF 파일명 |
| `pdf_exists` | PDF 파일 존재 여부 |
| `best_matched_maker` | 제조사 목록 내 최고 유사 메이커명 |
| `similarity_score` | 메이커명 유사도 점수 (0~100) |
| `expected_specs` | 입력 사양 목록 `[{title, value}]` |
| `step_results` | Step별 처리 결과 `[{step, status, confidence, flags}]` |
| `error_flags` | 발생한 반려 플래그 목록 |
| `outcome` | 최종 판단 결과: `APPROVED` / `REJECTED` / `PENDING` |
| `summary` | 판단 사유 요약 텍스트 |
| `decided_at` | 판단 일시 (UTC ISO8601) |

### Step별 status 값

| status | 의미 |
|---|---|
| `PASS` | 해당 Step 통과 |
| `FAIL` | 해당 Step 실패 (반려 플래그 발생) |
| `SKIP` | 전제 조건 미충족으로 검사 건너뜀 |
| `ERROR` | 시스템 오류 발생 |

### 검토표 출력 파일

- 형식: Excel (`.xlsx`) 및 PDF
- 포함 내용: 단계별 판단 결과, 근거 데이터, 반려 사유 자연어 요약
- 저장 경로: `output/` 디렉터리

---

## 7. UI 요구사항 (Streamlit)

### 7.1 사이드바 — 기준 데이터 등록 (초기 1회)

| 항목 | 설명 |
|---|---|
| PDF base 폴더 경로 입력 | 로컬 PDF 저장 디렉터리 경로 |
| Q코드 Master 업로드 | `.xlsx/.csv` |
| 상세 사양 업로드 | `.xlsx/.csv` |
| PDF 매핑 파일 업로드 | `.xlsx/.csv` |
| 제조사명 List 업로드 | `.xlsx/.csv` |
| **초기 데이터 저장** 버튼 | 4개 파일 모두 업로드 시 활성화, `data/` 에 저장 |

- 기 저장 데이터 존재 시 자동 로드 및 안내 메시지 표시
- 새로고침 버튼 제공

### 7.2 메인 화면 — 검증하기

| 항목 | 설명 |
|---|---|
| Q코드 검색 입력 | 텍스트 검색으로 목록 필터링 |
| Q코드 선택 드롭다운 | 필터링된 Q코드 목록 |
| **검증하기** 버튼 | 선택된 Q코드에 대해 검증 실행 |

### 7.3 결과 표시 항목

| 섹션 | 표시 내용 |
|---|---|
| 결과 헤더 | 최종 판단 (`APPROVED` / `REJECTED` / `PENDING`) + 요약 텍스트 |
| 기본 정보 | q_code, maker_name, pdf_exists (metric 카드) |
| 연결된 PDF 파일명 | code 블록 표시 |
| 제조사 매칭 결과 | best_matched_maker, similarity_score |
| 상세 사양 목록 | DataFrame 테이블 |
| Step별 결과 | DataFrame 테이블 (step, status, confidence, flags) |
| Error Flags | DataFrame 테이블 (code, step, message, evidence) |
| 근거자료 (Expander) | PDF 텍스트 샘플, 기준 사양 JSON, 제조사 매칭 JSON, 에러 플래그 JSON |

---

## 8. 데이터 저장 방식

### 로컬 파일 저장 (`data/` 디렉터리)

```
data/
├── qcode_master.xlsx       # Q코드 Master
├── spec_detail.xlsx        # 상세 사양
├── pdf_mapping.xlsx        # PDF 매핑
├── maker_list.xlsx         # 제조사명 List
└── app_config.json         # 앱 설정 (pdf_base_dir 등)
```

- 초기 1회 업로드 후 로컬 저장
- 이후 앱 실행 시 자동 로드
- `app_config.json` 구조:
  ```json
  {
    "pdf_base_dir": "경로/to/pdf/",
    "saved_at": 1234567890.0
  }
  ```

### 감사 로그 DB

- 엔진: SQLite (개발/MVP) / PostgreSQL (운영)
- 저장 항목: Step별 입력값, LLM 프롬프트·응답 원문, 처리 결과, 타임스탬프

---

## 9. 핵심 원칙 및 제약

| 원칙 | 상세 |
|---|---|
| 룰 기반 최종 판단 | LLM은 근거 수집·분류 보조 역할. 최종 승인/반려는 결정 룰 트리만 가능 |
| 보류 우선 원칙 | 불확실한 케이스는 REJECTED 대신 PENDING으로 처리, 사람이 최종 판단 |
| 플래그 누적 | 각 Step의 반려 플래그는 초기화 없이 누적, Step 6에서 일괄 집계 |
| 재시도 정책 | 외부 API·LLM 호출 실패 시 최대 3회 재시도, 최종 실패 시 PENDING 처리 |
| 단순하고 빠른 POC | 외부 통합(신용평가 API, POS-Appia, Slack 등)은 MVP에서 stub 처리 허용 |
| 감사 가능성 | 모든 판단 근거를 원문 보관하여 사후 검토·재판단 가능 |

---

*문서 버전: v1.1 | 최종 수정: 2026-03-31*
*구현 명세 상세: `ecatalog_agent_spec.md` 참조*
