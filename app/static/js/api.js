/**
 * API & LocalStorage Key Management Module
 */
const STORAGE_KEY = "pdf_trans_ai_config_v1";

const DEFAULT_CONFIG = {
    provider: "openai",
    api_key: "",
    model: "gpt-4o-mini",
    base_url: "",
    custom_prompt: "",
    temperature: 0.3
};

const DEFAULT_MODELS = {
    openai: "gpt-4o-mini",
    deepseek: "deepseek-chat",
    gemini: "gemini-1.5-flash",
    claude: "claude-3-5-sonnet-20241022",
    custom: ""
};

const API = {
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
    }
};

window.API = API;
window.DEFAULT_MODELS = DEFAULT_MODELS;
