<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>REELMETRICS / CINEMA INTELLIGENCE • LIVE TRACKER</title>
    <style>
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }
        body {
            background-image: linear-gradient(to bottom, rgba(15, 23, 42, 0.6), rgba(15, 23, 42, 0.7)), 
                              url('./assets/filters_quality(80)_format(webp).webp');
            background-size: cover;
            background-position: center center;
            background-attachment: fixed;
            background-repeat: no-repeat;
            color: #cbd5e1;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            min-height: 100vh;
            padding: 0 16px 64px 16px;
            -webkit-font-smoothing: antialiased;
        }
        header {
            max-width: 800px;
            margin: 0 auto;
            padding: 40px 8px 16px 8px;
        }
        h1 {
            font-size: 36px;
            font-weight: 900;
            letter-spacing: -0.03em;
            color: #ffffff;
            text-transform: uppercase;
        }
        .highlight { color: #d4a359; }
        .header-desc {
            margin-top: 12px;
            font-size: 13px;
            color: #94a3b8;
            max-width: 560px;
            line-height: 1.6;
        }
        .status-badge {
            display: inline-block;
            margin-top: 10px;
            font-size: 11px;
            font-weight: 600;
            color: #d4a359;
            background: rgba(212, 163, 89, 0.12);
            border: 1px solid rgba(212, 163, 89, 0.25);
            padding: 4px 10px;
            border-radius: 9999px;
        }
        main {
            max-width: 800px;
            margin: 20px auto 0 auto;
            display: flex;
            flex-direction: column;
            gap: 24px;
        }
        .liquid-glass {
            position: relative;
            background: rgba(255, 255, 255, 0.01);
            backdrop-filter: blur(24px);
            -webkit-backdrop-filter: blur(24px);
            border: 1px solid rgba(255, 255, 255, 0.07);
            box-shadow: inset 0 1px 1px rgba(255, 255, 255, 0.05), 0 20px 40px rgba(0, 0, 0, 0.35);
            border-radius: 20px;
            padding: 24px;
            width: 100%;
        }
        h2 { font-size: 18px; font-weight: 800; color: #ffffff; letter-spacing: -0.02em; text-transform: uppercase; }
        .section-desc { font-size: 12px; color: #64748b; margin-top: 4px; line-height: 1.5; }
        
        .main-display-panel {
            border: 1px solid rgba(212, 163, 89, 0.25);
            background: rgba(15, 23, 42, 0.45);
            border-radius: 16px;
            padding: 24px;
            margin-top: 16px;
        }
        .panel-label { font-size: 11px; font-weight: 700; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; }
        .big-number { font-size: 42px; font-weight: 900; color: #d4a359; letter-spacing: -0.02em; display: flex; align-items: baseline; gap: 4px; margin-top: 4px; }
        
        .split-grid { display: grid; grid-template-cols: 1fr; gap: 12px; margin-top: 12px; }
        @media (min-width: 580px) { .split-grid { grid-template-cols: repeat(2, 1fr); } }
        .split-card { background-color: rgba(15, 23, 42, 0.35); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 14px; padding: 16px; }
        .split-title { font-size: 11px; font-weight: 600; color: #64748b; text-transform: uppercase; }
        .split-number-box { font-size: 22px; font-weight: 800; margin-top: 4px; color: #ffffff; }

        /* Elevated Mobile-Safe Filter Scroller */
        .filter-scroller {
            position: relative;
            z-index: 50;
            display: flex;
            gap: 8px;
            overflow-x: auto;
            margin-top: 16px;
            padding-bottom: 8px;
            -webkit-overflow-scrolling: touch;
        }
        .filter-scroller::-webkit-scrollbar { height: 4px; }
        .filter-scroller::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.1); border-radius: 9999px; }
        .pill-btn {
            position: relative;
            z-index: 51;
            touch-action: manipulation;
            -webkit-tap-highlight-color: transparent;
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.08);
            color: #94a3b8;
            padding: 10px 18px;
            font-size: 12px;
            font-weight: 700;
            border-radius: 9999px;
            cursor: pointer;
            white-space: nowrap;
            transition: all 0.2s ease;
        }
        .pill-btn.active {
            background: rgba(212, 163, 89, 0.15);
            border-color: #d4a359;
            color: #d4a359;
        }

        .matrix-data-box {
            background: rgba(15, 23, 42, 0.35);
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-radius: 16px;
            padding: 20px;
            margin-top: 12px;
        }
        .week-meta-strip {
            display: flex;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 8px;
            padding-bottom: 12px;
            margin-bottom: 8px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            font-size: 11px;
            color: #94a3b8;
        }
        .week-meta-strip span strong { color: #ffffff; }
        .competitor-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px 0;
            font-size: 13px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.04);
        }
        .competitor-row:last-child { border-bottom: none; }
        .comp-name { color: #cbd5e1; font-weight: 500; }
        .comp-value { color: #ffffff; font-weight: 700; }
        .comp-name.highlight-film { color: #d4a359; font-weight: 700; }
        .comp-value.highlight-film { color: #d4a359; font-weight: 800; }

        .reviews-layout { display: flex; flex-direction: column; gap: 12px; margin-top: 16px; width: 100%; }
        .review-row { background-color: rgba(15, 23, 42, 0.35); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 14px; padding: 16px; }
        .review-author-line { display: flex; justify-content: space-between; align-items: center; font-size: 12px; margin-bottom: 6px; }
        .author-name { font-weight: 700; color: #ffffff; }
        .rating-chip { font-size: 10px; font-weight: 700; color: #d4a359; background: rgba(212, 163, 89, 0.1); padding: 2px 6px; border-radius: 4px; }
        .review-body { font-size: 12px; color: #cbd5e1; line-height: 1.5; font-style: italic; }
        
        .footer-note { text-align: center; font-size: 11px; color: #475569; margin-top: 32px; padding-bottom: 32px; line-height: 1.6; }
    </style>
</head>
<body>

    <header>
        <h1>REEL<span class="highlight">METRICS</span></h1>
        <p class="header-desc">Ramayana: Rise of a Legend • Week-Wise Worldwide Box Office Tracker</p>
        <div class="status-badge" id="last-updated-badge">Syncing live box office...</div>
    </header>

    <main>
        <!-- Lifetime Worldwide Gross Block -->
        <section class="liquid-glass">
            <h2>Worldwide Box Office Collection</h2>
            <p class="section-desc">Cumulative theatrical gross across India and Overseas territories.</p>

            <div class="main-display-panel">
                <span class="panel-label">Total Worldwide Gross</span>
                <div class="big-number">₹<span id="total-worldwide-gross">895.00</span> Cr</div>
            </div>

            <div class="split-grid">
                <div class="split-card">
                    <span class="split-title">India Gross</span>
                    <div class="split-number-box">₹<span id="total-india-gross">700.00</span> Cr</div>
                </div>
                <div class="split-card">
                    <span class="split-title">Overseas Gross</span>
                    <div class="split-number-box">₹<span id="total-overseas-gross">195.00</span> Cr</div>
                </div>
            </div>
        </section>

        <!-- Week-Wise Trajectory Matrix Block -->
        <section class="liquid-glass">
            <h2>Week-Wise Trajectory Matrix</h2>
            <p class="section-desc">Tap a week below to view Ramayana's weekly collections and historical benchmarks.</p>
            
            <div class="filter-scroller" id="week-filter-bar">
                <button type="button" class="pill-btn active" data-week="week1">Week 1</button>
                <button type="button" class="pill-btn" data-week="week2">Week 2</button>
                <button type="button" class="pill-btn" data-week="week3">Week 3</button>
                <button type="button" class="pill-btn" data-week="week4">Week 4</button>
                <button type="button" class="pill-btn" data-week="lifetime">Lifetime Total</button>
            </div>

            <div class="matrix-data-box">
                <div class="week-meta-strip">
                    <span>India: <strong id="meta-india">₹585.00 Cr</strong></span>
                    <span>Overseas: <strong id="meta-overseas">₹160.00 Cr</strong></span>
                    <span>Period: <strong id="meta-period">Days 1–7</strong></span>
                </div>

                <div class="competitor-row">
                    <span class="comp-name highlight-film" id="matrix-r-label">Ramayana (Week 1)</span>
                    <span class="comp-value highlight-film" id="matrix-active-r">₹745.00 Cr</span>
                </div>
                <div class="competitor-row">
                    <span class="comp-name">Baahubali 2 (Benchmark)</span>
