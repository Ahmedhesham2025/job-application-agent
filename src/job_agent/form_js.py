"""JavaScript run inside the application page to list and mark form questions.

Each question gets a data-ja-key attribute so Python can fill it later without
guessing selectors. Works on Greenhouse, Ashby and Lever style forms because it
reads labels and roles, not site-specific class names.
"""

EXTRACT_FIELDS_JS = r"""
() => {
  const clean = t => (t || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    if (!el) return false;
    if (el.type === 'file' || el.type === 'checkbox' || el.type === 'radio') {
      // These inputs are often visually hidden behind a styled label.
      const box = (el.closest('label') || el.parentElement || el).getBoundingClientRect();
      return box.width > 0 || box.height > 0;
    }
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const textOf = node => clean(node ? node.innerText || node.textContent : '');
  const ownLabel = el => {
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l && textOf(l)) return textOf(l);
    }
    const ids = el.getAttribute('aria-labelledby');
    if (ids) {
      const t = clean(ids.split(/\s+/).map(i => textOf(document.getElementById(i))).join(' '));
      if (t) return t;
    }
    const wrap = el.closest('label');
    if (wrap && textOf(wrap)) return textOf(wrap);
    return clean(el.getAttribute('aria-label'));
  };
  // Question text for a group: legend, or the first label/heading in an
  // ancestor that is not one of the option labels.
  const CONTROL = 'input:not([type=hidden]), select, textarea';
  const questionFor = (el, optionTexts, groupSize = 1) => {
    const fs = el.closest('fieldset');
    if (fs) {
      const lg = fs.querySelector('legend');
      if (lg && textOf(lg)) return textOf(lg);
      for (const c of fs.querySelectorAll('label, [class*="question-title"], [class*="title"]')) {
        const t = textOf(c);
        if (t && !optionTexts.includes(t) && !c.querySelector('input,select,textarea,button')) return t;
      }
    }
    let node = el.parentElement;
    for (let depth = 0; node && depth < 7; depth++, node = node.parentElement) {
      // Stop once the container also holds other questions' inputs.
      const own = node.querySelectorAll(CONTROL).length;
      if (own > groupSize || node.tagName === 'FORM' || node === document.body) break;
      const cands = node.querySelectorAll('label, legend, h3, h4, [class*="question"], [class*="title"], [class*="label"]');
      for (const c of cands) {
        const t = textOf(c);
        if (!t || optionTexts.includes(t)) continue;
        if (c.contains(el)) continue;
        if (c.querySelector('input,select,textarea,button')) continue;
        return t;
      }
    }
    return '';
  };
  // Field container: fieldset, Ashby/Greenhouse field wrapper, or parent.
  const entryOf = el => el.closest('fieldset, [class*="fieldEntry"], [class*="field-entry"], .field, .application-question') || el.parentElement;
  const markedRequired = el => {
    const entry = entryOf(el);
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l && /required/i.test(l.className)) return true;
    }
    return !!(entry && entry.querySelector('[class*="_required"], [class*="required"]:not(input)'));
  };
  const isRequired = (el, label) =>
    !!(el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label) || /\(required\)/i.test(label) || markedRequired(el));

  let n = 0;
  const nextKey = () => 'f' + (n++);
  const fields = [];
  const done = new Set();
  const forms = [...document.querySelectorAll('form')].filter(f => f.querySelector('input:not([type=hidden]), textarea, select'));
  const form = forms.sort((a, b) => b.querySelectorAll('input,textarea,select').length - a.querySelectorAll('input,textarea,select').length)[0] || document.body;
  const skip = new Set();

  // Ashby style Yes/No toggle buttons.
  for (const btn of form.querySelectorAll('button')) {
    const t = textOf(btn);
    if (t !== 'Yes' || done.has(btn)) continue;
    const parent = btn.parentElement;
    const no = [...parent.querySelectorAll('button')].find(b => textOf(b) === 'No');
    if (!no) continue;
    const key = nextKey();
    btn.setAttribute('data-ja-key', key); btn.setAttribute('data-ja-option', 'Yes');
    no.setAttribute('data-ja-key', key); no.setAttribute('data-ja-option', 'No');
    done.add(btn); done.add(no);
    const entry = entryOf(parent);
    // Ashby keeps a hidden checkbox behind the Yes/No buttons; skip it.
    const backing = entry ? [...entry.querySelectorAll('input[type=checkbox]')] : [];
    backing.forEach(b => skip.add(b));
    const label = questionFor(parent, ['Yes', 'No'], backing.length);
    fields.push({key, kind: 'yesno_buttons', label, required: /\*\s*$/.test(label) || markedRequired(btn), options: ['Yes', 'No']});
  }

  const controls = [...form.querySelectorAll('input, textarea, select')];
  const groups = new Map();
  for (const el of controls) {
    if (skip.has(el)) continue;
    const type = (el.type || el.tagName).toLowerCase();
    if (type === 'hidden' || type === 'submit' || type === 'button') continue;
    if ((el.name || '').startsWith('g-recaptcha') || (el.name || '').startsWith('h-captcha')) continue;
    if (!visible(el)) continue;
    // React-select keeps hidden "required" helper inputs; they are not questions.
    if (el.getAttribute('aria-hidden') === 'true' && el.tabIndex === -1) continue;
    if (type === 'radio' || type === 'checkbox') {
      // Group by shared name, or by shared fieldset when every box has its own name (Ashby).
      const fs = el.closest('fieldset');
      const g = (type === 'checkbox' && fs && fs.querySelectorAll('input[type=checkbox]').length > 1) ? fs : (el.name || el.id);
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(el);
      continue;
    }
    let label = ownLabel(el) || questionFor(el, []) || clean(el.placeholder);
    if (type === 'file' && (!label || /^(attach|upload|upload file|choose file|browse)$/i.test(label))) {
      label = questionFor(el, ['Attach', 'Dropbox', 'Google Drive', 'Enter manually', 'Upload File', 'Upload file'], 1)
        || clean((el.name || el.id || 'file').replace(/[_-]+/g, ' '));
    }
    // "Autofill from resume" uploads would overwrite the agent's answers.
    if (type === 'file' && /autofill|parse|import/i.test(label)) continue;
    const key = nextKey();
    el.setAttribute('data-ja-key', key);
    let kind = type;
    if (el.tagName === 'TEXTAREA') kind = 'textarea';
    else if (el.tagName === 'SELECT') kind = 'select';
    else if (el.getAttribute('role') === 'combobox') kind = 'combobox';
    else if (type === 'file') kind = 'file';
    const field = {key, kind, label, required: isRequired(el, label), name: el.name || el.id || ''};
    if (kind === 'select') field.options = [...el.options].map(o => clean(o.text)).filter(t => t && !/^select/i.test(t));
    fields.push(field);
  }
  for (const [g, els] of groups) {
    const key = nextKey();
    const opts = els.map(e => ownLabel(e) || clean(e.value));
    els.forEach((e, i) => { e.setAttribute('data-ja-key', key); e.setAttribute('data-ja-option', opts[i]); });
    const type = els[0].type;
    let label = questionFor(els[0], opts, els.length);
    let kind = type === 'radio' ? 'radio' : 'checkbox_group';
    // A single checkbox is a yes/no question or a consent box.
    if (type === 'checkbox' && els.length === 1) {
      kind = 'checkbox_single';
      label = label && label !== opts[0] ? (opts[0] ? label + ': ' + opts[0] : label) : opts[0];
    }
    if (!label && kind !== 'checkbox_single') label = 'Choose one: ' + opts.join(' / ');
    fields.push({key, kind, label, required: els.some(e => e.required) || /\*\s*$/.test(label) || markedRequired(els[0]), options: kind === 'checkbox_single' ? ['checked', 'unchecked'] : opts});
  }
  return fields;
}
"""


PAGE_SIGNALS_JS = r"""
() => {
  const html = document.documentElement.innerHTML.toLowerCase();
  const text = (document.body.innerText || '').toLowerCase();
  const frames = [...document.querySelectorAll('iframe')].map(f => f.src || '');
  const challengeVisible = [...document.querySelectorAll('iframe')].some(f => {
    const r = f.getBoundingClientRect();
    return /recaptcha.*bframe|hcaptcha.*challenge/.test(f.src || '') && r.width > 50 && r.height > 50;
  });
  return {
    host: location.hostname,
    url: location.href,
    captcha: html.includes('hcaptcha') || html.includes('recaptcha') || frames.some(s => /captcha/.test(s)),
    captcha_challenge_visible: challengeVisible,
    login_wall: /sign in to apply|log in to apply|create an account to apply/.test(text),
    confirmation: /thank you for (applying|your application|your interest)|application (has been )?(submitted|received)|we('| ha)ve received your application|your application was sent/.test(text),
    closed_posting: /no longer accepting applications|job (is )?no longer available|position has been filled/.test(text)
  };
}
"""


def banner_js(filled: int, todo: list[str]) -> str:
    import json

    items = "".join(f"<li>{t}</li>" for t in todo) or "<li>Nothing — just check the answers.</li>"
    html = (
        "<div style='font:14px Arial;padding:12px 16px;background:#fff3cd;border-bottom:3px solid #e0a800;"
        "position:fixed;top:0;left:0;right:0;z-index:2147483647;max-height:40vh;overflow:auto'>"
        f"<b>Job agent filled {filled} fields.</b> Please check every answer, fix anything wrong, "
        "solve the CAPTCHA if shown, then press <b>Submit</b> yourself."
        f"<br><b>Still needs you (red boxes):</b><ul style='margin:4px 0 0 18px'>{items}</ul>"
        "<button onclick=\"this.parentElement.remove()\" style='margin-top:6px'>Hide this note</button></div>"
    )
    return f"() => {{ const d=document.createElement('div'); d.innerHTML={json.dumps(html)}; document.body.prepend(d.firstChild); }}"


def mark_todo_js(keys: list[str]) -> str:
    import json

    return (
        "() => { for (const k of " + json.dumps(keys) + ") {"
        " for (const el of document.querySelectorAll(`[data-ja-key=\"${k}\"]`)) {"
        " let box = el.closest('fieldset') || el.parentElement; if (!box || box.tagName === 'FORM') box = el;"
        " box.style.outline = '3px solid #d9342b'; box.style.outlineOffset = '3px'; } } }"
    )
