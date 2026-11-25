/*
 * Pitch deck chrome: full screen button, shortcut hint, translated shortcut overlay, auto-hide in full screen.
 * A small classic script, no dependency. The texts come from deck-i18n.js (data-i18n attributes in index.html).
 *
 * Keys handled here (in the capture phase, before reveal.js sees them):
 *   ?  or F1   open / close the shortcut overlay (replaces reveal's English-only help)
 *   Esc        closes the overlay while it is open
 *   F          full screen on / off (reveal.js alone can only turn it on)
 * S (speaker notes), O / Esc (overview), B (pause) and the arrows are reveal.js built-ins.
 */
(function (win) {
  'use strict';
  var doc = win.document;
  var root = doc.documentElement;
  var fsButton = doc.getElementById('deck-fs');
  var hint = doc.getElementById('deck-hint');
  var help = doc.getElementById('deck-help');
  var helpClose = doc.getElementById('deck-help-close');
  var select = doc.getElementById('deck-lang');
  if (!help) return;
  if (/[?&]receiver(&|=|$)/.test(win.location.search)) root.classList.add('deck-receiver');

  var IDLE_MS = 3500;
  var NAV_KEYS = { ArrowLeft: 1, ArrowRight: 1, ArrowUp: 1, ArrowDown: 1, ' ': 1, PageUp: 1, PageDown: 1, Home: 1, End: 1 };

  // ---- full screen
  function fsElement() { return doc.fullscreenElement || doc.webkitFullscreenElement || null; }
  function fsEnabled() { return !!(doc.fullscreenEnabled || doc.webkitFullscreenEnabled); }
  function toggleFullscreen() {
    var el = root;
    try {
      var p = fsElement()
        ? (doc.exitFullscreen || doc.webkitExitFullscreen).call(doc)
        : (el.requestFullscreen || el.webkitRequestFullscreen).call(el);
      if (p && typeof p.catch === 'function') p.catch(function () { /* refused by the browser */ });
    } catch (e) { /* not allowed here */ }
  }
  if (fsButton) {
    if (fsEnabled()) fsButton.removeAttribute('hidden');
    fsButton.addEventListener('click', function () {
      toggleFullscreen();
      fsButton.blur(); // give the arrow keys back to the slides
    });
  }
  function onFsChange() {
    var on = !!fsElement();
    if (fsButton) fsButton.setAttribute('aria-pressed', on ? 'true' : 'false');
    wake();
  }
  doc.addEventListener('fullscreenchange', onFsChange);
  doc.addEventListener('webkitfullscreenchange', onFsChange);

  // ---- shortcut overlay
  var lastFocus = null;
  function helpOpen() { return !help.hasAttribute('hidden'); }
  function openHelp() {
    if (helpOpen()) return;
    lastFocus = doc.activeElement;
    help.removeAttribute('hidden');
    if (helpClose) helpClose.focus();
    wake();
  }
  function closeHelp() {
    if (!helpOpen()) return;
    help.setAttribute('hidden', '');
    var back = lastFocus && lastFocus !== doc.body && lastFocus.tagName !== 'SELECT' && lastFocus !== hint ? lastFocus : null;
    if (back && typeof back.focus === 'function') back.focus();
    else if (doc.activeElement && doc.activeElement.blur) doc.activeElement.blur();
    lastFocus = null;
    wake();
  }
  if (hint) hint.addEventListener('click', function () { openHelp(); });
  if (helpClose) helpClose.addEventListener('click', closeHelp);
  help.addEventListener('click', function (e) { if (e.target === help) closeHelp(); });

  // ---- keys (capture phase: runs before reveal.js)
  win.addEventListener('keydown', function (e) {
    if (e.ctrlKey || e.altKey || e.metaKey) return;
    var isHelpKey = e.key === '?' || e.key === 'F1';
    if (isHelpKey) {
      e.preventDefault();
      e.stopImmediatePropagation();
      if (helpOpen()) closeHelp(); else openHelp();
      return;
    }
    if (helpOpen()) {
      // The overlay owns the keyboard while it is open.
      if (e.key === 'Escape') closeHelp();
      else if (e.key === 'Tab') { e.preventDefault(); if (helpClose) helpClose.focus(); }
      else if (e.key === 'Enter' || e.key === ' ') { return; } // activates the focused Close button
      e.stopImmediatePropagation();
      return;
    }
    if (e.target === select) {
      // The focused language picker keeps ArrowUp/ArrowDown and the other letters (type-ahead). Left/Right and the
      // deck shortcuts F, S, B, O still work: reveal.js ignores keys aimed at a <select>, so run them here.
      var R = win.Reveal;
      var k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      var done = true;
      if (k === 'Escape') { /* just leave the picker */ }
      else if (k === 'ArrowLeft' && R) R.left();
      else if (k === 'ArrowRight' && R) R.right();
      else if (k === 'f') toggleFullscreen();
      else if (k === 's') { var notes = R && R.getPlugin && R.getPlugin('notes'); if (notes && notes.open) notes.open(); }
      else if (k === 'b' && R) R.togglePause();
      else if (k === 'o' && R) R.toggleOverview();
      else done = false;
      if (done) {
        e.preventDefault();
        e.stopImmediatePropagation();
        select.blur();
        return;
      }
    }
    if ((e.key === 'f' || e.key === 'F') && fsEnabled()) {
      // reveal.js only enters full screen on F; make the key a toggle like the button.
      e.preventDefault();
      e.stopImmediatePropagation();
      toggleFullscreen();
      return;
    }
    if (!NAV_KEYS[e.key]) wake();
  }, true);

  // ---- auto-hide of the chrome in full screen
  var timer = null;
  var lastX = -1, lastY = -1;
  function busy() { return helpOpen() || (doc.activeElement && doc.activeElement === select); }
  function hide() {
    if (fsElement() && !busy()) root.classList.add('deck-idle');
  }
  function wake() {
    root.classList.remove('deck-idle');
    if (timer) { win.clearTimeout(timer); timer = null; }
    if (fsElement() && !busy()) timer = win.setTimeout(hide, IDLE_MS);
  }
  doc.addEventListener('mousemove', function (e) {
    if (e.clientX === lastX && e.clientY === lastY) return; // layout shifts can replay a mousemove
    lastX = e.clientX; lastY = e.clientY;
    wake();
  });
  doc.addEventListener('mousedown', wake);
  doc.addEventListener('touchstart', wake, { passive: true });
  doc.addEventListener('focusin', wake);
  wake();

  win.DeckUi = { openHelp: openHelp, closeHelp: closeHelp, toggleFullscreen: toggleFullscreen, wake: wake };
})(window);
