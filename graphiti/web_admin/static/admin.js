/**
 * Personal Assistant Admin UI
 * JavaScript for handling tab switching, forms, and API interactions
 */

(function () {
  // DOM elements
  const tabButtons = document.querySelectorAll('.tab-button');
  const panels = document.querySelectorAll('[data-tab-panel]');
  const configForms = document.querySelectorAll('[data-config-form]');
  const directoryButtons = document.querySelectorAll('[data-directory-target]');
  const clearDirectoryButtons = document.querySelectorAll('[data-directory-clear]');
  const googleStatus = document.getElementById('google-auth-status');
  const googleButton = document.getElementById('google-signin');
  const slackForm = document.getElementById('slack-form');
  const slackWorkspace = document.getElementById('slack-workspace');
  const slackToken = document.getElementById('slack-token');
  const slackCookie = document.getElementById('slack-cookie');
  const slackStatus = document.getElementById('slack-status');
  const slackInventoryButton = document.getElementById('slack-inventory');
  const slackInventoryStatus = document.getElementById('slack-inventory-status');
  const slackChannels = document.getElementById('slack-channels');
  const backupStatus = document.getElementById('backup-status');
  const loaderStatus = document.getElementById('loader-status');
  const pollerStatus = document.getElementById('poller-status');
  const logsStatus = document.getElementById('logs-status');
  const logContainer = document.getElementById('logs');
  const logCategorySelect = document.getElementById('log-category');
  const logLimitInput = document.getElementById('log-limit');
  const logSinceInput = document.getElementById('log-since');
  const defaultStatus = document.querySelector('[data-default-status]');
  let currentConfig = {};

  // Utility functions
  const setStatus = (element, message, isError = false) => {
    if (!element) return;
    const text = message || '';
    element.textContent = text;
    if (!text) {
      element.classList.remove('error');
      return;
    }
    element.classList.toggle('error', Boolean(isError));
  };

  const getStatusTarget = (form) => form?.querySelector('.status-line') || defaultStatus;

  // Tab management with URL routing
  const activateTab = (name) => {
    tabButtons.forEach((button) => {
      const active = button.dataset.tab === name;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    panels.forEach((panel) => {
      const shouldShow = panel.dataset.tabPanel === name;
      panel.style.display = shouldShow ? 'block' : 'none';
    });
    // Update URL without reloading
    const path = name === 'episodes' ? '/' : `/${name}`;
    window.history.pushState({ tab: name }, '', path);
  };

  tabButtons.forEach((button) => {
    button.addEventListener('click', () => activateTab(button.dataset.tab));
  });

  // Handle browser back/forward buttons
  window.addEventListener('popstate', (event) => {
    const tab = event.state?.tab || getTabFromPath();
    activateTab(tab);
  });

  // Get tab from current URL path
  const getTabFromPath = () => {
    const path = window.location.pathname;
    if (path === '/' || path === '') return 'episodes';
    const tabName = path.substring(1); // Remove leading slash
    // Validate it's a real tab
    const validTabs = ['connections', 'google', 'slack', 'redaction', 'episodes', 'backups', 'logs', 'operations'];
    return validTabs.includes(tabName) ? tabName : 'episodes';
  };

  // Data transformation utilities
  const parseList = (value) => {
    if (!value) return [];
    return value
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean);
  };

  const parseRedaction = (value) => {
    if (!value) return [];
    return value
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const [pattern, replacement = '[REDACTED]'] = line.split('=>').map((part) => part.trim());
        return { pattern, replacement };
      })
      .filter((rule) => rule.pattern);
  };

  const getFieldNodes = (name) =>
    Array.from(document.querySelectorAll(`[name="${name}"]`));

  const populateFields = (config) => {
    Object.entries(config || {}).forEach(([key, value]) => {
      const fields = getFieldNodes(key);
      if (!fields.length) return;
      fields.forEach((field) => {
        if (Array.isArray(value)) {
          if (key === 'redaction_rules') {
            field.value = value
              .map((rule) => `${rule.pattern} => ${rule.replacement}`)
              .join('\n');
          } else {
            field.value = value.join(', ');
          }
        } else if (value === null || value === undefined) {
          field.value = '';
        } else {
          field.value = value;
        }
      });
    });

    [
      ['gmail', 'gmail_backfill_days'],
      ['drive', 'drive_backfill_days'],
      ['calendar', 'calendar_backfill_days'],
      ['slack', 'slack_backfill_days'],
    ].forEach(([service, key]) => {
      if (!(key in (config || {}))) return;
      const input = document.querySelector(`[name="${service}_manual_days"]`);
      if (input) {
        const value = config[key];
        input.dataset.default = String(value ?? '');
        input.value = String(value ?? '');
      }
    });
  };

  const loadConfig = async () => {
    const response = await fetch('/api/config');
    if (!response.ok) {
      throw new Error('Unable to load configuration');
    }
    const data = await response.json();
    currentConfig = data;
    populateFields(data);
  };

  const transformValue = (key, raw) => {
    const text = typeof raw === 'string' ? raw : '';
    switch (key) {
      case 'poll_gmail_drive_calendar_seconds':
      case 'poll_slack_active_seconds':
      case 'poll_slack_idle_seconds':
      case 'gmail_fallback_days':
      case 'gmail_backfill_days':
      case 'drive_backfill_days':
      case 'calendar_backfill_days':
      case 'slack_backfill_days':
      case 'summarization_threshold':
      case 'summarization_max_chars':
      case 'summarization_sentence_count':
      case 'backup_retention_days':
      case 'log_retention_days':
        return Number(text || '0');
      case 'calendar_ids':
        return parseList(text);
      case 'redaction_rules':
        return parseRedaction(text);
      case 'slack_search_queries':
        return text.split(',').map(s => s.trim()).filter(Boolean);
      case 'neo4j_uri':
      case 'neo4j_user':
      case 'neo4j_password':
      case 'group_id':
      case 'google_client_id':
      case 'google_client_secret':
      case 'backup_directory':
      case 'logs_directory':
      case 'redaction_rules_path':
        return text.trim();
      default:
        return text;
    }
  };

  // Google OAuth status
  const loadGoogleStatus = async () => {
    if (!googleStatus) return;
    try {
      const response = await fetch('/api/auth/google/status');
      if (!response.ok) {
        throw new Error('Unable to load Google status');
      }
      const data = await response.json();
      if (!data.has_client || !data.has_secret) {
        setStatus(googleStatus, 'Add your Google OAuth client ID and secret, then click "Sign in with Google".', true);
        return;
      }
      if (data.has_refresh_token) {
        const scopes = Array.isArray(data.scopes) ? data.scopes.join(', ') : 'gmail, drive, calendar';
        const updated = data.updated_at ? `Last updated ${new Date(data.updated_at).toLocaleString()}.` : '';
        setStatus(googleStatus, `Authorised with scopes: ${scopes}. ${updated}`.trim());
      } else {
        setStatus(googleStatus, 'Client saved. Click "Sign in with Google" to authorise access.');
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || 'Unable to load Google status');
      setStatus(googleStatus, message, true);
    }
  };

  // Slack status
  const loadSlackStatus = async () => {
    if (!slackStatus || !slackForm) return;
    try {
      const response = await fetch('/api/auth/slack');
      if (!response.ok) {
        throw new Error('Unable to load Slack status');
      }
      const data = await response.json();
      slackWorkspace.value = data.workspace || '';
      slackToken.value = '';
      slackCookie.value = '';
      if (data.has_token && data.has_cookie) {
        const updated = data.updated_at ? `Saved ${new Date(data.updated_at).toLocaleString()}.` : '';
        const workspace = data.workspace ? `for ${data.workspace}` : '';
        setStatus(slackStatus, `Slack credentials ${workspace} stored. ${updated}`.trim());
      } else if (data.has_token || data.has_cookie) {
        setStatus(slackStatus, 'Both Slack token and cookie are required.', true);
      } else {
        setStatus(slackStatus, 'Add Slack token and cookie, then click "Save Slack Credentials".', true);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || 'Unable to load Slack status');
      setStatus(slackStatus, message, true);
    }
  };

  // Log categories
  const loadLogCategories = async () => {
    try {
      const response = await fetch('/api/logs/categories');
      if (!response.ok) {
        throw new Error('Unable to load log categories');
      }
      const data = await response.json();
      const categories = data.categories || ['system', 'episodes'];
      const current = logCategorySelect.value;
      logCategorySelect.innerHTML = '';
      categories.forEach((category) => {
        const option = document.createElement('option');
        option.value = category;
        option.textContent = category;
        if (category === current) option.selected = true;
        logCategorySelect.appendChild(option);
      });
    } catch (error) {
      logCategorySelect.innerHTML = '<option value="system">system</option>';
      throw error;
    }
  };

  // Logs
  const refreshLogs = async () => {
    setStatus(logsStatus, 'Loading logs...');
    try {
      const params = new URLSearchParams();
      const category = logCategorySelect.value || 'system';
      params.set('category', category);
      const limit = Number(logLimitInput.value || '200');
      params.set('limit', String(Math.max(1, Math.floor(limit))));
      const since = Number(logSinceInput.value || '0');
      if (Number.isFinite(since) && since > 0) {
        params.set('since_days', String(Math.floor(since)));
      }
      const response = await fetch(`/api/logs?${params.toString()}`);
      if (!response.ok) {
        throw new Error('Unable to fetch logs');
      }
      const data = await response.json();
      const entries = Array.isArray(data.records) ? data.records : [];
      logContainer.textContent = entries.length
        ? entries.map((record) => JSON.stringify(record)).join('\n')
        : 'No log entries available.';
      setStatus(logsStatus, `Loaded ${entries.length} log entries.`);
    } catch (error) {
      logContainer.textContent = '';
      const message = error instanceof Error ? error.message : String(error || 'Unable to fetch logs');
      setStatus(logsStatus, message, true);
    }
  };

  // Config form submission
  const handleConfigSubmit = (form) => async (event) => {
    event.preventDefault();
    const statusEl = getStatusTarget(form);
    const submitButton = form.querySelector('button[type="submit"]');
    const formData = new FormData(form);
    const updates = {};
    formData.forEach((value, key) => {
      updates[key] = transformValue(key, value);
    });

    try {
      if (submitButton) submitButton.disabled = true;
      setStatus(statusEl, 'Saving settings...');
      const payload = { ...currentConfig, ...updates };
      const response = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || 'Unable to save configuration');
      }
      const data = await response.json();
      currentConfig = data;
      populateFields(data);
      setStatus(statusEl, 'Settings saved successfully.');
      if (googleStatus) {
        await loadGoogleStatus().catch(() => {});
      }
      await loadLogCategories().catch(() => {});
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || 'Unable to save configuration');
      setStatus(statusEl, message, true);
    } finally {
      if (submitButton) submitButton.disabled = false;
    }
  };

  configForms.forEach((form) => {
    form.addEventListener('submit', handleConfigSubmit(form));
  });

  // Directory picker
  const openDirectoryPicker = async (field, form) => {
    const input = document.querySelector(`[data-directory-input="${field}"]`);
    if (!input) return;
    const statusEl = getStatusTarget(form);
    try {
      const response = await fetch('/api/dialog/directory', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: field === 'backup_directory' ? 'Select backup directory' : 'Select logs directory',
          initial: input.value || undefined,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || 'Directory picker unavailable.');
      }
      if (data.path) {
        input.value = data.path;
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || 'Directory picker unavailable.');
      setStatus(statusEl, message, true);
    }
  };

  directoryButtons.forEach((button) => {
    button.addEventListener('click', async () => {
      const field = button.dataset.directoryTarget;
      if (!field) return;
      await openDirectoryPicker(field, button.closest('form'));
    });
  });

  clearDirectoryButtons.forEach((button) => {
    button.addEventListener('click', () => {
      const field = button.dataset.directoryClear;
      if (!field) return;
      const input = document.querySelector(`[data-directory-input="${field}"]`);
      if (input) {
        input.value = '';
      }
    });
  });

  // Google OAuth
  const googleAuthorize = async () => {
    if (!googleButton) return;
    googleButton.disabled = true;
    try {
      const response = await fetch('/api/auth/google/start', { method: 'POST' });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || 'Unable to start Google sign-in');
      }
      const data = await response.json();
      if (!data.auth_url) {
        throw new Error('Google did not return an authorisation URL');
      }
      setStatus(googleStatus, 'Complete the Google consent screen in the new window.');
      window.open(data.auth_url, 'google-oauth', 'width=520,height=720');
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || 'Unable to start Google sign-in');
      setStatus(googleStatus, message, true);
    } finally {
      googleButton.disabled = false;
    }
  };

  if (googleButton) {
    googleButton.addEventListener('click', googleAuthorize);
  }

  window.addEventListener('message', (event) => {
    const detail = event.data || {};
    if (detail.type !== 'google-oauth') return;
    const isError = detail.status !== 'success';
    setStatus(googleStatus, detail.message || '', isError);
    loadGoogleStatus().catch(() => {});
  });

  // Slack form
  if (slackForm) {
    slackForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const workspace = (slackWorkspace.value || '').trim();
      const token = (slackToken.value || '').trim();
      const cookie = (slackCookie.value || '').trim();
      if (!token) {
        setStatus(slackStatus, 'Please provide a Slack token.', true);
        return;
      }
      if (!cookie) {
        setStatus(slackStatus, 'Please provide a Slack cookie.', true);
        return;
      }
      setStatus(slackStatus, 'Saving Slack credentials...');
      try {
        const response = await fetch('/api/auth/slack', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ workspace, slack_token: token, slack_cookie: cookie }),
        });
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          throw new Error(detail.detail || 'Unable to save Slack credentials');
        }
        slackToken.value = '';
        slackCookie.value = '';
        await loadSlackStatus();
        setStatus(slackStatus, 'Slack credentials saved successfully.');
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error || 'Unable to save Slack credentials');
        setStatus(slackStatus, message, true);
      }
    });
  }

  // Slack inventory
  if (slackInventoryButton) {
    slackInventoryButton.addEventListener('click', async () => {
      slackInventoryButton.disabled = true;
      setStatus(slackInventoryStatus, 'Fetching channels...');
      try {
        const response = await fetch('/api/slack/inventory', { method: 'POST' });
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          throw new Error(detail.detail || 'Unable to inventory Slack channels');
        }
        const data = await response.json();
        const channels = Array.isArray(data.channels) ? data.channels : [];
        if (!channels.length) {
          slackChannels.textContent = 'No channels returned by Slack.';
        } else {
          const list = document.createElement('ul');
          list.className = 'channel-list';
          channels.forEach((channel) => {
            const li = document.createElement('li');
            const name = channel.name || channel.id || 'unknown';
            li.textContent = `#${name}`;
            list.appendChild(li);
          });
          slackChannels.innerHTML = '';
          slackChannels.appendChild(list);
        }
        setStatus(slackInventoryStatus, `Fetched ${channels.length} channels.`);
        await refreshLogs().catch(() => {});
      } catch (error) {
        slackChannels.textContent = '';
        const message = error instanceof Error ? error.message : String(error || 'Unable to inventory Slack channels');
        setStatus(slackInventoryStatus, message, true);
      } finally {
        slackInventoryButton.disabled = false;
      }
    });
  }

  // Manual load
  const runManualLoad = async (service, button) => {
    const input = document.querySelector(`[name="${service}_manual_days"]`);
    const fallback = Number(input?.dataset.default || '30');
    const parsed = Number(input?.value || fallback);
    if (!Number.isFinite(parsed) || parsed < 1) {
      setStatus(loaderStatus, 'Please provide a valid number of days.', true);
      return;
    }
    const days = Math.floor(parsed);
    setStatus(loaderStatus, `Running ${service} backfill...`);
    if (button) button.disabled = true;
    try {
      const response = await fetch(`/api/manual-load/${service}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ days }),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || 'Backfill failed');
      }
      const data = await response.json();
      setStatus(loaderStatus, `${service} backfill completed: ${data.processed} episodes.`);
      await refreshLogs().catch(() => {});
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || 'Backfill failed');
      setStatus(loaderStatus, message, true);
    } finally {
      if (button) button.disabled = false;
    }
  };

  document.querySelectorAll('[data-service]').forEach((button) => {
    button.addEventListener('click', () => runManualLoad(button.dataset.service, button));
  });

  // Run poller
  const runPoller = async (source, button) => {
    if (!source) return;
    setStatus(pollerStatus, `Running ${source} poller...`);
    if (button) button.disabled = true;
    try {
      const response = await fetch(`/api/pollers/${source}/run`, { method: 'POST' });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || 'Poller run failed');
      }
      const data = await response.json();
      setStatus(pollerStatus, `${source} poller processed ${data.processed} items.`);
      await refreshLogs().catch(() => {});
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || 'Poller run failed');
      setStatus(pollerStatus, message, true);
    } finally {
      if (button) button.disabled = false;
    }
  };

  document.querySelectorAll('[data-poller]').forEach((button) => {
    button.addEventListener('click', () => runPoller(button.dataset.poller, button));
  });

  // Backup
  const runBackup = async () => {
    const button = document.getElementById('run-backup');
    if (!button) return;
    button.disabled = true;
    setStatus(backupStatus, 'Starting backup...');
    try {
      const response = await fetch('/api/backup/run', { method: 'POST' });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || 'Unable to trigger backup');
      }
      const data = await response.json();
      const message = data.status || 'Backup completed.';
      setStatus(backupStatus, message);
      if (data.archive) {
        await refreshLogs().catch(() => {});
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || 'Unable to trigger backup');
      setStatus(backupStatus, message, true);
    } finally {
      button.disabled = false;
    }
  };

  const backupButton = document.getElementById('run-backup');
  if (backupButton) {
    backupButton.addEventListener('click', runBackup);
  }

  const refreshButton = document.getElementById('refresh-logs');
  if (refreshButton) {
    refreshButton.addEventListener('click', refreshLogs);
  }
  logCategorySelect.addEventListener('change', refreshLogs);

  // Episode Browser
  let currentEpisodeSource = null;
  let currentEpisodeOffset = 0;
  const episodeLimit = 50;
  let currentEpisodes = []; // Store for modal access

  const loadEpisodeStats = async () => {
    const statsEl = document.getElementById('episode-stats');
    if (!statsEl) return;
    
    try {
      const response = await fetch('/api/episodes/stats');
      const data = await response.json();
      
      const stats = data.stats || {};
      const total = data.total || 0;
      
      let html = `<p><strong>Total Episodes:</strong> ${total}</p><ul>`;
      for (const [source, count] of Object.entries(stats).sort()) {
        html += `<li><strong>${source}:</strong> ${count}</li>`;
      }
      html += '</ul>';
      statsEl.innerHTML = html;
    } catch (error) {
      statsEl.innerHTML = `<p class="error">Failed to load stats: ${error.message}</p>`;
    }
  };

  const loadEpisodes = async () => {
    const container = document.getElementById('episodes-list');
    const statusEl = document.getElementById('episodes-status');
    if (!container) return;
    
    setStatus(statusEl, 'Loading episodes...');
    
    try {
      let url = `/api/episodes?limit=${episodeLimit}&offset=${currentEpisodeOffset}`;
      if (currentEpisodeSource) {
        url += `&source=${encodeURIComponent(currentEpisodeSource)}`;
      }
      
      const response = await fetch(url);
      const data = await response.json();
      const episodes = data.episodes || [];
      currentEpisodes = episodes; // Store for modal access
      
      if (episodes.length === 0) {
        container.innerHTML = '<p>No episodes found.</p>';
        setStatus(statusEl, '');
        return;
      }
      
      let html = '<table class="episodes-table"><thead><tr>';
      html += '<th>Source</th><th>Date</th><th>Text Preview</th>';
      html += '</tr></thead><tbody>';
      
      episodes.forEach((ep, idx) => {
        const date = new Date(ep.valid_at).toLocaleString();
        const text = (ep.text || '').substring(0, 150);
        const textPreview = text ? text + (ep.text.length > 150 ? '...' : '') : '(no text)';
        html += `<tr class="episode-row" data-episode-idx="${idx}" style="cursor: pointer;">`;
        html += `<td><span class="source-badge">${ep.source}</span><br><small style="color: #888; font-family: monospace; font-size: 0.75em;">${ep.native_id}</small></td>`;
        html += `<td>${date}</td>`;
        html += `<td>${textPreview}</td>`;
        html += `</tr>`;
      });
      
      html += '</tbody></table>';
      
      // Pagination controls
      html += '<div style="margin-top: 1rem; display: flex; gap: 1rem; align-items: center;">';
      if (currentEpisodeOffset > 0) {
        html += `<button id="episodes-prev" class="button">← Previous</button>`;
      }
      if (episodes.length === episodeLimit) {
        html += `<button id="episodes-next" class="button">Next →</button>`;
      }
      html += `<span style="margin-left: auto;">Showing ${currentEpisodeOffset + 1}–${currentEpisodeOffset + episodes.length}</span>`;
      html += '</div>';
      
      container.innerHTML = html;
      setStatus(statusEl, '');
      
      // Attach pagination handlers
      const prevBtn = document.getElementById('episodes-prev');
      const nextBtn = document.getElementById('episodes-next');
      if (prevBtn) {
        prevBtn.addEventListener('click', () => {
          currentEpisodeOffset = Math.max(0, currentEpisodeOffset - episodeLimit);
          loadEpisodes();
        });
      }
      if (nextBtn) {
        nextBtn.addEventListener('click', () => {
          currentEpisodeOffset += episodeLimit;
          loadEpisodes();
        });
      }
      
      // Attach row click handlers
      const episodeRows = container.querySelectorAll('.episode-row');
      episodeRows.forEach(row => {
        row.addEventListener('click', () => {
          const idx = parseInt(row.dataset.episodeIdx, 10);
          showEpisodeModal(currentEpisodes[idx]);
        });
      });
    } catch (error) {
      container.innerHTML = `<p class="error">Failed to load episodes: ${error.message}</p>`;
      setStatus(statusEl, '');
    }
  };

  const showEpisodeModal = (episode) => {
    // Create modal overlay
    const modal = document.createElement('div');
    modal.style.cssText = `
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0, 0, 0, 0.85);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 10000;
      padding: 2rem;
    `;
    
    const modalContent = document.createElement('div');
    modalContent.style.cssText = `
      background: #1a1b1e;
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 12px;
      max-width: 900px;
      max-height: 90vh;
      width: 100%;
      overflow: auto;
      padding: 2rem;
      box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
    `;
    
    // Parse JSON fields
    let metadata = {};
    let jsonData = {};
    try {
      metadata = episode.metadata_json ? JSON.parse(episode.metadata_json) : {};
    } catch (e) {
      metadata = { _error: 'Failed to parse metadata' };
    }
    try {
      jsonData = episode.json_data ? JSON.parse(episode.json_data) : {};
    } catch (e) {
      jsonData = {};
    }
    
    const fullText = episode.text || '(no text content)';
    
    modalContent.innerHTML = `
      <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 1.5rem;">
        <h2 style="margin: 0;">Episode Details</h2>
        <button id="close-modal" class="button" style="padding: 0.5rem 1rem;">✕ Close</button>
      </div>
      
      <div style="display: grid; gap: 1.5rem;">
        <div>
          <h3 style="margin: 0 0 0.5rem 0; color: #888;">Episode ID</h3>
          <code style="display: block; padding: 0.75rem; background: rgba(255,255,255,0.05); border-radius: 6px; font-size: 0.9rem;">${episode.episode_id}</code>
        </div>
        
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem;">
          <div>
            <h3 style="margin: 0 0 0.5rem 0; color: #888;">Source</h3>
            <span class="source-badge">${episode.source}</span>
          </div>
          <div>
            <h3 style="margin: 0 0 0.5rem 0; color: #888;">Native ID</h3>
            <code style="font-size: 0.9rem;">${episode.native_id}</code>
          </div>
          <div>
            <h3 style="margin: 0 0 0.5rem 0; color: #888;">Version</h3>
            <code style="font-size: 0.9rem;">${episode.version}</code>
          </div>
          <div>
            <h3 style="margin: 0 0 0.5rem 0; color: #888;">Valid At</h3>
            <span style="font-size: 0.9rem;">${new Date(episode.valid_at).toLocaleString()}</span>
          </div>
        </div>
        
        <div>
          <h3 style="margin: 0 0 0.5rem 0; color: #888;">Text Content</h3>
          <pre style="padding: 1rem; background: rgba(255,255,255,0.05); border-radius: 6px; white-space: pre-wrap; word-break: break-word; max-height: 300px; overflow: auto; margin: 0; font-size: 0.9rem;">${fullText}</pre>
        </div>
        
        <div>
          <h3 style="margin: 0 0 0.5rem 0; color: #888;">Metadata</h3>
          <pre style="padding: 1rem; background: rgba(255,255,255,0.05); border-radius: 6px; overflow: auto; max-height: 300px; margin: 0; font-size: 0.85rem;">${JSON.stringify(metadata, null, 2)}</pre>
        </div>
        
        ${Object.keys(jsonData).length > 0 ? `
        <div>
          <h3 style="margin: 0 0 0.5rem 0; color: #888;">JSON Data</h3>
          <pre style="padding: 1rem; background: rgba(255,255,255,0.05); border-radius: 6px; overflow: auto; max-height: 300px; margin: 0; font-size: 0.85rem;">${JSON.stringify(jsonData, null, 2)}</pre>
        </div>
        ` : ''}
      </div>
    `;
    
    modal.appendChild(modalContent);
    document.body.appendChild(modal);
    
    // Close handlers
    const closeModal = () => modal.remove();
    document.getElementById('close-modal').addEventListener('click', closeModal);
    modal.addEventListener('click', (e) => {
      if (e.target === modal) closeModal();
    });
    document.addEventListener('keydown', function escHandler(e) {
      if (e.key === 'Escape') {
        closeModal();
        document.removeEventListener('keydown', escHandler);
      }
    });
  };

  // Bootstrap
  const bootstrap = async () => {
    // Activate tab based on URL path (default: episodes)
    const initialTab = getTabFromPath();
    activateTab(initialTab);
    await loadConfig();
    await Promise.allSettled([loadGoogleStatus(), loadSlackStatus()]);
    await loadLogCategories().catch((error) => setStatus(logsStatus, error.message, true));
    await refreshLogs().catch((error) => setStatus(logsStatus, error.message, true));
    
    // Setup episode browser if available
    const episodeSourceFilter = document.getElementById('episode-source-filter');
    if (episodeSourceFilter) {
      episodeSourceFilter.addEventListener('change', (e) => {
        currentEpisodeSource = e.target.value || null;
        currentEpisodeOffset = 0;
        loadEpisodes();
      });
    }

    const episodesRefreshBtn = document.getElementById('episodes-refresh');
    if (episodesRefreshBtn) {
      episodesRefreshBtn.addEventListener('click', () => {
        currentEpisodeOffset = 0;
        Promise.all([loadEpisodeStats(), loadEpisodes()]);
      });
    }
    
    // Load episodes if the tab is available
    if (document.getElementById('episodes-list')) {
      await Promise.allSettled([loadEpisodeStats(), loadEpisodes()]);
    }
  };

  bootstrap().catch((error) => {
    const message = error instanceof Error ? error.message : String(error || 'Initialisation failed');
    setStatus(defaultStatus, message, true);
  });
})();


