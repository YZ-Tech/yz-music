// ==UserScript==
// @name         YT Play Locally
// @namespace    yt-play
// @version      2.4
// @description  YouTube → local yt-play (download-if-missing + mpv). Branded collapsible widget on watch pages + per-thumbnail hover overlay everywhere.
// @match        https://www.youtube.com/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

(function () {
    'use strict';

    const WRAP_ID      = 'yt-play-local-wrap';
    const DELAY_KEY    = 'yt-play.audio-delay-ms';
    const COLLAPSE_KEY = 'yt-play.collapsed';
    const DELAY_MIN  = -3000;
    const DELAY_MAX  =  3000;
    const SLIDER_STEP = 50;
    const DECORATED  = 'ytPlayDecorated';

    // YouTube enforces Trusted Types — innerHTML is blocked. Build the plus icon via DOM API.
    const SVG_NS = 'http://www.w3.org/2000/svg';
    function makePlusIcon() {
        const svg = document.createElementNS(SVG_NS, 'svg');
        svg.setAttribute('width',  '14');
        svg.setAttribute('height', '14');
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('fill', 'none');
        svg.setAttribute('stroke', 'currentColor');
        svg.setAttribute('stroke-width', '3');
        svg.setAttribute('stroke-linecap', 'round');
        const path = document.createElementNS(SVG_NS, 'path');
        path.setAttribute('d', 'M12 5v14M5 12h14');
        svg.appendChild(path);
        return svg;
    }

    // Project logo (Jarvis assistant), inlined as SVG markup.
    // Source: Y:\projects\assistant\frontend\public\logo.svg
    const LOGO_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 59400 42000">
        <g fill="#fff" stroke="#000" stroke-width="7.62" stroke-linejoin="bevel" stroke-miterlimit="22.9256">
            <polygon points="11616.1,8249.12 11616.1,22417.04 24322.12,22417.04 24322.12,23326 15789.82,26458.12 15789.82,24700.98 11610.12,26235.29 11610.12,32404.98 28502.47,26387.7 28502.47,14246.16 22571.24,12139.74 22571.24,16618.6 24322.12,17196.7 24322.12,18147.8 15830.96,18147.8 15830.96,14182.39 17568.29,14856.66 17568.29,10363"/>
            <polygon points="47093.19,8225.33 30139.81,14246.16 30139.81,16475.31 36517.25,16475.31 42851.53,14225.75 42851.53,18170.76 30139.81,18170.76 30139.81,26387.7 47093.19,32426.72 47093.19,24108.92 42820.02,24108.92 42820.02,24487.97 42820.02,26458.31 34335.23,23420.94 34335.23,22451.01 47093.19,22451.01"/>
            <path d="M22802.89 10449.6l-4523.5 -1606.47c6163.23,-5924.29 15916.39,-5917.68 22087.23,-1.33l-4523.9 1606.62c-3958.6,-2605.67 -9083.15,-2607.37 -13039.83,1.18z"/>
            <path d="M22751.73 30209.19l-4508.17 1605.87c6186.94,5970.28 15978.27,5978.16 22158.89,1.34l-4508.57 -1606.03c-3977.4,2650.87 -9163.74,2648.16 -13142.15,-1.18z"/>
        </g>
    </svg>`;

    const log = (...a) => console.log('[yt-play]', ...a);

    // --- shared helpers --------------------------------------------------

    function fireProtocol(url) {
        log('navigating to', url);
        location.href = url;
    }
    function firePlay(proto, srcUrl) {
        // No srcUrl = main widget on current watch page → pick up current time.
        // srcUrl provided = thumbnail overlay → force start at 0 (overrides
        // mpv's save-position-on-quit resume, which otherwise picks up where
        // we left the file last time).
        if (!srcUrl) {
            const v = document.querySelector('video');
            let t = 0;
            if (v) {
                t = Math.floor(v.currentTime || 0);
                v.pause();
            }
            const u = new URL(location.href);
            // Always send t — even 0 — so Python triggers its "force from
            // beginning" path (resume-playback override + clear watch-later).
            // Otherwise mpv would resume from a previously-saved position.
            u.searchParams.set('t', String(t));
            srcUrl = u.toString();
        } else {
            const u = new URL(srcUrl);
            u.searchParams.set('t', '0');
            srcUrl = u.toString();
        }
        fireProtocol(proto + '://' + encodeURIComponent(srcUrl));
    }
    function fireDelay(ms) { fireProtocol('mpv-yt-d://' + ms); }

    function clampDelay(ms) { return Math.max(DELAY_MIN, Math.min(DELAY_MAX, Math.round(ms))); }
    function loadDelay() {
        const v = parseInt(localStorage.getItem(DELAY_KEY), 10);
        return Number.isFinite(v) ? clampDelay(v) : 0;
    }
    function saveDelay(ms) { localStorage.setItem(DELAY_KEY, String(ms)); }

    // --- one-time stylesheet ---------------------------------------------

    const style = document.createElement('style');
    style.textContent = `
        .yt-play-tile-overlay {
            position: absolute; top: 8px; right: 8px;
            display: flex; gap: 4px;
            opacity: 0; transition: opacity .12s;
            pointer-events: none; z-index: 30;
        }
        a:hover > .yt-play-tile-overlay,
        *:hover > a > .yt-play-tile-overlay { opacity: 1; pointer-events: auto; }
        .yt-play-tile-icon {
            width: 32px; height: 32px; border-radius: 16px;
            border: none; background: rgba(0,0,0,.72); color: #fff;
            font-size: 14px; cursor: pointer; padding: 0; line-height: 1;
            display: inline-flex; align-items: center; justify-content: center;
        }
        .yt-play-tile-icon:hover { background: #ff0033; }

        /* Main widget — collapse mechanics */
        #${WRAP_ID} .row-collapsible {
            max-height: 60px; margin-top: 8px;
            overflow: hidden; opacity: 1;
            transition: max-height .25s ease, margin-top .25s ease, opacity .2s ease;
        }
        #${WRAP_ID}.yt-play-collapsed .row-collapsible {
            max-height: 0; margin-top: 0; opacity: 0;
        }
        #${WRAP_ID} .yt-play-action {
            display: inline-flex; align-items: center; gap: 6px;
            padding: 0 14px; height: 36px; border-radius: 18px;
            color: #fff; border: none; cursor: pointer;
            font: 600 13px Roboto, Arial, sans-serif;
            transition: padding .2s ease, gap .2s ease;
        }
        #${WRAP_ID} .yt-play-label {
            display: inline-block;
            max-width: 80px; opacity: 1;
            white-space: nowrap; overflow: hidden;
            transition: max-width .2s ease, opacity .2s ease;
        }
        #${WRAP_ID}.yt-play-collapsed .yt-play-action { padding: 0 10px; gap: 0; }
        #${WRAP_ID}.yt-play-collapsed .yt-play-label { max-width: 0; opacity: 0; }
        #${WRAP_ID} .yt-play-icon {
            display: inline-flex; align-items: center; justify-content: center;
            line-height: 1;
        }
        #${WRAP_ID} .yt-play-logo {
            cursor: pointer; user-select: none;
            transition: filter .2s ease;
        }
        #${WRAP_ID} .yt-play-logo:hover { filter: brightness(1.15); }

        /* Container padding + transition (smaller padding when collapsed) */
        #${WRAP_ID} {
            padding: 10px 12px;
            transition: padding .2s ease, min-width .2s ease;
        }
        #${WRAP_ID}.yt-play-collapsed { padding: 6px 10px; }

        /* Compact delay readout — only shown when collapsed (row 2 has the full one). */
        #${WRAP_ID} .yt-play-delay-badge {
            font: 500 11px monospace;
            color: #aaa;
            white-space: nowrap;
            opacity: 0;
            max-width: 0;
            overflow: hidden;
            transition: opacity .2s ease, max-width .2s ease;
        }
        #${WRAP_ID}.yt-play-collapsed .yt-play-delay-badge {
            opacity: 1;
            max-width: 80px;
        }
    `;
    (document.head || document.documentElement).appendChild(style);

    // --- main widget (watch pages only) ----------------------------------

    const BTN_BASE = [
        'padding:0 14px','height:36px','border-radius:18px',
        'color:#fff','border:none','cursor:pointer',
        'font:600 13px Roboto,Arial,sans-serif',
        'display:inline-flex','align-items:center','justify-content:center',
    ].join(';');
    const SMALL_BTN_BASE = [
        'min-width:42px','height:30px','border-radius:15px',
        'color:#eee','background:#333','border:1px solid #555',
        'cursor:pointer','font:500 12px Roboto,Arial,sans-serif',
    ].join(';');

    function makeBtn(label, title, bg, onClick, base = BTN_BASE) {
        const b = document.createElement('button');
        b.textContent = label;
        b.title = title;
        b.style.cssText = base + ';background:' + bg;
        b.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); onClick(); });
        return b;
    }

    function makeActionBtn(iconContent, labelText, title, bg, onClick) {
        const b = document.createElement('button');
        b.className = 'yt-play-action';
        b.title = title;
        b.style.background = bg;
        const icon = document.createElement('span');
        icon.className = 'yt-play-icon';
        if (iconContent instanceof Element) icon.appendChild(iconContent);
        else                                icon.textContent = iconContent;
        const label = document.createElement('span');
        label.className = 'yt-play-label';
        label.textContent = labelText;
        b.appendChild(icon);
        b.appendChild(label);
        b.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); onClick(); });
        return b;
    }

    function buildWidget() {
        const wrap = document.createElement('div');
        wrap.id = WRAP_ID;
        // Note: padding lives in the stylesheet so it can transition on collapse.
        wrap.style.cssText = [
            'position:fixed','right:24px','bottom:24px','z-index:2147483647',
            'background:rgba(20,20,20,.85)','border-radius:12px',
            'box-shadow:0 4px 16px rgba(0,0,0,.5)',
            'display:flex','flex-direction:column',
            'color:#ddd',
        ].join(';');
        if (localStorage.getItem(COLLAPSE_KEY) === '1') wrap.classList.add('yt-play-collapsed');

        const row1 = document.createElement('div');
        row1.style.cssText = 'display:flex;gap:6px;align-items:center;justify-content:space-between';
        // Logo: paint via CSS background-image — no DOM parse, no attribute quirks, just a div.
        const logoBox = document.createElement('div');
        logoBox.className = 'yt-play-logo';
        logoBox.title = 'Click to collapse / expand';
        logoBox.style.cssText = [
            'flex:0 0 auto',
            'width:48px',
            'height:34px',
            'background-image:url("data:image/svg+xml;utf8,' + encodeURIComponent(LOGO_SVG) + '")',
            'background-repeat:no-repeat',
            'background-position:center',
            'background-size:contain',
        ].join(';');
        logoBox.addEventListener('click', () => {
            const collapsed = wrap.classList.toggle('yt-play-collapsed');
            localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0');
        });
        const leftCluster = document.createElement('div');
        leftCluster.style.cssText = 'display:flex;align-items:center;gap:8px';
        const delayBadge = document.createElement('span');
        delayBadge.className = 'yt-play-delay-badge';
        leftCluster.appendChild(logoBox);
        leftCluster.appendChild(delayBadge);
        row1.appendChild(leftCluster);
        const btns = document.createElement('div');
        btns.style.cssText = 'display:flex;gap:6px';
        btns.appendChild(makeActionBtn('▶', 'Play',  'Replace mpv playlist with this video', '#ff0033', () => firePlay('mpv-yt')));
        btns.appendChild(makeActionBtn('⏭', 'Next',  'Insert after current track',           '#666',    () => firePlay('mpv-yt-n')));
        btns.appendChild(makeActionBtn(makePlusIcon(), 'Queue', 'Append to end of mpv playlist',  '#444',    () => firePlay('mpv-yt-q')));
        row1.appendChild(btns);
        wrap.appendChild(row1);

        const row2 = document.createElement('div');
        row2.className = 'row-collapsible';
        row2.style.cssText = 'display:flex;align-items:center;gap:8px';
        const slider = document.createElement('input');
        slider.type = 'range'; slider.min = String(DELAY_MIN); slider.max = String(DELAY_MAX); slider.step = String(SLIDER_STEP);
        slider.style.cssText = 'flex:1;accent-color:#ff0033';
        const val = document.createElement('span');
        val.style.cssText = 'font:600 13px monospace;min-width:70px;text-align:right';
        row2.appendChild(slider); row2.appendChild(val); wrap.appendChild(row2);

        const row3 = document.createElement('div');
        row3.className = 'row-collapsible';
        row3.style.cssText = 'display:flex;align-items:center;gap:6px;justify-content:space-between';
        const stepLeft  = document.createElement('div'); stepLeft.style.cssText  = 'display:flex;gap:6px';
        const stepRight = document.createElement('div'); stepRight.style.cssText = 'display:flex;gap:6px;align-items:center';
        stepLeft.appendChild(makeBtn('−100', 'Audio earlier by 100 ms',  '#333', () => nudge(-100), SMALL_BTN_BASE));
        stepLeft.appendChild(makeBtn('−10',  'Audio earlier by 10 ms',   '#333', () => nudge(-10),  SMALL_BTN_BASE));
        stepRight.appendChild(makeBtn('+10',  'Audio later by 10 ms',    '#333', () => nudge(10),   SMALL_BTN_BASE));
        stepRight.appendChild(makeBtn('+100', 'Audio later by 100 ms',   '#333', () => nudge(100),  SMALL_BTN_BASE));
        stepRight.appendChild(makeBtn('reset','Clear delay (0 ms) and forget stored value', '#222', () => doReset(), SMALL_BTN_BASE));
        row3.appendChild(stepLeft); row3.appendChild(stepRight); wrap.appendChild(row3);

        let currentMs = loadDelay();
        const fmt = (ms) => (ms >= 0 ? '+' : '') + ms + ' ms';
        function render() {
            slider.value = String(currentMs);
            val.textContent = fmt(currentMs);
            delayBadge.textContent = fmt(currentMs);
        }
        function apply(ms) { currentMs = clampDelay(ms); render(); saveDelay(currentMs); fireDelay(currentMs); }
        function nudge(delta) { apply(currentMs + delta); }
        function doReset() { currentMs = 0; render(); localStorage.removeItem(DELAY_KEY); fireDelay(0); }
        slider.addEventListener('input',  () => {
            const live = parseInt(slider.value, 10);
            val.textContent        = fmt(live);
            delayBadge.textContent = fmt(live);
        });
        slider.addEventListener('change', () => apply(parseInt(slider.value, 10)));
        render();
        return wrap;
    }

    function ensureWidget() {
        let wrap = document.getElementById(WRAP_ID);
        const onWatch = location.pathname.startsWith('/watch');
        if (!onWatch) { if (wrap) wrap.remove(); return; }
        if (wrap) return;
        document.body.appendChild(buildWidget());
        log('widget mounted on', location.href);
    }

    // --- per-thumbnail hover overlay -------------------------------------

    function makeTileIcon(content, title, proto) {
        const b = document.createElement('button');
        b.className = 'yt-play-tile-icon';
        b.title = title;
        if (content instanceof Element) b.appendChild(content);
        else                            b.textContent = content;
        const fire = (e) => {
            e.preventDefault();
            e.stopPropagation();
            e.stopImmediatePropagation();
            const a = b.closest('a');
            if (!a) return;
            const url = new URL(a.getAttribute('href'), location.origin).toString();
            firePlay(proto, url);
        };
        // Intercept both — YouTube uses delegated mousedown navigation in places.
        b.addEventListener('mousedown', (e) => { e.stopPropagation(); e.stopImmediatePropagation(); });
        b.addEventListener('click', fire);
        return b;
    }

    function decorateAnchor(a) {
        if (a.dataset[DECORATED]) return;
        const href = a.getAttribute('href') || '';
        if (!/^\/watch\?(?:.*&)?v=[A-Za-z0-9_-]{11}/.test(href)) return;
        // Only thumbnails (anchors containing an image/thumb), not text-only title links.
        if (!a.querySelector('yt-image, ytd-thumbnail, img')) return;

        a.dataset[DECORATED] = '1';
        if (getComputedStyle(a).position === 'static') a.style.position = 'relative';

        const overlay = document.createElement('div');
        overlay.className = 'yt-play-tile-overlay';
        overlay.appendChild(makeTileIcon('▶', 'Play locally',     'mpv-yt'));
        overlay.appendChild(makeTileIcon('⏭', 'Play next in mpv', 'mpv-yt-n'));
        overlay.appendChild(makeTileIcon(makePlusIcon(), 'Queue in mpv', 'mpv-yt-q'));
        a.appendChild(overlay);
    }

    let decorateTimer = null;
    function scheduleDecorate() {
        clearTimeout(decorateTimer);
        decorateTimer = setTimeout(() => {
            document.querySelectorAll('a[href*="/watch"]').forEach(decorateAnchor);
        }, 150);
    }

    // --- lifecycle -------------------------------------------------------

    function tick() {
        ensureWidget();
        scheduleDecorate();
    }

    document.addEventListener('yt-navigate-finish', tick);
    window.addEventListener('popstate', tick);

    new MutationObserver(scheduleDecorate).observe(document.body, { childList: true, subtree: true });

    tick();
    let tries = 0;
    const poller = setInterval(() => {
        tick();
        if (++tries > 20) clearInterval(poller);
    }, 250);
})();
