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
- 상태: sent(발행자 확인·링크) → [상대방이 링크에서 휴대폰 뒤 4자리 + 비밀번호 설정 = `POST /contract/join`, 계약 진행용 로그인 `k<계약id>` 생성, 링크 만료] → signed(로그인 상태로 동의 `POST /contract/mine/sign`) → active(발행자 납부 확인) | cancelled.
- 체결 전 상대방은 링크 없이 `POST /login` 에 **휴대폰 번호 + 비밀번호**로 들어와 `contract.html`(`/contract/mine`)에서 이어 간다. 체결되면 그 비밀번호 해시를 최종 아이디(건물 코드 / 코드+호실)로 옮기고(must_change=False) 진행용 로그인은 닫는다 → `POST /contract/mine/continue` 로 최종 계정 세션.
- `POST /login`: 아이디, 또는 휴대폰 번호(숫자·하이픈만)면 최종 계정(임차인 Room.phone, 임대인 가맹 계약 party_phone) → 진행 중 계약 순으로 비밀번호를 맞춰 본다.
- membership(관리인→임대인): 가맹비 확인 시 Building 생성(없으면) + core User(branch_admin, 아이디=건물 코드) + LessorAccount(임시 비밀번호).
- lease(임대인→임차인): 잔금 확인 시 Room 입주 처리(이름·전화·입주일·금액) → 임차인 계정 동기화(새 임차인이면 새 코드).
- 링크 토큰은 sha256 해시만 저장. 재발급하면 이전 링크 무효. 서명 실패 10분 5회면 잠금.
- 납부 확인은 signed 상태에서만. active 는 취소 불가.
- 비밀번호 분실(reset, active 계약만): 새 임시 비밀번호 + 새 링크(이전 링크 무효), must_change=True. lease 는 계약 임차인이 아직 그 호실에 살 때만.
- 비밀번호 변경: `/account/password` 는 언제든 (관리인 8자, 임대인·임차인 6자 이상). 관리인 분실은 `scripts/set_password.py`.
- 로그인 토큰(12시간)은 core 가 상태 없이 서명하므로 초기화해도 이미 발급된 토큰은 만료 때까지 유효하다.
- core 기본 계정(admin/chic/sunho/sinbi)의 고정 비밀번호 1212 는 폐기 — BASS_SEED_PASSWORD 또는 무작위(로그 1회). 남아 있으면 시드 때 교체.
- core 의 branch_admin 이라도 LessorAccount(가맹 체결) 없으면 rent4 임대인 아님.
- 관리인: 서버 콘솔 `scripts/admin_link.py` → 설정 링크(setup.html, 토큰 해시 저장) + 임시 비밀번호 10자 → 첫 로그인 변경(8자 이상) 전에는 관리 API 403. 다시 발급하면 이전 링크·비밀번호 무효. AdminAccount 기록이 없는 관리인(BASS_SEED_PASSWORD 시드)은 변경 강제 없음.

## 로그인 · 권한
- **링크 = 첫 로그인 (임시 비밀번호는 사용자에게 보이지 않는다)**: 계정이 생기거나 초기화되면 내부적으로 무작위 임시 비밀번호를 걸어 두되, 화면·API 응답에 내보내지 않는다.
  - 관리인 `setup.html` → `POST /setup/start`, 임대인·임차인 계약 링크 `contract.html` (active) → `POST /contract/start`, 기존 임차인 안내 링크 `invite.html` → 휴대폰 뒤 4자리 확인 + `POST /invite/start`. 응답은 로그인 토큰 + 이동할 페이지(admin.html / 임대인.html / 임차인.html).
  - 링크 로그인은 must_change 동안만 된다. **비밀번호를 정하는 즉시 그 계정의 모든 링크(계약 token_hash · invite_hash · 관리인 link_hash)를 지워 만료**시킨다 → 404, 로그인 페이지로.
  - **링크 (재)발급은 '🔑 비밀번호 초기화' 하나뿐** (본인 요청·분실·계약 정리·처음 안내 모두). 계약 relink, 임차인 invite 동작은 없앴다. 체결 전 링크를 잃으면 계약 취소 후 재발행.
  - must_change 상태의 첫 설정은 현재 비밀번호 없이 `/account/password` (링크로 받은 세션이 본인 확인). 그 뒤 변경은 현재 비밀번호 필수.
- 로그인은 core `/auth/login` 하나. 로그인 뒤 `GET /me` 가 역할과 첫 페이지를 정한다.
  - superadmin/admin → 관리인 `admin.html` (건물 목록) → `임대인.html?branch=<지점>`
  - branch_admin + LessorAccount → 임대인 `임대인.html` (자기 지점 고정, 다른 branch 요청은 403, 비밀번호 변경 전 403)
  - user + TenantAccount → 임차인 `임차인.html` (자기 호실만)
- 임차인 계정: 아이디 = 건물 코드 + 호실(`chic603`). 거주 호실마다 시작·Excel 반영 때 자동 생성.
  - 첫 비밀번호 = 임대인과 같은 8자 임시 비밀번호(`temp_password`, 0/O·1/l/I 제외). 임대인 화면에는 보이지 않는다 — '📨 안내 링크'(invite, 그대로) 또는 '🔑 비밀번호 초기화'(reset, 새 임시 비밀번호)가 안내 링크를 만들고, 임차인은 링크에서 휴대폰 뒤 4자리 확인 후 받는다(10분 5회 실패 잠금). 첫 로그인에 변경 필수 — 변경 전 `/tenant/me` 는 403.
  - 안내 링크 토큰은 해시만 저장(`invite_hash`). 새 링크·재발급·임차인 변경·퇴거 때 이전 링크 무효. 사용 중 계정에는 invite 불가(분실이면 reset).
- 링크 보내기 창(rent4.js `sendLink`): 안내 문구 자동 복사 + navigator.share(휴대폰 카카오톡 등) + `sms:` 등록 번호 + 복사.
  - 새 비밀번호 6자 이상, 같은 숫자 반복 금지. 변경하면 임시 비밀번호는 pool 에서 지운다.
  - 호실의 임차인(이름+전화 해시)이 바뀌면 계정 초기화, 퇴거·공실이면 잠금(무작위 비밀번호).
- 임차인 화면에는 입주월 이전 기록과 전화번호를 보여 주지 않는다.

## 링크 · 카카오/구글 (현재 방식 — 위의 링크 관련 규칙보다 우선)
- **문자 발송은 쓰지 않는다** (sms.py · /otp · password.html · 텔레그램 · 계약 안내 문자 모두 삭제). 링크는 대면 · 카카오톡 등 · 채팅창으로 전달한다.
- 계약 시작 링크: 계약 발행(`POST /admin|lessor/contracts`) 응답의 `link`(reset.html?t=, 해시 `token_hash`, 24시간 `link_expires`). 다시 만들기 = 계약 action `link`(이전 링크 무효). 링크에서 비밀번호를 정하면(`POST /reset/set`) 계약 진행용 로그인 `k<id>` + 링크 만료 → contract.html. 이후 휴대폰 번호 + 비밀번호로 로그인.
- `/reset/view`·`/reset/set` 은 임차인 재설정 링크를 먼저, 아니면 계약 시작 링크를 본다 (같은 화면).
- 계약 동의 전 본인 확인: 카카오/구글 OAuth(`backend/oauth.py`, state 해시 10분). 키가 하나라도 설정되면 `verified_at` 없이는 동의 불가.
- 임차인 비밀번호 초기화 (대면 계약 마무리 · 분실): 임대인 '🔑 비밀번호 초기화' → `POST /lessor/accounts/{room}/reset` 이 이전 비밀번호를 즉시 막고 한 번만 쓰는 재설정 링크(`reset.html?t=`, 해시 저장 `invite_hash`, 24시간 `invite_expires`)를 만든다. 임대인은 링크를 채팅(카카오톡 등)으로 보내고, 임차인은 `POST /reset/set` 으로 새 비밀번호(해시 저장)를 정하면 바로 로그인 + 링크 만료. 자기 건물 호실만, 임차인이 바뀌면 링크 무효.
- 채팅창 (`backend/chat.py`): 로그인한 화면은 5초마다 `GET /chat/inbox` (= 접속 중 표시, 20초). 비밀번호 초기화 때 임차인이 접속 중이면 재설정 링크를 채팅 메시지(`messages`)로 보내고(`delivered: "chat"`), 아니면 보내기 창(카카오·문자 등)으로 대신한다. 새 메시지는 채팅창이 자동으로 열린다. 비밀번호 초기화는 로그인 토큰을 끊지 않으므로 접속 중인 임차인은 채팅을 계속 받는다.
- 아이디·비밀번호 찾기 (`backend/finder.py`, `find.html`): 구글 본인 확인(oauth purpose=find) → **미리 연결해 둔 구글 계정의 사람으로 고정** → 10분 찾기 권한(토큰 해시, 5회 틀리면 잠금, 틀린 칸은 알려 주지 않음). 아이디 찾기 = 이름·전화·부동산(임차인 '건물+호실', 임대인 '건물') → 아이디 → `index.html#id=` 로 채우고 비밀번호 칸에 커서. 비밀번호 찾기 = 아이디까지 확인 → 새 비밀번호 → 바로 로그인, 권한·링크 만료. 구글 연결은 로그인 뒤 상단 '🔗 구글 연결'(link, back=account).
- 로그인 '아이디 저장': 아이디만 그 기기 localStorage(`rent4.savedId`)에, 비밀번호는 저장하지 않는다.
