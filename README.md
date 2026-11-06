# Media Space

**Transform your raw media collections into a professional-grade, searchable digital library.**

Media Space is an intelligent personal media asset management system designed to bridge the gap between chaotic file directories and a polished viewing experience. Whether you are managing a massive NAS of movies and TV shows or a curated collection on external drives, Media Space provides the visibility, metadata, and discovery tools needed to master your library.

---

## 🚀 The Value Proposition

Managing large media collections often leads to "choice paralysis" or the frustration of not knowing exactly what you have. **Media Space solves this by providing:**

*   **Elimination of Decision Fatigue:** Stop scrolling through folders. Use our smart discovery engine to find exactly what fits your current mood, time constraints, and quality preferences.
*   **High-Fidelity Asset Visibility:** Go beyond filenames. Know the exact resolution, location, and metadata for every file in your collection without opening a single player.
*   **Seamless Ecosystem Integration:** Sync effortlessly with Jellyfin and enrich your local data using global industry standards like The Movie Database (TMDB).
*   **Professional-Grade Cataloging:** Turn messy, inconsistently named files into an organized database of rich, searchable information.

---

## ✨ Core Features

### 🔍 Intelligent Cataloging & Enrichment
*   **Automated Scanning:** Scan local drives and volumes to build your library. New files are placed in a review queue for safe integration.
*   **TMDB Integration:** Automatically pull titles, posters, genres, and ratings from The Movie Database to ensure your metadata is always world-class.
*   **Jellyfin Synchronization:** One-way sync with Jellyfin to keep your local catalog and streaming server perfectly aligned.

### 📊 Advanced Metadata & Quality Tracking
*   **Precision Resolution Detection:** Using `ffprobe` (optional), Media Space detects exact resolutions and handles widescreen/cropped aspect ratios intelligently (e.g., recognizing a 1920×800 file as high-quality 1080p).
*   **Deep Filtering:** Sort and filter your collection by resolution, release year, genre, rating, runtime, or availability.

### 🎲 Smart Discovery: "What to Watch?"
Stop wondering what to watch. Set your parameters—genre, minimum rating, maximum runtime, or specific resolutions—and let Media Space suggest the perfect titles from your existing library.

### 🛠️ Robust Management Tools
*   **Smart CSV Imports:** Bulk-update or add records via CSV with real-time previews and intelligent matching (Title + Year).
*   **Volume Tracking:** Drives are tracked by unique volume IDs, ensuring paths remain valid even if mount points shift.
*   **Safe Data Operations:** Deletions only affect database records—never your actual media files. Includes an "Excluded" safety net to restore accidentally removed titles.
*   **Direct Access:** If running on your local machine, instantly open any file in your native OS file manager (Dolphin, Nautilus, Explorer, Finder).

---

Reference for the Jellyfin connection, discovery flow and browser layout:
   [seerr-team/seerr](https://github.com/seerr-team/seerr).

---

## 🛠️ Technical Stack

Media Space is built with a modern, decoupled architecture:
*   **Backend:** Python-powered API managing metadata, scanning jobs, and database operations.
*   **Frontend:** A high-performance, responsive UI built with **React**, **TypeScript**, and **Tailwind CSS**.
*   **Database:** Lightweight and reliable **SQLite** for local data persistence.
*   **Containerization:** Fully Docker-ready for easy deployment.

---

## ⚙️ Installation & Setup

### Prerequisites
*   **Python 3.11+**
*   **Node.js 20+** (for frontend builds)
*   `ffprobe` (part of FFmpeg, optional but recommended for resolution detection)

### Quick Start (Development Mode)

1. **Clone the repository and set up the backend:**
   ```sh
   python3 -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   pip install -e '.[dev]'
   ```

2. **Build the frontend:**
   ```sh
   cd frontend
   npm install
   npm run build
   cd ..
   ```

3. **Configure Environment Variables:**
   Create a `.env` file in the root directory:
   ```env
   JELLYFIN_SERVER=http://your-server:8096
   JELLYFIN_API=your-jellyfin-api-key
   TMDB_API=your-tmdb-key
   ```
   **NOTE:**
   - These can also be added in the settings of the application itself

4. **Launch the application:**
   ```sh
   python3 -m mediamanager
   ```
   Open [http://127.0.0.1:8787](http://127.0.0.1:8787) in your browser.

---

## 🐳 Docker Deployment

For a production-ready setup, use the included `docker-compose.yml`:
```sh
docker-compose up -d
```

---

## 📂 Data Management

All persistent data is stored in the `data/` directory:
*   `data/media.db`: The SQLite database containing your library metadata.
*   `data/config.json`: Your application and path mapping configurations.

*Note: These files are git-ignored by default. You can specify a custom data location using the `MEDIA_MANAGER_DATA` environment variable.*
