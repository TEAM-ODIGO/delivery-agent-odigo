"""요기요 장바구니 담기 (결제 직전에서 멈춤).

- yogiyo.py  : 진짜 구현 (Playwright, 선택 설치)
- fake.py    : 가짜 구현 (테스트·Playwright 없는 개발 환경)
- models.py  : cart_job 테이블 (작업 상태·결과)
- service.py : 작업 시작·조회. 실제 담기는 작업 스레드에서 (5~20초 걸림)

O가 승인 대상 견적(B-06)을 넘겨 주고, 화면은 O API(O-13·O-14)만 부른다. B 코드는 건드리지 않는다.
"""
