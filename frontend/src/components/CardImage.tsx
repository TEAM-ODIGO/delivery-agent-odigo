// 결제수단 카드 이미지.
// frontend/public/cards/ 에 <결제수단ID>.png 를 넣으면 보인다 (jpg·jpeg·webp·svg도 된다).
//   예) CARD001.png, CARD002.png, CARD003.png, LOCAL001.png
// 파일이 없거나 읽지 못하면 이름이 적힌 기본 카드 모양을 대신 그린다.
//
// 보이는 방향: 기본은 세로형(신한카드 앱의 카드 디자인 화면과 같은 방향).
// frontend/.env 에 VITE_CARD_IMAGE_LAYOUT=landscape 를 쓰면 가로형으로 눕혀서 보여 준다 (개발 서버를 다시 켠다).
import { useState } from "react";

const EXTS = ["png", "jpg", "jpeg", "webp", "svg"];
const LANDSCAPE = import.meta.env.VITE_CARD_IMAGE_LAYOUT === "landscape";
const RATIO = 1.586; // 신용카드 긴 변 : 짧은 변

// 같은 카드 상품을 쓰는 다른 사용자의 결제수단 ID는 같은 이미지를 쓴다
const SAME_IMAGE: Record<string, string> = { CARD101: "CARD001", LOCAL101: "LOCAL001" };
// 내 카드 추가로 새 ID가 생긴 카드도 같은 상품이면 같은 이미지를 쓴다 (상품 ID를 알 때)
const PRODUCT_IMAGE: Record<string, string> = {
  "PRD-CARD-A": "CARD001",
  "PRD-CARD-B": "CARD002",
  "PRD-CARD-C": "CARD003",
  "PRD-LOCAL-A": "LOCAL001",
};

// long: 카드의 긴 변 길이(px). 세로형이면 높이, 가로형이면 너비가 된다. (가로형은 글자 자리가 모자라지 않게 조금 작게)
export function CardImage({
  id,
  name,
  type,
  productId,
  long = LANDSCAPE ? 72 : 88,
}: {
  id: string;
  name: string;
  type?: string | null;
  productId?: string | null;
  long?: number;
}) {
  const [tried, setTried] = useState(0); // 지금까지 실패한 확장자 수
  const [loaded, setLoaded] = useState(false);
  const file = SAME_IMAGE[id] ?? (productId ? PRODUCT_IMAGE[productId] : undefined) ?? id;
  const short = Math.round(long / RATIO);
  // 기본 카드 모양에는 괄호 앞 이름만 쓴다 (예: "서울페이(서울사랑상품권)" → "서울페이"). 칸이 작아 긴 이름은 잘린다
  const label = name.replace(/\s*\(.*\)\s*$/, "").trim() || name;
  const width = LANDSCAPE ? long : short;
  const height = LANDSCAPE ? short : long;

  return (
    <span className={`card-img${LANDSCAPE ? " land" : ""}`} style={{ width, height }} role="img" aria-label={name}>
      {!loaded && (
        <span className={`card-ph${type === "LOCAL_CURRENCY" ? " local" : ""}`}>
          <i className="card-ph-chip" />
          <span className="card-ph-name" style={{ fontSize: `${((short / 56) * 9.5).toFixed(1)}px` }}>
            {label}
          </span>
        </span>
      )}
      {tried < EXTS.length && (
        <img
          className={loaded ? "on" : ""}
          // 이미지 파일은 세로형이라, 가로형일 때는 칸 안에서 90도 돌려 눕힌다
          style={LANDSCAPE ? { width: short, height: long } : undefined}
          src={`/cards/${file}.${EXTS[tried]}`}
          alt=""
          onLoad={() => setLoaded(true)}
          onError={() => setTried((t) => t + 1)}
        />
      )}
    </span>
  );
}
