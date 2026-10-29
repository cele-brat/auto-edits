// Theme: Light, Dark or Auto (follows the computer's setting). Kept in this browser. Loaded in
// <head> before the styles are used, so the page never flashes the wrong theme (the page's
// security policy allows no inline scripts). Dark is the default: how the app has always looked.
'use strict';
(function () {
  const root = document.documentElement;
  const KEY = 'ae-theme';
  const read = () => { try { return localStorage.getItem(KEY) || 'dark'; } catch (e) { return 'dark'; } };
  root.dataset.theme = read();

  function draw() {
    for (const b of document.querySelectorAll('[data-theme-pick]')) b.setAttribute('aria-pressed', String(b.dataset.themePick === root.dataset.theme));
  }
  window.setTheme = function (t) {
    root.dataset.theme = t;
    try { localStorage.setItem(KEY, t); } catch (e) { /* fine: this visit only */ }
    draw();
  };
  // another tab changed it
  addEventListener('storage', (e) => { if (e.key === KEY) { root.dataset.theme = read(); draw(); } });
  document.addEventListener('DOMContentLoaded', () => {
    for (const b of document.querySelectorAll('[data-theme-pick]')) b.onclick = () => window.setTheme(b.dataset.themePick);
    draw();
  });
})();
