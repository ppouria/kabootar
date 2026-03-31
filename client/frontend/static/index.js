class MirrorApp {
    constructor() {
        this.AUTO_REFRESH_MS = 30000;
        this.STORAGE_KEY = 'kabootar_read_v1';
        this.LANG_KEY = 'kabootar_lang';
        this.INSTALL_PROMPT_KEY = 'kabootar_install_prompt_dismissed_v1';
        this.CHANNEL_NAV_LOADING_KEY = 'kabootar_channel_nav_loading_v1';
        this.CHANNEL_NAV_LOADING_MAX_AGE_MS = 20000;
        this.autoRefreshTimer = null;
        this.refreshSidebarSearch = null;
        this.lang = 'fa';
        this.i18n = {};
        const chat = document.getElementById('chat');
        this.selected = chat?.dataset.selected || '';
        this.sourceMode = (chat?.dataset.sourceMode || 'dns').toLowerCase() === 'direct' ? 'direct' : 'dns';
        this.dnsDomainsCount = Math.max(0, Number(chat?.dataset.dnsDomainsCount || '0') || 0);
    }
    t(key, fallback = '') {
        return this.i18n[key] || fallback || key;
    }
    withVars(template, vars = {}) {
        return template.replace(/\{(\w+)\}/g, (_, k) => vars[k] || '');
    }
    escapeRegExp(value) {
        return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }
    escapeHtml(value) {
        return value
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }
    channelAvatarText(value) {
        const clean = (value || '')
            .replace(/^https?:\/\/t\.me\/s\//i, '')
            .replace(/^https?:\/\/t\.me\//i, '')
            .replace(/^@+/, '')
            .trim();
        const glyphs = Array.from(clean.replace(/\s+/g, ''));
        return (glyphs.slice(0, 2).join('') || '?').toUpperCase();
    }
    sanitizeTelegramUsername(value) {
        let token = String(value || '').trim().replace(/\\/g, '/');
        token = token.replace(/^@+/, '').replace(/^\/+|\/+$/g, '');
        token = token.split(/[?#]/, 1)[0] || '';
        if (!token)
            return '';
        if (!/^[A-Za-z0-9_]{3,64}$/.test(token))
            return '';
        return token.toLowerCase();
    }
    extractTelegramUsername(value) {
        let raw = String(value || '').trim();
        if (!raw)
            return '';
        raw = raw.replace(/\\/g, '/');
        if (!raw.includes('://')) {
            const brokenScheme = raw.match(/^(https?|tg)(\/+)(.+)$/i);
            if (brokenScheme) {
                raw = `${brokenScheme[1]}://${brokenScheme[3]}`;
            }
            else if (raw.startsWith('//')) {
                raw = `https:${raw}`;
            }
        }
        const direct = this.sanitizeTelegramUsername(raw);
        if (direct)
            return direct;
        if (/^tg:/i.test(raw)) {
            try {
                const url = new URL(raw);
                const queryUsername = this.sanitizeTelegramUsername(url.searchParams.get('domain') || '');
                if (queryUsername)
                    return queryUsername;
            }
            catch {
                return '';
            }
        }
        const looksLikeTelegramUrl = /^(?:https?:\/\/|t\.me\/|telegram\.me\/|telegram\.dog\/|www\.t\.me\/|www\.telegram\.me\/)/i.test(raw);
        if (looksLikeTelegramUrl || raw.includes('/')) {
            try {
                const candidate = /^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : `https://${raw.replace(/^\/+/, '')}`;
                const url = new URL(candidate);
                const host = url.hostname.replace(/^www\./i, '').toLowerCase();
                if (!['t.me', 'telegram.me', 'telegram.dog'].includes(host))
                    return '';
                const segments = url.pathname.split('/').filter(Boolean);
                if (!segments.length)
                    return '';
                if (segments[0]?.toLowerCase() === 's' && segments[1]) {
                    return this.sanitizeTelegramUsername(segments[1]);
                }
                if (['c', 'joinchat', 'addlist', 'share'].includes((segments[0] || '').toLowerCase())) {
                    return '';
                }
                return this.sanitizeTelegramUsername(segments[0]);
            }
            catch {
                const parts = raw.split('/').filter(Boolean);
                if (!parts.length)
                    return '';
                if ((parts[0] || '').toLowerCase() === 's' && parts[1]) {
                    return this.sanitizeTelegramUsername(parts[1]);
                }
                return this.sanitizeTelegramUsername(parts[parts.length - 1]);
            }
        }
        return '';
    }
    normalizeChannelInputValue(value) {
        const username = this.extractTelegramUsername(value);
        return username ? `https://t.me/s/${username}` : '';
    }
    tokenizeChannelDraft(raw) {
        return String(raw || '')
            .split(/[,;\n\r،]+/)
            .map((part) => part.trim())
            .filter(Boolean);
    }
    parseChannelDraft(raw) {
        return this.tokenizeChannelDraft(raw).map((token) => {
            const normalized = this.normalizeChannelInputValue(token);
            return {
                raw: token,
                normalized,
                valid: !!normalized,
            };
        });
    }
    uniqueNormalizedChannels(entries) {
        const seen = new Set();
        const out = [];
        entries.forEach((entry) => {
            if (!entry.normalized || seen.has(entry.normalized))
                return;
            seen.add(entry.normalized);
            out.push(entry.normalized);
        });
        return out;
    }
    normalizeDomainInputValue(value) {
        let raw = String(value || '').trim();
        if (!raw)
            return '';
        raw = raw.replace(/\\/g, '/');
        if (!raw.includes('://')) {
            const brokenScheme = raw.match(/^(https?|wss?)(\/+)(.+)$/i);
            if (brokenScheme) {
                raw = `${brokenScheme[1]}://${brokenScheme[3]}`;
            }
        }
        try {
            const candidate = /^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : `https://${raw.replace(/^\/+/, '')}`;
            const url = new URL(candidate);
            const host = (url.hostname || '').trim().replace(/\.+$/g, '').toLowerCase();
            return /^[a-z0-9.-]+$/i.test(host) ? host : '';
        }
        catch {
            const host = raw
                .replace(/^[a-z][a-z0-9+.-]*:\/\//i, '')
                .replace(/^\/+/, '')
                .split(/[/?#]/, 1)[0]
                .trim()
                .replace(/\.+$/g, '')
                .toLowerCase();
            return /^[a-z0-9.-]+$/i.test(host) ? host : '';
        }
    }
    requiresDomainBeforeChannel() {
        return this.sourceMode === 'dns' && this.dnsDomainsCount < 1;
    }
    async loadLang(lang) {
        const resp = await fetch(`/static/i18n/${lang}.json`, { cache: 'no-cache' });
        if (!resp.ok)
            throw new Error(`lang_${lang}_not_found`);
        return (await resp.json());
    }
    applyI18n(root = document) {
        root.querySelectorAll('[data-i18n]').forEach((el) => {
            const key = el.dataset.i18n || '';
            if (!key)
                return;
            el.textContent = this.t(key, el.textContent || '');
        });
        root.querySelectorAll('[data-i18n-title]').forEach((el) => {
            const key = el.dataset.i18nTitle || '';
            if (!key)
                return;
            el.title = this.t(key, el.title || '');
            if (el.hasAttribute('aria-label')) {
                el.setAttribute('aria-label', this.t(key, el.getAttribute('aria-label') || ''));
            }
        });
        root.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
            const key = el.dataset.i18nPlaceholder || '';
            if (!key)
                return;
            const domain = (el.dataset.domain || '').trim();
            const text = this.withVars(this.t(key, el.placeholder || ''), { domain });
            el.placeholder = text;
        });
    }
    updateLangToggleLabel() {
        const btn = document.getElementById('langToggle');
        if (!btn)
            return;
        btn.textContent = this.lang === 'fa' ? 'EN' : 'FA';
    }
    applyUnsupportedMediaLabels(root = document) {
        root.querySelectorAll('.unsupported-media-copy[data-media-kind]').forEach((el) => {
            const kind = (el.dataset.mediaKind || 'media').trim().toLowerCase() || 'media';
            const key = `index.unsupported_media_${kind}`;
            el.textContent = this.t(key, this.t('index.unsupported_media_media', 'This message contains unsupported media and Kabootar does not support it yet.'));
        });
    }
    async initI18n() {
        const saved = (localStorage.getItem(this.LANG_KEY) || '').toLowerCase();
        this.lang = saved === 'en' ? 'en' : 'fa';
        try {
            this.i18n = await this.loadLang(this.lang);
        }
        catch {
            this.lang = 'en';
            this.i18n = await this.loadLang('en');
        }
        document.documentElement.lang = this.lang;
        document.documentElement.dir = 'ltr';
        this.applyI18n();
        this.applyUnsupportedMediaLabels();
        this.updateLangToggleLabel();
        const btn = document.getElementById('langToggle');
        btn?.addEventListener('click', () => {
            const next = this.lang === 'fa' ? 'en' : 'fa';
            localStorage.setItem(this.LANG_KEY, next);
            window.location.reload();
        });
    }
    loadReadMap() {
        try {
            return JSON.parse(localStorage.getItem(this.STORAGE_KEY) || '{}');
        }
        catch {
            return {};
        }
    }
    saveReadMap(map) {
        localStorage.setItem(this.STORAGE_KEY, JSON.stringify(map));
    }
    formatTime(iso) {
        if (!iso)
            return '';
        const d = new Date(iso);
        if (Number.isNaN(d.getTime()))
            return iso;
        return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }).toLowerCase();
    }
    formatDayLabel(date) {
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const target = new Date(date.getFullYear(), date.getMonth(), date.getDate());
        const diffDays = Math.round((today.getTime() - target.getTime()) / 86400000);
        if (diffDays === 0)
            return this.t('index.today', 'Today');
        if (diffDays === 1)
            return this.t('index.yesterday', 'Yesterday');
        const locale = this.lang === 'fa' ? 'fa-IR' : 'en-US';
        const sameYear = now.getFullYear() === date.getFullYear();
        return date.toLocaleDateString(locale, sameYear ? { month: 'long', day: 'numeric' } : { year: 'numeric', month: 'long', day: 'numeric' });
    }
    applyTimes() {
        document.querySelectorAll('.time[data-iso]').forEach((el) => {
            el.textContent = this.formatTime(el.dataset.iso || '');
        });
    }
    addDateDividers() {
        const wrap = document.getElementById('messages');
        if (!wrap)
            return;
        wrap.querySelectorAll('.date-divider').forEach((el) => el.remove());
        let lastKey = '';
        [...wrap.querySelectorAll('.msg[data-message-id]')].forEach((msg) => {
            const iso = msg.querySelector('.time[data-iso]')?.dataset.iso || '';
            if (!iso)
                return;
            const date = new Date(iso);
            if (Number.isNaN(date.getTime()))
                return;
            const key = `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
            if (key === lastKey)
                return;
            lastKey = key;
            const divider = document.createElement('div');
            divider.className = 'date-divider';
            divider.innerHTML = `<span class="date-divider-chip">${this.escapeHtml(this.formatDayLabel(date))}</span>`;
            msg.before(divider);
        });
    }
    applyUnreadBadges(readMap) {
        document.querySelectorAll('.channel').forEach((ch) => {
            const key = ch.dataset.channelKey || '';
            const latest = Number(ch.dataset.latestId || 0);
            const read = Number(readMap[key] || 0);
            const unread = Math.max(0, latest - read);
            const badge = ch.querySelector('.unread-badge');
            if (!badge)
                return;
            if (unread > 0) {
                badge.hidden = false;
                badge.textContent = unread > 99 ? '99+' : String(unread);
            }
            else {
                badge.hidden = true;
            }
        });
    }
    addUnreadDivider(readMap) {
        if (!this.selected)
            return null;
        const read = Number(readMap[this.selected] || 0);
        if (!read)
            return null;
        const messages = [...document.querySelectorAll('.msg[data-message-id]')];
        const firstUnread = messages.find((m) => Number(m.dataset.messageId || 0) > read);
        if (!firstUnread)
            return null;
        const divider = document.createElement('div');
        divider.className = 'unread-divider';
        divider.textContent = this.t('index.unread_divider', 'Unread messages');
        firstUnread.before(divider);
        return divider;
    }
    markCurrentAsRead(readMap) {
        if (!this.selected)
            return;
        const ids = [...document.querySelectorAll('.msg[data-message-id]')].map((m) => Number(m.dataset.messageId || 0));
        if (!ids.length)
            return;
        readMap[this.selected] = Math.max(...ids);
        this.saveReadMap(readMap);
        this.applyUnreadBadges(readMap);
    }
    scrollToUnreadOrBottom(divider) {
        const wrap = document.getElementById('messages');
        if (!wrap)
            return;
        if ('scrollRestoration' in history)
            history.scrollRestoration = 'manual';
        let autoPin = true;
        const disableAutoPin = () => {
            autoPin = false;
        };
        wrap.addEventListener('touchstart', disableAutoPin, { once: true, passive: true });
        wrap.addEventListener('wheel', disableAutoPin, { once: true, passive: true });
        wrap.addEventListener('mousedown', disableAutoPin, { once: true, passive: true });
        const jumpBottom = () => {
            if (!autoPin)
                return;
            wrap.scrollTop = wrap.scrollHeight;
        };
        if (divider) {
            const firstMsg = wrap.querySelector('.msg[data-message-id]');
            if (!(firstMsg && divider.nextElementSibling === firstMsg)) {
                divider.scrollIntoView({ block: 'center', behavior: 'auto' });
                autoPin = false;
                return;
            }
        }
        jumpBottom();
        requestAnimationFrame(jumpBottom);
        setTimeout(jumpBottom, 120);
        setTimeout(jumpBottom, 300);
        setTimeout(() => {
            autoPin = false;
        }, 900);
    }
    setupMobileMenu() {
        const btn = document.getElementById('menuBtn');
        const sidebar = document.getElementById('sidebar');
        const overlay = document.getElementById('sidebarOverlay');
        if (!btn || !sidebar || !overlay)
            return;
        const mobileQuery = window.matchMedia('(max-width: 900px)');
        const shouldAutoOpen = () => {
            const hasChannels = document.querySelectorAll('#sidebar .channel').length > 0;
            return !this.selected && hasChannels && mobileQuery.matches;
        };
        const syncButtonVisibility = () => {
            btn.hidden = !mobileQuery.matches || sidebar.classList.contains('open');
        };
        const setOpen = (open) => {
            sidebar.classList.toggle('open', open);
            overlay.hidden = !open;
            syncButtonVisibility();
        };
        setOpen(shouldAutoOpen());
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            setOpen(!sidebar.classList.contains('open'));
        });
        overlay.addEventListener('click', () => setOpen(false));
        document.querySelectorAll('.channel').forEach((ch) => {
            ch.addEventListener('click', () => setOpen(false), { capture: true });
        });
        window.addEventListener('pageshow', () => setOpen(shouldAutoOpen()));
        mobileQuery.addEventListener('change', () => setOpen(shouldAutoOpen()));
    }
    loadChannelNavigationState() {
        try {
            const raw = sessionStorage.getItem(this.CHANNEL_NAV_LOADING_KEY);
            if (!raw)
                return null;
            const parsed = JSON.parse(raw);
            const at = Number(parsed?.at || 0);
            if (!at || Date.now() - at > this.CHANNEL_NAV_LOADING_MAX_AGE_MS) {
                sessionStorage.removeItem(this.CHANNEL_NAV_LOADING_KEY);
                return null;
            }
            return {
                at,
                selected: typeof parsed?.selected === 'string' ? parsed.selected : '',
                title: typeof parsed?.title === 'string' ? parsed.title : '',
                href: typeof parsed?.href === 'string' ? parsed.href : '',
            };
        }
        catch {
            sessionStorage.removeItem(this.CHANNEL_NAV_LOADING_KEY);
            return null;
        }
    }
    persistChannelNavigationState(state) {
        try {
            sessionStorage.setItem(this.CHANNEL_NAV_LOADING_KEY, JSON.stringify(state));
        }
        catch {
            // Ignore storage errors and still allow navigation.
        }
    }
    showChannelLoading() {
        const feed = document.getElementById('chatFeed');
        document.documentElement.classList.add('channel-loading-pending');
        feed?.classList.add('is-loading');
    }
    hideChannelLoading() {
        const feed = document.getElementById('chatFeed');
        document.documentElement.classList.remove('channel-loading-pending');
        feed?.classList.remove('is-loading');
        document.querySelectorAll('.channel.pending-nav').forEach((el) => el.classList.remove('pending-nav'));
        try {
            sessionStorage.removeItem(this.CHANNEL_NAV_LOADING_KEY);
        }
        catch {
            // Ignore storage errors.
        }
    }
    setupChannelNavigationLoading() {
        document.querySelectorAll('.channel[href]').forEach((link) => {
            link.addEventListener('click', (event) => {
                if (event.defaultPrevented)
                    return;
                if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
                    return;
                if (link.target && link.target !== '_self')
                    return;
                const selected = link.dataset.channelKey || '';
                const href = link.href || '';
                if (!selected || !href || selected === this.selected)
                    return;
                event.preventDefault();
                const title = link.querySelector('.name')?.textContent?.trim()
                    || link.querySelector('.url')?.textContent?.trim()
                    || selected;
                document.querySelectorAll('.channel.pending-nav').forEach((el) => el.classList.remove('pending-nav'));
                link.classList.add('pending-nav');
                this.persistChannelNavigationState({
                    at: Date.now(),
                    selected,
                    title,
                    href,
                });
                this.showChannelLoading();
                requestAnimationFrame(() => {
                    window.setTimeout(() => {
                        window.location.assign(href);
                    }, 40);
                });
            });
        });
    }
    setupSidebarSearch() {
        const toggle = document.getElementById('channelSearchToggle');
        const row = document.getElementById('channelSearchRow');
        const input = document.getElementById('channelSearchInput');
        const empty = document.getElementById('channelSearchEmpty');
        if (!toggle || !row || !input)
            return;
        const getRows = () => [...document.querySelectorAll('#sidebar .channel')];
        const getSearchText = (row) => {
            return ((row.dataset.searchText || '').trim() || '').toLocaleLowerCase();
        };
        const applyFilter = () => {
            const query = (input.value || '').trim().toLocaleLowerCase();
            const rows = getRows();
            let visible = 0;
            rows.forEach((row) => {
                const matched = !query || getSearchText(row).includes(query);
                row.hidden = !matched;
                if (matched)
                    visible += 1;
            });
            if (empty)
                empty.hidden = !(query && rows.length > 0 && visible === 0);
        };
        const setOpen = (open) => {
            row.hidden = !open;
            toggle.classList.toggle('active', open);
            if (!open) {
                input.value = '';
                applyFilter();
            }
            else {
                window.setTimeout(() => input.focus(), 20);
            }
        };
        toggle.addEventListener('click', () => setOpen(row.hidden));
        input.addEventListener('input', applyFilter);
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                setOpen(false);
            }
        });
        this.refreshSidebarSearch = applyFilter;
        applyFilter();
    }
    setupMessageSearch() {
        const toggle = document.getElementById('messageSearchToggle');
        const bar = document.getElementById('messageSearchBar');
        const input = document.getElementById('messageSearchInput');
        const clearBtn = document.getElementById('messageSearchClear');
        const closeBtn = document.getElementById('messageSearchClose');
        const empty = document.getElementById('messageSearchEmpty');
        const messagesWrap = document.getElementById('messages');
        if (!toggle || !bar || !input || !messagesWrap)
            return;
        const getRows = () => [...messagesWrap.querySelectorAll('.msg[data-message-id]')];
        const getDateDividers = () => [...messagesWrap.querySelectorAll('.date-divider')];
        const getUnreadDivider = () => messagesWrap.querySelector('.unread-divider');
        const restoreText = (el) => {
            if (!el)
                return;
            const original = el.dataset.originalText;
            if (original == null) {
                el.dataset.originalText = el.textContent || '';
            }
            else {
                el.textContent = original;
            }
        };
        const highlightText = (el, query) => {
            if (!el)
                return;
            const original = el.dataset.originalText ?? (el.textContent || '');
            el.dataset.originalText = original;
            if (!query) {
                el.textContent = original;
                return;
            }
            const regex = new RegExp(`(${this.escapeRegExp(query)})`, 'ig');
            el.innerHTML = this.escapeHtml(original).replace(regex, '<mark>$1</mark>');
        };
        const setOpen = (open) => {
            bar.hidden = !open;
            toggle.classList.toggle('active', open);
            if (!open) {
                input.value = '';
                applyFilter();
            }
            else {
                window.setTimeout(() => input.focus(), 20);
            }
        };
        const applyFilter = () => {
            const query = (input.value || '').trim();
            const queryLower = query.toLocaleLowerCase();
            const rows = getRows();
            const dateDividers = getDateDividers();
            const unreadDivider = getUnreadDivider();
            let visible = 0;
            let firstVisible = null;
            toggle.disabled = rows.length === 0;
            rows.forEach((row) => {
                const textEl = row.querySelector('.text');
                const replyTextEl = row.querySelector('.reply-text');
                const replyAuthorEl = row.querySelector('.reply-author');
                const forwardSourceEl = row.querySelector('.forward-source');
                const text = textEl?.textContent || '';
                const replyText = replyTextEl?.textContent || '';
                const replyAuthor = replyAuthorEl?.textContent || '';
                const forwardSource = forwardSourceEl?.textContent || '';
                const blob = `${text}\n${replyText}\n${replyAuthor}\n${forwardSource}`.toLocaleLowerCase();
                const matched = !queryLower || blob.includes(queryLower);
                row.hidden = !matched;
                restoreText(textEl);
                restoreText(replyTextEl);
                restoreText(replyAuthorEl);
                restoreText(forwardSourceEl);
                if (matched && queryLower) {
                    highlightText(textEl, query);
                    highlightText(replyTextEl, query);
                    highlightText(replyAuthorEl, query);
                    highlightText(forwardSourceEl, query);
                }
                if (matched) {
                    visible += 1;
                    if (!firstVisible)
                        firstVisible = row;
                }
            });
            if (clearBtn)
                clearBtn.hidden = !query;
            if (empty)
                empty.hidden = !(query && visible === 0);
            if (unreadDivider)
                unreadDivider.hidden = !!query;
            dateDividers.forEach((divider) => {
                divider.hidden = !!query;
            });
            if (query && firstVisible)
                firstVisible.scrollIntoView({ block: 'nearest' });
        };
        toggle.addEventListener('click', () => setOpen(bar.hidden));
        closeBtn?.addEventListener('click', () => setOpen(false));
        clearBtn?.addEventListener('click', () => {
            input.value = '';
            applyFilter();
            input.focus();
        });
        input.addEventListener('input', applyFilter);
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                setOpen(false);
            }
        });
        applyFilter();
    }
    setupAddChannelBox() {
        const btnBottom = document.getElementById('addChannelBtnBottom');
        const btnMain = document.getElementById('addChannelBtnMain');
        const modal = document.getElementById('addModal');
        const form = document.getElementById('addModalForm');
        const cancel = document.getElementById('addModalCancel');
        const closeBtn = document.getElementById('addModalClose');
        const submit = document.getElementById('addModalSubmit');
        const textarea = form?.querySelector('textarea[name="channel"]');
        const errorHint = form?.querySelector('.add-form-error');
        const loadingHint = form?.querySelector('.add-form-loading');
        const preview = document.getElementById('channelPreview');
        const previewCount = document.getElementById('channelPreviewCount');
        const previewList = document.getElementById('channelPreviewList');
        if (!modal || !form)
            return;
        const renderPreview = () => {
            if (!preview || !previewList || !previewCount || !textarea)
                return;
            const entries = this.parseChannelDraft(textarea.value || '');
            const normalizedChannels = this.uniqueNormalizedChannels(entries);
            if (!entries.length) {
                preview.hidden = true;
                previewCount.textContent = this.t('index.channel_detected_none', 'No valid public channels detected yet.');
                previewList.innerHTML = '';
                return;
            }
            preview.hidden = false;
            previewCount.textContent = normalizedChannels.length
                ? this.withVars(this.t('index.channel_detected_count', '{count} channel(s) ready to add'), { count: String(normalizedChannels.length) })
                : this.t('index.channel_detected_invalid', 'No valid public channel IDs detected yet.');
            previewList.innerHTML = entries.slice(0, 12).map((entry) => {
                const tone = entry.valid ? 'ok' : 'error';
                const title = entry.valid ? entry.normalized : entry.raw;
                const username = entry.valid ? entry.normalized.split('/').pop() || entry.normalized : entry.raw;
                const subline = entry.valid
                    ? this.escapeHtml(entry.normalized)
                    : this.t('index.channel_preview_invalid', 'Unsupported or private link');
                return `
          <div class="preview-chip ${tone}" title="${this.escapeHtml(title)}">
            <strong>${entry.valid ? `@${this.escapeHtml(username)}` : this.escapeHtml(entry.raw)}</strong>
            <span>${subline}</span>
          </div>
        `;
            }).join('');
        };
        const open = (ev) => {
            ev.preventDefault();
            if (this.requiresDomainBeforeChannel())
                return;
            if (loadingHint)
                loadingHint.hidden = true;
            if (errorHint)
                errorHint.hidden = true;
            modal.hidden = false;
            renderPreview();
            setTimeout(() => textarea?.focus(), 20);
        };
        const close = () => {
            modal.hidden = true;
        };
        btnBottom?.addEventListener('click', open);
        btnMain?.addEventListener('click', open);
        closeBtn?.addEventListener('click', (ev) => {
            ev.preventDefault();
            close();
        });
        cancel?.addEventListener('click', (ev) => {
            ev.preventDefault();
            close();
        });
        modal.addEventListener('click', (ev) => {
            if (ev.target === modal)
                close();
        });
        const setSubmitting = (on) => {
            form.querySelectorAll('input,textarea,button').forEach((el) => {
                if (el === cancel) {
                    el.disabled = on;
                    return;
                }
                el.disabled = on;
            });
            if (loadingHint)
                loadingHint.hidden = !on;
            if (submit)
                submit.textContent = this.t(on ? 'index.adding' : 'index.add_channel_submit', on ? 'Adding...' : 'Add channels');
        };
        const addPendingChannels = (normalizedChannels) => {
            const sidebar = document.getElementById('sidebar');
            if (!sidebar)
                return;
            const addBox = sidebar.querySelector('.sidebar-add-box');
            const emptyState = sidebar.querySelector('.sidebar-empty');
            emptyState?.remove();
            normalizedChannels.forEach((sourceUrl) => {
                if (!sourceUrl)
                    return;
                if (sidebar.querySelector(`.channel[data-channel-key="${sourceUrl}"]`))
                    return;
                const username = sourceUrl.split('/').pop() || '';
                const safeUsername = this.escapeHtml(username);
                const safeSourceUrl = this.escapeHtml(sourceUrl);
                const row = document.createElement('div');
                row.className = 'channel pending';
                row.dataset.channelKey = sourceUrl;
                row.dataset.latestId = '0';
                row.dataset.searchText = `@${username} ${sourceUrl}`;
                row.innerHTML = `
          <div class="avatar avatar-fallback" aria-hidden="true">${this.escapeHtml(this.channelAvatarText(username))}</div>
          <div class="channel-main">
            <div class="name">@${safeUsername}</div>
            <div class="url">${safeSourceUrl}</div>
          </div>
          <div class="spinner"></div>
        `;
                if (addBox?.nextSibling) {
                    sidebar.insertBefore(row, addBox.nextSibling);
                }
                else {
                    sidebar.appendChild(row);
                }
            });
            this.refreshSidebarSearch?.();
        };
        textarea?.addEventListener('input', () => {
            if (errorHint)
                errorHint.hidden = true;
            renderPreview();
        });
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && !modal.hidden)
                close();
        });
        form.addEventListener('submit', async (ev) => {
            ev.preventDefault();
            const channelsRaw = textarea?.value?.trim() || '';
            if (errorHint)
                errorHint.hidden = true;
            const parsed = this.parseChannelDraft(channelsRaw);
            const channels = this.uniqueNormalizedChannels(parsed);
            if (!channels.length) {
                if (errorHint) {
                    const hasAnyInput = this.tokenizeChannelDraft(channelsRaw).length > 0;
                    errorHint.textContent = this.t(hasAnyInput ? 'index.channel_invalid_public_only' : 'index.channel_required', hasAnyInput ? 'Only public Telegram channel usernames and links are supported.' : 'Channel is required.');
                    errorHint.hidden = false;
                }
                return;
            }
            if (this.requiresDomainBeforeChannel()) {
                if (errorHint) {
                    errorHint.textContent = this.t('index.add_domain_first', 'Add a domain first to use DNS mode.');
                    errorHint.hidden = false;
                }
                return;
            }
            if (textarea)
                textarea.value = channels.join('\n');
            const payload = new FormData(form);
            if (channels.length)
                addPendingChannels(channels);
            setSubmitting(true);
            try {
                const response = await fetch(form.action, {
                    method: 'POST',
                    body: payload,
                    redirect: 'follow',
                });
                if (response.redirected && response.url) {
                    window.location.href = response.url;
                    return;
                }
                window.location.reload();
            }
            catch {
                if (errorHint) {
                    errorHint.textContent = this.t('index.request_failed', 'Request failed. Check your connection and try again.');
                    errorHint.hidden = false;
                }
                setSubmitting(false);
            }
        });
    }
    setupAddDomainBox() {
        const btnBottom = document.getElementById('addDomainBtnBottom');
        const btnMain = document.getElementById('addDomainBtnMain');
        const modal = document.getElementById('addDomainModal');
        const form = document.getElementById('addDomainModalForm');
        const cancel = document.getElementById('addDomainModalCancel');
        const closeBtn = document.getElementById('addDomainModalClose');
        const submit = document.getElementById('addDomainModalSubmit');
        const domainInput = form?.querySelector('input[name="domain"]');
        const passwordInput = form?.querySelector('input[name="password"]');
        const errorHint = form?.querySelector('.add-form-error');
        const loadingHint = form?.querySelector('.add-form-loading');
        const normalizedHint = document.getElementById('addDomainNormalizedHint');
        if (!modal || !form)
            return;
        const renderNormalizedHint = () => {
            if (!normalizedHint || !domainInput)
                return;
            const raw = domainInput.value || '';
            const normalized = this.normalizeDomainInputValue(raw);
            if (!raw.trim()) {
                normalizedHint.hidden = true;
                normalizedHint.classList.remove('invalid');
                normalizedHint.textContent = '';
                return;
            }
            normalizedHint.hidden = false;
            if (!normalized) {
                normalizedHint.classList.add('invalid');
                normalizedHint.textContent = this.t('index.domain_invalid', 'Enter a valid domain or URL.');
                return;
            }
            normalizedHint.classList.remove('invalid');
            normalizedHint.textContent = this.withVars(this.t('index.domain_normalized_hint', 'Will save as {domain}'), {
                domain: normalized,
            });
        };
        const open = (ev) => {
            ev.preventDefault();
            if (loadingHint)
                loadingHint.hidden = true;
            if (errorHint)
                errorHint.hidden = true;
            modal.hidden = false;
            renderNormalizedHint();
            setTimeout(() => domainInput?.focus(), 20);
        };
        const close = () => {
            modal.hidden = true;
        };
        btnBottom?.addEventListener('click', open);
        btnMain?.addEventListener('click', open);
        closeBtn?.addEventListener('click', (ev) => {
            ev.preventDefault();
            close();
        });
        cancel?.addEventListener('click', (ev) => {
            ev.preventDefault();
            close();
        });
        modal.addEventListener('click', (ev) => {
            if (ev.target === modal)
                close();
        });
        const setSubmitting = (on) => {
            form.querySelectorAll('input,button').forEach((el) => {
                if (el === cancel) {
                    el.disabled = on;
                    return;
                }
                el.disabled = on;
            });
            if (loadingHint)
                loadingHint.hidden = !on;
            if (submit)
                submit.textContent = this.t(on ? 'index.adding' : 'index.add_domain_submit', on ? 'Adding...' : 'Add domain');
        };
        domainInput?.addEventListener('input', () => {
            if (errorHint)
                errorHint.hidden = true;
            renderNormalizedHint();
        });
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && !modal.hidden)
                close();
        });
        form.addEventListener('submit', async (ev) => {
            ev.preventDefault();
            const domain = this.normalizeDomainInputValue(domainInput?.value || '');
            if (errorHint)
                errorHint.hidden = true;
            if (!domain) {
                if (errorHint) {
                    errorHint.textContent = this.t('index.domain_invalid', 'Enter a valid domain or URL.');
                    errorHint.hidden = false;
                }
                return;
            }
            if (domainInput)
                domainInput.value = domain;
            if (passwordInput)
                passwordInput.value = (passwordInput.value || '').trim();
            const payload = new FormData(form);
            setSubmitting(true);
            try {
                const response = await fetch(form.action, {
                    method: 'POST',
                    body: payload,
                    redirect: 'follow',
                });
                if (response.redirected && response.url) {
                    window.location.href = response.url;
                    return;
                }
                window.location.reload();
            }
            catch {
                if (errorHint) {
                    errorHint.textContent = this.t('index.request_failed', 'Request failed. Check your connection and try again.');
                    errorHint.hidden = false;
                }
                setSubmitting(false);
            }
        });
    }
    formatDuration(seconds) {
        if (seconds == null || !Number.isFinite(seconds) || seconds < 0)
            return '-';
        const total = Math.max(0, Math.round(seconds));
        const hours = Math.floor(total / 3600);
        const minutes = Math.floor((total % 3600) / 60);
        const secs = total % 60;
        if (hours > 0)
            return `${hours}h ${minutes}m`;
        if (minutes > 0)
            return `${minutes}m ${secs}s`;
        return `${secs}s`;
    }
    renderSyncChart(points) {
        const line = document.querySelector('#syncChartLine');
        if (!line)
            return;
        const values = points.length ? points : [0];
        const width = 320;
        const height = 72;
        const coords = values.map((value, index) => {
            const x = values.length === 1 ? 0 : (index / (values.length - 1)) * width;
            const pct = Math.max(0, Math.min(100, value));
            const y = height - (pct / 100) * height;
            return `${x.toFixed(1)},${y.toFixed(1)}`;
        });
        line.setAttribute('points', coords.join(' '));
    }
    buildSyncSummary(job) {
        if (job.status === 'error') {
            return this.withVars(this.t('index.sync_failed', 'Sync failed: {error}'), { error: job.error || this.t('index.sync_unknown_error', 'unknown error') });
        }
        if (job.saved > 0) {
            return this.withVars(this.t('index.sync_saved_summary', 'Updated {count} message(s).'), { count: String(job.saved) });
        }
        return this.t('index.sync_no_messages', 'No new messages were available.');
    }
    setupSyncDialog() {
        const trigger = document.getElementById('syncNowBtn');
        const modal = document.getElementById('syncModal');
        const closeBtn = document.getElementById('syncModalClose');
        const phase = document.getElementById('syncModalPhase');
        const fill = document.getElementById('syncProgressFill');
        const percent = document.getElementById('syncProgressPercent');
        const eta = document.getElementById('syncEtaLabel');
        const domains = document.getElementById('syncDomainsStat');
        const channels = document.getElementById('syncChannelsStat');
        const messages = document.getElementById('syncMessagesStat');
        const saved = document.getElementById('syncSavedStat');
        const currentDomain = document.getElementById('syncCurrentDomain');
        const currentChannel = document.getElementById('syncCurrentChannel');
        const resultBox = document.getElementById('syncResultBox');
        if (!trigger || !modal)
            return;
        let pollTimer = null;
        let jobId = '';
        let chartHistory = [];
        let reloadScheduled = false;
        const stopPolling = () => {
            if (pollTimer != null) {
                window.clearTimeout(pollTimer);
                pollTimer = null;
            }
        };
        const close = () => {
            modal.hidden = true;
            stopPolling();
        };
        const render = (job) => {
            const pct = Math.max(0, Math.min(100, Number(job.progress_percent || 0)));
            chartHistory.push(pct);
            if (chartHistory.length > 48)
                chartHistory = chartHistory.slice(-48);
            this.renderSyncChart(chartHistory);
            if (phase)
                phase.textContent = job.message || this.t('index.sync_waiting', 'Waiting to start...');
            if (fill)
                fill.style.width = `${pct}%`;
            if (percent)
                percent.textContent = `${Math.round(pct)}%`;
            if (eta)
                eta.textContent = `${this.t('index.sync_eta', 'ETA')} ${this.formatDuration(job.eta_seconds)}`;
            if (domains)
                domains.textContent = `${job.domains_done} / ${job.domains_total}`;
            if (channels)
                channels.textContent = `${job.channels_done} / ${job.channels_total}`;
            if (messages)
                messages.textContent = `${job.messages_done} / ${job.messages_total}`;
            if (saved)
                saved.textContent = String(job.saved || 0);
            if (currentDomain)
                currentDomain.textContent = job.current_domain || '-';
            if (currentChannel)
                currentChannel.textContent = job.current_channel || '-';
            if (resultBox) {
                if (job.status === 'running' || job.status === 'queued') {
                    resultBox.hidden = true;
                    resultBox.classList.remove('error');
                }
                else {
                    resultBox.hidden = false;
                    resultBox.classList.toggle('error', job.status === 'error' || job.ok === false);
                    resultBox.textContent = this.buildSyncSummary(job);
                }
            }
            if (job.status === 'done' && job.ok !== false && !reloadScheduled) {
                reloadScheduled = true;
                window.setTimeout(() => {
                    void this.refreshCurrentChannelView().catch(() => { });
                }, 500);
            }
        };
        const poll = async () => {
            if (!jobId)
                return;
            try {
                const response = await fetch(`/sync-now/status?id=${encodeURIComponent(jobId)}`, { cache: 'no-cache' });
                const payload = (await response.json());
                if (!response.ok || !payload.ok || !payload.job) {
                    throw new Error(payload.error || 'sync_status_failed');
                }
                render(payload.job);
                if (payload.job.status === 'running' || payload.job.status === 'queued') {
                    pollTimer = window.setTimeout(() => void poll(), 900);
                }
            }
            catch (err) {
                if (resultBox) {
                    resultBox.hidden = false;
                    resultBox.classList.add('error');
                    resultBox.textContent = this.withVars(this.t('index.sync_failed', 'Sync failed: {error}'), { error: String(err) });
                }
            }
        };
        const start = async () => {
            modal.hidden = false;
            stopPolling();
            chartHistory = [];
            reloadScheduled = false;
            if (resultBox) {
                resultBox.hidden = true;
                resultBox.classList.remove('error');
                resultBox.textContent = '';
            }
            if (phase)
                phase.textContent = this.t('index.sync_waiting', 'Waiting to start...');
            try {
                const response = await fetch('/sync-now', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ channel: this.selected || '' }),
                });
                const payload = (await response.json());
                if (!response.ok || !payload.ok || !payload.job) {
                    throw new Error(payload.error || 'sync_start_failed');
                }
                jobId = payload.job.id;
                render(payload.job);
                pollTimer = window.setTimeout(() => void poll(), 200);
            }
            catch (err) {
                if (resultBox) {
                    resultBox.hidden = false;
                    resultBox.classList.add('error');
                    resultBox.textContent = this.withVars(this.t('index.sync_failed', 'Sync failed: {error}'), { error: String(err) });
                }
            }
        };
        trigger.addEventListener('click', () => {
            void start();
        });
        closeBtn?.addEventListener('click', close);
        modal.addEventListener('click', (ev) => {
            if (ev.target === modal)
                close();
        });
    }
    setupImageViewer() {
        const modal = document.getElementById('imageViewer');
        const stage = document.getElementById('imageViewerStage');
        const shell = document.getElementById('imageViewerShell');
        const image = document.getElementById('imageViewerImage');
        const closeBtn = document.getElementById('imageViewerClose');
        const thumbs = [...document.querySelectorAll('.msg-photo')];
        if (!modal || !stage || !shell || !image || !thumbs.length)
            return;
        const staticBound = modal.dataset.viewerStaticBound === '1';
        let open = false;
        let pointerId = null;
        let startY = 0;
        let deltaY = 0;
        let dragging = false;
        let moved = false;
        const resetVisual = () => {
            shell.style.transition = 'transform .18s ease';
            modal.style.transition = 'background .18s ease';
            closeBtn?.style.setProperty('opacity', '1');
            shell.style.transform = 'translate3d(0,0,0) scale(1)';
            modal.style.background = 'rgba(3,8,12,.92)';
        };
        const applyDrag = (offsetY) => {
            const distance = Math.abs(offsetY);
            const scale = Math.max(0.88, 1 - Math.min(0.12, distance / 1200));
            const opacity = Math.max(0.38, 0.92 - Math.min(0.54, distance / 420));
            shell.style.transform = `translate3d(0, ${offsetY}px, 0) scale(${scale})`;
            modal.style.background = `rgba(3,8,12,${opacity})`;
            closeBtn?.style.setProperty('opacity', `${Math.max(0.3, 1 - Math.min(0.7, distance / 180))}`);
        };
        const close = () => {
            if (!open)
                return;
            open = false;
            dragging = false;
            pointerId = null;
            moved = false;
            deltaY = 0;
            document.body.classList.remove('viewer-open');
            resetVisual();
            modal.hidden = true;
            image.removeAttribute('src');
            image.alt = '';
        };
        const finishDrag = () => {
            if (!dragging)
                return;
            dragging = false;
            const shouldClose = Math.abs(deltaY) > Math.max(110, window.innerHeight * 0.14);
            if (shouldClose) {
                close();
                return;
            }
            deltaY = 0;
            resetVisual();
        };
        const onPointerDown = (event) => {
            if (!open || !stage.contains(event.target))
                return;
            pointerId = event.pointerId;
            startY = event.clientY;
            deltaY = 0;
            moved = false;
            dragging = true;
            shell.style.transition = 'none';
            modal.style.transition = 'none';
            stage.setPointerCapture?.(event.pointerId);
        };
        const onPointerMove = (event) => {
            if (!open || !dragging || pointerId !== event.pointerId)
                return;
            deltaY = event.clientY - startY;
            if (Math.abs(deltaY) > 4)
                moved = true;
            applyDrag(deltaY);
            if (moved)
                event.preventDefault();
        };
        const onPointerUp = (event) => {
            if (pointerId !== event.pointerId)
                return;
            stage.releasePointerCapture?.(event.pointerId);
            finishDrag();
            pointerId = null;
            window.setTimeout(() => {
                moved = false;
            }, 0);
        };
        const show = (src, alt) => {
            if (!src)
                return;
            open = true;
            pointerId = null;
            startY = 0;
            deltaY = 0;
            dragging = false;
            moved = false;
            image.src = src;
            image.alt = alt || '';
            modal.hidden = false;
            document.body.classList.add('viewer-open');
            resetVisual();
        };
        thumbs.forEach((thumb) => {
            if (thumb.dataset.viewerBound === '1')
                return;
            thumb.dataset.viewerBound = '1';
            thumb.tabIndex = 0;
            thumb.addEventListener('click', () => show(thumb.currentSrc || thumb.src, thumb.alt || ''));
            thumb.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    show(thumb.currentSrc || thumb.src, thumb.alt || '');
                }
            });
        });
        if (staticBound)
            return;
        modal.dataset.viewerStaticBound = '1';
        closeBtn?.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            close();
        });
        modal.addEventListener('click', (event) => {
            if (!open || moved)
                return;
            if (event.target === modal || event.target === stage)
                close();
        });
        stage.addEventListener('pointerdown', onPointerDown);
        stage.addEventListener('pointermove', onPointerMove);
        stage.addEventListener('pointerup', onPointerUp);
        stage.addEventListener('pointercancel', onPointerUp);
        stage.addEventListener('pointerleave', (event) => {
            if (dragging && pointerId === event.pointerId)
                onPointerUp(event);
        });
        document.addEventListener('keydown', (event) => {
            if (open && event.key === 'Escape')
                close();
        });
    }
    registerSW() {
        if (!('serviceWorker' in navigator))
            return;
        navigator.serviceWorker.register('/sw.js').catch(() => undefined);
    }
    setupInstallPrompt() {
        const prompt = document.getElementById('installPrompt');
        const title = document.getElementById('installPromptTitle');
        const text = document.getElementById('installPromptText');
        const action = document.getElementById('installPromptAction');
        const dismiss = document.getElementById('installPromptDismiss');
        if (!prompt || !title || !text || !action || !dismiss)
            return;
        const nav = navigator;
        const ua = (nav.userAgent || '').toLowerCase();
        const isIOS = /iphone|ipad|ipod/.test(ua);
        const isAndroid = /android/.test(ua);
        const isSafari = /safari/.test(ua) && !/crios|fxios|edgios|opr\//.test(ua);
        const isStandalone = window.matchMedia('(display-mode: standalone)').matches || nav.standalone === true;
        const isMobileViewport = window.matchMedia('(max-width: 900px)').matches;
        let deferredPrompt = null;
        const hiddenByUser = () => localStorage.getItem(this.INSTALL_PROMPT_KEY) === '1';
        const hidePrompt = (persist = false) => {
            prompt.hidden = true;
            if (persist)
                localStorage.setItem(this.INSTALL_PROMPT_KEY, '1');
        };
        const showPrompt = (mode) => {
            if (isStandalone || hiddenByUser() || !isMobileViewport)
                return;
            title.textContent = this.t('index.install_prompt_title', 'Add Kabootar to your home screen');
            if (mode === 'android') {
                text.textContent = this.t('index.install_prompt_android', 'Install this web app for faster access and a full-screen experience.');
                action.hidden = false;
                action.textContent = this.t('index.install_prompt_action', 'Add to Home');
            }
            else {
                text.textContent = this.t('index.install_prompt_ios', 'Use Safari Share and then tap Add to Home Screen.');
                action.hidden = true;
            }
            prompt.hidden = false;
        };
        dismiss.addEventListener('click', () => hidePrompt(true));
        action.addEventListener('click', async () => {
            if (!deferredPrompt) {
                hidePrompt(true);
                return;
            }
            try {
                await deferredPrompt.prompt();
                if (deferredPrompt.userChoice)
                    await deferredPrompt.userChoice;
                hidePrompt(true);
            }
            catch {
                hidePrompt(false);
            }
            finally {
                deferredPrompt = null;
            }
        });
        window.addEventListener('appinstalled', () => hidePrompt(true));
        window.addEventListener('beforeinstallprompt', (event) => {
            event.preventDefault();
            deferredPrompt = event;
            if (isAndroid)
                showPrompt('android');
        });
        if (isIOS && isSafari && !isStandalone && !hiddenByUser()) {
            window.setTimeout(() => showPrompt('ios'), 1200);
        }
    }
    async refreshCurrentChannelView() {
        if (!this.selected)
            return false;
        const wrap = document.getElementById('messages');
        const headerPrimary = document.getElementById('chatHeaderPrimary');
        const searchToggle = document.getElementById('messageSearchToggle');
        if (!wrap || !headerPrimary)
            return false;
        const hadMessages = !!wrap.querySelector('.msg[data-message-id]');
        const stickToBottom = wrap.scrollHeight - wrap.scrollTop - wrap.clientHeight < 40;
        const distanceFromBottom = Math.max(0, wrap.scrollHeight - wrap.scrollTop - wrap.clientHeight);
        const response = await fetch(`/channel/state?channel=${encodeURIComponent(this.selected)}`, {
            cache: 'no-store',
            headers: {
                Accept: 'application/json',
                'X-Kabootar-Request': 'fetch',
            },
        });
        const payload = (await response.json());
        if (!response.ok || !payload.ok) {
            throw new Error(payload.error || 'channel_refresh_failed');
        }
        headerPrimary.innerHTML = payload.header_html || '';
        wrap.innerHTML = payload.messages_html || '';
        if (searchToggle)
            searchToggle.disabled = !!payload.search_disabled;
        const activeRow = [...document.querySelectorAll('.channel')].find((el) => el.dataset.channelKey === this.selected);
        if (activeRow) {
            activeRow.dataset.latestId = String(Math.max(0, Number(payload.latest_id || 0) || 0));
        }
        const readMap = this.loadReadMap();
        this.applyI18n(headerPrimary);
        this.applyI18n(wrap);
        this.applyUnsupportedMediaLabels(wrap);
        this.applyTimes();
        this.addDateDividers();
        this.applyUnreadBadges(readMap);
        const divider = this.addUnreadDivider(readMap);
        this.setupImageViewer();
        this.refreshSidebarSearch?.();
        const messageSearchInput = document.getElementById('messageSearchInput');
        if (messageSearchInput?.value?.trim()) {
            messageSearchInput.dispatchEvent(new Event('input', { bubbles: true }));
            return true;
        }
        if (!payload.message_count) {
            wrap.scrollTop = 0;
            return true;
        }
        if (!hadMessages) {
            this.scrollToUnreadOrBottom(divider);
            return true;
        }
        const applyPosition = () => {
            if (stickToBottom) {
                wrap.scrollTop = wrap.scrollHeight;
                return;
            }
            const maxScroll = Math.max(0, wrap.scrollHeight - wrap.clientHeight);
            wrap.scrollTop = Math.max(0, Math.min(maxScroll, wrap.scrollHeight - wrap.clientHeight - distanceFromBottom));
        };
        applyPosition();
        requestAnimationFrame(applyPosition);
        return true;
    }
    hasBlockingModalOpen() {
        return ['addModal', 'addDomainModal', 'syncModal', 'imageViewer'].some((id) => {
            const el = document.getElementById(id);
            return !!el && !el.hidden;
        });
    }
    hasActiveInputFocus() {
        const active = document.activeElement;
        if (!active)
            return false;
        if (active.isContentEditable)
            return true;
        return ['INPUT', 'TEXTAREA', 'SELECT'].includes(active.tagName);
    }
    hasActiveSearchQuery() {
        const channelSearch = document.getElementById('channelSearchInput')?.value?.trim() || '';
        const messageSearch = document.getElementById('messageSearchInput')?.value?.trim() || '';
        return !!channelSearch || !!messageSearch;
    }
    canAutoRefresh() {
        if (document.visibilityState !== 'visible')
            return false;
        if (this.hasBlockingModalOpen())
            return false;
        if (this.hasActiveInputFocus())
            return false;
        if (this.hasActiveSearchQuery())
            return false;
        return true;
    }
    scheduleAutoRefresh(delay = this.AUTO_REFRESH_MS) {
        if (this.autoRefreshTimer != null) {
            window.clearTimeout(this.autoRefreshTimer);
        }
        this.autoRefreshTimer = window.setTimeout(async () => {
            if (!this.canAutoRefresh()) {
                this.scheduleAutoRefresh();
                return;
            }
            try {
                await this.refreshCurrentChannelView();
            }
            catch {
                // Ignore intermittent refresh failures and retry on the next cycle.
            }
            this.scheduleAutoRefresh();
        }, delay);
    }
    setupAutoRefresh(initialDelay = this.AUTO_REFRESH_MS) {
        this.scheduleAutoRefresh(initialDelay);
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') {
                this.scheduleAutoRefresh();
            }
        });
        window.addEventListener('focus', () => {
            this.scheduleAutoRefresh();
        });
    }
    async mount() {
        await this.initI18n();
        localStorage.removeItem('kabootar_lite_v1');
        const channelNavigationState = this.loadChannelNavigationState();
        const shouldKeepChannelLoader = !!channelNavigationState && !!this.selected && channelNavigationState.selected === this.selected;
        if (shouldKeepChannelLoader) {
            this.showChannelLoading();
        }
        else {
            this.hideChannelLoading();
        }
        const readMap = this.loadReadMap();
        this.applyTimes();
        this.addDateDividers();
        this.applyUnreadBadges(readMap);
        const divider = this.addUnreadDivider(readMap);
        this.setupMobileMenu();
        this.setupChannelNavigationLoading();
        this.setupSidebarSearch();
        this.setupMessageSearch();
        this.setupAddDomainBox();
        this.setupAddChannelBox();
        this.setupSyncDialog();
        this.setupImageViewer();
        this.setupInstallPrompt();
        this.registerSW();
        const hasMessages = !!document.querySelector('.msg[data-message-id]');
        this.setupAutoRefresh(this.selected && !hasMessages ? 5000 : this.AUTO_REFRESH_MS);
        window.setTimeout(() => {
            this.scrollToUnreadOrBottom(divider);
            if (shouldKeepChannelLoader) {
                window.setTimeout(() => this.hideChannelLoading(), 420);
            }
        }, 30);
        if (this.selected) {
            const maybeMarkRead = () => {
                const wrap = document.getElementById('messages');
                if (!wrap)
                    return;
                const nearBottom = wrap.scrollHeight - wrap.scrollTop - wrap.clientHeight < 40;
                if (!nearBottom)
                    return;
                const channelNode = [...document.querySelectorAll('.channel')].find((el) => el.dataset.channelKey === this.selected);
                const latest = Number(channelNode?.dataset.latestId || 0);
                if (latest > Number(readMap[this.selected] || 0)) {
                    this.markCurrentAsRead(readMap);
                }
            };
            setTimeout(maybeMarkRead, 1500);
            const wrap = document.getElementById('messages');
            if (wrap)
                wrap.addEventListener('scroll', maybeMarkRead, { passive: true });
        }
    }
}
window.addEventListener('DOMContentLoaded', () => {
    void new MirrorApp().mount();
});
