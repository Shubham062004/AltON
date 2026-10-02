// ocr.js - Image OCR and Translation Overlay

const ocrInput = document.getElementById('ocrInput');
const ocrCanvas = document.getElementById('ocrCanvas');
const ocrPrompt = document.getElementById('ocrPrompt');
const targetLangSelect = document.getElementById('targetLang'); // Use translator's target lang

ocrInput.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    // Show image on canvas immediately
    const imgUrl = URL.createObjectURL(file);
    const img = new Image();
    
    img.onload = () => {
        ocrCanvas.width = img.width;
        ocrCanvas.height = img.height;
        const ctx = ocrCanvas.getContext('2d');
        ctx.drawImage(img, 0, 0);
        
        ocrPrompt.classList.add('hidden');
        ocrCanvas.classList.remove('hidden');
        ocrCanvas.classList.add('opacity-50'); // Dim while processing
    };
    img.src = imgUrl;

    AppState.setAIState('THINKING');

    const form = new FormData();
    form.append('image', file);
    form.append('target', targetLangSelect.value);

    try {
        const res = await fetch('/api/ocr', { method: 'POST', body: form });
        const data = await res.json();
        
        if (res.ok) {
            // Draw result over image
            const ctx = ocrCanvas.getContext('2d');
            ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
            ctx.fillRect(0, 0, ocrCanvas.width, 40); // Top bar
            
            ctx.fillStyle = '#00F0FF';
            ctx.font = '24px monospace';
            ctx.fillText(data.translated || 'No text found', 10, 30);
            
            ocrCanvas.classList.remove('opacity-50');
            
            // Also populate translator text area
            document.getElementById('sourceText').value = data.extracted;
            document.getElementById('targetText').textContent = data.translated;
            
        } else {
            alert(data.detail || 'OCR Failed');
        }
    } catch (err) {
        console.error(err);
        alert('OCR Processing error.');
    } finally {
        AppState.setAIState('IDLE');
        URL.revokeObjectURL(imgUrl);
    }
});
