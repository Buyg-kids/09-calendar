/* 식당·카페 썸네일 2단계 폴백: ① 실제 사진(TourAPI 등 출처 있는 URL) ② 대표메뉴·업종 키워드로 고른 손그림 ③ 이모지.
 * 카드에 이미 있는 글자(대표메뉴·상호·업종)만 근거로 고르고, 맞는 게 없으면 null 을 돌려준다(지어내지 않음).
 * 손그림 파일: assets/img/food/food_<key>.webp - 실제로 올라온 key 만 FOOD_READY 에 넣는다(없는 파일을 요청하지 않게). */
(function (root) {
  'use strict';
  // 손그림 15종. label 은 사람이 읽는 이름, icon 은 그림이 없을 때 쓰는 이모지.
  var FOOD = {
    tonkatsu: { label: '돈가스', icon: '🍱' }, sushi: { label: '초밥·일식', icon: '🍣' }, bbq: { label: '고기구이', icon: '🥩' },
    noodle: { label: '면류', icon: '🍜' }, pizza_pasta: { label: '피자·파스타', icon: '🍝' }, bakery: { label: '베이커리', icon: '🥐' },
    dessert: { label: '빙수·아이스크림', icon: '🍧' }, brunch: { label: '브런치', icon: '🍳' }, coffee: { label: '커피·음료', icon: '☕' },
    korean: { label: '한식', icon: '🍚' }, chinese: { label: '중식', icon: '🥟' }, western: { label: '양식', icon: '🍽️' },
    snack: { label: '분식', icon: '🍢' }, burger: { label: '버거·샌드위치', icon: '🍔' }, chicken: { label: '치킨·튀김', icon: '🍗' }
  };
  // 위에서부터 먼저 맞는 규칙을 쓴다(좁은 것 -> 넓은 것). 예: '짜장면'은 면류보다 중식, '생선구이'는 고기구이가 아니라 한식.
  var RULES = [
    ['korean', /생선구이|굴비|회무침|물회|매운탕|보쌈|족발|국밥|해장국|순두부|두부전골|전골|한정식|쌈밥|비빔밥|백반|곰탕|갈비탕|삼계탕|불고기|뚝배기|죽/],
    ['chinese', /짜장|짬뽕|탕수육|마라|딤섬|훠궈|양꼬치|중화|중식|볶음밥/],
    ['tonkatsu', /돈가스|돈까스|카츠|까스|가츠|히레|로스카츠/],
    ['sushi', /초밥|스시|사시미|오마카세|돈부리|덮밥|규동|오코노미|타코야끼|일식|일본/],
    ['noodle', /칼국수|국수|냉면|우동|라멘|라면|소바|쌀국수|수제비|파스타면|면요리|막국수/],
    ['pizza_pasta', /피자|파스타|리조또|리조토|뇨끼|화덕|스파게티/],
    ['burger', /버거|샌드위치|샌드|핫도그|토스트|부리토/],
    ['chicken', /치킨|닭강정|후라이드|튀김|닭갈비|닭/],
    ['snack', /분식|떡볶이|김밥|라볶이|순대|어묵|오뎅/],
    ['bbq', /고기|등심|갈비|삼겹|목살|한우|바비큐|숯불|양념|구이|스테이크|곱창|막창/],
    ['brunch', /브런치/],
    ['dessert', /빙수|아이스크림|젤라또|소프트|와플|팬케이크|푸딩/],
    ['bakery', /빵|베이커리|베이글|스콘|바게트|소금빵|크루아상|크루키|케이크|마카롱|쿠키|도넛|타르트|롤|디저트|제과|카스테라/],
    ['coffee', /커피|라떼|아메리카노|음료|에이드|스무디|티$|차$|티\s|슈페너|프라페|더치|카페/]
  ];
  var CATEGORY_FALLBACK = { '한식': 'korean', '일식': 'sushi', '중식': 'chinese', '양식': 'western' };

  function text(v) { return typeof v === 'string' ? v : ''; }
  function menuText(e) { return text(e && e.menu && e.menu.v) + ' ' + text(e && e.recommend_for && e.recommend_for.v) + ' ' + text(e && e.ref_menu && e.ref_menu.name); }
  function match(s) {
    for (var i = 0; i < RULES.length; i++) if (RULES[i][1].test(s)) return RULES[i][0];
    return null;
  }
  // e: 주변 식당·카페 항목(name, kind, category, menu ...). 반환: FOOD 의 key 또는 null.
  function pick(e) {
    if (!e) return null;
    var byMenu = match(menuText(e)); if (byMenu) return byMenu;      // 1) 대표메뉴가 가장 믿을 만한 단서
    var byName = match(text(e.name)); if (byName && byName !== 'coffee') return byName;   // 2) 상호(‘카페’라는 글자만으로는 커피로 단정하지 않는다)
    if (e.kind === '카페' || e.category === '카페') return 'coffee';  // 3) 업종
    return CATEGORY_FALLBACK[e.category] || (byName || null);
  }
  var READY_KEYS = [];   // 손그림 파일(assets/img/food/food_<key>.webp)을 실제로 올린 key 만 여기에 적는다(없는 파일을 요청하지 않게)
  var READY = {};
  function setReady(keys) { READY = {}; (keys || []).forEach(function (k) { if (FOOD[k]) READY[k] = true; }); }
  setReady(READY_KEYS);
  function art(e) { var k = pick(e); return k && READY[k] ? { key: k, src: 'assets/img/food/food_' + k + '.webp', label: FOOD[k].label } : null; }
  function icon(e) { var k = pick(e); return k ? FOOD[k].icon : null; }

  var api = { FOOD: FOOD, RULES: RULES, pick: pick, art: art, icon: icon, setReady: setReady };
  if (typeof module !== 'undefined' && module.exports) module.exports = api; else root.BuygFood = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
