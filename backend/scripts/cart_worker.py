"""요기요 PC 작업기 — 배포 서버의 담기 작업(YOGIYO_CART_MODE=remote)과
후보 식당 실시간 확인 작업(LIVE_CHECK_MODE=remote, #80)을 이 PC에서 처리한다.

배포 서버(해외 클라우드 IP)는 요기요가 Access Denied로 막는다.
그래서 서버는 작업을 쌓아 두기만 하고,
한국에 있는 이 PC가 몇 초마다 작업을 가져가(claim) 지금 로컬과 같은 방식(창을 띄운 크로미움)으로
요기요 장바구니에 담은 뒤, 결과와 캡처를 서버에 올린다. 요기요의 주문하기·결제는 누르지 않는다.

준비: uv sync (크로미움은 처음 담을 때 자동으로 받는다)
실행 (backend 폴더에서):
    uv run python scripts/cart_worker.py --server https://odigo2.duckdns.org --token <토큰>
    또는 .env에 YOGIYO_WORKER_SERVER · YOGIYO_WORKER_TOKEN 을 넣고
    uv run --env-file .env python scripts/cart_worker.py
멈추기: Ctrl+C

- 토큰은 배포 서버의 INTERNAL_SERVICE_TOKEN (EC2의 ~/odigo-secrets.txt)과 같아야 한다.
- 담은 요기요 창은 다음 작업이 올 때까지 열어 둔다 (시연 때 화면에 보이게).
- 서버가 기다리는 시간(YOGIYO_CART_REMOTE_WAIT_SECONDS, 기본 30초) 안에 가져가지 못하면
  서버가 시연 모드(fake)로 마무리한다. 그때 올린 결과는 409로 거절된다.
- 실시간 확인 작업을 담기보다 먼저 처리한다 (후보 화면이 기다리는 작업). 서버가
  LIVE_CHECK_REMOTE_WAIT_SECONDS(기본 20초) 안에 못 가져가면 후보는 '요기요 확인 못 함'으로 넘어간다.
"""

import argparse
import base64
import logging
import os
import socket
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ 를 import 경로에 추가

from app.modules.cart.yogiyo import BrowserKeeper, CartItem, CartOrder, CartResult  # noqa: E402
from app.modules.livecheck.remote import (  # noqa: E402
    check_on_browser,
    result_to_json,
    target_from_json,
)

log = logging.getLogger("cart_worker")
CLAIM = "/orchestrator/cart-worker/jobs/claim"
LIVE_CLAIM = "/orchestrator/cart-worker/live-checks/claim"


def _order(job: dict) -> CartOrder:
    items = []
    for i in job["items"]:
        items.append(
            CartItem(
                menu_id=str(i["menu_id"]),
                quantity=int(i["quantity"]),
                name=i.get("name"),
                unit_price_krw=i.get("unit_price_krw"),
            )
        )
    return CartOrder(
        restaurant_id=str(job["restaurant_id"]),
        items=items,
        expected_menu_subtotal_krw=job.get("expected_menu_subtotal_krw"),
        expected_delivery_fee_krw=job.get("expected_delivery_fee_krw"),
    )


def _payload(r: CartResult) -> dict:
    shot = None
    if r.screenshot and Path(r.screenshot).is_file():
        shot = base64.b64encode(Path(r.screenshot).read_bytes()).decode()
    return {
        "status": r.status,
        "cart_items": r.cart_items,
        "menu_subtotal_krw": r.menu_subtotal_krw,
        "delivery_fee_krw": r.delivery_fee_krw,
        "mismatches": r.mismatches,
        "error": r.error,
        "url": r.url,
        "screenshot_base64": shot,
    }


def handle(client: httpx.Client, keeper: BrowserKeeper, job: dict, args) -> None:
    job_id = job["job_id"]
    log.info("작업 %s: 식당 %s 담기 시작", job_id, job["restaurant_id"])
    try:
        r = keeper.fill(
            _order(job),
            headless=False,
            browser_channel="chrome" if args.chrome else None,
            out_dir=args.out,
            name=job_id,
        )
    except Exception as exc:  # noqa: BLE001 — 브라우저 실행 실패도 결과로 올린다
        log.exception("담기 중 오류")
        r = CartResult(
            status="FAILED",
            restaurant_id=str(job["restaurant_id"]),
            error=f"PC 작업기 오류 — {type(exc).__name__}: {exc}",
        )
    log.info("작업 %s: %s %s", job_id, r.status, r.error or "")

    res = client.post(f"/orchestrator/cart-worker/jobs/{job_id}/result", json=_payload(r))
    if res.status_code == 409:
        log.warning("작업 %s: 서버가 이미 마무리한 작업이라 결과를 받지 않았어요", job_id)
    else:
        res.raise_for_status()
        log.info("작업 %s: 결과를 서버에 올렸어요", job_id)


def handle_live(client: httpx.Client, keeper: BrowserKeeper, job: dict, args) -> None:
    """후보 식당 실시간 확인 (#80): 식당 화면을 읽어 판정과 원본을 서버에 올린다."""
    job_id = job["job_id"]
    raw = job.get("targets") or []
    targets = [target_from_json(t) for t in raw]
    cached = {i: t["cached"] for i, t in enumerate(raw) if t.get("cached")}
    log.info("확인 %s: 후보 %d곳 (최근 확인 %d곳) 시작", job_id, len(targets), len(cached))
    body: dict = {"results": [], "error": None}
    try:
        results = keeper.with_browser(
            lambda browser: check_on_browser(
                browser,
                targets,
                cached=cached,
                concurrency=int(job.get("concurrency") or 3),
                want=int(job.get("want") or 3),
                timeout_seconds=float(job.get("timeout_seconds") or 8),
            ),
            headless=False,
            browser_channel="chrome" if args.chrome else None,
        )
        body["results"] = [result_to_json(int(t["index"]), r) for t, r in zip(raw, results, strict=True)]
        log.info("확인 %s: %s", job_id, ", ".join(f"{r.restaurant_id}={r.status}" for r in results))
    except Exception as exc:  # noqa: BLE001 — 브라우저 실행 실패도 결과로 올린다
        log.exception("확인 중 오류")
        body["error"] = f"PC 작업기 오류 — {type(exc).__name__}: {exc}"[:500]
    res = client.post(f"/orchestrator/cart-worker/live-checks/{job_id}/result", json=body)
    if res.status_code == 409:
        log.warning("확인 %s: 서버가 이미 마무리한 작업이라 결과를 받지 않았어요", job_id)
    else:
        res.raise_for_status()


def claim_live(client: httpx.Client, keeper: BrowserKeeper, name: str, args, state: dict) -> bool:
    """실시간 확인 작업이 있으면 처리하고 True. 이 기능이 없는 서버(404)면 다음부터 묻지 않는다."""
    if not state.get("live", True):
        return False
    res = client.post(LIVE_CLAIM, json={"worker": name})
    if res.status_code == 401:  # 토큰 오류는 아래 담기 작업 요청에서 안내하고 멈춘다
        return False
    if res.status_code == 404:
        log.info("서버에 실시간 확인 작업 API가 없어 담기 작업만 처리해요")
        state["live"] = False
        return False
    res.raise_for_status()
    if res.status_code == 200:
        handle_live(client, keeper, res.json(), args)
        return True
    return False


def main() -> int:
    p = argparse.ArgumentParser(description="요기요 장바구니 PC 작업기 (YOGIYO_CART_MODE=remote)")
    p.add_argument("--server", default=os.getenv("YOGIYO_WORKER_SERVER", ""), help="배포 서버 주소")
    p.add_argument("--token", default=os.getenv("YOGIYO_WORKER_TOKEN", ""), help="서비스 토큰")
    p.add_argument("--name", default=socket.gethostname(), help="작업기 이름 (서버 로그용)")
    p.add_argument("--interval", type=float, default=2.0, help="작업 확인 간격(초)")
    p.add_argument("--chrome", action="store_true", help="설치된 Chrome으로 실행")
    p.add_argument("--out", default="cart_runs_worker", help="캡처 저장 폴더")
    p.add_argument("--once", action="store_true", help="작업 하나만 처리하고 끝낸다 (확인용)")
    a = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # 2초마다 찍히는 요청 로그는 숨긴다
    if not a.server or not a.token:
        print("--server 와 --token (또는 YOGIYO_WORKER_SERVER · YOGIYO_WORKER_TOKEN)이 필요해요.")
        return 2

    keeper = BrowserKeeper()
    headers = {"Authorization": f"Bearer {a.token}"}
    with httpx.Client(base_url=a.server.rstrip("/"), headers=headers, timeout=30) as client:
        log.info("작업기 시작: %s (Ctrl+C로 멈춤)", a.server)
        try:
            state: dict = {}
            while True:
                try:
                    if claim_live(client, keeper, a.name, a, state):  # 후보 화면이 기다리는 작업 먼저
                        if a.once:
                            return 0
                        continue
                    res = client.post(CLAIM, json={"worker": a.name})
                    if res.status_code == 401:
                        log.error("토큰이 맞지 않아요 (서버의 INTERNAL_SERVICE_TOKEN 확인).")
                        return 1
                    res.raise_for_status()
                    if res.status_code == 200:
                        handle(client, keeper, res.json(), a)
                        if a.once:
                            return 0
                        continue  # 쌓인 작업이 더 있을 수 있으니 바로 다시 확인
                except httpx.HTTPError as exc:
                    log.warning("서버 연결 실패 (%s) — 잠시 뒤 다시 시도", exc)
                    time.sleep(5)
                time.sleep(a.interval)
        except KeyboardInterrupt:
            log.info("작업기를 멈춥니다")
        finally:
            keeper.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
