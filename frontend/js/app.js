// app.js - Global App State and Initialization

const AppState = {
    aiState: 'IDLE', // IDLE, LISTENING, THINKING, SPEAKING
    
    setAIState(state) {
        this.aiState = state;
        const statusEl = document.getElementById('sphereStatus');
        if(statusEl) {
            statusEl.textContent = `${state}.STATE`;
            
            // Update colors based on state
            if(state === 'LISTENING') statusEl.className = 'font-mono text-xs tracking-[0.3em] text-green-400 mt-auto glow-active p-2 rounded';
            else if(state === 'THINKING' || state === 'SPEAKING') statusEl.className = 'font-mono text-xs tracking-[0.3em] text-ultron-accent mt-auto animate-pulse';
            else statusEl.className = 'font-mono text-xs tracking-[0.3em] text-ultron-neon/50 mt-auto';
        }
        
        // Dispatch event for Sphere to react
        window.dispatchEvent(new CustomEvent('ai-state-change', { detail: state }));
    }
};

// UI Toggles
document.getElementById('ocrToggleBtn').addEventListener('click', () => {
    const ocrPanel = document.getElementById('ocrPanel');
    const sphereSpacer = document.getElementById('sphereSpacer');
    
    if (ocrPanel.classList.contains('hidden')) {
        ocrPanel.classList.remove('hidden');
        ocrPanel.classList.add('flex');
        sphereSpacer.classList.add('hidden');
        sphereSpacer.classList.remove('lg:flex');
    } else {
        ocrPanel.classList.add('hidden');
        ocrPanel.classList.remove('flex');
        sphereSpacer.classList.remove('hidden');
        sphereSpacer.classList.add('lg:flex');
    }
});

document.getElementById('closeOcrBtn').addEventListener('click', () => {
    document.getElementById('ocrPanel').classList.add('hidden');
    document.getElementById('ocrPanel').classList.remove('flex');
    document.getElementById('sphereSpacer').classList.remove('hidden');
    document.getElementById('sphereSpacer').classList.add('lg:flex');
});
