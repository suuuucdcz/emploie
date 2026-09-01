/* Emploi du temps Auriga - interface. */

const CACHE_KEY = 'auriga-edt-cache-v1';
const KIND_LABEL = {
  CM: 'CM', TD: 'TD', TP: 'TP', EXAM: 'EXAMEN', PROJET: 'PROJET', AUTRE: 'COURS',
};

const state = {
  events: [],
  selected: startOfDay(new Date()),
  view: 'day',      // 'day' | 'week'
  meta: null,
  loading: false,
};

const el = {
  month: document.getElementById('month-label'),
  strip: document.getElementById('weekstrip'),
  content: document.getElementById('content'),
  status: document.getElementById('status-text'),
  today: document.getElementById('today'),
  refresh: document.getElementById('refresh'),
  viewToggle: document.getElementById('view-toggle'),
  prevBtn: document.getElementById('prev-btn'),
  nextBtn: document.getElementById('next-btn'),
};

/* ------------------------------------------------------------- utilitaires */

function startOfDay(date) {
  const copy = new Date(date);
  copy.setHours(0, 0, 0, 0);
  return copy;
}

function addDays(date, count) {
  const copy = new Date(date);
  copy.setDate(copy.getDate() + count);
  return copy;
}

function mondayOf(date) {
  const copy = startOfDay(date);
  // getDay() : 0 = dimanche. On veut lundi comme premier jour.
  const shift = (copy.getDay() + 6) % 7;
  return addDays(copy, -shift);
}

function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate();
}

function fmtTime(date) {
  return date.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
}

function fmtDayLong(date) {
  const label = date.toLocaleDateString('fr-FR', {
    weekday: 'long', day: 'numeric', month: 'long',
  });
  // Intl ecrit "1 septembre" ; en francais on dit "1er septembre".
  return label.replace(/\b1 (?=\p{L})/u, '1er ');
}

function durationLabel(minutes) {
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  if (hours && mins) return `${hours} h ${String(mins).padStart(2, '0')}`;
  if (hours) return `${hours} h`;
  return `${mins} min`;
}

function eventsOn(date) {
  return state.events.filter((evt) => sameDay(evt._start, date));
}

/* ------------------------------------------------------------------ donnees */

function hydrate(payload) {
  const events = (payload.events || []).map((evt) => ({
    ...evt,
    _start: new Date(evt.start),
    _end: new Date(evt.end),
  })).filter((evt) => !Number.isNaN(evt._start.getTime()));

  events.sort((a, b) => a._start - b._start);
  state.events = events;
  state.meta = {
    fetchedAt: payload.fetchedAt,
    source: payload.source,
    stale: payload.stale,
    error: payload.error,
  };
}

function readCache() {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (err) {
    return null;
  }
}

function writeCache(payload) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(payload));
  } catch (err) {
    /* quota plein ou navigation privee : le cache est un bonus, on ignore */
  }
}

async function load({ force = false } = {}) {
  state.loading = true;
  el.refresh.classList.add('spinning');
  try {
    const res = await fetch(`/api/schedule${force ? '?refresh=1' : ''}`, {
      cache: 'no-store',
    });
    const payload = await res.json();
    if (!res.ok && !(payload.events || []).length) throw new Error(payload.error || 'erreur serveur');
    hydrate(payload);
    writeCache(payload);
  } catch (err) {
    const cached = readCache();
    if (cached) {
      hydrate(cached);
      state.meta = { ...state.meta, stale: true, error: 'hors ligne' };
    } else {
      state.meta = { error: err.message };
    }
  } finally {
    state.loading = false;
    el.refresh.classList.remove('spinning');
    render();
  }
}

/* ------------------------------------------------------------------- rendu */

function render() {
  renderHeader();
  renderStrip();
  renderContent();
  renderStatus();
}

function renderHeader() {
  const label = state.view === 'week'
    ? `Semaine du ${mondayOf(state.selected).toLocaleDateString('fr-FR', { day: 'numeric', month: 'long' })}`
    : state.selected.toLocaleDateString('fr-FR', { month: 'long', year: 'numeric' });
  el.month.textContent = label;
  el.viewToggle.textContent = state.view === 'week' ? 'Jour' : 'Semaine';
  el.viewToggle.setAttribute('aria-pressed', String(state.view === 'week'));
}

function renderStrip() {
  const monday = mondayOf(state.selected);
  const today = startOfDay(new Date());
  el.strip.innerHTML = '';

  for (let i = 0; i < 7; i += 1) {
    const day = addDays(monday, i);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'day-chip';
    if (sameDay(day, today)) button.classList.add('today');
    if (sameDay(day, state.selected)) button.classList.add('selected');

    const hasEvents = eventsOn(day).length > 0;
    button.innerHTML = `
      <span class="dow">${day.toLocaleDateString('fr-FR', { weekday: 'short' }).slice(0, 3)}</span>
      <span class="dom">${day.getDate()}</span>
      <span class="dot${hasEvents ? '' : ' no-events'}"></span>`;
    button.setAttribute('aria-label', fmtDayLong(day));
    button.addEventListener('click', () => {
      state.selected = day;
      if (state.view === 'week') state.view = 'day';
      render();
    });
    el.strip.appendChild(button);
  }
}

function renderContent() {
  if (state.loading && !state.events.length) {
    el.content.innerHTML = '<p class="loading">Chargement…</p>';
    return;
  }

  if (state.meta && state.meta.error && !state.events.length) {
    el.content.innerHTML = `
      <p class="empty"><span class="big">⚠️</span>
      Impossible de charger l'emploi du temps.<br>${escapeHtml(state.meta.error)}</p>`;
    return;
  }

  el.content.innerHTML = '';
  if (state.view === 'week') renderWeek();
  else renderDay();
}

function renderDay() {
  const events = eventsOn(state.selected);
  const isToday = sameDay(state.selected, startOfDay(new Date()));

  if (isToday) {
    const banner = nextCourseBanner();
    if (banner) el.content.appendChild(banner);
  }

  if (!events.length) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.innerHTML = `<span class="big">🎉</span>Aucun cours le ${fmtDayLong(state.selected)}.`;
    el.content.appendChild(empty);
    return;
  }

  events.forEach((evt) => el.content.appendChild(card(evt)));
}

function renderWeek() {
  const monday = mondayOf(state.selected);
  let total = 0;

  for (let i = 0; i < 7; i += 1) {
    const day = addDays(monday, i);
    const events = eventsOn(day);
    if (!events.length) continue;
    total += events.length;

    const heading = document.createElement('div');
    heading.className = 'day-heading';
    heading.textContent = fmtDayLong(day);
    el.content.appendChild(heading);
    events.forEach((evt) => el.content.appendChild(card(evt)));
  }

  if (!total) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.innerHTML = '<span class="big">🎉</span>Aucun cours cette semaine.';
    el.content.appendChild(empty);
  }
}

function card(evt) {
  const now = new Date();
  const ongoing = evt._start <= now && now < evt._end;
  const minutes = Math.round((evt._end - evt._start) / 60000);

  const node = document.createElement('article');
  node.className = `card${ongoing ? ' now' : ''}`;
  node.style.setProperty('--kind', `var(--${evt.kind.toLowerCase()})`);

  const meta = [];
  if (evt.location) meta.push(`<span>📍 ${escapeHtml(evt.location)}</span>`);
  if (evt.teacher) meta.push(`<span>👤 ${escapeHtml(evt.teacher)}</span>`);
  meta.push(`<span>⏱ ${durationLabel(minutes)}</span>`);

  node.innerHTML = `
    <div class="card-time">
      <span class="start">${evt.allDay ? 'Jour' : fmtTime(evt._start)}</span>
      <span class="end">${evt.allDay ? 'entier' : fmtTime(evt._end)}</span>
    </div>
    <div class="card-body">
      <span class="badge">${KIND_LABEL[evt.kind] || 'COURS'}</span>
      ${ongoing ? '<span class="badge now-badge">EN COURS</span>' : ''}
      <h2 class="card-title">${escapeHtml(evt.title || evt.rawTitle || 'Cours')}</h2>
      <div class="card-meta">${meta.join('')}</div>
    </div>`;
  return node;
}

function nextCourseBanner() {
  const now = new Date();
  const ongoing = state.events.find((evt) => evt._start <= now && now < evt._end);
  const upcoming = state.events.find((evt) => evt._start > now);

  const banner = document.createElement('div');
  banner.className = 'next-banner';

  if (ongoing) {
    const left = Math.round((ongoing._end - now) / 60000);
    banner.innerHTML = `En cours : <strong>${escapeHtml(ongoing.title)}</strong>
      ${ongoing.location ? `· ${escapeHtml(ongoing.location)}` : ''}
      <span class="when">fin dans ${durationLabel(left)}</span>`;
    return banner;
  }

  if (upcoming && sameDay(upcoming._start, startOfDay(now))) {
    const inMinutes = Math.round((upcoming._start - now) / 60000);
    banner.innerHTML = `Prochain cours : <strong>${escapeHtml(upcoming.title)}</strong>
      ${upcoming.location ? `· ${escapeHtml(upcoming.location)}` : ''}
      <span class="when">dans ${durationLabel(inMinutes)}</span>`;
    return banner;
  }

  return null;
}

function renderStatus() {
  if (!state.meta) { el.status.textContent = '—'; return; }

  const parts = [];
  if (state.meta.fetchedAt) {
    const when = new Date(state.meta.fetchedAt);
    if (!Number.isNaN(when.getTime())) {
      parts.push(`maj ${when.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}`);
    }
  }
  parts.push(`${state.events.length} cours`);

  el.status.textContent = parts.join(' · ');
  el.status.className = state.meta.stale ? 'warn' : '';
  if (state.meta.stale) {
    el.status.textContent += ' · données en cache';
  }
}

function escapeHtml(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/* ------------------------------------------------------------ interactions */

function step(direction) {
  const delta = state.view === 'week' ? 7 * direction : direction;
  state.selected = addDays(state.selected, delta);
  render();
}

el.today.addEventListener('click', () => {
  state.selected = startOfDay(new Date());
  render();
});

el.refresh.addEventListener('click', () => load({ force: true }));

el.viewToggle.addEventListener('click', () => {
  state.view = state.view === 'week' ? 'day' : 'week';
  render();
});

el.prevBtn.addEventListener('click', () => step(-1));
el.nextBtn.addEventListener('click', () => step(1));

document.addEventListener('keydown', (event) => {
  if (event.key === 'ArrowLeft') step(-1);
  else if (event.key === 'ArrowRight') step(1);
});

// Balayage horizontal pour changer de jour / semaine.
let touchStartX = 0;
let touchStartY = 0;
el.content.addEventListener('touchstart', (event) => {
  touchStartX = event.changedTouches[0].clientX;
  touchStartY = event.changedTouches[0].clientY;
}, { passive: true });

el.content.addEventListener('touchend', (event) => {
  const dx = event.changedTouches[0].clientX - touchStartX;
  const dy = event.changedTouches[0].clientY - touchStartY;
  // on ignore les gestes trop verticaux : c'est un scroll, pas un swipe
  if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.6) {
    step(dx < 0 ? 1 : -1);
  }
}, { passive: true });

// Le bandeau "prochain cours" et l'etat "en cours" doivent rester justes.
setInterval(() => {
  if (!state.loading) render();
}, 60000);

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') load();
});

/* ---------------------------------------------------------------- demarrage */

const cached = readCache();
if (cached) { hydrate(cached); render(); }
load();

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      /* pas de HTTPS ou navigation privee : l'appli marche quand meme */
    });
  });
}


/* SYNC LOGIC */
const syncModal = document.getElementById('sync-modal');
const syncBtn = document.getElementById('refresh'); // We will override the refresh button to open the modal instead!
const syncStartBtn = document.getElementById('sync-start-btn');
const syncCloseBtn = document.getElementById('sync-close-btn');
const syncStatus = document.getElementById('sync-status');
const syncA2f = document.getElementById('sync-a2f');
const syncEmail = document.getElementById('sync-email');
const syncPassword = document.getElementById('sync-password');

// Restaurer le mot de passe sauvegarde
const savedPwd = localStorage.getItem('auriga_password');
if (savedPwd) syncPassword.value = savedPwd;

syncBtn.addEventListener('click', () => { syncModal.hidden = false; });
syncCloseBtn.addEventListener('click', () => { syncModal.hidden = true; });

let pollInterval = null;
function pollSync() {
  const email = localStorage.getItem('auriga_email') || 'mathis.derory@ipsa.fr';
  fetch(/api/sync/status?email=).then(r => r.json()).then(st => {
    if (st.status === 'idle') { syncStatus.textContent = 'En attente...'; }
    else if (st.status === 'logging_in') { syncStatus.textContent = 'Connexion  Microsoft en cours...'; }
    else if (st.status === 'waiting_2fa') {
      syncStatus.textContent = 'Veuillez valider la notification sur votre tlphone avec ce numro :';
      syncA2f.style.display = 'block';
      syncA2f.textContent = st.code;
    }
    else if (st.status === 'downloading') {
      syncStatus.textContent = 'Tlchargement de l\\'anne en cours...';
      syncA2f.style.display = 'none';
    }
    else if (st.status === 'success') {
      syncStatus.textContent = 'Termin ! Le planning est  jour.';
      syncA2f.style.display = 'none';
      clearInterval(pollInterval);
      setTimeout(() => { syncModal.hidden = true; location.reload(); }, 2000);
    }
    else if (st.status === 'error') {
      syncStatus.textContent = 'Erreur : ' + st.error_msg;
      syncA2f.style.display = 'none';
      clearInterval(pollInterval);
    }
  });
}

syncStartBtn.addEventListener('click', () => {
  const email = syncEmail.value;
  const password = syncPassword.value;
  if(!password) return alert('Mot de passe requis');
  localStorage.setItem('auriga_password', password);
  localStorage.setItem('auriga_email', email);
  fetch('/api/sync/start', { method: 'POST', body: JSON.stringify({email, password}) });
  syncStatus.textContent = 'Dmarrage du robot...';
  pollInterval = setInterval(pollSync, 2000);
});
