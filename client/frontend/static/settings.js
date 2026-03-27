class SettingsView {
    constructor() {
        this.sourceMode = document.getElementById('source_mode');
        this.sourceModeSwitch = document.getElementById('sourceModeSwitch');
        this.directSection = document.getElementById('directSection');
        this.dnsSection = document.getElementById('dnsSection');
        this.querySizeInput = document.getElementById('dnsQuerySize');
        this.syncInput = document.getElementById('syncInterval');
        this.directChannelsHidden = document.getElementById('direct_channels');
        this.dnsClientChannelsHidden = document.getElementById('dns_client_channels');
        this.directProxiesHidden = document.getElementById('direct_proxies');
        this.dnsResolversHidden = document.getElementById('dns_resolvers');
        this.dnsDomainsHidden = document.getElementById('dns_domains');
        this.dnsUseSystemHidden = document.getElementById('dns_use_system_resolver');
        // Legacy hidden field (old format), optional for migration in existing installs.
        this.legacyDnsSourcesHidden = document.getElementById('dns_sources');
        this.legacyDnsRoutesHidden = document.getElementById('dns_channel_routes');
        this.directChannelsList = document.getElementById('directChannelsList');
        this.directProxiesList = document.getElementById('directProxiesList');
        this.dnsResolversList = document.getElementById('dnsResolversList');
        this.dnsDomainsList = document.getElementById('dnsDomainsList');
        this.dnsUseSystemCheckbox = document.getElementById('dnsUseSystem');
        this.addDnsResolverBtn = document.getElementById('addDnsResolver');
        this.domainHealthModal = document.getElementById('domainHealthModal');
        this.domainHealthSummary = document.getElementById('domainHealthSummary');
        this.domainHealthPayload = document.getElementById('domainHealthPayload');
        this.domainHealthClose = document.getElementById('domainHealthClose');
        this.form = document.getElementById('settingsForm');
        this.LANG_KEY = 'kabootar_lang';
        this.lang = 'fa';
        this.i18n = {};
        this.domainLastStatus = new WeakMap();
    }
    t(key, fallback = '') {
        return this.i18n[key] || fallback || key;
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
        });
        root.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
            const key = el.dataset.i18nPlaceholder || '';
            if (!key)
                return;
            el.placeholder = this.t(key, el.placeholder || '');
        });
    }
    updateLangToggleLabel() {
        const btn = document.getElementById('langToggle');
        if (!btn)
            return;
        btn.textContent = this.lang === 'fa' ? 'EN' : 'FA';
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
        document.documentElement.dir = this.lang === 'fa' ? 'rtl' : 'ltr';
        this.applyI18n();
        this.updateLangToggleLabel();
        const btn = document.getElementById('langToggle');
        btn?.addEventListener('click', () => {
            const next = this.lang === 'fa' ? 'en' : 'fa';
            localStorage.setItem(this.LANG_KEY, next);
            window.location.reload();
        });
    }
    splitValues(v) {
        return (v || '').split(/[,;\n\r،]+/).map((x) => x.trim()).filter(Boolean);
    }
    escapeAttr(value) {
        return value.replace(/&/g, '&amp;').replace(/\"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
    normalizeChannel(v) {
        const x = v.trim();
        if (!x)
            return '';
        if (x.startsWith('http://') || x.startsWith('https://') || x.startsWith('@'))
            return x;
        return '@' + x;
    }
    normalizeDomain(v) {
        return (v || '').trim().replace(/\.+$/, '').toLowerCase();
    }
    looksLikeChannelToken(v) {
        const token = (v || '').trim().toLowerCase();
        if (!token)
            return false;
        return token.startsWith('@') || token.startsWith('http://') || token.startsWith('https://') || token.includes('t.me');
    }
    normalizeResolverHost(v) {
        let host = (v || '').trim().replace(/^dns:\/\//, '');
        if (host.startsWith('[') && host.endsWith(']'))
            host = host.slice(1, -1).trim();
        return host;
    }
    parseResolverToken(token) {
        const raw = (token || '').trim();
        if (!raw)
            return null;
        let host = raw;
        let port = '53';
        if (raw.startsWith('[') && raw.includes(']')) {
            const end = raw.indexOf(']');
            host = raw.slice(1, end).trim();
            const rest = raw.slice(end + 1).trim();
            if (rest.startsWith(':') && /^\d+$/.test(rest.slice(1)))
                port = rest.slice(1);
            return host ? { host, port } : null;
        }
        if (raw.includes(',') && raw.split(',').length >= 2) {
            const p = raw.split(',').map((x) => x.trim());
            host = this.normalizeResolverHost(p[1] || p[0] || '');
            port = /^\d+$/.test(p[2] || '') ? p[2] : '53';
            return host ? { host, port } : null;
        }
        if (raw.indexOf(':') > -1 && raw.indexOf(':') === raw.lastIndexOf(':')) {
            const [h, pr] = raw.split(':', 2);
            if (/^\d+$/.test(pr || '')) {
                host = this.normalizeResolverHost(h || '');
                port = pr || '53';
                return host ? { host, port } : null;
            }
        }
        host = this.normalizeResolverHost(raw);
        return host ? { host, port } : null;
    }
    canonicalResolver(hostRaw, portRaw) {
        const host = this.normalizeResolverHost(hostRaw);
        if (!host)
            return '';
        let port = Number((portRaw || '').trim() || '53');
        if (!Number.isFinite(port) || port <= 0)
            port = 53;
        port = Math.max(1, Math.min(65535, Math.floor(port)));
        return port === 53 ? host : `${host}:${port}`;
    }
    formatEpoch(epochSeconds) {
        if (!Number.isFinite(epochSeconds) || epochSeconds <= 0)
            return '-';
        const d = new Date(epochSeconds * 1000);
        return Number.isNaN(d.getTime()) ? '-' : d.toLocaleString();
    }
    domainValues(row) {
        const domain = this.normalizeDomain(row.querySelector('.domain-name')?.value || '');
        const password = (row.querySelector('.domain-password')?.value || '').trim();
        return { domain, password };
    }
    setDomainBadge(row, text, state = 'idle') {
        const badge = row.querySelector('.domain-health-badge');
        if (!badge)
            return;
        badge.textContent = text;
        badge.dataset.state = state;
    }
    setDomainButtonsState(row, busy) {
        row.querySelectorAll('.domain-fetch,.domain-status').forEach((btn) => {
            btn.disabled = busy;
        });
    }
    async fetchJson(url, init = {}) {
        try {
            const response = await fetch(url, { ...init, cache: 'no-store' });
            const data = (await response.json().catch(() => ({})));
            if (response.ok)
                return data;
            return { ok: false, http_status: response.status, ...data };
        }
        catch (err) {
            return { ok: false, error: String(err || 'network_error') };
        }
    }
    statusSummary(domain, status) {
        const ok = !!status.ok;
        const channels = Number(status.channels || status.sync?.channels || 0);
        const saved = Number(status.sync?.saved || 0);
        const elapsed = Number(status.elapsed_total_ms || status.elapsed_ms || 0);
        const error = String(status.error || status.sync?.error || '');
        if (!ok)
            return `${this.t('settings.health_fail', 'Failed')}: ${error || 'unknown'}`;
        return `${this.t('settings.health_ok', 'OK')} ${domain} • ch:${channels} • saved:${saved} • ${elapsed}ms`;
    }
    showDomainHealthDialog(domain, status) {
        const checkedAt = Number(status.checked_at || status.last_seen || 0);
        const lastSeen = Number(status.last_seen || 0);
        const action = String(status.action || '-');
        const channels = Number(status.channels || status.sync?.channels || 0);
        const saved = Number(status.sync?.saved || 0);
        const elapsed = Number(status.elapsed_total_ms || status.elapsed_ms || 0);
        const resolvers = Array.isArray(status.resolvers) ? status.resolvers.join(', ') : '-';
        const error = String(status.error || status.sync?.error || '-');
        const lines = [
            `${this.t('settings.status_domain', 'Domain')}: ${domain}`,
            `${this.t('settings.status_action', 'Action')}: ${action}`,
            `${this.t('settings.status_ok', 'Result')}: ${status.ok ? this.t('settings.health_ok', 'OK') : this.t('settings.health_fail', 'Failed')}`,
            `${this.t('settings.status_channels', 'Channels')}: ${channels}`,
            `${this.t('settings.status_saved', 'Saved messages')}: ${saved}`,
            `${this.t('settings.status_elapsed', 'Elapsed')}: ${elapsed}ms`,
            `${this.t('settings.status_resolvers', 'Resolvers')}: ${resolvers}`,
            `${this.t('settings.status_checked', 'Checked at')}: ${this.formatEpoch(checkedAt)}`,
            `${this.t('settings.status_last_seen', 'Last seen')}: ${this.formatEpoch(lastSeen)}`,
            `${this.t('settings.status_error', 'Error')}: ${error || '-'}`,
        ];
        if (this.domainHealthSummary)
            this.domainHealthSummary.textContent = lines.join('\n');
        if (this.domainHealthPayload)
            this.domainHealthPayload.textContent = JSON.stringify(status, null, 2);
        if (this.domainHealthModal)
            this.domainHealthModal.hidden = false;
    }
    closeDomainHealthDialog() {
        if (this.domainHealthModal)
            this.domainHealthModal.hidden = true;
    }
    async fetchDomainNow(row) {
        const { domain, password } = this.domainValues(row);
        if (!domain) {
            this.setDomainBadge(row, this.t('settings.domain_required', 'Domain is required.'), 'error');
            return;
        }
        this.setDomainButtonsState(row, true);
        this.setDomainBadge(row, this.t('settings.checking', 'Checking...'), 'busy');
        const payload = await this.fetchJson('/dns/domain/check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ domain, password, action: 'fetch' }),
        });
        this.domainLastStatus.set(row, payload);
        const ok = !!payload.ok;
        this.setDomainBadge(row, this.statusSummary(domain, payload), ok ? 'ok' : 'error');
        this.setDomainButtonsState(row, false);
    }
    async openDomainStatus(row) {
        const { domain } = this.domainValues(row);
        if (!domain) {
            this.setDomainBadge(row, this.t('settings.domain_required', 'Domain is required.'), 'error');
            return;
        }
        this.setDomainButtonsState(row, true);
        const remote = await this.fetchJson(`/dns/domain/health?domain=${encodeURIComponent(domain)}`);
        this.setDomainButtonsState(row, false);
        let status = remote;
        if (!remote.ok && String(remote.error || '') === 'no_history') {
            const last = this.domainLastStatus.get(row);
            status = last ? last : remote;
            this.setDomainBadge(row, this.t('settings.no_history', 'No health history yet.'), 'idle');
        }
        else if (remote.ok) {
            this.domainLastStatus.set(row, remote);
            this.setDomainBadge(row, this.statusSummary(domain, remote), 'ok');
        }
        else {
            this.setDomainBadge(row, this.statusSummary(domain, remote), 'error');
        }
        this.showDomainHealthDialog(domain, status);
    }
    async hydrateDomainHealthBadges() {
        const rows = [...(this.dnsDomainsList?.querySelectorAll('.list-row') || [])];
        await Promise.all(rows.map(async (row) => {
            const { domain } = this.domainValues(row);
            if (!domain)
                return;
            const status = await this.fetchJson(`/dns/domain/health?domain=${encodeURIComponent(domain)}`);
            if (status.ok) {
                this.domainLastStatus.set(row, status);
                this.setDomainBadge(row, this.statusSummary(domain, status), 'ok');
            }
        }));
    }
    refreshIndices(container) {
        if (!container)
            return;
        [...container.querySelectorAll('.list-row .idx')].forEach((n, i) => {
            n.textContent = String(i + 1);
        });
    }
    attachRemove(row, container) {
        row.querySelector('.x')?.addEventListener('click', () => {
            row.remove();
            this.refreshIndices(container);
            if (container === this.dnsResolversList)
                this.syncResolverUiState();
        });
    }
    addSimpleRow(container, value = '', placeholderKey = 'settings.placeholder.channel') {
        if (!container)
            return;
        const row = document.createElement('div');
        row.className = 'list-row';
        row.innerHTML = `
      <div class="idx"></div>
      <div class="fields single">
        <input class="input val" value="${this.escapeAttr(value)}" data-i18n-placeholder="${placeholderKey}">
      </div>
      <button type="button" class="x">−</button>`;
        this.attachRemove(row, container);
        container.appendChild(row);
        this.applyI18n(row);
        this.refreshIndices(container);
    }
    addDnsResolverRow(host = '', port = '53') {
        if (!this.dnsResolversList)
            return;
        const row = document.createElement('div');
        row.className = 'list-row';
        row.innerHTML = `
      <div class="idx"></div>
      <div class="fields dns-resolver">
        <input class="input resolver-host" data-i18n-placeholder="settings.placeholder.resolver" value="${this.escapeAttr(host)}">
        <input class="input resolver-port" data-i18n-placeholder="settings.placeholder.port" value="${this.escapeAttr(port)}" inputmode="numeric">
      </div>
      <button type="button" class="x">−</button>`;
        this.attachRemove(row, this.dnsResolversList);
        this.dnsResolversList.appendChild(row);
        this.applyI18n(row);
        this.refreshIndices(this.dnsResolversList);
        this.syncResolverUiState();
    }
    addDnsDomainRow(domain = '', password = '') {
        if (!this.dnsDomainsList)
            return;
        const row = document.createElement('div');
        row.className = 'list-row';
        row.innerHTML = `
      <div class="idx"></div>
      <div class="fields domain-two">
        <input class="input domain-name" data-i18n-placeholder="settings.placeholder.domain" value="${this.escapeAttr(domain)}">
        <input class="input domain-password" type="password" data-i18n-placeholder="settings.placeholder.route_password" value="${this.escapeAttr(password)}" autocomplete="off">
        <div class="domain-health-line">
          <button type="button" class="btn-secondary domain-fetch" data-i18n="settings.fetch_now">Fetch now</button>
          <button type="button" class="btn-secondary domain-status" data-i18n="settings.show_status">Status</button>
          <span class="domain-health-badge" data-state="idle" data-i18n="settings.never_checked">Never checked</span>
        </div>
      </div>
      <button type="button" class="x">−</button>`;
        this.attachRemove(row, this.dnsDomainsList);
        row.querySelector('.domain-fetch')?.addEventListener('click', () => {
            void this.fetchDomainNow(row);
        });
        row.querySelector('.domain-status')?.addEventListener('click', () => {
            void this.openDomainStatus(row);
        });
        this.dnsDomainsList.appendChild(row);
        this.applyI18n(row);
        this.refreshIndices(this.dnsDomainsList);
    }
    currentMode() {
        const mode = (this.sourceMode?.value || 'direct').toLowerCase();
        return mode === 'dns' ? 'dns' : 'direct';
    }
    syncModeButtons() {
        const mode = this.currentMode();
        this.sourceModeSwitch?.querySelectorAll('.mode-btn').forEach((btn) => {
            btn.classList.toggle('active', (btn.dataset.mode || '') === mode);
        });
    }
    toggleSections() {
        const mode = this.currentMode();
        if (this.directSection)
            this.directSection.style.display = mode === 'direct' ? '' : 'none';
        if (this.dnsSection)
            this.dnsSection.style.display = mode === 'dns' ? '' : 'none';
        this.syncModeButtons();
    }
    syncResolverUiState() {
        const useSystem = !!this.dnsUseSystemCheckbox?.checked;
        if (this.addDnsResolverBtn)
            this.addDnsResolverBtn.disabled = useSystem;
        this.dnsResolversList?.querySelectorAll('.list-row').forEach((row) => {
            row.querySelectorAll('.resolver-host,.resolver-port').forEach((input) => {
                input.disabled = useSystem;
            });
            const removeBtn = row.querySelector('.x');
            if (removeBtn)
                removeBtn.disabled = useSystem;
            row.classList.toggle('disabled-row', useSystem);
        });
    }
    hydrateFromHidden() {
        const channelsRaw = (this.directChannelsHidden?.value || '').trim() || (this.dnsClientChannelsHidden?.value || '').trim();
        this.splitValues(channelsRaw).forEach((v) => this.addSimpleRow(this.directChannelsList, v, 'settings.placeholder.channel'));
        this.splitValues(this.directProxiesHidden?.value || '').forEach((v) => this.addSimpleRow(this.directProxiesList, v, 'settings.placeholder.proxy'));
        const resolverLines = [];
        (this.dnsResolversHidden?.value || '')
            .split('\n')
            .map((x) => x.trim())
            .filter(Boolean)
            .forEach((line) => resolverLines.push(line));
        // Legacy migration path (old dns_sources format).
        if (!resolverLines.length && this.legacyDnsSourcesHidden?.value) {
            this.legacyDnsSourcesHidden.value
                .split('\n')
                .map((x) => x.trim())
                .filter(Boolean)
                .forEach((line) => resolverLines.push(line));
        }
        resolverLines.forEach((line) => {
            const parsed = this.parseResolverToken(line);
            if (parsed)
                this.addDnsResolverRow(parsed.host, parsed.port);
        });
        const domainLines = [];
        (this.dnsDomainsHidden?.value || '')
            .split('\n')
            .map((x) => x.trim())
            .filter(Boolean)
            .forEach((line) => domainLines.push(line));
        // Legacy migration path (old dns_channel_routes format).
        if (!domainLines.length && this.legacyDnsRoutesHidden?.value) {
            this.legacyDnsRoutesHidden.value
                .split('\n')
                .map((x) => x.trim())
                .filter(Boolean)
                .forEach((line) => domainLines.push(line));
        }
        domainLines.forEach((line) => {
            const parts = line.split('|').map((x) => x.trim());
            let domain = '';
            let password = '';
            if (parts.length === 1) {
                domain = parts[0] || '';
            }
            else {
                const first = parts[0] || '';
                const second = parts[1] || '';
                if (!first) {
                    // Legacy: |domain|password
                    domain = second;
                    password = parts.length >= 3 ? parts.slice(2).join('|').trim() : '';
                }
                else if (this.looksLikeChannelToken(first)) {
                    // Legacy: channel|domain|password
                    domain = second;
                    password = parts.length >= 3 ? parts.slice(2).join('|').trim() : '';
                }
                else {
                    // New: domain|password
                    domain = first;
                    password = parts.slice(1).join('|').trim();
                }
            }
            domain = this.normalizeDomain(domain);
            if (domain)
                this.addDnsDomainRow(domain, password);
        });
        if (!this.directChannelsList?.children.length)
            this.addSimpleRow(this.directChannelsList, '', 'settings.placeholder.channel');
        if (!this.directProxiesList?.children.length)
            this.addSimpleRow(this.directProxiesList, '', 'settings.placeholder.proxy');
        if (!this.dnsResolversList?.children.length)
            this.addDnsResolverRow();
        if (!this.dnsDomainsList?.children.length)
            this.addDnsDomainRow();
        const useSystem = (this.dnsUseSystemHidden?.value || '1') === '1';
        if (this.dnsUseSystemCheckbox)
            this.dnsUseSystemCheckbox.checked = useSystem;
        this.syncResolverUiState();
    }
    serializeToHidden() {
        const channels = [...(this.directChannelsList?.querySelectorAll('.val') || [])]
            .map((i) => this.normalizeChannel(i.value))
            .filter(Boolean);
        const channelsCsv = channels.join(',');
        if (this.directChannelsHidden)
            this.directChannelsHidden.value = channelsCsv;
        if (this.dnsClientChannelsHidden)
            this.dnsClientChannelsHidden.value = channelsCsv;
        const proxies = [...(this.directProxiesList?.querySelectorAll('.val') || [])]
            .map((i) => i.value.trim())
            .filter(Boolean);
        if (this.directProxiesHidden)
            this.directProxiesHidden.value = proxies.join(',');
        const useSystem = !!this.dnsUseSystemCheckbox?.checked;
        if (this.dnsUseSystemHidden)
            this.dnsUseSystemHidden.value = useSystem ? '1' : '0';
        const resolvers = [...(this.dnsResolversList?.querySelectorAll('.list-row') || [])]
            .map((row) => {
            const host = row.querySelector('.resolver-host')?.value || '';
            const port = row.querySelector('.resolver-port')?.value || '53';
            return this.canonicalResolver(host, port);
        })
            .filter(Boolean)
            .filter((value, idx, arr) => arr.indexOf(value) === idx)
            .join('\n');
        if (this.dnsResolversHidden)
            this.dnsResolversHidden.value = resolvers;
        const domains = [...(this.dnsDomainsList?.querySelectorAll('.list-row') || [])]
            .map((row) => {
            const domain = this.normalizeDomain(row.querySelector('.domain-name')?.value || '');
            const password = (row.querySelector('.domain-password')?.value || '').trim();
            if (!domain)
                return '';
            return password ? `${domain}|${password}` : domain;
        })
            .filter(Boolean)
            .filter((value, idx, arr) => arr.indexOf(value) === idx)
            .join('\n');
        if (this.dnsDomainsHidden)
            this.dnsDomainsHidden.value = domains;
    }
    setupSortable() {
        const SortableEngine = window.Sortable;
        if (!SortableEngine)
            return;
        [this.directChannelsList, this.directProxiesList, this.dnsResolversList, this.dnsDomainsList].forEach((el) => {
            if (!el)
                return;
            SortableEngine.create(el, {
                animation: 120,
                handle: '.idx',
                onSort: () => this.refreshIndices(el),
            });
        });
    }
    bindModeSwitch() {
        this.sourceModeSwitch?.querySelectorAll('.mode-btn').forEach((btn) => {
            btn.addEventListener('click', () => {
                const mode = (btn.dataset.mode || 'direct').toLowerCase() === 'dns' ? 'dns' : 'direct';
                if (this.sourceMode)
                    this.sourceMode.value = mode;
                this.toggleSections();
            });
        });
    }
    async mount() {
        await this.initI18n();
        if (!this.form)
            return;
        this.hydrateFromHidden();
        this.setupSortable();
        this.bindModeSwitch();
        document.getElementById('addDirectChannel')?.addEventListener('click', () => this.addSimpleRow(this.directChannelsList, '', 'settings.placeholder.channel'));
        document.getElementById('addDirectProxy')?.addEventListener('click', () => this.addSimpleRow(this.directProxiesList, '', 'settings.placeholder.proxy'));
        this.addDnsResolverBtn?.addEventListener('click', () => {
            if (this.dnsUseSystemCheckbox?.checked)
                return;
            this.addDnsResolverRow();
        });
        document.getElementById('addDnsDomain')?.addEventListener('click', () => this.addDnsDomainRow());
        this.dnsUseSystemCheckbox?.addEventListener('change', () => this.syncResolverUiState());
        this.domainHealthClose?.addEventListener('click', () => this.closeDomainHealthDialog());
        this.domainHealthModal?.addEventListener('click', (ev) => {
            if (ev.target === this.domainHealthModal)
                this.closeDomainHealthDialog();
        });
        this.querySizeInput?.addEventListener('input', () => {
            const n = Number(this.querySizeInput?.value || '60');
            if (Number.isFinite(n) && n > 0)
                this.querySizeInput.value = String(Math.max(16, Math.min(220, Math.floor(n))));
        });
        this.syncInput?.addEventListener('input', () => {
            const n = Number(this.syncInput?.value || '1');
            if (Number.isFinite(n) && n > 0)
                this.syncInput.value = String(Math.max(1, Math.min(59, Math.floor(n))));
        });
        this.sourceMode?.addEventListener('change', () => this.toggleSections());
        this.toggleSections();
        await this.hydrateDomainHealthBadges();
        this.form?.addEventListener('submit', () => this.serializeToHidden());
    }
}
window.addEventListener('DOMContentLoaded', () => {
    void new SettingsView().mount();
});
export {};
