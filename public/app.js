/* Emploi du temps Auriga - interface. */

const CACHE_KEY = 'auriga-edt-cache-v1';
const KIND_LABEL = {
  CM: 'CM', TD: 'TD', TP: 'TP', EXAM: 'EXAMEN', PROJET: 'PROJET', AUTRE: 'COURS',
};

const state = {
  events: [],
  selected: startOfDay(new Date()),
  view: 'day',
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

function escapeHtml(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function getUserEmail() {
  return localStorage.getItem('auriga_email') || '';
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

// Le cache porte l'email auquel il appartient : sur un telephone partage, ou
// apres un changement de compte, on ne doit pas afficher l'agenda d'un autre.
function readCache() {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const entry = JSON.parse(raw);
    if (!entry || entry.email !== getUserEmail()) return null;
    return entry.payload;
  } catch (err) {
    return null;
  }
}

function writeCache(payload) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify({ email: getUserEmail(), payload }));
  } catch (err) {
    /* quota plein ou navigation privee */
  }
}

// Le serveur ne rafraichit son propre cache que toutes les 15 min : recharger
// plus souvent renverrait les memes octets. Sur un agenda d'annee complete la
// reponse depasse 250 Ko, autant ne pas la retelecharger a chaque fois que
// l'utilisateur revient dans l'appli.
const VISIBILITY_RELOAD_MS = 5 * 60 * 1000;
let lastLoadAt = 0;

async function load({ force = false } = {}) {
  const email = getUserEmail();

  // Pas d'email enregistre : rien a demander au serveur.
  if (!email) {
    state.loading = false;
    state.meta = { error: 'Veuillez vous connecter.' };
    render();
    openSyncModal();
    return;
  }

  // Pose avant l'attente : deux retours rapproches ne doivent pas lancer deux
  // requetes concurrentes.
  lastLoadAt = Date.now();
  state.loading = true;
  el.refresh.classList.add('spinning');
  try {
    const url = `/api/schedule?email=${encodeURIComponent(email)}${force ? '&refresh=1' : ''}`;
    const res = await fetch(url, { cache: 'no-store' });
    const payload = await res.json();
    if (!res.ok && !(payload.events || []).length) throw new Error(payload.error || 'erreur serveur');
    hydrate(payload);
    writeCache(payload);
  } catch (err) {
    lastLoadAt = 0;  // l'appel a echoue : la prochaine occasion doit reessayer
    const cached = readCache();
    if (cached) {
      hydrate(cached);
      state.meta = { ...state.meta, stale: true, error: 'hors ligne' };
    } else {
      state.meta = { error: err.message };
      openSyncModal();
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
    el.content.innerHTML = '<p class="loading">Chargement\u2026</p>';
    return;
  }

  if (state.meta && state.meta.error && !state.events.length) {
    el.content.innerHTML = `
      <p class="empty"><span class="big">\u26A0\uFE0F</span>
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
    empty.innerHTML = `<span class="big">\uD83C\uDF89</span>Aucun cours le ${fmtDayLong(state.selected)}.`;
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
    empty.innerHTML = '<span class="big">\uD83C\uDF89</span>Aucun cours cette semaine.';
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
  if (evt.location) meta.push(`<span>\uD83D\uDCCD ${escapeHtml(evt.location)}</span>`);
  if (evt.teacher) meta.push(`<span>\uD83D\uDC64 ${escapeHtml(evt.teacher)}</span>`);
  meta.push(`<span>\u23F1 ${durationLabel(minutes)}</span>`);

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
      ${ongoing.location ? `\u00B7 ${escapeHtml(ongoing.location)}` : ''}
      <span class="when">fin dans ${durationLabel(left)}</span>`;
    return banner;
  }

  if (upcoming && sameDay(upcoming._start, startOfDay(now))) {
    const inMinutes = Math.round((upcoming._start - now) / 60000);
    banner.innerHTML = `Prochain cours : <strong>${escapeHtml(upcoming.title)}</strong>
      ${upcoming.location ? `\u00B7 ${escapeHtml(upcoming.location)}` : ''}
      <span class="when">dans ${durationLabel(inMinutes)}</span>`;
    return banner;
  }

  return null;
}

function renderStatus() {
  if (!state.meta) { el.status.textContent = '\u2014'; return; }

  const parts = [];
  if (state.meta.fetchedAt) {
    const when = new Date(state.meta.fetchedAt);
    if (!Number.isNaN(when.getTime())) {
      parts.push(`maj ${when.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}`);
    }
  }
  parts.push(`${state.events.length} cours`);

  el.status.textContent = parts.join(' \u00B7 ');
  el.status.className = state.meta.stale ? 'warn' : '';
  if (state.meta.stale) {
    el.status.textContent += ' \u00B7 donn\u00E9es en cache';
  }
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

// Le bouton refresh ouvre la modal de synchronisation
el.refresh.addEventListener('click', () => openSyncModal({ force: true }));

el.viewToggle.addEventListener('click', () => {
  state.view = state.view === 'week' ? 'day' : 'week';
  render();
});

el.prevBtn.addEventListener('click', () => step(-1));
el.nextBtn.addEventListener('click', () => step(1));

document.addEventListener('keydown', (event) => {
  // Ne pas changer de jour pendant que l'utilisateur deplace le curseur dans
  // un champ, ni pendant que la modal de synchronisation est ouverte.
  const target = event.target;
  if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA'
      || target.isContentEditable)) return;
  if (!sync.modal.hidden) return;

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
  if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.6) {
    step(dx < 0 ? 1 : -1);
  }
}, { passive: true });

setInterval(() => {
  if (!state.loading) render();
}, 60000);

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') return;
  if (Date.now() - lastLoadAt < VISIBILITY_RELOAD_MS) return;
  load();
});

/* ------------------------------------------------------------ sync robot */

const POLL_MS = 2000;

const sync = {
  modal: document.getElementById('sync-modal'),
  startBtn: document.getElementById('sync-start-btn'),
  closeBtn: document.getElementById('sync-close-btn'),
  status: document.getElementById('sync-status'),
  a2f: document.getElementById('sync-a2f'),
  email: document.getElementById('sync-email'),
  password: document.getElementById('sync-password'),
  shot: document.getElementById('sync-screenshot'),
};

// Seul l'email est memorise : un mot de passe dans localStorage serait lisible
// par n'importe quel script de la page.
sync.email.value = getUserEmail();

let syncDismissed = false;   // l'utilisateur a ferme la modal, on la laisse fermee
let syncTimer = null;
let syncId = null;

function openSyncModal({ force = false } = {}) {
  if (syncDismissed && !force) return;
  if (force) syncDismissed = false;
  sync.modal.hidden = false;
}

function closeSyncModal() {
  sync.modal.hidden = true;
  syncDismissed = true;
}

function stopPolling() {
  clearInterval(syncTimer);
  syncTimer = null;
}

function showScreenshot(dataUri) {
  sync.shot.hidden = !dataUri;
  if (dataUri) sync.shot.src = dataUri;
}

function showA2fCode(code) {
  sync.a2f.hidden = !code;
  if (code) sync.a2f.textContent = code;
}

function finishSync(message, { reload = false } = {}) {
  stopPolling();
  showA2fCode(null);
  sync.status.textContent = message;
  sync.startBtn.disabled = false;
  if (reload) setTimeout(() => { sync.modal.hidden = true; location.reload(); }, 2000);
}

function applySyncState(st) {
  showScreenshot(st.screenshot);

  switch (st.status) {
    case 'waiting_2fa':
      sync.status.textContent = st.detail || 'Tapez ce num\u00E9ro sur votre t\u00E9l\u00E9phone :';
      showA2fCode(st.code);
      break;
    case 'downloading':
      sync.status.textContent = st.detail || 'T\u00E9l\u00E9chargement en cours\u2026';
      showA2fCode(null);
      break;
    case 'success':
      showScreenshot(null);
      finishSync('Termin\u00E9 ! Le planning est \u00E0 jour.', { reload: true });
      break;
    case 'error':
      finishSync('Erreur : ' + (st.error_msg || 'inconnue'));
      break;
    case 'unknown':
      finishSync('Synchronisation introuvable, relance-la.');
      break;
    default:  // starting, logging_in
      sync.status.textContent = st.detail || 'Connexion \u00E0 Microsoft en cours\u2026';
  }
}

function pollSync() {
  if (!syncId) return;
  fetch(`/api/sync/status?id=${encodeURIComponent(syncId)}`)
    .then((res) => res.json())
    .then(applySyncState)
    .catch(() => { sync.status.textContent = 'Erreur de connexion au serveur\u2026'; });
}

async function startSync() {
  const email = sync.email.value.trim();
  const password = sync.password.value;
  if (!email || !password) {
    sync.status.textContent = 'Email et mot de passe requis.';
    return;
  }

  localStorage.setItem('auriga_email', email);
  sync.status.textContent = 'D\u00E9marrage du robot\u2026';
  sync.startBtn.disabled = true;
  showA2fCode(null);
  showScreenshot(null);
  stopPolling();

  try {
    const res = await fetch('/api/sync/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    const payload = await res.json();
    if (!payload.success) throw new Error(payload.error || 'demarrage impossible');

    syncId = payload.syncId;
    sync.password.value = '';  // ne pas le laisser trainer dans le DOM
    syncTimer = setInterval(pollSync, POLL_MS);
  } catch (err) {
    finishSync('Erreur : ' + err.message);
  }
}

sync.startBtn.addEventListener('click', startSync);
sync.closeBtn.addEventListener('click', () => { stopPolling(); closeSyncModal(); });

/* ---------------------------------------------------------------- demarrage */

const cached = readCache();
if (cached) { hydrate(cached); render(); }
load();

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}

/* ------------------------------------------------------- installation PWA */

const install = {
  bar: document.getElementById('install-bar'),
  text: document.getElementById('install-text'),
  btn: document.getElementById('install-btn'),
  dismiss: document.getElementById('install-dismiss'),
};

const INSTALL_DISMISSED_KEY = 'auriga_install_dismissed';

function isStandalone() {
  return window.matchMedia('(display-mode: standalone)').matches
    || window.navigator.standalone === true;
}

function isIos() {
  return /iphone|ipad|ipod/i.test(navigator.userAgent)
    || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
}

function hideInstallBar() {
  install.bar.hidden = true;
}

function showInstallBar(message, withButton) {
  if (isStandalone()) return;                       // deja installee
  if (localStorage.getItem(INSTALL_DISMISSED_KEY)) return;
  install.text.textContent = message;
  install.btn.hidden = !withButton;
  install.bar.hidden = false;
}

// Android / Chrome : l'evenement fournit la vraie invite d'installation. On le
// met de cote pour l'offrir dans notre propre barre — mais cette barre vit en
// dehors de #content, sinon le prochain render() l'effacerait.
let deferredPrompt = null;

window.addEventListener('beforeinstallprompt', (event) => {
  event.preventDefault();
  deferredPrompt = event;
  showInstallBar('Installe l\'appli pour l\'ouvrir en plein écran, sans la barre du navigateur.', true);
});

install.btn.addEventListener('click', async () => {
  if (!deferredPrompt) return;
  install.btn.disabled = true;
  deferredPrompt.prompt();
  const { outcome } = await deferredPrompt.userChoice;
  // Une invite ne sert qu'une fois : Chrome en renverra une neuve si besoin.
  deferredPrompt = null;
  install.btn.disabled = false;
  if (outcome === 'accepted') hideInstallBar();
});

install.dismiss.addEventListener('click', () => {
  localStorage.setItem(INSTALL_DISMISSED_KEY, '1');
  hideInstallBar();
});

window.addEventListener('appinstalled', () => {
  deferredPrompt = null;
  hideInstallBar();
});

// iOS ne declenche jamais beforeinstallprompt : la seule voie est le menu
// Partager, donc on explique ou cliquer.
if (isIos() && !isStandalone()) {
  showInstallBar('Pour installer : appuie sur Partager, puis « Sur l\'écran d\'accueil ».', false);
}
