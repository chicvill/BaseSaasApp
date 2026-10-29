"""
scripts/generate_rent3_qa_pdf.py — Rent3 5대 분리 페이지 디폴트 게시 데이터 & 실행 기능 QA 테스트 체크리스트 PDF 생성기.

특징:
  - 모든 게시 데이터 항목과 실행 버튼(기능) 항목마다 클릭 가능한 인터랙티브 AcroForm 체크박스(Checkbox) 탑재
  - Chrome, Edge, Adobe Acrobat 등에서 직접 마우스로 체크/해제 후 저장 가능하며, A4 인쇄 시 수기 체크용으로도 최적화된 레이아웃 제공
  - 테스트 담당자·일자·환경·비고 입력용 인터랙티브 텍스트 필드 포함
"""
from __future__ import annotations

import os
import shutil
from typing import Any, Dict, List

from reportlab.lib import colors  # type: ignore[import-untyped]
from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
from reportlab.lib.styles import ParagraphStyle  # type: ignore[import-untyped]
from reportlab.lib.units import mm  # type: ignore[import-untyped]
from reportlab.pdfbase import pdfmetrics  # type: ignore[import-untyped]
from reportlab.pdfbase.ttfonts import TTFont  # type: ignore[import-untyped]
from reportlab.platypus import (  # type: ignore[import-untyped]
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
OUT_WEB = os.path.join(ROOT, "apps", "rent3", "frontend", "Rent3_QA_Test_Checklist.pdf")


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

    def __init__(self, field_name: str, tooltip: str = "", size: int = 13):
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

    def __init__(self, field_name: str, width: float = 72.0, height: float = 16.0, tooltip: str = ""):
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


SECTIONS: List[Dict[str, Any]] = [
    {
        "code": "HUB",
        "title": "1. 통합 관리 허브 페이지 (rent3)",
        "url": "http://localhost:8000/rent3/pool.html  (올인원 포털: /rent3/)",
        "accent": "#0284C7",
        "bg": "#E0F2FE",
        "items": [
            ("HUB-D01", "게시데이터", "상단 Pool 동기화 배지", "총 리비전(Pool vN) 및 전체 객체 수 합계가 정상 표기되고 3초 주기로 자동 갱신되는지 확인"),
            ("HUB-D02", "게시데이터", "8대 공동 JSON 현황 카드", "users(8)·buildings(3)·rooms(7)·contracts(4)·cosign_sessions(1)·payments(5)·outlays(3)·ops(5) 객체 수와 파일 크기 표기 확인"),
            ("HUB-D03", "게시데이터", "디폴트 JSON 원문 뷰어", "초기 진입 시 기본 선택 컬렉션인 rooms.json의 디스크 JSON 전문이 하단 뷰어에 출력되는지 확인"),
            ("HUB-D04", "게시데이터", "실시간 감사 이벤트 스트림", "_events.jsonl 최근 15건의 [시각 · 호출앱 · 컬렉션 · 액션 · 요약] 로그가 정상 표시되는지 확인"),
            ("HUB-F01", "실행기능", "4대 역할 앱 이동 카드 클릭", "관리인(/rent3_admin/) · 임대인(/rent3_lessor/) · 임차인(/rent3_tenant/) · 공동계약(/rent3_contract/) 각 카드 클릭 시 즉시 이동 확인"),
            ("HUB-F02", "실행기능", "*.json 파일 카드 클릭 전환", "users.json, contracts.json 등 다른 파일 카드를 클릭할 때 활성 테두리와 하단 JSON 원문이 즉시 교체되는지 확인"),
            ("HUB-F03", "실행기능", "타 앱 변경분 3초 자동 반영", "임대인/임차인 앱에서 데이터 수정 후 새로고침 없이 3초 내 Pool 리비전 및 이벤트 로그가 갱신되는지 확인"),
        ],
    },
    {
        "code": "ADM",
        "title": "2. 관리자(관리인) 페이지 (rent3_admin)",
        "url": "http://localhost:8000/rent3_admin/",
        "accent": "#7C3AED",
        "bg": "#EDE9FE",
        "items": [
            ("ADM-D01", "게시데이터", "전사 포트폴리오 4대 KPI", "가맹 건물 3채(4/7실) · 월 SaaS MRR 99,000원 · 전사 총수입 3,150,000원 · 전사 순운영이익(NOI) 2,695,000원 기본 표시 확인"),
            ("ADM-D02", "게시데이터", "가맹 임대인 3명 기본 목록", "김임대(main, PAID) · 박선호(sunho, PAID) · 최신비(sinbi, PENDING 미납) 목록 및 계좌·사업자번호 표기 확인"),
            ("ADM-D03", "게시데이터", "지점별 가동률·NOI 비교 탭", "[전 지점 건물 포트폴리오 & RTMS] 탭 전환 시 시크빌·선호아트빌·신비주택 3개 지점의 가동률·Cap Rate·NOI·부가세 예수금 비교 확인"),
            ("ADM-D04", "게시데이터", "전월세 신고제(RTMS) 관제", "기존 3건의 확정일자(FILED) 녹색 배지와 중개 미신고 건 등록 시 '미신고 D-N일 (PENDING)' 황색 경고 배지 구분 확인"),
            ("ADM-D05", "게시데이터", "JSON Data Pool 원문 탐색기", "[실시간 JSON Data Pool 탐색기] 탭에서 기본 contracts.json 원문 및 최근 20건 감사 스트림 표시 확인"),
            ("ADM-F01", "실행기능", "[가맹 임대인 및 건물 등재]", "지점 코드·건물명·성명 입력 후 클릭 시 users.json과 buildings.json에 동시 등재되고 KPI 건물 수·MRR이 즉시 증가하는지 확인"),
            ("ADM-F02", "실행기능", "[사용료 완납 정산] 버튼", "PENDING 상태 임대인(최신비 등)의 [사용료 완납 정산] 클릭 시 PAID 배지로 즉시 전환되고 users.json이 갱신되는지 확인"),
            ("ADM-F03", "실행기능", "[지점 장부 열기 →] 링크", "임대인 카드 우측 버튼 클릭 시 해당 지점(?branch=...)이 자동 선택된 상태로 임대인 앱이 열리는지 확인"),
            ("ADM-F04", "실행기능", "Data Pool 컬렉션 드롭다운", "8종 JSON 파일 드롭다운 변경 시 해당 디스크 파일 원문·바이트 크기·리비전이 즉시 조회되는지 확인"),
            ("ADM-F05", "실행기능", "상단 네비게이션 & [매뉴얼]", "타 역할 앱 이동 버튼 및 [매뉴얼] 클릭 시 관리자 공식 매뉴얼(manual.html) 열람·인쇄 동작 확인"),
        ],
    },
    {
        "code": "LES",
        "title": "3. 임대인 페이지 (rent3_lessor)",
        "url": "http://localhost:8000/rent3_lessor/",
        "accent": "#059669",
        "bg": "#D1FAE5",
        "items": [
            ("LES-D01", "게시데이터", "기본 지점(시크빌) & 헤더 정보", "초기 진입 시 '시크빌 본점 (main)' · 주소 · 소유자 '김임대' 및 Pool 리비전 배지가 표시되는지 확인"),
            ("LES-D02", "게시데이터", "지점 4대 KPI 카드 (main)", "가동 호실 2/3실(66.7%) · 지점 미납 합계 1,140,000원(가짜체납 0) · 연간 수납 1,650,000원 · NOI 1,195,000원 표시 확인"),
            ("LES-D03", "게시데이터", "시크빌 201·202·203호 원장 카드", "201호(완납, 7월 비과세) · 202호(8월 비과세, 9·10월 미납 114만) · 203호(공실 매물 공개중) 상태 및 월별 칩 색상 확인"),
            ("LES-D04", "게시데이터", "중개 계약서 사진 등록 탭 폼", "[중개 계약서 사진 등록] 탭 진입 시 공실(203호) 기본 선택, 사진 첨부창, OCR 문구창, 계약·중개사 입력폼 노출 확인"),
            ("LES-D05", "게시데이터", "공용운영비 · NOI · 종소세 탭", "미화(18만)·승강기(11만)·인터넷(16.5만) 경비 합계(45.5만) 차감 NOI 및 15.4% 분리과세 예상세액 표기 확인"),
            ("LES-D06", "게시데이터", "A/S · 채팅 · 퇴실정산 탭", "201호 세면대 배수 점검 요청(RECEIVED) 티켓 및 203호 김예비 방문투어 희망 채팅 기본 노출 확인"),
            ("LES-D07", "게시데이터", "신규호실 · 도어락/주차 관제 탭", "201~203호 현재 도어락 비밀번호(4860*, 7942*, 2030*) 및 등록 차량(201호 12가 3456) 표시 확인"),
            ("LES-D08", "게시데이터", "만기 6~2개월 전 법정 통지구간 배너", "주택임대차보호법 제6조 통지구간(D-180~D-60) 도래 호실(시크빌 201호 등) 상단 경보 배너 및 통지마감일 표시 확인"),
            ("LES-F01", "실행기능", "지점 선택 드롭다운 전환", "상단 드롭다운에서 선호아트빌(sunho) · 신비주택(sinbi, 상가 VAT 포함) 전환 시 KPI와 호실 목록이 즉시 바뀌는지 확인"),
            ("LES-F02", "실행기능", "[문자 파싱 & 즉시 수납] + 중복차단", "은행 입금문자 파싱 시 가장 오래된 미납월부터 FIFO 순차 상계되며, 동일 문자 재클릭 시 409 중복입금 황색 배너로 차단되는지 확인"),
            ("LES-F03", "실행기능", "[직접 FIFO 수납] 버튼", "호실 카드에서 수납액 입력 후 클릭 시 payments.json 반영 및 미납액/월별 상태 칩이 즉시 갱신되는지 확인"),
            ("LES-F04", "실행기능", "[비공개 전환 / 직거래 매물 공개]", "공실 카드 토글 클릭 시 rooms.json의 is_published가 변경되고 임차인 앱 공실 쇼케이스에 즉시 노출/비노출되는지 확인"),
            ("LES-F05", "실행기능", "[중개계약 사진등록] 바로가기", "호실 카드의 [중개계약 사진등록] 클릭 시 해당 호실이 선택된 상태로 중개 계약서 등록 탭으로 이동하는지 확인"),
            ("LES-F06", "실행기능", "[샘플 중개계약서 생성] / 파일첨부", "실제 이미지/PDF 파일을 선택하거나 [샘플 중개계약서 촬영본 자동 생성] 클릭 시 미리보기 이미지가 즉시 표시되는지 확인"),
            ("LES-F07", "실행기능", "[문구에서 계약조건 자동 채우기]", "카메라 OCR 문구 입력 후 클릭 시 보증금·월세·관리비·계약금·입주일·임차인·중개사무소명이 자동 입력되는지 확인"),
            ("LES-F08", "실행기능", "[계약서 사진 보관 및 계약내용 적용]", "적용 클릭 시 uploads/contracts/ 이미지 저장 및 contracts·rooms(공실마감)·users·payments·ops(RTMS) 5종 JSON 동시 반영 확인"),
            ("LES-F09", "실행기능", "[첨부 계약서 보기] 토글 & 새창", "중개 지면계약 호실에서 [첨부 계약서 보기] 클릭 시 계약서 사본 이미지·중개소·SHA-256 해시가 펼쳐지고 원본 열기가 되는지 확인"),
            ("LES-F10", "실행기능", "[5월 종소세 홈택스 CSV 다운로드]", "클릭 시 한글 깨짐 없는 UTF-8 BOM CSV 세무 명세 파일이 즉시 다운로드되는지 확인"),
            ("LES-F11", "실행기능", "[공용 운영비 저장] 버튼", "8대 표준 분류 선택 후 지출 저장 시 outlays.json에 등재되고 지점 필요경비 합계·NOI가 즉시 재계산되는지 확인"),
            ("LES-F12", "실행기능", "A/S [기사 배정] & [수리 완료]", "기사 배정 시 안내 채팅이 자동 발송되고, 수리 완료 시 임대인 부담 수리비가 outlays.json 필요경비에 자동 등재되는지 확인"),
            ("LES-F13", "실행기능", "[퇴실 정산서 발행] & [반환 완료]", "미납월세·공과금·청소비 자동 공제 정산서 발행 후, 반환 완료 클릭 시 호실이 즉시 '공실 매물 공개'로 자동 복귀되는지 확인"),
            ("LES-F14", "실행기능", "[호실 저장] · [비번 변경] · [채팅 전송]", "신규 호실 추가, 도어락 비밀번호 변경(입주자 마이홈 즉시 반영), 1:1 메신저 답변 전송이 각각 정상 작동하는지 확인"),
            ("LES-F15", "실행기능", "[전출계획 확인 요청] & [전출/연장 기록]", "만기 6~2개월 전 확인 요청 발송 및 유선 확인 기록 시 ops.json 반영, 만기전출 시 사전공실 노출 동작 확인"),
            ("LES-F16", "실행기능", "[연장 재계약 예고 발송] (5% 상한 검증)", "전출의사 없을 때 연장 조건 예고 시 5% 초과 인상은 422 차단되고 적법 조건은 마이홈 예고장 및 공동 재계약방에 전달되는지 확인"),
        ],
    },
    {
        "code": "TEN",
        "title": "4. 임차인 페이지 (rent3_tenant)",
        "url": "http://localhost:8000/rent3_tenant/",
        "accent": "#D97706",
        "bg": "#FEF3C7",
        "items": [
            ("TEN-D01", "게시데이터", "기본 입주자(201호 이민수) 마이홈", "초기 진입 시 '[시크빌 본점 201호] 이민수' 선택, '미납 0원 (완납)' 및 '만기 D-Day' 배지가 정상 표시되는지 확인"),
            ("TEN-D02", "게시데이터", "출입·와이파이 & 주차 등록 카드", "세대 도어락 기본 마스킹(••••*) · 공동현관(#2580*) · Wi-Fi 정보 및 등록 차량(12가 3456) 표시 확인"),
            ("TEN-D03", "게시데이터", "중개 지면계약서 사본 카드(해당자)", "공인중개사 계약으로 등록된 세입자 선택 시 중개업소 정보와 첨부 계약서 원본 이미지 미리보기 카드가 자동 노출되는지 확인"),
            ("TEN-D04", "게시데이터", "월별 FIFO 납부 현황 & A/S 목록", "후불 입주월 비과세(EXEMPT_POSTPAID) 내역, 월별 청구/납부 내역 및 접수된 A/S 진행 상태 표시 확인"),
            ("TEN-D05", "게시데이터", "17% 월세 세액공제 공식 증명서", "연간 총 납입액(1,650,000원), 17% 예상 세액공제(280,500원), 확정일자 및 현금영수증 승인번호가 찍힌 영수필 직인 양식 확인"),
            ("TEN-D06", "게시데이터", "복비 0원 공실 탐색 쇼케이스", "[복비 0원 즉시입주 공실 탐색] 전환 시 공개 공실 3실(시크빌 203·선호 302·신비 202) 및 등기 클린 배지 노출 확인"),
            ("TEN-D07", "게시데이터", "만기 6~2개월 전 전출확인·연장예고 카드", "법정 통지구간 세입자(201호 이민수 등) 마이홈 진입 시 만기일·통지마감일 및 연장 재계약 예고장 카드 표시 확인"),
            ("TEN-F01", "실행기능", "[마이홈] ↔ [공실 탐색] 모드 스위치", "상단 2개 모드 버튼 클릭 시 세입자 마이홈 라운지와 예비 임차인 공실 탐색 화면이 즉시 전환되는지 확인"),
            ("TEN-F02", "실행기능", "입주 세입자 선택 드롭다운", "202호 정서연(미납 보유), 301호 한지훈, 101호 디자인스튜디오 봄 등으로 전환 시 각 세대 원장과 증명서가 갱신되는지 확인"),
            ("TEN-F03", "실행기능", "세대 도어락 [보기 / 숨기기]", "버튼 클릭 시 마스킹(••••*)이 실제 도어락 비밀번호(4860* 등)로 토글되고 임대인 변경분과 일치하는지 확인"),
            ("TEN-F04", "실행기능", "내 차량 주차 [등록] 버튼", "차량번호 수정 후 [등록] 클릭 시 users.json에 즉시 반영되고 임대인 앱 주차 관제 탭에서도 동기화되는지 확인"),
            ("TEN-F05", "실행기능", "[계약서 원본 열기/다운로드]", "중개 계약 세입자 마이홈에서 클릭 시 새 탭으로 고해상도 계약서 촬영본/PDF가 정상 서빙되는지 확인"),
            ("TEN-F06", "실행기능", "퇴실 정산 [확인 및 동의 서명]", "임대인이 발행한 퇴실 정산서 도착 시 반환 계좌·동의 서명 후 클릭하면 임대인 화면이 세입자 동의 완료로 갱신되는지 확인"),
            ("TEN-F07", "실행기능", "[월세 즉시 납부] 온라인 결제", "카카오페이/카드/계좌이체 선택 후 납부 클릭 시 payments.json 반영, 미납액 차감 및 세액공제 증명서 금액이 즉시 합산되는지 확인"),
            ("TEN-F08", "실행기능", "[임대인에게 수리 요청 보내기]", "수리 분류·제목 입력 후 클릭 시 ops.json에 티켓이 생성되고 임대인 A/S 대장에 실시간 노출되는지 확인"),
            ("TEN-F09", "실행기능", "[납입증명서 인쇄] & 공실 [방문문의/계약방]", "증명서 인쇄 시 불필요한 버튼이 숨겨져 A4 출력되며, 공실 탭에서 [1:1 방문문의] 전송 및 [열린 계약방 입장] 이동 확인"),
            ("TEN-F10", "실행기능", "[전출의사 없음] / [만기 전출 예정 회신]", "마이홈에서 회신 시 임대인 화면에 즉시 동기화되며, 만기 전출 시 공실 쇼케이스에 사전공개 배지로 노출되는지 확인"),
            ("TEN-F11", "실행기능", "[연장 재계약 예고 조건 확인·동의]", "임대인 연장 재계약 예고장에서 조건 확인·동의 클릭 시 상태가 동의 완료로 변경되고 공동 재계약방 원클릭 입장이 되는지 확인"),
        ],
    },
    {
        "code": "CON",
        "title": "5. 화면공유 공동 임대계약 페이지 (rent3_contract)",
        "url": "http://localhost:8000/rent3_contract/",
        "accent": "#DC2626",
        "bg": "#FEE2E2",
        "items": [
            ("CON-D01", "게시데이터", "기본 시점 & 열린 계약 세션 로드", "초기 진입 시 '임대인(갑) 시점', '[Step 1/5] 시크빌 본점 203호 (김예비)' 세션, Revision v1 및 6자리 참여코드 배지 표시 확인"),
            ("CON-D02", "게시데이터", "Step 1 등기부등본 권리관계 값", "발급확인번호, 갑구 소유자(김임대), 을구 선순위 채권최고액(0원) 및 양측 확인 상태 배지 기본 표시 확인"),
            ("CON-D03", "게시데이터", "Step 2 옵션 실물 공동 점검표", "에어컨·냉장고·세탁기·인덕션·도어락 5대 시설 체크리스트와 현장 시설 특기사항 메모란 노출 확인"),
            ("CON-D04", "게시데이터", "Step 3 계약 조건 및 특약 디폴트", "보증금 500만 · 월세 50만 · 관리비 5만(후불) · 계약금 30만 · 12개월 및 표준 직거래 특약 문구 기본 세팅 확인"),
            ("CON-D05", "게시데이터", "Step 4 서명 캔버스 & Step 5 계좌", "마우스/터치 자필 서명 패드(#sigCanvas), Step 5 임대인 입금계좌 및 체결 후 A4 표준 전자계약서(확정일자 직인) 렌더링 확인"),
            ("CON-F01", "실행기능", "[임대인(갑)] ↔ [임차인(을)] 시점 전환", "상단 역할 스위치 클릭 시 동일 세션에서 갑·을 전용 입력 권한과 승인 버튼 문구가 즉시 전환되는지 확인"),
            ("CON-F02", "실행기능", "6자리 참여코드 [입장] & 세션 선택", "참여코드 입력 후 [입장] 클릭 시 해당 공동 계약방으로 즉시 합류하며 2.5초 폴링으로 양측 화면이 동기화되는지 확인"),
            ("CON-F03", "실행기능", "[새 공동 임대계약방 개설] (신규/재계약)", "아코디언을 열어 대상 호실과 '신규 계약' 또는 '조건변경 재계약(5% 상한)' 선택 후 개설 시 cosign_sessions.json 생성 확인"),
            ("CON-F04", "실행기능", "Step 1 등기 제시 & 임차인 확인 잠금", "임대인 등기 제시 후 임차인 시점에서 [등기부 이상 없음 확인 잠금] 클릭 시 Step 2로 자동 이동하는지 확인"),
            ("CON-F05", "실행기능", "Step 2 시설·옵션 상호 동의 잠금", "옵션 체크 상태 및 시설 메모 확인 후 양측 확인 클릭 시 퇴실 정산 비교 원본으로 잠금되고 Step 3으로 이동하는지 확인"),
            ("CON-F06", "실행기능", "Step 3 [조건 수정 반영] Revision Lock", "동의 후 금액·특약을 1글자라도 수정하면 Revision이 증가하며 양측 기존 동의가 자동 초기화되고, 재계약 5% 초과 인상 시 422 차단되는지 확인"),
            ("CON-F07", "실행기능", "Step 3 [현재 조건(vN) 최종 합의 잠금]", "임대인(갑)과 임차인(을) 시점에서 각각 최종 합의 잠금을 완료하면 Step 4 전자서명 단계로 진입하는지 확인"),
            ("CON-F08", "실행기능", "Step 4 자필 서명 [지우기] & [날인 완료]", "캔버스에 서명 드로잉 후 양측 날인 완료 시 계약 전문에 대한 SHA-256 무결성 해시가 즉시 봉인 생성되는지 확인"),
            ("CON-F09", "실행기능", "Step 5 [최종 체결 봉인] & [A4 인쇄]", "체결 클릭 시 contracts·rooms(공실 마감)·users·payments·ops(확정일자 FIX-...) 5개 JSON이 일괄 갱신되고 표준 전자계약서 인쇄가 작동하는지 확인"),
        ],
    },
]


def draw_page_chrome(canvas, doc):
    """모든 페이지 상단 머리글 및 하단 페이지 번호 렌더링."""
    canvas.saveState()
    w, h = A4
    canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
    canvas.setLineWidth(0.6)
    canvas.line(14 * mm, h - 13 * mm, w - 14 * mm, h - 13 * mm)

    canvas.setFont("MalgunBd", 8.5)
    canvas.setFillColor(colors.HexColor("#334155"))
    canvas.drawString(14 * mm, h - 10.5 * mm, "BaseSaasApp · Rent3 공동 JSON Data Pool 5대 페이지 통합 QA 테스트 체크리스트")

    canvas.setFont("Malgun", 8)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawRightString(w - 14 * mm, h - 10.5 * mm, f"Page {doc.page}")

    canvas.line(14 * mm, 12 * mm, w - 14 * mm, 12 * mm)
    canvas.drawString(
        14 * mm,
        7.5 * mm,
        "※ 안내: Chrome / Edge / Adobe Acrobat에서 열면 각 항목의 [v] 체크박스와 메모란을 마우스로 직접 체크·입력 후 저장할 수 있습니다.",
    )
    canvas.restoreState()


def build_pdf(output_path: str) -> int:
    reg_font, bold_font = register_korean_fonts()

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=17 * mm,
        bottomMargin=16 * mm,
        title="Rent3 5대 페이지 디폴트 데이터 및 실행 기능 QA 테스트 체크리스트",
        author="BaseSaasApp Antigravity",
    )

    usable_w = A4[0] - 28 * mm

    st_title = ParagraphStyle(
        "DocTitle",
        fontName=bold_font,
        fontSize=16,
        leading=22,
        textColor=colors.HexColor("#0F172A"),
    )
    st_sub = ParagraphStyle(
        "DocSub",
        fontName=reg_font,
        fontSize=9,
        leading=13.5,
        textColor=colors.HexColor("#475569"),
    )
    st_sec_title = ParagraphStyle(
        "SecTitle",
        fontName=bold_font,
        fontSize=11.5,
        leading=15,
        textColor=colors.white,
    )
    st_sec_url = ParagraphStyle(
        "SecUrl",
        fontName=reg_font,
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#1E293B"),
    )
    st_th = ParagraphStyle(
        "TableHead",
        fontName=bold_font,
        fontSize=8.5,
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
    st_badge_data = ParagraphStyle(
        "BadgeData",
        fontName=bold_font,
        fontSize=7.8,
        leading=10,
        alignment=1,
        textColor=colors.HexColor("#0369A1"),
    )
    st_badge_func = ParagraphStyle(
        "BadgeFunc",
        fontName=bold_font,
        fontSize=7.8,
        leading=10,
        alignment=1,
        textColor=colors.HexColor("#047857"),
    )
    st_name = ParagraphStyle(
        "ItemName",
        fontName=bold_font,
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#0F172A"),
    )
    st_desc = ParagraphStyle(
        "ItemDesc",
        fontName=reg_font,
        fontSize=8.2,
        leading=12,
        textColor=colors.HexColor("#334155"),
    )
    st_meta_lbl = ParagraphStyle(
        "MetaLbl",
        fontName=bold_font,
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#1E293B"),
    )

    story: List[Any] = []

    # 1. 문서 타이틀 및 테스트 메타 입력 박스
    total_data_cnt = sum(1 for s in SECTIONS for it in s["items"] if it[1] == "게시데이터")
    total_func_cnt = sum(1 for s in SECTIONS for it in s["items"] if it[1] == "실행기능")
    total_cnt = total_data_cnt + total_func_cnt

    story.append(Paragraph("Rent3 5대 페이지 디폴트 게시 데이터 & 실행 기능 테스트 체크리스트", st_title))
    story.append(Spacer(1, 3 * mm))
    story.append(
        Paragraph(
            f"불변의 BaseSaasApp 위에서 공동 실시간 저장소(<b>data/rent3_pool/*.json</b>)를 공유하는 5개 분리 페이지의 "
            f"<b>디폴트 게시 데이터({total_data_cnt}개)</b>와 <b>실행 버튼·핵심 기능({total_func_cnt}개)</b> 총 <b>{total_cnt}개</b> 검증 항목입니다. "
            f"PDF 뷰어에서 좌측 체크박스를 직접 클릭해 테스트 완료 여부를 기록하세요.",
            st_sub,
        )
    )
    story.append(Spacer(1, 4 * mm))

    # 테스터/일자/환경 입력용 인터랙티브 헤더 테이블
    env_col_w = usable_w - 133 * mm
    meta_table = Table(
        [
            [
                Paragraph("테스트 담당자", st_meta_lbl),
                InteractiveNoteField("meta_tester", width=36 * mm - 11, height=15, tooltip="테스터 성명 입력"),
                Paragraph("테스트 일자", st_meta_lbl),
                InteractiveNoteField("meta_date", width=33 * mm - 11, height=15, tooltip="예: 2026-10-29"),
                Paragraph("실행 환경", st_meta_lbl),
                InteractiveNoteField("meta_env", width=env_col_w - 11, height=15, tooltip="예: Windows / run.bat (localhost:8000)"),
            ]
        ],
        colWidths=[24 * mm, 36 * mm, 21 * mm, 33 * mm, 19 * mm, env_col_w],
    )
    meta_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F1F5F9")),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#94A3B8")),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(meta_table)
    story.append(Spacer(1, 4 * mm))

    # 요약 집계 테이블
    sum_rows = [
        [
            Paragraph("페이지 구분", st_th),
            Paragraph("접속 경로", st_th),
            Paragraph("디폴트 게시데이터", st_th),
            Paragraph("실행 버튼(기능)", st_th),
            Paragraph("합계", st_th),
        ]
    ]
    for sec in SECTIONS:
        d_c = sum(1 for it in sec["items"] if it[1] == "게시데이터")
        f_c = sum(1 for it in sec["items"] if it[1] == "실행기능")
        short_url = sec["url"].split("(")[0].replace("http://localhost:8000", "").strip()
        sum_rows.append(
            [
                Paragraph(sec["title"], st_name),
                Paragraph(short_url, st_desc),
                Paragraph(f"{d_c} 건", st_code),
                Paragraph(f"{f_c} 건", st_code),
                Paragraph(f"{d_c + f_c} 건", st_code),
            ]
        )
    sum_table = Table(
        sum_rows,
        colWidths=[64 * mm, 44 * mm, 26 * mm, 26 * mm, usable_w - 160 * mm],
    )
    sum_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#94A3B8")),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(sum_table)
    story.append(Spacer(1, 5 * mm))

    # 2. 5대 페이지별 상세 체크리스트 테이블 렌더링
    col_widths = [12 * mm, 18 * mm, 18 * mm, 42 * mm, usable_w - 116 * mm, 26 * mm]

    for sec in SECTIONS:
        accent_color = colors.HexColor(sec["accent"])
        light_bg = colors.HexColor(sec["bg"])

        sec_banner = Table(
            [
                [Paragraph(sec["title"], st_sec_title)],
                [Paragraph(f"접속 주소: <b>{sec['url']}</b>", st_sec_url)],
            ],
            colWidths=[usable_w],
        )
        sec_banner.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), accent_color),
                    ("BACKGROUND", (0, 1), (-1, 1), light_bg),
                    ("BOX", (0, 0), (-1, -1), 0.8, accent_color),
                    ("TOPPADDING", (0, 0), (-1, 0), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                    ("TOPPADDING", (0, 1), (-1, 1), 4),
                    ("BOTTOMPADDING", (0, 1), (-1, 1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )

        rows: List[List[Any]] = [
            [
                Paragraph("체크", st_th),
                Paragraph("항목 ID", st_th),
                Paragraph("구분", st_th),
                Paragraph("게시 항목 / 버튼명", st_th),
                Paragraph("디폴트 표시 데이터 및 실행 기능 검증 내용", st_th),
                Paragraph("테스트 메모", st_th),
            ]
        ]

        row_styles = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
            ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#94A3B8")),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 1), (0, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 4.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 4.5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4.5),
        ]

        for idx, (item_id, kind, name, desc) in enumerate(sec["items"], start=1):
            is_data = kind == "게시데이터"
            badge_p = Paragraph(kind, st_badge_data if is_data else st_badge_func)
            chk = InteractiveCheckBox(
                field_name=f"chk_{item_id}",
                tooltip=f"[{item_id}] {name} 테스트 완료 체크",
                size=13,
            )
            note = InteractiveNoteField(
                field_name=f"note_{item_id}",
                width=23 * mm,
                height=14,
                tooltip=f"[{item_id}] 테스트 결과/비고 입력",
            )
            rows.append(
                [
                    chk,
                    Paragraph(item_id, st_code),
                    badge_p,
                    Paragraph(name, st_name),
                    Paragraph(desc, st_desc),
                    note,
                ]
            )
            bg_hex = "#F8FAFC" if is_data else "#FFFFFF"
            badge_bg = "#E0F2FE" if is_data else "#ECFDF5"
            row_styles.append(("BACKGROUND", (0, idx), (-1, idx), colors.HexColor(bg_hex)))
            row_styles.append(("BACKGROUND", (2, idx), (2, idx), colors.HexColor(badge_bg)))

        sec_table = Table(rows, colWidths=col_widths, repeatRows=1)
        sec_table.setStyle(TableStyle(row_styles))

        story.append(KeepTogether([sec_banner, Spacer(1, 1.5 * mm)]))
        story.append(sec_table)
        story.append(Spacer(1, 5.5 * mm))

    # 3. 최종 검수 확인란
    final_col_w = usable_w - 130 * mm
    signoff = Table(
        [
            [
                Paragraph("최종 합격 판정", st_meta_lbl),
                InteractiveCheckBox("chk_final_pass", tooltip="전체 기능 정상 작동 최종 승인", size=13),
                Paragraph("PASS (전체 정상)", st_name),
                InteractiveCheckBox("chk_final_retest", tooltip="일부 항목 재검증 필요", size=13),
                Paragraph("RETEST (보완 후 재검증)", st_name),
                Paragraph("종합 의견", st_meta_lbl),
                InteractiveNoteField("note_final_summary", width=final_col_w - 11, height=16, tooltip="종합 테스트 의견"),
            ]
        ],
        colWidths=[25 * mm, 9 * mm, 30 * mm, 9 * mm, 39 * mm, 18 * mm, final_col_w],
    )
    signoff.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EFF6FF")),
                ("BOX", (0, 0), (-1, -1), 0.9, colors.HexColor("#2563EB")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
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
