/* BUYG 주말 일정 플래너 - 순수 계산 로직 (DOM/네트워크 없음, 브라우저와 node 모두에서 동작)
 * 입력:  { place_id, party:{adult,teen,child,toddler}, car, visit_minutes, extras:{theme_garden}, eat_id, cafe_id, manual:{meal,cafe}, start, end }
 * 장소 데이터: place_guides.json 의 places[id] (costs, eats ...)
 * 원칙: 확인된(공식) 요금만 자동 합산한다. 모르는 요금은 합계에 억지로 더하지 않고 unknown 목록에 '확인 필요'로 남긴다.
 *       식비·카페비는 검증된 가격 데이터가 없어 사용자가 직접 입력한 값만 합산하고, 입력하지 않으면 '직접 입력' 으로 표시한다. */
(function (root) {
  'use strict';
  var AGE_KEYS = ['adult', 'teen', 'child', 'toddler'];
  var AGE_LABEL = { adult: '어른', teen: '청소년', child: '어린이', toddler: '영유아' };
  var DEFAULT_PARTY = { adult: 2, teen: 0, child: 0, toddler: 1 };

  function toMin(hhmm) { var m = /^(\d{1,2}):(\d{2})$/.exec(hhmm || ''); return m ? Number(m[1]) * 60 + Number(m[2]) : NaN; }
  function toHHMM(min) { min = Math.round(min); var h = Math.floor(min / 60), m = min % 60; return (h < 10 ? '0' : '') + h + ':' + (m < 10 ? '0' : '') + m; }
  function round5(n) { return Math.round(n / 5) * 5; }
  function won(n) { return n.toLocaleString('ko-KR') + '원'; }
  function int0(v) { var n = Math.floor(Number(v)); return isFinite(n) && n > 0 ? n : 0; }

  // 단계형 주차요금: free_minutes 이하 무료 -> base_minutes 까지 base -> 이후 step_minutes 마다 step, daily_max 상한
  function parkingFee(t, minutes) {
    minutes = Math.max(0, minutes);
    if (minutes <= t.free_minutes) return 0;
    var fee = minutes <= t.base_minutes ? t.base : t.base + Math.ceil((minutes - t.base_minutes) / t.step_minutes) * t.step;
    return Math.min(fee, t.daily_max);
  }

  function partyCount(party) { return AGE_KEYS.reduce(function (n, k) { return n + int0(party && party[k]); }, 0); }

  function admission(adm, party, extras) {
    var res = { lines: [], unknown: [], amount: 0 };
    if (!adm || adm.model === 'unknown' || !adm.model) { res.unknown.push({ key: 'admission', label: '입장료', note: (adm && adm.note) || '' }); return res; }
    if (adm.model === 'free') { res.lines.push({ key: 'admission', label: '입장료', amount: 0, detail: adm.note || '무료' }); return res; }
    function sum(prices) { return AGE_KEYS.reduce(function (n, k) { return n + int0(party[k]) * (prices[k] || 0); }, 0); }
    function detail(prices) { return AGE_KEYS.filter(function (k) { return int0(party[k]) > 0; }).map(function (k) { return AGE_LABEL[k] + ' ' + int0(party[k]) + '명 × ' + (prices[k] ? won(prices[k]) : '무료'); }).join(', '); }
    var a = sum(adm.prices);
    res.lines.push({ key: 'admission', label: '입장료', amount: a, detail: detail(adm.prices) });
    res.amount += a;
    var opts = adm.options || {};
    Object.keys(opts).forEach(function (k) {
      if (extras && extras[k]) { var s = sum(opts[k].prices); res.lines.push({ key: 'extra_' + k, label: opts[k].label || k, amount: s, detail: detail(opts[k].prices) }); res.amount += s; }
    });
    return res;
  }

  function findEat(place, id) { return (place.eats || []).filter(function (e) { return e.id === id; })[0] || null; }

  function plan(input, place) {
    var inp = input || {};
    var party = {}; AGE_KEYS.forEach(function (k) { party[k] = int0((inp.party || DEFAULT_PARTY)[k]); });
    var costs = (place && place.costs) || {};
    var eat = inp.eat_id ? findEat(place, inp.eat_id) : null;
    var start = toMin(inp.start || '10:30'), end = toMin(inp.end || '15:00');
    var visit = Math.max(30, int0(inp.visit_minutes) || 90);
    var out = { timetable: [], budget: { lines: [], known_total: 0, unknown: [], manual: [] }, warnings: [], party_count: partyCount(party) };
    var t = start, mealMin = 60, cafeMin = 45;

    // ---- 타임테이블 ----
    out.timetable.push({ start: toHHMM(t), end: toHHMM(t + visit), label: (place.name || '장소') + ' 관람', kind: 'place' });
    t += visit;
    var walkEat = false;
    if (eat) {
      var d = eat.distance || {};
      var travel = round5(((d.min_minutes || 0) + (d.max_minutes || 0)) / 2) || 5;
      walkEat = d.mode === 'walk';
      out.timetable.push({ start: toHHMM(t), end: toHHMM(t + travel), label: (walkEat ? '도보 이동' : '차로 이동') + ' → ' + eat.name, kind: 'move', note: d.v || '' });
      t += travel;
      var pk = eat.peak;
      if (pk && toMin(pk.start) < t + mealMin && t < toMin(pk.end)) {
        var wait = toMin(pk.end) - t;
        if (wait > 0) {
          out.timetable.push({ start: toHHMM(t), end: toHHMM(t + wait), label: '여유 시간(식당 피크 피하기)', kind: 'wait', note: pk.label });
          out.warnings.push(pk.label + '와 겹쳐 식사를 ' + toHHMM(toMin(pk.end)) + ' 이후로 잡았어요. 관람을 ' + Math.max(15, round5(wait)) + '분쯤 줄여 ' + toHHMM(Math.max(start, toMin(pk.start) - 30)) + '까지 도착하는 방법도 있어요.');
          t += wait;
        }
      }
      out.timetable.push({ start: toHHMM(t), end: toHHMM(t + mealMin), label: eat.name + ' 점심', kind: 'meal' });
      t += mealMin;
    }
    if (inp.cafe_id) {
      var cafe = findEat(place, inp.cafe_id);
      if (cafe) { out.timetable.push({ start: toHHMM(t), end: toHHMM(t + cafeMin), label: cafe.name + ' 휴식', kind: 'cafe' }); t += cafeMin; }
    }
    out.timetable.push({ start: toHHMM(t), end: toHHMM(t), label: '귀가 준비', kind: 'end' });
    out.end_time = toHHMM(t);
    if (t > end) out.warnings.push('일정이 ' + toHHMM(end) + '을 ' + (t - end) + '분 넘겨요. 머무는 시간을 줄여 보세요.');

    // ---- 예산 ----
    var adm = admission(costs.admission, party, inp.extras);
    adm.lines.forEach(function (l) { out.budget.lines.push(l); out.budget.known_total += l.amount; });
    adm.unknown.forEach(function (u) { out.budget.unknown.push(u); });
    var pkCost = costs.parking;
    if (!inp.car) {
      out.budget.lines.push({ key: 'parking', label: '주차비', amount: 0, detail: '차량을 이용하지 않아요' });
    } else if (pkCost && pkCost.model === 'tiered') {
      var parkMin = visit + (walkEat ? mealMin + (inp.cafe_id ? cafeMin : 0) : 0);   // 걸어서 식당에 가면 차는 이 장소 주차장에 계속 있다
      var fee = parkingFee(pkCost, parkMin);
      out.budget.lines.push({ key: 'parking', label: '주차비', amount: fee, detail: '약 ' + Math.floor(parkMin / 60) + '시간 ' + (parkMin % 60) + '분 주차 기준' + (pkCost.note ? ' · ' + pkCost.note : '') });
      out.budget.known_total += fee;
    } else {
      out.budget.unknown.push({ key: 'parking', label: '주차비', note: (pkCost && pkCost.note) || '' });
    }
    var manual = inp.manual || {};
    if (eat) {
      var m = manual.meal;
      if (typeof m === 'number' && m >= 0) { out.budget.manual.push({ key: 'meal', label: '식비(직접 입력)', amount: m }); out.budget.known_total += m; }
      else out.budget.unknown.push({ key: 'meal', label: '식비', note: '검증된 가격 정보가 없어 직접 입력이 필요해요' });
    }
    if (inp.cafe_id) {
      var c = manual.cafe;
      if (typeof c === 'number' && c >= 0) { out.budget.manual.push({ key: 'cafe', label: '카페비(직접 입력)', amount: c }); out.budget.known_total += c; }
      else out.budget.unknown.push({ key: 'cafe', label: '카페비', note: '검증된 가격 정보가 없어 직접 입력이 필요해요' });
    }
    return out;
  }


  // ---- 저장 상태 정규화(순수 함수) + 브라우저 LocalStorage 래퍼 ----
  var VISIT_CHOICES = [60, 90, 120, 150];
  function normalizePlan(o) {
    o = (o && typeof o === 'object') ? o : {};
    var party = {}; AGE_KEYS.forEach(function (k) { party[k] = Math.min(9, int0((o.party || DEFAULT_PARTY)[k])); });
    var man = o.manual || {};
    function money(v) { return (typeof v === 'number' && isFinite(v) && v >= 0 && v <= 10000000) ? Math.floor(v) : null; }
    var visit = int0(o.visit_minutes); if (VISIT_CHOICES.indexOf(visit) < 0) visit = 90;
    return {
      v: 1, place_id: typeof o.place_id === 'string' ? o.place_id.replace(/[^\w-]/g, '').slice(0, 60) : '',
      party: party, car: o.car !== false, visit_minutes: visit, extras: { theme_garden: !!(o.extras && o.extras.theme_garden) },
      eat_id: typeof o.eat_id === 'string' && o.eat_id ? o.eat_id.replace(/[^\w-]/g, '').slice(0, 60) : null,
      cafe_id: typeof o.cafe_id === 'string' && o.cafe_id ? o.cafe_id.replace(/[^\w-]/g, '').slice(0, 60) : null,
      manual: { meal: money(man.meal), cafe: money(man.cafe) }, lodging: 'day', start: '10:30', end: '15:00',
      updated_at: typeof o.updated_at === 'number' ? o.updated_at : 0
    };
  }
  function loadPlan() { try { return normalizePlan(JSON.parse(globalThis.localStorage.getItem(api.STORE_KEY) || 'null')); } catch (e) { return normalizePlan(null); } }
  function savePlan(o) { var n = normalizePlan(o); n.updated_at = Date.now(); try { globalThis.localStorage.setItem(api.STORE_KEY, JSON.stringify(n)); } catch (e) { /* 저장소 차단: 화면은 계속 동작 */ } return n; }
  function clearPlan() { try { globalThis.localStorage.removeItem(api.STORE_KEY); } catch (e) { /* 무시 */ } }

  var api = { normalizePlan: normalizePlan, loadPlan: loadPlan, savePlan: savePlan, clearPlan: clearPlan, VISIT_CHOICES: VISIT_CHOICES, plan: plan, parkingFee: parkingFee, admission: admission, toMin: toMin, toHHMM: toHHMM, won: won, DEFAULT_PARTY: DEFAULT_PARTY, AGE_KEYS: AGE_KEYS, AGE_LABEL: AGE_LABEL, STORE_KEY: 'buyg_weekend_plan_v1' };
  if (typeof module !== 'undefined' && module.exports) module.exports = api; else root.Planner = api;
})(typeof window !== 'undefined' ? window : this);
