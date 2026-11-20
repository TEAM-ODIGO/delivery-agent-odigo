"""O · 오케스트레이터 모듈 (MVP 1).

화면이 부르는 유일한 모듈. 주문 1건을 A → D-01 → A → B → C → B → D-02 → D-03 → B → E 순서로
진행하고, 사용자 확인이 필요한 곳(재질문·후보·결제수단·승인)에서 멈췄다가 이어 간다.

    service.py  상태 머신 (유일한 입구)
    router.py   HTTP 입구 O-01~O-05
    ports.py    A·B·C·E에 기대하는 모양 (+ E HTTP 어댑터)
    wiring.py   지금 쓸 A·B·C·D·E 고르기 (A·B·C는 app/fakes)
"""
