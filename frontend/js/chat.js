// chat.js - LLM Chat Interface using SSE Streaming

const chatInput = document.getElementById('chatInput');
const chatSendBtn = document.getElementById('chatSendBtn');
const chatBox = document.getElementById('chatBox');

let chatHistory = [];

function addMessage(role, content) {
    const div = document.createElement('div');
    div.className = `flex gap-3 ${role === 'user' ? 'justify-end' : ''}`;
    
    if (role === 'user') {
        div.innerHTML = `
            <div class="bg-ultron-neon/10 border border-ultron-neon/30 rounded-2xl rounded-tr-sm p-3 text-sm text-white/90 max-w-[85%]">
                ${content}
            </div>
        `;
    } else {
        div.innerHTML = `
            <div class="w-6 h-6 rounded-full bg-ultron-neon flex-shrink-0 flex items-center justify-center mt-1 shadow-[0_0_10px_rgba(0,240,255,0.5)]">
                <span class="text-[10px] text-black font-bold font-mono">U</span>
            </div>
            <div class="bg-ultron-dark/80 border border-ultron-border/30 rounded-2xl rounded-tl-sm p-3 text-sm text-white/90 max-w-[85%] prose prose-invert content-box">
                ${marked.parse(content)}
            </div>
        `;
    }
    
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
    return div;
}

async function sendMessage() {
    const text = chatInput.value.trim();
    if (!text) return;
    
    chatInput.value = '';
    addMessage('user', text);
    chatHistory.push({ role: 'user', content: text });
    
    AppState.setAIState('THINKING');
    
    // Create empty AI message box for streaming
    const aiMessageDiv = document.createElement('div');
    aiMessageDiv.className = 'flex gap-3';
    aiMessageDiv.innerHTML = `
        <div class="w-6 h-6 rounded-full bg-ultron-neon flex-shrink-0 flex items-center justify-center mt-1 shadow-[0_0_10px_rgba(0,240,255,0.5)]">
            <span class="text-[10px] text-black font-bold font-mono">U</span>
        </div>
        <div class="bg-ultron-dark/80 border border-ultron-border/30 rounded-2xl rounded-tl-sm p-3 text-sm text-white/90 max-w-[85%] prose prose-invert content-box">
            <span class="animate-pulse">...</span>
        </div>
    `;
    chatBox.appendChild(aiMessageDiv);
    chatBox.scrollTop = chatBox.scrollHeight;
    
    const contentBox = aiMessageDiv.querySelector('.content-box');
    
    try {
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ messages: chatHistory })
        });
        
        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let aiFullText = "";
        contentBox.innerHTML = "";
        
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            
            const chunk = decoder.decode(value, { stream: true });
            const lines = chunk.split('\n\n');
            
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    const data = JSON.parse(line.substring(6));
                    if (data.done) {
                        chatHistory.push({ role: 'assistant', content: aiFullText });
                        AppState.setAIState('IDLE');
                        break;
                    }
                    if (data.content) {
                        AppState.setAIState('SPEAKING'); // Flash sphere
                        aiFullText += data.content;
                        contentBox.innerHTML = marked.parse(aiFullText);
                        chatBox.scrollTop = chatBox.scrollHeight;
                    }
                    if (data.agent_log) {
                        // Dynamically update Desktop Agent HUD and Log
                        const logContainer = document.getElementById('agentLogContainer');
                        const statusLabel = document.getElementById('agentOSStatus');
                        const browserLabel = document.getElementById('agentBrowserStatus');
                        const activeActionLabel = document.getElementById('agentActiveAction');
                        const progressBar = document.getElementById('agentProgressBar');
                        const timelinePercent = document.getElementById('agentTimelinePercent');

                        if (logContainer) {
                            if (logContainer.querySelector('.italic')) {
                                logContainer.innerHTML = '';
                            }
                            const div = document.createElement('div');
                            div.className = "text-[10px] font-mono text-slate-400 bg-slate-950/40 px-2 py-1 rounded border border-slate-900 break-all leading-normal";
                            div.textContent = data.agent_log;
                            logContainer.appendChild(div);
                            logContainer.scrollTop = logContainer.scrollHeight;
                        }

                        const log = data.agent_log;
                        if (log.includes("Executing:")) {
                            if (statusLabel) {
                                statusLabel.textContent = "BUSY";
                                statusLabel.className = "text-amber-400 font-bold uppercase tracking-wider animate-pulse";
                            }
                        } else if (log.includes("SUCCESS")) {
                            if (statusLabel) {
                                statusLabel.textContent = "ACTIVE";
                                statusLabel.className = "text-emerald-400 font-bold uppercase tracking-wider";
                            }
                        } else if (log.includes("failed") || log.includes("ERROR")) {
                            if (statusLabel) {
                                statusLabel.textContent = "ERROR";
                                statusLabel.className = "text-rose-500 font-bold uppercase tracking-wider animate-pulse";
                            }
                        }

                        // Progress Timeline
                        const stepMatch = log.match(/Step (\d+)\/(\d+)/);
                        if (stepMatch) {
                            const current = parseInt(stepMatch[1]);
                            const total = parseInt(stepMatch[2]);
                            const percent = Math.round((current / total) * 100);
                            
                            if (progressBar) progressBar.style.width = `${percent}%`;
                            if (timelinePercent) timelinePercent.textContent = `${percent}%`;
                            
                            if (activeActionLabel) {
                                activeActionLabel.classList.remove('hidden');
                                activeActionLabel.textContent = log.split(':').slice(1).join(':').trim();
                            }
                        }

                        if (log.includes("Navigated") || log.includes("Opened new tab") || log.includes("Playwright")) {
                            if (browserLabel) {
                                browserLabel.textContent = "ACTIVE";
                                browserLabel.className = "text-emerald-400 font-bold uppercase tracking-wider";
                            }
                        }

                        if (typeof fetchActiveWindowAndTab === 'function') {
                            fetchActiveWindowAndTab();
                        }
                    }
                }
            }
        }
    } catch (e) {
        contentBox.innerHTML = "Error connecting to AI Matrix.";
        AppState.setAIState('IDLE');
    }
}

chatSendBtn.addEventListener('click', sendMessage);
chatInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') sendMessage();
});
