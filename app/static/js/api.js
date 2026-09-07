/**
 * API & LocalStorage Key Management Module
 */
const STORAGE_KEY = "pdf_trans_ai_config_v1";

const DEFAULT_CONFIG = {
    provider: "google",
    api_key: "",
    model: "",
    base_url: "",
    custom_prompt: "",
    temperature: 0.3
};

const DEFAULT_MODELS = {
    google: "",
    bing: "",
    openai: "gpt-4o-mini",
    deepseek: "deepseek-chat",
    gemini: "gemini-1.5-flash",
    claude: "claude-3-5-sonnet-20241022",
    custom: ""
};

const API = {
    runtimeConfigPromise: null,

    isFreeProvider(provider) {
        const p = (provider || "").toLowerCase();
        return p === "google" || p === "bing" || p === "google_free" || p === "bing_free";
    },

    isApiKeyRequired(provider) {
        return !this.isFreeProvider(provider);
    },

    getConfig() {
        const saved = localStorage.getItem(STORAGE_KEY);
        if (saved) {
            try {
                return { ...DEFAULT_CONFIG, ...JSON.parse(saved) };
            } catch (e) {
                console.error("Failed to parse config", e);
            }
        }
        return { ...DEFAULT_CONFIG };
    },

    saveConfig(config) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
    },

    getRuntimeConfig() {
        if (!this.runtimeConfigPromise) {
            this.runtimeConfigPromise = fetch("/api/runtime-config")
                .then(res => {
                    if (!res.ok) throw new Error(`HTTP ${res.status}`);
                    return res.json();
                })
                .catch(() => ({ max_upload_size_mb: 50 }));
        }
        return this.runtimeConfigPromise;
    },

    async testConnection(config) {
        const res = await fetch("/api/test-connection", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(config)
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: res.statusText }));
            throw new Error(err.detail || "Test kết nối thất bại");
        }
        return await res.json();
    },

    async uploadPdf(file) {
        const formData = new FormData();
        formData.append("file", file);

        const res = await fetch("/api/upload", {
            method: "POST",
            body: formData
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: res.statusText }));
            throw new Error(err.detail || "Upload PDF thất bại");
        }
        return await res.json();
    },

    async deleteUpload(fileId) {
        if (!fileId) return;
        await fetch(`/api/upload/${encodeURIComponent(fileId)}`, {
            method: "DELETE",
            keepalive: true
        }).catch(() => {});
    }
};

window.API = API;
window.DEFAULT_MODELS = DEFAULT_MODELS;
