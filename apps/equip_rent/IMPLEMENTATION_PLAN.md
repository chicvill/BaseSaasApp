# 🏗️ EquipRent (`apps/equip_rent`) — 건설장비·중장비 스마트 임대차 및 다중 임차료 관리 앱 구현계획서

- **작성일:** 2026-11-27
- **기반 플랫폼:** `BaseSaasApp` 멀티테넌트 SaaS 게이트웨이 (제로터치 마운트)
- **참조 템플릿:** `apps/rent_dist` (`Rent3` 포털 · 채팅형 전자계약 · 4방향 스크롤 문서 모달 · FIFO 수납장부 · 세무 허브 · Network-First PWA/TWA)
- **핵심 원칙:** 기존 `Rent3`(부동산 직거래·전세사기 예방, 170+ 테스트)와 **완전히 분리된 독립 앱**으로 구축하여 부동산 도메인 안정성을 100% 보존한다.

---

## 1. 기획 배경 및 분리 구축 이유

### 1.1 왜 별개 앱(`apps/equip_rent`)으로 분리하는가?
1. **1:N 다중 장비 · 다중 현장 임차 구조의 차이**
   - `Rent3`는 **임대인(건물) 1 : 호실 N**, **임차인(개인) 1 : 호실 1**(`current_room_id` 단일 필드)을 전제로 설계됨.
   - 건설장비 임대차에서는 **임대 회사(장비 보유사) 1곳이 여러 장비**를 보유할 뿐 아니라, **임차 회사(건설사) 1곳도 크레인·굴착기·지게차 등 여러 대의 장비를 복수 공사현장에 동시에 임차**하여 **장비별 개별 임차료 + 회사/현장별 합산 청구·세금계산서**를 관리해야 함 (`임차 회사 1 : 현장 N : 장비 계약 N`).
2. **부동산 전용 법령·게이트와의 도메인 충돌 방지**
   - `Rent3` 서버 코어에는 법원등기소(`iros.go.kr`) 등기부등본 강제 게이트, 주택임대차보호법 5% 상한, 국토부 RTMS 전월세 신고·확정일자 안내, 도어락 교부 등이 깊게 결합되어 있음.
   - 건설장비는 **공정거래위원회 「건설기계임대차 표준계약서(표준약관 제10059호)」** 기반이며, 등기부 대신 **건설기계등록원부 · 정기검사 · 의무보험(공제) · 대여대금 지급보증서**가 핵심 안전장치임.
3. **공모전·사업화 포트폴리오 시너지**
   - `BaseSaasApp` 위에 **① Rent3(부동산·전세사기 예방)**와 **② EquipRent(건설장비·임대료 체불/파손 분쟁 예방)**를 나란히 운영함으로써, *"표준계약서와 검증 모듈만 교체해 모든 임대차 산업으로 확장하는 범용 스마트 임대차 SaaS 엔진"*임을 실증.

---

## 2. Rent3 검증 템플릿 재사용 매핑

| 구성 요소 | Rent3 원본 참조 | EquipRent 적용 방식 |
| :--- | :--- | :--- |
| **포털 + `☰` 메뉴 + PWA/TWA** | `apps/rent_dist/rent3/frontend/` (`index.html`, `hamburger.js`, `manifest.json`, `sw.js`, `pwa.js`) | 히어로 문구 *"임대인과 직접, 수수료 없이 계약하세요"* + 장비 유형 필터(크레인·굴착기·지게차·펌프카·고소작업대 등) + Network-First PWA 그대로 적용 |
| **채팅형 스마트 계약 + 4방향 스크롤 모달** | `apps/rent_dist/rent3_contract/frontend/` (`chat.js`, `steps.js`, `index.html`) | 채팅창 안 앱 개입 카드 + 좌우·상하 스크롤 모달 구조 그대로 재사용, 문서 시트만 **장비 적격확인표 & 공정위 제10059호 표준계약서**로 교체 |
| **현장 사진 SHA-256 무결성 봉인** | `cosign_steps.py` (`add_facility_photo`) | 입주 도배 점검 → **출고 전 / 반납 시 장비 외관·유압·와이어·계기판(가동시간) 사진 SHA-256 봉인**으로 전용화 |
| **사양·임대조건서 PDF 자동 생성** | `_rent3_core/listing_pdf.py` (ReportLab + NanumGothic) | 매물 표준조건서 PDF → **건설기계 사양·보험·검사·단가 표준안내서 PDF** 자동 생성 + 좌우 스크롤 모달 뷰어 |
| **수납장부 · 세금계산서** | `_rent3_core/billing.py`, `shared_ui/r3_taxinvoice.js` | 장비별 월대/일대 청구·수납 장부 + 부가세(10% 별도 기본) + **회사/현장 단위 합산 전자세금계산서(홈택스 CSV)** |
| **공동 JSON Data Pool** | `_rent3_core/pool.py` | 독립 경로 `data/equip_rent_pool/*.json` (원자적 저장 + revision lock + 자동 백업) |

---

## 3. 핵심 도메인 모델 설계 (다중 장비 임차료 관리)

```text
LessorCompany (임대 회사 / 장비 보유사)
 └── Equipment (보유 장비 N대)
       ├── equipment_type   : "크레인" | "굴착기" | "지게차" | "콘크리트펌프" | "고소작업대" | ...
       ├── reg_no           : 건설기계 등록번호 (예: "서울04가1234")
       ├── model_spec       : 형식·규격·톤수 (예: "25톤 카고크레인 / 붐 32m", "06W 휠굴착기")
       ├── year_made        : 제작 연식
       ├── inspection_until : 정기검사 유효기간 만료일 (YYYY-MM-DD)
       ├── insurance_info   : 의무보험(공제) 가입사·증권번호·만료일
       ├── rate_daily       : 일대 기준 단가 (원, VAT 별도)
       ├── rate_monthly     : 월대 기준 단가 (원, VAT 별도)
       ├── overtime_rate_hr : 초과 가동 시간당 할증 단가 (원)
       ├── std_hours_day    : 기본 가동시간 (기본 8시간/일)
       ├── std_hours_month  : 월 기준 가동시간 (기본 174시간/월)
       ├── cost_terms       : { operator_included, fuel_payer, transport_payer, lodging_payer }
       ├── status           : "AVAILABLE" | "RESERVED" | "DEPLOYED" | "MAINTENANCE"
       └── spec_pdf_url     : 장비 사양·표준 임대조건서 PDF URL

LesseeCompany (임차 회사 / 건설사 — 1개 회사가 복수 현장·복수 장비 동시 임차)
 ├── biz_no, company_name, ceo_name, tax_email, manager_name, manager_phone
 └── WorkSite (공사 현장 N곳)
       ├── site_name, site_address, prime_contractor (발주자/원청)
       ├── payment_guarantee_no (건설기계 대여대금 지급보증서 번호·첨부)
       └── EquipContract (현장별 투입 장비 계약 N건 — 장비 1대당 1계약)
             ├── billing_unit      : "MONTHLY" (월대) | "DAILY" (일대)
             ├── unit_rate         : 약정 단가
             ├── vat_separate      : True (부가세 10% 별도 기본)
             ├── start_date / end_date
             ├── payment_due_days  : 정산 마감 후 지급 기한 (예: 월 마감 후 15일 이내)
             ├── delay_interest_pct: 지연이자율 (공정위 고시 이율 기준)
             ├── dispatch_photos   : 출고 전 점검 사진 (SHA-256)
             ├── return_photos     : 반납 시 점검 사진 (SHA-256)
             ├── standard_terms    : 공정위 제10059호 일반조건 + 특약사항
             └── integrity_hash    : 전자서명 SHA-256 무결성 해시

OperationLog (장비별 가동일지)
 └── id_contract, work_date, hours_worked, overtime_hours, idle_reason(우천/현장대기 등), operator_ok, site_mgr_ok

EquipPayment & CompanyBillingSummary (장비별 개별 임차료 + 회사 합산 정산)
 ├── 장비별 청구액 = 기본료(월대 또는 가동일수 × 일대) + 초과가동 할증 + 운반비/부대비 + 부가세(10%)
 ├── 장비별 FIFO 입금 매칭 · 미수금(체불) 경보 · 지연이자 자동 산출
 └── 임차 회사(건설사) 기준 월별 합산 청구서 & 전자세금계산서 일괄 발행
```

---

## 4. 채팅 기반 스마트 계약 5단계 흐름 (장비 전용)

| 단계 | 채팅창 내 앱 개입 카드 | 4방향 스크롤 모달 내용 및 승인 |
| :--- | :--- | :--- |
| **Step 0 (연결)** | 임대사·임차사 담당자 호출 및 본인인증 연결 | 초대 링크·QR · 사전 초안 작성 |
| **Step 1 (적격·안전 확인)** | `지금부터 장비 스마트 계약을 시작합니다.` 상호 담당자·사업자 확인 `[✓]` + `[ 건설기계 적격·보험 확인 모달 ]` 링크 | **① 건설기계등록원부(소유자·저당/압류 여부)**<br>**② 정기검사 유효기간 확인**<br>**③ 의무보험(공제) 가입 확인**<br>**④ 대여대금 지급보증서 발급 여부 확인** (AI 문서 읽기 선택 지원) |
| **Step 2 (출고 전 상태 점검)** | `[ 출고 전 장비 점검표·사진 확인 ]` 링크 (외관·유압·와이어·안전장치·계기판 가동시간 메모) | 장비 체크리스트 + 메모 + **출고 전 현장 사진(최대 12장, SHA-256 봉인)** 상호 확인 |
| **Step 3 (표준계약서 확인·동의)** | `[ 공정위 건설기계임대차 표준계약서 확인 ]` 링크 + `임대인 동의 [✓]` / `임차인 동의 [✓]` | **공정위 표준약관 제10059호 전문 시트(좌우·상하 스크롤)**: 장비 사양, 현장·발주자, 월대/일대 단가, 가동시간(8h/174h)·할증, 유류·운반·기사 부담, 지급기한·지연이자·안전책임·특약 확인 및 채팅 내 조건 일괄 제시·수락 → 동의 |
| **Step 4 (전자서명)** | `상기 내용에 동의합니다.` + 날짜 + 임대사·임차사 서명란 | 최종 표준계약서 요약 확인 + 자필 전자서명 + SHA-256 봉인 |
| **Step 5 (현장 인도 & 장부 개설)** | 장비 현장 인도 확인 & 계약 체결 완료 | **장비 인도(키·운행 개시) 확인** → `EquipContract` 활성화 + **임대사/임차사 다중 장비 임차료 장부에 즉시 추가** *(RTMS·전입신고·확정일자·주임법 5% 상한·도어락은 적용하지 않음)* |

---

## 5. 공정위 건설기계임대차 표준계약서(제10059호) 필수 항목 & 사양 JSON 예제

### 5.1 필수 기재 항목 체크리스트
- [ ] **목적물(장비 사양):** 건설기계명, 등록번호, 형식/규격(톤수·호수), 의무보험(공제) 가입현황, 정기검사 유효 여부
- [ ] **현장 및 당사자:** 공사 현장명, 현장 소재지, 발주자(원수급인), 임차인(건설업자), 임대인(장비사업자)
- [ ] **대여대금 지급보증:** 건설산업기본법·하도급법에 따른 건설기계 대여대금 지급보증서 발급·교부 여부
- [ ] **사용기간 및 가동시간:** 시작일~종료일, 기준 가동시간(**1일 8시간 / 월 174시간**), 초과 가동 할증 기준
- [ ] **임대료 및 지급방법:** 일대/월대 단가, 부가세(VAT 10%) 별도 여부, 지급 기한(1개월 초과 시 매월 종료일로부터 N일 이내 / 1개월 이하 시 종료일로부터 N일 이내), 지연이자율
- [ ] **부대비용 부담:** 유류비, 장비 운반(회송)비, 조종사(기사) 포함 여부 및 숙식비 부담 주체
- [ ] **안전관리·책임 및 분쟁해결:** 현장 안전수칙 준수, 과실 책임 범위, 건설기계분쟁조정위원회 조정 조항

### 5.2 장비 사양 데이터(`equipment_spec`) 예제
```json
{
  "equipment_type": "크레인",
  "equipment_name": "25톤 카고크레인",
  "reg_no": "경기04바2580",
  "model_spec": "25톤 / 최대 붐 32m / 아웃트리거 포함",
  "year_made": 2023,
  "inspection_valid_until": "2027-06-30",
  "insurance_provider": "건설기계공제조합",
  "insurance_valid_until": "2027-05-15",
  "billing_unit": "MONTHLY",
  "rate_monthly": 8500000,
  "rate_daily": 450000,
  "vat_separate": true,
  "std_hours_day": 8,
  "std_hours_month": 174,
  "overtime_rate_hr": 65000,
  "operator_included": true,
  "fuel_payer": "임차인(건설사)",
  "transport_payer": "임차인(건설사)",
  "lodging_payer": "현장 제공",
  "payment_guarantee_required": true,
  "payment_due_days": 15,
  "custom_clauses": "우천·강풍(풍속 10m/s 이상) 시 안전 규정에 따라 작업을 중지하며 대기 기준은 상호 협의한다."
}
```

---

## 6. 예정 디렉터리 구조 (`BaseSaasApp` 제로터치 규격)

> ⚠️ 현재 폴더(`apps/equip_rent/`)에는 구현계획서만 두므로 `core/loader.py`가 앱으로 로드하지 않으며 기존 게이트웨이에 영향이 없습니다. 실제 구현 시작 시 아래 구조대로 `app.py`를 추가합니다.

```text
apps/equip_rent/
├── IMPLEMENTATION_PLAN.md      # 본 구현계획서
├── _equip_core/                # 공통 도메인 모델·JSON Data Pool·장비 PDF 생성·정산 엔진
│   ├── pool.py                 # data/equip_rent_pool/*.json
│   ├── models.py               # LessorCompany, Equipment, LesseeCompany, WorkSite, EquipContract, OperationLog, EquipPayment
│   ├── cosign.py               # 장비 스마트 계약 세션 (등록원부·정기검사·보험·지급보증·출고사진·표준계약서)
│   ├── billing.py              # 장비별 월대/일대/초과가동 정산 + 회사/현장 합산 청구 & 세금계산서
│   └── spec_pdf.py             # 건설기계 사양·표준 임대조건서 PDF (ReportLab + NanumGothic)
├── equip_portal/               # 공개 장비 탐색 포털 (app.py + frontend/ + PWA)
├── equip_lessor/               # 임대 회사 콘솔 (보유 장비 관리·다중 현장 수납장부·체불 경보·세금계산서)
├── equip_lessee/               # 임차 회사(건설사) 콘솔 (현장별 다중 임차 장비 대시보드·가동일지·합산 정산)
└── equip_contract/             # 채팅 기반 건설기계 스마트 전자계약 앱 (4방향 스크롤 공정위 표준계약서 모달)
```

---

## 7. 단계별 구현 로드맵

### Phase 1 — 코어 모델 · 장비 포털 · 사양 PDF (MVP 1단계)
- `_equip_core` JSON Data Pool 및 `Equipment`, `LesseeCompany`, `WorkSite`, `EquipContract` 모델 구축.
- `equip_portal` 포털(Rent3 포털 템플릿 복제: *"임대인과 직접, 수수료 없이 계약하세요"*, 장비 유형·톤수·지역 검색 필터, Network-First PWA).
- `spec_pdf.py`로 장비 사양·보험·정기검사·일대/월대 조건서 PDF 자동 생성 및 좌우 스크롤 미리보기 모달.

### Phase 2 — 채팅형 스마트 계약 & 공정위 제10059호 4방향 스크롤 모달 (MVP 2단계)
- `equip_contract`: 채팅창 내 앱 개입 카드(① 사업자·담당자 확인 → ② 등록원부·정기검사·의무보험·지급보증 확인 모달 → ③ 출고 전 점검 메모·사진 SHA-256 → ④ 공정위 제10059호 표준계약서 좌우·상하 스크롤 모달 승인 → ⑤ 전자서명·장비 인도).
- 체결 시 `EquipContract` 원장 생성 및 `Equipment.status = "DEPLOYED"` 자동 전환.

### Phase 3 — 다중 장비 · 다중 현장 임차료 & 세금계산서 장부 (핵심 B2B 가치)
- `equip_lessor`: 보유 장비 전체 가동 현황, 현장/건설사별 필터, 장비별 개별 임차료 수납(FIFO) + **지급기한 초과 체불 경보 및 지연이자 자동 계산**, 회사별 합산 전자세금계산서(홈택스 CSV) 내보내기.
- `equip_lessee`: 한 건설사 계정에서 **여러 현장·여러 장비의 계약·가동일지·월별 합산 청구액**을 한눈에 확인하고 반납 정산(출고 vs 반납 사진 비교) 처리.

### Phase 4 — 검증 · PWA/TWA 패키징 · 공모전 시연 패키지
- 전용 자동 검증 테스트 스위트(`tests/test_equip_rent_suite.py`) 작성 (임대사 1곳 다중 장비 + 건설사 1곳 다중 현장·다중 장비 동시 임차료 정산 End-to-End).
- `verify_system.py` 및 Pyrefly 0 errors 검증.
- `BaseSaasApp` 통합 포털에서 `Rent3`(부동산) + `EquipRent`(건설장비) 동시 시연 시나리오 및 공모전 발표 자료 연계.

---

## 8. 불변 제약사항 (구현 시 필독)
- `core/` 및 `run.py` 절대 수정 금지 (`BaseSaasApp` 제로터치 마운트 준수).
- `apps/rent_dist/` (`Rent3`) 데이터 풀(`data/rent3_pool`)과 철저히 분리 (`data/equip_rent_pool` 사용).
- 모든 비인쇄 UI 폰트 크기 `12px` 이상 유지, `alert()` 금지(Resilient UX 알림 배너 사용).
- 테스트는 독립 파일(`tests/test_equip_rent_suite.py`)로 분리 작성.
