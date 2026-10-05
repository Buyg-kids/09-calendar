/* 작성자(인플루언서) 활동 요약 시트 - index.html / map.html 공통.
   프로필 사진·실시간 팔로워는 크롤링하지 않고, 파이프라인이 만든 creators_stats.json(누적 텍스트 통계)만 쓴다.
   사용: window.openCreatorSheet({ handle, nickname, live: [{ title, badge, sub, onClick }] })
   index.html은 파이프라인이 매일 재생성하므로 이 파일은 템플릿(generator/templates/view_page.html)과 map.html에서 <script src>로 불러온다. */
(function () {
  var STATS_URLS = ['./creators_stats.json', 'https://buyg-kids.github.io/09-calendar/creators_stats.json'];
  var statsPromise = null;
  var css = '' +
    '.cs-backdrop{position:fixed;inset:0;z-index:500;background:rgba(15,23,42,.45);opacity:0;pointer-events:none;transition:opacity .25s}' +
    '.cs-backdrop.show{opacity:1;pointer-events:auto}' +
    '.cs-sheet{position:fixed;left:0;right:0;bottom:0;z-index:501;margin:0 auto;width:100%;max-width:480px;max-height:86vh;max-height:86dvh;display:flex;flex-direction:column;' +
    'background:#fff;border-radius:20px 20px 0 0;box-shadow:0 -8px 30px rgba(0,0,0,.18);transform:translateY(100%);visibility:hidden;transition:transform .3s cubic-bezier(.2,.8,.2,1),visibility 0s linear .3s;color:var(--text-main,#222)}' +
    '.cs-sheet.show{transform:translateY(0);visibility:visible;transition:transform .3s cubic-bezier(.2,.8,.2,1)}' +
    '@media(min-width:640px){.cs-sheet{top:50%;bottom:auto;left:50%;right:auto;width:480px;border-radius:20px;opacity:0;transform:translate(-50%,-46%);transition:transform .2s,opacity .2s,visibility 0s linear .2s}' +
    '.cs-sheet.show{opacity:1;transform:translate(-50%,-50%);transition:transform .2s,opacity .2s}}' +
    '.cs-head{position:relative;flex:0 0 auto;height:40px}' +
    '.cs-grab{position:absolute;top:8px;left:50%;width:40px;height:4px;margin-left:-20px;border-radius:2px;background:#e2e8f0}' +
    '.cs-close{position:absolute;top:6px;right:10px;width:32px;height:32px;border:0;border-radius:50%;background:#f1f5f9;color:#334155;font-size:15px;cursor:pointer;padding:0}' +
    '.cs-scroll{flex:1 1 auto;overflow-y:auto;overscroll-behavior:contain;-webkit-overflow-scrolling:touch;padding:0 18px calc(18px + env(safe-area-inset-bottom,0px))}' +
    '.cs-who{display:flex;align-items:center;gap:12px}' +
    '.cs-avatar{flex:0 0 52px;width:52px;height:52px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#fff;font-size:21px;font-weight:800}' +
    '.cs-names{min-width:0}' +
    '.cs-nick{display:block;font-size:17px;font-weight:800;line-height:1.3;word-break:keep-all}' +
    '.cs-handle{display:block;margin-top:1px;font-size:13px;color:#64748b;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
    '.cs-profile{display:block;margin-top:12px;padding:11px 12px;border-radius:12px;background:#f3effa;color:var(--brand-purple,#5f0080);font-size:13.5px;font-weight:800;text-align:center;text-decoration:none}' +
    '.cs-stats{display:grid;grid-template-columns:1fr 1fr 1.25fr;gap:8px;margin-top:14px}' +
    '.cs-stat{background:var(--bg-light,#f8fafc);border-radius:12px;padding:10px 6px;text-align:center;min-width:0}' +
    '.cs-stat b{display:block;font-size:16px;font-weight:800;color:var(--text-main,#222);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.cs-stat span{display:block;margin-top:2px;font-size:11px;color:#94a3b8;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.cs-sec{margin-top:20px}' +
    '.cs-sec h3{margin:0 0 8px;font-size:14.5px;font-weight:800}' +
    '.cs-item{display:flex;align-items:center;gap:10px;padding:8px 10px;margin-bottom:6px;border:1px solid #eef0f3;border-radius:14px;text-decoration:none;color:inherit;cursor:pointer;background:#fff;box-shadow:0 1px 3px rgba(15,23,42,.04)}.cs-item:active{background:#fafafa}.cs-thumb{flex:0 0 44px;width:44px;height:44px;border-radius:10px;background:#ede5f3 center/cover no-repeat;overflow:hidden}.cs-thumb img{display:block;width:100%;height:100%;object-fit:cover}' +
    '.cs-item-body{flex:1;min-width:0}' +
    '.cs-item-title{font-size:13px;font-weight:700;line-height:1.35;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.cs-item-sub{margin-top:2px;font-size:11.5px;color:#94a3b8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.cs-badge{flex:0 0 auto;font-size:11px;font-weight:800;padding:3px 7px;border-radius:6px;background:#f1f5f9;color:#64748b;white-space:nowrap}' +
    '.cs-badge.hot{background:#fff0ee;color:#e5484d}' +
    '.cs-empty{padding:14px;border-radius:12px;background:#f8fafc;color:#94a3b8;font-size:12.5px;text-align:center}' +
    '.cs-note{margin-top:14px;font-size:11px;color:#b0b8c4;text-align:center}' +
    'body.cs-open{overflow:hidden}';
  var built = false, backdrop, sheet, scrollEl;

  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function build() {
    if (built) return; built = true;
    var st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
    backdrop = el('div', 'cs-backdrop');
    sheet = el('div', 'cs-sheet'); sheet.setAttribute('role', 'dialog'); sheet.setAttribute('aria-modal', 'true'); sheet.setAttribute('aria-label', '작성자 활동 요약');
    var head = el('div', 'cs-head'); head.appendChild(el('div', 'cs-grab'));
    var close = el('button', 'cs-close', '✕'); close.type = 'button'; close.setAttribute('aria-label', '닫기'); head.appendChild(close);
    scrollEl = el('div', 'cs-scroll');
    sheet.appendChild(head); sheet.appendChild(scrollEl);
    document.body.appendChild(backdrop); document.body.appendChild(sheet);
    close.addEventListener('click', closeSheet); backdrop.addEventListener('click', closeSheet);
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeSheet(); });
  }
  function closeSheet() {
    if (!built) return;
    sheet.classList.remove('show'); backdrop.classList.remove('show'); document.body.classList.remove('cs-open');
  }
  function loadStats() {
    if (statsPromise) return statsPromise;
    statsPromise = (function next(i) {
      if (i >= STATS_URLS.length) return Promise.resolve(null);
      return fetch(STATS_URLS[i], { cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : Promise.reject(); })
        .catch(function () { return next(i + 1); });
    })(0).then(function (j) { if (!j) statsPromise = null; return j; });
    return statsPromise;
  }
  function avatarColor(seed) {
    var colors = ['#7b1fa2', '#e11d48', '#0891b2', '#ea580c', '#4f46e5', '#059669', '#db2777'], h = 0;
    for (var i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
    return colors[h % colors.length];
  }
  function thumb(url) {
    var t = el('div', 'cs-thumb');
    if (url && url.indexOf('/icons/placeholder-') < 0) { var i = document.createElement('img'); i.alt = ''; i.loading = 'lazy'; i.src = url; i.onerror = function () { i.remove(); }; t.appendChild(i); }
    return t;
  }
  function dt(s) { return typeof window.displayTitle === 'function' ? window.displayTitle(s) : s; }
  function md(iso) { var m = /^\d{4}-(\d{2})-(\d{2})/.exec(iso || ''); return m ? (+m[1]) + '/' + (+m[2]) : ''; }
  function ga(name, params) { try { if (typeof gtag === 'function') gtag('event', name, params); } catch (e) {} }

  function render(opts, stats) {
    var handle = opts.handle, c = stats && stats.creators && stats.creators[handle.toLowerCase()];
    var nickname = opts.nickname || (c && c.nickname) || '';
    scrollEl.innerHTML = '';
    scrollEl.scrollTop = 0;
    var who = el('div', 'cs-who');
    var av = el('div', 'cs-avatar', (nickname || handle).replace(/[^0-9a-zA-Z가-힣]/g, '').charAt(0).toUpperCase() || 'B');
    av.style.background = avatarColor(handle);
    var names = el('div', 'cs-names');
    names.appendChild(el('span', 'cs-nick', nickname || '@' + handle));
    names.appendChild(el('span', 'cs-handle', '@' + handle));
    who.appendChild(av); who.appendChild(names);
    scrollEl.appendChild(who);
    var prof = el('a', 'cs-profile', 'Instagram 프로필 바로가기 ›');
    prof.href = 'https://www.instagram.com/' + encodeURIComponent(handle) + '/'; prof.target = '_blank'; prof.rel = 'noopener';
    prof.addEventListener('click', function () { ga('creator_profile_click', { influencer_handle: handle }); });
    scrollEl.appendChild(prof);

    var total = c ? c.total : (opts.live || []).length;
    var active = c ? c.active : (opts.live || []).length;
    var bar = el('div', 'cs-stats');
    function stat(v, l) { var s = el('div', 'cs-stat'); s.appendChild(el('b', '', v)); s.appendChild(el('span', '', l)); bar.appendChild(s); }
    stat(total + '건', '누적 공구');
    stat(active + '건', '진행·예정');
    stat(c ? c.top_percent + '%' : '-', c ? c.top_category + ' 주력' : '주력 카테고리');
    scrollEl.appendChild(bar);

    var sec1 = el('div', 'cs-sec'); sec1.appendChild(el('h3', '', '지금 진행·예정 공구'));
    if ((opts.live || []).length) {
      opts.live.forEach(function (it) {
        var row = el('div', 'cs-item'); row.appendChild(thumb(it.img)); var body = el('div', 'cs-item-body');
        body.appendChild(el('div', 'cs-item-title', it.title)); if (it.sub) body.appendChild(el('div', 'cs-item-sub', it.sub));
        row.appendChild(body); if (it.badge) row.appendChild(el('span', 'cs-badge' + (it.hot ? ' hot' : ''), it.badge));
        row.addEventListener('click', function () { closeSheet(); if (it.onClick) it.onClick(); });
        sec1.appendChild(row);
      });
    } else sec1.appendChild(el('div', 'cs-empty', '지금 진행 중인 공구는 없어요'));
    scrollEl.appendChild(sec1);

    var sec2 = el('div', 'cs-sec'); sec2.appendChild(el('h3', '', '최근 공구 이력'));
    if (c && c.history && c.history.length) {
      c.history.forEach(function (h) {
        var row = el(h.post_url ? 'a' : 'div', 'cs-item'); var body = el('div', 'cs-item-body');
        if (h.post_url) { row.href = 'https://www.instagram.com/' + h.post_url; row.target = '_blank'; row.rel = 'noopener'; }
        row.appendChild(thumb(h.image_url)); body.appendChild(el('div', 'cs-item-title', dt(h.product_name)));
        body.appendChild(el('div', 'cs-item-sub', h.category + ' · ' + md(h.start_date) + (h.end_date && h.end_date !== h.start_date ? ' ~ ' + md(h.end_date) : '')));
        row.appendChild(body); sec2.appendChild(row);
      });
    } else sec2.appendChild(el('div', 'cs-empty', stats ? '아직 쌓인 이력이 없어요' : '이력을 불러오지 못했어요'));
    scrollEl.appendChild(sec2);
    scrollEl.appendChild(el('div', 'cs-note', 'Buyg에 모인 공구 기록 기준 통계예요'));
  }

  window.openCreatorSheet = function (opts) {
    if (!opts || !opts.handle) return;
    build();
    render(opts, null);
    document.body.classList.add('cs-open'); backdrop.classList.add('show'); sheet.classList.add('show');
    ga('creator_sheet_open', { influencer_handle: opts.handle });
    loadStats().then(function (stats) { if (stats && sheet.classList.contains('show')) render(opts, stats); });
  };
  window.closeCreatorSheet = closeSheet;
  window.loadCreatorStats = loadStats;
})();
