---
name: bass-rent4-ops
description: Business rules for the rent4 app (bank deposit SMS → rent ledger knowledge pool, Excel ledger import, tenant payment lookup). Use when changing apps/rent4.
---

# Rent4 운영 규칙

## Knowledge Pool
- 위치: `data/rent4_pool/` (환경변수 `RENT4_POOL_DIR` 로 교체 가능). git 제외 — 임차인 이름·전화번호 포함.
- 컬렉션: `rooms` · `payments` · `marks`(공실/미납/전입/전출 표기) · `bank_messages`(반영한 문자 원문).
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

## 권한
- 임대인: core.auth `branch_admin`(자기 지점 호실만) 또는 `superadmin`.
- 임차인: 호실 + 휴대폰 뒤 4자리. 10분에 5회 실패하면 해당 호실 조회 잠금. 입주월 이전 기록과 전화번호는 보여 주지 않는다.
