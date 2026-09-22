// Story player: loads a story JSON, plays its narration audio and renders the scene
// timeline in sync with it. Every animation is driven by audio.currentTime (not by wall
// clock), so pausing, seeking and scrubbing always keep the visuals locked to the voice.
(() => {
  'use strict';

  const CONFIG = Object.assign({ storyUrl: 'data/story.hi.json' }, window.STORY_PLAYER_CONFIG);
  const ICONS = window.ICONS || {};
  const params = new URLSearchParams(location.search);
  // Embedded in an app (Flutter WebView, React iframe): no builder, full-screen player, events
  // posted to the host. Set by index.html from ?embed=1, or automatically inside an iframe.
  const EMBED = document.documentElement.hasAttribute('data-embed');

  const EASE = 'cubic-bezier(.2,.8,.2,1)';
  const RISE = [
    { opacity: 0, transform: 'translateY(4cqw) scale(.98)', filter: 'blur(4px)' },
    { opacity: 1, transform: 'none', filter: 'blur(0)' },
  ];
  const SLIDE = [{ opacity: 0, transform: 'translateX(-4cqw)' }, { opacity: 1, transform: 'none' }];
  const POP = [
    { opacity: 0, transform: 'scale(.6)' },
    { opacity: 1, transform: 'scale(1.08)', offset: 0.7 },
    { opacity: 1, transform: 'none' },
  ];
  const TONE_TINT = { good: 'green', warn: 'amber', bad: 'red', neutral: 'violet' };
  const DEFAULT_BANDS = [
    { from: 300, to: 549, label: 'Poor', color: '#f04438' },
    { from: 550, to: 649, label: 'Needs work', color: '#fb6514' },
    { from: 650, to: 724, label: 'Fair', color: '#fdb022' },
    { from: 725, to: 774, label: 'Good', color: '#66c61c' },
    { from: 775, to: 900, label: 'Excellent', color: '#12b76a' },
  ];

  const $ = (id) => document.getElementById(id);
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const easeOutCubic = (p) => 1 - Math.pow(1 - p, 3);
  const easeInOutCubic = (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);

  const phone = $('phone');
  const stage = $('stage');
  const audio = $('audio');

  const state = {
    story: null,
    storyUrl: '',
    segs: [],              // narration segments, see buildSegs()
    scenes: [],            // scenes of every loaded segment, on the story clock
    captions: [],
    duration: 0,           // whole story, with estimates for chapters still being recorded
    available: 0,          // end of the part that is recorded, from the start without gaps
    estimated: false,
    pos: { seg: 0, local: 0 },  // playhead: segment and seconds into it
    bound: -1,             // segment whose audio is loaded in the <audio> element
    bindToken: 0,
    wantPlay: false,       // the viewer wants it playing (it may be waiting for a chapter)
    waiting: false,
    waitTimer: 0,
    expectPause: 0,        // pause / play events we caused ourselves, so the rest can be told
    expectPlay: 0,         // apart as coming from the system (calls, lock screen, headset)
    scrubbing: false,
    scrubT: 0,
    active: null,
    activeKey: null,
    chapterKey: null,
    lastT: -1,
    captionsOn: true,
    caption: null,
    captionWords: [],
    loadToken: 0,
    pollTimer: 0,
    pollFails: 0,
    lastChange: 0,
    reported: { playing: false, at: 0, t: -1 },
  };

  // ---------------------------------------------------------------- helpers

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }

  // Text with *accent* segments, e.g. "Your path to *800+*". Built with text nodes, never innerHTML.
  function rich(tag, className, text) {
    const node = el(tag, className);
    String(text ?? '').split('*').forEach((part, i) => {
      if (part) node.append(i % 2 ? el('span', 'accent', part) : document.createTextNode(part));
    });
    return node;
  }

  function resolveUrl(path) {
    return new URL(path, state.storyUrl || location.href).href;
  }

  function icon(name) {
    const holder = el('span');
    holder.innerHTML = (Object.prototype.hasOwnProperty.call(ICONS, name) && ICONS[name]) || ICONS.info || '';
    return holder.firstElementChild || holder;
  }

  function tile(name, tint = 'blue', extra = '') {
    const node = el('span', `tile tint-${tint} ${extra}`.trim());
    node.append(icon(name));
    return node;
  }

  function chip(status) {
    const node = el('span', `chip tone-${(status && status.tone) || 'good'}`, status && status.text);
    if (!status || !status.text) node.hidden = true;
    return node;
  }

  function impactTag(text) {
    const level = /high/i.test(text) ? 3 : /medium/i.test(text) ? 2 : 1;
    const tag = el('span', 'impact');
    const bars = el('span', 'bars');
    for (let i = 0; i < 3; i++) bars.append(el('i', i < level ? 'on' : ''));
    tag.append(bars, text || '');
    return tag;
  }

  function block(tag, className, parent, text) {
    const node = el(tag, `block hide ${className}`, text);
    parent.append(node);
    return node;
  }

  // Eyebrow, title and subtitle stacked in one flow block, so long titles push the
  // subtitle down instead of overlapping it. Returns the parts to animate in order.
  function sceneHead(root, { eyebrow, aside, title, sub }, className = 'block scene-head') {
    const head = el('div', className);
    const parts = [];
    if (eyebrow || aside) {
      const row = el('div', 'scene-head-row hide');
      row.append(el('p', 'eyebrow', eyebrow || ''));
      if (aside) row.append(aside);
      parts.push(row);
    }
    if (title) parts.push(rich('h1', 'title hide', title));
    if (sub) parts.push(el('p', 'scene-sub hide', sub));
    head.append(...parts);
    root.append(head);
    return parts;
  }

  function animateHead(tl, parts, at) {
    parts.forEach((part, i) => tl.to(part, RISE, at + 0.05 + i * 0.1));
  }

  function beat(scene, action, index) {
    return (scene.beats || []).find((b) => b.action === action && (index == null || b.index === index)) || null;
  }

  function beatAt(scene, action, fallback, index) {
    const b = beat(scene, action, index);
    return b && Number.isFinite(b.at) ? b.at : fallback;
  }

  function clock(t) {
    const s = Math.max(0, Math.floor(t || 0));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  }

  // "100%" -> count-up friendly parts; null when the value is not a plain number.
  function numberParts(value) {
    const m = /^(\D*?)(\d+(?:\.\d+)?)(.*)$/.exec(String(value ?? ''));
    return m ? { prefix: m[1], number: parseFloat(m[2]), decimals: (m[2].split('.')[1] || '').length, suffix: m[3] } : null;
  }

  // ---------------------------------------------------------------- timeline

  // Collects Web Animations (paused) and JS tweens for one scene, then scrubs them to a
  // scene-local time on every frame. The first animation on an element fills backwards so
  // it defines the element's state before its beat; later ones only fill forwards.
  class Timeline {
    constructor() {
      this.anims = [];
      this.tweens = [];
      this.seen = new WeakSet();
    }

    // A negative `at` means the animation already happened, e.g. in a scene that continues the
    // previous one: it is finished from the scene's first frame.
    to(node, keyframes, at, duration = 0.6, easing = EASE) {
      if (!node || !Number.isFinite(at)) return;
      const fill = this.seen.has(node) ? 'forwards' : 'both';
      this.seen.add(node);
      const anim = node.animate(keyframes, {
        duration: Math.max(1, duration * 1000),
        delay: at * 1000,
        fill,
        easing,
      });
      anim.pause();
      this.anims.push(anim);
    }

    tween(at, duration, apply, ease = easeOutCubic) {
      if (Number.isFinite(at)) this.tweens.push({ at, duration, apply, ease });
    }

    toggle(at, node, className) {
      this.tween(at, 0, (e) => node.classList.toggle(className, e >= 1));
    }

    count(node, value, at, duration = 0.9) {
      const parts = numberParts(value);
      if (!parts) { node.textContent = value ?? ''; return; }
      this.tween(at, duration, (e) => {
        node.textContent = parts.prefix + (parts.number * e).toFixed(parts.decimals) + parts.suffix;
      });
    }

    seek(t) {
      const ms = t * 1000;
      for (const a of this.anims) a.currentTime = ms;
      for (const tw of this.tweens) {
        const p = tw.duration > 0 ? clamp((t - tw.at) / tw.duration, 0, 1) : t >= tw.at ? 1 : 0;
        tw.apply(tw.ease(p), p);
      }
    }

    destroy() {
      for (const a of this.anims) a.cancel();
    }
  }

  // ---------------------------------------------------------------- scene: score_dial

  function renderScoreDial(scene, root, tl) {
    const p = scene.props || {};
    const min = Number(p.min ?? 300) || 0;
    const max = Number(p.max ?? 900) || 900;
    const score = clamp(Math.round(p.score ?? min), min, max);
    const from = clamp(p.count_from ?? min, min, max);
    // Band colours go into SVG markup, so only plain colour values are accepted.
    const bands = (Array.isArray(p.bands) && p.bands.length ? p.bands : DEFAULT_BANDS).map((b) => ({
      ...b, from: Number(b.from) || 0, to: Number(b.to) || 0,
      color: /^(#[0-9a-f]{3,8}|rgba?\([\d\s.,%]+\))$/i.test(String(b.color)) ? b.color : '#8e8e93',
    }));
    const tone = (p.status && p.status.tone) || 'good';

    const head = sceneHead(root, { eyebrow: p.eyebrow, title: p.title });

    // Dial: bands as arc segments, a marker riding the arc, the score in the middle.
    const CX = 120, CY = 116, R = 96;
    const frac = (v) => clamp((v - min) / (max - min), 0, 1);
    const at = (f, r = R) => {
      const a = Math.PI * (1 + f);
      return [CX + r * Math.cos(a), CY + r * Math.sin(a)];
    };
    const pt = (xy) => xy.map((n) => n.toFixed(2)).join(' ');
    const bandFor = (v) => bands.find((b) => v >= b.from && v <= b.to) || bands[bands.length - 1];
    const gap = 0.006;
    const arcs = bands.map((b) => {
      const f0 = frac(b.from) + gap;
      const f1 = frac(Math.min(b.to + 1, max)) - gap;
      return `<path class="sd-band" d="M${pt(at(f0))} A${R} ${R} 0 0 1 ${pt(at(f1))}" stroke="${b.color}"/>`;
    }).join('');

    const dial = block('div', 'card sd-dial', root);
    const art = el('div', 'sd-art');
    art.innerHTML = `<svg viewBox="0 0 240 132" aria-hidden="true">${arcs}
      <text class="sd-scale" x="${CX - R}" y="${CY + 14}" text-anchor="middle">${min}</text>
      <text class="sd-scale" x="${CX + R}" y="${CY + 14}" text-anchor="middle">${max}</text>
      <circle class="sd-marker" r="9"/></svg>`;
    const bandEls = [...art.querySelectorAll('.sd-band')];
    const marker = art.querySelector('.sd-marker');
    const center = el('div', 'sd-center');
    const scoreEl = el('div', 'sd-score', from);
    const statusChip = chip(p.status);
    center.append(scoreEl, el('div', 'sd-outof', `out of ${max}`), statusChip);
    art.append(center);
    const legend = el('div', 'sd-legend');
    const legendEls = bands.map((b) => {
      const item = el('span', '', b.label);
      item.style.setProperty('--c', b.color);
      legend.append(item);
      return item;
    });
    dial.append(art, legend);

    // Percentile: a row of ten people, filled in proportion.
    const pct = p.percentile || null;
    const pctCard = pct ? block('div', 'card sd-pct', root) : null;
    const people = [];
    if (pctCard) {
      pctCard.append(rich('p', 'sd-pct-text', pct.text));
      const row = el('div', 'sd-people');
      for (let i = 0; i < 10; i++) {
        const person = icon('person');
        row.append(person);
        people.push(person);
      }
      pctCard.append(row);
    }

    // How lenders see you
    const lenders = p.lenders || null;
    const lendCard = lenders ? block('div', `card sd-lenders${pctCard ? '' : ' is-up'}`, root) : null;
    const stats = [];
    if (lendCard) {
      lendCard.append(el('p', 'sd-lenders-title', lenders.title || ''));
      const grid = el('div', 'sd-stats');
      (lenders.items || []).forEach((item) => {
        const stat = el('div', 'sd-stat');
        stat.append(tile(item.icon, TONE_TINT[tone] || 'blue'), el('span', 'sd-stat-label', item.label), el('span', 'sd-stat-value', item.value));
        grid.append(stat);
        stats.push(stat);
      });
      lendCard.append(grid);
    }

    const introAt = beatAt(scene, 'intro', 0);
    const reveal = beat(scene, 'reveal');
    const revealAt = reveal && Number.isFinite(reveal.at) ? reveal.at : introAt + 0.8;
    const revealDur = (reveal && reveal.duration) || 2.2;
    const pctAt = beatAt(scene, 'percentile', null);
    const lendAt = beatAt(scene, 'lenders', null);

    animateHead(tl, head, introAt);
    tl.to(dial, RISE, introAt + 0.3, 0.7);

    tl.tween(revealAt, revealDur, (e) => {
      const v = from + (score - from) * e;
      scoreEl.textContent = Math.round(v);
      const [x, y] = at(frac(v));
      marker.setAttribute('cx', x.toFixed(2));
      marker.setAttribute('cy', y.toFixed(2));
      marker.style.stroke = bandFor(v).color;
      bands.forEach((b, i) => { bandEls[i].style.opacity = String(0.18 + 0.82 * clamp((v - b.from) / 25, 0, 1)); });
    }, easeInOutCubic);
    tl.to(statusChip, POP, revealAt + revealDur - 0.1, 0.5);
    const activeBand = bands.indexOf(bandFor(score));
    if (legendEls[activeBand]) tl.toggle(revealAt + revealDur, legendEls[activeBand], 'is-active');

    if (pctCard && pctAt != null) {
      tl.to(pctCard, RISE, pctAt);
      const filled = Math.round(clamp(Number(pct.value) || 0, 0, 100) / 10);
      people.slice(0, filled).forEach((person, i) => tl.toggle(pctAt + 0.45 + i * 0.09, person, 'on'));
    }
    if (lendCard && lendAt != null) {
      tl.to(lendCard, RISE, lendAt);
      stats.forEach((stat, i) => tl.to(stat, RISE, lendAt + 0.25 + i * 0.12, 0.5));
    }
  }

  // ---------------------------------------------------------------- scene: factor_overview

  function renderFactorOverview(scene, root, tl) {
    const p = scene.props || {};
    const items = Array.isArray(p.items) ? p.items : [];
    const length = scene.end - scene.start;

    const head = sceneHead(root, { eyebrow: p.eyebrow, title: p.title, sub: p.subtitle });
    const list = block('div', 'card fo-list', root);
    const rows = items.map((item) => {
      const row = el('div', 'fo-row');
      const main = el('div', 'fo-main');
      main.append(el('span', 'fo-name', item.name || ''), impactTag(item.impact));
      const result = el('div', 'fo-result');
      result.append(el('span', 'fo-value', item.result ?? ''), chip({ text: toneLabel(item.tone), tone: item.tone }));
      row.append(tile(item.icon, TONE_TINT[item.tone] || 'blue'), main, result);
      list.append(row);
      return row;
    });
    const note = p.note ? block('div', 'card fo-note', root) : null;
    if (note) note.append(tile('info', 'blue'), el('p', '', p.note));

    animateHead(tl, head, 0);
    const times = items.map((_, i) => beatAt(scene, 'item', 0.3 + (i * length) / (items.length + 1), i));
    tl.to(list, RISE, Math.max(0, (times[0] ?? 0.3) - 0.15));
    rows.forEach((row, i) => tl.to(row, SLIDE, times[i], 0.5));
    if (note) tl.to(note, RISE, (times[times.length - 1] ?? 0.3) + 0.7);
  }

  function toneLabel(tone) {
    return { good: 'Healthy', warn: 'Watch', bad: 'At risk', neutral: 'No data' }[tone] || '';
  }

  // ---------------------------------------------------------------- scene: factor_insight

  function renderFactorInsight(scene, root, tl) {
    const p = scene.props || {};
    const metric = p.metric || {};
    const detail = p.detail || null;
    const tone = (metric.status && metric.status.tone) || 'good';

    const head = sceneHead(root, {
      eyebrow: p.eyebrow, aside: p.impact ? impactTag(p.impact) : null, title: p.title, sub: p.explainer,
    });

    // Metric card: ring (percent-like values) or a big count.
    const card = block('div', 'card fi-metric', root);
    const isCount = metric.visual === 'count';
    const visual = el('div', `fi-visual tone-${tone}${isCount ? ' is-count' : ''}`);
    const C = 2 * Math.PI * 42;
    let ring = null;
    if (isCount) {
      visual.append(el('span', 'fi-count'));
    } else {
      visual.innerHTML = `<svg viewBox="0 0 100 100" aria-hidden="true"><circle class="fi-ring-track" cx="50" cy="50" r="42"/>
        <circle class="fi-ring" cx="50" cy="50" r="42" stroke-dasharray="${C.toFixed(2)}" stroke-dashoffset="${C.toFixed(2)}"/></svg>`;
      ring = visual.querySelector('.fi-ring');
    }
    const valueEl = el('span', 'fi-value', metric.value ?? '');
    visual.append(valueEl);
    const text = el('div');
    text.append(el('p', 'fi-label', metric.label || ''), el('p', 'fi-sublabel', metric.sublabel || ''), chip(metric.status));
    card.append(visual, text);

    const detailCard = detail ? block('div', 'card fi-detail', root) : null;
    const animateDetail = detailCard ? buildDetail(detail, detailCard, tone) : null;

    const tip = p.tip ? block('div', 'card fi-tip', root) : null;
    if (tip) {
      const body = el('div');
      body.append(el('p', 'fi-tip-title', p.tip.title || ''));
      if (p.tip.text) body.append(el('p', 'fi-tip-text', p.tip.text));
      tip.append(tile(p.tip.icon || 'bulb', 'blue'), body);
    }

    const introAt = beatAt(scene, 'intro', 0);
    const valueAt = Math.max(introAt, beatAt(scene, 'value', introAt + 0.8));
    const detailAt = beatAt(scene, 'detail', null);
    const tipAt = beatAt(scene, 'tip', detailAt != null ? detailAt + 0.9 : null);

    animateHead(tl, head, introAt);
    tl.to(card, RISE, valueAt);
    tl.count(valueEl, metric.value, valueAt + 0.2);
    if (ring) {
      const progress = clamp(Number(metric.progress) || 0, 0, 1);
      tl.tween(valueAt + 0.2, 1.1, (e) => ring.setAttribute('stroke-dashoffset', (C * (1 - progress * e)).toFixed(2)));
    } else {
      tl.to(visual, POP, valueAt + 0.1, 0.6);
    }
    if (detailCard && detailAt != null) {
      tl.to(detailCard, RISE, detailAt);
      animateDetail(tl, detailAt + 0.3);
    }
    if (tip && tipAt != null) tl.to(tip, RISE, tipAt);
  }

  // Builds the detail visual and returns a function that schedules its animation.
  function buildDetail(detail, card, tone) {
    card.append(el('p', 'fi-detail-title', detail.title || ''));
    const summary = el('p', 'fi-summary');
    let schedule = () => {};

    if (detail.type === 'months') {
      const wrap = el('div', 'months');
      const dots = (detail.months || []).map((m) => {
        const cell = el('div', `month is-${m.state === 'late' ? 'late' : m.state === 'none' ? 'none' : 'ok'}`);
        const dot = el('span', 'month-dot');
        dot.append(icon(m.state === 'late' ? 'x' : m.state === 'none' ? 'info' : 'check'));
        cell.append(dot, el('span', '', m.label || ''));
        wrap.append(cell);
        return dot;
      });
      card.append(wrap);
      schedule = (tl, at) => dots.forEach((dot, i) => tl.to(dot, POP, at + i * 0.09, 0.45));
    } else if (detail.type === 'meter') {
      const ideal = clamp(Number(detail.ideal_max) || 30, 0, 100);
      const empty = detail.value == null;
      const meter = el('div', `meter${empty ? ' is-empty' : ''}`);
      const zone = el('div', 'meter-ideal');
      zone.style.width = `${ideal}%`;
      meter.append(zone);
      const scale = el('div', 'meter-scale');
      const idealLabel = el('span', 'is-ideal', `Healthy ≤ ${ideal}%`);
      idealLabel.style.left = `${ideal}%`;
      scale.append(el('span', '', '0%'), idealLabel, el('span', '', '100%'));
      scale.lastChild.style.left = '100%';
      let fill = null;
      if (!empty) {
        const value = clamp(Number(detail.value) || 0, 0, 100);
        fill = el('div', `meter-fill${value > ideal ? ' tone-bad' : ''}`);
        meter.append(fill);
        schedule = (tl, at) => tl.tween(at, 0.9, (e) => { fill.style.width = `${(value * e).toFixed(2)}%`; });
      } else {
        schedule = (tl, at) => tl.to(meter, [{ opacity: 0.2 }, { opacity: 1 }], at, 0.5);
      }
      card.append(meter, scale);
    } else if (detail.type === 'slots') {
      const limit = clamp(Math.round(Number(detail.limit) || 3), 1, 8);
      const value = Math.max(0, Math.round(Number(detail.value) || 0));
      const wrap = el('div', 'slots');
      const slots = [];
      for (let i = 0; i < Math.max(limit, value); i++) {
        const slot = el('span', `slot${i < value ? (i >= limit ? ' is-over' : ' is-used') : ''}`, i < value ? '' : 'Free');
        wrap.append(slot);
        slots.push(slot);
      }
      const countEl = el('span', 'slot-count');
      countEl.append(`${value}`, el('small', '', ` / ${limit}`));
      wrap.append(countEl);
      card.append(wrap);
      schedule = (tl, at) => slots.forEach((slot, i) => tl.to(slot, POP, at + i * 0.1, 0.45));
    }

    if (detail.summary) {
      const good = (detail.tone || tone) === 'good';
      summary.classList.toggle('is-info', !good);
      summary.append(icon(good ? 'check' : 'info'), detail.summary);
      card.append(summary);
    }
    return schedule;
  }

  // ---------------------------------------------------------------- scene: action_plan

  function renderActionPlan(scene, root, tl) {
    const p = scene.props || {};
    const current = Number(p.current) || 0;
    const target = Number(p.target) || current;
    const [lo, hi] = Array.isArray(p.range) && p.range.length === 2 ? p.range : [current - 100, target + 50];
    const pos = (v) => clamp(((v - lo) / (hi - lo)) * 100, 0, 100);

    const head = sceneHead(root, { eyebrow: p.eyebrow, title: p.title });

    const card = block('div', 'card ap-progress', root);
    const ends = el('div', 'ap-ends');
    const cur = el('div', 'ap-end');
    const curValue = el('div', 'ap-end-value', current);
    cur.append(el('div', 'ap-end-label', 'Current'), curValue);
    const tgt = el('div', 'ap-end is-target');
    tgt.append(el('div', 'ap-end-label', 'Target'), el('div', 'ap-end-value', p.target_label ?? target));
    ends.append(cur, tgt);
    const track = el('div', 'ap-track');
    const fill = el('div', 'ap-fill');
    const gap = el('div', 'ap-gap');
    gap.style.left = `${pos(current)}%`;
    gap.style.width = `${Math.max(0, pos(target) - pos(current))}%`;
    const knob = el('div', 'ap-knob');
    const flag = el('div', 'ap-flag');
    flag.style.left = `${pos(target)}%`;
    track.append(fill, gap, flag, knob);
    const scale = el('div', 'ap-scale');
    scale.append(el('span', '', lo), el('span', '', hi));
    const gapChip = el('span', 'ap-gap-chip');
    gapChip.append(icon('trend'), p.gap_label || '');
    card.append(ends, track, scale, gapChip);

    const list = el('ol', 'block ap-steps');
    root.append(list);
    const steps = (Array.isArray(p.steps) ? p.steps : []).map((step, i) => {
      const item = el('li', 'card ap-step hide');
      const body = el('div');
      body.append(el('p', 'ap-step-title', step.title || ''));
      if (step.text) body.append(el('p', 'ap-step-text', step.text));
      item.append(el('span', 'ap-num', i + 1), body, tile(step.icon || 'check', 'blue'));
      list.append(item);
      return item;
    });

    const note = p.note ? el('p', 'ap-note hide') : null;
    if (note) { note.append(icon('info'), p.note); list.append(note); }
    list.classList.toggle('is-compact', steps.length > 3);

    const reveal = beat(scene, 'reveal');
    const revealAt = reveal && Number.isFinite(reveal.at) ? reveal.at : 0;
    const drawDur = (reveal && reveal.duration) || 1.6;
    animateHead(tl, head, revealAt);
    tl.to(card, RISE, revealAt + 0.3);
    tl.tween(revealAt + 0.5, drawDur, (e) => {
      const x = pos(lo + (current - lo) * e);
      fill.style.width = `${x}%`;
      knob.style.left = `${x}%`;
      curValue.textContent = Math.round(lo + (current - lo) * e);
    }, easeInOutCubic);
    tl.to(gap, [{ opacity: 0 }, { opacity: 1 }], revealAt + 0.5 + drawDur, 0.4);
    tl.to(flag, POP, revealAt + 0.6 + drawDur, 0.5);
    tl.to(gapChip, POP, revealAt + 0.8 + drawDur, 0.5);

    const stepGap = (scene.end - scene.start - revealAt - 2) / Math.max(1, steps.length + 1);
    let lastStep = revealAt + 1;
    steps.forEach((item, i) => {
      lastStep = beatAt(scene, 'step', revealAt + 2 + stepGap * i, i);
      tl.to(item, RISE, lastStep);
    });
    if (note) tl.to(note, RISE, lastStep + 1.2);
  }

  // ---------------------------------------------------------------- flow scenes
  // These scene types stack their blocks under the heading instead of using fixed positions,
  // so a report with many rows never overlaps itself. Each block reveals on its own beat.

  function flow(root, p, className = '') {
    const box = el('div', `flow ${className}`.trim());
    root.append(box);
    const head = sceneHead(box, { eyebrow: p.eyebrow, title: p.title, sub: p.subtitle }, 'scene-head');
    return { box, head };
  }

  const tint = (tone) => TONE_TINT[tone] || 'blue';

  function itemTimes(scene, action, count, from = 0.6, gap = 0.6) {
    return Array.from({ length: count }, (_, i) => beatAt(scene, action, from + i * gap, i));
  }

  function renderTitle(scene, root, tl) {
    const p = scene.props || {};
    const { box, head } = flow(root, p, 'flow-title');
    const mark = tile('sparkle', 'blue', 'tile-lg hide');
    box.prepend(mark);
    const chips = el('div', 'chips');
    const chipEls = (p.chips || []).map((c) => {
      const chipEl = el('span', 'info-chip hide');
      chipEl.append(icon(c.icon || 'check'), c.text || '');
      chips.append(chipEl);
      return chipEl;
    });
    box.append(chips);
    const introAt = beatAt(scene, 'intro', 0);
    tl.to(mark, POP, introAt + 0.05, 0.6);
    animateHead(tl, head, introAt + 0.15);
    itemTimes(scene, 'chip', chipEls.length, 1).forEach((at, i) => tl.to(chipEls[i], POP, at, 0.5));
  }

  function renderPoints(scene, root, tl) {
    const p = scene.props || {};
    const { box, head } = flow(root, p);
    const card = el('div', 'card rows hide');
    const rows = (p.items || []).map((item) => {
      const row = el('div', 'row');
      const body = el('div', 'row-body');
      body.append(el('span', 'row-title', item.title || ''), el('span', 'row-sub', item.text || ''));
      row.append(tile(item.icon || 'check', tint(item.tone)), body);
      if (item.tag) row.append(el('span', 'row-tag', item.tag));
      card.append(row);
      return row;
    });
    box.append(card);
    animateHead(tl, head, 0);
    const times = itemTimes(scene, 'item', rows.length);
    tl.to(card, RISE, Math.max(0.3, (times[0] ?? 0.6) - 0.2));
    rows.forEach((row, i) => tl.to(row, SLIDE, times[i], 0.5));
  }

  function renderStats(scene, root, tl) {
    const p = scene.props || {};
    const { box, head } = flow(root, p);
    const grid = el('div', 'stat-grid');
    const tiles = (p.tiles || []).map((t, i, all) => {
      const card = el('div', `card stat-tile hide${i === all.length - 1 && all.length % 2 ? ' is-wide' : ''}`);
      const top = el('div', 'stat-top');
      top.append(tile(t.icon || 'doc', tint(t.tone), 'tile-sm'), el('span', 'stat-label', t.label || ''));
      const value = el('span', `stat-value${t.tone ? ` tone-${t.tone}` : ''}`, t.value ?? '');
      card.append(top, value);
      if (t.sub) card.append(el('span', 'stat-sub', t.sub));
      grid.append(card);
      return { card, value, raw: t.value };
    });
    box.append(grid);
    const note = p.note ? el('div', 'card note hide') : null;
    if (note) { note.append(tile(p.note.icon || 'info', 'blue', 'tile-sm'), el('p', '', p.note.text || '')); box.append(note); }
    animateHead(tl, head, 0);
    itemTimes(scene, 'item', tiles.length).forEach((at, i) => {
      tl.to(tiles[i].card, RISE, at, 0.5);
      tl.count(tiles[i].value, tiles[i].raw, at + 0.1, 0.8);
    });
    if (note) tl.to(note, RISE, beatAt(scene, 'note', (scene.end - scene.start) - 3));
  }

  function renderBars(scene, root, tl) {
    const p = scene.props || {};
    const { box, head } = flow(root, p);
    let summary = null;
    if (p.summary) {
      summary = el('div', 'card bar-summary hide');
      const text = el('div');
      text.append(el('span', 'bar-summary-label', p.summary.label || ''), el('span', 'bar-summary-hint', p.summary.hint || ''));
      summary.append(el('span', `bar-summary-value tone-${p.summary.tone || 'good'}`, p.summary.value || ''), text);
      box.append(summary);
    }
    const card = el('div', 'card bar-card hide');
    const bars = p.bars || [];
    const maxAll = Math.max(1, ...bars.map((b) => (b.max ? 0 : Number(b.value) || 0)));
    let group = null;
    const rows = bars.map((b) => {
      if (b.group && b.group !== group) {
        group = b.group;
        card.append(el('p', 'bar-group', group));
      }
      const row = el('div', 'bar-row');
      const top = el('div', 'bar-top');
      const label = el('div', 'bar-label');
      label.append(el('span', 'row-title', b.label || ''));
      if (b.sub) label.append(el('span', 'row-sub', b.sub));
      top.append(label, el('span', 'bar-value', b.display ?? b.value ?? ''));
      const track = el('div', 'bar-track');
      const fill = el('div', `bar-fill tone-${b.tone || 'blue'}`);
      track.append(fill);
      row.append(top, track);
      card.append(row);
      const frac = b.max ? clamp((Number(b.value) || 0) / b.max, 0, 1) : clamp((Number(b.value) || 0) / maxAll, 0, 1);
      return { row, fill, frac: Math.max(frac, (Number(b.value) || 0) > 0 ? 0.015 : 0) };
    });
    box.append(card);
    animateHead(tl, head, 0);
    const times = itemTimes(scene, 'item', rows.length, 0.6, 0.35);
    tl.to(card, RISE, Math.max(0.3, (times[0] ?? 0.6) - 0.2));
    rows.forEach((r, i) => {
      tl.to(r.row, SLIDE, times[i], 0.45);
      tl.tween(times[i] + 0.1, 0.9, (e) => { r.fill.style.width = `${(r.frac * e * 100).toFixed(2)}%`; });
    });
    if (summary) tl.to(summary, POP, beatAt(scene, 'summary', (times[times.length - 1] ?? 0.6) + 0.8), 0.55);
  }

  function renderHistoryGrid(scene, root, tl) {
    const p = scene.props || {};
    const { box, head } = flow(root, p);
    const months = p.months || [];
    const card = el('div', 'card grid-card hide');
    card.style.setProperty('--cols', months.length || 12);
    const header = el('div', 'grid-row grid-head');
    header.append(el('span'));
    const cellsHead = el('div', 'grid-cells');
    months.forEach((m) => cellsHead.append(el('span', 'grid-month', m)));
    header.append(cellsHead);
    card.append(header);
    const hot = [];
    const rows = (p.rows || []).map((r) => {
      const row = el('div', 'grid-row');
      const label = el('div', 'grid-label');
      label.append(el('span', 'row-title', r.label || ''), el('span', 'row-sub', r.sub || ''));
      const cells = el('div', 'grid-cells');
      (r.cells || []).forEach((state) => {
        const cell = el('span', `cell is-${state}`);
        cells.append(cell);
        if (state === 'late' || state === 'severe') hot.push(cell);
      });
      row.append(label, cells);
      card.append(row);
      return row;
    });
    const legend = el('div', 'legend hide');
    (p.legend || []).forEach((l) => {
      const item = el('span');
      item.append(el('i', `cell is-${l.state}`), l.text || '');
      legend.append(item);
    });
    box.append(card, legend);
    animateHead(tl, head, 0);
    const times = itemTimes(scene, 'row', rows.length, 0.6, 0.15);
    tl.to(card, RISE, Math.max(0.3, (times[0] ?? 0.6) - 0.2));
    rows.forEach((row, i) => tl.to(row, SLIDE, times[i], 0.45));
    tl.to(legend, RISE, beatAt(scene, 'legend', (times[times.length - 1] ?? 0.6) + 1));
    const hl = beatAt(scene, 'highlight', null);
    if (hl != null) hot.forEach((cell) => tl.toggle(hl, cell, 'is-hot'));
  }

  function renderList(scene, root, tl) {
    const p = scene.props || {};
    const { box, head } = flow(root, p);
    const card = el('div', 'card rows hide');
    const rows = (p.rows || []).map((r) => {
      const row = el('div', 'row');
      const body = el('div', 'row-body');
      body.append(el('span', 'row-title', r.title || ''));
      if (r.sub) body.append(el('span', 'row-sub', r.sub));
      const value = el('div', 'row-value');
      value.append(el('span', `row-value-main tone-${r.tone || 'neutral'}`, r.value ?? ''));
      if (r.value_sub) value.append(el('span', 'row-sub', r.value_sub));
      row.append(tile(r.icon || 'doc', tint(r.tone)), body, value);
      card.append(row);
      return row;
    });
    box.append(card);
    const note = p.note ? el('div', 'card note hide') : null;
    if (note) { note.append(tile(p.note.icon || 'info', 'blue', 'tile-sm'), el('p', '', p.note.text || '')); box.append(note); }
    animateHead(tl, head, 0);
    const times = itemTimes(scene, 'item', rows.length);
    tl.to(card, RISE, Math.max(0.3, (times[0] ?? 0.6) - 0.2));
    rows.forEach((row, i) => tl.to(row, SLIDE, times[i], 0.5));
    if (note) tl.to(note, RISE, beatAt(scene, 'note', (times[times.length - 1] ?? 0.6) + 1.5));
  }

  function renderUnknown(scene, root) {
    const box = el('div', 'block card unknown');
    box.append('Unknown scene type ', el('code', '', scene.type), ` in scene "${scene.id || '?'}"`);
    root.append(box);
  }

  const RENDERERS = {
    score_dial: renderScoreDial,
    factor_overview: renderFactorOverview,
    factor_insight: renderFactorInsight,
    action_plan: renderActionPlan,
    title: renderTitle,
    points: renderPoints,
    stats: renderStats,
    bars: renderBars,
    history_grid: renderHistoryGrid,
    list: renderList,
  };

  // ---------------------------------------------------------------- segments
  // The narration is a list of segments played back to back on the one <audio> element. One
  // element, because a single tap on Play then unlocks audio for the whole story on iOS and in
  // Android WebViews. A single-file story is one segment. A generated CRIF story has one
  // segment per chapter, and while it is still being recorded the player polls its manifest
  // and picks up new chapters as they appear. The playhead is kept as (segment, time within
  // it), because the start times of later segments move as estimates become real durations.

  const POLL_MS = 1500;
  const STALL_MS = 3 * 60 * 1000;   // no new chapter for this long: the recording has stopped

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function segLen(seg) {
    return seg.ready ? seg.duration : seg.estimate;
  }

  function makeSeg(raw, base, seg = { data: null, blobUrl: null, audioJob: null, dataJob: null }) {
    const ready = Boolean(raw.ready);
    return Object.assign(seg, {
      id: raw.id || '',
      chapter: raw.chapter || '',
      theme: raw.theme || null,
      continues: Boolean(raw.continues),   // second part of the previous segment's chapter
      ready,
      duration: ready ? Number(raw.duration) || 0 : null,
      estimate: Math.max(0, Number(raw.estimate) || 0),
      audioUrl: ready ? new URL(raw.audio, base).href : null,
      dataUrl: ready ? new URL(raw.data, base).href : null,
    });
  }

  function buildSegs(story, base) {
    if (Array.isArray(story.segments)) return story.segments.map((raw) => makeSeg(raw, base));
    return [{
      id: 'story', chapter: '', theme: null, ready: true, single: true, estimate: 0,
      duration: Number(story.audio.duration) || 0,
      audioUrl: new URL(story.audio.src, base).href, dataUrl: null,
      data: { scenes: story.scenes, captions: story.captions || [] }, blobUrl: null, audioJob: null, dataJob: null,
    }];
  }

  // Puts every loaded segment's scenes and captions on the story clock.
  function rebuildTimeline() {
    const scenes = [];
    const captions = [];
    let offset = 0;
    let available = 0;
    let gap = false;
    state.segs.forEach((seg, i) => {
      seg.offset = offset;
      if (seg.ready && !gap) available = offset + segLen(seg);
      else gap = true;
      if (seg.data) {
        const at = (x) => Math.round((offset + (Number(x) || 0)) * 1000) / 1000;
        seg.data.scenes.forEach((scene, j) => {
          scenes.push({ ...scene, start: at(scene.start), end: at(scene.end), key: `${i}:${j}`, seg: i });
        });
        (seg.data.captions || []).forEach((c) => captions.push({
          ...c, start: at(c.start), end: at(c.end),
          words: Array.isArray(c.words) ? c.words.map((w) => ({ ...w, start: at(w.start) })) : null,
        }));
      }
      offset += segLen(seg);
    });
    scenes.sort((a, b) => a.start - b.start);
    state.scenes = scenes;
    state.captions = captions;
    state.duration = offset;
    state.available = available;
    state.estimated = state.segs.some((s) => !s.ready);
  }

  function segIndexAt(t) {
    const segs = state.segs;
    for (let i = 0; i < segs.length; i++) if (t < segs[i].offset + segLen(segs[i])) return i;
    return segs.length - 1;
  }

  function posAt(t) {
    const max = state.duration > 0 ? state.duration - 0.05 : Infinity;
    const at = clamp(t, 0, Math.max(0, max));
    const k = segIndexAt(at);
    return { seg: k, local: at - state.segs[k].offset };
  }

  // The scene on screen at t: the latest scene of t's segment that has started.
  function sceneAt(t) {
    const k = segIndexAt(t);
    let hit = null;
    let first = null;
    for (const scene of state.scenes) {
      if (scene.seg !== k) continue;
      first = first || scene;
      if (scene.start <= t) hit = scene;
    }
    return hit || first;
  }

  async function fetchRetry(url, tries = 3) {
    for (let attempt = 1; ; attempt++) {
      try {
        const res = await fetch(url, { cache: 'no-store' });
        if (res.status >= 500 && attempt < tries) throw new Error(`HTTP ${res.status}`);
        return res;
      } catch (err) {
        if (attempt >= tries) throw err;
        await sleep(600 * attempt);
      }
    }
  }

  async function fetchJson(url, tries = 3) {
    const res = await fetchRetry(url, tries);
    if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText} while fetching\n${url}`);
    const text = await res.text();
    try {
      return JSON.parse(text);
    } catch (err) {
      throw new Error(`Invalid JSON in ${url}\n${err.message}`);
    }
  }

  // Audio is fetched into a Blob URL so seeking works on any static server (python -m
  // http.server has no Range support) and the next chapter switches in instantly. Falls back
  // to streaming the URL directly if the fetch is blocked, e.g. by CORS.
  function loadSegAudio(seg) {
    if (seg.blobUrl) return Promise.resolve(seg.blobUrl);
    if (!seg.audioJob) {
      seg.audioJob = (async () => {
        try {
          const res = await fetchRetry(seg.audioUrl);
          if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText} while fetching the audio\n${seg.audioUrl}`);
          seg.blobUrl = URL.createObjectURL(await res.blob());
        } catch (err) {
          if (!(err instanceof TypeError)) throw err;
          console.warn('Audio fetch failed, streaming it directly instead', err);
          seg.blobUrl = seg.audioUrl;
        }
        return seg.blobUrl;
      })().catch((err) => { seg.audioJob = null; throw err; });
    }
    return seg.audioJob;
  }

  function loadSegData(seg) {
    if (seg.data) return Promise.resolve(seg.data);
    if (!seg.dataJob) {
      const token = state.loadToken;
      seg.dataJob = fetchJson(seg.dataUrl).then((data) => {
        const problems = [];
        if (!data || !Array.isArray(data.scenes) || !data.scenes.length) problems.push('scenes must be a non-empty array');
        else sceneProblems(data.scenes, problems);
        if (problems.length) throw new Error(`${seg.dataUrl} has ${problems.length} problem(s):\n• ${problems.join('\n• ')}`);
        seg.data = data;
        if (token === state.loadToken) {
          rebuildTimeline();
          render(true);
        }
        return data;
      }).catch((err) => { seg.dataJob = null; throw err; });
    }
    return seg.dataJob;
  }

  // Loads the next chapters' audio ahead of time, and every recorded chapter's scene data
  // (a few KB each) so scrubbing can preview any part that is ready.
  function prefetch(k) {
    [k + 1, k + 2].forEach((i) => {
      const seg = state.segs[i];
      if (seg && seg.ready) loadSegAudio(seg).catch(() => {});
    });
    state.segs.forEach((seg) => { if (seg.ready && !seg.data) loadSegData(seg).catch(() => {}); });
  }

  function releaseMedia() {
    internalPause();
    state.bound = -1;
    state.bindToken++;
    setWaiting(false);
    state.segs.forEach((seg) => { if (seg.blobUrl && seg.blobUrl.startsWith('blob:')) URL.revokeObjectURL(seg.blobUrl); });
  }

  // ---------------------------------------------------------------- playback

  function internalPause() {
    if (!audio.paused) {
      state.expectPause++;
      audio.pause();
    }
  }

  function startAudio() {
    const counted = audio.paused;
    if (counted) state.expectPlay++;
    const attempt = audio.play();
    if (attempt && attempt.catch) {
      attempt.catch((err) => {
        if (!err || err.name !== 'NotAllowedError') return;   // AbortError: a pause or new source won the race
        if (counted) state.expectPlay = Math.max(0, state.expectPlay - 1);   // no play event comes
        // Autoplay was blocked (no tap yet): fall back to the start screen's Play button.
        if (state.wantPlay) {
          state.wantPlay = false;
          syncPlayState();
          showStart();
        }
      });
    }
  }

  function setSource(url) {
    return new Promise((resolve, reject) => {
      const done = (fn) => () => {
        audio.removeEventListener('loadedmetadata', onOk);
        audio.removeEventListener('error', onErr);
        fn();
      };
      const onOk = done(resolve);
      const onErr = done(() => reject(new Error(`Could not play the audio file:\n${url}`)));
      audio.addEventListener('loadedmetadata', onOk);
      audio.addEventListener('error', onErr);
      audio.src = url;
      audio.load();
    });
  }

  function mediaLength(seg) {
    return Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : segLen(seg);
  }

  // Makes the <audio> element match the playhead: same segment only moves currentTime,
  // another segment is fetched and swapped in, a chapter still being recorded waits for it.
  async function bindCurrent() {
    const k = state.pos.seg;
    const seg = state.segs[k];
    if (!seg) return;
    if (state.bound === k) {
      const local = clamp(state.pos.local, 0, Math.max(0, mediaLength(seg) - 0.05));
      if (Math.abs(audio.currentTime - local) > 0.02) audio.currentTime = local;
      setWaiting(false);
      if (state.wantPlay) startAudio();
      return;
    }
    const token = ++state.bindToken;
    internalPause();
    state.bound = -1;
    setWaiting(true);
    if (!seg.ready) {
      checkGeneration();
      return;
    }
    try {
      await Promise.all([loadSegAudio(seg), loadSegData(seg)]);
      if (token !== state.bindToken) return;
      await setSource(seg.blobUrl);
    } catch (err) {
      if (token === state.bindToken) showError(err, seg.audioUrl);
      return;
    }
    if (token !== state.bindToken) return;
    state.bound = k;
    if (seg.single && Number.isFinite(audio.duration) && Math.abs(audio.duration - seg.duration) > 0.01) {
      seg.duration = audio.duration;
      rebuildTimeline();
    }
    audio.currentTime = clamp(state.pos.local, 0, Math.max(0, mediaLength(seg) - 0.05));
    setWaiting(false);
    if (state.wantPlay) startAudio();
    prefetch(k);
    render(true);
  }

  function onSegmentEnded() {
    const k = state.bound;
    if (k < 0) return;
    if (k < state.segs.length - 1) {
      state.pos = { seg: k + 1, local: 0 };
      bindCurrent();
      return;
    }
    state.wantPlay = false;
    syncPlayState();
    render(true);
    showEndCard();
    emit('ended');
  }

  function atEnd() {
    const last = state.segs.length - 1;
    return state.pos.seg === last && state.pos.local >= segLen(state.segs[last]) - 0.1;
  }

  // Called straight from taps, so the play() inside bindCurrent's fast path runs within the
  // gesture, which is what lets iOS and WebViews start audio.
  function play() {
    if (!state.story || !state.segs.length) return;
    hideOverlays();
    if (atEnd()) state.pos = { seg: 0, local: 0 };
    state.wantPlay = true;
    syncPlayState();
    bindCurrent();
  }

  function pause() {
    state.wantPlay = false;
    internalPause();
    syncPlayState();
  }

  function togglePlay() {
    if (!state.story) return;
    if (state.wantPlay) pause();
    else play();
  }

  function goTo(k, local) {
    if (!state.segs[k]) return;
    state.pos = { seg: k, local: Math.max(0, local) };
    $('endCard').hidden = true;
    bindCurrent();
    render(true);
  }

  function seek(t) {
    if (!state.story || !state.segs.length) return;
    const pos = posAt(t);
    goTo(pos.seg, pos.local);
  }

  function setWaiting(on) {
    state.waiting = on;
    clearTimeout(state.waitTimer);
    if (!on) {
      show('buffering', false);
    } else {
      const seg = state.segs[state.pos.seg];
      $('bufferingText').textContent = seg && !seg.ready ? 'Recording this chapter…' : 'Loading…';
      // Only shown if the wait is noticeable, so switching chapters doesn't flash it.
      state.waitTimer = setTimeout(() => { if (state.waiting) show('buffering', true); }, 300);
    }
    syncPlayState();
  }

  // ---------------------------------------------------------------- generation (polling)

  function startPolling() {
    clearTimeout(state.pollTimer);
    if (!state.story || state.story.status !== 'generating') return;
    const token = state.loadToken;
    const poll = async () => {
      if (token !== state.loadToken) return;
      try {
        const next = await fetchJson(state.storyUrl, 1);
        if (token !== state.loadToken) return;
        validate(next);
        applyManifest(next);
        state.pollFails = 0;
      } catch (err) {
        state.pollFails++;
        if (/HTTP 404/.test(err.message)) {
          markStopped('This video has expired.');
          return;
        }
      }
      if (token !== state.loadToken || state.story.status !== 'generating') return;
      if (Date.now() - state.lastChange > STALL_MS) {
        markStopped('Recording stopped before the whole video was ready.');
        return;
      }
      state.pollTimer = setTimeout(poll, Math.min(10000, POLL_MS * (1 + state.pollFails)));
    };
    state.pollTimer = setTimeout(poll, POLL_MS);
  }

  function applyManifest(next) {
    if (!Array.isArray(next.segments) || next.segments.length !== state.segs.length) return;
    let changed = false;
    next.segments.forEach((raw, i) => {
      const seg = state.segs[i];
      if (!seg.ready && (raw.ready || Number(raw.estimate) !== seg.estimate)) {
        makeSeg(raw, state.storyUrl, seg);
        changed = true;
      }
    });
    const statusChanged = next.status !== state.story.status;
    Object.assign(state.story, { status: next.status, error: next.error, intro: next.intro || state.story.intro });
    if (changed) {
      state.lastChange = Date.now();
      rebuildTimeline();
      buildDevScenes();
      prefetch(state.pos.seg);
    }
    if (changed || statusChanged) emitGeneration();
    checkGeneration();
    render(true);
  }

  // Resumes a playhead that was waiting for a chapter, or reports that it will never come.
  function checkGeneration() {
    const seg = state.segs[state.pos.seg];
    if (!seg || state.bound === state.pos.seg) return;
    if (seg.ready) {
      if (state.waiting) bindCurrent();
    } else if (state.story.status === 'failed') {
      const err = new Error(state.story.error || 'This chapter could not be recorded.');
      err.generation = true;
      showError(err, state.storyUrl);
    }
  }

  function markStopped(message) {
    clearTimeout(state.pollTimer);
    state.story.status = 'failed';
    state.story.error = state.story.error || message;
    emitGeneration();
    checkGeneration();
  }

  function emitGeneration() {
    const detail = {
      status: state.story.status,
      ready: state.segs.filter((s) => s.ready).length,
      total: state.segs.length,
      error: state.story.error || null,
    };
    window.dispatchEvent(new CustomEvent('story:generation', { detail }));
    emit('generation', detail);
  }

  // ---------------------------------------------------------------- engine

  function unmountActive(animate) {
    const prev = state.active;
    state.active = null;
    state.activeKey = null;
    if (!prev) return;
    if (!animate) {
      prev.tl.destroy();
      prev.root.remove();
      return;
    }
    prev.root.classList.add('is-leaving');
    setTimeout(() => { prev.tl.destroy(); prev.root.remove(); }, 400);
  }

  // A scene marked `continues` picks up exactly where the previous one left off (the same
  // screen, recorded as a separate segment), so it replaces it without a transition.
  function mountScene(scene) {
    unmountActive(!(scene && scene.continues));
    if (!scene) return;
    const root = el('section', 'scene');
    root.dataset.type = scene.type;
    root.dataset.id = scene.id || '';
    stage.append(root);
    const tl = new Timeline();
    try {
      (RENDERERS[scene.type] || renderUnknown)(scene, root, tl);
    } catch (err) {
      console.error(`Scene "${scene.id}" failed to render`, err);
      tl.destroy();
      root.replaceChildren();
      renderUnknown({ ...scene, type: `${scene.type} (error: ${err.message})` }, root);
    }
    state.active = { scene, root, tl };
    state.activeKey = scene.key;
    phone.dataset.theme = scene.theme || 'blue';
    highlightDevScene(scene);
  }

  // Story time now: the scrub position while dragging, otherwise the playhead (read from the
  // audio element while its segment is loaded).
  function now() {
    if (state.scrubbing) return state.scrubT;
    const seg = state.segs[state.pos.seg];
    if (!seg) return 0;
    if (state.bound === state.pos.seg) state.pos.local = audio.currentTime;
    return seg.offset + state.pos.local;
  }

  function render(force) {
    if (!state.story) return;
    const t = now();
    if (!force && t === state.lastT) return;
    state.lastT = t;
    const scene = sceneAt(t);
    if ((scene ? scene.key : null) !== state.activeKey) mountScene(scene);
    else if (scene && state.active) state.active.scene = scene;
    if (state.active) state.active.tl.seek(t - state.active.scene.start);
    updateChapter(t);
    updateProgress(t);
    updateCaptions(t);
    updateReadout(t);
    reportTime(t);
  }

  function tick() {
    render(false);
    requestAnimationFrame(tick);
  }

  // ---------------------------------------------------------------- chrome

  function updateChapter(t) {
    const k = segIndexAt(t);
    const seg = state.segs[k];
    const scene = state.active && state.active.scene;
    const title = (scene && scene.chapter) || (seg && seg.chapter) || '';
    const label = $('chapterLabel');
    if (label.textContent !== title) label.textContent = title;
    if (!scene && seg && seg.theme) phone.dataset.theme = seg.theme;
    // Reported when the chapter changes; the second part of a chapter is the same chapter.
    if (title !== state.chapterKey && !state.scrubbing) {   // previews while dragging aren't reported
      state.chapterKey = title;
      const multi = state.segs.length > 1;
      const index = multi
        ? state.segs.slice(0, k + 1).filter((s) => !s.continues).length - 1
        : state.scenes.filter((s) => !s.continues && s.start <= t).length - 1;
      emit('chapter', {
        index: Math.max(0, index),
        count: chapterCount(),
        id: multi ? seg.id : (scene && scene.id) || '',
        title,
      });
    }
  }

  function chapterCount() {
    return (state.segs.length > 1 ? state.segs : state.scenes).filter((s) => !s.continues).length;
  }

  function updateProgress(t) {
    const d = Math.max(0.001, state.duration);
    const played = clamp(t / d, 0, 1) * 100;
    $('scrubFill').style.width = `${played.toFixed(3)}%`;
    $('scrubThumb').style.left = `${played.toFixed(3)}%`;
    $('scrubReady').style.width = `${(clamp(state.available / d, 0, 1) * 100).toFixed(3)}%`;
    const total = `${state.estimated ? '~' : ''}${clock(state.duration)}`;
    $('timeLabel').textContent = `${clock(t)} / ${total}`;
    const scrub = $('scrub');
    scrub.setAttribute('aria-valuemax', String(Math.round(state.duration)));
    scrub.setAttribute('aria-valuenow', String(Math.round(t)));
    scrub.setAttribute('aria-valuetext', `${clock(t)} of ${total}`);

    const tip = $('scrubTip');
    tip.hidden = !state.scrubbing;
    if (state.scrubbing) {
      const scene = sceneAt(t);
      const seg = state.segs[segIndexAt(t)];
      $('scrubTipTime').textContent = clock(t);
      $('scrubTipChapter').textContent = (scene && scene.chapter) || (seg && seg.chapter) || '';
      const width = $('scrubTrack').clientWidth;
      const half = tip.offsetWidth / 2;
      tip.style.left = `${clamp((played / 100) * width, half, Math.max(half, width - half))}px`;
    }
  }

  function updateCaptions(t) {
    const box = $('captions');
    const current = state.captionsOn
      ? state.captions.find((c) => t >= c.start - 0.15 && t <= c.end + 0.5) || null
      : null;
    if (current !== state.caption) {
      state.caption = current;
      box.replaceChildren();
      state.captionWords = [];
      if (current) {
        const words = Array.isArray(current.words) && current.words.length ? current.words : null;
        if (words) {
          words.forEach((w, i) => {
            const span = el('span', 'w', w.text);
            state.captionWords.push({ span, start: w.start });
            box.append(span);
            if (i < words.length - 1) box.append(' ');
          });
        } else {
          box.append(el('span', 'w on', current.text));
        }
      }
    }
    for (const w of state.captionWords) w.span.classList.toggle('on', t >= w.start);
  }

  function updateReadout(t) {
    const a = state.active;
    $('devReadout').textContent = a
      ? `t ${t.toFixed(2)} / ${state.duration.toFixed(2)}s · ${a.scene.id || a.scene.type} +${(t - a.scene.start).toFixed(2)}s`
      : `t ${t.toFixed(2)}`;
  }

  function buildDevScenes() {
    const list = $('devScenes');
    list.replaceChildren();
    const item = (key, name, from, to, onClick) => {
      const li = el('li');
      const btn = el('button');
      btn.type = 'button';
      btn.dataset.key = key;
      btn.append(el('span', '', name), el('span', '', `${from.toFixed(1)}–${to.toFixed(1)}s`));
      btn.addEventListener('click', onClick);
      li.append(btn);
      list.append(li);
    };
    if (state.segs.length > 1) {
      state.segs.forEach((seg, i) => item(`seg:${i}`, `${seg.id}${seg.ready ? '' : ' · recording'}`,
        seg.offset, seg.offset + segLen(seg), () => goTo(i, 0)));
    } else {
      state.scenes.forEach((scene) => item(scene.key, scene.id || scene.type, scene.start, scene.end, () => seek(scene.start)));
    }
    if (state.active) highlightDevScene(state.active.scene);
  }

  function highlightDevScene(scene) {
    document.querySelectorAll('#devScenes button').forEach((b) => {
      b.classList.toggle('is-active', b.dataset.key === scene.key || b.dataset.key === `seg:${scene.seg}`);
    });
  }

  function buildBrand() {
    const name = (state.story.brand && state.story.brand.name) || '';
    const [first, ...rest] = name.split(' ');
    const brand = $('brand');
    brand.replaceChildren();
    if (first) brand.append(el('span', 'accent', first), rest.length ? ` ${rest.join(' ')}` : '');
  }

  function setCaptions(on) {
    state.captionsOn = on;
    $('ccBtn').setAttribute('aria-pressed', String(on));
    state.caption = undefined;
    render(true);
  }

  function syncPlayState() {
    const playing = state.wantPlay;
    phone.classList.toggle('is-paused', !playing || audio.paused || state.waiting);
    const btn = $('playBtn');
    const mode = playing ? 'pause' : 'play';
    if (btn.dataset.mode !== mode) {
      btn.dataset.mode = mode;
      btn.innerHTML = ICONS[mode];
      btn.setAttribute('aria-label', playing ? 'Pause' : 'Play');
    }
    if (state.story && playing !== state.reported.playing) {
      state.reported.playing = playing;
      emit(playing ? 'play' : 'pause', { time: round2(now()) });
    }
  }

  function show(id, visible) {
    $(id).hidden = !visible;
  }

  function hideOverlays() {
    ['loader', 'startScreen', 'endCard', 'errorBox'].forEach((id) => show(id, false));
    closeSheet();
  }

  function showStart() {
    hideOverlays();
    const story = state.story || {};
    const intro = story.intro || {};
    const title = $('startTitle');
    title.replaceChildren(...rich('span', '', intro.title || story.title || '').childNodes);
    $('startSub').textContent = intro.subtitle || '';
    const lang = (story.languages || []).find((l) => l.code === story.language);
    const chapters = chapterCount();
    const meta = $('startMeta');
    meta.replaceChildren();
    [`${state.estimated ? '~' : ''}${clock(state.duration)}`, lang ? lang.label : story.language, `${chapters} chapters`]
      .filter(Boolean).forEach((text) => meta.append(el('span', '', text)));
    const badges = $('startBadges');
    badges.replaceChildren();
    (intro.badges || []).forEach((b) => {
      const badge = el('span');
      badge.append(icon(b.icon || 'check'), b.text || '');
      badges.append(badge);
    });
    show('startScreen', true);
  }

  function showEndCard() {
    const card = (state.story && state.story.end_card) || {};
    closeSheet();
    $('endTitle').replaceChildren(...rich('span', '', card.title || 'That was your story').childNodes);
    $('endText').textContent = card.text || '';
    const recap = $('endRecap');
    recap.replaceChildren();
    (card.recap || []).forEach((row) => {
      const li = el('li');
      li.append(el('span', 'recap-label', row.label || ''), chip({ text: row.value, tone: row.tone }));
      recap.append(li);
    });
    recap.hidden = !(card.recap || []).length;
    $('replayLabel').textContent = card.primary_label || 'Replay story';
    $('endCloseBtn').textContent = card.secondary_label || 'Close';
    $('endDisclaimer').textContent = card.disclaimer || '';
    show('endCard', true);
  }

  function showError(err, url) {
    console.error(err);
    state.wantPlay = false;
    internalPause();
    syncPlayState();
    hideOverlays();
    const message = err && err.message ? err.message : String(err);
    $('errorText').textContent = message;
    const hint = $('errorHint');
    hint.replaceChildren();
    if (location.protocol === 'file:') {
      hint.append('This page was opened from disk, so the browser blocks loading the JSON. Serve the folder instead, e.g. ',
        el('code', '', 'python3 -m http.server 8080 --directory frontend'), ' and open ', el('code', '', 'http://localhost:8080'), '.');
    } else if (err && err.generation) {
      hint.append('Part of this video could not be recorded. Generate it again: the chapters that are already recorded are reused, so it is quick.');
    } else if (/HTTP 404/.test(message) && /\/stories\//.test(String(url))) {
      hint.append('This video has expired. Generated videos are only kept for a limited time (24 hours by default), so generate it again from the CRIF report tab.');
    } else {
      hint.append('Tried to load ', el('code', '', url), '. Fix the file, pick another one in the Story file tab, or generate a new story.');
    }
    show('errorBox', true);
    emit('error', { message });
  }

  // ---------------------------------------------------------------- language sheet

  function buildLanguages() {
    const story = state.story;
    const langs = Array.isArray(story.languages) ? story.languages : [];
    const current = langs.find((l) => l.code === story.language);
    $('langBtn').hidden = langs.length < 2;
    $('langLabel').textContent = current ? current.label : (story.language || '');
    const list = $('langList');
    list.replaceChildren();
    langs.forEach((lang) => {
      const li = el('li');
      const btn = el('button', lang.code === story.language ? 'is-current' : '');
      btn.type = 'button';
      btn.append(el('span', '', lang.label || lang.code));
      if (lang.code === story.language) btn.append(icon('check'));
      btn.addEventListener('click', () => switchLanguage(lang));
      li.append(btn);
      list.append(li);
    });
  }

  function openSheet() {
    show('sheetBackdrop', true);
    show('langSheet', true);
  }

  function closeSheet() {
    show('sheetBackdrop', false);
    show('langSheet', false);
  }

  // Reopens the same chapter in the other language.
  function switchLanguage(lang) {
    closeSheet();
    if (!lang || !lang.src || lang.code === state.story.language) return;
    const t = now();
    const scene = state.active && state.active.scene;
    const resumeId = state.segs.length > 1 ? state.segs[segIndexAt(t)].id : scene && scene.id;
    emit('language', { code: lang.code });
    loadStory(new URL(lang.src, state.storyUrl).href, {
      resumeId, play: state.wantPlay, stayPaused: true, captions: state.captionsOn,
    });
  }

  // ---------------------------------------------------------------- loading

  function sceneProblems(scenes, problems, prefix = 'scenes') {
    scenes.forEach((scene, i) => {
      const where = `${prefix}[${i}]${scene && scene.id ? ` ("${scene.id}")` : ''}`;
      if (!scene || typeof scene !== 'object') { problems.push(`${where} must be an object`); return; }
      if (typeof scene.type !== 'string') problems.push(`${where}.type is required`);
      if (!Number.isFinite(scene.start) || !Number.isFinite(scene.end)) problems.push(`${where}.start and .end must be numbers (seconds)`);
      else if (scene.end <= scene.start) problems.push(`${where}.end must be greater than .start`);
      if (scene.beats != null && !Array.isArray(scene.beats)) problems.push(`${where}.beats must be an array`);
    });
  }

  function validate(story) {
    const problems = [];
    if (!story || typeof story !== 'object' || Array.isArray(story)) throw new Error('The story JSON must be an object.');
    if (Array.isArray(story.segments)) {
      if (!story.segments.length) problems.push('segments must be a non-empty array');
      story.segments.forEach((s, i) => {
        const where = `segments[${i}]${s && s.id ? ` ("${s.id}")` : ''}`;
        if (!s || typeof s !== 'object') { problems.push(`${where} must be an object`); return; }
        if (!s.ready) return;
        if (typeof s.audio !== 'string' || !s.audio) problems.push(`${where}.audio is required once the segment is ready`);
        if (typeof s.data !== 'string' || !s.data) problems.push(`${where}.data is required once the segment is ready`);
        if (!(Number(s.duration) > 0)) problems.push(`${where}.duration must be a positive number of seconds`);
      });
    } else {
      if (!story.audio || typeof story.audio.src !== 'string' || !story.audio.src) problems.push('audio.src is required (path or URL of the narration audio)');
      if (!Array.isArray(story.scenes) || !story.scenes.length) problems.push('scenes must be a non-empty array');
      else sceneProblems(story.scenes, problems);
      if (story.captions != null && !Array.isArray(story.captions)) problems.push('captions must be an array');
    }
    if (problems.length) throw new Error(`The story JSON has ${problems.length} problem(s):\n• ${problems.join('\n• ')}`);
  }

  function startPosition(opts) {
    if (opts.resumeId) {
      const i = state.segs.length > 1 ? state.segs.findIndex((s) => s.id === opts.resumeId) : -1;
      if (i >= 0) return { seg: i, local: 0 };
      const scene = state.scenes.find((s) => s.id === opts.resumeId);
      if (scene) return posAt(scene.start);
    }
    return Number.isFinite(opts.seekTo) ? posAt(opts.seekTo) : { seg: 0, local: 0 };
  }

  // Waits until the playhead's segment is loaded in the <audio> element (it may still be
  // recording). False if another story was loaded meanwhile or an error is on screen.
  async function untilBound(token) {
    bindCurrent();
    while (state.bound !== state.pos.seg) {
      await sleep(150);
      if (token !== state.loadToken || !$('errorBox').hidden) return false;
    }
    return true;
  }

  async function loadStory(url, opts = {}) {
    const token = ++state.loadToken;
    clearTimeout(state.pollTimer);
    hideOverlays();
    $('loaderTitle').textContent = 'Preparing your credit story';
    show('loader', true);
    state.wantPlay = false;
    state.bindToken++;
    internalPause();

    let story;
    let absUrl;
    try {
      absUrl = new URL(url, location.href).href;
      story = await fetchJson(absUrl);
      validate(story);
    } catch (err) {
      if (token === state.loadToken) showError(err, absUrl || url);
      return;
    }
    if (token !== state.loadToken) return;

    unmountActive(false);
    releaseMedia();
    state.story = story;
    state.storyUrl = absUrl;
    state.segs = buildSegs(story, absUrl);
    state.lastT = -1;
    state.caption = undefined;
    state.chapterKey = null;
    state.lastChange = Date.now();
    state.pollFails = 0;
    state.reported = { playing: false, at: 0, t: -1 };
    rebuildTimeline();
    $('captions').lang = story.language || '';
    $('devUrl').value = url;
    buildBrand();
    buildDevScenes();
    buildLanguages();
    if (opts.captions != null) setCaptions(opts.captions);
    else if (story.player && typeof story.player.captions === 'boolean') setCaptions(story.player.captions);
    state.pos = startPosition(opts);
    startPolling();
    emitGeneration();

    if (!state.segs[state.pos.seg].ready) $('loaderTitle').textContent = 'Recording your video, just a few seconds…';
    if (!(await untilBound(token))) return;
    render(true);
    show('loader', false);
    window.dispatchEvent(new CustomEvent('story:loaded', { detail: story }));
    emit('loaded', snapshot());

    if (opts.play) play();
    else if (!opts.stayPaused) showStart();
  }

  // Shows the loader while something else (the builder, or the host app) prepares a story.
  function busy(message) {
    state.loadToken++;
    clearTimeout(state.pollTimer);
    pause();
    hideOverlays();
    $('loaderTitle').textContent = message || 'Preparing your credit story';
    show('loader', true);
  }

  function cancelBusy() {
    show('loader', false);
    if (state.story) {
      startPolling();
      showStart();
    }
  }

  // ---------------------------------------------------------------- embedding
  // Events go to whichever host is listening: the parent page (iframe, window.postMessage),
  // a Flutter webview_flutter JavaScriptChannel or flutter_inappwebview handler named
  // ?bridge= (default StoryPlayerBridge), or a React Native WebView. Every message is
  // { source: 'credit-story-player', type, ...details }. Hosts control the player with
  // window.StoryPlayer.* (Flutter: runJavaScript) or, from a parent page, postMessage
  // { target: 'credit-story-player', command: 'play' | 'pause' | 'toggle' | 'seek' (time) |
  //   'load' (url, autoplay) | 'captions' (on) | 'language' (code) | 'state' }.

  const BRIDGE = params.get('bridge') || 'StoryPlayerBridge';
  const PARENT_ORIGIN = params.get('origin') || '*';
  const round2 = (x) => Math.round(x * 100) / 100;

  function emit(type, details = {}) {
    const msg = { source: 'credit-story-player', type, ...details };
    const text = JSON.stringify(msg);
    try { if (window.parent !== window) window.parent.postMessage(msg, PARENT_ORIGIN); } catch (e) { /* host gone */ }
    try { const ch = window[BRIDGE]; if (ch && typeof ch.postMessage === 'function') ch.postMessage(text); } catch (e) { /* no channel */ }
    try { const iaw = window.flutter_inappwebview; if (iaw && iaw.callHandler) iaw.callHandler(BRIDGE, msg); } catch (e) { /* no handler */ }
    try { if (window.ReactNativeWebView) window.ReactNativeWebView.postMessage(text); } catch (e) { /* not RN */ }
  }

  function snapshot() {
    const scene = state.active && state.active.scene;
    const seg = state.segs[state.pos.seg];
    return {
      storyId: state.story ? state.story.story_id || null : null,
      language: state.story ? state.story.language || null : null,
      status: state.story ? state.story.status || 'ready' : null,
      time: round2(now()),
      duration: round2(state.duration),
      estimated: state.estimated,
      playing: state.wantPlay,
      waiting: state.waiting,
      captions: state.captionsOn,
      chapter: (scene && scene.chapter) || (seg && seg.chapter) || '',
    };
  }

  // About once a second while playing, plus on every jump.
  function reportTime(t) {
    const r = state.reported;
    const stamp = performance.now();
    if (state.scrubbing || Math.abs(t - r.t) < 0.05) return;
    if (stamp - r.at < 1000 && Math.abs(t - r.t) < 2) return;
    r.at = stamp;
    r.t = t;
    emit('timeupdate', { time: round2(t), duration: round2(state.duration), estimated: state.estimated });
  }

  function setLanguage(code) {
    const lang = ((state.story && state.story.languages) || []).find((l) => l.code === code);
    if (lang) switchLanguage(lang);
  }

  function command(msg) {
    switch (msg.command) {
      case 'play': play(); break;
      case 'pause': pause(); break;
      case 'toggle': togglePlay(); break;
      case 'seek': if (Number.isFinite(Number(msg.time))) seek(Number(msg.time)); break;
      case 'load': if (typeof msg.url === 'string') loadStory(msg.url, { play: Boolean(msg.autoplay) }); break;
      case 'captions': setCaptions(Boolean(msg.on)); break;
      case 'language': setLanguage(msg.code); break;
      case 'state': emit('state', snapshot()); break;
      default: break;
    }
  }

  function closeStory() {
    pause();
    emit('close', { time: round2(now()) });
  }

  // ---------------------------------------------------------------- wiring

  function initScrubber() {
    const scrub = $('scrub');
    const timeAt = (x) => {
      const box = $('scrubTrack').getBoundingClientRect();
      return clamp((x - box.left) / Math.max(1, box.width), 0, 1) * state.duration;
    };
    scrub.addEventListener('pointerdown', (e) => {
      if (!state.story || e.button > 0) return;
      e.preventDefault();
      scrub.setPointerCapture(e.pointerId);
      state.scrubT = timeAt(e.clientX);
      state.scrubbing = true;
      internalPause();
      phone.classList.add('is-scrubbing');
      render(true);
    });
    scrub.addEventListener('pointermove', (e) => {
      if (!state.scrubbing) return;
      state.scrubT = timeAt(e.clientX);
      render(true);
    });
    const release = () => {
      if (!state.scrubbing) return;
      state.scrubbing = false;
      phone.classList.remove('is-scrubbing');
      seek(state.scrubT);
    };
    scrub.addEventListener('pointerup', release);
    scrub.addEventListener('pointercancel', release);
    scrub.addEventListener('lostpointercapture', release);
    scrub.addEventListener('keydown', (e) => {
      const step = { ArrowLeft: -5, ArrowRight: 5, PageDown: -30, PageUp: 30 }[e.key];
      if (step) { e.preventDefault(); e.stopPropagation(); seek(now() + step); }
      else if (e.key === 'Home') { e.preventDefault(); seek(0); }
      else if (e.key === 'End') { e.preventDefault(); seek(state.duration); }
    });
  }

  function initControls() {
    $('closeBtn').innerHTML = ICONS.close;
    $('sheetClose').innerHTML = ICONS.close;
    $('ccBtn').innerHTML = ICONS.captions;
    $('langGlobe').innerHTML = ICONS.globe;
    $('langChev').innerHTML = ICONS.chevron;
    $('startIcon').innerHTML = ICONS.sparkle;
    $('startBtnIcon').innerHTML = ICONS.play;
    $('endIcon').innerHTML = ICONS.check;
    $('replayIcon').innerHTML = ICONS.replay;
    if (params.get('close') === '0') $('closeBtn').hidden = true;
    syncPlayState();

    $('playBtn').addEventListener('click', togglePlay);
    $('startBtn').addEventListener('click', play);
    $('replayBtn').addEventListener('click', () => { state.pos = { seg: 0, local: 0 }; play(); });
    $('endCloseBtn').addEventListener('click', () => {
      if (EMBED) closeStory();
      pause();
      goTo(0, 0);
      showStart();
    });
    $('closeBtn').addEventListener('click', () => {
      if (EMBED) closeStory();
      else pause();
      if (state.story) showEndCard();
    });
    $('ccBtn').addEventListener('click', () => setCaptions(!state.captionsOn));
    $('langBtn').addEventListener('click', openSheet);
    $('sheetClose').addEventListener('click', closeSheet);
    $('sheetBackdrop').addEventListener('click', closeSheet);

    audio.addEventListener('pause', () => {
      if (state.expectPause > 0) state.expectPause--;
      else if (!audio.ended && state.wantPlay) state.wantPlay = false;   // paused by the system (call, headphones)
      syncPlayState();
    });
    audio.addEventListener('play', () => {
      if (state.expectPlay > 0) state.expectPlay--;
      else if (!state.wantPlay) {   // started by the system (lock screen, headset button)
        state.wantPlay = true;
        hideOverlays();
      }
      syncPlayState();
    });
    audio.addEventListener('playing', syncPlayState);
    audio.addEventListener('ended', onSegmentEnded);
    initScrubber();

    document.addEventListener('keydown', (e) => {
      if (e.target.closest('input, textarea, select, .builder, .scrub') || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === ' ' || e.key === 'k') { e.preventDefault(); togglePlay(); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); seek(now() + 5); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); seek(now() - 5); }
      else if (e.key === 'Escape') closeSheet();
    });

    window.addEventListener('message', (e) => {
      if (window.parent === window || e.source !== window.parent) return;
      if (PARENT_ORIGIN !== '*' && e.origin !== PARENT_ORIGIN) return;
      let msg = e.data;
      if (typeof msg === 'string') {
        try { msg = JSON.parse(msg); } catch (err) { return; }
      }
      if (msg && msg.target === 'credit-story-player') command(msg);
    });

    $('devForm').addEventListener('submit', (e) => {
      e.preventDefault();
      const url = $('devUrl').value.trim();
      if (!url) return;
      const next = new URL(location.href);
      next.searchParams.set('story', url);
      history.replaceState(null, '', next);
      loadStory(url);
    });
    $('devReload').addEventListener('click', () => {
      if (!state.storyUrl) return;
      loadStory($('devUrl').value.trim() || state.storyUrl, {
        seekTo: now(), play: state.wantPlay, stayPaused: true, captions: state.captionsOn,
      });
    });
    let resizeTimer;
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        unmountActive(false);
        render(true);
      }, 150);
    });
  }

  initControls();
  requestAnimationFrame(tick);

  // Public API for the builder and for host apps (Flutter: controller.runJavaScript('StoryPlayer.pause()')).
  window.StoryPlayer = {
    load: (url, opts) => loadStory(url, opts),
    play,
    pause,
    toggle: togglePlay,
    seek,
    setCaptions: (on) => setCaptions(Boolean(on)),
    setLanguage,
    busy,
    cancelBusy,
    get story() { return state.story; },
    get state() { return snapshot(); },
  };

  const storyParam = params.get('story');
  const startTime = parseFloat(params.get('t'));
  const autoplay = params.get('autoplay') === '1';
  emit('ready', { embed: EMBED });
  if (storyParam || !EMBED) {
    loadStory(storyParam || CONFIG.storyUrl, Number.isFinite(startTime)
      ? { seekTo: startTime, stayPaused: !autoplay, play: autoplay }
      : { play: autoplay });
  }
  // Embedded without ?story=: the loader stays up until the host sends a story (load command).
})();
