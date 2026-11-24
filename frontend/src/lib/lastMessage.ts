// 주문 문장 기억 (이슈 #68): 후보 화면에서 [이전]을 누르면 처음 화면에 같은 문장을 다시 채운다.
// 이 브라우저 탭에서만 쓰는 편의 기능이라 sessionStorage에 두고, 막혀 있으면 조용히 넘어간다.
const key = (requestId: string) => `odigo.message.${requestId}`;

export function rememberMessage(requestId: string, message: string) {
  try {
    sessionStorage.setItem(key(requestId), message);
  } catch {
    /* 저장소를 못 쓰면 문장을 다시 채우지 않을 뿐 */
  }
}

export function recallMessage(requestId: string): string {
  try {
    return sessionStorage.getItem(key(requestId)) ?? "";
  } catch {
    return "";
  }
}
