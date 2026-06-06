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
html,body{height:100%} body{background:var(--vscode-editor-background)}"""

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
    return (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Sassy Brain</title><style>" + THEME + "\n" + css + "</style></head>"
        "<body><div id=\"root\"></div>" + SHIM +
        "<script>" + js + "</script></body></html>"
    )


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
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
