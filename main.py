#!/usr/bin/env python3

'''
        DO WHAT THE FUCK YOU WANT TO PUBLIC LICENSE 
                    Version 2, August 10

 Copyright (C) 2026 Taylor Newsome (support@tsgh.org)

 Everyone is permitted to copy and distribute verbatim or modified 
 copies of this license document, and changing it is allowed as long 
 as the name is changed. 

            DO WHAT THE FUCK YOU WANT TO PUBLIC LICENSE 
   TERMS AND CONDITIONS FOR COPYING, DISTRIBUTION AND MODIFICATION 

  0. You just DO WHAT THE FUCK YOU WANT TO.
'''

import os
import re
import sys
import gzip
import io
import time
import uuid
import json
import hashlib
from datetime import datetime, timedelta
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.exceptions import TooManyRequests
import requests
from bs4 import BeautifulSoup
import redis
from celery import Celery

app = Flask(__name__)
CORS(app)

# Configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB max upload

# Rate limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["100 per minute", "1000 per hour"],
    storage_uri="memory://"
)

# Redis setup for caching (optional)
redis_client = None
try:
    redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    redis_client = redis.from_url(redis_url)
    redis_client.ping()
    print("[+] Redis connected successfully")
except:
    print("[-] Redis not available, using memory cache")
    redis_client = None

# Celery setup for async tasks
celery_app = Celery(
    'database_search',
    broker=os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/1'),
    backend=os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/2')
)
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=600,
    task_soft_time_limit=300,
)

# Cache for database list
DATABASE_CACHE = []
CACHE_TIMESTAMP = 0
CACHE_TTL = 300  # 5 minutes
CACHE_LOCK = Lock()

# Thread pool for concurrent searches
EXECUTOR = ThreadPoolExecutor(max_workers=10)

class Colours:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def PrintErrors(msg, ExitBool=True):
    print(Colours.FAIL + msg + Colours.ENDC)
    if ExitBool:
        sys.exit(1)

def cache_get(key):
    """Get value from cache"""
    if redis_client:
        try:
            value = redis_client.get(key)
            if value:
                return json.loads(value)
        except:
            pass
    return None

def cache_set(key, value, ttl=300):
    """Set value in cache"""
    if redis_client:
        try:
            redis_client.setex(key, ttl, json.dumps(value))
        except:
            pass

def GetDatabaseList(force_refresh=False):
    """Get the list of databases from the archived page with caching"""
    global DATABASE_CACHE, CACHE_TIMESTAMP
    
    if not force_refresh:
        # Try Redis cache first
        cached = cache_get('database_list')
        if cached:
            DATABASE_CACHE = cached
            CACHE_TIMESTAMP = time.time()
            return cached
        
        # Try memory cache
        current_time = time.time()
        if DATABASE_CACHE and (current_time - CACHE_TIMESTAMP) < CACHE_TTL:
            return DATABASE_CACHE
    
    with CACHE_LOCK:
        url = "https://web.archive.org/web/20190720034909/http://cdn.databases.today/"
        try:
            print(f"{Colours.OKBLUE}[*] Fetching database list from archive...{Colours.ENDC}")
            response = requests.get(url, timeout=30, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            
            if response.status_code != 200:
                print(f"{Colours.FAIL}[!] Failed to fetch page: {response.status_code}{Colours.ENDC}")
                return DATABASE_CACHE if DATABASE_CACHE else []
            
            soup = BeautifulSoup(response.text, "html.parser")
            databases = []
            
            # Find all links to .gz files
            for link in soup.find_all('a'):
                href = link.get('href')
                if href and href.endswith('.gz'):
                    # Get the full URL
                    if href.startswith('http'):
                        full_url = href
                    else:
                        full_url = "https://web.archive.org/web/20190720034909/http://cdn.databases.today/" + href.lstrip('/')
                    
                    # Extract database name from filename
                    filename = href.split('/')[-1]
                    name = filename.replace('.txt.gz', '').replace('.gz', '')
                    
                    # Get file size if available
                    size = "Unknown"
                    parent = link.parent
                    if parent and parent.next_sibling:
                        size_text = parent.next_sibling.string
                        if size_text:
                            size = size_text.strip()
                    
                    databases.append({
                        'name': name,
                        'url': full_url,
                        'filename': filename,
                        'size': size,
                        'hash': hashlib.md5(full_url.encode()).hexdigest()[:8]
                    })
            
            DATABASE_CACHE = databases
            CACHE_TIMESTAMP = time.time()
            
            # Cache in Redis
            cache_set('database_list', databases, ttl=CACHE_TTL)
            
            print(f"{Colours.OKGREEN}[+] Found {len(databases)} databases{Colours.ENDC}")
            return databases
            
        except Exception as e:
            print(f"{Colours.FAIL}[!] Error fetching database list: {e}{Colours.ENDC}")
            return DATABASE_CACHE if DATABASE_CACHE else []

def SearchDatabase(url, search_term, database_name, max_results=50, case_sensitive=False):
    """Download and search a gzipped database for a term"""
    results = {
        'database': database_name,
        'matches': [],
        'match_count': 0,
        'error': None,
        'search_time': 0,
        'size': 0
    }
    
    start_time = time.time()
    
    try:
        print(f"{Colours.OKBLUE}[*] Searching {database_name}...{Colours.ENDC}")
        
        # Check cache for this search
        cache_key = f"search:{database_name}:{hashlib.md5(search_term.encode()).hexdigest()}"
        cached_result = cache_get(cache_key)
        if cached_result:
            print(f"{Colours.OKGREEN}[+] Using cached result for {database_name}{Colours.ENDC}")
            return cached_result
        
        response = requests.get(url, stream=True, timeout=60, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        if response.status_code != 200:
            results['error'] = f"Failed to download: HTTP {response.status_code}"
            return results
        
        # Get content size
        results['size'] = len(response.content)
        
        # Decompress gzip content
        try:
            gzip_file = gzip.GzipFile(fileobj=io.BytesIO(response.content))
            content = gzip_file.read().decode('utf-8', errors='ignore')
        except:
            content = response.text
        
        # Search for the term
        lines = content.split('\n')
        matches = []
        
        search_func = str.find if case_sensitive else lambda x, y: x.lower().find(y.lower())
        
        for i, line in enumerate(lines):
            if search_func(line, search_term) != -1:
                matches.append({
                    'line': i + 1,
                    'content': line.strip()[:500]
                })
                if len(matches) >= max_results:
                    break
        
        results['matches'] = matches
        results['match_count'] = len(matches)
        
        # Cache results
        if results['match_count'] > 0:
            cache_set(cache_key, results, ttl=3600)  # Cache for 1 hour
        
    except Exception as e:
        results['error'] = str(e)
    
    results['search_time'] = round(time.time() - start_time, 2)
    return results

@celery_app.task(bind=True)
def async_search_task(self, search_term, db_indices=None, max_results=50, case_sensitive=False):
    """Background task for searching databases"""
    databases = GetDatabaseList()
    
    if not databases:
        return {'error': 'No databases available'}
    
    # Determine which databases to search
    if db_indices is not None:
        target_dbs = [databases[i] for i in db_indices if 0 <= i < len(databases)]
    else:
        target_dbs = databases
    
    total = len(target_dbs)
    results = []
    databases_with_matches = 0
    
    for idx, db in enumerate(target_dbs):
        self.update_state(state='PROGRESS', meta={
            'current': idx + 1,
            'total': total,
            'database': db['name'],
            'status': 'searching'
        })
        
        result = SearchDatabase(db['url'], search_term, db['name'], max_results, case_sensitive)
        results.append(result)
        if result['match_count'] > 0:
            databases_with_matches += 1
    
    return {
        'search_term': search_term,
        'results': results,
        'total_databases_searched': len(target_dbs),
        'databases_with_matches': databases_with_matches,
        'case_sensitive': case_sensitive
    }

@app.route('/api/databases', methods=['GET'])
@limiter.limit("30 per minute")
def list_databases():
    """List all available databases with pagination"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    force_refresh = request.args.get('refresh', 'false').lower() == 'true'
    search = request.args.get('search', '')
    
    databases = GetDatabaseList(force_refresh)
    
    if not databases:
        return jsonify({
            'status': 'error',
            'message': 'No databases found',
            'databases': []
        }), 404
    
    # Filter by search
    if search:
        databases = [db for db in databases if search.lower() in db['name'].lower()]
    
    # Pagination
    total = len(databases)
    start = (page - 1) * per_page
    end = start + per_page
    paginated = databases[start:end]
    
    # Safe response
    safe_databases = []
    for db in paginated:
        safe_databases.append({
            'name': db['name'],
            'filename': db['filename'],
            'size': db['size'],
            'hash': db['hash'],
            'index': databases.index(db)
        })
    
    return jsonify({
        'status': 'success',
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page,
        'databases': safe_databases
    })

@app.route('/api/search', methods=['GET', 'POST'])
@limiter.limit("20 per minute")
def search_databases():
    """Search for a term across all or specific databases"""
    # Parse request
    if request.method == 'GET':
        search_term = request.args.get('q')
        db_indices = request.args.get('indices')
        max_results = request.args.get('max_results', 50, type=int)
        case_sensitive = request.args.get('case_sensitive', 'false').lower() == 'true'
        timeout = request.args.get('timeout', 120, type=int)
    else:
        data = request.get_json()
        if not data:
            return jsonify({'status': 'error', 'message': 'Invalid JSON'}), 400
        search_term = data.get('q')
        db_indices = data.get('indices')
        max_results = data.get('max_results', 50)
        case_sensitive = data.get('case_sensitive', False)
        timeout = data.get('timeout', 120)
    
    # Validation
    if not search_term:
        return jsonify({
            'status': 'error',
            'message': 'Missing search term (q parameter)'
        }), 400
    
    if len(search_term) < 2:
        return jsonify({
            'status': 'error',
            'message': 'Search term must be at least 2 characters'
        }), 400
    
    max_results = min(max_results, 500)
    
    databases = GetDatabaseList()
    if not databases:
        return jsonify({
            'status': 'error',
            'message': 'No databases available'
        }), 404
    
    # Parse database indices
    if db_indices is not None:
        if isinstance(db_indices, str):
            try:
                if ',' in db_indices:
                    db_indices = [int(i.strip()) for i in db_indices.split(',')]
                else:
                    db_indices = [int(db_indices)]
            except ValueError:
                return jsonify({
                    'status': 'error',
                    'message': 'Invalid indices format. Use comma-separated integers'
                }), 400
        
        if isinstance(db_indices, list):
            # Validate indices
            for idx in db_indices:
                if not (0 <= idx < len(databases)):
                    return jsonify({
                        'status': 'error',
                        'message': f'Index {idx} out of range (0-{len(databases)-1})'
                    }), 400
            target_dbs = [databases[idx] for idx in db_indices]
        else:
            return jsonify({
                'status': 'error',
                'message': 'Invalid indices format'
            }), 400
    else:
        target_dbs = databases
    
    # Check if this is an async request
    if request.args.get('async') == 'true' or (request.method == 'POST' and data.get('async')):
        task = async_search_task.delay(
            search_term, 
            [databases.index(db) for db in target_dbs],
            max_results,
            case_sensitive
        )
        return jsonify({
            'status': 'accepted',
            'task_id': task.id,
            'message': 'Search started. Use /api/task/<task_id> to check status'
        }), 202
    
    # Synchronous search with timeout
    results = []
    databases_with_matches = 0
    search_start = time.time()
    
    try:
        # Use ThreadPoolExecutor for concurrent searching
        future_to_db = {}
        with ThreadPoolExecutor(max_workers=10) as executor:
            for db in target_dbs:
                future = executor.submit(
                    SearchDatabase, 
                    db['url'], 
                    search_term, 
                    db['name'], 
                    max_results,
                    case_sensitive
                )
                future_to_db[future] = db
            
            # Collect results with timeout
            for future in as_completed(future_to_db, timeout=timeout):
                result = future.result()
                results.append(result)
                if result['match_count'] > 0:
                    databases_with_matches += 1
                    
    except TimeoutError:
        return jsonify({
            'status': 'error',
            'message': f'Search timed out after {timeout} seconds',
            'partial_results': results
        }), 408
    
    return jsonify({
        'status': 'success',
        'search_term': search_term,
        'results': results,
        'total_databases_searched': len(target_dbs),
        'databases_with_matches': databases_with_matches,
        'search_time': round(time.time() - search_start, 2),
        'case_sensitive': case_sensitive
    })

@app.route('/api/task/<task_id>', methods=['GET'])
def get_task_status(task_id):
    """Get status of an async search task"""
    task = async_search_task.AsyncResult(task_id)
    
    if task.state == 'PENDING':
        response = {
            'status': 'pending',
            'message': 'Task is waiting to start'
        }
    elif task.state == 'PROGRESS':
        response = {
            'status': 'progress',
            'progress': task.info
        }
    elif task.state == 'SUCCESS':
        response = {
            'status': 'success',
            'result': task.result
        }
    elif task.state == 'FAILURE':
        response = {
            'status': 'error',
            'message': str(task.info)
        }
    else:
        response = {
            'status': 'unknown',
            'state': task.state
        }
    
    return jsonify(response)

@app.route('/api/download/<filename>', methods=['GET'])
@limiter.limit("10 per minute")
def download_database(filename):
    """Download a specific database file directly"""
    databases = GetDatabaseList()
    
    target_db = None
    for db in databases:
        if db['filename'] == filename:
            target_db = db
            break
    
    if not target_db:
        return jsonify({
            'status': 'error',
            'message': 'Database not found'
        }), 404
    
    try:
        response = requests.get(target_db['url'], stream=True)
        if response.status_code != 200:
            return jsonify({
                'status': 'error',
                'message': f'Failed to download: HTTP {response.status_code}'
            }), 500
        
        return send_file(
            io.BytesIO(response.content),
            as_attachment=True,
            download_name=filename,
            mimetype='application/gzip'
        )
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    databases = GetDatabaseList()
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'databases_cached': len(DATABASE_CACHE),
        'cache_age': int(time.time() - CACHE_TIMESTAMP) if CACHE_TIMESTAMP else 0,
        'redis_connected': redis_client is not None,
        'celery_connected': celery_app.control.ping() if celery_app else False,
        'active_searches': len(EXECUTOR._threads) if hasattr(EXECUTOR, '_threads') else 0
    })

@app.route('/api/stats', methods=['GET'])
def stats():
    """Get statistics about available databases"""
    databases = GetDatabaseList()
    
    if not databases:
        return jsonify({
            'status': 'error',
            'message': 'No databases available'
        }), 404
    
    # Calculate sizes
    total_size = 0
    size_count = 0
    size_distribution = {'small': 0, 'medium': 0, 'large': 0, 'huge': 0}
    
    for db in databases:
        if db['size'] != 'Unknown':
            try:
                if 'M' in db['size']:
                    size_mb = float(db['size'].replace('M', '').strip())
                    total_size += size_mb
                    size_count += 1
                    
                    if size_mb < 1:
                        size_distribution['small'] += 1
                    elif size_mb < 10:
                        size_distribution['medium'] += 1
                    elif size_mb < 50:
                        size_distribution['large'] += 1
                    else:
                        size_distribution['huge'] += 1
            except:
                pass
    
    return jsonify({
        'status': 'success',
        'total_databases': len(databases),
        'estimated_total_size_mb': round(total_size, 2),
        'estimated_total_size_gb': round(total_size / 1024, 2),
        'databases_with_known_size': size_count,
        'average_size_mb': round(total_size / size_count, 2) if size_count > 0 else 0,
        'size_distribution': size_distribution,
        'largest_db': max(databases, key=lambda x: float(x['size'].replace('M', '')) if x['size'] != 'Unknown' and 'M' in x['size'] else 0)['name'] if databases else None
    })

@app.route('/api/export/<search_term>', methods=['GET'])
def export_results(search_term):
    """Export search results as CSV or JSON"""
    format_type = request.args.get('format', 'json')
    max_results = request.args.get('max_results', 100, type=int)
    
    # Perform search
    databases = GetDatabaseList()
    results = []
    
    for db in databases[:10]:  # Limit to first 10 for export
        result = SearchDatabase(db['url'], search_term, db['name'], max_results)
        if result['match_count'] > 0:
            results.append(result)
    
    if format_type == 'csv':
        import csv
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Database', 'Line', 'Content'])
        for result in results:
            for match in result['matches']:
                writer.writerow([result['database'], match['line'], match['content']])
        return send_file(
            io.BytesIO(output.getvalue().encode()),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'search_{search_term}.csv'
        )
    else:
        return jsonify({
            'status': 'success',
            'search_term': search_term,
            'results': results
        })

@app.errorhandler(TooManyRequests)
def handle_rate_limit(e):
    return jsonify({
        'status': 'error',
        'message': 'Rate limit exceeded. Please try again later.',
        'retry_after': e.retry_after
    }), 429

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        'status': 'error',
        'message': 'Endpoint not found'
    }), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        'status': 'error',
        'message': 'Internal server error'
    }), 500

if __name__ == "__main__":
    # Pre-fetch database list on startup
    print(f"{Colours.HEADER}[*] Starting Database Search API v2.0...{Colours.ENDC}")
    GetDatabaseList()
    
    # Run the Flask app
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=os.environ.get('DEBUG', 'false').lower() == 'true',
        threaded=True
    )
