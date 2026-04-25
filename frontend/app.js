document.addEventListener('DOMContentLoaded', () => {
    const refreshBtn = document.getElementById('refreshBtn');
    const signalsGrid = document.getElementById('signalsGrid');
    const loader = document.getElementById('loader');
    const lastUpdated = document.getElementById('lastUpdated');
    const searchBtn = document.getElementById('searchBtn');
    const searchInput = document.getElementById('searchInput');
    const analysisContainer = document.getElementById('analysisContainer');

    const API_URL = '/api/signals';
    let ws = null;

    searchBtn.addEventListener('click', async () => {
        const symbol = searchInput.value.trim();
        if (!symbol) return;
        
        // Show loader and container
        analysisContainer.innerHTML = '';
        analysisContainer.classList.remove('hidden');
        loader.classList.remove('hidden');
        
        try {
            const response = await fetch(`/api/analyze/${symbol}`);
            const data = await response.json();
            
            if (data.status === 'success') {
                if (data.data.error) {
                    analysisContainer.innerHTML = `<div class="analysis-report"><h2>Error</h2><p>${data.data.error}</p></div>`;
                } else {
                    analysisContainer.innerHTML = data.data.html;
                }
            } else {
                analysisContainer.innerHTML = `<div class="analysis-report"><h2>Error</h2><p>${data.message}</p></div>`;
            }
        } catch (error) {
            analysisContainer.innerHTML = `<div class="analysis-report"><h2>Network Error</h2><p>Could not connect to backend.</p></div>`;
        }
        
        loader.classList.add('hidden');
    });

    // Allow Enter key to trigger search
    searchInput.addEventListener('keypress', function (e) {
        if (e.key === 'Enter') {
            searchBtn.click();
        }
    });

    async function fetchSignals() {
        signalsGrid.innerHTML = '';
        loader.classList.remove('hidden');
        refreshBtn.disabled = true;

        try {
            const response = await fetch(API_URL);
            const result = await response.json();
            
            if (result.status === 'success') {
                renderSignals(result.data, result.meta);
                const now = new Date();
                lastUpdated.textContent = `Last scan: ${now.toLocaleTimeString()}`;
            } else {
                showError("Backend Error: " + result.message);
            }
        } catch (error) {
            console.error("Fetch error:", error);
            showError("Could not connect to the backend server. Is it running on port 8000?");
        } finally {
            loader.classList.add('hidden');
            refreshBtn.disabled = false;
        }
    }

    function renderSignals(signals, meta) {
        // Update the header indicator
        const scanText = document.getElementById('scanText');
        if (scanText && meta) {
            scanText.innerText = `Analyzing ${meta.current}... (${meta.total} pairs)`;
        }

        if (!signals || signals.length === 0) {
            signalsGrid.innerHTML = `
                <div class="empty-state">
                    <h3>Scanning ${meta ? meta.total : '100+'} Markets...</h3>
                    <p>No highly-probable setups found right now. The background engine is continuously scanning for the perfect entry.</p>
                </div>
            `;
            return;
        }

        // Only redraw cards if the number of signals changed or their actions changed
        // To prevent WebSocket flickering, we do a simple re-render for now. 
        // In a full production app, we would use a diffing algorithm (like React).
        // Fix scrolling bug: Don't clear innerHTML completely to prevent layout collapse.
        // Instead, update existing cards or create new ones, and remove stale ones.
        const newAssets = new Set(signals.map(s => s.asset.toLowerCase()));
        
        Array.from(signalsGrid.children).forEach(child => {
            if (child.id && child.id.startsWith('card-')) {
                const assetId = child.id.replace('card-', '');
                if (!newAssets.has(assetId)) {
                    signalsGrid.removeChild(child);
                }
            } else {
                // Remove empty state messages if any
                signalsGrid.removeChild(child);
            }
        });

        signals.forEach(sig => {
            let card = document.getElementById(`card-${sig.asset.toLowerCase()}`);
            let isNew = false;
            
            if (!card) {
                card = document.createElement('div');
                card.id = `card-${sig.asset.toLowerCase()}`;
                isNew = true;
            }
            
            const actionBase = sig.action.split(' ')[0];
            card.className = `signal-card ${actionBase}`;
            
            let scoreClass = 'score-med';
            if (sig.confidence >= 75) scoreClass = 'score-high';
            else if (sig.confidence < 50) scoreClass = 'score-low';

            card.innerHTML = `
                <div class="card-header">
                    <div>
                        <div class="asset-name">${sig.asset.replace('USDT', '/USDT')}</div>
                        <div class="asset-price" id="price-container-${sig.asset.toLowerCase()}">
                            $<span id="price-val-${sig.asset.toLowerCase()}">${sig.current_price.toFixed(4)}</span>
                            <span class="change-badge ${sig.change_24h >= 0 ? 'positive' : 'negative'}">
                                ${sig.change_24h > 0 ? '+' : ''}${sig.change_24h}%
                            </span>
                        </div>
                    </div>
                </div>

                <div class="clear-signal-format">
                    <div class="format-row">
                        <span class="format-label">Signal:</span> 
                        <span class="format-val ${actionBase}">${sig.action}</span>
                    </div>
                    <div class="format-row">
                        <span class="format-label">Strategy:</span> 
                        <span class="format-val">${sig.setup}</span>
                    </div>
                    <div class="format-row">
                        <span class="format-label">Confidence:</span> 
                        <span class="format-val ${scoreClass}">${sig.confidence}%</span>
                    </div>
                    
                    <div class="spacer"></div>
                    
                    <div class="format-row">
                        <span class="format-label">Entry:</span> 
                        <span class="format-val val-entry">${sig.parameters.entry}</span>
                    </div>
                    <div class="format-row">
                        <span class="format-label">SL:</span> 
                        <span class="format-val val-sl">${sig.parameters.sl}</span>
                    </div>
                    <div class="format-row">
                        <span class="format-label">TP1:</span> 
                        <span class="format-val val-tp">${sig.parameters.tp1}</span>
                    </div>
                    <div class="format-row">
                        <span class="format-label">TP2:</span> 
                        <span class="format-val val-tp">${sig.parameters.tp2}</span>
                    </div>
                    <div class="format-row">
                        <span class="format-label">TP3:</span> 
                        <span class="format-val val-tp">${sig.parameters.tp3}</span>
                    </div>
                    
                    <div class="spacer"></div>
                    
                    <div class="format-row">
                        <span class="format-label">Market:</span> 
                        <span class="format-val">${sig.market || 'UNKNOWN'}</span>
                    </div>
                </div>
            `;
            
            if (isNew) {
                signalsGrid.appendChild(card);
            }
        });
        
        startLivePrices(signals.map(s => s.asset.toLowerCase()));
    }

    function startLivePrices(symbols) {
        if (ws) ws.close();
        if (!symbols.length) return;
        
        const streams = symbols.map(s => `${s}@ticker`).join('/');
        ws = new WebSocket(`wss://stream.binance.com:9443/ws/${streams}`);
        
        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            const symbol = data.s.toLowerCase();
            const priceValEl = document.getElementById(`price-val-${symbol}`);
            const priceContainerEl = document.getElementById(`price-container-${symbol}`);
            
            if (priceValEl && priceContainerEl) {
                const newPrice = parseFloat(data.c).toFixed(4);
                const oldPrice = parseFloat(priceValEl.innerText);
                priceValEl.innerText = newPrice;
                
                // Update the percentage badge with live data from Binance ticker
                const badgeEl = priceContainerEl.querySelector('.change-badge');
                if (badgeEl && data.P) {
                    const percentChange = parseFloat(data.P);
                    badgeEl.innerText = `${percentChange > 0 ? '+' : ''}${percentChange.toFixed(2)}%`;
                    badgeEl.className = `change-badge ${percentChange >= 0 ? 'positive' : 'negative'}`;
                }
                
                // Add micro-animation for price change
                if (newPrice > oldPrice) {
                    priceContainerEl.style.color = 'var(--buy-color)';
                } else if (newPrice < oldPrice) {
                    priceContainerEl.style.color = 'var(--sell-color)';
                }
                setTimeout(() => {
                    priceContainerEl.style.color = 'var(--text-muted)';
                }, 500);
            }
        };
    }

    function showError(message) {
        signalsGrid.innerHTML = `
            <div style="grid-column: 1/-1; text-align:center; color: var(--sell-color); background: var(--sell-bg); padding: 1rem; border-radius: 8px; border: 1px solid rgba(239, 68, 68, 0.3);">
                ${message}
            </div>
        `;
    }

    refreshBtn.addEventListener('click', fetchSignals);
    
    // Initial fetch
    fetchSignals();
    
    // Auto-refresh signals from cache every 5 seconds
    setInterval(fetchSignals, 5000);
});
