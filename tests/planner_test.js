// planner.js 단위 테스트 (node tests/planner_test.js). 파이썬 unittest(tests/test_planner_js.py)가 호출한다.
const assert = require('node:assert/strict');
const path = require('node:path');
const P = require(path.join(__dirname, '..', 'planner.js'));
const guides = require(path.join(__dirname, '..', 'place_guides.json')).places;
const museum = guides.spot_06, arbo = guides.spot_57, zoo = guides.guide_seoulgrandpark;
let n = 0;
function t(name, fn) { try { fn(); n++; } catch (e) { console.error('FAIL', name, '\n', e.message); process.exitCode = 1; } }

t('parkingFee: 경기도박물관 요금표', () => {
  const k = museum.costs.parking;
  assert.equal(P.parkingFee(k, 20), 0);          // 30분 이내 무료
  assert.equal(P.parkingFee(k, 30), 0);
  assert.equal(P.parkingFee(k, 45), 1000);       // 기본 1시간
  assert.equal(P.parkingFee(k, 60), 1000);
  assert.equal(P.parkingFee(k, 61), 1200);       // 초과 10분당 200원(올림)
  assert.equal(P.parkingFee(k, 120), 2200);
  assert.equal(P.parkingFee(k, 600), 8000);      // 1일 최대
});
t('parkingFee: 서울대공원 요금표', () => {
  const k = zoo.costs.parking;
  assert.equal(P.parkingFee(k, 25), 0);
  assert.equal(P.parkingFee(k, 120), 3000);      // 기본 2시간
  assert.equal(P.parkingFee(k, 150), 4000);      // 초과 30분당 1,000원
  assert.equal(P.parkingFee(k, 180), 5000);      // 3000 + 2×1,000 = 5,000(= 1일 최대)
  assert.equal(P.parkingFee(k, 181), 5000);
  assert.equal(P.parkingFee(k, 400), 5000);      // 1일 최대(특별할인 적용가)
});
t('입장료: 서울대공원 가족 합산(어른2·영유아1)', () => {
  const r = P.plan({ party: { adult: 2, teen: 0, child: 0, toddler: 1 }, car: false, visit_minutes: 90 }, zoo);
  const a = r.budget.lines.find(l => l.key === 'admission');
  assert.equal(a.amount, 10000);                 // 5,000×2 + 영유아 무료
  assert.equal(r.budget.known_total, 10000);
  assert.equal(r.budget.unknown.length, 0);
});
t('입장료: 어린이·청소년 요금 + 테마가든 옵션', () => {
  const r = P.plan({ party: { adult: 2, teen: 1, child: 1, toddler: 1 }, car: false, extras: { theme_garden: true } }, zoo);
  assert.equal(r.budget.lines.find(l => l.key === 'admission').amount, 5000 * 2 + 3000 + 2000);       // 15,000
  assert.equal(r.budget.lines.find(l => l.key === 'extra_theme_garden').amount, 2000 * 2 + 1500 + 1000); // 6,500
  assert.equal(r.budget.known_total, 21500);
});
t('경기도박물관: 입장 무료 + 차량 주차비 합산', () => {
  const r = P.plan({ party: { adult: 2, toddler: 1 }, car: true, visit_minutes: 90 }, museum);
  assert.equal(r.budget.lines.find(l => l.key === 'admission').amount, 0);
  assert.equal(r.budget.lines.find(l => l.key === 'parking').amount, 1000 + 3 * 200);   // 90분 -> 1,600원
  assert.equal(r.budget.known_total, 1600);
});
t('국립수목원: 입장료·주차비 모두 확인 필요(합계에 더하지 않음)', () => {
  const r = P.plan({ party: { adult: 2, toddler: 1 }, car: true, visit_minutes: 120 }, arbo);
  assert.equal(r.budget.known_total, 0);
  assert.deepEqual(r.budget.unknown.map(u => u.key).sort(), ['admission', 'parking']);
  assert.ok(r.budget.unknown.every(u => u.note && u.note.length > 0));
});
t('차량 미이용이면 주차비 0원이고 확인 필요가 아니다', () => {
  const r = P.plan({ party: { adult: 2 }, car: false }, arbo);
  assert.equal(r.budget.lines.find(l => l.key === 'parking').amount, 0);
  assert.deepEqual(r.budget.unknown.map(u => u.key), ['admission']);
});
t('식비·카페비: 직접 입력 > 대표메뉴 기준가 예상치(어른 수 × 1인 기준가) > 확인 필요', () => {
  const base = { party: { adult: 2 }, car: false, visit_minutes: 60, eat_id: 'eat_hwangje_jjamppong' };
  let r = P.plan(base, museum);                                   // 입력 없음 -> 대표메뉴 바지락짬뽕 10,000원 × 어른 2명
  assert.equal(r.budget.estimates.length, 1);
  assert.equal(r.budget.estimates[0].amount, 20000);
  assert.ok(r.budget.estimates[0].detail.indexOf('바지락짬뽕') >= 0 && r.budget.estimates[0].detail.indexOf('어른 2명') >= 0);
  assert.equal(r.budget.known_total, 0);                          // 공식 요금 합계에는 섞이지 않는다
  assert.equal(r.budget.estimate_total, 20000);
  assert.equal(r.budget.total, 20000);
  assert.ok(!r.budget.unknown.some(u => u.key === 'meal'));
  r = P.plan(Object.assign({}, base, { manual: { meal: 32000 } }), museum);   // 직접 입력이 우선
  assert.equal(r.budget.estimates.length, 0);
  assert.equal(r.budget.manual[0].amount, 32000);
  assert.equal(r.budget.known_total, 32000); assert.equal(r.budget.total, 32000);
  const noRef = JSON.parse(JSON.stringify(museum)); delete noRef.eats.find(e => e.id === 'eat_hwangje_jjamppong').ref_menu;
  r = P.plan(base, noRef);                                        // 기준가가 없으면 일반 외식 기본값(1인 12,000원)으로 추정
  assert.ok(!r.budget.unknown.some(u => u.key === 'meal'));
  assert.equal(r.budget.estimates[0].amount, 24000); assert.equal(r.budget.estimates[0].basis, 'default'); assert.equal(r.budget.total, 24000);
});
t('기본 추정치: 식당 1인 12,000원·카페 1인 6,000원(어른+청소년), 직접 입력이 우선, 주차 무료 모델', () => {
  const place = { id: 'x', name: '시험 장소', costs: { admission: { model: 'free', note: '무료입장(일반 정보)' }, parking: { model: 'free', note: '무료주차(일반 정보)' } },
    eats: [{ id: 'e1', kind: '식당', name: 'a', level: 'listed', distance: { v: 'v', mode: 'straight', meters: 800 } }, { id: 'c1', kind: '카페', name: 'b', level: 'listed', distance: { v: 'v', mode: 'straight', meters: 900 } }] };
  let r = P.plan({ party: { adult: 2, teen: 1, child: 1 }, car: true, eat_id: 'e1', cafe_id: 'c1' }, place);
  assert.equal(r.budget.estimates.find(x => x.key === 'meal').amount, 36000);    // (어른 2 + 청소년 1) × 12,000
  assert.equal(r.budget.estimates.find(x => x.key === 'cafe').amount, 18000);    // 3 × 6,000
  assert.equal(r.budget.unknown.length, 0); assert.equal(r.budget.known_total, 0); assert.equal(r.budget.total, 54000);
  assert.ok(r.budget.lines.some(l => l.key === 'parking' && l.amount === 0 && l.detail.indexOf('무료주차') >= 0));
  r = P.plan({ party: { adult: 2 }, car: true, eat_id: 'e1', cafe_id: 'c1', manual: { meal: 20000 } }, place);
  assert.equal(r.budget.manual[0].amount, 20000); assert.equal(r.budget.estimates.length, 1); assert.equal(r.budget.total, 20000 + 12000);
  r = P.plan({ party: { adult: 0, toddler: 2 }, car: false, eat_id: 'e1' }, place);   // 어른이 없어도 최소 1명 기준
  assert.equal(r.budget.estimates[0].amount, 12000);
});
t('기준가 계산: 어른 0명이면 0원, 인원 변화 반영, 수목원 동이손만두는 1인 환산 표기', () => {
  assert.equal(P.plan({ party: { adult: 0, toddler: 2 }, car: false, eat_id: 'eat_hwangje_jjamppong' }, museum).budget.estimate_total, 0);
  assert.equal(P.plan({ party: { adult: 3 }, car: false, eat_id: 'eat_samdae_dubu' }, museum).budget.estimate_total, 33000);
  const r = P.plan({ party: { adult: 2 }, car: false, eat_id: 'eat_kn_dongi_mandu', cafe_id: 'eat_kn_poook' }, arbo);
  assert.equal(r.budget.estimates.find(x => x.key === 'meal').amount, 26000);
  assert.ok(r.budget.estimates.find(x => x.key === 'meal').detail.indexOf('1인 환산 약') >= 0);
  assert.equal(r.budget.estimates.find(x => x.key === 'cafe').amount, 13000);
});
t('걸어서 가는 식당이면 식사 시간만큼 박물관 주차장 시간이 늘어난다', () => {
  const base = { party: { adult: 2 }, car: true, visit_minutes: 60 };
  const none = P.plan(base, museum).budget.lines.find(l => l.key === 'parking').amount;
  const walk = P.plan(Object.assign({}, base, { eat_id: 'eat_hwangje_jjamppong' }), museum).budget.lines.find(l => l.key === 'parking').amount;
  const car = P.plan(Object.assign({}, base, { eat_id: 'eat_samdae_dubu' }), museum).budget.lines.find(l => l.key === 'parking').amount;
  assert.equal(none, 1000);
  assert.equal(walk, 1000 + Math.ceil(60 / 10) * 200);      // 120분 -> 2,200원
  assert.equal(car, 1000);                                  // 차로 이동하면 박물관 주차 시간은 늘지 않는다
});
t('타임테이블: 10:30 시작, 피크(12~13시)와 겹치면 13:00 이후로 미루고 경고', () => {
  const r = P.plan({ party: { adult: 2 }, car: true, visit_minutes: 90, eat_id: 'eat_samdae_dubu' }, museum);
  assert.equal(r.timetable[0].start, '10:30');
  const meal = r.timetable.find(x => x.kind === 'meal');
  assert.equal(meal.start, '13:00');
  assert.ok(r.timetable.some(x => x.kind === 'wait'));
  assert.ok(r.warnings.some(w => w.indexOf('12~13시') >= 0));
  assert.equal(r.end_time, '14:00');
});
t('타임테이블: 피크 정보 없는 식당은 그대로 이어서 배치', () => {
  const r = P.plan({ party: { adult: 2 }, visit_minutes: 90, eat_id: 'eat_hwangje_jjamppong' }, museum);
  assert.ok(!r.timetable.some(x => x.kind === 'wait'));
  const meal = r.timetable.find(x => x.kind === 'meal');
  assert.equal(meal.start, '12:05');           // 12:00 + 도보 3분 -> 5분 단위 반올림 5분
});
t('타임테이블: 종료 시각 초과 경고', () => {
  const r = P.plan({ party: { adult: 2 }, visit_minutes: 240, eat_id: 'eat_samdae_dubu' }, museum);
  assert.ok(r.warnings.some(w => w.indexOf('넘겨요') >= 0));
});
t('입력 방어: 음수·문자·누락 값', () => {
  const r = P.plan({ party: { adult: -3, toddler: 'x' }, visit_minutes: 'abc', manual: { meal: -5 } }, zoo);
  assert.equal(r.party_count, 0);
  assert.equal(r.timetable[0].end, '12:00');   // 기본 90분
  assert.equal(r.budget.known_total, 0);
});
t('normalizePlan: 기본값·범위·식별자 정리', () => {
  const d = P.normalizePlan(null);
  assert.deepEqual(d.party, { adult: 2, teen: 0, child: 0, toddler: 1 });
  assert.equal(d.visit_minutes, 90); assert.equal(d.car, true); assert.equal(d.eat_id, null); assert.equal(d.lodging, 'day');
  const x = P.normalizePlan({ place_id: 'spot_06<script>', party: { adult: 99, teen: -1, child: 'a' }, visit_minutes: 77, eat_id: 'a b', manual: { meal: -1, cafe: 12000.9 }, car: false });
  assert.equal(x.place_id, 'spot_06script'); assert.equal(x.party.adult, 9); assert.equal(x.party.teen, 0);
  assert.equal(x.visit_minutes, 90); assert.equal(x.eat_id, 'ab'); assert.equal(x.manual.meal, null); assert.equal(x.manual.cafe, 12000); assert.equal(x.car, false);
});
t('LocalStorage 저장/불러오기/삭제(주입한 저장소)', () => {
  const store = {}; globalThis.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; }, removeItem: k => { delete store[k]; } };
  const P2 = (delete require.cache[require.resolve('../planner.js')], require('../planner.js'));
  P2.savePlan({ place_id: 'spot_06', party: { adult: 3 }, eat_id: 'eat_samdae_dubu' });
  assert.ok(store[P2.STORE_KEY]);
  const back = P2.loadPlan(); assert.equal(back.place_id, 'spot_06'); assert.equal(back.party.adult, 3); assert.ok(back.updated_at > 0);
  store[P2.STORE_KEY] = '{깨진 json'; assert.equal(P2.loadPlan().place_id, '');       // 손상 데이터는 기본값으로
  P2.clearPlan(); assert.equal(P2.STORE_KEY in store, false);
  delete globalThis.localStorage;
});
t('legTravel: 확인된 분 > 공원 안 추정 > 직선거리 추정', () => {
  assert.deepEqual(P.legTravel({ mode: 'walk', min_minutes: 3, max_minutes: 3 }), { minutes: 5, estimate: false, walk: true, stays: true });
  assert.deepEqual(P.legTravel({ mode: 'car', min_minutes: 3, max_minutes: 5 }), { minutes: 5, estimate: false, walk: false, stays: false });
  assert.deepEqual(P.legTravel({ mode: 'in_park', meters: 0 }), { minutes: 10, estimate: true, walk: true, stays: true });
  assert.equal(P.legTravel({ mode: 'straight', meters: 1833 }).minutes, 10);
  assert.equal(P.legTravel({ mode: 'straight', meters: 5200 }).minutes, 20);      // 5.2km × 3분 = 15.6 -> 20
  assert.equal(P.legTravel({ mode: 'straight', meters: 300 }).minutes, 10);       // 최소 10분
  assert.equal(P.legTravel({}).estimate, true);
});
t('서울대공원: 공원 안 식당·카페(목록형) - 이동은 추정, 차는 공원 주차장에 계속 있다', () => {
  const r = P.plan({ party: { adult: 2, toddler: 1 }, car: true, visit_minutes: 90, eat_id: 'eat_sg_giraffe_foodcourt', cafe_id: 'eat_sg_wonder_cafe' }, zoo);
  const kinds = r.timetable.map(x => x.kind);
  assert.deepEqual(kinds, ['place', 'move', 'meal', 'cafe', 'end']);          // 같은 공원 안이면 카페 이동 행을 따로 만들지 않는다
  assert.ok(r.timetable[1].note.indexOf('추정') >= 0);
  assert.ok(r.warnings.some(w => w.indexOf('추정값') >= 0));
  assert.equal(r.budget.lines.find(l => l.key === 'parking').amount, 5000);       // 90+60+45 = 195분 -> 일 최대
  assert.equal(r.budget.unknown.length, 0);
  assert.equal(r.budget.estimates.find(x => x.key === 'meal').amount, 20000);     // 돈가스 10,000원 × 어른 2명
  assert.equal(r.budget.estimates.find(x => x.key === 'cafe').amount, 10000);     // 아메리카노 5,000원 × 어른 2명
  assert.equal(r.budget.known_total, 15000); assert.equal(r.budget.total, 45000);   // 공식 요금 15,000원 + 대표메뉴 기준 예상 30,000원
});
t('국립수목원: 직선거리 식당·카페는 차로 이동(추정), 수목원 주차 시간은 늘지 않는다', () => {
  const r = P.plan({ party: { adult: 2 }, car: true, visit_minutes: 120, eat_id: 'eat_kn_dongi_mandu', cafe_id: 'eat_kn_poook' }, arbo);
  assert.deepEqual(r.timetable.map(x => x.kind), ['place', 'move', 'meal', 'move', 'cafe', 'end']);
  assert.equal(r.timetable[1].start, '12:30'); assert.equal(r.timetable[1].end, '12:40');
  assert.ok(r.budget.unknown.some(u => u.key === 'parking'));
});
t('경기도박물관: 인근 카페(직선 2.5km)는 차로 이동 추정, 식당과 함께 선택 가능', () => {
  const r = P.plan({ party: { adult: 2 }, car: true, visit_minutes: 60, eat_id: 'eat_hwangje_jjamppong', cafe_id: 'eat_gm_nineblock' }, museum);
  assert.ok(r.timetable.some(x => x.kind === 'cafe' && x.label.indexOf('나인블럭') >= 0));
  assert.equal(r.budget.lines.find(l => l.key === 'parking').amount, 1000 + Math.ceil(60 / 10) * 200);   // 식당만 걸어서 -> 60+60=120분
});
t('방문 예정일: 기본은 다가오는 토요일(토요일이면 당일), 잘못된 날짜는 비운다', () => {
  assert.equal(P.nextSaturday(new Date(2026, 9, 14)), '2026-10-17');   // 수
  assert.equal(P.nextSaturday(new Date(2026, 9, 17)), '2026-10-17');   // 토
  assert.equal(P.nextSaturday(new Date(2026, 9, 18)), '2026-10-24');   // 일
  assert.equal(P.dateLabel('2026-10-17'), '10/17(토)');
  assert.equal(P.daysFrom(new Date(2026, 9, 14), '2026-10-17'), 3);
  assert.equal(P.normalizePlan({ visit_date: '2026-02-30' }).visit_date, '');
  assert.equal(P.normalizePlan({ visit_date: '2026-10-17' }).visit_date, '2026-10-17');
});
t('날씨 안내: 코드별 라벨, 비·더위·추위 경고, 맑으면 야외활동 추천', () => {
  assert.equal(P.wmo(0).label, '맑음'); assert.equal(P.wmo(61).kind, 'rain'); assert.equal(P.wmo(999).kind, 'unknown');
  assert.equal(P.weatherAdvice({ code: 0, tmax: 21, pop: 0 }).text, '야외활동 추천');
  assert.equal(P.weatherAdvice({ code: 61, tmax: 18, pop: 80 }, 'museum').tone, 'warn');
  assert.ok(P.weatherAdvice({ code: 1, tmax: 33, pop: 0 }).text.indexOf('더위') >= 0);
  assert.ok(P.weatherAdvice({ code: 2, tmax: 2, pop: 0 }).text.indexOf('추위') >= 0);
  assert.equal(P.weatherAdvice({ code: 3, tmax: 20, pop: 70 }).tone, 'warn');
});
t('공유 링크: 인코딩·디코딩 왕복, 깨진 입력은 null, 범위 밖 값은 정규화', () => {
  const st = P.normalizePlan({ place_id: 'spot_06', eat_id: 'eat_hwangje_jjamppong', party: { adult: 2, toddler: 1 }, car: false, visit_minutes: 90, visit_date: '2026-10-17', manual: { meal: 12000 } });
  const back = P.decodePlan(P.encodePlan(st));
  assert.deepEqual(back, st);
  assert.equal(P.decodePlan('###'), null); assert.equal(P.decodePlan(''), null);
  assert.equal(P.decodePlan('x'.repeat(700)), null);
});
t('보관함: 규격 레코드(schema_version·guest_id·created_at·plan_data), 같은 일정은 덮어쓰고 최대 10개', () => {
  const store = {}; globalThis.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } };
  const r1 = P.addSaved({ place_id: 'spot_06', visit_date: '2026-10-17' }, '가', { total: 39000, unknown: 1 });
  assert.equal(r1.schema_version, 1); assert.match(r1.guest_id, /^[0-9a-f-]{36}$/); assert.ok(!isNaN(Date.parse(r1.created_at)));
  assert.equal(r1.plan_data.place_id, 'spot_06'); assert.equal(r1.completed, false); assert.equal(r1.summary.total, 39000);
  assert.equal(P.guestId(), r1.guest_id);                                   // 같은 기기는 같은 guest_id
  const r2 = P.addSaved({ place_id: 'spot_06', visit_date: '2026-10-17' }, '나');
  assert.equal(P.listSaved().length, 1); assert.equal(r2.title, '나'); assert.equal(r2.created_at, r1.created_at); assert.equal(r2.summary.total, 39000);
  for (let i = 0; i < 12; i++) P.addSaved({ place_id: 'spot_06', visit_minutes: 60, visit_date: '2026-11-' + String(10 + i) }, 'x' + i);
  assert.equal(P.listSaved().length, 10);
  const id = P.listSaved()[0].id; assert.equal(P.removeSaved(id).length, 9);
  store['buyg_saved_plans_v1'] = '깨짐'; assert.deepEqual(P.listSaved(), []);
});
t('방문 완료 스탬프: 보관함에 없으면 저장 후 완료 처리, 다시 저장해도 완료 상태 유지', () => {
  const store = {}; globalThis.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } };
  const st = { place_id: 'spot_57', visit_date: '2026-10-17' };
  assert.equal(P.findSaved(st), null);
  const rec = P.completeSaved(st, '수목원', { total: 1000, unknown: 0 });
  assert.equal(rec.completed, true); assert.ok(!isNaN(Date.parse(rec.completed_at)));
  assert.equal(P.listSaved().length, 1); assert.equal(P.findSaved(st).completed, true);
  const again = P.addSaved(st, '수목원2'); assert.equal(again.completed, true); assert.equal(again.completed_at, rec.completed_at);
  assert.equal(P.findSaved({ place_id: 'spot_57', visit_date: '2026-10-24' }), null);   // 다른 날짜는 다른 일정
});
t('보관함: 이전 형식({state, saved_at}) 레코드는 새 규격으로 승격', () => {
  const store = { buyg_saved_plans_v1: JSON.stringify([{ id: 'abc', saved_at: 1700000000000, title: '옛', state: { place_id: 'spot_06' } }]) };
  globalThis.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } };
  const l = P.listSaved(); assert.equal(l.length, 1); assert.equal(l[0].schema_version, 1); assert.equal(l[0].plan_data.place_id, 'spot_06'); assert.equal(l[0].completed, false);
});
t('건너뛰기 표시: 새 상태는 건너뛰지 않음, 이전 저장본은 선택이 비면 건너뜀, 공유 링크에 보존', () => {
  assert.equal(P.normalizePlan({}).meal_skip, false);
  assert.equal(P.normalizePlan({ place_id: 'spot_06' }).meal_skip, true);                      // 이전 저장본(선택 없음) -> 건너뛴 것으로
  assert.equal(P.normalizePlan({ place_id: 'spot_06', eat_id: 'e1' }).meal_skip, false);
  assert.equal(P.normalizePlan({ place_id: 'spot_06', meal_skip: false }).meal_skip, false);
  const st = P.normalizePlan({ place_id: 'spot_06', eat_id: 'e1', meal_skip: false, cafe_skip: true });
  const back = P.decodePlan(P.encodePlan(st));
  assert.equal(back.meal_skip, false); assert.equal(back.cafe_skip, true); assert.deepEqual(back, st);
});
t('함께 갈 곳(공연·전시): 이동·관람이 타임테이블에 들어가고 무료면 0원, 아니면 확인 필요, 입력은 정리된다', () => {
  const place = { name: '서울숲', costs: { admission: { model: 'free' }, parking: { model: 'free' } }, eats: [] };
  const st = P.normalizePlan({ place_id: 'spot_04', extra_stop: { key: 'tour_1', name: '어린이 <b>축제</b>', kind: '행사', meters: 3200, free: true } });
  assert.equal(st.extra_stop.name, '어린이 b축제/b');                                         // < > 제거
  let r = P.plan(st, place);
  assert.deepEqual(r.timetable.map(x => x.kind), ['place', 'move', 'stop', 'end']);
  assert.equal(r.timetable[1].start, '12:00'); assert.equal(r.timetable[1].end, '12:10');   // 3.2km -> 10분(최소)
  assert.equal(r.timetable[2].start, '12:10'); assert.equal(r.timetable[2].end, '13:40');   // 기본 90분 관람
  assert.ok(r.warnings.some(w => w.indexOf('추정값') >= 0));
  assert.ok(r.budget.lines.some(l => l.key === 'stop' && l.amount === 0)); assert.equal(r.budget.unknown.length, 0);
  const paid = P.normalizePlan({ place_id: 'spot_04', extra_stop: { name: '유료 전시', meters: 9000, free: false, visit_minutes: 60 } });
  r = P.plan(paid, place); assert.ok(r.budget.unknown.some(u => u.key === 'stop'));
  assert.equal(r.timetable[1].end, '12:30');                                                  // 9km -> 27분 -> 5분 단위 올림 30분
  assert.equal(P.normalizePlan({ extra_stop: { name: '' } }).extra_stop, null);
  assert.equal(P.normalizePlan({ extra_stop: { name: 'a', meters: 999999999 } }).extra_stop.meters, 5000);
  const back = P.decodePlan(P.encodePlan(st)); assert.deepEqual(back.extra_stop, st.extra_stop);
});
t('출발 시간: 선택지·기본값·잘못된 값, 공유 링크 왕복, 기본값은 링크에 넣지 않는다', () => {
  assert.deepEqual(P.START_CHOICES, ['09:00', '09:30', '10:00', '10:30', '11:00', '13:00']);
  assert.equal(P.normalizePlan({}).start, '10:30');
  assert.equal(P.normalizePlan({ start: '07:15' }).start, '10:30');
  const base = P.normalizePlan({ place_id: 'spot_06', eat_id: 'eat_samdae_dubu' });
  assert.equal(P.encodePlan(base), P.encodePlan(Object.assign({}, base, { start: '10:30' })));   // 이전 링크·보관함 id 유지
  const s13 = Object.assign({}, base, { start: '13:00' });
  assert.notEqual(P.encodePlan(s13), P.encodePlan(base));
  assert.equal(P.decodePlan(P.encodePlan(s13)).start, '13:00');
  assert.equal(P.decodePlan(P.encodePlan(base)).start, '10:30');
});
t('출발 시간: 시각에 따라 식사·카페가 순차 재계산되고 늦은 출발은 끝 시각도 따라간다', () => {
  const inp = { party: { adult: 2 }, car: true, visit_minutes: 90, eat_id: 'eat_samdae_dubu', cafe_id: null };
  const early = P.plan(Object.assign({ start: '09:00' }, inp), museum);
  assert.equal(early.timetable[0].start, '09:00');
  const meal = early.timetable.find(x => x.kind === 'meal'); assert.ok(meal.start >= '10:30' && meal.label.indexOf('식사') >= 0);
  const late = P.plan(Object.assign({ start: '13:00' }, inp), museum);
  assert.equal(late.timetable[0].start, '13:00');
  assert.equal(late.timetable.find(x => x.kind === 'meal').label.slice(-3), ' 식사');       // 14시 이후 식사는 '점심'이라 부르지 않는다
  assert.ok(!late.warnings.some(w => w.indexOf('넘겨요') >= 0));
  const def = P.plan(inp, museum); assert.equal(def.timetable[0].start, '10:30'); assert.ok(def.timetable.find(x => x.kind === 'meal').label.slice(-3) === ' 점심');
});
t('5-1 후보: 방문일에 열린 키즈 행사 + 전시 장소, 직선거리 차로 20분 이하만, 가까운 순', () => {
  const place = { id: 'x1', name: '테스트공원', geo: { lat: 37.5, lon: 127.0 } };
  const near = { lat: 37.51, lon: 127.0 }, far = { lat: 37.9, lon: 127.0 };
  const ev = [
    { id: 'tour_ok', title: '가까운 키즈 행사', kid_score: 2, mapy: near.lat, mapx: near.lon, start_date: '2026-10-01', end_date: '2026-11-30', is_free: true, image: 'https://img.example/a.jpg' },
    { id: 'tour_far', title: '먼 행사', kid_score: 2, mapy: far.lat, mapx: far.lon, start_date: '2026-10-01', end_date: '2026-11-30' },
    { id: 'tour_adult', title: '어른 행사', kid_score: 0, mapy: near.lat, mapx: near.lon, start_date: '2026-10-01', end_date: '2026-11-30' },
    { id: 'tour_past', title: '끝난 행사', kid_score: 2, mapy: near.lat, mapx: near.lon, start_date: '2026-08-01', end_date: '2026-09-01' },
    { id: 'tour_later', title: '방문일 뒤 시작', kid_score: 2, mapy: near.lat, mapx: near.lon, start_date: '2026-12-05', end_date: '2026-12-31' }
  ];
  const seeds = [{ id: 's_m', title: '어린이박물관', category: '박물관/체험', tag: '무료입장' }, { id: 's_p', title: '그냥공원', category: '공원/산책' }, { id: 'x1', title: '테스트공원', category: '박물관' }];
  const enr = { s_m: { geo: { lat: 37.52, lon: 127.0 } }, s_p: { geo: { lat: 37.5, lon: 127.0 } }, x1: { geo: { lat: 37.5, lon: 127.0 } } };
  const r = P.nearbyStops(place, { events: ev, seeds, enr, visit_date: '2026-10-17', today: '2026-10-17' });
  assert.deepEqual(r.map(c => c.key), ['tour_ok', 's_m']);                       // 가까운 순, 먼·어른·끝난·늦게 시작 행사와 자기 자신·공원은 제외
  assert.equal(r[0].kind, '행사'); assert.equal(r[0].free, true); assert.ok(r[0].minutes <= 20 && r[0].meters > 0);
  assert.equal(r[1].kind, '전시 장소'); assert.equal(r[1].free, true);
  assert.deepEqual(P.nearbyStops(place, { events: ev, seeds, enr, visit_date: '2026-12-10' }).map(c => c.key), ['tour_later', 's_m']);   // 방문일에 따라 달라진다
  assert.deepEqual(P.nearbyStops({ id: 'n', name: 'n' }, { events: ev }), []);   // 좌표가 없으면 후보 없음
  assert.equal(P.nearbyStops(place, { events: ev, seeds, enr, visit_date: 'bad', today: '2026-10-17' }).length, 2);   // 잘못된 방문일은 '오늘 이후'로 처리
});
t('5-1 후보에 가이드가 있는 박물관 앵커 포함(시드·자기 자신·비박물관·좌표 없음 제외)', () => {
  const place = { id: 'guide_a', name: '앵커 박물관', geo: { lat: 37.5, lon: 127.0 } };
  const mk = (id, name, theme, lat, extra) => Object.assign({ id: id, name: name, theme: theme, geo: { lat: lat, lon: 127.0 }, photos: [], costs: { admission: { model: 'per_person' } } }, extra || {});
  const guides = [place, mk('guide_b', '가까운 어린이박물관', 'museum', 37.52, { costs: { admission: { model: 'free' } }, photos: [{ url: 'https://img.example/b.jpg', thumb: 'https://img.example/bt.jpg' }] }), mk('guide_c', '먼 박물관', 'museum', 38.5), mk('guide_d', '실외 공원', 'outdoor', 37.51), { id: 'guide_e', name: '좌표 없는 박물관', theme: 'museum' }, mk('spot_s', '시드 가이드', 'museum', 37.51, { seed: true }), mk('guide_f', '앵커 박물관 별관', 'museum', 37.51), mk('guide_g', '유료 박물관', 'museum', 37.53)];
  const r = P.nearbyStops(place, { guides });
  assert.deepEqual(r.map(c => c.key), ['guide_b', 'guide_g']);                    // 가까운 순, 먼 곳·실외·좌표 없음·시드 표기·같은 이름 포함 장소는 제외
  assert.equal(r[0].kind, '전시 장소'); assert.equal(r[0].free, true); assert.equal(r[0].img, 'https://img.example/bt.jpg'); assert.equal(r[1].free, null); assert.equal(r[1].img, '');
  const seeds = [{ id: 'spot_b', title: '가까운 어린이박물관', category: '박물관' }], enr = { spot_b: { geo: { lat: 37.52, lon: 127.0 } } };
  assert.deepEqual(P.nearbyStops(place, { guides, seeds, enr }).map(c => c.key), ['spot_b', 'guide_g']);   // 시드와 이름이 같으면 중복으로 넣지 않는다
});
console.log('planner tests passed: ' + n);
