---
name: basesaasapp-governor
description: Master operations, architectural standards, and Skill-First governance rules for the BaseSaasApp object-oriented micro-SaaS platform. Use this skill when creating new apps, migrating legacy apps, or modifying platform core capabilities.
---

# BaseSaasApp Master Platform Governor Skill

본 스킬은 50~100개 마이크로 SaaS 앱을 하나의 통일된 체계로 관리하기 위한 **플랫폼 총괄 헌법이자 거버넌스 엔진**입니다.

---

## 1. 🌟 5역할 협업 기반 Skill-First 개발 원칙 (절대 규칙)

새로운 기능을 추가하거나 기존 비즈니스 로직을 변경할 때, **코드를 먼저 수정하는 것은 엄격히 금지**되며 반드시 아래 5역할 페르소나 협업 사이클을 순서대로 통과해야 합니다.

```text
[요구사항 발생]
       │
       ▼
[1] 🧭 Advisor (자문가)
    • 방향성 심사 & 나침반 3원칙 검증:
      ① 수동 엑셀보다 단순한가? ② 외부 API/보안 리스크가 최소인가? ③ 1분 내 추적 가능한가?
    • 과도한 설계/복잡도(오버엔지니어링) 즉시 제동
       │
       ▼
[2] 📚 Librarian (사서)
    • 도메인 규칙 및 외부 팩트(법령, 세율, 공식 규격) 선행 조사 (Librarian 선행 원칙)
    • .agents/skills/bass-<app_id>-ops/SKILL.md 비즈니스 규칙 개정
       │
       ▼
[3] 🎓 Tutor (튜터)
    • 비즈니스 산출 원리 분해 및 공식 운영 매뉴얼(manual.html) 동기화
    • 눈높이 해설 및 사용자 인터페이스 안내 구성
       │
       ▼
[4] 🛠️ Editor (엔지니어)
    • BaseSaaSApp 및 Kernel Obj(domain.py) 비즈니스 로직 구현
    • API 라우터(api.py) 및 단위 테스트(test_domain.py) 작성
       │
       ▼
[5] 🤝 Roommate (동거인)
    • 5단계 자동 검증 파이프라인(verify_system.py) 100% 통과 확인
    • 진도 요약 및 Git 커밋, 다음 실행 과제 제시
```

### 왜 5역할 협업인가?
1. **코드-명세 불일치(Drift) 및 환각(Hallucination) 원천 차단**: Librarian의 선행 팩트체크 없이는 코딩이 시작되지 않습니다.
2. **오버엔지니어링 방지**: Advisor의 나침반 3원칙이 불필요한 복잡성과 아키텍처 비대화를 실시간으로 차단합니다.
3. **사용자 중심 투명성**: Tutor의 매뉴얼 동기화를 통해 코드만 돌아가는 블랙박스가 아닌 투명한 서비스가 됩니다.
4. **자율적 자가 업그레이드(Self-Upgrade)**: Editor와 Roommate가 검증 파이프라인을 통해 무결성을 입증한 뒤에만 배포를 완료합니다.

---

## 2. 2단계 객체화 아키텍처 (Two-Tier OOP Standard)

1. **계층 1. 시스템 레벨 객체화 (`BaseSaaSApp`)**:
   - 모든 앱은 `apps/<app_id>/app.py` 에 `BaseSaaSApp` 을 상속한 클래스 1개로 정의됩니다.
   - 메타데이터(`app_id`, `app_name`, `icon`, `category`, `features`, `subdomains`), `get_router()`, `on_startup()`, `health_check()` 구현.
   - 게이트웨이는 앱 내부 구현을 알 필요 없이 제로터치로 자동 마운트합니다.

2. **계층 2. 도메인 레벨 객체화 (`Kernel Obj`)**:
   - 모든 비즈니스 엔티티는 `core.kernel.Obj` 를 상속받습니다.
   - 스키마리스 WAL 저장소(`Store`)에 JSON-KV 형태로 영구 보존되므로 DDL 마이그레이션이 필요 없습니다.
   - 스스로 저장(`save()`), 조회(`get()`, `find()`), 삭제(`delete()`), 이력 로깅(`log()`)을 수행합니다.

---

## 3. Resilient UX 및 알림 배너 표준

브라우저 기본 `alert()` 사용을 금지하며, `Bass.updateAppAlert` 4색 배너를 준수합니다:
- ⚠️ **황색 (`warning`)**: 이미 처리됨 / 중복 실행 방지 / 비어 있음 등 현실 안내.
- ⚖️ **주황색 (`diff`)**: 입력 금액과 요금 간 차액(`difference`) 실시간 안내.
- ❌ **적색 (`error`)**: 엔티티 부재(`NotFound`), 권한 부족, 시스템/격리 오류.
- ✅ **녹색 (`success`)**: 정상 처리 완료 및 다음 행동 안내.

버튼 클릭 시 반드시 `Bass.withLock(btn, async () => { ... })` 을 사용하여 **이중 클릭 차단 및 에러 시 버튼 상태 복원**을 보장해야 합니다.

---

## 4. 공식 운영 매뉴얼 (`manual.html`) 표준

- 모든 앱의 프론트엔드 루트에 번들리스 `manual.html` 이 반드시 존재해야 합니다.
- 상단에 `[🖨️ 매뉴얼 인쇄 / PDF 저장]` 버튼을 두고 `@media print` A4 고대비 흑백 인쇄를 지원해야 합니다.
