/**
 * LaTeX Beamer & Document Visual Renderer
 * Parses LaTeX/Beamer syntax into visual HTML elements resembling compiled Beamer slides.
 */
function renderBeamerToHtml(rawText, pageNumber = 1) {
    if (!rawText || !rawText.trim()) {
        return '<div class="text-slate-400 italic">Trang trống</div>';
    }

    let text = rawText.trim();

    // If text does not contain LaTeX environments, treat as standard Markdown + Math
    const hasLatexEnvs = /\\begin\{(frame|block|alertblock|exampleblock|definition|theorem|itemize|enumerate|columns)\}/i.test(text) ||
                         /\\frametitle\{/i.test(text);

    if (!hasLatexEnvs) {
        // Fallback to Markdown with enhanced container styling
        let mdHtml = window.marked ? window.marked.parse(text) : escapeHTML(text);
        return `
            <div class="beamer-slide-frame bg-white rounded-xl shadow-md border border-slate-200 overflow-hidden font-sans">
                <div class="beamer-header bg-gradient-to-r from-blue-800 to-indigo-900 text-white px-5 py-3 flex justify-between items-center">
                    <span class="font-bold text-sm tracking-wide">Trang ${pageNumber}</span>
                    <span class="text-xs text-blue-200 font-mono">Slide ${pageNumber}</span>
                </div>
                <div class="p-6 text-slate-800 leading-relaxed text-sm">
                    ${mdHtml}
                </div>
            </div>
        `;
    }

    // Extract frame title if present
    let frameTitle = `Trang ${pageNumber}`;
    const frameTitleMatch = text.match(/\\begin\{frame\}(?:\[.*?\])?\{([^}]*)\}/i) || text.match(/\\frametitle\{([^}]*)\}/i);
    if (frameTitleMatch && frameTitleMatch[1]) {
        frameTitle = frameTitleMatch[1];
    }

    // Clean frame wrappers
    text = text.replace(/\\begin\{frame\}(?:\[.*?\])?(?:\{[^}]*\})?/gi, "");
    text = text.replace(/\\end\{frame\}/gi, "");
    text = text.replace(/\\frametitle\{[^}]*\}/gi, "");

    // 1. Process Alert Blocks
    text = text.replace(/\\begin\{alertblock\}\{([^}]*)\}([\s\S]*?)\\end\{alertblock\}/gi, (match, title, content) => {
        return `
            <div class="beamer-block-alert my-4 rounded-lg overflow-hidden border border-rose-300 shadow-sm bg-rose-50/50">
                <div class="bg-rose-600 text-white px-4 py-2 text-xs font-bold flex items-center gap-1.5 shadow-sm">
                    <span class="inline-block w-2 h-2 rounded-full bg-white"></span>
                    <span>${title || 'Chú ý'}</span>
                </div>
                <div class="p-4 text-slate-800 text-sm leading-relaxed">${content.trim()}</div>
            </div>
        `;
    });

    // 2. Process Example Blocks
    text = text.replace(/\\begin\{exampleblock\}\{([^}]*)\}([\s\S]*?)\\end\{exampleblock\}/gi, (match, title, content) => {
        return `
            <div class="beamer-block-example my-4 rounded-lg overflow-hidden border border-emerald-300 shadow-sm bg-emerald-50/50">
                <div class="bg-emerald-600 text-white px-4 py-2 text-xs font-bold flex items-center gap-1.5 shadow-sm">
                    <span class="inline-block w-2 h-2 rounded-full bg-white"></span>
                    <span>${title || 'Ví dụ'}</span>
                </div>
                <div class="p-4 text-slate-800 text-sm leading-relaxed">${content.trim()}</div>
            </div>
        `;
    });

    // 3. Process Standard Blocks / Definitions / Theorems
    text = text.replace(/\\begin\{(block|definition|theorem|lemma|corollary)\}\{([^}]*)\}([\s\S]*?)\\end\{\1\}/gi, (match, env, title, content) => {
        const envNames = {
            block: title || 'Khối thông tin',
            definition: `Định nghĩa: ${title || ''}`,
            theorem: `Định lý: ${title || ''}`,
            lemma: `Bổ đề: ${title || ''}`,
            corollary: `Hệ quả: ${title || ''}`
        };
        const displayTitle = envNames[env.toLowerCase()] || title || 'Khối nội dung';
        return `
            <div class="beamer-block-standard my-4 rounded-lg overflow-hidden border border-blue-200 shadow-sm bg-blue-50/40">
                <div class="bg-blue-700 text-white px-4 py-2 text-xs font-bold flex items-center gap-1.5 shadow-sm">
                    <span class="inline-block w-2 h-2 rounded-full bg-blue-300"></span>
                    <span>${displayTitle}</span>
                </div>
                <div class="p-4 text-slate-800 text-sm leading-relaxed">${content.trim()}</div>
            </div>
        `;
    });

    // 4. Process Columns
    text = text.replace(/\\begin\{columns\}([\s\S]*?)\\end\{columns\}/gi, (match, colsContent) => {
        let cols = colsContent.split(/\\column\{[^}]*\}/gi).filter(c => c.trim().length > 0);
        if (cols.length === 0) return colsContent;
        let colsHtml = cols.map(c => `<div class="flex-1 min-w-[200px]">${c.trim()}</div>`).join('');
        return `<div class="grid grid-cols-1 md:grid-cols-${Math.min(cols.length, 3)} gap-4 my-3">${colsHtml}</div>`;
    });

    // 5. Process Itemize & Enumerate
    text = text.replace(/\\begin\{itemize\}([\s\S]*?)\\end\{itemize\}/gi, (match, listContent) => {
        let items = listContent.split(/\\item\s+/gi).filter(it => it.trim().length > 0);
        let itemsHtml = items.map(it => `
            <li class="flex items-start gap-2.5 my-1.5 text-slate-800">
                <span class="text-blue-600 font-bold mt-0.5 text-xs">▶</span>
                <span>${it.trim()}</span>
            </li>
        `).join('');
        return `<ul class="my-3 space-y-1">${itemsHtml}</ul>`;
    });

    text = text.replace(/\\begin\{enumerate\}([\s\S]*?)\\end\{enumerate\}/gi, (match, listContent) => {
        let items = listContent.split(/\\item\s+/gi).filter(it => it.trim().length > 0);
        let itemsHtml = items.map((it, idx) => `
            <li class="flex items-start gap-2.5 my-1.5 text-slate-800">
                <span class="inline-flex items-center justify-center w-5 h-5 rounded-full bg-blue-100 text-blue-700 text-xs font-bold flex-shrink-0">${idx + 1}</span>
                <span>${it.trim()}</span>
            </li>
        `).join('');
        return `<ol class="my-3 space-y-1">${itemsHtml}</ol>`;
    });

    // 6. Inline text formattings
    text = text.replace(/\\textbf\{([^}]*)\}/gi, '<strong class="font-bold text-slate-900">$1</strong>');
    text = text.replace(/\\textit\{([^}]*)\}/gi, '<em class="italic text-slate-700">$1</em>');
    text = text.replace(/\\underline\{([^}]*)\}/gi, '<span class="underline">$1</span>');
    text = text.replace(/\\alert\{([^}]*)\}/gi, '<span class="text-rose-600 font-semibold">$1</span>');

    // 7. Line breaks
    text = text.replace(/\\\\/g, "<br/>");
    text = text.replace(/\n\n+/g, "<br/><br/>");

    // Assemble full Beamer Slide Theme (Madrid Style)
    return `
        <div class="beamer-slide-frame bg-white rounded-xl shadow-lg border border-slate-300 overflow-hidden font-sans relative flex flex-col justify-between min-h-[380px]">
            <!-- Beamer Top Navigation Banner -->
            <div class="beamer-top-banner bg-slate-900 text-slate-300 text-[10px] px-4 py-1 flex justify-between items-center tracking-wider">
                <span>AI PDF TRANSLATOR • BEAMER ENGINE</span>
                <span class="font-mono">LaTeX Presentation</span>
            </div>

            <!-- Beamer Frame Title Banner -->
            <div class="beamer-frame-title bg-gradient-to-r from-blue-800 via-blue-700 to-indigo-800 text-white px-5 py-3.5 shadow-md">
                <h2 class="text-base font-bold tracking-wide">${frameTitle}</h2>
            </div>

            <!-- Slide Body Content -->
            <div class="beamer-body-content p-6 flex-1 text-slate-800 text-sm leading-relaxed overflow-x-auto">
                ${text}
            </div>

            <!-- Beamer Footline / Bottom Info Bar -->
            <div class="beamer-footline bg-slate-100 border-t border-slate-200 text-slate-500 text-[11px] px-5 py-2 flex justify-between items-center">
                <span class="font-medium text-blue-900">Bản dịch Slide Hoàn Chỉnh</span>
                <span class="bg-blue-600 text-white px-2 py-0.5 rounded-full font-bold text-[10px] shadow-sm">
                    ${pageNumber}
                </span>
            </div>
        </div>
    `;
}

window.renderBeamerToHtml = renderBeamerToHtml;
