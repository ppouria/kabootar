type SyncJobState = {
  id: string;
  status: string;
  ok: boolean | null;
  mode: string;
  phase: string;
  message: string;
  progress_percent: number;
  domains_total: number;
  domains_done: number;
  channels_total: number;
  channels_done: number;
  messages_total: number;
  messages_done: number;
  saved: number;
  elapsed_seconds: number;
  eta_seconds: number | null;
  current_domain: string;
  current_channel: string;
  error: string;
  result?: Record<string, unknown> | null;
  refresh_result?: Record<string, unknown> | null;
};

type AutoRefreshState = {
  at: number;
  selected: string;
  messageScrollTop: number;
  stickToBottom: boolean;
};

class MirrorApp {
  private selected: string;
  private readonly AUTO_REFRESH_MS = 30000;
  private readonly STORAGE_KEY = 'kabootar_read_v1';
  private readonly LANG_KEY = 'kabootar_lang';
  private readonly INSTALL_PROMPT_KEY = 'kabootar_install_prompt_dismissed_v1';
  private readonly AUTO_REFRESH_STATE_KEY = 'kabootar_auto_refresh_state_v1';
  private readonly sourceMode: 'dns' | 'direct';
  private readonly dnsDomainsCount: number;
  private autoRefreshTimer: number | null = null;
  private refreshSidebarSearch: (() => void) | null = null;
  private lang: 'fa' | 'en' = 'fa';
  private i18n: Record<string, string> = {};

  constructor() {
    const chat = document.getElementById('chat') as HTMLElement | null;
    this.selected = chat?.dataset.selected || '';
    this.sourceMode = (chat?.dataset.sourceMode || 'dns').toLowerCase() === 'direct' ? 'direct' : 'dns';
    this.dnsDomainsCount = Math.max(0, Number(chat?.dataset.dnsDomainsCount || '0') || 0);
  }

  private t(key: string, fallback = ''): string {
    return this.i18n[key] || fallback || key;
  }

  private withVars(template: string, vars: Record<string, string> = {}): string {
    return template.replace(/\{(\w+)\}/g, (_, k) => vars[k] || '');
  }

  private escapeRegExp(value: string): string {
    return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  private escapeHtml(value: string): string {
    return value
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  private channelAvatarText(value: string): string {
    const clean = (value || '')
      .replace(/^https?:\/\/t\.me\/s\//i, '')
      .replace(/^https?:\/\/t\.me\//i, '')
      .replace(/^@+/, '')
      .trim();
    const glyphs = Array.from(clean.replace(/\s+/g, ''));
    return (glyphs.slice(0, 2).join('') || '?').toUpperCase();
  }

  private requiresDomainBeforeChannel(): boolean {
    return this.sourceMode === 'dns' && this.dnsDomainsCount < 1;
  }

  private async loadLang(lang: 'fa' | 'en'): Promise<Record<string, string>> {
    const resp = await fetch(`/static/i18n/${lang}.json`, { cache: 'no-cache' });
    if (!resp.ok) throw new Error(`lang_${lang}_not_found`);
    return (await resp.json()) as Record<string, string>;
  }

  private applyI18n(root: ParentNode = document): void {
    root.querySelectorAll<HTMLElement>('[data-i18n]').forEach((el) => {
      const key = el.dataset.i18n || '';
      if (!key) return;
      el.textContent = this.t(key, el.textContent || '');
    });

    root.querySelectorAll<HTMLElement>('[data-i18n-title]').forEach((el) => {
      const key = el.dataset.i18nTitle || '';
      if (!key) return;
      el.title = this.t(key, el.title || '');
      if (el.hasAttribute('aria-label')) {
        el.setAttribute('aria-label', this.t(key, el.getAttribute('aria-label') || ''));
      }
    });

    root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('[data-i18n-placeholder]').forEach((el) => {
      const key = el.dataset.i18nPlaceholder || '';
      if (!key) return;
      const domain = (el.dataset.domain || '').trim();
      const text = this.withVars(this.t(key, el.placeholder || ''), { domain });
      el.placeholder = text;
    });
  }

  private updateLangToggleLabel(): void {
    const btn = document.getElementById('langToggle');
    if (!btn) return;
    btn.textContent = this.lang === 'fa' ? 'EN' : 'FA';
  }

  private applyUnsupportedMediaLabels(root: ParentNode = document): void {
    root.querySelectorAll<HTMLElement>('.unsupported-media-copy[data-media-kind]').forEach((el) => {
      const kind = (el.dataset.mediaKind || 'media').trim().toLowerCase() || 'media';
      const key = `index.unsupported_media_${kind}`;
      el.textContent = this.t(key, this.t('index.unsupported_media_media', 'This message contains unsupported media and Kabootar does not support it yet.'));
    });
  }

  private async initI18n(): Promise<void> {
    const saved = (localStorage.getItem(this.LANG_KEY) || '').toLowerCase();
    this.lang = saved === 'en' ? 'en' : 'fa';
    try {
      this.i18n = await this.loadLang(this.lang);
    } catch {
      this.lang = 'en';
      this.i18n = await this.loadLang('en');
    }

    document.documentElement.lang = this.lang;
    document.documentElement.dir = this.lang === 'fa' ? 'rtl' : 'ltr';
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

  private loadReadMap(): Record<string, number> {
    try {
      return JSON.parse(localStorage.getItem(this.STORAGE_KEY) || '{}');
    } catch {
      return {};
    }
  }

  private saveReadMap(map: Record<string, number>): void {
    localStorage.setItem(this.STORAGE_KEY, JSON.stringify(map));
  }

  private formatTime(iso: string): string {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }).toLowerCase();
  }

  private formatDayLabel(date: Date): string {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const target = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    const diffDays = Math.round((today.getTime() - target.getTime()) / 86400000);

    if (diffDays === 0) return this.t('index.today', 'Today');
    if (diffDays === 1) return this.t('index.yesterday', 'Yesterday');

    const locale = this.lang === 'fa' ? 'fa-IR' : 'en-US';
    const sameYear = now.getFullYear() === date.getFullYear();
    return date.toLocaleDateString(
      locale,
      sameYear ? { month: 'long', day: 'numeric' } : { year: 'numeric', month: 'long', day: 'numeric' },
    );
  }

  private applyTimes(): void {
    document.querySelectorAll<HTMLElement>('.time[data-iso]').forEach((el) => {
      el.textContent = this.formatTime(el.dataset.iso || '');
    });
  }

  private addDateDividers(): void {
    const wrap = document.getElementById('messages') as HTMLElement | null;
    if (!wrap) return;

    wrap.querySelectorAll('.date-divider').forEach((el) => el.remove());

    let lastKey = '';
    [...wrap.querySelectorAll<HTMLElement>('.msg[data-message-id]')].forEach((msg) => {
      const iso = msg.querySelector<HTMLElement>('.time[data-iso]')?.dataset.iso || '';
      if (!iso) return;
      const date = new Date(iso);
      if (Number.isNaN(date.getTime())) return;

      const key = `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
      if (key === lastKey) return;
      lastKey = key;

      const divider = document.createElement('div');
      divider.className = 'date-divider';
      divider.innerHTML = `<span class="date-divider-chip">${this.escapeHtml(this.formatDayLabel(date))}</span>`;
      msg.before(divider);
    });
  }

  private applyUnreadBadges(readMap: Record<string, number>): void {
    document.querySelectorAll<HTMLElement>('.channel').forEach((ch) => {
      const key = ch.dataset.channelKey || '';
      const latest = Number(ch.dataset.latestId || 0);
      const read = Number(readMap[key] || 0);
      const unread = Math.max(0, latest - read);
      const badge = ch.querySelector<HTMLElement>('.unread-badge');
      if (!badge) return;
      if (unread > 0) {
        badge.hidden = false;
        badge.textContent = unread > 99 ? '99+' : String(unread);
      } else {
        badge.hidden = true;
      }
    });
  }

  private addUnreadDivider(readMap: Record<string, number>): HTMLElement | null {
    if (!this.selected) return null;
    const read = Number(readMap[this.selected] || 0);
    if (!read) return null;

    const messages = [...document.querySelectorAll<HTMLElement>('.msg[data-message-id]')];
    const firstUnread = messages.find((m) => Number(m.dataset.messageId || 0) > read);
    if (!firstUnread) return null;

    const divider = document.createElement('div');
    divider.className = 'unread-divider';
    divider.textContent = this.t('index.unread_divider', 'Unread messages');
    firstUnread.before(divider);
    return divider;
  }

  private markCurrentAsRead(readMap: Record<string, number>): void {
    if (!this.selected) return;
    const ids = [...document.querySelectorAll<HTMLElement>('.msg[data-message-id]')].map((m) => Number(m.dataset.messageId || 0));
    if (!ids.length) return;
    readMap[this.selected] = Math.max(...ids);
    this.saveReadMap(readMap);
    this.applyUnreadBadges(readMap);
  }

  private scrollToUnreadOrBottom(divider: HTMLElement | null): void {
    const wrap = document.getElementById('messages') as HTMLElement | null;
    if (!wrap) return;

    if ('scrollRestoration' in history) history.scrollRestoration = 'manual';

    let autoPin = true;
    const disableAutoPin = () => {
      autoPin = false;
    };

    wrap.addEventListener('touchstart', disableAutoPin, { once: true, passive: true });
    wrap.addEventListener('wheel', disableAutoPin, { once: true, passive: true });
    wrap.addEventListener('mousedown', disableAutoPin, { once: true, passive: true });

    const jumpBottom = () => {
      if (!autoPin) return;
      wrap.scrollTop = wrap.scrollHeight;
    };

    if (divider) {
      const firstMsg = wrap.querySelector<HTMLElement>('.msg[data-message-id]');
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

  private setupMobileMenu(): void {
    const btn = document.getElementById('menuBtn');
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebarOverlay');
    if (!btn || !sidebar || !overlay) return;

    const shouldAutoOpen = (): boolean => {
      const hasChannels = document.querySelectorAll('#sidebar .channel').length > 0;
      return !this.selected && hasChannels && window.matchMedia('(max-width: 900px)').matches;
    };

    const setOpen = (open: boolean) => {
      sidebar.classList.toggle('open', open);
      overlay.hidden = !open;
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
  }

  private setupSidebarSearch(): void {
    const toggle = document.getElementById('channelSearchToggle') as HTMLButtonElement | null;
    const row = document.getElementById('channelSearchRow') as HTMLElement | null;
    const input = document.getElementById('channelSearchInput') as HTMLInputElement | null;
    const empty = document.getElementById('channelSearchEmpty') as HTMLElement | null;
    if (!toggle || !row || !input) return;

    const getRows = (): HTMLElement[] => [...document.querySelectorAll<HTMLElement>('#sidebar .channel')];

    const getSearchText = (row: HTMLElement): string => {
      return ((row.dataset.searchText || '').trim() || '').toLocaleLowerCase();
    };

    const applyFilter = () => {
      const query = (input.value || '').trim().toLocaleLowerCase();
      const rows = getRows();
      let visible = 0;

      rows.forEach((row) => {
        const matched = !query || getSearchText(row).includes(query);
        row.hidden = !matched;
        if (matched) visible += 1;
      });

      if (empty) empty.hidden = !(query && rows.length > 0 && visible === 0);
    };

    const setOpen = (open: boolean) => {
      row.hidden = !open;
      toggle.classList.toggle('active', open);
      if (!open) {
        input.value = '';
        applyFilter();
      } else {
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

  private setupMessageSearch(): void {
    const toggle = document.getElementById('messageSearchToggle') as HTMLButtonElement | null;
    const bar = document.getElementById('messageSearchBar') as HTMLElement | null;
    const input = document.getElementById('messageSearchInput') as HTMLInputElement | null;
    const clearBtn = document.getElementById('messageSearchClear') as HTMLButtonElement | null;
    const closeBtn = document.getElementById('messageSearchClose') as HTMLButtonElement | null;
    const empty = document.getElementById('messageSearchEmpty') as HTMLElement | null;
    const unreadDivider = document.querySelector('.unread-divider') as HTMLElement | null;
    const messagesWrap = document.getElementById('messages') as HTMLElement | null;
    if (!toggle || !bar || !input || !messagesWrap) return;

    const rows = [...messagesWrap.querySelectorAll<HTMLElement>('.msg[data-message-id]')];
    const dateDividers = [...messagesWrap.querySelectorAll<HTMLElement>('.date-divider')];
    if (!rows.length) {
      toggle.disabled = true;
      return;
    }

    const restoreText = (el: HTMLElement | null) => {
      if (!el) return;
      const original = el.dataset.originalText;
      if (original == null) {
        el.dataset.originalText = el.textContent || '';
      } else {
        el.textContent = original;
      }
    };

    const highlightText = (el: HTMLElement | null, query: string) => {
      if (!el) return;
      const original = el.dataset.originalText ?? (el.textContent || '');
      el.dataset.originalText = original;
      if (!query) {
        el.textContent = original;
        return;
      }
      const regex = new RegExp(`(${this.escapeRegExp(query)})`, 'ig');
      el.innerHTML = this.escapeHtml(original).replace(regex, '<mark>$1</mark>');
    };

    const setOpen = (open: boolean) => {
      bar.hidden = !open;
      toggle.classList.toggle('active', open);
      if (!open) {
        input.value = '';
        applyFilter();
      } else {
        window.setTimeout(() => input.focus(), 20);
      }
    };

    const applyFilter = () => {
      const query = (input.value || '').trim();
      const queryLower = query.toLocaleLowerCase();
      let visible = 0;
      let firstVisible: HTMLElement | null = null;

      rows.forEach((row) => {
        const textEl = row.querySelector<HTMLElement>('.text');
        const replyTextEl = row.querySelector<HTMLElement>('.reply-text');
        const replyAuthorEl = row.querySelector<HTMLElement>('.reply-author');
        const forwardSourceEl = row.querySelector<HTMLElement>('.forward-source');

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
          if (!firstVisible) firstVisible = row;
        }
      });

      if (clearBtn) clearBtn.hidden = !query;
      if (empty) empty.hidden = !(query && visible === 0);
      if (unreadDivider) unreadDivider.hidden = !!query;
      dateDividers.forEach((divider) => {
        divider.hidden = !!query;
      });
      if (query && firstVisible) firstVisible.scrollIntoView({ block: 'nearest' });
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
  }

  private setupAddChannelBox(): void {
    const btnBottom = document.getElementById('addChannelBtnBottom');
    const btnMain = document.getElementById('addChannelBtnMain');
    const modal = document.getElementById('addModal') as HTMLElement | null;
    const form = document.getElementById('addModalForm') as HTMLFormElement | null;
    const cancel = document.getElementById('addModalCancel') as HTMLButtonElement | null;
    const submit = document.getElementById('addModalSubmit') as HTMLButtonElement | null;
    const errorHint = form?.querySelector('.add-form-error') as HTMLElement | null;
    const loadingHint = form?.querySelector('.add-form-loading') as HTMLElement | null;
    if (!modal || !form) return;

    const open = (ev: Event) => {
      ev.preventDefault();
      if (this.requiresDomainBeforeChannel()) return;
      if (loadingHint) loadingHint.hidden = true;
      if (errorHint) errorHint.hidden = true;
      modal.hidden = false;
      const t = form.querySelector<HTMLTextAreaElement>('textarea[name="channel"]');
      setTimeout(() => t?.focus(), 20);
    };

    const close = () => {
      modal.hidden = true;
    };

    btnBottom?.addEventListener('click', open);
    btnMain?.addEventListener('click', open);
    cancel?.addEventListener('click', (ev) => {
      ev.preventDefault();
      close();
    });

    modal.addEventListener('click', (ev) => {
      if (ev.target === modal) close();
    });

    const setSubmitting = (on: boolean) => {
      form.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLButtonElement>('input,textarea,button').forEach((el) => {
        if (el === cancel) {
          el.disabled = on;
          return;
        }
        el.disabled = on;
      });
      if (loadingHint) loadingHint.hidden = !on;
      if (submit) submit.textContent = this.t(on ? 'index.adding' : 'common.add', on ? 'Adding...' : 'Add');
    };

    const parseChannels = (raw: string): string[] => {
      return (raw || '').split(/[,;\n\r،]+/).map((x) => x.trim()).filter(Boolean);
    };

    const normalizeSourceUrl = (value: string): string => {
      const v = value.trim().replace(/^@+/, '');
      if (!v) return '';
      if (v.startsWith('http://') || v.startsWith('https://')) {
        const normalized = v.replace('http://', 'https://').replace(/\/+$/, '');
        if (normalized.includes('/s/')) return normalized;
        if (normalized.includes('t.me/')) {
          const username = normalized.split('/').filter(Boolean).pop() || '';
          return username ? `https://t.me/s/${username}` : '';
        }
        return normalized;
      }
      if (v.includes('t.me/s/')) {
        return `https://${v.replace(/^https?:\/\//, '')}`.replace(/\/+$/, '');
      }
      if (v.includes('t.me/')) {
        const username = v.split('/').filter(Boolean).pop() || '';
        return username ? `https://t.me/s/${username}` : '';
      }
      return `https://t.me/s/${v}`;
    };

    const addPendingChannels = (rawChannels: string[]) => {
      const sidebar = document.getElementById('sidebar');
      if (!sidebar) return;
      const addBox = sidebar.querySelector('.sidebar-add-box');
      const emptyState = sidebar.querySelector('.sidebar-empty');
      emptyState?.remove();
      rawChannels.forEach((raw) => {
        const sourceUrl = normalizeSourceUrl(raw);
        if (!sourceUrl) return;
        if (sidebar.querySelector(`.channel[data-channel-key="${sourceUrl}"]`)) return;
        const username = sourceUrl.split('/').pop() || raw.replace(/^@+/, '');
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
        } else {
          sidebar.appendChild(row);
        }
      });
      this.refreshSidebarSearch?.();
    };

    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const channelsRaw = (form.querySelector('textarea[name="channel"]') as HTMLTextAreaElement | null)?.value?.trim() || '';
      if (errorHint) errorHint.hidden = true;
      const channels = parseChannels(channelsRaw);
      if (!channels.length) {
        if (errorHint) {
          errorHint.textContent = this.t('index.channel_required', 'Channel is required.');
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

      if (channels.length) addPendingChannels(channels);
      setSubmitting(true);
      try {
        const response = await fetch(form.action, {
          method: 'POST',
          body: new FormData(form),
          redirect: 'follow',
        });
        if (response.redirected && response.url) {
          window.location.href = response.url;
          return;
        }
        window.location.reload();
      } catch {
        if (errorHint) {
          errorHint.textContent = this.t('index.request_failed', 'Request failed. Check your connection and try again.');
          errorHint.hidden = false;
        }
        setSubmitting(false);
      }
    });
  }

  private setupAddDomainBox(): void {
    const btnBottom = document.getElementById('addDomainBtnBottom');
    const btnMain = document.getElementById('addDomainBtnMain');
    const modal = document.getElementById('addDomainModal') as HTMLElement | null;
    const form = document.getElementById('addDomainModalForm') as HTMLFormElement | null;
    const cancel = document.getElementById('addDomainModalCancel') as HTMLButtonElement | null;
    const submit = document.getElementById('addDomainModalSubmit') as HTMLButtonElement | null;
    const errorHint = form?.querySelector('.add-form-error') as HTMLElement | null;
    const loadingHint = form?.querySelector('.add-form-loading') as HTMLElement | null;
    if (!modal || !form) return;

    const open = (ev: Event) => {
      ev.preventDefault();
      if (loadingHint) loadingHint.hidden = true;
      if (errorHint) errorHint.hidden = true;
      modal.hidden = false;
      const field = form.querySelector<HTMLInputElement>('input[name="domain"]');
      setTimeout(() => field?.focus(), 20);
    };

    const close = () => {
      modal.hidden = true;
    };

    btnBottom?.addEventListener('click', open);
    btnMain?.addEventListener('click', open);
    cancel?.addEventListener('click', (ev) => {
      ev.preventDefault();
      close();
    });

    modal.addEventListener('click', (ev) => {
      if (ev.target === modal) close();
    });

    const setSubmitting = (on: boolean) => {
      form.querySelectorAll<HTMLInputElement | HTMLButtonElement>('input,button').forEach((el) => {
        if (el === cancel) {
          el.disabled = on;
          return;
        }
        el.disabled = on;
      });
      if (loadingHint) loadingHint.hidden = !on;
      if (submit) submit.textContent = this.t(on ? 'index.adding' : 'common.add', on ? 'Adding...' : 'Add');
    };

    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const domain = (form.querySelector('input[name="domain"]') as HTMLInputElement | null)?.value?.trim() || '';
      if (errorHint) errorHint.hidden = true;
      if (!domain) {
        if (errorHint) {
          errorHint.textContent = this.t('index.domain_required', 'Domain is required.');
          errorHint.hidden = false;
        }
        return;
      }

      setSubmitting(true);
      try {
        const response = await fetch(form.action, {
          method: 'POST',
          body: new FormData(form),
          redirect: 'follow',
        });
        if (response.redirected && response.url) {
          window.location.href = response.url;
          return;
        }
        window.location.reload();
      } catch {
        if (errorHint) {
          errorHint.textContent = this.t('index.request_failed', 'Request failed. Check your connection and try again.');
          errorHint.hidden = false;
        }
        setSubmitting(false);
      }
    });
  }

  private formatDuration(seconds?: number | null): string {
    if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '-';
    const total = Math.max(0, Math.round(seconds));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    if (hours > 0) return `${hours}h ${minutes}m`;
    if (minutes > 0) return `${minutes}m ${secs}s`;
    return `${secs}s`;
  }

  private renderSyncChart(points: number[]): void {
    const line = document.querySelector('#syncChartLine') as SVGPolylineElement | null;
    if (!line) return;
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

  private buildSyncSummary(job: SyncJobState): string {
    if (job.status === 'error') {
      return this.withVars(this.t('index.sync_failed', 'Sync failed: {error}'), { error: job.error || this.t('index.sync_unknown_error', 'unknown error') });
    }
    if (job.saved > 0) {
      return this.withVars(this.t('index.sync_saved_summary', 'Updated {count} message(s).'), { count: String(job.saved) });
    }
    return this.t('index.sync_no_messages', 'No new messages were available.');
  }

  private setupSyncDialog(): void {
    const trigger = document.getElementById('syncNowBtn') as HTMLButtonElement | null;
    const modal = document.getElementById('syncModal') as HTMLElement | null;
    const closeBtn = document.getElementById('syncModalClose') as HTMLButtonElement | null;
    const phase = document.getElementById('syncModalPhase') as HTMLElement | null;
    const fill = document.getElementById('syncProgressFill') as HTMLElement | null;
    const percent = document.getElementById('syncProgressPercent') as HTMLElement | null;
    const eta = document.getElementById('syncEtaLabel') as HTMLElement | null;
    const domains = document.getElementById('syncDomainsStat') as HTMLElement | null;
    const channels = document.getElementById('syncChannelsStat') as HTMLElement | null;
    const messages = document.getElementById('syncMessagesStat') as HTMLElement | null;
    const saved = document.getElementById('syncSavedStat') as HTMLElement | null;
    const currentDomain = document.getElementById('syncCurrentDomain') as HTMLElement | null;
    const currentChannel = document.getElementById('syncCurrentChannel') as HTMLElement | null;
    const resultBox = document.getElementById('syncResultBox') as HTMLElement | null;
    if (!trigger || !modal) return;

    let pollTimer: number | null = null;
    let jobId = '';
    let chartHistory: number[] = [];
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

    const render = (job: SyncJobState) => {
      const pct = Math.max(0, Math.min(100, Number(job.progress_percent || 0)));
      chartHistory.push(pct);
      if (chartHistory.length > 48) chartHistory = chartHistory.slice(-48);
      this.renderSyncChart(chartHistory);

      if (phase) phase.textContent = job.message || this.t('index.sync_waiting', 'Waiting to start...');
      if (fill) fill.style.width = `${pct}%`;
      if (percent) percent.textContent = `${Math.round(pct)}%`;
      if (eta) eta.textContent = `${this.t('index.sync_eta', 'ETA')} ${this.formatDuration(job.eta_seconds)}`;
      if (domains) domains.textContent = `${job.domains_done} / ${job.domains_total}`;
      if (channels) channels.textContent = `${job.channels_done} / ${job.channels_total}`;
      if (messages) messages.textContent = `${job.messages_done} / ${job.messages_total}`;
      if (saved) saved.textContent = String(job.saved || 0);
      if (currentDomain) currentDomain.textContent = job.current_domain || '-';
      if (currentChannel) currentChannel.textContent = job.current_channel || '-';

      if (resultBox) {
        if (job.status === 'running' || job.status === 'queued') {
          resultBox.hidden = true;
          resultBox.classList.remove('error');
        } else {
          resultBox.hidden = false;
          resultBox.classList.toggle('error', job.status === 'error' || job.ok === false);
          resultBox.textContent = this.buildSyncSummary(job);
        }
      }

      if (job.status === 'done' && job.saved > 0 && !reloadScheduled) {
        reloadScheduled = true;
        window.setTimeout(() => window.location.reload(), 900);
      }
    };

    const poll = async () => {
      if (!jobId) return;
      try {
        const response = await fetch(`/sync-now/status?id=${encodeURIComponent(jobId)}`, { cache: 'no-cache' });
        const payload = (await response.json()) as { ok: boolean; job?: SyncJobState; error?: string };
        if (!response.ok || !payload.ok || !payload.job) {
          throw new Error(payload.error || 'sync_status_failed');
        }
        render(payload.job);
        if (payload.job.status === 'running' || payload.job.status === 'queued') {
          pollTimer = window.setTimeout(() => void poll(), 900);
        }
      } catch (err) {
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
      if (phase) phase.textContent = this.t('index.sync_waiting', 'Waiting to start...');
      try {
        const response = await fetch('/sync-now', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ channel: this.selected || '' }),
        });
        const payload = (await response.json()) as { ok: boolean; job?: SyncJobState; error?: string };
        if (!response.ok || !payload.ok || !payload.job) {
          throw new Error(payload.error || 'sync_start_failed');
        }
        jobId = payload.job.id;
        render(payload.job);
        pollTimer = window.setTimeout(() => void poll(), 200);
      } catch (err) {
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
      if (ev.target === modal) close();
    });
  }

  private setupImageViewer(): void {
    const modal = document.getElementById('imageViewer') as HTMLElement | null;
    const stage = document.getElementById('imageViewerStage') as HTMLElement | null;
    const shell = document.getElementById('imageViewerShell') as HTMLElement | null;
    const image = document.getElementById('imageViewerImage') as HTMLImageElement | null;
    const closeBtn = document.getElementById('imageViewerClose') as HTMLButtonElement | null;
    const thumbs = [...document.querySelectorAll<HTMLImageElement>('.msg-photo')];
    if (!modal || !stage || !shell || !image || !thumbs.length) return;

    let open = false;
    let pointerId: number | null = null;
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

    const applyDrag = (offsetY: number) => {
      const distance = Math.abs(offsetY);
      const scale = Math.max(0.88, 1 - Math.min(0.12, distance / 1200));
      const opacity = Math.max(0.38, 0.92 - Math.min(0.54, distance / 420));
      shell.style.transform = `translate3d(0, ${offsetY}px, 0) scale(${scale})`;
      modal.style.background = `rgba(3,8,12,${opacity})`;
      closeBtn?.style.setProperty('opacity', `${Math.max(0.3, 1 - Math.min(0.7, distance / 180))}`);
    };

    const close = () => {
      if (!open) return;
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
      if (!dragging) return;
      dragging = false;
      const shouldClose = Math.abs(deltaY) > Math.max(110, window.innerHeight * 0.14);
      if (shouldClose) {
        close();
        return;
      }
      deltaY = 0;
      resetVisual();
    };

    const onPointerDown = (event: PointerEvent) => {
      if (!open || !stage.contains(event.target as Node)) return;
      pointerId = event.pointerId;
      startY = event.clientY;
      deltaY = 0;
      moved = false;
      dragging = true;
      shell.style.transition = 'none';
      modal.style.transition = 'none';
      stage.setPointerCapture?.(event.pointerId);
    };

    const onPointerMove = (event: PointerEvent) => {
      if (!open || !dragging || pointerId !== event.pointerId) return;
      deltaY = event.clientY - startY;
      if (Math.abs(deltaY) > 4) moved = true;
      applyDrag(deltaY);
      if (moved) event.preventDefault();
    };

    const onPointerUp = (event: PointerEvent) => {
      if (pointerId !== event.pointerId) return;
      stage.releasePointerCapture?.(event.pointerId);
      finishDrag();
      pointerId = null;
      window.setTimeout(() => {
        moved = false;
      }, 0);
    };

    const show = (src: string, alt: string) => {
      if (!src) return;
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
      thumb.tabIndex = 0;
      thumb.addEventListener('click', () => show(thumb.currentSrc || thumb.src, thumb.alt || ''));
      thumb.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          show(thumb.currentSrc || thumb.src, thumb.alt || '');
        }
      });
    });

    closeBtn?.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      close();
    });

    modal.addEventListener('click', (event) => {
      if (!open || moved) return;
      if (event.target === modal || event.target === stage) close();
    });

    stage.addEventListener('pointerdown', onPointerDown);
    stage.addEventListener('pointermove', onPointerMove);
    stage.addEventListener('pointerup', onPointerUp);
    stage.addEventListener('pointercancel', onPointerUp);
    stage.addEventListener('pointerleave', (event) => {
      if (dragging && pointerId === event.pointerId) onPointerUp(event);
    });

    document.addEventListener('keydown', (event) => {
      if (open && event.key === 'Escape') close();
    });
  }

  private setupLiteMode(): void {
    const btn = document.getElementById('liteBtn');
    const key = 'kabootar_lite_v1';
    const isLite = localStorage.getItem(key) === '1';
    if (isLite) document.body.classList.add('lite-mode');
    if (!btn) return;
    btn.classList.toggle('active', isLite);
    btn.addEventListener('click', () => {
      const next = !document.body.classList.contains('lite-mode');
      document.body.classList.toggle('lite-mode', next);
      localStorage.setItem(key, next ? '1' : '0');
      btn.classList.toggle('active', next);
    });
  }

  private registerSW(): void {
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker.register('/sw.js').catch(() => undefined);
  }

  private setupInstallPrompt(): void {
    const prompt = document.getElementById('installPrompt') as HTMLElement | null;
    const title = document.getElementById('installPromptTitle') as HTMLElement | null;
    const text = document.getElementById('installPromptText') as HTMLElement | null;
    const action = document.getElementById('installPromptAction') as HTMLButtonElement | null;
    const dismiss = document.getElementById('installPromptDismiss') as HTMLButtonElement | null;
    if (!prompt || !title || !text || !action || !dismiss) return;

    const nav = navigator as Navigator & { standalone?: boolean };
    const ua = (nav.userAgent || '').toLowerCase();
    const isIOS = /iphone|ipad|ipod/.test(ua);
    const isAndroid = /android/.test(ua);
    const isSafari = /safari/.test(ua) && !/crios|fxios|edgios|opr\//.test(ua);
    const isStandalone = window.matchMedia('(display-mode: standalone)').matches || nav.standalone === true;
    const isMobileViewport = window.matchMedia('(max-width: 900px)').matches;

    let deferredPrompt: {
      prompt: () => Promise<void>;
      userChoice?: Promise<{ outcome?: string }>;
    } | null = null;

    const hiddenByUser = () => localStorage.getItem(this.INSTALL_PROMPT_KEY) === '1';

    const hidePrompt = (persist = false) => {
      prompt.hidden = true;
      if (persist) localStorage.setItem(this.INSTALL_PROMPT_KEY, '1');
    };

    const showPrompt = (mode: 'android' | 'ios') => {
      if (isStandalone || hiddenByUser() || !isMobileViewport) return;
      title.textContent = this.t('index.install_prompt_title', 'Add Kabootar to your home screen');
      if (mode === 'android') {
        text.textContent = this.t('index.install_prompt_android', 'Install this web app for faster access and a full-screen experience.');
        action.hidden = false;
        action.textContent = this.t('index.install_prompt_action', 'Add to Home');
      } else {
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
        if (deferredPrompt.userChoice) await deferredPrompt.userChoice;
        hidePrompt(true);
      } catch {
        hidePrompt(false);
      } finally {
        deferredPrompt = null;
      }
    });

    window.addEventListener('appinstalled', () => hidePrompt(true));

    window.addEventListener('beforeinstallprompt', (event) => {
      event.preventDefault();
      deferredPrompt = event as unknown as typeof deferredPrompt;
      if (isAndroid) showPrompt('android');
    });

    if (isIOS && isSafari && !isStandalone && !hiddenByUser()) {
      window.setTimeout(() => showPrompt('ios'), 1200);
    }
  }

  private loadAutoRefreshState(): AutoRefreshState | null {
    try {
      const raw = sessionStorage.getItem(this.AUTO_REFRESH_STATE_KEY);
      if (!raw) return null;
      sessionStorage.removeItem(this.AUTO_REFRESH_STATE_KEY);
      const parsed = JSON.parse(raw) as Partial<AutoRefreshState> | null;
      const at = Number(parsed?.at || 0);
      if (!at || Date.now() - at > this.AUTO_REFRESH_MS * 3) return null;
      return {
        at,
        selected: typeof parsed?.selected === 'string' ? parsed.selected : '',
        messageScrollTop: Math.max(0, Number(parsed?.messageScrollTop || 0) || 0),
        stickToBottom: parsed?.stickToBottom === true,
      };
    } catch {
      return null;
    }
  }

  private persistAutoRefreshState(): void {
    try {
      const wrap = document.getElementById('messages') as HTMLElement | null;
      const state: AutoRefreshState = {
        at: Date.now(),
        selected: this.selected,
        messageScrollTop: Math.max(0, Number(wrap?.scrollTop || 0) || 0),
        stickToBottom: !!wrap && (wrap.scrollHeight - wrap.scrollTop - wrap.clientHeight < 40),
      };
      sessionStorage.setItem(this.AUTO_REFRESH_STATE_KEY, JSON.stringify(state));
    } catch {
      // Ignore storage errors and still allow the refresh.
    }
  }

  private restoreAfterAutoRefresh(state: AutoRefreshState | null, fallbackDivider: HTMLElement | null): void {
    if (!state || state.selected !== this.selected) {
      this.scrollToUnreadOrBottom(fallbackDivider);
      return;
    }

    const wrap = document.getElementById('messages') as HTMLElement | null;
    if (!wrap) {
      this.scrollToUnreadOrBottom(fallbackDivider);
      return;
    }

    const apply = () => {
      if (state.stickToBottom) {
        wrap.scrollTop = wrap.scrollHeight;
        return;
      }
      const maxScroll = Math.max(0, wrap.scrollHeight - wrap.clientHeight);
      wrap.scrollTop = Math.min(state.messageScrollTop, maxScroll);
    };

    apply();
    requestAnimationFrame(apply);
    window.setTimeout(apply, 120);
  }

  private hasBlockingModalOpen(): boolean {
    return ['addModal', 'addDomainModal', 'syncModal', 'imageViewer'].some((id) => {
      const el = document.getElementById(id) as HTMLElement | null;
      return !!el && !el.hidden;
    });
  }

  private hasActiveInputFocus(): boolean {
    const active = document.activeElement as HTMLElement | null;
    if (!active) return false;
    if (active.isContentEditable) return true;
    return ['INPUT', 'TEXTAREA', 'SELECT'].includes(active.tagName);
  }

  private hasActiveSearchQuery(): boolean {
    const channelSearch = (document.getElementById('channelSearchInput') as HTMLInputElement | null)?.value?.trim() || '';
    const messageSearch = (document.getElementById('messageSearchInput') as HTMLInputElement | null)?.value?.trim() || '';
    return !!channelSearch || !!messageSearch;
  }

  private canAutoRefresh(): boolean {
    if (document.visibilityState !== 'visible') return false;
    if (this.hasBlockingModalOpen()) return false;
    if (this.hasActiveInputFocus()) return false;
    if (this.hasActiveSearchQuery()) return false;
    return true;
  }

  private scheduleAutoRefresh(delay = this.AUTO_REFRESH_MS): void {
    if (this.autoRefreshTimer != null) {
      window.clearTimeout(this.autoRefreshTimer);
    }
    this.autoRefreshTimer = window.setTimeout(() => {
      if (!this.canAutoRefresh()) {
        this.scheduleAutoRefresh();
        return;
      }
      this.persistAutoRefreshState();
      window.location.reload();
    }, delay);
  }

  private setupAutoRefresh(): void {
    this.scheduleAutoRefresh();

    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        this.scheduleAutoRefresh();
      }
    });

    window.addEventListener('focus', () => {
      this.scheduleAutoRefresh();
    });
  }

  public async mount(): Promise<void> {
    await this.initI18n();

    const autoRefreshState = this.loadAutoRefreshState();
    const readMap = this.loadReadMap();
    this.setupLiteMode();
    this.applyTimes();
    this.addDateDividers();
    this.applyUnreadBadges(readMap);
    const divider = this.addUnreadDivider(readMap);
    this.setupMobileMenu();
    this.setupSidebarSearch();
    this.setupMessageSearch();
    this.setupAddDomainBox();
    this.setupAddChannelBox();
    this.setupSyncDialog();
    this.setupImageViewer();
    this.setupInstallPrompt();
    this.registerSW();
    this.setupAutoRefresh();
    window.setTimeout(() => this.restoreAfterAutoRefresh(autoRefreshState, divider), 30);

    if (this.selected) {
      const maybeMarkRead = () => {
        const wrap = document.getElementById('messages') as HTMLElement | null;
        if (!wrap) return;
        const nearBottom = wrap.scrollHeight - wrap.scrollTop - wrap.clientHeight < 40;
        if (!nearBottom) return;

        const channelNode = [...document.querySelectorAll<HTMLElement>('.channel')].find((el) => el.dataset.channelKey === this.selected);
        const latest = Number(channelNode?.dataset.latestId || 0);
        if (latest > Number(readMap[this.selected] || 0)) {
          this.markCurrentAsRead(readMap);
        }
      };

      setTimeout(maybeMarkRead, 1500);
      const wrap = document.getElementById('messages');
      if (wrap) wrap.addEventListener('scroll', maybeMarkRead, { passive: true });
    }
  }
}

window.addEventListener('DOMContentLoaded', () => {
  void new MirrorApp().mount();
});
