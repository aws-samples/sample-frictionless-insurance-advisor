/*
 * Pitch deck localisation. A small classic script: no build step, no framework.
 *
 * The English text stays in index.html as the built-in fallback. This script picks a language,
 * fetches ./i18n/<code>.json and swaps the text of every [data-i18n] element, the SVG labels,
 * the attributes listed in [data-i18n-attr] and the <title>.
 *
 * Language priority (first match wins; only supported codes count; de-AT and zh-CN map to de and zh):
 *   1. ?lang=<code> in the URL
 *   2. the viewer's previous choice in this deck's picker (localStorage "deckLang")
 *   3. the app's language (localStorage "i18nextLng", read only)
 *   4. navigator.languages / navigator.language
 *   5. English
 *
 * To add a language: add a code to SUPPORTED, an <option> to #deck-lang in index.html and
 * an i18n/<code>.json with the same keys as i18n/en.json.
 */
(function (root) {
  'use strict';

  var SUPPORTED = ['en', 'ja', 'ko', 'es', 'fr', 'de', 'zh', 'ms', 'th', 'id'];
  var DEFAULT_LANG = 'en';
  var DECK_KEY = 'deckLang';
  var APP_KEY = 'i18nextLng';
  // Tags allowed in data-i18n-html values. Anything else is replaced by its text.
  var ALLOWED_TAGS = { SPAN: 1, STRONG: 1, EM: 1, BR: 1, CODE: 1 };

  /** Map "de", "de-AT", "zh_CN", "ZH-hans" to a supported base code, or null. */
  function normalizeLang(value) {
    if (typeof value !== 'string') return null;
    var base = value.trim().toLowerCase().split(/[-_]/)[0];
    return SUPPORTED.indexOf(base) !== -1 ? base : null;
  }

  function queryLang(search) {
    if (typeof search !== 'string') return null;
    var m = /(?:^|[?&])lang=([^&#]*)/.exec(search);
    if (!m) return null;
    try {
      return normalizeLang(decodeURIComponent(m[1].replace(/\+/g, ' ')));
    } catch (e) {
      return null;
    }
  }

  /**
   * Pure language choice.
   * env: { search, deckLang, appLang, languages (array), language (string) }
   */
  function pickLang(env) {
    env = env || {};
    var fromQuery = queryLang(env.search);
    if (fromQuery) return fromQuery;
    var saved = normalizeLang(env.deckLang);
    if (saved) return saved;
    var app = normalizeLang(env.appLang);
    if (app) return app;
    var list = Array.isArray(env.languages) ? env.languages.slice() : [];
    if (typeof env.language === 'string') list.push(env.language);
    for (var i = 0; i < list.length; i++) {
      var nav = normalizeLang(list[i]);
      if (nav) return nav;
    }
    return DEFAULT_LANG;
  }

  /** Copy a trusted-format HTML value into `target`, keeping only the allowed tags and no attributes
   *  except class="grad-text" on span. Anything else becomes plain text. */
  function setSafeHtml(doc, target, html) {
    var tpl = doc.createElement('template');
    tpl.innerHTML = html;
    function copy(from, to) {
      for (var n = from.firstChild; n; n = n.nextSibling) {
        if (n.nodeType === 3) {
          to.appendChild(doc.createTextNode(n.nodeValue));
        } else if (n.nodeType === 1) {
          var tag = n.tagName.toUpperCase();
          if (ALLOWED_TAGS[tag]) {
            var el = doc.createElement(tag.toLowerCase());
            if (tag === 'SPAN' && n.getAttribute('class') === 'grad-text') el.setAttribute('class', 'grad-text');
            copy(n, el);
            to.appendChild(el);
          } else {
            to.appendChild(doc.createTextNode(n.textContent));
          }
        }
      }
    }
    while (target.firstChild) target.removeChild(target.firstChild);
    copy(tpl.content, target);
  }

  function parseAttrSpec(spec) {
    // "alt:s1.logoAlt;aria-label:ui.language" -> [[attr, key], ...]
    return String(spec || '').split(';').map(function (p) {
      var i = p.indexOf(':');
      return i > 0 ? [p.slice(0, i).trim(), p.slice(i + 1).trim()] : null;
    }).filter(Boolean);
  }

  function has(dict, key) {
    return Object.prototype.hasOwnProperty.call(dict, key) && typeof dict[key] === 'string' && dict[key] !== '';
  }

  /** Browser wiring. Returns an API object (also exposed as window.DeckI18n). */
  function init(win) {
    var doc = win.document;
    var select = doc.getElementById('deck-lang');
    var textEls = Array.prototype.slice.call(doc.querySelectorAll('[data-i18n]'));
    var attrEls = Array.prototype.slice.call(doc.querySelectorAll('[data-i18n-attr]'));

    // Snapshot of the built-in English, used for "en" and as the fallback after any error.
    var english = {
      text: textEls.map(function (el) {
        var isHtml = el.hasAttribute('data-i18n-html');
        return { el: el, html: isHtml, value: isHtml ? el.innerHTML : el.textContent };
      }),
      attrs: attrEls.map(function (el) {
        return {
          el: el,
          pairs: parseAttrSpec(el.getAttribute('data-i18n-attr')).map(function (p) {
            return [p[0], p[1], el.getAttribute(p[0])];
          })
        };
      }),
      title: doc.title
    };

    var current = DEFAULT_LANG;
    var requestId = 0;

    function relayout() {
      var R = win.Reveal;
      if (!R || typeof R.layout !== 'function') return;
      var run = function () { try { R.layout(); } catch (e) { /* ignore */ } };
      if (typeof R.isReady === 'function' && !R.isReady() && typeof R.on === 'function') R.on('ready', run);
      else run();
    }

    // The speaker view gets the notes of the current slide through reveal's own events; replay one so it
    // receives the notes in the new language.
    function refreshNotes() {
      var R = win.Reveal;
      if (!R || typeof R.dispatchEvent !== 'function') return;
      if (typeof R.isReady === 'function' && !R.isReady()) return;
      try { R.dispatchEvent({ type: 'resumed' }); } catch (e) { /* ignore */ }
    }

    function finish(code) {
      current = code;
      doc.documentElement.setAttribute('lang', code);
      if (select) select.value = code;
      relayout();
      refreshNotes();
    }

    function applyEnglish() {
      english.text.forEach(function (t) {
        if (t.html) setSafeHtml(doc, t.el, t.value);
        else t.el.textContent = t.value;
      });
      english.attrs.forEach(function (a) {
        a.pairs.forEach(function (p) { if (p[2] !== null) a.el.setAttribute(p[0], p[2]); });
      });
      doc.title = english.title;
      finish(DEFAULT_LANG);
    }

    function applyDict(dict, code) {
      textEls.forEach(function (el) {
        var key = el.getAttribute('data-i18n');
        if (!has(dict, key)) return;
        if (el.hasAttribute('data-i18n-html')) setSafeHtml(doc, el, dict[key]);
        else el.textContent = dict[key];
      });
      attrEls.forEach(function (el) {
        parseAttrSpec(el.getAttribute('data-i18n-attr')).forEach(function (p) {
          if (has(dict, p[1])) el.setAttribute(p[0], dict[p[1]]);
        });
      });
      if (has(dict, 'meta.title')) doc.title = dict['meta.title'];
      finish(code);
    }

    /** Load and apply a language. Resolves to the language actually shown. */
    function setLanguage(code) {
      var lang = normalizeLang(code) || DEFAULT_LANG;
      var id = ++requestId;
      if (lang === DEFAULT_LANG) {
        applyEnglish();
        return Promise.resolve(DEFAULT_LANG);
      }
      return win.fetch('./i18n/' + lang + '.json', { credentials: 'same-origin' })
        .then(function (r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then(function (dict) {
          if (id !== requestId) return current; // a newer request won
          if (!dict || typeof dict !== 'object') throw new Error('bad dictionary');
          applyDict(dict, lang);
          return lang;
        })
        .catch(function () {
          if (id !== requestId) return current;
          applyEnglish();
          return DEFAULT_LANG;
        });
    }

    function readStorage(key) {
      try { return win.localStorage.getItem(key); } catch (e) { return null; }
    }

    function writeDeckLang(code) {
      try { win.localStorage.setItem(DECK_KEY, code); } catch (e) { /* storage blocked */ }
    }

    function updateUrl(code) {
      try {
        var u = new win.URL(win.location.href);
        u.searchParams.set('lang', code);
        win.history.replaceState(win.history.state, '', u.pathname + u.search + u.hash);
      } catch (e) { /* ignore */ }
    }

    function chosen() {
      var nav = win.navigator || {};
      return pickLang({
        search: win.location.search,
        deckLang: readStorage(DECK_KEY),
        appLang: readStorage(APP_KEY),
        languages: nav.languages ? Array.prototype.slice.call(nav.languages) : [],
        language: nav.language
      });
    }

    if (select) {
      select.addEventListener('change', function () {
        var code = normalizeLang(select.value) || DEFAULT_LANG;
        setLanguage(code).then(function (shown) {
          if (shown === code) {
            writeDeckLang(code);
            updateUrl(code);
          }
        });
        // Give the arrow keys back to the slides.
        select.blur();
      });
      select.parentNode.removeAttribute('hidden');
    }

    // Inside the speaker view the current/next slide previews are iframes of this page: follow the language
    // chosen in the main window (the picker writes deckLang, which fires "storage" in the other windows).
    if (win.parent && win.parent !== win) {
      win.addEventListener('storage', function (e) {
        if (e.key !== DECK_KEY) return;
        var code = normalizeLang(e.newValue);
        if (code && code !== current) setLanguage(code);
      });
    }

    var ready = setLanguage(chosen());
    return { setLanguage: setLanguage, ready: ready, current: function () { return current; } };
  }

  var api = {
    SUPPORTED: SUPPORTED,
    DEFAULT_LANG: DEFAULT_LANG,
    normalizeLang: normalizeLang,
    pickLang: pickLang,
    setSafeHtml: setSafeHtml
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root && root.document && root.fetch && root.document.querySelector('[data-i18n]')) root.DeckI18n = Object.assign(api, init(root));
})(typeof window !== 'undefined' ? window : undefined);
