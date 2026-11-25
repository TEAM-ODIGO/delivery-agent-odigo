// 화면에서 쓰는 이미지 경로와 미리 받아 두기.
// 서버(시드니)까지 요청 한 번에 약 0.2초가 걸려서, 후보 화면·결제수단 화면이 뜬 뒤에 이미지를 요청하면 늦게 보인다.
// 첫 화면이 뜬 뒤 한가할 때 미리 받아 두면, 브라우저 캐시(nginx에서 7일)에서 바로 꺼내 보여 준다.

// 음식 종류(A의 CUISINES 키) → 후보 카드 오른쪽 이미지 (public/images/cuisine)
export const CUISINE_IMAGES: Record<string, string> = {
  치킨: "chicken",
  피자: "pizza",
  양식: "western",
  버거: "burger",
  중식: "chinese",
  일식: "japanese",
  "회/초밥": "sushi",
  한식: "korean",
  분식: "snack",
  "찜/탕": "stew",
  "족발/보쌈": "jokbal",
  "고기/구이": "meat",
  "도시락/죽": "lunchbox",
  샐러드: "salad",
  샌드위치: "sandwich",
  아시안: "asian",
  "카페/디저트": "cafe",
};

export const cuisineImage = (cuisine?: string | null) =>
  `/images/cuisine/${(cuisine && CUISINE_IMAGES[cuisine]) || "default"}.webp`;

// 미리 받아 둘 카드 이미지 (실제로 파일이 있는 것만. 없는 파일을 넣으면 404 요청만 늘어난다)
const CARD_IMAGES = [
  // 결제수단 목록 (CardImage, public/cards)
  "/cards/CARD001.webp",
  "/cards/CARD002.webp",
  "/cards/CARD003.webp",
  "/cards/PRD-CARD-D.webp",
  "/cards/PRD-CARD-E.webp",
  "/cards/PRD-CARD-F.webp",
  // NFC 카드 모달 앞면 (CardFlipModal, public/images/cards)
  "/images/cards/card-a.webp",
  "/images/cards/card-b.webp",
  "/images/cards/card-c.webp",
];

function preloadList(): string[] {
  const list: string[] = [];
  for (const name of Object.values(CUISINE_IMAGES)) {
    list.push(`/images/cuisine/${name}.webp`);
  }
  list.push("/images/cuisine/default.webp");
  for (const src of CARD_IMAGES) {
    list.push(src);
  }
  return list;
}

// 받아 둔 Image 객체를 잡아 둔다 (받는 도중에 가비지 컬렉션으로 취소되지 않게)
const preloaded: HTMLImageElement[] = [];

export function preloadImages() {
  if (preloaded.length > 0) return; // 이미 시작했으면 다시 하지 않는다
  for (const src of preloadList()) {
    const img = new Image();
    img.decoding = "async";
    img.src = src;
    preloaded.push(img);
  }
}

// 첫 화면을 그리는 데 방해되지 않게, 브라우저가 한가할 때 시작한다
export function preloadImagesWhenIdle() {
  if ("requestIdleCallback" in window) {
    window.requestIdleCallback(preloadImages, { timeout: 2000 });
  } else {
    setTimeout(preloadImages, 1000);
  }
}
