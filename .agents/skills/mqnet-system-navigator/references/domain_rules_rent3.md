# MQnet Domain Rules & Business Logic (Rent3 Core)

`apps/rent_dist/` (`_rent3_core` 및 5개 역할 앱: `rent3`, `rent3_admin`, `rent3_lessor`, `rent3_tenant`, `rent3_contract`)은 실제 부동산 임대차 현장의 실무 규칙을 반영한 객체지향 자산·계약·수납 관리 엔진입니다.

---

## 1. 선납 / 후불 및 체납 판정 원칙
- **후불 입주월 비과세 / 익월 청구**: 월세가 후불인 경우 입주 당월에는 청구하지 않으며 다음 달부터 전월 사용료를 청구합니다.
- **가짜 체납(Ghost Overdue) 차단**: 전입일(`move_in_date`) 이전과 퇴실 이후 기간은 절대 청구하지 않습니다.
- **선수금 불가**: 청구 미납 합계를 초과하는 선수금 입력은 차단합니다.

## 2. 스마트 은행 문자 파싱 및 FIFO(선입선출) 수납
- **문자 파싱 (`_rent3_core/billing.py` `SmsDepositParser`)**: 통장 잔액·계좌번호·전화번호·시각을 마스킹한 뒤 입금액(`원` / `만원` 단위 포함), 호실번호, 입금자명(계약 임차인 또는 송금자명), 입금일을 추출합니다.
- **FIFO 순차 상계 (`FifoPaymentEngine`)**: 가장 오래된 미납월부터 순차 상계하며, 중복 입금은 멱등성 키로 차단합니다.

## 3. 부가세(VAT) 및 세무
- 주거용 원룸: 면세 (VAT 0원).
- 상가/사무실 또는 세입자 세금계산서 요청 호실: 공급가액 + 10% VAT 분리 계산, 국세청 홈택스 30컬럼 일괄발급 CSV 및 5월 종소세 CSV 지원.

## 4. 단일 진입점 정규화 & 공동 JSON Data Pool
- 지점 ID 에일리어스(`normalize_branch_id`)는 진입점에서 단일 Canonical ID로 정규화합니다.
- 모든 객체(`PoolObj`)는 `data/rent3_pool/*.json`에 원자적으로 저장되며 5개 앱이 실시간 공유합니다.
- 상세 계약·전출정산·보안 불변식은 `.agents/skills/bass-rent3-ops/SKILL.md`를 따릅니다.
