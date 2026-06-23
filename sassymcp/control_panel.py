"""SassyMCP Control Panel — a localhost web UI for the permission engine,
runtime config, and the live event log.

It runs as its own threaded HTTP server (stdlib http.server) in a daemon
thread, independent of the MCP transport, so the panel is reachable whether
the server runs over stdio (Claude Desktop) or HTTP. It binds 127.0.0.1
only and requires a per-install token stored owner-only in HOME, so other
local users/processes can't drive it.

Three panes:
  - Event log   — tails audit.jsonl (tool calls, intercepts, policy events)
  - Settings    — permission mode, sandbox roots, tier, key config
  - Classifiers — destructive-pattern tiers + allow/ask/deny rules

API (all under /api, token-gated):
  GET  /api/status
  GET  /api/events?limit=N
  GET  /api/settings      POST /api/settings   {mode, sandboxRoots, destructiveAction}
  GET  /api/rules         POST /api/rules       {rules: [...]}

The request routing is a pure function — handle_api(method, path, query,
body) -> (status, obj) — so it is unit-testable without binding a socket.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger("sassymcp.control_panel")

DEFAULT_PORT = 8765
_server: ThreadingHTTPServer | None = None
_thread: threading.Thread | None = None
_token: str | None = None


# ── Token ─────────────────────────────────────────────────────────────

def _token_file() -> Path:
    from sassymcp._paths import HOME
    return HOME / "control_panel.token"


def panel_token() -> str:
    """Load or create the per-install panel token (owner-only file)."""
    global _token
    if _token:
        return _token
    if os.environ.get("SASSYMCP_PANEL_TOKEN"):
        _token = os.environ["SASSYMCP_PANEL_TOKEN"]
        return _token
    tf = _token_file()
    try:
        if tf.exists():
            _token = tf.read_text().strip()
            if _token:
                return _token
    except Exception:
        pass
    _token = secrets.token_urlsafe(32)
    try:
        tf.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(tf), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, _token.encode())
        finally:
            os.close(fd)
        try:
            tf.chmod(0o600)
        except OSError:
            pass
    except Exception as e:
        logger.warning(f"Could not persist panel token: {e}")
    return _token


def _token_ok(provided: str | None) -> bool:
    if not provided:
        return False
    return secrets.compare_digest(provided, panel_token())


# ── Data access ───────────────────────────────────────────────────────

_TAIL_BYTES = 262144  # ~256 KB — enough for a few thousand recent events


def _audit_events(limit: int = 100) -> list[dict]:
    """Return the most recent audit.jsonl entries, newest first.

    Reads only the last ~256 KB of the file rather than the whole thing, so
    the 5s auto-refresh stays cheap as the log grows (it rotates at 10 MB).
    The partial first line of the tail window is dropped.
    """
    from sassymcp._paths import HOME
    jsonl = HOME / "audit.jsonl"
    if not jsonl.exists():
        return []
    try:
        size = jsonl.stat().st_size
        read_bytes = min(size, _TAIL_BYTES)
        with jsonl.open("rb") as f:
            if size > read_bytes:
                f.seek(size - read_bytes)
            chunk = f.read()
    except OSError:
        return []
    lines = chunk.decode("utf-8", errors="replace").splitlines()
    if size > read_bytes and lines:
        lines = lines[1:]  # the first line is almost certainly truncated
    out: list[dict] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out


def _status() -> dict:
    from sassymcp import policy
    from sassymcp.modules.runtime_config import get
    try:
        from sassymcp import __version__
    except Exception:
        __version__ = "?"
    tier = "free"
    try:
        from sassymcp.license import validate_license
        lic = validate_license()
        tier = lic.get("tier", "free")
        if lic.get("addons"):
            tier += "+" + ",".join(lic["addons"])
    except Exception:
        pass
    return {
        "version": __version__,
        "tier": tier,
        "effective_mode": policy.current_mode(),
        "permission_mode_raw": get("permission.mode", "") or "",
        "valid_modes": list(policy.VALID_MODES),
        "sandbox_roots": [str(r) for r in policy.sandbox_roots()],
    }


def _settings() -> dict:
    from sassymcp import policy
    from sassymcp.modules.runtime_config import get
    return {
        "effective_mode": policy.current_mode(),
        "permission.mode": get("permission.mode", "") or "",
        "valid_modes": list(policy.VALID_MODES),
        "sandboxRoots": list(get("permission.sandboxRoots", []) or []),
        "sandboxRoots_resolved": [str(r) for r in policy.sandbox_roots()],
        "interceptor.destructiveAction": get("interceptor.destructiveAction", "block"),
        "tier": _status()["tier"],
    }


def _apply_settings(body: dict) -> tuple[int, dict]:
    from sassymcp import policy
    from sassymcp.modules.runtime_config import set_val
    if "mode" in body:
        m = str(body["mode"] or "").strip().lower()
        if m and m not in policy.VALID_MODES:
            return 400, {"error": f"invalid mode {m!r}", "valid": list(policy.VALID_MODES)}
        set_val("permission.mode", m)
    if "sandboxRoots" in body:
        roots = body["sandboxRoots"]
        if not isinstance(roots, list) or not all(isinstance(r, str) for r in roots):
            return 400, {"error": "sandboxRoots must be a list of strings"}
        set_val("permission.sandboxRoots", roots)
    if "destructiveAction" in body:
        da = str(body["destructiveAction"] or "block").strip().lower()
        if da not in ("block", "confirm"):
            return 400, {"error": "destructiveAction must be 'block' or 'confirm'"}
        set_val("interceptor.destructiveAction", da)
    return 200, _settings()


def _classifiers() -> dict:
    """Read-only view of the built-in destructive-command classifiers that
    gate the shell, so the panel can show *why* a command would be caught."""
    try:
        from sassymcp.modules import _security as sec
    except Exception:
        return {"delete_keywords": [], "word_blocks": [], "hard_blocks": [], "pattern_tiers": {}}
    return {
        "delete_keywords": sorted(getattr(sec, "_DELETE_KEYWORDS", [])),
        "word_blocks": sorted(getattr(sec, "_WORD_MATCH_BLOCKS", [])),
        "hard_blocks": sorted(getattr(sec, "_HARDCODED_BLOCKS", [])),
        "pattern_tiers": dict(getattr(sec, "_PATTERN_TIERS", {})),
    }


def _rules() -> dict:
    from sassymcp.modules.runtime_config import get
    return {"rules": list(get("permission.rules", []) or [])}


def _apply_rules(body: dict) -> tuple[int, dict]:
    from sassymcp.modules.runtime_config import set_val
    rules = body.get("rules")
    if not isinstance(rules, list):
        return 400, {"error": "body must be {\"rules\": [...]}"}
    for r in rules:
        if not isinstance(r, dict) or str(r.get("action", "")).lower() not in ("allow", "ask", "deny"):
            return 400, {"error": "each rule needs action in allow|ask|deny", "bad": r}
    set_val("permission.rules", rules)
    return 200, _rules()


# ── Pure router (unit-testable) ───────────────────────────────────────

def handle_api(method: str, path: str, query: dict, body: dict | None) -> tuple[int, dict]:
    """Route an API request to a (status_code, json_obj). No I/O on sockets."""
    body = body or {}
    if path == "/api/status" and method == "GET":
        return 200, _status()
    if path == "/api/events" and method == "GET":
        try:
            limit = int((query.get("limit") or ["100"])[0])
        except (ValueError, TypeError):
            limit = 100
        limit = max(1, min(limit, 1000))
        return 200, {"events": _audit_events(limit)}
    if path == "/api/settings":
        if method == "GET":
            return 200, _settings()
        if method == "POST":
            return _apply_settings(body)
    if path == "/api/classifiers" and method == "GET":
        return 200, _classifiers()
    if path == "/api/rules":
        if method == "GET":
            return 200, _rules()
        if method == "POST":
            return _apply_rules(body)
    return 404, {"error": f"no route for {method} {path}"}


# ── HTTP handler ──────────────────────────────────────────────────────

def _provided_token(handler: BaseHTTPRequestHandler, query: dict) -> str | None:
    h = handler.headers.get("X-Panel-Token")
    if h:
        return h
    q = query.get("token")
    if q:
        return q[0]
    return None


class _PanelHandler(BaseHTTPRequestHandler):
    server_version = "SassyMCPPanel"

    def log_message(self, *a):  # silence stdlib request logging
        pass

    def _send(self, status: int, body: bytes, ctype: str):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, status: int, obj: dict):
        self._send(status, json.dumps(obj).encode("utf-8"), "application/json")

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        token = _provided_token(self, query)
        if parsed.path in ("/", "/index.html"):
            if not _token_ok(token):
                self._send(403, _AUTH_HINT.encode("utf-8"), "text/html; charset=utf-8")
                return
            self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path.startswith("/api/"):
            if not _token_ok(token):
                self._json(403, {"error": "missing or bad token"})
                return
            status, obj = handle_api("GET", parsed.path, query, None)
            self._json(status, obj)
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        token = _provided_token(self, query)
        if not parsed.path.startswith("/api/"):
            self._send(404, b"not found", "text/plain")
            return
        if not _token_ok(token):
            self._json(403, {"error": "missing or bad token"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"error": "body must be JSON"})
            return
        status, obj = handle_api("POST", parsed.path, query, body)
        self._json(status, obj)


# ── Lifecycle ─────────────────────────────────────────────────────────

# Loopback is non-negotiable: bearer-style token over plain HTTP is only
# safe when the packets never leave the host. start_panel always binds here.
_LOOPBACK = "127.0.0.1"


def coerce_port(val, default: int = DEFAULT_PORT) -> int:
    """Best-effort int port from possibly-bad config (string, None, junk)."""
    try:
        p = int(val)
    except (TypeError, ValueError):
        return default
    return p if 1 <= p <= 65535 else default


def current_port() -> int | None:
    """The actual bound port if the panel is running, else None."""
    return _server.server_address[1] if _server is not None else None


def start_panel(port: int = DEFAULT_PORT) -> dict:
    """Start the panel in a daemon thread (idempotent). Returns {url, token}.

    Always binds loopback (127.0.0.1) — never an externally reachable
    interface, regardless of config. If the preferred port is taken, tries
    the next few before giving up.
    """
    global _server, _thread
    if _server is not None:
        return panel_info()  # reports the actual bound port
    port = coerce_port(port)
    last_err = None
    for p in range(port, port + 10):
        try:
            srv = ThreadingHTTPServer((_LOOPBACK, p), _PanelHandler)
        except OSError as e:
            last_err = e
            continue
        _server = srv
        _thread = threading.Thread(target=srv.serve_forever, name="sassymcp-panel", daemon=True)
        _thread.start()
        info = panel_info()
        logger.info(f"Control Panel on {info['url']}")
        return info
    logger.warning(f"Control Panel could not bind a port: {last_err}")
    return {"url": None, "token": panel_token(), "error": str(last_err)}


def stop_panel() -> None:
    global _server, _thread
    if _server is not None:
        try:
            _server.shutdown()
            _server.server_close()
        except Exception:
            pass
    _server = None
    _thread = None


def panel_info(port: int | None = None) -> dict:
    """URL + token. When the panel is running, reports the ACTUAL bound
    port (which may differ from `port` if the preferred one was taken);
    otherwise falls back to `port` (or the default) as a best guess."""
    tok = panel_token()
    p = current_port() or coerce_port(DEFAULT_PORT if port is None else port)
    return {"url": f"http://{_LOOPBACK}:{p}/?token={tok}", "token": tok, "port": p}


def is_running() -> bool:
    return _server is not None


_AUTH_HINT = (
    "<!doctype html><meta charset=utf-8><body style='font-family:system-ui;"
    "background:#0d1117;color:#c9d1d9;padding:2rem'>"
    "<h2>SassyMCP Control Panel</h2><p>Append your panel token: "
    "<code>?token=...</code></p><p>Find it via <code>sassy_panel status</code> "
    "or in <code>~/.sassymcp/control_panel.token</code>.</p></body>"
)

# The single-page UI. Vanilla JS, no external deps (CSP-clean). It reads the
# token from the URL and sends it as X-Panel-Token on every API call.
INDEX_HTML = r"""<!doctype html>
<html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>SassyMCP Control Panel</title>
<style>
  :root{--bg:#0d1117;--panel:#161b22;--line:#30363d;--fg:#c9d1d9;--muted:#8b949e;--accent:#58a6ff;--ok:#3fb950;--warn:#d29922;--bad:#f85149}
  *{box-sizing:border-box}
  body{margin:0;font-family:ui-sans-serif,system-ui,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--fg)}
  header{display:flex;align-items:center;gap:1rem;padding:.75rem 1rem;border-bottom:1px solid var(--line);background:var(--panel)}
  header h1{font-size:1rem;margin:0;font-weight:600}
  header .meta{color:var(--muted);font-size:.8rem}
  .pill{padding:.1rem .5rem;border:1px solid var(--line);border-radius:999px;font-size:.75rem}
  nav{display:flex;gap:.25rem;padding:.5rem 1rem;border-bottom:1px solid var(--line)}
  nav button{background:transparent;border:1px solid transparent;color:var(--muted);padding:.4rem .8rem;border-radius:6px;cursor:pointer;font-size:.85rem}
  nav button.active{color:var(--fg);background:var(--bg);border-color:var(--line)}
  main{padding:1rem;max-width:1100px}
  .pane{display:none}.pane.active{display:block}
  table{width:100%;border-collapse:collapse;font-size:.8rem}
  th,td{text-align:left;padding:.35rem .5rem;border-bottom:1px solid var(--line);vertical-align:top}
  th{color:var(--muted);font-weight:500}
  td.cmd{font-family:ui-monospace,Consolas,monospace;color:var(--fg);word-break:break-all}
  .tag{font-size:.7rem;padding:.05rem .4rem;border-radius:4px;border:1px solid var(--line)}
  .tag.block,.tag.deny{color:var(--bad);border-color:var(--bad)} .tag.allow{color:var(--ok);border-color:var(--ok)}
  .tag.ask,.tag.confirm{color:var(--warn);border-color:var(--warn)}
  label{display:block;margin:.75rem 0 .25rem;color:var(--muted);font-size:.8rem}
  select,input,textarea{background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:.4rem .5rem;font-size:.85rem;width:100%;max-width:520px}
  textarea{min-height:160px;font-family:ui-monospace,Consolas,monospace}
  button.act{background:var(--accent);color:#04101f;border:none;border-radius:6px;padding:.45rem .9rem;cursor:pointer;font-weight:600;margin-top:.75rem}
  .row{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap}
  .chip{display:inline-flex;gap:.4rem;align-items:center;border:1px solid var(--line);border-radius:6px;padding:.2rem .5rem;font-size:.8rem;font-family:ui-monospace,monospace}
  .chip button{background:none;border:none;color:var(--bad);cursor:pointer;font-size:1rem;line-height:1}
  .hint{color:var(--muted);font-size:.78rem;margin:.25rem 0 0}
  .toast{position:fixed;right:1rem;bottom:1rem;background:var(--panel);border:1px solid var(--line);padding:.6rem .9rem;border-radius:8px;font-size:.85rem;opacity:0;transition:opacity .2s}
  .toast.show{opacity:1}
  .toast.err{border-color:var(--bad);color:var(--bad)}
</style></head>
<body>
<header>
  <h1>SassyMCP <span class=meta>Control Panel</span></h1>
  <span class=pill id=mode>mode …</span>
  <span class=pill id=tier>tier …</span>
  <span class=meta id=ver></span>
</header>
<nav>
  <button data-pane=events class=active>Event log</button>
  <button data-pane=settings>Settings</button>
  <button data-pane=rules>Classifiers &amp; rules</button>
</nav>
<main>
  <section class="pane active" id=events>
    <div class=row><button class=act id=refresh>Refresh</button>
      <label style="margin:0">auto every 5s <input type=checkbox id=auto style="width:auto"></label></div>
    <table><thead><tr><th>time</th><th>event</th><th>tool</th><th>detail</th></tr></thead>
      <tbody id=evrows></tbody></table>
  </section>

  <section class=pane id=settings>
    <label>Permission mode</label>
    <select id=setmode></select>
    <p class=hint id=modehint></p>
    <label>Sandbox roots (the jail — one per line, absolute paths)</label>
    <textarea id=roots placeholder="V:\Projects\MyApp"></textarea>
    <label>Legacy interceptor.destructiveAction (used when mode is unset)</label>
    <select id=destr><option value=block>block</option><option value=confirm>confirm</option></select>
    <div><button class=act id=savesettings>Save settings</button></div>
  </section>

  <section class=pane id=rules>
    <h3 style="margin:.25rem 0">Built-in classifiers <span class=hint>(read-only — what the shell gates by default)</span></h3>
    <div id=classifiers></div>
    <h3 style="margin:1.25rem 0 .25rem">Your allow / ask / deny rules</h3>
    <p class=hint>First match wins, evaluated before the mode default.
      Fields: action (allow|ask|deny), and any of tool (glob), path (glob), command (regex).</p>
    <div id=rulelist></div>
    <label>Add rule (JSON)</label>
    <textarea id=newrule placeholder='{"action":"deny","tool":"sassy_shell","command":"rm"}'></textarea>
    <div class=row><button class=act id=addrule>Add rule</button>
      <button class=act id=saverules style="background:#238636;color:#fff">Save all rules</button></div>
  </section>
</main>
<div class=toast id=toast></div>
<script>
const TOKEN = new URLSearchParams(location.search).get('token') || '';
const H = {'X-Panel-Token':TOKEN,'Content-Type':'application/json'};
let RULES = [];
function toast(m,err){const t=document.getElementById('toast');t.textContent=m;t.className='toast show'+(err?' err':'');setTimeout(()=>t.className='toast',2200);}
async function api(path,method,body){
  const r=await fetch('/api'+path,{method:method||'GET',headers:H,body:body?JSON.stringify(body):undefined});
  const j=await r.json().catch(()=>({}));
  if(!r.ok){toast(j.error||('HTTP '+r.status),true);throw new Error(j.error||r.status);}
  return j;
}
function esc(s){return (s==null?'':String(s)).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
// tabs
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('nav button').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.pane').forEach(x=>x.classList.remove('active'));
  b.classList.add('active');document.getElementById(b.dataset.pane).classList.add('active');
});
// header + status
async function loadStatus(){const s=await api('/status');
  document.getElementById('mode').textContent='mode: '+s.effective_mode;
  document.getElementById('tier').textContent='tier: '+s.tier;
  document.getElementById('ver').textContent='v'+s.version;}
// events
function eventTag(e){const t=(e.event||e.type||'').toLowerCase();
  let cls='';if(t.includes('block')||t.includes('deny'))cls='deny';
  else if(t.includes('allow'))cls='allow';else if(t.includes('confirm')||t.includes('ask'))cls='ask';
  return '<span class="tag '+cls+'">'+esc(e.event||e.type||'event')+'</span>';}
async function loadEvents(){const {events}=await api('/events?limit=200');
  document.getElementById('evrows').innerHTML=events.map(e=>{
    const ts=e.timestamp?new Date(e.timestamp*1000).toLocaleTimeString():(e.time||'');
    const detail=esc(e.command||e.pattern||e.detail||JSON.stringify(e.extra||{}));
    return '<tr><td>'+esc(ts)+'</td><td>'+eventTag(e)+'</td><td>'+esc(e.tool_name||e.tool||'')+'</td><td class=cmd>'+detail+'</td></tr>';
  }).join('')||'<tr><td colspan=4 class=hint>No events yet.</td></tr>';}
document.getElementById('refresh').onclick=loadEvents;
let autoTimer=null;
document.getElementById('auto').onchange=e=>{if(e.target.checked){autoTimer=setInterval(loadEvents,5000);}else{clearInterval(autoTimer);}};
// settings
const MODE_HINTS={strict:'Block destructive patterns everywhere.',confirm:'Destructive patterns need a confirm token.',sandbox:'Relaxed inside the jail; anything outside the roots is refused.',bypass:'Allow everything except protected paths.','':'Derive from the legacy destructiveAction setting.'};
async function loadSettings(){const s=await api('/settings');
  const sel=document.getElementById('setmode');
  sel.innerHTML=['',...s.valid_modes].map(m=>'<option value="'+m+'">'+(m||'(unset — derived)')+'</option>').join('');
  sel.value=s['permission.mode']||'';
  document.getElementById('modehint').textContent=MODE_HINTS[sel.value]||'';
  document.getElementById('roots').value=(s.sandboxRoots||[]).join('\n');
  document.getElementById('destr').value=s['interceptor.destructiveAction']||'block';}
document.getElementById('setmode').onchange=e=>{document.getElementById('modehint').textContent=MODE_HINTS[e.target.value]||'';};
document.getElementById('savesettings').onclick=async()=>{
  const roots=document.getElementById('roots').value.split('\n').map(x=>x.trim()).filter(Boolean);
  await api('/settings','POST',{mode:document.getElementById('setmode').value,sandboxRoots:roots,destructiveAction:document.getElementById('destr').value});
  toast('Settings saved');loadStatus();loadSettings();};
// rules
function renderRules(){document.getElementById('rulelist').innerHTML=RULES.map((r,i)=>
  '<div class=chip><span class="tag '+esc(r.action)+'">'+esc(r.action)+'</span>'+
  esc(JSON.stringify({tool:r.tool,path:r.path,command:r.command}))+
  '<button data-i="'+i+'">×</button></div>').join('')||'<p class=hint>No rules.</p>';
  document.querySelectorAll('#rulelist .chip button').forEach(b=>b.onclick=()=>{RULES.splice(+b.dataset.i,1);renderRules();});}
async function loadClassifiers(){const c=await api('/classifiers');
  const tierTag=t=>'<span class="tag '+(t==='high'?'deny':t==='medium'?'ask':'')+'">'+esc(t)+'</span>';
  const kw=(c.delete_keywords||[]).map(k=>'<span class=chip>'+esc(k)+'</span>').join(' ');
  const wb=(c.word_blocks||[]).map(k=>'<span class=chip>'+esc(k)+'</span>').join(' ');
  const hb=(c.hard_blocks||[]).map(k=>'<span class=chip>'+esc(k)+'</span>').join(' ');
  const pt=Object.entries(c.pattern_tiers||{}).map(([k,v])=>'<tr><td class=cmd>'+esc(k)+'</td><td>'+tierTag(v)+'</td></tr>').join('');
  document.getElementById('classifiers').innerHTML=
    '<p class=hint>Delete keywords (auto-staged to _DELETE_ when leading):</p><div class=row>'+kw+'</div>'+
    '<p class=hint style="margin-top:.6rem">Always-blocked, every mode (catastrophic):</p><div class=row>'+hb+' '+wb+'</div>'+
    '<p class=hint style="margin-top:.6rem">Tiered regex patterns:</p><table><tbody>'+pt+'</tbody></table>';}
async function loadRules(){const {rules}=await api('/rules');RULES=rules||[];renderRules();}
document.getElementById('addrule').onclick=()=>{try{const r=JSON.parse(document.getElementById('newrule').value);
  if(!['allow','ask','deny'].includes((r.action||'').toLowerCase())){toast('action must be allow|ask|deny',true);return;}
  RULES.push(r);renderRules();document.getElementById('newrule').value='';}catch(e){toast('rule must be valid JSON',true);}};
document.getElementById('saverules').onclick=async()=>{await api('/rules','POST',{rules:RULES});toast('Rules saved');};
// init
loadStatus();loadEvents();loadSettings();loadClassifiers();loadRules();
</script>
</body></html>
"""
