export interface Peer {
    peer_id: string;
    name: string;
    platform: string;
    capabilities: string[];
    endpoint: string;
    last_seen: string;
    age_seconds: number;
    alive: boolean;
}

export interface Session {
    session_id: string;
    name: string;
    platform: string;
    last_seen: string;
    created_at: string;
}

export interface Handoff {
    id: number;
    channel: string;
    from: string;
    to: string;
    task: string;
    created_at: string;
    age_seconds: number;
}

export interface MemoryEntry {
    key: string;
    priority: string;
    project: string;
    tags: string[];
    age_seconds: number;
    value?: string;
}

export interface Milestone {
    event: string;
    project: string;
    age_seconds: number;
}

export interface MemorySummary {
    available?: boolean;
    memory_count?: number;
    milestone_count?: number;
    recent?: MemoryEntry[];
    active_tasks?: MemoryEntry[];
    milestones?: Milestone[];
    db?: string;
    error?: string;
}

export interface RecentCall {
    tool: string;
    elapsed_ms?: number | null;
    error?: string | null;
    age_seconds?: number | null;
}

export interface HookInfo {
    name: string;
    module: string;
    description: string;
    triggers: string[];
    active: boolean;
}

export interface HooksSummary {
    hooks?: HookInfo[];
    active_count?: number;
    error?: string;
}

export interface Board {
    peers: Peer[];
    channels: { channel: string; count: number }[];
    handoffs: Handoff[];
    sessions: Session[];
    memory?: MemorySummary;
    recent_calls?: RecentCall[] | { error?: string };
    hooks?: HooksSummary;
    db?: string;
    generated_at?: string;
    error?: string;
}

export interface BrainGroup {
    name: string;
    module_count: number;
    always_load: boolean;
    allowed: boolean;
    description: string;
}

export interface BrainStatus {
    version?: string;
    tier?: string;
    addons?: string[];
    license_valid?: boolean;
    email?: string;
    memory_count?: number;
    milestones?: number;
    projects?: string[];
    audit_count?: number;
    persona?: boolean;
    groups?: BrainGroup[];
    group_count?: number;
    allowed_group_count?: number;
    module_total?: number;
    module_allowed?: number;
    home?: string;
    error?: string;
}

export interface PhoneDevice {
    serial: string;
    model?: string;
    state?: string;
}

export interface PhoneState {
    devices: PhoneDevice[];
    adb?: boolean;
    error?: string;
}

export interface VsCodeApi {
    postMessage(msg: unknown): void;
    getState(): unknown;
    setState(state: unknown): void;
}
