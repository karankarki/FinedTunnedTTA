// Story builder: collects a customer's credit details (form or JSON), asks the backend to
// generate the narrated story (POST /api/story), then plays it in the player next to it.
(() => {
  'use strict';

  if (document.documentElement.hasAttribute('data-embed')) return;   // embedded: player only

  const CONFIG = Object.assign({ apiBase: '' }, window.STORY_PLAYER_CONFIG);
  const $ = (id) => document.getElementById(id);

  const thisMonth = () => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
  };

  const SAMPLE = {
    customer_name: 'Karan',
    customer_name_hi: 'करण',
    credit_score: 776,
    score_bureau: 'CIBIL',
    report_month: thisMonth(),
    on_time_repayment_pct: 100,
    missed_payments_count: 0,
    active_credit_cards: 0,
    credit_utilization_pct: 0,
    recent_inquiries: 0,
    languages: ['hi', 'en'],
    voice_speed: 0.92,
  };

  const LANGUAGES = [{ code: 'hi', label: 'Hindi' }, { code: 'en', label: 'English' }];
  // Same bands as app/story_engine.py, so the preview label matches the video.
  const BANDS = [
    { to: 549, label: 'Poor', tone: 'bad' },
    { to: 649, label: 'Needs work', tone: 'bad' },
    { to: 724, label: 'Fair', tone: 'warn' },
    { to: 774, label: 'Good', tone: 'good' },
    { to: 900, label: 'Excellent', tone: 'good' },
  ];

  // One spec drives the form, the JSON sync and validation. Ranges match the API.
  const FIELDS = [
    { section: 'Customer' },
    { key: 'customer_name', label: 'Customer name', type: 'text', required: true, maxLength: 40, placeholder: 'e.g. Karan', full: true },
    { key: 'customer_name_hi', label: 'Name in Hindi script', type: 'text', maxLength: 40, placeholder: 'e.g. करण', full: true,
      hint: 'Spoken in the Hindi narration. Leave empty to use the name above.' },
    { section: 'Credit profile' },
    { key: 'credit_score', label: 'Credit score', type: 'score', min: 300, max: 900, step: 1, required: true, full: true },
    { key: 'score_bureau', label: 'Bureau', type: 'select', options: ['CIBIL', 'Experian', 'Equifax', 'CRIF High Mark'] },
    { key: 'report_month', label: 'Report month', type: 'month' },
    { key: 'on_time_repayment_pct', label: 'On-time payments %', type: 'number', min: 0, max: 100, step: 0.1, required: true },
    { key: 'missed_payments_count', label: 'Missed payments', type: 'number', min: 0, max: 36, step: 1, required: true, hint: 'Last 6 months' },
    { key: 'active_credit_cards', label: 'Active credit cards', type: 'number', min: 0, max: 30, step: 1, required: true },
    { key: 'credit_utilization_pct', label: 'Card utilisation %', type: 'number', min: 0, max: 100, step: 0.1, required: true },
    { key: 'recent_inquiries', label: 'New applications', type: 'number', min: 0, max: 50, step: 1, required: true, hint: 'Last 6 months' },
    { section: 'Narration' },
    { key: 'languages', label: 'Languages', type: 'languages', full: true },
    { key: 'voice_speed', label: 'Voice speed', type: 'range', min: 0.8, max: 1.1, step: 0.01, full: true },
  ];
  const INPUTS = FIELDS.filter((f) => f.key);

  const state = { dirty: false, busy: false, jsonTimer: 0, mode: 'crif', crif: null, readyIn: '' };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }

  function api(path) {
    return new URL(path, CONFIG.apiBase || location.origin).href;
  }

  // ---------------------------------------------------------------- form

  function buildForm() {
    const form = $('detailsForm');
    let group = null;
    FIELDS.forEach((f) => {
      if (f.section) {
        form.append(el('h2', 'b-section', f.section));
        group = el('div', 'b-grid');
        form.append(group);
        return;
      }
      const wrap = el('div', `b-field${f.full ? ' is-full' : ''}`);
      wrap.dataset.key = f.key;
      const id = `f_${f.key}`;
      const label = el('label', 'b-label', f.label);
      label.htmlFor = id;
      wrap.append(label);

      if (f.type === 'select') {
        const select = el('select', 'b-input');
        select.id = id;
        f.options.forEach((o) => select.append(new Option(o, o)));
        wrap.append(select);
      } else if (f.type === 'languages') {
        label.removeAttribute('for');
        const row = el('div', 'b-langs');
        LANGUAGES.forEach((lang) => {
          const opt = el('label', 'b-check');
          const box = el('input');
          box.type = 'checkbox';
          box.value = lang.code;
          box.name = 'languages';
          opt.append(box, el('span', '', lang.label));
          row.append(opt);
        });
        const start = el('select', 'b-input b-start');
        start.id = 'f_language_start';
        start.setAttribute('aria-label', 'Plays first');
        row.append(el('span', 'b-start-label', 'Plays first'), start);
        wrap.append(row);
      } else if (f.type === 'range') {
        const row = el('div', 'b-range');
        const input = el('input');
        Object.assign(input, { id, type: 'range', min: f.min, max: f.max, step: f.step });
        row.append(input, el('output', 'b-range-value'));
        wrap.append(row);
      } else if (f.type === 'score') {
        const row = el('div', 'b-score');
        const input = el('input', 'b-input');
        Object.assign(input, { id, type: 'number', min: f.min, max: f.max, step: f.step, inputMode: 'numeric' });
        const slider = el('input', 'b-score-slider');
        Object.assign(slider, { type: 'range', min: f.min, max: f.max, step: f.step });
        slider.setAttribute('aria-label', 'Credit score slider');
        row.append(input, el('span', 'b-band'), slider);
        wrap.append(row);
        slider.addEventListener('input', () => { input.value = slider.value; onFormChange(); });
      } else {
        const input = el('input', 'b-input');
        input.id = id;
        input.type = f.type;
        if (f.type === 'number') Object.assign(input, { min: f.min, max: f.max, step: f.step, inputMode: 'decimal' });
        if (f.maxLength) input.maxLength = f.maxLength;
        if (f.placeholder) input.placeholder = f.placeholder;
        wrap.append(input);
      }
      if (f.hint) wrap.append(el('p', 'b-hint', f.hint));
      wrap.append(el('p', 'b-field-error'));
      group.append(wrap);
    });
    form.addEventListener('input', () => { state.dirty = true; onFormChange(); });
    form.addEventListener('change', () => { state.dirty = true; onFormChange(); });
    form.addEventListener('submit', (e) => { e.preventDefault(); generate(); });
  }

  // Keeps derived bits of the form in step: score band, slider, speed label, utilisation, start language.
  function onFormChange() {
    const score = parseInt($('f_credit_score').value, 10);
    const band = BANDS.find((b) => score <= b.to);
    const form = $('detailsForm');
    const bandEl = form.querySelector('.b-band');
    bandEl.textContent = Number.isFinite(score) && score >= 300 && score <= 900 && band ? band.label : '';
    bandEl.className = `b-band${band ? ` tone-${band.tone}` : ''}`;
    if (Number.isFinite(score)) form.querySelector('.b-score-slider').value = score;

    const speed = parseFloat($('f_voice_speed').value);
    form.querySelector('.b-range-value').textContent = `${speed.toFixed(2)}×`;

    const noCards = parseInt($('f_active_credit_cards').value, 10) === 0;
    const util = $('f_credit_utilization_pct');
    util.disabled = noCards;
    util.closest('.b-field').classList.toggle('is-disabled', noCards);

    const checked = [...document.querySelectorAll('input[name="languages"]:checked')].map((b) => b.value);
    const start = $('f_language_start');
    const previous = start.value;
    start.replaceChildren(...LANGUAGES.filter((l) => checked.includes(l.code)).map((l) => new Option(l.label, l.code)));
    if (checked.includes(previous)) start.value = previous;
    start.disabled = checked.length < 2;
  }

  function readForm() {
    const data = {};
    INPUTS.forEach((f) => {
      if (f.type === 'languages') {
        const checked = [...document.querySelectorAll('input[name="languages"]:checked')].map((b) => b.value);
        const first = $('f_language_start').value;
        data.languages = checked.includes(first) ? [first, ...checked.filter((c) => c !== first)] : checked;
        return;
      }
      const raw = $(`f_${f.key}`).value.trim();
      if (f.type === 'number' || f.type === 'score' || f.type === 'range') {
        data[f.key] = raw === '' ? null : Number(raw);
      } else if (raw !== '' || f.required) {
        data[f.key] = raw;
      }
    });
    if (data.active_credit_cards === 0) data.credit_utilization_pct = 0;
    return data;
  }

  // What a field falls back to when the given data leaves it out (matches the API defaults;
  // name and score are required, so they fall back to empty and get flagged).
  const defaults = () => ({
    customer_name: '', customer_name_hi: '', credit_score: '', score_bureau: 'CIBIL', report_month: thisMonth(),
    on_time_repayment_pct: 100, missed_payments_count: 0, active_credit_cards: 0, credit_utilization_pct: 0,
    recent_inquiries: 0, languages: ['hi', 'en'], voice_speed: 0.92,
  });

  // Replaces the whole form with `data`; fields it leaves out go back to their defaults so
  // nothing from a previous customer carries over.
  function writeForm(input) {
    const data = { ...defaults(), ...input };
    INPUTS.forEach((f) => {
      const value = data[f.key];
      if (f.type === 'languages') {
        const langs = Array.isArray(value) ? value : [];
        document.querySelectorAll('input[name="languages"]').forEach((box) => { box.checked = langs.includes(box.value); });
        onFormChange();
        if (langs[0]) $('f_language_start').value = langs[0];
        return;
      }
      $(`f_${f.key}`).value = value == null ? '' : value;
    });
    onFormChange();
    clearErrors();
  }

  function validate(data) {
    const errors = {};
    INPUTS.forEach((f) => {
      const v = data[f.key];
      if (f.type === 'languages') {
        if (!Array.isArray(v) || !v.length) errors[f.key] = 'Pick at least one language';
        else if (v.some((c) => !LANGUAGES.some((l) => l.code === c))) errors[f.key] = 'Supported languages: hi, en';
        return;
      }
      if (f.required && (v == null || v === '')) { errors[f.key] = 'Required'; return; }
      if (v == null || v === '') return;
      if (f.type === 'number' || f.type === 'score' || f.type === 'range') {
        if (typeof v !== 'number' || !Number.isFinite(v)) errors[f.key] = 'Must be a number';
        else if (v < f.min || v > f.max) errors[f.key] = `Must be between ${f.min} and ${f.max}`;
        else if (f.step === 1 && !Number.isInteger(v)) errors[f.key] = 'Must be a whole number';
      } else if (f.type === 'month' && !/^\d{4}-(0[1-9]|1[0-2])$/.test(v)) {
        errors[f.key] = 'Use YYYY-MM';
      } else if (f.type === 'select' && !f.options.includes(v)) {
        errors[f.key] = `One of: ${f.options.join(', ')}`;
      } else if (typeof v !== 'string') {
        errors[f.key] = 'Must be text';
      }
    });
    return errors;
  }

  function showFieldErrors(errors) {
    document.querySelectorAll('#detailsForm .b-field').forEach((wrap) => {
      const msg = errors[wrap.dataset.key] || '';
      wrap.classList.toggle('has-error', Boolean(msg));
      wrap.querySelector('.b-field-error').textContent = msg;
    });
  }

  function clearErrors() {
    showFieldErrors({});
    showError('');
  }

  function showError(message) {
    const box = $('builderError');
    box.textContent = message;
    box.hidden = !message;
  }

  // ---------------------------------------------------------------- JSON tab

  function refreshJson() {
    $('jsonInput').value = JSON.stringify(readForm(), null, 2);
    setJsonStatus('Edits here update the form. This is the exact body sent to POST /api/story.', '');
  }

  function setJsonStatus(text, kind) {
    const status = $('jsonStatus');
    status.textContent = text;
    status.className = `b-json-status${kind ? ` is-${kind}` : ''}`;
  }

  function onJsonInput() {
    clearTimeout(state.jsonTimer);
    state.jsonTimer = setTimeout(() => {
      let data;
      try {
        data = JSON.parse($('jsonInput').value);
      } catch (err) {
        setJsonStatus(`Invalid JSON: ${err.message}`, 'error');
        return;
      }
      if (!data || typeof data !== 'object' || Array.isArray(data)) {
        setJsonStatus('The JSON must be an object, like the sample.', 'error');
        return;
      }
      const known = new Set(INPUTS.map((f) => f.key));
      const unknown = Object.keys(data).filter((k) => !known.has(k));
      writeForm({ ...data });
      state.dirty = true;
      const errors = validate(readFormFromJson(data));
      const problems = Object.entries(errors).map(([k, m]) => `${k}: ${m}`);
      if (unknown.length) problems.push(`Ignored unknown field${unknown.length > 1 ? 's' : ''}: ${unknown.join(', ')}`);
      setJsonStatus(problems.length ? problems.join(' · ') : 'Looks good. Press Generate video.', problems.length ? 'warn' : 'ok');
    }, 250);
  }

  // Validation of pasted JSON uses its own values (merged over the form), not re-read inputs.
  function readFormFromJson(data) {
    return { ...readForm(), ...data };
  }

  function selectTab(name) {
    ['Crif', 'Details', 'Json', 'File'].forEach((tab) => {
      const on = tab === name;
      $(`tab${tab}`).setAttribute('aria-selected', String(on));
      $(`panel${tab}`).hidden = !on;
    });
    if (name === 'Json') refreshJson();
    $('sampleBtn').hidden = !(name === 'Details' || name === 'Json');
    // The File tab keeps whichever kind of story was being prepared.
    if (name === 'Crif') state.mode = 'crif';
    else if (name !== 'File') state.mode = 'quick';
  }

  // ---------------------------------------------------------------- CRIF report

  // Finds the individual report inside whatever level of the CRIF response was given
  // (mirrors app/crif_report.find_report). Read locally only to show a summary.
  function findCrifReport(data) {
    let node = data;
    for (const key of ['data', 'crifReport', 'INDV-REPORT-FILE']) if (node && typeof node === 'object' && key in node) node = node[key];
    if (node && Array.isArray(node['INDV-REPORTS'])) node = node['INDV-REPORTS'][0];
    if (node && node['INDV-REPORT']) node = node['INDV-REPORT'];
    return node && node.SCORES && node.RESPONSES ? node : null;
  }

  function parseDate(value) {
    const m = /^(\d{2})-(\d{2})-(\d{4})$/.exec(String(value || '').trim());
    return m ? new Date(Number(m[3]), Number(m[2]) - 1, Number(m[1])) : null;
  }

  function crifSummary(report) {
    const names = ((report['PERSONAL-INFO-VARIATION'] || {})['NAME-VARIATIONS'] || [])
      .filter((v) => /^[A-Za-z][A-Za-z .]*$/.test(String(v.VALUE || '').trim()))
      .map((v, i) => ({ i, date: parseDate(v['REPORTED-DATE']) || new Date(0), name: String(v.VALUE).trim() }));
    const latest = names.reduce((a, b) => (b.date > a.date ? b : a), names[0] || null);
    const first = latest ? latest.name.split(/\s+/)[0] : '';
    const accounts = report.RESPONSES || [];
    const loans = accounts.map((r) => r['LOAN-DETAILS'] || {});
    const reported = loans.map((l) => parseDate(l['DATE-REPORTED'])).filter(Boolean);
    const asOf = reported.length ? new Date(Math.max(...reported)) : null;
    const derived = ((report['ACCOUNTS-SUMMARY'] || {})['DERIVED-ATTRIBUTES']) || {};
    return {
      name: first ? first[0].toUpperCase() + first.slice(1).toLowerCase() : '',
      score: ((report.SCORES || [])[0] || {})['SCORE-VALUE'] || '—',
      accounts: accounts.length,
      active: loans.filter((l) => String(l['ACCOUNT-STATUS'] || '').toLowerCase() === 'active').length,
      enquiries: derived['INQUIRIES-IN-LAST-SIX-MONTHS'] ?? '—',
      asOf: asOf ? asOf.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : '—',
    };
  }

  function loadCrifText(text, source) {
    const err = $('crifError');
    err.textContent = '';
    let data;
    try {
      data = JSON.parse(text);
    } catch (e) {
      state.crif = null;
      showCrifSummary(null);
      err.textContent = `That isn't valid JSON (${e.message}).`;
      return;
    }
    const report = findCrifReport(data);
    if (!report) {
      state.crif = null;
      showCrifSummary(null);
      err.textContent = "This JSON doesn't look like a CRIF High Mark report (no INDV-REPORT with SCORES and RESPONSES).";
      return;
    }
    state.crif = data;
    const summary = crifSummary(report);
    showCrifSummary(summary, source);
    const nameInput = $('c_name');
    nameInput.placeholder = summary.name || 'Name from the report';
    showError('');
  }

  function showCrifSummary(s, source) {
    const box = $('crifSummary');
    box.replaceChildren();
    box.hidden = !s;
    if (!s) return;
    const head = el('div', 'b-report-head');
    head.append(el('span', 'b-report-name', s.name || 'Customer'), el('span', 'b-report-score', `Score ${s.score}`));
    const facts = el('div', 'b-report-facts');
    [['', s.accounts, ' accounts'], ['', s.active, ' active'], ['', s.enquiries, ' enquiries in 6 months'], ['as of ', s.asOf, '']]
      .forEach(([before, value, after]) => {
        const f = el('span');
        f.append(before, el('b', '', value), after);
        facts.append(f);
      });
    box.append(head, facts);
    if (source) box.append(el('p', 'b-hint', source));
  }

  function buildCrifOptions() {
    const box = $('crifOptions');
    const name = el('div', 'b-field is-full');
    const nameLabel = el('label', 'b-label', 'Name to use in the video');
    nameLabel.htmlFor = 'c_name';
    const nameInput = el('input', 'b-input');
    Object.assign(nameInput, { id: 'c_name', type: 'text', maxLength: 40, placeholder: 'Name from the report' });
    name.append(nameLabel, nameInput, el('p', 'b-hint', 'Optional. Leave empty to use the first name from the report.'));

    const langs = el('div', 'b-field is-full');
    const row = el('div', 'b-langs');
    LANGUAGES.forEach((lang) => {
      const opt = el('label', 'b-check');
      const box = el('input');
      Object.assign(box, { type: 'checkbox', value: lang.code, name: 'c_languages', checked: true });
      opt.append(box, el('span', '', lang.label));
      row.append(opt);
    });
    const start = el('select', 'b-input b-start');
    start.id = 'c_start';
    start.setAttribute('aria-label', 'Plays first');
    row.append(el('span', 'b-start-label', 'Plays first'), start);
    langs.append(el('span', 'b-label', 'Languages'), row);

    const speed = el('div', 'b-field is-full');
    const speedRow = el('div', 'b-range');
    const range = el('input');
    Object.assign(range, { id: 'c_speed', type: 'range', min: 0.8, max: 1.2, step: 0.01, value: 1.05 });
    const out = el('output', 'b-range-value', '1.05×');
    speedRow.append(range, out);
    speed.append(el('span', 'b-label', 'Voice speed'), speedRow, el('p', 'b-hint', 'Faster speech makes the video shorter.'));
    box.append(name, langs, speed);

    const sync = () => {
      out.textContent = `${parseFloat(range.value).toFixed(2)}×`;
      const checked = [...document.querySelectorAll('input[name="c_languages"]:checked')].map((b) => b.value);
      const prev = start.value;
      start.replaceChildren(...LANGUAGES.filter((l) => checked.includes(l.code)).map((l) => new Option(l.label, l.code)));
      if (checked.includes(prev)) start.value = prev;
      start.disabled = checked.length < 2;
    };
    box.addEventListener('input', sync);
    box.addEventListener('change', sync);
    sync();
  }

  function readFile(file) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => loadCrifText(String(reader.result), `Loaded from ${file.name}`);
    reader.onerror = () => { $('crifError').textContent = `Couldn't read ${file.name}.`; };
    reader.readAsText(file);
  }

  async function generateCrif() {
    if (!state.crif) {
      showError('Add a CRIF report first: drop the file above or paste its JSON.');
      return;
    }
    const checked = [...document.querySelectorAll('input[name="c_languages"]:checked')].map((b) => b.value);
    if (!checked.length) {
      showError('Pick at least one language.');
      return;
    }
    const first = $('c_start').value;
    const body = {
      report: state.crif,
      languages: checked.includes(first) ? [first, ...checked.filter((c) => c !== first)] : checked,
      voice_speed: parseFloat($('c_speed').value),
    };
    const name = $('c_name').value.trim();
    if (name) body.customer_name = name;
    await runGeneration('/api/story/crif', body, 'Writing the walkthrough and recording the greeting…',
      'Recording the opening line, usually 1–3 seconds. The rest records while it plays.');
  }

  // ---------------------------------------------------------------- generate

  function describeApiError(body, status) {
    if (body && Array.isArray(body.detail)) {
      const fieldErrors = {};
      const lines = body.detail.map((d) => {
        const key = Array.isArray(d.loc) ? d.loc[d.loc.length - 1] : '';
        if (key) fieldErrors[key] = d.msg;
        return `${key || 'request'}: ${d.msg}`;
      });
      showFieldErrors(fieldErrors);
      return `The API rejected the details:\n${lines.join('\n')}`;
    }
    if (body && typeof body.detail === 'string') return body.detail;
    return `The API returned HTTP ${status}.`;
  }

  async function generate() {
    if (state.busy) return;
    if (state.mode === 'crif') {
      await generateCrif();
      return;
    }
    const onJsonTab = !$('panelJson').hidden;
    let payload = readForm();
    if (onJsonTab) {
      try {
        payload = { ...payload, ...JSON.parse($('jsonInput').value) };
      } catch (err) {
        showError(`Fix the JSON first: ${err.message}`);
        return;
      }
    }
    const errors = validate(payload);
    showFieldErrors(errors);
    if (Object.keys(errors).length) {
      showError('Some details need fixing before the video can be generated.');
      if (onJsonTab) setJsonStatus(Object.entries(errors).map(([k, m]) => `${k}: ${m}`).join(' · '), 'error');
      return;
    }
    showError('');
    await runGeneration('/api/story', payload, 'Recording your score…',
      'Recording the first stage, usually 1–3 seconds. The rest records while it plays.');
  }

  // Posts to a story endpoint, then plays the story it returns.
  async function runGeneration(path, payload, busyMessage, statusMessage) {
    setBusy(true, statusMessage);
    window.StoryPlayer.busy(busyMessage);
    const started = performance.now();
    try {
      let res;
      try {
        res = await fetch(api(path), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      } catch (err) {
        throw new Error(`Could not reach the API at ${api(path)}. Is the backend running?`);
      }
      const body = await res.json().catch(() => null);
      if (!res.ok) throw new Error(describeApiError(body, res.status));
      const storyUrl = api(body.story_url);
      const next = new URL(location.href);
      next.searchParams.set('story', storyUrl);
      history.replaceState(null, '', next);
      state.dirty = false;
      await window.StoryPlayer.load(storyUrl, { play: true });
      const secs = ((performance.now() - started) / 1000).toFixed(1);
      state.readyIn = body.cached ? `Ready in ${secs}s (same details as before, reused)`
        : body.status === 'generating' ? `Playing after ${secs}s` : `Generated in ${secs}s`;
      $('builderStatus').textContent = state.readyIn;
      if (window.matchMedia('(max-width: 1000px)').matches) $('phone').scrollIntoView({ behavior: 'smooth' });
    } catch (err) {
      showError(err.message);
      window.StoryPlayer.cancelBusy();
    } finally {
      setBusy(false);
    }
  }

  function setBusy(busy, message) {
    state.busy = busy;
    const btn = $('generateBtn');
    btn.disabled = busy;
    btn.classList.toggle('is-busy', busy);
    $('generateLabel').textContent = busy ? 'Generating…' : 'Generate video';
    if (busy) {
      state.readyIn = '';
      $('builderStatus').textContent = message || '';
    }
  }

  // ---------------------------------------------------------------- wiring

  buildForm();
  writeForm(SAMPLE);
  buildCrifOptions();
  selectTab('Crif');
  $('crifDropIcon').innerHTML = (window.ICONS || {}).doc || '';
  $('tabCrif').addEventListener('click', () => selectTab('Crif'));
  $('crifFile').addEventListener('change', (e) => readFile(e.target.files[0]));
  const drop = $('crifDrop');
  ['dragenter', 'dragover'].forEach((type) => drop.addEventListener(type, (e) => { e.preventDefault(); drop.classList.add('is-over'); }));
  ['dragleave', 'drop'].forEach((type) => drop.addEventListener(type, () => drop.classList.remove('is-over')));
  drop.addEventListener('drop', (e) => { e.preventDefault(); readFile(e.dataTransfer.files[0]); });
  let pasteTimer = 0;
  $('crifPaste').addEventListener('input', () => {
    clearTimeout(pasteTimer);
    pasteTimer = setTimeout(() => {
      const text = $('crifPaste').value.trim();
      if (text) loadCrifText(text, 'Pasted JSON');
    }, 300);
  });

  $('tabDetails').addEventListener('click', () => selectTab('Details'));
  $('tabJson').addEventListener('click', () => selectTab('Json'));
  $('tabFile').addEventListener('click', () => selectTab('File'));
  $('jsonInput').addEventListener('input', onJsonInput);
  $('generateBtn').addEventListener('click', generate);
  $('sampleBtn').addEventListener('click', () => {
    writeForm({ ...SAMPLE, report_month: thisMonth() });
    state.dirty = true;
    if (!$('panelJson').hidden) refreshJson();
  });
  $('copyJson').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText($('jsonInput').value);
      setJsonStatus('Copied to the clipboard.', 'ok');
    } catch {
      $('jsonInput').select();
      setJsonStatus('Select-all done, press Cmd/Ctrl+C to copy.', 'warn');
    }
  });

  // While a new video's later chapters are still recording, show how far along it is.
  window.addEventListener('story:generation', (e) => {
    const g = e.detail || {};
    if (state.busy || !state.readyIn || g.total < 2) return;
    const progress = g.status === 'generating' ? `recording chapters, ${g.ready} of ${g.total} ready`
      : g.status === 'failed' ? `${g.ready} of ${g.total} chapters recorded, the rest failed: generate again to finish`
        : `all ${g.total} chapters ready`;
    $('builderStatus').textContent = `${state.readyIn} · ${progress}`;
  });

  // When a story loads (the bundled sample, a ?story= link or a language switch), show its
  // details in the form, unless the user is in the middle of editing.
  window.addEventListener('story:loaded', (e) => {
    const input = e.detail && e.detail.input;
    if (!input || state.dirty || state.busy) return;
    writeForm(input);
    if (!$('panelJson').hidden) refreshJson();
  });
})();
