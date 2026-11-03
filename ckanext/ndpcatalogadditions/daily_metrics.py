import json
import os
from flask import make_response, jsonify

# Path to the JSON file
METRICS_JSON_PATH = '/tmp/daily_metrics.json'
DATASET_ACCESS_JSON_PATH = '/tmp/dataset_access.json'
METRICS_DASHBOARD_BASE_URL = os.getenv('CKAN_SITE_URL', 'https://ndp-test.sdsc.edu')


def load_metrics():
    """Load metrics from JSON file with error handling."""
    try:
        if os.path.exists(METRICS_JSON_PATH):
            with open(METRICS_JSON_PATH, 'r') as f:
                return json.load(f), None
        else:
            return None, 'Metrics file not found'
    except json.JSONDecodeError as e:
        return None, f'Invalid JSON: {str(e)}'
    except Exception as e:
        return None, f'Error loading metrics: {str(e)}'


def load_dataset_access():
    """Load dataset_access.json with error handling."""
    try:
        if os.path.exists(DATASET_ACCESS_JSON_PATH):
            with open(DATASET_ACCESS_JSON_PATH, 'r') as f:
                return json.load(f), None
        else:
            return None, 'Dataset access file not found'
    except json.JSONDecodeError as e:
        return None, f'Invalid dataset access JSON: {str(e)}'
    except Exception as e:
        return None, f'Error loading dataset access: {str(e)}'
    

def daily_metrics():
    """Return Daily Metrics HTML page"""
    html_content = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NDP Metrics Report</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/3.9.1/chart.min.js"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');
        body { font-family: 'Inter', sans-serif; }
        .metrics-card { background: white; padding: 1.5rem; border-radius: 0.75rem; box-shadow: 0 1px 2px rgba(0,0,0,0.05); border: 1px solid #f3f4f6; }
        .hidden { display: none !important; }
    </style>
</head>
<body class="bg-gray-50 text-gray-800">

    <nav class="bg-white border-b border-gray-200 px-8 py-4 flex justify-between items-center sticky top-0 z-50">
        <div class="flex items-center gap-3">
            <div class="w-8 h-8 bg-blue-700 rounded flex items-center justify-center text-white font-bold">N</div>
            <div>
                <h1 class="text-xl font-bold text-gray-900">National Data Platform</h1>
                <p class="text-xs text-gray-500 uppercase tracking-wide">Metrics Report</p>
            </div>
        </div>
        <div class="flex gap-4 items-center">
            <div id="report-date" class="bg-gray-100 px-3 py-1.5 rounded text-sm font-medium text-gray-600">
                <i class="far fa-calendar-alt mr-2"></i> Loading...
            </div>
            <a href="daily_metrics.json" target="_blank" class="bg-gray-200 hover:bg-gray-300 text-gray-700 px-4 py-1.5 rounded text-sm font-medium transition shadow-sm">
                <i class="fas fa-code mr-2"></i> JSON
            </a>
        </div>
    </nav>

    <main class="p-8 max-w-7xl mx-auto space-y-6">

        <div id="error-alert" class="hidden bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg">
            <i class="fas fa-exclamation-circle mr-2"></i> <span id="error-message"></span>
        </div>

        <!-- Section 1: KPIs -->
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            <div class="metrics-card">
                <div class="flex justify-between items-start">
                    <div>
                        <p class="text-sm font-medium text-gray-500">Total Catalog Datasets</p>
                        <h3 id="kpi-catalog-entries" class="text-3xl font-bold text-gray-900 mt-2">--</h3>
                        <p id="kpi-catalog-since" class="text-xs text-gray-400 mt-2">Since --</p>
                    </div>
                    <button id="catalog-chart-btn" class="text-gray-400 hover:text-blue-600 transition" title="View 12-month trend">
                        <i class="fas fa-chart-line text-lg"></i>
                    </button>
                </div>
            </div>

            <div class="metrics-card">
                <div class="flex justify-between items-start">
                    <div>
                        <p class="text-sm font-medium text-gray-500">Actively Used Datasets</p>
                        <h3 id="kpi-active-entries" class="text-3xl font-bold text-gray-900 mt-2">--</h3>
                    </div>
                    <span id="kpi-active-badge" class="text-xs font-bold px-2 py-1 rounded bg-blue-50 text-blue-700">--%</span>
                </div>
                <div class="mt-2">
                    <div class="h-1 w-full bg-gray-100 rounded-full overflow-hidden">
                        <div id="kpi-active-bar" class="h-full bg-blue-600" style="width: 0%"></div>
                    </div>
                    <p id="kpi-active-subtitle" class="text-xs text-gray-400 mt-1">In workspaces (-- added last 30 days)</p>
                </div>
            </div>

            <div class="metrics-card">
                <div class="flex justify-between items-start">
                    <div>
                        <p class="text-sm font-medium text-gray-500">Recently Added Datasets</p>
                        <h3 id="kpi-recent-datasets" class="text-3xl font-bold text-gray-900 mt-2">--</h3>
                    </div>
                    <span id="kpi-recent-badge" class="text-xs font-bold px-2 py-1 rounded bg-green-50 text-green-700">--%</span>
                </div>
                <div class="mt-2">
                    <div class="h-1 w-full bg-gray-100 rounded-full overflow-hidden">
                        <div id="kpi-recent-bar" class="h-full bg-green-600" style="width: 0%"></div>
                    </div>
                    <p id="kpi-recent-subtitle" class="text-xs text-gray-400 mt-1">Last 30 days (-- in 7 days)</p>
                </div>
            </div>

            <div class="metrics-card">
                <div class="flex justify-between items-start">
                    <div>
                        <p class="text-sm font-medium text-gray-500">Workspace Storage</p>
                        <h3 id="kpi-storage" class="text-3xl font-bold text-gray-900 mt-2">-- TB</h3>
                    </div>
                    <span id="kpi-storage-badge" class="text-xs font-bold px-2 py-1 rounded bg-gray-50 text-gray-700">--%</span>
                </div>
                <div class="mt-2">
                    <div class="h-1 w-full bg-gray-100 rounded-full overflow-hidden">
                        <div id="kpi-storage-bar" class="h-full bg-blue-600" style="width: 0%"></div>
                    </div>
                    <p class="text-xs text-gray-400 mt-1">User results & outputs</p>
                </div>
            </div>
        </div>

        <!-- Section 2: CollabStudio & User Journey -->
        <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
            
            <!-- CollabStudio Table -->
            <div class="lg:col-span-2 bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
                <div class="px-6 py-4 border-b border-gray-100 flex justify-between items-center">
                    <h3 class="font-semibold text-gray-800">CollabStudio Impact</h3>
                    <!-- span class="text-xs bg-gray-100 text-gray-600 px-2 py-1 rounded">Metric 1.0</span-->
                </div>
                <table class="w-full text-sm text-left">
                    <thead class="bg-gray-50 text-gray-500 font-medium border-b border-gray-100">
                        <tr>
                            <th class="px-6 py-3">Entity Type</th>
                            <th class="px-6 py-3">Active Count</th>
                            <th class="px-6 py-3">Users Engaged</th>
                            <th class="px-6 py-3">Datasets Included</th>
                            <th class="px-6 py-3 text-center">Trend</th>
                        </tr>
                    </thead>
                    <tbody id="collabstudio-body" class="divide-y divide-gray-100">
                        <tr><td colspan="5" class="px-6 py-4 text-center text-gray-400">Loading...</td></tr>
                    </tbody>
                </table>
            </div>

            <!-- User Journey -->
            <div class="bg-white rounded-xl shadow-sm border border-gray-100">
                <div class="px-6 py-4 border-b border-gray-100">
                    <h3 class="font-semibold text-gray-800">User Journey</h3>
                    <p id="journey-period" class="text-xs text-gray-400 mt-1">Loading...</p>
                </div>
                <div class="p-6 space-y-4">
                    <div class="flex justify-between items-center">
                        <span class="text-sm text-gray-600">Registered Users</span>
                        <span id="journey-registered-users" class="text-lg font-bold text-gray-900" title="Total user accounts in Keycloak">--</span>
                    </div>
                    <div class="flex justify-between items-center">
                        <span class="text-sm text-gray-600">Unique Visitors</span>
                        <span id="journey-visitors" class="text-lg font-bold text-gray-900">--</span>
                    </div>
                    <div class="flex justify-between items-center">
                        <span class="text-sm text-gray-600">Datasets Viewed</span>
                        <button id="datasets-viewed-btn" class="text-lg font-bold text-blue-600 hover:text-blue-800 underline underline-offset-4 decoration-dashed hover:decoration-solid transition cursor-pointer focus:outline-none">--</button>
                    </div>
                    <div class="flex justify-between items-center">
                        <span class="text-sm text-gray-600">Total Searches</span>
                        <span id="journey-searches" class="text-lg font-bold text-gray-900">--</span>
                    </div>
                    <div class="border-t pt-4">
                        <p class="text-xs text-gray-500 mb-2">Datasets Added to Workspaces</p>
                        <div class="flex justify-between items-center mb-1">
                            <span class="text-sm text-gray-600">Last 7 days</span>
                            <span id="journey-weekly" class="text-lg font-bold text-gray-600">--</span>
                        </div>
                        <div class="flex justify-between items-center">
                            <span class="text-sm text-gray-600">Last 30 days</span>
                            <span id="journey-monthly" class="text-lg font-bold text-gray-600">--</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Section 3: Infrastructure & Global -->
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">

            <!-- Infrastructure -->
            <div class="metrics-card md:col-span-2">
                <h3 class="font-semibold text-gray-800 mb-4">Infrastructure Status</h3>
                <div class="grid grid-cols-2 gap-4">
                    <div class="bg-gray-50 p-4 rounded-lg">
                        <p class="text-xs text-gray-500 uppercase font-bold">Total Workspaces</p>
                        <p id="infra-workspaces" class="text-2xl font-bold text-gray-800">--</p>
                    </div>
                    <div class="bg-gray-50 p-4 rounded-lg">
                        <div class="flex justify-between items-start">
                            <p class="text-xs text-gray-500 uppercase font-bold">Copied Public Workspaces</p>
                            <button id="copied-workspaces-btn" class="text-gray-400 hover:text-blue-600 transition" title="View breakdown">
                                <i class="fas fa-list text-sm"></i>
                            </button>
                        </div>
                        <button id="copied-workspaces-count-btn" class="text-2xl font-bold text-blue-600 hover:text-blue-800 underline underline-offset-4 decoration-dashed hover:decoration-solid transition focus:outline-none">--</button>
                    </div>
                    <div class="bg-gray-50 p-4 rounded-lg">
                        <p class="text-xs text-gray-500 uppercase font-bold">Curated Catalogs</p>
                        <p id="infra-catalogs" class="text-2xl font-bold text-gray-800">--</p>
                    </div>
                    <div class="bg-gray-50 p-4 rounded-lg">
                        <p class="text-xs text-gray-500 uppercase font-bold">Workspace JHub API Calls</p>
                        <p id="infra-jhub-fetches" class="text-2xl font-bold text-gray-800">--</p>
                        <p class="text-xs text-gray-400 mt-1">Since 2026-04-15</p>
                    </div>
                </div>
                <div id="infra-alerts"></div>
            </div>

            <!-- Global Data Products -->
            <div class="metrics-card">
                <h3 class="font-semibold text-gray-800 mb-4">Global Data Products</h3>
                <div class="space-y-3">
                    <div class="flex items-center justify-between p-3 border border-gray-100 rounded-lg">
                        <div class="flex items-center gap-3">
                            <div class="bg-green-100 text-green-600 w-8 h-8 rounded-full flex items-center justify-center">
                                <i class="fas fa-lock-open"></i>
                            </div>
                            <span class="text-sm font-medium text-gray-700">Public NDP Datasets</span>
                        </div>
                        <span id="global-public" class="font-bold text-gray-900">--</span>
                    </div>
                    <div class="flex items-center justify-between p-3 border border-gray-100 rounded-lg">
                        <div class="flex items-center gap-3">
                            <div class="bg-gray-100 text-gray-600 w-8 h-8 rounded-full flex items-center justify-center">
                                <i class="fas fa-lock"></i>
                            </div>
                            <span class="text-sm font-medium text-gray-700">Private NDP Datasets</span>
                        </div>
                        <span id="global-private" class="font-bold text-gray-900">--</span>
                    </div>
                    <div class="flex items-center justify-between p-3 border border-gray-100 rounded-lg">
                        <div class="flex items-center gap-3">
                            <div class="bg-purple-100 text-purple-600 w-8 h-8 rounded-full flex items-center justify-center">
                                <i class="fas fa-users"></i>
                            </div>
                            <span class="text-sm font-medium text-gray-700">Public CollabStudio Objects</span>
                        </div>
                        <span id="global-public-objects" class="font-bold text-gray-900" title="Public projects + data challenges + classrooms + workspaces">--</span>
                    </div>
                </div>
            </div>
        </div>

        <!-- Section 4: Top Viewed (Last 30 Days) -->
        <div class="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
            <div class="px-6 py-4 border-b border-gray-100 flex justify-between items-center">
                <h3 class="font-semibold text-gray-800">Top Viewed CollabStudio — Last 30 Days</h3>
                <div class="flex rounded-lg overflow-hidden border border-gray-200 text-sm font-medium">
                    <button id="top-viewed-ndp-btn" onclick="switchTopViewedPlatform('NDP')"
                        class="px-4 py-1.5 bg-blue-600 text-white transition">NDP</button>
                    <button id="top-viewed-wstc-btn" onclick="switchTopViewedPlatform('WSTC')"
                        class="px-4 py-1.5 bg-white text-gray-600 hover:bg-gray-50 transition">WSTC</button>
                </div>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-gray-100">
                <!-- CollabStudio -->
                <div class="p-6">
                    <h4 class="text-sm font-semibold text-gray-500 uppercase mb-4">CollabStudio</h4>
                    <ol id="top-collabstudio-list" class="space-y-2">
                        <li class="text-sm text-gray-400">Loading...</li>
                    </ol>
                </div>
                <!-- Workspaces -->
                <div class="p-6">
                    <h4 class="text-sm font-semibold text-gray-500 uppercase mb-4">Workspaces</h4>
                    <ol id="top-workspaces-list" class="space-y-2">
                        <li class="text-sm text-gray-400">Loading...</li>
                    </ol>
                </div>
            </div>
        </div>

    </main>

    <!-- Chart Modal (for monthly trends) -->
    <div id="chart-modal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[100] p-4 hidden">
        <div class="bg-white rounded-xl shadow-2xl w-full max-w-4xl max-h-[85vh] overflow-hidden flex flex-col">
            <div class="flex justify-between items-center px-6 py-4 border-b border-gray-200">
                <h3 id="chart-title" class="text-lg font-bold text-gray-900">Created - Last 12 Months</h3>
                <button id="chart-close-btn" class="text-gray-400 hover:text-gray-600 transition text-2xl">X</button>
            </div>
            <div class="p-6" style="height: 500px;">
                <canvas id="datasets-chart"></canvas>
            </div>
        </div>
    </div>

    <!-- Dataset Access Modal -->
    <div id="dataset-access-modal" style="display: none;" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[100] p-4">
        <div class="bg-white rounded-xl shadow-2xl w-full max-w-4xl max-h-[85vh] overflow-hidden flex flex-col">
            <div class="flex justify-between items-center px-6 py-4 border-b border-gray-200">
                <h3 class="text-lg font-bold text-gray-900">Dataset Access Analysis</h3>
                <a href="dataset_access.json" 
                   target="_blank" 
                   class="ml-auto bg-gray-200 hover:bg-gray-300 text-gray-700 px-4 py-1.5 rounded text-sm font-medium transition shadow-sm">
                   <i class="fas fa-code mr-2"></i> JSON
                </a>
                <button id="dataset-access-close-btn" class="ml-4 text-gray-400 hover:text-gray-600 transition text-2xl">X</button>
            </div>
            
            <div class="px-6 py-4 border-b border-gray-100 bg-gray-50">
                <div class="flex gap-4 mb-4">
                    <input type="text" id="dataset-search-input" placeholder="Search dataset..." class="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                    <select id="dataset-sort-select" class="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white">
                        <option value="visitors_desc">Most Visitors</option>
                        <option value="visitors_asc">Least Visitors</option>
                        <option value="name_asc">Name (A-Z)</option>
                        <option value="name_desc">Name (Z-A)</option>
                    </select>
                </div>
                <div class="text-sm text-gray-600">
                    <span id="dataset-total">0</span> datasets • <span id="dataset-unique-ips">0</span> unique visitors
                    <span class="text-xs text-gray-400 mt-2"> •  Since 2025-11-22</span>
                </div>
            </div>
            
            <div class="flex-1 overflow-y-auto">
                <table class="w-full text-sm">
                    <thead class="bg-gray-50 text-gray-500 font-medium sticky top-0 border-b">
                        <tr>
                            <th class="px-6 py-3 text-left">Dataset Name</th>
                            <th class="px-6 py-3 text-center" style="width: 100px;">Visitors</th>
                            <!-- th class="px-6 py-3 text-left">IPs</th -->
                        </tr>
                    </thead>
                    <tbody id="dataset-access-body" class="divide-y divide-gray-100">
                        <tr><td colspan="2" class="px-6 py-4 text-center text-gray-400">Loading...</td></tr>
                    </tbody>
                </table>
            </div>
            
            <div class="px-6 py-3 border-t border-gray-100 bg-gray-50 flex justify-between items-center">
                <span id="dataset-page-info" class="text-sm text-gray-600"></span>
                <div class="flex gap-2">
                    <button id="dataset-prev-btn" class="px-3 py-1 rounded text-sm font-medium bg-gray-200 hover:bg-gray-300 text-gray-700 transition disabled:opacity-50">Prev</button>
                    <button id="dataset-next-btn" class="px-3 py-1 rounded text-sm font-medium bg-gray-200 hover:bg-gray-300 text-gray-700 transition disabled:opacity-50">Next</button>
                </div>
            </div>
        </div>
    </div>

    <!-- Copied Workspaces Modal -->
    <div id="copied-workspaces-modal" style="display: none;" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[100] p-4">
        <div class="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[85vh] overflow-hidden flex flex-col">
            <div class="flex justify-between items-center px-6 py-4 border-b border-gray-200">
                <h3 class="text-lg font-bold text-gray-900">Copied Public Workspaces</h3>
                <button id="copied-workspaces-close-btn" class="ml-4 text-gray-400 hover:text-gray-600 transition text-2xl">X</button>
            </div>

            <div class="px-6 py-4 border-b border-gray-100 bg-gray-50">
                <input type="text" id="copied-workspaces-search" placeholder="Search workspace..." class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                <p class="text-sm text-gray-600 mt-2"><span id="copied-workspaces-total">0</span> public workspaces copied by others</p>
            </div>

            <div class="flex-1 overflow-y-auto">
                <table class="w-full text-sm">
                    <thead class="bg-gray-50 text-gray-500 font-medium sticky top-0 border-b">
                        <tr>
                            <th class="px-6 py-3 text-left">Workspace Name</th>
                            <th class="px-6 py-3 text-center" style="width: 120px;">Times Copied</th>
                        </tr>
                    </thead>
                    <tbody id="copied-workspaces-body" class="divide-y divide-gray-100">
                        <tr><td colspan="2" class="px-6 py-4 text-center text-gray-400">Loading...</td></tr>
                    </tbody>
                </table>
            </div>

            <div class="px-6 py-3 border-t border-gray-100 bg-gray-50 flex justify-between items-center">
                <span id="copied-workspaces-page-info" class="text-sm text-gray-600"></span>
                <div class="flex gap-2">
                    <button id="copied-workspaces-prev-btn" class="px-3 py-1 rounded text-sm font-medium bg-gray-200 hover:bg-gray-300 text-gray-700 transition disabled:opacity-50">Prev</button>
                    <button id="copied-workspaces-next-btn" class="px-3 py-1 rounded text-sm font-medium bg-gray-200 hover:bg-gray-300 text-gray-700 transition disabled:opacity-50">Next</button>
                </div>
            </div>
        </div>
    </div>

    <footer class="max-w-7xl mx-auto px-8 py-6 text-center text-gray-400 text-sm">
        <p>© 2025 National Data Platform. Confidential Internal Report.</p>
    </footer>

    <script>
        let chartsInstance = null;
        let topViewedData = null;
        let topViewedPlatform = 'NDP';
        let datasetAccessData = null;
        let datasetAccessCurrentPage = 1;
        const datasetAccessPageSize = 20;
        let datasetAccessFiltered = [];
        let datasetStatuses = {}; // Cache for API statuses: {datasetName: {isPublic: bool, url: string}}

        const BASE_URL = 'https://ndp-test.sdsc.edu';
        const CKAN_API_PATH = '/catalog/api/3/action/package_show';

        // Function to check dataset status via CKAN API
        async function getDatasetStatus(datasetName) {
            if (datasetStatuses[datasetName]) return datasetStatuses[datasetName];

            try {
                const apiUrl = `${BASE_URL}${CKAN_API_PATH}?id=${encodeURIComponent(datasetName)}`;
                const response = await fetch(apiUrl);
                const data = await response.json();

                let isPublic = false;
                let targetUrl = `${BASE_URL}/dataset/${encodeURIComponent(datasetName)}`; // Default to public

                if (data.success) {
                    isPublic = true;
                } else if (data.error && data.error.__type === 'Authorization Error') {
                    // Private: open API response (shows access denied)
                    targetUrl = apiUrl;
                } else {
                    // Deleted/Not found: open API response (shows not found)
                    targetUrl = apiUrl;
                }

                datasetStatuses[datasetName] = { isPublic, url: targetUrl };
                return datasetStatuses[datasetName];
            } catch (err) {
                // Fallback to public URL on error
                datasetStatuses[datasetName] = { isPublic: true, url: targetUrl };
                return datasetStatuses[datasetName];
            }
        }

        function formatNumber(num) {
            if (num === null || num === undefined) return '--';
            if (typeof num === 'number') return num.toLocaleString();
            return String(num);
        }

        function openChartModal(data, title) {
            const modal = document.getElementById('chart-modal');
            document.getElementById('chart-title').textContent = title;
            modal.classList.remove('hidden');
            
            if (chartsInstance) {
                chartsInstance.destroy();
            }
            
            const ctx = document.getElementById('datasets-chart').getContext('2d');
            
            const labels = data.map(d => {
                const [year, month] = d.month.split('-');
                const date = new Date(year, parseInt(month) - 1, 1);
                return date.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
            });
            const counts = data.map(d => d.count);
            const maxCount = Math.max(...counts);
            
            chartsInstance = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'Created',
                        data: counts,
                        backgroundColor: 'rgba(59, 130, 246, 0.8)',
                        borderColor: 'rgb(59, 130, 246)',
                        borderWidth: 1,
                        borderRadius: 6,
                        hoverBackgroundColor: 'rgba(37, 99, 235, 0.9)',
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    indexAxis: 'y',
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                label: function(context) {
                                    return 'Created: ' + context.parsed.x.toLocaleString();
                                }
                            }
                        }
                    },
                    scales: {
                        x: {
                            beginAtZero: true,
                            max: maxCount * 1.1,
                            ticks: {
                                font: { size: 11, family: "'Inter', sans-serif" },
                                callback: value => value.toLocaleString()
                            },
                            grid: { color: 'rgba(0, 0, 0, 0.05)' }
                        },
                        y: {
                            ticks: { font: { size: 12, family: "'Inter', sans-serif" } },
                            grid: { display: false }
                        }
                    }
                }
            });
        }

        function closeChartModal() {
            document.getElementById('chart-modal').classList.add('hidden');
            if (chartsInstance) {
                chartsInstance.destroy();
                chartsInstance = null;
            }
        }

        async function loadMetrics() {
            try {
                const response = await fetch('daily_metrics.json');
                if (!response.ok) throw new Error('Failed to load metrics');
                const data = await response.json();
                
                if (data.error) {
                    document.getElementById('error-alert').classList.remove('hidden');
                    document.getElementById('error-message').textContent = data.error;
                    return;
                }

                document.getElementById('report-date').innerHTML = '<i class="far fa-calendar-alt mr-2"></i> ' + data.report_date;

                const kpis = data.kpis || {};
                
                let totalDatasets = 0;
                let monthlyChartData = [];
                if (kpis.total_catalog_entries) {
                    totalDatasets = kpis.total_catalog_entries.value;
                    monthlyChartData = kpis.total_catalog_entries.monthly_data || [];
                    document.getElementById('kpi-catalog-entries').textContent = formatNumber(totalDatasets);
                    if (kpis.total_catalog_entries.start_date) {
                        document.getElementById('kpi-catalog-since').textContent = 'Since ' + kpis.total_catalog_entries.start_date;
                    }
                    document.getElementById('catalog-chart-btn').addEventListener('click', () => {
                        if (monthlyChartData.length > 0) {
                            openChartModal(monthlyChartData, 'Datasets - Created Last 12 Months');
                        }
                    });
                }

                if (kpis.actively_used_entries) {
                    const activeTotal = kpis.actively_used_entries.value;
                    const addedMonthly = kpis.actively_used_entries.added_monthly || 0;
                    document.getElementById('kpi-active-entries').textContent = formatNumber(activeTotal);
                    document.getElementById('kpi-active-subtitle').textContent = 'In workspaces (' + formatNumber(addedMonthly) + ' added last 30 days)';
                    if (activeTotal > 0) {
                        const activePct = Math.round((addedMonthly / activeTotal) * 100);
                        document.getElementById('kpi-active-bar').style.width = activePct + '%';
                        document.getElementById('kpi-active-badge').textContent = activePct + '%';
                    }
                }

                if (kpis.recently_added_datasets) {
                    const last30 = kpis.recently_added_datasets.last_30_days || 0;
                    const last7 = kpis.recently_added_datasets.last_7_days || 0;
                    document.getElementById('kpi-recent-datasets').textContent = formatNumber(last30);
                    document.getElementById('kpi-recent-subtitle').textContent = 'Last 30 days (' + formatNumber(last7) + ' in 7 days)';
                    if (totalDatasets > 0) {
                        const recentPct = Math.round((last30 / totalDatasets) * 100);
                        document.getElementById('kpi-recent-bar').style.width = recentPct + '%';
                        document.getElementById('kpi-recent-badge').textContent = recentPct + '%';
                    }
                }

                if (kpis.storage_usage) {
                    document.getElementById('kpi-storage').textContent = kpis.storage_usage.value_tb + ' TB';
                    const storagePct = kpis.storage_usage.percent_used;
                    document.getElementById('kpi-storage-badge').textContent = storagePct + '%';
                    document.getElementById('kpi-storage-bar').style.width = storagePct + '%';
                    if (storagePct >= 80) {
                        const badge = document.getElementById('kpi-storage-badge');
                        badge.className = 'text-xs font-bold px-2 py-1 rounded bg-amber-50 text-amber-700';
                        badge.innerHTML = '<i class="fas fa-exclamation-triangle mr-1"></i>' + storagePct + '%';
                        document.getElementById('kpi-storage-bar').className = 'h-full bg-amber-500';
                    }
                }

                // CollabStudio
                const collabBody = document.getElementById('collabstudio-body');
                const iconColors = { purple: 'text-purple-500', amber: 'text-amber-500', blue: 'text-blue-500' };
                const monthlyTrends = data.collabstudio_monthly_trends || {};
                const trendKeyMap = {
                    'Projects': 'projects',
                    'Data Challenges': 'data_challenges',
                    'Classrooms': 'classrooms'
                };
                
                if (data.collabstudio && data.collabstudio.length > 0) {
                    collabBody.innerHTML = data.collabstudio.map(item => {
                        const trendKey = trendKeyMap[item.type];
                        const trendData = monthlyTrends[trendKey] || [];
                        return `
                        <tr class="hover:bg-gray-50 transition">
                            <td class="px-6 py-4 font-medium text-gray-900">
                                <i class="fas fa-${item.icon} ${iconColors[item.icon_color] || 'text-gray-500'} mr-2 w-5"></i> ${item.type}
                            </td>
                            <td class="px-6 py-4">${item.active_count}</td>
                            <td class="px-6 py-4">${item.users_engaged}</td>
                            <td class="px-6 py-4">${item.datasets_included}</td>
                            <td class="px-6 py-4 text-center">
                                <button class="trend-chart-btn text-gray-400 hover:text-blue-600 transition" data-entity="${item.type}" data-trend-key="${trendKey}">
                                    <i class="fas fa-chart-bar text-lg"></i>
                                </button>
                            </td>
                        </tr>`;
                    }).join('');
                    
                    document.querySelectorAll('.trend-chart-btn').forEach(btn => {
                        btn.addEventListener('click', (e) => {
                            const entityType = e.currentTarget.dataset.entity;
                            const trendKey = e.currentTarget.dataset.trendKey;
                            const trendData = monthlyTrends[trendKey] || [];
                            if (trendData.length > 0) {
                                openChartModal(trendData, entityType + ' - Created Last 12 Months');
                            }
                        });
                    });
                }

                // User Journey
                const journey = data.user_journey || {};
                const logPeriod = data.log_collection_period || {};
                if (logPeriod.days) {
                    document.getElementById('journey-period').textContent = 'Since ' + logPeriod.start_date + ' (' + logPeriod.days + ' days)';
                }
                document.getElementById('journey-registered-users').textContent = formatNumber(journey.registered_users);
                document.getElementById('journey-visitors').textContent = formatNumber(journey.unique_visitors);
                document.getElementById('datasets-viewed-btn').textContent = formatNumber(journey.datasets_viewed);
                document.getElementById('journey-searches').textContent = formatNumber(journey.total_searches);
                document.getElementById('journey-weekly').textContent = formatNumber(journey.datasets_added_weekly);
                document.getElementById('journey-monthly').textContent = formatNumber(journey.datasets_added_monthly);

                // Infrastructure
                const infra = data.infrastructure || {};
                document.getElementById('infra-workspaces').textContent = formatNumber(infra.total_workspaces);
                document.getElementById('copied-workspaces-count-btn').textContent = formatNumber(infra.copied_workspaces);
                copiedWorkspacesData = infra.copied_workspaces_detail || [];
                document.getElementById('infra-catalogs').textContent = formatNumber(infra.curated_catalogs);
                document.getElementById('infra-jhub-fetches').textContent = formatNumber(infra.jhub_workspace_fetches);
                
                if (infra.alerts && infra.alerts.length > 0) {
                    document.getElementById('infra-alerts').innerHTML = infra.alerts.map(alert => `
                        <div class="mt-4 bg-amber-50 border border-amber-100 p-3 rounded-md flex items-start gap-3">
                            <i class="fas fa-bell text-amber-600 mt-0.5"></i>
                            <div>
                                <p class="text-sm font-medium text-amber-800">${alert.title}</p>
                                <p class="text-xs text-amber-700">${alert.message}</p>
                            </div>
                        </div>
                    `).join('');
                }

                // Global Data Products
                const global = data.global_data_products || {};
                document.getElementById('global-public').textContent = formatNumber(global.public_entries);
                document.getElementById('global-private').textContent = formatNumber(global.private_entries);

                // Public CollabStudio objects (projects + data challenges + classrooms + workspaces)
                const publicEntities = data.public_entities || {};
                const publicObjectsTotal = publicEntities.total != null
                    ? publicEntities.total
                    : (publicEntities.public_projects || 0) + (publicEntities.public_data_challenges || 0)
                      + (publicEntities.public_classrooms || 0) + (publicEntities.public_workspaces || 0);
                const publicObjectsEl = document.getElementById('global-public-objects');
                publicObjectsEl.textContent = formatNumber(publicObjectsTotal);
                publicObjectsEl.title = `Projects: ${publicEntities.public_projects ?? 0} · `
                    + `Data Challenges: ${publicEntities.public_data_challenges ?? 0} · `
                    + `Classrooms: ${publicEntities.public_classrooms ?? 0} · `
                    + `Workspaces: ${publicEntities.public_workspaces ?? 0}`;

                // Top Viewed
                topViewedData = data.top_viewed || {};
                renderTopViewed();

            } catch (error) {
                document.getElementById('error-alert').classList.remove('hidden');
                document.getElementById('error-message').textContent = error.message;
            }
        }

        // Modal event listeners
        document.getElementById('chart-close-btn')?.addEventListener('click', closeChartModal);
        document.getElementById('chart-modal')?.addEventListener('click', (e) => {
            if (e.target.id === 'chart-modal') closeChartModal();
        });

        // Dataset Access Modal Functions
        async function renderDatasetAccessTable() {
            if (!datasetAccessData) return;
            let filtered = Object.entries(datasetAccessData.datasets);
            const searchTerm = document.getElementById('dataset-search-input').value.toLowerCase();
            const sortBy = document.getElementById('dataset-sort-select').value;
            
            if (searchTerm) {
                filtered = filtered.filter(([name]) => name.toLowerCase().includes(searchTerm));
            }
            
            filtered.sort(([nameA, dataA], [nameB, dataB]) => {
                if (sortBy === 'visitors_desc') return dataB.unique_visitors - dataA.unique_visitors;
                if (sortBy === 'visitors_asc') return dataA.unique_visitors - dataB.unique_visitors;
                if (sortBy === 'name_asc') return nameA.localeCompare(nameB);
                if (sortBy === 'name_desc') return nameB.localeCompare(nameA);
            });
            
            datasetAccessFiltered = filtered;
            datasetAccessCurrentPage = 1;
            await renderDatasetAccessPage(); // Now async
        }

        async function renderDatasetAccessPage() {
            const body = document.getElementById('dataset-access-body');
            const start = (datasetAccessCurrentPage - 1) * datasetAccessPageSize;
            const end = start + datasetAccessPageSize;
            const page = datasetAccessFiltered.slice(start, end);
            const totalPages = Math.ceil(datasetAccessFiltered.length / datasetAccessPageSize);

            // Fetch statuses for all datasets on this page (parallel)
            const statusPromises = page.map(([name]) => getDatasetStatus(name));
            await Promise.all(statusPromises);

            body.innerHTML = page.map(([name, data]) => {
                const status = datasetStatuses[name];
                const linkUrl = status ? status.url : `${BASE_URL}/dataset/${encodeURIComponent(name)}`;
                return `
                <tr class="hover:bg-gray-50">
                    <td class="px-6 py-3">
                        <a href="${linkUrl}" target="_blank" rel="noopener noreferrer"
                           class="font-medium text-gray-900 hover:text-blue-600 hover:underline transition cursor-pointer">
                            ${name}
                        </a>
                    </td>
                    <td class="px-6 py-3 text-center font-semibold text-blue-600">${data.unique_visitors}</td>
                    <!-- td class="px-6 py-3 text-xs text-gray-600">${data.visitor_ips.join(', ')}</td -->
                </tr>
                `;
            }).join('');
            
            document.getElementById('dataset-page-info').textContent = `Page ${datasetAccessCurrentPage} of ${totalPages || 1} (${datasetAccessFiltered.length} results)`;
            document.getElementById('dataset-prev-btn').disabled = datasetAccessCurrentPage === 1;
            document.getElementById('dataset-next-btn').disabled = datasetAccessCurrentPage === totalPages;
        }
        
        function openDatasetAccessModal() {
            document.getElementById('dataset-access-modal').style.display = 'flex';
            if (!datasetAccessData) {
                fetch('dataset_access.json')
                    .then(r => {
                        if (!r.ok) throw new Error('HTTP ' + r.status);
                        return r.json();
                    })
                    .then(data => {
                        datasetAccessData = data;
                        document.getElementById('dataset-total').textContent = data.summary?.total_datasets_accessed || 0;
                        document.getElementById('dataset-unique-ips').textContent = data.summary?.total_unique_ips || 0;
                        renderDatasetAccessTable();
                    })
                    .catch(err => {
                        document.getElementById('dataset-access-body').innerHTML = `<tr><td colspan="2" class="px-6 py-12 text-center text-red-600">Failed to load data: ${err.message}</td></tr>`;
                    });
            } else {
                renderDatasetAccessTable();
            }
        }
        
        function closeDatasetAccessModal() {
            document.getElementById('dataset-access-modal').style.display = 'none';
        }
        
        // Dataset access modal listeners (delayed to ensure DOM ready)
        setTimeout(() => {
            document.getElementById('datasets-viewed-btn')?.addEventListener('click', openDatasetAccessModal);
            document.getElementById('dataset-access-close-btn')?.addEventListener('click', closeDatasetAccessModal);
            document.getElementById('dataset-access-modal')?.addEventListener('click', (e) => {
                if (e.target.id === 'dataset-access-modal') closeDatasetAccessModal();
            });
            document.getElementById('dataset-search-input')?.addEventListener('input', renderDatasetAccessTable);
            document.getElementById('dataset-sort-select')?.addEventListener('change', renderDatasetAccessTable);
            document.getElementById('dataset-prev-btn')?.addEventListener('click', async () => {
                if (datasetAccessCurrentPage > 1) {
                    datasetAccessCurrentPage--;
                    await renderDatasetAccessPage();
                }
            });
            document.getElementById('dataset-next-btn')?.addEventListener('click', async () => {
                if (datasetAccessCurrentPage < Math.ceil(datasetAccessFiltered.length / datasetAccessPageSize)) {
                    datasetAccessCurrentPage++;
                    await renderDatasetAccessPage();
                }
            });
        }, 100);

        // Top Viewed
        const typeColors = {
            'Project': 'bg-purple-100 text-purple-700',
            'Classroom': 'bg-blue-100 text-blue-700',
            'Data Challenge': 'bg-amber-100 text-amber-700'
        };

        function renderTopViewed() {
            if (!topViewedData) return;
            const collabItems = (topViewedData.collabstudio || {})[topViewedPlatform] || [];
            const workspaceItems = (topViewedData.workspaces || {})[topViewedPlatform] || [];

            const collab = document.getElementById('top-collabstudio-list');
            collab.innerHTML = collabItems.length === 0
                ? '<li class="text-sm text-gray-400">No data</li>'
                : collabItems.map((item, i) => `
                    <li class="flex items-start justify-between gap-3">
                        <div class="flex items-start gap-2 min-w-0">
                            <span class="text-xs font-bold text-gray-400 w-5 shrink-0 pt-0.5">${i + 1}</span>
                            <div class="min-w-0">
                                <div class="flex items-center gap-1.5 flex-wrap">
                                    <span class="text-xs px-1.5 py-0.5 rounded font-medium shrink-0 ${typeColors[item.type] || 'bg-gray-100 text-gray-600'}">${item.type}</span>
                                    <span class="text-sm text-gray-800 truncate">${item.name}</span>
                                </div>
                                ${item.owner_name ? `<p class="text-xs text-gray-400 mt-0.5">${item.owner_name}${item.owner_email ? ' · ' + item.owner_email : ''}</p>` : ''}
                            </div>
                        </div>
                        <span class="text-sm font-bold text-gray-700 shrink-0">${item.views}</span>
                    </li>`).join('');

            const workspaces = document.getElementById('top-workspaces-list');
            workspaces.innerHTML = workspaceItems.length === 0
                ? '<li class="text-sm text-gray-400">No data</li>'
                : workspaceItems.map((item, i) => `
                    <li class="flex items-start justify-between gap-3">
                        <div class="flex items-start gap-2 min-w-0">
                            <span class="text-xs font-bold text-gray-400 w-5 shrink-0 pt-0.5">${i + 1}</span>
                            <div class="min-w-0">
                                <span class="text-sm text-gray-800 truncate block">${item.name}</span>
                                ${item.owner_name ? `<p class="text-xs text-gray-400 mt-0.5">${item.owner_name}${item.owner_email ? ' · ' + item.owner_email : ''}</p>` : ''}
                            </div>
                        </div>
                        <span class="text-sm font-bold text-gray-700 shrink-0">${item.views}</span>
                    </li>`).join('');
        }

        function switchTopViewedPlatform(platform) {
            topViewedPlatform = platform;
            document.getElementById('top-viewed-ndp-btn').className =
                'px-4 py-1.5 transition ' + (platform === 'NDP' ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50');
            document.getElementById('top-viewed-wstc-btn').className =
                'px-4 py-1.5 transition ' + (platform === 'WSTC' ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50');
            renderTopViewed();
        }

        // Copied Workspaces Modal
        let copiedWorkspacesData = [];
        let copiedWorkspacesFiltered = [];
        let copiedWorkspacesPage = 1;
        const copiedWorkspacesPageSize = 20;

        function renderCopiedWorkspacesTable() {
            const search = document.getElementById('copied-workspaces-search').value.toLowerCase();
            copiedWorkspacesFiltered = copiedWorkspacesData.filter(w => w.name.toLowerCase().includes(search));
            copiedWorkspacesPage = 1;
            renderCopiedWorkspacesPage();
        }

        function renderCopiedWorkspacesPage() {
            const body = document.getElementById('copied-workspaces-body');
            const start = (copiedWorkspacesPage - 1) * copiedWorkspacesPageSize;
            const page = copiedWorkspacesFiltered.slice(start, start + copiedWorkspacesPageSize);
            const totalPages = Math.ceil(copiedWorkspacesFiltered.length / copiedWorkspacesPageSize) || 1;

            body.innerHTML = page.length === 0
                ? '<tr><td colspan="2" class="px-6 py-8 text-center text-gray-400">No results</td></tr>'
                : page.map(w => `
                    <tr class="hover:bg-gray-50">
                        <td class="px-6 py-3 font-medium text-gray-900">${w.name}</td>
                        <td class="px-6 py-3 text-center font-semibold text-blue-600">${w.copies}</td>
                    </tr>`).join('');

            document.getElementById('copied-workspaces-page-info').textContent =
                `Page ${copiedWorkspacesPage} of ${totalPages} (${copiedWorkspacesFiltered.length} results)`;
            document.getElementById('copied-workspaces-prev-btn').disabled = copiedWorkspacesPage === 1;
            document.getElementById('copied-workspaces-next-btn').disabled = copiedWorkspacesPage === totalPages;
        }

        function openCopiedWorkspacesModal() {
            document.getElementById('copied-workspaces-modal').style.display = 'flex';
            document.getElementById('copied-workspaces-total').textContent = copiedWorkspacesData.length;
            renderCopiedWorkspacesTable();
        }

        function closeCopiedWorkspacesModal() {
            document.getElementById('copied-workspaces-modal').style.display = 'none';
        }

        setTimeout(() => {
            document.getElementById('copied-workspaces-btn')?.addEventListener('click', openCopiedWorkspacesModal);
            document.getElementById('copied-workspaces-count-btn')?.addEventListener('click', openCopiedWorkspacesModal);
            document.getElementById('copied-workspaces-close-btn')?.addEventListener('click', closeCopiedWorkspacesModal);
            document.getElementById('copied-workspaces-modal')?.addEventListener('click', e => {
                if (e.target.id === 'copied-workspaces-modal') closeCopiedWorkspacesModal();
            });
            document.getElementById('copied-workspaces-search')?.addEventListener('input', renderCopiedWorkspacesTable);
            document.getElementById('copied-workspaces-prev-btn')?.addEventListener('click', () => {
                if (copiedWorkspacesPage > 1) { copiedWorkspacesPage--; renderCopiedWorkspacesPage(); }
            });
            document.getElementById('copied-workspaces-next-btn')?.addEventListener('click', () => {
                const totalPages = Math.ceil(copiedWorkspacesFiltered.length / copiedWorkspacesPageSize);
                if (copiedWorkspacesPage < totalPages) { copiedWorkspacesPage++; renderCopiedWorkspacesPage(); }
            });
        }, 100);

        loadMetrics();
    </script>

</body>
</html>'''
    html_content = html_content.replace("https://ndp-test.sdsc.edu", METRICS_DASHBOARD_BASE_URL, 1)
    response = make_response(html_content)
    response.headers['Content-Type'] = 'text/html'
    return response


def daily_metrics_json():
    """Return metrics as JSON API"""
    metrics, error = load_metrics()
    
    if error:
        return jsonify({'error': error}), 404
    
    return jsonify(metrics)


def dataset_access_json():
    """Serve /tmp/dataset_access.json"""
    data, error = load_dataset_access()
    if error:
        return jsonify({'error': error}), 404

    return jsonify(data)
