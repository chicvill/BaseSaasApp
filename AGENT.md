# 🏛️ AGENT.md — BaseSaasApp 객체지향 마이크로 SaaS 아키텍처 헌법

본 문서는 **불변의 BaseSaasApp(BasicSaasApp) 플랫폼** 위에서 도메인 시스템을 설계·구축·확장할 때 모든 에이전트와 개발자가 반드시 준수해야 하는 최상위 아키텍처 원칙을 정의합니다.

---

## 1. 🔒 불변의 BaseSaasApp 코어 원칙 (Immutable Core)

- 플랫폼 코어 계층(`core/app_base.py`, `core/gateway.py`, `core/loader.py`, `core/auth.py`, `core/errors.py`, `core/web.py`, `core/kernel/`, `run.py`)은 **절대 수정하지 않는 불변(Immutable) 기반 엔진**입니다.
- 새로운 비즈니스 시스템 구축이나 기능 확장은 코어 코드 수정 없이, 오직 `apps/<app_id>/app.py`에 **`BaseSaaSApp`을 상속받은 독립 앱 객체(App Object)**를 추가하여 제로터치(Zero-Touch)로 자동 발견·마운트되게 합니다.

---

## 2. 🧩 전 구성 요소의 완전 객체화 (Everything-is-an-Object)

시스템을 이루는 **앱 자체와 내부의 모든 도메인 구성 요소는 각각 독립된 객체(Object)**로 설계합니다.

1. **시스템 레벨 객체 (`BaseSaaSApp`)**:
   - 각각의 마이크로 앱(통합관리용, 관리인용, 임대인용, 임차인용, 공동계약용 등)은 고유한 메타데이터, 라우터(`get_router()`), 기동 훅(`on_startup()`), 상태 진단(`health_check()`)을 가진 독립된 **앱 객체**입니다.
2. **도메인 레벨 객체 (`PoolObj` / `Obj` 하위 클래스)**:
   - 건물(`Building`), 호실(`Room`), 임대계약(`Contract`), 수납(`Pay`), 지출(`Outlay`), 사용자(`User` — 관리인 `Admin` · 임대인 `Lessor` · 임차인 `Tenant`), 공동계약 세션(`CoSignSession`) 등 **모든 실체와 이벤트 단위가 각각의 클래스/객체**가 됩니다.
   - 절차적 딕셔너리 조작을 지양하고, 각 객체가 자신의 상태 검증, 비즈니스 계산(예: FIFO 순차 상계, 후불 입주월 가짜 체납 방지, 부가세 산출, 계약 조건 변경 시 재동의 잠금), 직렬화 및 저장/삭제 책임을 스스로 캡슐화합니다.

---

## 3. 🔄 실시간 JSON 파일 동기화 & 공동 Data Pool 원칙 (Real-Time JSON Data Pool)

분리된 여러 앱 객체들은 고립된 사일로(Silo) DB를 갖지 않고, **하나의 공동 데이터 풀(Shared Data Pool)**을 실시간으로 함께 사용합니다.

1. **업데이트 즉시 실시간 JSON 파일 갱신**:
   - 어느 앱에서든 객체(`Building`, `Room`, `Contract`, `Pay`, `User` 등)의 데이터가 생성·수정·삭제될 때마다, 공동 데이터 풀 디렉터리(예: `data/<domain>_pool/`) 내 해당 JSON 파일이 **실시간·원자적(Atomic Write: 임시 파일 기록 후 `os.replace`)**으로 즉시 업데이트됩니다.
   - 모든 JSON 파일은 사람이 직접 열람·검증·백업할 수 있도록 UTF-8(`ensure_ascii=False, indent=2`) 규격으로 유지합니다.
2. **멀티 앱 · 멀티 프로세스 실시간 공유 (`mtime` Live Sync)**:
   - 통합 게이트웨이(`py run.py`) 환경은 물론, 앱별 단독 실행(`py run.py --app <app_id> --port ...`) 환경에서도 파일 변경 시각(`mtime`) 기반 동기화를 통해 모든 앱이 항상 최신 JSON 상태를 즉시 읽고 반영합니다.
   - 플랫폼 기본 커널 저장소(`core.kernel.Store` SQLite WAL 및 변경 감사 로그 `/api/timeline`)와도 함께 연동되어 무결성 추적과 감사 이력을 동시에 보장합니다.

```text
[통합관리 앱]   [관리인용 앱]   [임대인용 앱]   [임차인용 앱]   [공동계약용 앱]
      │               │               │               │               │
      └───────────────┴───────────────┼───────────────┴───────────────┘
                                      ▼
               [공유 도메인 객체 계층 (Building, Room, Contract, Pay, User...)]
                                      │  save() / delete() / find()
                                      ▼
         ┌─────────────────────────────────────────────────────────┐
         │       공동 실시간 JSON Data Pool (data/<pool>/*.json)    │
         │  buildings.json · rooms.json · contracts.json ·         │
         │  payments.json  · users.json · outlays.json · ...       │
         └─────────────────────────────────────────────────────────┘
```

---

## 4. 🎯 기능 및 용도별 마이크로 앱 분리 원칙 (Role & Purpose Separation)

하나의 비대한 만능 앱에 모든 역할 화면과 API를 혼합하지 않고, **공동 Data Pool 위에서 사용자 역할과 업무 목적(기능/용도)에 따라 별개의 앱 객체로 명확히 분리**하여 구축합니다.

- **통합관리용 앱** (예: `rent3`): 공동 Data Pool 객체 현황 및 실시간 JSON 동기화 상태 관제, 전체 워크플로우 통합 모니터링 및 역할별 앱 런처 허브
- **관리인용 앱** (예: `rent3_admin`): 플랫폼 운영 관리인 전용 가맹 승인, 월 SaaS 이용료 정산, 전 지점 통계, 법정 신고 마감 감시
- **임대인용 앱** (예: `rent3_lessor`): 건물주 전용 보유 건물·호실 관리, 공실 게시, FIFO 월세 수납/문자 파싱, 운영 경비·세무 결산, 시설·퇴실 정산
- **임차인용 앱** (예: `rent3_tenant`): 세입자·예비임차인 전용 공실 탐색/투어 신청, 납부 조회/결제, 도어락/주차 등록, A/S 요청, 세액공제 증명서 발급
- **협업/특수 기능 전용 앱** (예: `rent3_contract`): 임대인과 임차인이 대면/비대면으로 함께 화면을 보며 실시간으로 검증·작성·서명하는 중립 공동 임대계약 전용 앱

---

## 5. 🛡️ 거버넌스 및 품질 준수 표준

1. **5역할 Skill-First 개발 사이클**: 코드 작성 전 반드시 `.agents/skills/basesaasapp-governor/SKILL.md` 및 도메인별 `SKILL.md`에 따라 **Advisor ➔ Librarian ➔ Tutor ➔ Editor ➔ Roommate** 절차를 준수합니다.
2. **Resilient UX 표준**: 브라우저 기본 `alert()` 사용을 금지하며, `/shared/ui/bass.js`의 4색 상태 배너(`Bass.updateAppAlert`)와 이중 클릭 방지 락(`Bass.withLock`)을 적용합니다.
3. **공식 운영 매뉴얼 필수화**: 분리된 모든 개별 앱마다 `frontend/manual.html`을 구비하고 A4 인쇄(`@media print`)를 지원합니다.

