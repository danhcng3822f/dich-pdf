/**
 * Frontend Application Controller
 */
document.addEventListener("DOMContentLoaded", () => {
    // State
    let currentConfig = window.API.getConfig();
    let currentUploadedFile = null; // { file_id, filename, total_pages, file_size }
    let isTranslating = false;
    let pagesData = []; // list of { page_number, original_text, translated_text, image_base64 }
    let activeJobId = null;

    // DOM Elements - Settings Modal
    const settingsBtn = document.getElementById("settings-btn");
    const settingsModal = document.getElementById("settings-modal");
    const closeSettingsBtn = document.getElementById("close-settings-btn");
    const saveSettingsBtn = document.getElementById("save-settings-btn");
    const testSettingsBtn = document.getElementById("test-settings-btn");
    const testStatus = document.getElementById("test-status");

    const providerSelect = document.getElementById("provider-select");
    const apiKeyInput = document.getElementById("api-key-input");
    const modelInput = document.getElementById("model-input");
    const baseUrlGroup = document.getElementById("base-url-group");
    const baseUrlInput = document.getElementById("base-url-input");
    const customPromptInput = document.getElementById("custom-prompt-input");
    const tempInput = document.getElementById("temp-input");
    const tempValue = document.getElementById("temp-value");

    // DOM Elements - Upload & Config
    const dropZone = document.getElementById("drop-zone");
    const fileInput = document.getElementById("file-input");
    const fileInfoCard = document.getElementById("file-info-card");
    const fileNameSpan = document.getElementById("file-name");
    const fileMetaSpan = document.getElementById("file-meta");
    const removeFileBtn = document.getElementById("remove-file-btn");

    const targetLangSelect = document.getElementById("target-lang-select");
    const styleSelect = document.getElementById("style-select");
    const pageRangeInput = document.getElementById("page-range-input");
    const startTranslateBtn = document.getElementById("start-translate-btn");

    // DOM Elements - Progress & Results
    const progressSection = document.getElementById("progress-section");
    const progressBar = document.getElementById("progress-bar");
    const progressPercent = document.getElementById("progress-percent");
    const progressText = document.getElementById("progress-text");

    const resultsSection = document.getElementById("results-section");
    const pagesContainer = document.getElementById("pages-container");
    const viewModeSelect = document.getElementById("view-mode-select");

    // Download buttons
    const downloadDocxBtn = document.getElementById("download-docx-btn");
    const downloadPdfBtn = document.getElementById("download-pdf-btn");
    const downloadMdBtn = document.getElementById("download-md-btn");

    // --- Init UI from Config ---
    function populateConfigForm() {
        providerSelect.value = currentConfig.provider;
        apiKeyInput.value = currentConfig.api_key || "";
        modelInput.value = currentConfig.model || window.DEFAULT_MODELS[currentConfig.provider] || "";
        baseUrlInput.value = currentConfig.base_url || "";
        customPromptInput.value = currentConfig.custom_prompt || "";
        tempInput.value = currentConfig.temperature ?? 0.3;
        tempValue.textContent = tempInput.value;

        toggleBaseUrlVisibility();
    }

    function toggleBaseUrlVisibility() {
        const isCustom = providerSelect.value === "custom";
        baseUrlGroup.classList.toggle("hidden", !isCustom);
    }

    providerSelect.addEventListener("change", () => {
        const p = providerSelect.value;
        modelInput.value = window.DEFAULT_MODELS[p] || "";
        if (p === "deepseek") {
            baseUrlInput.value = "https://api.deepseek.com/v1";
        } else if (p === "openai") {
            baseUrlInput.value = "https://api.openai.com/v1";
        }
        toggleBaseUrlVisibility();
    });

    tempInput.addEventListener("input", () => {
        tempValue.textContent = tempInput.value;
    });

    // --- Settings Modal Events ---
    settingsBtn.addEventListener("click", () => {
        populateConfigForm();
        testStatus.textContent = "";
        settingsModal.classList.remove("hidden");
    });

    closeSettingsBtn.addEventListener("click", () => {
        settingsModal.classList.add("hidden");
    });

    saveSettingsBtn.addEventListener("click", () => {
        currentConfig = {
            provider: providerSelect.value,
            api_key: apiKeyInput.value.trim(),
            model: modelInput.value.trim(),
            base_url: baseUrlInput.value.trim(),
            custom_prompt: customPromptInput.value.trim(),
            temperature: parseFloat(tempInput.value)
        };
        window.API.saveConfig(currentConfig);
        settingsModal.classList.add("hidden");
        showToast("Đã lưu cấu hình API thành công!", "success");
    });

    testSettingsBtn.addEventListener("click", async () => {
        const testConfig = {
            provider: providerSelect.value,
            api_key: apiKeyInput.value.trim(),
            model: modelInput.value.trim(),
            base_url: baseUrlInput.value.trim(),
            custom_prompt: customPromptInput.value.trim(),
            temperature: parseFloat(tempInput.value)
        };

        if (!testConfig.api_key) {
            testStatus.innerHTML = `<span class="text-rose-500">Vui lòng nhập API Key</span>`;
            return;
        }

        testStatus.innerHTML = `<span class="text-blue-500 animate-pulse">Đang kết nối thử nghiệm...</span>`;
        try {
            const res = await window.API.testConnection(testConfig);
            testStatus.innerHTML = `<span class="text-emerald-600 font-medium">✓ Kết nối thành công! (Dịch thử: "${res.sample_translation}")</span>`;
        } catch (err) {
            testStatus.innerHTML = `<span class="text-rose-500 font-medium">✗ Lỗi: ${err.message}</span>`;
        }
    });

    // --- File Upload & Drag & Drop ---
    dropZone.addEventListener("click", () => fileInput.click());

    dropZone.addEventListener("dragover", (e) => {
        e.preventDefault();
        dropZone.classList.add("dragover");
    });

    dropZone.addEventListener("dragleave", () => {
        dropZone.classList.remove("dragover");
    });

    dropZone.addEventListener("drop", (e) => {
        e.preventDefault();
        dropZone.classList.remove("dragover");
        if (e.dataTransfer.files.length > 0) {
            handleFileUpload(e.dataTransfer.files[0]);
        }
    });

    fileInput.addEventListener("change", () => {
        if (fileInput.files.length > 0) {
            handleFileUpload(fileInput.files[0]);
        }
    });

    async function handleFileUpload(file) {
        if (!file.name.toLowerCase().endsWith(".pdf")) {
            showToast("Vui lòng chọn file định dạng PDF (.pdf)", "error");
            return;
        }

        dropZone.classList.add("opacity-50", "pointer-events-none");
        try {
            const res = await window.API.uploadPdf(file);
            currentUploadedFile = res;
            fileNameSpan.textContent = res.filename;
            fileMetaSpan.textContent = `${res.total_pages} trang • ${(res.file_size / (1024 * 1024)).toFixed(2)} MB`;

            dropZone.classList.add("hidden");
            fileInfoCard.classList.remove("hidden");
            startTranslateBtn.disabled = false;
            showToast(`Tải lên thành công: ${res.filename}`, "success");
        } catch (err) {
            showToast(err.message, "error");
        } finally {
            dropZone.classList.remove("opacity-50", "pointer-events-none");
            fileInput.value = "";
        }
    }

    removeFileBtn.addEventListener("click", () => {
        currentUploadedFile = null;
        fileInfoCard.classList.add("hidden");
        dropZone.classList.remove("hidden");
        startTranslateBtn.disabled = true;
    });

    // --- Translation Streaming (SSE) ---
    startTranslateBtn.addEventListener("click", async () => {
        if (!currentUploadedFile) {
            showToast("Vui lòng tải lên tài liệu PDF trước", "warning");
            return;
        }

        if (!currentConfig.api_key) {
            showToast("Vui lòng thiết lập API Key trong phần Cài đặt trước khi dịch", "error");
            settingsBtn.click();
            return;
        }

        // Reset UI for new run
        isTranslating = true;
        pagesData = [];
        activeJobId = null;
        pagesContainer.innerHTML = "";
        resultsSection.classList.remove("hidden");
        progressSection.classList.remove("hidden");
        setDownloadButtonsEnabled(false);
        startTranslateBtn.disabled = true;

        updateProgress(0, "Đang khởi tạo tiến trình dịch...");

        const payload = {
            file_id: currentUploadedFile.file_id,
            target_lang: targetLangSelect.value,
            style: styleSelect.value,
            page_range: pageRangeInput.value.trim() || "all",
            ai_config: currentConfig
        };

        try {
            const response = await fetch("/api/translate/stream", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                const errData = await response.json().catch(() => ({ detail: response.statusText }));
                throw new Error(errData.detail || "Không thể khởi chạy phiên dịch");
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder("utf-8");
            let buffer = "";

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const parts = buffer.split("\n\n");
                buffer = parts.pop(); // Keep last partial chunk in buffer

                for (const part of parts) {
                    if (!part.trim()) continue;
                    parseSSEEvent(part);
                }
            }
        } catch (err) {
            showToast(`Lỗi trong quá trình dịch: ${err.message}`, "error");
            updateProgress(0, `Đã dừng do lỗi: ${err.message}`);
        } finally {
            isTranslating = false;
            startTranslateBtn.disabled = false;
        }
    });

    function parseSSEEvent(eventBlock) {
        const lines = eventBlock.split("\n");
        let eventType = "message";
        let dataStr = "";

        for (const line of lines) {
            if (line.startsWith("event:")) {
                eventType = line.replace("event:", "").trim();
            } else if (line.startsWith("data:")) {
                dataStr = line.replace("data:", "").trim();
            }
        }

        if (!dataStr) return;
        try {
            const data = JSON.parse(dataStr);
            handleSSEAction(eventType, data);
        } catch (e) {
            console.error("Failed to parse SSE JSON:", e, dataStr);
        }
    }

    function handleSSEAction(type, data) {
        if (type === "start") {
            activeJobId = data.job_id;
            updateProgress(5, `Bắt đầu dịch ${data.total_pages} trang được chọn...`);
        } else if (type === "page_progress") {
            const percent = Math.round(((data.current_index - 0.5) / data.total_pages) * 90);
            updateProgress(percent, `Đang xử lý trang ${data.page_number} (${data.current_index}/${data.total_pages})...`);
        } else if (type === "page_completed") {
            pagesData.push(data);
            renderPageCard(data);
            const total = parseInt(progressBar.dataset.total || pagesData.length);
            const percent = Math.round((pagesData.length / total) * 90);
            updateProgress(percent, `Đã hoàn tất trang ${data.page_number}`);
        } else if (type === "page_error") {
            showToast(`Lỗi tại trang ${data.page_number}: ${data.error}`, "error");
        } else if (type === "completed") {
            updateProgress(100, "Hoàn tất dịch toàn bộ tài liệu!");
            activeJobId = data.job_id;
            setDownloadButtonsEnabled(true);
            showToast("Tất cả trang đã dịch xong! Bạn có thể tải file kết quả bên dưới.", "success");
        }
    }

    function updateProgress(percent, text) {
        progressBar.style.width = `${percent}%`;
        progressPercent.textContent = `${percent}%`;
        progressText.textContent = text;
    }

    function renderPageCard(page) {
        const card = document.createElement("div");
        card.className = "bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden mb-6 transition-all hover:shadow-md";
        card.id = `page-card-${page.page_number}`;

        const isSplit = viewModeSelect.value === "split";

        card.innerHTML = `
            <div class="bg-slate-50 px-5 py-3 border-b border-slate-200 flex justify-between items-center">
                <div class="flex items-center space-x-2">
                    <span class="inline-flex items-center justify-center w-7 h-7 rounded-full bg-blue-600 text-white text-xs font-bold">
                        ${page.page_number}
                    </span>
                    <h3 class="font-semibold text-slate-800 text-sm">Trang ${page.page_number}</h3>
                </div>
                <button class="copy-page-btn text-xs text-slate-500 hover:text-blue-600 flex items-center gap-1 py-1 px-2.5 rounded-lg border border-slate-200 hover:border-blue-300 bg-white">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path></svg>
                    <span>Sao chép bản dịch</span>
                </button>
            </div>
            <div class="grid ${isSplit ? 'grid-cols-1 lg:grid-cols-2' : 'grid-cols-1'} divide-y lg:divide-y-0 lg:divide-x divide-slate-200">
                <!-- Cột Trái: Ảnh Preview gốc -->
                <div class="p-4 bg-slate-100 flex flex-col items-center justify-center ${isSplit ? '' : 'hidden'} preview-col">
                    <span class="text-xs text-slate-500 font-medium mb-2 uppercase tracking-wider">Trang gốc (PDF)</span>
                    <div class="max-h-[600px] overflow-auto rounded shadow-sm border border-slate-300 bg-white">
                        <img src="${page.image_base64}" alt="Trang ${page.page_number}" class="max-w-full h-auto object-contain select-none" />
                    </div>
                </div>
                <!-- Cột Phải: Bản dịch -->
                <div class="p-5 flex flex-col justify-start bg-white">
                    <span class="text-xs text-blue-600 font-medium mb-2 uppercase tracking-wider">Bản dịch AI (${targetLangSelect.options[targetLangSelect.selectedIndex].text})</span>
                    <div class="prose prose-sm max-w-none text-slate-700 whitespace-pre-wrap leading-relaxed font-sans select-text">
                        ${escapeHTML(page.translated_text)}
                    </div>
                </div>
            </div>
        `;

        // Copy button
        const copyBtn = card.querySelector(".copy-page-btn");
        copyBtn.addEventListener("click", () => {
            navigator.clipboard.writeText(page.translated_text);
            showToast(`Đã sao chép bản dịch trang ${page.page_number}!`, "success");
        });

        pagesContainer.appendChild(card);
    }

    // View mode switch
    viewModeSelect.addEventListener("change", () => {
        const isSplit = viewModeSelect.value === "split";
        document.querySelectorAll(".preview-col").forEach(el => {
            el.classList.toggle("hidden", !isSplit);
            const parentGrid = el.parentElement;
            if (isSplit) {
                parentGrid.classList.add("lg:grid-cols-2");
            } else {
                parentGrid.classList.remove("lg:grid-cols-2");
            }
        });
    });

    // --- Export Downloads ---
    function setDownloadButtonsEnabled(enabled) {
        [downloadDocxBtn, downloadPdfBtn, downloadMdBtn].forEach(btn => {
            btn.disabled = !enabled;
            if (enabled) {
                btn.classList.remove("opacity-50", "cursor-not-allowed");
            } else {
                btn.classList.add("opacity-50", "cursor-not-allowed");
            }
        });
    }

    downloadDocxBtn.addEventListener("click", () => triggerDownload("docx"));
    downloadPdfBtn.addEventListener("click", () => triggerDownload("pdf"));
    downloadMdBtn.addEventListener("click", () => triggerDownload("md"));

    function triggerDownload(fmt) {
        if (!activeJobId) {
            showToast("Chưa có tài liệu hoàn chỉnh để tải về", "warning");
            return;
        }
        window.location.href = `/api/download/${activeJobId}/${fmt}`;
    }

    // Helper: Toast Notifications
    function showToast(msg, type = "info") {
        const toast = document.createElement("div");
        const bgColors = {
            success: "bg-emerald-600",
            error: "bg-rose-600",
            warning: "bg-amber-500",
            info: "bg-slate-800"
        };
        toast.className = `fixed bottom-5 right-5 z-50 text-white text-sm font-medium px-4 py-3 rounded-xl shadow-lg flex items-center space-x-2 transition-all transform duration-300 translate-y-3 ${bgColors[type] || bgColors.info}`;
        toast.innerHTML = `<span>${msg}</span>`;

        document.body.appendChild(toast);
        setTimeout(() => toast.classList.remove("translate-y-3"), 10);
        setTimeout(() => {
            toast.classList.add("opacity-0", "translate-y-3");
            setTimeout(() => toast.remove(), 300);
        }, 3500);
    }

    function escapeHTML(str) {
        return str
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    // Initial check
    if (!currentConfig.api_key) {
        // Subtle hint on first load
        setTimeout(() => {
            showToast("Chào bạn! Hãy nhấn 'Cài đặt API Key' ở góc trên để cấu hình AI nhé.", "info");
        }, 800);
    }
});
