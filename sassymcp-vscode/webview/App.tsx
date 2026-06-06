import { useEffect, useMemo, useRef, useState } from "react";
import type { Board, BrainStatus, Handoff, Peer, Session, VsCodeApi } from "./types";

declare function acquireVsCodeApi(): VsCodeApi;
const vscode = acquireVsCodeApi();

const ANDROID_RE = /android|mobile|adb|phone/i;
type Tab = "coord" | "dash" | "actions";

function ago(seconds: number): string {
    if (!isFinite(seconds) || seconds < 0) { return "—"; }
    if (seconds < 60) { return `${Math.round(seconds)}s ago`; }
    if (seconds < 3600) { return `${Math.round(seconds / 60)}m ago`; }
    if (seconds < 86400) { return `${Math.round(seconds / 3600)}h ago`; }
    return `${Math.round(seconds / 86400)}d ago`;
}

export function App() {
    const [board, setBoard] = useState<Board | null>(null);
    const [brain, setBrain] = useState<BrainStatus | null>(null);
    const [hermesRunning, setHermesRunning] = useState(false);
    const [hermesLog, setHermesLog] = useState<string[]>([]);
    const [updated, setUpdated] = useState<number>(0);
    const [tab, setTab] = useState<Tab>("coord");
    const logRef = useRef<HTMLDivElement | null>(null);

    useEffect(() => {
        const onMsg = (event: MessageEvent) => {
            const msg = event.data;
            if (msg?.type === "board") {
                setBoard(msg.data as Board);
                setUpdated(Date.now());
            } else if (msg?.type === "brain") {
                setBrain(msg.data as BrainStatus);
            } else if (msg?.type === "hermes") {
                if (typeof msg.running === "boolean") { setHermesRunning(msg.running); }
                if (msg.line) {
                    setHermesLog((prev) => [...prev, String(msg.line)].slice(-200));
                }
            }
        };
        window.addEventListener("message", onMsg);
        vscode.postMessage({ type: "ready" });
        return () => window.removeEventListener("message", onMsg);
    }, []);

    useEffect(() => {
        if (logRef.current) { logRef.current.scrollTop = logRef.current.scrollHeight; }
    }, [hermesLog]);

    const peers = board?.peers ?? [];
    const aliveCount = peers.filter((p) => p.alive).length + (hermesRunning ? 1 : 0);

    return (
        <div className="cockpit">
            <Header
                aliveCount={aliveCount}
                error={board?.error}
                tab={tab}
                setTab={setTab}
                onRefresh={() => vscode.postMessage({ type: "refresh" })}
            />

            {tab === "coord" && (
                <CoordinationView board={board} hermesRunning={hermesRunning} hermesLog={hermesLog} logRef={logRef} aliveCount={aliveCount} />
            )}
            {tab === "dash" && <DashboardView brain={brain} />}
            {tab === "actions" && <ActionsView hermesRunning={hermesRunning} />}

            <footer className="foot">
                <span>{board?.db ? `db: ${board.db}` : "connecting…"}{updated ? ` · updated ${ago((Date.now() - updated) / 1000)}` : ""}</span>
            </footer>
        </div>
    );
}

function Header(props: { aliveCount: number; error?: string; tab: Tab; setTab: (t: Tab) => void; onRefresh: () => void }) {
    const tabs: [Tab, string][] = [["coord", "Coordination"], ["dash", "Dashboard"], ["actions", "Actions"]];
    return (
        <header className="head">
            <div className="title">
                <span className="logo">🧠</span>
                <span className="brand">Sassy&nbsp;Brain</span>
                <nav className="tabs">
                    {tabs.map(([id, label]) => (
                        <button key={id} className={`tab ${props.tab === id ? "active" : ""}`} onClick={() => props.setTab(id)}>{label}</button>
                    ))}
                </nav>
            </div>
            <div className="head-right">
                {props.error
                    ? <span className="status err" title={props.error}>● disconnected</span>
                    : <span className="status ok">● live · {props.aliveCount} active</span>}
                <button className="btn ghost" onClick={props.onRefresh}>↻ Refresh</button>
            </div>
        </header>
    );
}

// ── Coordination tab ──────────────────────────────────────────────────
function CoordinationView(props: {
    board: Board | null; hermesRunning: boolean; hermesLog: string[];
    logRef: React.RefObject<HTMLDivElement | null>; aliveCount: number;
}) {
    const board = props.board;
    const peers = board?.peers ?? [];
    const sessions = board?.sessions ?? [];
    const peerIds = useMemo(() => new Set(peers.map((p) => p.peer_id)), [peers]);
    const idleSessions = sessions.filter((s) => !peerIds.has(s.session_id));
    const androidNodes = [
        ...peers.filter((p) => ANDROID_RE.test(`${p.platform} ${p.name}`)),
        ...idleSessions.filter((s) => ANDROID_RE.test(`${s.platform} ${s.name}`)),
    ];

    return (
        <div className="grid">
            <section className="card span2">
                <h2>Coordination mesh{props.aliveCount >= 2 && <span className="badge two-headed">⚡ {props.aliveCount} heads live</span>}</h2>
                {peers.length === 0 && (
                    <p className="empty">
                        No announced peers yet. Agents appear here when they call
                        <code> sassy_peer_announce</code>, or start the second head.
                    </p>
                )}
                <div className="peers">
                    {peers.map((p) => <PeerCard key={p.peer_id} peer={p} />)}
                </div>
                {idleSessions.length > 0 && (
                    <>
                        <h3 className="muted">Registered sessions</h3>
                        <div className="sessions">
                            {idleSessions.slice(0, 8).map((s) => <SessionChip key={s.session_id} s={s} />)}
                        </div>
                    </>
                )}
            </section>

            <section className="card">
                <h2>Second head — Hermes</h2>
                <HermesControl running={props.hermesRunning} log={props.hermesLog} logRef={props.logRef} />
            </section>

            <section className="card">
                <h2>Channels</h2>
                <Channels channels={board?.channels ?? []} />
            </section>

            <section className="card span2">
                <h2>Handoff timeline</h2>
                <Handoffs handoffs={board?.handoffs ?? []} />
            </section>

            <section className="card">
                <h2>Phone</h2>
                <AndroidTile nodes={androidNodes} />
            </section>
        </div>
    );
}

// ── Dashboard tab ─────────────────────────────────────────────────────
function DashboardView({ brain }: { brain: BrainStatus | null }) {
    useEffect(() => { vscode.postMessage({ type: "refreshBrain" }); }, []);
    if (!brain) { return <p className="empty pad">Reading brain state…</p>; }
    if (brain.error) { return <p className="empty pad">Couldn’t read brain: {brain.error}</p>; }

    const tierLabel = (brain.tier || "free").toUpperCase() + (brain.addons && brain.addons.length ? ` + ${brain.addons.join(", ")}` : "");
    const allowed = (brain.groups ?? []).filter((g) => g.allowed);
    return (
        <div className="grid">
            <Stat label="License tier" value={tierLabel} sub={brain.license_valid ? (brain.email || "valid") : "free / unlicensed"} accent />
            <Stat label="Memory entries" value={String(brain.memory_count ?? 0)} sub={`${brain.milestones ?? 0} milestones`} />
            <Stat label="Audit log" value={String(brain.audit_count ?? 0)} sub="recorded tool calls" />
            <Stat label="Persona" value={brain.persona ? "configured" : "not set"} sub={brain.persona ? "every session reads it" : "run Setup Wizard"} />
            <Stat label="Tool groups" value={`${brain.allowed_group_count ?? 0}/${brain.group_count ?? 0}`} sub={`${brain.module_allowed ?? 0}/${brain.module_total ?? 0} modules enabled`} />
            <Stat label="Version" value={`v${brain.version ?? "?"}`} sub={brain.home || ""} />

            <section className="card span2">
                <h2>Tool groups</h2>
                <div className="groups">
                    {(brain.groups ?? []).map((g) => (
                        <div key={g.name} className={`group ${g.allowed ? "on" : "off"}`} title={g.description}>
                            <span className={`dot ${g.allowed ? "g" : "x"}`} />
                            <span className="g-name">{g.name}</span>
                            <span className="g-meta">{g.module_count} mod{g.always_load ? " · auto" : ""}</span>
                        </div>
                    ))}
                </div>
                <p className="hint">{allowed.length} of {brain.groups?.length ?? 0} groups available on this tier.</p>
            </section>
        </div>
    );
}

function Stat({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: boolean }) {
    return (
        <section className="card stat">
            <div className="stat-label">{label}</div>
            <div className={`stat-value ${accent ? "accent" : ""}`}>{value}</div>
            {sub && <div className="stat-sub">{sub}</div>}
        </section>
    );
}

// ── Actions tab ───────────────────────────────────────────────────────
interface ActionDef { label: string; hint: string; fire: () => void; }

function ActionsView({ hermesRunning }: { hermesRunning: boolean }) {
    const [q, setQ] = useState("");
    const send = (m: unknown) => vscode.postMessage(m);
    const actions: ActionDef[] = [
        hermesRunning
            ? { label: "■ Stop Hermes", hint: "stop the local Ollama peer", fire: () => send({ type: "stopHermes" }) }
            : { label: "⚡ Start Hermes", hint: "bring the second head online", fire: () => send({ type: "startHermes" }) },
        { label: "📣 Announce this instance", hint: "register VS Code in the mesh", fire: () => send({ type: "announce" }) },
        { label: "🧭 Run Setup Wizard", hint: "generate persona.md", fire: () => send({ type: "action", action: "runWizard" }) },
        { label: "📜 Open Audit Log", hint: "every tool call, logged", fire: () => send({ type: "action", action: "openAudit" }) },
        { label: "🗑 Open _DELETE_ folder", hint: "safe-delete staging", fire: () => send({ type: "action", action: "openDelete" }) },
        { label: "📂 Open ~/.sassymcp", hint: "the brain on disk", fire: () => send({ type: "action", action: "openHome" }) },
        { label: "🔧 Reinstall client configs", hint: "re-patch every MCP client", fire: () => send({ type: "action", action: "reinstall" }) },
        { label: "⚙ Open SassyMCP settings", hint: "repo/python paths, hotkeys", fire: () => send({ type: "action", action: "openSettings" }) },
        { label: "↻ Refresh everything", hint: "board + brain", fire: () => send({ type: "refresh" }) },
    ];
    const filtered = q.trim()
        ? actions.filter((a) => (a.label + " " + a.hint).toLowerCase().includes(q.trim().toLowerCase()))
        : actions;

    return (
        <div className="actions-wrap">
            <input
                className="search"
                placeholder="Search actions…  (Enter runs the first match)"
                value={q}
                autoFocus
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && filtered[0]) { filtered[0].fire(); } }}
            />
            <div className="actions">
                {filtered.map((a) => (
                    <button key={a.label} className="action" onClick={a.fire}>
                        <span className="a-label">{a.label}</span>
                        <span className="a-hint">{a.hint}</span>
                    </button>
                ))}
                {filtered.length === 0 && <p className="empty">No actions match “{q}”.</p>}
            </div>
        </div>
    );
}

// ── shared bits ───────────────────────────────────────────────────────
function PeerCard({ peer }: { peer: Peer }) {
    return (
        <div className={`peer ${peer.alive ? "alive" : "stale"}`}>
            <div className="peer-top">
                <span className={`dot ${peer.alive ? "g" : "x"}`} />
                <span className="peer-name">{peer.name || peer.peer_id}</span>
                <span className="peer-plat">{peer.platform || "?"}</span>
            </div>
            <div className="caps">
                {(peer.capabilities ?? []).map((c) => <span key={c} className="cap">{c}</span>)}
                {(!peer.capabilities || peer.capabilities.length === 0) && <span className="cap dim">no caps</span>}
            </div>
            <div className="peer-foot">{ago(peer.age_seconds)}{peer.endpoint ? ` · ${peer.endpoint}` : ""}</div>
        </div>
    );
}

function SessionChip({ s }: { s: Session }) {
    return <span className="chip" title={s.session_id}>{s.name || s.session_id}<i>{s.platform}</i></span>;
}

function Channels({ channels }: { channels: { channel: string; count: number }[] }) {
    if (channels.length === 0) { return <p className="empty">No channels yet.</p>; }
    const max = Math.max(...channels.map((c) => c.count), 1);
    return (
        <ul className="channels">
            {channels.map((c) => (
                <li key={c.channel}>
                    <span className="ch-name">{c.channel}</span>
                    <span className="bar"><i style={{ width: `${(c.count / max) * 100}%` }} /></span>
                    <span className="ch-count">{c.count}</span>
                </li>
            ))}
        </ul>
    );
}

function Handoffs({ handoffs }: { handoffs: Handoff[] }) {
    if (handoffs.length === 0) { return <p className="empty">No handoffs recorded yet.</p>; }
    return (
        <ol className="timeline">
            {handoffs.map((h) => (
                <li key={h.id}>
                    <span className="t-route">
                        <b>{h.from || "?"}</b>
                        <span className="arrow">→</span>
                        <b>{h.to || "any"}</b>
                    </span>
                    <span className="t-task">{h.task || <i className="dim">(no task text)</i>}</span>
                    <span className="t-meta">{h.channel} · {ago(h.age_seconds)}</span>
                </li>
            ))}
        </ol>
    );
}

function HermesControl(props: { running: boolean; log: string[]; logRef: React.RefObject<HTMLDivElement | null> }) {
    return (
        <div className="hermes">
            <div className="hermes-row">
                <span className={`status ${props.running ? "ok" : "idle"}`}>
                    ● {props.running ? "running" : "stopped"}
                </span>
                {props.running
                    ? <button className="btn danger" onClick={() => vscode.postMessage({ type: "stopHermes" })}>Stop</button>
                    : <button className="btn primary" onClick={() => vscode.postMessage({ type: "startHermes" })}>Start Hermes</button>}
            </div>
            <p className="hint">Local Ollama peer joins the <code>joint</code> channel and trades turns with Claude.</p>
            <div className="log" ref={props.logRef}>
                {props.log.length === 0
                    ? <span className="dim">no output yet</span>
                    : props.log.map((l, i) => <div key={i} className="log-line">{l}</div>)}
            </div>
        </div>
    );
}

function AndroidTile({ nodes }: { nodes: { name?: string; platform?: string; peer_id?: string; session_id?: string }[] }) {
    if (nodes.length === 0) {
        return (
            <p className="empty">
                No phone peer yet. Connect a device over ADB and run
                <code> sassy_combo_phone_observe</code> — it joins the mesh as a coordinated actuator.
            </p>
        );
    }
    return (
        <div className="sessions">
            {nodes.map((n, i) => (
                <span key={i} className="chip phone">
                    {n.name || n.peer_id || n.session_id}<i>{n.platform}</i>
                </span>
            ))}
        </div>
    );
}
