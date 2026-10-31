---
name: bass-rent4-ops
description: Business rules for the rent4 app (bank deposit SMS → rent ledger knowledge pool, Excel ledger import, tenant payment lookup). Use when changing apps/rent4.
---

# Rent4 운영 규칙

## Knowledge Pool
- 위치: `data/rent4_pool/` (환경변수 `RENT4_POOL_DIR` 로 교체 가능). git 제외 — 임차인 이름·전화번호 포함.
- 컬렉션: `buildings` · `rooms` · `payments` · `marks`(공실/미납/전입/전출 표기) · `bank_messages`(반영한 문자 원문) · `tenant_accounts`.
- 모든 기록은 건물(`branch`, core.auth 지점 코드) 단위다. 호실 번호는 건물 안에서만 유일하다.
- 건물: chic=시크빌(main) · sunho=선호아트빌(sunho) · sinbi=신비주택(sinbi). 시작 때 없으면 만든다.
- 주민번호는 어떤 컬렉션에도 저장하지 않는다. Excel 가져오기에서 3번째 칸을 읽지 않는다.

## Excel 수납부 가져오기 (`backend/excel_import.py`)
- 시트: 9개 정보 칸 + 월마다 [월일, 임차료, 관리비]. 월은 헤더 첫 라벨부터 순서대로 증가 (라벨 오기 '2030.09' 는 경고만).
- 입금 연도: 11·12월분을 1·2월에 내면 이듬해, 1·2월분을 11·12월에 내면 전년.
- 후불 호실의 입금일이 귀속월보다 2개월 이상 앞서면 '오기 의심' 플래그.
- 시트 하단 합계 행과 월별 임차료·관리비 합계를 대조해 결과에 표시.
- 재가져오기: 해당 연월의 Excel 출처만 교체, bank/manual 기록은 보존하고 그 연월의 Excel 행은 건너뜀.

## 입금 문자 → 수납 (`backend/bank_parser.py`, `backend/ledger.py`)
- 금액: '입금' 바로 뒤 금액 우선, 잔액 뒤 금액은 제외. 날짜가 오늘 이후면 전년.
- 호실 매칭: 입금자명 속 호실 번호 > 세입자명 일치/포함. 여러 호실이 맞으면 임대인 확인.
- 귀속월: FIFO — 미납 → 이번 달 → 다음 달. 금액이 월 청구액(임차료+관리비)의 정확한 배수면 '반영 준비', 아니면 '금액 확인'.
- 중복: (입금일, 시각, 금액, 입금자 후보) 지문이 이미 있으면 반영하지 않는다.
- AI(Gemini)는 규칙으로 금액·입금자를 못 찾은 문자에만 쓴다. 키가 없으면 규칙 결과 그대로.
- 미리보기(preview)는 저장하지 않고, 확정(commit)만 저장한다.

## 월 상태
paid · unpaid(지난달까지 기록 없음) · due(이번 달) · move_in(입주월, 미납 아님) · vacant · move_out · prior(현 임차인 입주 전).

## 계약 상호 확인 → 계정 (`backend/contracts.py`)
- 상태: sent(발행자 확인·링크) → signed(상대방이 링크에서 휴대폰 뒤 4자리 확인 + 동의) → active(발행자 납부 확인) | cancelled.
- membership(관리인→임대인): 가맹비 확인 시 Building 생성(없으면) + core User(branch_admin, 아이디=건물 코드) + LessorAccount(임시 비밀번호).
- lease(임대인→임차인): 잔금 확인 시 Room 입주 처리(이름·전화·입주일·금액) → 임차인 계정 동기화(새 임차인이면 새 코드).
- 링크 토큰은 sha256 해시만 저장. 재발급하면 이전 링크 무효. 서명 실패 10분 5회면 잠금.
- 납부 확인은 signed 상태에서만. active 는 취소 불가.
- 비밀번호 분실(reset, active 계약만): 새 임시 비밀번호 + 새 링크(이전 링크 무효), must_change=True. lease 는 계약 임차인이 아직 그 호실에 살 때만.
- 비밀번호 변경: `/account/password` 는 언제든 (관리인 8자, 임대인·임차인 6자 이상). 관리인 분실은 `scripts/set_password.py`.
- 로그인 토큰(12시간)은 core 가 상태 없이 서명하므로 초기화해도 이미 발급된 토큰은 만료 때까지 유효하다.
- core 기본 계정(admin/chic/sunho/sinbi)의 고정 비밀번호 1212 는 폐기 — BASS_SEED_PASSWORD 또는 무작위(로그 1회). 남아 있으면 시드 때 교체.
- core 의 branch_admin 이라도 LessorAccount(가맹 체결) 없으면 rent4 임대인 아님.

## 로그인 · 권한
- 로그인은 core `/auth/login` 하나. 로그인 뒤 `GET /me` 가 역할과 첫 페이지를 정한다.
  - superadmin/admin → 관리인 `admin.html` (건물 목록) → `lessor.html?branch=<지점>`
  - branch_admin + LessorAccount → 임대인 `lessor.html` (자기 지점 고정, 다른 branch 요청은 403, 비밀번호 변경 전 403)
  - user + TenantAccount → 임차인 `tenant.html` (자기 호실만)
- 임차인 계정: 아이디 = 건물 코드 + 호실(`chic603`). 거주 호실마다 시작·Excel 반영 때 자동 생성.
  - 첫 비밀번호 = 무작위 6자리 코드 (임대인 '임차인 계정' 탭에서 확인·재발급). 첫 로그인에 변경 필수 — 변경 전 `/tenant/me` 는 403.
  - 새 비밀번호 6자 이상, 같은 숫자 반복 금지. 변경하면 코드는 pool 에서 지운다.
  - 호실의 임차인(이름+전화 해시)이 바뀌면 계정 초기화, 퇴거·공실이면 잠금(무작위 비밀번호).
- 임차인 화면에는 입주월 이전 기록과 전화번호를 보여 주지 않는다.
