# YouTube Music to Last.fm Scrobbler 🎵

An intelligent, automated scrobbler that syncs your YouTube Music history to Last.fm. It supports encryption for secure credential storage and can run 24/7 using GitHub Actions.

## ✨ Features

- **Hyper-Optimized Execution**: Routine sync runs complete in **~0.8s to 1.5s**, featuring lazy API loading, HTTP session pooling, and $O(1)$ hash lookups.
- **Smart Scrobbling**: Dual-pattern position tracking handles interleaved replays (`A → B → A`) and single-track repeat loops (`A → A → A`).
- **Hybrid SQLite Caching**: Persistent SQLite cache + 100-track delta refresh for YouTube Music liked songs (reduces API network calls by 90%+).
- **Secure**: AES-256 Fernet encryption for YouTube Music session credentials.
- **Multilingual Support**: Advanced date detection supporting 50+ languages.
- **Automated**: Integrated with GitHub Actions for 24/7 synchronization every 30 minutes.
- **Discord Notifications**: Rich markdown reports featuring a top-5 **Scrobbled** tracks list with `+ X more` overflow, **Liked Today**, **Most Played Track**, and **Most Played Artist**.
- **Lightweight**: Minimal dependencies, B-Tree indexed SQLite database, and single-commit batch transactions.

---

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.11+
- A [Last.fm account](https://www.last.fm/)
- [Last.fm API Credentials](https://www.last.fm/api/account/create)

### 2. Setup YouTube Music Authentication
To fetch your history, generate and encrypt your YouTube Music session credentials:

1.  **Generate `browser.json`**:
    Follow the official [ytmusicapi setup instructions](https://ytmusicapi.readthedocs.io/en/stable/setup/browser.html):
    - Open Developer Tools (`F12`) on [music.youtube.com](https://music.youtube.com) → **Network** tab.
    - Filter for an authenticated POST request to `/browse`.
    - Copy the request headers (or copy as fetch/Node.js).
    - Run `ytmusicapi browser` (on macOS: `pbpaste | ytmusicapi browser`) and paste the headers when prompted.
    - *Alternatively*, manually create `browser.json` with:
      ```json
      {
          "Accept": "*/*",
          "Authorization": "PASTE_AUTHORIZATION",
          "Content-Type": "application/json",
          "X-Goog-AuthUser": "0",
          "x-origin": "https://music.youtube.com",
          "Cookie": "PASTE_COOKIE"
      }
      ```
2.  **Encrypt credentials**:
    ```bash
    python encrypt_auth.py
    ```
    This creates `browser.json.enc` and outputs your **`YTMUSIC_AUTH_KEY`**.
    - **Save this key!** You will need it for your `.env` or GitHub Secrets.
    - Delete `browser.json` (`rm browser.json`) so plain text credentials are never committed.

### 3. Installation
```bash
git clone https://github.com/yourusername/youtube-music-scrobbler.git
cd youtube-music-scrobbler
pip install -r requirements.txt
```

### 4. Configuration
Create a `.env` file based on `.env.example`:
```ini
LAST_FM_API=your_api_key
LAST_FM_API_SECRET=your_api_secret
YTMUSIC_AUTH_KEY=your_encryption_key
DISCORD_WEBHOOK_URL=your_webhook_url (optional)
```

### 5. First Run
```bash
python start_ytm_scobble.py
```
On the first run, it will open your browser to authorize Last.fm. Once done, a `LASTFM_SESSION` will be saved to your `.env` file.

---

## 🤖 Automation (Recommended: GitHub Actions Scheduler)

The scrobbler is configured by default to run automatically every **30 minutes** using native GitHub Actions (`.github/workflows/sync.yml`).

### Features of 30-Minute Synchronization:
- **Real-Time Scrobbles**: Syncs your Last.fm history every 30 minutes.
- **Accurate Replay Tracking**: Detects both interleaved replays (`A → B → A`) and single-track repeat loops (`A → A → A`).
- **Quiet Run Suppression**: Automatically skips Discord notifications when no music was played during the 30-minute window to avoid channel spam.

### Quick Setup:
1. Add required GitHub Environment Secrets (`LAST_FM_API`, `LAST_FM_API_SECRET`, `LASTFM_SESSION`, `YTMUSIC_AUTH_KEY`, `DISCORD_WEBHOOK_URL`).
2. Commit `browser.json.enc` to your repository.
3. The workflow in `.github/workflows/sync.yml` is enabled out-of-the-box:
   ```yaml
   on:
     schedule:
       - cron: '17,47 * * * *' # Every 30 minutes at odd offsets (:17 and :47)
   ```
4. Refer to [**SCHEDULE_GUIDE.md**](SCHEDULE_GUIDE.md) for replay tracking mechanics and GitHub ToS compliance, and the [**GitHub Actions Guide**](GITHUB_ACTIONS_GUIDE.md) for CI/CD setup.

---

## Alternative: External Scheduler (cron-job.org)

If you prefer external triggering (e.g. to bypass GitHub Actions scheduler runner queue delays):

1. **Create a GitHub Personal Access Token** with Actions read/write permissions.
2. **Create a cronjob on cron-job.org**:
   - **URL**: `https://api.github.com/repos/<username>/youtube-music-scrobbler/actions/workflows/sync.yml/dispatches`
   - **Method**: `POST`
   - **Headers**:
     ```
     Content-Type: application/json
     Accept: application/vnd.github+json
     Authorization: Bearer YOUR_GITHUB_PAT
     ```
   - **Schedule**: Every 30 minutes (`0,30 * * * *`).

## 🛠️ Project Structure

- `start_ytm_scobble.py`: Main execution process, Last.fm OAuth handler, and database persistence manager.
- `ytmusic_fetcher.py`: Fetches YTM history and manages hybrid SQLite persistent caching for liked songs.
- `scrobble_utils.py`: Logic for `SmartScrobbler`, `PositionTracker`, metadata cleaning, and timestamp generation.
- `notifications.py`: Generates Discord markdown reports with `## Scrobbled` tracks, `## Liked Today`, and `## Most Played` sections.
- `lastpy/`: Lightweight Last.fm API client module supporting HTTPS connection pooling and batch scrobbling.
- `encrypt_auth.py`: Utility tool for encrypting YouTube Music session credentials into `browser.json.enc`.
- `data.db`: Local B-Tree indexed SQLite database tracking scrobbles, loved tracks, and liked song caches.
- `tests/`: Comprehensive test suite containing 110+ unit, integration, and optimization regression test cases.

---

## 🤝 Contributing
Contributions are welcome! Please refer to [AGENTS.md](AGENTS.md) for architectural details if you are using AI assistance.

## 📄 License
MIT License. See [LICENSE](LICENSE) for details.
