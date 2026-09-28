// Buyg 최소형(패스스루) 서비스 워커.
//
// 목적은 오프라인 캐싱이 아니라 "PWA 설치 가능(installable) 판정"이다 - 일부
// 브라우저(특히 구형 Chrome/일부 안드로이드 웹뷰)는 manifest.json만으로는
// beforeinstallprompt를 쏘지 않고, fetch 핸들러가 있는 서비스 워커 등록까지
// 요구한다. 그래서 캐시 저장 없이 모든 요청을 그냥 네트워크로 통과시키기만 한다.
// 나중에 진짜 오프라인 지원이 필요해지면 이 파일에 캐시 로직을 추가하면 된다.

self.addEventListener("install", function (event) {
  // 새 서비스 워커가 설치되자마자 곧장 활성화되게 한다(기존 대기 중인
  // 워커가 있어도 기다리지 않음) - 이 파일은 계속 no-op이라 이전 버전과
  // 동작 차이가 없으므로 굳이 기존 탭이 닫히길 기다릴 이유가 없다.
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  // 이미 열려 있는 탭들도 바로 이 워커의 제어를 받게 한다(새로고침 없이).
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", function (event) {
  // 캐시를 전혀 만지지 않고 있는 그대로 네트워크에 위임한다 - 오프라인
  // 지원이 아니라 "서비스 워커가 이 스코프를 제어하고 있다"는 사실 자체가
  // 목적이다.
  event.respondWith(fetch(event.request));
});
