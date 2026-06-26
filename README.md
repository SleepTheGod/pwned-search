![Uploading image.png…]()


# 🔍 Breach Scanner API

[![License: WTFPL](https://img.shields.io/badge/License-WTFPL-brightgreen.svg)](http://www.wtfpl.net/)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-2.3.2-green.svg)](https://flask.palletsprojects.com/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

A high-performance RESTful API for searching through archived database leaks. Built for security researchers, penetration testers, and CTF participants.

Scan multiple databases concurrently, cache results, and export findings with this powerful search engine for archived data breaches.

**Technologies** Flask, Celery, Redis, BeautifulSoup, Docker

**Quick Start**
```bash
git clone https://github.com/SleepTheGod/pwned-search/
cd pwned-search
docker-compose up -d
curl http://localhost:5000/api/databases
```

## ⚡ Features

- 🚀 Concurrent database searching for maximum performance
- 💾 Redis caching for lightning-fast repeated queries
- 🔄 Async task queue with Celery for background processing
- 🛡️ Rate limiting to prevent API abuse
- 📊 CSV and JSON export capabilities
- 🐳 Docker ready with docker-compose support
- 📦 Pagination for handling large result sets
- 🔍 Case-sensitive and case-insensitive search options
- 📈 Health monitoring and statistics endpoints

## 🎯 Use Cases

- Security research and analysis
- CTF challenges and competitions
- Password breach verification
- OSINT investigations
- Educational and training purposes

## 🚀 Quick Start

### Using Docker
```bash
docker-compose up -d
```

### Manual Setup
```bash
pip install -r requirements.txt
python3 main.py
```

## 📚 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/databases` | GET | List all available databases |
| `/api/search` | GET/POST | Search across databases |
| `/api/task/<id>` | GET | Check async task status |
| `/api/download/<file>` | GET | Download database file |
| `/api/export/<term>` | GET | Export search results |
| `/api/stats` | GET | View database statistics |
| `/api/health` | GET | Health check endpoint |

## 🔧 Environment Variables

```bash
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/2
DEBUG=false
SECRET_KEY=your-secret-key
```

## 📝 License

WTFPL - Do What The Fuck You Want To Public License

## ⚠️ Disclaimer

This tool is for educational and research purposes only. Always respect privacy and applicable laws. The author assumes no responsibility for misuse of this software.
