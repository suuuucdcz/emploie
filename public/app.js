/* Emploi du temps Auriga - interface. */

const CACHE_KEY = 'auriga-edt-cache-v1';
const KIND_LABEL = {
  CM: 'CM', TD: 'TD', TP: 'TP', EXAM: 'EXAMEN', PROJET: 'PROJET', AUTRE: 'COURS',
};

const TICK_MS = 30000;        // cadence du direct
const GAP_MIN_MINUTES = 20;   // en deca, un trou entre deux cours n'est pas une pause

const state = {
  events: [],
  selected: startOfDay(new Date()),
  view: 'day',
  meta: null,
  loading: false,
  liveSignature: '',
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
  state.liveSignature = liveSignature(new Date());
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
  const now = new Date();
  const events = eventsOn(state.selected);
  const isToday = sameDay(state.selected, startOfDay(now));

  if (isToday) {
    const banner = nextCourseBanner(now);
    if (banner) el.content.appendChild(banner);
  }

  if (!events.length) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.innerHTML = `<span class="big">\uD83C\uDF89</span>Aucun cours le ${fmtDayLong(state.selected)}.`;
    el.content.appendChild(empty);
    return;
  }

  // Le repere \u00AB maintenant \u00BB ne sert que si on est entre deux cours : pendant
  // un cours, la barre de progression de la carte dit deja ou on en est.
  let markerPlaced = !isToday || Boolean(ongoingEvent(now));

  events.forEach((evt, index) => {
    if (!markerPlaced && now < evt._start) {
      el.content.appendChild(nowMarker(now));
      markerPlaced = true;
    }
    el.content.appendChild(card(evt, now));

    const next = events[index + 1];
    if (next) {
      const gap = Math.round((next._start - evt._end) / 60000);
      if (gap >= GAP_MIN_MINUTES) el.content.appendChild(gapRow(gap));
    }
  });
}

function nowMarker(now) {
  const node = document.createElement('div');
  node.className = 'now-marker';
  node.innerHTML = `<span class="now-marker-time" data-live="now-time">${fmtTime(now)}</span>`;
  return node;
}

function gapRow(minutes) {
  const node = document.createElement('div');
  node.className = 'gap';
  node.innerHTML = `<span>${durationLabel(minutes)} de libre</span>`;
  return node;
}

/* --------------------------------------------------------- vue semaine */

// Grille horaire : les jours en colonnes, les heures en lignes, les cours
// places a leur position reelle. On voit la forme de la semaine \u2014 les trous,
// les journees chargees \u2014 sans rien lire.

function hourOf(date) {
  return date.getHours() + date.getMinutes() / 60;
}

function endHourOf(evt) {
  const end = hourOf(evt._end);
  // Un cours qui deborde sur le lendemain se termine visuellement a minuit.
  return end <= hourOf(evt._start) ? 24 : end;
}

function renderWeek() {
  const monday = mondayOf(state.selected);
  const now = new Date();
  const today = startOfDay(now);

  const columns = [];
  for (let i = 0; i < 7; i += 1) {
    const day = addDays(monday, i);
    const events = eventsOn(day);
    // Le week-end n'occupe une colonne que s'il a cours.
    if (i >= 5 && !events.length) continue;
    columns.push({ day, events });
  }

  const all = columns.reduce((acc, col) => acc.concat(col.events), []);
  if (!all.length) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.innerHTML = '<span class="big">\uD83C\uDF89</span>Aucun cours cette semaine.';
    el.content.appendChild(empty);
    return;
  }

  // Amplitude reelle de la semaine : inutile d'afficher la nuit.
  const first = Math.max(0, Math.floor(Math.min(...all.map((e) => hourOf(e._start)))));
  const last = Math.min(24, Math.ceil(Math.max(...all.map(endHourOf))));
  const span = Math.max(1, last - first);

  const grid = document.createElement('div');
  grid.className = 'grid';
  grid.style.setProperty('--cols', String(columns.length));
  grid.style.setProperty('--span', String(span));

  const corner = document.createElement('div');
  corner.className = 'grid-corner';
  grid.appendChild(corner);

  const head = document.createElement('div');
  head.className = 'grid-days';
  columns.forEach(({ day }) => {
    const cell = document.createElement('button');
    cell.type = 'button';
    cell.className = `grid-day${sameDay(day, today) ? ' is-today' : ''}`;
    cell.innerHTML = `
      <span class="dow">${day.toLocaleDateString('fr-FR', { weekday: 'short' }).slice(0, 3)}</span>
      <span class="dom">${day.getDate()}</span>`;
    cell.setAttribute('aria-label', fmtDayLong(day));
    cell.addEventListener('click', () => openDay(day));
    head.appendChild(cell);
  });
  grid.appendChild(head);

  const hours = document.createElement('div');
  hours.className = 'grid-hours';
  for (let h = first; h < last; h += 1) {
    const label = document.createElement('span');
    label.className = 'grid-hour';
    label.textContent = String(h).padStart(2, '0');
    hours.appendChild(label);
  }
  grid.appendChild(hours);

  const canvas = document.createElement('div');
  canvas.className = 'grid-canvas';
  columns.forEach(({ day, events }) => {
    const col = document.createElement('div');
    col.className = `grid-col${sameDay(day, today) ? ' is-today' : ''}`;
    events.forEach((evt) => col.appendChild(slot(evt, first, now)));
    canvas.appendChild(col);
  });

  // Le trait de l'heure courante, s'il tombe dans la plage affichee.
  const nowHour = hourOf(now);
  if (columns.some(({ day }) => sameDay(day, today)) && nowHour >= first && nowHour <= last) {
    const line = document.createElement('div');
    line.className = 'grid-now';
    line.dataset.live = 'grid-now';
    line.dataset.first = String(first);
    line.dataset.last = String(last);
    line.style.top = `calc(${(nowHour - first).toFixed(3)} * var(--hour))`;
    canvas.appendChild(line);
  }

  grid.appendChild(canvas);
  el.content.appendChild(grid);
}

function slot(evt, first, now) {
  const ongoing = evt._start <= now && now < evt._end;
  const past = evt._end <= now;
  const top = hourOf(evt._start) - first;
  const height = Math.max(endHourOf(evt) - hourOf(evt._start), 0.42);

  const node = document.createElement('button');
  node.type = 'button';
  node.className = `slot${ongoing ? ' now' : ''}${past ? ' past' : ''}`;
  node.style.setProperty('--kind', `var(--${evt.kind.toLowerCase()})`);
  node.style.top = `calc(${top.toFixed(3)} * var(--hour))`;
  node.style.height = `calc(${height.toFixed(3)} * var(--hour) - 3px)`;

  // Le contenu deborde et se fait rogner : un creneau court montre son type,
  // un creneau long montre aussi le titre et la salle.
  node.innerHTML = `
    <span class="slot-kind">${KIND_LABEL[evt.kind] || 'COURS'}</span>
    <span class="slot-title">${escapeHtml(evt.title || evt.rawTitle || 'Cours')}</span>
    ${evt.location ? `<span class="slot-room">${escapeHtml(evt.location)}</span>` : ''}`;
  node.setAttribute('aria-label',
    `${fmtTime(evt._start)} ${evt.title || 'Cours'}${evt.location ? ', ' + evt.location : ''}`);
  node.addEventListener('click', () => openDay(evt._start));
  return node;
}

function openDay(date) {
  state.selected = startOfDay(date);
  state.view = 'day';
  render();
  window.scrollTo({ top: 0 });
}

function card(evt, now = new Date()) {
  const ongoing = evt._start <= now && now < evt._end;
  // On ne grise que la journee en cours : sur un jour passe, tout serait
  // efface et la page aurait l'air cassee.
  const past = evt._end <= now && sameDay(evt._start, startOfDay(now));
  const minutes = Math.round((evt._end - evt._start) / 60000);

  const node = document.createElement('article');
  node.className = `card${ongoing ? ' now' : ''}${past ? ' past' : ''}`;
  node.style.setProperty('--kind', `var(--${evt.kind.toLowerCase()})`);

  const meta = [];
  if (evt.location) meta.push(`<span>\uD83D\uDCCD ${escapeHtml(evt.location)}</span>`);
  if (evt.teacher) meta.push(`<span>\uD83D\uDC64 ${escapeHtml(evt.teacher)}</span>`);
  // Pendant le cours, le temps restant est plus utile que la duree totale.
  meta.push(ongoing
    ? `<span class="meta-live">\u23F3 ${remainingSpan(evt)}</span>`
    : `<span>\u23F1 ${durationLabel(minutes)}</span>`);

  node.innerHTML = `
    <div class="card-time">
      <span class="start">${evt.allDay ? 'Jour' : fmtTime(evt._start)}</span>
      <span class="end">${evt.allDay ? 'entier' : fmtTime(evt._end)}</span>
    </div>
    <div class="card-body">
      <div class="card-badges">
        <span class="badge">${KIND_LABEL[evt.kind] || 'COURS'}</span>
        ${ongoing ? '<span class="badge now-badge">EN COURS</span>' : ''}
      </div>
      <h2 class="card-title">${escapeHtml(evt.title || evt.rawTitle || 'Cours')}</h2>
      <div class="card-meta">${meta.join('')}</div>
    </div>
    ${ongoing ? progressBar(evt, now) : ''}`;
  return node;
}

function nextCourseBanner(now) {
  const ongoing = ongoingEvent(now);
  const upcoming = nextEvent(now);

  const banner = document.createElement('div');
  banner.className = 'next-banner';

  if (ongoing) {
    banner.classList.add('is-ongoing');
    banner.innerHTML = `
      <span class="next-banner-label">En cours</span>
      <strong>${escapeHtml(ongoing.title)}</strong>
      ${ongoing.location ? `<span class="next-banner-place">\uD83D\uDCCD ${escapeHtml(ongoing.location)}</span>` : ''}
      ${progressBar(ongoing, now)}
      <span class="when">${remainingSpan(ongoing)}</span>`;
    return banner;
  }

  if (upcoming && sameDay(upcoming._start, startOfDay(now))) {
    banner.innerHTML = `
      <span class="next-banner-label">Prochain cours</span>
      <strong>${escapeHtml(upcoming.title)}</strong>
      ${upcoming.location ? `<span class="next-banner-place">\uD83D\uDCCD ${escapeHtml(upcoming.location)}</span>` : ''}
      <span class="when" data-live="countdown" data-start="${upcoming._start.getTime()}">${countdownLabel(upcoming._start, now)}</span>`;
    return banner;
  }

  return null;
}

/* ------------------------------------------------------------- le direct */

function ongoingEvent(now) {
  return state.events.find((evt) => evt._start <= now && now < evt._end) || null;
}

function nextEvent(now) {
  return state.events.find((evt) => evt._start > now) || null;
}

function remainingLabel(end, now) {
  const minutes = Math.round((end - now) / 60000);
  return minutes <= 0 ? 'termine' : `il reste ${durationLabel(minutes)}`;
}

function countdownLabel(start, now) {
  const minutes = Math.round((start - now) / 60000);
  return minutes <= 0 ? '\u00E7a commence' : `dans ${durationLabel(minutes)}`;
}

function remainingSpan(evt) {
  return `<span data-live="remaining" data-end="${evt._end.getTime()}">${
    remainingLabel(evt._end, new Date())}</span>`;
}

function progressBar(evt, now) {
  const pct = ratio(evt._start.getTime(), evt._end.getTime(), now) * 100;
  return `<div class="progress" data-live="progress"
       data-start="${evt._start.getTime()}" data-end="${evt._end.getTime()}"
       role="progressbar" aria-valuemin="0" aria-valuemax="100"
       aria-valuenow="${Math.round(pct)}" aria-label="Avancement du cours"
     ><span class="progress-fill" style="width:${pct.toFixed(1)}%"></span></div>`;
}

function ratio(start, end, now) {
  if (end <= start) return 0;
  return Math.min(1, Math.max(0, (now - start) / (end - start)));
}

// Empreinte de l'etat \u00AB direct \u00BB : tant qu'elle ne change pas, la structure de
// la page est bonne et il suffit d'animer les elements vivants.
function liveSignature(now) {
  const ongoing = ongoingEvent(now);
  const upcoming = nextEvent(now);
  return `${ongoing ? ongoing.uid : '-'}|${upcoming ? upcoming.uid : '-'}`;
}

function updateLive(now) {
  document.querySelectorAll('[data-live="progress"]').forEach((node) => {
    const pct = ratio(Number(node.dataset.start), Number(node.dataset.end), now) * 100;
    node.firstElementChild.style.width = `${pct.toFixed(1)}%`;
    node.setAttribute('aria-valuenow', String(Math.round(pct)));
  });
  document.querySelectorAll('[data-live="remaining"]').forEach((node) => {
    node.textContent = remainingLabel(Number(node.dataset.end), now);
  });
  document.querySelectorAll('[data-live="countdown"]').forEach((node) => {
    node.textContent = countdownLabel(Number(node.dataset.start), now);
  });
  const marker = document.querySelector('[data-live="now-time"]');
  if (marker) marker.textContent = fmtTime(now);

  const line = document.querySelector('[data-live="grid-now"]');
  if (line) {
    // Passe minuit, hourOf() repart de 0 : sans ce garde-fou le trait
    // remonterait au-dessus de la grille.
    const first = Number(line.dataset.first);
    const offset = hourOf(now) - first;
    const inRange = offset >= 0 && offset <= Number(line.dataset.last) - first;
    line.hidden = !inRange;
    if (inRange) line.style.top = `calc(${offset.toFixed(3)} * var(--hour))`;
  }
}

function tick() {
  const now = new Date();
  // Un cours vient de commencer ou de finir : la page change de forme.
  if (liveSignature(now) !== state.liveSignature) {
    render();
    return;
  }
  updateLive(now);
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

// Rythme du direct : on redessine seulement si un cours a change d'etat,
// sinon on se contente de faire avancer la barre et les compteurs.
setInterval(() => {
  if (!state.loading) tick();
}, TICK_MS);

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
  // Relire l'agenda suffit : recharger toute la page reinitialiserait le jour
  // affiche et ferait reclignoter l'interface.
  if (reload) {
    setTimeout(() => {
      sync.modal.hidden = true;
      load({ force: true });
    }, 1500);
  }
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
