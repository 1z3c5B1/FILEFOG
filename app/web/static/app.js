const toastBox = () => {
  let el = document.querySelector('.toast');
  if (!el) {
    el = document.createElement('div');
    el.className = 'toast';
    document.body.appendChild(el);
  }
  return el;
};

function toast(msg, kind = '') {
  const el = toastBox();
  const d = document.createElement('div');
  if (kind) d.className = kind;
  d.textContent = msg;
  el.appendChild(d);
  setTimeout(() => {
    d.style.transition = 'opacity .3s, transform .3s';
    d.style.opacity = '0';
    d.style.transform = 'translateX(120%)';
    setTimeout(() => d.remove(), 300);
  }, 3200);
}

async function api(url, opts = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  let data = {};
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) throw new Error(data.error || data.detail || res.statusText);
  return data;
}

const fmtSize = (n) => {
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n : n.toFixed(1)) + ' ' + u[i];
};

const fmtDate = (s) => {
  if (!s) return '—';
  try { return new Date(s).toLocaleString(); } catch (_) { return s; }
};

const icon = (name) => {
  const map = { folder: '\U0001f4c1', file: '\U0001f4c4' };
  return map[name] || '\U0001f4c4';
};

function applyTheme(theme) {
  if (theme === 'light') document.body.classList.add('light');
  else document.body.classList.remove('light');
}

document.addEventListener('DOMContentLoaded', () => {
  const theme = localStorage.getItem('tb_theme');
  if (theme) applyTheme(theme);

  const drop = document.getElementById('drop');
  if (drop) initDrop(drop);
});

function initDrop(drop) {
  const input = document.getElementById('fileInput');
  const path = drop.dataset.path || '';
  drop.addEventListener('click', () => input.click());
  input.addEventListener('change', () => upload(input.files));
  ['dragenter', 'dragover'].forEach(e => drop.addEventListener(e, ev => {
    ev.preventDefault(); drop.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach(e => drop.addEventListener(e, ev => {
    ev.preventDefault(); drop.classList.remove('over');
  }));
  drop.addEventListener('drop', ev => upload(ev.dataTransfer.files));
}

async function upload(files) {
  if (!files || !files.length) return;
  const fd = new FormData();
  const path = (document.getElementById('drop') || {}).dataset?.path || '';
  fd.append('path', path || '');
  for (const f of files) fd.append('files', f);
  const t = toast(`Загрузка ${files.length} файл(ов)...`);
  try {
    const res = await fetch('/api/files/upload', { method: 'POST', body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'upload failed');
    t.remove();
    toast('Загружено: ' + data.saved.length, 'ok');
    setTimeout(() => location.reload(), 600);
  } catch (e) {
    t.remove();
    toast('Ошибка: ' + e.message, 'err');
  }
}

function selectAll(checked) {
  document.querySelectorAll('.pick').forEach(c => c.checked = checked);
}

async function deleteSelected() {
  const paths = [...document.querySelectorAll('.pick:checked')].map(c => c.value);
  if (!paths.length) return toast('Ничего не выбрано', 'err');
  if (!confirm('Удалить ' + paths.length + ' объект(ов)?')) return;
  try {
    await api('/api/files/delete', { method: 'POST', body: JSON.stringify({ paths }) });
    toast('Удалено', 'ok');
    setTimeout(() => location.reload(), 500);
  } catch (e) { toast('Ошибка: ' + e.message, 'err'); }
}

async function mkdir() {
  const name = prompt('Имя новой папки:');
  if (!name) return;
  const path = new URLSearchParams(location.search).get('path') || '';
  try {
    await api('/api/files/mkdir', { method: 'POST', body: JSON.stringify({ name, path }) });
    location.reload();
  } catch (e) { toast('Ошибка: ' + e.message, 'err'); }
}

function openSettingsSection(name) {
  document.querySelectorAll('[data-section]').forEach(el => el.hidden = el.dataset.section !== name);
  document.querySelectorAll('.section-tabs button').forEach(b => b.classList.toggle('active', b.dataset.target === name));
  history.replaceState(null, '', '#' + name);
  localStorage.setItem('tb_section', name);
}

function initSettingsPage() {
  const wrap = document.getElementById('settingsWrap');
  if (!wrap) return;

  wrap.addEventListener('click', e => {
    const btn = e.target.closest('.section-tabs button');
    if (btn) openSettingsSection(btn.dataset.target);
  });

  let timer = null;
  wrap.addEventListener('change', async e => {
    const input = e.target.closest('[data-key]');
    if (!input) return;
    const key = input.dataset.key;
    let value;
    if (input.type === 'checkbox') value = input.checked ? 1 : 0;
    else if (input.type === 'number') value = input.value;
    else value = input.value;

    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        const data = await api('/api/settings', {
          method: 'POST',
          body: JSON.stringify({ prefs: { [key]: value } }),
        });
        if (key === 'theme') { localStorage.setItem('tb_theme', value); applyTheme(value); }
        if (key === 'locale') setTimeout(() => location.reload(), 300);
        toast('Сохранено', 'ok');
        if (!data.ok) toast('Ошибка: ' + JSON.stringify(data.errors), 'err');
      } catch (err) { toast('Ошибка: ' + err.message, 'err'); }
    }, 350);
  });

  const start = (location.hash || '').slice(1) || localStorage.getItem('tb_section');
  openSettingsSection(start || document.querySelector('.section-tabs button')?.dataset.target);
}

document.addEventListener('DOMContentLoaded', initSettingsPage);

async function runSync(direction = 'both') {
  const btn = document.getElementById('syncBtn');
  if (btn) { btn.disabled = true; btn.textContent = 'Синхронизация...'; }
  const t = toast('Запущена синхронизация...');
  try {
    const data = await api('/api/sync', { method: 'POST', body: JSON.stringify({ direction }) });
    t.remove();
    toast(data.summary, data.ok ? 'ok' : 'err');
    setTimeout(() => location.reload(), 1500);
  } catch (e) {
    t.remove();
    toast('Ошибка: ' + e.message, 'err');
    if (btn) { btn.disabled = false; btn.textContent = '\U0001f504 Синхронизировать'; }
  }
}

async function unlink(provider) {
  if (!confirm('Отвязать ' + provider + '?')) return;
  try {
    await api(`/api/accounts/${provider}/unlink`, { method: 'POST' });
    location.reload();
  } catch (e) { toast('Ошибка: ' + e.message, 'err'); }
}

async function loadLogs() {
  const box = document.getElementById('logs');
  if (!box) return;
  try {
    const data = await api('/api/sync/logs');
    if (!data.items.length) { box.innerHTML = '<tr><td colspan="6" class="muted">Пока пусто</td></tr>'; return; }
    box.innerHTML = data.items.map(i => `
      <tr>
        <td>${i.provider}</td>
        <td><span class="badge ${i.status === 'ok' ? 'ok' : 'err'}">${i.status}</span></td>
        <td class="mono">↑${i.up} ↓${i.down} \u{1f5d1}${i.del}</td>
        <td class="muted">${i.direction}</td>
        <td class="muted">${fmtDate(i.created_at)}</td>
      </tr>`).join('');
  } catch (e) { box.innerHTML = `<tr><td colspan="6" class="err">${e.message}</td></tr>`; }
}

document.addEventListener('DOMContentLoaded', loadLogs);

/* ---------- неон: счётчики чисел + курсорный свет ---------- */
function animateCounters() {
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  document.querySelectorAll('[data-count]').forEach(el => {
    const target = parseFloat(el.dataset.count);
    if (reduce || !isFinite(target)) return;
    const dec = (el.dataset.count.split('.')[1] || '').length;
    const t0 = performance.now();
    const dur = 1100;
    const step = (t) => {
      const p = Math.min(1, (t - t0) / dur);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = (target * eased).toFixed(dec);
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
}

function cursorGlow() {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  if (window.matchMedia('(hover: none)').matches) return;
  const glow = document.createElement('div');
  glow.style.cssText =
    'position:fixed;z-index:-1;pointer-events:none;border-radius:50%;' +
    'width:520px;height:520px;margin:-260px 0 0 -260px;mix-blend-mode:screen;' +
    'background:radial-gradient(circle,rgba(0,229,255,.11),rgba(177,74,255,.07) 45%,transparent 70%);' +
    'transition:opacity .3s;will-change:transform';
  document.body.appendChild(glow);
  let tx = 0, ty = 0, x = 0, y = 0, raf = null;

  const onMove = (e) => {
    tx = e.clientX; ty = e.clientY;
    if (!raf) raf = requestAnimationFrame(loop);
  };
  const loop = () => {
    x += (tx - x) * 0.12;
    y += (ty - y) * 0.12;
    glow.style.transform = `translate(${x}px, ${y}px)`;
    raf = Math.abs(tx - x) > 0.4 || Math.abs(ty - y) > 0.4 ? requestAnimationFrame(loop) : null;
  };
  window.addEventListener('pointermove', onMove, { passive: true });
}

document.addEventListener('DOMContentLoaded', () => {
  animateCounters();
  cursorGlow();
  initGallery();
  initTelegramApp();
});

/* ---------- Telegram Mini App ---------- */
const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

function tgTheme() {
  if (!tg || !tg.themeParams) return;
  const p = tg.themeParams;
  const set = (k, v) => v && document.documentElement.style.setProperty(k, v);
  set('--tg-bg', p.bg_color || '');
  set('--tg-fg', p.text_color || '');
  set('--tg-hint', p.hint_color || '');
  set('--tg-link', p.link_color || '');
  document.body.classList.toggle('tg-dark', p.theme === 'dark');
}

function tgChrome() {
  if (!tg) return;
  const apply = () => {
    if (tg.platform === 'unknown') return;   // открыто в обычном браузере
    document.body.classList.add('in-telegram');
    if (tg.colorChanged) tg.themeChangedCallback(tgTheme);
    if (tg.fullScreenChanged) tg.fullscreenChangedCallback(() => document.body.classList.toggle('tg-fs', tg.isFullscreen));
  };
  apply();
  tg.onEvent?.('themeChanged', apply);
  tg.onEvent?.('fullscreenChanged', apply);

  // элемент с таким id уже может быть в разметке страницы,
  // второй раз создавать нельзя - иначе кнопки накапливаются
  let back = document.getElementById('tgBack');
  if (!back) {
    back = document.createElement('button');
    back.id = 'tgBack';
    back.className = 'tg-back';
    back.type = 'button';
    back.innerHTML = '<span>&#8592;</span>';
    back.addEventListener('click', () => tg.BackButton && tg.BackButton.click());
    document.body.appendChild(back);
  }

  tg.BackButton?.show();
  tg.onEvent?.('backButtonClicked', () => {
    if (history.length > 1) history.back();
    else tg.close();
  });
}

function tgReady() {
  const l = document.getElementById('tgLoader');
  if (l) l.remove();
  document.body.classList.remove('tg-unready');
}

async function tgAuth() {
  if (!tg || !tg.initData) return false;   // не Mini App — OAuth остаётся основным
  try {
    const r = await fetch('/api/tg/auth', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ init_data: tg.initData }),
    });
    if (!r.ok) return false;
    const d = await r.json();
    if (d.ok) {
      const to = d.redirect || '/';
      // перезагружать страницу, если мы уже на ней, нельзя:
      // иначе location.replace('/') на '/' зациклит страницу
      if (location.pathname + location.search !== to) location.replace(to);
      else document.body.dataset.authed = '1';
    }
    return !!d.ok;
  } catch (_) {
    return false;
  }
}

function alreadyAuthed() {
  return document.body.dataset.authed === '1';
}

function initTelegramApp() {
  if (!tg) {
    tgReady();
    return;
  }
  tg.ready();
  tg.expand();
  tgTheme();
  tgChrome();
  // внутри клиента, но initData нет
  if (tg.isAvailable && !tg.isAvailable()) {
    tgReady();
    return;
  }
  // сессия уже есть (кука пришла от сервера) — повторный вход вызвал бы
  // бесконечную перезагрузку, поэтому просто снимаем лоадер
  if (alreadyAuthed()) {
    tgReady();
    return;
  }
  tgAuth().finally(() => tgReady());
}

/* ---------- медиатека: галерея + просмотр ---------- */
let MEDIA = [];
let MEDIA_KIND = 'all';
let MEDIA_INDEX = -1;

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

function mediaUrl(p) { return '/api/raw?path=' + encodeURIComponent(p); }

function renderGallery() {
  const box = document.getElementById('gallery');
  if (!box) return;
  const list = MEDIA.filter(i => MEDIA_KIND === 'all' || i.kind === MEDIA_KIND);
  if (!list.length) {
    box.innerHTML = `<div class="gal-empty">
      <div class="gal-empty-ico">&#128247;</div>
      <div>Пока пусто</div>
      <div class="muted" style="margin-top:6px;font-size:13px">
        Загрузи фото или видео через <a href="/files">Файлы</a> или прямо в бота
      </div>
    </div>`;
    return;
  }
  box.innerHTML = list.map((i, n) => `
    <button class="gal-item ${i.kind}" data-i="${n}" title="${esc(i.name)}">
      ${i.kind === 'photo'
        ? `<img src="${mediaUrl(i.path)}" alt="${esc(i.name)}" loading="lazy">`
        : `<video src="${mediaUrl(i.path)}" preload="metadata" muted playsinline></video>
           <span class="gal-play">&#9654;</span>`}
      <span class="gal-meta">
        <span class="gal-name">${esc(i.name)}</span>
        <span class="gal-size">${esc(i.size_h)}</span>
      </span>
    </button>`).join('');
  box.querySelectorAll('.gal-item').forEach(el => {
    el.addEventListener('click', () => openViewer(Number(el.dataset.i), list));
  });
}

function filteredMedia() {
  return MEDIA.filter(i => MEDIA_KIND === 'all' || i.kind === MEDIA_KIND);
}

function openViewer(idx, list) {
  MEDIA_INDEX = idx;
  const it = list[idx];
  if (!it) return;
  let box = document.getElementById('viewer');
  if (!box) {
    box = document.createElement('div');
    box.id = 'viewer';
    box.className = 'viewer';
    box.innerHTML = `
      <div class="viewer-backdrop"></div>
      <div class="viewer-panel" role="dialog" aria-modal="true">
        <button class="viewer-close" aria-label="Закрыть">&#10005;</button>
        <button class="viewer-nav prev" aria-label="Назад">&#10094;</button>
        <div class="viewer-stage"></div>
        <button class="viewer-nav next" aria-label="Вперёд">&#10095;</button>
        <div class="viewer-bar">
          <div class="viewer-name"></div>
          <div class="viewer-sub"></div>
        </div>
        <div class="viewer-dots"></div>
      </div>`;
    document.body.appendChild(box);
    box.querySelector('.viewer-backdrop').addEventListener('click', closeViewer);
    box.querySelector('.viewer-close').addEventListener('click', closeViewer);
    box.querySelector('.prev').addEventListener('click', () => stepViewer(-1));
    box.querySelector('.next').addEventListener('click', () => stepViewer(1));
    box.addEventListener('click', (e) => { if (e.target === box) closeViewer(); });
  }
  paintViewer(list);
  box.classList.add('open');
  document.body.style.overflow = 'hidden';
}

function paintViewer(list) {
  const box = document.getElementById('viewer');
  const it = list[MEDIA_INDEX];
  if (!it) return closeViewer();
  const stage = box.querySelector('.viewer-stage');
  stage.innerHTML = it.kind === 'photo'
    ? `<img src="${mediaUrl(it.path)}" alt="${esc(it.name)}">`
    : `<video src="${mediaUrl(it.path)}" controls autoplay playsinline></video>`;
  box.querySelector('.viewer-name').textContent = it.name;
  box.querySelector('.viewer-sub').textContent = `${it.size_h} · ${it.kind === 'photo' ? 'фото' : 'видео'}`;
  const many = list.length > 1;
  box.querySelector('.prev').style.display = many ? '' : 'none';
  box.querySelector('.next').style.display = many ? '' : 'none';
  box.querySelector('.viewer-dots').innerHTML = many
    ? list.map((_, n) => `<span class="dot ${n === MEDIA_INDEX ? 'on' : ''}"></span>`).join('')
    : '';
}

function stepViewer(d) {
  const list = filteredMedia();
  if (!list.length) return;
  MEDIA_INDEX = (MEDIA_INDEX + d + list.length) % list.length;
  paintViewer(list);
}

function closeViewer() {
  const box = document.getElementById('viewer');
  if (!box) return;
  const v = box.querySelector('video');
  if (v) { v.pause(); v.removeAttribute('src'); v.load(); }
  box.classList.remove('open');
  document.body.style.overflow = '';
  MEDIA_INDEX = -1;
}

function initGallery() {
  const box = document.getElementById('gallery');
  if (!box) return;

  const filter = document.getElementById('galFilter');
  if (filter) {
    filter.querySelectorAll('button').forEach(b => {
      b.addEventListener('click', () => {
        filter.querySelectorAll('button').forEach(x => x.classList.remove('active'));
        b.classList.add('active');
        MEDIA_KIND = b.dataset.kind || 'all';
        renderGallery();
      });
    });
  }

  document.addEventListener('keydown', (e) => {
    if (MEDIA_INDEX < 0) return;
    if (e.key === 'Escape') closeViewer();
    else if (e.key === 'ArrowLeft') stepViewer(-1);
    else if (e.key === 'ArrowRight') stepViewer(1);
  });

  api('/api/media')
    .then(d => { MEDIA = d.items || []; renderGallery(); })
    .catch(e => { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; });
}
