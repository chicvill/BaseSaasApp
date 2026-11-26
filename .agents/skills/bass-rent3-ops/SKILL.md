---
name: bass-rent3-ops
description: Operational business rules, domain invariants, security rules, and verification instructions for the BaseSaasApp Smart Rent3 platform (apps/rent_dist/rent3*, _rent3_core). Review and update this skill before modifying Rent3 features.
---

# Bass Rent3 (비대면 직거래 임대차 & 올인원 건물관리) 도메인 운영규칙 Skill

## 1. 아키텍처 & 불변 규칙 (Core Architecture)

- **절대 금지**: `core/` 및 `run.py`는 단 1줄도 수정하지 않는다 (`test_01` 검증).
- **실제 코드 위치 (`apps/rent_dist/`)**:
  - `_rent3_core/` — 공유 OOP 도메인 엔진 (`PoolObj`, `JsonDataPool`, `models.py`, `billing.py`, `cosign.py`, `ops.py`, `broker.py`, `authz.py`, `franchise.py`, `fieldcrypto.py`, `notifications.py`, `contract_chat.py` 등)
  - `rent3/` — 직거래 포털 (`/rent3/`, 매물 검색·로그인·가입·비밀번호 재설정·Data Pool 관제, 서브도메인 `rent3.localhost`·`rent.localhost`)
  - `rent3_admin/` — 본사 관리인 전용 (`/rent3_admin/`, 가맹 심사·승인, 월 SaaS 이용료 정산, 전사 관제)
  - `rent3_lessor/` — 임대인 전용 (`/rent3_lessor/`, 호실 수납·은행문자 파싱, 계약관리, 만기통보, 전출 정산, 매물·중개계약 등록, 경비·세무 결산)
  - `rent3_tenant/` — 임차인 전용 (`/rent3_tenant/`, 공실 탐색, 마이홈 납부현황, 도어락·잔금일 등기부 확인, 채팅, 연말정산 증명서, 퇴실 정산)
  - `rent3_contract/` — 중립 공동 임대계약 전용 (`/rent3_contract/`, 채팅 중심 계약방, 진위 확인 `verify.html`)
- **공동 실시간 JSON Data Pool**: `data/rent3_pool/*.json` (`users`, `buildings`, `rooms`, `contracts`, `payments`, `outlays`, `cosign_sessions`, `ops`, `rooms/` 분할 파일). `PoolObj.save()` / `.delete()` 시 임시 파일 + `os.replace` 원자적 갱신 및 `mtime` 실시간 공유.
- **레거시 `rent` / `rent2` 통합 완료**: `apps/rent2` 및 관련 데이터·스킬은 삭제되었으며, FIFO 수납·후불 비과세·가짜 체납 방지·은행문자 계좌/시각 마스킹·`만원` 파싱 등 핵심 규칙은 모두 `_rent3_core`에 통합되었다.
- **작업·배포 워크플로우**: 코드 수정 및 테스트 검증 완료 시 `docs/agent_board.md` 기록은 생략하고 바로 `origin/main`에 커밋·푸시한다.

---

## 2. 수납 · 회계 · 세무 불변식 (`_rent3_core/billing.py`, `models.py`)

1. **후불 입주월 비과세 / 가짜 체납(Ghost Overdue) 차단**:
   - 전입일(`move_in_date`) 이전 월 및 퇴실 이후 월은 절대 청구하지 않는다.
   - 월세가 `후불`이면 입주 당월 청구액은 0원(`POSTPAID_EXEMPT`)이며 익월부터 전월 사용료를 청구한다.
   - 납입일은 `move_in_date` 일자 기준(단월 말일 자동 보정). 납입일 당일까지 미입금은 `DUE_SOON(납부 예정)`, 다음 날부터 `OVERDUE(미납)`.
2. **FIFO 순차 상계 & 선수금 차단 & 중복 입금 방지**:
   - 입금은 가장 오래된 미납월부터 순차 상계하고 여러 달에 걸치면 청구월별 납부 건으로 분할 기록한다.
   - 미납 합계를 초과하는 **선수금은 받지 않고 오류**로 차단한다.
   - 동일 입금 중복 등록은 `idempotency_key`로 차단. 납부 삭제는 사유 필수 **무효 처리(기록 보존)**.
3. **은행 입금 문자 자동 파싱 (`SmsDepositParser`)**:
   - `잔액` 이후 제거, 계좌번호·전화번호·시각 마스킹 후 금액(`원` / `만원` 단위 포함)·날짜·호실번호·입금자명(계약자 또는 `payer_names` 송금자명)을 추출한다.
4. **세무 (부가세·종소세·면세 사업장현황)**:
   - 주거용 원룸 면세, 상가·사무실 또는 세입자 세금계산서 요청(`tax_invoice_requested`) 호실은 10% VAT 분리 계산.
   - `Building.vat_rooms`, `Building.income_tax_rooms` 건물별 관리, 홈택스 전자세금계산서 일괄발급 30컬럼 CSV, 8대 공용운영비 NOI 결산, 5월 종소세 UTF-8 BOM CSV, `apps/tax`의 `/api/tax/sync/rent3` 연동.

---

## 3. 현장 계약 흐름 & 다음 계약 조건 (`cosign.py`, `contract_chat.py`, `rent3_lessor`)

1. **채팅 중심 통합 계약방 (`rent3_contract`, `test_158~160`)**:
   - 진입 즉시 통합 채팅방(`#chatWorkspace`)이 메인으로 동작하며, 상단 액션 카드(`#wsActionBar`)에 현재 계약 조건(보증금·계약금·월세·관리비·청소비·입주일)이 양측에 표시된다.
   - 조건 일괄 제시(`kind="bundle"`) 및 한 번에 수락(복합 변경값 기준 계약금≤보증금 일관성 검증, `test_159`).
   - **신규 계약 (`NEW`)**: 계약금 기본값 = **보증금의 10%**. 채팅방 순서: 조건 표시 → `🪪 상호 신원 확인 체크` → `📄 법원 등기부 열람·PDF·AI 분석`(모달) → `✅ 등기부 확인 체크` → `🛋️ 옵션 확인·벽지 상태 메모·📷 사진 촬영` → `✅ 조건 확인 체크` → `✍️ 서명 날인` → 체결 직전 등기부 재열람 → 임대인 계좌번호 + 계약금(10%) 표시 → `💰 계약금 수납 확인 & 계약 종료`.
   - **연장 계약 (`RENEWAL`)**: 본인·등기부·옵션 확인 및 계약금(0원) 생략 → 채팅방에서 **법정 5% 상한** 조건 확인 체크 → `✍️ 서명 날인` 즉시 체결(도어락 유지).
   - 체결 계약서에는 임차료 수납 계좌 + 자동이체 권유 문구 표시(임대인 계좌 미등록 시 가짜 계좌 금지).
2. **현재 계약 조건 vs 다음 계약 조건(`next_terms`) 분리 (`test_149~151`, `160~161`)**:
   - 호실 수납 창에는 `다음 계약 조건` 카드를 두지 않는다.
   - `호실 카드 ▸ 계약내용 ▸ 🤝 신규 계약` 또는 `🔁 연장 계약` 시작 창에서 **현재 계약 조건을 먼저 노출**하고, 임대인이 직접 수정(연장 계약은 자동 5% 인상 없이 법정 5% 상한 안내·`enforce_renewal_cap` 검증)하여 **`💾 다음 계약 조건 저장`**한다.
   - 전출/만기 전까지 현재 활성 계약과 호실 조건은 절대 덮어쓰지 않는다.
   - **신규 임차인이 입주하는 순간**(퇴실 확정 후 예약 계약 활성화, 공실 계약 체결, 중개 계약 등록) 또는 **연장 시작일 도래 순간** `next_terms`(보증금·월세·관리비·**퇴실 청소비 `cleaning_fee`**·납부방식)가 현재 계약 조건을 대체하고 `next_terms`는 비워진다.
3. **사전 매물 공개(1개월 전 제한) & 예약 계약 (`SCHEDULED`, `test_146~148`)**:
   - 입주 중 호실의 사전 매물 공개는 퇴실 기준일(전출예정일 또는 만기일) **1개월 전부터**만 허용(조기 회신 시 공개일에 자동 공개).
   - 사전 매물 호실에 미리 체결한 계약은 `SCHEDULED` 예약 계약으로 걸리고, 기존 임차인 퇴실 확정(`Room.vacate`) 순간 자동 `ACTIVE` 입주 전환된다.
4. **중개 지면계약 사진 등록 (`BROKER_PAPER`, `broker.py`)**:
   - 공인중개사 지면계약 사진/PDF 보관(`uploads/contracts/`), OCR 텍스트 파싱 보조, 원클릭 Data Pool 5종 동기화.

---

## 4. 전출 정산 & 채팅 기반 반환 계좌 흐름 (`ops.py`, `test_152~156`)

1. **버튼 명칭**: 호실 카드 상단 3번째 버튼은 **`전출 정산`**(모달 제목·채팅 버튼·매뉴얼 모두 통일).
2. **정산 공식 (`CheckOutSettlement`)**:
   - `반환 예정액 = 임차보증금 − 미납 임차료 − 일할 계산 임차료 − 청소비 − 미납 공과금(전기·수도·가스·기타) − 파손 복구비`
   - **만기 전 퇴실 월세 부담 (`test_152`)**: 만기일 또는 새 임차인 입주 전날(먼저 오는 날)까지 부담. 1개월 주기(전입일~다음 달 전입일 전날) 중간 종료 시 `(경과 일수 × 월 임차료) ÷ 30` 일할 계산(선납 차감, 부족분 표시).
   - **퇴실 청소비 (`test_153`)**: 계약에 약정한 경우에만 약정 금액 이내로 공제(미약정 시 0원 잠금).
   - **항목별 수정 & 게시 체크 (`test_154~155`)**: 기본 저장은 `DRAFT(임시 저장)`로 임차인에게 금액·알림 미노출. 임대인이 수정을 마치고 **`임차인에게 게시`**에 체크해야 `CALCULATED`로 게시된다.
   - **채팅 정산 카드 (`test_156`)**: 게시 후 채팅창 위 카드에서 임차인이 `[정산 내역 확인]` → 본인 명의 계좌·동의 서명 제출 → 임대인 채팅 카드에서 계좌 확인·복사·`[💸 송금 완료 표시]` → 임차인 `[✅ 받았습니다]`(또는 이의 없이 7일 경과) 시 `Room.vacate()` 공실/예약입주 자동 전환.

---

## 5. 보안 · 분쟁 대응 불변식 (`authz.py`, `franchise.py`, `fieldcrypto.py`)

1. **등기부 확인 3회 & 차단 정책**: ① 계약일 임차인 직접 열람(`submit_tenant_registry`, AI 읽기는 참고용·동의 필수·계약당 5회/가맹점 월 한도) ② 서명 후 체결 직전 재열람(변동 시 즉시 `CANCELLED`) ③ 잔금일 재열람(`balance-registry-check`, 변동 없음 확인 후에만 임대인 도어락 교부 `release-keys`).
   - 소유자 불일치 또는 압류·가압류·가처분·신탁·경매·임차권등기 시 절대 차단(409 `CANCELLED`).
   - 전세 또는 보증금 5천만 원 초과 + 선순위 근저당 시 보증보험('가입 완료'/'가입 예정' + 거절 시 무효 특약 자동 삽입 + 잔금 전 증빙 제출) 또는 근저당 말소 특약 필수. 소액 월세(2천만 원 이하)+근저당은 위험 확인 체크 필수.
2. **계약 무결성 · 진위 확인**: 체결 시 SHA-256 해시 봉인(`integrity_hash`), 공개 `verify.html` / `GET /rent3_contract/verify` 대조(개인정보 미노출). `CoSignSession`·`Contract`는 `normalize_phones = False`.
   - `FIX-...` 번호는 **내부 계약 접수번호(법적 확정일자 아님)**로 명시하고 주민센터·등기소 확정일자 안내.
3. **한 호실 = 진행 계약 1건 / 임차인 동시 2건**: 진행 계약 호실은 타인 호출 차단, 무응답 24시간(사전작성 72시간) 자동 해제. 체결(Step 5)은 임대인 `own_account_checked` 필수.
4. **가맹 승인 & 접근 통제**:
   - 포털 가입 임대인은 `PENDING_APPROVAL` → 휴대폰 본인인증 + 주소 + 등기부 제출 + 본사 관리자 원본 대조 기록(`registry-verify`) 후 `ACTIVE` 승인. 승인 전 매물 공개·직거래 계약 차단.
   - 토큰 인증(12시간 유효, 로그아웃 시 `_revoked_tokens.json` 즉시 폐기, 401 시 `rent3_session_guard.js` 자동 로그아웃 안내), 지점/본인 격리, 임차인 휴대폰 본인인증 게이트(`Tenant.identity.verified`).
   - 비밀번호는 `pbkdf2_sha256` 해시 저장, 로그인 무차별 대입 잠금(계정 5회/IP 30회 → 15분). 데모 비밀번호·가짜 본인인증은 로컬 loopback(프록시 헤더 없음) 또는 명시적 환경변수에서만 허용.
   - 공개 목록 API는 반드시 `authz.public_room()`으로 민감 필드(`ROOM_PRIVATE_FIELDS`) 제거.
   - 민감 필드(`doorlock_pwd`, `common_entrance_pwd`, `account_number`, `lessor_account`)는 `RENT3_CRYPTO_KEY` 또는 `MASTER_CRYPTO_KEY`로 AES-256-GCM 암호화 저장(`fieldcrypto.py`).
   - 잔금 확인 전 `keys_released=False`로 도어락·공동현관 비밀번호 서버 마스킹.
5. **운영 필수 환경변수**: `BASS_SECRET`, `BASS_SEED_PASSWORD`, `RENT3_DEMO_LOGIN=0`, `GEMINI_API_KEY`, `MASTER_CRYPTO_KEY`(또는 `RENT3_CRYPTO_KEY`), `RENT3_TRUSTED_PROXY_HOPS`, `RENT3_POOL_DIR`, `RENT3_IDENTITY_PROVIDER=sms` + `RENT3_SMS_*`. 배포 전 `py scripts/deploy_check.py --strict` 점검.

---

## 6. UI · 용어 · 검증 표준

- **Resilient UX**: 브라우저 `alert()` 금지, 4색 배너(`Bass.updateAppAlert`, Rent3 오류/경고 2초 자동 닫힘) + `Bass.withLock` 이중 클릭 방지 + `manual.html` A4 인쇄 지원.
- **모바일 규칙**: 모든 CSS 글자 크기 **`>= 12px`** (`test_71`), 버튼 높이 `>= 40~44px`, 공통 라이트 테마(`shared_ui/airbnb_theme.css`, `rent3_mobile.css`), `r3_notify.js` 🔔 알림.
- **용어 통일 (`test_138`)**: `메시지` = 휴대폰 문자(SMS), `채팅` = 앱 내 채팅(`계약방 채팅` 등, `계약방 대화`/`대화 열기` 금지).
- **자동 검증 명령**:
  - 전체 Rent3 테스트(161개): `py -3.14 -m unittest tests.test_rent3_pool_suite`
  - 플랫폼 통합 테스트: `py -3.14 -m unittest tests.test_platform`
  - JS 문법 검사 포함: `test_131_frontend_scripts_have_valid_syntax` 또는 `py -3.14 .agents/skills/mqnet-system-navigator/scripts/verify_system.py`
  - 세무 연동 검증: `py -3.14 .agents/skills/bass-tax-ops/scripts/verify_tax_system.py`
