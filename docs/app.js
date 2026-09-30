'use strict';

/* ───────── outils ───────── */
const $ = (s) => document.querySelector(s);

function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'style') Object.assign(e.style, v);
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? '' : v);
    }
  }
  for (const k of kids.flat(Infinity)) {
    if (k == null || k === false) continue;
    e.append(k instanceof Node ? k : String(k));
  }
  return e;
}

const nf = (v, d = 0) => Number(v).toLocaleString('fr-FR', { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (v, d = 1) => nf(v * 100, d) + ' %';
const size = (b) => (b >= 1048576 ? nf(b / 1048576, 1) + ' Mo' : nf(b / 1024, 0) + ' Ko');
const clamp01 = (v) => Math.max(0, Math.min(1, v));
const fmtDate = (s) => {
  const d = new Date(s);
  return isNaN(d) ? s : d.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric' });
};

/* ───────── état ───────── */
let DATA = { dessins: [], auteur: '', titre_site: 'Portfolio' };
const PREFS = loadPrefs();
const state = { q: '', cat: null, sort: 'date-desc', scroll: 0, lastId: null,
  layersOpen: PREFS.open ?? !window.matchMedia('(max-width: 900px)').matches, layersPos: PREFS.pos || null };
let cleanup = null; // nettoyage de la vue courante (listeners)

const V = {
  view: $('#view'), status: $('#status'), crumbs: $('#crumbs'), tab: $('#tabTitle'),
  sidebar: $('#sidebar'), count: $('#count'), filterLabel: $('#filterLabel'),
  search: $('#search'), sort: $('#sort'), catSelect: $('#catSelect'),
  back: $('#btnBack'), up: $('#btnUp'), prev: $('#btnPrev'), next: $('#btnNext'),
};

/* ───────── protection légère ───────── */
document.addEventListener('contextmenu', (e) => { if (e.target.closest('.protect')) e.preventDefault(); });
document.addEventListener('dragstart', (e) => { if (e.target.closest('.protect')) e.preventDefault(); });

/* ───────── catégories = tags (+ « categorie » si renseignée) ───────── */
function catsOf(d) {
  const set = new Set((d.tags || []).map((t) => String(t).trim()).filter(Boolean));
  if (d.categorie) set.add(String(d.categorie).trim());
  return [...set];
}

/* ───────── liste filtrée / triée ───────── */
function list() {
  const q = state.q.trim().toLowerCase();
  let items = DATA.dessins.filter((d) =>
    (!state.cat || catsOf(d).includes(state.cat)) &&
    (!q || [d.titre, d.description, d.categorie, ...(d.tags || [])].join(' ').toLowerCase().includes(q)));
  const by = {
    'date-desc': (a, b) => b.date.localeCompare(a.date),
    'date-asc': (a, b) => a.date.localeCompare(b.date),
    nom: (a, b) => a.titre.localeCompare(b.titre, 'fr'),
    couleur: (a, b) => b.stats.couleur.colorfulness - a.stats.couleur.colorfulness,
    sombre: (a, b) => a.stats.lumiere.moyenne - b.stats.lumiere.moyenne,
  }[state.sort];
  return items.sort(by);
}

/* ───────── chrome commun ───────── */
function setMode(mode) {
  document.body.dataset.mode = mode;
  if (cleanup) { cleanup(); cleanup = null; }
}

function setCrumbs(parts) {
  const nodes = [el('span', { class: 'chev' }, '›'), el('span', null, DATA.titre_site)];
  parts.forEach((p, i) => {
    nodes.push(el('span', { class: 'chev' }, '›'));
    const last = i === parts.length - 1;
    nodes.push(last ? el('span', { class: 'last' }, p.label) : el('a', { href: p.href }, p.label));
  });
  V.crumbs.replaceChildren(...nodes);
}

function setStatus(...parts) {
  V.status.replaceChildren(...parts.map((p) => el('span', null, p)));
}

/* ───────── barre latérale ───────── */
function renderCatSelect(cats) {
  const opts = [el('option', { value: '' }, `Toutes (${DATA.dessins.length})`)]
    .concat(Object.keys(cats).sort((a, b) => a.localeCompare(b, 'fr'))
      .map((c) => el('option', { value: c }, `${c} (${cats[c]})`)));
  V.catSelect.replaceChildren(...opts);
  V.catSelect.value = state.cat || '';
}

function renderSidebar() {
  const cats = {};
  DATA.dessins.forEach((d) => catsOf(d).forEach((c) => { cats[c] = (cats[c] || 0) + 1; }));
  renderCatSelect(cats);
  const btn = (label, cat, n) => el('button', {
    class: document.body.dataset.mode === 'bureau' && state.cat === cat ? 'on' : null,
    onclick: () => { state.cat = cat; state.scroll = 0; location.hash === '#/' || location.hash === '' ? route() : (location.hash = '#/'); },
  }, el('span', null, label), el('span', { class: 'n' }, n));
  V.sidebar.replaceChildren(...[
    btn('Tous les dessins', null, DATA.dessins.length),
    Object.keys(cats).length ? el('h2', null, 'Catégories') : null,
    Object.keys(cats).sort((a, b) => a.localeCompare(b, 'fr')).map((c) => btn(c, c, cats[c])),
    el('div', { class: 'spacer' }),
    el('a', { href: '#/droits', class: document.body.dataset.mode === 'droits' ? 'on' : null }, 'Droits & mentions'),
  ].flat().filter(Boolean));
}

/* ───────── vue bureau ───────── */
function folderEl(d) {
  return el('a', { class: 'folder' + (d.id === state.lastId ? ' sel' : ''), href: `#/d/${d.id}`, title: d.titre },
    el('div', { class: 'ficon protect', 'aria-hidden': 'true' },
      el('div', { class: 'ftab' }),
      el('div', { class: 'fback' }),
      el('div', { class: 'fpaper', style: { backgroundImage: `url("${d.thumb}")` } }),
      el('div', { class: 'ffront' })),
    el('span', { class: 'fname' }, d.titre));
}

function showBureau() {
  setMode('bureau');
  document.title = DATA.titre_site;
  V.tab.textContent = state.cat || 'Dessins';
  setCrumbs(state.cat ? [{ label: 'Dessins', href: '#/' }, { label: state.cat }] : [{ label: 'Dessins' }]);
  V.back.disabled = V.up.disabled = !state.cat;
  V.filterLabel.textContent = 'Filtre : ' + (state.cat || 'tous');
  V.count.textContent = `${DATA.dessins.length} dessins`;
  renderSidebar();

  const items = list();
  const grid = el('div', { class: 'grid' }, items.map(folderEl));
  if (!items.length) grid.append(el('p', { class: 'empty' }, DATA.dessins.length ? 'Aucun dessin ne correspond.' : 'Aucun dessin pour le moment.'));
  V.view.replaceChildren(grid);
  V.view.scrollTop = state.scroll;
  setStatus(`${items.length} élément${items.length > 1 ? 's' : ''}`, state.q ? `recherche : « ${state.q} »` : '');

  const onScroll = () => { state.scroll = V.view.scrollTop; };
  V.view.addEventListener('scroll', onScroll);
  cleanup = () => V.view.removeEventListener('scroll', onScroll);
}

/* ───────── vue dossier ───────── */
function section(letter, title) {
  return el('div', { class: 'sec' }, el('b', null, letter), el('span', null, title), el('i'));
}
function kv(rows) {
  return el('div', { class: 'kv' }, rows.filter(Boolean).map(([k, v, wide]) =>
    el('div', { class: wide ? 'wide' : null }, el('span', { class: 'k' }, k), el('span', { class: 'v', title: typeof v === 'string' ? v : null }, v))));
}
function gauge(label, value, frac, note, ticks) {
  return el('div', { class: 'gauge' },
    el('div', { class: 'top' }, el('span', null, label), el('span', null, value)),
    el('div', { class: 'track' }, el('div', { class: 'fill', style: { width: `${clamp01(frac) * 100}%` } })),
    ticks ? el('div', { class: 'ticks' }, ticks.map((t) => el('span', null, t))) : null,
    note ? el('span', { class: 'note' }, note) : null);
}
function histo(values, color, small) {
  return el('div', { class: 'histo' + (small ? ' small' : '') },
    values.map((v) => el('div', { style: { height: `${v}%`, background: color || null } })));
}
function colorVal(hex) {
  return [el('span', { class: 'chip', style: { background: hex } }), hex];
}

function renderFiche(d, lw) {
  const s = d.stats, F = s.fichier, C = s.couleur, L = s.lumiere, T = s.texture, P = s.composition;
  const ar = `${F.largeur} / ${F.hauteur}`;

  const head = el('div', { class: 'fiche-head' },
    el('div', { class: 'fiche-meta' }, el('span', null, d.fichier), el('span', null, '·'), el('span', null, fmtDate(d.date)),
      d.categorie ? [el('span', null, '·'), el('span', null, d.categorie)] : null),
    el('h1', null, d.titre),
    d.description ? el('p', null, d.description) : null,
    d.tags && d.tags.length ? el('div', { class: 'tags' }, d.tags.map((t) => el('span', null, t))) : null);

  /* A. fichier */
  const A = [
    section('A', 'Fichier & encodage'),
    kv([
      ['Dimensions', `${nf(F.largeur)} × ${nf(F.hauteur)} px`],
      ['Mégapixels', `${nf(F.megapixels, 2)} MP`],
      ['Ratio', `1:${nf(F.ratio, 3)}${F.ratio_nom ? ` (${F.ratio_nom})` : ''}`],
      ['Orientation', F.orientation],
      ['Format', F.format],
      ['Poids', size(F.poids)],
      ['Bits / pixel', `${nf(F.bpp, 2)} bpp`],
      ['Profondeur', `${F.profondeur} bits/canal`],
      ['Canaux', `${F.canaux} (${F.mode})`],
      ['Résolution', F.dpi],
      ['Compression', F.compression, true],
      ['Profil ICC', F.profil, true],
      ['Transparence', pct(F.transparence)],
    ]),
    gauge('Taux de compression', `${size(F.brut)} → ${size(F.poids)} · ${nf(F.taux_compression, 2)}:1`,
      F.poids / F.brut, F.poids_calques
        ? `image aplatie non compressée → fichier complet (dont ${size(F.poids_calques)} de calques, ${pct(F.poids_calques / F.poids, 0)} du fichier)`
        : 'part du poids brut (non compressé) réellement stockée'),
    el('div', { class: 'box' }, el('strong', null, 'SHA-256 · '), F.sha256),
  ];

  /* B. couleur */
  const wheelBars = C.teintes.map((v, i) => el('div', {
    class: 'bar',
    style: {
      height: `${Math.round(20 + v * 0.5)}px`,
      background: `hsl(${i * 15 + 7.5}, 70%, ${v > 10 ? 55 : 35}%)`,
      transform: `rotate(${i * 15 + 7.5}deg)`,
    },
  }));
  const tempPos = C.temperature ? clamp01((C.temperature - 2000) / 8000) : 0.5;
  const B = [
    section('B', 'Couleur'),
    el('span', { class: 'sub', style: { marginTop: '0' } }, `Palette · k-means ${C.palette.length}`),
    el('div', { class: 'palette-band' }, C.palette.map((p) => el('div', { style: { flexGrow: p.pct, background: p.hex }, title: `${p.hex} · ${nf(p.pct, 1)} %` }))),
    el('div', { class: 'swatches' }, C.palette.map((p) => el('div', null,
      el('div', { class: 'sq', style: { background: p.hex } }),
      el('div', { class: 't' }, el('span', null, p.hex), el('span', null, `${nf(p.pct, 1)} %`))))),
    el('div', { class: 'wheel-row' },
      el('div', { class: 'wheel', role: 'img', 'aria-label': 'Roue chromatique' }, wheelBars,
        el('div', { class: 'hub' }, C.teinte_dominante != null ? `${nf(C.teinte_dominante)}°` : '—')),
      el('div', { class: 'wheel-text' },
        el('span', { class: 'k' }, 'Roue chromatique · 24 secteurs de 15°'),
        C.part_chromatique < 0.02
          ? el('span', null, 'Dessin quasi achromatique : moins de 2 % de pixels colorés.')
          : el('span', null, `${pct(C.part_autour_dominante, 0)} des pixels colorés à ±30° de ${nf(C.teinte_dominante)}° (${C.teinte_nom}). ${pct(C.part_chromatique, 0)} de l'image est colorée.`))),
    el('div', { style: { marginTop: '18px' } }, kv([
      ['Couleurs uniques', nf(C.couleurs_uniques)],
      ['Teinte dom.', C.teinte_dominante != null ? `${nf(C.teinte_dominante)}° ${C.teinte_nom}` : '—'],
      ['Saturation moy.', pct(C.saturation_moy, 0)],
      ['Saturation σ', pct(C.saturation_std, 0)],
      ['Couleur moyenne', colorVal(C.moyenne)],
      ['Couleur médiane', colorVal(C.mediane)],
    ])),
    gauge('Colorfulness (Hasler-Süsstrunk)', `${nf(C.colorfulness, 1)} · ${C.colorfulness_label}`, C.colorfulness / 110, null,
      ['0', '15', '33', '45', '59', '82', '109+']),
    el('div', { class: 'gauge' },
      el('div', { class: 'top' }, el('span', null, 'Température estimée (McCamy)'),
        el('span', null, C.temperature ? `${nf(C.temperature)} K · ${C.temperature_label}` : C.temperature_label)),
      el('div', { class: 'track temp' }, el('div', { class: 'marker', style: { left: `calc(${tempPos * 100}% - 1px)` } })),
      el('div', { class: 'ticks' }, el('span', null, '2000 K'), el('span', null, '6000 K'), el('span', null, '10000 K'))),
    el('div', { class: 'harmony' },
      el('div', { class: 'groups' }, C.harmonie.groupes.map((g) => el('div', { class: 'g' }, g.map((hx) => el('div', { style: { background: hx } }))))),
      el('div', { class: 't' }, el('span', null, 'Harmonie détectée'), el('span', null, `${C.harmonie.type} · ${C.harmonie.detail}`))),
  ];

  /* C. lumière */
  const keys = ['low-key', 'mid-key', 'high-key'];
  const Cl = [
    section('C', 'Lumière & tonalité'),
    el('span', { class: 'sub', style: { marginTop: '0' } }, 'Histogramme de luminance'),
    histo(L.histo),
    el('span', { class: 'sub' }, 'Histogrammes R · G · B'),
    [['R', L.rgb.r, '#c9454a'], ['G', L.rgb.g, '#5aa85a'], ['B', L.rgb.b, '#5a7ad8']].map(([n, v, c]) =>
      el('div', { class: 'rgb-row' }, el('span', null, n), histo(v, c, true))),
    el('div', { style: { marginTop: '14px' } }, kv([
      ['Luminosité moy.', pct(L.moyenne, 0)],
      ['Médiane', pct(L.mediane, 0)],
      ['Écart-type', pct(L.ecart_type, 0)],
      ['Contraste RMS', nf(L.contraste_rms, 3)],
      ['Plage P1–P99', `${pct(L.p1, 0)} – ${pct(L.p99, 0)}`],
      ['Plage dynamique', pct(L.plage, 0)],
      ['Noirs bouchés', pct(L.noirs)],
      ['Blancs cramés', pct(L.blancs)],
    ])),
    el('span', { class: 'sub' }, 'Clé tonale'),
    el('div', { class: 'keys' }, keys.map((k) => el('div', { class: L.cle === k ? 'on' : null }, k.toUpperCase()))),
  ];

  /* D. texture */
  const roseBars = T.orientation.map((v, i) => el('div', {
    style: { height: `${Math.round(v * 0.75)}px`, transform: `rotate(${90 - (i * 10 + 5)}deg)`, opacity: (0.35 + v / 160).toFixed(2) },
  }));
  const D = [
    section('D', 'Texture & trait'),
    gauge('Densité de contours (Canny)', pct(T.densite_contours), T.densite_contours / 0.4, 'part des pixels qui sont du trait'),
    gauge('Netteté (variance du laplacien)', nf(T.nettete), Math.log10(T.nettete + 1) / 4, 'mesurée à 1200 px · au-delà de 100 : image nette'),
    gauge('Entropie de Shannon', `${nf(T.entropie, 2)} / 8 bits`, T.entropie / 8, 'quantité d’information par pixel'),
    gauge('Dimension fractale (box-counting)', nf(T.fractale, 3), T.fractale - 1, '1 = ligne simple · 2 = surface saturée de détails'),
    el('div', { class: 'rose-row' },
      el('div', { class: 'rose', role: 'img', 'aria-label': 'Rose des orientations' }, roseBars),
      el('div', { class: 'wheel-text' },
        el('span', { class: 'k' }, 'Orientation des traits'),
        el('span', null, T.orientation_dom != null ? `Dominante ${T.orientation_nom} · ${nf(T.orientation_dom)}°` : 'Aucune orientation'),
        el('span', { class: 'k', style: { fontSize: '10px' } }, `anisotropie ${nf(T.anisotropie, 2)} (1 = aucune direction privilégiée)`))),
  ];

  /* E. composition */
  const sal = el('canvas', { width: P.saillance.l, height: P.saillance.h, 'aria-hidden': 'true' });
  const ctx = sal.getContext('2d');
  const imgData = ctx.createImageData(P.saillance.l, P.saillance.h);
  P.saillance.grille.forEach((v, i) => {
    const t = v / 100;
    imgData.data.set([Math.round(224 * t), Math.round(180 * t), Math.round(90 * t + 20 * (1 - t)), 255], i * 4);
  });
  ctx.putImageData(imgData, 0, 0);
  const qMax = Math.max(...P.quadrants);
  const qNames = ['haut', 'haut', 'bas', 'bas'];
  const heavy = P.quadrants.indexOf(qMax);
  const lines = ['33.33%', '66.66%'].flatMap((p) => [
    el('div', { class: 'line', style: { left: p, top: 0, bottom: 0, width: '1px' } }),
    el('div', { class: 'line', style: { top: p, left: 0, right: 0, height: '1px' } }),
  ]);
  const E = [
    section('E', 'Composition'),
    el('div', { class: 'minis' },
      el('div', { class: 'mini' },
        el('div', { class: 'frame shade protect', style: { aspectRatio: ar, backgroundImage: `url("${d.thumb}")` } }, lines,
          el('div', { class: 'dot', style: { left: `${P.centre[0] * 100}%`, top: `${P.centre[1] * 100}%` } })),
        el('span', null, 'Centre de masse'),
        el('span', null, `x ${pct(P.centre[0], 0)} · y ${pct(P.centre[1], 0)}`)),
      el('div', { class: 'mini' },
        el('div', { class: 'frame', style: { aspectRatio: ar } }, sal),
        el('span', null, 'Saillance'),
        el('span', null, `pic : ${P.pic_zone}`)),
      el('div', { class: 'mini' },
        el('div', { class: 'frame', style: { aspectRatio: ar } },
          el('div', { class: 'quads' }, P.quadrants.map((q) => el('div', {
            style: { background: `rgba(207,207,207,${(0.12 + 0.5 * q / qMax).toFixed(2)})` },
          }, pct(q, 0))))),
        el('span', null, 'Poids / quadrant'),
        el('span', null, `${qNames[heavy]}-${heavy % 2 ? 'droite' : 'gauche'} dominant`))),
    gauge('Symétrie gauche / droite', nf(P.symetrie, 2), P.symetrie, 'corrélation entre l’image et son miroir (1 = parfaitement symétrique)'),
    gauge('Écart aux points forts (tiers)', pct(P.ecart_tiers, 1), 1 - P.ecart_tiers / 0.33, 'distance du centre de masse au point fort le plus proche'),
  ];

  /* F. portfolio */
  const byId = Object.fromEntries(DATA.dessins.map((x) => [x.id, x]));
  const Fp = (d.rangs && d.rangs.length) || (d.proches && d.proches.length) ? [
    section('F', 'Dans le portfolio'),
    d.rangs.length ? el('div', { class: 'ranks' }, d.rangs.map((r) => el('div', null,
      el('b', null, String(r.rang), el('sup', null, r.rang === 1 ? 'er' : 'e')), el('span', null, `${r.libelle} sur ${r.total}`)))) : null,
    d.proches.length ? [
      el('span', { class: 'sub', style: { marginTop: '16px' } }, 'Dessins les plus proches (palette)'),
      el('div', { class: 'similar' }, d.proches.filter((p) => byId[p.id]).map((p) => el('a', { href: `#/d/${p.id}` },
        el('div', { class: 'th protect', style: { backgroundImage: `url("${byId[p.id].thumb}")` } }),
        el('div', { class: 'row' }, el('span', null, p.titre), el('span', null, pct(p.score, 0)))))),
    ] : null,
  ] : null;

  /* G. calques */
  const K = d.calques;
  let G;
  if (K) {
    const peints = K.liste.filter((x) => x.type === 'calque' && x.couverture != null)
      .sort((a, b) => b.couverture - a.couverture).slice(0, 10);
    const maxC = Math.max(...peints.map((x) => x.couverture), 0.0001);
    G = [
      section('G', 'Calques'),
      kv([
        ['Calques', nf(K.nb_calques)],
        ['Groupes', nf(K.nb_groupes)],
        ['Masqués', nf(K.nb_masques)],
        ['Vides', nf(K.nb_vides)],
        ['Écrêtage', nf(K.nb_ecretage)],
        ['Opacité < 100 %', nf(K.nb_opacite_reduite)],
        ['Imbrication max', `${K.profondeur_max} niveau${K.profondeur_max > 1 ? 'x' : ''}`],
        ['Profondeur', `${K.profondeur} bits`],
        ['Source', K.source, true],
        K.fichier && K.fichier !== d.fichier ? ['Fichier calques', K.fichier, true] : null,
        ['Couverture moy.', pct(K.couverture_moy, 1)],
        ['Plus couvrant', K.plus_couvrant || '—'],
      ]),
      el('span', { class: 'sub' }, 'Modes de fusion'),
      el('div', { class: 'tags modes' }, Object.entries(K.modes).map(([m, n]) => el('span', null, `${m} × ${n}`))),
      peints.length ? [
        el('span', { class: 'sub' }, 'Couverture du canevas par calque'),
        el('div', { class: 'cover' }, peints.map((x) => el('div', { class: x.visible_effectif === false ? 'off' : null },
          el('span', { title: x.nom }, x.nom),
          el('div', null, el('i', { style: { width: `${(x.couverture / maxC) * 100}%`, background: x.couleur || '#cfcfcf' } })),
          el('span', null, pct(x.couverture, 1))))),
      ] : null,
      el('button', { class: 'pill wide', onclick: () => lw.setOpen(true) }, '▤ Ouvrir la fenêtre Calques'),
    ];
  } else {
    G = [section('G', 'Calques'),
      el('div', { class: 'box' }, 'Fichier aplati : aucun calque détecté. Un .tif enregistré avec ses calques (ou un .psd) les afficherait ici.')];
  }

  return el('aside', { class: 'fiche' }, head, el('div', { class: 'fiche-scroll' }, A, B, Cl, D, E, Fp, G));
}

function renderViewer(d) {
  const canvas = el('canvas', { class: 'art protect', role: 'img', 'aria-label': d.titre });
  const bbox = el('div', { class: 'bbox', hidden: true }, el('span'));
  const wrap = el('div', { class: 'art-wrap' }, canvas, bbox);
  const loading = el('div', { class: 'loading' }, 'chargement…');
  const stage = el('div', { class: 'stage protect' }, loading);
  const zoomLbl = el('span', { class: 'zoom' }, 'Ajusté');
  const steps = [1, 1.5, 2, 3, 4];
  let zi = 0, ready = false;

  const layout = () => {
    if (!ready) return;
    const pad = 2 * parseFloat(getComputedStyle(stage).paddingLeft || 0);
    // petit écran : l'image prend toute la largeur et la zone s'adapte à sa hauteur
    const narrow = window.matchMedia('(max-width: 900px)').matches && !document.fullscreenElement;
    const fit = narrow
      ? Math.min((stage.clientWidth - pad) / canvas.width, (window.innerHeight * 0.75) / canvas.height)
      : Math.min((stage.clientWidth - pad) / canvas.width, (stage.clientHeight - pad) / canvas.height);
    const scale = Math.max(0.05, fit) * steps[zi];
    canvas.style.width = `${Math.round(canvas.width * scale)}px`;
    canvas.style.height = `${Math.round(canvas.height * scale)}px`;
    zoomLbl.textContent = zi === 0 ? 'Ajusté' : `${Math.round(scale * 100)} %`;
  };
  const zoom = (dir) => { zi = Math.max(0, Math.min(steps.length - 1, zi + dir)); layout(); };

  (async () => {
    try {
      const res = await fetch(d.img);
      const bmp = await createImageBitmap(await res.blob());
      canvas.width = bmp.width;
      canvas.height = bmp.height;
      canvas.getContext('2d').drawImage(bmp, 0, 0);
      if (bmp.close) bmp.close();
      ready = true;
      stage.replaceChildren(wrap);
      layout();
    } catch {
      loading.textContent = 'Image indisponible';
    }
  })();

  // cadre du calque sélectionné, en % de l'image d'origine
  const F = d.stats.fichier;
  // repère des calques : la toile du fichier à calques (peut différer de l'image affichée)
  const toile = (d.calques && d.calques.toile) || [F.largeur, F.hauteur];
  const showBox = (layer) => {
    const r = layer && (layer.contenu || layer.bbox);
    if (!r || layer.type === 'groupe') { bbox.hidden = true; return; }
    const [l, t, rr, b] = r;
    Object.assign(bbox.style, {
      left: `${(l / toile[0]) * 100}%`, top: `${(t / toile[1]) * 100}%`,
      width: `${((rr - l) / toile[0]) * 100}%`, height: `${((b - t) / toile[1]) * 100}%`,
    });
    bbox.firstChild.textContent = layer.nom;
    bbox.hidden = false;
  };

  const layersBtn = el('button', { class: 'pill', 'aria-pressed': 'false', title: 'Fenêtre Calques (C)' }, '▤ Calques');
  const annee = String(d.date).slice(0, 4);
  const bar = el('div', { class: 'vbar' },
    el('button', { class: 'icon', 'aria-label': 'Dézoomer', onclick: () => zoom(-1) }, '−'),
    zoomLbl,
    el('button', { class: 'icon', 'aria-label': 'Zoomer', onclick: () => zoom(1) }, '+'),
    el('button', { class: 'pill', style: { marginLeft: '8px' }, onclick: () => (stage.requestFullscreen ? stage.requestFullscreen() : null) }, '⤢ Plein écran'),
    layersBtn,
    el('span', { class: 'copy' }, `© ${DATA.auteur} ${annee} · Tous droits réservés`));

  const node = el('section', { class: 'viewer' }, stage, bar);
  return { node, layout, zoom, showBox, layersBtn };
}

/* ───────── fenêtre Calques ───────── */
const EYE = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>';
const EYE_OFF = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3l18 18"/><path d="M10.6 5.1A10.9 10.9 0 0 1 12 5c7 0 11 7 11 7a18.6 18.6 0 0 1-3.2 4.2M6.6 6.6C3.2 8.7 1 12 1 12s4 7 11 7a10.7 10.7 0 0 0 5.4-1.4"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>';
const FOLDER = '<svg viewBox="0 0 24 24" width="26" height="26"><path fill="#d8a338" d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path fill="#f6cf62" d="M3 9h18v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';

function svg(markup) {
  const t = document.createElement('template');
  t.innerHTML = markup; // constantes du code, jamais de données du fichier
  return t.content.firstChild;
}

function loadPrefs() {
  try { return JSON.parse(localStorage.getItem('calques') || 'null') || {}; } catch { return {}; }
}
function savePrefs() {
  try { localStorage.setItem('calques', JSON.stringify({ open: state.layersOpen, pos: state.layersPos })); } catch { /* navigation privée */ }
}

function layersWindow(d, viewer) {
  const C = d.calques;
  const F = d.stats.fichier;
  const liste = C ? C.liste : [];
  let sel = -1;
  const collapsed = new Set();

  const modeBox = el('div', { class: 'lw-mode' }, '—');
  const opaBox = el('div', { class: 'lw-opa mono' }, '—');
  const list = el('div', { class: 'lw-list', role: 'listbox', 'aria-label': 'Calques', tabindex: '0' });
  const details = el('div', { class: 'lw-details mono' });
  const closeBtn = el('button', { class: 'lw-close', 'aria-label': 'Fermer la fenêtre Calques', title: 'Fermer' }, '✕');
  const head = el('div', { class: 'lw-title' },
    el('span', null, 'Calques'),
    el('span', { class: 'lw-count mono' }, C ? `${C.nb_calques} · ${C.nb_groupes} gr.` : ''),
    closeBtn);
  const win = el('div', { class: 'lw' + (liste.length ? '' : ' lw-flat'), role: 'dialog', 'aria-label': 'Fenêtre Calques', hidden: true },
    head,
    el('div', { class: 'lw-tools' }, modeBox, opaBox),
    list,
    details);

  const thumbEl = (it) => {
    if (it.type === 'groupe') return el('div', { class: 'lw-thumb lw-folder' }, svg(FOLDER));
    const box = el('div', { class: 'lw-thumb protect' });
    const sp = C.sprite;
    if (sp && it.vignette != null) {
      const k = Math.min(44 / sp.l, 38 / sp.h);
      const col = it.vignette % sp.cols, row = Math.floor(it.vignette / sp.cols);
      box.append(el('div', {
        class: 'lw-img',
        style: {
          width: `${sp.l * k}px`, height: `${sp.h * k}px`,
          backgroundImage: `url("${sp.url}")`,
          backgroundSize: `${sp.cols * sp.l * k}px ${sp.rows * sp.h * k}px`,
          backgroundPosition: `${-col * sp.l * k}px ${-row * sp.h * k}px`,
        },
      }));
    }
    return box;
  };

  const rows = liste.map((it, i) => {
    const hidden = it.visible_effectif === false;
    const eye = el('span', { class: 'lw-eye', title: it.visible ? 'Visible' : 'Masqué' }, svg(it.visible ? EYE : EYE_OFF));
    const radio = el('span', { class: 'lw-radio' + (it.verrou ? ' on' : ''), title: it.verrou ? 'Transparence verrouillée' : '' });
    const labels = el('div', { class: 'lw-labels' },
      it.fusion !== 'Normal' && it.fusion !== 'Transfert' ? el('span', { class: 'lw-blend' }, it.fusion) : null,
      el('span', { class: 'lw-name' },
        it.type === 'groupe' ? el('span', { class: 'lw-chev' }, '▾') : null,
        it.nom),
      el('span', { class: 'lw-meta mono' },
        [it.opacite < 100 ? `${it.opacite} %` : null,
          it.type === 'groupe' ? `${it.enfants} élément${it.enfants > 1 ? 's' : ''}` : null,
          it.ecretage ? 'écrêtage' : null,
          it.special || null,
          it.vide ? 'vide' : null].filter(Boolean).join(' · ')));
    const row = el('div', {
      class: 'lw-row' + (hidden ? ' off' : '') + (it.type === 'groupe' ? ' grp' : '') + (it.ecretage ? ' clip' : ''),
      role: 'option', 'aria-selected': 'false',
      style: { '--lvl': it.niveau },
      onclick: (e) => {
        if (it.type === 'groupe' && e.target.closest('.lw-chev')) toggle(i);
        else select(i);
      },
      ondblclick: () => { if (it.type === 'groupe') toggle(i); },
    }, el('div', { class: 'lw-side' }, eye, radio),
    el('div', { class: 'lw-main' }, it.ecretage ? el('span', { class: 'lw-cliparrow' }, '↳') : null, thumbEl(it), labels));
    return row;
  });

  function refreshCollapse() {
    let hideBelow = Infinity;
    liste.forEach((it, i) => {
      if (it.niveau <= hideBelow) hideBelow = Infinity;
      rows[i].hidden = it.niveau > hideBelow;
      if (!rows[i].hidden && it.type === 'groupe' && collapsed.has(i)) hideBelow = it.niveau;
      if (it.type === 'groupe') rows[i].querySelector('.lw-chev').textContent = collapsed.has(i) ? '▸' : '▾';
    });
  }
  function toggle(i) { collapsed.has(i) ? collapsed.delete(i) : collapsed.add(i); refreshCollapse(); }

  function detailRows(it) {
    const out = [];
    const add = (k, v) => out.push(el('div', null, el('span', null, k), el('span', null, v)));
    add('Type', it.type === 'groupe' ? 'Groupe' : (it.special ? `Calque (${it.special})` : 'Calque de pixels'));
    add('Visibilité', it.visible ? (it.visible_effectif === false ? 'visible, groupe parent masqué' : 'visible') : 'masqué');
    add('Fusion', it.fusion);
    add('Opacité', `${it.opacite} %${it.fond != null ? ` · fond ${it.fond} %` : ''}`);
    if (it.type === 'groupe') {
      add('Contenu', `${it.enfants} élément${it.enfants > 1 ? 's' : ''}`);
    } else {
      if (it.contenu) {
        const [l, t, r, b] = it.contenu;
        add('Contenu', `${nf(r - l)} × ${nf(b - t)} px`);
        add('Position', `x ${nf(l)} · y ${nf(t)}`);
      } else if (it.vide) add('Contenu', 'vide');
      if (it.pixels != null) add('Pixels peints', nf(it.pixels));
      if (it.couverture != null) add('Couverture', pct(it.couverture, 2));
      if (it.couleur) out.push(el('div', null, el('span', null, 'Couleur moy.'), el('span', null, el('i', { class: 'chip', style: { background: it.couleur } }), it.couleur)));
      add('Écrêtage', it.ecretage ? 'oui' : 'non');
      add('Verrou alpha', it.verrou ? 'oui' : 'non');
      if (it.masque) add('Masque', 'oui');
    }
    return out;
  }

  function select(i) {
    sel = i;
    rows.forEach((r, j) => { r.classList.toggle('sel', j === i); r.setAttribute('aria-selected', String(j === i)); });
    const it = liste[i];
    modeBox.textContent = it.fusion;
    opaBox.textContent = String(it.opacite);
    details.replaceChildren(...detailRows(it));
    viewer.showBox(it);
    rows[i].scrollIntoView({ block: 'nearest' });
  }

  list.addEventListener('keydown', (e) => {
    const visible = rows.map((r, j) => (!r.hidden ? j : -1)).filter((j) => j >= 0);
    const k = visible.indexOf(sel);
    if (e.key === 'ArrowDown') { e.preventDefault(); e.stopPropagation(); select(visible[Math.min(visible.length - 1, k + 1)] ?? visible[0]); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); e.stopPropagation(); select(visible[Math.max(0, k - 1)] ?? visible[0]); }
  });

  if (liste.length) {
    list.append(...rows);
  } else {
    list.append(el('div', { class: 'lw-empty' },
      el('strong', null, 'Image aplatie'),
      el('span', null, `Aucun calque n’a été trouvé dans ${d.fichier}. Enregistre le dessin en .tif avec calques (ou en .psd) pour les voir ici.`)));
    details.replaceChildren(el('div', null, el('span', null, 'Format'), el('span', null, F.format)));
  }
  if (C) details.replaceChildren(el('div', null, el('span', null, 'Source'), el('span', null, C.source)),
    el('div', null, el('span', null, 'Astuce'), el('span', null, 'clique un calque')));

  /* ouverture / fermeture */
  const setOpen = (open) => {
    state.layersOpen = open;
    win.hidden = !open;
    viewer.layersBtn.setAttribute('aria-pressed', String(open));
    viewer.layersBtn.classList.toggle('on', open);
    if (!open) viewer.showBox(null);
    else if (sel >= 0) viewer.showBox(liste[sel]);
    if (open) place();
    savePrefs();
  };
  closeBtn.addEventListener('click', () => setOpen(false));
  viewer.layersBtn.addEventListener('click', () => setOpen(win.hidden));

  /* déplacement (barre de titre) + taille mémorisée */
  const host = viewer.node;
  function place() {
    const p = state.layersPos;
    if (!p) return;
    const maxX = Math.max(0, host.clientWidth - win.offsetWidth);
    const maxY = Math.max(0, host.clientHeight - 48 - Math.min(win.offsetHeight, 120));
    win.style.left = `${Math.min(maxX, Math.max(0, p.x))}px`;
    win.style.top = `${Math.min(maxY, Math.max(0, p.y))}px`;
    if (p.w && p.v === 2) win.style.width = `${p.w}px`;
    if (p.h && liste.length) win.style.height = `${p.h}px`;
  }
  head.addEventListener('pointerdown', (e) => {
    if (e.target.closest('button') || window.matchMedia('(max-width: 900px)').matches) return;
    e.preventDefault();
    const sx = e.clientX - win.offsetLeft, sy = e.clientY - win.offsetTop;
    head.setPointerCapture(e.pointerId);
    const move = (ev) => {
      state.layersPos = { ...(state.layersPos || {}), x: ev.clientX - sx, y: ev.clientY - sy };
      place();
    };
    const up = () => {
      head.removeEventListener('pointermove', move);
      head.removeEventListener('pointerup', up);
      savePrefs();
    };
    head.addEventListener('pointermove', move);
    head.addEventListener('pointerup', up);
  });
  const ro = new ResizeObserver(() => {
    if (win.hidden || !liste.length || (!win.style.height && !win.dataset.touched)) return;
    state.layersPos = { ...(state.layersPos || { x: win.offsetLeft, y: win.offsetTop }), w: win.offsetWidth, h: win.offsetHeight, v: 2 };
  });
  win.addEventListener('pointerdown', () => { win.dataset.touched = '1'; });
  win.addEventListener('pointerup', savePrefs);
  ro.observe(win);

  host.append(win);
  return { setOpen, toggle: () => setOpen(win.hidden), win, destroy: () => ro.disconnect(), place };
}

function showDossier(id) {
  const d = DATA.dessins.find((x) => x.id === id);
  if (!d) { location.hash = '#/'; return; }
  setMode('dossier');
  state.lastId = id;
  document.title = `${d.titre} · ${DATA.titre_site}`;
  V.tab.textContent = d.titre;
  setCrumbs([{ label: 'Dessins', href: '#/' }, { label: d.titre }]);
  V.back.disabled = V.up.disabled = false;

  let items = list();
  if (!items.some((x) => x.id === id)) items = DATA.dessins;
  const i = items.findIndex((x) => x.id === id);
  const prev = items[i - 1], next = items[i + 1];
  V.prev.disabled = !prev;
  V.next.disabled = !next;
  V.prev.onclick = () => prev && (location.hash = `#/d/${prev.id}`);
  V.next.onclick = () => next && (location.hash = `#/d/${next.id}`);

  const viewer = renderViewer(d);
  const lw = layersWindow(d, viewer);
  V.view.replaceChildren(el('div', { class: 'dossier' }, viewer.node, renderFiche(d, lw)));
  lw.setOpen(state.layersOpen);
  V.view.scrollTop = 0;
  const F = d.stats.fichier;
  setStatus(`Dessin ${i + 1} / ${items.length}`, `${F.format} · ${nf(F.largeur)} × ${nf(F.hauteur)} px`, size(F.poids));

  const onKey = (e) => {
    if (e.target.closest('input, select, textarea')) return;
    if (e.key === 'ArrowLeft' && prev) location.hash = `#/d/${prev.id}`;
    else if (e.key === 'ArrowRight' && next) location.hash = `#/d/${next.id}`;
    else if (e.key === 'Escape' && !document.fullscreenElement) location.hash = '#/';
    else if (e.key === '+' || e.key === '=') viewer.zoom(1);
    else if (e.key === '-') viewer.zoom(-1);
    else if (e.key === 'c' || e.key === 'C') lw.toggle();
  };
  window.addEventListener('keydown', onKey);
  const onResize = () => { viewer.layout(); lw.place(); };
  window.addEventListener('resize', onResize);
  document.addEventListener('fullscreenchange', viewer.layout);
  cleanup = () => {
    lw.destroy();
    window.removeEventListener('resize', onResize);
    window.removeEventListener('keydown', onKey);
    document.removeEventListener('fullscreenchange', viewer.layout);
  };
}

/* ───────── page droits ───────── */
function showDroits() {
  setMode('droits');
  document.title = `Droits · ${DATA.titre_site}`;
  V.tab.textContent = 'Droits & mentions';
  setCrumbs([{ label: 'Droits & mentions' }]);
  V.back.disabled = V.up.disabled = false;
  renderSidebar();
  const a = DATA.auteur;
  V.view.replaceChildren(el('div', { class: 'droits' },
    el('h1', null, 'Droits & mentions'),
    el('h2', null, 'Droits d’auteur'),
    el('p', null, `Tous les dessins publiés sur ce site sont des œuvres originales de ${a}. © ${a}, tous droits réservés.`),
    el('p', null, 'Toute reproduction, représentation, modification, diffusion ou réutilisation, totale ou partielle, sans autorisation écrite préalable est interdite (articles L.122-4 et L.335-2 du Code de la propriété intellectuelle).'),
    el('h2', null, 'Intelligence artificielle'),
    el('p', null, 'L’utilisation de ces images pour entraîner, affiner ou alimenter un modèle d’intelligence artificielle, ainsi que leur collecte automatisée (scraping), sont expressément interdites.'),
    el('h2', null, 'Authenticité'),
    el('p', null, 'L’empreinte SHA-256 de chaque fichier original est publiée sur sa fiche et horodatée par l’historique de publication du site. Elle permet de prouver l’antériorité et l’intégrité de chaque œuvre.'),
    DATA.contact ? [el('h2', null, 'Contact'), el('p', null, 'Pour toute demande d’utilisation : ', el('a', { href: `mailto:${DATA.contact}` }, DATA.contact))] : null,
    el('h2', null, 'Hébergement'),
    el('p', null, 'GitHub Pages — GitHub, Inc., 88 Colin P. Kelly Jr. Street, San Francisco, CA 94107, États-Unis.'),
  ));
  V.view.scrollTop = 0;
  setStatus('Droits & mentions');
}

/* ───────── routeur ───────── */
function route() {
  const m = location.hash.match(/^#\/d\/([\w-]+)/);
  if (m) showDossier(m[1]);
  else if (location.hash === '#/droits') showDroits();
  else showBureau();
}

V.search.addEventListener('input', () => { state.q = V.search.value; state.scroll = 0; if (document.body.dataset.mode === 'bureau') showBureau(); });
V.catSelect.addEventListener('change', () => { state.cat = V.catSelect.value || null; state.scroll = 0; showBureau(); });
V.sort.addEventListener('change', () => { state.sort = V.sort.value; if (document.body.dataset.mode === 'bureau') showBureau(); });
V.up.addEventListener('click', () => {
  if (document.body.dataset.mode === 'bureau' && state.cat) { state.cat = null; showBureau(); }
  else location.hash = '#/';
});
V.back.addEventListener('click', () => {
  if (document.body.dataset.mode === 'bureau' && state.cat) { state.cat = null; showBureau(); }
  else if (history.length > 1) history.back();
  else location.hash = '#/';
});
window.addEventListener('hashchange', route);

function removeSplash() {
  const s = document.getElementById('splash');
  if (s) s.remove();
}

fetch('data.json', { cache: 'no-cache' })
  .then((r) => r.json())
  .then((data) => {
    DATA = data;
    route();
    // page d'accueil : seulement quand on arrive sur le bureau (pas sur un lien direct vers un dessin)
    const deepLink = /^#\/(d\/|droits)/.test(location.hash);
    if (DATA.accueil && !deepLink && window.Intro) window.Intro.start(DATA.accueil);
    else removeSplash();
  })
  .catch(() => {
    removeSplash();
    V.view.replaceChildren(el('p', { class: 'empty' }, 'Impossible de charger data.json — lance d’abord « python analyse.py », puis ouvre le site via un serveur (voir README).'));
  });
