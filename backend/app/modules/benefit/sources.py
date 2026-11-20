"""외부 연동 지점. 실제 연동 전까지는 mock을 쓰고, set_*로 교체한다.
- MerchantSource: 한국조폐공사 지역사랑상품권 가맹점 공공데이터 (C-08)
- InferenceClient: A-09 공용 LLM 라우터 (C-10, task_type EXTRACT_BENEFIT_RULES)"""
from typing import Protocol

from .errors import AppError


class MerchantSource(Protocol):
    def fetch(self, region_codes: list[str]) -> list[dict]:
        """가맹점 목록 [{name, address, lat, lng, region_code}]을 반환한다."""


class MockMerchantSource:
    DATA = [
        {"name": "가상치킨 강남점", "address": "서울 강남구 테헤란로 123 (역삼동)", "lat": 37.5001, "lng": 127.0362, "region_code": "11680"},
        {"name": "다른가게", "address": "서울 강남구 논현로 1", "lat": 37.5100, "lng": 127.0300, "region_code": "11680"},
    ]

    def fetch(self, region_codes):
        return [m for m in self.DATA if m["region_code"] in region_codes]


class InferenceClient(Protocol):
    def extract_benefit_rules(self, product_id: str, document: dict) -> dict:
        """{"rules": [...], "warnings": [...], "llm": {"route", "model"}}을 반환한다."""


class UnavailableInference:
    def extract_benefit_rules(self, product_id, document):
        raise AppError(503, "INFERENCE_UNAVAILABLE", "LLM 라우터(A-09)가 연결되지 않았습니다.")


_merchant_source: MerchantSource = MockMerchantSource()
_inference: InferenceClient = UnavailableInference()


def get_merchant_source() -> MerchantSource:
    return _merchant_source


def set_merchant_source(source: MerchantSource) -> None:
    global _merchant_source
    _merchant_source = source


def get_inference() -> InferenceClient:
    return _inference


def set_inference(client: InferenceClient) -> None:
    global _inference
    _inference = client
