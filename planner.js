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

  var DEFAULT_MEAL_PER_PERSON = 12000, DEFAULT_CAFE_PER_PERSON = 6000;   // 대표메뉴 기준가가 없는 식당·카페의 일반 추정 1인 금액

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

  // 이동 구간: 확인된 분(min/max_minutes)이 있으면 그대로, 없으면 거리 정보로 '추정'한다(estimate=true).
  //  - in_park(공원 안): 도보 10분 추정, 차는 그대로 둔다 / straight(직선거리): 직선 1km당 약 3분(우회 포함)을 5분 단위로 올림, 최소 10분
  function legTravel(d) {
    d = d || {};
    if (typeof d.min_minutes === 'number' && typeof d.max_minutes === 'number') return { minutes: round5((d.min_minutes + d.max_minutes) / 2) || 5, estimate: false, walk: d.mode === 'walk', stays: d.mode === 'walk' };
    if (d.mode === 'in_park') return { minutes: 10, estimate: true, walk: true, stays: true };
    if (d.mode === 'straight' && typeof d.meters === 'number') return { minutes: Math.max(10, Math.ceil(d.meters / 1000 * 3 / 5) * 5), estimate: true, walk: false, stays: false };
    return { minutes: 15, estimate: true, walk: false, stays: false };
  }
  function findEat(place, id) { return (place.eats || []).filter(function (e) { return e.id === id; })[0] || null; }

  function plan(input, place) {
    var inp = input || {};
    var party = {}; AGE_KEYS.forEach(function (k) { party[k] = int0((inp.party || DEFAULT_PARTY)[k]); });
    var costs = (place && place.costs) || {};
    var eat = inp.eat_id ? findEat(place, inp.eat_id) : null;
    var start = toMin(inp.start || '10:30'), end = toMin(inp.end || '15:00');
    var visit = Math.max(30, int0(inp.visit_minutes) || 90);
    var out = { timetable: [], budget: { lines: [], known_total: 0, unknown: [], manual: [], estimates: [], estimate_total: 0, total: 0 }, warnings: [], party_count: partyCount(party) };
    var t = start, mealMin = 60, cafeMin = 45;

    // ---- 타임테이블 ----
    out.timetable.push({ start: toHHMM(t), end: toHHMM(t + visit), label: (place.name || '장소') + ' 관람', kind: 'place' });
    t += visit;
    var walkEat = false, cafeStays = false, anyEstimate = false;
    if (eat) {
      var d = eat.distance || {};
      var lg = legTravel(d), travel = lg.minutes;
      walkEat = lg.stays; anyEstimate = anyEstimate || lg.estimate;
      out.timetable.push({ start: toHHMM(t), end: toHHMM(t + travel), label: (lg.walk ? '도보 이동' : '차로 이동') + ' → ' + eat.name, kind: 'move', note: (d.v || '') + (lg.estimate ? ' · 추정 시간' : '') });
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
      if (cafe) {
        var cl = legTravel(cafe.distance || {}); anyEstimate = anyEstimate || cl.estimate; cafeStays = cl.stays;
        if (!eat || !(eat && eat.distance && cafe.distance && eat.distance.mode === cafe.distance.mode && cafe.distance.mode === 'in_park')) {
          out.timetable.push({ start: toHHMM(t), end: toHHMM(t + cl.minutes), label: (cl.walk ? '도보 이동' : '차로 이동') + ' → ' + cafe.name, kind: 'move', note: ((cafe.distance && cafe.distance.v) || '') + (cl.estimate ? ' · 추정 시간' : '') });
          t += cl.minutes;
        }
        out.timetable.push({ start: toHHMM(t), end: toHHMM(t + cafeMin), label: cafe.name + ' 휴식', kind: 'cafe' }); t += cafeMin;
      }
    }
    if (anyEstimate) out.warnings.push('이동 시간 중 일부는 거리로 계산한 추정값이에요. 실제 경로는 지도 앱으로 확인해 주세요.');
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
    } else if (pkCost && pkCost.model === 'free') {
      out.budget.lines.push({ key: 'parking', label: '주차비', amount: 0, detail: pkCost.note || '무료 주차' });
    } else if (pkCost && pkCost.model === 'tiered') {
      var parkMin = visit + (walkEat ? mealMin : 0) + (cafeStays ? cafeMin : 0);   // 걸어서(또는 공원 안에서) 식사·휴식하면 차는 이 장소 주차장에 계속 있다
      var fee = parkingFee(pkCost, parkMin);
      out.budget.lines.push({ key: 'parking', label: '주차비', amount: fee, detail: '약 ' + Math.floor(parkMin / 60) + '시간 ' + (parkMin % 60) + '분 주차 기준' + (pkCost.note ? ' · ' + pkCost.note : '') });
      out.budget.known_total += fee;
    } else {
      out.budget.unknown.push({ key: 'parking', label: '주차비', note: (pkCost && pkCost.note) || '' });
    }
    var manual = inp.manual || {};
    // 식비·카페비: 직접 입력 > 대표메뉴 기준가(어른 인원수 × 1인 기준가, '예상치') > 확인 필요
    function foodLine(key, label, item, val) {
      if (typeof val === 'number' && val >= 0) { out.budget.manual.push({ key: key, label: label + '(직접 입력)', amount: val }); out.budget.known_total += val; return; }
      var rm = item && item.ref_menu;
      if (rm && typeof rm.price === 'number' && rm.price > 0) {
        var amt = party.adult * rm.price;
        out.budget.estimates.push({ key: key, label: label, amount: amt, detail: '대표메뉴 ' + rm.name + ' ' + (rm.note ? rm.note + ' ' : '') + won(rm.price) + ' × 어른 ' + party.adult + '명', basis: rm.basis || '' });
        out.budget.estimate_total += amt; return;
      }
      // 기준가가 없으면 일반 외식 기본값으로 추정해 합계가 비지 않게 한다(어른+청소년 인원수 × 1인 기본값, '추정치'로 표시)
      var per = key === 'cafe' ? DEFAULT_CAFE_PER_PERSON : DEFAULT_MEAL_PER_PERSON, heads = Math.max(1, party.adult + party.teen);
      out.budget.estimates.push({ key: key, label: label, amount: heads * per, detail: '일반 ' + (key === 'cafe' ? '카페' : '외식') + ' 기준 1인 ' + won(per) + ' × ' + heads + '명(실제 메뉴·가격과 다를 수 있어요)', basis: 'default' });
      out.budget.estimate_total += heads * per;
    }
    if (eat) foodLine('meal', '식비', eat, manual.meal);
    if (inp.cafe_id) { var cafeItem = findEat(place, inp.cafe_id); foodLine('cafe', '카페비', cafeItem, manual.cafe); }
    out.budget.total = out.budget.known_total + out.budget.estimate_total;
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
      // 식사·카페 '건너뛰기' 표시: true 면 기본 선택(가장 가까운 곳)을 자동으로 채우지 않는다. 이 값이 없던 이전 저장본은 선택이 비어 있으면 건너뛴 것으로 본다.
      meal_skip: typeof o.meal_skip === 'boolean' ? o.meal_skip : !!(o.place_id && !o.eat_id),
      cafe_skip: typeof o.cafe_skip === 'boolean' ? o.cafe_skip : !!(o.place_id && !o.cafe_id),
      manual: { meal: money(man.meal), cafe: money(man.cafe) }, lodging: 'day', start: '10:30', end: '15:00',
      visit_date: (typeof o.visit_date === 'string' && parseIso(o.visit_date)) ? o.visit_date : '',
      updated_at: typeof o.updated_at === 'number' ? o.updated_at : 0
    };
  }

  // ---- 날짜 · 날씨 · 공유 · 저장 목록 ----
  function pad2(n) { return (n < 10 ? '0' : '') + n; }
  function isoDate(d) { return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()); }
  function parseIso(s) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || ''); if (!m) return null;
    var d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    return (d.getFullYear() === Number(m[1]) && d.getMonth() === Number(m[2]) - 1 && d.getDate() === Number(m[3])) ? d : null;
  }
  function nextSaturday(from) {   // 오늘이 토요일이면 오늘, 아니면 다가오는 토요일
    var d = new Date(from.getFullYear(), from.getMonth(), from.getDate());
    d.setDate(d.getDate() + ((6 - d.getDay() + 7) % 7));
    return isoDate(d);
  }
  function dateLabel(iso) { var d = parseIso(iso); return d ? (d.getMonth() + 1) + '/' + d.getDate() + '(' + '일월화수목금토'.charAt(d.getDay()) + ')' : ''; }
  function daysFrom(from, iso) { var d = parseIso(iso); if (!d) return NaN; var a = new Date(from.getFullYear(), from.getMonth(), from.getDate()); return Math.round((d - a) / 864e5); }
  // WMO 날씨 코드(Open-Meteo) -> 한글 라벨 · 아이콘 · 종류
  function wmo(code) {
    code = Number(code);
    if (code === 0) return { label: '맑음', icon: '☀️', kind: 'clear' };
    if (code === 1 || code === 2) return { label: '구름 조금', icon: '🌤️', kind: 'cloud' };
    if (code === 3) return { label: '흐림', icon: '☁️', kind: 'cloud' };
    if (code === 45 || code === 48) return { label: '안개', icon: '🌫️', kind: 'fog' };
    if (code >= 51 && code <= 67) return { label: '비', icon: '🌧️', kind: 'rain' };
    if (code >= 80 && code <= 82) return { label: '소나기', icon: '🌦️', kind: 'rain' };
    if ((code >= 71 && code <= 77) || code === 85 || code === 86) return { label: '눈', icon: '❄️', kind: 'snow' };
    if (code >= 95 && code <= 99) return { label: '천둥번개', icon: '⛈️', kind: 'storm' };
    return { label: '날씨 정보 없음', icon: '🌡️', kind: 'unknown' };
  }
  // 하루 예보 -> 안내 문구. w: { code, tmax, tmin, pop(강수확률 %) }, theme: 장소 테마(museum 은 실내 비중이 커서 비 올 때 문구가 다르다)
  function weatherAdvice(w, theme) {
    var k = wmo(w.code).kind, pop = typeof w.pop === 'number' ? w.pop : 0, tmax = Number(w.tmax);
    if (k === 'storm') return { text: '천둥번개 예보 · 야외 활동은 피하는 게 좋아요', tone: 'warn' };
    if (k === 'snow') return { text: '눈 예보 · 미끄러운 길을 조심하고 따뜻하게 입어요', tone: 'warn' };
    if (k === 'rain' || pop >= 60) return { text: theme === 'museum' ? '비 소식 · 우산 챙기고 실내 관람 위주로 즐겨요' : '비 소식 · 우산을 챙기고 실내 활동을 함께 계획해요', tone: 'warn' };
    if (tmax >= 30) return { text: '더위 주의 · 그늘과 물을 넉넉히 챙겨요', tone: 'warn' };
    if (tmax <= 5) return { text: '추위 주의 · 아이를 따뜻하게 입혀요', tone: 'warn' };
    return { text: '야외활동 추천', tone: 'good' };
  }
  function b64enc(s) { var b = (typeof btoa === 'function') ? btoa(unescape(encodeURIComponent(s))) : Buffer.from(s, 'utf8').toString('base64'); return b.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''); }
  function b64dec(s) { s = String(s).replace(/-/g, '+').replace(/_/g, '/'); while (s.length % 4) s += '='; return (typeof atob === 'function') ? decodeURIComponent(escape(atob(s))) : Buffer.from(s, 'base64').toString('utf8'); }
  // 공유 링크용 압축 직렬화(짧은 키). 받는 쪽은 반드시 normalizePlan 을 거친다(외부 입력 - 길이·문자·범위 제한).
  function encodePlan(st) {
    var n = normalizePlan(st);
    return b64enc(JSON.stringify({ p: n.place_id, e: n.eat_id, c: n.cafe_id, a: [n.party.adult, n.party.teen, n.party.child, n.party.toddler], k: n.car ? 1 : 0, v: n.visit_minutes, d: n.visit_date, m: [n.manual.meal, n.manual.cafe], s: [n.meal_skip ? 1 : 0, n.cafe_skip ? 1 : 0], x: n.extras.theme_garden ? 1 : 0 }));
  }
  function decodePlan(s) {
    try {
      if (!s || String(s).length > 600) return null;
      var o = JSON.parse(b64dec(s)); if (!o || typeof o !== 'object') return null;
      var a = Array.isArray(o.a) ? o.a : [], m = Array.isArray(o.m) ? o.m : [];
      return normalizePlan({ place_id: o.p, eat_id: o.e, cafe_id: o.c, meal_skip: Array.isArray(o.s) ? o.s[0] === 1 : undefined, cafe_skip: Array.isArray(o.s) ? o.s[1] === 1 : undefined, party: { adult: a[0], teen: a[1], child: a[2], toddler: a[3] }, car: o.k !== 0, visit_minutes: o.v, visit_date: o.d, manual: { meal: m[0], cafe: m[1] }, extras: { theme_garden: !!o.x } });
    } catch (e) { return null; }
  }
  // ---- 내 일정 보관함: 서버 DB 이전을 대비한 규격 레코드 ----
  // { schema_version, id, guest_id(UUID), created_at, updated_at, title, plan_data, summary{total,unknown}, completed, completed_at }
  var SAVED_KEY = 'buyg_saved_plans_v1', GUEST_KEY = 'buyg_guest_id_v1', SAVED_MAX = 10, SCHEMA_VERSION = 1;
  function uuid() {
    try { if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') return globalThis.crypto.randomUUID(); } catch (e) { /* 폴백 */ }
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) { var r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 3 | 8)).toString(16); });
  }
  function guestId() {
    try { var g = globalThis.localStorage.getItem(GUEST_KEY); if (g && /^[0-9a-f-]{36}$/.test(g)) return g; g = uuid(); globalThis.localStorage.setItem(GUEST_KEY, g); return g; } catch (e) { return uuid(); }
  }
  function toRecord(x) {   // 저장소 값 -> 규격 레코드(이전 형식 {id,saved_at,title,state} 도 승격)
    if (!x || typeof x.id !== 'string') return null;
    var pd = x.plan_data || x.state; if (!pd || typeof pd !== 'object') return null;
    var created = x.created_at || (x.saved_at ? new Date(x.saved_at).toISOString() : new Date(0).toISOString());
    var sm = x.summary || {};
    return { schema_version: SCHEMA_VERSION, id: x.id, guest_id: (typeof x.guest_id === 'string' && /^[0-9a-f-]{36}$/.test(x.guest_id)) ? x.guest_id : guestId(), created_at: created, updated_at: x.updated_at || created, title: String(x.title || '').slice(0, 80), plan_data: normalizePlan(pd), summary: { total: Math.max(0, Math.floor(Number(sm.total) || 0)), unknown: Math.max(0, Math.floor(Number(sm.unknown) || 0)) }, completed: x.completed === true, completed_at: x.completed === true ? (x.completed_at || null) : null };
  }
  function listSaved() { try { var a = JSON.parse(globalThis.localStorage.getItem(SAVED_KEY) || '[]'); return Array.isArray(a) ? a.map(toRecord).filter(Boolean) : []; } catch (e) { return []; } }
  function writeSaved(list) { try { globalThis.localStorage.setItem(SAVED_KEY, JSON.stringify(list)); } catch (e) { /* 저장소 차단 */ } }
  function findSaved(st) { var id = encodePlan(st); return listSaved().filter(function (x) { return x.id === id; })[0] || null; }
  function addSaved(st, title, summary) {   // 같은 일정이면 덮어쓰되 만든 시각·방문 완료 상태는 유지, 최대 10개
    var n = normalizePlan(st), id = encodePlan(n), old = findSaved(n), now = new Date().toISOString();
    var rec = toRecord({ id: id, guest_id: old ? old.guest_id : guestId(), created_at: old ? old.created_at : now, updated_at: now, title: title, plan_data: n, summary: summary || (old && old.summary), completed: old ? old.completed : false, completed_at: old ? old.completed_at : null });
    var list = listSaved().filter(function (x) { return x.id !== id; }); list.unshift(rec); writeSaved(list.slice(0, SAVED_MAX)); return rec;
  }
  function completeSaved(st, title, summary) {   // '다녀왔어요' 스탬프: 보관함에 없으면 먼저 저장
    var rec = addSaved(st, title, summary); rec.completed = true; rec.completed_at = rec.completed_at || new Date().toISOString();
    var list = listSaved().filter(function (x) { return x.id !== rec.id; }); list.unshift(rec); writeSaved(list.slice(0, SAVED_MAX)); return rec;
  }
  function removeSaved(id) { var list = listSaved().filter(function (x) { return x.id !== id; }); writeSaved(list); return list; }

  function loadPlan() { try { return normalizePlan(JSON.parse(globalThis.localStorage.getItem(api.STORE_KEY) || 'null')); } catch (e) { return normalizePlan(null); } }
  function savePlan(o) { var n = normalizePlan(o); n.updated_at = Date.now(); try { globalThis.localStorage.setItem(api.STORE_KEY, JSON.stringify(n)); } catch (e) { /* 저장소 차단: 화면은 계속 동작 */ } return n; }
  function clearPlan() { try { globalThis.localStorage.removeItem(api.STORE_KEY); } catch (e) { /* 무시 */ } }

  var api = { DEFAULT_MEAL_PER_PERSON: DEFAULT_MEAL_PER_PERSON, DEFAULT_CAFE_PER_PERSON: DEFAULT_CAFE_PER_PERSON, isoDate: isoDate, parseIso: parseIso, nextSaturday: nextSaturday, dateLabel: dateLabel, daysFrom: daysFrom, wmo: wmo, weatherAdvice: weatherAdvice, encodePlan: encodePlan, decodePlan: decodePlan, listSaved: listSaved, findSaved: findSaved, addSaved: addSaved, completeSaved: completeSaved, removeSaved: removeSaved, guestId: guestId, SAVED_KEY: SAVED_KEY, SCHEMA_VERSION: SCHEMA_VERSION, legTravel: legTravel, normalizePlan: normalizePlan, loadPlan: loadPlan, savePlan: savePlan, clearPlan: clearPlan, VISIT_CHOICES: VISIT_CHOICES, plan: plan, parkingFee: parkingFee, admission: admission, toMin: toMin, toHHMM: toHHMM, won: won, DEFAULT_PARTY: DEFAULT_PARTY, AGE_KEYS: AGE_KEYS, AGE_LABEL: AGE_LABEL, STORE_KEY: 'buyg_weekend_plan_v1' };
  if (typeof module !== 'undefined' && module.exports) module.exports = api; else root.Planner = api;
})(typeof window !== 'undefined' ? window : this);
