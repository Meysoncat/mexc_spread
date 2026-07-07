"""DNS Dashboard API endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/dns", tags=["DNS"])


@router.get("/")
async def dns_home() -> HTMLResponse:
    """DNS Dashboard landing page."""
    return HTMLResponse(
        content="""
        <!DOCTYPE html>
        <html>
        <head>
            <title>DNS Dashboard</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }}
                h1 {{ color: #333; text-align: center; }}
                .dashboard {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-top: 20px; }}
                .card {{ background: white; border-radius: 10px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
                .card h2 {{ margin-top: 0; color: #666; font-size: 14px; text-transform: uppercase; }}
                .stat {{ font-size: 24px; font-weight: bold; color: #333; margin: 10px 0; }}
                .stat-label {{ color: #999; font-size: 12px; }}
                .chart-container {{ height: 250px; }}
                .recent-queries {{ max-height: 300px; overflow-y: auto; }}
                .query-item {{ padding: 5px; border-bottom: 1px solid #eee; font-size: 12px; }}
            </style>
        </head>
        <body>
            <h1>🔍 DNS Dashboard</h1>
            <div class="dashboard">
                <div class="card">
                    <h2>Global Status</h2>
                    <div id="global-status">Loading...</div>
                </div>
                <div class="card">
                    <h2>Exchange Status</h2>
                    <div id="exchange-status">Loading...</div>
                </div>
            </div>
            <div class="chart-container" style="margin-top: 20px;">
                <canvas id="exchange-chart"></canvas>
            </div>
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <script>
                const chartCanvas = document.getElementById('exchange-chart');
                const exchangeChart = new Chart(chartCanvas, {{
                    type: 'bar',
                    data: {{
                        labels: [],
                        datasets: [{{
                            label: 'Response Time (ms)',
                            data: [],
                            backgroundColor: 'rgba(54, 162, 235, 0.8)',
                        }}, {{
                            label: 'Success Rate (%)',
                            data: [],
                            backgroundColor: 'rgba(75, 192, 192, 0.8)',
                        }}, {{
                            label: 'Health Score',
                            data: [],
                            backgroundColor: 'rgba(255, 99, 132, 0.8)',
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {{
                            title: {{ display: true, text: 'Exchange Performance' }}
                        }},
                        scales: {{
                            x: {{ stacked: true }},
                            y: {{ stacked: true, beginAtZero: true }}
                        }}
                    }}
                }});
                async function fetchMetrics() {{
                    try {{
                        const response = await fetch('/api/dns/metrics');
                        const data = await response.json();
                        updateDashboard(data);
                    }} catch (error) {{
                        console.error('Error fetching metrics:', error);
                    }}
                }}
                function updateDashboard(data) {{
                    const globalConfig = data.global_config;
                    document.getElementById('global-status').innerHTML = `
                        <div style="margin-bottom: 10px;">DNS Type: <strong>${
                        globalConfig.dns_type
                    }</strong></div>
                        <div style="margin-bottom: 10px;">Custom DNS: <span style="padding: 5px; border-radius: 3px; background-color: ${
                        globalConfig.use_custom_dns ? '#d4edda' : '#fff3cd'
                    }; color: ${
                        globalConfig.use_custom_dns ? '#155724' : '#856404'
                    };">${
                        globalConfig.use_custom_dns ? 'Enabled' : 'Disabled'
                    }</span></div>
                        <div>Cache Enabled: <span style="padding: 5px; border-radius: 3px; background-color: ${
                        globalConfig.dns_cache_enabled ? '#d4edda' : '#fff3cd'
                    }; color: ${
                        globalConfig.dns_cache_enabled ? '#155724' : '#856404'
                    };">${
                        globalConfig.dns_cache_enabled ? 'Yes' : 'No'
                    }</span></div>
                    `;

                    const exchanges = [];
                    const responseTimes = [];
                    const successRates = [];
                    const healthScores = [];

                    for (const [exchange, metrics] of Object.entries(data.exchange_metrics)) {{
                        exchanges.push(exchange);
                        responseTimes.push(Math.round(metrics.avg_response_time_ms) || 0);
                        successRates.push(Math.round(metrics.success_rate) || 0);
                        healthScores.push(Math.round(metrics.health_score * 100) || 0);

                        const statusClass = metrics.health_score < 0.5 ? 'error' :
                            metrics.health_score < 0.7 ? 'warning' : 'ok';
                        const statusText = metrics.health_score < 0.5 ? 'Critical' :
                            metrics.health_score < 0.7 ? 'Warning' : 'Healthy';

                        document.getElementById('exchange-status').innerHTML += `
                            <div style="margin: 10px 0; padding: 8px; background: #f8f9fa; border-radius: 5px;">
                                <strong>${
                                exchange
                            }</strong><br>
                                Avg Time: ${
                                metrics.avg_response_time_ms.toFixed(2)
                            }ms<br>
                                Success Rate: ${
                                metrics.success_rate.toFixed(2)
                            }%<br>
                                Health: <span style="padding: 5px; border-radius: 3px; background-color: ${
                                statusClass
                            };">${
                                statusText
                            }</span></div>
                        `;
                    }}

                    const exchangeChart = Chart.getChart('exchange-chart');
                    if (exchangeChart) {{
                        exchangeChart.data.labels = exchanges;
                        exchangeChart.data.datasets[0].data = responseTimes;
                        exchangeChart.data.datasets[1].data = successRates;
                        exchangeChart.data.datasets[2].data = healthScores;
                        exchangeChart.update();
                    }}
                }}
                fetchMetrics();
                setInterval(fetchMetrics, 5000);
            </script>
        </body>
        </html>
        """,
    )


@router.get("/api/metrics")
async def dns_metrics() -> dict[str, Any]:
    """Get DNS metrics for all exchanges."""
    from mexc_monitor.config import load_network_dns_config
    from mexc_monitor.network.dns_resolver import CustomDNSResolver

    config = load_network_dns_config()
    resolver = CustomDNSResolver(config)

    metrics = resolver.get_all_metrics()

    total_queries = sum(m.get("total_queries", 0) for m in metrics.values())
    successful_resolutions = sum(m.get("successful_resolutions", 0) for m in metrics.values())
    cache_hits = sum(m.get("cache_hits", 0) for m in metrics.values())
    total_response_time = sum(m.get("total_response_time_ms", 0) for m in metrics.values())

    global_avg_response_time = (total_response_time / successful_resolutions) if successful_resolutions > 0 else 0.0
    global_cache_hit_rate = (cache_hits / total_queries * 100) if total_queries > 0 else 0.0
    global_success_rate = (successful_resolutions / total_queries * 100) if total_queries > 0 else 0.0

    return {
        "global_config": config.global_config.to_dict(),
        "exchange_metrics": metrics,
        "global_avg_response_time": round(global_avg_response_time, 2),
        "global_cache_hit_rate": round(global_cache_hit_rate, 1),
        "global_success_rate": round(global_success_rate, 1),
    }


@router.get("/api/config")
async def dns_config() -> dict[str, Any]:
    """Get current DNS configuration."""
    from mexc_monitor.config import load_network_dns_config
    from mexc_monitor.network.dns_resolver import CustomDNSResolver

    config = load_network_dns_config()
    resolver = CustomDNSResolver(config)

    return config.to_dict()