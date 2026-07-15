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

export interface Board {
    peers: Peer[];
    channels: { channel: string; count: number }[];
    handoffs: Handoff[];
    sessions: Session[];
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
