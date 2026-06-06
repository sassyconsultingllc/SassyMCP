"""Standalone Sassy Brain window. Inlines the built React cockpit (IIFE bundle)
into a pywebview window and bridges it to the in-process coordination layer."""

import sys
from pathlib import Path

from sassymcp.desktop.bridge import Bridge

# Dark palette supplying the --vscode-* base vars the cockpit CSS consumes
# (cockpit.css already provides --sassy/--card/--line with fallbacks).
THEME = """:root{
  --vscode-font-family:"Segoe UI",system-ui,sans-serif;
  --vscode-editor-font-family:"Cascadia Code",Consolas,monospace;
  --vscode-foreground:#e6e6e6;
  --vscode-editor-foreground:#e6e6e6;
  --vscode-editor-background:#161618;
  --vscode-descriptionForeground:#9a9aa2;
  --vscode-panel-border:rgba(140,140,150,.22);
  --vscode-editorWidget-background:#1f1f23;
  --vscode-charts-green:#3fb950; --vscode-charts-yellow:#d29922;
  --vscode-errorForeground:#f85149;
  --vscode-button-background:#d6409f; --vscode-button-foreground:#fff;
  --vscode-button-hoverBackground:#b83488;
  --vscode-input-background:#26262b; --vscode-input-foreground:#e6e6e6;
  --vscode-focusBorder:#d6409f;
}
html,body{height:100%} body{background:var(--vscode-editor-background)}
#__err{display:none;position:fixed;inset:0;z-index:99999;background:#2a0a0a;color:#ff9b9b;
 font:12px/1.5 ui-monospace,Consolas,monospace;padding:16px;white-space:pre-wrap;overflow:auto}"""

# Surfaces any JS load/runtime error to the window (no more silent black screen)
# AND to stdout via the bridge log, so a headless launch is debuggable.
ERRTRAP = """<div id="__err"></div>
<script>
(function(){
  var pending=[];
  function flush(){ try{ if(window.pywebview&&window.pywebview.api&&window.pywebview.api.log){ pending.splice(0).forEach(function(m){ window.pywebview.api.log(m); }); } }catch(_){} }
  function show(m){ var e=document.getElementById('__err'); if(e){ e.style.display='block'; e.textContent='Sassy Brain JS error:\\n\\n'+m; } pending.push(String(m).slice(0,3000)); flush(); }
  window.addEventListener('error', function(ev){ show((ev.error&&ev.error.stack)||ev.message||String(ev)); });
  window.addEventListener('unhandledrejection', function(ev){ show('promise: '+((ev.reason&&ev.reason.stack)||ev.reason)); });
  window.addEventListener('pywebviewready', flush);
  var n=0,iv=setInterval(function(){ if(window.pywebview&&window.pywebview.api){ clearInterval(iv); flush(); } else if(++n>100){clearInterval(iv);} },100);
})();
</script>"""

# Bridges the VS Code message protocol to window.pywebview.api so the SAME
# React bundle runs unmodified. Queues messages until pywebview is ready, then
# drives the live polling the VS Code host normally does.
SHIM = """<script>
(function(){
  var ready=false, queue=[];
  function dispatch(s){ try{ (JSON.parse(s)||[]).forEach(function(m){ window.postMessage(m,'*'); }); }catch(e){ console.error('dispatch',e); } }
  function call(msg){
    if(!ready || !(window.pywebview && window.pywebview.api)){ queue.push(msg); return; }
    try{ window.pywebview.api.request(JSON.stringify(msg)).then(dispatch).catch(function(e){console.error('bridge',e);}); }catch(e){ console.error('call',e); }
  }
  window.acquireVsCodeApi=function(){ return { postMessage:call, getState:function(){return null;}, setState:function(){} }; };
  function start(){
    if(ready) return; ready=true;
    queue.splice(0).forEach(call);
    setInterval(function(){ call({type:'refresh'}); }, 4000);
    setInterval(function(){ call({type:'refreshPhone'}); }, 15000);
    setInterval(function(){ call({type:'refreshBrain'}); }, 60000);
  }
  window.addEventListener('pywebviewready', start);
  var n=0, iv=setInterval(function(){ if(window.pywebview&&window.pywebview.api){ clearInterval(iv); start(); } else if(++n>100){ clearInterval(iv); } },100);
})();
</script>"""


def _assets_dir() -> Path:
    here = Path(__file__).resolve().parent
    for cand in (here / "web", here.parent.parent / "sassymcp-vscode" / "media" / "cockpit"):
        if (cand / "cockpit.js").exists():
            return cand
    return here / "web"


def build_html() -> str:
    d = _assets_dir()
    css = (d / "cockpit.css").read_text(encoding="utf-8") if (d / "cockpit.css").exists() else ""
    js_path = d / "cockpit.js"
    if not js_path.exists():
        raise FileNotFoundError(
            f"cockpit.js not found in {d}. Build it: npm run compile (in sassymcp-vscode)."
        )
    js = js_path.read_text(encoding="utf-8")
    # Inlining guard: a literal "</script>" / "</style>" inside the bundle would
    # close the tag early and blank the page. Escape the only dangerous tokens.
    js = js.replace("</script", "<\\/script")
    css = css.replace("</style", "<\\/style")
    return (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Sassy Brain</title><style>" + THEME + "\n" + css + "</style></head>"
        "<body><script>window.process=window.process||{env:{NODE_ENV:\"production\"}};</script>"
        "<div id=\"root\"></div>" + ERRTRAP + SHIM +
        "<script>" + js + "</script>" + MOUNT_PROBE + "</body></html>"
    )


# Reports whether React actually rendered into #root — a definitive headless
# signal (logged to stdout) so we can debug a blank window without a screenshot.
MOUNT_PROBE = """<script>
setTimeout(function(){
  try{
    var r=document.getElementById('root');
    var n=r?r.childElementCount:-1;
    var msg='mount-check root.children='+n+' bodylen='+(document.body.innerText||'').length;
    if(window.pywebview&&window.pywebview.api&&window.pywebview.api.log){ window.pywebview.api.log(msg); }
  }catch(e){ try{ window.pywebview.api.log('probe-fail '+e); }catch(_){} }
}, 2500);
</script>"""


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--check" in argv:
        import json
        b = Bridge()
        ready = json.loads(b.request(json.dumps({"type": "ready"})))
        types = [m.get("type") for m in ready]
        try:
            html = build_html()
            html_info = f"{len(html)} bytes from {_assets_dir()}"
        except FileNotFoundError as e:
            html_info = f"(no build: {e})"
        print(f"desktop check OK | ready -> {types} | html={html_info}")
        return 0

    try:
        import webview
    except ImportError:
        print("pywebview not installed. Run: pip install -e .[desktop]  (or pip install pywebview)", file=sys.stderr)
        return 1

    bridge = Bridge()
    webview.create_window(
        "Sassy Brain", html=build_html(), js_api=bridge,
        width=1180, height=800, min_size=(860, 560),
    )
    webview.start(debug="--debug" in argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
