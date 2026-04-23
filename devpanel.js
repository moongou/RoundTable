#!/usr/bin/env node
/**
 * RoundTable 开发面板 — localhost:8888
 * 无需 npm install，使用 Node.js 内置模块运行。
 * 启动: node devpanel.js
 */

const http = require('http');
const { spawn, execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const url = require('url');

const PORT = 8888;
const ROOT = __dirname;
const BACKEND_DIR = path.join(ROOT, 'backend');
const FRONTEND_DIR = path.join(ROOT, 'frontend');

// ── 进程管理 ──────────────────────────────────────────────
const processes = {
  backend: { proc: null, logs: [], label: '后端 (FastAPI :8001)' },
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
  if (isPortListening(8001)) {
    log('backend', '⚠ 端口 8001 已被占用（外部进程）');
    broadcastStatus();
    return { ok: false, msg: '端口 8001 已被占用，请先停止再重试' };
  }
  const venvPy = path.join(BACKEND_DIR, 'venv', 'bin', 'python');
  const pyBin = fs.existsSync(venvPy) ? venvPy : 'python3';
  const proc = spawn(pyBin, ['-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8001'], {
    cwd: BACKEND_DIR,
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
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
      getAllPortPids(8001).forEach(pid => {
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
      if (!isPortListening(8001) || attempts >= 20) {
        clearInterval(pollTimer);
        _backendStopping = false;
        broadcastStatus();
      }
    }, 500);
    return { ok: true };
  }
  _backendStopping = true;
  const pids = getAllPortPids(8001);
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
    if (!isPortListening(8001) || attempts >= 20) {
      clearInterval(timer);
      _backendStopping = false;
      if (!isPortListening(8001)) {
        log('backend', '✅ 端口 8001 已释放');
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
  const backendPortUp = isPortListening(8001);
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

  // 后端 API 服务
  try {
    const data = await httpGet('http://127.0.0.1:8001/api/v1/config/current');
    const cfg = JSON.parse(data);
    results.backend_api = { name: '后端 API 服务', url: 'http://localhost:8001', reachable: true, detail: 'LLM: ' + cfg.llm_provider_name + ' › ' + cfg.model };
  } catch(e) {
    results.backend_api = { name: '后端 API 服务', url: 'http://localhost:8001', reachable: false, detail: '无法连接' };
  }

  // 前端
  const flutterEmbedded = fs.existsSync(path.join(BACKEND_DIR, 'static', 'index.html'));
  results.frontend = { name: '前端 Flutter Web', url: 'http://localhost:8001', reachable: flutterEmbedded && results.backend_api.reachable, detail: flutterEmbedded ? '已内嵌到后端 :8001' : '未构建' };

  // 开发面板
  results.devpanel = { name: '开发面板', url: 'http://localhost:8888', reachable: true, detail: '运行中' };

  // 本地服务
  try {
    const data = await httpGet('http://127.0.0.1:8001/api/v1/config/health');
    const services = JSON.parse(data);
    const nameMap = { edge_tts: 'Edge TTS', cosyvoice: 'CosyVoice', funasr: 'FunASR', ollama: 'Ollama' };
    for (const [key, val] of Object.entries(services)) {
      results[key] = { name: nameMap[key] || key, url: val.url, reachable: val.reachable, detail: val.reachable ? 'HTTP ' + val.status_code : '不可达' };
    }
  } catch(e) { /* backend unavailable */ }

  // LLM 配置
  try {
    const data = await httpGet('http://127.0.0.1:8001/api/v1/config/validate');
    const v = JSON.parse(data);
    results.llm_config = { name: 'LLM 配置验证', url: '-', reachable: v.valid, detail: v.valid ? '配置有效' : v.message };
  } catch(e) { /* skip */ }

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
    <div class="card-title">🔗 快速导航</div>
    <div style="padding:10px 0 2px;color:#94a3b8;font-size:12px;line-height:1.7">
      常用操作入口已统一收纳到页面底部工具条，避免顶部与右侧分散操作按钮。
    </div>
  </div>
  <div class="card">
    <div class="card-title">
      ❤ 系统健康检查
      <span class="status-label" id="health-summary">加载中…</span>
      <button class="btn-refresh" onclick="refreshHealth()" style="margin-left:auto">🔄 刷新</button>
    </div>
    <div class="health-grid" id="health-grid">
      <div style="padding:14px;color:#666;text-align:center">加载中…</div>
    </div>
  </div>
  <div class="card" id="card-backend">
    <div class="card-title">📟 后端运行日志 (FastAPI :8001)</div>
    <div class="log-box" id="log-backend"></div>
  </div>
  <div class="card" id="card-flutter">
    <div class="card-title">🎨 前端 Flutter Web</div>
    <div class="monitor-info" id="flutter-monitor">
      <div><span class="label">部署方式:</span> <span class="val">内嵌到后端 :8001</span></div>
      <div><span class="label">访问地址:</span> <a href="http://localhost:8001" target="_blank" rel="noopener" style="color:#d4a017">http://localhost:8001</a></div>
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
    <span class="hw-opt" id="hw-opt">点击“开始检测”以生成优化建议</span>
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
<!-- 需求23：主要操作按钮固定到页面底部 -->
<div style="position:fixed;left:0;right:0;bottom:0;background:linear-gradient(180deg,rgba(26,26,46,0) 0%,#101828 40%);padding:14px 24px;display:flex;gap:10px;justify-content:center;flex-wrap:wrap;z-index:50;border-top:1px solid #0f3460">
  <button class="btn-start" id="btn-start-backend" onclick="ctrl('backend','start')">▶ 启动后端</button>
  <button class="btn-stop" id="btn-stop-backend" onclick="ctrl('backend','stop')" disabled>⏹ 停止后端</button>
  <a class="btn-open" href="http://localhost:8001" target="_blank" rel="noopener">🏠 打开应用</a>
  <a class="btn-open" href="http://localhost:8001/browser-asr-test.html" target="_blank" rel="noopener">🎙 ASR 测试</a>
  <a class="btn-open" href="http://localhost:8001/docs" target="_blank" rel="noopener">📚 API 文档</a>
  <a class="btn-open" href="http://localhost:8001/api/v1/topics/" target="_blank" rel="noopener">💬 话题列表</a>
  <a class="btn-open" href="http://localhost:8001/api/v1/thinkers/" target="_blank" rel="noopener">🧠 思想家</a>
  <button class="btn-open" id="btn-hw-detect" onclick="fetchHardware()" style="cursor:pointer">🖥 检测并打开报告</button>
</div>
<style>body{padding-bottom:160px}</style>
<script>
function ctrl(svc, action) {
  fetch('/api/' + action + '/' + svc, {method:'POST'})
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
var box = document.getElementById('log-backend');
var es = new EventSource('/log/backend');
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
  document.getElementById('flutter-status-detail').textContent = ok ? '✓ 通过后端 :8001 正常提供服务' : s.embedded ? '⚠ 后端未启动，无法访问' : '⚠ 请先构建前端';
}
fetch('/api/status').then(function(r){return r.json()}).then(function(d) {
  updateBackendStatus(d.backend);
  updateFlutterStatus(d.flutter);
});
function refreshHealth() {
  var grid = document.getElementById('health-grid');
  grid.innerHTML = '<div style="padding:14px;color:#666;text-align:center">检查中…</div>';
  document.getElementById('health-summary').textContent = '检查中…';
  fetch('/api/health').then(function(r){return r.json()}).then(renderHealth);
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
  var summary = document.getElementById('health-summary');
  summary.textContent = ok + '/' + total + ' 服务正常';
  summary.style.color = ok === total ? '#4caf50' : ok > 0 ? '#ff9800' : '#ef5350';
}
function escHtml(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
refreshHealth();

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
    '<div class="muted">数据来源：/api/v1/benchmark/hardware?apply_tuning=true</div>';
}

// Fetch hardware info
function fetchHardware() {
  var btn = document.getElementById('btn-hw-detect');
  var reportWindow = openHardwareReportWindow();
  if (!reportWindow) return;
  btn.disabled = true;
  btn.textContent = '检测中...';
  fetch('http://localhost:8001/api/v1/benchmark/hardware?apply_tuning=true')
    .then(function(r){ return r.json(); })
    .then(function(hw) {
      var el = document.getElementById('hw-info');
      el.style.display = 'flex';
      document.getElementById('hw-chip').textContent = hw.apple_chip || hw.cpu_brand || 'CPU';
      document.getElementById('hw-cpu').textContent = hw.cpu_cores + ' cores / ' + hw.cpu_threads + ' perf';
      document.getElementById('hw-mem').textContent = hw.memory_gb + ' GB RAM';
      document.getElementById('hw-gpu').textContent = hw.mps_available ? 'MPS ✓' + (hw.gpu_cores ? ' ' + hw.gpu_cores + ' cores' : '') : hw.cuda_available ? 'CUDA ✓' : 'CPU only';

      var rt = hw.runtime_tuning || {};
      var hr = hw.hardware_report || {};
      var tuningText = 'workers=' + (rt.workers || '-') + ', prefetch=' + (rt.prefetch_batch || '-') + ', ASR预热=' + (rt.asr_warmup_interval_ms || '-') + 'ms, 设备=' + (rt.device || '-');

      document.getElementById('hw-opt').textContent = '建议并行线程: ' + (hw.recommended_workers || '-') + ' ｜ 已应用: ' + tuningText;
      writeHardwareReport(
        reportWindow,
        'RoundTable 硬件检测报告',
        buildHardwareReportBody(hw, tuningText)
      );
    })
    .catch(function() {
      document.getElementById('hw-opt').textContent = '硬件检测失败：请确认后端已启动并允许跨域访问';
      writeHardwareReport(
        reportWindow,
        'RoundTable 硬件检测报告',
        '<h1>🖥 RoundTable 硬件检测报告</h1><div class="card"><div class="title">检测失败</div><div class="row">请确认后端已经启动，并且接口 /api/v1/benchmark/hardware 可以正常访问。</div><div class="muted">API: /api/v1/benchmark/hardware?apply_tuning=true</div></div>'
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
  const { pathname } = url.parse(req.url);
  if (pathname === '/' || pathname === '/index.html') {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    return res.end(DASHBOARD_HTML);
  }
  if (pathname === '/api/status') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify(getStatus()));
  }
  if (pathname === '/api/health') {
    try {
      const health = await getFullHealth();
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify(health));
    } catch (e) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ error: e.message }));
    }
  }
  if (pathname.startsWith('/api/') && req.method === 'POST') {
    const [, , action, service] = pathname.split('/');
    let result = { ok: false, msg: 'unknown' };
    if (action === 'start' && service === 'backend') result = startBackend();
    else if (action === 'stop' && service === 'backend') result = stopBackend();
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

server.listen(PORT, '127.0.0.1', () => {
  console.log('\n🕯  RoundTable 开发面板已启动');
  console.log('   面板地址: http://localhost:' + PORT);
  console.log('   按 Ctrl+C 退出\n');
});

// 每 5 秒广播一次状态，确保客户端按钮始终与实际端口状态同步
setInterval(broadcastStatus, 5000);

process.on('SIGINT', () => {
  console.log('\n正在停止所有服务...');
  stopBackend();
  setTimeout(() => process.exit(0), 500);
});
