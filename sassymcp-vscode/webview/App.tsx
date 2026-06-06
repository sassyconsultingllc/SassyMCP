import { useEffect, useMemo, useRef, useState } from "react";
import type { Board, Handoff, Peer, Session, VsCodeApi } from "./types";

declare function acquireVsCodeApi(): VsCodeApi;
const vscode = acquireVsCodeApi();

const ANDROID_RE = /android|mobile|adb|phone/i;

function ago(seconds: number): string {
    if (!isFinite(seconds) || seconds < 0) { return "—"; }
    if (seconds < 60) { return `${Math.round(seconds)}s ago`; }
    if (seconds < 3600) { return `${Math.round(seconds / 60)}m ago`; }
    if (seconds < 86400) { return `${Math.round(seconds / 3600)}h ago`; }
    return `${Math.round(seconds / 86400)}d ago`;
}

export function App() {
    const [board, setBoard] = useState<Board | null>(null);
    const [hermesRunning, setHermesRunning] = useState(false);
    const [hermesLog, setHermesLog] = useState<string[]>([]);
    const [updated, setUpdated] = useState<number>(0);
    const logRef = useRef<HTMLDivElement | null>(null);

    useEffect(() => {
        const onMsg = (event: MessageEvent) => {
            const msg = event.data;
            if (msg?.type === "board") {
                setBoard(msg.data as Board);
                setUpdated(Date.now());
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
    const sessions = board?.sessions ?? [];
    const aliveCount = peers.filter((p) => p.alive).length + (hermesRunning ? 1 : 0);

    // Sessions that aren't already represented as a live peer (idle history).
    const peerIds = useMemo(() => new Set(peers.map((p) => p.peer_id)), [peers]);
    const idleSessions = sessions.filter((s) => !peerIds.has(s.session_id));
    const androidNodes = [
        ...peers.filter((p) => ANDROID_RE.test(`${p.platform} ${p.name}`)),
        ...idleSessions.filter((s) => ANDROID_RE.test(`${s.platform} ${s.name}`)),
    ];

    return (
        <div className="cockpit">
            <Header
                aliveCount={aliveCount}
                updated={updated}
                error={board?.error}
                onRefresh={() => vscode.postMessage({ type: "refresh" })}
            />

            <div className="grid">
                <section className="card span2">
                    <h2>Coordination mesh{aliveCount >= 2 && <span className="badge two-headed">⚡ {aliveCount} heads live</span>}</h2>
                    {peers.length === 0 && (
                        <p className="empty">
                            No announced peers yet. Agents appear here when they call
                            <code> sassy_peer_announce</code>, or start the second head below.
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
                    <HermesControl running={hermesRunning} log={hermesLog} logRef={logRef} />
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

            <footer className="foot">
                <span>{board?.db ? `db: ${board.db}` : "connecting…"}</span>
            </footer>
        </div>
    );
}

function Header(props: { aliveCount: number; updated: number; error?: string; onRefresh: () => void }) {
    return (
        <header className="head">
            <div className="title">
                <span className="logo">🧠</span>
                <span className="brand">Sassy&nbsp;Brain</span>
                <span className="sub">Coordination cockpit</span>
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
