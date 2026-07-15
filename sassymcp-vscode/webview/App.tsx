import { useEffect, useMemo, useState } from "react";
import type {
    Board, BrainStatus, Handoff, HooksSummary, MemorySummary,
    Peer, PhoneState, RecentCall, Session, VsCodeApi,
} from "./types";

declare function acquireVsCodeApi(): VsCodeApi;
const vscode = acquireVsCodeApi();

type Tab = "coord" | "dash" | "actions";

function ago(seconds: number): string {
    if (!isFinite(seconds) || seconds < 0) { return "—"; }
    if (seconds < 60) { return `${Math.round(seconds)}s ago`; }
    if (seconds < 3600) { return `${Math.round(seconds / 60)}m ago`; }
    if (seconds < 86400) { return `${Math.round(seconds / 3600)}h ago`; }
    return `${Math.round(seconds / 86400)}d ago`;
}

const send = (m: unknown) => vscode.postMessage(m);

export function App() {
    const [board, setBoard] = useState<Board | null>(null);
    const [brain, setBrain] = useState<BrainStatus | null>(null);
    const [phone, setPhone] = useState<PhoneState | null>(null);
    const [updated, setUpdated] = useState<number>(0);
    const [tab, setTab] = useState<Tab>("coord");

    useEffect(() => {
        const onMsg = (event: MessageEvent) => {
            const msg = event.data;
            if (msg?.type === "board") {
                setBoard(msg.data as Board);
                setUpdated(Date.now());
            } else if (msg?.type === "brain") {
                setBrain(msg.data as BrainStatus);
            } else if (msg?.type === "phone") {
                setPhone(msg.data as PhoneState);
            }
        };
        window.addEventListener("message", onMsg);
        send({ type: "ready" });
        return () => window.removeEventListener("message", onMsg);
    }, []);

    const peers = board?.peers ?? [];
    const agentCount = peers.filter((p) => p.alive).length;
    const deviceCount = phone?.devices?.length ?? 0;

    return (
        <div className="cockpit">
            <Header
                agentCount={agentCount}
                deviceCount={deviceCount}
                error={board?.error}
                tab={tab}
                setTab={setTab}
            />

            {tab === "coord" && <CoordinationView board={board} phone={phone} agentCount={agentCount} />}
            {tab === "dash" && <DashboardView brain={brain} board={board} />}
            {tab === "actions" && <ActionsView phone={phone} />}

            <footer className="foot">
                <span>{board?.db ? `db: ${board.db}` : "connecting…"}{updated ? ` · updated ${ago((Date.now() - updated) / 1000)}` : ""}</span>
            </footer>
        </div>
    );
}

function Header(props: { agentCount: number; deviceCount: number; error?: string; tab: Tab; setTab: (t: Tab) => void }) {
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
                    : <span className="status ok">● {props.agentCount} agent{props.agentCount === 1 ? "" : "s"} · {props.deviceCount} device{props.deviceCount === 1 ? "" : "s"}</span>}
                <button className="btn ghost" onClick={() => send({ type: "refresh" })}>↻ Refresh</button>
            </div>
        </header>
    );
}

// ── Coordination tab ──────────────────────────────────────────────────
function CoordinationView(props: { board: Board | null; phone: PhoneState | null; agentCount: number }) {
    const board = props.board;
    const peers = board?.peers ?? [];
    const sessions = board?.sessions ?? [];
    const peerIds = useMemo(() => new Set(peers.map((p) => p.peer_id)), [peers]);
    const idleSessions = sessions.filter((s) => !peerIds.has(s.session_id));

    return (
        <div className="grid">
            <section className="card span2">
                <h2>Agents{props.agentCount >= 2 && <span className="badge two-headed">⚡ {props.agentCount} coordinating</span>}</h2>
                {peers.length === 0 && (
                    <p className="empty">
                        No agents in the mesh yet. Claude Desktop, Cursor, Windsurf and other MCP
                        clients show up here when they call <code>sassy_peer_announce</code>.
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
                <h2>Phone</h2>
                <PhonePanel phone={props.phone} />
            </section>

            <section className="card">
                <h2>Channels</h2>
                <Channels channels={board?.channels ?? []} />
            </section>

            <section className="card span2">
                <h2>Memory{board?.memory?.available !== false &&
                    <span className="badge">{board?.memory?.memory_count ?? 0} entries · {board?.memory?.milestone_count ?? 0} milestones</span>}
                </h2>
                <MemoryPanel memory={board?.memory} />
            </section>

            <section className="card span2">
                <h2>Handoff timeline</h2>
                <Handoffs handoffs={board?.handoffs ?? []} />
            </section>

            <section className="card span2">
                <h2>Continuity playbooks</h2>
                <HooksPanel hooks={board?.hooks} />
            </section>
        </div>
    );
}

function MemoryPanel({ memory }: { memory?: MemorySummary }) {
    if (!memory) { return <p className="empty">Reading memory…</p>; }
    if (memory.error) { return <p className="empty">Memory unavailable: {memory.error}</p>; }
    if (memory.available === false) { return <p className="empty">No memory database yet — sassy_memory_remember creates it.</p>; }
    const tasks = memory.active_tasks ?? [];
    const milestones = memory.milestones ?? [];
    return (
        <div>
            <h3 className="muted">Active task state</h3>
            {tasks.length === 0 && <p className="empty">No task-state entries. Handoffs write task_&lt;concept&gt;_&lt;project&gt;_state keys.</p>}
            <ol className="timeline">
                {tasks.map((t) => (
                    <li key={t.key}>
                        <span className="t-route"><b>{t.key}</b></span>
                        <span className="t-task">{t.value || <i className="dim">(empty)</i>}</span>
                        <span className="t-meta">{t.project || "no project"} · {t.priority} · {ago(t.age_seconds)}</span>
                    </li>
                ))}
            </ol>
            {milestones.length > 0 && (
                <>
                    <h3 className="muted">Recent milestones</h3>
                    <ol className="timeline">
                        {milestones.map((m, i) => (
                            <li key={i}>
                                <span className="t-task">{m.event}</span>
                                <span className="t-meta">{m.project || "—"} · {ago(m.age_seconds)}</span>
                            </li>
                        ))}
                    </ol>
                </>
            )}
        </div>
    );
}

function HooksPanel({ hooks }: { hooks?: HooksSummary }) {
    if (!hooks || hooks.error) { return <p className="empty">Playbooks unavailable{hooks?.error ? `: ${hooks.error}` : "…"}</p>; }
    const list = hooks.hooks ?? [];
    if (list.length === 0) { return <p className="empty">No hooks registered.</p>; }
    return (
        <div>
            <div className="caps" style={{ marginBottom: 6 }}>
                {list.map((h) => (
                    <span key={h.name} className="cap" title={h.description}>
                        <span className={`dot ${h.active ? "g" : "x"}`} /> {h.name}
                    </span>
                ))}
            </div>
            <p className="empty">
                Active on the server for every connected agent: session startup loads context and
                checks handoffs, session handoff saves state before context runs out, coordination
                keeps the mesh honest. Agents see these via sassy_hooks_list.
            </p>
        </div>
    );
}

function PhonePanel({ phone }: { phone: PhoneState | null }) {
    const devices = phone?.devices ?? [];
    if (!phone) { return <p className="empty">Checking for devices…</p>; }
    if (devices.length === 0) {
        return (
            <div>
                <p className="empty">
                    {phone.error
                        ? phone.error
                        : "No phone connected. Plug in an Android device (USB debugging on) — it joins the mesh as a node you can drive and observe."}
                </p>
                <button className="btn ghost" onClick={() => send({ type: "refreshPhone" })}>↻ Re-scan</button>
            </div>
        );
    }
    return (
        <div className="devices">
            {devices.map((d) => (
                <div key={d.serial} className="device">
                    <div className="device-top">
                        <span className={`dot ${d.state === "device" ? "g" : "x"}`} />
                        <span className="device-name">{d.model || d.serial}</span>
                        <span className="device-state">{d.state}</span>
                    </div>
                    <div className="device-serial">{d.serial}</div>
                    <div className="device-actions">
                        <button className="btn primary" onClick={() => send({ type: "action", action: "observePhone", serial: d.serial })}>Observe</button>
                        <button className="btn" onClick={() => send({ type: "action", action: "mirrorPhone", serial: d.serial })}>Mirror</button>
                    </div>
                </div>
            ))}
        </div>
    );
}

// ── Dashboard tab ─────────────────────────────────────────────────────
function DashboardView({ brain, board }: { brain: BrainStatus | null; board: Board | null }) {
    useEffect(() => { send({ type: "refreshBrain" }); }, []);
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

            <section className="card span2">
                <h2>Live activity</h2>
                <ActivityFeed calls={Array.isArray(board?.recent_calls) ? board!.recent_calls : []} />
            </section>
        </div>
    );
}

function ActivityFeed({ calls }: { calls: RecentCall[] }) {
    if (calls.length === 0) { return <p className="empty">No tool calls in the audit tail yet.</p>; }
    return (
        <ol className="timeline">
            {calls.map((c, i) => (
                <li key={i}>
                    <span className="t-route"><b>{c.tool}</b></span>
                    <span className="t-task">{c.error
                        ? <span className="err">{c.error}</span>
                        : <span className="dim">{c.elapsed_ms != null ? `${c.elapsed_ms} ms` : "ok"}</span>}</span>
                    <span className="t-meta">{c.age_seconds != null ? ago(c.age_seconds) : ""}</span>
                </li>
            ))}
        </ol>
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

function ActionsView({ phone }: { phone: PhoneState | null }) {
    const [q, setQ] = useState("");
    const firstSerial = phone?.devices?.[0]?.serial;
    const actions: ActionDef[] = [
        { label: "📣 Announce this instance", hint: "register in the agent mesh", fire: () => send({ type: "announce" }) },
        { label: "📱 Observe phone", hint: "pull the phone's screen + UI into the mesh", fire: () => send({ type: "action", action: "observePhone", serial: firstSerial }) },
        { label: "🖥 Mirror phone (scrcpy)", hint: "live screen mirror", fire: () => send({ type: "action", action: "mirrorPhone", serial: firstSerial }) },
        { label: "🧭 Run Setup Wizard", hint: "generate persona.md", fire: () => send({ type: "action", action: "runWizard" }) },
        { label: "📜 Open Audit Log", hint: "every tool call, logged", fire: () => send({ type: "action", action: "openAudit" }) },
        { label: "📂 Open ~/.sassymcp", hint: "the brain on disk", fire: () => send({ type: "action", action: "openHome" }) },
        { label: "↻ Refresh everything", hint: "agents + phone + brain", fire: () => send({ type: "refresh" }) },
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
