/* ------------------------------------------------------------------------
   Sonde de structure - emploi du temps Auriga.

   A COLLER dans la console de TON navigateur (F12 > Console), sur
   auriga.ipsa.fr, page "Mon planning".

   Ce script n'envoie RIEN a personne. Il se contente d'observer les reponses
   de l'API que l'appli recupere deja avec ta session, et d'en afficher la
   STRUCTURE (noms des champs), pas le contenu brut.

   Mode d'emploi :
     1. colle tout ce bloc, Entree ;
     2. change de semaine (fleche >) ou clique "Aujourd'hui" ;
     3. tape  __report()  puis Entree ;
     4. copie-moi ce qui s'affiche.
   ------------------------------------------------------------------------ */

(() => {
  if (window.__aurigaProbe) { console.log('Sonde deja active. Change de semaine puis tape __report()'); return; }
  window.__aurigaProbe = true;
  window.__cap = [];

  const OrigOpen = XMLHttpRequest.prototype.open;
  const OrigSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function (method, url) {
    this.__probe = { method, url };
    return OrigOpen.apply(this, arguments);
  };

  XMLHttpRequest.prototype.send = function () {
    this.addEventListener('load', () => {
      try {
        const info = this.__probe || {};
        if (!info.url || !/\/api\//.test(info.url)) return;
        let body = null;
        try { body = JSON.parse(this.responseText); } catch (_) { /* pas du JSON */ }
        window.__cap.push({
          method: info.method,
          url: info.url,
          bytes: (this.responseText || '').length,
          body,
        });
        if (window.__cap.length > 60) window.__cap.shift();
      } catch (_) { /* on n'interrompt jamais l'appli */ }
    });
    return OrigSend.apply(this, arguments);
  };

  // -- analyse -----------------------------------------------------------

  const DATE_HINT = /(date|start|end|debut|fin|hour|heure|time|jour|day|slot|creneau|from|to)/i;

  function firstArray(node, depth = 0) {
    if (depth > 4 || node == null) return null;
    if (Array.isArray(node)) {
      return node.length && typeof node[0] === 'object' ? node : null;
    }
    if (typeof node === 'object') {
      for (const key of Object.keys(node)) {
        const found = firstArray(node[key], depth + 1);
        if (found) return found;
      }
    }
    return null;
  }

  function shorten(value) {
    if (typeof value === 'string') return value.length > 70 ? value.slice(0, 70) + '…' : value;
    if (value && typeof value === 'object') return Array.isArray(value) ? `[${value.length}]` : '{…}';
    return value;
  }

  window.__report = () => {
    const apis = window.__cap.filter((c) => c.body != null);
    if (!apis.length) {
      console.log('%cAucune reponse /api captee. Change de semaine, puis retape __report().',
        'color:#c0392b');
      return;
    }

    console.log('%c=== ENDPOINTS /api CAPTES ===', 'font-weight:bold');
    apis.forEach((c) => {
      const path = c.url.replace(/^https?:\/\/[^/]+/, '');
      console.log(`${c.method}  ${path}   (${c.bytes} octets)`);
    });

    // Cherche l'endpoint qui ressemble a une liste d'evenements.
    let best = null;
    for (const c of apis) {
      const arr = firstArray(c.body);
      if (!arr) continue;
      const keys = new Set();
      arr.slice(0, 5).forEach((o) => Object.keys(o).forEach((k) => keys.add(k)));
      const dateKeys = [...keys].filter((k) => DATE_HINT.test(k));
      const score = dateKeys.length * 10 + arr.length;
      if (!best || score > best.score) best = { c, arr, keys: [...keys], dateKeys, score };
    }

    if (!best || !best.dateKeys.length) {
      console.log('%cPas de liste avec des champs de date reperee. ' +
        'Colle-moi quand meme la liste ci-dessus + clique une requete "planning" ' +
        'dans l\'onglet Network et copie sa reponse.', 'color:#c0392b');
      return;
    }

    const path = best.c.url.replace(/^https?:\/\/[^/]+/, '');
    console.log('\n%c=== CANDIDAT EMPLOI DU TEMPS ===', 'font-weight:bold;color:#1f5fd0');
    console.log('endpoint :', best.c.method, path);
    console.log('nombre d\'elements :', best.arr.length);
    console.log('champs :', best.keys.join(', '));
    console.log('champs de date/heure :', best.dateKeys.join(', '));
    console.log('\n%c-- 1 element (valeurs longues tronquees, verifie qu\'il n\'y a rien de perso avant de copier) --',
      'color:#66707f');
    const sample = {};
    Object.entries(best.arr[0]).forEach(([k, v]) => { sample[k] = shorten(v); });
    console.log(JSON.stringify(sample, null, 2));

    console.log('\n%cCopie-moi ce bloc CANDIDAT. Pour arreter la sonde : recharge la page.',
      'color:#0d9488');
    return sample;
  };

  console.log('%cSonde active.', 'color:#0d9488;font-weight:bold',
    'Change de semaine (fleche >) puis tape  __report()');
})();
