// food_thumb.js 단위 테스트 (node tests/food_thumb_test.js)
const assert = require('node:assert/strict');
const path = require('node:path');
const F = require(path.join(__dirname, '..', 'food_thumb.js'));
let n = 0;
function t(name, fn) { try { fn(); n++; } catch (e) { console.error('FAIL', name, '\n', e.message); process.exitCode = 1; } }
const e = (name, kind, category, menu) => ({ name, kind, category, menu: menu ? { v: menu } : undefined });

t('15종 키가 모두 정의돼 있다', () => { assert.equal(Object.keys(F.FOOD).length, 15); });
t('대표메뉴 키워드 매칭', () => {
  assert.equal(F.pick(e('A', '식당', '일식', '오로시 히레카츠')), 'tonkatsu');
  assert.equal(F.pick(e('A', '식당', '한식', '꿀등심')), 'bbq');
  assert.equal(F.pick(e('A', '식당', '이색 음식점', '소고기 쌀국수')), 'noodle');
  assert.equal(F.pick(e('A', '식당', '중식', '매운 철판짜장')), 'chinese');           // 짜장은 면류가 아니라 중식
  assert.equal(F.pick(e('A', '식당', '한식', '생선구이(고등어 / 갈치)')), 'korean');   // 생선구이는 고기구이가 아니다
  assert.equal(F.pick(e('A', '식당', '한식', '우럭 회무침 코스')), 'korean');
  assert.equal(F.pick(e('A', '카페', '카페', '팥빙수')), 'dessert');
  assert.equal(F.pick(e('A', '카페', '카페', '우유 크림 스콘 / 크루키 / 생망고 요거크림 케이크 조각')), 'bakery');
  assert.equal(F.pick(e('A', '카페', '카페', '브런치플레이트')), 'brunch');
  assert.equal(F.pick(e('A', '카페', '카페', '시그니처 음료: 몬스애플슈페너')), 'coffee');
  assert.equal(F.pick(e('A', '식당', '양식', '칠리쉬림프샌드위치')), 'burger');
});
t('상호·업종 폴백, 근거 없으면 null', () => {
  assert.equal(F.pick(e('원조 칼국수', '식당', '한식')), 'noodle');
  assert.equal(F.pick(e('동네 베이커리', '카페', '카페')), 'bakery');
  assert.equal(F.pick(e('OO 카페', '카페', '카페')), 'coffee');
  assert.equal(F.pick(e('엘더클래식', '식당', '양식', '엘더클래식')), 'western');        // 메뉴·상호에 단서 없음 -> 업종 대표
  assert.equal(F.pick(e('모르는집', '식당', '한식')), 'korean');
  assert.equal(F.pick(e('모르는집', '식당', '이색 음식점')), null);                         // 단서 없음 -> 이모지로 둔다
  assert.equal(F.pick(null), null);
});
t('손그림 파일이 등록된 key 만 art 로 나간다', () => {
  assert.equal(F.art(e('A', '식당', '한식', '꿀등심')), null);
  F.setReady(['bbq', 'nope']);
  const a = F.art(e('A', '식당', '한식', '꿀등심')); assert.equal(a.src, 'assets/img/food/food_bbq.webp'); assert.equal(a.label, '고기구이');
  assert.equal(F.art(e('A', '카페', '카페', '팥빙수')), null);
  F.setReady([]);
});
console.log('food_thumb tests passed: ' + n);
