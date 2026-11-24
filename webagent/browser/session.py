"""One real browser per task (Playwright + Chromium / Edge).

The page is shown to the model as a short list of elements. Each element gets a ref
(`e12`) written into the page as `data-wa-ref`, so a tool can act on exactly that element.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# The main content: <main> / role=main, or a lone <article>; else the whole body.
# Never just the first <article> of a list (on many shops every product card is an <article>).
MAIN_CONTENT_JS = r"""
(() => {
  const body = document.body;
  if (!body) return null;
  let el = document.querySelector('main,[role=main]');
  if (!el) { const arts = document.querySelectorAll('article'); if (arts.length === 1) el = arts[0]; }
  if (!el) return body;
  return (el.innerText || '').length >= (body.innerText || '').length * 0.3 ? el : body;
})
"""

SNAPSHOT_JS = r"""
(opts) => {
  const MAX = opts.max || 120;
  const vh = window.innerHeight, vw = window.innerWidth;
  const SEL = 'a[href],button,input,select,textarea,summary,[role=button],[role=link],[role=checkbox],' +
              '[role=radio],[role=tab],[role=menuitem],[role=option],[role=combobox],[role=searchbox],' +
              '[role=textbox],[role=switch],[role=slider],[contenteditable=""],[contenteditable=true],[onclick],' +
              '[tabindex="0"],[draggable=true],canvas';
  // Also inside web components (open shadow roots), which plain querySelectorAll does not see.
  const deepAll = (root, sel, out = []) => {
    root.querySelectorAll(sel).forEach(e => out.push(e));
    root.querySelectorAll('*').forEach(e => { if (e.shadowRoot) deepAll(e.shadowRoot, sel, out); });
    return out;
  };
  deepAll(document, '[data-wa-ref]').forEach(e => e.removeAttribute('data-wa-ref'));
  const withShadow = el => {
    if (!el) return '';
    let t = el.innerText || '';
    deepAll(el, '*').forEach(h => { if (h.shadowRoot) t += '\n' + Array.from(h.shadowRoot.children)
      .filter(c => c.tagName !== 'STYLE').map(c => c.innerText || '').join('\n'); });
    return t;
  };
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const st = getComputedStyle(el);
    return !(st.visibility === 'hidden' || st.display === 'none' || parseFloat(st.opacity) === 0);
  };
  const labelFor = el => {
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) return clean(l.innerText); }
    const p = el.closest('label'); if (p) return clean(p.innerText);
    return '';
  };
  const nameOf = el => {
    const a = el.getAttribute('aria-label'); if (a) return clean(a);
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = lb.split(' ').map(id => document.getElementById(id)).filter(Boolean).map(e => e.innerText).join(' '); if (clean(t)) return clean(t); }
    const tag = el.tagName.toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
      return clean(labelFor(el) || el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('name') || '');
    }
    let t = clean(el.innerText);
    if (!t) { const img = el.querySelector('img[alt]'); if (img) t = clean(img.getAttribute('alt')); }
    return t || clean(el.getAttribute('title') || el.getAttribute('alt') || el.getAttribute('value') || '');
  };
  const roleOf = el => {
    const r = el.getAttribute('role'); if (r) return r;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'input') return 'input:' + (el.getAttribute('type') || 'text').toLowerCase();
    if (tag === 'select') return 'select';
    if (tag === 'textarea') return 'textarea';
    if (tag === 'summary') return 'button';
    if (el.isContentEditable) return 'textbox';
    if (tag === 'canvas') return 'canvas';
    if (el.getAttribute('draggable') === 'true') return 'draggable';
    if (el.hasAttribute('data-wa-drop')) return 'drop area';
    return tag === 'button' ? 'button' : 'clickable';
  };
  // Which question a radio button / checkbox answers ("yes" alone means nothing on a survey page).
  const groupOf = (el, role) => {
    if (!/^(radio|checkbox|switch|option|input:radio|input:checkbox)$/.test(role)) return '';
    const g = el.closest('[role=radiogroup],[role=group],[role=listbox],fieldset,[role=list][aria-labelledby],[role=list][aria-label]');
    if (!g) return '';
    let t = g.getAttribute('aria-label') || '';
    const lb = g.getAttribute('aria-labelledby');
    if (!t && lb) t = lb.split(' ').map(id => document.getElementById(id)).filter(Boolean).map(e => e.innerText).join(' ');
    if (!t && g.tagName === 'FIELDSET') { const lg = g.querySelector('legend'); if (lg) t = lg.innerText; }
    t = clean(t);
    return t.length > 80 ? t.slice(0, 79) + '…' : t;
  };
  const items = [];
  // Drag and drop boards: the columns a card can be dropped on (the parents of draggable cards and their
  // look-alike siblings, also an empty column), named by their heading.
  deepAll(document, '[data-wa-drop]').forEach(e => e.removeAttribute('data-wa-drop'));
  const drops = new Set();
  deepAll(document, '[draggable=true]').forEach(d => {
    const c = d.parentElement; if (!c || c === document.body) return;
    drops.add(c);
    if (c.parentElement) Array.from(c.parentElement.children)
      .filter(x => x.tagName === c.tagName && x.className === c.className).forEach(x => drops.add(x));
  });
  drops.forEach(c => c.setAttribute('data-wa-drop', '1'));
  const dropName = c => { const h = c.querySelector('h1,h2,h3,h4,h5,h6,[role=heading],header');
    return clean(c.getAttribute('aria-label') || c.getAttribute('data-col') || (h && h.innerText) || '').slice(0, 60); };
  // An open pop-up (alert, modal dialog) blocks the rest of the page: its buttons go first and it is named.
  const modal = deepAll(document, '[role=alertdialog],[aria-modal=true],dialog[open],[role=dialog]').filter(visible)
    .filter(d => { const r = d.getBoundingClientRect(); return r.bottom > 0 && r.top < vh; })
    .filter(d => d.getAttribute('role') === 'alertdialog' || d.getAttribute('aria-modal') === 'true'
      || (d.tagName === 'DIALOG' && d.matches(':modal')) || /modal/i.test(String(d.className || ''))).pop() || null;
  const all = deepAll(document, SEL + ',[data-wa-drop]').filter(visible)
    .filter(el => el.tagName !== 'CANVAS' || (el.getBoundingClientRect().width >= 120 && el.getBoundingClientRect().height >= 60));
  const seen = new Set();
  // In view first, then below (up to 2 screens), then above.
  const ranked = all.map(el => {
    const r = el.getBoundingClientRect();
    const where = r.bottom <= 0 ? 'above' : (r.top >= vh ? 'below' : 'view');
    const rank = (modal && modal.contains(el)) ? -1
      : where === 'view' ? 0 : (where === 'below' ? (r.top < vh * 3 ? 1 : 3) : 2);
    return {el, r, where, rank};
  }).sort((a, b) => a.rank - b.rank || a.r.top - b.r.top || a.r.left - b.r.left);
  let n = opts.start || 0;   // frames continue the numbering of the page
  for (const {el, r, where} of ranked) {
    if (items.length >= MAX) break;
    // skip a clickable wrapper whose inner element is already listed
    if (seen.has(el)) continue;
    const role = roleOf(el);
    const fullName = role === 'drop area' ? (dropName(el) || 'drop area') : role === 'canvas'
      ? (clean(el.getAttribute('aria-label') || '') || `drawing ${Math.round(r.width)}x${Math.round(r.height)}`) : nameOf(el);
    let name = fullName.length > 90 ? fullName.slice(0, 89) + '…' : fullName;
    if (!name && role === 'clickable') continue;
    n += 1;
    const ref = 'e' + n;
    el.setAttribute('data-wa-ref', ref);
    el.querySelectorAll(SEL).forEach(c => { if (role === 'link' || role === 'button') seen.add(c); });
    const form = el.form || el.closest('form');
    items.push({
      ref, role, name, where, group: groupOf(el, role),
      href: el.getAttribute('href') || '',
      // Long values (a share link in a read-only box) are kept whole up to 400 characters; a cut is marked with
      // "…" so the agent never builds an address from a cut value.
      value: (el.value !== undefined && typeof el.value === 'string')
        ? (el.value.length > 400 ? el.value.slice(0, 399) + '…' : (/^https?:\/\//.test(el.value) || el.value.length <= 80 ? el.value : el.value.slice(0, 79) + '…')) : '',
      type: (el.getAttribute('type') || '').toLowerCase(),
      autocomplete: (el.getAttribute('autocomplete') || '').toLowerCase(),
      field: ((el.getAttribute('name') || '') + ' ' + (el.id || '')).toLowerCase(),
      form: form ? clean((form.getAttribute('action') || '') + ' ' + (form.getAttribute('id') || '') + ' ' + (form.getAttribute('name') || '')).toLowerCase() : '',
      disabled: !!el.disabled,
      // native boxes, and custom ones (Google Forms, Material, ...) that say it with aria-checked
      checked: (el.type === 'checkbox' || el.type === 'radio') ? !!el.checked
        : (/^(checkbox|radio|switch|menuitemcheckbox|menuitemradio|option)$/.test(role) && el.hasAttribute('aria-checked'))
          ? el.getAttribute('aria-checked') === 'true'
        : (role === 'option' && el.hasAttribute('aria-selected')) ? el.getAttribute('aria-selected') === 'true' : null,
      options: el.tagName === 'SELECT' ? Array.from(el.options).slice(0, 15).map(o => clean(o.text)) : null,
      range: el.type === 'range' ? [el.min || '0', el.max || '100'] :
             (el.getAttribute('role') === 'slider' ? [el.getAttribute('aria-valuemin') || '', el.getAttribute('aria-valuemax') || ''] : null),
      nav: !!el.closest('nav,header,footer,[role=navigation],[role=banner],[role=contentinfo]'),
      expanded: el.hasAttribute('aria-expanded') ? el.getAttribute('aria-expanded') === 'true' : null,
      popup: el.hasAttribute('aria-haspopup') && el.getAttribute('aria-haspopup') !== 'false',
    });
  }
  const heads = Array.from(document.querySelectorAll('h1,h2,h3')).filter(visible)
      .filter(h => { const r = h.getBoundingClientRect(); return r.bottom > 0 && r.top < vh * 2; })
      .slice(0, 12).map(h => clean(h.innerText).slice(0, 100)).filter(Boolean);
  const main = MAIN_CONTENT();
  const text = clean(withShadow(main)).slice(0, opts.textChars || 1500);
  // The text on screen now. The start of the page is not enough: in a chat or a feed the newest messages are at
  // the bottom (live run, Google Chat: the agent saw only yesterday's messages and said nothing new came today).
  let view = '';
  if (main) {
    const parts = []; let steps = 0;
    const tw = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
    while (tw.nextNode() && steps++ < 20000) {
      const s = clean(tw.currentNode.nodeValue); const par = tw.currentNode.parentElement;
      if (!s || !par || /^(SCRIPT|STYLE|NOSCRIPT)$/.test(par.tagName)) continue;
      const r = par.getBoundingClientRect();
      if (r.width < 1 || r.height < 1 || r.bottom <= 0 || r.top >= vh || r.right <= 0 || r.left >= vw) continue;
      parts.push(s);
    }
    view = parts.join(' ');
    const max = opts.viewChars || 1800;
    if (view.length > max) view = '…' + view.slice(-max);
    const tail = view.slice(-150).replace(/^…/, '');
    if (!tail || text.includes(tail)) view = '';   // already in the start of the page text
  }
  const captcha = !!document.querySelector('iframe[src*="recaptcha"],iframe[src*="hcaptcha"],iframe[src*="challenges.cloudflare"],#challenge-form,.g-recaptcha,.h-captcha')
      || /verify (that )?you are (a )?human|are you a robot|not a (ro)?bot|made by a human|unusual traffic|press (and|&) hold/i.test(clean(document.body ? document.body.innerText : '').slice(0, 3000));
  const se = document.scrollingElement || document.documentElement;
  const tables = Array.from(document.querySelectorAll('table,[role=table],[role=grid]')).filter(visible)
      .filter(t => t.querySelectorAll('tr,[role=row]').length >= 2 && t.querySelectorAll('td,th,[role=cell],[role=gridcell]').length >= 4).length;
  return {
    url: location.href, title: document.title, items, heads, text, view, captcha, tables, total: all.length,
    dialog: modal ? (clean(modal.innerText) || nameOf(modal) || 'a pop-up').slice(0, 300) : '',
    scroll: {y: Math.round(se.scrollTop), max: Math.max(0, Math.round(se.scrollHeight - vh))},
  };
}
"""

READ_JS = r"""
() => {
  const pick = MAIN_CONTENT();
  let t = (pick && pick.innerText) || '';
  if (pick) {  // text inside web components (open shadow roots)
    const hosts = []; const walk = r => r.querySelectorAll('*').forEach(e => { if (e.shadowRoot) { hosts.push(e); walk(e.shadowRoot); } });
    walk(pick);
    hosts.forEach(h => { t += '\n' + Array.from(h.shadowRoot.children).filter(c => c.tagName !== 'STYLE').map(c => c.innerText || '').join('\n'); });
  }
  if (t.trim().length < 200 && document.body && (document.body.innerText || '').length > t.length) t = document.body.innerText;
  return t.replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
}
"""


SNAPSHOT_JS = SNAPSHOT_JS.replace("MAIN_CONTENT()", MAIN_CONTENT_JS.strip() + "()")

# What scroll moves: the scrolling pane under the middle of the main content (a chat's message list, a mail
# list), else the page, else the biggest scrolling pane on screen. It is marked so the next calls find it again.
SCROLL_PICK_JS = r"""
() => {
  const vh = innerHeight, vw = innerWidth;
  document.querySelectorAll('[data-wa-scroll]').forEach(e => e.removeAttribute('data-wa-scroll'));
  const scrolls = el => { const s = getComputedStyle(el);
    return /(auto|scroll|overlay)/.test(s.overflowY) && el.scrollHeight > el.clientHeight + 4; };
  const se = document.scrollingElement || document.documentElement;
  const main = document.querySelector('main,[role=main]');
  const box = main && main.getBoundingClientRect();
  const cx = box && box.width > 50 ? Math.min(vw - 2, Math.max(1, box.left + box.width / 2)) : vw / 2;
  const cy = box && box.height > 50 ? Math.min(vh - 2, Math.max(1, (Math.max(0, box.top) + Math.min(vh, box.bottom)) / 2)) : vh / 2;
  let el = document.elementFromPoint(cx, cy), pick = null;
  while (el && el !== document.body && el !== document.documentElement) {
    if (scrolls(el)) { pick = el; break; }
    el = el.parentElement;
  }
  if (!pick && se.scrollHeight > se.clientHeight + 4) pick = se;
  if (!pick) {
    let best = 0;
    document.querySelectorAll('*').forEach(e => {
      if (!scrolls(e)) return;
      const r = e.getBoundingClientRect();
      const a = Math.max(0, Math.min(vh, r.bottom) - Math.max(0, r.top)) * Math.max(0, Math.min(vw, r.right) - Math.max(0, r.left));
      if (a > best) { best = a; pick = e; }
    });
  }
  if (!pick) return null;
  const doc = pick === se;
  if (!doc) pick.setAttribute('data-wa-scroll', '1');
  const r = doc ? {left: 0, top: 0, width: vw, height: vh} : pick.getBoundingClientRect();
  const lab = doc ? null : (pick.hasAttribute('aria-label') ? pick : pick.closest('[aria-label]'));
  const name = lab ? (lab.getAttribute('aria-label') || '').trim().slice(0, 60) : '';
  return {doc, name, top: Math.round(pick.scrollTop), max: Math.max(0, Math.round(pick.scrollHeight - pick.clientHeight)),
          x: Math.round(Math.min(vw - 2, Math.max(1, r.left + r.width / 2))),
          y: Math.round(Math.min(vh - 2, Math.max(1, r.top + r.height / 2)))};
}
"""
SCROLL_STATE_JS = r"""
() => { const el = document.querySelector('[data-wa-scroll]') || document.scrollingElement || document.documentElement;
  return {top: Math.round(el.scrollTop), max: Math.max(0, Math.round(el.scrollHeight - el.clientHeight))}; }
"""
SCROLL_BY_JS = r"""
(dy) => { const el = document.querySelector('[data-wa-scroll]') || document.scrollingElement || document.documentElement;
  el.scrollBy(0, dy); }
"""

# Is this box / radio button / option ticked now? null when the element has no such state.
STATE_JS = r"""el => {
  if (el.type === 'checkbox' || el.type === 'radio') return !!el.checked;
  const r = el.getAttribute('role') || '';
  if (!/^(checkbox|radio|switch|menuitemcheckbox|menuitemradio|option)$/.test(r)) return null;
  const v = el.hasAttribute('aria-checked') ? el.getAttribute('aria-checked') : el.getAttribute('aria-selected');
  return v === null ? null : v === 'true';
}"""
READ_JS = READ_JS.replace("MAIN_CONTENT()", MAIN_CONTENT_JS.strip() + "()")

# Real tables (<table>, role=table/grid) as rows of cells, with a caption or the heading above them.
TABLES_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const visible = el => { const r = el.getBoundingClientRect(); return r.width > 1 && r.height > 1; };
  const captionOf = t => {
    const c = t.querySelector('caption'); if (c && clean(c.innerText)) return clean(c.innerText);
    const a = t.getAttribute('aria-label'); if (a) return clean(a);
    let el = t;
    for (let i = 0; i < 6 && el; i++) {
      let prev = el.previousElementSibling;
      while (prev) {
        const h = /^H[1-6]$/.test(prev.tagName) ? prev : prev.querySelector('h1,h2,h3,h4');
        if (h && clean(h.innerText)) return clean(h.innerText);
        prev = prev.previousElementSibling;
      }
      el = el.parentElement;
    }
    return '';
  };
  const out = [];
  const tables = Array.from(document.querySelectorAll('table,[role=table],[role=grid],[role=treegrid]')).filter(visible);
  for (const t of tables) {
    const rows = [];
    if (t.tagName === 'TABLE') {
      for (const tr of Array.from(t.rows)) {
        if (tr.closest('table') !== t) continue;   // rows of a nested table belong to it
        const cells = [];
        for (const c of Array.from(tr.cells)) {
          const txt = clean(c.innerText).slice(0, 200);
          const span = Math.min(parseInt(c.getAttribute('colspan') || '1', 10) || 1, 10);
          for (let k = 0; k < span; k++) cells.push(k === 0 ? txt : '');
        }
        if (cells.some(Boolean)) rows.push(cells);
      }
    } else {
      for (const r of Array.from(t.querySelectorAll('[role=row]'))) {
        const cells = Array.from(r.querySelectorAll('[role=cell],[role=gridcell],[role=columnheader],[role=rowheader]'))
            .map(c => clean(c.innerText).slice(0, 200));
        if (cells.some(Boolean)) rows.push(cells);
      }
    }
    if (rows.length < 2 || Math.max(...rows.map(r => r.length)) < 2) continue;  // layout tables
    out.push({caption: captionOf(t).slice(0, 150), rows: rows.slice(0, 200), total: rows.length});
  }
  return out;
}
"""


def format_table(t: dict, max_chars: int = 6000) -> str:
    lines = [f"Table: {t.get('caption') or '(no title)'}  ({t.get('total', len(t['rows']))} rows)"]
    size = 0
    for row in t["rows"]:
        line = "| " + " | ".join(c.replace("|", "/") for c in row) + " |"
        size += len(line) + 1
        if size > max_chars:
            lines.append("... (more rows: use find_in_page with key words for the rest)")
            break
        lines.append(line)
    return "\n".join(lines)


def find_passages(text: str, query: str, limit: int = 3500) -> str:
    """The paragraphs of `text` that best match the words of `query`, in page order, with one line of context."""
    lines = [ln.strip() for ln in text.split("\n")]
    # The exact text first (also symbols such as ★☆☆☆☆ or "1 of 5 stars"): every line that has it, with a count,
    # so the agent can count things on the page instead of guessing.
    phrase = query.strip().lower()
    exact = [i for i, ln in enumerate(lines) if phrase and phrase in ln.lower()]
    if exact and (len(phrase) >= 3 or not re.search(r"\w", phrase)):
        out = [f"{len(exact)} line(s) contain exactly \"{query.strip()}\":"]
        hits = set(exact)
        for i in exact:
            line = lines[i][:600]
            if len(line) < 80:
                # A short line (a name, a heading) means little alone: add what follows it, such as the message
                # under a sender's name (live run: Google Chat showed "Mahmoud Ayman Sayed" 4 times, never his message).
                more, j = [], i + 1
                while j < len(lines) and len(more) < 3 and j not in hits and sum(map(len, more)) < 300:
                    if lines[j]:
                        more.append(lines[j][:300])
                    j += 1
                if more:
                    line += "  ->  " + " | ".join(more)
            out.append(line)
        if sum(len(x) + 1 for x in out) > limit:
            # Too many: keep the first and the LAST ones (in a chat the last are the newest messages; cutting the
            # end hid exactly the message the person asked about).
            head, tail, size = [], [], 0
            for x in out[1:]:
                if size + len(x) > limit // 3:
                    break
                head.append(x)
                size += len(x) + 1
            for x in reversed(out[1 + len(head):]):
                if size + len(x) > limit:
                    break
                tail.insert(0, x)
                size += len(x) + 1
            skipped = len(out) - 1 - len(head) - len(tail)
            out = [out[0]] + head + ([f"... ({skipped} more in between) ..."] if skipped else []) + tail
        if len(exact) > 1:
            out.append("(In page order: in a chat or a feed the last one is the newest.)")
        return "\n".join(out)
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) >= 3 or w.isdigit()]
    if not words:
        return ""
    scored = []
    for i, ln in enumerate(lines):
        low = ln.lower()
        hits = sum(1 for w in set(words) if w in low)
        if hits:
            scored.append((hits, i))
    if not scored:
        return ""
    best = max(h for h, _ in scored)
    # Keep the best matches first (all words > most words), then return them in page order.
    keep = sorted(scored, key=lambda x: (-x[0], x[1]))
    chosen: set[int] = set()
    size = 0
    for hits, i in keep:
        if hits < max(1, best - 1):
            break
        block = [j for j in (i - 1, i, i + 1) if 0 <= j < len(lines) and lines[j]]
        add = sum(len(lines[j]) + 1 for j in block if j not in chosen)
        if size + add > limit:
            break
        chosen.update(block)
        size += add
    out, prev = [], None
    for j in sorted(chosen):
        if prev is not None and j > prev + 1:
            out.append("...")
        out.append(lines[j][:1200])
        prev = j
    return "\n".join(out)


def _domain_match(host: str, domain: str) -> bool:
    host, domain = host.lstrip(".").lower(), domain.lstrip(".").lower()
    return bool(host) and (host == domain or host.endswith("." + domain) or domain.endswith("." + host))


def load_sessions(folder: Path | None, only: str | None = None) -> dict | None:
    """Saved logins: data/sessions/<domain>.json (Playwright storage state), each limited to its own website."""
    if not folder or not Path(folder).is_dir():
        return None
    cookies, origins = [], []
    for f in sorted(Path(folder).glob("*.json")):
        domain = f.stem
        if only and not _domain_match(only, domain):
            continue
        try:
            st = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        cookies += [c for c in st.get("cookies", []) if _domain_match(c.get("domain", ""), domain)]
        origins += [o for o in st.get("origins", [])
                    if _domain_match(urlparse(o.get("origin", "")).hostname or "", domain)]
    if not cookies and not origins:
        return None
    return {"cookies": cookies, "origins": origins}


class BrowserError(Exception):
    """A browser action failed. The message is shown to the model so it can try something else."""


@dataclass
class Snapshot:
    url: str
    title: str
    text: str                      # the list the model reads
    refs: dict[str, dict] = field(default_factory=dict)
    captcha: bool = False
    tables: int = 0                # data tables on the page (read_table can read them)

    def element(self, ref: str) -> dict:
        ref = ref.strip().lstrip("[").rstrip("]")
        if ref not in self.refs:
            raise BrowserError(f"There is no element {ref} on the current page. Use a ref from the latest page list.")
        return self.refs[ref]


def _short_href(href: str, page_url: str) -> str:
    if not href or href.startswith("javascript"):
        return ""
    try:
        p, base = urlparse(href), urlparse(page_url)
        if p.netloc and p.netloc != base.netloc:
            return p.netloc + (p.path[:40] if p.path not in ("", "/") else "")
        return (p.path or "/")[:50] + (("?" + p.query[:30]) if p.query else "")
    except ValueError:
        return href[:50]


SHOW_ITEMS = 120   # elements listed for the model; the rest keep their refs and are found with find_in_page


def format_snapshot(raw: dict) -> Snapshot:
    lines = [f"URL: {raw['url']}", f"Title: {raw.get('title') or '(none)'}"]
    sc = raw.get("scroll") or {}
    if sc.get("max"):
        pct = int(100 * sc["y"] / sc["max"]) if sc["max"] else 0
        lines.append(f"Scroll: {pct}% down the page")
    if raw.get("captcha"):
        lines.append("WARNING: this page shows a CAPTCHA / bot check. Do not try to solve it: use ask_user or another website.")
    if raw.get("dialog"):
        lines.append(f"A POP-UP IS OPEN and blocks the page: \"{raw['dialog']}\". Deal with it first: its buttons "
                     "are listed first (or keys {Escape} closes it).")
    if raw.get("heads"):
        lines.append("Headings: " + " | ".join(raw["heads"]))
    refs: dict[str, dict] = {it["ref"]: it for it in raw["items"]}
    main = [it for it in raw["items"] if it.get("frame_title") is None]
    if len(main) > SHOW_ITEMS:
        # Too many to list (shops with mega menus): keep the ones a task needs most. In view first; then form
        # controls and buttons (search box, filters, Add to Cart), then links in the content, then menu links.
        where = {"view": 0, "below": 1, "above": 2}

        def prio(it):
            link = it.get("role") in ("link", "clickable")
            return where.get(it.get("where"), 1), (2 if it.get("nav") else 1) if link else 0
        keep = {id(it) for it in sorted(main, key=prio)[:SHOW_ITEMS]}
    else:
        keep = {id(it) for it in main}
    shown = [it for it in raw["items"] if id(it) in keep or it.get("frame_title") is not None]
    total = raw.get("total", len(raw["items"]))
    lines.append(f"Elements ({len(shown)} of {total} shown):")
    last_where = "view"
    last_frame = None
    for it in shown:
        if it.get("frame_title") is not None and it.get("frame_title") != last_frame:
            lines.append(f"-- inside the embedded frame \"{it['frame_title']}\" (use these refs as usual) --")
            last_frame, last_where = it["frame_title"], "view"
        if it["where"] != last_where and it["where"] != "view":
            lines.append(f"-- {it['where']} the visible area (scroll to see) --")
            last_where = it["where"]
        line = f"{it['ref']} [{it['role']}] \"{it['name']}\""
        if it.get("value") and not it["role"].startswith("input:password"):
            line += f" value=\"{it['value']}\""
        if it.get("checked") is not None:
            line += " (checked)" if it["checked"] else " (not checked)"
        if it.get("group") and it["group"].lower() != str(it.get("name") or "").lower():
            line += f" in \"{it['group']}\""
        if it.get("options"):
            line += " options: " + ", ".join(it["options"][:8])
        if it.get("range"):
            line += f" (slider {it['range'][0]}-{it['range'][1]}: use type with a number)"
        if it.get("expanded") is not None:
            line += " (open)" if it["expanded"] else " (closed: click to open)"
        elif it.get("popup"):
            line += " (opens a menu)"
        if it["role"] == "canvas":
            line += " (a drawing: read it with look; use it with click and keys)"
        elif it["role"] == "draggable":
            line += " (drag it onto a drop area)"
        if it.get("disabled"):
            line += " (disabled)"
        href = _short_href(it.get("href", ""), raw["url"])
        if href and it["role"] == "link":
            line += f" -> {href}"
        lines.append(line)
    if total > len(shown):
        lines.append(f"-- {total - len(shown)} more elements are not listed: find_in_page with a few words of a "
                     "button or link (e.g. 'Add to Cart') gives their refs --")
    if raw.get("text"):
        lines.append("Page text (start): " + raw["text"])
    if raw.get("view"):
        lines.append("Text in view now (what is on screen; in a chat the newest messages are at the bottom): "
                     + raw["view"])
    return Snapshot(url=raw["url"], title=raw.get("title", ""), text="\n".join(lines), refs=refs,
                    captcha=bool(raw.get("captcha")), tables=int(raw.get("tables") or 0))


class BrowserSession:
    def __init__(self, task_id: str = "local", *, headless: bool = True, channel: str | None = None,
                 live_dir: Path | None = None, viewport=(1280, 860), downloads_dir: Path | None = None,
                 sessions_dir: Path | None = None, cdp: str | None = None):
        self.task_id = task_id
        # cdp: work in tabs of an Edge window that is already open (the agent's Edge, where the person signed in
        # themselves). Only this session's own tabs are used and closed; the window stays open.
        self.cdp = cdp
        self.downloads_dir = Path(downloads_dir) if downloads_dir else None
        self.sessions_dir = Path(sessions_dir) if sessions_dir else None
        self._pending_downloads: list = []
        self._owns_browser = True
        self.downloaded: list[dict] = []   # {name, path, size, url}
        self.live_source = None            # optional callable -> jpeg bytes (the desktop app while it is used)
        self.headless = headless
        self.channel = channel
        self.live_dir = Path(live_dir) / task_id if live_dir else None
        self.viewport = viewport
        self._pw = self._browser = self._context = None
        self.pages: list = []
        self._frames: list = []   # embedded frames listed in the last page list
        self.active = 0
        self.last: Snapshot | None = None
        self.dialogs: list[str] = []

    # -------------------------------------------------------------- lifecycle
    def start(self) -> "BrowserSession":
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        if self.cdp:
            try:
                self._browser = self._pw.chromium.connect_over_cdp(self.cdp, timeout=15000)
            except Exception as exc:
                self._pw.stop()
                self._pw = None
                raise BrowserError(f"The agent's Edge window is not open ({exc.__class__.__name__}).") from exc
            self._shared_context()
            self._on_page(self._context.new_page())
            return self
        kwargs = {"headless": self.headless}
        if self.channel and self.channel != "chromium":
            kwargs["channel"] = self.channel
        try:
            self._browser = self._pw.chromium.launch(**kwargs)
        except Exception as exc:
            if "channel" in kwargs:  # fall back to Playwright's own Chromium if Edge/Chrome is missing
                log.warning("Browser channel %s failed (%s); trying bundled Chromium", self.channel, exc)
                kwargs.pop("channel")
                self._browser = self._pw.chromium.launch(**kwargs)
            else:
                raise
        self._new_context()
        page = self._context.new_page()
        if not self.pages:
            self._on_page(page)
        return self

    def _new_context(self) -> None:
        state = load_sessions(self.sessions_dir) if self.sessions_dir else None
        self._context = self._browser.new_context(
            viewport={"width": self.viewport[0], "height": self.viewport[1]}, locale="en-US",
            accept_downloads=bool(self.downloads_dir), storage_state=state)
        self._context.set_default_timeout(15000)
        self._context.on("page", self._on_page)

    def _shared_context(self) -> None:
        """The agent's Edge: use its profile (the person's sign-ins there). Other tabs in that window are not ours:
        only tabs this session opens, and pop-ups from them, are followed."""
        self._context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        self._context.set_default_timeout(15000)

    def child(self, task_id: str) -> "BrowserSession":
        """A separate browser context (own tabs and history) in the same browser, for a helper agent.
        In the agent's Edge: own tabs in the same window (same sign-ins)."""
        c = BrowserSession(task_id, headless=self.headless, channel=self.channel, viewport=self.viewport,
                           downloads_dir=self.downloads_dir, sessions_dir=self.sessions_dir, cdp=self.cdp)
        c._pw, c._browser, c._owns_browser = self._pw, self._browser, False
        if self.cdp:
            c._context = self._context
        else:
            c._new_context()
        c._on_page(c._context.new_page())
        return c

    def _on_page(self, page) -> None:
        if page in self.pages:
            return
        page.on("dialog", self._on_dialog)
        page.on("download", self._on_download)
        if self.cdp:  # shared window: follow only pop-ups of our own tabs
            page.on("popup", self._on_page)
        self.pages.append(page)
        self.active = len(self.pages) - 1

    def _on_download(self, download) -> None:
        self._pending_downloads.append(download)  # saved (or blocked) after the action, in _save_downloads

    def _on_dialog(self, dialog) -> None:
        # alert/confirm/prompt: never accept automatically (a confirm can be "Delete?").
        self.dialogs.append(f"{dialog.type}: {dialog.message[:200]}")
        try:
            dialog.dismiss()
        except Exception:
            pass

    def close(self) -> None:
        if self.cdp:  # the agent's Edge: close only our tabs, leave the window (and its sign-ins) open
            for p in self.pages:
                try:
                    if not p.is_closed():
                        p.close()
                except Exception:
                    pass
            self.pages = []
            if self._owns_browser:
                try:
                    if self._pw:
                        self._pw.stop()  # disconnects; the Edge window keeps running
                except Exception:
                    pass
                self._pw = self._browser = None
            self._context = None
            return
        if not self._owns_browser:  # a helper's context: the browser belongs to the parent
            try:
                if self._context:
                    self._context.close()
            except Exception:
                pass
            self._context = None
            return
        for obj in (self._context, self._browser):
            try:
                if obj:
                    obj.close()
            except Exception:
                pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self._browser = self._context = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()

    # -------------------------------------------------------------- helpers
    @property
    def page(self):
        self.pages = [p for p in self.pages if not p.is_closed()]
        if not self.pages:
            self._on_page(self._context.new_page())
        self.active = min(self.active, len(self.pages) - 1)
        return self.pages[self.active]

    @property
    def url(self) -> str:
        try:
            return self.page.url
        except Exception:
            return ""

    def settle(self, quick: bool = False) -> None:
        p = self.page
        try:
            p.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        try:
            p.wait_for_load_state("networkidle", timeout=1200 if quick else 3000)
        except Exception:
            pass

    def _locator(self, ref: str):
        if self.last is None:
            raise BrowserError("Read the page first.")
        el = self.last.element(ref)
        root = self.page
        if el.get("frame") is not None:  # an element inside an embedded frame
            frames = getattr(self, "_frames", [])
            if el["frame"] >= len(frames) or frames[el["frame"]].is_detached():
                raise BrowserError(f"Element {ref} was in a frame that is gone. Look at the new page list.")
            root = frames[el["frame"]]
        loc = root.locator(f'[data-wa-ref="{ref}"]')
        if loc.count() == 0:
            raise BrowserError(f"Element {ref} is no longer on the page (the page changed). Look at the new page list.")
        return loc.first

    def _body_lines(self) -> list[str]:
        try:
            text = self.page.evaluate("() => (document.body ? document.body.innerText : '').slice(0, 30000)") or ""
        except Exception:
            return []
        return [l.strip() for l in text.splitlines() if l.strip()]

    def _after_action(self, before_url: str, before_pages: int, before_lines: list[str] | None = None) -> str:
        self.settle(quick=True)
        for _ in range(4):  # let Playwright deliver a "new tab" event (it only arrives while we call into it)
            if len(self.pages) > before_pages or self._pending_downloads:
                break
            try:
                self.pages[self.active].wait_for_timeout(150)
            except Exception:
                break
        notes = []
        self._drop_browser_pages()
        saved = self._save_downloads()
        if saved:
            notes.append(saved)
        if len(self.pages) > before_pages:
            self.active = len(self.pages) - 1
            notes.append(f"A new tab opened and is now active ({len(self.pages)} tabs).")
            self.settle()
        if self.url != before_url:
            notes.append(f"Now at {self.url}")
        elif before_lines is not None:
            # Same page: show the text that appeared (confirmations, errors, opened menus).
            old = set(before_lines)
            after = self._body_lines()
            new = [l for l in after if l not in old]
            if new:
                notes.append("New text on the page: " + " | ".join(new)[:400])
            elif after != before_lines:
                # same text in a new order: a card moved to another column, a list was sorted (eval: a successful
                # drag reported as "nothing changed" made the agent drag the card on, into the wrong column)
                notes.append("The page changed: the same text is now in a different order.")
            else:
                notes.append("No visible text changed on the page.")
        empty = self._no_results_line()
        if empty:
            # Live run (Gmail): "No messages matched your search" was not seen as an answer, and the agent retyped
            # the same search four times until the time ran out.
            notes.append(f'The page says: "{empty}". If your search words were right, that is the answer: none. '
                         "Do not retype the same search in other forms.")
        if self.dialogs:
            notes.append("A browser dialog was dismissed: " + self.dialogs.pop())
        return " ".join(notes) or "Done."

    NO_RESULTS = re.compile(r"\b(no (messages|results|items|products|matches|emails|records|conversations) "
                            r"(matched|found|match)|did not match any|no results (for|found)|"
                            r"(found|showing|there are) 0 (results|items|matches)|0 results (for|found)|"
                            r"لا توجد نتائج|لم يتم العثور على|لم نعثر على|لا نتائج|لا توجد (رسائل|منتجات|عناصر)|"
                            r"0 نتيجة|٠ نتيجة)\b", re.I)

    def _no_results_line(self) -> str:
        try:
            lines = self._body_lines()
        except Exception:
            return ""
        for line in lines:
            if len(line) < 220 and self.NO_RESULTS.search(line):
                return line.strip()[:200]
        return ""

    def _drop_browser_pages(self) -> None:
        """Edge opens its own "Downloads" tab (edge://downloads-hub) after a download: ignore it and stay on the
        page (closing that internal tab hangs Playwright)."""
        def keep(p) -> bool:
            try:
                return not p.is_closed() and not p.url.startswith(("edge://", "chrome://"))
            except Exception:
                return False
        alive = [p for p in self.pages if keep(p)]
        if len(alive) != len(self.pages):
            current = self.pages[self.active] if self.active < len(self.pages) else None
            self.pages = alive
            self.active = alive.index(current) if current in alive else max(0, len(alive) - 1)

    def _save_downloads(self) -> str:
        from webagent import files

        out = []
        while self._pending_downloads:
            d = self._pending_downloads.pop(0)
            name = files.safe_name(d.suggested_filename or "download")
            if not self.downloads_dir or files.blocked(name):
                try:
                    d.cancel()
                except Exception:
                    pass
                out.append(f"The download of {name} was blocked (program files are never downloaded).")
                continue
            try:
                path = files.unique_path(self.downloads_dir, name)
                d.save_as(str(path))
                size = path.stat().st_size
                self.downloaded.append({"name": path.name, "path": str(path), "size": size, "url": d.url})
                out.append(f"Downloaded the file {path.name} ({files.human_size(size)}). Read it with read_file.")
            except Exception as exc:
                out.append(f"The download of {name} failed: {str(exc).splitlines()[0][:150]}")
        return " ".join(out)

    # -------------------------------------------------------------- observe
    def snapshot(self, max_items: int = 400) -> Snapshot:
        self._drop_browser_pages()
        p = self.page
        try:
            raw = p.evaluate(SNAPSHOT_JS, {"max": max_items, "textChars": 1500})
        except Exception as exc:
            # Navigation in progress: wait and try once more.
            self.settle()
            try:
                raw = p.evaluate(SNAPSHOT_JS, {"max": max_items, "textChars": 1500})
            except Exception:
                raise BrowserError(f"Could not read the page: {str(exc)[:200]}") from exc
        self._frames = []
        self._add_frames(p, raw)
        if len(self.pages) > 1:
            raw["title"] = f"{raw.get('title', '')}  [tab {self.active + 1} of {len(self.pages)}]"
        self.last = format_snapshot(raw)
        return self.last

    def _add_frames(self, p, raw: dict, max_frames: int = 4) -> None:
        """Embedded frames (sign-up widgets, payment and booking forms, maps...): list their elements too, with
        refs that go on from the page's. Tiny or hidden frames (trackers, ads) are skipped."""
        start = len(raw["items"]) and max(int(it["ref"][1:]) for it in raw["items"])
        for fr in p.frames[1:]:
            if len(self._frames) >= max_frames:
                break
            try:
                if fr.is_detached() or fr.url.startswith(("about:", "javascript:")) and not fr.child_frames:
                    continue
                el = fr.frame_element()
                box = el.bounding_box()
                if not box or box["width"] < 80 or box["height"] < 40:
                    continue
                title = el.get_attribute("title") or el.get_attribute("name") or (urlparse(fr.url).hostname or "frame")
                sub = fr.evaluate(SNAPSHOT_JS, {"max": 40, "textChars": 300, "start": start})
            except Exception:
                continue
            if not sub.get("items"):
                continue
            idx = len(self._frames)
            self._frames.append(fr)
            for it in sub["items"]:
                it["frame"], it["frame_title"] = idx, title
                raw["items"].append(it)
            start = max(int(it["ref"][1:]) for it in sub["items"])
            raw["total"] = raw.get("total", 0) + sub.get("total", 0)
            if sub.get("text"):
                raw["text"] = (raw.get("text") or "") + f" [frame \"{title}\": {sub['text'][:300]}]"

    def read_text(self, part: int = 1, size: int = 6000) -> str:
        text = self.page.evaluate(READ_JS) or ""
        parts = max(1, (len(text) + size - 1) // size)
        part = max(1, min(part, parts))
        chunk = text[(part - 1) * size: part * size]
        out = f"Page text, part {part} of {parts} ({self.url}):\n{chunk}"
        if part < parts:  # models tend to scroll here, which does not change this text
            out += (f"\n[End of part {part} of {parts}. Scrolling does not change this text: call read_page with "
                    f"part={part + 1} for more, or find_in_page with key words.]")
        return out

    def full_text(self, max_chars: int = 40000) -> str:
        return (self.page.evaluate(READ_JS) or "")[:max_chars]

    def find_elements(self, query: str, limit: int = 12) -> list[str]:
        """Elements of the latest page list whose name has the words (also the ones not listed to the model)."""
        if not self.last or self.last.url != self.url:
            try:
                self.snapshot()
            except BrowserError:
                return []
        q = query.lower().strip()
        words = [w for w in re.findall(r"\w+", q) if len(w) > 1 or w.isdigit()]
        hits = []
        for it in self.last.refs.values():
            name = str(it.get("name") or "").lower()
            if not name:
                continue
            exact = q in name
            if exact or (words and all(w in name for w in words)):
                hits.append((0 if exact else 1, len(name), it))
        hits.sort(key=lambda h: (h[0], h[1]))
        return [f'{it["ref"]} [{it["role"]}] "{it["name"]}"' + (" (disabled)" if it.get("disabled") else "")
                for _, _, it in hits[:limit]]

    def find_text(self, query: str, limit: int = 3500) -> str:
        text = self.page.evaluate(READ_JS) or ""
        found = find_passages(text, query, limit)
        els = self.find_elements(query)
        head = (f"Elements that match \"{query}\" (use these refs):\n" + "\n".join(els) + "\n") if els else ""
        if not found:
            return head + f"No paragraph on this page ({self.url}) contains: {query}." + (
                "" if els else " Try other words or read_page.")
        return head + f"Paragraphs that match \"{query}\" ({self.url}):\n{found}"

    def tables(self, words: str = "", index: int = 0) -> str:
        found = self.page.evaluate(TABLES_JS) or []
        if not found:
            return (f"There is no data table on this page ({self.url}). Use read_page or find_in_page (comparison "
                    "pages are often built from columns, not tables), or look.")
        if index:
            if not 1 <= index <= len(found):
                return f"There are {len(found)} tables; use index 1 to {len(found)}."
            pick = [found[index - 1]]
        elif words:
            ws = [w for w in re.findall(r"\w+", words.lower()) if len(w) > 2 or w.isdigit()]

            def score(t):
                hay = (t["caption"] + " " + " ".join(" ".join(r) for r in t["rows"][:40])).lower()
                return sum(w in hay for w in ws)
            pick = sorted(found, key=score, reverse=True)[:1]
        else:
            pick = found[:2]
        head = f"{len(found)} table(s) on {self.url}."
        others = [f"{i + 1}. {t['caption'] or '(no title)'}" for i, t in enumerate(found[:12]) if t not in pick]
        if others:
            head += " Other tables (use index): " + "; ".join(others)
        return head + "\n" + "\n\n".join(format_table(t, 6000 // len(pick)) for t in pick)

    def fill_form(self, fields: list[dict]) -> str:
        """Fill several fields at once: text fields, dropdowns (option text), checkboxes and radios (true/false)."""
        done, problems = [], []
        for f in fields:
            ref, value = str(f.get("ref", "")), str(f.get("value", ""))
            try:
                el = self.last.element(ref) if self.last else {}
                loc = self._locator(ref)
                role = el.get("role", "")
                if role == "select" or el.get("options"):
                    try:
                        loc.select_option(label=value, timeout=5000)
                    except Exception:
                        loc.select_option(value=value, timeout=5000)
                elif role in ("input:checkbox", "input:radio", "checkbox", "radio", "switch"):
                    on = value.strip().lower() not in ("false", "no", "0", "off", "uncheck", "")
                    loc.set_checked(on, timeout=5000, force=True)
                    value = "checked" if on else "not checked"
                elif role == "input:file":
                    raise BrowserError("this is a file field: use upload_file")
                else:
                    loc.scroll_into_view_if_needed(timeout=5000)
                    loc.fill(value, timeout=8000)
                done.append(f"{ref} ({el.get('name') or role}) = {value}")
            except BrowserError as exc:
                problems.append(f"{ref}: {exc}")
            except Exception as exc:
                problems.append(f"{ref}: {str(exc).splitlines()[0][:150]}")
        self.settle(quick=True)
        out = (f"Filled {len(done)} field(s): " + "; ".join(done)) if done else "No field was filled."
        if problems:
            out += " | Problems: " + "; ".join(problems)
        return out + " (Nothing was sent yet: click the form's button to send it.)"

    def upload(self, ref: str, path: Path) -> str:
        loc = self._locator(ref)
        el = self.last.element(ref)
        try:
            if el.get("role") == "input:file":
                loc.set_input_files(str(path), timeout=8000)
            else:  # a styled button that opens the file chooser
                with self.page.expect_file_chooser(timeout=8000) as fc:
                    loc.click(timeout=8000)
                fc.value.set_files(str(path))
        except Exception as exc:
            raise BrowserError(f"Could not upload into {ref}: {str(exc).splitlines()[0][:200]}") from exc
        self.settle(quick=True)
        return f"Attached {path.name} to {ref}. (Not sent yet: click the form's button to send it.)"

    def fetch_file(self, url: str) -> Path:
        """Download a file by its address with the browser's cookies (a PDF link, a CSV export...)."""
        from webagent import files

        if not self.downloads_dir:
            raise BrowserError("Downloads are switched off.")
        try:
            resp = self._context.request.get(url, timeout=30000)
        except Exception as exc:
            raise BrowserError(f"Could not download {url}: {str(exc).splitlines()[0][:200]}") from exc
        if not resp.ok:
            raise BrowserError(f"Could not download {url}: HTTP {resp.status}")
        name = files.safe_name(urlparse(url).path.rsplit("/", 1)[-1] or "download")
        ctype = (resp.headers.get("content-type") or "").lower()
        if "." not in name:
            name += ".pdf" if "pdf" in ctype else (".html" if "html" in ctype else ".txt")
        if files.blocked(name):
            raise BrowserError(f"{name} is a program file: it is never downloaded.")
        path = files.unique_path(self.downloads_dir, name)
        path.write_bytes(resp.body())
        self.downloaded.append({"name": path.name, "path": str(path), "size": path.stat().st_size, "url": url})
        return path

    def load_login(self, domain: str) -> bool:
        """Add a saved login (made by the person in a login window) to this browser, without restarting it."""
        if self.cdp:  # the agent's Edge: the person signed in right in this window; the profile keeps it
            try:
                return any(_domain_match(c.get("domain", ""), domain) for c in self._context.cookies())
            except Exception:
                return False
        state =load_sessions(self.sessions_dir, only=domain) if self.sessions_dir else None
        if not state:
            return False
        if state["cookies"]:
            self._context.add_cookies(state["cookies"])
        for origin in state["origins"]:
            items = {i["name"]: i["value"] for i in origin.get("localStorage", [])}
            self._context.add_init_script(
                "(() => { const o = %s; if (location.origin === o.origin) for (const [k, v] of Object.entries(o.items))"
                " { try { localStorage.setItem(k, v); } catch (e) {} } })()" % json.dumps(
                    {"origin": origin["origin"], "items": items}))
        return True

    def screenshot(self, full_page: bool = False, quality: int = 60) -> bytes:
        if self.live_source is not None:  # the desktop app the agent is using
            try:
                data = self.live_source()
                if data:
                    return data
            except Exception:
                pass
        return self.page.screenshot(type="jpeg", quality=quality, full_page=full_page, timeout=15000)

    def save_live(self) -> Path | None:
        if not self.live_dir:
            return None
        try:
            self.live_dir.mkdir(parents=True, exist_ok=True)
            data = self.screenshot()
            tmp = self.live_dir / "latest.tmp"
            tmp.write_bytes(data)
            target = self.live_dir / "latest.jpg"
            tmp.replace(target)
            return target
        except Exception as exc:  # live view must never stop a task
            log.debug("live screenshot failed: %s", exc)
            return None

    # -------------------------------------------------------------- act
    def goto(self, url: str) -> str:
        url = url.strip()
        if not re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I):
            url = "https://" + url
        # Opening the address the tab is already on means "reload". With a "#" in it, page.goto only jumps inside
        # the page and nothing is fetched again (eval 24 Nov: a chat at "inbox.html#" was "reloaded" twice and the
        # new message never showed).
        same = "#" in url and url == (self.url or "")
        for attempt in (1, 2):
            try:
                if same:
                    self.page.reload(wait_until="domcontentloaded", timeout=30000)
                else:
                    self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
                break
            except Exception as exc:
                # A redirect or a late error page can interrupt the navigation: try once more.
                if attempt == 1 and "interrupted by another navigation" in str(exc):
                    self.page.wait_for_timeout(500)
                    continue
                raise BrowserError(f"Could not open {url}: {str(exc).splitlines()[0][:200]}") from exc
        self.settle()
        empty = self._no_results_line()
        if empty:
            return (f'Opened {self.url} The page says: "{empty}". If your search words were right, that is the '
                    "answer: none. Do not retype the same search in other forms.")
        return f"Opened {self.url}"

    def click(self, ref: str) -> str:
        loc = self._locator(ref)
        before_url, before_pages, before_lines = self.url, len(self.pages), self._body_lines()
        try:
            loc.scroll_into_view_if_needed(timeout=5000)
            loc.click(timeout=8000)
        except Exception as exc:
            msg = str(exc).splitlines()[0][:200]
            drawing = ((self.last.refs.get(ref) if self.last else None) or {}).get("role") == "canvas"
            try:  # covered by an overlay: a direct DOM click often still works; a drawing needs a real mouse click
                loc.click(timeout=5000, force=True) if drawing else loc.evaluate("el => el.click()")
            except Exception:
                raise BrowserError(f"Could not click {ref}: {msg}") from exc
        out = self._after_action(before_url, before_pages, before_lines)
        it = (self.last.refs.get(ref) if self.last else None) or {}
        if it.get("role") == "canvas" and self.url == before_url:
            return ("You clicked the middle of a drawing: in a spreadsheet that selects a cell in the middle, not A1. "
                    "Go to the first cell with keys {Ctrl+Home} (or type A1 in the Name box), then type with keys. "
                    "A drawing shows no text: check it with look.")
        # Ticking a box changes no text: say what the click did, so the agent does not click it again.
        if self.url == before_url:
            try:
                state = loc.evaluate(STATE_JS, timeout=2000)
            except Exception:
                state = None
            if state is not None:
                it = (self.last.refs.get(ref) if self.last else None) or {}
                what = f'{ref} "{it.get("name", "")}"' + (f' in "{it["group"]}"' if it.get("group") else "")
                unchanged = out.startswith("No visible text changed")
                out = f"{what} is now {'checked' if state else 'NOT checked'}. " + ("" if unchanged else out)
                apply = self._apply_button(ref) if unchanged else ""
                if apply:
                    # eval: the agent ticked "16 GB", never pressed Go, and took the unfiltered list as filtered
                    out += (f"Nothing else on the page changed: if this is a filter, it is not applied yet. Click "
                            f"{apply} to apply it, then check that the list changed.")
        return out.strip()

    def _apply_button(self, ref: str) -> str:
        """The button that applies a filter ("Go", "Apply"), looked for near the box that was ticked (not the
        search box's "Go" in the header). Returns 'e12 "Go"', or ''."""
        try:
            return self._locator(ref).evaluate(r"""el => {
              const re = /^(go|apply|apply filters?|update|update results|filter|show results|refine|done)$/i;
              let p = el.parentElement;
              for (let i = 0; p && p !== document.body && i < 6; i++, p = p.parentElement) {
                const b = Array.from(p.querySelectorAll('button,input[type=submit],input[type=button],[role=button]'))
                  .find(x => re.test((x.innerText || x.value || x.getAttribute('aria-label') || '').trim()));
                if (b) return ((b.getAttribute('data-wa-ref') || '') + ' "' + (b.innerText || b.value || '').trim() + '"').trim();
              }
              return '';
            }""", timeout=2000) or ""
        except Exception:
            return ""

    def item_text(self, ref: str) -> str:
        """The text of the item a button belongs to: the smallest product card (or buy box) around it with some
        detail, plus the page heading when the card does not hold it. Used to check an "Add to cart" target."""
        try:
            return self._locator(ref).evaluate(r"""el => {
              const clean = s => (s || '').replace(/\s+/g, ' ').trim();
              const own = clean(el.innerText || el.value || '');
              const h1 = clean((document.querySelector('h1') || {}).innerText || '');
              let p = el.parentElement, best = '';
              for (let i = 0; p && p !== document.body && i < 8; i++, p = p.parentElement) {
                const t = clean(p.innerText);
                if (t.length > 1500) break;
                best = t;
                if (t.replace(own, '').trim().length >= 50 && /\d/.test(t)) break;
              }
              best = best.slice(0, 900);
              return (h1 && !best.includes(h1) ? h1.slice(0, 150) + ' | ' : '') + best;
            }""", timeout=2000) or ""
        except Exception:
            return ""

    def type(self, ref: str, text: str, submit: bool = False) -> str:
        el = self.last.element(ref) if self.last else {}
        role = str(el.get("role") or "")
        if role == "canvas":  # a drawn app (spreadsheet, editor): only the keyboard reaches it
            return self.keys(text + ("\n" if submit else ""), ref)
        if role in ("input:range", "slider") or el.get("range"):
            return self._set_slider(ref, text)
        if role == "textbox" and not role.startswith("input") and "\n" in text:
            return self.keys(text, ref, clear=True)  # rich text editor: real line breaks
        loc = self._locator(ref)
        before_url, before_pages = self.url, len(self.pages)
        before_lines = self._body_lines() if submit else None
        try:
            loc.scroll_into_view_if_needed(timeout=5000)
            try:
                loc.fill(text, timeout=8000)
            except Exception:
                loc.click(timeout=5000)
                self.page.keyboard.press("Control+A")
                self.page.keyboard.type(text, delay=10)
            if submit:
                loc.press("Enter")
        except Exception as exc:
            raise BrowserError(f"Could not type into {ref}: {str(exc).splitlines()[0][:200]}") from exc
        warn = ""
        if role.startswith("input") and ("\t" in text or "\n" in text):
            warn = (" Note: this is a one-line field, so tabs and line breaks were lost. To fill a grid or a "
                    "spreadsheet, use keys (Tab between cells, Enter at the end of a row).")
        if submit:
            return "Typed and pressed Enter. " + self._after_action(before_url, before_pages, before_lines) + warn
        return f"Typed into {ref}.{warn}"

    def _set_slider(self, ref: str, value: str) -> str:
        loc = self._locator(ref)
        try:
            got = loc.evaluate("""(el, v) => {
              if (el.type === 'range') {
                const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                setter.call(el, v);
                el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true}));
                return el.value;
              }
              return null;
            }""", value.strip())
            if got is None:  # an ARIA slider: move it with the keyboard
                loc.click(timeout=5000)
                self.page.keyboard.press("Home")
                target = float(value)
                for _ in range(200):
                    now = float(loc.get_attribute("aria-valuenow") or 0)
                    if now >= target:
                        break
                    self.page.keyboard.press("ArrowRight")
                got = loc.get_attribute("aria-valuenow")
        except Exception as exc:
            raise BrowserError(f"Could not set the slider {ref}: {str(exc).splitlines()[0][:200]}") from exc
        self.settle(quick=True)
        try:
            shown = loc.evaluate("""el => el.getAttribute('aria-valuetext') || (el.labels && el.labels[0] &&
                                   el.labels[0].innerText) || ''""") or ""
            top = loc.evaluate("el => el.max || el.getAttribute('aria-valuemax') || ''") or ""
        except Exception:
            shown, top = "", ""
        out = f"The slider {ref} is now at {got}" + (f' (it shows "{shown.strip()[:60]}")' if shown.strip() else "")
        if str(got).strip() != value.strip():
            out += (f". It did not take {value}" + (f": its steps go up to {top}, they are not {value} units" if top
                                                    else "") +
                    ". Check what it shows. For prices, the site's price links, min/max boxes, or the price in the "
                    "address (go_to) are more reliable than a slider.")
        return out + "."

    KEY_TOKEN = re.compile(r"\{([A-Za-z]+(?:\+[A-Za-z0-9]+)*)\}|\t|\n")
    # The names people (and models) write -> Playwright's names ("Ctrl" is unknown to Playwright).
    KEY_NAMES = {"ctrl": "Control", "control": "Control", "cmd": "Meta", "command": "Meta", "win": "Meta",
                 "meta": "Meta", "alt": "Alt", "option": "Alt", "shift": "Shift", "esc": "Escape", "escape": "Escape",
                 "del": "Delete", "delete": "Delete", "return": "Enter", "enter": "Enter", "tab": "Tab",
                 "backspace": "Backspace", "space": "Space", "home": "Home", "end": "End", "pageup": "PageUp",
                 "pagedown": "PageDown", "pgup": "PageUp", "pgdn": "PageDown", "up": "ArrowUp", "down": "ArrowDown",
                 "left": "ArrowLeft", "right": "ArrowRight", "arrowup": "ArrowUp", "arrowdown": "ArrowDown",
                 "arrowleft": "ArrowLeft", "arrowright": "ArrowRight", "insert": "Insert"}

    @classmethod
    def key_name(cls, combo: str) -> str:
        """'ctrl+home' -> 'Control+Home', 'Ctrl+a' -> 'Control+a', 'f5' -> 'F5'."""
        out = []
        for k in combo.split("+"):
            low = k.lower()
            out.append(cls.KEY_NAMES.get(low) or (k.upper() if re.fullmatch(r"f\d{1,2}", low) else k))
        return "+".join(out)

    def keys(self, text: str, ref: str | None = None, clear: bool = False) -> str:
        """Type like a person at the keyboard, into whatever has focus (after clicking `ref`, if given).
        Tab and line breaks are real key presses (\\t = Tab, \\n = Enter); {Ctrl+A}, {Backspace}, {ArrowDown}...
        press that key. This is how spreadsheets, canvas apps and rich editors get text."""
        before_url, before_pages, before_lines = self.url, len(self.pages), self._body_lines()
        kb = self.page.keyboard
        el = (self.last.refs.get(ref) if (ref and self.last) else None) or {}
        # A table typed into a drawn grid: clicking the grid lands in a middle cell (eval: tables at C8/D8), so start
        # at the first cell like a person would, unless the agent chose the place itself ({Ctrl+Home}, {ArrowDown}..).
        table = el.get("role") == "canvas" and ("\t" in text or "\n" in text)
        to_start = table and not text.startswith("{")
        if table and not text.endswith(("\n", "\t", "}")):
            text += "\n"   # the last cell stays in edit mode (not saved) until Enter, as in Google Sheets / Excel
        try:
            if ref:
                loc = self._locator(ref)
                loc.scroll_into_view_if_needed(timeout=5000)
                try:
                    loc.click(timeout=8000 if el.get("role") != "canvas" else 3000)
                except Exception:
                    if el.get("role") != "canvas":
                        raise
                    # Google Sheets lays a clear layer over its drawn grid: click where a person would click
                    loc.click(timeout=5000, force=True)
                if clear:
                    kb.press("Control+A")
                    kb.press("Delete")
                if to_start:
                    kb.press("Control+Home")
            pos, typed = 0, 0
            for m in self.KEY_TOKEN.finditer(text):
                if m.start() > pos:
                    kb.type(text[pos:m.start()], delay=5)
                    typed += m.start() - pos
                tok = m.group(0)
                kb.press("Tab" if tok == "\t" else "Enter" if tok == "\n" else self.key_name(m.group(1)))
                pos = m.end()
            if pos < len(text):
                kb.type(text[pos:], delay=5)
                typed += len(text) - pos
        except Exception as exc:
            msg = str(exc).splitlines()[0][:200]
            if "Timeout" in msg:
                msg += (". Something covers it, often a pop-up: close the pop-up first (its OK/Close button, or "
                        "keys {Escape} with no ref), then try again.")
            raise BrowserError(f"Could not type with the keyboard: {msg}") from exc
        rows = text.count("\n")
        after = self._after_action(before_url, before_pages, before_lines)
        try:
            # the grid itself, the focus on a canvas, or a page that draws its content (Sheets types into a hidden box)
            drawn = el.get("role") == "canvas" or \
                any(it.get("role") == "canvas" for it in (self.last.refs.values() if self.last else [])) or \
                self.page.evaluate("() => (document.activeElement || {}).tagName === 'CANVAS'")
        except Exception:
            drawn = False
        if drawn and after.startswith("No visible text changed"):
            after = "The app is a drawing, so typed cells do not show as page text: check the result with look."
        elif drawn:
            after += " The app is a drawing, so typed cells may not show as page text: check the result with look."
        return (f"Typed {typed} characters with the keyboard" + (f" ({rows} line breaks / rows)" if rows else "") +
                (", starting at the first cell (Ctrl+Home)" if to_start else "") +
                ". " + after + ("" if drawn else " Check the result (read_page, or look for drawn apps)."))

    def hover(self, ref: str) -> str:
        """Move the mouse over an element: menus that open on hover show their links in the next page list."""
        loc = self._locator(ref)
        before_url, before_pages, before_lines = self.url, len(self.pages), self._body_lines()
        try:
            loc.scroll_into_view_if_needed(timeout=5000)
            loc.hover(timeout=8000)
        except Exception as exc:
            raise BrowserError(f"Could not hover over {ref}: {str(exc).splitlines()[0][:200]}") from exc
        time.sleep(0.3)
        return self._after_action(before_url, before_pages, before_lines)

    def drag(self, ref: str, to_ref: str) -> str:
        """Drag one element and drop it on another (kanban cards, sortable lists, file drop zones)."""
        src, dst = self._locator(ref), self._locator(to_ref)
        before_url, before_pages, before_lines = self.url, len(self.pages), self._body_lines()
        try:
            handles = [src.element_handle(timeout=5000), dst.element_handle(timeout=5000)]
        except Exception:
            handles = None
        try:
            src.scroll_into_view_if_needed(timeout=5000)
            src.drag_to(dst, timeout=10000)
        except Exception as exc:
            try:  # step by step with the mouse (some boards only react to real mouse moves)
                a, b = src.bounding_box(), dst.bounding_box()
                m = self.page.mouse
                m.move(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
                m.down()
                for i in range(1, 11):
                    m.move(a["x"] + (b["x"] - a["x"]) * i / 10 + a["width"] / 2,
                           a["y"] + (b["y"] - a["y"]) * i / 10 + b["height"] / 2)
                m.up()
            except Exception:
                raise BrowserError(f"Could not drag {ref} to {to_ref}: {str(exc).splitlines()[0][:200]}") from exc
        out = self._after_action(before_url, before_pages, before_lines)
        if handles and self.url == before_url:
            try:  # say plainly where the dragged element is now: text order alone is easy to misread
                inside, what, where = self.page.evaluate(
                    "([s, d]) => [d.contains(s), (s.innerText || '').trim().split('\\n')[0].slice(0, 60),"
                    " (d.getAttribute('aria-label') || d.dataset.col || (d.querySelector('h1,h2,h3,h4,[role=heading]') || d)"
                    ".innerText || '').trim().split('\\n')[0].slice(0, 60)]", handles)
                out += (f' Done: "{what}" is now inside "{where}". Do not drag it again.' if inside else
                        f' "{what}" is not inside "{where}" (fine for re-ordering a list; for a column, check where it is).')
            except Exception:
                pass
        return out

    def select(self, ref: str, option: str) -> str:
        loc = self._locator(ref)
        try:
            try:
                loc.select_option(label=option, timeout=5000)
            except Exception:
                loc.select_option(value=option, timeout=5000)
        except Exception as exc:
            raise BrowserError(f"Could not select '{option}' in {ref}: {str(exc).splitlines()[0][:200]}") from exc
        self.settle(quick=True)
        return f"Selected '{option}'."

    def press(self, key: str) -> str:
        before_url, before_pages, before_lines = self.url, len(self.pages), self._body_lines()
        try:
            self.page.keyboard.press(self.key_name(key.strip().strip("{}")))
        except Exception as exc:
            raise BrowserError(f"Could not press {key}: {str(exc).splitlines()[0][:200]}") from exc
        return self._after_action(before_url, before_pages, before_lines)

    def scroll(self, direction: str = "down") -> str:
        """Scroll the part of the page that scrolls: the page itself, or the inner pane under the middle of the
        screen (chats, mail, web apps). The wheel used to turn wherever the mouse was, so in Google Chat nothing
        moved and the agent was told "Scrolled down." anyway."""
        up = direction.lower().startswith("up")
        dy = -700 if up else 700
        word = "up" if up else "down"
        p = self.page
        try:
            t = p.evaluate(SCROLL_PICK_JS)
        except Exception:
            t = None
        if not t:
            p.mouse.wheel(0, dy)
            time.sleep(0.4)
            return f"Scrolled {word}."
        area = "the page" if t["doc"] else (f'the "{t["name"]}" area' if t.get("name") else "the scrolling area")
        if (up and t["top"] <= 1) or (not up and t["top"] >= t["max"] - 1):
            return (f"Could not scroll {word}: {area} is already at the {'top' if up else 'bottom'} "
                    f"(nothing more {'above' if up else 'below'}).")
        try:
            p.mouse.move(t["x"], t["y"])
            p.mouse.wheel(0, dy)
            time.sleep(0.4)
            now = p.evaluate(SCROLL_STATE_JS)
            if now is not None and abs(now["top"] - t["top"]) < 2:   # the wheel did not reach it: scroll it directly
                p.evaluate(SCROLL_BY_JS, dy)
                time.sleep(0.3)
                now = p.evaluate(SCROLL_STATE_JS)
        except Exception:
            now = None
        if not now:
            return f"Scrolled {word}."
        if abs(now["top"] - t["top"]) < 2:
            return f"Could not scroll {word}: {area} did not move."
        pct = int(100 * now["top"] / now["max"]) if now["max"] else 100
        end = " (the bottom)" if now["top"] >= now["max"] - 1 else " (the top)" if now["top"] <= 1 else ""
        return f"Scrolled {word} {area}: now {pct}% down{end}."

    def back(self) -> str:
        before = self.url
        try:
            self.page.go_back(wait_until="commit", timeout=10000)
        except Exception as exc:
            try:
                self.page.evaluate("() => history.back()")
                self.page.wait_for_timeout(1500)
            except Exception:
                pass
            if self.url == before:
                raise BrowserError(f"Could not go back: {str(exc).splitlines()[0][:200]}. Use go_to with the "
                                   "address you need (search pages keep their filters in the address).") from exc
        self.settle(quick=True)
        return f"Went back to {self.url}"

    def switch_tab(self, index: int) -> str:
        pages = [p for p in self.pages if not p.is_closed()]
        if not 1 <= index <= len(pages):
            raise BrowserError(f"There are {len(pages)} tabs; use a number from 1 to {len(pages)}.")
        self.pages, self.active = pages, index - 1
        self.page.bring_to_front()
        return f"Switched to tab {index}: {self.url}"
