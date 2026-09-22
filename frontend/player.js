// Story player: loads a story JSON, plays its narration audio and renders the scene
// timeline in sync with it. Every animation is driven by audio.currentTime (not by wall
// clock), so pausing, seeking and scrubbing always keep the visuals locked to the voice.
(() => {
  'use strict';

  const CONFIG = Object.assign({ storyUrl: 'data/story.hi.json' }, window.STORY_PLAYER_CONFIG);
  const ICONS = window.ICONS || {};
  const params = new URLSearchParams(location.search);

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
    scenes: [],
    duration: 0,
    active: null,
    activeIdx: -1,
    lastT: -1,
    captionsOn: true,
    caption: null,
    captionWords: [],
    segments: [],
    loadToken: 0,
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
    holder.innerHTML = ICONS[name] || ICONS.info || '';
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

    to(node, keyframes, at, duration = 0.6, easing = EASE) {
      if (!node || !Number.isFinite(at)) return;
      const fill = this.seen.has(node) ? 'forwards' : 'both';
      this.seen.add(node);
      const anim = node.animate(keyframes, {
        duration: Math.max(1, duration * 1000),
        delay: Math.max(0, at) * 1000,
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
    const min = p.min ?? 300;
    const max = p.max ?? 900;
    const score = clamp(Math.round(p.score ?? min), min, max);
    const from = clamp(p.count_from ?? min, min, max);
    const bands = Array.isArray(p.bands) && p.bands.length ? p.bands : DEFAULT_BANDS;
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
    tl.to(mark, POP, 0.05, 0.6);
    animateHead(tl, head, 0.15);
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

  // ---------------------------------------------------------------- engine

  function sceneIndexAt(t) {
    let idx = 0;
    for (let i = 0; i < state.scenes.length; i++) {
      if (state.scenes[i].start <= t) idx = i;
    }
    return state.scenes.length ? idx : -1;
  }

  function unmountActive(animate) {
    const prev = state.active;
    state.active = null;
    state.activeIdx = -1;
    if (!prev) return;
    if (!animate) {
      prev.tl.destroy();
      prev.root.remove();
      return;
    }
    prev.root.classList.add('is-leaving');
    setTimeout(() => { prev.tl.destroy(); prev.root.remove(); }, 400);
  }

  function mountScene(idx) {
    unmountActive(true);
    if (idx < 0) return;
    const scene = state.scenes[idx];
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
    state.activeIdx = idx;
    phone.dataset.theme = scene.theme || 'blue';
    $('chapterLabel').textContent = scene.chapter || '';
    highlightDevScene(idx);
  }

  function render(force) {
    if (!state.story) return;
    const t = audio.currentTime || 0;
    if (!force && t === state.lastT) return;
    state.lastT = t;
    const idx = sceneIndexAt(t);
    if (idx !== state.activeIdx) mountScene(idx);
    if (state.active) state.active.tl.seek(t - state.active.scene.start);
    updateProgress(t);
    updateCaptions(t);
    updateReadout(t);
  }

  function tick() {
    render(false);
    requestAnimationFrame(tick);
  }

  // ---------------------------------------------------------------- chrome

  function buildSegments() {
    const wrap = $('segments');
    wrap.replaceChildren();
    state.segments = state.scenes.map((scene) => {
      const seg = el('div', 'seg');
      seg.title = scene.chapter || scene.id || '';
      const fill = el('div', 'seg-fill');
      seg.append(fill);
      wrap.append(seg);
      return fill;
    });
  }

  function updateProgress(t) {
    state.scenes.forEach((scene, i) => {
      const f = clamp((t - scene.start) / Math.max(0.001, scene.end - scene.start), 0, 1);
      state.segments[i].style.width = `${(f * 100).toFixed(2)}%`;
    });
    $('timeLabel').textContent = `${clock(t)} / ${clock(state.duration)}`;
  }

  function updateCaptions(t) {
    const box = $('captions');
    const caps = (state.story && state.story.captions) || [];
    const current = state.captionsOn
      ? caps.find((c) => t >= c.start - 0.15 && t <= c.end + 0.5) || null
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
    state.scenes.forEach((scene, i) => {
      const li = el('li');
      const btn = el('button');
      btn.type = 'button';
      btn.dataset.idx = i;
      btn.append(el('span', '', scene.id || scene.type), el('span', '', `${scene.start.toFixed(1)}–${scene.end.toFixed(1)}s`));
      btn.addEventListener('click', () => seek(scene.start));
      li.append(btn);
      list.append(li);
    });
  }

  function highlightDevScene(idx) {
    document.querySelectorAll('#devScenes button').forEach((b) => b.classList.toggle('is-active', Number(b.dataset.idx) === idx));
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

  function syncPlayButton() {
    const playing = !audio.paused && !audio.ended;
    phone.classList.toggle('is-paused', !playing);
    const btn = $('playBtn');
    btn.innerHTML = playing ? ICONS.pause : ICONS.play;
    btn.setAttribute('aria-label', playing ? 'Pause' : 'Play');
  }

  function show(id, visible) {
    $(id).hidden = !visible;
  }

  function hideOverlays() {
    ['loader', 'startScreen', 'endCard', 'errorBox'].forEach((id) => show(id, false));
    closeSheet();
  }

  function play() {
    hideOverlays();
    if (audio.ended || audio.currentTime >= state.duration - 0.05) audio.currentTime = 0;
    audio.play().catch(() => showStart());
  }

  function togglePlay() {
    if (!state.story) return;
    if (audio.paused) play();
    else audio.pause();
  }

  function seek(t) {
    if (!state.story) return;
    $('endCard').hidden = true;
    audio.currentTime = clamp(t, 0, Math.max(0, state.duration - 0.01));
    render(true);
  }

  function showStart() {
    hideOverlays();
    const story = state.story || {};
    const intro = story.intro || {};
    const title = $('startTitle');
    title.replaceChildren(...rich('span', '', intro.title || story.title || '').childNodes);
    $('startSub').textContent = intro.subtitle || '';
    const lang = (story.languages || []).find((l) => l.code === story.language);
    const meta = $('startMeta');
    meta.replaceChildren();
    [clock(state.duration), lang ? lang.label : story.language, `${state.scenes.length} chapters`]
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
    hideOverlays();
    $('errorText').textContent = err && err.message ? err.message : String(err);
    const hint = $('errorHint');
    hint.replaceChildren();
    if (location.protocol === 'file:') {
      hint.append('This page was opened from disk, so the browser blocks loading the JSON. Serve the folder instead, e.g. ',
        el('code', '', 'python3 -m http.server 8080 --directory frontend'), ' and open ', el('code', '', 'http://localhost:8080'), '.');
    } else if (/HTTP 404/.test(String(err && err.message)) && /\/stories\//.test(String(url))) {
      hint.append('This video has expired. Generated videos are only kept for a limited time (24 hours by default), so generate it again from the CRIF report tab.');
    } else {
      hint.append('Tried to load ', el('code', '', url), '. Fix the file, pick another one in the Story file tab, or generate a new story.');
    }
    show('errorBox', true);
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

  function switchLanguage(lang) {
    closeSheet();
    if (!lang.src || lang.code === state.story.language) return;
    const wasPlaying = !audio.paused;
    const sceneId = state.active && state.active.scene.id;
    audio.pause();
    loadStory(new URL(lang.src, state.storyUrl).href, {
      resumeSceneId: sceneId, play: wasPlaying, stayPaused: true, captions: state.captionsOn,
    });
  }

  // ---------------------------------------------------------------- loading

  function validate(story) {
    const problems = [];
    if (!story || typeof story !== 'object' || Array.isArray(story)) throw new Error('The story JSON must be an object.');
    if (!story.audio || typeof story.audio.src !== 'string' || !story.audio.src) problems.push('audio.src is required (path or URL of the narration audio)');
    if (!Array.isArray(story.scenes) || !story.scenes.length) {
      problems.push('scenes must be a non-empty array');
    } else {
      story.scenes.forEach((scene, i) => {
        const where = `scenes[${i}]${scene && scene.id ? ` ("${scene.id}")` : ''}`;
        if (!scene || typeof scene !== 'object') { problems.push(`${where} must be an object`); return; }
        if (typeof scene.type !== 'string') problems.push(`${where}.type is required`);
        if (!Number.isFinite(scene.start) || !Number.isFinite(scene.end)) problems.push(`${where}.start and .end must be numbers (seconds)`);
        else if (scene.end <= scene.start) problems.push(`${where}.end must be greater than .start`);
        if (scene.beats != null && !Array.isArray(scene.beats)) problems.push(`${where}.beats must be an array`);
      });
    }
    if (story.captions != null && !Array.isArray(story.captions)) problems.push('captions must be an array');
    if (problems.length) throw new Error(`The story JSON has ${problems.length} problem(s):\n• ${problems.join('\n• ')}`);
  }

  // The narration is fetched into a Blob URL so seeking works on any static server
  // (python -m http.server has no Range support, which makes streamed audio unseekable).
  // Falls back to streaming the URL directly if the fetch is blocked, e.g. by CORS.
  async function loadAudio(src) {
    let playable = src;
    try {
      const res = await fetch(src, { cache: 'no-cache' });
      if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText} while fetching the audio\n${src}`);
      playable = URL.createObjectURL(await res.blob());
    } catch (err) {
      if (err instanceof TypeError) console.warn('Audio fetch failed, streaming it directly instead', err);
      else throw err;
    }
    await new Promise((resolve, reject) => {
      const done = (fn) => () => {
        audio.removeEventListener('loadedmetadata', onOk);
        audio.removeEventListener('error', onErr);
        fn();
      };
      const onOk = done(resolve);
      const onErr = done(() => reject(new Error(`Could not play the audio file:\n${src}`)));
      audio.addEventListener('loadedmetadata', onOk);
      audio.addEventListener('error', onErr);
      const previous = audio.src;
      audio.src = playable;
      audio.load();
      if (previous.startsWith('blob:')) URL.revokeObjectURL(previous);
    });
  }

  async function loadStory(url, opts = {}) {
    const token = ++state.loadToken;
    hideOverlays();
    $('loaderTitle').textContent = 'Preparing your credit story';
    show('loader', true);
    audio.pause();

    let story;
    let absUrl;
    try {
      absUrl = new URL(url, location.href).href;
      const res = await fetch(absUrl, { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText} while fetching\n${absUrl}`);
      const text = await res.text();
      try {
        story = JSON.parse(text);
      } catch (err) {
        throw new Error(`Invalid JSON in ${absUrl}\n${err.message}`);
      }
      validate(story);
      state.storyUrl = absUrl;
      await loadAudio(resolveUrl(story.audio.src));
    } catch (err) {
      if (token === state.loadToken) showError(err, absUrl || url);
      return;
    }
    if (token !== state.loadToken) return;

    unmountActive(false);
    state.story = story;
    state.scenes = story.scenes.slice().sort((a, b) => a.start - b.start);
    state.duration = Number.isFinite(audio.duration) ? audio.duration : Number(story.audio.duration) || 0;
    state.lastT = -1;
    state.caption = undefined;
    $('captions').lang = story.language || '';
    $('devUrl').value = url;
    buildBrand();
    buildSegments();
    buildDevScenes();
    buildLanguages();
    if (opts.captions != null) setCaptions(opts.captions);
    else if (story.player && typeof story.player.captions === 'boolean') setCaptions(story.player.captions);

    let startAt = Number.isFinite(opts.seekTo) ? opts.seekTo : 0;
    if (opts.resumeSceneId) {
      const match = state.scenes.find((s) => s.id === opts.resumeSceneId);
      if (match) startAt = match.start;
    }
    audio.currentTime = clamp(startAt, 0, Math.max(0, state.duration - 0.01));
    render(true);
    show('loader', false);
    window.dispatchEvent(new CustomEvent('story:loaded', { detail: story }));

    if (opts.play) play();
    else if (!opts.stayPaused) showStart();
  }

  // Shows the loader while something else (the builder) prepares a story.
  function busy(message) {
    state.loadToken++;
    audio.pause();
    hideOverlays();
    $('loaderTitle').textContent = message || 'Preparing your credit story';
    show('loader', true);
  }

  function cancelBusy() {
    show('loader', false);
    if (state.story) showStart();
  }

  // ---------------------------------------------------------------- wiring

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
    syncPlayButton();

    $('playBtn').addEventListener('click', togglePlay);
    $('startBtn').addEventListener('click', play);
    $('replayBtn').addEventListener('click', () => { audio.currentTime = 0; play(); });
    $('endCloseBtn').addEventListener('click', () => { audio.pause(); audio.currentTime = 0; render(true); showStart(); });
    $('closeBtn').addEventListener('click', () => { audio.pause(); showEndCard(); });
    $('ccBtn').addEventListener('click', () => setCaptions(!state.captionsOn));
    $('langBtn').addEventListener('click', openSheet);
    $('sheetClose').addEventListener('click', closeSheet);
    $('sheetBackdrop').addEventListener('click', closeSheet);

    audio.addEventListener('play', syncPlayButton);
    audio.addEventListener('pause', syncPlayButton);
    audio.addEventListener('ended', () => { syncPlayButton(); showEndCard(); });

    // Chapter segments double as the seek bar: the pointer position inside a segment
    // maps to the same fraction of that chapter.
    const segments = $('segments');
    const seekFromEvent = (e) => {
      const segs = [...segments.children];
      if (!segs.length) return;
      let i = segs.findIndex((seg) => e.clientX <= seg.getBoundingClientRect().right);
      if (i < 0) i = segs.length - 1;
      const box = segs[i].getBoundingClientRect();
      const scene = state.scenes[i];
      seek(scene.start + clamp((e.clientX - box.left) / box.width, 0, 1) * (scene.end - scene.start));
    };
    segments.addEventListener('pointerdown', (e) => {
      if (!state.story) return;
      segments.setPointerCapture(e.pointerId);
      seekFromEvent(e);
      const move = (ev) => seekFromEvent(ev);
      const up = () => {
        segments.removeEventListener('pointermove', move);
        segments.removeEventListener('pointerup', up);
        segments.removeEventListener('pointercancel', up);
      };
      segments.addEventListener('pointermove', move);
      segments.addEventListener('pointerup', up);
      segments.addEventListener('pointercancel', up);
    });

    document.addEventListener('keydown', (e) => {
      if (e.target.closest('input, textarea, select, .builder') || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === ' ' || e.key === 'k') { e.preventDefault(); togglePlay(); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); seek(audio.currentTime + 5); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); seek(audio.currentTime - 5); }
      else if (e.key === 'Escape') closeSheet();
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
        seekTo: audio.currentTime, play: !audio.paused, stayPaused: true, captions: state.captionsOn,
      });
    });
    let resizeTimer;
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        const idx = state.activeIdx;
        unmountActive(false);
        if (idx >= 0) { mountScene(idx); render(true); }
      }, 150);
    });
  }

  initControls();
  requestAnimationFrame(tick);

  // Public API for embedding pages (the builder uses it to play freshly generated stories).
  window.StoryPlayer = {
    load: (url, opts) => loadStory(url, opts),
    busy,
    cancelBusy,
    get story() { return state.story; },
  };

  const startTime = parseFloat(params.get('t'));
  loadStory(params.get('story') || CONFIG.storyUrl, Number.isFinite(startTime) ? { seekTo: startTime, stayPaused: true } : {});
})();
