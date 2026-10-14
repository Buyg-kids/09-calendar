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
t('식비·카페비: 입력 전에는 확인 필요, 입력하면 직접 입력으로만 합산', () => {
  let r = P.plan({ party: { adult: 2 }, car: false, visit_minutes: 60, eat_id: 'eat_hwangje_jjamppong' }, museum);
  assert.ok(r.budget.unknown.some(u => u.key === 'meal'));
  assert.equal(r.budget.known_total, 0);
  r = P.plan({ party: { adult: 2 }, car: false, visit_minutes: 60, eat_id: 'eat_hwangje_jjamppong', manual: { meal: 32000 } }, museum);
  assert.ok(!r.budget.unknown.some(u => u.key === 'meal'));
  assert.equal(r.budget.manual[0].amount, 32000);
  assert.equal(r.budget.known_total, 32000);
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
console.log('planner tests passed: ' + n);
