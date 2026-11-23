"""
shared/ai/gemini_client.py
Gemini AI 공통 클라이언트 (BaseAIClient 구현).
모든 앱의 AI 기능은 이 클래스를 상속하거나 직접 인스턴스화하여 사용합니다.

상속 예시:
    from shared.ai.gemini_client import GeminiClient

    class GwansangAIService(GeminiClient):
        async def analyze_face(self, image_data: str, user_name: str) -> dict:
            prompt = f"사용자: {user_name}\n" + self.system_prompt
            return await self.generate_with_image(prompt, image_data)
"""
import os
import json
import logging
from typing import Optional, Dict, Any

from shared.ai.base import BaseAIClient

logger = logging.getLogger("mqnet.gemini")

genai: Any = None
types: Any = None
try:
    from google import genai as _genai
    from google.genai import types as _types
    genai = _genai
    types = _types
except Exception:
    try:
        import importlib
        genai = importlib.import_module("google.genai")
        types = importlib.import_module("google.genai.types")
    except Exception:
        genai = None
        types = None


def clean_json_markdown(text: str) -> str:
    """AI 응답 문자열에서 ```json ... ``` 코드 블록 마크다운을 안전하게 제거"""
    clean = text.strip()
    if clean.startswith("```json"):
        clean = clean[7:]
    elif clean.startswith("```"):
        clean = clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    return clean.strip()


def extract_base64_data(image_data: str, default_mime: str = "image/jpeg") -> tuple[str, str]:
    """Base64 data URL에서 순수 base64 문자열과 MIME 타입을 추출"""
    if "," in image_data:
        header, b64 = image_data.split(",", 1)
        mime = default_mime
        if header.startswith("data:") and ";base64" in header:
            mime = header[5:].split(";")[0]
        return b64.strip(), mime
    return image_data.strip(), default_mime


FALLBACK_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3-flash-preview",
    "gemini-3.8-flash",
]


class GeminiClient(BaseAIClient):
    """
    Gemini AI 기반 공통 클라이언트 클래스.
    각 앱은 이 클래스를 상속하여 앱별 AI 로직을 구현합니다.
    """

    def __init__(self, api_key: str = "", model: str = "gemini-3.5-flash", system_prompt: str = ""):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self.model = os.getenv("GEMINI_MODEL", model) or "gemini-3.5-flash"
        self.system_prompt = system_prompt
        self._client = None
        self.last_error: Optional[str] = None

        if self.api_key:
            if genai is not None:
                try:
                    self._client = genai.Client(api_key=self.api_key)
                    logger.info(f"Gemini AI 클라이언트 초기화 완료. 모델: {self.model}")
                except Exception as e:
                    self.last_error = f"Gemini 클라이언트 초기화 실패: {e}"
                    logger.warning(self.last_error)
            else:
                self.last_error = "google-genai 패키지가 설치되지 않았습니다. (pip install google-genai 필요)"
                logger.warning(self.last_error)
        else:
            self.last_error = "GEMINI_API_KEY가 설정되지 않았습니다."
            logger.warning("GEMINI_API_KEY가 설정되지 않았습니다. Mock 모드로 실행됩니다.")

    @staticmethod
    def _format_api_error(exc: Exception) -> str:
        msg = str(exc)
        lower = msg.lower()
        if "402" in msg or "prepayment credits are depleted" in lower or "payment required" in lower:
            return "Gemini API 선불 크레딧 잔액이 소진되었습니다. AI Studio에서 크레딧을 충전하거나 무료 티어 API 키를 사용하세요. (402 Payment Required)"
        if "429" in msg or "resource_exhausted" in lower or "quota" in lower:
            return "Gemini API 요청 한도(Quota)를 초과했습니다. 잠시 후 다시 시도하거나 요금제를 확인하세요. (429 RESOURCE_EXHAUSTED)"
        if "401" in msg or "403" in msg or "api_key_invalid" in lower or "permission_denied" in lower:
            return "Gemini API 키가 유효하지 않거나 접근 권한이 없습니다. API 키 설정을 확인하세요."
        return f"Gemini API 호출 실패: {msg[:200]}"

    @property
    def is_available(self) -> bool:
        """Gemini AI 실제 사용 가능 여부"""
        return self._client is not None

    async def generate_text(self, prompt: str) -> str:
        """텍스트 기반 AI 생성 (쿼터 소진 시 가용 모델 자동 폴백)"""
        if not self._client:
            if not self.last_error:
                self.last_error = "GEMINI_API_KEY가 설정되지 않았습니다."
            return self._mock_text_response(prompt, self.last_error)

        self.last_error = None
        full_prompt = f"{self.system_prompt}\n\n{prompt}" if self.system_prompt else prompt
        models_to_try = [self.model] + [m for m in FALLBACK_MODELS if m != self.model]
        last_exc: Optional[Exception] = None

        for model_name in models_to_try:
            try:
                response = self._client.models.generate_content(
                    model=model_name,
                    contents=full_prompt,
                )
                if response and response.text:
                    if model_name != self.model:
                        logger.info(f"Gemini 모델 폴백 성공: {self.model} -> {model_name}")
                        self.model = model_name
                    return response.text
            except Exception as e:
                last_exc = e
                logger.warning(f"Gemini 모델({model_name}) 요청 실패, 다음 후보 시도: {e}")
                continue

        self.last_error = (
            self._format_api_error(last_exc)
            if last_exc is not None
            else "Gemini API에서 빈 응답을 반환했습니다."
        )
        logger.error(f"모든 Gemini 텍스트 모델 요청 실패: {self.last_error}")
        return self._mock_text_response(prompt, self.last_error)

    async def generate_with_image(self, prompt: str, image_base64: str, mime_type: str = "image/jpeg") -> str:
        """이미지 + 텍스트 기반 AI 생성 (관상 분석, 스마트팜 진단 등)"""
        if not self._client:
            if not self.last_error:
                self.last_error = "GEMINI_API_KEY가 설정되지 않았습니다."
            return self._mock_image_response(self.last_error)

        self.last_error = None
        try:
            import base64
            clean_b64 = image_base64.split(",")[1] if "," in image_base64 else image_base64
            image_bytes = base64.b64decode(clean_b64)
            full_prompt = f"{self.system_prompt}\n\n{prompt}" if self.system_prompt else prompt

            if types is None:
                self.last_error = "google-genai types 모듈을 불러올 수 없습니다."
                return self._mock_image_response(self.last_error)
            image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

            models_to_try = [self.model] + [m for m in FALLBACK_MODELS if m != self.model]
            last_exc: Optional[Exception] = None
            for model_name in models_to_try:
                try:
                    response = self._client.models.generate_content(
                        model=model_name,
                        contents=[image_part, full_prompt],
                    )
                    if response and response.text:
                        return response.text
                except Exception as e:
                    last_exc = e
                    logger.warning(f"Gemini 이미지 분석 ({model_name}) 실패, 다음 후보 시도: {e}")
                    continue

            self.last_error = (
                self._format_api_error(last_exc)
                if last_exc is not None
                else "Gemini API에서 빈 응답을 반환했습니다."
            )
            logger.error(f"모든 Gemini 이미지 모델 요청 실패: {self.last_error}")
            return self._mock_image_response(self.last_error)
        except Exception as e:
            self.last_error = self._format_api_error(e)
            logger.error(f"Gemini 이미지 분석 오류: {e}")
            return self._mock_image_response(self.last_error)

    async def generate_structured(
        self,
        prompt: str,
        schema: Optional[Dict[str, Any]] = None,
        fallback_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """구조화된 JSON 응답 생성 (데이터 분석 등)"""
        if not self.is_available:
            err = self.last_error or "GEMINI_API_KEY가 설정되지 않았습니다."
            if fallback_data:
                return {**fallback_data, "is_mock": True, "error_message": err}
            return {"mock": True, "is_mock": True, "error_message": err}

        json_prompt = f"{prompt}\n\n응답은 반드시 마크다운이나 부가 설명 없이 순수한 JSON 형식으로만 작성하세요."
        text = await self.generate_text(json_prompt)

        if self.last_error:
            if fallback_data:
                return {**fallback_data, "is_mock": True, "error_message": self.last_error}
            return {"raw": text, "mock": True, "is_mock": True, "error_message": self.last_error}

        try:
            clean = clean_json_markdown(text)
            parsed = json.loads(clean)
            if isinstance(parsed, dict):
                parsed.setdefault("is_mock", False)
            return parsed
        except Exception as e:
            err = f"Gemini 응답 JSON 파싱 실패: {e}"
            self.last_error = err
            logger.warning(f"JSON 파싱 실패 ({e}), fallback 또는 원본 반환")
            if fallback_data:
                return {**fallback_data, "is_mock": True, "error_message": err}
            return {"raw": text, "parse_error": True, "is_mock": True, "error_message": err}

    async def generate_structured_with_image(
        self,
        prompt: str,
        image_base64: str,
        mime_type: str = "image/jpeg",
        schema: Optional[Dict[str, Any]] = None,
        fallback_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """이미지 기반 구조화된 JSON 데이터 분석 및 생성 (관상, 얼굴분석, 작물진단 등)"""
        if not self.is_available:
            err = self.last_error or "GEMINI_API_KEY가 설정되지 않았습니다."
            if fallback_data:
                return {**fallback_data, "is_mock": True, "error_message": err}
            return json.loads(self._mock_image_response(err))

        b64_clean, detected_mime = extract_base64_data(image_base64, default_mime=mime_type)
        json_prompt = f"{prompt}\n\n응답은 반드시 마크다운(```json) 없이 순수한 JSON 형식으로만 작성하세요."
        raw_text = ""
        try:
            raw_text = await self.generate_with_image(json_prompt, b64_clean, mime_type=detected_mime)
            clean = clean_json_markdown(raw_text)
            parsed = json.loads(clean)
            if isinstance(parsed, dict) and parsed.get("mock") is True:
                err = self.last_error or parsed.get("message") or "Gemini API 호출에 실패했습니다."
                if fallback_data:
                    return {**fallback_data, "is_mock": True, "error_message": err}
                return {**parsed, "is_mock": True, "error_message": err}
            if isinstance(parsed, dict):
                parsed.setdefault("is_mock", False)
            return parsed
        except Exception as e:
            err = self.last_error or f"Gemini 응답 JSON 파싱 실패: {e}"
            logger.warning(f"Gemini 이미지 구조화 파싱 실패 ({e})")
            if fallback_data:
                return {**fallback_data, "is_mock": True, "error_message": err}
            return {"raw": raw_text, "parse_error": True, "is_mock": True, "error_message": err}

    def generate_with_image_sync(
        self,
        prompt: str,
        image_base64: str,
        mime_type: str = "image/jpeg",
        fallback_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """동기 컨텍스트에서 안전하게 이미지 구조화 분석을 수행하는 헬퍼"""
        import asyncio
        import concurrent.futures
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        asyncio.run,
                        self.generate_structured_with_image(prompt, image_base64, mime_type=mime_type, fallback_data=fallback_data)
                    )
                    return future.result()
            else:
                return asyncio.run(
                    self.generate_structured_with_image(prompt, image_base64, mime_type=mime_type, fallback_data=fallback_data)
                )
        except Exception as e:
            err = self._format_api_error(e)
            self.last_error = err
            logger.warning(f"generate_with_image_sync 실행 오류: {e}")
            if fallback_data:
                return {**fallback_data, "is_mock": True, "error_message": err}
            return {"mock": True, "is_mock": True, "error_message": err}

    # ─── Mock 응답 (API Key 없을 때 / 호출 실패 시) ───────────
    def _mock_text_response(self, prompt: str, reason: Optional[str] = None) -> str:
        detail = reason or "실제 운영 시 GEMINI_API_KEY를 설정하세요"
        return f"[Mock AI 응답] '{prompt[:30]}...' 에 대한 샘플 응답입니다. ({detail})"

    def _mock_image_response(self, reason: Optional[str] = None) -> str:
        return json.dumps({
            "mock": True,
            "is_mock": True,
            "message": reason or "이미지 분석 Mock 응답입니다. GEMINI_API_KEY를 설정하세요.",
            "error_message": reason or "GEMINI_API_KEY가 설정되지 않았습니다.",
        }, ensure_ascii=False)
