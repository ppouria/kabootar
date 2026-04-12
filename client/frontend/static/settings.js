class SettingsView {
    constructor() {
        this.sourceMode = document.getElementById('source_mode');
        this.sourceModeSwitch = document.getElementById('sourceModeSwitch');
        this.directSection = document.getElementById('directSection');
        this.dnsSection = document.getElementById('dnsSection');
        this.querySizeInput = document.getElementById('dnsQuerySize');
        this.dnsTimeoutInput = document.getElementById('dnsTimeoutSeconds');
        this.syncInput = document.getElementById('syncInterval');
        this.initialChannelHistoryInput = document.getElementById('initialChannelHistoryCount');
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
        this.resolverQuickScanBtn = document.getElementById('resolverQuickScanBtn');
        this.resolverDeepScanBtn = document.getElementById('resolverDeepScanBtn');
        this.resolverPauseBtn = document.getElementById('resolverPauseBtn');
        this.resolverResumeBtn = document.getElementById('resolverResumeBtn');
        this.resolverStopBtn = document.getElementById('resolverStopBtn');
        this.resolverRunE2eBtn = document.getElementById('resolverRunE2eBtn');
        this.resolverSaveBtn = document.getElementById('resolverSaveBtn');
        this.resolverSortSelect = document.getElementById('resolverSort');
        this.resolverScanOnlyCheckbox = document.getElementById('resolverScanOnly');
        this.resolverScanInlineE2eCheckbox = document.getElementById('resolverScanInlineE2e');
        this.resolverScanAutoApplyCheckbox = document.getElementById('resolverScanAutoApply');
        this.resolverScanImportBtn = document.getElementById('resolverScanImportBtn');
        this.resolverScanUseCurrentBtn = document.getElementById('resolverScanUseCurrentBtn');
        this.resolverScanClearBtn = document.getElementById('resolverScanClearBtn');
        this.resolverScanFileInput = document.getElementById('resolverScanFileInput');
        this.resolverScanInput = document.getElementById('resolverScanInput');
        this.resolverScanInputCount = document.getElementById('resolverScanInputCount');
        this.resolverScanConsole = document.getElementById('resolverScanConsole');
        this.resolverScanTop = document.getElementById('resolverScanTop');
        this.resolverScanTopRows = document.getElementById('resolverScanTopRows');
        this.form = document.getElementById('settingsForm');
        this.saveSettingsBtn = document.getElementById('saveSettingsBtn');
        this.settingsFlash = document.getElementById('settingsFlash');
        this.LANG_KEY = 'kabootar_lang';
        this.lang = 'fa';
        this.i18n = {};
        this.domainLastStatus = new WeakMap();
        this.saveButtonResetTimer = null;
        this.resolverScanJobId = '';
        this.resolverScanBusy = false;
        this.resolverScanPollTimer = null;
        this.resolverScanLastStatus = '';
        this.resolverScanLastControlState = '';
        this.resolverScanLastResult = null;
        this.resolverScanLastScanResult = null;
        this.resolverScanLastJob = null;
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
        const label = document.getElementById('langToggleLabel');
        if (label) {
            label.textContent = this.lang === 'fa' ? 'EN' : 'FA';
            return;
        }
        const btn = document.getElementById('langToggle');
        if (!btn)
            return;
        btn.textContent = this.lang === 'fa' ? 'EN' : 'FA';
    }
    clearSaveButtonResetTimer() {
        if (this.saveButtonResetTimer != null) {
            window.clearTimeout(this.saveButtonResetTimer);
            this.saveButtonResetTimer = null;
        }
    }
    saveButtonIcon(state) {
        if (state === 'loading') {
            return '<span class="save-btn-icon"><span class="save-btn-spinner" aria-hidden="true"></span></span>';
        }
        if (state === 'success') {
            return `
        <span class="save-btn-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none">
            <path d="M5 12.5L9.2 16.5L19 7.5" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"></path>
          </svg>
        </span>
      `;
        }
        if (state === 'error') {
            return `
        <span class="save-btn-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none">
            <path d="M12 8V13" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"></path>
            <circle cx="12" cy="17" r="1.3" fill="currentColor"></circle>
            <path d="M12 3.8L21 19.4C21.3 19.9 20.9 20.5 20.3 20.5H3.7C3.1 20.5 2.7 19.9 3 19.4L12 3.8Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"></path>
          </svg>
        </span>
      `;
        }
        return '';
    }
    renderSaveButton(state) {
        if (!this.saveSettingsBtn)
            return;
        const label = state === 'loading'
            ? this.t('settings.saving_settings', 'Saving...')
            : state === 'success'
                ? this.t('settings.saved_settings', 'Saved')
                : state === 'error'
                    ? this.t('settings.save_failed', 'Save failed')
                    : this.t('settings.save_settings', 'Save settings');
        this.saveSettingsBtn.disabled = state === 'loading';
        this.saveSettingsBtn.classList.toggle('is-loading', state === 'loading');
        this.saveSettingsBtn.classList.toggle('is-success', state === 'success');
        this.saveSettingsBtn.classList.toggle('is-error', state === 'error');
        this.saveSettingsBtn.innerHTML = `<span class="save-btn-content">${this.saveButtonIcon(state)}<span class="save-btn-label">${label}</span></span>`;
    }
    showFlash(message, tone = 'success') {
        if (!this.settingsFlash)
            return;
        const clean = String(message || '').trim();
        if (!clean) {
            this.settingsFlash.hidden = true;
            this.settingsFlash.textContent = '';
            this.settingsFlash.removeAttribute('data-tone');
            return;
        }
        this.settingsFlash.hidden = false;
        this.settingsFlash.dataset.tone = tone;
        this.settingsFlash.textContent = clean;
    }
    clearResolverScanPollTimer() {
        if (this.resolverScanPollTimer != null) {
            window.clearTimeout(this.resolverScanPollTimer);
            this.resolverScanPollTimer = null;
        }
    }
    setResolverScanBusy(busy) {
        this.resolverScanBusy = busy;
        const inputLock = busy;
        if (this.resolverScanImportBtn)
            this.resolverScanImportBtn.disabled = inputLock;
        if (this.resolverScanUseCurrentBtn)
            this.resolverScanUseCurrentBtn.disabled = inputLock;
        if (this.resolverScanClearBtn)
            this.resolverScanClearBtn.disabled = inputLock;
        if (this.resolverScanInput)
            this.resolverScanInput.disabled = inputLock;
        if (this.resolverScanOnlyCheckbox)
            this.resolverScanOnlyCheckbox.disabled = inputLock;
        if (this.resolverScanInlineE2eCheckbox)
            this.resolverScanInlineE2eCheckbox.disabled = inputLock;
        this.syncResolverScanOptionState();
        this.syncResolverScanActionState();
    }
    appendResolverConsole(lines) {
        if (!this.resolverScanConsole)
            return;
        this.resolverScanConsole.textContent = String(lines || '').trim();
    }
    resolverSortMode() {
        return String(this.resolverSortSelect?.value || 'score_latency').trim().toLowerCase();
    }
    sortedCompatible(result) {
        const list = Array.isArray(result?.compatible) ? [...result?.compatible] : [];
        const mode = this.resolverSortMode();
        list.sort((a, b) => {
            const aScore = Number(a.score || 0);
            const bScore = Number(b.score || 0);
            const aLatency = Number(a.latency_ms || 0);
            const bLatency = Number(b.latency_ms || 0);
            const aResolver = String(a.resolver || '');
            const bResolver = String(b.resolver || '');
            if (mode === 'latency') {
                return aLatency - bLatency || bScore - aScore || aResolver.localeCompare(bResolver);
            }
            if (mode === 'score') {
                return bScore - aScore || aLatency - bLatency || aResolver.localeCompare(bResolver);
            }
            if (mode === 'resolver') {
                return aResolver.localeCompare(bResolver) || bScore - aScore || aLatency - bLatency;
            }
            return bScore - aScore || aLatency - bLatency || aResolver.localeCompare(bResolver);
        });
        return list;
    }
    scanCompatibleResolvers(result) {
        const out = [];
        const seen = new Set();
        for (const item of this.sortedCompatible(result)) {
            const resolver = String(item.resolver || '').trim();
            if (!resolver || seen.has(resolver))
                continue;
            seen.add(resolver);
            out.push(resolver);
        }
        return out;
    }
    syncResolverScanOptionState() {
        if (!this.resolverScanAutoApplyCheckbox)
            return;
        const scanOnly = !!this.resolverScanOnlyCheckbox?.checked;
        if (scanOnly) {
            this.resolverScanAutoApplyCheckbox.checked = false;
        }
        this.resolverScanAutoApplyCheckbox.disabled = scanOnly || this.resolverScanBusy;
    }
    syncResolverScanActionState() {
        const status = this.resolverScanLastStatus;
        const controlState = this.resolverScanLastControlState;
        const active = this.resolverScanBusy || ['queued', 'running', 'paused'].includes(status);
        const paused = status === 'paused' || controlState === 'paused';
        if (this.resolverQuickScanBtn)
            this.resolverQuickScanBtn.disabled = active;
        if (this.resolverDeepScanBtn)
            this.resolverDeepScanBtn.disabled = active;
        if (this.resolverPauseBtn)
            this.resolverPauseBtn.disabled = !active || paused;
        if (this.resolverResumeBtn)
            this.resolverResumeBtn.disabled = !active || !paused;
        if (this.resolverStopBtn)
            this.resolverStopBtn.disabled = !active;
        const hasScanCandidates = this.scanCompatibleResolvers(this.resolverScanLastScanResult).length > 0;
        if (this.resolverRunE2eBtn)
            this.resolverRunE2eBtn.disabled = active || !hasScanCandidates;
        if (this.resolverSaveBtn)
            this.resolverSaveBtn.disabled = !this.resolverScanLastResult;
    }
    renderResolverTop(result) {
        if (!this.resolverScanTop || !this.resolverScanTopRows)
            return;
        this.resolverScanTopRows.innerHTML = '';
        const top = this.sortedCompatible(result).slice(0, 8);
        if (!top.length) {
            this.resolverScanTop.hidden = true;
            return;
        }
        this.resolverScanTop.hidden = false;
        for (const item of top) {
            const resolver = String(item.resolver || '-');
            const score = Number(item.score || 0);
            const latency = Number(item.latency_ms || 0);
            const details = String(item.details || '');
            const row = document.createElement('div');
            row.className = 'resolver-scan-top-row';
            row.innerHTML = `
        <div class="resolver-scan-top-main">
          <div class="resolver-scan-top-resolver">${this.escapeAttr(resolver)}</div>
          <div class="resolver-scan-top-details">${this.escapeAttr(details)}</div>
        </div>
        <div class="resolver-scan-top-meta">${score}/6 • ${latency}ms</div>
      `;
            this.resolverScanTopRows.appendChild(row);
        }
    }
    firstDomainFromUi() {
        const rows = [...(this.dnsDomainsList?.querySelectorAll('.list-row') || [])];
        for (const row of rows) {
            const current = this.domainValues(row);
            if (current.domain)
                return current;
        }
        return { domain: '', password: '' };
    }
    configuredResolversFromUi() {
        const out = [];
        const seen = new Set();
        const rows = [...(this.dnsResolversList?.querySelectorAll('.list-row') || [])];
        for (const row of rows) {
            const host = row.querySelector('.resolver-host')?.value || '';
            const port = row.querySelector('.resolver-port')?.value || '53';
            const canonical = this.canonicalResolver(host, port);
            if (!canonical || seen.has(canonical))
                continue;
            seen.add(canonical);
            out.push(canonical);
        }
        return out;
    }
    parseResolverScanInput(raw) {
        const out = [];
        const seen = new Set();
        for (const lineRaw of String(raw || '').split(/\r?\n/g)) {
            const line = lineRaw.split('#', 1)[0]?.trim() || '';
            if (!line)
                continue;
            const chunks = line.split(/[;,،\s]+/g).map((x) => x.trim()).filter(Boolean);
            for (const chunk of chunks) {
                const parsed = this.parseResolverToken(chunk);
                if (!parsed)
                    continue;
                const canonical = this.canonicalResolver(parsed.host, parsed.port);
                if (!canonical || seen.has(canonical))
                    continue;
                seen.add(canonical);
                out.push(canonical);
            }
        }
        return out;
    }
    setResolverScanInputValues(values) {
        if (!this.resolverScanInput)
            return;
        this.resolverScanInput.value = values.join('\n');
        this.updateResolverScanInputCount();
    }
    updateResolverScanInputCount() {
        if (!this.resolverScanInputCount)
            return;
        const count = this.parseResolverScanInput(this.resolverScanInput?.value || '').length;
        this.resolverScanInputCount.textContent = `${this.t('settings.resolver_scan_input_count', 'Resolvers for scan')}: ${count}`;
    }
    async importResolverScanFile(file) {
        const text = await file.text();
        const resolvers = this.parseResolverScanInput(text);
        if (!resolvers.length) {
            this.showFlash(this.t('settings.resolver_scan_import_empty', 'No valid resolver found in file.'), 'error');
            return;
        }
        this.setResolverScanInputValues(resolvers);
        this.showFlash(`${this.t('settings.resolver_scan_import_ok', 'Imported resolvers')}: ${resolvers.length}`, 'success');
    }
    applyAutoResolversToUi(applied) {
        if (!this.dnsResolversList)
            return;
        const clean = applied.map((x) => String(x || '').trim()).filter(Boolean);
        if (!clean.length)
            return;
        this.dnsResolversList.innerHTML = '';
        for (const token of clean) {
            const parsed = this.parseResolverToken(token);
            if (!parsed)
                continue;
            this.addDnsResolverRow(parsed.host, parsed.port);
        }
        if (this.dnsUseSystemCheckbox)
            this.dnsUseSystemCheckbox.checked = false;
        this.syncResolverUiState();
        this.serializeToHidden();
        this.setResolverScanInputValues(clean);
    }
    renderResolverScanJob(job) {
        const status = String(job.status || '');
        const controlState = String(job.control_state || '');
        const phaseKind = String(job.phase_kind || 'scan').toLowerCase();
        this.resolverScanLastStatus = status;
        this.resolverScanLastControlState = controlState;
        this.resolverScanLastJob = { ...job };
        const result = (job.result && typeof job.result === 'object') ? job.result : {};
        const resultMode = String(result.mode || phaseKind || 'scan').toLowerCase();
        if (Object.keys(result).length) {
            this.resolverScanLastResult = result;
            if (resultMode !== 'e2e')
                this.resolverScanLastScanResult = result;
        }
        const total = Number(job.total || 0);
        const scanned = Number(job.scanned || 0);
        const working = Number(job.working || 0);
        const timeout = Number(job.timeout || 0);
        const errorCount = Number(job.error_count || 0);
        const elapsedSeconds = Number(job.elapsed_seconds || 0);
        const e2eTotal = Number(job.e2e_total || 0);
        const e2eTested = Number(job.e2e_tested || 0);
        const e2ePassed = Number(job.e2e_passed || 0);
        const transparent = job.transparent_proxy_detected;
        const selected = String(job.selected_resolver || '');
        const autoApplied = !!job.auto_applied;
        const compatible = resultMode === 'e2e' ? [] : this.sortedCompatible(result);
        const e2eRows = Array.isArray(result.results) ? result.results : [];
        const stopped = !!job.stopped || !!result.stopped || status === 'stopped';
        const lines = [];
        const pad = (value, width, left = false) => {
            const clean = String(value || '');
            if (clean.length >= width)
                return clean.slice(0, width);
            return left ? `${' '.repeat(width - clean.length)}${clean}` : `${clean}${' '.repeat(width - clean.length)}`;
        };
        if (resultMode !== 'e2e') {
            let transparentLine = 'Checking for transparent DNS proxy...';
            if (transparent === true)
                transparentLine += ' DETECTED';
            else if (transparent === false)
                transparentLine += ' not detected';
            else
                transparentLine += ' ...';
            lines.push(transparentLine);
            lines.push('');
            if (total > 0) {
                lines.push(`Scanning... ${scanned}/${total}  (working: ${working})`);
            }
            else {
                lines.push('Scanning...');
            }
        }
        else {
            lines.push(`Running E2E... ${e2eTested}/${e2eTotal || total}  (passed: ${e2ePassed})`);
        }
        if (e2eTotal > 0 && resultMode !== 'e2e') {
            lines.push(`E2E... ${e2eTested}/${e2eTotal}  (passed: ${e2ePassed})`);
        }
        if (status === 'paused' || controlState === 'paused') {
            lines.push(this.t('settings.resolver_scan_paused', 'Paused by user.'));
        }
        else if (stopped) {
            lines.push(this.t('settings.resolver_scan_stopped', 'Stopped by user.'));
        }
        lines.push('');
        lines.push('── Results ──────────────────────────────────────');
        lines.push('');
        lines.push(`Status: ${status || '-'}${controlState ? ` (${controlState})` : ''}`);
        if (resultMode === 'e2e') {
            lines.push(`Total: ${e2eTotal || total} | Tested: ${e2eTested} | Passed: ${e2ePassed}`);
        }
        else {
            lines.push(`Total: ${scanned} | Working: ${working} | Timeout: ${timeout} | Error: ${errorCount}`);
        }
        lines.push(`Elapsed: ${elapsedSeconds}s`);
        if (selected)
            lines.push(`Selected resolver: ${selected}`);
        if (autoApplied)
            lines.push('Auto apply: enabled');
        if (compatible.length && resultMode !== 'e2e') {
            lines.push('');
            lines.push(`Compatible resolvers (${compatible.length}):`);
            lines.push('');
            lines.push(`${pad('RESOLVER', 22)} ${pad('SCORE', 5, true)} ${pad('MS', 5, true)}  DETAILS`);
            lines.push(`${'-'.repeat(22)} ${'-'.repeat(5)} ${'-'.repeat(5)}  ${'-'.repeat(30)}`);
            for (const item of compatible.slice(0, 22)) {
                const resolver = String(item.resolver || '-');
                const score = `${Number(item.score || 0)}/6`;
                const latency = `${Number(item.latency_ms || 0)}ms`;
                const details = String(item.details || '');
                lines.push(`${pad(resolver, 22)} ${pad(score, 5, true)} ${pad(latency, 5, true)}  ${details}`);
            }
            if (compatible.length > 22) {
                lines.push(`... +${compatible.length - 22} more`);
            }
        }
        if (resultMode === 'e2e' && e2eRows.length) {
            lines.push('');
            lines.push(`E2E probes (${e2eRows.length}):`);
            lines.push('');
            lines.push(`${pad('RESOLVER', 22)} ${pad('OK', 5, true)} ${pad('MS', 5, true)}  DETAILS`);
            lines.push(`${'-'.repeat(22)} ${'-'.repeat(5)} ${'-'.repeat(5)}  ${'-'.repeat(30)}`);
            for (const item of e2eRows.slice(0, 22)) {
                const resolver = String(item.resolver || '-');
                const ok = item.ok ? 'yes' : 'no';
                const elapsed = `${Number(item.elapsed_ms || 0)}ms`;
                const detail = item.ok ? 'bridge response received' : String(item.error || '');
                lines.push(`${pad(resolver, 22)} ${pad(ok, 5, true)} ${pad(elapsed, 5, true)}  ${detail}`);
            }
            if (e2eRows.length > 22) {
                lines.push(`... +${e2eRows.length - 22} more`);
            }
        }
        if (status === 'error') {
            lines.push(`Error: ${String(job.error || 'scan_failed')}`);
        }
        this.appendResolverConsole(lines.join('\n'));
        this.renderResolverTop(this.resolverScanLastScanResult);
        this.syncResolverScanActionState();
    }
    async pollResolverScan(silent = false) {
        if (!this.resolverScanJobId) {
            this.setResolverScanBusy(false);
            return;
        }
        const payload = await this.fetchJson(`/dns/resolvers/scan/status?id=${encodeURIComponent(this.resolverScanJobId)}`);
        if (!payload.ok) {
            this.setResolverScanBusy(false);
            if (!silent) {
                this.showFlash(String(payload.error || 'Resolver scan status failed'), 'error');
            }
            return;
        }
        const job = payload.job || {};
        this.renderResolverScanJob(job);
        const status = String(job.status || '');
        if (status === 'done' || status === 'stopped') {
            this.setResolverScanBusy(false);
            const result = job.result || {};
            const autoApplied = !!result.auto_applied;
            const selected = String(result.selected_resolver || '');
            const applied = Array.isArray(result.applied_resolvers) ? result.applied_resolvers.map((x) => String(x || '')) : [];
            if (autoApplied && applied.length) {
                this.applyAutoResolversToUi(applied);
            }
            if (!silent) {
                if (status === 'stopped') {
                    this.showFlash(this.t('settings.resolver_scan_stopped', 'Stopped by user.'), 'success');
                }
                else {
                    this.showFlash(autoApplied && selected
                        ? `Resolver scanner done. Selected: ${selected}`
                        : this.t('settings.resolver_scan_done', 'Resolver scanner completed.'), 'success');
                }
            }
            return;
        }
        if (status === 'error') {
            this.setResolverScanBusy(false);
            if (!silent) {
                this.showFlash(String(job.error || 'Resolver scanner failed'), 'error');
            }
            return;
        }
        this.setResolverScanBusy(true);
        this.clearResolverScanPollTimer();
        this.resolverScanPollTimer = window.setTimeout(() => {
            void this.pollResolverScan(silent);
        }, 900);
    }
    async startResolverScan(mode) {
        if (this.resolverScanBusy)
            return;
        this.syncResolverScanOptionState();
        this.serializeToHidden();
        const rawScanInput = (this.resolverScanInput?.value || '').trim();
        const customResolvers = this.parseResolverScanInput(rawScanInput);
        if (rawScanInput && !customResolvers.length) {
            this.showFlash(this.t('settings.resolver_scan_input_invalid', 'Resolver input is invalid.'), 'error');
            return;
        }
        const fallbackResolvers = this.parseResolverScanInput(this.dnsResolversHidden?.value || '');
        const scanResolvers = customResolvers.length ? customResolvers : fallbackResolvers;
        this.clearResolverScanPollTimer();
        this.setResolverScanBusy(true);
        this.resolverScanLastStatus = 'running';
        this.resolverScanLastControlState = 'running';
        this.syncResolverScanActionState();
        this.appendResolverConsole('Checking for transparent DNS proxy...\nScanning...\n');
        const firstDomain = this.firstDomainFromUi();
        const scanOnly = !!this.resolverScanOnlyCheckbox?.checked;
        const inlineE2E = !!this.resolverScanInlineE2eCheckbox?.checked;
        const autoApply = !scanOnly && !!this.resolverScanAutoApplyCheckbox?.checked;
        const payload = await this.fetchJson('/dns/resolvers/scan/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                scan_mode: mode,
                scan_only: scanOnly ? 1 : 0,
                e2e_enabled: inlineE2E ? 1 : 0,
                auto_apply_best: autoApply ? 1 : 0,
                dns_resolvers: scanResolvers.join('\n'),
                dns_domains: this.dnsDomainsHidden?.value || '',
                domain: firstDomain.domain || '',
                password: firstDomain.password || '',
                dns_timeout_seconds: this.dnsTimeoutInput?.value || '',
                dns_query_size: this.querySizeInput?.value || '',
            }),
        });
        if (!payload.ok) {
            this.setResolverScanBusy(false);
            this.showFlash(String(payload.error || 'Resolver scan failed to start'), 'error');
            return;
        }
        const job = payload.job || {};
        this.resolverScanJobId = String(job.id || '');
        this.renderResolverScanJob(job);
        if (!this.resolverScanJobId) {
            this.setResolverScanBusy(false);
            this.showFlash('Resolver scan job id missing.', 'error');
            return;
        }
        await this.pollResolverScan(false);
    }
    async controlResolverScan(action) {
        if (!this.resolverScanJobId && !this.resolverScanBusy)
            return;
        const payload = await this.fetchJson('/dns/resolvers/scan/control', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                id: this.resolverScanJobId || '',
                action,
            }),
        });
        if (!payload.ok) {
            this.showFlash(String(payload.error || 'Resolver scan control failed'), 'error');
            return;
        }
        const job = payload.job || {};
        this.resolverScanJobId = String(job.id || this.resolverScanJobId || '');
        this.renderResolverScanJob(job);
        if (action === 'stop') {
            this.setResolverScanBusy(true);
            await this.pollResolverScan(false);
            return;
        }
        this.clearResolverScanPollTimer();
        await this.pollResolverScan(true);
    }
    async startResolverE2E() {
        if (this.resolverScanBusy)
            return;
        const candidates = this.scanCompatibleResolvers(this.resolverScanLastScanResult);
        if (!candidates.length) {
            this.showFlash(this.t('settings.resolver_scan_e2e_no_candidates', 'No compatible resolver available. Run scan first.'), 'error');
            return;
        }
        this.serializeToHidden();
        this.clearResolverScanPollTimer();
        this.setResolverScanBusy(true);
        this.resolverScanLastStatus = 'running';
        this.resolverScanLastControlState = 'running';
        this.syncResolverScanActionState();
        this.appendResolverConsole('Running E2E checks...\n');
        const firstDomain = this.firstDomainFromUi();
        const payload = await this.fetchJson('/dns/resolvers/e2e/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                resolvers: candidates,
                dns_domains: this.dnsDomainsHidden?.value || '',
                domain: firstDomain.domain || '',
                password: firstDomain.password || '',
            }),
        });
        if (!payload.ok) {
            this.setResolverScanBusy(false);
            this.showFlash(String(payload.error || 'Resolver E2E failed to start'), 'error');
            return;
        }
        const job = payload.job || {};
        this.resolverScanJobId = String(job.id || '');
        this.renderResolverScanJob(job);
        if (!this.resolverScanJobId) {
            this.setResolverScanBusy(false);
            this.showFlash('Resolver E2E job id missing.', 'error');
            return;
        }
        await this.pollResolverScan(false);
    }
    saveResolverScanResultAsTxt() {
        if (!this.resolverScanLastResult) {
            this.showFlash(this.t('settings.resolver_scan_no_result', 'No scan result available yet.'), 'error');
            return;
        }
        const now = new Date();
        const stamp = [
            now.getFullYear(),
            String(now.getMonth() + 1).padStart(2, '0'),
            String(now.getDate()).padStart(2, '0'),
            '-',
            String(now.getHours()).padStart(2, '0'),
            String(now.getMinutes()).padStart(2, '0'),
            String(now.getSeconds()).padStart(2, '0'),
        ].join('');
        const summary = String(this.resolverScanConsole?.textContent || '').trim();
        const payload = `${summary}\n\n----- JSON -----\n${JSON.stringify(this.resolverScanLastResult, null, 2)}\n`;
        const blob = new Blob([payload], { type: 'text/plain;charset=utf-8' });
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = href;
        a.download = `kabootar-resolver-scan-${stamp}.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(href);
        this.showFlash(this.t('settings.resolver_scan_saved_file', 'Scan result exported to txt.'), 'success');
    }
    async restoreResolverScanJobFromBackend() {
        const payload = await this.fetchJson('/dns/resolvers/scan/status');
        if (!payload.ok)
            return;
        const job = payload.job || {};
        const jobId = String(job.id || '');
        if (!jobId)
            return;
        this.resolverScanJobId = jobId;
        this.renderResolverScanJob(job);
        const status = String(job.status || '');
        if (['queued', 'running', 'paused'].includes(status)) {
            this.setResolverScanBusy(true);
            await this.pollResolverScan(true);
        }
        else {
            this.setResolverScanBusy(false);
        }
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
    compactInlineText(text, maxLength = 88) {
        const normalized = String(text || '').replace(/\s+/g, ' ').trim();
        if (normalized.length <= maxLength)
            return normalized;
        return `${normalized.slice(0, Math.max(0, maxLength - 1)).trimEnd()}…`;
    }
    setDomainBadge(row, text, state = 'idle') {
        const badge = row.querySelector('.domain-health-badge');
        if (!badge)
            return;
        const fullText = String(text || '').trim();
        badge.textContent = this.compactInlineText(fullText);
        badge.title = fullText;
        badge.setAttribute('aria-label', fullText);
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
    async submitSettingsForm() {
        if (!this.form)
            return;
        this.serializeToHidden();
        this.clearSaveButtonResetTimer();
        this.renderSaveButton('loading');
        const action = this.form.getAttribute('action') || window.location.pathname || '/settings';
        const payload = new FormData(this.form);
        try {
            const response = await fetch(action, {
                method: 'POST',
                body: payload,
                cache: 'no-store',
                headers: {
                    Accept: 'application/json',
                    'X-Kabootar-Request': 'fetch',
                },
            });
            const data = (await response.json().catch(() => ({})));
            const ok = response.ok && data.ok !== false;
            const message = String(data.message || this.t(ok ? 'settings.saved_settings' : 'settings.save_failed', ok ? 'Saved' : 'Save failed'));
            this.showFlash(message, ok ? 'success' : 'error');
            this.renderSaveButton(ok ? 'success' : 'error');
            this.saveButtonResetTimer = window.setTimeout(() => {
                this.renderSaveButton('idle');
            }, ok ? 2200 : 2600);
        }
        catch (err) {
            const message = String(err || this.t('settings.save_failed', 'Save failed'));
            this.showFlash(message, 'error');
            this.renderSaveButton('error');
            this.saveButtonResetTimer = window.setTimeout(() => {
                this.renderSaveButton('idle');
            }, 2600);
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
    clampNumberField(input, min, max) {
        if (!input)
            return;
        const raw = (input.value || '').trim();
        if (!raw)
            return;
        const n = Number(raw);
        if (!Number.isFinite(n) || n <= 0)
            return;
        input.value = String(Math.max(min, Math.min(max, Math.floor(n))));
    }
    async mount() {
        await this.initI18n();
        if (!this.form)
            return;
        this.renderSaveButton('idle');
        this.hydrateFromHidden();
        const initialScanResolvers = this.configuredResolversFromUi();
        if (initialScanResolvers.length) {
            this.setResolverScanInputValues(initialScanResolvers);
        }
        else {
            this.updateResolverScanInputCount();
        }
        this.setResolverScanBusy(false);
        this.syncResolverScanOptionState();
        this.syncResolverScanActionState();
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
        this.resolverQuickScanBtn?.addEventListener('click', () => {
            void this.startResolverScan('quick');
        });
        this.resolverDeepScanBtn?.addEventListener('click', () => {
            void this.startResolverScan('deep');
        });
        this.resolverPauseBtn?.addEventListener('click', () => {
            void this.controlResolverScan('pause');
        });
        this.resolverResumeBtn?.addEventListener('click', () => {
            void this.controlResolverScan('resume');
        });
        this.resolverStopBtn?.addEventListener('click', () => {
            void this.controlResolverScan('stop');
        });
        this.resolverRunE2eBtn?.addEventListener('click', () => {
            void this.startResolverE2E();
        });
        this.resolverSaveBtn?.addEventListener('click', () => {
            this.saveResolverScanResultAsTxt();
        });
        this.resolverSortSelect?.addEventListener('change', () => {
            if (this.resolverScanLastJob)
                this.renderResolverScanJob(this.resolverScanLastJob);
        });
        this.resolverScanOnlyCheckbox?.addEventListener('change', () => this.syncResolverScanOptionState());
        this.resolverScanAutoApplyCheckbox?.addEventListener('change', () => this.syncResolverScanOptionState());
        this.resolverScanUseCurrentBtn?.addEventListener('click', () => {
            const values = this.configuredResolversFromUi();
            this.setResolverScanInputValues(values);
        });
        this.resolverScanClearBtn?.addEventListener('click', () => {
            this.setResolverScanInputValues([]);
        });
        this.resolverScanInput?.addEventListener('input', () => this.updateResolverScanInputCount());
        this.resolverScanImportBtn?.addEventListener('click', () => {
            this.resolverScanFileInput?.click();
        });
        this.resolverScanFileInput?.addEventListener('change', async () => {
            const file = this.resolverScanFileInput?.files?.[0];
            if (!file)
                return;
            try {
                await this.importResolverScanFile(file);
            }
            catch {
                this.showFlash(this.t('settings.resolver_scan_import_failed', 'Resolver file import failed.'), 'error');
            }
            finally {
                if (this.resolverScanFileInput)
                    this.resolverScanFileInput.value = '';
            }
        });
        this.dnsUseSystemCheckbox?.addEventListener('change', () => this.syncResolverUiState());
        this.domainHealthClose?.addEventListener('click', () => this.closeDomainHealthDialog());
        this.domainHealthModal?.addEventListener('click', (ev) => {
            if (ev.target === this.domainHealthModal)
                this.closeDomainHealthDialog();
        });
        this.querySizeInput?.addEventListener('change', () => this.clampNumberField(this.querySizeInput, 16, 220));
        this.querySizeInput?.addEventListener('blur', () => this.clampNumberField(this.querySizeInput, 16, 220));
        this.dnsTimeoutInput?.addEventListener('change', () => this.clampNumberField(this.dnsTimeoutInput, 1, 30));
        this.dnsTimeoutInput?.addEventListener('blur', () => this.clampNumberField(this.dnsTimeoutInput, 1, 30));
        this.syncInput?.addEventListener('change', () => this.clampNumberField(this.syncInput, 1, 59));
        this.syncInput?.addEventListener('blur', () => this.clampNumberField(this.syncInput, 1, 59));
        this.initialChannelHistoryInput?.addEventListener('change', () => this.clampNumberField(this.initialChannelHistoryInput, 1, 200));
        this.initialChannelHistoryInput?.addEventListener('blur', () => this.clampNumberField(this.initialChannelHistoryInput, 1, 200));
        this.sourceMode?.addEventListener('change', () => this.toggleSections());
        this.toggleSections();
        await this.hydrateDomainHealthBadges();
        await this.restoreResolverScanJobFromBackend();
        this.form.addEventListener('submit', (event) => {
            event.preventDefault();
            void this.submitSettingsForm();
        });
    }
}
window.addEventListener('DOMContentLoaded', () => {
    void new SettingsView().mount();
});
export {};
