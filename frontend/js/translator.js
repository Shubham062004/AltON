// translator.js - Text, Audio, and Translation Logic

const sourceText = document.getElementById('sourceText');
const targetText = document.getElementById('targetText');
const sourceLang = document.getElementById('sourceLang');
const targetLang = document.getElementById('targetLang');
const charCount = document.getElementById('charCount');
const detectedLang = document.getElementById('detectedLang');

let debounceTimer = null;
let currentAudio = null;

// Initialize Languages
async function initLanguages() {
    try {
        const res = await fetch('/api/languages');
        const languages = await res.json();
        
        let targetHtml = '';
        let sourceHtml = '<option value="auto" class="bg-[#050B14] text-white">Auto Detect</option>';
        
        languages.forEach(l => {
            const opt = `<option value="${l.code}" class="bg-[#050B14] text-white">${l.name}</option>`;
            sourceHtml += opt;
            targetHtml += opt;
        });
        
        sourceLang.innerHTML = sourceHtml;
        targetLang.innerHTML = targetHtml;
        targetLang.value = 'es'; // Default to Spanish
    } catch (e) {
        console.error("Failed to load languages");
    }
}

// Translate Text
async function runTranslate() {
    const text = sourceText.value;
    charCount.textContent = `${text.length} / 5000`;

    if (!text.trim()) {
        targetText.textContent = '';
        detectedLang.textContent = '';
        AppState.setAIState('IDLE');
        return;
    }

    AppState.setAIState('THINKING');

    try {
        const res = await fetch('/api/translate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                text,
                source: sourceLang.value,
                target: targetLang.value,
            }),
        });
        const data = await res.json();

        targetText.textContent = data.translated || '';
        detectedLang.textContent = data.detected ? `DETECTED: ${data.detected.toUpperCase()}` : '';
        AppState.setAIState('IDLE');
    } catch (err) {
        targetText.textContent = 'Translation matrix offline...';
        AppState.setAIState('IDLE');
    }
}

sourceText.addEventListener('input', () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(runTranslate, 400);
});
sourceLang.addEventListener('change', runTranslate);
targetLang.addEventListener('change', runTranslate);

// Text to Speech
document.getElementById('speakTargetBtn').addEventListener('click', async () => {
    const text = targetText.textContent;
    if (!text.trim()) return;

    if (currentAudio) {
        currentAudio.pause();
        currentAudio = null;
    }

    AppState.setAIState('SPEAKING');

    try {
        const res = await fetch('/api/speak', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, lang: targetLang.value }),
        });

        if (!res.ok) throw new Error('Audio generation failed');

        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        currentAudio = new Audio(url);
        
        currentAudio.onended = () => {
            AppState.setAIState('IDLE');
            URL.revokeObjectURL(url);
        };
        
        await currentAudio.play();
    } catch (err) {
        console.error(err);
        AppState.setAIState('IDLE');
    }
});

// Speech to Text (Microphone)
let mediaRecorder;
let audioChunks = [];
let isRecording = false;

document.getElementById('micBtn').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    
    if (sourceLang.value === 'auto') {
        alert("Please select a specific language first. Auto-detect is not supported for voice.");
        return;
    }

    if (!isRecording) {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            mediaRecorder = new MediaRecorder(stream);
            audioChunks = [];

            mediaRecorder.ondataavailable = e => {
                if (e.data.size > 0) audioChunks.push(e.data);
            };

            mediaRecorder.onstop = async () => {
                const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                const form = new FormData();
                form.append('audio', audioBlob, 'mic.webm');
                form.append('spoken_lang', sourceLang.value);
                form.append('target', targetLang.value);

                AppState.setAIState('THINKING');

                try {
                    const res = await fetch('/api/transcribe', { method: 'POST', body: form });
                    const data = await res.json();

                    sourceText.value = data.transcript;
                    targetText.textContent = data.translated;
                    charCount.textContent = `${data.transcript.length} / 5000`;
                } catch (err) {
                    console.error(err);
                } finally {
                    AppState.setAIState('IDLE');
                }
            };

            mediaRecorder.start();
            isRecording = true;
            btn.classList.add('text-ultron-accent', 'animate-pulse');
            AppState.setAIState('LISTENING');
        } catch (err) {
            alert('Microphone access denied.');
        }
    } else {
        mediaRecorder.stop();
        isRecording = false;
        btn.classList.remove('text-ultron-accent', 'animate-pulse');
        mediaRecorder.stream.getTracks().forEach(track => track.stop());
    }
});

// Copy
document.getElementById('copyBtn').addEventListener('click', () => {
    if (targetText.textContent) navigator.clipboard.writeText(targetText.textContent);
});

// Swap
document.getElementById('swapLangBtn').addEventListener('click', () => {
    if (sourceLang.value === 'auto') return;
    
    const sVal = sourceLang.value;
    sourceLang.value = targetLang.value;
    targetLang.value = sVal;
    
    const sText = sourceText.value;
    sourceText.value = targetText.textContent;
    targetText.textContent = sText;
    
    runTranslate();
});

// Init
initLanguages();
