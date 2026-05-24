#!/usr/bin/env node
/**
 * RoundTable 开发面板 — 端口可配置
 * 无需 npm install，使用 Node.js 内置模块运行。
 * 启动: node devpanel.js
 */

const http = require('http');
const { spawn, execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

function parsePort(value, fallback) {
  const parsed = Number.parseInt(String(value || ''), 10);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

const DEV_PANEL_PORT = parsePort(process.env.ROUNDTABLE_DEVPANEL_PORT, 8888);
const BACKEND_PORT = parsePort(process.env.ROUNDTABLE_BACKEND_PORT, 8001);
const LOCAL_BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;
const LOCALHOST_BACKEND_URL = `http://localhost:${BACKEND_PORT}`;
const LOCALHOST_DEVPANEL_URL = `http://localhost:${DEV_PANEL_PORT}`;
const ROOT = __dirname;
const BACKEND_DIR = path.join(ROOT, 'backend');
const FRONTEND_DIR = path.join(ROOT, 'frontend');
const MEETING_HISTORY_DIR = path.join(BACKEND_DIR, 'runtime', 'meeting_history');
const MEETING_HISTORY_RETENTION_LIMIT = 9999;
const MEETING_RUNNING_STALE_TIMEOUT_SECONDS = 10 * 60;

// ── 进程管理 ──────────────────────────────────────────────
const processes = {
  backend: { proc: null, logs: [], label: `后端 (FastAPI :${BACKEND_PORT})` },
};

// 是否正在停止中（kill 后端口可能短暂残留，此时强制上报 running=false）
let _backendStopping = false;
const logTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
});

function log(service, line) {
  const entry = { t: logTimeFormatter.format(new Date()), line };
  processes[service].logs.push(entry);
  if (processes[service].logs.length > 300) processes[service].logs.shift();
  sseClients[service]?.forEach(res => {
    res.write(`data: ${JSON.stringify(entry)}\n\n`);
  });
}

function startBackend() {
  _backendStopping = false; // clear any stale stopping state
  if (processes.backend.proc) return { ok: false, msg: '已在运行' };
  if (isPortListening(BACKEND_PORT)) {
    log('backend', `⚠ 端口 ${BACKEND_PORT} 已被占用（外部进程）`);
    broadcastStatus();
    return { ok: false, msg: '检测到后端已由外部进程运行，可直接“打开应用”；若需面板接管，请先点击“停止后端”后再启动' };
  }
  const rootVenvPy = path.join(ROOT, '.venv', 'bin', 'python');
  const backendVenvPy = path.join(BACKEND_DIR, 'venv', 'bin', 'python');
  const pyBin = fs.existsSync(rootVenvPy)
    ? rootVenvPy
    : (fs.existsSync(backendVenvPy) ? backendVenvPy : 'python3');
  const envFile = path.join(BACKEND_DIR, '.env');
  const args = [
    '-m', 'uvicorn',
    'app.main:app',
    '--app-dir', BACKEND_DIR,
    '--host', '0.0.0.0',
    '--port', String(BACKEND_PORT),
  ];
  if (fs.existsSync(envFile)) {
    args.splice(5, 0, '--env-file', envFile);
  }
  const proc = spawn(pyBin, args, {
    cwd: ROOT,
    env: { ...process.env, PYTHONUNBUFFERED: '1', DEBUG: process.env.DEBUG || 'false' },
  });
  processes.backend.proc = proc;
  proc.stdout.on('data', d => d.toString().split('\n').filter(Boolean).forEach(l => log('backend', l)));
  proc.stderr.on('data', d => d.toString().split('\n').filter(Boolean).forEach(l => log('backend', l)));
  proc.on('exit', code => {
    log('backend', '⏹ 进程退出 (code ' + code + ')');
    processes.backend.proc = null;
    _backendStopping = false;
    broadcastStatus();
  });
  log('backend', '▶ 启动后端...');
  broadcastStatus();
  return { ok: true };
}

function stopBackend() {
  if (processes.backend.proc) {
    _backendStopping = true;
    const procToKill = processes.backend.proc;
    processes.backend.proc = null;
    procToKill.kill('SIGTERM');
    // Kill any remaining processes on the port (e.g., uvicorn --reload children)
    setTimeout(() => {
      getAllPortPids(BACKEND_PORT).forEach(pid => {
        try { execSync('kill -9 ' + pid, { timeout: 1000 }); } catch(e) {}
      });
      broadcastStatus();
    }, 600);
    log('backend', '⏹ 已停止');
    broadcastStatus(); // _backendStopping=true → running=false immediately
    // Clear flag once port is actually free
    let attempts = 0;
    const pollTimer = setInterval(() => {
      attempts++;
      if (!isPortListening(BACKEND_PORT) || attempts >= 20) {
        clearInterval(pollTimer);
        _backendStopping = false;
        broadcastStatus();
      }
    }, 500);
    return { ok: true };
  }
  _backendStopping = true;
  const pids = getAllPortPids(BACKEND_PORT);
  if (pids.length === 0) { _backendStopping = false; return { ok: false, msg: '未运行' }; }
  pids.forEach(pid => {
    try { execSync('kill -9 ' + pid, { timeout: 2000 }); } catch(e) { /* ignore */ }
  });
  log('backend', '⏹ 已停止外部进程，等待端口释放…');
  broadcastStatus(); // _backendStopping=true → running=false immediately
  // Poll until port is actually released, then clear stopping state
  let attempts = 0;
  const timer = setInterval(() => {
    attempts++;
    if (!isPortListening(BACKEND_PORT) || attempts >= 20) {
      clearInterval(timer);
      _backendStopping = false;
      if (!isPortListening(BACKEND_PORT)) {
        log('backend', `✅ 端口 ${BACKEND_PORT} 已释放`);
      } else {
        log('backend', '⚠ 端口释放超时，请手动检查');
      }
      broadcastStatus();
    }
  }, 500);
  return { ok: true };
}

// ── SSE ────────────────────────────────────────────────────
const sseClients = { backend: new Set() };

function broadcastStatus() {
  const status = getStatus();
  sseClients.backend.forEach(res => {
    res.write('event: status\ndata: ' + JSON.stringify(status) + '\n\n');
  });
}

function isPortListening(port) {
  try {
    const out = execSync('lsof -i :' + port + ' -sTCP:LISTEN -t 2>/dev/null', { encoding: 'utf8', timeout: 2000 }).trim();
    return out.length > 0;
  } catch(e) { return false; }
}

function getPortPid(port) {
  try {
    const out = execSync('lsof -i :' + port + ' -sTCP:LISTEN -t 2>/dev/null', { encoding: 'utf8', timeout: 2000 }).trim();
    return out.split('\n')[0] || null;
  } catch(e) { return null; }
}

// Returns ALL pids listening on a port (handles uvicorn --reload parent+child)
function getAllPortPids(port) {
  try {
    const out = execSync('lsof -i :' + port + ' -sTCP:LISTEN -t 2>/dev/null', { encoding: 'utf8', timeout: 2000 }).trim();
    return out.split('\n').filter(Boolean);
  } catch(e) { return []; }
}

function getStatus() {
  const backendManaged = !!processes.backend.proc;
  const backendPortUp = isPortListening(BACKEND_PORT);
  // While explicitly stopping: report not-running so Start button re-enables immediately
  const backendRunning = _backendStopping ? false : backendPortUp;
  const flutterEmbedded = fs.existsSync(path.join(BACKEND_DIR, 'static', 'index.html'));
  return {
    backend: { running: backendRunning, managed: backendManaged },
    flutter: { embedded: flutterEmbedded, backendRunning: backendRunning },
  };
}

// ── 综合健康检查 ────────────────────────────────────────────
async function getFullHealth() {
  const results = {};
  let currentConfig = null;

  const serviceNames = {
    chattts: 'ChatTTS',
    capswriter: 'CapsWriter',
    cosyvoice: 'CosyVoice',
    edge_tts: 'Edge TTS',
    fireredtts: 'FireRedTTS',
    funasr: 'FunASR',
    ollama: 'Ollama',
    openai_tts: 'OpenAI TTS',
    openai_whisper: 'OpenAI Whisper',
    openvoice: 'OpenVoice',
    vibevoice: 'VibeVoice',
    vosk: 'Vosk',
  };

  function serviceDisplayName(serviceId) {
    return serviceNames[serviceId] || serviceId;
  }

  function configuredServiceTitle(config, serviceId) {
    if (serviceId === config.asr_provider) {
      return '当前 ASR · ' + serviceDisplayName(serviceId);
    }
    if (serviceId === config.tts_provider) {
      return '当前 TTS · ' + serviceDisplayName(serviceId);
    }
    if (serviceId === 'ollama' && config.llm_provider === 'ollama') {
      return '当前 LLM · Ollama';
    }
    return serviceDisplayName(serviceId);
  }

  function healthDetail(service) {
    if (service && service.detail) {
      return service.detail;
    }
    if (service && service.reachable && service.status_code) {
      return 'HTTP ' + service.status_code;
    }
    return service && service.reachable ? '可达' : '不可达';
  }

  // 后端 API 服务
  try {
    const data = await httpGet(`${LOCAL_BACKEND_URL}/api/v1/config/current`);
    currentConfig = JSON.parse(data);
    results.backend_api = {
      name: '后端 API 服务',
      url: LOCALHOST_BACKEND_URL,
      reachable: true,
      detail:
        'LLM: ' + currentConfig.llm_provider_name + ' › ' + currentConfig.model +
        ' ｜ ASR: ' + serviceDisplayName(currentConfig.asr_provider) +
        ' ｜ TTS: ' + serviceDisplayName(currentConfig.tts_provider),
    };
  } catch(e) {
    results.backend_api = { name: '后端 API 服务', url: LOCALHOST_BACKEND_URL, reachable: false, detail: '无法连接' };
  }

  // 前端
  const flutterEmbedded = fs.existsSync(path.join(BACKEND_DIR, 'static', 'index.html'));
  results.frontend = { name: '前端 Flutter Web', url: LOCALHOST_BACKEND_URL, reachable: flutterEmbedded && results.backend_api.reachable, detail: flutterEmbedded ? `已内嵌到后端 :${BACKEND_PORT}` : '未构建' };

  // 开发面板
  results.devpanel = { name: '开发面板', url: LOCALHOST_DEVPANEL_URL, reachable: true, detail: '运行中' };

  // 仅展示当前配置真正使用到的语音/本地模型服务。
  if (currentConfig) {
    try {
      const data = await httpGet(`${LOCAL_BACKEND_URL}/api/v1/config/health?current_only=1`);
      const services = JSON.parse(data);
      for (const [key, val] of Object.entries(services)) {
        results['configured_' + key] = {
          name: configuredServiceTitle(currentConfig, key),
          url: val.url || '-',
          reachable: !!val.reachable,
          detail: healthDetail(val),
        };
      }
    } catch(e) {
      results.configured_services = {
        name: '当前语音/本地模型服务',
        url: `${LOCALHOST_BACKEND_URL}/api/v1/config/health?current_only=1`,
        reachable: false,
        detail: '当前配置健康检查获取失败',
      };
    }
  }

  return results;
}

function httpGet(reqUrl) {
  return new Promise((resolve, reject) => {
    const req = http.get(reqUrl, { timeout: 4000 }, res => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => resolve(data));
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
  });
}

function readRequestBody(req, maxBytes) {
  const limit = typeof maxBytes === 'number' && maxBytes > 0 ? maxBytes : 1024 * 1024;
  return new Promise((resolve, reject) => {
    let body = '';
    req.on('data', chunk => {
      body += chunk.toString();
      if (Buffer.byteLength(body, 'utf8') > limit) {
        reject(new Error('request_body_too_large'));
        req.destroy();
      }
    });
    req.on('end', () => resolve(body));
    req.on('error', reject);
  });
}

function requestBackend(method, reqPath, options) {
  const opts = options || {};
  const headers = Object.assign({}, opts.headers || {});
  const body = typeof opts.body === 'string' ? opts.body : '';
  if (body && !headers['Content-Length']) {
    headers['Content-Length'] = Buffer.byteLength(body, 'utf8');
  }

  return new Promise((resolve, reject) => {
    const req = http.request(
      {
        hostname: '127.0.0.1',
        port: BACKEND_PORT,
        method,
        path: reqPath,
        timeout: 6000,
        headers,
      },
      res => {
        let raw = '';
        res.on('data', chunk => {
          raw += chunk.toString();
        });
        res.on('end', () => {
          resolve({
            statusCode: res.statusCode || 500,
            body: raw,
            headers: res.headers || {},
          });
        });
      }
    );

    req.on('error', reject);
    req.on('timeout', () => {
      req.destroy(new Error('backend_request_timeout'));
    });

    if (body) req.write(body);
    req.end();
  });
}

function sendJson(res, statusCode, payload) {
  res.writeHead(statusCode, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(payload));
}

async function proxyBackendJson(res, requestOptions) {
  const opts = requestOptions || {};
  const method = opts.method || 'GET';
  const reqPath = opts.path || '/';
  const upstream = await requestBackend(method, reqPath, {
    headers: opts.headers || {},
    body: opts.body || '',
  });
  res.writeHead(upstream.statusCode, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(upstream.body || '{}');
}

let backendAdminSessionToken = '';
let backendAdminSessionExpireAt = 0;
let backendManagementToken = '';
let backendManagementTokenLoadedAt = 0;

function parseBackendJson(raw) {
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch (_) {
    return {};
  }
}

function loadBackendManagementToken(forceRefresh) {
  const now = Date.now();
  if (!forceRefresh && backendManagementToken && now - backendManagementTokenLoadedAt < 60 * 1000) {
    return backendManagementToken;
  }

  let token = String(process.env.MANAGEMENT_API_TOKEN || '').trim();
  if (!token) {
    try {
      const envPath = path.join(BACKEND_DIR, '.env');
      if (fs.existsSync(envPath)) {
        const lines = fs.readFileSync(envPath, 'utf8').split('\n');
        for (const line of lines) {
          const trimmed = String(line || '').trim();
          if (!trimmed || trimmed.startsWith('#')) continue;
          const idx = trimmed.indexOf('=');
          if (idx <= 0) continue;
          const key = trimmed.slice(0, idx).trim();
          if (key !== 'MANAGEMENT_API_TOKEN') continue;
          token = trimmed.slice(idx + 1).trim().replace(/^['\"]|['\"]$/g, '');
          break;
        }
      }
    } catch (_) {
      token = '';
    }
  }

  backendManagementToken = token;
  backendManagementTokenLoadedAt = now;
  return backendManagementToken;
}

async function requestBackendAdminViaManagementToken(method, reqPath, baseHeaders, body) {
  let token = loadBackendManagementToken(false);
  if (!token) {
    token = loadBackendManagementToken(true);
  }
  if (!token) {
    return null;
  }
  return requestBackend(method, reqPath, {
    headers: Object.assign({}, baseHeaders, { 'X-Admin-Token': token }),
    body: body || '',
  });
}

async function ensureBackendAdminSession(forceRefresh) {
  const now = Date.now();
  if (!forceRefresh && backendAdminSessionToken && now < backendAdminSessionExpireAt) {
    return backendAdminSessionToken;
  }

  const loginPayload = {
    username: 'admin',
    password: '',
  };
  const upstream = await requestBackend('POST', '/api/v1/auth/login', {
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
    body: JSON.stringify(loginPayload),
  });

  const parsed = parseBackendJson(upstream.body);
  const token = String(parsed.token || '').trim();
  if (upstream.statusCode >= 400 || !token) {
    const reason = parsed.detail || parsed.error || ('HTTP ' + upstream.statusCode);
    throw new Error('admin_auto_auth_failed: ' + reason);
  }

  backendAdminSessionToken = token;
  // 会话 token 由后端维持 7 天，面板侧保守缓存 6 小时并按 401 自动刷新。
  backendAdminSessionExpireAt = now + 6 * 60 * 60 * 1000;
  return backendAdminSessionToken;
}

async function proxyBackendAdminJson(res, requestOptions) {
  const opts = requestOptions || {};
  const method = opts.method || 'GET';
  const reqPath = opts.path || '/';
  const baseHeaders = Object.assign({}, opts.headers || {});

  let upstream = await requestBackendAdminViaManagementToken(
    method,
    reqPath,
    baseHeaders,
    opts.body || '',
  );

  if (!upstream || upstream.statusCode === 401 || upstream.statusCode === 403) {
    let token = await ensureBackendAdminSession(false);
    upstream = await requestBackend(method, reqPath, {
      headers: Object.assign({}, baseHeaders, { Authorization: 'Bearer ' + token }),
      body: opts.body || '',
    });

    if (upstream.statusCode === 401) {
      token = await ensureBackendAdminSession(true);
      upstream = await requestBackend(method, reqPath, {
        headers: Object.assign({}, baseHeaders, { Authorization: 'Bearer ' + token }),
        body: opts.body || '',
      });
    }
  }

  res.writeHead(upstream.statusCode, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(upstream.body || '{}');
}

async function loadAllThinkersFromBackend() {
  const pageSize = 100;
  let page = 1;
  let total = Infinity;
  const items = [];
  while (items.length < total && page <= 10) {
    const upstream = await requestBackend('GET', '/api/v1/thinkers/?page=' + page + '&size=' + pageSize);
    if ((upstream.statusCode || 500) >= 400) {
      const parsed = parseBackendJson(upstream.body);
      throw new Error(parsed.detail || parsed.error || ('thinkers_proxy_failed_http_' + upstream.statusCode));
    }
    const parsed = parseBackendJson(upstream.body);
    const batch = Array.isArray(parsed.items) ? parsed.items : [];
    total = Number(parsed.total || batch.length || 0);
    items.push(...batch);
    if (batch.length < pageSize) break;
    page += 1;
  }
  return {
    ok: true,
    total: items.length,
    items,
  };
}

function safeHistoryId(sessionId) {
  return String(sessionId || '').trim().replace(/[^A-Za-z0-9._-]+/g, '_');
}

function readJsonFile(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function readJsonFileIfExists(filePath, fallbackValue) {
  if (!fs.existsSync(filePath)) return fallbackValue;
  try {
    return readJsonFile(filePath);
  } catch (_) {
    return fallbackValue;
  }
}

function parseIsoDate(value) {
  var raw = String(value || '').trim();
  if (!raw) return null;
  var parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed;
}

function normalizeMeetingHistorySummary(summary) {
  if (!summary || typeof summary !== 'object') return {};
  var normalized = Object.assign({}, summary);
  var status = String(normalized.status || '').trim().toLowerCase();
  if (status !== 'running') return normalized;

  var marker = parseIsoDate(
    normalized.updated_at || normalized.ended_at || normalized.started_at
  );
  var isStale = true;
  if (marker) {
    var ageSeconds = (Date.now() - marker.getTime()) / 1000;
    isStale = ageSeconds > MEETING_RUNNING_STALE_TIMEOUT_SECONDS;
  }
  if (!isStale) return normalized;

  normalized.status = 'disconnected';
  if (!normalized.finish_reason) {
    normalized.finish_reason = 'stale_running_session';
  }
  if (!normalized.ended_at) {
    normalized.ended_at = normalized.updated_at || new Date().toISOString();
  }
  return normalized;
}

function buildMeetingRecordingAudioUrl(sessionId, recordingId) {
  return 'api/meeting-history/' + encodeURIComponent(sessionId) + '/recordings/' + encodeURIComponent(recordingId) + '/audio';
}

function enrichMeetingRecording(sessionId, recording) {
  if (!recording || typeof recording !== 'object') return null;
  var normalized = Object.assign({}, recording);
  if (normalized.recording_id) {
    normalized.audio_url = buildMeetingRecordingAudioUrl(sessionId, normalized.recording_id);
  }
  return normalized;
}

function collectMeetingHistories() {
  if (!fs.existsSync(MEETING_HISTORY_DIR)) return [];
  return fs.readdirSync(MEETING_HISTORY_DIR, { withFileTypes: true })
    .filter(entry => entry.isDirectory())
    .map(entry => {
      const summaryPath = path.join(MEETING_HISTORY_DIR, entry.name, 'summary.json');
      if (!fs.existsSync(summaryPath)) return null;
      try {
        return normalizeMeetingHistorySummary(readJsonFile(summaryPath));
      } catch (e) {
        return {
          session_id: entry.name,
          safe_session_id: entry.name,
          status: 'error',
          updated_at: '',
          last_event_preview: 'summary.json 读取失败: ' + e.message,
        };
      }
    })
    .filter(Boolean)
    .sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')));
}

function listMeetingHistories(limit = MEETING_HISTORY_RETENTION_LIMIT) {
  const items = collectMeetingHistories();
  if (typeof limit !== 'number' || limit <= 0) return items;
  return items.slice(0, limit);
}

function deleteMeetingHistory(sessionId) {
  const safeId = safeHistoryId(sessionId);
  if (!safeId) {
    return { ok: false, msg: '无效的会议 ID' };
  }
  const sessionDir = path.join(MEETING_HISTORY_DIR, safeId);
  if (!fs.existsSync(sessionDir)) {
    return { ok: false, msg: '会议记录不存在' };
  }
  fs.rmSync(sessionDir, { recursive: true, force: true });
  return { ok: true, msg: '已删除会议记录', sessionId: safeId };
}

function pruneMeetingHistories(retainCount = MEETING_HISTORY_RETENTION_LIMIT) {
  const items = collectMeetingHistories();
  if (items.length <= retainCount) {
    return { ok: true, removed: 0, remaining: items.length, retainCount };
  }
  const removed = items.slice(retainCount);
  removed.forEach(item => {
    const safeId = safeHistoryId(item.safe_session_id || item.session_id);
    if (!safeId) return;
    fs.rmSync(path.join(MEETING_HISTORY_DIR, safeId), { recursive: true, force: true });
  });
  return {
    ok: true,
    removed: removed.length,
    remaining: Math.min(items.length, retainCount),
    retainCount,
  };
}

function readMeetingHistory(sessionId) {
  const safeId = safeHistoryId(sessionId);
  const sessionDir = path.join(MEETING_HISTORY_DIR, safeId);
  const summaryPath = path.join(sessionDir, 'summary.json');
  const eventsPath = path.join(sessionDir, 'events.jsonl');
  const scriptPath = path.join(sessionDir, 'script.json');
  const recordingsPath = path.join(sessionDir, 'recordings.json');
  if (!fs.existsSync(summaryPath)) {
    const err = new Error('history_not_found');
    err.code = 'ENOENT';
    throw err;
  }
  const summary = normalizeMeetingHistorySummary(readJsonFile(summaryPath));
  const events = fs.existsSync(eventsPath)
    ? fs.readFileSync(eventsPath, 'utf8')
        .split('\n')
        .filter(Boolean)
        .map(line => JSON.parse(line))
    : [];
  const recordingsPayload = readJsonFileIfExists(recordingsPath, { recordings: [] });
  const recordings = Array.isArray(recordingsPayload && recordingsPayload.recordings)
    ? recordingsPayload.recordings
        .map(item => enrichMeetingRecording(summary.session_id || safeId, item))
        .filter(Boolean)
    : [];
  const recordingsById = recordings.reduce((acc, item) => {
    if (item && item.recording_id) {
      acc[item.recording_id] = item;
    }
    return acc;
  }, {});
  const scriptPayload = readJsonFileIfExists(scriptPath, { lines: [] });
  const scriptLines = Array.isArray(scriptPayload && scriptPayload.lines)
    ? scriptPayload.lines.map(line => {
        const normalized = Object.assign({}, line);
        if (normalized.recording && normalized.recording.recording_id) {
          const attached = recordingsById[normalized.recording.recording_id];
          normalized.recording = attached
            ? Object.assign({}, attached, normalized.recording)
            : enrichMeetingRecording(summary.session_id || safeId, normalized.recording);
        }
        return normalized;
      })
    : [];
  return {
    summary,
    events,
    recordings,
    script: Object.assign({}, scriptPayload, {
      line_count: scriptLines.length,
      recording_count: recordings.length,
      lines: scriptLines,
    }),
  };
}

function resolveMeetingRecording(sessionId, recordingId) {
  const safeId = safeHistoryId(sessionId);
  const sessionDir = path.join(MEETING_HISTORY_DIR, safeId);
  const summaryPath = path.join(sessionDir, 'summary.json');
  const recordingsPath = path.join(sessionDir, 'recordings.json');
  if (!fs.existsSync(summaryPath)) {
    const err = new Error('history_not_found');
    err.code = 'ENOENT';
    throw err;
  }
  const recordingsPayload = readJsonFileIfExists(recordingsPath, { recordings: [] });
  const recordings = Array.isArray(recordingsPayload && recordingsPayload.recordings)
    ? recordingsPayload.recordings
    : [];
  const recording = recordings.find(item => item && item.recording_id === recordingId);
  if (!recording) {
    const err = new Error('recording_not_found');
    err.code = 'ENOENT';
    throw err;
  }
  const relativePath = String(recording.relative_path || '').trim();
  if (!relativePath) {
    const err = new Error('recording_path_missing');
    err.code = 'ENOENT';
    throw err;
  }
  const resolvedPath = path.resolve(sessionDir, relativePath);
  const sessionRoot = path.resolve(sessionDir) + path.sep;
  if (resolvedPath !== path.resolve(sessionDir) && !resolvedPath.startsWith(sessionRoot)) {
    const err = new Error('invalid_recording_path');
    err.code = 'EINVAL';
    throw err;
  }
  if (!fs.existsSync(resolvedPath)) {
    const err = new Error('recording_file_not_found');
    err.code = 'ENOENT';
    throw err;
  }
  return { recording, filePath: resolvedPath };
}

const THINKERS_PAGE_HTML = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RoundTable 思想家图谱</title>
<style>
  *{box-sizing:border-box}
  body{margin:0;background:radial-gradient(circle at top,#182b53 0%,#0c1426 40%,#070d18 100%);color:#eef4ff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
  a{color:inherit}
  .page{max-width:1320px;margin:0 auto;padding:32px 24px 48px}
  .hero{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(280px,.8fr);gap:18px;align-items:stretch;margin-bottom:22px}
  .hero-main,.hero-side{border:1px solid rgba(67,103,167,.55);border-radius:22px;background:linear-gradient(160deg,rgba(12,23,45,.94),rgba(19,34,62,.88));box-shadow:0 24px 60px rgba(0,0,0,.24)}
  .hero-main{padding:28px}
  .hero-side{padding:22px}
  .kicker{font-size:12px;letter-spacing:2px;text-transform:uppercase;color:#8fa3cc;font-weight:700}
  h1{margin:10px 0 14px;font-size:38px;line-height:1.15;color:#fff6d7}
  .hero-copy{font-size:15px;line-height:1.85;color:#cbd8f4;max-width:860px}
  .hero-actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}
  .hero-btn{display:inline-flex;align-items:center;justify-content:center;height:40px;padding:0 16px;border-radius:999px;border:1px solid rgba(108,138,196,.5);background:rgba(20,36,63,.9);color:#eef4ff;text-decoration:none;font-size:13px;font-weight:600}
  .hero-btn:hover{border-color:#f4d98b;color:#fff6d7}
  .hero-side h2{margin:0 0 10px;font-size:16px;color:#fff6d7}
  .hero-side p{margin:0 0 14px;font-size:13px;line-height:1.8;color:#b4c2e0}
  .hero-side .stat{display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-top:1px solid rgba(67,103,167,.28);font-size:13px;color:#dce8ff}
  .hero-side .stat strong{color:#fff6d7}
  .toolbar{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;margin-bottom:18px;padding:14px 16px;border:1px solid rgba(67,103,167,.44);border-radius:18px;background:rgba(10,18,34,.88)}
  .toolbar-copy{font-size:13px;color:#aebddb;line-height:1.7}
  .toolbar-copy strong{color:#fff6d7}
  .toolbar-search{display:flex;align-items:center;gap:10px;min-width:min(100%,360px)}
  .toolbar-search input{width:100%;height:42px;padding:0 14px;border-radius:14px;border:1px solid rgba(67,103,167,.52);background:rgba(16,28,52,.95);color:#eef4ff;font-size:14px;outline:none}
  .toolbar-search input::placeholder{color:#7289b4}
  .domain-nav{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:18px}
  .domain-chip{display:inline-flex;align-items:center;gap:8px;padding:10px 14px;border-radius:999px;border:1px solid rgba(67,103,167,.48);background:rgba(12,21,38,.84);color:#dfe9ff;text-decoration:none;font-size:13px}
  .domain-chip span{display:inline-flex;align-items:center;justify-content:center;min-width:24px;height:24px;padding:0 8px;border-radius:999px;background:rgba(212,160,23,.16);color:#f8dd8b;font-size:11px}
  .domain-chip:hover{border-color:#f4d98b;color:#fff6d7}
  .sections{display:flex;flex-direction:column;gap:18px}
  .section{border:1px solid rgba(67,103,167,.46);border-radius:20px;background:linear-gradient(180deg,rgba(8,16,30,.95),rgba(13,24,45,.92));padding:18px}
  .section-head{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;margin-bottom:14px}
  .section-title{font-size:24px;color:#fff6d7;font-weight:700}
  .section-meta{font-size:12px;color:#92a7cf}
  .thinker-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
  .thinker-card{display:flex;flex-direction:column;gap:14px;height:100%;padding:18px;border-radius:18px;border:1px solid rgba(67,103,167,.45);background:linear-gradient(180deg,rgba(19,31,58,.9),rgba(12,21,38,.96))}
  .thinker-head{display:flex;gap:14px;align-items:flex-start}
  .thinker-avatar{width:54px;height:54px;border-radius:16px;display:flex;align-items:center;justify-content:center;font-size:28px;background:linear-gradient(145deg,rgba(245,201,98,.2),rgba(84,136,235,.14));border:1px solid rgba(244,217,139,.42)}
  .thinker-name{font-size:21px;font-weight:700;color:#fff}
  .thinker-era{margin-top:5px;font-size:12px;color:#97acd4}
  .thinker-description{font-size:13px;line-height:1.75;color:#c4d2ef}
  .thinker-block{padding:12px 14px;border-radius:14px;background:rgba(9,16,29,.6);border:1px solid rgba(67,103,167,.28)}
  .thinker-block h3{margin:0 0 8px;font-size:12px;letter-spacing:1.2px;text-transform:uppercase;color:#f8dd8b}
  .thinker-core{font-size:14px;line-height:1.8;color:#eff4ff}
  .thinker-bio{font-size:14px;line-height:1.95;color:#d1dcf5}
  .tag-list{display:flex;flex-wrap:wrap;gap:8px}
  .tag{display:inline-flex;align-items:center;padding:6px 10px;border-radius:999px;background:rgba(31,49,83,.88);border:1px solid rgba(67,103,167,.4);font-size:12px;color:#dfe9ff}
  .empty-state{display:none;padding:34px 22px;border:1px dashed rgba(67,103,167,.45);border-radius:18px;background:rgba(8,16,30,.7);font-size:14px;line-height:1.8;color:#9fb1d7;text-align:center}
  .footer-note{margin-top:22px;font-size:12px;color:#87a0cf;text-align:center}
  @media (max-width: 980px){
    .hero{grid-template-columns:1fr}
    .thinker-grid{grid-template-columns:1fr}
  }
</style>
</head>
<body>
<div class="page">
  <section class="hero">
    <div class="hero-main">
      <div class="kicker">RoundTable Thinkers Atlas</div>
      <h1>思想家图谱</h1>
      <div class="hero-copy">这里不再直接展示原始 JSON，而是按思想领域分组呈现所有思想家。每张卡片都补充了核心思想摘要、约 300-600 字的生平与影响简介，以及适合课堂对话的思考切口，方便直接浏览、筛选和教学准备。</div>
      <div class="hero-actions">
        <a class="hero-btn" href="/">← 返回开发面板</a>
        <a class="hero-btn" href="${LOCALHOST_BACKEND_URL}/docs" target="_blank" rel="noopener">查看 API 文档</a>
      </div>
    </div>
    <aside class="hero-side">
      <h2>阅读方式</h2>
      <p>先按领域浏览，再用搜索快速定位姓名、时代、简介或核心观念。适合在备课、选角、组局前快速比较思想家的切入角度。</p>
      <div class="stat"><span>当前状态</span><strong id="thinkers-status">加载中…</strong></div>
      <div class="stat"><span>领域数</span><strong id="thinkers-domain-count">-</strong></div>
      <div class="stat"><span>思想家总数</span><strong id="thinkers-total-count">-</strong></div>
    </aside>
  </section>
  <section class="toolbar">
    <div class="toolbar-copy" id="thinkers-summary"><strong>准备中：</strong> 正在从后端汇总思想家数据。</div>
    <label class="toolbar-search">
      <input id="thinker-search" type="search" placeholder="搜索姓名、时代、简介或核心内容">
    </label>
  </section>
  <nav class="domain-nav" id="domain-nav"></nav>
  <div class="empty-state" id="thinkers-empty">没有找到匹配的思想家，请尝试更短的关键词或切换搜索词。</div>
  <section class="sections" id="thinker-sections"></section>
  <div class="footer-note">数据源：/api/thinkers（由开发面板代理 backend/api/v1/thinkers 聚合返回）</div>
</div>
<script>
  var thinkerState = { items: [], query: '' };
  function escHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
  function normalize(value) {
    return String(value == null ? '' : value).toLowerCase().trim();
  }
  function thinkerText(item) {
    return [
      item.display_name || '',
      item.name || '',
      item.era || '',
      item.domain_cn || '',
      item.description || '',
      item.core_summary || '',
      item.biography || '',
      Array.isArray(item.suggested_questions) ? item.suggested_questions.join(' ') : ''
    ].join(' ');
  }
  function matchesQuery(item, query) {
    if (!query) return true;
    return normalize(thinkerText(item)).indexOf(query) >= 0;
  }
  function groupByDomain(items) {
    var groups = {};
    items.forEach(function(item) {
      var domain = String(item.domain_cn || item.domain || '综合').trim() || '综合';
      if (!groups[domain]) groups[domain] = [];
      groups[domain].push(item);
    });
    return Object.keys(groups).sort(function(a, b) {
      return a.localeCompare(b, 'zh-Hans-CN');
    }).map(function(domain) {
      groups[domain].sort(function(a, b) {
        return String(a.display_name || a.name || '').localeCompare(String(b.display_name || b.name || ''), 'zh-Hans-CN');
      });
      return { domain: domain, items: groups[domain] };
    });
  }
  function renderDomainNav(groups) {
    var nav = document.getElementById('domain-nav');
    nav.innerHTML = groups.map(function(group) {
      return '<a class="domain-chip" href="#domain-' + encodeURIComponent(group.domain) + '">' +
        escHtml(group.domain) + '<span>' + group.items.length + '</span></a>';
    }).join('');
  }
  function renderQuestionTags(questions) {
    if (!Array.isArray(questions) || !questions.length) return '<span class="tag">适合课堂追问与角色扮演</span>';
    return questions.slice(0, 3).map(function(question) {
      return '<span class="tag">' + escHtml(question) + '</span>';
    }).join('');
  }
  function renderThinkerCard(item) {
    var name = item.display_name || item.name || '未命名思想家';
    var description = item.description || '这位思想家的代表性观点适合放在课堂对话中展开。';
    var core = item.core_summary || '核心思想摘要暂未生成。';
    var biography = item.biography || description;
    return '' +
      '<article class="thinker-card">' +
        '<div class="thinker-head">' +
          '<div class="thinker-avatar">' + escHtml(item.avatar || '🧠') + '</div>' +
          '<div>' +
            '<div class="thinker-name">' + escHtml(name) + '</div>' +
            '<div class="thinker-era">' + escHtml(item.era || '时代信息待补充') + '</div>' +
          '</div>' +
        '</div>' +
        '<div class="thinker-description">' + escHtml(description) + '</div>' +
        '<div class="thinker-block"><h3>思想体系核心内容</h3><div class="thinker-core">' + escHtml(core) + '</div></div>' +
        '<div class="thinker-block"><h3>生平与影响简介</h3><div class="thinker-bio">' + escHtml(biography) + '</div></div>' +
        '<div class="tag-list">' + renderQuestionTags(item.suggested_questions) + '</div>' +
      '</article>';
  }
  function renderSections(groups) {
    var container = document.getElementById('thinker-sections');
    container.innerHTML = groups.map(function(group) {
      return '' +
        '<section class="section" id="domain-' + encodeURIComponent(group.domain) + '">' +
          '<div class="section-head">' +
            '<div class="section-title">' + escHtml(group.domain) + '</div>' +
            '<div class="section-meta">共 ' + group.items.length + ' 位思想家</div>' +
          '</div>' +
          '<div class="thinker-grid">' + group.items.map(renderThinkerCard).join('') + '</div>' +
        '</section>';
    }).join('');
  }
  function renderThinkers() {
    var query = normalize(thinkerState.query);
    var filtered = thinkerState.items.filter(function(item) { return matchesQuery(item, query); });
    var groups = groupByDomain(filtered);
    document.getElementById('thinkers-total-count').textContent = thinkerState.items.length;
    document.getElementById('thinkers-domain-count').textContent = groups.length;
    document.getElementById('thinkers-summary').innerHTML =
      '<strong>当前结果：</strong> 共展示 ' + filtered.length + ' 位思想家，分布在 ' + groups.length + ' 个领域中。';
    document.getElementById('thinkers-status').textContent = query ? '已筛选' : '已加载';
    document.getElementById('thinkers-empty').style.display = filtered.length ? 'none' : 'block';
    renderDomainNav(groups);
    renderSections(groups);
  }
  function loadThinkers() {
    fetch('/api/thinkers')
      .then(function(r) {
        if (!r.ok) {
          return r.json().catch(function(){ return {}; }).then(function(payload) {
            throw new Error(payload.detail || payload.error || ('HTTP ' + r.status));
          });
        }
        return r.json();
      })
      .then(function(payload) {
        thinkerState.items = Array.isArray(payload.items) ? payload.items : [];
        renderThinkers();
      })
      .catch(function(err) {
        document.getElementById('thinkers-status').textContent = '加载失败';
        document.getElementById('thinkers-summary').innerHTML =
          '<strong>加载失败：</strong> ' + escHtml((err && err.message) || '无法读取思想家数据');
        document.getElementById('thinkers-empty').style.display = 'block';
        document.getElementById('thinkers-empty').textContent = '思想家数据加载失败，请确认后端已启动。';
      });
  }
  document.getElementById('thinker-search').addEventListener('input', function(event) {
    thinkerState.query = event.target.value || '';
    renderThinkers();
  });
  loadThinkers();
</script>
</body>
</html>`;

// ── Dashboard HTML ────────────────────────────────────────
const DASHBOARD_HTML = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RoundTable 开发面板</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#1a1a2e;color:#e0e0e0;font-family:'SF Mono',Menlo,'Courier New',monospace;min-height:100vh;padding:24px}
  h1{color:#d4a017;font-size:24px;margin-bottom:6px;letter-spacing:2px}
  .subtitle{color:#888;font-size:13px;margin-bottom:24px}
  /* 需求6：重新布局 - 改为单列堆叠 + 顶部状态条，信息密度更低、层次更清晰 */
  .topbar{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;
    background:linear-gradient(135deg,#16213e 0%,#0f1b35 100%);
    border:1px solid #0f3460;border-radius:12px;padding:14px 18px;margin-bottom:20px}
  .topbar .tb-left{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
  .topbar .tb-chip{display:inline-flex;align-items:center;gap:8px;padding:6px 12px;border-radius:999px;
    background:#0d1a33;border:1px solid #1b335f;font-size:12px;color:#c8cee2}
  .topbar .tb-actions{display:flex;gap:8px;flex-wrap:wrap}
  .stack{display:flex;flex-direction:column;gap:20px}
  .card{background:#16213e;border:1px solid #0f3460;border-radius:12px;padding:20px}
  .card-title{display:flex;align-items:center;gap:10px;margin-bottom:14px;font-size:15px;font-weight:600}
  .dot{width:10px;height:10px;border-radius:50%;background:#555;transition:background .3s}
  .dot.running{background:#4caf50;box-shadow:0 0 8px #4caf50}
  .dot.warn{background:#ff9800;box-shadow:0 0 8px #ff9800}
  .status-label{font-size:12px;color:#888;margin-left:auto}
  .status-label.running{color:#4caf50}
  .btns{display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap;align-items:center}
  button{border:none;border-radius:8px;padding:8px 18px;font-size:13px;cursor:pointer;font-family:inherit;transition:all .2s}
  .btn-start{background:#4caf50;color:#fff}
  .btn-start:hover{background:#66bb6a}
  .btn-start:disabled{background:#2e7d32;opacity:.5;cursor:default}
  .btn-stop{background:#ef5350;color:#fff}
  .btn-stop:hover{background:#e53935}
  .btn-stop:disabled{background:#7f0000;opacity:.5;cursor:default}
  a.btn-open{background:#0f3460;color:#d4a017;border:1px solid #d4a017;text-decoration:none;display:inline-flex;align-items:center;border-radius:8px;padding:8px 18px;font-size:13px;font-family:inherit;cursor:pointer}
  a.btn-open:hover{background:#1a4a7a}
  .btn-refresh{background:#0f3460;color:#80cbc4;border:1px solid #0f3460}
  .btn-refresh:hover{border-color:#80cbc4}
  .btn-danger-subtle{background:#2b1616;color:#ffb1b1;border:1px solid #874040}
  .btn-danger-subtle:hover{border-color:#ffb1b1}
  .btn-danger-subtle:disabled{opacity:.42;cursor:default;border-color:#5a2f2f;color:#946868}
  .log-box{background:#0d0d1a;border:1px solid #0f3460;border-radius:8px;height:200px;overflow-y:auto;padding:10px;font-size:11px;line-height:1.6}
  .log-line{color:#aaa;word-break:break-all}
  .log-line.err{color:#ef9a9a}
  .log-line.info{color:#80cbc4}
  .health-grid{margin-top:10px}
  .health-row{display:flex;align-items:center;gap:10px;padding:8px 12px;border-bottom:1px solid rgba(15,52,96,0.5)}
  .health-row:last-child{border-bottom:none}
  .health-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
  .health-dot.ok{background:#4caf50}
  .health-dot.fail{background:#ef5350}
  .health-name{flex:0 0 140px;font-size:13px;color:#e0e0e0}
  .health-url{flex:1;font-size:11px;color:#666;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .health-detail{flex:0 0 140px;font-size:11px;text-align:right}
  .health-detail.ok{color:#4caf50}
  .health-detail.fail{color:#ef5350}
  .monitor-info{background:#0d0d1a;border:1px solid #0f3460;border-radius:8px;padding:14px;font-size:12px;color:#aaa;line-height:1.8}
  .monitor-info .label{color:#888;display:inline-block;width:80px}
  .monitor-info .val{color:#e0e0e0}
  .monitor-info .val.ok{color:#4caf50}
  .monitor-info .val.warn{color:#ff9800}
  .links{margin-top:20px;display:flex;gap:16px;flex-wrap:wrap}
  .link-btn{background:#0f3460;color:#d4a017;border:1px solid #0f3460;border-radius:8px;padding:8px 16px;font-size:13px;text-decoration:none;display:inline-block}
  .link-btn:hover{border-color:#d4a017}
  .header-row{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:24px}
  .header-left{flex:0 0 auto}
  .header-right{flex:1;display:flex;flex-direction:column;align-items:flex-end;gap:8px}
  .action-row{display:flex;align-items:center;justify-content:flex-end;gap:10px;flex-wrap:wrap}
  .action-btn{height:34px;display:inline-flex;align-items:center;justify-content:center;padding:0 14px}
  .hw-info{display:flex;gap:12px;align-items:center;font-size:11px;color:#888;background:#16213e;border:1px solid #0f3460;border-radius:8px;padding:6px 12px}
  .hw-info .hw-chip{color:#d4a017;font-weight:600;font-size:12px}
  .hw-info .hw-sep{color:#333}
  .hw-opt{font-size:11px;color:#80cbc4;max-width:780px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:right}
  .quick-actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center;justify-content:flex-start}
  .runtime-split{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,2fr);gap:20px;align-items:start}
  .runtime-col{min-width:0}
  .runtime-col + .runtime-col{border-left:1px solid rgba(15,52,96,0.75);padding-left:20px}
  .card-title-spacer{margin-left:auto}
  .health-grid{margin-top:10px;background:#0d0d1a;border:1px solid #0f3460;border-radius:8px;overflow:hidden}
  .health-empty{padding:14px;color:#666;text-align:center}
  .history-panel-head{align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:10px}
  .history-panel-title{display:flex;align-items:center;gap:10px;min-width:0}
  .history-panel-actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:flex-end}
  .history-toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;margin-bottom:14px;padding:12px 14px;border-radius:14px;border:1px solid rgba(36,66,116,0.88);background:linear-gradient(180deg,rgba(8,16,30,0.95),rgba(13,24,45,0.92))}
  .history-toolbar-summary{display:flex;align-items:center;gap:8px;font-size:12px;color:#d7e5ff;font-weight:600;white-space:nowrap}
  .history-session-filters{display:flex;flex:1;flex-wrap:wrap;gap:8px;align-items:center;justify-content:flex-end;min-width:280px}
  .history-filter-field{display:inline-flex;align-items:center;gap:8px;padding:8px 10px;border-radius:12px;border:1px solid rgba(36,66,116,0.84);background:rgba(15,28,50,0.9);color:#dce8ff;min-height:40px}
  .history-filter-field span{font-size:10px;color:#8fa3cc;font-weight:700;letter-spacing:1px;text-transform:uppercase;flex-shrink:0}
  .history-filter-field input,.history-filter-field select{border:none;outline:none;background:transparent;color:#eef4ff;font-size:12px;min-width:0}
  .history-filter-field input::placeholder{color:#6379a2}
  .history-filter-field input[type="text"]{width:140px}
  .history-filter-field input[type="datetime-local"]{width:168px}
  .history-filter-field input[type="number"]{width:76px}
  .history-filter-field select{cursor:pointer}
  .history-filter-field-compact{gap:6px}
  .history-filter-field-compact select{width:74px}
  .history-dropdown{position:relative}
  .history-dropdown > summary{list-style:none}
  .history-dropdown > summary::-webkit-details-marker{display:none}
  .history-dropdown-menu{position:absolute;right:0;top:calc(100% + 8px);display:flex;flex-direction:column;gap:6px;min-width:168px;padding:8px;border-radius:14px;border:1px solid rgba(56,92,148,0.82);background:linear-gradient(180deg,rgba(8,17,31,0.98),rgba(18,34,62,0.96));box-shadow:0 14px 28px rgba(0,0,0,0.26);z-index:20}
  .history-dropdown-item{display:flex;align-items:center;justify-content:flex-start;border:none;border-radius:10px;padding:9px 12px;background:rgba(15,27,50,0.94);color:#e7f0ff;font-size:12px;cursor:pointer;text-align:left}
  .history-dropdown-item:hover{background:rgba(44,66,105,0.94);color:#fff7d4}
  .history-dropdown.disabled{opacity:.44;pointer-events:none}
  .history-split{display:grid;grid-template-columns:300px minmax(0,1fr);gap:20px;align-items:start}
  .history-list,.history-detail{background:#0d0d1a;border:1px solid #0f3460;border-radius:10px;min-height:280px}
  .history-list{padding:10px;display:flex;flex-direction:column;gap:10px;max-height:620px;overflow:auto}
  .history-item{border:1px solid rgba(27,51,95,0.9);border-radius:10px;padding:12px 12px 12px 14px;background:linear-gradient(180deg,rgba(17,25,47,0.92),rgba(11,17,32,0.96));cursor:pointer;transition:border-color .2s,transform .2s}
  .history-item:hover{border-color:#80cbc4;transform:translateY(-1px)}
  .history-item.active{border-color:#d4a017;box-shadow:0 0 0 1px rgba(212,160,23,0.25) inset}
  .history-item.selected{border-color:#80cbc4;box-shadow:0 0 0 1px rgba(128,203,196,0.22) inset}
  .history-item-head{display:flex;gap:12px;align-items:stretch}
  .history-item-body{flex:1;min-width:0}
  .history-select-wrap{display:flex;align-items:center;justify-content:center;padding-top:2px}
  .history-select-box{width:18px;height:18px;border-radius:6px;accent-color:#80cbc4;cursor:pointer}
  .history-item-main{display:flex;gap:12px;align-items:flex-start;justify-content:space-between}
  .history-item-copy{flex:1;min-width:0}
  .history-session-id{font-size:10px;line-height:1.4;color:#8fa3cc;letter-spacing:1.4px;text-transform:uppercase;margin-bottom:5px}
  .history-topic{font-size:13px;color:#f3f5ff;font-weight:700;line-height:1.5;margin-bottom:6px}
  .history-item-time{font-size:11px;color:#8b98b7;line-height:1.5}
  .history-item-side{display:flex;flex-direction:column;align-items:flex-end;gap:8px;flex-shrink:0;padding-top:1px}
  .history-item-count{display:inline-flex;align-items:center;justify-content:center;padding:4px 9px;border-radius:999px;border:1px solid rgba(52,88,143,0.68);background:rgba(16,32,61,0.86);color:#cfe0ff;font-size:10px;line-height:1.2}
  .history-meta{display:flex;flex-wrap:wrap;gap:8px;font-size:11px;color:#8b98b7;margin-bottom:8px}
  .history-pill{display:inline-flex;align-items:center;gap:6px;padding:3px 8px;border-radius:999px;border:1px solid #244274;background:#10203d;color:#c9d4f2}
  .history-pill.ok{border-color:#2f7a46;color:#7edb92;background:#112619}
  .history-pill.warn{border-color:#8a6825;color:#ffd976;background:#2b2212}
  .history-pill.fail{border-color:#874040;color:#ffb1b1;background:#2b1616}
  .history-preview{font-size:11px;line-height:1.6;color:#aab2c8}
  .history-detail{padding:16px;overflow:auto}
  .history-detail-head{display:grid;grid-template-columns:minmax(0,1fr) minmax(340px,430px);gap:14px;align-items:stretch;margin-bottom:14px}
  .history-head-panel{border:1px solid rgba(36,66,116,0.84);border-radius:16px;background:linear-gradient(160deg,rgba(9,20,39,0.97),rgba(18,34,62,0.93));padding:14px 16px;box-shadow:0 12px 28px rgba(0,0,0,0.18)}
  .history-head-main{display:flex;flex-direction:column;justify-content:space-between;min-height:118px}
  .history-head-kicker{font-size:10px;color:#8fa3cc;font-weight:700;letter-spacing:1.8px;text-transform:uppercase}
  .history-detail-title{margin-top:8px;font-size:20px;color:#f3f5ff;font-weight:700;line-height:1.45}
  .history-head-meta{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:12px;font-size:11px;color:#9cb2dc}
  .history-detail-actions{display:flex;flex-wrap:wrap;gap:10px;align-items:flex-start;justify-content:flex-end}
  .history-export-quick{display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:flex-end}
  .history-export-btn{display:inline-flex;align-items:center;gap:6px;padding:8px 12px;border-radius:999px;border:1px solid #315488;background:linear-gradient(135deg,rgba(17,39,73,0.96),rgba(33,63,109,0.92));color:#e7f0ff;font-size:11px;letter-spacing:.4px;cursor:pointer;transition:transform .18s,border-color .18s,background .18s}
  .history-export-btn:hover{transform:translateY(-1px);border-color:#f6e3a5;background:linear-gradient(135deg,rgba(54,75,34,0.92),rgba(60,78,124,0.96));color:#fff7d4}
  .history-export-btn.alt{border-color:#5b437d;background:linear-gradient(135deg,rgba(34,20,52,0.94),rgba(44,35,84,0.94));color:#f1e8ff}
  .history-export-btn.alt:hover{border-color:#f6e3a5;background:linear-gradient(135deg,rgba(76,52,26,0.94),rgba(72,48,104,0.96));color:#fff6d7}
  .history-export-note{font-size:11px;color:#8fa3cc;line-height:1.5;padding-right:4px}
  .history-package-panel{display:grid;grid-template-rows:auto auto;gap:12px;min-width:0;max-width:none;height:100%;padding:14px 16px;border-radius:16px;border:1px solid rgba(56,92,148,0.82);background:linear-gradient(160deg,rgba(9,20,39,0.97),rgba(18,34,62,0.93));box-shadow:0 12px 28px rgba(0,0,0,0.18)}
  .history-package-top{display:flex;align-items:flex-start;justify-content:space-between;gap:14px}
  .history-package-copy{min-width:0}
  .history-package-title{font-size:13px;font-weight:700;letter-spacing:.3px;color:#eef4ff}
  .history-package-subtitle{margin-top:4px;font-size:11px;line-height:1.5;color:#8fa3cc}
  .history-package-bottom{display:flex;align-items:center;justify-content:space-between;gap:12px}
  .history-package-options{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;flex:1}
  .history-package-option{display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:12px;border:1px solid rgba(54,82,126,0.82);background:rgba(15,28,48,0.88);color:#dbe8ff;font-size:12px}
  .history-package-option.disabled{opacity:.45}
  .history-package-option input{accent-color:#d7c17a}
  .history-package-actions{display:flex;align-items:center;justify-content:flex-end;gap:10px}
  .history-package-status{font-size:11px;line-height:1.6;color:#9cb2dc;text-align:right;max-width:170px}
  .history-summary-strip{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px;padding:12px 14px;border-radius:14px;border:1px solid rgba(36,66,116,0.88);background:linear-gradient(180deg,rgba(8,16,30,0.95),rgba(13,24,45,0.92))}
  .history-summary-segment{display:inline-flex;align-items:center;gap:6px;max-width:100%;padding:7px 10px;border-radius:999px;border:1px solid rgba(52,88,143,0.68);background:rgba(16,32,61,0.86);color:#dbe8ff;font-size:11px;line-height:1.4}
  .history-summary-segment.wide{flex:1 1 260px}
  .history-summary-segment-label{font-size:10px;color:#8fa3cc;font-weight:700;letter-spacing:1px;text-transform:uppercase;flex-shrink:0}
  .history-summary-segment-value{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .history-runtime-insights{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(300px,1fr);gap:14px;margin-bottom:14px}
  .history-runtime-panel{border:1px solid rgba(36,66,116,0.88);border-radius:14px;background:linear-gradient(180deg,rgba(8,16,30,0.95),rgba(13,24,45,0.92));padding:14px}
  .history-runtime-panel-head{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;margin-bottom:12px}
  .history-runtime-panel-title{font-size:12px;color:#f6e3a5;font-weight:700;letter-spacing:1.6px;text-transform:uppercase}
  .history-runtime-panel-meta{font-size:11px;color:#8fa3cc;line-height:1.6}
  .history-status-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(138px,1fr));gap:10px}
  .history-status-card{display:flex;flex-direction:column;gap:8px;padding:10px 11px;border-radius:12px;border:1px solid rgba(52,88,143,0.68);background:rgba(16,32,61,0.86)}
  .history-status-card.ok{border-color:rgba(62,134,84,0.9);background:linear-gradient(180deg,rgba(16,45,31,0.92),rgba(12,33,24,0.96))}
  .history-status-card.warn{border-color:rgba(149,112,43,0.9);background:linear-gradient(180deg,rgba(54,37,13,0.9),rgba(38,28,13,0.95))}
  .history-status-card.fail{border-color:rgba(138,64,64,0.88);background:linear-gradient(180deg,rgba(51,22,22,0.92),rgba(36,18,18,0.96))}
  .history-status-card.muted{border-color:rgba(63,82,118,0.82);background:linear-gradient(180deg,rgba(18,28,46,0.9),rgba(15,24,38,0.95))}
  .history-status-name{font-size:12px;color:#eef4ff;font-weight:700;line-height:1.4;word-break:break-word}
  .history-status-pill{display:inline-flex;align-items:center;align-self:flex-start;padding:4px 8px;border-radius:999px;border:1px solid currentColor;font-size:10px;line-height:1.2;font-weight:700;letter-spacing:.6px}
  .history-status-pill.ok{color:#8be2a2}
  .history-status-pill.warn{color:#ffd976}
  .history-status-pill.fail{color:#ffb1b1}
  .history-status-pill.muted{color:#b8c8ea}
  .history-status-note{font-size:11px;line-height:1.6;color:#9fb4da}
  .history-status-summary{display:flex;flex-wrap:wrap;gap:8px}
  .history-status-summary-chip{display:inline-flex;align-items:center;gap:6px;padding:5px 8px;border-radius:999px;border:1px solid rgba(52,88,143,0.68);background:rgba(16,32,61,0.86);color:#dbe8ff;font-size:10px;line-height:1.2}
  .history-status-empty{min-height:0;padding:16px 12px;border-radius:12px;border:1px dashed rgba(63,82,118,0.82);background:rgba(13,21,35,0.76);color:#7088b6;font-size:11px;line-height:1.7;text-align:left}
  .history-sections-cards{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}
  .history-card{border:1px solid rgba(27,51,95,0.88);border-radius:12px;background:linear-gradient(180deg,rgba(11,17,32,0.96),rgba(15,27,50,0.9));overflow:hidden}
  .history-card-head{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:14px 16px;cursor:pointer;user-select:none;border-bottom:1px solid rgba(27,51,95,0.6);transition:background .2s}
  .history-card-head:hover{background:rgba(255,255,255,0.03)}
  .history-card-title{font-size:14px;color:#f6e3a5;font-weight:700;letter-spacing:1px}
  .history-card-meta{font-size:11px;color:#7f90b5;flex:1}
  .history-card-toggle{font-size:12px;color:#7f90b5;transition:transform .2s}
  .history-card-toggle.collapsed{transform:rotate(-90deg)}
  .history-card-body{padding:14px}
  .history-card-body.collapsed{display:none}
  .history-section{border:1px solid rgba(27,51,95,0.88);border-radius:12px;background:linear-gradient(180deg,rgba(11,17,32,0.96),rgba(15,27,50,0.9));padding:14px}
  .history-section-head{display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between;margin-bottom:12px}
  .history-section-title{font-size:14px;color:#f6e3a5;font-weight:700;letter-spacing:1px}
  .history-section-meta{font-size:11px;color:#7f90b5}
  .history-script-lines,.history-recording-list,.history-events{display:flex;flex-direction:column;gap:10px}
  .history-script-line,.history-recording-item{border:1px solid rgba(36,66,116,0.84);border-radius:10px;padding:12px;background:rgba(17,25,47,0.82)}
  .history-script-line.note{background:rgba(16,24,40,0.88);border-color:rgba(52,88,143,0.6)}
  .history-script-meta{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:8px;font-size:11px;color:#96a7cb}
  .history-script-speaker{font-size:12px;font-weight:700;color:#ffe19a}
  .history-script-text{font-size:13px;line-height:1.8;color:#eef3ff;white-space:pre-wrap;word-break:break-word}
  .history-script-line.note .history-script-text{color:#bfcae5}
  .history-script-audio{margin-top:10px;width:100%;accent-color:#d4a017}
  .history-script-extra{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
  .history-recording-preview{margin-top:8px;font-size:11px;color:#aab2c8;line-height:1.7;white-space:pre-wrap;word-break:break-word}
  .history-events{display:flex;flex-direction:column;gap:10px}
  .history-event{border:1px solid rgba(27,51,95,0.7);border-radius:10px;padding:12px;background:rgba(17,25,47,0.75)}
  .history-event-head{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:8px;font-size:11px;color:#8b98b7}
  .history-event-body{font-size:12px;line-height:1.7;color:#e5ebff;white-space:pre-wrap;word-break:break-word}
  .history-json{margin-top:8px;padding:10px;border-radius:8px;background:#09101d;border:1px solid rgba(27,51,95,0.7);font-size:11px;color:#9db0db;overflow:auto}
  .history-empty{display:flex;align-items:center;justify-content:center;min-height:220px;padding:16px;color:#5f6f95;font-size:12px;text-align:center}
  .history-retention-note{font-size:11px;color:#93a8d3;line-height:1.6}
  .history-filter-panel{margin-bottom:14px;padding:14px;border-radius:14px;border:1px solid rgba(36,66,116,0.92);background:linear-gradient(180deg,rgba(10,19,35,0.96),rgba(15,28,52,0.88))}
  .history-filter-toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;margin-bottom:12px}
  .history-filter-title{font-size:11px;color:#f6e3a5;font-weight:700;letter-spacing:2px}
  .history-filter-summary{font-size:11px;color:#93a8d3;line-height:1.6}
  .history-filter-clear{border:1px solid #34588f;background:#10203d;color:#dce8ff;padding:7px 12px;border-radius:999px;font-size:11px}
  .history-filter-clear:hover{border-color:#80cbc4;color:#f0fffc}
  .history-filter-clear:disabled{opacity:.42;cursor:default;border-color:#244274;color:#6c7ea5}
  .history-filter-groups{display:flex;flex-direction:column;gap:12px}
  .history-filter-group{display:flex;flex-direction:column;gap:8px}
  .history-filter-label{font-size:10px;color:#7088b6;font-weight:700;letter-spacing:1.8px;text-transform:uppercase}
  .history-filter-chips{display:flex;flex-wrap:wrap;gap:8px}
  .history-filter-chip{display:inline-flex;align-items:center;gap:7px;padding:7px 11px;border-radius:999px;border:1px solid #1c3762;background:#0f1b35;color:#c6d4f4;font-size:11px;cursor:pointer;transition:transform .18s,border-color .18s,background .18s,color .18s}
  .history-filter-chip:hover{transform:translateY(-1px);border-color:#80cbc4;color:#f0fffc}
  .history-filter-chip.active{background:linear-gradient(135deg,rgba(110,75,31,0.92),rgba(38,59,104,0.94));border-color:#d4a017;color:#fff3cb;box-shadow:0 0 0 1px rgba(212,160,23,0.22) inset}
  .history-filter-chip-count{font-size:10px;opacity:.72}
  .history-event-raw{color:#5f6f95}
  .history-event-tags{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 0}
  .history-mini-pill{display:inline-flex;align-items:center;padding:3px 8px;border-radius:999px;border:1px solid rgba(52,88,143,0.68);background:rgba(16,32,61,0.86);color:#cfe0ff;font-size:10px;line-height:1.2}
  .history-mini-pill.active{border-color:#d4a017;background:rgba(80,56,19,0.92);color:#fff1c8}
  .history-empty-note{min-height:160px;flex-direction:column;gap:12px}
  .ops-auth{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:12px}
  .ops-auth input{height:34px;padding:0 10px;border-radius:8px;border:1px solid #1c3762;background:#0f1b35;color:#dce8ff;font-size:12px;min-width:150px}
  .ops-auth input::placeholder{color:#6c7ea5}
  .ops-auth-note{font-size:11px;color:#8fa3cc;line-height:1.6}
  .ops-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-bottom:12px}
  .ops-stat-card{background:#0d0d1a;border:1px solid #0f3460;border-radius:10px;padding:12px}
  .ops-stat-value{font-size:24px;color:#f6e3a5;font-weight:700;line-height:1.2}
  .ops-stat-label{font-size:11px;color:#7f90b5;margin-top:6px}
  .ops-toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:10px}
  .ops-toolbar input{height:34px;padding:0 10px;border-radius:8px;border:1px solid #1c3762;background:#0f1b35;color:#dce8ff;font-size:12px;min-width:220px}
  .ops-toolbar-meta{margin-left:auto;font-size:11px;color:#8fa3cc}
  .ops-table-wrap{background:#0d0d1a;border:1px solid #0f3460;border-radius:10px;overflow:auto}
  .ops-table{width:100%;border-collapse:collapse;min-width:760px}
  .ops-table th{font-size:11px;color:#7f90b5;font-weight:600;padding:10px 12px;text-align:left;border-bottom:1px solid rgba(15,52,96,0.7);background:#11192f}
  .ops-table td{font-size:12px;color:#dce8ff;padding:9px 12px;border-bottom:1px solid rgba(15,52,96,0.38)}
  .ops-table tr:hover td{background:rgba(255,255,255,0.02)}
  .ops-table-actions{display:flex;gap:6px;flex-wrap:wrap}
  .ops-btn-mini{border:1px solid #315488;background:#10203d;color:#dce8ff;padding:4px 8px;border-radius:6px;font-size:11px}
  .ops-btn-mini:hover{border-color:#80cbc4}
  .ops-btn-mini.danger{border-color:#874040;color:#ffb1b1;background:#2b1616}
  .ops-btn-mini.danger:hover{border-color:#ef5350;color:#ffd8d8}
  .ops-table-empty{padding:20px;text-align:center;color:#6c7ea5;font-size:12px}
  @media (max-width: 1320px){
    .history-detail-head{grid-template-columns:1fr}
    .history-package-status{text-align:left;max-width:none}
    .history-package-bottom{flex-direction:column;align-items:stretch}
    .history-package-actions{justify-content:flex-start}
    .history-runtime-insights{grid-template-columns:1fr}
  }
  @media (max-width: 1080px){
    .runtime-split{grid-template-columns:1fr}
    .runtime-col + .runtime-col{border-left:none;border-top:1px solid rgba(15,52,96,0.75);padding-left:0;padding-top:18px}
    .history-panel-head{flex-direction:column;align-items:stretch}
    .history-panel-actions{justify-content:flex-start}
    .history-toolbar{align-items:flex-start}
    .history-toolbar-summary{width:100%}
    .history-session-filters{justify-content:flex-start}
    .history-split{grid-template-columns:1fr}
    .ops-grid{grid-template-columns:1fr}
  }
  .footer{margin-top:24px;color:#444;font-size:12px}
</style>
</head>
<body>
<div class="topbar">
  <div class="tb-left">
    <h1 style="margin:0;font-size:20px">🕯 RoundTable 开发面板</h1>
    <span class="tb-chip"><span class="dot" id="dot-backend"></span><span id="status-backend">检测中…</span></span>
    <span class="tb-chip"><span class="dot" id="dot-flutter"></span><span id="status-flutter">前端检测中…</span></span>
  </div>
</div>
<div class="stack">
  <div class="card" id="card-actions">
    <div class="quick-actions">
      <button class="btn-start" id="btn-start-backend" onclick="ctrl('backend','start')">▶ 启动后端</button>
      <button class="btn-stop" id="btn-stop-backend" onclick="ctrl('backend','stop')" disabled>⏹ 停止后端</button>
      <a class="btn-open" id="quick-link-app" href="${LOCAL_BACKEND_URL}/" target="_blank" rel="noopener">🏠 打开应用</a>
      <a class="btn-open" id="quick-link-admin" href="${LOCAL_BACKEND_URL}/admin/" target="_blank" rel="noopener">🛠 管理后台</a>
      <a class="btn-open" id="quick-link-asr" href="${LOCAL_BACKEND_URL}/browser-asr-test.html" target="_blank" rel="noopener">🎙 ASR 测试</a>
      <a class="btn-open" id="quick-link-docs" href="${LOCAL_BACKEND_URL}/docs" target="_blank" rel="noopener">📚 API 文档</a>
      <a class="btn-open" id="quick-link-topics" href="${LOCAL_BACKEND_URL}/api/v1/topics/" target="_blank" rel="noopener">💬 话题列表</a>
      <a class="btn-open" id="quick-link-thinkers" href="/thinkers" target="_blank" rel="noopener">🧠 思想家</a>
      <button class="btn-open" id="btn-hw-detect" onclick="fetchHardware()" style="cursor:pointer">🖥 检测并打开报告</button>
    </div>
  </div>
  <div class="card" id="card-runtime">
    <div class="runtime-split">
      <section class="runtime-col" id="card-backend">
        <div class="card-title">📟 后端运行日志 (FastAPI :${BACKEND_PORT})<span class="card-title-spacer"></span><button class="btn-danger-subtle" id="btn-clear-backend-log" onclick="clearBackendLog()">🧹 清空</button></div>
        <div class="log-box" id="log-backend"></div>
      </section>
      <section class="runtime-col" id="card-health">
        <div class="card-title">
          ❤ 系统健康检查
          <span class="status-label" id="health-summary">加载中…</span>
          <button class="btn-refresh" onclick="refreshHealth()" style="margin-left:auto">🔄 刷新</button>
        </div>
        <div class="health-grid" id="health-grid">
          <div class="health-empty">加载中…</div>
        </div>
      </section>
    </div>
  </div>
  <div class="card" id="card-ops">
    <div class="card-title">
      📊 用户使用统计（已合并 admin）
      <span class="status-label" id="ops-status">自动连接中…</span>
      <button class="btn-refresh" onclick="refreshAdminStats()" style="margin-left:auto">🔄 刷新</button>
      <button class="btn-open" onclick="openAdminPage()" style="cursor:pointer">↗ 打开 /admin</button>
    </div>
    <div class="ops-auth-note" id="ops-auth-note">本面板已切换为管理员自动鉴权模式，无需手动输入用户名密码。</div>
    <div class="ops-grid">
      <div class="ops-stat-card"><div class="ops-stat-value" id="ops-stat-users">-</div><div class="ops-stat-label">总用户数</div></div>
      <div class="ops-stat-card"><div class="ops-stat-value" id="ops-stat-active">-</div><div class="ops-stat-label">7日活跃</div></div>
      <div class="ops-stat-card"><div class="ops-stat-value" id="ops-stat-sessions">-</div><div class="ops-stat-label">累计场次</div></div>
    </div>
    <div class="ops-toolbar">
      <input id="ops-search" placeholder="搜索用户名或显示名" oninput="renderAdminUsers()">
      <button class="btn-refresh" onclick="exportAdminUsersCsv()">📤 导出 CSV</button>
      <span class="ops-toolbar-meta" id="ops-user-count">未加载用户数据</span>
    </div>
    <div class="ops-table-wrap">
      <table class="ops-table">
        <thead>
          <tr>
            <th>ID</th><th>用户名</th><th>显示名</th><th>注册时间</th><th>最近登录</th><th>场次</th><th>发言</th><th>操作</th>
          </tr>
        </thead>
        <tbody id="ops-user-table">
          <tr><td class="ops-table-empty" colspan="8">正在加载统计数据…</td></tr>
        </tbody>
      </table>
    </div>
  </div>
  <div class="card" id="card-history">
    <div class="card-title history-panel-head">
      <div class="history-panel-title">🗂 历史发言记录板</div>
      <div class="history-panel-actions">
        <button class="history-export-btn" onclick="toggleSelectAllMeetingHistories()" id="btn-select-all-history" disabled>☑ 全选</button>
        <button class="history-export-btn alt" onclick="clearSelectedMeetingHistories()" id="btn-clear-history-selection" disabled>✖ 清空选择</button>
        <details class="history-dropdown disabled" id="history-export-dropdown">
          <summary class="history-export-btn">📤 导出</summary>
          <div class="history-dropdown-menu">
            <button type="button" class="history-dropdown-item" onclick="closeHistoryExportMenu();downloadMeetingScriptExport(&quot;markdown&quot;)">Markdown 剧本</button>
            <button type="button" class="history-dropdown-item" onclick="closeHistoryExportMenu();downloadMeetingScriptExport(&quot;json&quot;)">JSON 剧本</button>
          </div>
        </details>
        <button class="btn-refresh" onclick="refreshMeetingHistory()">🔄 刷新</button>
        <button class="btn-danger-subtle" id="btn-delete-history" onclick="deleteActiveMeetingHistory()" disabled>🗑 删除</button>
        <button class="btn-danger-subtle" id="btn-delete-history-selected" onclick="deleteSelectedMeetingHistories()" disabled>🗑 删除所选</button>
      </div>
    </div>
    <div class="history-toolbar">
      <div class="history-toolbar-summary"><span class="status-label" id="history-summary">加载中…</span></div>
      <div class="history-session-filters">
        <label class="history-filter-field"><span>话题</span><input id="history-filter-topic" type="text" placeholder="包含关键词" oninput="applyMeetingHistorySessionFilters()"></label>
        <label class="history-filter-field"><span>起始</span><input id="history-filter-time-from" type="datetime-local" onchange="applyMeetingHistorySessionFilters()"></label>
        <label class="history-filter-field"><span>截止</span><input id="history-filter-time-to" type="datetime-local" onchange="applyMeetingHistorySessionFilters()"></label>
        <label class="history-filter-field"><span>状态</span><select id="history-filter-status" onchange="applyMeetingHistorySessionFilters()"><option value="__all__">全部状态</option></select></label>
        <label class="history-filter-field history-filter-field-compact"><span>剧本</span><select id="history-filter-script-op" onchange="applyMeetingHistorySessionFilters()"><option value="__all__">全部</option><option value="gt">大于</option><option value="lt">小于</option><option value="eq">等于</option></select><input id="history-filter-script-value" type="number" min="0" placeholder="行数" oninput="applyMeetingHistorySessionFilters()"></label>
        <label class="history-filter-field"><span>用户</span><input id="history-filter-user" type="text" placeholder="用户名或昵称" oninput="applyMeetingHistorySessionFilters()"></label>
        <button type="button" class="history-filter-clear" id="btn-clear-history-filters" onclick="clearMeetingHistorySessionFilters()" disabled>重置筛选</button>
      </div>
    </div>
    <div class="history-split">
      <aside class="history-list" id="history-list">
        <div class="history-empty">正在读取会议历史…</div>
      </aside>
      <section class="history-detail" id="history-detail">
        <div class="history-empty">选择左侧一场会议以查看完整时间线。</div>
      </section>
    </div>
  </div>
  <div class="card" id="card-flutter">
    <div class="card-title">🎨 前端 Flutter Web</div>
    <div class="monitor-info" id="flutter-monitor">
      <div><span class="label">部署方式:</span> <span class="val">内嵌到后端 :${BACKEND_PORT}</span></div>
      <div><span class="label">访问地址:</span> <a id="flutter-app-link" href="${LOCAL_BACKEND_URL}/" target="_blank" rel="noopener" style="color:#d4a017">${LOCAL_BACKEND_URL}/</a></div>
      <div><span class="label">静态文件:</span> <span class="val" id="flutter-static">检测中…</span></div>
      <div><span class="label">运行状态:</span> <span class="val" id="flutter-status-detail">检测中…</span></div>
      <div style="margin-top:10px;color:#666;font-size:11px">
        💡 Flutter Web 已编译为静态文件，由后端 FastAPI 提供服务。如需更新前端，运行 <code style="color:#d4a017">./build_web.sh</code>
      </div>
    </div>
  </div>
  <div class="card" id="card-hardware">
    <div class="card-title">
      🖥 硬件检测与优化
    </div>
    <span class="hw-opt" id="hw-opt">点击“检测并打开报告”以读取当前主机配置，并确认已应用的运行时优化。</span>
    <div class="hw-info" id="hw-info" style="display:none;margin-top:10px">
      <span class="hw-chip" id="hw-chip"></span>
      <span class="hw-sep">|</span>
      <span id="hw-cpu"></span>
      <span class="hw-sep">|</span>
      <span id="hw-mem"></span>
      <span class="hw-sep">|</span>
      <span id="hw-gpu"></span>
    </div>
  </div>
</div>
<div class="footer">RoundTable Dev Panel · 使用 <kbd>Ctrl+C</kbd> 停止面板</div>
<script>
var DEV_PANEL_PORT = ${JSON.stringify(DEV_PANEL_PORT)};
var BACKEND_PORT = ${JSON.stringify(BACKEND_PORT)};
var MEETING_HISTORY_RETENTION_LIMIT = ${MEETING_HISTORY_RETENTION_LIMIT};
function currentOpsToken() {
  try {
    var params = new URLSearchParams(window.location.search || '');
    return String(params.get('token') || '').trim();
  } catch (_) {
    return '';
  }
}

function withOpsToken(pathname) {
  var raw = String(pathname || '/');
  var token = currentOpsToken();
  if (!token) return raw;
  var parts = raw.split('#');
  var hash = parts.length > 1 ? '#' + parts.slice(1).join('#') : '';
  var pathAndQuery = parts[0];
  var queryIndex = pathAndQuery.indexOf('?');
  var basePath = queryIndex >= 0 ? pathAndQuery.slice(0, queryIndex) : pathAndQuery;
  var query = queryIndex >= 0 ? pathAndQuery.slice(queryIndex + 1) : '';
  var params = new URLSearchParams(query);
  if (!params.get('token')) params.set('token', token);
  var finalQuery = params.toString();
  return basePath + (finalQuery ? ('?' + finalQuery) : '') + hash;
}

function panelApiPath(pathname) {
  var suffix = String(pathname || '/');
  if (!suffix.startsWith('/')) suffix = '/' + suffix;
  var currentPath = window.location.pathname || '/';
  if (currentPath === '/ops' || currentPath.startsWith('/ops/')) {
    if (suffix === '/ops' || suffix.startsWith('/ops/')) return withOpsToken(suffix);
    return withOpsToken('/ops' + suffix);
  }
  return suffix;
}
function panelFetch(pathname, options) {
  return fetch(panelApiPath(pathname), options || {});
}
function panelPagePath(pathname) {
  var suffix = String(pathname || '/');
  if (!suffix.startsWith('/')) suffix = '/' + suffix;
  return withOpsToken(suffix);
}
function ctrl(svc, action) {
  panelFetch('/api/' + action + '/' + svc, {method:'POST'})
    .then(function(r){return r.json()}).then(function(d){
      console.log(d);
      if (!d.ok && d.msg) showToast(d.msg);
    });
}
function showToast(msg) {
  var t = document.createElement('div');
  t.style.cssText = 'position:fixed;top:20px;right:20px;background:#ef5350;color:#fff;padding:12px 20px;border-radius:8px;z-index:999;font-size:13px';
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(function(){ t.remove(); }, 3000);
}
function clearBackendLog() {
  var box = document.getElementById('log-backend');
  if (box) box.innerHTML = '';
  var btn = document.getElementById('btn-clear-backend-log');
  if (btn) btn.disabled = true;
  panelFetch('/api/log/backend/clear', {method:'POST'})
    .then(function(r){ return r.json(); })
    .then(function(d){
      if (!d.ok) {
        showToast(d.msg || '清空后端日志失败');
      }
    })
    .catch(function(){
      showToast('清空后端日志失败');
    })
    .finally(function(){
      if (btn) btn.disabled = false;
    });
}
var box = document.getElementById('log-backend');
var es = new EventSource(panelApiPath('/log/backend'));
es.onmessage = function(e) {
  var d = JSON.parse(e.data);
  var div = document.createElement('div');
  div.className = 'log-line' + (d.line.indexOf('ERROR') >= 0 || d.line.indexOf('Error') >= 0 ? ' err' : d.line.charAt(0) === '▶' || d.line.charAt(0) === '✅' ? ' info' : '');
  div.textContent = d.t + ' ' + d.line;
  box.appendChild(div);
  if (box.children.length > 200) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
};
es.addEventListener('status', function(e) {
  var d = JSON.parse(e.data);
  updateBackendStatus(d.backend);
  updateFlutterStatus(d.flutter);
});
function updateBackendStatus(s) {
  var running = s.running;
  document.getElementById('dot-backend').className = 'dot' + (running ? ' running' : '');
  document.getElementById('status-backend').className = 'status-label' + (running ? ' running' : '');
  document.getElementById('status-backend').textContent = running ? (s.managed ? '运行中 (面板管理)' : '运行中 (外部启动)') : '已停止';
  document.getElementById('btn-start-backend').disabled = running;
  document.getElementById('btn-stop-backend').disabled = !running;
}
function updateFlutterStatus(s) {
  var ok = s.embedded && s.backendRunning;
  document.getElementById('dot-flutter').className = 'dot' + (ok ? ' running' : s.embedded ? ' warn' : '');
  document.getElementById('status-flutter').className = 'status-label' + (ok ? ' running' : '');
  document.getElementById('status-flutter').textContent = ok ? '正常运行' : s.embedded ? '后端未启动' : '未部署';
  document.getElementById('flutter-static').className = 'val' + (s.embedded ? ' ok' : ' warn');
  document.getElementById('flutter-static').textContent = s.embedded ? '✓ 已部署 (backend/static/)' : '✗ 未找到静态文件';
  document.getElementById('flutter-status-detail').className = 'val' + (ok ? ' ok' : ' warn');
  document.getElementById('flutter-status-detail').textContent = ok ? '✓ 通过当前后端入口正常提供服务' : s.embedded ? '⚠ 后端未启动，无法访问' : '⚠ 请先构建前端';
}
panelFetch('/api/status').then(function(r){return r.json()}).then(function(d) {
  updateBackendStatus(d.backend);
  updateFlutterStatus(d.flutter);
});
function refreshHealth() {
  var grid = document.getElementById('health-grid');
  grid.innerHTML = '<div class="health-empty">检查中…</div>';
  document.getElementById('health-summary').textContent = '检查中…';
  panelFetch('/api/health')
    .then(function(r){return r.json()})
    .then(renderHealth)
    .catch(function(err) {
      grid.innerHTML = '<div class="health-empty">健康检查失败：' + escHtml((err && err.message) || 'unknown') + '</div>';
      var summary = document.getElementById('health-summary');
      summary.textContent = '检查失败';
      summary.style.color = '#ef5350';
    });
}
function renderHealth(data) {
  var grid = document.getElementById('health-grid');
  grid.innerHTML = '';
  var total = 0, ok = 0;
  for (var key in data) {
    if (!data.hasOwnProperty(key)) continue;
    var svc = data[key];
    total++;
    if (svc.reachable) ok++;
    var row = document.createElement('div');
    row.className = 'health-row';
    row.innerHTML =
      '<div class="health-dot ' + (svc.reachable ? 'ok' : 'fail') + '"></div>' +
      '<div class="health-name">' + escHtml(svc.name) + '</div>' +
      '<div class="health-url">' + escHtml(svc.url) + '</div>' +
      '<div class="health-detail ' + (svc.reachable ? 'ok' : 'fail') + '">' + escHtml(svc.detail) + '</div>';
    grid.appendChild(row);
  }
  if (total === 0) {
    grid.innerHTML = '<div class="health-empty">当前配置没有需要后端检查的语音或本地模型服务。</div>';
  }
  var summary = document.getElementById('health-summary');
  summary.textContent = total > 0 ? (ok + '/' + total + ' 服务正常') : '无需检查';
  summary.style.color = total === 0 ? '#80cbc4' : ok === total ? '#4caf50' : ok > 0 ? '#ff9800' : '#ef5350';
}
function escHtml(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function escAttr(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}
var adminUsers = [];
var adminStats = null;

function backendOrigin() {
  var protocol = window.location.protocol || 'http:';
  var hostname = window.location.hostname || '127.0.0.1';
  var host = window.location.host || '';
  if (hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1') {
    return protocol + '//' + hostname + ':' + String(BACKEND_PORT);
  }
  if (host) {
    return protocol + '//' + host;
  }
  return protocol + '//' + hostname;
}

function backendUrl(pathname) {
  var suffix = String(pathname || '/');
  if (!suffix.startsWith('/')) suffix = '/' + suffix;
  return backendOrigin() + suffix;
}

function applyQuickLinks() {
  var mappings = [
    ['quick-link-app', '/'],
    ['quick-link-admin', '/admin/'],
    ['quick-link-asr', '/browser-asr-test.html'],
    ['quick-link-docs', '/docs'],
    ['quick-link-topics', '/api/v1/topics/'],
  ];
  mappings.forEach(function(item) {
    var el = document.getElementById(item[0]);
    if (!el) return;
    el.href = backendUrl(item[1]);
  });
  var thinkersLink = document.getElementById('quick-link-thinkers');
  if (thinkersLink) thinkersLink.href = panelPagePath('/thinkers');

  var flutterLink = document.getElementById('flutter-app-link');
  if (flutterLink) {
    flutterLink.href = backendUrl('/');
    flutterLink.textContent = backendUrl('/');
  }
}

function openAdminPage() {
  var targetUrl = backendUrl('/admin/');
  var opened = null;
  try {
    opened = window.open(targetUrl, '_blank', 'noopener');
  } catch (_) {
    opened = null;
  }
  if (!opened) {
    window.location.href = targetUrl;
  }
}

function setOpsStatus(text, color) {
  var el = document.getElementById('ops-status');
  if (!el) return;
  el.textContent = text;
  if (color) el.style.color = color;
}

function proxyAdminFetch(path, opts) {
  var options = opts || {};
  var headers = {};
  var key;
  if (options.headers) {
    for (key in options.headers) {
      if (Object.prototype.hasOwnProperty.call(options.headers, key)) {
        headers[key] = options.headers[key];
      }
    }
  }
  return panelFetch(path, Object.assign({}, options, { headers: headers }))
    .then(function(r) {
      return r.text().then(function(text) {
        var payload = {};
        if (text) {
          try {
            payload = JSON.parse(text);
          } catch (_) {
            payload = { detail: text };
          }
        }
        if (!r.ok) {
          var msg = payload.detail || payload.error || ('HTTP ' + r.status);
          var err = new Error(msg);
          err.status = r.status;
          throw err;
        }
        return payload;
      });
    });
}

function formatAdminDate(value) {
  if (!value) return '从未';
  var date = new Date(value);
  if (isNaN(date.getTime())) return String(value);
  return date.toLocaleString('zh-CN', { hour12: false });
}

function formatAdminDuration(ms) {
  var num = Number(ms || 0);
  if (!isFinite(num) || num <= 0) return '0 秒';
  if (num < 60000) return Math.round(num / 1000) + ' 秒';
  if (num < 3600000) return Math.round(num / 60000) + ' 分钟';
  return (num / 3600000).toFixed(1) + ' 小时';
}

function updateAdminStatCards(stats) {
  var payload = stats || {};
  document.getElementById('ops-stat-users').textContent = payload.total_users || 0;
  document.getElementById('ops-stat-active').textContent = payload.active_users_7d || 0;
  document.getElementById('ops-stat-sessions').textContent = payload.total_sessions || 0;
}

function refreshAdminStats() {
  setOpsStatus('加载中…', '#80cbc4');
  document.getElementById('ops-auth-note').textContent = '正在自动连接管理员统计通道…';
  Promise.all([
    proxyAdminFetch('/api/admin/stats'),
    proxyAdminFetch('/api/admin/users?limit=500'),
  ])
    .then(function(results) {
      adminStats = results[0] || {};
      adminUsers = (results[1] && results[1].users) || [];
      updateAdminStatCards(adminStats);
      renderAdminUsers();
      if (allMeetingHistoryItems.length) {
        refreshMeetingHistoryStatusFilterOptions(allMeetingHistoryItems);
        renderMeetingHistoryList(allMeetingHistoryItems);
      }
      setOpsStatus('已连接', '#4caf50');
      document.getElementById('ops-auth-note').textContent = '统计已更新：' + new Date().toLocaleTimeString('zh-CN', { hour12: false });
    })
    .catch(function(err) {
      setOpsStatus('加载失败', '#ef5350');
      document.getElementById('ops-auth-note').textContent = '统计加载失败（管理员自动鉴权失败）：' + ((err && err.message) || 'unknown');
      showToast('统计加载失败：' + ((err && err.message) || 'unknown'));
    });
}

function renderAdminUsers() {
  var tbody = document.getElementById('ops-user-table');
  var searchValue = String(document.getElementById('ops-search').value || '').toLowerCase();
  var filtered = searchValue
    ? adminUsers.filter(function(user) {
        return String(user.username || '').toLowerCase().indexOf(searchValue) >= 0 ||
          String(user.display_name || '').toLowerCase().indexOf(searchValue) >= 0;
      })
    : adminUsers.slice();

  document.getElementById('ops-user-count').textContent = adminUsers.length
    ? ('共 ' + filtered.length + ' / ' + adminUsers.length + ' 名用户')
    : '暂无用户数据';

  if (!filtered.length) {
    tbody.innerHTML = '<tr><td class="ops-table-empty" colspan="8">没有匹配的用户。</td></tr>';
    return;
  }

  tbody.innerHTML = filtered.map(function(user) {
    return '' +
      '<tr>' +
        '<td>' + escHtml(String(user.id || '-')) + '</td>' +
        '<td>' + escHtml(user.username || '-') + '</td>' +
        '<td>' + escHtml(user.display_name || '-') + '</td>' +
        '<td>' + escHtml(formatAdminDate(user.created_at)) + '</td>' +
        '<td>' + escHtml(formatAdminDate(user.last_login)) + '</td>' +
        '<td>' + escHtml(String(user.session_count || 0)) + '</td>' +
        '<td>' + escHtml(String(user.total_speech_count || 0)) + '</td>' +
        '<td><div class="ops-table-actions">' +
          '<button class="ops-btn-mini" onclick="showAdminUserDetail(' + Number(user.id || 0) + ')">详情</button>' +
          '<button class="ops-btn-mini danger" onclick="deleteAdminUser(' + Number(user.id || 0) + ')">删除</button>' +
        '</div></td>' +
      '</tr>';
  }).join('');
}

function showAdminUserDetail(userId) {
  proxyAdminFetch('/api/admin/users/' + encodeURIComponent(String(userId)))
    .then(function(payload) {
      var user = payload.user || {};
      var events = payload.events || [];
      var recentEvents = events.slice(0, 8).map(function(item) {
        return '- [' + formatAdminDate(item.created_at) + '] ' + (item.event_type || 'event') + (item.event_data ? ' ｜ ' + item.event_data : '');
      });
      var lines = [
        '用户详情',
        'ID: ' + (user.id || '-'),
        '用户名: ' + (user.username || '-'),
        '显示名: ' + (user.display_name || '-'),
        '注册时间: ' + formatAdminDate(user.created_at),
        '最近登录: ' + formatAdminDate(user.last_login),
        '讨论场次: ' + (user.session_count || 0),
        '发言次数: ' + (user.total_speech_count || 0),
        '在线时长: ' + formatAdminDuration(user.total_online_ms || 0),
        '',
        '最近事件:',
      ];
      if (recentEvents.length) {
        lines = lines.concat(recentEvents);
      } else {
        lines.push('- 暂无事件');
      }
      window.alert(lines.join('\\n'));
    })
    .catch(function(err) {
      showToast('用户详情加载失败：' + ((err && err.message) || 'unknown'));
    });
}

function deleteAdminUser(userId) {
  var safeName = 'ID ' + String(userId || '-');
  for (var idx = 0; idx < adminUsers.length; idx++) {
    if (Number(adminUsers[idx].id || 0) === Number(userId || 0)) {
      safeName = String(adminUsers[idx].username || safeName);
      break;
    }
  }
  if (!window.confirm('确认删除用户 "' + safeName + '"？此操作不可撤销。')) return;
  proxyAdminFetch('/api/admin/users/' + encodeURIComponent(String(userId)), { method: 'DELETE' })
    .then(function() {
      showToast('已删除用户：' + safeName);
      refreshAdminStats();
    })
    .catch(function(err) {
      showToast('删除失败：' + ((err && err.message) || 'unknown'));
    });
}

function exportAdminUsersCsv() {
  if (!adminUsers.length) {
    showToast('当前没有可导出的用户数据');
    return;
  }
  var header = 'ID,用户名,显示名,注册时间,最近登录,场次,发言次数,在线时长(ms)\\n';
  var rows = adminUsers.map(function(user) {
    return [
      user.id,
      user.username,
      user.display_name,
      user.created_at,
      user.last_login,
      user.session_count,
      user.total_speech_count,
      user.total_online_ms,
    ].map(function(value) {
      return '"' + String(value == null ? '' : value).replace(/"/g, '""') + '"';
    }).join(',');
  }).join('\\n');
  var blob = new Blob(['﻿' + header + rows], { type: 'text/csv;charset=utf-8' });
  var anchor = document.createElement('a');
  anchor.href = URL.createObjectURL(blob);
  anchor.download = 'roundtable_users.csv';
  anchor.click();
  URL.revokeObjectURL(anchor.href);
}

function initAdminOps() {
  setOpsStatus('自动连接中…', '#80cbc4');
  updateAdminStatCards({});
  refreshAdminStats();
}

refreshHealth();
initAdminOps();
applyQuickLinks();

var activeMeetingHistoryId = '';
var currentMeetingHistoryRecord = null;
var allMeetingHistoryItems = [];
var selectedMeetingHistoryIds = new Set();
var ALL_MEETING_FILTER_VALUE = '__all__';
var meetingHistoryFilters = {
  speaker: ALL_MEETING_FILTER_VALUE,
  eventType: ALL_MEETING_FILTER_VALUE,
};
var meetingHistorySessionFilters = {
  topicQuery: '',
  timeFrom: '',
  timeTo: '',
  status: ALL_MEETING_FILTER_VALUE,
  scriptComparator: ALL_MEETING_FILTER_VALUE,
  scriptLineCount: '',
  userQuery: '',
};

function resetMeetingHistoryFilters() {
  meetingHistoryFilters = {
    speaker: ALL_MEETING_FILTER_VALUE,
    eventType: ALL_MEETING_FILTER_VALUE,
  };
}

function resetMeetingHistorySessionFilters() {
  meetingHistorySessionFilters = {
    topicQuery: '',
    timeFrom: '',
    timeTo: '',
    status: ALL_MEETING_FILTER_VALUE,
    scriptComparator: ALL_MEETING_FILTER_VALUE,
    scriptLineCount: '',
    userQuery: '',
  };
}

function hasActiveMeetingHistorySessionFilters() {
  return !!(
    meetingHistorySessionFilters.topicQuery ||
    meetingHistorySessionFilters.timeFrom ||
    meetingHistorySessionFilters.timeTo ||
    (meetingHistorySessionFilters.status && meetingHistorySessionFilters.status !== ALL_MEETING_FILTER_VALUE) ||
    (meetingHistorySessionFilters.scriptComparator && meetingHistorySessionFilters.scriptComparator !== ALL_MEETING_FILTER_VALUE && meetingHistorySessionFilters.scriptLineCount) ||
    meetingHistorySessionFilters.userQuery
  );
}

function syncMeetingHistorySessionFilterClearButton() {
  var clearBtn = document.getElementById('btn-clear-history-filters');
  if (clearBtn) clearBtn.disabled = !hasActiveMeetingHistorySessionFilters();
}

function syncMeetingHistorySessionFilterControls() {
  var controlValues = {
    'history-filter-topic': meetingHistorySessionFilters.topicQuery,
    'history-filter-time-from': meetingHistorySessionFilters.timeFrom,
    'history-filter-time-to': meetingHistorySessionFilters.timeTo,
    'history-filter-status': meetingHistorySessionFilters.status,
    'history-filter-script-op': meetingHistorySessionFilters.scriptComparator,
    'history-filter-script-value': meetingHistorySessionFilters.scriptLineCount,
    'history-filter-user': meetingHistorySessionFilters.userQuery,
  };
  Object.keys(controlValues).forEach(function(controlId) {
    var node = document.getElementById(controlId);
    if (node) node.value = controlValues[controlId];
  });
  syncMeetingHistorySessionFilterClearButton();
}

function refreshMeetingHistoryStatusFilterOptions(items) {
  var select = document.getElementById('history-filter-status');
  if (!select) return;
  var statuses = Array.from(new Set((Array.isArray(items) ? items : []).map(function(item) {
    return String(item && item.status || '').trim();
  }).filter(Boolean))).sort();
  var currentValue = meetingHistorySessionFilters.status || ALL_MEETING_FILTER_VALUE;
  select.innerHTML = '<option value="' + ALL_MEETING_FILTER_VALUE + '">全部状态</option>' + statuses.map(function(status) {
    return '<option value="' + escAttr(status) + '">' + escHtml(meetingStatusLabel(status)) + '</option>';
  }).join('');
  if (statuses.indexOf(currentValue) < 0) {
    meetingHistorySessionFilters.status = ALL_MEETING_FILTER_VALUE;
    currentValue = ALL_MEETING_FILTER_VALUE;
  }
  select.value = currentValue;
}

function normalizeMeetingSearchValue(value) {
  return String(value || '').trim().toLowerCase();
}

function addMeetingHistorySearchToken(target, value) {
  var token = String(value || '').trim();
  if (!token) return;
  target.add(token);
}

function collectMeetingHistoryUserTokens(item) {
  var tokens = new Set();
  var summary = item && typeof item === 'object' ? item : {};
  var config = summary.config && typeof summary.config === 'object' ? summary.config : {};
  ['username', 'user_name', 'display_name', 'nickname', 'owner_username', 'owner_display_name'].forEach(function(key) {
    addMeetingHistorySearchToken(tokens, summary[key]);
    addMeetingHistorySearchToken(tokens, config[key]);
  });
  var userIds = [];
  if (summary.user_id != null) userIds.push(Number(summary.user_id));
  if (config.user_id != null) userIds.push(Number(config.user_id));
  userIds.forEach(function(userId) {
    if (!isFinite(userId) || userId <= 0) return;
    adminUsers.forEach(function(user) {
      if (Number(user.id || 0) === userId) {
        addMeetingHistorySearchToken(tokens, user.username);
        addMeetingHistorySearchToken(tokens, user.display_name);
      }
    });
  });
  var humanNames = Array.isArray(config.human_names) ? config.human_names.slice() : [];
  humanNames.forEach(function(name) {
    addMeetingHistorySearchToken(tokens, name);
  });
  if (!humanNames.length && Array.isArray(summary.participants)) {
    summary.participants.forEach(function(name) {
      addMeetingHistorySearchToken(tokens, name);
    });
  }
  var normalizedHumanNames = humanNames.map(normalizeMeetingSearchValue).filter(Boolean);
  if (normalizedHumanNames.length) {
    adminUsers.forEach(function(user) {
      var displayName = normalizeMeetingSearchValue(user.display_name);
      if (displayName && normalizedHumanNames.indexOf(displayName) >= 0) {
        addMeetingHistorySearchToken(tokens, user.username);
        addMeetingHistorySearchToken(tokens, user.display_name);
      }
    });
  }
  return Array.from(tokens);
}

function buildMeetingHistoryTopicSearchText(item) {
  var summary = item && typeof item === 'object' ? item : {};
  var topic = summary.topic && typeof summary.topic === 'object' ? summary.topic : {};
  var config = summary.config && typeof summary.config === 'object' ? summary.config : {};
  return [
    summary.session_id,
    topic.title,
    topic.description,
    topic.category,
    config.free_topic,
    config.free_topic_detail,
  ].map(function(value) {
    return String(value || '').trim();
  }).filter(Boolean).join(' ');
}

function meetingHistoryIntersectsTimeRange(item) {
  var from = parseMeetingFilterDate(meetingHistorySessionFilters.timeFrom);
  var to = parseMeetingFilterDate(meetingHistorySessionFilters.timeTo);
  if (!from && !to) return true;
  if (from && to && from.getTime() > to.getTime()) {
    var temp = from;
    from = to;
    to = temp;
  }
  var start = parseMeetingFilterDate(item && (item.started_at || item.updated_at || item.ended_at));
  var end = parseMeetingFilterDate(item && (item.ended_at || item.updated_at || item.started_at));
  if (!start && !end) return false;
  var lower = start || end;
  var upper = end || start;
  if (from && upper && upper.getTime() < from.getTime()) return false;
  if (to && lower && lower.getTime() > to.getTime()) return false;
  return true;
}

function meetingHistoryMatchesSessionFilters(item) {
  var topicQuery = normalizeMeetingSearchValue(meetingHistorySessionFilters.topicQuery);
  if (topicQuery && normalizeMeetingSearchValue(buildMeetingHistoryTopicSearchText(item)).indexOf(topicQuery) < 0) {
    return false;
  }
  if (!meetingHistoryIntersectsTimeRange(item)) {
    return false;
  }
  if (meetingHistorySessionFilters.status !== ALL_MEETING_FILTER_VALUE) {
    if (String(item && item.status || '').trim() !== meetingHistorySessionFilters.status) {
      return false;
    }
  }
  if (meetingHistorySessionFilters.scriptComparator !== ALL_MEETING_FILTER_VALUE && meetingHistorySessionFilters.scriptLineCount) {
    var scriptCount = Number(item && item.script_line_count || 0);
    var compareValue = Number(meetingHistorySessionFilters.scriptLineCount);
    if (isFinite(compareValue) && compareValue >= 0) {
      if (meetingHistorySessionFilters.scriptComparator === 'gt' && !(scriptCount > compareValue)) return false;
      if (meetingHistorySessionFilters.scriptComparator === 'lt' && !(scriptCount < compareValue)) return false;
      if (meetingHistorySessionFilters.scriptComparator === 'eq' && !(scriptCount === compareValue)) return false;
    }
  }
  var userQuery = normalizeMeetingSearchValue(meetingHistorySessionFilters.userQuery);
  if (userQuery) {
    var userSearchText = normalizeMeetingSearchValue(collectMeetingHistoryUserTokens(item).join(' '));
    if (!userSearchText || userSearchText.indexOf(userQuery) < 0) {
      return false;
    }
  }
  return true;
}

function getFilteredMeetingHistoryItems(items) {
  return (Array.isArray(items) ? items : []).filter(meetingHistoryMatchesSessionFilters);
}

function buildMeetingHistorySummaryText(totalCount, visibleCount) {
  if (!totalCount) return '暂无记录';
  var parts = [totalCount + ' 场会议'];
  if (typeof visibleCount === 'number' && visibleCount >= 0 && visibleCount !== totalCount) {
    parts.push('显示 ' + visibleCount + ' 场');
  }
  parts.push('已选 ' + selectedMeetingHistoryIds.size + ' 场');
  return parts.join(' · ');
}

function closeHistoryExportMenu() {
  var dropdown = document.getElementById('history-export-dropdown');
  if (dropdown) dropdown.removeAttribute('open');
}

function parseMeetingFilterDate(value) {
  var raw = String(value || '').trim();
  if (!raw) return null;
  var parsed = new Date(raw);
  if (isNaN(parsed.getTime())) return null;
  return parsed;
}

function applyMeetingHistorySessionFilters() {
  var topicInput = document.getElementById('history-filter-topic');
  var timeFromInput = document.getElementById('history-filter-time-from');
  var timeToInput = document.getElementById('history-filter-time-to');
  var statusInput = document.getElementById('history-filter-status');
  var scriptOpInput = document.getElementById('history-filter-script-op');
  var scriptValueInput = document.getElementById('history-filter-script-value');
  var userInput = document.getElementById('history-filter-user');
  var previousActiveId = activeMeetingHistoryId;
  meetingHistorySessionFilters = {
    topicQuery: String(topicInput && topicInput.value || '').trim(),
    timeFrom: String(timeFromInput && timeFromInput.value || '').trim(),
    timeTo: String(timeToInput && timeToInput.value || '').trim(),
    status: String(statusInput && statusInput.value || ALL_MEETING_FILTER_VALUE).trim() || ALL_MEETING_FILTER_VALUE,
    scriptComparator: String(scriptOpInput && scriptOpInput.value || ALL_MEETING_FILTER_VALUE).trim() || ALL_MEETING_FILTER_VALUE,
    scriptLineCount: String(scriptValueInput && scriptValueInput.value || '').trim(),
    userQuery: String(userInput && userInput.value || '').trim(),
  };
  syncMeetingHistorySessionFilterClearButton();
  var visibleItems = renderMeetingHistoryList(allMeetingHistoryItems);
  if (visibleItems.length && previousActiveId !== activeMeetingHistoryId) {
    openMeetingHistory(activeMeetingHistoryId, true);
  }
}

function clearMeetingHistorySessionFilters() {
  resetMeetingHistorySessionFilters();
  syncMeetingHistorySessionFilterControls();
  var previousActiveId = activeMeetingHistoryId;
  var visibleItems = renderMeetingHistoryList(allMeetingHistoryItems);
  if (visibleItems.length && previousActiveId !== activeMeetingHistoryId) {
    openMeetingHistory(activeMeetingHistoryId, true);
  }
}

function formatMeetingTime(value) {
  if (!value) return '-';
  var date = new Date(value);
  if (isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
}

function formatMeetingTimeCompact(value) {
  if (!value) return '-';
  var date = new Date(value);
  if (isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function formatMeetingDurationMs(value) {
  var duration = Number(value || 0);
  if (!isFinite(duration) || duration <= 0) return '-';
  if (duration < 1000) return Math.round(duration) + ' ms';
  var totalSeconds = duration / 1000;
  if (totalSeconds < 60) {
    return (totalSeconds >= 10 ? totalSeconds.toFixed(0) : totalSeconds.toFixed(1)) + ' 秒';
  }
  var minutes = Math.floor(totalSeconds / 60);
  var seconds = Math.round(totalSeconds % 60);
  if (minutes < 60) return minutes + ' 分 ' + seconds + ' 秒';
  var hours = Math.floor(minutes / 60);
  minutes = minutes % 60;
  return hours + ' 小时 ' + minutes + ' 分';
}

function formatMeetingBytes(value) {
  var size = Number(value || 0);
  if (!isFinite(size) || size <= 0) return '-';
  if (size < 1024) return Math.round(size) + ' B';
  if (size < 1024 * 1024) return (size / 1024).toFixed(1) + ' KB';
  return (size / (1024 * 1024)).toFixed(1) + ' MB';
}

function meetingStatusClass(status) {
  if (status === 'completed') return 'ok';
  if (status === 'running' || status === 'disconnected') return 'warn';
  return 'fail';
}

function meetingStatusLabel(status) {
  if (status === 'completed') return '已完成';
  if (status === 'running') return '进行中';
  if (status === 'disconnected') return '连接断开';
  if (status === 'error') return '异常结束';
  return status || '未知状态';
}

function meetingDirectionLabel(direction) {
  if (direction === 'outbound') return '后端→前端';
  if (direction === 'inbound') return '前端→后端';
  if (direction === 'internal') return '内部记录';
  return direction || 'unknown';
}

function meetingUtteranceStatusLabel(status) {
  var key = String(status || '').trim();
  var labels = {
    nominated_only: '仅被点名',
    skipped: '主动跳过',
    timed_out: '等待超时',
    spoke_with_content: '已实质发言',
    spoke_empty: '空发言/无效输入',
  };
  return labels[key] || (key || '未知');
}

function meetingUtteranceStatusTone(status) {
  var key = String(status || '').trim();
  if (key === 'spoke_with_content') return 'ok';
  if (key === 'nominated_only' || key === 'timed_out') return 'warn';
  if (key === 'skipped') return 'muted';
  if (key === 'spoke_empty') return 'fail';
  return 'muted';
}

function collectMeetingUtteranceStatuses(summary) {
  var floorManagerStats = summary && summary.final_stats && summary.final_stats.floor_manager
    ? summary.final_stats.floor_manager
    : null;
  var rawStatuses = floorManagerStats && floorManagerStats.speaker_utterance_statuses && typeof floorManagerStats.speaker_utterance_statuses === 'object'
    ? floorManagerStats.speaker_utterance_statuses
    : null;
  if (!rawStatuses) return [];

  var preferredOrder = Array.isArray(summary && summary.participants) ? summary.participants.slice() : [];
  var seen = {};
  var orderedNames = [];
  preferredOrder.forEach(function(name) {
    var normalized = String(name || '').trim();
    if (!normalized || seen[normalized] || !Object.prototype.hasOwnProperty.call(rawStatuses, normalized)) return;
    seen[normalized] = true;
    orderedNames.push(normalized);
  });
  Object.keys(rawStatuses).sort(function(a, b) {
    return a.localeCompare(b, 'zh-CN');
  }).forEach(function(name) {
    var normalized = String(name || '').trim();
    if (!normalized || seen[normalized]) return;
    seen[normalized] = true;
    orderedNames.push(normalized);
  });

  return orderedNames.map(function(name) {
    var status = String(rawStatuses[name] || '').trim();
    return {
      name: name,
      status: status,
      label: meetingUtteranceStatusLabel(status),
      tone: meetingUtteranceStatusTone(status),
    };
  });
}

function renderMeetingUtteranceStatusPanel(summary) {
  var items = collectMeetingUtteranceStatuses(summary);
  var floorManagerStats = summary && summary.final_stats && summary.final_stats.floor_manager
    ? summary.final_stats.floor_manager
    : null;
  if (!items.length) {
    return '' +
      '<div class="history-runtime-panel">' +
        '<div class="history-runtime-panel-head">' +
          '<div class="history-runtime-panel-title">发言状态</div>' +
          '<div class="history-runtime-panel-meta">仅新会话会写入 utterance status</div>' +
        '</div>' +
        '<div class="history-status-empty">这场历史记录还没有 speaker_utterance_statuses。旧会话仍可继续看完整剧本、时间线和 closing gate，但不会显示逐角色发言状态。</div>' +
      '</div>';
  }

  var counts = {};
  items.forEach(function(item) {
    counts[item.label] = (counts[item.label] || 0) + 1;
  });
  var summaryChips = Object.keys(counts).sort(function(a, b) {
    return a.localeCompare(b, 'zh-CN');
  }).map(function(label) {
    return '<span class="history-status-summary-chip">' + escHtml(label) + ' · ' + escHtml(String(counts[label])) + '</span>';
  }).join('');

  var noteParts = [];
  if (floorManagerStats && Array.isArray(floorManagerStats.spoken_display_names) && floorManagerStats.spoken_display_names.length) {
    noteParts.push('实质发言：' + floorManagerStats.spoken_display_names.join(' · '));
  }
  if (floorManagerStats && Array.isArray(floorManagerStats.missing_ai_display_names) && floorManagerStats.missing_ai_display_names.length) {
    noteParts.push('未覆盖 AI：' + floorManagerStats.missing_ai_display_names.join(' · '));
  }

  return '' +
    '<div class="history-runtime-panel">' +
      '<div class="history-runtime-panel-head">' +
        '<div class="history-runtime-panel-title">发言状态</div>' +
        '<div class="history-runtime-panel-meta">按 summary.final_stats.floor_manager.speaker_utterance_statuses 渲染</div>' +
      '</div>' +
      '<div class="history-status-summary">' + summaryChips + '</div>' +
      '<div class="history-status-grid">' + items.map(function(item) {
        return '' +
          '<div class="history-status-card ' + item.tone + '">' +
            '<div class="history-status-name">' + escHtml(item.name) + '</div>' +
            '<span class="history-status-pill ' + item.tone + '">' + escHtml(item.label) + '</span>' +
          '</div>';
      }).join('') + '</div>' +
      (noteParts.length
        ? '<div class="history-status-note">' + escHtml(noteParts.join(' ｜ ')) + '</div>'
        : '') +
    '</div>';
}

function prettyMeetingEventType(entryType) {
  var labels = {
    message: '发言消息',
    stream: '流式片段',
    turn_change: '轮次切换',
    human_input_requested: '请求用户发言',
    human_input: '用户输入',
    state_change: '状态变化',
    phase_telemetry: '阶段遥测',
    interrupt: '打断',
    system: '系统消息',
    session_config: '会话配置',
    designate_speaker: '指定发言者',
    push_to_talk_start: '按住说话开始',
    push_to_talk_end: '按住说话结束',
    asr_status: 'ASR 状态',
    pause: '暂停',
    resume: '恢复',
    send_drop: '发送丢弃',
    ended: '会话结束',
    error: '错误',
    api_error: '接口错误',
  };
  return labels[entryType] || entryType || 'unknown';
}

function extractMeetingEventSpeakers(event) {
  var data = event && event.data ? event.data : {};
  var speakers = [];
  var seen = {};

  function pushSpeaker(value) {
    var normalized = String(value || '').trim();
    if (!normalized || seen[normalized]) return;
    seen[normalized] = true;
    speakers.push(normalized);
  }

  switch (event && event.entry_type) {
    case 'message':
      pushSpeaker(data.source);
      break;
    case 'stream':
      pushSpeaker(data.source || data.speaker);
      break;
    case 'human_input':
    case 'human_input_requested':
    case 'turn_change':
    case 'push_to_talk_start':
    case 'push_to_talk_end':
    case 'asr_status':
    case 'state_change':
      pushSpeaker(data.speaker);
      break;
    case 'interrupt':
      pushSpeaker(data.interrupter);
      pushSpeaker(data.interrupted_speaker);
      pushSpeaker(data.approved_by);
      break;
    case 'phase_telemetry':
      pushSpeaker(data.speaker);
      pushSpeaker(data.designate_target);
      break;
    case 'designate_speaker':
      pushSpeaker(data.target);
      pushSpeaker(data.speaker);
      break;
    default:
      break;
  }

  return speakers;
}

function buildMeetingHistorySpeakerStats(summary, events) {
  var counts = {};
  if (summary && Array.isArray(summary.participants)) {
    summary.participants.forEach(function(name) {
      var normalized = String(name || '').trim();
      if (!normalized || counts.hasOwnProperty(normalized)) return;
      counts[normalized] = 0;
    });
  }
  events.forEach(function(event) {
    extractMeetingEventSpeakers(event).forEach(function(name) {
      counts[name] = (counts[name] || 0) + 1;
    });
  });

  return Object.keys(counts)
    .map(function(name) {
      return { name: name, count: counts[name] || 0 };
    })
    .sort(function(a, b) {
      if (b.count !== a.count) return b.count - a.count;
      return a.name.localeCompare(b.name, 'zh-CN');
    });
}

function buildMeetingHistoryTypeStats(events) {
  var counts = {};
  var order = [
    'message',
    'stream',
    'turn_change',
    'human_input_requested',
    'human_input',
    'state_change',
    'phase_telemetry',
    'interrupt',
    'system',
    'session_config',
    'designate_speaker',
    'push_to_talk_start',
    'push_to_talk_end',
    'asr_status',
    'pause',
    'resume',
    'send_drop',
    'ended',
    'error',
    'api_error',
  ];

  events.forEach(function(event) {
    var type = String(event && event.entry_type || '').trim();
    if (!type) return;
    counts[type] = (counts[type] || 0) + 1;
  });

  return Object.keys(counts)
    .map(function(type) {
      return { type: type, count: counts[type] };
    })
    .sort(function(a, b) {
      var indexA = order.indexOf(a.type);
      var indexB = order.indexOf(b.type);
      if (indexA === -1) indexA = order.length + 1;
      if (indexB === -1) indexB = order.length + 1;
      if (indexA !== indexB) return indexA - indexB;
      return a.type.localeCompare(b.type);
    });
}

function eventMatchesMeetingHistoryFilters(event) {
  if (meetingHistoryFilters.eventType !== ALL_MEETING_FILTER_VALUE && event.entry_type !== meetingHistoryFilters.eventType) {
    return false;
  }
  if (meetingHistoryFilters.speaker !== ALL_MEETING_FILTER_VALUE) {
    return extractMeetingEventSpeakers(event).indexOf(meetingHistoryFilters.speaker) >= 0;
  }
  return true;
}

function buildMeetingHistoryFilterChip(filterKind, value, label, count, active) {
  var encodedValue = encodeURIComponent(String(value || ''));
  var handler = filterKind === 'speaker'
    ? 'setMeetingHistorySpeakerFilter'
    : 'setMeetingHistoryEventTypeFilter';
  return '' +
    '<button type="button" class="history-filter-chip' + (active ? ' active' : '') + '" onclick="' + handler + '(decodeURIComponent(&quot;' + encodedValue + '&quot;))">' +
      '<span>' + escHtml(label) + '</span>' +
      '<span class="history-filter-chip-count">' + escHtml(String(count)) + '</span>' +
    '</button>';
}

function activeMeetingHistoryFilterSummary() {
  var parts = [];
  if (meetingHistoryFilters.speaker !== ALL_MEETING_FILTER_VALUE) {
    parts.push('人物：' + meetingHistoryFilters.speaker);
  }
  if (meetingHistoryFilters.eventType !== ALL_MEETING_FILTER_VALUE) {
    parts.push('类型：' + prettyMeetingEventType(meetingHistoryFilters.eventType));
  }
  return parts.length ? parts.join(' ｜ ') : '未启用筛选';
}

function renderMeetingHistoryFilterPanel(summary, events, filteredEvents) {
  var speakerStats = buildMeetingHistorySpeakerStats(summary, events);
  var typeStats = buildMeetingHistoryTypeStats(events);
  var filtersActive = meetingHistoryFilters.speaker !== ALL_MEETING_FILTER_VALUE ||
    meetingHistoryFilters.eventType !== ALL_MEETING_FILTER_VALUE;

  return '' +
    '<div class="history-filter-panel">' +
      '<div class="history-filter-toolbar">' +
        '<div>' +
          '<div class="history-filter-title">调试筛选</div>' +
          '<div class="history-filter-summary">显示 ' + escHtml(String(filteredEvents.length)) + ' / ' + escHtml(String(events.length)) + ' 条事件 · ' + escHtml(activeMeetingHistoryFilterSummary()) + '</div>' +
        '</div>' +
        '<button type="button" class="history-filter-clear" onclick="clearMeetingHistoryFilters()"' + (filtersActive ? '' : ' disabled') + '>清除筛选</button>' +
      '</div>' +
      '<div class="history-filter-groups">' +
        '<div class="history-filter-group">' +
          '<div class="history-filter-label">Speaker</div>' +
          '<div class="history-filter-chips">' +
            buildMeetingHistoryFilterChip('speaker', ALL_MEETING_FILTER_VALUE, '全部人物', events.length, meetingHistoryFilters.speaker === ALL_MEETING_FILTER_VALUE) +
            speakerStats.map(function(item) {
              return buildMeetingHistoryFilterChip('speaker', item.name, item.name, item.count, meetingHistoryFilters.speaker === item.name);
            }).join('') +
          '</div>' +
        '</div>' +
        '<div class="history-filter-group">' +
          '<div class="history-filter-label">Event Type</div>' +
          '<div class="history-filter-chips">' +
            buildMeetingHistoryFilterChip('eventType', ALL_MEETING_FILTER_VALUE, '全部事件', events.length, meetingHistoryFilters.eventType === ALL_MEETING_FILTER_VALUE) +
            typeStats.map(function(item) {
              return buildMeetingHistoryFilterChip('eventType', item.type, prettyMeetingEventType(item.type), item.count, meetingHistoryFilters.eventType === item.type);
            }).join('') +
          '</div>' +
        '</div>' +
      '</div>' +
    '</div>';
}

function renderMeetingEventSpeakerTags(event) {
  var speakers = extractMeetingEventSpeakers(event);
  if (!speakers.length) return '';
  return '<div class="history-event-tags">' + speakers.map(function(name) {
    var active = meetingHistoryFilters.speaker === name;
    return '<span class="history-mini-pill' + (active ? ' active' : '') + '">' + escHtml(name) + '</span>';
  }).join('') + '</div>';
}

function buildMeetingProcessIndexMap(events) {
  var map = new Map();
  (Array.isArray(events) ? events : []).forEach(function(event, index) {
    map.set(event, index + 1);
  });
  return map;
}

function setMeetingHistorySpeakerFilter(value) {
  meetingHistoryFilters.speaker = value || ALL_MEETING_FILTER_VALUE;
  if (currentMeetingHistoryRecord) renderMeetingHistoryDetail(currentMeetingHistoryRecord);
}

function setMeetingHistoryEventTypeFilter(value) {
  meetingHistoryFilters.eventType = value || ALL_MEETING_FILTER_VALUE;
  if (currentMeetingHistoryRecord) renderMeetingHistoryDetail(currentMeetingHistoryRecord);
}

function clearMeetingHistoryFilters() {
  resetMeetingHistoryFilters();
  if (currentMeetingHistoryRecord) renderMeetingHistoryDetail(currentMeetingHistoryRecord);
}

function normalizeSelectedMeetingHistoryIds(items) {
  var validIds = new Set((Array.isArray(items) ? items : []).map(function(item) {
    return item && item.session_id ? item.session_id : '';
  }).filter(Boolean));
  selectedMeetingHistoryIds = new Set(Array.from(selectedMeetingHistoryIds).filter(function(sessionId) {
    return validIds.has(sessionId);
  }));
}

function isMeetingHistorySelected(sessionId) {
  return !!sessionId && selectedMeetingHistoryIds.has(sessionId);
}

function toggleMeetingHistorySelection(sessionId, forceSelected) {
  if (!sessionId) return;
  var shouldSelect = typeof forceSelected === 'boolean'
    ? forceSelected
    : !selectedMeetingHistoryIds.has(sessionId);
  if (shouldSelect) {
    selectedMeetingHistoryIds.add(sessionId);
  } else {
    selectedMeetingHistoryIds.delete(sessionId);
  }
  refreshMeetingHistorySelectionUi();
}

function clearSelectedMeetingHistories() {
  if (!selectedMeetingHistoryIds.size) return;
  selectedMeetingHistoryIds.clear();
  refreshMeetingHistorySelectionUi();
}

function toggleSelectAllMeetingHistories() {
  var listEl = document.getElementById('history-list');
  if (!listEl) return;
  var sessionIds = Array.from(listEl.querySelectorAll('.history-item[data-session-id]')).map(function(node) {
    return node.getAttribute('data-session-id') || '';
  }).filter(Boolean);
  if (!sessionIds.length) return;
  var allSelected = sessionIds.every(function(sessionId) { return selectedMeetingHistoryIds.has(sessionId); });
  if (allSelected) {
    sessionIds.forEach(function(sessionId) { selectedMeetingHistoryIds.delete(sessionId); });
  } else {
    sessionIds.forEach(function(sessionId) { selectedMeetingHistoryIds.add(sessionId); });
  }
  refreshMeetingHistorySelectionUi();
}

function refreshMeetingHistorySelectionUi() {
  var listEl = document.getElementById('history-list');
  var itemCount = 0;
  var visibleSessionIds = [];
  if (listEl) {
    var historyNodes = Array.from(listEl.querySelectorAll('.history-item[data-session-id]'));
    itemCount = historyNodes.length;
    historyNodes.forEach(function(node) {
      var sessionId = node.getAttribute('data-session-id') || '';
      if (sessionId) visibleSessionIds.push(sessionId);
      var selected = selectedMeetingHistoryIds.has(sessionId);
      node.classList.toggle('selected', selected);
      var checkbox = node.querySelector('.history-select-box');
      if (checkbox) checkbox.checked = selected;
    });
  }
  var summaryEl = document.getElementById('history-summary');
  if (summaryEl) {
    var totalCount = Number(listEl && listEl.getAttribute('data-total-count') || 0);
    var visibleCount = Number(listEl && listEl.getAttribute('data-visible-count') || itemCount);
    if (totalCount > 0) {
      summaryEl.textContent = buildMeetingHistorySummaryText(totalCount, visibleCount);
    }
  }
  syncMeetingHistoryActions(itemCount, visibleSessionIds);
}

function syncMeetingHistoryActions(totalCount, visibleSessionIds) {
  var sessionIds = Array.isArray(visibleSessionIds)
    ? visibleSessionIds.slice()
    : Array.from(document.querySelectorAll('.history-item[data-session-id]')).map(function(node) {
        return node.getAttribute('data-session-id') || '';
      }).filter(Boolean);
  var itemCount = typeof totalCount === 'number' ? totalCount : sessionIds.length;
  var selectedCount = selectedMeetingHistoryIds.size;
  var visibleSelectedCount = sessionIds.filter(function(sessionId) {
    return selectedMeetingHistoryIds.has(sessionId);
  }).length;
  var deleteBtn = document.getElementById('btn-delete-history');
  if (deleteBtn) deleteBtn.disabled = !activeMeetingHistoryId;
  var deleteSelectedBtn = document.getElementById('btn-delete-history-selected');
  if (deleteSelectedBtn) {
    deleteSelectedBtn.disabled = !selectedCount;
    deleteSelectedBtn.textContent = selectedCount ? ('🗑 删除所选（' + selectedCount + '）') : '🗑 删除所选';
  }
  var selectAllBtn = document.getElementById('btn-select-all-history');
  if (selectAllBtn) {
    selectAllBtn.disabled = !itemCount;
    var allSelected = itemCount > 0 && visibleSelectedCount === itemCount;
    selectAllBtn.textContent = allSelected ? '☑ 取消全选' : '☑ 全选';
  }
  var clearSelectionBtn = document.getElementById('btn-clear-history-selection');
  if (clearSelectionBtn) clearSelectionBtn.disabled = !selectedCount;
  var exportDropdown = document.getElementById('history-export-dropdown');
  if (exportDropdown) {
    exportDropdown.classList.toggle('disabled', !activeMeetingHistoryId);
    if (!activeMeetingHistoryId) exportDropdown.removeAttribute('open');
  }
}

function toggleHistoryCard(cardName) {
  var body = document.getElementById('history-card-body-' + cardName);
  var toggle = document.getElementById('history-card-toggle-' + cardName);
  if (!body || !toggle) return;
  var collapsed = body.classList.toggle('collapsed');
  toggle.classList.toggle('collapsed', collapsed);
}

function buildMeetingScriptExportUrl(sessionId, format) {
  var exportFormat = format === 'json' ? 'json' : 'markdown';
  return backendUrl('/api/v1/history/sessions/' + encodeURIComponent(sessionId) + '/script/export?format=' + encodeURIComponent(exportFormat));
}

function buildMeetingScriptPackageUrl(sessionId, options) {
  var params = new URLSearchParams();
  params.set('include_markdown', options.includeMarkdown ? 'true' : 'false');
  params.set('include_json', options.includeJson ? 'true' : 'false');
  params.set('include_recording_manifest', options.includeRecordingManifest ? 'true' : 'false');
  params.set('include_recording_audio', options.includeRecordingAudio ? 'true' : 'false');
  return backendUrl('/api/v1/history/sessions/' + encodeURIComponent(sessionId) + '/script/package?' + params.toString());
}

function readMeetingScriptPackageSelection() {
  var markdownInput = document.getElementById('meeting-package-markdown');
  var jsonInput = document.getElementById('meeting-package-json');
  var manifestInput = document.getElementById('meeting-package-recording-manifest');
  var audioInput = document.getElementById('meeting-package-recording-audio');
  return {
    includeMarkdown: !!(markdownInput && markdownInput.checked),
    includeJson: !!(jsonInput && jsonInput.checked),
    includeRecordingManifest: !!(manifestInput && manifestInput.checked && !manifestInput.disabled),
    includeRecordingAudio: !!(audioInput && audioInput.checked && !audioInput.disabled),
  };
}

function hasMeetingScriptPackageSelection(options) {
  return !!(options.includeMarkdown || options.includeJson || options.includeRecordingManifest || options.includeRecordingAudio);
}

function downloadMeetingScriptExport(format) {
  if (!activeMeetingHistoryId) {
    showToast('请先选择一场会议，再导出完整剧本。');
    return;
  }
  var exportFormat = format === 'json' ? 'json' : 'markdown';
  var link = document.createElement('a');
  link.href = buildMeetingScriptExportUrl(activeMeetingHistoryId, exportFormat);
  link.target = '_blank';
  link.rel = 'noopener';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

function downloadMeetingScriptPackage() {
  if (!activeMeetingHistoryId) {
    showToast('请先选择一场会议，再导出复盘包。');
    return;
  }
  var options = readMeetingScriptPackageSelection();
  if (!hasMeetingScriptPackageSelection(options)) {
    showToast('请至少勾选一种打包内容。');
    return;
  }
  var link = document.createElement('a');
  link.href = buildMeetingScriptPackageUrl(activeMeetingHistoryId, options);
  link.target = '_blank';
  link.rel = 'noopener';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

function renderMeetingScriptPackagePanel(recordings, summary) {
  var recordingItems = Array.isArray(recordings) ? recordings : [];
  var summaryRecordingCount = (summary && Number(summary.recording_count)) || 0;
  var hasRecordingItems = recordingItems.length > 0;
  // 只要后台记录到过录音或 manifest 有条目，都允许勾选。
  var hasRecordings = hasRecordingItems || summaryRecordingCount > 0;
  var totalCount = hasRecordingItems ? recordingItems.length : summaryRecordingCount;
  var status = hasRecordings
    ? '当前可选录音 ' + totalCount + ' 段，可按需附带清单或音频。'
    : '当前没有录音附件，可只打包文字剧本。';
  return '' +
    '<div class="history-package-panel">' +
      '<div class="history-package-top">' +
        '<div class="history-package-copy">' +
          '<div class="history-package-title">ZIP 复盘包</div>' +
          '<div class="history-package-subtitle">默认只打包文字。需要结构化归档或录音时，再勾选附加内容。</div>' +
        '</div>' +
        '<div class="history-package-status">' + escHtml(status) + '</div>' +
      '</div>' +
      '<div class="history-package-bottom">' +
        '<div class="history-package-options">' +
          '<label class="history-package-option"><input id="meeting-package-markdown" type="checkbox" checked> Markdown 剧本</label>' +
          '<label class="history-package-option"><input id="meeting-package-json" type="checkbox"> JSON 剧本</label>' +
          '<label class="history-package-option' + (hasRecordings ? '' : ' disabled') + '"><input id="meeting-package-recording-manifest" type="checkbox"' + (hasRecordings ? '' : ' disabled') + '> 录音清单</label>' +
          '<label class="history-package-option' + (hasRecordings ? '' : ' disabled') + '"><input id="meeting-package-recording-audio" type="checkbox"' + (hasRecordings ? '' : ' disabled') + '> 音频附件</label>' +
        '</div>' +
        '<div class="history-package-actions">' +
          '<button type="button" class="history-export-btn" onclick="downloadMeetingScriptPackage()">下载 ZIP 复盘包</button>' +
        '</div>' +
      '</div>' +
    '</div>';
}

function renderMeetingSummaryStrip(summary, script, recordings) {
  var recordingCount = Number(summary.recording_count || recordings.length || 0);
  var recordingDuration = formatMeetingDurationMs(summary.recording_duration_ms);
  var floorManagerStats = summary && summary.final_stats && summary.final_stats.floor_manager
    ? summary.final_stats.floor_manager
    : null;
  var closingGate = floorManagerStats && floorManagerStats.closing_gate
    ? floorManagerStats.closing_gate
    : null;
  var segments = [
    { label: 'SESSION', value: summary.session_id || '-' },
    { label: '开始', value: formatMeetingTime(summary.started_at) },
    { label: '结束', value: formatMeetingTime(summary.ended_at) },
    { label: '参与者', value: Array.isArray(summary.participants) ? summary.participants.join(' · ') : '-', wide: true },
    { label: '事件', value: String(summary.event_count || 0) + ' 条' },
    { label: '剧本', value: String(summary.script_line_count || script.line_count || 0) + ' 行' },
    { label: '录音', value: recordingCount + ' 段' + (recordingDuration !== '-' ? (' / ' + recordingDuration) : '') },
  ];
  if (closingGate && Array.isArray(closingGate.blockers) && closingGate.blockers.length) {
    segments.push({
      label: '收尾闸门',
      value: closingGate.blockers.join(' ｜ '),
      wide: true,
    });
  }
  var summaryStrip = '<div class="history-summary-strip">' + segments.map(function(segment) {
    var titleAttr = segment.value && segment.value !== '-' ? ' title="' + escAttr(segment.value) + '"' : '';
    return '<div class="history-summary-segment' + (segment.wide ? ' wide' : '') + '"' + titleAttr + '>' +
      '<span class="history-summary-segment-label">' + escHtml(segment.label) + '</span>' +
      '<span class="history-summary-segment-value">' + escHtml(segment.value) + '</span>' +
    '</div>';
  }).join('') + '</div>';
  return '<div class="history-runtime-insights">' +
    '<div>' + summaryStrip + '</div>' +
    renderMeetingUtteranceStatusPanel(summary) +
  '</div>';
}

function summarizeMeetingEvent(event) {
  var data = event && event.data ? event.data : {};
  if (event.entry_type === 'message') {
    return ((data.source || '未知发言者') + '：' + (data.content || '')).trim();
  }
  if (event.entry_type === 'human_input') {
    return ((data.speaker || '用户') + '：' + (data.content || '')).trim();
  }
  if (event.entry_type === 'turn_change') {
    return '轮到 ' + (data.speaker || '未知角色') + ' 发言';
  }
  if (event.entry_type === 'human_input_requested') {
    return '等待 ' + (data.speaker || '用户') + ' 发言';
  }
  if (event.entry_type === 'state_change') {
    return (data.old_label || data.old_state || '未知状态') + ' → ' + (data.new_label || data.new_state || '未知状态');
  }
  if (event.entry_type === 'interrupt') {
    return (data.interrupter || '有人') + ' 打断了 ' + (data.interrupted_speaker || '当前发言者');
  }
  if (event.entry_type === 'session_config') {
    return '会话初始化：' + ((data.topic_id || data.free_topic || '未命名话题') + ' ｜ max_turns=' + (data.max_turns || '-'));
  }
  if (event.entry_type === 'phase_telemetry') {
    return (data.phase || 'unknown') + (data.reason ? ' ｜ ' + data.reason : '') + (data.speaker ? ' ｜ ' + data.speaker : '');
  }
  if (event.entry_type === 'system') {
    return data.message || '系统消息';
  }
  if (event.entry_type === 'push_to_talk_start') {
    return (data.speaker || '用户') + ' 开始按住说话';
  }
  if (event.entry_type === 'push_to_talk_end') {
    return (data.speaker || '用户') + ' 结束按住说话';
  }
  if (event.entry_type === 'asr_status') {
    return (data.speaker || '用户') + ' 的 ASR 状态：' + (data.status || 'unknown') + (data.provider ? ' ｜ ' + data.provider : '');
  }
  if (event.entry_type === 'designate_speaker') {
    return '指定下一位发言者：' + (data.target || data.speaker || 'unknown');
  }
  if (event.entry_type === 'send_drop') {
    return '发送丢弃：' + (data.reason || 'unknown') + ' ｜ ' + (data.event_type || 'unknown');
  }
  if (event.entry_type === 'error' || event.entry_type === 'api_error') {
    return data.message || '发生错误';
  }
  if (event.entry_type === 'ended') {
    return data.message || '会话结束';
  }
  return JSON.stringify(data, null, 2);
}

function renderMeetingScriptSection(script) {
  var lines = Array.isArray(script && script.lines) ? script.lines : [];
  return '' +
    '<section class="history-section">' +
      '<div class="history-section-head">' +
        '<div class="history-section-title">完整剧本</div>' +
        '<div class="history-section-meta">' + escHtml(String(lines.length)) + ' 行</div>' +
      '</div>' +
      (lines.length
        ? '<div class="history-script-lines">' + lines.map(function(line, index) {
            var recording = line && line.recording ? line.recording : null;
            var meta = [];
            if (line && line.entry_type) meta.push(prettyMeetingEventType(line.entry_type));
            if (line && line.event_seq != null) meta.push('事件 #' + line.event_seq);
            if (line && line.echo_event_seq != null) meta.push('合并回声 #' + line.echo_event_seq);
            return '' +
              '<article class="history-script-line ' + (line && line.kind === 'note' ? 'note' : 'speech') + '">' +
                '<div class="history-script-meta">' +
                  '<span class="history-pill">片段 ' + escHtml(String(index + 1)) + '</span>' +
                  (line && line.speaker ? '<span class="history-script-speaker">' + escHtml(line.speaker) + '</span>' : '<span class="history-pill">注记</span>') +
                  '<span>' + escHtml(formatMeetingTime(line && line.timestamp)) + '</span>' +
                  meta.map(function(item) {
                    return '<span class="history-mini-pill">' + escHtml(item) + '</span>';
                  }).join('') +
                '</div>' +
                '<div class="history-script-text">' + escHtml(line && line.text || '') + '</div>' +
                (recording && recording.audio_url
                  ? '<audio class="history-script-audio" controls preload="none" src="' + escAttr(recording.audio_url) + '"></audio>'
                  : '') +
                (recording
                  ? '<div class="history-script-extra">' +
                      '<span class="history-mini-pill">录音 ID ' + escHtml(recording.recording_id || '-') + '</span>' +
                      '<span class="history-mini-pill">时长 ' + escHtml(formatMeetingDurationMs(recording.duration_ms)) + '</span>' +
                      '<span class="history-mini-pill">大小 ' + escHtml(formatMeetingBytes(recording.size_bytes)) + '</span>' +
                    '</div>'
                  : '') +
              '</article>';
          }).join('') + '</div>'
        : '<div class="history-empty history-empty-note">当前会议还没有生成剧本行。</div>') +
    '</section>';
}

function renderMeetingRecordingsSection(recordings) {
  var items = Array.isArray(recordings) ? recordings : [];
  return '' +
    '<section class="history-section">' +
      '<div class="history-section-head">' +
        '<div class="history-section-title">用户录音留存</div>' +
        '<div class="history-section-meta">' + escHtml(String(items.length)) + ' 段</div>' +
      '</div>' +
      (items.length
        ? '<div class="history-recording-list">' + items.map(function(recording) {
            return '' +
              '<article class="history-recording-item">' +
                '<div class="history-script-meta">' +
                  '<span class="history-script-speaker">' + escHtml(recording.speaker || '用户') + '</span>' +
                  '<span>' + escHtml(formatMeetingTime(recording.created_at)) + '</span>' +
                  '<span class="history-mini-pill">' + escHtml(recording.extension || 'bin') + '</span>' +
                  '<span class="history-mini-pill">' + escHtml(formatMeetingDurationMs(recording.duration_ms)) + '</span>' +
                  '<span class="history-mini-pill">' + escHtml(formatMeetingBytes(recording.size_bytes)) + '</span>' +
                '</div>' +
                '<audio class="history-script-audio" controls preload="none" src="' + escAttr(recording.audio_url || '') + '"></audio>' +
                '<div class="history-recording-preview">转写摘要：' + escHtml(recording.transcript_preview || '无') + '</div>' +
              '</article>';
          }).join('') + '</div>'
        : '<div class="history-empty history-empty-note">当前会议还没有用户录音附件。</div>') +
    '</section>';
}

function renderMeetingHistoryList(items) {
  var listEl = document.getElementById('history-list');
  var detailEl = document.getElementById('history-detail');
  var summaryEl = document.getElementById('history-summary');
  var allItems = Array.isArray(items) ? items : [];
  refreshMeetingHistoryStatusFilterOptions(allItems);
  syncMeetingHistorySessionFilterClearButton();
  if (!allItems.length) {
    listEl.innerHTML = '<div class="history-empty">还没有持久化的会议记录。下一次讨论开始后，这里会自动出现完整时间线。</div>';
    detailEl.innerHTML = '<div class="history-empty">暂无会议记录可查看。</div>';
    summaryEl.textContent = '暂无记录';
    summaryEl.style.color = '#5f6f95';
    listEl.setAttribute('data-total-count', '0');
    listEl.setAttribute('data-visible-count', '0');
    activeMeetingHistoryId = '';
    currentMeetingHistoryRecord = null;
    selectedMeetingHistoryIds.clear();
    syncMeetingHistoryActions(0, []);
    return [];
  }

  normalizeSelectedMeetingHistoryIds(allItems);
  var filteredItems = getFilteredMeetingHistoryItems(allItems);
  listEl.setAttribute('data-total-count', String(allItems.length));
  listEl.setAttribute('data-visible-count', String(filteredItems.length));
  summaryEl.textContent = buildMeetingHistorySummaryText(allItems.length, filteredItems.length);
  summaryEl.style.color = filteredItems.length ? '#80cbc4' : '#f6e3a5';

  if (!filteredItems.length) {
    listEl.innerHTML = '<div class="history-empty history-empty-note">当前筛选下没有匹配的会议记录。<button type="button" class="history-filter-clear" onclick="clearMeetingHistorySessionFilters()">清除筛选</button></div>';
    detailEl.innerHTML = '<div class="history-empty history-empty-note">没有符合当前筛选条件的会议记录。<button type="button" class="history-filter-clear" onclick="clearMeetingHistorySessionFilters()">恢复全部会议</button></div>';
    activeMeetingHistoryId = '';
    currentMeetingHistoryRecord = null;
    syncMeetingHistoryActions(0, []);
    return [];
  }

  if (!filteredItems.some(function(item) { return item.session_id === activeMeetingHistoryId; })) {
    activeMeetingHistoryId = filteredItems[0].session_id;
  }
  syncMeetingHistoryActions(filteredItems.length, filteredItems.map(function(item) { return item.session_id; }));

  listEl.innerHTML = filteredItems.map(function(item) {
    var topicTitle = item.topic && item.topic.title ? item.topic.title : (item.config && item.config.free_topic) || item.session_id;
    var encodedSessionId = encodeURIComponent(item.session_id || '');
    var selected = isMeetingHistorySelected(item.session_id);
    return '' +
      '<div class="history-item' + (item.session_id === activeMeetingHistoryId ? ' active' : '') + (selected ? ' selected' : '') + '" data-session-id="' + escAttr(item.session_id) + '" onclick="openMeetingHistory(decodeURIComponent(&quot;' + encodedSessionId + '&quot;))">' +
        '<div class="history-item-head">' +
          '<label class="history-select-wrap" title="选择这条会议记录" onclick="event.stopPropagation()">' +
            '<input class="history-select-box" type="checkbox" ' + (selected ? 'checked ' : '') + 'onchange="toggleMeetingHistorySelection(decodeURIComponent(&quot;' + encodedSessionId + '&quot;), this.checked)">' +
          '</label>' +
          '<div class="history-item-body">' +
            '<div class="history-item-main">' +
              '<div class="history-item-copy">' +
                '<div class="history-session-id">' + escHtml(item.session_id || '-') + '</div>' +
                '<div class="history-topic">' + escHtml(topicTitle) + '</div>' +
                '<div class="history-item-time">' + escHtml(formatMeetingTimeCompact(item.updated_at || item.started_at)) + '</div>' +
              '</div>' +
              '<div class="history-item-side">' +
                '<span class="history-pill ' + meetingStatusClass(item.status) + '">' + escHtml(meetingStatusLabel(item.status)) + '</span>' +
                '<span class="history-item-count">' + escHtml(String(item.event_count || 0)) + ' 条</span>' +
              '</div>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>';
  }).join('');

  return filteredItems;
}

function renderMeetingHistoryDetail(record) {
  var detailEl = document.getElementById('history-detail');
  var summary = record && record.summary ? record.summary : {};
  var events = Array.isArray(record && record.events) ? record.events : [];
  var script = record && record.script ? record.script : { lines: [] };
  var recordings = Array.isArray(record && record.recordings) ? record.recordings : [];
  var filteredEvents = events.filter(eventMatchesMeetingHistoryFilters);
  var processIndexMap = buildMeetingProcessIndexMap(events);
  var topicTitle = summary.topic && summary.topic.title ? summary.topic.title : summary.session_id || '未命名会议';
  detailEl.innerHTML = '' +
    '<div class="history-detail-head">' +
      '<div class="history-head-panel history-head-main">' +
        '<div class="history-head-kicker">会议主题</div>' +
        '<div class="history-detail-title">' + escHtml(topicTitle) + '</div>' +
        '<div class="history-head-meta">' +
          '<span class="history-pill ' + meetingStatusClass(summary.status) + '">' + escHtml(meetingStatusLabel(summary.status)) + '</span>' +
          '<span class="history-pill">最后更新 ' + escHtml(formatMeetingTime(summary.updated_at)) + '</span>' +
        '</div>' +
      '</div>' +
      '<div class="history-detail-actions">' +
        renderMeetingScriptPackagePanel(recordings, summary) +
      '</div>' +
    '</div>' +
    renderMeetingSummaryStrip(summary, script, recordings) +
    '<div class="history-sections-cards">' +
      // 左侧卡片: 完整剧本
      '<div class="history-card" id="history-card-script">' +
        '<div class="history-card-head" onclick="toggleHistoryCard(&quot;script&quot;)">' +
          '<div class="history-card-title">📜 完整剧本</div>' +
          '<div class="history-card-meta">' + escHtml(String((script.lines || []).length)) + ' 行</div>' +
          '<span class="history-card-toggle" id="history-card-toggle-script">▼</span>' +
        '</div>' +
        '<div class="history-card-body" id="history-card-body-script">' +
          renderMeetingScriptSection(script) +
        '</div>' +
      '</div>' +
      // 右侧卡片: 完整时间线
      '<div class="history-card" id="history-card-timeline">' +
        '<div class="history-card-head" onclick="toggleHistoryCard(&quot;timeline&quot;)">' +
          '<div class="history-card-title">⏱ 完整时间线</div>' +
          '<div class="history-card-meta">' + escHtml(String(filteredEvents.length)) + ' / ' + escHtml(String(events.length)) + ' 条</div>' +
          '<span class="history-card-toggle" id="history-card-toggle-timeline">▼</span>' +
        '</div>' +
        '<div class="history-card-body" id="history-card-body-timeline">' +
          renderMeetingHistoryFilterPanel(summary, events, filteredEvents) +
          '<div class="history-events">' + (filteredEvents.length ? filteredEvents.map(function(event) {
      var processIndex = processIndexMap.get(event) || '-';
      return '' +
        '<div class="history-event">' +
          '<div class="history-event-head">' +
            '<span class="history-pill ' + meetingStatusClass(event.direction === 'outbound' ? 'completed' : event.direction === 'internal' ? 'error' : 'running') + '">' + escHtml(meetingDirectionLabel(event.direction)) + '</span>' +
            '<span>' + escHtml(prettyMeetingEventType(event.entry_type || 'unknown')) + '</span>' +
            '<span class="history-event-raw">' + escHtml(event.entry_type || 'unknown') + '</span>' +
            '<span>过程 ' + escHtml(String(processIndex)) + '</span>' +
            '<span>' + escHtml(formatMeetingTime(event.timestamp)) + '</span>' +
          '</div>' +
          '<div class="history-event-body">' + escHtml(summarizeMeetingEvent(event)) + '</div>' +
          renderMeetingEventSpeakerTags(event) +
          '<details><summary style="margin-top:8px;color:#7f90b5;cursor:pointer">查看原始数据</summary><div class="history-event-raw" style="margin-top:8px">原始事件序号 #' + escHtml(String(event.event_seq || '-')) + '</div><pre class="history-json">' + escHtml(JSON.stringify(event.data || {}, null, 2)) + '</pre></details>' +
        '</div>';
    }).join('') : '<div class="history-empty history-empty-note">当前筛选下没有匹配事件。<button type="button" class="history-filter-clear" onclick="clearMeetingHistoryFilters()">清除筛选</button></div>') + '</div>' +
        '</div>' +
      '</div>' +
      // 录音留存 (下方全宽)
      renderMeetingRecordingsSection(recordings) +
    '</div>';
}

function openMeetingHistory(sessionId, preserveFilters) {
  var previousSessionId = activeMeetingHistoryId;
  activeMeetingHistoryId = sessionId;
  if (!preserveFilters || previousSessionId !== sessionId) {
    resetMeetingHistoryFilters();
  }
  var detailEl = document.getElementById('history-detail');
  detailEl.innerHTML = '<div class="history-empty">正在加载完整时间线…</div>';
  panelFetch('/api/meeting-history/' + encodeURIComponent(sessionId))
    .then(function(r) {
      if (!r.ok) throw new Error('history_detail_failed');
      return r.json();
    })
    .then(function(data) {
      currentMeetingHistoryRecord = data;
      renderMeetingHistoryDetail(data);
      return panelFetch('/api/meeting-history');
    })
    .then(function(r) { return r.json(); })
    .then(function(items) {
      allMeetingHistoryItems = Array.isArray(items) ? items : [];
      renderMeetingHistoryList(allMeetingHistoryItems);
    })
    .catch(function(err) {
      detailEl.innerHTML = '<div class="history-empty">读取会议历史失败：' + escHtml((err && err.message) || 'unknown') + '</div>';
    });
}

function deleteActiveMeetingHistory() {
  if (!activeMeetingHistoryId) return;
  var sessionId = activeMeetingHistoryId;
  if (!window.confirm('删除后无法恢复，确认删除当前会议记录？')) return;
  panelFetch('/api/meeting-history/' + encodeURIComponent(sessionId), { method: 'DELETE' })
    .then(function(r) {
      return r.json().then(function(data) {
        if (!r.ok || !data.ok) throw new Error((data && data.msg) || (data && data.error) || 'history_delete_failed');
        return data;
      });
    })
    .then(function() {
      activeMeetingHistoryId = '';
      currentMeetingHistoryRecord = null;
      resetMeetingHistoryFilters();
      showToast('已删除当前会议记录');
      return refreshMeetingHistory();
    })
    .catch(function(err) {
      showToast('删除失败：' + ((err && err.message) || 'unknown'));
    });
}

function deleteSelectedMeetingHistories() {
  var sessionIds = Array.from(selectedMeetingHistoryIds);
  if (!sessionIds.length) return;
  if (!window.confirm('删除后无法恢复，确认删除所选的 ' + sessionIds.length + ' 场会议记录？')) return;
  Promise.all(sessionIds.map(function(sessionId) {
    return panelFetch('/api/meeting-history/' + encodeURIComponent(sessionId), { method: 'DELETE' })
      .then(function(r) {
        return r.json().then(function(data) {
          if (!r.ok || !data.ok) throw new Error((data && data.msg) || (data && data.error) || sessionId);
          return data;
        });
      });
  }))
    .then(function() {
      var deletedActive = sessionIds.indexOf(activeMeetingHistoryId) >= 0;
      sessionIds.forEach(function(sessionId) { selectedMeetingHistoryIds.delete(sessionId); });
      if (deletedActive) {
        activeMeetingHistoryId = '';
        currentMeetingHistoryRecord = null;
        resetMeetingHistoryFilters();
      }
      showToast('已删除所选的 ' + sessionIds.length + ' 场会议记录');
      return refreshMeetingHistory();
    })
    .catch(function(err) {
      showToast('批量删除失败：' + ((err && err.message) || 'unknown'));
    });
}

function pruneMeetingHistory() {
  panelFetch('/api/meeting-history/prune', { method: 'POST' })
    .then(function(r) {
      return r.json().then(function(data) {
        if (!r.ok || !data.ok) throw new Error((data && data.error) || 'history_prune_failed');
        return data;
      });
    })
    .then(function(data) {
      showToast(data.removed > 0
        ? ('已清理 ' + data.removed + ' 场旧会议，仅保留最近 ' + data.retainCount + ' 场')
        : ('当前已经不超过最近 ' + data.retainCount + ' 场'));
      return refreshMeetingHistory();
    })
    .catch(function(err) {
      showToast('清理失败：' + ((err && err.message) || 'unknown'));
    });
}

function refreshMeetingHistory() {
  var listEl = document.getElementById('history-list');
  listEl.innerHTML = '<div class="history-empty">正在读取会议历史…</div>';
  document.getElementById('history-summary').textContent = '加载中…';
  listEl.setAttribute('data-total-count', '0');
  listEl.setAttribute('data-visible-count', '0');
  syncMeetingHistoryActions();
  return panelFetch('/api/meeting-history')
    .then(function(r) {
      if (!r.ok) throw new Error('history_list_failed');
      return r.json();
    })
    .then(function(items) {
      allMeetingHistoryItems = Array.isArray(items) ? items : [];
      var visibleItems = renderMeetingHistoryList(allMeetingHistoryItems);
      if (visibleItems.length > 0) {
        if (!activeMeetingHistoryId) {
          activeMeetingHistoryId = visibleItems[0].session_id;
        }
        if (!currentMeetingHistoryRecord || !currentMeetingHistoryRecord.summary || currentMeetingHistoryRecord.summary.session_id !== activeMeetingHistoryId) {
          openMeetingHistory(activeMeetingHistoryId, true);
        }
      }
    })
    .catch(function(err) {
      document.getElementById('history-list').innerHTML = '<div class="history-empty">读取会议历史失败：' + escHtml((err && err.message) || 'unknown') + '</div>';
      document.getElementById('history-detail').innerHTML = '<div class="history-empty">请先确认后端已写入历史文件。</div>';
      var summaryEl = document.getElementById('history-summary');
      summaryEl.textContent = '读取失败';
      summaryEl.style.color = '#ef5350';
      allMeetingHistoryItems = [];
      syncMeetingHistoryActions();
    });
}

refreshMeetingHistory();

function writeHardwareReport(win, title, bodyHtml) {
  if (!win) return;
  win.document.open();
  win.document.write(
    '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>' + escHtml(title) + '</title>' +
    '<style>' +
    'body{margin:0;padding:24px;background:#0f172a;color:#e2e8f0;font-family:SFMono-Regular,Menlo,monospace;line-height:1.7}' +
    'h1{margin:0 0 16px;color:#fbbf24;font-size:24px}' +
    '.card{background:#11192f;border:1px solid #1b335f;border-radius:14px;padding:18px 20px;margin-bottom:16px}' +
    '.title{color:#fbbf24;font-size:14px;font-weight:700;margin-bottom:8px}' +
    '.row{margin-bottom:8px;color:#dbe4ff}' +
    '.label{color:#7f90b5;margin-right:8px}' +
    '.muted{color:#94a3b8;font-size:12px}' +
    '</style></head><body>' + bodyHtml + '</body></html>'
  );
  win.document.close();
}

function openHardwareReportWindow() {
  var win = window.open('', '_blank', 'width=860,height=760');
  if (!win) {
    showToast('浏览器拦截了硬件报告弹窗，请允许新窗口后重试。');
    return null;
  }
  writeHardwareReport(
    win,
    'RoundTable 硬件检测报告',
    '<h1>🖥 RoundTable 硬件检测报告</h1><div class="card"><div class="title">正在检测</div><div class="muted">请稍候，面板正在向后端请求硬件信息与优化建议。</div></div>'
  );
  return win;
}

function buildHardwareReportBody(hw, tuningText) {
  var hr = hw.hardware_report || {};
  var rt = hw.runtime_tuning || {};
  var optimizedSummary = rt.device || rt.workers || rt.prefetch_batch
    ? '本次检测已读取并应用本机运行时参数，后端后续会按这组设置执行。'
    : '本次检测只拿到了硬件摘要，未发现可确认的运行时优化结果。';
  return '' +
    '<h1>🖥 RoundTable 硬件检测报告</h1>' +
    '<div class="card">' +
      '<div class="title">硬件摘要</div>' +
      '<div class="row"><span class="label">芯片</span>' + escHtml(hw.apple_chip || hw.cpu_brand || 'CPU') + '</div>' +
      '<div class="row"><span class="label">CPU</span>' + escHtml((hw.cpu_cores || '-') + ' cores / ' + (hw.cpu_threads || '-') + ' threads') + '</div>' +
      '<div class="row"><span class="label">内存</span>' + escHtml((hw.memory_gb || '-') + ' GB RAM') + '</div>' +
      '<div class="row"><span class="label">GPU</span>' + escHtml(hw.mps_available ? ('MPS ✓' + (hw.gpu_cores ? (' ' + hw.gpu_cores + ' cores') : '')) : hw.cuda_available ? 'CUDA ✓' : 'CPU only') + '</div>' +
    '</div>' +
    '<div class="card">' +
      '<div class="title">优化建议</div>' +
      '<div class="row"><span class="label">摘要</span>' + escHtml(hr.summary || '-') + '</div>' +
      '<div class="row"><span class="label">推荐</span>' + escHtml(hr.recommendation || ('建议并行线程: ' + (hw.recommended_workers || '-'))) + '</div>' +
      '<div class="row"><span class="label">已应用优化</span>' + escHtml(tuningText) + '</div>' +
    '</div>' +
    '<div class="card">' +
      '<div class="title">当前已落地的运行时参数</div>' +
      '<div class="row"><span class="label">执行设备</span>' + escHtml(rt.device || '-') + '</div>' +
      '<div class="row"><span class="label">工作线程</span>' + escHtml(String(rt.workers || '-')) + '</div>' +
      '<div class="row"><span class="label">TTS 预取</span>' + escHtml(String(rt.prefetch_batch || '-')) + '</div>' +
      '<div class="row"><span class="label">ASR 预热</span>' + escHtml(String(rt.asr_warmup_interval_ms || '-')) + ' ms</div>' +
      '<div class="row"><span class="label">TTS 重试</span>' + escHtml(String(rt.tts_retry_delay_ms || '-')) + ' ms</div>' +
      '<div class="muted">' + escHtml(optimizedSummary) + '</div>' +
    '</div>' +
    '<div class="muted">数据来源：/api/v1/benchmark/hardware?apply_tuning=true</div>';
}

// Fetch hardware info
function fetchHardware() {
  var btn = document.getElementById('btn-hw-detect');
  var reportWindow = openHardwareReportWindow();
  if (!reportWindow) return;
  btn.disabled = true;
  btn.textContent = '检测中...';
  panelFetch('/api/hardware')
    .then(function(r){
      if (!r.ok) {
        return r.json().catch(function(){ return {}; }).then(function(payload) {
          throw new Error(payload.detail || payload.error || ('HTTP ' + r.status));
        });
      }
      return r.json();
    })
    .then(function(hw) {
      var el = document.getElementById('hw-info');
      el.style.display = 'flex';
      document.getElementById('hw-chip').textContent = hw.apple_chip || hw.cpu_brand || 'CPU';
      document.getElementById('hw-cpu').textContent = hw.cpu_cores + ' cores / ' + hw.cpu_threads + ' perf';
      document.getElementById('hw-mem').textContent = hw.memory_gb + ' GB RAM';
      document.getElementById('hw-gpu').textContent = hw.mps_available ? 'MPS ✓' + (hw.gpu_cores ? ' ' + hw.gpu_cores + ' cores' : '') : hw.cuda_available ? 'CUDA ✓' : 'CPU only';

      var rt = hw.runtime_tuning || {};
      var tuningText = '设备=' + (rt.device || '-') + ', workers=' + (rt.workers || '-') + ', prefetch=' + (rt.prefetch_batch || '-') + ', ASR预热=' + (rt.asr_warmup_interval_ms || '-') + 'ms, TTS重试=' + (rt.tts_retry_delay_ms || '-') + 'ms';

      document.getElementById('hw-opt').textContent = '已按当前主机完成运行时优化：' + tuningText + ' ｜ 推荐并行线程: ' + (hw.recommended_workers || '-');
      writeHardwareReport(
        reportWindow,
        'RoundTable 硬件检测报告',
        buildHardwareReportBody(hw, tuningText)
      );
    })
    .catch(function(err) {
      document.getElementById('hw-opt').textContent = '硬件检测失败：' + ((err && err.message) || '请确认后端已启动');
      writeHardwareReport(
        reportWindow,
        'RoundTable 硬件检测报告',
        '<h1>🖥 RoundTable 硬件检测报告</h1><div class="card"><div class="title">检测失败</div><div class="row">请确认后端已经启动，并且接口 /api/v1/benchmark/hardware 可以正常访问。</div><div class="muted">面板现已改为通过 localhost:${DEV_PANEL_PORT} 代理检测，避免浏览器跨域失败。</div><div class="muted">错误：' + escHtml((err && err.message) || 'unknown') + '</div><div class="muted">API: /api/v1/benchmark/hardware?apply_tuning=true</div></div>'
      );
    })
    .finally(function() {
      btn.disabled = false;
      btn.textContent = '🖥 检测并打开报告';
    });
}
</script>
</body>
</html>`;

const server = http.createServer(async (req, res) => {
  const parsedUrl = new URL(req.url || '/', 'http://127.0.0.1');
  const pathname = parsedUrl.pathname || '/';
  const search = parsedUrl.search || '';
  if (pathname === '/' || pathname === '/index.html') {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    return res.end(DASHBOARD_HTML);
  }
  if (pathname === '/thinkers') {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    return res.end(THINKERS_PAGE_HTML);
  }
  if (pathname === '/api/status') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify(getStatus()));
  }
  if (pathname === '/api/thinkers' && req.method === 'GET') {
    try {
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      return res.end(JSON.stringify(await loadAllThinkersFromBackend()));
    } catch (e) {
      return sendJson(res, 500, { detail: e.message || 'thinkers_proxy_failed' });
    }
  }
  if (pathname === '/api/hardware' && req.method === 'GET') {
    try {
      await proxyBackendJson(res, {
        method: 'GET',
        path: '/api/v1/benchmark/hardware?apply_tuning=true',
      });
      return;
    } catch (e) {
      return sendJson(res, 500, { detail: e.message || 'hardware_proxy_failed' });
    }
  }
  if (pathname === '/api/health') {
    try {
      const health = await getFullHealth();
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify(health));
    } catch (e) {
      return sendJson(res, 500, { error: e.message });
    }
  }
  if (pathname === '/api/admin/stats' && req.method === 'GET') {
    try {
      await proxyBackendAdminJson(res, {
        method: 'GET',
        path: '/api/v1/admin/stats',
      });
      return;
    } catch (e) {
      return sendJson(res, 500, { detail: e.message || 'admin_stats_proxy_failed' });
    }
  }
  if (pathname === '/api/admin/users' && req.method === 'GET') {
    try {
      await proxyBackendAdminJson(res, {
        method: 'GET',
        path: '/api/v1/admin/users' + search,
      });
      return;
    } catch (e) {
      return sendJson(res, 500, { detail: e.message || 'admin_users_proxy_failed' });
    }
  }
  if (/^\/api\/admin\/users\/\d+$/.test(pathname) && (req.method === 'GET' || req.method === 'DELETE')) {
    try {
      const userId = pathname.split('/').pop();
      await proxyBackendAdminJson(res, {
        method: req.method,
        path: '/api/v1/admin/users/' + encodeURIComponent(String(userId || '')),
      });
      return;
    } catch (e) {
      return sendJson(res, 500, { detail: e.message || 'admin_user_proxy_failed' });
    }
  }
  if (pathname === '/api/meeting-history/prune' && req.method === 'POST') {
    try {
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      return res.end(JSON.stringify(pruneMeetingHistories()));
    } catch (e) {
      res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
      return res.end(JSON.stringify({ ok: false, error: e.message }));
    }
  }
  if (pathname === '/api/meeting-history' && req.method === 'GET') {
    try {
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      return res.end(JSON.stringify(listMeetingHistories()));
    } catch (e) {
      res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
      return res.end(JSON.stringify({ error: e.message }));
    }
  }
  if (pathname.startsWith('/api/meeting-history/') && req.method === 'DELETE') {
    const sessionId = decodeURIComponent(pathname.slice('/api/meeting-history/'.length));
    try {
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      return res.end(JSON.stringify(deleteMeetingHistory(sessionId)));
    } catch (e) {
      res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
      return res.end(JSON.stringify({ ok: false, error: e.message }));
    }
  }
  if (/^\/api\/meeting-history\/[^/]+\/recordings\/[^/]+\/audio$/.test(pathname) && req.method === 'GET') {
    const parts = pathname.split('/');
    const sessionId = decodeURIComponent(parts[3]);
    const recordingId = decodeURIComponent(parts[5]);
    try {
      const resolved = resolveMeetingRecording(sessionId, recordingId);
      const stream = fs.createReadStream(resolved.filePath);
      res.writeHead(200, {
        'Content-Type': resolved.recording.content_type || 'application/octet-stream',
        'Cache-Control': 'no-store',
      });
      stream.on('error', () => {
        if (!res.headersSent) {
          res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
        }
        res.end(JSON.stringify({ error: 'recording_stream_failed' }));
      });
      return stream.pipe(res);
    } catch (e) {
      const statusCode = e && e.code === 'ENOENT' ? 404 : 500;
      res.writeHead(statusCode, { 'Content-Type': 'application/json; charset=utf-8' });
      return res.end(JSON.stringify({ error: e.message }));
    }
  }
  if (pathname.startsWith('/api/meeting-history/') && req.method === 'GET') {
    const sessionId = decodeURIComponent(pathname.slice('/api/meeting-history/'.length));
    try {
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      return res.end(JSON.stringify(readMeetingHistory(sessionId)));
    } catch (e) {
      const statusCode = e && e.code === 'ENOENT' ? 404 : 500;
      res.writeHead(statusCode, { 'Content-Type': 'application/json; charset=utf-8' });
      return res.end(JSON.stringify({ error: e.message }));
    }
  }
  if (pathname.startsWith('/api/') && req.method === 'POST') {
    const [, , action, service] = pathname.split('/');
    let result = { ok: false, msg: 'unknown' };
    if (action === 'start' && service === 'backend') result = startBackend();
    else if (action === 'stop' && service === 'backend') result = stopBackend();
    else if (action === 'log' && service === 'backend' && pathname === '/api/log/backend/clear') {
      processes.backend.logs = [];
      result = { ok: true, msg: 'backend_log_cleared' };
    }
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify(result));
  }
  if (pathname.startsWith('/log/')) {
    const svc = pathname.slice(5);
    if (!sseClients[svc]) { res.writeHead(404); return res.end(); }
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
      'Access-Control-Allow-Origin': '*',
    });
    res.write(':ok\n\n');
    processes[svc].logs.forEach(e => res.write('data: ' + JSON.stringify(e) + '\n\n'));
    res.write('event: status\ndata: ' + JSON.stringify(getStatus()) + '\n\n');
    sseClients[svc].add(res);
    req.on('close', () => sseClients[svc].delete(res));
    return;
  }
  res.writeHead(404); res.end();
});

server.listen(DEV_PANEL_PORT, '127.0.0.1', () => {
  console.log('\n🕯  RoundTable 开发面板已启动');
  console.log('   面板地址: http://localhost:' + DEV_PANEL_PORT);
  console.log('   后端地址: ' + LOCALHOST_BACKEND_URL);
  console.log('   按 Ctrl+C 退出\n');
});

// 每 5 秒广播一次状态，确保客户端按钮始终与实际端口状态同步
setInterval(broadcastStatus, 5000);

process.on('SIGINT', () => {
  console.log('\n正在停止所有服务...');
  stopBackend();
  setTimeout(() => process.exit(0), 500);
});
