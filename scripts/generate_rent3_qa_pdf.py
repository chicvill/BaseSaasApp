"""
scripts/generate_rent3_qa_pdf.py — Rent3 5대 페이지 통합 QA 테스트 체크리스트 PDF 생성기.

특징:
  - 임대인 페이지 → 임차인 페이지 → 관리자 페이지 → 스마트 계약 → 포털·통합 인증·Data Pool 허브 순으로 구성
  - A4 가로(Landscape) 3열 핵심 구조: 테스트 항목(기능) / 테스트 방법 / 기대되는 결과 (+ 체크박스 · ID · 메모)
  - 모든 검증 항목마다 클릭 가능한 인터랙티브 AcroForm 체크박스(Checkbox)와 메모 입력란 탑재
  - Chrome, Edge, Adobe Acrobat에서 직접 체크·입력 후 저장 가능하며 A4 가로 인쇄 최적화
"""
from __future__ import annotations

import os
import shutil
from typing import Any, Dict, List
from xml.sax.saxutils import escape as xml_escape

from reportlab.lib import colors  # type: ignore[import-untyped]
from reportlab.lib.pagesizes import A4, landscape  # type: ignore[import-untyped]
from reportlab.lib.styles import ParagraphStyle  # type: ignore[import-untyped]
from reportlab.lib.units import mm  # type: ignore[import-untyped]
from reportlab.pdfbase import pdfmetrics  # type: ignore[import-untyped]
from reportlab.pdfbase.ttfonts import TTFont  # type: ignore[import-untyped]
from reportlab.platypus import (  # type: ignore[import-untyped]
    CondPageBreak,
    Flowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT_MAIN = os.path.join(ROOT, "Rent3_QA_Test_Checklist.pdf")
OUT_WEB = os.path.join(ROOT, "apps", "rent_dist", "rent3", "frontend", "Rent3_QA_Test_Checklist.pdf")
PAGE_SIZE = landscape(A4)


def register_korean_fonts() -> tuple[str, str]:
    reg_path = r"C:\Windows\Fonts\malgun.ttf"
    bold_path = r"C:\Windows\Fonts\malgunbd.ttf"
    if os.path.exists(reg_path) and os.path.exists(bold_path):
        pdfmetrics.registerFont(TTFont("Malgun", reg_path))
        pdfmetrics.registerFont(TTFont("MalgunBd", bold_path))
        return "Malgun", "MalgunBd"
    raise FileNotFoundError("맑은 고딕(malgun.ttf) 폰트를 찾을 수 없습니다.")


class InteractiveCheckBox(Flowable):
    """PDF 뷰어에서 직접 클릭하여 체크/해제할 수 있는 AcroForm 체크박스 Flowable."""

    def __init__(self, field_name: str, tooltip: str = "", size: int = 12):
        super().__init__()
        self.field_name = field_name
        self.tooltip = tooltip
        self.size = size
        self.width = size + 4
        self.height = size + 4

    def draw(self):
        self.canv.acroForm.checkbox(
            name=self.field_name,
            tooltip=self.tooltip,
            x=2,
            y=2,
            size=self.size,
            checked=False,
            buttonStyle="check",
            shape="square",
            fillColor=colors.white,
            borderColor=colors.HexColor("#2563EB"),
            textColor=colors.HexColor("#0F172A"),
            borderWidth=1,
            borderStyle="solid",
            fieldFlags="",
            forceBorder=True,
            relative=True,
        )


class InteractiveNoteField(Flowable):
    """PDF 뷰어에서 항목별 테스트 메모/결과를 직접 타이핑할 수 있는 AcroForm 텍스트필드."""

    def __init__(self, field_name: str, width: float = 60.0, height: float = 15.0, tooltip: str = ""):
        super().__init__()
        self.field_name = field_name
        self.box_width = round(width)
        self.box_height = round(height)
        self.width = width
        self.height = height
        self.tooltip = tooltip

    def draw(self):
        self.canv.acroForm.textfield(
            name=self.field_name,
            tooltip=self.tooltip,
            value="",
            x=0,
            y=0,
            width=self.box_width,
            height=self.box_height,
            fillColor=colors.HexColor("#F8FAFC"),
            borderColor=colors.HexColor("#CBD5E1"),
            textColor=colors.HexColor("#0F172A"),
            borderWidth=1,
            fontSize=8,
            fieldFlags="",
            forceBorder=True,
            relative=True,
        )


# 각 항목: (항목 ID, 테스트 항목(기능), 테스트 방법, 기대되는 결과)
SECTIONS: List[Dict[str, Any]] = [
    {
        "code": "LES",
        "title": "1. 임대인 페이지 (rent3_lessor)",
        "url": "http://localhost:8000/rent3_lessor/",
        "accent": "#059669",
        "bg": "#D1FAE5",
        "items": [
            (
                "LES-F01",
                "기본 지점 헤더 & 4대 KPI 카드",
                "임대인 계정(예: 시크빌 본점 chic)으로 로그인해 /rent3_lessor/ 초기 화면 진입",
                "건물명·소유자 계정명·현재 시각·알림 아이콘이 표시되고, KPI 스트립에 당월 목표액·수납액(달성률 바)·미납액(납부 예정 분리 배지, 가짜 체납 0원)·가동률이 정확히 출력됨",
            ),
            (
                "LES-F02",
                "호실 원장 카드 & 월별 상태 칩",
                "초기 [수납 현황] 화면에서 입주 호실(완납/미납)과 공실 호실 카드 확인",
                "후불 입주월은 EXEMPT_POSTPAID(비과세) 파란 칩, 납입일 전 미입금은 납부 예정, 납입일 경과 미입금은 미납 붉은 칩, 완납은 녹색 칩으로 구분되며 전입 전·퇴실 후 월은 청구되지 않음",
            ),
            (
                "LES-F03",
                "지점 전환 & 지점별 데이터 격리",
                "타 지점(sunho, sinbi 상가 VAT 포함) 계정 로그인 또는 관리인 원격 지원으로 ?branch=... 전환",
                "헤더 건물명·4대 KPI·호실 목록·부가세(10% 분리) 적용 여부가 해당 지점 기준으로 즉시 교체되고, 타 지점 임대인 토큰으로 다른 지점 수정 시 차단됨",
            ),
            (
                "LES-F04",
                "은행 입금 문자 파싱 & FIFO 수납 + 중복·선수금 차단",
                "입금 문자 입력창에 은행 SMS(잔액·계좌번호·시각·50만원/500,000원·입금자명/송금자명 포함) 입력 후 수납 실행 → 동일 문자 재실행 → 미납 합계 초과 금액 입력",
                "잔액 이후·계좌·시각이 마스킹된 채 금액·날짜·호실이 추출되어 가장 오래된 미납월부터 FIFO 순차 상계됨. 동일 문자 재실행은 409 중복 경고 배너로 차단되고, 미납 초과 선수금은 오류로 차단됨",
            ),
            (
                "LES-F05",
                "직접 FIFO 수납 · 송금 알림 확인/반려 · 납부 무효",
                "호실 수납 창에서 직접 금액 입력 후 수납 → 임차인 이체 알림 [확인]/[반려] → 납부 내역에서 사유 입력 후 무효 처리",
                "직접 수납 및 이체 알림 확인 시 payments.json 반영·미납액/월별 칩 즉시 갱신·영수 문자 초안 생성. 납부 무효는 사유 미입력 시 차단되며 처리 후에도 이력(감사 흔적) 보존",
            ),
            (
                "LES-F06",
                "계약내용 수정 & 현재/다음 계약 조건(next_terms) 분리",
                "호실 카드 > 계약내용에서 조건·청소비·송금자명(최대 3개)·부가세 수정 저장 → [신규 계약]/[연장 계약] 시작 창에서 조건 수정 후 [다음 계약 조건 저장] 클릭",
                "호실 수납 창에는 다음 계약 조건 카드가 없고, 계약 시작 창에서 현재 조건이 먼저 노출됨. next_terms 저장 시 현재 활성 계약은 덮어쓰지 않고, 신규 입주/연장 시작일 도래 시 자동 적용 후 비워짐",
            ),
            (
                "LES-F07",
                "임대매물 등록 · 직거래 공개 토글 · 등기부 확인",
                "[더보기 > 2단계 > 임대매물 등록]에서 신규 호실 추가 → 공실 카드 [직거래 매물 공개/비공개 전환] 토글 → [등기부 확인(등기클린)] 등록",
                "신규 호실이 rooms.json에 저장되고, 공개 토글 변경 즉시 포털(/rent3/)과 임차인 앱 [빈 집 찾기]에 반영됨. 등기부 확인 완료 시 등기클린 배지(90일 신선도 기준) 표시",
            ),
            (
                "LES-F08",
                "중개 계약서 사진 등록 (OCR 채우기 & 5종 JSON 동기화)",
                "[더보기 > 중개 계약서 사진 등록]에서 사진/PDF 첨부(또는 샘플 생성) → OCR 문구 [자동 채우기] → [계약서 사진 보관 및 계약내용 적용] → 호실 카드 [첨부 계약서 보기]",
                "조건 자동 입력 후 적용 시 uploads/contracts/ 보관 + contracts·rooms(공실 마감)·users·payments·ops(RTMS) 5종 JSON 동시 갱신. [첨부 계약서 보기]에서 원본·중개소·SHA-256 해시 및 새 창 열기 확인",
            ),
            (
                "LES-F09",
                "만기 6~2개월 통지 배너 · 전출계획 확인 · 사전 매물(1개월 제한)",
                "[더보기 > 만기 통보] 진입 → D-180~D-60 배너 확인 → [전출계획 확인 요청] 발송 및 유선 회신 기록 → 만기 전출 호실 사전 매물 공개 시도",
                "법정 통지구간 호실의 통지마감일이 경고 배너로 표시되고 ops.json에 기록됨. 만기 전출 시 퇴실 기준일 1개월 전부터만 사전 매물 공개가 허용되며 조기 등록 시 공개일에 자동 공개됨",
            ),
            (
                "LES-F10",
                "연장 재계약 예고 발송 & 법정 5% 상한 검증",
                "전출의사 없음(STAY) 호실에서 연장 조건을 5% 초과 입력해 예고 발송 시도 → 5% 이내 적법 금액으로 재발송",
                "주택임대차보호법 제7조 5% 초과 시 422 오류로 차단됨. 적법 조건 발송 시 임차인 마이홈 연장 예고장에 표시되고, 조기 연장 체결 시 SCHEDULED 예약 유지 후 만기 다음 날 ACTIVE 자동 전환",
            ),
            (
                "LES-F11",
                "전출 정산 (DRAFT 임시저장 → 게시 → 채팅 송금 → 퇴실 확정)",
                "호실 카드 3번째 버튼 [전출 정산] 클릭 → 항목별 금액 입력 → (1) [임차인에게 게시] 미체크 저장(DRAFT) → (2) 체크 후 게시 → 임차인 동의·계좌 접수 후 채팅 카드 [송금 완료 표시]",
                "만기 전 퇴실 월세는 만기일/새 입주 전날까지 (경과일수x월임차료)/30 일할 계산, 청소비는 약정액 이내 제한. DRAFT는 임차인 미노출, 게시 후 송금→수취 확인(또는 7일 자동확정) 시 Room.vacate() 공실/예약입주 자동 전환",
            ),
            (
                "LES-F12",
                "일괄 납입 통보 (D-3 사전통보 · D+1 미납통보)",
                "[더보기 > 일괄 납입 통보]에서 D-3 납입일 통보 및 D+1 미납통보 실행 → 완납 호실에 미납통보 발송 시도",
                "입주일 기준 3일 전 사전통보, 납입일 1일 경과 미납 호실 미납통보가 발송되고, 완납 호실 오발송은 400 오류로 차단됨",
            ),
            (
                "LES-F13",
                "세무 허브 · 부가세/종소세 호실 설정 · 홈택스 CSV",
                "[더보기 > 부가세·소득세 대상 호실 설정] 저장 → 하단 [세무·결산] 허브에서 부가세·종소세·면세사업장현황 확인 → 홈택스 30컬럼 CSV · 5월 종소세 CSV · 납부현황 CSV 다운로드",
                "주거 면세 vs 상가/요청 호실 10% VAT 분리 집계되고, 30컬럼 전자세금계산서 CSV 및 한글 깨짐 없는 UTF-8 BOM 종소세·납부현황 CSV가 즉시 다운로드됨",
            ),
            (
                "LES-F14",
                "공용 운영비(8대 분류 · 참고용 제외) · NOI · 관리비 산출근거",
                "세무 허브 경비 탭에서 8대 표준 분류 지출 저장 + 참고용(감가상각 등) 항목 추가 → [더보기 > 관리비 산출근거] 열기",
                "일반 운영비는 outlays.json 반영 및 지점 경비·NOI 즉시 재계산, reference_only 참고용 항목은 세무 경비 합계에서 자동 제외됨. 관리비 산출근거에 호실당 연/월 금액과 실제 관리비 평균 비교 표시",
            ),
            (
                "LES-F15",
                "세금계산서·현금영수증 발행 & 사업자등록증 촬영(AI 읽기)",
                "[더보기 > 세금계산서·현금영수증 발행] 확인 → [더보기 > 사업자등록증 촬영]에서 임차인 사업자등록증 업로드(AI 동의 체크) 후 저장",
                "사업자등록번호 10자리 체크섬 검증 후 계약(tenant_*)·임차인(business_*) 정보에 동기화되며, 부가세 청구 여부(vat_target)는 임의로 변경하지 않음",
            ),
            (
                "LES-F16",
                "A/S 접수 처리 (기사 배정 & 수리 완료 경비 연동)",
                "[더보기 > A/S 접수]에서 RECEIVED 티켓에 [기사 배정] 클릭 → 임대인 부담 수리비 입력 후 [수리 완료] 클릭",
                "기사 배정 시 임차인에게 안내 채팅 자동 발송, 수리 완료 시 임대인 부담 수리비가 outlays.json 필요경비에 자동 등재되어 NOI에 반영됨",
            ),
            (
                "LES-F17",
                "호실별 1:1 채팅 · 도어락 변경 · 잔금일 등기 확인 후 교부",
                "하단 [채팅]에서 임차인에게 답장(전화 버튼 확인) → 도어락 비번 변경 → 신규 계약 호실의 잔금일 등기부 확인 전/후 [도어락 교부] 실행",
                "채팅은 2.5초 주기 자동 갱신, 도어락 비번 변경은 임차인 마이홈 즉시 동기화. 잔금일 등기 변동 없음 확인 전에는 교부 차단되며, 교부 전까지 임차인 측 도어락·공동현관 비번은 서버 마스킹 유지",
            ),
            (
                "LES-F18",
                "임차인 가입 문자 · 가맹비 증빙 · PIN 수납 허용 · 원격 지원 설정",
                "[더보기]에서 [임차인 앱 가입 안내 문자], [가맹비 납부·세금계산서], [바로가기·간편 비밀번호](기기 수납 허용 시 비번 재확인), [원격 지원 허용], [관리인과 채팅], [이용 매뉴얼] 확인",
                "PIN 로그인에서는 조회·채팅·시설·허용 기기 수납(일 25건·1,000만 원 한도)만 허용되고 수정·삭제 차단. 원격 지원 허용 토글이 관리인 화면에 실시간 반영되고 매뉴얼 A4 인쇄 정상 동작",
            ),
        ],
    },
    {
        "code": "TEN",
        "title": "2. 임차인 페이지 (rent3_tenant)",
        "url": "http://localhost:8000/rent3_tenant/",
        "accent": "#D97706",
        "bg": "#FEF3C7",
        "items": [
            (
                "TEN-F01",
                "내 집(마이홈) 초기 진입 & 본인 계정 격리",
                "입주 임차인 계정(예: 201호 이민수 / 202호 정서연)으로 로그인해 /rent3_tenant/ 진입",
                "본인 호실·성명·미납액(완납/미납)·만기 D-Day 배지가 정상 표시됨. 일반 임차인 계정으로는 타 세입자 목록(/tenants) 조회가 차단되고 본인 데이터만 노출됨",
            ),
            (
                "TEN-F02",
                "월별 FIFO 납부 원장 & 후불 입주월 비과세 확인",
                "[내 집] 화면에서 월별 청구·납부 이력 확인",
                "후불 계약 입주 당월은 EXEMPT_POSTPAID(0원)로 표시되고, 월별 청구/납부 내역 및 완납·납부예정·미납 상태가 임대인 원장과 100% 일치함",
            ),
            (
                "TEN-F03",
                "이체 알림 / 월세 납부 & 납입일 D-3·미납 통보 해소",
                "미납 호실(예: 202호)에서 납입예고/미납통보 배너 [확인] → 이체 알림 전송(또는 온라인 납부) → 임대인 수납 확인 후 재조회",
                "payments.json 반영, 미납액 차감, 연말정산 납부확인서 금액 합산이 즉시 갱신되며, 도착한 납입예고·미납 통보 상태가 RESOLVED_PAID로 자동 해소됨",
            ),
            (
                "TEN-F04",
                "세대 도어락 [보기/숨기기] · 공동현관/Wi-Fi & 차량 등록",
                "[더보기 > 도어락 · 차량] 진입 → 도어락 [보기/숨기기] 토글 → 차량번호 수정 후 [등록] 클릭",
                "기본 마스킹(••••*) 표시이며 잔금 교부 완료 계약만 [보기] 시 실제 비밀번호 표시(임대인 변경분 즉시 일치). 차량번호 등록 시 users.json 갱신 및 임대인 앱 주차 관제 즉시 동기화",
            ),
            (
                "TEN-F05",
                "잔금일 등기부 최종 확인 (balance-registry-check)",
                "잔금 납부 전 마이홈에서 잔금일 등기부 확인 절차 실행(변동 없음 vs 권리 변동)",
                "변동 없음 제출 시 임대인의 도어락 교부(release-keys)가 활성화됨. 권리 변동 감지 시 경고 표시 및 도어락 교부 보류",
            ),
            (
                "TEN-F06",
                "연말정산 17% 월세 세액공제 납부확인서 & A4 인쇄",
                "[더보기 > 연말정산 납부확인서] 열기 → 연간 납입액·17% 세액공제·접수번호·현금영수증 승인번호 확인 → [인쇄] 클릭",
                "영수필 직인 양식이 정상 렌더링되고 인쇄 시 불필요한 버튼이 숨겨져 A4 규격 출력됨. 퇴실 후에도 다음 해 2월 말까지 조회 전용 로그인으로 발급 가능",
            ),
            (
                "TEN-F07",
                "중개 지면계약서 사본 확인 & 원본 다운로드",
                "공인중개사 지면계약 세입자로 로그인 → [더보기 > 중개 계약서 사본] → [계약서 원본 열기/다운로드] 클릭",
                "중개사무소 정보와 첨부 계약서 미리보기 카드가 자동 노출되고, 원본 열기 시 새 탭에서 고해상도 이미지/PDF가 정상 서빙됨",
            ),
            (
                "TEN-F08",
                "만기 6~2개월 전출의사 회신 & 연장 재계약 예고 동의",
                "법정 통지구간 세입자로 [더보기 > 만기 · 연장 안내] 진입 → [전출의사 없음]/[만기 전출 예정] 회신 → 연장 예고장 [조건 확인·동의] 클릭",
                "회신 즉시 임대인 화면에 동기화되고 만기 전출 시 사전 매물 규칙에 연계됨. 연장 예고 조건 동의 시 상태 완료 및 공동 재계약방 원클릭 입장 활성화",
            ),
            (
                "TEN-F09",
                "퇴실 신청 & 채팅 정산 카드 (확인·계좌/서명·이의·수취확인)",
                "[더보기 > 퇴실 정산]에서 퇴실 신청 → 임대인 DRAFT 상태 확인 → 임대인 게시 후 채팅 정산 카드에서 [정산 내역 확인] → 본인 계좌·동의 서명(또는 이의 제기) → 송금 표시 후 [받았습니다] 클릭",
                "DRAFT 중에는 금액·알림 미노출. 게시 후 타인 명의 계좌는 차단되고 본인 계좌·서명 제출 시 임대인 카드에 표시됨. [받았습니다] 클릭 시 퇴실 확정 및 기존 로그인 수단(바로가기·기기) 정리됨",
            ),
            (
                "TEN-F10",
                "A/S 수리 요청 & 임대인과 1:1 채팅",
                "하단 [A/S]에서 분류·제목·사진 입력 후 [임대인에게 수리 요청 보내기] → 하단 [채팅]에서 메시지 전송 및 임대인 답장 대기",
                "ops.json에 RECEIVED 티켓이 생성되어 임대인 A/S 대장에 실시간 노출됨. 채팅방은 2.5초 주기 자동 새로고침되며 안 읽은 채팅은 알림 및 분홍 강조 표시",
            ),
            (
                "TEN-F11",
                "빈 집 찾기(공실 탐색) & 방문문의 · 직거래 계약 이동",
                "하단 [빈 집 찾기] 전환 → 공개 공실·사전공개 배지·등기 클린 배지 확인 → [1:1 방문문의] 전송 및 [직거래 계약]/[열린 계약방 입장] 클릭",
                "임대인이 공개한 공실만 실시간 표시(계약 마감 시 즉시 제거)되며, 방문문의가 임대인 채팅에 도착하고 계약 버튼 클릭 시 /rent3_contract/로 연결됨",
            ),
            (
                "TEN-F12",
                "사업자 정보 · PIN 바로가기 · 원격 지원 · 관리인 채팅 · 집주인 초대",
                "[더보기]에서 [사업자 정보·세금계산서 요청] 저장 → [바로가기·간편 비밀번호] 등록 → [원격 지원 허용] 토글 → [관리인과 채팅] → [집주인에게 안심계약 제안하기]",
                "사업자 정보 저장 시 활성 계약(tenant_*)에 즉시 동기화됨. 원격 지원 허용 토글이 관리인 화면에 실시간 반영되고, 관리인 채팅 및 집주인 초대 링크 생성이 정상 동작함",
            ),
        ],
    },
    {
        "code": "ADM",
        "title": "3. 관리자 페이지 (rent3_admin)",
        "url": "http://localhost:8000/rent3_admin/",
        "accent": "#7C3AED",
        "bg": "#EDE9FE",
        "items": [
            (
                "ADM-F01",
                "전사 포트폴리오 KPI & 네비게이션 · 매뉴얼/QA PDF",
                "총괄 관리인(admin) 로그인(MFA 설정 시 2단계 인증 포함) 후 /rent3_admin/ 진입 → 상단 KPI·하단 4개 탭·[더보기] 메뉴 확인",
                "전사 가맹 건물 수·가동 호실·월 SaaS MRR·전사 총수입·전사 NOI가 표시되고, 모든 탭 전환과 이용 매뉴얼·통합 사용자 매뉴얼 PDF·QA 체크리스트 PDF 링크가 정상 열림",
            ),
            (
                "ADM-F02",
                "가맹 임대인 목록 · 신규 등재 & 1회용 비밀번호 초대 링크",
                "[임대인 가맹·월 가맹비] 탭에서 신규 지점·건물·성명 입력 후 [가맹 임대인 및 건물 등재] → 계정 관리에서 [1회용 비밀번호 초대 링크 발급]",
                "users.json·buildings.json에 동시 등재되어 KPI 건물 수·MRR 즉시 증가. 7일 유효 1회용 초대 링크가 발급되며 저장소(users.json)에는 원문 토큰을 평문 저장하지 않음",
            ),
            (
                "ADM-F03",
                "가맹 심사 — 등기부 원본 대조 & 홈택스 사업자등록증 승인",
                "PENDING_APPROVAL 임대인의 제출 등기부 열람·발급확인번호 원본 대조 기록(registry-verify) → 사업자등록증 홈택스 상태(계속/폐업)·중복 점검 후 승인(bizreg-verify) → ACTIVE 전환",
                "폐업·휴업·오류 번호·타 임대인 중복 사업자번호는 차단됨. 승인 전에는 매물 공개·직거래 계약이 차단되며, ACTIVE 승인 후 정상 활성화됨",
            ),
            (
                "ADM-F04",
                "월 가맹비 일괄 청구 · 요금제 설정 · 납부 정산 & 전자세금계산서",
                "임대인별 월 가맹비 설정 → [월 가맹비 일괄 청구] → 미납(PENDING) 임대인 [납부 등록] → 전자세금계산서 API 접수 또는 홈택스 승인번호 기록",
                "요금제(0~1,000만 원 검증)가 다음 청구에 반영되고 중복 청구는 건너뜀. 납부 등록 시 PAID 전환 및 users.json 갱신, 자동/수동 전자세금계산서 접수 상태 기록",
            ),
            (
                "ADM-F05",
                "전 지점 건물 현황 & 전월세 신고제(RTMS) 관제",
                "[전 지점 건물 현황] 탭 전환 → 지점별 지표 및 RTMS 목록 확인 → 임대인 앱에서 중개 미신고 계약 등록 후 재조회",
                "전 지점 가동률·Cap Rate·NOI·부가세 예수금 비교 표가 출력되고, 확정일자 완료는 녹색 FILED 배지, 중개 미신고 건은 황색 [미신고 D-N일 (PENDING)] 배지로 구분됨",
            ),
            (
                "ADM-F06",
                "채팅 · 전화 요청 접수 & 원격 지원 화면 입장",
                "[채팅·원격 지원] 탭에서 사용자 선택 → 채팅·전화 버튼·[전화 요청 접수] 기록 → (1) 원격 지원 미허용 시 입장 시도 → (2) 사용자 허용 직후 [입장] 클릭",
                "전화 요청 접수가 지원 채팅에 [전화 요청 접수]로 자동 기록됨. 미허용 시 입장 차단되고, 허용 상태에서 입장 시 상단 프록시 배너가 표시된 채 해당 임대인/임차인 앱(지정 호실 포함)으로 진입",
            ),
            (
                "ADM-F07",
                "실시간 JSON Data Pool 탐색기 & 감사 이벤트 스트림",
                "[더보기 > Data Pool 탐색기] 진입 → 8종 JSON 컬렉션 드롭다운 변경 → 하단 최근 20건 감사 스트림 확인",
                "선택한 컬렉션의 디스크 원문·바이트 크기·리비전이 즉시 조회되고 감사 로그가 표시되며, 암호화 대상 민감 필드는 암호문으로 보호됨",
            ),
            (
                "ADM-F08",
                "운영 안전 · 백업 · 비밀번호 6개월 경과 관리",
                "[운영 안전·백업·계정] 탭 진입 → 풀 백업 현황/수동 백업 실행 → 임대인 6개월 비밀번호 경과(due_6m) 배지 및 잠금 해제 확인",
                "백업 파일이 정상 생성·목록화되고, 비밀번호 변경 6개월 경과 임대인은 경고 배지 표시 및 계정 잠금 해제 기능이 정상 작동함",
            ),
            (
                "ADM-F09",
                "시험 계정 도구 & 시험 호실 초기화 도구",
                "[더보기 > 시험 계정 도구] 진입 → 예비 임차인 시험 본인인증 처리(dry-run 확인 후 적용)·비번 재설정·잠금 해제 → 시험 호실 퇴실·재공개 도구 실행",
                "실제 입주자는 보호되고 예비 임차인만 감사 로그와 함께 안전하게 처리되며, 시험 호실이 공실 공개 상태로 깔끔히 복구됨",
            ),
        ],
    },
    {
        "code": "CON",
        "title": "4. 스마트 계약 (rent3_contract)",
        "url": "http://localhost:8000/rent3_contract/  (진위 확인: /rent3_contract/verify.html)",
        "accent": "#DC2626",
        "bg": "#FEE2E2",
        "items": [
            (
                "CON-F01",
                "계약 홈 · 본인인증 · 상대방 호출/응답 & 독점/만료 규칙",
                "임대인·임차인 각자 본인인증 로그인 후 /rent3_contract/ 진입 → 매물 선택·호출(CALLING) → 상대방 [호출 응답 및 공동계약 시작] 클릭 → 동일 호실 제3자 호출 시도",
                "양측이 CONNECTED로 계약방에 연결됨. 진행 호실 중복 호출은 차단(1호실=1계약)되고 임차인 동시 진행은 최대 2건 제한, 무응답 24시간(사전작성 72시간) 후 자동 해제",
            ),
            (
                "CON-F02",
                "채팅 중심 통합 계약방 & 차례 안내 · 알림 패널",
                "연결 직후 통합 채팅방(#chatWorkspace) 확인 → 상단 액션 카드(#wsActionBar)·5단계 스텝바·차례 안내(#turnLine)·전화 버튼·알림 패널 확인",
                "진입 즉시 채팅방이 메인으로 열리고 상단 액션 카드에 현재 조건(보증금·계약금[기본 10%]·월세·관리비·청소비·입주일) 표시. 내 차례/상대 차례 안내와 2점 진행 상태(초록=완료·주황=대기) 실시간 동기화",
            ),
            (
                "CON-F03",
                "[신규] Step 1 상호 신분증 확인 & 등기부 열람·위험 차단",
                "(1) 신분증 미체크 상태 서명 시도 → (2) 양방향 신분증 체크 → (3) 임차인 등기부 PDF 열람·AI 분석(동의 필수·계약당 5회 한도) → (4) 소유자 불일치/압류·경매·신탁 등 입력 → (5) 고액 보증금+근저당 입력",
                "신분증 미체크 시 서명 400 차단. 소유자 불일치 또는 압류·가압류·가처분·신탁·경매·임차권등기 감지 시 즉시 409 CANCELLED 차단. 고액 보증금+근저당은 보증보험(거절 시 무효 특약 자동삽입) 또는 말소 특약 필수",
            ),
            (
                "CON-F04",
                "[신규] Step 2 시설·옵션 실물 점검 & 벽지 메모·사진 촬영",
                "채팅방 액션 카드에서 [옵션 확인·벽지 상태 메모·사진 촬영] 모달 열기 → 5대 시설 체크·벽지 메모·사진 첨부 → 임대인 확인 후 임차인 확인 잠금",
                "양측 확인 완료 시 시설 점검표·메모·사진이 퇴실 정산 비교 원본으로 잠금되고 Step 3으로 자동 전환됨",
            ),
            (
                "CON-F05",
                "Step 3 조건 변경은 제안 → 상대 수락 (일괄 제시 bundle 포함)",
                "(1) 6개 핵심 조건 직접 수정 시도 → (2) 채팅방 조건 일괄 제시(bundle) 또는 개별 [제안](계약금 > 보증금 오류 케이스 포함) → (3) 상대방 [전체 수락]/[수락] 클릭 (특약은 [수정 요청]으로 협의)",
                "직접 수정 불가, 복합 변경값 기준 계약금 > 보증금이면 차단됨. 상대 수락(임차인은 본인인증 필요) 시 Revision 증가 및 양측 기존 동의 초기화됨",
            ),
            (
                "CON-F06",
                "[연장 계약] 법정 5% 상한 검증 & 간소화 즉시 체결",
                "입주 중 호실 연장 계약방(RENEWAL) 진입 → (1) 5% 초과 인상 제안 시도 → (2) 적법 조건으로 양측 확인 체크 → (3) 자필 서명",
                "신규 계약의 신분증·등기부·옵션·계약금(0원) 생략. 5% 초과 인상은 422 차단되며, 적법 조건 서명 시 즉시 체결(도어락 유지)되고 시작일 전이면 SCHEDULED 예약 계약으로 보관됨",
            ),
            (
                "CON-F07",
                "Step 3 [계약 조건 전체 동의] 단추 & 열린 제안 차단",
                "(1) 열린 제안·수정 요청이 남아 있는 상태에서 [계약 조건 전체 동의] 클릭 → (2) 모든 제안 수락/거절·철회 후 임대인·임차인 각자 [계약 조건 전체 동의] 클릭",
                "열린 제안·수정 요청이 하나라도 있으면 전체 동의가 막히고 안내문 표시. 모두 정리된 뒤 양측 동의 완료 시 Step 4 전자서명으로 이동하며 진행 상태는 2점(초록=완료·주황=대기)으로 표시",
            ),
            (
                "CON-F08",
                "Step 4 자필 전자서명 [지우기] & [날인 완료]",
                "사전 4대 필수검증(로그인인증·신분증체크·등기확인·조건합의) 통과 후 서명패드(#sigCanvas)에 날인 → [지우기] 재작성 → 양측 [날인 완료] 클릭",
                "양측 날인 시 신분확인 증적을 포함한 SHA-256 무결성 해시가 즉시 생성되고 Step 5 체결 단계로 진입함",
            ),
            (
                "CON-F09",
                "Step 5 체결 직전 등기부 재열람 · 계약금(10%) 수납 & 봉인 · 진위 확인",
                "양측 서명 후 (1) 체결 직전 2차 등기부 재열람 → (2) 임대인 계좌·own_account_checked 확인 후 [계약금 수납 확인 & 계약 종료] → (3) 표준 전자계약서 [A4 인쇄] 및 /rent3_contract/verify.html 해시 대조",
                "재열람 권리 변동 시 즉시 CANCELLED 차단. 체결 시 contracts·rooms(공실 마감/예약)·users·payments·ops(접수번호 FIX-..., 법적 확정일자 별도 안내) 5종 JSON 동시 갱신 및 verify.html 공개 진위 검증(개인정보 미노출) 일치 확인",
            ),
            (
                "CON-F10",
                "계약방 채팅 접근 통제 & 대화 해시(chat_digest)",
                "(1) Step 4 이후 조건 제안 시도 → (2) 당사자 외 제3자 계정으로 채팅 읽기/쓰기 API 호출 → (3) _events.jsonl 본문 유출 여부 및 체결 계약서 chat_digest 일치 확인",
                "Step 3까지 문의·제안·수정 요청, Step 4 이후 문의만 허용. 당사자 외 접근은 403 차단되고 채팅 본문은 풀 이벤트 로그에 남지 않으며, 체결 시 chat_digest 해시가 기록·일치 표시됨",
            ),
            (
                "CON-F11",
                "서명 후 [조건 다시 열기]",
                "양측 또는 일방 서명 이후 조건을 수정해야 할 때 경고 안내 확인 후 [조건 다시 열기] 클릭",
                "기존 전자서명이 안전하게 초기화되고 Step 3 조건 협의 단계로 돌아가 새 제안 → 상대 수락 절차를 다시 진행할 수 있음",
            ),
            (
                "CON-F12",
                "계약방 채팅 보관 기간 자동 삭제 (취소 90일 · 종료 5년)",
                "취소 후 90일 경과 계약방 및 체결 종료 후 5년 경과 계약 조회 (ACTIVE 진행 계약 비교 포함)",
                "보관 기간 만료 시 로그인·조회 시점에 채팅이 자동 삭제되며(ACTIVE 계약은 삭제 안 함), 해시 변조 경고와 구분되어 [보관 기간이 지나 삭제되었습니다] 안내가 표시됨",
            ),
        ],
    },
    {
        "code": "HUB",
        "title": "5. 포털 · 통합 인증 · Data Pool 허브 (rent3)",
        "url": "http://localhost:8000/rent3/  (Data Pool 허브: /rent3/pool.html)",
        "accent": "#0284C7",
        "bg": "#E0F2FE",
        "items": [
            (
                "HUB-F01",
                "OpenRent 직거래 포털 메인 · 검색/필터 & 민감정보 비노출",
                "/rent3/ 진입 → 검색어·지점·매물 유형·퀵필터(전체 공개 매물/즉시입주 가능) 조합 검색 → 네트워크 탭 공개 목록 API 응답 확인",
                "rooms.json 공개 매물만 실시간 필터링되어 카드에 표시되며, authz.public_room() 적용으로 도어락 비번·계좌 등 ROOM_PRIVATE_FIELDS가 응답에 포함되지 않음",
            ),
            (
                "HUB-F02",
                "월세 적정 예산 계산기 & [+ 매물 등록] / [직거래 계약] 연결",
                "[더보기 > 월세 적정 예산 계산기] 모달에서 세후 월소득 입력·적용 → 상단 [매물 등록] 클릭 → 공실 카드/배너 [계약 화면 열기] 클릭",
                "계산기가 모달로 열려 30% 권장 예산 필터가 즉시 적용됨. [매물 등록]은 임대인 호실 등록 탭으로, 공실 계약 버튼은 /rent3_contract/로 정상 연결됨",
            ),
            (
                "HUB-F03",
                "통합 로그인 · 역할별 자동 분기 · 동의 게이트 & 로그아웃 폐기",
                "통합 로그인 모달에서 admin·임대인·입주 임차인·예비 임차인 각각 로그인 → 최초 동의 화면(필수 미체크 차단 확인) → [로그아웃] 후 뒤로가기 및 기존 토큰 API 재호출",
                "역할별 전용 페이지로 즉시 자동 분기되며 필수 개인정보 동의 없이는 진입 불가. 로그아웃 시 _revoked_tokens.json에 즉시 폐기되어 재사용 시 401 반환 및 세션 가드 자동 안내 동작",
            ),
            (
                "HUB-F04",
                "로그인 보안 — 브루트포스 잠금 · 비번 찾기 · MFA · PIN",
                "(1) 오입력 5회 연속 로그인 시도 → (2) 비밀번호 찾기 본인 확인 재설정 → (3) 관리인 TOTP 2단계 인증 → (4) 간편 비밀번호(PIN) 5회 오입력 잠금 후 정상 ID/PW 로그인",
                "계정 5회(IP 30회) 실패 시 15분 잠금, 본인 확인 후 비번 재설정 정상 동작, 관리인 MFA 검증 통과, PIN 5회 실패 잠금은 정상 ID/PW 로그인 시 자동 해제됨",
            ),
            (
                "HUB-F05",
                "통합 Data Pool 허브 (/rent3/pool.html) 실시간 3초 동기화",
                "/rent3/pool.html 진입 → 8대 JSON 현황 카드·기본 rooms.json 원문·최근 15건 감사 스트림 확인 → 다른 JSON 카드 클릭 → 타 앱 데이터 수정 후 새로고침 없이 3초 대기",
                "카드 클릭 시 활성 테두리와 JSON 원문이 즉시 교체되고, 타 앱 수정 후 새로고침 없이 3초 이내에 상단 Pool vN 리비전·객체 수·_events.jsonl 감사 스트림이 자동 갱신됨",
            ),
            (
                "HUB-F06",
                "공통 UI/UX 표준 (4색 배너 · 모바일 가독성 · 용어 통일 · 알림)",
                "모바일 뷰포트(375~390px)에서 5대 페이지 순회하며 오류 유도, 버튼 클릭, 알림 패널, 메뉴·라벨 확인",
                "브라우저 alert() 미사용, 4색 상태 배너(오류/경고 2초 자동 닫힘) + 이중 클릭 방지(Bass.withLock) 동작. 모든 글자 >= 12px·버튼 높이 >= 40px, 용어 [채팅](앱 내) vs [메시지](SMS) 통일, 안 읽은 알림 분홍 강조 및 딥링크 이동",
            ),
        ],
    },
]


def draw_page_chrome(canvas, doc):
    """모든 페이지 상단 머리글 및 하단 안내/페이지 번호 렌더링."""
    canvas.saveState()
    w, h = PAGE_SIZE
    canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
    canvas.setLineWidth(0.6)
    canvas.line(12 * mm, h - 12 * mm, w - 12 * mm, h - 12 * mm)

    canvas.setFont("MalgunBd", 8.5)
    canvas.setFillColor(colors.HexColor("#334155"))
    canvas.drawString(
        12 * mm,
        h - 9.5 * mm,
        "BaseSaasApp · Rent3 통합 QA 테스트 체크리스트 (임대인 · 임차인 · 관리자 · 스마트 계약 · 포털/허브)",
    )

    canvas.setFont("Malgun", 8)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawRightString(w - 12 * mm, h - 9.5 * mm, f"Page {doc.page}")

    canvas.line(12 * mm, 11 * mm, w - 12 * mm, 11 * mm)
    canvas.drawString(
        12 * mm,
        6.8 * mm,
        "※ 안내: Chrome / Edge / Adobe Acrobat에서 열면 각 항목의 [v] 체크박스와 메모란을 마우스로 직접 체크·입력 후 저장할 수 있습니다.",
    )
    canvas.restoreState()


def build_pdf(output_path: str) -> int:
    reg_font, bold_font = register_korean_fonts()

    doc = SimpleDocTemplate(
        output_path,
        pagesize=PAGE_SIZE,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=15.5 * mm,
        bottomMargin=14.5 * mm,
        title="Rent3 5대 페이지 통합 QA 테스트 체크리스트 (현행 재정비 반영본)",
        author="BaseSaasApp Antigravity",
    )

    usable_w = PAGE_SIZE[0] - 24 * mm  # 297 - 24 = 273 mm

    st_title = ParagraphStyle(
        "DocTitle",
        fontName=bold_font,
        fontSize=15.5,
        leading=21,
        textColor=colors.HexColor("#0F172A"),
    )
    st_sub = ParagraphStyle(
        "DocSub",
        fontName=reg_font,
        fontSize=8.8,
        leading=13,
        textColor=colors.HexColor("#475569"),
    )
    st_sec_title = ParagraphStyle(
        "SecTitle",
        fontName=bold_font,
        fontSize=11,
        leading=14.5,
        textColor=colors.white,
    )
    st_sec_url = ParagraphStyle(
        "SecUrl",
        fontName=reg_font,
        fontSize=8.3,
        leading=11.5,
        textColor=colors.HexColor("#1E293B"),
    )
    st_th = ParagraphStyle(
        "TableHead",
        fontName=bold_font,
        fontSize=8.4,
        leading=11,
        alignment=1,
        textColor=colors.HexColor("#1E293B"),
    )
    st_code = ParagraphStyle(
        "ItemCode",
        fontName=bold_font,
        fontSize=8,
        leading=10.5,
        alignment=1,
        textColor=colors.HexColor("#334155"),
    )
    st_name = ParagraphStyle(
        "ItemName",
        fontName=bold_font,
        fontSize=8.2,
        leading=11.5,
        textColor=colors.HexColor("#0F172A"),
    )
    st_body = ParagraphStyle(
        "ItemBody",
        fontName=reg_font,
        fontSize=8,
        leading=11.5,
        textColor=colors.HexColor("#334155"),
    )
    st_meta_lbl = ParagraphStyle(
        "MetaLbl",
        fontName=bold_font,
        fontSize=8.4,
        leading=11.5,
        textColor=colors.HexColor("#1E293B"),
    )

    story: List[Any] = []
    total_cnt = sum(len(s["items"]) for s in SECTIONS)

    # 1. 문서 타이틀 및 안내
    story.append(Paragraph("Rent3 5대 페이지 통합 QA 테스트 체크리스트 (현행 재정비 반영본)", st_title))
    story.append(Spacer(1, 2 * mm))
    story.append(
        Paragraph(
            f"공동 실시간 저장소(<b>data/rent3_pool/*.json</b>)를 공유하는 5대 페이지를 "
            f"<b>임대인 페이지 → 임차인 페이지 → 관리자 페이지 → 스마트 계약 → 포털·통합 인증·Data Pool 허브</b> 순으로 재정비한 "
            f"총 <b>{total_cnt}개</b> 통합 검증 시나리오입니다. 각 항목은 <b>테스트 항목(기능) / 테스트 방법 / 기대되는 결과</b>로 구성되어 있습니다.",
            st_sub,
        )
    )
    story.append(Spacer(1, 3.5 * mm))

    # 테스트 메타 입력 테이블 (usable_w = 273mm)
    meta_w = [24 * mm, 44 * mm, 22 * mm, 38 * mm, 22 * mm, usable_w - 150 * mm]
    meta_table = Table(
        [
            [
                Paragraph("테스트 담당자", st_meta_lbl),
                InteractiveNoteField("meta_tester", width=meta_w[1] - 10, height=15, tooltip="테스터 성명 입력"),
                Paragraph("테스트 일자", st_meta_lbl),
                InteractiveNoteField("meta_date", width=meta_w[3] - 10, height=15, tooltip="예: 2026-11-26"),
                Paragraph("실행 환경", st_meta_lbl),
                InteractiveNoteField("meta_env", width=meta_w[5] - 10, height=15, tooltip="예: Windows / localhost:8000 / Chrome 모바일(390px)"),
            ]
        ],
        colWidths=meta_w,
    )
    meta_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F1F5F9")),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#94A3B8")),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(meta_table)
    story.append(Spacer(1, 3.5 * mm))

    # 요약 집계 테이블
    sum_rows: List[List[Any]] = [
        [
            Paragraph("순서 · 페이지 구분", st_th),
            Paragraph("접속 경로", st_th),
            Paragraph("항목 ID 범위", st_th),
            Paragraph("시나리오 수", st_th),
        ]
    ]
    for sec in SECTIONS:
        items = sec["items"]
        id_range = f"{items[0][0]} ~ {items[-1][0]}"
        short_url = sec["url"].replace("http://localhost:8000", "").strip()
        sum_rows.append(
            [
                Paragraph(xml_escape(sec["title"]), st_name),
                Paragraph(xml_escape(short_url), st_body),
                Paragraph(xml_escape(id_range), st_code),
                Paragraph(f"{len(items)} 건", st_code),
            ]
        )
    sum_rows.append(
        [
            Paragraph("합계", st_th),
            Paragraph("Rent3 5대 분리 앱 + 진위 확인 + Data Pool 허브 전체", st_body),
            Paragraph("5개 영역", st_code),
            Paragraph(f"{total_cnt} 건", st_th),
        ]
    )
    sum_table = Table(
        sum_rows,
        colWidths=[78 * mm, 121 * mm, 44 * mm, usable_w - 243 * mm],
    )
    sum_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F1F5F9")),
                ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#94A3B8")),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3.8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.8),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(sum_table)
    story.append(Spacer(1, 5 * mm))

    # 2. 5대 페이지별 상세 체크리스트 테이블 (usable_w = 273mm)
    col_widths = [11 * mm, 16 * mm, 46 * mm, 86 * mm, 90 * mm, usable_w - 249 * mm]  # 마지막 열 = 24mm

    for sec in SECTIONS:
        accent_color = colors.HexColor(sec["accent"])
        light_bg = colors.HexColor(sec["bg"])

        sec_banner = Table(
            [
                [Paragraph(xml_escape(sec["title"]), st_sec_title)],
                [Paragraph(f"접속 주소: <b>{xml_escape(sec['url'])}</b>", st_sec_url)],
            ],
            colWidths=[usable_w],
        )
        sec_banner.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), accent_color),
                    ("BACKGROUND", (0, 1), (-1, 1), light_bg),
                    ("BOX", (0, 0), (-1, -1), 0.8, accent_color),
                    ("TOPPADDING", (0, 0), (-1, 0), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
                    ("TOPPADDING", (0, 1), (-1, 1), 3.5),
                    ("BOTTOMPADDING", (0, 1), (-1, 1), 3.5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )

        rows: List[List[Any]] = [
            [
                Paragraph("체크", st_th),
                Paragraph("ID", st_th),
                Paragraph("테스트 항목 (기능)", st_th),
                Paragraph("테스트 방법", st_th),
                Paragraph("기대되는 결과", st_th),
                Paragraph("메모", st_th),
            ]
        ]

        row_styles: List[Any] = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
            ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#94A3B8")),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 1), (1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]

        for idx, (item_id, name, method, expected) in enumerate(sec["items"], start=1):
            chk = InteractiveCheckBox(
                field_name=f"chk_{item_id}",
                tooltip=f"[{item_id}] {name} 테스트 완료 체크",
                size=12,
            )
            note = InteractiveNoteField(
                field_name=f"note_{item_id}",
                width=col_widths[5] - 8,
                height=14,
                tooltip=f"[{item_id}] 테스트 결과/비고 입력",
            )
            rows.append(
                [
                    chk,
                    Paragraph(xml_escape(item_id), st_code),
                    Paragraph(xml_escape(name), st_name),
                    Paragraph(xml_escape(method), st_body),
                    Paragraph(xml_escape(expected), st_body),
                    note,
                ]
            )
            bg_hex = "#FFFFFF" if idx % 2 == 1 else "#F8FAFC"
            row_styles.append(("BACKGROUND", (0, idx), (-1, idx), colors.HexColor(bg_hex)))

        sec_table = Table(rows, colWidths=col_widths, repeatRows=1)
        sec_table.setStyle(TableStyle(row_styles))

        story.append(CondPageBreak(45 * mm))
        story.append(KeepTogether([sec_banner, Spacer(1, 1.2 * mm)]))
        story.append(sec_table)
        story.append(Spacer(1, 5 * mm))

    # 3. 최종 검수 확인란
    final_col_w = usable_w - 134 * mm
    signoff = Table(
        [
            [
                Paragraph("최종 합격 판정", st_meta_lbl),
                InteractiveCheckBox("chk_final_pass", tooltip="전체 기능 정상 작동 최종 승인", size=12),
                Paragraph("PASS (전체 정상)", st_name),
                InteractiveCheckBox("chk_final_retest", tooltip="일부 항목 재검증 필요", size=12),
                Paragraph("RETEST (보완 후 재검증)", st_name),
                Paragraph("종합 의견", st_meta_lbl),
                InteractiveNoteField("note_final_summary", width=final_col_w - 10, height=16, tooltip="종합 테스트 의견"),
            ]
        ],
        colWidths=[26 * mm, 9 * mm, 32 * mm, 9 * mm, 40 * mm, 18 * mm, final_col_w],
    )
    signoff.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EFF6FF")),
                ("BOX", (0, 0), (-1, -1), 0.9, colors.HexColor("#2563EB")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 5.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(KeepTogether([signoff]))

    doc.build(story, onFirstPage=draw_page_chrome, onLaterPages=draw_page_chrome)
    return total_cnt


def main() -> None:
    total = build_pdf(OUT_MAIN)
    os.makedirs(os.path.dirname(OUT_WEB), exist_ok=True)
    shutil.copy2(OUT_MAIN, OUT_WEB)
    print(f"[OK] Generated interactive QA PDF ({total} check items):")
    print(f"  1) {OUT_MAIN}")
    print(f"  2) {OUT_WEB}")


if __name__ == "__main__":
    main()
