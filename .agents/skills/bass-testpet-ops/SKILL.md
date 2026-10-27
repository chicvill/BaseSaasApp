---
name: bass-testpet-ops
description: Operational business rules, domain invariants, 5-role collaboration governance, and verification instructions for 테스트 펫 (apps/testpet).
---

# 🐶 BaseSaasApp 테스트 펫 (testpet) 운영 거버넌스 스킬

본 스킬은 `테스트 펫` 마이크로 SaaS 앱의 비즈니스 불변 규칙과 5역할 협업 사이클을 준수하기 위한 공식 운영 명세서입니다.

---

## 1. 🌟 5역할 협업 개발 거버넌스 (Five-Role Governance)

모든 요구사항 반영 시 다음 5가지 페르소나 단계를 순차적으로 통과해야 합니다:

1. **🧭 Advisor (자문가)**: 나침반 3원칙(수동 대비 간소화, 보안 리스크 최소화, 1분 내 추적성)으로 오버엔지니어링 제동.
2. **📚 Librarian (사서)**: 테스트 펫 관련 업무 규칙 및 외부 규격 사전 조사 (선행 팩트체크 원칙).
3. **🎓 Tutor (튜터)**: 도메인 산출식 및 manual.html 매뉴얼 실시간 동기화.
4. **🛠️ Editor (엔지니어)**: Kernel Obj(domain.py) 기반 도메인 구현 및 단위 테스트 무결성 보장.
5. **🤝 Roommate (동거인)**: 검증 통과 확인, 진도 요약 및 배포/커밋 완료.

---

## 2. 🏛️ 도메인 비즈니스 불변 규칙 (Domain Invariants)
- 모든 엔티티는 `core.kernel.Obj` 를 상속받아 스키마리스 WAL로 영구 보존됩니다.
- 상태 변경 시 이력 로그(`log()`)가 자동으로 기록되어 감사 추적이 가능해야 합니다.

---

## 3. 🚀 자가 업그레이드 검증 (Self-Upgrading)
- 코드 수정 후 `apps/testpet/tests/test_domain.py` 단위 테스트를 반드시 실행하여 100% 통과를 확인합니다.
